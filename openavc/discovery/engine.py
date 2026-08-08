"""Discovery engine — orchestrates all scanning methods."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket as _socket
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from openavc.discovery import icmp
from openavc.discovery.network_scanner import get_local_subnets, ping_sweep, harvest_arp_table, netbios_sweep
from openavc.discovery.port_scanner import scan_host_ports, grab_banners, BASELINE_PORTS
from openavc.discovery.oui_database import OUIDatabase
from openavc.discovery.hints import (
    DiscoveryHint,
    build_signal_index,
    load_discovery_hints,
)
from openavc.discovery.community_index import CommunityDevicesCache, CommunityIndexCache
from openavc.discovery.mdns_scanner import (
    BASELINE_SERVICE_TYPES,
    MDNSScanner,
)
from openavc.discovery.ssdp_scanner import SSDPScanner
from openavc.discovery.snmp_scanner import SNMPScanner
from openavc.discovery.amx_ddp_scanner import AMXDDPScanner
from openavc.discovery.probe_runner import (
    RateLimiter,
    run_tcp_active_probe,
    run_udp_broadcast_probe,
)
from openavc.discovery.companion import (
    CompanionProbe,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    ProbeContext,
    load_discovery_companions,
    run_companion,
)
from openavc.discovery.tier_matcher import (
    SignalIndex,
    TierMatcher,
    evidence_hostname,
    evidence_open_port,
    evidence_oui,
    extract_vendor_strings,
)
from openavc.discovery.result import (
    DiscoveredDevice,
    device_info_from_evidence,
    merge_device_info,
)
from openavc.discovery import scan_budget as budgeting
from openavc.discovery.scan_budget import ScanBudget

log = logging.getLogger("discovery")

# Flood guard for the aggregate results dict. Each passive listener already
# caps its own distinct sources per scan window (512 each in the mDNS, SSDP,
# and AMX-DDP scanners); this bound covers their sum with headroom and only
# counts NEW result entries created by passive/unsolicited traffic. Active
# results (a ping sweep on a /16 is legitimately thousands of hosts, port
# scans, driver probes) are never counted against it.
MAX_PASSIVE_RESULT_SOURCES = 2048


def _driver_version_tuple(version: object) -> tuple[int, int, int]:
    """Comparable (major, minor, patch) from a driver version string like
    ``'1.2.0'``. Unparseable or missing versions sort lowest, so a real catalog
    version supersedes a driver that declares no usable version. Stdlib-only
    (no regex) and never raises.
    """
    nums: list[int] = []
    for part in str(version or "").split(".")[:3]:
        digits = ""
        for ch in part.strip():
            if ch.isdigit():
                digits += ch
            else:
                break
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])

# Phase weights per scan depth — proportional to expected wall-clock time.
# Ensures the progress bar allocates more space to slower phases (ping sweep)
# and less to instant ones (subnet detection).  Must sum to 1.0.
PHASE_ORDER: dict[str, list[str]] = {
    "quick": [
        "subnet_detection", "passive_listen", "ping_sweep", "arp_harvest",
        "port_scan", "protocol_probe", "passive_collect", "finalize",
    ],
    "standard": [
        "subnet_detection", "passive_listen", "ping_sweep", "arp_harvest",
        "port_scan", "protocol_probe", "passive_collect", "finalize",
    ],
    "thorough": [
        "subnet_detection", "passive_listen", "ping_sweep", "arp_harvest",
        "port_scan", "protocol_probe", "passive_collect", "finalize",
    ],
}

PHASE_WEIGHTS: dict[str, dict[str, float]] = {
    "quick": {
        "subnet_detection": 0.02, "passive_listen": 0.02, "ping_sweep": 0.35,
        "arp_harvest": 0.10, "port_scan": 0.25, "protocol_probe": 0.18,
        "passive_collect": 0.03, "finalize": 0.05,
    },
    "standard": {
        "subnet_detection": 0.02, "passive_listen": 0.02, "ping_sweep": 0.25,
        "arp_harvest": 0.10, "port_scan": 0.22, "protocol_probe": 0.20,
        "passive_collect": 0.10, "finalize": 0.09,
    },
    "thorough": {
        "subnet_detection": 0.01, "passive_listen": 0.01, "ping_sweep": 0.18,
        "arp_harvest": 0.08, "port_scan": 0.25, "protocol_probe": 0.22,
        "passive_collect": 0.17, "finalize": 0.08,
    },
}

# Passive-collect heartbeat window. The listeners run in the background the
# whole scan; phase 7 waits for late mDNS/SSDP/AMX answers (and the SSDP XML
# fetches) before cancelling them. How long it waits is a depth policy
# (``DepthPolicy.passive_collect_seconds``), possibly shortened by the
# budget; the constant below is the fallback for callers that pass no wait,
# and is module-level so tests can shrink the otherwise-untestable 5–30s
# window.
_PASSIVE_COLLECT_TICK_SECONDS: float = 1.0
_PASSIVE_COLLECT_DEFAULT_WAIT_SECONDS: float = 15.0


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


class ScanStatus:
    """Tracks scan progress."""

    def __init__(self) -> None:
        self.scan_id: str = ""
        # idle, running, complete, cancelled, partial ("partial" = the scan hit
        # its time limit or errored, so the phases below ran on a subset of the
        # network; the accompanying warning says so).
        self.status: str = "idle"
        self.phase: str = ""
        self.phase_number: int = 0
        self.total_phases: int = 8
        self.message: str = ""
        self.progress: float = 0.0
        self.devices_found: int = 0
        self.started_at: float = 0.0
        self.duration: float = 0.0
        self.subnets: list[str] = []
        self.total_hosts_scanned: int = 0
        self.active_adapter: dict[str, str] | None = None
        # Environment problems that kept scan phases from working (missing
        # ping capability, multicast joins failing, ...) plus anything the
        # budget narrowed. Zero devices plus a reason beats zero devices
        # plus silence, and narrowed coverage must never read as full.
        self.warnings: list[str] = []
        # Ceiling on the whole scan: the request's explicit timeout, or the
        # scan_depth policy's when it supplied none.
        self.budget_seconds: float = 0.0
        # Deadline actually granted to each budgeted phase, derived from
        # that phase's measured workload (see discovery.scan_budget).
        self.phase_budgets: dict[str, float] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "status": self.status,
            "phase": self.phase,
            "phase_number": self.phase_number,
            "total_phases": self.total_phases,
            "message": self.message,
            "progress": round(self.progress, 2),
            "devices_found": self.devices_found,
            "started_at": self.started_at,
            "duration": round(self.duration, 2),
            "subnets": self.subnets,
            "total_hosts_scanned": self.total_hosts_scanned,
            "active_adapter": self.active_adapter,
            "warnings": list(self.warnings),
            "budget_seconds": round(self.budget_seconds, 2),
            "phase_budgets": dict(self.phase_budgets),
        }


def _ip_in_subnets(ip: str, subnets: list[str]) -> bool:
    """True if ``ip`` parses as an address inside one of ``subnets``.

    Keeps community Python discovery companions from fabricating device
    records for IPs outside the scanned ranges (see ``_run_custom_probes``).
    A malformed ``ip`` or CIDR is treated as "not in range" — the parse is
    guarded so one bad value can't raise into the probe path.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in subnets:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _broadcast_addresses_for(subnets: list[str]) -> list[str]:
    """Return the directed broadcast address for each CIDR.

    Skips invalid CIDRs and prefixes with no meaningful broadcast
    address (/31 and /32).
    """
    out: list[str] = []
    for cidr in subnets:
        try:
            net = ipaddress.IPv4Network(cidr, strict=False)
        except ValueError:
            continue
        if net.prefixlen >= 31:
            continue
        out.append(str(net.broadcast_address))
    return out


class DiscoveryEngine:
    """Orchestrates device discovery across all scanning methods."""

    def __init__(self) -> None:
        self.oui_db = OUIDatabase()
        self.discovery_hints: list[DiscoveryHint] = []
        self.signal_index: SignalIndex = SignalIndex()
        self.tier_matcher: TierMatcher = TierMatcher(self.signal_index)
        self._installed_registry: list[dict[str, Any]] = []
        # Driver-supplied Python discovery companions
        # ({driver_id: async probe}). Populated by
        # load_discovery_companions_from_dirs().
        self._discovery_companions: dict[str, CompanionProbe] = {}
        self.community_index = CommunityIndexCache()
        self.community_devices = CommunityDevicesCache()
        self.results: dict[str, DiscoveredDevice] = {}
        # Passive-source flood guard (see _get_or_create_passive).
        self._passive_sources_created = 0
        self._passive_cap_warned = False
        # Live passive listeners for the current scan, so their evidence can be
        # merged after a truncation as well as in phase 7 (see
        # merge_passive_results), plus the (source, ip) pairs already merged.
        self._passive_scanners: tuple[Any, Any, Any] | None = None
        self._passive_merged: set[tuple[str, str]] = set()
        # Per-phase budget for the current scan, and whether any phase
        # covered less than the user asked for (a narrowed scan reports
        # ``partial``, never ``complete``). Replaced on every start_scan.
        self._budget: ScanBudget = ScanBudget(
            budgeting.resolve_policy("standard"), budgeting.depth_total_seconds("standard"),
        )
        self._narrowed = False
        self.scan_status = ScanStatus()
        self._scan_task: asyncio.Task | None = None
        self._scan_lock = asyncio.Lock()
        self._on_update: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._scan_counter = 0
        # Discovery settings (persisted in project)
        self.config: dict[str, Any] = {
            "snmp_enabled": True,
            "snmp_community": "public",
            "gentle_mode": False,
            "scan_depth": "standard",
            "max_subnet_size": 20,  # Min CIDR prefix (/20 = ~4K hosts, /16 = ~65K)
        }

    def _get_control_interface(self) -> str:
        """Read the control_interface setting from system config."""
        from openavc.system_config import get_system_config
        return get_system_config().get("network", "control_interface") or ""

    def load_discovery_companions_from_dirs(
        self, directories: list[Path | str],
    ) -> None:
        """Scan directories for ``*_discovery.py`` companions.

        Replaces any previously-loaded companions. The engine invokes
        each loaded companion's ``probe()`` once per scan, alongside
        the declarative ``tcp_probe:`` / ``udp_probe:`` specs.
        """
        self._discovery_companions = load_discovery_companions(directories)
        log.info(
            "Loaded %d Python discovery companion(s)",
            len(self._discovery_companions),
        )

    def load_driver_hints_from_registry(self, registry: list[dict[str, Any]]) -> None:
        """Parse new-schema discovery hints + build the SignalIndex.

        Also enriches the OUI database with each driver's oui_prefixes
        so the ARP/OUI scan phase can attach a friendly vendor name
        before the matcher runs.
        """
        self._installed_registry = list(registry)
        self._rebuild_signal_index(community_drivers=[])

    async def refresh_signal_index_with_catalog(self) -> None:
        """Re-fold the community catalog into the SignalIndex.

        Discovery's job is to suggest what driver to install, so the
        catalog (un-installed drivers) must contribute rules just like
        the installed registry does. Installed wins on collisions.

        Called at scan start so a freshly-fetched catalog takes effect
        without a server restart.
        """
        try:
            community = await self.community_index.get_drivers()
        except Exception:
            log.warning("Could not fetch community catalog for SignalIndex; using installed only", exc_info=True)
            community = []
        self._rebuild_signal_index(community_drivers=community)

    def _rebuild_signal_index(
        self, community_drivers: list[dict[str, Any]],
    ) -> None:
        """Rebuild the SignalIndex from installed registry + community catalog.

        For a driver that's both installed and in the catalog, the NEWER copy
        is authoritative for discovery hints. The installed copy usually wins
        (it may carry local corrections), but a STALE install must not mask a
        newer catalog's improved fingerprints — otherwise a device that a clean
        machine would identify outright shows here only as a weak "possible"
        just because an older copy happens to be installed. Catalog drivers with
        no installed counterpart fill in coverage for devices not yet installed
        — that's how discovery surfaces "Install & Add" candidates.
        """
        installed_by_id: dict[str, dict[str, Any]] = {
            str(d.get("id") or ""): d
            for d in self._installed_registry
            if d.get("id")
        }

        catalog_only: list[dict[str, Any]] = []
        superseded_installed_ids: set[str] = set()
        for d in community_drivers:
            # The cache filters non-object entries at the fetch boundary; guard
            # here too so a malformed element from any caller degrades this one
            # rule rather than aborting the whole scan.
            if not isinstance(d, dict):
                continue
            cid = str(d.get("id") or "")
            if not cid:
                continue
            installed = installed_by_id.get(cid)
            if installed is None:
                catalog_only.append(d)  # not installed — catalog covers it
            elif _driver_version_tuple(d.get("version")) > _driver_version_tuple(
                installed.get("version")
            ):
                # Catalog is newer than the installed copy: its (improved)
                # fingerprints win, and the stale installed hints are dropped
                # so they can't shadow the catalog's stronger signal.
                catalog_only.append(d)
                superseded_installed_ids.add(cid)

        installed_kept = [
            d for d in self._installed_registry
            if str(d.get("id") or "") not in superseded_installed_ids
        ]
        installed_hints = load_discovery_hints(installed_kept)
        community_hints = load_discovery_hints(catalog_only)

        all_hints = installed_hints + community_hints
        self.discovery_hints = all_hints

        try:
            self.signal_index = build_signal_index(all_hints)
        except ValueError as exc:
            # build_signal_index isolates colliding rules itself (drops
            # the rule, keeps the rest), so this only fires on something
            # unexpected. Installed-only is the last line of defense.
            log.error("Discovery signal index rejected with catalog: %s; falling back to installed-only", exc)
            try:
                self.signal_index = build_signal_index(installed_hints)
            except ValueError as exc2:
                log.error("Installed-only signal index also rejected: %s", exc2)
                self.signal_index = SignalIndex()
        self.tier_matcher = TierMatcher(self.signal_index)

        added = 0
        for hint in all_hints:
            for prefix in hint.oui:
                before = len(self.oui_db._table)
                self.oui_db.add_prefix(prefix, hint.manufacturer, hint.category)
                if len(self.oui_db._table) > before:
                    added += 1

        log.info(
            "Discovery loaded %d hint(s) (%d installed + %d catalog); "
            "signal index covers %d driver(s); %d new OUI prefixes",
            len(all_hints), len(installed_hints), len(community_hints),
            self.signal_index.driver_count(), added,
        )

    def get_results(self) -> list[dict[str, Any]]:
        """Return current discovery results sorted identified > possible > unknown."""
        # State-then-IP ordering: identified first, then possible, then
        # unknown. Within each bucket, sort by IP for stable display.
        state_rank = {"identified": 0, "possible": 1, "unknown": 2}

        def sort_key(d: DiscoveredDevice) -> tuple:
            state = d.identification.state.value if d.identification else "unknown"
            try:
                ip_tuple = tuple(int(p) for p in d.ip.split("."))
            except ValueError:
                ip_tuple = (999, 999, 999, 999)
            return (state_rank.get(state, 9), ip_tuple)

        devices = sorted(self.results.values(), key=sort_key)
        return [d.to_dict() for d in devices]

    def get_status(self) -> dict[str, Any]:
        return self.scan_status.to_dict()

    def get_subnets(self) -> list[str]:
        """Auto-detect local subnets (filtered to control interface if set)."""
        control_ip = self._get_control_interface()
        return get_local_subnets(interface_ip=control_ip or None)

    def clear_results(self) -> None:
        """Clear all discovery results."""
        self.results.clear()
        self.scan_status = ScanStatus()

    async def refresh_device_matches(self, ip: str) -> dict[str, Any] | None:
        """Re-run TierMatcher.match() for a single device.

        Used after installing a community driver so the device card
        updates without a full rescan. The evidence_log is preserved
        from the original scan; only the identification result changes
        when a new driver hint claims one of the existing signals.
        """
        device = self.results.get(ip)
        if not device:
            return None

        device.identification = self.tier_matcher.match(device.evidence_log)
        return device.to_dict()

    async def start_scan(
        self,
        subnets: list[str] | None = None,
        extra_subnets: list[str] | None = None,
        on_update: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        timeout: float | None = None,
        ignore_control_interface: bool = False,
    ) -> str:
        """Start a discovery scan. Returns scan_id.

        Args:
            subnets: CIDR ranges to scan. Auto-detect if None.
            extra_subnets: Additional ranges to scan alongside auto-detected ones.
            ignore_control_interface: Run THIS scan as if no control interface
                were pinned. The pin is both a subnet filter and the source
                address the scanners bind, so when its adapter is gone every
                probe fails at the socket even with explicit subnets. This is
                the one-off escape hatch the Discovery tab offers for a stale
                pin; the setting itself is untouched.
            on_update: Async callback for live WebSocket push.
            timeout: Ceiling on the whole scan, in seconds. ``None`` takes the
                ceiling from the ``scan_depth`` policy — the normal path, since
                each phase is separately budgeted from its own workload and the
                ceiling is a backstop rather than a target. An explicit value
                still caps the scan, and the phases divide it by share instead
                of the early ones spending it all.
        """
        async with self._scan_lock:
            if self._scan_task and not self._scan_task.done():
                raise RuntimeError("Scan already running")

            self._scan_counter += 1
            scan_id = f"scan_{self._scan_counter}_{int(time.time())}"

            # Determine target subnets (filtered to control interface if set)
            control_ip = "" if ignore_control_interface else self._get_control_interface()
            if ignore_control_interface:
                log.info("Scan requested with the control-interface pin ignored")
            elif control_ip:
                log.info("Control interface set to %s — filtering subnets to this adapter", control_ip)
            else:
                log.info("No control interface set — scanning all physical adapters")
            targets = subnets if subnets else get_local_subnets(
                interface_ip=control_ip or None
            )
            if extra_subnets:
                for s in extra_subnets:
                    if s not in targets:
                        targets.append(s)

            log.info("Discovery target subnets: %s", targets)

            if not targets:
                # A pinned control interface that no longer exists is the usual
                # culprit here (the box moved networks since it was set), and
                # the generic message sent one integrator hunting a phantom
                # subnet — name the pin and where to clear it.
                if control_ip and not subnets:
                    raise ValueError(
                        f"Control interface {control_ip} is set in Settings > "
                        "Network but no adapter on this machine has that "
                        "address. Pick a current adapter there (or Auto), or "
                        "specify subnets manually."
                    )
                raise ValueError("No subnets to scan. Specify subnets manually.")

            depth = self.config.get("scan_depth", "standard")
            gentle = bool(self.config.get("gentle_mode", False))
            policy = budgeting.resolve_policy(depth, gentle)
            total_seconds = (
                float(timeout) if timeout is not None else policy.total_seconds
            )

            self._on_update = on_update
            self._scan_ignores_pin = ignore_control_interface
            self.scan_status = ScanStatus()
            self.scan_status.scan_id = scan_id
            self.scan_status.status = "running"
            self.scan_status.started_at = time.time()
            self.scan_status.subnets = targets
            self.scan_status.budget_seconds = total_seconds

            # Store active adapter info for UI display
            if control_ip:
                from openavc.discovery.network_scanner import get_network_adapters
                for adapter in get_network_adapters():
                    if adapter["ip"] == control_ip:
                        self.scan_status.active_adapter = {
                            "name": adapter["name"],
                            "ip": adapter["ip"],
                            "subnet": adapter["subnet"],
                        }
                        break

            # Mark previously found devices as stale — they'll be removed at the
            # end of the scan if not re-discovered.  This gives the full pipeline
            # (ping, port scan, probes, SSDP, mDNS) a chance to re-find them
            # before we clean up.
            #
            # Reset each device's per-scan observations too. The evidence_log,
            # open ports, and banners are re-derived from scratch every scan;
            # without this they accumulate across re-scans — the evidence_log
            # grows unbounded (memory, plus an O(n) match() cost that climbs
            # every scan) and finalize re-appends an identical open-port
            # enrichment record per scan, piling up duplicate rows in the
            # "Why?" reveal. Identity fields stay (they're re-merged from the
            # fresh evidence) so the card doesn't blank mid-scan.
            for device in self.results.values():
                device.alive = False
                device.evidence_log.clear()
                device.open_ports.clear()
                device.banners.clear()

            self._passive_sources_created = 0
            self._passive_cap_warned = False
            self._passive_scanners = None
            self._passive_merged.clear()
            self._budget = ScanBudget(policy, total_seconds)
            self._narrowed = False

            self._scan_task = asyncio.create_task(
                self._run_scan(targets, total_seconds)
            )
            return scan_id

    async def stop_scan(self) -> None:
        """Cancel a running scan."""
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self.scan_status.status = "cancelled"
            self.scan_status.duration = time.time() - self.scan_status.started_at

    async def _run_scan(self, subnets: list[str], timeout: float) -> None:
        """Execute the full scan pipeline.

        The deadline covers the network-bound phases (1-7) only. Phase 8 — the
        matcher — runs afterwards, unbudgeted, because it is CPU-only over
        evidence already in memory and is what turns a scan into answers. A
        truncated scan therefore still identifies everything it managed to
        observe, and reports itself ``partial`` rather than ``complete`` so a
        client can tell "we ran out of time" from "the network was empty".

        This outer deadline is a backstop, not the mechanism. Each phase runs
        under its own budget derived from its own workload (see
        ``discovery.scan_budget``), and the phase budgets are capped so their
        sum stays inside this one. Reaching here means the per-phase model
        under-estimated the network badly enough that even the reserve ran
        out.
        """
        truncated = False
        cancelled = False
        try:
            await asyncio.wait_for(self._scan_pipeline(subnets), timeout=timeout)
        except asyncio.TimeoutError:
            truncated = True
            phase = self.scan_status.phase or "startup"
            log.warning("Scan timed out after %.0fs during phase %s", timeout, phase)
            self.scan_status.warnings.append(
                f"Scan stopped at its {timeout:.0f}s limit during "
                f"\"{self.scan_status.message or phase}\". Devices found after that "
                "point are missing. Scan a smaller subnet, or raise the time limit, "
                "for complete results."
            )
        except asyncio.CancelledError:
            cancelled = True
            log.info("Scan cancelled")
            raise
        except Exception:  # Catch-all: isolates any scan pipeline error to ensure cleanup runs
            truncated = True
            log.exception("Scan failed")
            self.scan_status.warnings.append(
                "Scan ended early after an unexpected error. Results are incomplete "
                "— see the server log for details."
            )
        finally:
            # Match drivers against whatever evidence was collected, even when
            # the phases above were cut short. Skipped only on an explicit
            # cancel, where the user asked for no further work.
            if not cancelled:
                # Recover the passive listeners' evidence first. On a truncated
                # scan phase 7 never collected them and the pipeline's finally
                # has since cancelled their tasks, so this is the only chance to
                # merge announcements that were already received and parsed —
                # and it must happen before matching, not after, or the matcher
                # runs without them. Idempotent, so the normal path (where phase
                # 7 already merged) is a no-op.
                if truncated:
                    try:
                        recovered = await self.merge_passive_results()
                        if recovered:
                            log.info(
                                "Recovered %d passive result(s) from a truncated scan",
                                recovered,
                            )
                    except Exception:
                        log.exception("Passive result recovery failed")
                try:
                    await self._finalize_scan()
                except Exception:
                    log.exception("Driver matching failed")
                    self.scan_status.warnings.append(
                        "Driver matching failed — devices were found but could not "
                        "be identified. See the server log for details."
                    )
            # A narrowed scan is partial too: the budget dropped work before
            # running it, so the results cover less of the network than was
            # asked for. Reporting that as "complete" is the silent-cap
            # failure the warnings exist to prevent.
            self.scan_status.status = (
                "cancelled" if self.scan_status.status == "cancelled"
                else "partial" if (truncated or self._narrowed)
                else "complete"
            )
            self.scan_status.duration = time.time() - self.scan_status.started_at
            self.scan_status.devices_found = len(self.results)
            await self._emit({
                "type": "discovery.complete",
                "scan_id": self.scan_status.scan_id,
                "status": self.scan_status.status,
                "total_devices": len(self.results),
                "duration_seconds": self.scan_status.duration,
                "warnings": list(self.scan_status.warnings),
            })

    async def _scan_pipeline(self, subnets: list[str]) -> None:
        # Refresh the SignalIndex with the latest community catalog so
        # un-installed drivers contribute deterministic rules — that's
        # how discovery proposes what to install.
        await self.refresh_signal_index_with_catalog()
        return await self._scan_pipeline_inner(subnets)

    async def _scan_pipeline_inner(self, subnets: list[str]) -> None:
        """The core scan phases.

        Phase layout:
          1: Subnet detection (already done)
          2: Start passive listeners (mDNS + SSDP + AMX DDP)
          3: Ping sweep
          4: ARP harvest + OUI lookup + hostname resolution
          5: Port scan + banner grab
          6: Launch SNMP scan in the background
          7: Collect passive listener results + SNMP results, port-scan
             passive-only hosts, then run driver-declared probes (UDP
             broadcasts, TCP probes, Python companions)
          8: Run the deterministic matcher per device + finalize —
             NOT part of this coroutine. It lives in _finalize_scan and is
             driven by _run_scan outside the scan deadline, so a scan that
             runs out of time still identifies what it observed.

        Every signal-producing phase appends ``Evidence`` records to the
        device's ``evidence_log``. The matcher runs once at finalize and
        produces an ``IdentificationMatch`` — identified, possible, or
        unknown — based purely on those records.
        """
        gentle = bool(self.config.get("gentle_mode", False))
        policy = self._budget.policy
        snmp_enabled = self.config.get("snmp_enabled", True)
        snmp_community = self.config.get("snmp_community", "public")
        control_ip = "" if getattr(self, "_scan_ignores_pin", False) else self._get_control_interface()

        # --- Phase 1: Subnet Detection (already done) ---
        await self._set_phase(1, "subnet_detection", "Detecting network interfaces...")

        # --- Phase 2: Start Passive Listeners (background) ---
        await self._set_phase(2, "passive_listen", "Starting mDNS, SSDP, and AMX DDP listeners...")

        # mDNS service types come from loaded drivers' mdns: declarations
        # plus a small consumer baseline. The DNS-SD meta-query is
        # always added by the scanner so unknown types surface for
        # catalog growth.
        driver_service_types: list[str] = []
        for hint in self.discovery_hints:
            for fp in hint.mdns:
                if fp.service:
                    driver_service_types.append(fp.service)
        mdns_service_types = list(BASELINE_SERVICE_TYPES) + driver_service_types

        mdns_scanner = MDNSScanner(service_types=mdns_service_types, control_ip=control_ip)
        ssdp_scanner = SSDPScanner(control_ip=control_ip)
        amx_ddp_scanner = AMXDDPScanner(control_ip=control_ip)

        # Reachable from _run_scan so a truncated scan can still merge what the
        # listeners heard — their tasks get cancelled below, which zeroes the
        # task results but not the scanners' own accumulated state.
        self._passive_scanners = (mdns_scanner, ssdp_scanner, amx_ddp_scanner)

        # Passive listeners run throughout all active scan phases and are
        # stopped explicitly in phase 7 when we're ready to collect.
        # The 600s cap is a safety net; they'll be stopped much sooner.
        # Created inside the try so the finally cleans them up on EVERY exit
        # path — see _cancel_background_tasks for why that matters.
        mdns_task: asyncio.Task | None = None
        ssdp_task: asyncio.Task | None = None
        amx_ddp_task: asyncio.Task | None = None
        snmp_task: asyncio.Task | None = None
        snmp_scanner: SNMPScanner | None = None

        try:
            mdns_task = asyncio.create_task(mdns_scanner.start(duration=600.0))
            ssdp_task = asyncio.create_task(ssdp_scanner.scan(
                timeout=600.0, fetch_descriptions=True,
            ))
            amx_ddp_task = asyncio.create_task(amx_ddp_scanner.start(duration=600.0))

            # --- Phase 3: Ping Sweep ---
            await self._set_phase(3, "ping_sweep", "Scanning for live hosts...")

            async def on_ping_found(ip: str) -> None:
                device = self._get_or_create(ip)
                device.alive = True
                await self._emit_device_update(device, "ping_sweep")

            # Track per-host ping progress for smooth progress bar. Count only
            # the hosts ping_sweep will actually probe: skip malformed CIDRs
            # (one bad subnet must not abort the whole scan — every other
            # subnet-parse site is guarded) and skip subnets larger than the
            # configured limit (ping_sweep skips those via min_prefix too), so
            # total_hosts_scanned matches what was scanned instead of
            # over-counting skipped ranges.
            min_prefix = self.config.get("max_subnet_size", 20)
            ping_total = 0
            for s in subnets:
                try:
                    net = ipaddress.IPv4Network(s, strict=False)
                except ValueError:
                    continue
                if net.prefixlen < min_prefix:
                    continue
                ping_total += max(0, net.num_addresses - 2)
            ping_done = 0

            async def on_ping_progress(completed: int, total: int) -> None:
                nonlocal ping_done
                ping_done = completed
                if total > 0:
                    await self._update_intra_progress(completed / total)

            # Budget phase 3 from the address count — the one phase whose
            # cost is fully knowable before it runs. Capping it here is what
            # keeps it from spending the allowance the identification phases
            # need; without the cap a /20 sweep alone ate two thirds of a
            # standard scan.
            ping_plan = budgeting.plan_ping_sweep(self._budget, ping_total, gentle)
            self._apply_plan(budgeting.PHASE_PING, ping_plan.warnings)

            ping_stats = icmp.PingSweepStats()
            alive_ips: list[str] = []

            async def _do_ping_sweep() -> None:
                nonlocal alive_ips
                alive_ips = await ping_sweep(
                    subnets,
                    concurrency=ping_plan.concurrency,
                    on_found=on_ping_found,
                    on_progress=on_ping_progress,
                    min_prefix=min_prefix,
                    source_ip=control_ip,
                    stats=ping_stats,
                    max_hosts=ping_plan.max_hosts,
                )

            if not await self._run_budgeted(
                budgeting.PHASE_PING, ping_plan.budget, _do_ping_sweep(),
                "The host sweep ran out of its share of the scan time. Devices "
                "at the end of the range may be missing.",
            ):
                # The sweep was cut mid-flight. Every host it had already
                # reached is in ``results`` via on_ping_found, so recover the
                # alive list from there rather than losing the phase entirely
                # — the phases that identify those hosts still have their own
                # budgets to spend.
                alive_ips = self._alive_ips()

            self.scan_status.total_hosts_scanned = (
                ping_plan.max_hosts if ping_plan.max_hosts is not None else ping_total
            )

            # Environment failures must be loud: a sweep where pings
            # ERRORED (no ICMP permission, no ping binary, exec failures)
            # is not an empty network, and the UI must say so.
            if ping_stats.method == icmp.METHOD_NONE:
                self.scan_status.warnings.append(
                    "Host scan could not run: this system has no permission "
                    "to send pings and no ping command was found. Only "
                    "devices that announce themselves can be discovered."
                )
            elif ping_stats.errors:
                self.scan_status.warnings.append(
                    f"Pings failed with system errors on {ping_stats.errors} "
                    f"of {ping_stats.total} addresses (not timeouts) — "
                    "results may be incomplete."
                )

            if not alive_ips:
                log.info("No live hosts found — will still collect passive results")
                # Don't return — passive listeners may have found devices

            # --- Phase 4: ARP Harvest + OUI Lookup + Hostname Resolution ---
            await self._set_phase(4, "arp_harvest", "Reading MAC addresses and hostnames...")

            if alive_ips:
                # Budget phase 4 from the alive count — the pivot the plan
                # turns on, and the first number the scan knows that actually
                # predicts what everything downstream costs.
                arp_plan = budgeting.plan_arp_phase(
                    self._budget, len(alive_ips), policy.netbios,
                )
                self._apply_plan(budgeting.PHASE_ARP, arp_plan.warnings)
                await self._run_budgeted(
                    budgeting.PHASE_ARP, arp_plan.budget,
                    self._arp_phase(alive_ips, arp_plan),
                    "Reading MAC addresses and hostnames ran out of its share "
                    "of the scan time. Some devices are missing their vendor "
                    "or hostname.",
                )

            # --- Phase 5: Port Scan ---
            await self._set_phase(5, "port_scan", "Probing device ports...")

            # Build the scan list at runtime: every loaded driver
            # contributes the port it declares for ``tcp_probe:`` and
            # ``port_open:`` hints; the community catalog contributes
            # ports of un-installed drivers so a known-but-uninstalled
            # device still surfaces with its open ports in the UI; and
            # a small generic baseline (SSH, Telnet, HTTP/HTTPS,
            # alt-HTTP) covers banner reading and web management for
            # devices we don't yet have a driver for.
            port_set: set[int] = set(BASELINE_PORTS)

            for hint in self.discovery_hints:
                if hint.tcp_probe is not None:
                    port_set.add(hint.tcp_probe.port)
                for p in hint.port_open:
                    port_set.add(p)

            # The AV-critical subset the budget narrows to when the full list
            # won't fit: the universal baseline plus the ports of drivers
            # actually installed here. Everything past this point is coverage
            # for devices no installed driver claims.
            core_port_list = sorted(port_set)

            community_drivers = await self.community_index.get_drivers()
            for drv in community_drivers:
                for p in drv.get("ports", []):
                    if isinstance(p, int):
                        port_set.add(p)

            # Thorough mode: add a handful of generic extended ports.
            # No vendor labels — these are common alternate web / RTSP
            # / management ports.
            if policy.extended_ports:
                port_set.update([554, 3000, 4000, 5060, 8443, 8888, 9000, 10000])

            port_list = sorted(port_set)
            log.info(
                "Port scan: %d ports (%d baseline + driver/catalog)",
                len(port_list), len(BASELINE_PORTS),
            )

            if alive_ips:
                # Budget phase 5 from alive x ports. Ports narrow before
                # hosts: dropping a port costs one signal on every device,
                # dropping a host costs every signal on one device.
                port_plan = budgeting.plan_port_scan(
                    self._budget,
                    len(alive_ips),
                    port_list,
                    core_port_list,
                    policy.port_host_concurrency,
                )
                self._apply_plan(budgeting.PHASE_PORT, port_plan.warnings)
                port_list = port_plan.ports
                targets = (
                    alive_ips[: port_plan.host_limit]
                    if port_plan.host_limit is not None
                    else alive_ips
                )
                await self._run_budgeted(
                    budgeting.PHASE_PORT, port_plan.budget,
                    self._port_scan_hosts(
                        targets, port_list, port_plan.concurrency, gentle,
                    ),
                    "The port scan ran out of its share of the scan time. Some "
                    "devices are missing their open-port signal.",
                )

            # --- Phase 6: SNMP launch ---
            #
            # Driver-declared probes (UDP broadcasts + TCP active probes
            # + Python companions) used to run here, but they need the
            # full host inventory (passive-only mDNS/SSDP devices
            # included) to land probes on every reachable target. They
            # now run during phase 7 after passive collection — see
            # ``_run_custom_probes`` below. SNMP still kicks off here in
            # the background so its 5-second wait overlaps with the
            # passive-listener collection window.
            await self._set_phase(6, "protocol_probe", "Identifying device protocols...")

            if snmp_enabled and alive_ips:
                snmp_scanner = SNMPScanner()
                snmp_concurrency = 10 if gentle else 20
                snmp_task = asyncio.create_task(
                    snmp_scanner.scan_devices(
                        alive_ips,
                        community=snmp_community,
                        timeout=2.0,
                        concurrency=snmp_concurrency,
                        entity_mib=policy.snmp_entity_mib,
                    )
                )

            # --- Phase 7: Collect Passive + SNMP Results ---
            await self._set_phase(7, "passive_collect", "Collecting passive and SNMP results...")

            # Signal passive listeners to stop. They exit their receive
            # loops within 0.5s; SSDP then fetches UPnP XML descriptions
            # for everything it found before the task completes.
            mdns_scanner._running = False
            ssdp_scanner._running = False
            await amx_ddp_scanner.stop()

            # The collect half of phase 7 narrows by waiting less rather than
            # by dropping work — the announcements it gathers are the
            # strongest identification signal a scan gets, so shortening the
            # window beats skipping it.
            collect_plan = budgeting.plan_passive_collect(self._budget)
            self._apply_plan(budgeting.PHASE_PASSIVE, collect_plan.warnings)
            await self._run_budgeted(
                budgeting.PHASE_PASSIVE, collect_plan.budget,
                self._collect_phase(
                    mdns_task, ssdp_task, amx_ddp_task, snmp_task, snmp_scanner,
                    collect_plan.wait_seconds, alive_ips, port_list,
                    policy.port_host_concurrency, gentle,
                ),
                "Collecting device announcements ran out of its share of the "
                "scan time. Some announcements may be missing.",
            )

            # Surface listener environment failures (multicast join failed
            # on every interface, socket unavailable, ...) as scan warnings
            # — a silent {} from a listener that never ran reads as "no
            # devices on the network", which is the wrong story.
            for listener in (mdns_scanner, ssdp_scanner, amx_ddp_scanner):
                if listener.env_error:
                    self.scan_status.warnings.append(listener.env_error)

            # Driver-declared probes run after the full host inventory is
            # known — UDP broadcasts, TCP probes against every host whose
            # port-scan results include the spec port, and Python
            # companions. Evidence lands in each device's evidence_log
            # before the matcher runs in phase 8.
            #
            # This is the phase the old flat stopwatch always starved: its
            # cost is the least predictable of any (it depends on how many
            # hosts turned out to have a driver's port open) and it runs
            # last. Its budget is reserved up front, which is the whole
            # point of budgeting by share rather than in pipeline order.
            tcp_jobs, udp_sends, companions = self._count_probe_jobs(subnets)
            probe_plan = budgeting.plan_probes(
                self._budget, tcp_jobs, udp_sends, companions,
            )
            self._apply_plan(budgeting.PHASE_PROBE, probe_plan.warnings)
            await self._run_budgeted(
                budgeting.PHASE_PROBE, probe_plan.budget,
                self._run_custom_probes(
                    subnets, control_ip, max_tcp_jobs=probe_plan.max_tcp_jobs,
                ),
                "Driver probes ran out of their share of the scan time. Some "
                "devices may show as possible rather than identified.",
            )

            # Phase 8 (the matcher) deliberately does NOT run here — it runs
            # in _run_scan, outside the scan deadline. See _finalize_scan.

        finally:
            # Release the passive listeners (each holds a bound UDP socket on
            # 5353/1900/9131 plus a blocked recvfrom executor thread) and the
            # SNMP scanner on EVERY exit path. On the success path phase 7
            # already collected them (done -> no-op); on a timeout, a cancel,
            # or ANY pipeline error this finally is the only thing that stops
            # their 600s loops, so the sockets/threads don't leak into the next
            # scan that binds the same SO_REUSEADDR ports.
            await self._cancel_background_tasks(
                mdns_task, ssdp_task, amx_ddp_task, snmp_task,
            )

    def _alive_ips(self) -> list[str]:
        """Hosts marked alive so far this scan, in address order.

        The ping sweep's return value is lost when its budget cuts it off, but
        every host it reached was already recorded through ``on_ping_found``.
        Reading them back here is what lets the identification phases run on a
        narrowed sweep instead of the whole scan collapsing with it.
        """
        def _key(ip: str) -> tuple:
            try:
                return (0, ipaddress.IPv4Address(ip))
            except ValueError:
                return (1, ip)

        return sorted(
            (ip for ip, dev in self.results.items() if dev.alive), key=_key,
        )

    async def _arp_phase(self, alive_ips: list[str], plan) -> None:
        """Phase 4 body: ARP table read, OUI lookup, reverse DNS + NetBIOS.

        The ARP read always covers every alive host — it is one cheap exec and
        the phase's strongest signal (MAC -> OUI -> vendor). Only the name
        lookups are bounded by ``plan.name_lookup_hosts``, because hostnames
        feed ``discovery.hostname`` driver hints and are worth bounding rather
        than dropping.
        """
        arp_table = await harvest_arp_table()

        named = (
            alive_ips
            if plan.name_lookup_hosts is None
            else alive_ips[: plan.name_lookup_hosts]
        )

        # Resolve hostnames + NetBIOS concurrently (best-effort)
        hostname_task = asyncio.create_task(_resolve_hostnames(named))
        netbios_task = None
        if plan.netbios and named:
            netbios_task = asyncio.create_task(
                netbios_sweep(named, concurrency=30, timeout=1.0)
            )

        hostnames = await hostname_task
        netbios_results: dict[str, dict[str, str]] = {}
        if netbios_task:
            try:
                netbios_results = await netbios_task
            except Exception:
                log.debug("NetBIOS sweep failed", exc_info=True)

        for ip in alive_ips:
            device = self._get_or_create(ip)
            info: dict[str, Any] = {}

            # Hostname from reverse DNS / NetBIOS — both feed
            # the hostname enrichment evidence record.
            hostname = hostnames.get(ip)
            if hostname:
                info["hostname"] = hostname

            nbt = netbios_results.get(ip)
            if nbt:
                nbt_name = nbt.get("hostname")
                if nbt_name:
                    info["device_name"] = nbt_name
                    if not hostname:
                        info["hostname"] = nbt_name
                        hostname = nbt_name

            if hostname:
                # One evidence record per matching driver pattern
                # so the "Why?" reveal can render the specific
                # regex that fired. If no driver pattern matches,
                # emit a single bare record as audit trail.
                matched_patterns = self.signal_index.matched_hostname_patterns(hostname)
                if matched_patterns:
                    for pat in matched_patterns:
                        device.evidence_log.append(
                            evidence_hostname(hostname, matched_pattern=pat)
                        )
                else:
                    device.evidence_log.append(evidence_hostname(hostname))

            # MAC + OUI: lookup keeps the friendly vendor name
            # visible in the UI; the matcher consumes the OUI
            # enrichment evidence record instead.
            mac = arp_table.get(ip)
            if mac:
                info["mac"] = mac
                oui_result = self.oui_db.lookup(mac)
                oui_vendor = None
                if oui_result:
                    manufacturer, category = oui_result
                    info["manufacturer"] = manufacturer
                    info["category"] = category
                    oui_vendor = manufacturer
                device.evidence_log.append(evidence_oui(mac, vendor=oui_vendor))

            if info:
                # Split the merge by trust. manufacturer/category
                # come from the OUI registry, which names the NIC
                # vendor (often not the device vendor) — those only
                # fill fields nothing else populated. mac/hostname/
                # device_name are this scan's own ARP / reverse-DNS
                # / NetBIOS reads and keep the standard merge rule,
                # so a re-scan can refresh a renamed or reassigned
                # host instead of showing the previous occupant
                # forever.
                oui_info = {
                    k: info.pop(k)
                    for k in ("manufacturer", "category")
                    if k in info
                }
                if info:
                    merge_device_info(device, info, "arp")
                if oui_info:
                    merge_device_info(device, oui_info, "arp", fill_only=True)
                await self._emit_device_update(device, "arp_harvest")

    async def _port_scan_hosts(
        self,
        hosts: list[str],
        ports: list[int],
        concurrency: int,
        gentle: bool,
        phase: str = "port_scan",
        always_emit: bool = False,
    ) -> None:
        """Port-scan a set of hosts, ``concurrency`` of them at a time.

        Hosts used to be scanned strictly one after another, which made this
        phase cost ``alive x per-host`` with no way to trade parallelism for
        time — the budget formula's ``effective_concurrency`` term was
        silently 1. Each host's own port probes were already concurrent and
        staggered, so overlapping a bounded number of hosts adds breadth
        without changing what any single device sees. Gentle mode keeps its
        per-host pause and a much lower width.
        """
        total = len(hosts)
        if not total or not ports:
            return
        semaphore = asyncio.Semaphore(max(1, concurrency))
        done = 0

        async def _scan_one(ip: str) -> None:
            nonlocal done
            async with semaphore:
                device = self._get_or_create(ip)
                open_ports = await scan_host_ports(ip, ports, timeout=1.0)
                if open_ports:
                    merge_device_info(device, {"open_ports": open_ports}, "port_scan")

                    # Grab banners from banner-friendly ports.
                    banners = await grab_banners(ip, open_ports, timeout=2.0)
                    if banners:
                        merge_device_info(device, {"banners": banners}, "banner")

                if open_ports or always_emit:
                    await self._emit_device_update(device, phase)

                # Gentle mode: small delay before this slot takes another host
                if gentle:
                    await asyncio.sleep(0.1)

            done += 1
            await self._update_intra_progress(done / total)

        await asyncio.gather(*[_scan_one(ip) for ip in hosts])

    async def _collect_phase(
        self,
        mdns_task: asyncio.Task,
        ssdp_task: asyncio.Task,
        amx_ddp_task: asyncio.Task,
        snmp_task: asyncio.Task | None,
        snmp_scanner: "SNMPScanner | None",
        passive_wait: float,
        alive_ips: list[str],
        port_list: list[int],
        concurrency: int,
        gentle: bool,
    ) -> None:
        """Phase 7's collection half: gather everything already observed.

        Passive announcements, SNMP, a port-scan follow-up for hosts only the
        listeners saw, and a second ARP read for the MACs that follow-up just
        put in the cache. All of it feeds the probe pass that comes next.
        """
        await self._collect_passive_results(
            mdns_task, ssdp_task, amx_ddp_task, wait_seconds=passive_wait,
        )
        await self._collect_snmp_results(snmp_task, snmp_scanner)

        # Follow-up: port scan devices found only by passive discovery
        # (mDNS/SSDP/AMX-DDP) that weren't in the ping sweep, so the
        # custom-probe pass sees their open ports.
        ping_found = set(alive_ips) if alive_ips else set()
        passive_only = [
            ip for ip, dev in self.results.items()
            if dev.alive and ip not in ping_found and not dev.open_ports
        ]
        if passive_only:
            log.info(
                "Port scanning %d passive-only devices (mDNS/SSDP)",
                len(passive_only),
            )
            await self._port_scan_hosts(
                passive_only, port_list, concurrency, gentle,
                phase="passive_followup", always_emit=True,
            )

        # Pick up a MAC + OUI for passive-discovered devices the phase-4
        # ARP harvest missed (found via mDNS/SSDP on a ping-skipped subnet).
        await self._late_arp_harvest()

    def _count_probe_jobs(self, subnets: list[str]) -> tuple[int, int, int]:
        """(tcp probe jobs, udp broadcast sends, companions) for this scan.

        Counted before ``_run_custom_probes`` runs so the phase can be budgeted
        from real work rather than a guess. The TCP expression mirrors the job
        list that method builds — same spec/port test, so the projection and
        the execution can't disagree about what the phase is about to do.
        """
        broadcasts = len(_broadcast_addresses_for(subnets))
        udp_sends = sum(
            broadcasts for h in self.discovery_hints if h.udp_probe is not None
        )
        tcp_jobs = 0
        for hint in self.discovery_hints:
            spec = hint.tcp_probe
            if spec is None:
                continue
            tcp_jobs += sum(
                1 for dev in self.results.values()
                if spec.port in (dev.open_ports or [])
            )
        return tcp_jobs, udp_sends, len(self._discovery_companions)

    def _apply_plan(self, key: str, warnings: list[str]) -> None:
        """Record a phase plan: its granted deadline, and any narrowing.

        A narrowing warning is the contract from §5 of the budget plan — work
        dropped before a phase starts must be named, never silently omitted —
        and it also flips the scan to ``partial``, because results that cover
        less than the request asked for are not a complete scan.
        """
        granted = self._budget.grants.get(key)
        if granted is not None:
            self.scan_status.phase_budgets[key] = round(granted, 2)
        for warning in warnings:
            self._narrowed = True
            log.info("Scan budget narrowed %s: %s", key, warning)
            self.scan_status.warnings.append(warning)

    async def _run_budgeted(
        self, key: str, budget_seconds: float, coro: Awaitable[Any], overrun: str,
    ) -> bool:
        """Run one phase under its own deadline. False if it overran.

        Overrunning is the fallback, not the mechanism: each phase's work was
        already narrowed to fit before it started, so reaching the deadline
        means the network was slower than the per-item costs model it. The
        phase keeps whatever it merged before the cut (every scanner writes
        into ``self.results`` as it goes), the warning says the coverage is
        short, and — crucially — the phases after this one still get their own
        budgets instead of inheriting a dead scan.
        """
        try:
            await asyncio.wait_for(coro, timeout=max(0.1, budget_seconds))
            return True
        except asyncio.TimeoutError:
            log.warning(
                "Discovery phase %s exceeded its %.1fs budget", key, budget_seconds,
            )
            self._narrowed = True
            self.scan_status.warnings.append(overrun)
            return False

    async def _finalize_scan(self) -> None:
        """Phase 8: run the deterministic matcher per device, then finalize.

        Runs OUTSIDE the scan deadline (see _run_scan). This phase is pure CPU
        over evidence already collected in memory — it makes no network calls —
        and it is the entire point of the scan: without it every device carries
        ``identification: None``. Budgeting it meant a slow network spent the
        whole allowance on phases 3-5 and then threw away the evidence it had
        just paid for, reporting a "complete" scan with zero identifications.
        """
        await self._set_phase(8, "finalize", "Matching drivers...")

        finalize_total = len(self.results)
        for i, device in enumerate(self.results.values()):
            # Emit open-port enrichment evidence for any port that's
            # both observed open AND referenced by at least one
            # driver's ``port_open:`` hint. Bare openness on a
            # generic port is too weak to emit unconditionally.
            for port in device.open_ports:
                if self.signal_index.find_soft_open_port(port):
                    device.evidence_log.append(evidence_open_port(port))

            # Mine probe responses for manufacturer / make strings
            # and append vendor_string enrichment evidence so the
            # matcher can pick a best-fit driver via
            # ``manufacturer_alias:`` hints — e.g. a probe response
            # carrying ``manufacturer=<vendor>`` surfaces a driver
            # that claims that alias without needing an OUI hit.
            device.evidence_log.extend(
                extract_vendor_strings(device.evidence_log)
            )

            device.identification = self.tier_matcher.match(device.evidence_log)
            await self._emit_device_update(device, "driver_match")
            if finalize_total > 0:
                await self._update_intra_progress((i + 1) / finalize_total)

        # Remove devices that were not re-discovered in this scan
        stale_ips = [ip for ip, dev in self.results.items() if not dev.alive]
        for ip in stale_ips:
            del self.results[ip]
        if stale_ips:
            log.info(
                "Removed %d stale devices not found in this scan", len(stale_ips)
            )

        self.scan_status.devices_found = len(self.results)

    async def _cancel_background_tasks(
        self, *tasks: asyncio.Task | None,
    ) -> None:
        """Cancel and await any still-running background scan tasks.

        Idempotent and safe on every exit path: a ``None`` or already-finished
        task is skipped, so this is a cheap no-op on the success path (phase 7
        already collected the passive listeners + SNMP scanner) and the
        guaranteed cleanup on timeout / cancellation / pipeline error —
        releasing each listener's bound UDP socket and blocked recvfrom
        executor thread instead of leaking them into the next scan.

        ``gather(return_exceptions=True)`` swallows each child's own
        CancelledError but still propagates a cancellation aimed at the
        awaiting coroutine, so calling this from a ``finally`` preserves
        cooperative cancellation.
        """
        pending = [t for t in tasks if t is not None and not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _collect_passive_results(
        self,
        mdns_task: asyncio.Task,
        ssdp_task: asyncio.Task,
        amx_ddp_task: asyncio.Task,
        wait_seconds: float | None = None,
    ) -> None:
        """Wait for passive listeners and append their evidence to each device.

        Uses a 1-second heartbeat loop so the progress bar moves steadily
        instead of appearing stuck while waiting for SSDP XML fetches.

        ``wait_seconds`` is the window the scan budget granted (the depth
        policy's value, possibly shortened to leave room for driver probes).
        None falls back to the module default.
        """
        total_wait = (
            wait_seconds if wait_seconds is not None
            else _PASSIVE_COLLECT_DEFAULT_WAIT_SECONDS
        )
        remaining_tasks = {mdns_task, ssdp_task, amx_ddp_task}
        elapsed = 0.0
        tick = _PASSIVE_COLLECT_TICK_SECONDS

        while elapsed < total_wait and remaining_tasks:
            _, pending = await asyncio.wait(remaining_tasks, timeout=tick)
            remaining_tasks = pending
            elapsed += tick
            fraction = min(elapsed / total_wait, 1.0)
            await self._update_intra_progress(fraction)
            secs_left = max(0, int(total_wait - elapsed))
            await self._emit_progress(
                "passive_collect",
                f"Collecting passive results... ({secs_left}s remaining)",
            )

        # Cancel any listeners still running past the collect window. gather()
        # with return_exceptions swallows each task's own CancelledError but
        # still propagates a cancellation aimed at THIS coroutine — so a
        # stop_scan()/timeout cancellation isn't silently eaten here (which
        # would let the collect loop run on instead of aborting promptly).
        if remaining_tasks:
            for task in remaining_tasks:
                task.cancel()
            await asyncio.gather(*remaining_tasks, return_exceptions=True)

        # Retrieve each listener's outcome so a crashed one is logged, and so a
        # failed task doesn't resurface later as asyncio's "exception was never
        # retrieved". The evidence itself comes from the scanners below, not
        # from these results.
        for task, label in (
            (mdns_task, "mDNS"), (ssdp_task, "SSDP"), (amx_ddp_task, "AMX DDP"),
        ):
            if task.done() and not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    log.debug("%s listener failed", label, exc_info=exc)

        await self.merge_passive_results()

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
        merged = 0

        for ip, mdns_result in mdns_scanner.results.items():
            if not self._claim_passive_merge("mdns", ip):
                continue
            device = self._get_or_create_passive(ip)
            if device is None:
                continue
            device.alive = True
            ev = mdns_result.to_evidence()
            if ev is not None:
                device.evidence_log.append(ev)
            merge_device_info(device, mdns_result.to_device_info(), "mdns")
            await self._emit_device_update(device, "mdns")
            merged += 1

        for ip, ssdp_result in ssdp_scanner.results.items():
            if not self._claim_passive_merge("ssdp", ip):
                continue
            device = self._get_or_create_passive(ip)
            if device is None:
                continue
            device.alive = True
            # One record per observed UPnP type — the driver-claimed
            # family URN is only one of the types a device advertises.
            device.evidence_log.extend(ssdp_result.to_evidence_records())
            merge_device_info(device, ssdp_result.to_device_info(), "ssdp")
            await self._emit_device_update(device, "ssdp")
            merged += 1

        for ip, beacon in amx_ddp_scanner.results.items():
            if not self._claim_passive_merge("amx_ddp", ip):
                continue
            device = self._get_or_create_passive(ip)
            if device is None:
                continue
            device.alive = True
            device.evidence_log.append(beacon.to_evidence())
            merge_device_info(device, beacon.to_device_info(), "amx_ddp")
            await self._emit_device_update(device, "amx_ddp")
            merged += 1

        return merged

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
        """Wait for SNMP scan and merge results."""
        if snmp_task is None:
            return

        # Wait with timeout
        done, pending = await asyncio.wait([snmp_task], timeout=5.0)

        # Cancel if still running. gather() with return_exceptions swallows the
        # SNMP task's own CancelledError but still propagates a cancellation
        # aimed at THIS coroutine, preserving cooperative cancellation.
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        snmp_results = {}
        if snmp_task.done() and not snmp_task.cancelled():
            try:
                snmp_results = snmp_task.result()
            except Exception:  # Catch-all: task.result() re-raises whatever the task raised
                log.debug("SNMP task failed", exc_info=True)
        elif snmp_scanner is not None:
            # Overran the 5s window and got cancelled. The scanner populates
            # its results incrementally, so every device it identified before
            # the cutoff is still available — merge those rather than dropping
            # all SNMP enrichment on a large/slow subnet.
            snmp_results = snmp_scanner.results
            if snmp_results:
                log.info(
                    "SNMP scan overran the collect window; merging %d partial result(s)",
                    len(snmp_results),
                )

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

    async def _run_custom_probes(
        self,
        subnets: list[str],
        control_ip: str,
        max_tcp_jobs: int | None = None,
    ) -> None:
        """Dispatch driver-declared probes.

        Walks ``self.discovery_hints`` for any declared
        ``udp_probe:`` / ``tcp_probe:`` / ``python:`` specs:

        - **UDP probes** fire once per scan against every subnet's
          directed broadcast address, sharing a single 10/sec
          ``RateLimiter``.
        - **TCP probes** run against every host whose port-scan results
          include the spec's port, with a 20 ms stagger so the SYN burst
          is spread and sharing the same 10/sec ``RateLimiter`` as the
          UDP probes so the global send cap bounds the connect rate too.
        - **Python companions** are invoked with a ``ProbeContext``
          carrying the engine's port-scan map so the companion can
          consume already-discovered hosts instead of re-iterating
          subnets.

        Resulting Evidence is appended to each device's ``evidence_log``
        for the matcher to consume in phase 8.

        ``max_tcp_jobs`` caps the per-host TCP probes when the scan budget
        could not afford them all (the UDP broadcasts and companions are kept
        — one send each covers a whole subnet, so they are the cheapest
        identification per packet). The caller warns about the shortfall.
        """
        udp_specs = [
            h.udp_probe for h in self.discovery_hints
            if h.udp_probe is not None
        ]
        tcp_specs = [
            h.tcp_probe for h in self.discovery_hints
            if h.tcp_probe is not None
        ]
        if (
            not udp_specs
            and not tcp_specs
            and not self._discovery_companions
        ):
            return

        rate_limiter = RateLimiter(rate_per_sec=10.0)

        if udp_specs:
            broadcasts = _broadcast_addresses_for(subnets)
            if broadcasts:
                udp_tasks = [
                    run_udp_broadcast_probe(
                        spec,
                        targets=broadcasts,
                        source_ip=control_ip,
                        rate_limiter=rate_limiter,
                    )
                    for spec in udp_specs
                ]
                udp_results = await asyncio.gather(
                    *udp_tasks, return_exceptions=True,
                )
                for spec, result in zip(udp_specs, udp_results):
                    if isinstance(result, BaseException):
                        log.debug(
                            "Custom UDP probe %s failed",
                            spec.probe_id, exc_info=result,
                        )
                        continue
                    if not isinstance(result, dict):
                        continue
                    for ip, ev in result.items():
                        device = self._get_or_create(ip)
                        device.alive = True
                        device.evidence_log.append(ev)
                        # Lift extracted hostname/model/firmware/etc. onto the
                        # device record so the card and the export report show
                        # them. Passive listeners do this; probes need to too.
                        # fill_only: a probe runs after passive collection, so
                        # it enriches empty fields but never clobbers an
                        # authoritative passive/SNMP identity by being longer.
                        info = device_info_from_evidence(ev)
                        if info:
                            merge_device_info(
                                device, info, "broadcast_probe", fill_only=True,
                            )
                        await self._emit_device_update(device, "broadcast_probe")

        if tcp_specs:
            async def _run_one_tcp(spec, target, idx):
                ev = await run_tcp_active_probe(
                    spec,
                    target=target,
                    source_ip=control_ip,
                    stagger_ms=idx * 20.0,
                    rate_limiter=rate_limiter,
                )
                return target, spec, ev

            tcp_jobs: list = []
            for spec in tcp_specs:
                hosts = [
                    ip for ip, dev in self.results.items()
                    if spec.port in (dev.open_ports or [])
                ]
                for idx, ip in enumerate(hosts):
                    tcp_jobs.append(_run_one_tcp(spec, ip, idx))
            if max_tcp_jobs is not None and len(tcp_jobs) > max_tcp_jobs:
                for extra in tcp_jobs[max_tcp_jobs:]:
                    extra.close()  # never awaited — don't leave it pending
                tcp_jobs = tcp_jobs[:max_tcp_jobs]
            if tcp_jobs:
                tcp_results = await asyncio.gather(
                    *tcp_jobs, return_exceptions=True,
                )
                for r in tcp_results:
                    if isinstance(r, BaseException):
                        log.debug(
                            "Custom TCP probe failed", exc_info=r,
                        )
                        continue
                    target, spec, ev = r
                    if ev is None:
                        continue
                    device = self._get_or_create(target)
                    device.alive = True
                    device.evidence_log.append(ev)
                    info = device_info_from_evidence(ev)
                    if info:
                        merge_device_info(
                            device, info, "protocol_probe", fill_only=True,
                        )
                    await self._emit_device_update(device, "protocol_probe")

        if self._discovery_companions:
            async def _emit_for_host(host: str, ev) -> None:
                # Companion code is community-trust (same tier as a Python
                # driver) and passes the host string verbatim. Enforce the
                # engine-wide invariant that every result is a host on a
                # scanned subnet: without this a buggy or hostile companion
                # could fabricate a record — even an IDENTIFIED one — for an IP
                # that was never scanned. Built-in probes are already
                # constrained (UDP keys off the recorded sender; TCP only
                # targets hosts already in results).
                if not _ip_in_subnets(host, subnets):
                    log.warning(
                        "Discovery companion emitted host %r outside the "
                        "scanned subnets; ignoring", host,
                    )
                    return
                device = self._get_or_create(host)
                device.alive = True
                device.evidence_log.append(ev)
                info = device_info_from_evidence(ev)
                if info:
                    merge_device_info(device, info, "companion", fill_only=True)
                await self._emit_device_update(device, "broadcast_probe")

            # Build the port -> hosts map once and share it across
            # every companion invocation — companions consume the
            # engine's existing port-scan results instead of
            # re-iterating subnets to rediscover live hosts.
            hosts_by_open_port: dict[int, list[str]] = {}
            for ip, dev in self.results.items():
                for port in dev.open_ports or ():
                    hosts_by_open_port.setdefault(port, []).append(ip)
            hosts_by_open_port_frozen: dict[int, tuple[str, ...]] = {
                port: tuple(ips) for port, ips in hosts_by_open_port.items()
            }

            companion_logger = logging.getLogger("discovery.companion.run")
            companion_tasks = []
            companion_ids = []
            for driver_id, probe_fn in self._discovery_companions.items():
                ctx = ProbeContext(
                    driver_id=driver_id,
                    source_ip=control_ip,
                    target_subnets=tuple(subnets),
                    timeout_seconds=DEFAULT_PROBE_TIMEOUT_SECONDS,
                    log=companion_logger,
                    _emit_for_host=_emit_for_host,
                    hosts_by_open_port=hosts_by_open_port_frozen,
                )
                companion_tasks.append(run_companion(driver_id, probe_fn, ctx))
                companion_ids.append(driver_id)
            if companion_tasks:
                # run_companion isolates a companion's own failures internally;
                # an exception surfacing HERE is a runner-level fault (e.g. the
                # worker thread failed to start). Read the results so it's
                # logged, not swallowed by return_exceptions.
                outcomes = await asyncio.gather(
                    *companion_tasks, return_exceptions=True
                )
                for cid, outcome in zip(companion_ids, outcomes):
                    if isinstance(outcome, BaseException) and not isinstance(
                        outcome, asyncio.CancelledError
                    ):
                        log.warning(
                            "Discovery companion %s runner failed: %r",
                            cid, outcome,
                        )

    def _get_or_create(self, ip: str) -> DiscoveredDevice:
        """Get existing device record or create a new one."""
        if ip not in self.results:
            self.results[ip] = DiscoveredDevice(ip=ip)
        return self.results[ip]

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
            info: dict[str, Any] = {"mac": mac}
            oui_result = self.oui_db.lookup(mac)
            oui_vendor = None
            if oui_result:
                manufacturer, category = oui_result
                info["manufacturer"] = manufacturer
                info["category"] = category
                oui_vendor = manufacturer
            # fill_only: this runs AFTER passive collection, and OUI names
            # the NIC vendor — a longer IEEE registrant string must not
            # clobber the manufacturer/category a device self-reported via
            # mDNS/SSDP/SNMP. Enrich empty fields; never overwrite.
            merge_device_info(device, info, "arp", fill_only=True)
            device.evidence_log.append(evidence_oui(mac, vendor=oui_vendor))
            enriched += 1
            await self._emit_device_update(device, "arp_harvest")
        if enriched:
            log.info(
                "Late ARP harvest enriched %d passive-only device(s) with a "
                "MAC/OUI", enriched,
            )

    def _phase_base_progress(self, phase: str) -> float:
        """Cumulative progress at the START of a phase (sum of preceding weights)."""
        depth = self.config.get("scan_depth", "standard")
        order = PHASE_ORDER.get(depth, PHASE_ORDER["standard"])
        weights = PHASE_WEIGHTS.get(depth, PHASE_WEIGHTS["standard"])
        idx = order.index(phase) if phase in order else 0
        return sum(weights.get(p, 0.0) for p in order[:idx])

    def _phase_weight(self, phase: str) -> float:
        """Weight (fraction of bar) allocated to the current phase."""
        depth = self.config.get("scan_depth", "standard")
        weights = PHASE_WEIGHTS.get(depth, PHASE_WEIGHTS["standard"])
        return weights.get(phase, 0.05)

    async def _set_phase(self, number: int, phase: str, message: str) -> None:
        """Update scan phase and emit progress event."""
        self.scan_status.phase_number = number
        self.scan_status.phase = phase
        self.scan_status.message = message
        self.scan_status.progress = self._phase_base_progress(phase)
        log.info("Discovery phase %d: %s", number, message)
        await self._emit_progress(phase, message)

    async def _update_intra_progress(self, fraction: float) -> None:
        """Update progress within the current phase (fraction 0.0 – 1.0)."""
        phase = self.scan_status.phase
        base = self._phase_base_progress(phase)
        weight = self._phase_weight(phase)
        self.scan_status.progress = base + weight * min(fraction, 1.0)

    async def _emit_progress(self, phase: str, message: str) -> None:
        """Emit a discovery_phase event with current progress."""
        await self._emit({
            "type": "discovery.phase",
            "phase": phase,
            "phase_number": self.scan_status.phase_number,
            "total_phases": self.scan_status.total_phases,
            "message": message,
            "progress": self.scan_status.progress,
            "warnings": list(self.scan_status.warnings),
        })

    async def _emit_device_update(self, device: DiscoveredDevice, phase: str) -> None:
        """Emit a device update event."""
        await self._emit({
            "type": "discovery.update",
            "device": device.to_dict(),
            "phase": phase,
            "progress": self.scan_status.progress,
        })

    async def _emit(self, message: dict[str, Any]) -> None:
        """Send event to the callback (WebSocket broadcast)."""
        if self._on_update:
            try:
                await self._on_update(message)
            except Exception:  # Catch-all: callback may be a WebSocket broadcast; don't crash scan
                log.debug("Failed to emit discovery event", exc_info=True)
