"""Discovery engine — orchestrates all scanning methods."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket as _socket
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from server.discovery.network_scanner import get_local_subnets, ping_sweep, harvest_arp_table, netbios_sweep
from server.discovery.port_scanner import scan_host_ports, grab_banners, BASELINE_PORTS
from server.discovery.oui_database import OUIDatabase
from server.discovery.hints import (
    DiscoveryHint,
    build_signal_index,
    load_discovery_hints,
)
from server.discovery.community_index import CommunityDevicesCache, CommunityIndexCache
from server.discovery.mdns_scanner import (
    BASELINE_SERVICE_TYPES,
    MDNSScanner,
)
from server.discovery.ssdp_scanner import SSDPScanner
from server.discovery.snmp_scanner import SNMPScanner
from server.discovery.amx_ddp_scanner import AMXDDPScanner
from server.discovery.probe_runner import (
    RateLimiter,
    run_tcp_active_probe,
    run_udp_broadcast_probe,
)
from server.discovery.companion import (
    CompanionProbe,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    ProbeContext,
    load_discovery_companions,
    run_companion,
)
from server.discovery.tier_matcher import (
    SignalIndex,
    TierMatcher,
    evidence_hostname,
    evidence_open_port,
    evidence_oui,
    extract_vendor_strings,
)
from server.discovery.result import (
    DiscoveredDevice,
    merge_device_info,
)

log = logging.getLogger("discovery")

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
        self.status: str = "idle"  # idle, running, complete, cancelled
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
        }


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
        from server.system_config import get_system_config
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

        Installed drivers register first; their rules win on (kind, source_id,
        txt_match) collisions. Community drivers fill in coverage for devices
        not yet installed — that's how discovery surfaces "Install & Add"
        candidates for unfamiliar gear.
        """
        installed_ids: set[str] = {
            str(d.get("id") or "") for d in self._installed_registry
        }

        installed_hints = load_discovery_hints(self._installed_registry)

        # Skip catalog drivers already represented by an installed driver —
        # the installed copy is authoritative (may be a newer version with
        # corrected hints).
        catalog_only = [
            d for d in community_drivers
            if str(d.get("id") or "") not in installed_ids
        ]
        community_hints = load_discovery_hints(catalog_only)

        all_hints = installed_hints + community_hints
        self.discovery_hints = all_hints

        try:
            self.signal_index = build_signal_index(all_hints)
        except ValueError as exc:
            # Strong-signal collisions abort the build. Fall back to an
            # installed-only index so an inconsistent catalog can't break
            # discovery on the device.
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
        timeout: float = 120.0,
    ) -> str:
        """Start a discovery scan. Returns scan_id.

        Args:
            subnets: CIDR ranges to scan. Auto-detect if None.
            extra_subnets: Additional ranges to scan alongside auto-detected ones.
            on_update: Async callback for live WebSocket push.
            timeout: Max scan duration in seconds.
        """
        async with self._scan_lock:
            if self._scan_task and not self._scan_task.done():
                raise RuntimeError("Scan already running")

            self._scan_counter += 1
            scan_id = f"scan_{self._scan_counter}_{int(time.time())}"

            # Determine target subnets (filtered to control interface if set)
            control_ip = self._get_control_interface()
            if control_ip:
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
                raise ValueError("No subnets to scan. Specify subnets manually.")

            self._on_update = on_update
            self.scan_status = ScanStatus()
            self.scan_status.scan_id = scan_id
            self.scan_status.status = "running"
            self.scan_status.started_at = time.time()
            self.scan_status.subnets = targets

            # Store active adapter info for UI display
            if control_ip:
                from server.discovery.network_scanner import get_network_adapters
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
            for device in self.results.values():
                device.alive = False

            self._scan_task = asyncio.create_task(
                self._run_scan(targets, timeout)
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
        """Execute the full scan pipeline."""
        try:
            await asyncio.wait_for(self._scan_pipeline(subnets), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("Scan timed out after %.0fs", timeout)
        except asyncio.CancelledError:
            log.info("Scan cancelled")
            raise
        except Exception:  # Catch-all: isolates any scan pipeline error to ensure cleanup runs
            log.exception("Scan failed")
        finally:
            self.scan_status.status = (
                "cancelled" if self.scan_status.status == "cancelled" else "complete"
            )
            self.scan_status.duration = time.time() - self.scan_status.started_at
            self.scan_status.devices_found = len(self.results)
            await self._emit({
                "type": "discovery_complete",
                "scan_id": self.scan_status.scan_id,
                "total_devices": len(self.results),
                "duration_seconds": self.scan_status.duration,
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
          8: Run the deterministic matcher per device + finalize

        Every signal-producing phase appends ``Evidence`` records to the
        device's ``evidence_log``. The matcher runs once at finalize and
        produces an ``IdentificationMatch`` — identified, possible, or
        unknown — based purely on those records.
        """
        gentle = self.config.get("gentle_mode", False)
        depth = self.config.get("scan_depth", "standard")
        ping_concurrency = 10 if gentle else 50
        snmp_enabled = self.config.get("snmp_enabled", True)
        snmp_community = self.config.get("snmp_community", "public")
        control_ip = self._get_control_interface()

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

        mdns_scanner = MDNSScanner(service_types=mdns_service_types)
        ssdp_scanner = SSDPScanner()
        amx_ddp_scanner = AMXDDPScanner(control_ip=control_ip)

        # Passive listeners run throughout all active scan phases and are
        # stopped explicitly in phase 7 when we're ready to collect.
        # The 600s cap is a safety net; they'll be stopped much sooner.
        mdns_task = asyncio.create_task(mdns_scanner.start(duration=600.0))
        ssdp_task = asyncio.create_task(ssdp_scanner.scan(
            timeout=600.0, fetch_descriptions=True,
        ))
        amx_ddp_task = asyncio.create_task(amx_ddp_scanner.start(duration=600.0))

        snmp_task: asyncio.Task | None = None

        try:
            # --- Phase 3: Ping Sweep ---
            await self._set_phase(3, "ping_sweep", "Scanning for live hosts...")

            async def on_ping_found(ip: str) -> None:
                device = self._get_or_create(ip)
                device.alive = True
                await self._emit_device_update(device, "ping_sweep")

            # Track per-host ping progress for smooth progress bar
            ping_total = sum(
                max(0, ipaddress.IPv4Network(s, strict=False).num_addresses - 2)
                for s in subnets
            )
            ping_done = 0

            async def on_ping_progress(completed: int, total: int) -> None:
                nonlocal ping_done
                ping_done = completed
                if total > 0:
                    await self._update_intra_progress(completed / total)

            alive_ips = await ping_sweep(
                subnets,
                concurrency=ping_concurrency,
                on_found=on_ping_found,
                on_progress=on_ping_progress,
                min_prefix=self.config.get("max_subnet_size", 20),
                source_ip=control_ip,
            )
            self.scan_status.total_hosts_scanned = ping_total

            if not alive_ips:
                log.info("No live hosts found — will still collect passive results")
                # Don't return — passive listeners may have found devices

            # --- Phase 4: ARP Harvest + OUI Lookup + Hostname Resolution ---
            await self._set_phase(4, "arp_harvest", "Reading MAC addresses and hostnames...")

            if alive_ips:
                arp_table = await harvest_arp_table()

                # Resolve hostnames + NetBIOS concurrently (best-effort)
                hostname_task = asyncio.create_task(_resolve_hostnames(alive_ips))
                netbios_task = None
                if depth != "quick":
                    netbios_task = asyncio.create_task(
                        netbios_sweep(alive_ips, concurrency=30, timeout=1.0)
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
                        merge_device_info(device, info, "arp")
                        await self._emit_device_update(device, "arp_harvest")

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

            community_drivers = await self.community_index.get_drivers()
            for drv in community_drivers:
                for p in drv.get("ports", []):
                    if isinstance(p, int):
                        port_set.add(p)

            # Thorough mode: add a handful of generic extended ports.
            # No vendor labels — these are common alternate web / RTSP
            # / management ports.
            if depth == "thorough":
                port_set.update([554, 3000, 4000, 5060, 8443, 8888, 9000, 10000])

            port_list = sorted(port_set)
            log.info(
                "Port scan: %d ports (%d baseline + driver/catalog)",
                len(port_list), len(BASELINE_PORTS),
            )

            if alive_ips:
                total = len(alive_ips)

                for i, ip in enumerate(alive_ips):
                    device = self._get_or_create(ip)
                    open_ports = await scan_host_ports(ip, port_list, timeout=1.0)

                    if open_ports:
                        merge_device_info(device, {"open_ports": open_ports}, "port_scan")

                        # Grab banners from banner-friendly ports.
                        banners = await grab_banners(ip, open_ports, timeout=2.0)
                        if banners:
                            merge_device_info(device, {"banners": banners}, "banner")

                        await self._emit_device_update(device, "port_scan")

                    # Update progress within this phase
                    await self._update_intra_progress((i + 1) / total)

                    # Gentle mode: small delay between hosts
                    if gentle and i < total - 1:
                        await asyncio.sleep(0.1)

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
                use_entity_mib = depth != "quick"
                snmp_task = asyncio.create_task(
                    snmp_scanner.scan_devices(
                        alive_ips,
                        community=snmp_community,
                        timeout=2.0,
                        concurrency=snmp_concurrency,
                        entity_mib=use_entity_mib,
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

            await self._collect_passive_results(mdns_task, ssdp_task, amx_ddp_task)
            await self._collect_snmp_results(snmp_task)

            # Follow-up: port scan devices found only by passive discovery
            # (mDNS/SSDP/AMX-DDP) that weren't in the ping sweep, so the
            # custom-probe pass below sees their open ports.
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
                for ip in passive_only:
                    device = self._get_or_create(ip)
                    open_ports = await scan_host_ports(ip, port_list, timeout=1.0)
                    if open_ports:
                        merge_device_info(device, {"open_ports": open_ports}, "port_scan")
                    await self._emit_device_update(device, "passive_followup")

            # Driver-declared probes run after the full host inventory is
            # known — UDP broadcasts, TCP probes against every host whose
            # port-scan results include the spec port, and Python
            # companions. Evidence lands in each device's evidence_log
            # before the matcher runs in phase 8.
            await self._run_custom_probes(subnets, control_ip)

            # --- Phase 8: Run the matcher per device + Finalize ---
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
                # carrying ``manufacturer=NEC`` surfaces a driver that
                # claims that alias without needing an OUI hit.
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

        except asyncio.CancelledError:
            # Clean up background tasks on cancellation
            mdns_task.cancel()
            ssdp_task.cancel()
            amx_ddp_task.cancel()
            tasks_to_cancel = [mdns_task, ssdp_task, amx_ddp_task]
            if snmp_task:
                snmp_task.cancel()
                tasks_to_cancel.append(snmp_task)
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
            raise

    async def _collect_passive_results(
        self,
        mdns_task: asyncio.Task,
        ssdp_task: asyncio.Task,
        amx_ddp_task: asyncio.Task,
    ) -> None:
        """Wait for passive listeners and append their evidence to each device.

        Uses a 1-second heartbeat loop so the progress bar moves steadily
        instead of appearing stuck while waiting for SSDP XML fetches.
        """
        depth = self.config.get("scan_depth", "standard")
        total_wait = {"quick": 5.0, "thorough": 30.0}.get(depth, 15.0)
        remaining_tasks = {mdns_task, ssdp_task, amx_ddp_task}
        elapsed = 0.0
        tick = 1.0

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

        for task in remaining_tasks:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        mdns_results = self._task_result(mdns_task, "mDNS")
        for ip, mdns_result in mdns_results.items():
            device = self._get_or_create(ip)
            device.alive = True
            ev = mdns_result.to_evidence()
            if ev is not None:
                device.evidence_log.append(ev)
            merge_device_info(device, mdns_result.to_device_info(), "mdns")
            await self._emit_device_update(device, "mdns")

        ssdp_results = self._task_result(ssdp_task, "SSDP")
        for ip, ssdp_result in ssdp_results.items():
            device = self._get_or_create(ip)
            device.alive = True
            ev = ssdp_result.to_evidence()
            if ev is not None:
                device.evidence_log.append(ev)
            merge_device_info(device, ssdp_result.to_device_info(), "ssdp")
            await self._emit_device_update(device, "ssdp")

        amx_results = self._task_result(amx_ddp_task, "AMX DDP")
        for ip, beacon in amx_results.items():
            device = self._get_or_create(ip)
            device.alive = True
            device.evidence_log.append(beacon.to_evidence())
            merge_device_info(device, beacon.to_device_info(), "amx_ddp")
            await self._emit_device_update(device, "amx_ddp")

    def _task_result(self, task: asyncio.Task, label: str) -> dict:
        if not task.done() or task.cancelled():
            return {}
        try:
            return task.result() or {}
        except Exception:  # Catch-all: task.result() re-raises whatever the task raised
            log.debug("%s task failed", label, exc_info=True)
            return {}

    async def _collect_snmp_results(self, snmp_task: asyncio.Task | None) -> None:
        """Wait for SNMP scan and merge results."""
        if snmp_task is None:
            return

        # Wait with timeout
        done, pending = await asyncio.wait([snmp_task], timeout=5.0)

        # Cancel if still running
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        snmp_results = {}
        if snmp_task.done() and not snmp_task.cancelled():
            try:
                snmp_results = snmp_task.result()
            except Exception:  # Catch-all: task.result() re-raises whatever the task raised
                log.debug("SNMP task failed", exc_info=True)

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
    ) -> None:
        """Dispatch driver-declared probes.

        Walks ``self.discovery_hints`` for any declared
        ``udp_probe:`` / ``tcp_probe:`` / ``python:`` specs:

        - **UDP probes** fire once per scan against every subnet's
          directed broadcast address, sharing a single 10/sec
          ``RateLimiter``.
        - **TCP probes** run against every host whose port-scan results
          include the spec's port, with a 20 ms stagger so the SYN burst
          is spread.
        - **Python companions** are invoked with a ``ProbeContext``
          carrying the engine's port-scan map so the companion can
          consume already-discovered hosts instead of re-iterating
          subnets.

        Resulting Evidence is appended to each device's ``evidence_log``
        for the matcher to consume in phase 8.
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
                        await self._emit_device_update(device, "broadcast_probe")

        if tcp_specs:
            async def _run_one_tcp(spec, target, idx):
                ev = await run_tcp_active_probe(
                    spec,
                    target=target,
                    source_ip=control_ip,
                    stagger_ms=idx * 20.0,
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
                    await self._emit_device_update(device, "protocol_probe")

        if self._discovery_companions:
            async def _emit_for_host(host: str, ev) -> None:
                device = self._get_or_create(host)
                device.alive = True
                device.evidence_log.append(ev)
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
            if companion_tasks:
                await asyncio.gather(*companion_tasks, return_exceptions=True)

    def _get_or_create(self, ip: str) -> DiscoveredDevice:
        """Get existing device record or create a new one."""
        if ip not in self.results:
            self.results[ip] = DiscoveredDevice(ip=ip)
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
        """Update progress within the current phase (fraction 0.0 – 1.0)."""
        phase = self.scan_status.phase
        base = self._phase_base_progress(phase)
        weight = self._phase_weight(phase)
        self.scan_status.progress = base + weight * min(fraction, 1.0)

    async def _emit_progress(self, phase: str, message: str) -> None:
        """Emit a discovery_phase event with current progress."""
        await self._emit({
            "type": "discovery_phase",
            "phase": phase,
            "phase_number": self.scan_status.phase_number,
            "total_phases": self.scan_status.total_phases,
            "message": message,
            "progress": self.scan_status.progress,
        })

    async def _emit_device_update(self, device: DiscoveredDevice, phase: str) -> None:
        """Emit a device update event."""
        await self._emit({
            "type": "discovery_update",
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
