"""Phase 3 of a Discovery scan: the ping sweep for live hosts.

The rules worth knowing before changing anything here:

* The sweep is budgeted from its address count, the one phase whose cost is
  fully known before it runs. Capping it is what keeps it from spending the
  time the identification phases need (uncapped, a /20 sweep alone took two
  thirds of a standard scan).
* A sweep cut off by its budget keeps every host it reached: each one was
  recorded as it answered, and the phases after it run on those.
* A sweep whose pings ERRORED (no permission to send them, no ping command)
  is not an empty network, and the scan's warnings say so.
* Hosts scanned is what the sweep pings: the same subnets it expands
  (``network_scanner.sweep_size``), or the budget's cap.

``HostSweep`` is a mixin that ``DiscoveryEngine`` inherits, so every name
stays on the engine. From its host it needs ``config``, ``scan_status``,
``_budget``, ``_ping_method``, ``_scan_source_ip``, ``_set_phase()``, ``_apply_plan()``,
``_run_budgeted()``, ``_alive_ips()``, ``_get_or_create()``,
``_emit_device_update()`` and ``_update_intra_progress()``.
"""

from __future__ import annotations

import logging

from openavc.discovery import icmp
from openavc.discovery import scan_budget as budgeting
from openavc.discovery.network_scanner import ping_sweep, sweep_size
from openavc.discovery.scan_budget import PingPlan

# The discovery engine's own log, which the IDE's log view files these lines under.
log = logging.getLogger("discovery")

_NO_PING = (
    "Host scan could not run: this system has no permission "
    "to send pings and no ping command was found. Only "
    "devices that announce themselves can be discovered."
)
_OVERRUN = (
    "The host sweep ran out of its share of the scan time. Devices "
    "at the end of the range may be missing."
)


class HostSweep:
    """Phase 3: which addresses answer a ping."""

    async def _sweep_phase(self, subnets: list[str], gentle: bool) -> list[str]:
        """Sweep ``subnets`` from the scan's source address; the hosts that
        answered."""
        await self._set_phase(3, "ping_sweep", "Scanning for live hosts...")
        min_prefix = self.config.get("max_subnet_size", 20)
        ping_total = sweep_size(subnets, min_prefix)
        plan = budgeting.plan_ping_sweep(self._budget, ping_total, gentle)
        self._apply_plan(budgeting.PHASE_PING, plan.warnings)

        stats = icmp.PingSweepStats()
        alive_ips = await self._sweep(subnets, plan, min_prefix, stats)
        self.scan_status.total_hosts_scanned = (
            plan.max_hosts if plan.max_hosts is not None else ping_total
        )
        # Kept so the ARP phase can re-ping a short list without re-running
        # the selection probe.
        self._ping_method = stats.method
        self._warn_ping_failures(stats)
        if not alive_ips:
            log.info("No live hosts found — will still collect passive results")
        return alive_ips

    async def _sweep(
        self,
        subnets: list[str],
        plan: PingPlan,
        min_prefix: int,
        stats: icmp.PingSweepStats,
    ) -> list[str]:
        """Run the sweep under its budget. Cut short, the hosts it had reached
        are read back from ``results``, where each was recorded as it answered."""
        alive_ips: list[str] = []

        async def _do_ping_sweep() -> None:
            nonlocal alive_ips
            alive_ips = await ping_sweep(
                subnets,
                concurrency=plan.concurrency,
                on_found=self._on_ping_found,
                on_progress=self._on_ping_progress,
                min_prefix=min_prefix,
                source_ip=self._scan_source_ip,
                stats=stats,
                max_hosts=plan.max_hosts,
            )

        if not await self._run_budgeted(
            budgeting.PHASE_PING, plan.budget, _do_ping_sweep(), _OVERRUN,
        ):
            return self._alive_ips()
        return alive_ips

    async def _on_ping_found(self, ip: str) -> None:
        device = self._get_or_create(ip)
        device.alive = True
        await self._emit_device_update(device, "ping_sweep")

    async def _on_ping_progress(self, completed: int, total: int) -> None:
        if total > 0:
            await self._update_intra_progress(completed / total)

    def _warn_ping_failures(self, stats: icmp.PingSweepStats) -> None:
        """No way to ping at all, or pings that failed with system errors
        (not timeouts): either way the sweep saw less than the network."""
        if stats.method == icmp.METHOD_NONE:
            self.scan_status.warnings.append(_NO_PING)
        elif stats.errors:
            self.scan_status.warnings.append(
                f"Pings failed with system errors on {stats.errors} "
                f"of {stats.total} addresses (not timeouts) — "
                "results may be incomplete."
            )
