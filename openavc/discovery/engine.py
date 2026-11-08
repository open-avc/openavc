"""Discovery engine: runs a scan's phases and turns what they observed into
answers.

A scan starts its passive listeners, sweeps for live hosts, reads MAC
addresses and names, port-scans, launches SNMP, collects what the listeners
heard, runs the drivers' own probes, and finally matches every device's
evidence against the drivers (``_scan_pipeline_inner`` lists the phases).
The sweep (phase 3) is ``sweep_phase.HostSweep``, phase 4 is
``arp_phase.ArpHarvest``, the port scan (phase 5) is ``port_phase.PortScan``,
the driver probes are ``probe_phase.DriverProbes`` and the listeners'
collection is ``passive_phase.PassiveCollection``, mixins this class
inherits; the records each phase adds are built in ``scan_evidence``.

The rules worth knowing before changing anything here:

* Every network phase runs under its own budget, set from its own workload
  (``scan_budget``), and keeps what it found when that budget runs out; the
  whole scan has a ceiling only as a backstop.
* The matcher (``_finalize_scan``) runs outside every deadline, so a scan cut
  short still identifies what it observed, and reports itself ``partial``.
* An address only the ARP table named is held back until it answers
  something for itself (``_arp_provisional``): a panel cannot un-see a
  device, so it is announced only once it has earned its place.
* A port a connected project device holds is never connected to
  (``port_guard``); that device is identified from its driver instead.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Awaitable, Iterable

from openavc.discovery.arp_phase import ArpHarvest
from openavc.discovery.network_scanner import get_local_subnets
from openavc.discovery.passive_phase import BackgroundScans, PassiveCollection
from openavc.discovery.port_guard import PortGuard, held_ports
from openavc.discovery.port_phase import (  # noqa: F401 -- THOROUGH_EXTRA_PORTS: re-exported, imported from here
    THOROUGH_EXTRA_PORTS,
    PortScan,
    PortScanSettings,
)
from openavc.discovery.probe_phase import DriverProbes
from openavc.discovery.scan_scope import ip_in_subnets
from openavc.discovery.sweep_phase import HostSweep
from openavc.discovery.oui_database import OUIDatabase
from openavc.discovery.hints import (
    DiscoveryHint,
    build_signal_index,
    load_discovery_hints,
)
from openavc.discovery.community_index import CommunityIndexCache
from openavc.discovery.mdns_scanner import (
    BASELINE_SERVICE_TYPES,
    MDNSScanner,
)
from openavc.discovery.ssdp_scanner import SSDPScanner
from openavc.discovery.snmp_scanner import SNMPScanner
from openavc.discovery.amx_ddp_scanner import AMXDDPScanner
from openavc.discovery.companion import (
    CompanionProbe,
    load_discovery_companions,
)
from openavc.discovery.scan_evidence import banner_texts, derived_evidence
from openavc.discovery.tier_matcher import (
    DriverProfile,
    SignalIndex,
    TierMatcher,
    evidence_connected,
)
from openavc.discovery.result import (
    DeviceState,
    DiscoveredDevice,
    Evidence,
    merge_device_info,
)
from openavc.discovery import scan_budget as budgeting
from openavc.discovery.scan_budget import DepthPolicy, ScanBudget

log = logging.getLogger("discovery")


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

# Minimum spacing between intra-phase progress pushes. The sweep phases tick
# once per host; the bar only needs to look alive, so one message a second is
# plenty and keeps a 1022-host sweep off the WS send queue.
_INTRA_EMIT_INTERVAL: float = 1.0


@dataclass(frozen=True)
class _SnmpLaunch:
    """What phase 6 asks SNMP with: read from the discovery settings as the
    scan starts, like the rest of the scan's settings."""

    enabled: bool
    community: str
    source_ip: str
    concurrency: int
    entity_mib: bool


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


def _log_reach_choice(control_ip: str, ignore_pin: bool) -> None:
    """Say in the log which adapters this scan reaches devices through."""
    if ignore_pin:
        log.info("Scan requested with the Reach devices through adapter ignored")
    elif control_ip:
        log.info("Reach devices through %s: scanning that adapter's subnets only", control_ip)
    else:
        log.info("Reach devices through every connected adapter: scanning every adapter that has a link")


def _refuse_unusable_reach_choice() -> None:
    """Raise the sentence the person reads when the adapter chosen in Reach
    devices through has no address to scan from."""
    from openavc import reach_devices

    unusable = reach_devices.unusable_choice()
    if unusable is not None:
        raise ValueError(reach_devices.no_address_sentence(unusable.adapter_name))


def _add_extra_subnets(targets: list[str], extra_subnets: list[str] | None) -> None:
    """Append each of ``extra_subnets`` not already in ``targets``, in place."""
    for subnet in extra_subnets or []:
        if subnet not in targets:
            targets.append(subnet)


def _adapter_at(address: str) -> dict[str, Any] | None:
    """The first network adapter on this computer with ``address``, or None."""
    from openavc.discovery.network_scanner import get_network_adapters

    return next((a for a in get_network_adapters() if a["ip"] == address), None)


def _no_targets_sentence(control_ip: str, subnets: list[str] | None) -> str:
    """Why a scan found nothing to scan, in the words the person reads.

    A pinned control interface that no longer exists is the usual culprit
    (the box moved networks since it was set), and the generic message sent
    one integrator hunting a phantom subnet, so a pin is named, with where to
    clear it.
    """
    if not control_ip or subnets:
        return "No subnets to scan. Specify subnets manually."
    pinned = _adapter_at(control_ip)
    if pinned is not None and not pinned.get("link", True):
        return (
            f"{pinned['name']} ({control_ip}), chosen in Settings > "
            "Network > Reach devices through, has no link. Connect "
            "its cable, or choose another adapter (or Every "
            "connected adapter) there."
        )
    from openavc import reach_devices
    try:
        name = reach_devices.current().adapter_name
    except Exception:  # the sentence still names the address
        name = ""
    return (
        f"{name or 'The adapter'} chosen in Settings > Network > "
        f"Reach devices through isn't connected: no adapter on "
        f"this computer has {control_ip}. Choose a current adapter "
        "there (or Every connected adapter), or specify subnets "
        "manually."
    )


class ScanBlocked(RuntimeError):
    """A scan cannot start because something else holds the network.

    Its message is the sentence the person reads. A ``RuntimeError`` so a
    caller that only knows "a scan could not start" still catches it.
    """


class DiscoveryEngine(DriverProbes, PassiveCollection, PortScan, ArpHarvest, HostSweep):
    """Orchestrates device discovery across all scanning methods."""

    def __init__(self) -> None:
        self.oui_db = OUIDatabase()
        self.discovery_hints: list[DiscoveryHint] = []
        self.signal_index: SignalIndex = SignalIndex()
        self.tier_matcher: TierMatcher = TierMatcher(self.signal_index)
        self._installed_registry: list[dict[str, Any]] = []
        # driver_id -> the category that driver declares (installed or
        # catalog copy, whichever the signal index uses). An identified device
        # with no category takes its driver's; see _fill_category_from_match.
        self._driver_categories: dict[str, str] = {}
        # Driver-supplied Python discovery companions
        # ({driver_id: async probe}). Populated by
        # load_discovery_companions_from_dirs().
        self._discovery_companions: dict[str, CompanionProbe] = {}
        self.community_index = CommunityIndexCache()
        self.results: dict[str, DiscoveredDevice] = {}
        # Passive-source flood guard (see _get_or_create_passive).
        self._passive_sources_created = 0
        self._passive_cap_warned = False
        # Live passive listeners for the current scan, so their evidence can be
        # merged after a truncation as well as in phase 7 (see
        # merge_passive_results), plus the (source, ip) pairs already merged.
        self._passive_scanners: tuple[Any, Any, Any] | None = None
        self._passive_merged: set[tuple[str, str]] = set()
        # Driver probes that have matched in the current scan, keyed by the
        # position each was started in, so their evidence survives a probe
        # phase cut short (see merge_probe_results), plus the positions merged.
        self._probe_finished: dict[int, list[tuple[str, Evidence, str]]] = {}
        self._probe_merged: set[int] = set()
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
        # Last time intra-phase progress was pushed. The sweep phases update
        # progress per host, which is far too often to put on the wire, so
        # the push is throttled and the timestamp lives here.
        self._last_intra_emit: float = 0.0
        # Ping method + source this scan settled on, kept so the ARP phase
        # can re-ping a short list without re-running the selection probe.
        self._ping_method: str = ""
        self._scan_source_ip: str = ""
        # Hosts the ARP table vouched for that answered no ping. They are
        # scanned like any other host but must EARN their place; anything
        # still mute at the end is dropped in _finalize_scan.
        self._arp_provisional: set[str] = set()
        # Hosts this scan has port-scanned, whatever the result. Phase 7's
        # follow-up decides which passive-only hosts still need a scan from
        # this, not from open_ports: an mDNS SRV record puts its port into
        # open_ports without anything having been scanned.
        self._port_scanned: set[str] = set()
        self._scan_counter = 0
        # Asked before a scan starts; returns why it cannot, or None. A device
        # audit sets it: the two never run together (both open the multicast
        # listeners and both send probes).
        self.scan_blocker: Callable[[], str | None] | None = None
        # Asked when a scan starts: where each connected project device's
        # driver is connected now, as objects with ``host``, ``port`` and
        # ``name`` (``DeviceManager.live_connections``). The scan connects to
        # none of those ports (see port_guard). Unset: nothing is held.
        self.held_connections: Callable[[], Iterable[Any]] | None = None
        # This scan's port guard: the ports it leaves alone, one connection
        # per port at a time, and what each port received. Replaced on every
        # start_scan, and filled with the held ports when the scan starts.
        self._guard: PortGuard = PortGuard()
        # The connections behind those held ports, (address, port) ->
        # held_connections' object: a device the scan left alone is
        # identified from the driver connected to it (_finalize_scan).
        self._held: dict[tuple[str, int], Any] = {}
        # Discovery settings (persisted in project)
        self.config: dict[str, Any] = {
            "snmp_enabled": True,
            "snmp_community": "public",
            "gentle_mode": False,
            "scan_depth": "standard",
            "max_subnet_size": 20,  # Min CIDR prefix (/20 = ~4K hosts, /16 = ~65K)
        }

    def _get_control_interface(self) -> str:
        """The address of the adapter chosen in Reach devices through, or ""
        for every connected adapter (``openavc/reach_devices.py``)."""
        from openavc import reach_devices
        return reach_devices.address()

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

    @property
    def discovery_companions(self) -> dict[str, CompanionProbe]:
        """The loaded Python discovery companions, by driver id (a copy)."""
        return dict(self._discovery_companions)

    def load_driver_hints_from_registry(self, registry: list[dict[str, Any]]) -> None:
        """Parse new-schema discovery hints + build the SignalIndex.

        Also enriches the OUI database with each driver's oui_prefixes
        so the ARP/OUI scan phase can attach a friendly vendor name
        before the matcher runs.
        """
        self._installed_registry = list(registry)
        self._rebuild_signal_index(community_drivers=[])

    async def refresh_signal_index_with_catalog(self, force_catalog: bool = False) -> None:
        """Re-fold the community catalog into the SignalIndex.

        Discovery's job is to suggest what driver to install, so the
        catalog (un-installed drivers) must contribute rules just like
        the installed registry does. Installed wins on collisions.

        Called at scan start so a freshly-fetched catalog takes effect
        without a server restart. ``force_catalog`` fetches the catalog now
        instead of taking a cached copy (a device audit records which
        catalog it checked against, so it wants the current one).
        """
        try:
            community = await self.community_index.get_drivers(force=force_catalog)
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

        # Every driver that can identify a device, with or without a
        # discovery: block (a companion identifies without one).
        self._driver_categories = {
            str(d["id"]): str(d["category"])
            for d in installed_kept + catalog_only
            if d.get("id") and isinstance(d.get("category"), str) and d["category"]
        }
        profiles = {
            str(d["id"]): DriverProfile.of(d)
            for d in installed_kept + catalog_only
            if d.get("id")
        }

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
        self.tier_matcher = TierMatcher(self.signal_index, profiles=profiles)

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

    def is_scanning(self) -> bool:
        """True while a scan is running."""
        return self._scan_task is not None and not self._scan_task.done()

    def get_subnets(self) -> list[str]:
        """Auto-detect local subnets (filtered to the Reach devices through
        adapter when one is chosen)."""
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
        self._fill_category_from_match(device)
        return device.to_dict()

    def _fill_category_from_match(self, device: DiscoveredDevice) -> None:
        """An identified device takes its driver's category when it has none.

        The category is what the driver declares about itself, the same data
        an ``oui:`` hint's category comes from, so core learns nothing about
        any product here. Only an outright identification counts (a
        "possible" names several drivers), and a category already on the
        device stays.
        """
        match = device.identification
        if match is None or match.state != DeviceState.IDENTIFIED or not match.driver_id:
            return
        category = self._driver_categories.get(match.driver_id)
        if category:
            merge_device_info(device, {"category": category}, "driver_match", fill_only=True)

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
            self._refuse_to_start()

            self._scan_counter += 1
            scan_id = f"scan_{self._scan_counter}_{int(time.time())}"

            control_ip = "" if ignore_control_interface else self._get_control_interface()
            targets = self._scan_targets(
                subnets, extra_subnets, control_ip, ignore_control_interface,
            )
            policy, total_seconds = self._scan_policy(timeout)

            self._on_update = on_update
            self._scan_ignores_pin = ignore_control_interface
            self._begin_status(scan_id, targets, total_seconds, control_ip)
            self._reset_for_scan(policy, total_seconds)

            self._scan_task = asyncio.create_task(
                self._run_scan(targets, total_seconds)
            )
            return scan_id

    def _refuse_to_start(self) -> None:
        """Raise when a scan is already running, or when something else holds
        the network (``scan_blocker``: a device audit)."""
        if self._scan_task and not self._scan_task.done():
            raise RuntimeError("Scan already running")
        blocked = self.scan_blocker() if self.scan_blocker is not None else None
        if blocked:
            raise ScanBlocked(blocked)

    def _scan_targets(
        self,
        subnets: list[str] | None,
        extra_subnets: list[str] | None,
        control_ip: str,
        ignore_pin: bool,
    ) -> list[str]:
        """The subnets this scan covers: the ones asked for, else every subnet
        of the Reach devices through adapter (every connected adapter when
        none is chosen), plus ``extra_subnets``.

        Raises ``ValueError`` with the sentence the person reads when the
        chosen adapter has no address, or when there is nothing to scan.
        ``subnets`` is extended in place with the extras, and is the list the
        scan's status reports.
        """
        _log_reach_choice(control_ip, ignore_pin)
        if not control_ip and not ignore_pin:
            _refuse_unusable_reach_choice()
        targets = subnets if subnets else get_local_subnets(
            interface_ip=control_ip or None
        )
        _add_extra_subnets(targets, extra_subnets)

        log.info("Discovery target subnets: %s", targets)

        if not targets:
            raise ValueError(_no_targets_sentence(control_ip, subnets))
        return targets

    def _scan_policy(self, timeout: float | None) -> tuple[DepthPolicy, float]:
        """The depth policy this scan runs under (gentle mode applied) and its
        ceiling in seconds: ``timeout`` when given, else the policy's."""
        depth = self.config.get("scan_depth", "standard")
        gentle = bool(self.config.get("gentle_mode", False))
        policy = budgeting.resolve_policy(depth, gentle)
        total_seconds = (
            float(timeout) if timeout is not None else policy.total_seconds
        )
        return policy, total_seconds

    def _begin_status(
        self, scan_id: str, targets: list[str], total_seconds: float, control_ip: str,
    ) -> None:
        """A fresh, running ``scan_status`` for this scan, naming the adapter
        it scans through for the UI when one is chosen."""
        self.scan_status = ScanStatus()
        self.scan_status.scan_id = scan_id
        self.scan_status.status = "running"
        self.scan_status.started_at = time.time()
        self.scan_status.subnets = targets
        self.scan_status.budget_seconds = total_seconds

        adapter = _adapter_at(control_ip) if control_ip else None
        if adapter is not None:
            self.scan_status.active_adapter = {
                "name": adapter["name"],
                "ip": adapter["ip"],
                "subnet": adapter["subnet"],
            }

    def _reset_for_scan(self, policy: DepthPolicy, total_seconds: float) -> None:
        """Clear what the last scan left behind before this one starts.

        Previously found devices are marked stale: they are removed at the
        end of the scan if not re-discovered, which gives the full pipeline
        (ping, port scan, probes, SSDP, mDNS) a chance to re-find them first.
        Their per-scan observations are cleared too. The evidence_log, open
        ports and banners are re-derived from scratch every scan; kept, the
        evidence_log grows without bound (memory, plus a match() cost that
        climbs every scan) and finalize re-appends an identical open-port
        record per scan, piling up duplicate rows in the "Why?" reveal.
        Identity fields stay (they're re-merged from the fresh evidence) so
        the card doesn't blank mid-scan.
        """
        for device in self.results.values():
            device.alive = False
            device.evidence_log.clear()
            device.open_ports.clear()
            device.banners.clear()
            device.port_probes.clear()

        self._passive_sources_created = 0
        self._passive_cap_warned = False
        self._passive_scanners = None
        self._passive_merged.clear()
        self._probe_finished.clear()
        self._probe_merged.clear()
        self._budget = ScanBudget(policy, total_seconds)
        self._arp_provisional = set()
        self._port_scanned = set()
        self._narrowed = False
        self._guard = PortGuard()
        self._held = {}

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
                # The same for driver probes, and not only after the outer
                # deadline: the probe phase runs under its own, which ends it
                # without truncating the scan. Idempotent, so a probe phase
                # that finished (and merged) leaves nothing to do here.
                try:
                    recovered = await self.merge_probe_results()
                    if recovered:
                        log.info(
                            "Kept %d driver probe result(s) from a probe phase "
                            "that ran out of time", recovered,
                        )
                except Exception:
                    log.exception("Probe result recovery failed")
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
        self._held = await self._held_ports()
        self._guard = PortGuard({key: conn.name for key, conn in self._held.items()})
        return await self._scan_pipeline_inner(subnets)

    async def _held_ports(self) -> dict[tuple[str, int], Any]:
        """``(address, port)`` -> the connection, for every connected project
        device, read once as the scan starts.

        A device set up by host name is looked up here, since a scan knows
        devices by address. One whose name does not resolve cannot be
        recognized, and the scan's warnings say so.
        """
        if self.held_connections is None:
            return {}
        try:
            connections = list(self.held_connections())
        except Exception:
            log.exception("Could not read the project's connected devices")
            return {}
        held, unresolved = await held_ports(connections)
        for conn in unresolved:
            log.warning("Could not look up %s (%s) to keep the scan off its port", conn.host, conn.name)
            self.scan_status.warnings.append(
                f"Could not look up {conn.host}, the address of {conn.name}, so this "
                f"scan may have connected to it on port {conn.port}."
            )
        if held:
            log.info(
                "Scan leaves alone %d port(s) connected project devices hold: %s",
                len(held), ", ".join(f"{h}:{p}" for h, p in sorted(held)),
            )
        return held

    async def _scan_pipeline_inner(self, subnets: list[str]) -> None:
        """The core scan phases.

        Phase layout:
          1: Subnet detection (already done)
          2: Start passive listeners (mDNS + SSDP + AMX DDP)
          3: Ping sweep (``sweep_phase``)
          4: ARP harvest + OUI lookup + hostname resolution (``arp_phase``)
          5: Port scan + banner grab (``port_phase``)
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
        control_ip = "" if getattr(self, "_scan_ignores_pin", False) else self._get_control_interface()
        snmp = self._snmp_launch_settings(control_ip, gentle)
        # Every probe this scan sends binds to the control interface, so
        # replies come back on a multi-homed host (spec: discovery binds
        # to the control interface).
        self._scan_source_ip = control_ip

        await self._set_phase(1, "subnet_detection", "Detecting network interfaces...")
        listeners = await self._open_listeners(subnets, control_ip)

        # The listeners run through every active phase and are stopped in
        # phase 7, when the scan is ready to collect them. Their tasks are
        # created inside the try so the finally cleans them up on EVERY exit
        # path — see _cancel_background_tasks for why that matters.
        background = BackgroundScans()
        try:
            self._start_listeners(listeners, background)
            alive_ips = await self._sweep_phase(subnets, gentle)
            await self._arp_harvest_phase(alive_ips, policy)
            port_list = await self._port_phase(alive_ips, policy, gentle)
            await self._launch_snmp(background, alive_ips, snmp)
            await self._passive_collect_phase(
                background, alive_ips,
                PortScanSettings(port_list, policy.port_host_concurrency, gentle),
            )
            self._report_listener_failures(listeners)
            await self._driver_probe_phase(subnets, control_ip)
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
            await self._cancel_background_tasks(*background.tasks())

    def _mdns_service_types(self) -> list[str]:
        """What the mDNS listener asks for: a small consumer baseline, then
        each loaded driver's ``mdns:`` service. The scanner adds the DNS-SD
        meta-query itself, so unknown types surface for catalog growth."""
        driver_service_types = [
            fp.service for hint in self.discovery_hints for fp in hint.mdns if fp.service
        ]
        return list(BASELINE_SERVICE_TYPES) + driver_service_types

    async def _open_listeners(
        self, subnets: list[str], control_ip: str,
    ) -> tuple[MDNSScanner, SSDPScanner, AMXDDPScanner]:
        """Phase 2: the passive listeners, built for this scan.

        Kept where _run_scan can reach them, so a truncated scan can still
        merge what they heard: their tasks get cancelled, which zeroes the
        task results but not the scanners' own accumulated state.
        """
        await self._set_phase(2, "passive_listen", "Starting mDNS, SSDP, and AMX DDP listeners...")
        mdns_scanner = MDNSScanner(service_types=self._mdns_service_types(), control_ip=control_ip)
        ssdp_scanner = SSDPScanner(
            control_ip=control_ip,
            fetch_scope=lambda ip: ip_in_subnets(ip, subnets),
        )
        amx_ddp_scanner = AMXDDPScanner(control_ip=control_ip)
        self._passive_scanners = (mdns_scanner, ssdp_scanner, amx_ddp_scanner)
        return self._passive_scanners

    @staticmethod
    def _start_listeners(
        listeners: tuple[MDNSScanner, SSDPScanner, AMXDDPScanner], background: BackgroundScans,
    ) -> None:
        """Start each listener's task on ``background``, one by one, so a task
        already started is there for the pipeline's finally to cancel. The
        600s cap is a safety net; phase 7 stops them much sooner."""
        mdns_scanner, ssdp_scanner, amx_ddp_scanner = listeners
        background.mdns = asyncio.create_task(mdns_scanner.start(duration=600.0))
        background.ssdp = asyncio.create_task(ssdp_scanner.scan(
            timeout=600.0, fetch_descriptions=True,
        ))
        background.amx_ddp = asyncio.create_task(amx_ddp_scanner.start(duration=600.0))

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

    def _snmp_launch_settings(self, control_ip: str, gentle: bool) -> _SnmpLaunch:
        """What phase 6 asks SNMP with, read from the settings as the scan starts."""
        return _SnmpLaunch(
            enabled=bool(self.config.get("snmp_enabled", True)),
            community=self.config.get("snmp_community", "public"),
            source_ip=control_ip,
            concurrency=10 if gentle else 20,
            entity_mib=self._budget.policy.snmp_entity_mib,
        )

    async def _launch_snmp(
        self, background: BackgroundScans, alive_ips: list[str], snmp: _SnmpLaunch,
    ) -> None:
        """Phase 6: start the SNMP scan of the live hosts in the background,
        so its wait overlaps phase 7's collection window.

        The driver probes run in phase 7, after the passive collection, since
        they need the full host inventory (passive-only devices included).
        """
        await self._set_phase(6, "protocol_probe", "Identifying device protocols...")
        if not (snmp.enabled and alive_ips):
            return
        background.snmp_scanner = SNMPScanner(source_ip=snmp.source_ip)
        background.snmp = asyncio.create_task(
            background.snmp_scanner.scan_devices(
                alive_ips,
                community=snmp.community,
                timeout=2.0,
                concurrency=snmp.concurrency,
                entity_mib=snmp.entity_mib,
            )
        )

    async def _passive_collect_phase(
        self, background: BackgroundScans, alive_ips: list[str], followup: PortScanSettings,
    ) -> None:
        """Phase 7's collection, under its own budget.

        It narrows by waiting less rather than by dropping work: the
        announcements it gathers are the strongest identification signal a
        scan gets, so shortening the window beats skipping it. The listeners
        keep running through the window and are stopped at its end (see
        _collect_passive_results).
        """
        await self._set_phase(7, "passive_collect", "Collecting passive and SNMP results...")
        collect_plan = budgeting.plan_passive_collect(self._budget)
        self._apply_plan(budgeting.PHASE_PASSIVE, collect_plan.warnings)
        await self._run_budgeted(
            budgeting.PHASE_PASSIVE, collect_plan.budget,
            self._collect_phase(background, collect_plan.wait_seconds, alive_ips, followup),
            "Collecting device announcements ran out of its share of the "
            "scan time. Some announcements may be missing.",
        )

    def _report_listener_failures(self, listeners: Iterable[Any]) -> None:
        """A listener that could not work (multicast join failed on every
        interface, socket unavailable, ...) becomes a scan warning: a silent
        {} from a listener that never ran reads as "no devices on the
        network", which is the wrong story."""
        for listener in listeners:
            if listener.env_error:
                self.scan_status.warnings.append(listener.env_error)

    async def _driver_probe_phase(self, subnets: list[str], control_ip: str) -> None:
        """The driver-declared probes, once the full host inventory is known.

        UDP broadcasts, TCP probes against every host whose port scan found
        the probe's port, and Python companions; their evidence lands in each
        device's evidence_log before the matcher runs in phase 8. Its cost is
        the least predictable of any phase and it runs last, so its budget is
        reserved up front: the point of budgeting by share rather than in
        pipeline order.
        """
        (
            tcp_jobs, udp_sends, companions, udp_listen, tcp_wait,
        ) = self._count_probe_jobs(subnets)
        probe_plan = budgeting.plan_probes(
            self._budget, tcp_jobs, udp_sends, companions,
            udp_listen=udp_listen, tcp_wait=tcp_wait,
        )
        self._apply_plan(budgeting.PHASE_PROBE, probe_plan.warnings)
        log.info(
            "Driver probes: %d TCP, %d UDP send(s), %d companion(s); %.1fs allowed",
            tcp_jobs, udp_sends, companions, probe_plan.budget,
        )
        probes_began = time.monotonic()
        await self._run_budgeted(
            budgeting.PHASE_PROBE, probe_plan.budget,
            self._run_custom_probes(
                subnets, control_ip, max_tcp_jobs=probe_plan.max_tcp_jobs,
            ),
            "Driver probes ran out of their share of the scan time. Some "
            "devices may show as possible rather than identified.",
        )
        log.info(
            "Driver probes: %d matched, %.1fs taken",
            len(self._probe_finished), time.monotonic() - probes_began,
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

    async def _collect_phase(
        self,
        background: BackgroundScans,
        passive_wait: float,
        alive_ips: list[str],
        followup: PortScanSettings,
    ) -> None:
        """Phase 7's collection half: gather everything already observed.

        Passive announcements, SNMP, a port-scan follow-up for hosts only the
        listeners saw, and a second ARP read for the MACs that follow-up just
        put in the cache. All of it feeds the probe pass that comes next.
        """
        await self._collect_passive_results(
            background.mdns, background.ssdp, background.amx_ddp,
            wait_seconds=passive_wait,
        )
        await self._collect_snmp_results(background.snmp, background.snmp_scanner)
        await self._port_scan_passive_only(alive_ips, followup)

        # Pick up a MAC + OUI for passive-discovered devices the phase-4
        # ARP harvest missed (found via mDNS/SSDP on a ping-skipped subnet).
        await self._late_arp_harvest()

    def _apply_plan(self, key: str, warnings: list[str]) -> None:
        """Record a phase plan: its granted deadline, and any narrowing.

        A narrowing warning is the contract here — work
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

        connected = self._connected_evidence()
        finalize_total = len(self.results)
        for i, device in enumerate(self.results.values()):
            # Open-port records for ports a driver's port_open: hint names,
            # and manufacturer strings mined from probe replies (so a reply
            # carrying ``manufacturer=<vendor>`` surfaces a driver that
            # claims that alias without an OUI hit).
            device.evidence_log.extend(derived_evidence(
                device.evidence_log, device.open_ports, self.signal_index,
                banner_texts(device.banners),
            ))
            # A project device's driver connected to this one: the identification
            # when no fingerprint answered (the scan left that port alone).
            device.evidence_log.extend(connected.get(device.ip, []))

            device.identification = self.tier_matcher.match(device.evidence_log)
            self._fill_category_from_match(device)
            device.port_probes = self._guard.record(device.ip)
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

        # An ARP hint that never answered anything is not a device. It got a
        # sweep, a second ping, a port scan and the passive listeners to show
        # itself; with nothing to show, listing it would be inventing
        # equipment out of a cache entry that nobody can act on.
        # A device a project driver is connected to answers that driver, even
        # when the port it answers on was the one the scan left alone.
        unproven = [
            ip for ip in self._arp_provisional
            if ip in self.results
            and not self._answered_for_itself(self.results[ip])
            and ip not in connected
        ]
        for ip in unproven:
            del self.results[ip]
        if unproven:
            log.info(
                "Dropped %d ARP-named host(s) that answered nothing",
                len(unproven),
            )

        # Whatever is left has earned its place, so announce it now -- this
        # is the first the panel hears of these.
        for ip in sorted(self._arp_provisional):
            self._arp_provisional.discard(ip)
            earned = self.results.get(ip)
            if earned is not None:
                await self._emit_device_update(earned, "finalize")

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

    def _connected_evidence(self) -> dict[str, list[Evidence]]:
        """Address -> one ``connected`` record per project device whose driver
        held a port there when the scan started.

        A serial device reached through a bridge's pass-through port is left
        out: the address is the bridge's, and the bridge device's own
        connection (a port of its own) is the one that names it.
        """
        out: dict[str, list[Evidence]] = {}
        for (address, port), conn in sorted(self._held.items()):
            driver_id = getattr(conn, "driver", "")
            if not driver_id or getattr(conn, "bridge", ""):
                continue
            out.setdefault(address, []).append(
                evidence_connected(driver_id, getattr(conn, "name", "") or driver_id, port),
            )
        return out

    @staticmethod
    def _answered_for_itself(device: DiscoveredDevice) -> bool:
        """Did the DEVICE answer something, rather than a cache or a DNS?

        Reverse DNS is deliberately not on this list: that is the name
        server talking, not the equipment.
        """
        return bool(
            device.open_ports
            or device.mdns_services
            or device.ssdp_info
            or device.snmp_info
            or device.protocols
            or device.device_name
        )

    def _refresh_device_count(self) -> None:
        """Publish the count of devices actually being shown.

        Hosts the ARP table only named are excluded: they are held off the
        wire until they earn their place, so counting them would report
        more devices than the panel is displaying and then tick DOWN when
        the unproven ones are dropped.
        """
        self.scan_status.devices_found = len(self.results) - len(
            self._arp_provisional
        )

    def _get_or_create(self, ip: str) -> DiscoveredDevice:
        """Get existing device record or create a new one."""
        if ip not in self.results:
            self.results[ip] = DiscoveredDevice(ip=ip)
            self._refresh_device_count()
        return self.results[ip]

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
        """Update progress within the current phase, and push it.

        The push is what makes the bar move while a phase is still running.
        Without it the only progress the client sees inside a phase rides on
        ``discovery.update`` — so the bar advances when a host is FOUND and
        stalls across dead address space, which is backwards: it goes quiet
        exactly when a scan is finding nothing and the user most needs to
        know the sweep is still alive.

        Throttled to _INTRA_EMIT_INTERVAL because the callers tick per host
        (1022 of them on a /22), and one WS message each would flood a
        client's send queue for no extra information.
        """
        phase = self.scan_status.phase
        base = self._phase_base_progress(phase)
        weight = self._phase_weight(phase)
        self.scan_status.progress = base + weight * min(fraction, 1.0)

        if time.monotonic() - self._last_intra_emit < _INTRA_EMIT_INTERVAL:
            return
        await self._emit_progress(phase, self.scan_status.message)

    async def _emit_progress(self, phase: str, message: str) -> None:
        """Emit a discovery_phase event with current progress.

        Restarts the intra-phase throttle: any progress push counts, so a
        phase change or a caller with a better message (the passive-collect
        countdown) suppresses the next generic tick instead of doubling it.
        """
        self._last_intra_emit = time.monotonic()
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
        """Emit a device update event.

        A host the ARP table merely NAMED is held back until it has earned
        its place. A panel has no way to un-see a device -- results arrive
        over the wire and are never reconciled against a final list -- so
        announcing one that may be dropped at the end would leave it on
        screen for good. _finalize_scan announces the ones that survive.
        """
        if device.ip in self._arp_provisional:
            return
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
