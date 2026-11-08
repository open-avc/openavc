"""The driver probes a discovery scan runs once it knows every host: the
checks the drivers declare to recognise their own devices.

Three kinds, from each driver's ``discovery:`` block (``discovery_hints``) and
its ``*_discovery.py`` companion:

* **UDP probes** fire once per scan at every subnet's directed broadcast
  address (unicast to each address of a /31 or /32, which have none), sharing
  one 10-a-second ``RateLimiter``.
* **TCP probes** run against every host whose port scan found the probe's
  port open, one port at a time (``probe_runner.observe_port_probes``: the
  greeting read first, a login prompt sent only the checks that expect it,
  then each check in turn). The ports start 20 ms apart and share the UDP
  probes' ``RateLimiter``, so the global send cap bounds the connect rate too.
* **Python companions** get a ``ProbeContext`` carrying the scan's open-port
  map, so they reuse the hosts already found instead of sweeping the subnets
  again, and the scan's port guard behind ``ctx.hold_port``.

The rules worth knowing before changing anything here:

* A probe's evidence is kept the moment it answers, under the position the
  probe was started in (``_probe_finished``), and merged in start order
  (``merge_probe_results``). The phase runs under its own deadline, so one
  cut short loses only the probes still waiting, and the fields a probe fills
  in come out the same whichever answered first.
* The phase is budgeted before it runs (``_count_probe_jobs``) by the same
  rule it then runs its TCP probes by (``_tcp_probe_jobs``: driver by driver,
  every scanned host with the port open), so the projection and the
  execution agree about what the phase is about to do. The phase reads the
  drivers once, as it begins.
* A probe enriches the device's empty fields and never replaces what the
  passive listeners or SNMP already said (``fill_only``).
* Every record names a host on a scanned subnet (discovery/scan_scope.py): a
  companion's host outside them is dropped.

``DriverProbes`` is a mixin that ``DiscoveryEngine`` inherits, so every name
stays on the engine. From its host it needs ``discovery_hints``, ``results``,
``scan_status``, ``_discovery_companions``, ``_probe_finished``,
``_probe_merged``, ``_guard``, ``_get_or_create()`` and
``_emit_device_update()``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from openavc.discovery.companion import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    ProbeContext,
    run_companion,
)
from openavc.discovery.probe_runner import (
    RateLimiter,
    ReplyPortUnavailable,
    observe_port_probes,
    port_probe_seconds,
    run_udp_broadcast_probe,
)
from openavc.discovery.result import Evidence, device_info_from_evidence, merge_device_info
from openavc.discovery.scan_scope import ip_in_subnets, udp_probe_targets_for

# The discovery engine's own log, which the IDE's log view files these lines under.
log = logging.getLogger("discovery")

# How far apart the TCP probe ports' first connects start.
_PORT_STAGGER_SECONDS = 0.020


@dataclass
class _ProbeRun:
    """What every probe in one run of the phase shares."""

    subnets: list[str]
    control_ip: str
    rate_limiter: RateLimiter


def _runner_failed(outcome: object) -> bool:
    """True when a companion's run ended in an exception of the runner's own:
    ``run_companion`` keeps a companion's failures inside, and a cancellation
    is the scan's, not the runner's."""
    if not isinstance(outcome, BaseException):
        return False
    return not isinstance(outcome, asyncio.CancelledError)


class DriverProbes:
    """The driver probe phase (see the module docstring)."""

    def _udp_probe_specs(self) -> list[Any]:
        return [h.udp_probe for h in self.discovery_hints if h.udp_probe is not None]

    def _tcp_probe_specs(self) -> list[Any]:
        return [h.tcp_probe for h in self.discovery_hints if h.tcp_probe is not None]

    def _hosts_with_port_open(self, port: int, subnets: list[str]) -> list[str]:
        """The scanned hosts whose port scan found ``port`` open."""
        return [
            ip for ip, dev in self.results.items()
            if port in (dev.open_ports or []) and ip_in_subnets(ip, subnets)
        ]

    def _tcp_probe_jobs(
        self, tcp_specs: list[Any], subnets: list[str],
    ) -> list[tuple[Any, str]]:
        """Every (spec, host) pair a TCP probe runs against, in the order the
        phase runs them and the budget counts them: driver by driver, then
        host by host."""
        return [
            (spec, ip)
            for spec in tcp_specs
            for ip in self._hosts_with_port_open(spec.port, subnets)
        ]

    def _count_probe_jobs(
        self, subnets: list[str],
    ) -> tuple[int, int, int, float, float]:
        """(tcp probe jobs, udp broadcast sends, companions, udp listen
        seconds, tcp wait seconds) for this scan.

        Counted before ``_run_custom_probes`` runs so the phase can be budgeted
        from real work rather than a guess.

        The two waits come from the probes that will run: the longest UDP
        listen window, and the longest one port's TCP checks can take
        (``probe_runner.port_probe_seconds``): a port's checks run one after
        another, so a port many drivers probe is the slowest. Writing the few
        bytes a probe sends never waits on a fresh connection, so the drains
        are left out.
        """
        udp_specs = self._udp_probe_specs()
        udp_sends = len(udp_probe_targets_for(subnets)) * len(udp_specs)
        udp_listen = 0.0
        if udp_sends:
            udp_listen = max(spec.timeout_ms for spec in udp_specs) / 1000.0
        jobs = self._tcp_probe_jobs(self._tcp_probe_specs(), subnets)
        by_port: dict[tuple[str, int], list] = {}
        for spec, ip in jobs:
            by_port.setdefault((ip, spec.port), []).append(spec)
        tcp_wait = max((port_probe_seconds(specs) for specs in by_port.values()), default=0.0)
        return (
            len(jobs), udp_sends, len(self._discovery_companions),
            udp_listen, tcp_wait,
        )

    async def _run_custom_probes(
        self,
        subnets: list[str],
        control_ip: str,
        max_tcp_jobs: int | None = None,
    ) -> None:
        """Run every driver-declared probe: the UDP probes, then the TCP
        probes, then the Python companions. Their evidence lands in each
        device's ``evidence_log`` for the matcher in phase 8.

        ``max_tcp_jobs`` caps the per-host TCP probes when the scan budget
        could not afford them all (the UDP broadcasts and companions are kept
        — one send each covers a whole subnet, so they are the cheapest
        identification per packet). The caller warns about the shortfall.
        """
        # The drivers are read once, as the phase begins: one installed or
        # removed while the UDP probes wait for answers changes nothing here.
        udp_specs = self._udp_probe_specs()
        tcp_specs = self._tcp_probe_specs()
        run = _ProbeRun(subnets, control_ip, RateLimiter(rate_per_sec=10.0))
        position = 0
        if udp_specs:
            position = await self._run_udp_probes(run, udp_specs)
        if tcp_specs:
            await self._run_tcp_probes(run, tcp_specs, position, max_tcp_jobs)
        if self._discovery_companions:
            await self._run_companions(run)

    # --- UDP probes ---

    async def _run_udp_probes(self, run: _ProbeRun, udp_specs: list[Any]) -> int:
        """Send every driver's UDP probe and keep what answers. Returns the
        position the next probe starts at: none was started when the subnets
        give the probes nowhere to go."""
        targets = udp_probe_targets_for(run.subnets)
        if not targets:
            return 0
        outcomes = await asyncio.gather(
            *(self._run_one_udp(run, at, spec, targets) for at, spec in enumerate(udp_specs)),
            return_exceptions=True,
        )
        self._report_udp_failures(udp_specs, outcomes)
        await self.merge_probe_results()
        return len(udp_specs)

    async def _run_one_udp(
        self, run: _ProbeRun, at: int, spec: Any, targets: list[str],
    ) -> None:
        result = await run_udp_broadcast_probe(
            spec,
            targets=targets,
            source_ip=run.control_ip,
            rate_limiter=run.rate_limiter,
            accept_from=run.subnets,
        )
        if isinstance(result, dict) and result:
            self._probe_finished[at] = [
                (ip, ev, "broadcast_probe") for ip, ev in result.items()
            ]

    def _report_udp_failures(self, udp_specs: list[Any], outcomes: list[Any]) -> None:
        """A probe whose reply port could not be opened is a scan warning,
        one per port naming the drivers it stopped; any other failure is a
        debug line.

        Like a listener that could not start (the collect phase's env_error):
        said, but not a partial scan. Everything else ran, and the devices
        these checks would have named still appear by their other signals.
        """
        unheard: dict[int, tuple[list[str], str]] = {}
        names_by_id = {
            h.driver_id: h.driver_name or h.driver_id
            for h in self.discovery_hints
        }
        for spec, outcome in zip(udp_specs, outcomes):
            if isinstance(outcome, ReplyPortUnavailable):
                names, _ = unheard.setdefault(outcome.port, ([], outcome.reason))
                names.append(names_by_id.get(spec.driver_id, spec.driver_id))
            elif isinstance(outcome, BaseException):
                log.debug(
                    "Custom UDP probe %s failed",
                    spec.probe_id, exc_info=outcome,
                )
        for port, (names, reason) in sorted(unheard.items()):
            self.scan_status.warnings.append(
                f"Could not listen on UDP port {port} for the "
                f"{', '.join(names)} identification check ({reason}). "
                "Another program on this computer may be using it."
            )

    # --- TCP probes ---

    async def _run_tcp_probes(
        self, run: _ProbeRun, tcp_specs: list[Any], first: int,
        max_tcp_jobs: int | None,
    ) -> None:
        """Run the TCP probes, port by port, numbering them from ``first``;
        the budget's cap cuts the job list before it is grouped by port."""
        jobs = [
            (first + i, spec, ip)
            for i, (spec, ip) in enumerate(self._tcp_probe_jobs(tcp_specs, run.subnets))
        ]
        if max_tcp_jobs is not None:
            jobs = jobs[:max_tcp_jobs]
        by_port: dict[tuple[str, int], list[tuple[int, Any]]] = {}
        for at, spec, ip in jobs:
            by_port.setdefault((ip, spec.port), []).append((at, spec))
        if not by_port:
            return
        outcomes = await asyncio.gather(
            *(
                self._probe_port(run, idx, host_port, entries)
                for idx, (host_port, entries) in enumerate(by_port.items())
            ),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                log.debug("Custom TCP probe failed", exc_info=outcome)
        await self.merge_probe_results()

    async def _probe_port(
        self, run: _ProbeRun, idx: int, host_port: tuple[str, int],
        entries: list[tuple[int, Any]],
    ) -> None:
        """Every TCP probe for one host's port, after its place in the stagger."""
        host, port = host_port
        if idx:
            await asyncio.sleep(idx * _PORT_STAGGER_SECONDS)

        def _answered(i: int, obs) -> None:
            if obs.evidence is not None:
                self._probe_finished[entries[i][0]] = [
                    (host, obs.evidence, "protocol_probe"),
                ]

        await observe_port_probes(
            host, port, [spec for _, spec in entries],
            source_ip=run.control_ip, guard=self._guard,
            rate_limiter=run.rate_limiter, on_observation=_answered,
        )

    # --- Python companions ---

    async def _run_companions(self, run: _ProbeRun) -> None:
        """Run every driver's ``*_discovery.py`` companion on the hosts found.

        ``run_companion`` isolates a companion's own failures; an exception
        surfacing here is the runner's (the worker thread failed to start, for
        one), so it is logged rather than swallowed.
        """
        hosts_by_open_port = self._hosts_by_open_port()
        companion_logger = logging.getLogger("discovery.companion.run")

        async def _emit_for_host(host: str, ev: Evidence) -> None:
            await self._record_companion_evidence(run.subnets, host, ev)

        driver_ids = list(self._discovery_companions)
        outcomes = await asyncio.gather(
            *(
                run_companion(driver_id, probe_fn, ProbeContext(
                    driver_id=driver_id,
                    source_ip=run.control_ip,
                    target_subnets=tuple(run.subnets),
                    timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
                    log=companion_logger,
                    _emit_for_host=_emit_for_host,
                    hosts_by_open_port=hosts_by_open_port,
                    _guard=self._guard,
                ))
                for driver_id, probe_fn in self._discovery_companions.items()
            ),
            return_exceptions=True,
        )
        for driver_id, outcome in zip(driver_ids, outcomes):
            if _runner_failed(outcome):
                log.warning(
                    "Discovery companion %s runner failed: %r",
                    driver_id, outcome,
                )

    def _hosts_by_open_port(self) -> dict[int, tuple[str, ...]]:
        """Port -> the hosts with it open, built once and shared by every
        companion. A port a connected project device holds is left out (an
        mDNS SRV record can put one in open_ports without a port scan)."""
        hosts: dict[int, list[str]] = {}
        for ip, dev in self.results.items():
            for port in dev.open_ports or ():
                if self._guard.held_by(ip, port) is not None:
                    self._guard.note_in_use(ip, port)
                    continue
                hosts.setdefault(port, []).append(ip)
        return {port: tuple(ips) for port, ips in hosts.items()}

    async def _record_companion_evidence(
        self, subnets: list[str], host: str, ev: Evidence,
    ) -> None:
        """One record a companion emitted.

        Companion code is community-trust (the same tier as a Python driver)
        and passes the host string verbatim, so the engine-wide rule that
        every result is a host on a scanned subnet is enforced here: without
        it a buggy or hostile companion could fabricate a record, even an
        identified one, for an address that was never scanned. The built-in
        probes are constrained already (UDP keys off the recorded sender; TCP
        only targets hosts already in results).
        """
        if not ip_in_subnets(host, subnets):
            log.warning(
                "Discovery companion emitted host %r outside the "
                "scanned subnets; ignoring", host,
            )
            return
        await self._add_probe_evidence(host, ev, "companion", "broadcast_probe")

    # --- Keeping what answered ---

    async def merge_probe_results(self) -> int:
        """Merge every driver probe that has matched into ``self.results``.

        A probe's evidence is held from the moment it matches, not from when
        its phase ends. The probe phase runs under its own deadline, and when
        the last few probes outran it the phase used to drop every probe that
        had already answered too, because the merge ran only once all of them
        returned: on a busy subnet nothing was identified from a probe at all.
        Called when each kind of probe has run, and again by ``_run_scan``
        before matching, which is what keeps the answered ones after a cut.

        Merged in the order the probes were started, not the order they
        answered, so the fields a probe fills in on a device come out the same
        whichever answered first. Idempotent per probe.

        Returns the number of probes merged by this call.
        """
        merged = 0
        for at in sorted(self._probe_finished):
            if at in self._probe_merged:
                continue
            self._probe_merged.add(at)
            for ip, ev, source in self._probe_finished[at]:
                await self._add_probe_evidence(ip, ev, source, source)
            merged += 1
        return merged

    async def _add_probe_evidence(
        self, ip: str, ev: Evidence, info_source: str, update_source: str,
    ) -> None:
        """Put one probe's evidence on its device, and lift what it extracted
        (hostname, model, firmware) onto the record so the card and the
        export report show it. ``fill_only``: a probe runs after passive
        collection, so it enriches empty fields but never clobbers an
        authoritative passive or SNMP identity by being longer."""
        device = self._get_or_create(ip)
        device.alive = True
        device.evidence_log.append(ev)
        info = device_info_from_evidence(ev)
        if info:
            merge_device_info(device, info, info_source, fill_only=True)
        await self._emit_device_update(device, update_source)
