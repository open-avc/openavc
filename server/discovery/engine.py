"""Discovery engine — orchestrates all scanning methods."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket as _socket
import time
from typing import Any, Callable, Awaitable

from server.discovery.network_scanner import get_local_subnets, ping_sweep, harvest_arp_table, netbios_sweep
from server.discovery.port_scanner import scan_host_ports, grab_banners, AV_PORTS
from server.discovery.protocol_prober import probe_device as run_protocol_probes
from server.discovery.oui_database import OUIDatabase
from server.discovery.driver_matcher import DriverMatcher, CommunityDriverMatcher
from server.discovery.hints import load_driver_hints
from server.discovery.community_index import CommunityIndexCache
from server.discovery.mdns_scanner import MDNSScanner
from server.discovery.ssdp_scanner import SSDPScanner
from server.discovery.snmp_scanner import SNMPScanner
from server.discovery.result import (
    DiscoveredDevice,
    merge_device_info,
    compute_confidence,
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


class DiscoveryEngine:
    """Orchestrates device discovery across all scanning methods."""

    def __init__(self) -> None:
        self.oui_db = OUIDatabase()
        self.driver_matcher: DriverMatcher | None = None
        self.community_index = CommunityIndexCache()
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

    def load_driver_hints_from_registry(self, registry: list[dict[str, Any]]) -> None:
        """Load driver hints from the driver registry for matching.

        Also merges MAC prefixes from driver hints into the OUI database
        so they're available during the ARP/OUI scan phase — not just
        during driver matching.
        """
        hints = load_driver_hints(registry)
        self.driver_matcher = DriverMatcher(hints)

        # Enrich OUI database with MAC prefixes from installed drivers
        added = 0
        for hint in hints:
            for prefix in hint.mac_prefixes:
                before = len(self.oui_db._table)
                self.oui_db.add_prefix(prefix, hint.manufacturer, hint.category)
                if len(self.oui_db._table) > before:
                    added += 1

        log.info(
            "Driver matcher loaded with %d driver hints (%d new OUI prefixes)",
            len(hints), added,
        )

    def get_results(self) -> list[dict[str, Any]]:
        """Return current discovery results sorted by confidence (highest first)."""
        devices = sorted(
            self.results.values(),
            key=lambda d: d.confidence,
            reverse=True,
        )
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
        """Re-run both installed + community matching for a single device.

        Used after installing a community driver so the device card
        updates without a full rescan.
        """
        device = self.results.get(ip)
        if not device:
            return None

        device.matched_drivers.clear()

        # Installed driver matching
        installed_ids: set[str] = set()
        if self.driver_matcher:
            installed_ids = {h.driver_id for h in self.driver_matcher.hints}
            matches = self.driver_matcher.match_device(device)
            device.matched_drivers.extend(matches)

        # Community driver matching
        community_drivers = await self.community_index.get_drivers()
        if community_drivers:
            community_matcher = CommunityDriverMatcher(
                community_drivers, installed_ids,
            )
            community_matches = community_matcher.match_device(device)
            device.matched_drivers.extend(community_matches)

        # Sort: installed first, then by confidence descending
        device.matched_drivers.sort(
            key=lambda m: (0 if m.source == "installed" else 1, -m.confidence),
        )

        if device.matched_drivers and "driver_matched" not in device.sources:
            device.sources.append("driver_matched")
            device.confidence = compute_confidence(device.sources)

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
        """The core scan phases.

        Phase layout:
          1: Subnet detection (already done)
          2: Start passive listeners (mDNS + SSDP, run in background)
          3: Ping sweep
          4: ARP harvest + OUI lookup + hostname resolution
          5: Port scan + banner grab
          6: Protocol probes + SNMP (SNMP runs concurrently as background task)
          7: Collect passive + SNMP results
          8: Driver matching + finalize
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
        await self._set_phase(2, "passive_listen", "Starting mDNS and SSDP listeners...")

        mdns_scanner = MDNSScanner()
        ssdp_scanner = SSDPScanner()

        # Passive listeners run throughout all active scan phases and are
        # stopped explicitly in phase 7 when we're ready to collect.
        # The 600s cap is a safety net; they'll be stopped much sooner.
        mdns_task = asyncio.create_task(mdns_scanner.start(duration=600.0))
        ssdp_task = asyncio.create_task(ssdp_scanner.scan(
            timeout=600.0, fetch_descriptions=True,
        ))

        snmp_task: asyncio.Task | None = None

        try:
            # --- Phase 3: Ping Sweep ---
            await self._set_phase(3, "ping_sweep", "Scanning for live hosts...")

            async def on_ping_found(ip: str) -> None:
                device = self._get_or_create(ip)
                device.alive = True
                if "alive" not in device.sources:
                    device.sources.append("alive")
                    device.confidence = compute_confidence(device.sources)
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

                    # Hostname from reverse DNS
                    hostname = hostnames.get(ip)
                    if hostname:
                        info["hostname"] = hostname

                    # NetBIOS hostname (may override or supplement DNS)
                    nbt = netbios_results.get(ip)
                    if nbt:
                        nbt_name = nbt.get("hostname")
                        if nbt_name:
                            # Use NetBIOS name as device_name
                            info["device_name"] = nbt_name
                            # Use as hostname if reverse DNS failed
                            if not hostname:
                                info["hostname"] = nbt_name
                            if "netbios_resolved" not in device.sources:
                                device.sources.append("netbios_resolved")

                    # MAC + OUI
                    mac = arp_table.get(ip)
                    if mac:
                        info["mac"] = mac
                        if "mac_known" not in device.sources:
                            device.sources.append("mac_known")

                        oui_result = self.oui_db.lookup(mac)
                        if oui_result:
                            manufacturer, category = oui_result
                            info["manufacturer"] = manufacturer
                            info["category"] = category
                            if self.oui_db.is_av_manufacturer(mac):
                                if "oui_av_mfg" not in device.sources:
                                    device.sources.append("oui_av_mfg")

                    if info:
                        merge_device_info(device, info, "arp")
                        await self._emit_device_update(device, "arp_harvest")

            # --- Phase 5: Port Scan ---
            await self._set_phase(5, "port_scan", "Probing AV ports...")

            # Build port list unconditionally — also used for passive follow-up
            port_set = set(AV_PORTS.keys())
            if self.driver_matcher:
                for hint in self.driver_matcher.hints:
                    port_set.update(hint.ports)
            community_drivers = await self.community_index.get_drivers()
            for drv in community_drivers:
                for p in drv.get("ports", []):
                    if isinstance(p, int):
                        port_set.add(p)
            # Thorough mode: add extended ports for broader coverage
            if depth == "thorough":
                port_set.update([
                    554,    # RTSP (cameras, encoders)
                    3000,   # Various web UIs
                    4000,   # Various control protocols
                    5060,   # SIP (VoIP, conferencing)
                    8443,   # HTTPS alt
                    8888,   # HTTP alt
                    9000,   # Various web UIs
                    10000,  # Various management
                ])
            port_list = sorted(port_set)
            log.info("Port scan: %d ports (%d base + driver/community)", len(port_list), len(AV_PORTS))

            if alive_ips:
                total = len(alive_ips)

                for i, ip in enumerate(alive_ips):
                    device = self._get_or_create(ip)
                    open_ports = await scan_host_ports(ip, port_list, timeout=1.0)

                    if open_ports:
                        has_av_port = any(p in AV_PORTS for p in open_ports)
                        info = {"open_ports": open_ports}
                        if has_av_port and "av_port_open" not in device.sources:
                            device.sources.append("av_port_open")

                        merge_device_info(device, info, "port_scan")

                        # Grab banners from banner-friendly ports
                        banners = await grab_banners(ip, open_ports, timeout=2.0)
                        if banners:
                            merge_device_info(device, {"banners": banners}, "banner")

                        await self._emit_device_update(device, "port_scan")

                    # Update progress within this phase
                    await self._update_intra_progress((i + 1) / total)

                    # Gentle mode: small delay between hosts
                    if gentle and i < total - 1:
                        await asyncio.sleep(0.1)

            # --- Phase 6: Protocol Probes + SNMP (concurrent) ---
            await self._set_phase(6, "protocol_probe", "Identifying device protocols...")

            # Start SNMP as a concurrent background task if enabled
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

            # Only probe devices that have open ports
            probe_targets = [
                (ip, dev) for ip, dev in self.results.items()
                if dev.open_ports
            ]
            total_probes = len(probe_targets)

            for i, (ip, device) in enumerate(probe_targets):
                probe_results = await run_protocol_probes(
                    ip, device.open_ports, device.banners or None
                )
                for pr in probe_results:
                    info: dict[str, Any] = {"protocols": [pr.protocol]}
                    if pr.manufacturer:
                        info["manufacturer"] = pr.manufacturer
                    if pr.model:
                        info["model"] = pr.model
                    if pr.device_name:
                        info["device_name"] = pr.device_name
                    if pr.firmware:
                        info["firmware"] = pr.firmware
                    if pr.category:
                        info["category"] = pr.category

                    if "probe_confirmed" not in device.sources:
                        device.sources.append("probe_confirmed")
                    if pr.model and "model_known" not in device.sources:
                        device.sources.append("model_known")

                    # Track protocol-specific sources for confidence
                    if pr.protocol == "https" and "tls_cert_matched" not in device.sources:
                        if pr.manufacturer:
                            device.sources.append("tls_cert_matched")
                    if pr.protocol == "ssh" and "ssh_identified" not in device.sources:
                        device.sources.append("ssh_identified")
                    if pr.protocol == "smb" and "smb_identified" not in device.sources:
                        if pr.device_name:
                            device.sources.append("smb_identified")
                    if pr.extra.get("www_auth_realm") and "www_auth_matched" not in device.sources:
                        device.sources.append("www_auth_matched")

                    # Check banners for banner_matched source
                    if device.banners and "banner_matched" not in device.sources:
                        device.sources.append("banner_matched")

                    merge_device_info(device, info, "probe")
                    await self._emit_device_update(device, "protocol_probe")

                # Update progress within this phase
                if total_probes > 0:
                    await self._update_intra_progress((i + 1) / total_probes)

                if gentle and i < total_probes - 1:
                    await asyncio.sleep(0.05)

            # --- Phase 7: Collect Passive + SNMP Results ---
            await self._set_phase(7, "passive_collect", "Collecting passive and SNMP results...")

            # Signal passive listeners to stop. They exit their receive
            # loops within 0.5s; SSDP then fetches UPnP XML descriptions
            # for everything it found before the task completes.
            mdns_scanner._running = False
            ssdp_scanner._running = False

            await self._collect_passive_results(mdns_task, ssdp_task)
            await self._collect_snmp_results(snmp_task)

            # Follow-up: port scan + probe devices found only by passive
            # discovery (mDNS/SSDP) that weren't in the ping sweep
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
                        has_av_port = any(p in AV_PORTS for p in open_ports)
                        if has_av_port and "av_port_open" not in device.sources:
                            device.sources.append("av_port_open")
                        merge_device_info(device, {"open_ports": open_ports}, "port_scan")
                        banners = await grab_banners(ip, open_ports, timeout=2.0)
                        if banners:
                            merge_device_info(device, {"banners": banners}, "banner")
                        probe_results = await run_protocol_probes(
                            ip, open_ports, banners or None,
                        )
                        for pr in probe_results:
                            pinfo: dict[str, Any] = {"protocols": [pr.protocol]}
                            if pr.manufacturer:
                                pinfo["manufacturer"] = pr.manufacturer
                            if pr.model:
                                pinfo["model"] = pr.model
                            if pr.device_name:
                                pinfo["device_name"] = pr.device_name
                            if pr.firmware:
                                pinfo["firmware"] = pr.firmware
                            if pr.category:
                                pinfo["category"] = pr.category
                            if "probe_confirmed" not in device.sources:
                                device.sources.append("probe_confirmed")
                            if pr.model and "model_known" not in device.sources:
                                device.sources.append("model_known")
                            merge_device_info(device, pinfo, "probe")
                    await self._emit_device_update(device, "passive_followup")

            # --- Phase 8: Driver Matching + Finalize ---
            await self._set_phase(8, "finalize", "Matching drivers...")

            # Collect installed driver IDs for community matcher filtering
            installed_ids: set[str] = set()
            if self.driver_matcher:
                installed_ids = {h.driver_id for h in self.driver_matcher.hints}

            # Run installed driver matching
            finalize_total = len(self.results)
            if self.driver_matcher:
                for i, device in enumerate(self.results.values()):
                    matches = self.driver_matcher.match_device(device)
                    if matches:
                        device.matched_drivers = matches
                        if "driver_matched" not in device.sources:
                            device.sources.append("driver_matched")
                        await self._emit_device_update(device, "driver_match")
                    if finalize_total > 0:
                        await self._update_intra_progress((i + 1) / finalize_total * 0.5)

            # Run community driver matching
            community_drivers = await self.community_index.get_drivers()
            if community_drivers:
                community_matcher = CommunityDriverMatcher(
                    community_drivers, installed_ids,
                )
                for i, device in enumerate(self.results.values()):
                    community_matches = community_matcher.match_device(device)
                    if community_matches:
                        device.matched_drivers.extend(community_matches)
                        # Sort: installed first, then by confidence descending
                        device.matched_drivers.sort(
                            key=lambda m: (0 if m.source == "installed" else 1, -m.confidence),
                        )
                        await self._emit_device_update(device, "community_match")
                    if finalize_total > 0:
                        await self._update_intra_progress(0.5 + (i + 1) / finalize_total * 0.5)

            # Remove devices that were not re-discovered in this scan
            stale_ips = [ip for ip, dev in self.results.items() if not dev.alive]
            for ip in stale_ips:
                del self.results[ip]
            if stale_ips:
                log.info(
                    "Removed %d stale devices not found in this scan", len(stale_ips)
                )

            # Recalculate all confidence scores
            for device in self.results.values():
                device.confidence = compute_confidence(device.sources)

            self.scan_status.devices_found = len(self.results)

        except asyncio.CancelledError:
            # Clean up background tasks on cancellation
            mdns_task.cancel()
            ssdp_task.cancel()
            tasks_to_cancel = [mdns_task, ssdp_task]
            if snmp_task:
                snmp_task.cancel()
                tasks_to_cancel.append(snmp_task)
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
            raise

    async def _collect_passive_results(
        self,
        mdns_task: asyncio.Task,
        ssdp_task: asyncio.Task,
    ) -> None:
        """Wait for passive listeners and merge their results into the main results.

        Uses a 1-second heartbeat loop so the progress bar moves steadily
        instead of appearing stuck while waiting for SSDP XML fetches.
        """
        depth = self.config.get("scan_depth", "standard")
        total_wait = {"quick": 5.0, "thorough": 30.0}.get(depth, 15.0)
        remaining_tasks = {mdns_task, ssdp_task}
        elapsed = 0.0
        tick = 1.0

        while elapsed < total_wait and remaining_tasks:
            done, pending = await asyncio.wait(
                remaining_tasks, timeout=tick,
            )
            remaining_tasks = pending
            elapsed += tick
            fraction = min(elapsed / total_wait, 1.0)
            await self._update_intra_progress(fraction)
            secs_left = max(0, int(total_wait - elapsed))
            await self._emit_progress(
                "passive_collect",
                f"Collecting passive results... ({secs_left}s remaining)",
            )

        # Cancel any still-running tasks
        for task in remaining_tasks:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        # Merge mDNS results
        mdns_results = {}
        if mdns_task.done() and not mdns_task.cancelled():
            try:
                mdns_results = mdns_task.result()
            except Exception:  # Catch-all: task.result() re-raises whatever the task raised
                log.debug("mDNS task failed", exc_info=True)

        for ip, mdns_result in mdns_results.items():
            device = self._get_or_create(ip)
            device.alive = True
            info = mdns_result.to_device_info()

            if "mdns_advertised" not in device.sources:
                device.sources.append("mdns_advertised")

            merge_device_info(device, info, "mdns")
            await self._emit_device_update(device, "mdns")

        # Merge SSDP results
        ssdp_results = {}
        if ssdp_task.done() and not ssdp_task.cancelled():
            try:
                ssdp_results = ssdp_task.result()
            except Exception:  # Catch-all: task.result() re-raises whatever the task raised
                log.debug("SSDP task failed", exc_info=True)

        for ip, ssdp_result in ssdp_results.items():
            device = self._get_or_create(ip)
            device.alive = True
            info = ssdp_result.to_device_info()

            if "ssdp_identified" not in device.sources:
                device.sources.append("ssdp_identified")

            merge_device_info(device, info, "ssdp")
            await self._emit_device_update(device, "ssdp")

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
            info = snmp_info.to_device_info()

            if "snmp_identified" not in device.sources:
                device.sources.append("snmp_identified")
            if snmp_info.entity_model and "entity_mib_found" not in device.sources:
                device.sources.append("entity_mib_found")

            merge_device_info(device, info, "snmp")
            await self._emit_device_update(device, "snmp")

        if snmp_results:
            log.info("SNMP enriched %d devices", len(snmp_results))

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
