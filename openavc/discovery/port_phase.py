"""Phase 5 of a Discovery scan: which TCP ports each live host has open, and
the banner a port greets with; plus the same scan, in phase 7, for hosts only
the listeners found.

The rules worth knowing before changing anything here:

* The ports come from the drivers, not from a fixed list: each loaded driver's
  probe and ``port_open:`` ports, the catalog's ports for drivers not
  installed, a small generic baseline, and the thorough extras
  (``port_scan_lists``). The budget narrows to the ``core`` list (baseline
  plus loaded drivers) when the full one will not fit.
* Ports narrow before hosts: dropping a port costs one signal on every
  device, dropping a host costs every signal on one device.
* A host counts as scanned whatever it answered (``_port_scanned``): phase 7's
  follow-up asks that, not whether it has open ports, since an mDNS record
  puts a port there without any scan.
* A port a connected project device holds is never connected to: the port
  guard (``discovery/port_guard.py``) goes with every scan.

``PortScan`` is a mixin that ``DiscoveryEngine`` inherits, so every name stays
on the engine. From its host it needs ``discovery_hints``,
``community_index``, ``results``, ``scan_status``, ``_budget``, ``_guard``,
``_scan_source_ip``, ``_port_scanned``, ``_set_phase()``, ``_apply_plan()``,
``_run_budgeted()``, ``_get_or_create()``, ``_emit_device_update()`` and
``_update_intra_progress()``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Iterable

from openavc.discovery import scan_budget as budgeting
from openavc.discovery.port_scanner import BASELINE_PORTS, grab_banners, scan_host_ports
from openavc.discovery.result import DiscoveredDevice, merge_device_info
from openavc.discovery.scan_budget import DepthPolicy
from openavc.discovery.scan_scope import ip_in_subnets

# The discovery engine's own log, which the IDE's log view files these lines under.
log = logging.getLogger("discovery")

# Generic alternate web, streaming and management ports a thorough scan adds.
# No vendor labels.
THOROUGH_EXTRA_PORTS: tuple[int, ...] = (554, 3000, 4000, 5060, 8443, 8888, 9000, 10000)

_OVERRUN = (
    "The port scan ran out of its share of the scan time. Some "
    "devices are missing their open-port signal."
)


@dataclass(frozen=True)
class PortScanSettings:
    """How a scan port-scans its hosts: the ports it checks, how many hosts
    at once, and whether gentle mode pauses between them."""

    ports: list[int]
    concurrency: int
    gentle: bool


def _hint_ports(hints: Iterable[Any]) -> set[int]:
    """The port each driver hint names for its ``tcp_probe:`` and ``port_open:``."""
    ports: set[int] = set()
    for hint in hints:
        if hint.tcp_probe is not None:
            ports.add(hint.tcp_probe.port)
        ports.update(hint.port_open)
    return ports


def _catalog_ports(community_drivers: list[dict[str, Any]]) -> set[int]:
    """The whole-number ports the catalog lists for its drivers; a malformed
    entry or port is skipped."""
    ports: set[int] = set()
    for drv in community_drivers:
        if isinstance(drv, dict):
            ports.update(p for p in drv.get("ports", []) or [] if isinstance(p, int))
    return ports


class PortScan:
    """Phase 5: the open ports and banners of every live host."""

    def port_scan_lists(
        self, community_drivers: list[dict[str, Any]], extended: bool,
    ) -> tuple[list[int], list[int]]:
        """(core, full) TCP port lists a scan checks.

        Every loaded driver contributes the port it declares for
        ``tcp_probe:`` and ``port_open:`` hints; the community catalog
        contributes ports of un-installed drivers so a known-but-uninstalled
        device still surfaces with its open ports; and a small generic
        baseline (SSH, Telnet, HTTP/HTTPS, alt-HTTP) covers banner reading and
        web management for devices no driver claims yet. ``extended`` (a
        thorough scan) adds ``THOROUGH_EXTRA_PORTS``.

        ``core`` is the AV-critical subset a budget narrows to when the full
        list will not fit: the baseline plus the ports of drivers actually
        loaded here.
        """
        port_set = set(BASELINE_PORTS) | _hint_ports(self.discovery_hints)
        core = sorted(port_set)
        port_set |= _catalog_ports(community_drivers)
        if extended:
            port_set.update(THOROUGH_EXTRA_PORTS)
        return core, sorted(port_set)

    async def _port_phase(
        self, alive_ips: list[str], policy: DepthPolicy, gentle: bool,
    ) -> PortScanSettings:
        """Port-scan the live hosts; what phase 7's follow-up scans with: the
        port list (the budget's narrowed one, when there were hosts to plan
        for) at the policy's width."""
        await self._set_phase(5, "port_scan", "Probing device ports...")
        community_drivers = await self.community_index.get_drivers()
        core_port_list, port_list = self.port_scan_lists(
            community_drivers, extended=policy.extended_ports,
        )
        log.info(
            "Port scan: %d ports (%d baseline + driver/catalog)",
            len(port_list), len(BASELINE_PORTS),
        )
        if not alive_ips:
            return PortScanSettings(port_list, policy.port_host_concurrency, gentle)

        plan = budgeting.plan_port_scan(
            self._budget, len(alive_ips), port_list, core_port_list,
            policy.port_host_concurrency,
        )
        self._apply_plan(budgeting.PHASE_PORT, plan.warnings)
        targets = alive_ips[: plan.host_limit] if plan.host_limit is not None else alive_ips
        await self._run_budgeted(
            budgeting.PHASE_PORT, plan.budget,
            self._port_scan_hosts(
                targets, PortScanSettings(plan.ports, plan.concurrency, gentle),
            ),
            _OVERRUN,
        )
        return PortScanSettings(plan.ports, policy.port_host_concurrency, gentle)

    async def _port_scan_hosts(
        self,
        hosts: list[str],
        settings: PortScanSettings,
        phase: str = "port_scan",
        always_emit: bool = False,
    ) -> None:
        """Port-scan a set of hosts, ``settings.concurrency`` of them at a time.

        Each host's own port probes are concurrent and staggered, so
        overlapping a bounded number of hosts adds breadth without changing
        what any single device sees; it is what lets the budget trade width
        for time. Gentle mode keeps its per-host pause and a much lower width.
        """
        total = len(hosts)
        if not total or not settings.ports:
            return
        semaphore = asyncio.Semaphore(max(1, settings.concurrency))
        done = 0

        async def _scan_one(ip: str) -> None:
            nonlocal done
            async with semaphore:
                await self._port_scan_host(ip, settings, phase, always_emit)
            done += 1
            await self._update_intra_progress(done / total)

        await asyncio.gather(*[_scan_one(ip) for ip in hosts])

    async def _port_scan_host(
        self, ip: str, settings: PortScanSettings, phase: str, always_emit: bool,
    ) -> None:
        """One host: its open ports and their banners onto its record,
        announced when it had any open (or ``always_emit``)."""
        device = self._get_or_create(ip)
        open_ports = await scan_host_ports(
            ip, settings.ports, timeout=1.0, source_ip=self._scan_source_ip,
            guard=self._guard,
        )
        self._port_scanned.add(ip)
        if open_ports:
            merge_device_info(device, {"open_ports": open_ports}, "port_scan")
            await self._read_banners(device, open_ports)
        if open_ports or always_emit:
            await self._emit_device_update(device, phase)
        # Gentle mode: a small pause before this slot takes another host.
        if settings.gentle:
            await asyncio.sleep(0.1)

    async def _read_banners(self, device: DiscoveredDevice, open_ports: list[int]) -> None:
        """What the banner-friendly ones among ``open_ports`` greet with."""
        banners = await grab_banners(
            device.ip, open_ports, timeout=2.0, source_ip=self._scan_source_ip,
        )
        if banners:
            merge_device_info(device, {"banners": banners}, "banner")

    async def _port_scan_passive_only(
        self, alive_ips: list[str], settings: PortScanSettings,
    ) -> None:
        """Port-scan the devices only passive discovery found.

        Those (mDNS/SSDP/AMX-DDP) that weren't in the ping sweep, so the
        custom-probe pass sees their open ports. "Not scanned yet" is the
        test, not "no open ports": an mDNS SRV port already sits in
        open_ports, and a device that drops ping but advertises one service
        must still have its other ports found and probed. Only inside the
        scanned subnets (``ip_in_subnets``).
        """
        ping_found = set(alive_ips) if alive_ips else set()
        passive_only = [
            ip for ip, dev in self.results.items()
            if dev.alive and ip not in ping_found and ip not in self._port_scanned
            and ip_in_subnets(ip, self.scan_status.subnets)
        ]
        if passive_only:
            log.info(
                "Port scanning %d passive-only devices (mDNS/SSDP)",
                len(passive_only),
            )
            await self._port_scan_hosts(
                passive_only, settings, phase="passive_followup", always_emit=True,
            )
