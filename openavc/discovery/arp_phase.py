"""Phase 4 of a Discovery scan: the MAC address the ARP table holds for each
host, the network card maker it names, and each host's names from reverse DNS
and NetBIOS; plus the late read of the ARP table, in phase 7, for hosts only
the listeners found.

The rules worth knowing before changing anything here:

* The ARP table is a hint about where to look, never a discovery on its own:
  an entry proves an address replied at layer 2 once. An address only the
  table names, inside the scanned subnets, gets a second, unhurried ping
  (``_second_look``). One that answers is a host; one that stays quiet is
  carried on to the port scan unproven (``_arp_provisional``) and kept only if
  it answers something there. With no way to ping, those addresses are left
  out.
* The ARP read covers every alive host: it is one cheap read and the phase's
  strongest signal. Only the name lookups are bounded
  (``ArpPlan.name_lookup_hosts``).
* A host's names and MAC address become device info and evidence in
  ``scan_evidence.names_and_mac``, which the audit's network check uses too,
  so the two read a device the same way.

``ArpHarvest`` is a mixin that ``DiscoveryEngine`` inherits, so every name
stays on the engine. From its host it needs ``results``, ``scan_status``,
``oui_db``, ``_budget``, ``_ping_method`` (the sweep sets it),
``_scan_source_ip``, ``_arp_provisional``, ``_set_phase()``,
``_apply_plan()``, ``_run_budgeted()``, ``_get_or_create()``,
``_refresh_device_count()`` and ``_emit_device_update()``.
"""

from __future__ import annotations

import asyncio
import logging
import socket as _socket

from openavc.discovery import icmp
from openavc.discovery.network_scanner import harvest_arp_table, netbios_sweep
from openavc.discovery.result import merge_device_info
from openavc.discovery import scan_budget as budgeting
from openavc.discovery.scan_budget import ArpPlan, DepthPolicy
from openavc.discovery.scan_evidence import mac_info_and_evidence, names_and_mac
from openavc.discovery.scan_scope import ip_in_subnets, network_edges_for

# The discovery engine's own log, which the IDE's log view files these lines under.
log = logging.getLogger("discovery")

# Second look at addresses the ARP table named and the sweep missed. Narrow
# and unhurried where the sweep is wide and fast: the list is short and its
# addresses are already resolved, so this costs little and is the whole
# difference between a cold-cache scan and a warm one.
_REPING_CONCURRENCY: int = 16
_REPING_ATTEMPTS: int = 2
_REPING_TIMEOUT: float = 1.5
# Hard ceilings. Phase 4's budget was set from the sweep's host count, before
# this pass existed, and its primary job -- a MAC, an OUI and a name for every
# host -- must not be starved by it. So the second look is best-effort: past
# these limits an address simply stays unproven, gets port-scanned like the
# rest, and earns its place or is dropped.
_REPING_MAX_HOSTS: int = 128
_REPING_MAX_SECONDS: float = 6.0


async def _resolve_hostnames(
    ips: list[str],
    concurrency: int = 20,
) -> dict[str, str]:
    """Resolve IP addresses to hostnames via reverse DNS.

    Best-effort: returns only successful lookups. Timeouts are expected
    for most IPs on a local network.
    """
    results: dict[str, str] = {}
    sem = asyncio.Semaphore(concurrency)
    loop = asyncio.get_event_loop()

    async def resolve_one(ip: str) -> None:
        async with sem:
            try:
                hostname, _, _ = await asyncio.wait_for(
                    loop.run_in_executor(None, _socket.gethostbyaddr, ip),
                    timeout=1.0,
                )
                if hostname and hostname != ip:
                    results[ip] = hostname
            except (OSError, asyncio.TimeoutError):
                pass

    await asyncio.gather(
        *[resolve_one(ip) for ip in ips],
        return_exceptions=True,
    )
    return results


class ArpHarvest:
    """Phase 4 and the late ARP read, for ``DiscoveryEngine``."""

    async def _arp_harvest_phase(self, alive_ips: list[str], policy: DepthPolicy) -> None:
        """Phase 4, budgeted from the alive count: the first number the scan
        knows that predicts what everything downstream costs.

        Runs even when the sweep heard nobody: the ARP table can still name
        hosts that drop ping, and the second look in _arp_phase is how they
        reach the port scan. A single-device scan of a device that ignores
        ping is exactly that case.
        """
        await self._set_phase(4, "arp_harvest", "Reading MAC addresses and hostnames...")
        arp_plan = budgeting.plan_arp_phase(self._budget, len(alive_ips), policy.netbios)
        self._apply_plan(budgeting.PHASE_ARP, arp_plan.warnings)
        await self._run_budgeted(
            budgeting.PHASE_ARP, arp_plan.budget,
            self._arp_phase(alive_ips, arp_plan),
            "Reading MAC addresses and hostnames ran out of its share "
            "of the scan time. Some devices are missing their vendor "
            "or hostname.",
        )

    async def _arp_phase(self, alive_ips: list[str], plan: ArpPlan) -> None:
        """Phase 4 body: the ARP table, the second look, the names, and what
        they put on each alive host.

        ``alive_ips`` grows in place by the addresses the second look carries
        on: the phases after this one take the same list, so a host found
        here is port-scanned and probed like any other.
        """
        arp_table = await harvest_arp_table()
        await self._second_look(self._arp_only_addresses(arp_table), alive_ips)

        named = (
            alive_ips
            if plan.name_lookup_hosts is None
            else alive_ips[: plan.name_lookup_hosts]
        )
        hostnames, netbios = await self._look_up_names(named, plan.netbios)

        for ip in alive_ips:
            device = self._get_or_create(ip)
            reads = names_and_mac(
                hostnames.get(ip), netbios.get(ip), arp_table.get(ip), self,
            )
            device.evidence_log.extend(reads.evidence)
            if reads.learned:
                reads.merge_into(device)
                await self._emit_device_update(device, "arp_harvest")

    def _arp_only_addresses(self, arp_table: dict[str, str]) -> list[str]:
        """The addresses the ARP table names inside the scanned subnets that
        the sweep did not find, in the table's order.

        A subnet's network and broadcast addresses are not hosts and are left
        out (``network_edges_for``).
        """
        subnets = self.scan_status.subnets
        edges = network_edges_for(subnets)
        return [
            ip for ip in arp_table
            if ip not in self.results
            and ip not in edges
            and ip_in_subnets(ip, subnets)
        ]

    async def _second_look(self, candidates: list[str], alive_ips: list[str]) -> None:
        """Ping the ARP-only addresses again and carry every one of them on.

        A sweep drops hosts that are perfectly willing to answer. Each echo
        waits on an address lookup first, the kernel queues only a couple of
        packets per unresolved neighbour, and a /22 sits on its
        neighbour-table ceiling -- so on a COLD cache a real share of live
        hosts never get pinged at all. Measured here: of 18 addresses the ARP
        table held and the sweep did not, 5 answered immediately when asked
        again unhurried. That is the whole reason the first scan on a freshly
        provisioned box found about half what the next one found -- the first
        scan's real work was filling the cache.

        One that answers is a host; the rest are carried on unproven
        (``_arp_provisional``). With no way to ping, none is carried on.
        """
        if not candidates or self._ping_method in ("", icmp.METHOD_NONE):
            return
        recovered: set[str] = set()
        try:
            await asyncio.wait_for(
                self._reping(candidates[:_REPING_MAX_HOSTS], recovered),
                timeout=_REPING_MAX_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError):
            log.debug("Second ping pass hit its ceiling; keeping what replied")
        for ip in recovered:
            self._get_or_create(ip).alive = True
        still_quiet = [ip for ip in candidates if ip not in recovered]
        log.info(
            "ARP named %d host(s) the sweep missed: %d answered a second "
            "ping, %d carried on to the port scan unproven",
            len(candidates), len(recovered), len(still_quiet),
        )
        for ip in still_quiet:
            self._get_or_create(ip).alive = True
            self._arp_provisional.add(ip)
        self._refresh_device_count()
        alive_ips.extend(candidates)

    async def _look_up_names(
        self, named: list[str], netbios: bool,
    ) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        """Reverse DNS and, when ``netbios``, NetBIOS for ``named``, at the
        same time; both best-effort. A NetBIOS sweep that fails gives no
        names and the reverse-DNS names still count."""
        hostname_task = asyncio.create_task(_resolve_hostnames(named))
        netbios_task = None
        if netbios and named:
            netbios_task = asyncio.create_task(
                netbios_sweep(
                    named, concurrency=30, timeout=1.0,
                    source_ip=self._scan_source_ip,
                )
            )

        hostnames = await hostname_task
        netbios_results: dict[str, dict[str, str]] = {}
        if netbios_task:
            try:
                netbios_results = await netbios_task
            except Exception:
                log.debug("NetBIOS sweep failed", exc_info=True)
        return hostnames, netbios_results

    async def _reping(self, ips: list[str], alive: set[str]) -> None:
        """Ping a short list again, unhurried, recording what answers.

        A host that lost its first echo to a cold address cache is not a
        dead host, and asking once more is the cheapest way to tell the two
        apart.

        Answers go into the caller's ``alive`` set as they arrive rather than
        being returned, so that cancelling this pass on its ceiling keeps
        every host that had already replied.
        """
        semaphore = asyncio.Semaphore(_REPING_CONCURRENCY)

        async def one(ip: str) -> None:
            async with semaphore:
                for _ in range(_REPING_ATTEMPTS):
                    result = await icmp.ping_host(
                        ip,
                        timeout=_REPING_TIMEOUT,
                        source_ip=self._scan_source_ip,
                        method=self._ping_method,
                    )
                    if result == icmp.RESULT_ALIVE:
                        alive.add(ip)
                        return

        await asyncio.gather(*(one(ip) for ip in ips), return_exceptions=True)

    async def _late_arp_harvest(self) -> None:
        """Harvest a MAC + OUI for alive devices the phase-4 ARP pass missed.

        Passive-discovered devices (mDNS/SSDP) on a subnet the ping sweep
        skipped — e.g. a switch at its factory-default link-local /16, which
        exceeds ``max_subnet_size`` — never went through phase 4, so they carry
        no MAC and thus no OUI enrichment, and an OUI-only driver hint can never
        surface them. By now the unicast traffic from the passive-only port
        scan (and the passive exchange) has populated the OS ARP cache for them,
        so a second read picks up the MAC and emits the same OUI evidence +
        vendor lookup phase 4 does. Only touches devices without a MAC, so
        ping-swept hosts already harvested in phase 4 are left untouched.
        """
        mac_missing = [
            ip for ip, dev in self.results.items()
            if dev.alive and not dev.mac
        ]
        if not mac_missing:
            return
        arp_late = await harvest_arp_table()
        enriched = 0
        for ip in mac_missing:
            mac = arp_late.get(ip)
            if not mac:
                continue
            device = self._get_or_create(ip)
            info, oui_ev = mac_info_and_evidence(mac, self.oui_db)
            # fill_only: this runs AFTER passive collection, and OUI names
            # the NIC vendor — a longer IEEE registrant string must not
            # clobber the manufacturer/category a device self-reported via
            # mDNS/SSDP/SNMP. Enrich empty fields; never overwrite.
            merge_device_info(device, info, "arp", fill_only=True)
            device.evidence_log.append(oui_ev)
            enriched += 1
            await self._emit_device_update(device, "arp_harvest")
        if enriched:
            log.info(
                "Late ARP harvest enriched %d passive-only device(s) with a "
                "MAC/OUI", enriched,
            )
