"""What a discovery scan hears without asking: the mDNS, SSDP and AMX DDP
listeners, collected together with the SNMP scan that runs beside them.

The listeners start as a scan begins and listen through every active phase.
Phase 7 keeps them listening for late answers (``_listen_out_window``), tells
them to stop, waits a bounded time for SSDP's description fetches, and merges
what each one heard into the scan's results; the SNMP scan, launched in phase
6, is collected the same way.

The rules worth knowing before changing anything here:

* Evidence is read from each scanner's own ``results``, never from its task:
  a scan cut short cancels the tasks, and ``_run_scan`` still merges what was
  already heard (``merge_passive_results``) before the matcher runs.
* Each listener's address is merged once a scan (``_claim_passive_merge``),
  so the second merge after a truncation adds nothing twice.
* Unsolicited traffic creates at most ``MAX_PASSIVE_RESULT_SOURCES`` new
  devices a scan (``_get_or_create_passive``); a host the active phases
  already found keeps updating.
* A listener that could not open its socket ends the collect window early
  only when every listener has exited: nothing is left to hear anything.

``PassiveCollection`` is a mixin that ``DiscoveryEngine`` inherits, so every
name stays on the engine. From its host it needs ``results``, ``scan_status``,
``signal_index``, ``_passive_scanners``, ``_passive_merged``,
``_passive_sources_created``, ``_passive_cap_warned``, ``_get_or_create()``,
``_cancel_background_tasks()``, ``_update_intra_progress()``,
``_emit_progress()`` and ``_emit_device_update()``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable

from openavc.discovery.result import DiscoveredDevice, merge_device_info
from openavc.discovery.scan_evidence import mdns_hostname_evidence

if TYPE_CHECKING:
    from openavc.discovery.snmp_scanner import SNMPScanner

# The discovery engine's own log, which the IDE's log view files these lines under.
log = logging.getLogger("discovery")

# Flood guard for the aggregate results dict. Each passive listener already
# caps its own distinct sources per scan window (512 each in the mDNS, SSDP,
# and AMX-DDP scanners); this bound covers their sum with headroom and only
# counts NEW result entries created by passive/unsolicited traffic. Active
# results (a ping sweep on a /16 is legitimately thousands of hosts, port
# scans, driver probes) are never counted against it.
MAX_PASSIVE_RESULT_SOURCES = 2048

# Passive-collect heartbeat window. How long phase 7 listens is a depth
# policy (``DepthPolicy.passive_collect_seconds``), possibly shortened by the
# budget; the constant below is the fallback for callers that pass no wait,
# and these are module-level so tests can shrink the otherwise-untestable
# 5-30s window.
_PASSIVE_COLLECT_TICK_SECONDS: float = 1.0
_PASSIVE_COLLECT_DEFAULT_WAIT_SECONDS: float = 15.0
# Ceiling on the wait after the listeners are told to stop: mDNS and AMX DDP
# exit within half a second, and SSDP fetches description XML ten at a time
# with a 3 s timeout each, so this covers two full rounds of slow fetches.
# The phase's budget already covers it: SNMP, which the budget allows 5 s
# after the window, has usually finished during the window itself.
_LISTENER_DRAIN_SECONDS: float = 6.0


@dataclass
class BackgroundScans:
    """The tasks a scan runs behind its active phases: the three listeners
    started in phase 2 and the SNMP scan launched in phase 6, with the SNMP
    scanner, whose results outlive a cancelled task. Each stays None until it
    starts, and SNMP may never start."""

    mdns: asyncio.Task | None = None
    ssdp: asyncio.Task | None = None
    amx_ddp: asyncio.Task | None = None
    snmp: asyncio.Task | None = None
    snmp_scanner: "SNMPScanner | None" = None

    def tasks(self) -> tuple[asyncio.Task | None, ...]:
        """Every task, started or not, in the order they start."""
        return (self.mdns, self.ssdp, self.amx_ddp, self.snmp)


def _add_ssdp_evidence(device: DiscoveredDevice, result: Any) -> None:
    """One record per observed UPnP type: the driver-claimed family URN is
    only one of the types a device advertises."""
    device.evidence_log.extend(result.to_evidence_records())


def _add_amx_ddp_evidence(device: DiscoveredDevice, beacon: Any) -> None:
    device.evidence_log.append(beacon.to_evidence())


def _log_listener_failures(listeners: Iterable[tuple[asyncio.Task, str]]) -> None:
    """Retrieve each listener's outcome so a crashed one is logged, and so a
    failed task doesn't resurface later as asyncio's "exception was never
    retrieved". The evidence itself comes from the scanners, not from these
    results."""
    for task, label in listeners:
        if task.done() and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                log.debug("%s listener failed", label, exc_info=exc)


def _snmp_results(snmp_task: asyncio.Task, snmp_scanner: "SNMPScanner | None") -> dict:
    """What the SNMP scan found: its result when it finished, else (it
    overran its window and was cancelled) everything its scanner identified
    before the cutoff, since the scanner fills its results as it goes. A
    large or slow subnet keeps its SNMP enrichment that way."""
    if snmp_task.done() and not snmp_task.cancelled():
        try:
            return snmp_task.result()
        except Exception:  # Catch-all: task.result() re-raises whatever the task raised
            log.debug("SNMP task failed", exc_info=True)
            return {}
    if snmp_scanner is None:
        return {}
    partial = snmp_scanner.results
    if partial:
        log.info(
            "SNMP scan overran the collect window; merging %d partial result(s)",
            len(partial),
        )
    return partial


class PassiveCollection:
    """Phase 7's collection of the listeners and SNMP, and the merge a
    truncated scan repeats; a mixin ``DiscoveryEngine`` inherits."""

    async def _collect_passive_results(
        self,
        mdns_task: asyncio.Task,
        ssdp_task: asyncio.Task,
        amx_ddp_task: asyncio.Task,
        wait_seconds: float | None = None,
    ) -> None:
        """Listen through the collect window, stop the listeners, then merge.

        The listeners have been running since phase 2 and keep running for
        the whole window: late mDNS answers, SSDP NOTIFYs and AMX DDP beacons
        (sent every 30 to 60 seconds) arrive only while a socket is open to
        hear them. At the end of the window they are told to stop; mDNS and
        AMX DDP leave their receive loops within half a second, and SSDP then
        fetches the UPnP description XML for everything it heard, which gets
        its own ceiling (``_LISTENER_DRAIN_SECONDS``). A fetch still running
        at the ceiling is cancelled, and every description already parsed
        stays on its result, so one unresponsive web server can't hold the
        scan.

        ``wait_seconds`` is the window the scan budget granted (the depth
        policy's value, possibly shortened to leave room for driver probes).
        None falls back to the module default.
        """
        total_wait = (
            wait_seconds if wait_seconds is not None
            else _PASSIVE_COLLECT_DEFAULT_WAIT_SECONDS
        )
        remaining = await self._listen_out_window(
            {mdns_task, ssdp_task, amx_ddp_task}, total_wait,
        )
        await self._stop_passive_listeners()
        if remaining:
            _, remaining = await asyncio.wait(
                remaining, timeout=_LISTENER_DRAIN_SECONDS,
            )
        await self._cancel_background_tasks(*remaining)
        _log_listener_failures(
            ((mdns_task, "mDNS"), (ssdp_task, "SSDP"), (amx_ddp_task, "AMX DDP")),
        )
        await self.merge_passive_results()

    async def _listen_out_window(
        self, tasks: set[asyncio.Task], total_wait: float,
    ) -> set[asyncio.Task]:
        """Keep listening for ``total_wait`` seconds, a heartbeat a tick so
        the progress bar moves with a countdown rather than looking stuck.
        Returns the listeners still running. The window ends early only when
        every listener has already exited (for example, none could open its
        socket), since nothing is left to hear anything.

        The window is measured on the loop's clock, not by counting ticks:
        where the clock is coarser than a tick (15.6 ms on Windows before
        Python 3.13), a short timeout fires at once and a count of ticks
        would end the window before any time had passed.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        elapsed = 0.0
        while elapsed < total_wait and tasks:
            _, tasks = await asyncio.wait(
                tasks, timeout=min(_PASSIVE_COLLECT_TICK_SECONDS, total_wait - elapsed),
            )
            elapsed = loop.time() - started
            await self._update_intra_progress(min(elapsed / total_wait, 1.0))
            secs_left = max(0, int(total_wait - elapsed))
            await self._emit_progress(
                "passive_collect",
                f"Collecting passive results... ({secs_left}s remaining)",
            )
        return tasks

    async def _stop_passive_listeners(self) -> None:
        """Tell this scan's passive listeners to leave their receive loops."""
        scanners = self._passive_scanners
        if scanners is None:
            return
        mdns_scanner, ssdp_scanner, amx_ddp_scanner = scanners
        # mDNS and SSDP check the flag between receives (every 0.5 s). SSDP's
        # task then fetches descriptions before it finishes, so it is not
        # stopped outright the way AMX DDP is.
        mdns_scanner._running = False
        ssdp_scanner._running = False
        await amx_ddp_scanner.stop()

    async def merge_passive_results(self) -> int:
        """Merge whatever the passive listeners have heard into ``self.results``.

        Reads each scanner's in-memory ``results`` rather than its task's return
        value. That is the same dict — every listener's coroutine ends with
        ``return dict(self._results)`` — but reading it directly works when the
        task was **cancelled**, which is the whole point: a scan that runs out of
        time cancels the listeners in ``_scan_pipeline_inner``'s ``finally``, and
        reading the task result there yields nothing. The announcements were
        already received and parsed; discarding them threw away the strongest
        identification signals a scan collects (mDNS/SSDP/AMX-DDP), which is why
        a truncated scan used to return devices as ``possible`` rather than
        ``identified``.

        Idempotent per (source, ip): phase 7 calls this on the normal path and
        ``_run_scan`` calls it again after a truncation, including a truncation
        that landed midway through phase 7's own merge. Without the guard the
        second pass would re-append evidence for devices already merged, growing
        the evidence log and the "Why?" reveal with duplicates.

        Returns the number of (source, ip) pairs merged by this call.
        """
        scanners = self._passive_scanners
        if scanners is None:
            return 0
        mdns_scanner, ssdp_scanner, amx_ddp_scanner = scanners
        merged = await self._merge_passive_source(
            "mdns", mdns_scanner.results, self._add_mdns_evidence,
        )
        merged += await self._merge_passive_source(
            "ssdp", ssdp_scanner.results, _add_ssdp_evidence,
        )
        merged += await self._merge_passive_source(
            "amx_ddp", amx_ddp_scanner.results, _add_amx_ddp_evidence,
        )
        return merged

    async def _merge_passive_source(
        self, source: str, results: dict[str, Any],
        add_evidence: Callable[[DiscoveredDevice, Any], None],
    ) -> int:
        """Merge one listener's results: each address once a scan, a new one
        only under the passive cap (``_get_or_create_passive``)."""
        merged = 0
        for ip, result in results.items():
            if not self._claim_passive_merge(source, ip):
                continue
            device = self._get_or_create_passive(ip)
            if device is None:
                continue
            device.alive = True
            add_evidence(device, result)
            merge_device_info(device, result.to_device_info(), source)
            await self._emit_device_update(device, source)
            merged += 1
        return merged

    def _add_mdns_evidence(self, device: DiscoveredDevice, result: Any) -> None:
        """One record per advertised service: the vendor type that identifies
        a device is often only one of the types it announces, and packet
        order must not decide which survives. Then the announced name, unless
        a record for it is already there."""
        device.evidence_log.extend(result.to_evidence_records())
        device.evidence_log.extend(
            mdns_hostname_evidence(result, self.signal_index, device.evidence_log)
        )

    def _claim_passive_merge(self, source: str, ip: str) -> bool:
        """True the first time this (source, ip) is merged in the current scan."""
        key = (source, ip)
        if key in self._passive_merged:
            return False
        self._passive_merged.add(key)
        return True

    async def _collect_snmp_results(
        self,
        snmp_task: asyncio.Task | None,
        snmp_scanner: "SNMPScanner | None" = None,
    ) -> None:
        """Wait for the SNMP scan, up to its window, and merge what it found."""
        if snmp_task is None:
            return
        await asyncio.wait([snmp_task], timeout=5.0)
        await self._cancel_background_tasks(snmp_task)
        snmp_results = _snmp_results(snmp_task, snmp_scanner)
        for ip, snmp_info in snmp_results.items():
            device = self._get_or_create(ip)
            device.alive = True
            ev = snmp_info.to_evidence()
            if ev is not None:
                device.evidence_log.append(ev)
            merge_device_info(device, snmp_info.to_device_info(), "snmp")
            await self._emit_device_update(device, "snmp")
        if snmp_results:
            log.info("SNMP enriched %d devices", len(snmp_results))

    def _get_or_create_passive(self, ip: str) -> DiscoveredDevice | None:
        """``_get_or_create`` for passive/unsolicited sources, capped.

        Returns None (caller drops the record) once passive listeners have
        created MAX_PASSIVE_RESULT_SOURCES new entries this scan — each new
        entry costs a DiscoveredDevice plus a WebSocket fan-out to every
        client, so unsolicited traffic must not grow it without bound.
        Existing entries (hosts the ping sweep already found) keep updating.
        """
        if ip in self.results:
            return self.results[ip]
        if self._passive_sources_created >= MAX_PASSIVE_RESULT_SOURCES:
            if not self._passive_cap_warned:
                self._passive_cap_warned = True
                msg = (
                    "Passive discovery hit the %d-source result cap; "
                    "ignoring additional mDNS/SSDP/AMX sources for this scan"
                    % MAX_PASSIVE_RESULT_SOURCES
                )
                log.warning(msg)
                self.scan_status.warnings.append(msg)
            return None
        self._passive_sources_created += 1
        return self._get_or_create(ip)
