"""REST API routes for device discovery."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from server.api.auth import require_programmer_auth
from server.api.errors import api_error as _api_error
from server.discovery.engine import DiscoveryEngine
from server.utils.logger import get_logger

log = get_logger(__name__)

# Lazy import to avoid circular deps — set by main.py
_app_engine = None  # The main OpenAVC engine (for add-device)

def set_app_engine(engine) -> None:
    global _app_engine
    _app_engine = engine


router = APIRouter(
    prefix="/api/discovery",
    dependencies=[Depends(require_programmer_auth)],
)

_engine: DiscoveryEngine | None = None
_broadcast_fn = None  # async fn to broadcast WebSocket messages


def set_discovery_engine(discovery_engine: DiscoveryEngine) -> None:
    global _engine
    _engine = discovery_engine


def set_broadcast_fn(fn) -> None:
    """Set the WebSocket broadcast function for live scan updates."""
    global _broadcast_fn
    _broadcast_fn = fn


def _get_engine() -> DiscoveryEngine:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Discovery engine not initialized")
    return _engine


async def refresh_all_device_matches() -> None:
    """Reload driver hints and re-match all discovered devices.

    Call this after installing or uninstalling drivers so discovery
    results stay in sync with the current driver registry.
    """
    if _engine is None:
        return

    from server.core.device_manager import get_driver_registry

    _engine.load_driver_hints_from_registry(get_driver_registry())

    for ip in list(_engine.results.keys()):
        updated = await _engine.refresh_device_matches(ip)
        if updated and _broadcast_fn:
            try:
                await _broadcast_fn({
                    "type": "discovery_update",
                    "device": updated,
                    "phase": "refresh",
                    "progress": 1.0,
                })
            except Exception:
                pass


async def _build_port_labels(engine: DiscoveryEngine) -> dict[str, str]:
    """Build port label map from hardcoded AV_PORTS + community driver data.

    The frontend uses this to display port descriptions and filter AV devices.
    Community driver ports fill in gaps not covered by the base AV_PORTS table.
    """
    from server.discovery.port_scanner import AV_PORTS

    labels: dict[str, str] = {}

    # Base: hardcoded AV ports (shortened for display)
    for port, desc in AV_PORTS.items():
        short = desc.split("(")[0].strip() if "(" in desc else desc
        labels[str(port)] = short

    # Community drivers: add ports not already covered
    community_drivers = await engine.community_index.get_drivers()
    for drv in community_drivers:
        for p in drv.get("ports", []):
            if isinstance(p, int) and str(p) not in labels:
                labels[str(p)] = drv.get("name", f"Port {p}")

    return labels


# --- Request models ---

ScanDepth = Literal["quick", "standard", "thorough"]

_DEPTH_TIMEOUTS: dict[str, float] = {
    "quick": 60.0,
    "standard": 120.0,
    "thorough": 180.0,
}


class ScanRequest(BaseModel):
    subnets: list[str] | None = None
    extra_subnets: list[str] | None = None
    snmp_enabled: bool = True
    snmp_community: str = "public"
    gentle_mode: bool = False
    scan_depth: ScanDepth = "standard"
    max_subnet_size: int = 20  # Min CIDR prefix (/20=4K hosts, /16=65K)
    timeout: float | None = None  # None = auto from scan_depth


class DiscoveryConfigRequest(BaseModel):
    snmp_enabled: bool = True
    snmp_community: str = "public"
    gentle_mode: bool = False
    scan_depth: ScanDepth = "standard"
    max_subnet_size: int = 20


class AddDeviceRequest(BaseModel):
    ip: str
    driver_id: str
    name: str = ""
    config: dict[str, Any] = {}


class InstallAndMatchRequest(BaseModel):
    ip: str
    driver_id: str
    file_url: str


# --- Endpoints ---

@router.post("/scan")
async def start_scan(req: ScanRequest) -> dict[str, Any]:
    """Start a new discovery scan."""
    engine = _get_engine()

    # Apply config
    engine.config["snmp_enabled"] = req.snmp_enabled
    engine.config["snmp_community"] = req.snmp_community
    engine.config["gentle_mode"] = req.gentle_mode
    engine.config["scan_depth"] = req.scan_depth
    max_prefix = max(8, min(24, req.max_subnet_size))  # Clamp to /8../24
    engine.config["max_subnet_size"] = max_prefix

    # Validate subnet inputs
    import ipaddress
    all_subnets = (req.subnets or []) + (req.extra_subnets or [])
    if len(all_subnets) > 10:
        raise HTTPException(status_code=400, detail="Too many subnets (max 10)")
    for subnet in all_subnets:
        try:
            net = ipaddress.ip_network(subnet, strict=False)
            if net.prefixlen < max_prefix:
                raise HTTPException(
                    status_code=400,
                    detail=f"Subnet {subnet} too large (min prefix /{max_prefix})",
                )
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid CIDR: {subnet}")

    timeout = req.timeout if req.timeout is not None else _DEPTH_TIMEOUTS.get(req.scan_depth, 120.0)

    try:
        scan_id = await engine.start_scan(
            subnets=req.subnets,
            extra_subnets=req.extra_subnets,
            on_update=_broadcast_fn,
            timeout=timeout,
        )
    except RuntimeError as e:
        raise _api_error(409, "A scan is already in progress", e)
    except ValueError as e:
        raise _api_error(400, str(e) or "Invalid scan parameters", e)

    status = engine.get_status()
    return {
        "scan_id": scan_id,
        "status": status["status"],
        "subnets": status["subnets"],
        "started_at": status["started_at"],
    }


@router.get("/status")
async def get_scan_status() -> dict[str, Any]:
    """Get current scan progress."""
    return _get_engine().get_status()


@router.get("/results")
async def get_results(
    min_confidence: float = 0.0,
    category: str | None = None,
    sort: str = "confidence",
) -> dict[str, Any]:
    """Get discovered devices."""
    engine = _get_engine()
    devices = engine.get_results()

    # Filter by min confidence
    if min_confidence > 0:
        devices = [d for d in devices if d["confidence"] >= min_confidence]

    # Filter by category
    if category:
        devices = [d for d in devices if d.get("category") == category]

    # Sort
    if sort == "ip":
        def _ip_sort_key(d):
            try:
                return tuple(int(p) for p in d["ip"].split("."))
            except (ValueError, AttributeError):
                return (999, 999, 999, 999)
        devices.sort(key=_ip_sort_key)
    elif sort == "manufacturer":
        devices.sort(key=lambda d: (d.get("manufacturer") or "zzz").lower())
    elif sort == "category":
        devices.sort(key=lambda d: (d.get("category") or "zzz").lower())
    # Default: already sorted by confidence (descending) from engine

    # Build dynamic port labels from AV_PORTS + community driver data
    port_labels = await _build_port_labels(engine)

    status = engine.get_status()
    return {
        "scan_id": status["scan_id"],
        "status": status["status"],
        "devices": devices,
        "total_hosts_scanned": status["total_hosts_scanned"],
        "total_alive": sum(1 for d in devices if d.get("alive")),
        "total_devices": len(devices),
        "scan_duration_seconds": status["duration"],
        "port_labels": port_labels,
    }


@router.post("/stop")
async def stop_scan() -> dict[str, str]:
    """Stop a running scan."""
    await _get_engine().stop_scan()
    return {"status": "stopped"}


@router.post("/clear")
async def clear_results() -> dict[str, str]:
    """Clear all discovery results."""
    _get_engine().clear_results()
    return {"status": "cleared"}


@router.get("/subnets")
async def get_subnets() -> dict[str, list[str]]:
    """Get auto-detected subnets."""
    return {"subnets": _get_engine().get_subnets()}


@router.put("/config")
async def update_config(req: DiscoveryConfigRequest) -> dict[str, str]:
    """Update discovery settings."""
    engine = _get_engine()
    engine.config["snmp_enabled"] = req.snmp_enabled
    engine.config["snmp_community"] = req.snmp_community
    engine.config["gentle_mode"] = req.gentle_mode
    engine.config["scan_depth"] = req.scan_depth
    engine.config["max_subnet_size"] = max(8, min(24, req.max_subnet_size))
    return {"status": "ok"}


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Get current discovery settings (community string masked)."""
    config = dict(_get_engine().config)
    if config.get("snmp_community"):
        config["snmp_community"] = "****"
    return config


@router.post("/add-device")
async def add_device(req: AddDeviceRequest) -> dict[str, Any]:
    """Add a discovered device to the project.

    Creates a new device entry in the project from discovery results.
    The device is immediately connected.
    """
    if _app_engine is None:
        raise HTTPException(status_code=503, detail="Engine not available")

    discovery = _get_engine()

    # Look up the discovered device for extra info
    discovered = discovery.results.get(req.ip)

    # Build device ID from driver + IP suffix (handle IPv4 and IPv6)
    import re
    ip_suffix = re.sub(r'[.:\-]', '_', req.ip)
    raw_id = f"{req.driver_id}_{ip_suffix}"
    device_id = re.sub(r'[^a-z0-9_]', '_', raw_id.lower())

    # Use provided name, or build one from discovery info
    name = req.name
    if not name and discovered:
        name = discovered.model or discovered.device_name or f"{discovered.manufacturer or 'Device'} ({req.ip})"
    if not name:
        name = f"{req.driver_id} ({req.ip})"

    # Merge suggested config with any overrides from the request
    config = {"host": req.ip}
    if discovered and discovered.matched_drivers:
        for m in discovered.matched_drivers:
            if m.driver_id == req.driver_id:
                config.update(m.suggested_config)
                break
    config.update(req.config)

    # Split config into connection fields and protocol fields
    from server.core.project_migration import CONNECTION_FIELDS
    protocol_config = {}
    conn_overrides = {}
    for key, value in config.items():
        if key in CONNECTION_FIELDS:
            conn_overrides[key] = value
        else:
            protocol_config[key] = value

    device_config = {
        "id": device_id,
        "driver": req.driver_id,
        "name": name,
        "config": protocol_config,
        "enabled": True,
    }

    # Pass merged config (protocol + connection) to the device manager
    runtime_config = dict(device_config)
    runtime_config["config"] = {**protocol_config, **conn_overrides}

    try:
        await _app_engine.devices.add_device(runtime_config)
    except Exception as e:
        raise _api_error(400, f"Failed to add device '{device_id}'", e)

    # Save to project with connection table separation
    try:
        from server.core.project_loader import save_project
        if _app_engine.project:
            from server.core.project_loader import DeviceConfig
            _app_engine.project.devices.append(DeviceConfig(**device_config))
            if conn_overrides:
                _app_engine.project.connections[device_id] = conn_overrides
            save_project(_app_engine.project_path, _app_engine.project)
    except Exception as e:
        # Device was added to runtime but project save failed
        raise _api_error(500, f"Device '{device_id}' added but project save failed", e)

    # Notify the IDE to refresh the project (so Devices tab updates immediately)
    if _broadcast_fn:
        try:
            await _broadcast_fn({"type": "project.reloaded"})
        except Exception:
            pass  # Non-critical — worst case user refreshes manually

    return {
        "status": "ok",
        "device_id": device_id,
        "name": name,
    }


@router.post("/install-and-match")
async def install_and_match(req: InstallAndMatchRequest) -> dict[str, Any]:
    """Install a community driver and add the device to the project.

    One-click flow: install driver → add device to project → return result.
    """
    from server.api.rest import install_community_driver, CommunityDriverInstallRequest

    engine = _get_engine()

    # Step 1: Install the driver (also refreshes discovery matches)
    install_req = CommunityDriverInstallRequest(
        driver_id=req.driver_id,
        file_url=req.file_url,
    )
    await install_community_driver(install_req)

    # Step 2: Add device to project (reuse add_device logic)
    add_req = AddDeviceRequest(ip=req.ip, driver_id=req.driver_id)
    try:
        result = await add_device(add_req)
    except HTTPException as e:
        # Driver installed but add failed — return partial success
        device = engine.results.get(req.ip)
        return {
            "status": "installed_not_added",
            "error": e.detail,
            "device": device.to_dict() if device else None,
        }

    # Return the updated discovery device + add result
    device = engine.results.get(req.ip)
    return {
        "status": "ok",
        "device_id": result.get("device_id"),
        "name": result.get("name"),
        "device": device.to_dict() if device else None,
    }


@router.get("/export", response_class=PlainTextResponse)
async def export_results() -> str:
    """Export discovered devices as a plain text report."""
    engine = _get_engine()
    devices = engine.get_results()

    if not devices:
        return "No devices discovered.\n"

    lines: list[str] = []
    lines.append("OpenAVC Discovery Report")
    lines.append("=" * 60)

    status = engine.get_status()
    if status["scan_id"]:
        lines.append(f"Scan: {status['scan_id']}")
        lines.append(f"Subnets: {', '.join(status['subnets'])}")
        lines.append(f"Duration: {status['duration']:.1f}s")
    lines.append(f"Devices found: {len(devices)}")
    lines.append("")

    for d in devices:
        confidence = f"{d['confidence'] * 100:.0f}%"
        name = d.get("model") or d.get("device_name") or "Unknown"
        mfg = d.get("manufacturer") or ""
        cat = d.get("category") or ""

        lines.append(f"  {d['ip']:<16} {confidence:>4}  {mfg + ' ' if mfg else ''}{name}")

        details: list[str] = []
        if d.get("mac"):
            details.append(f"MAC: {d['mac']}")
        if d.get("hostname"):
            details.append(f"Hostname: {d['hostname']}")
        if cat:
            details.append(f"Category: {cat}")
        if d.get("firmware"):
            details.append(f"Firmware: {d['firmware']}")
        if d.get("serial_number"):
            details.append(f"Serial: {d['serial_number']}")
        if d.get("protocols"):
            details.append(f"Protocols: {', '.join(d['protocols'])}")
        if d.get("open_ports"):
            details.append(f"Ports: {', '.join(str(p) for p in d['open_ports'])}")
        if d.get("matched_drivers"):
            driver_strs = []
            for m in d["matched_drivers"]:
                src = m.get("source", "installed")
                conf = f"{m['confidence'] * 100:.0f}%"
                driver_strs.append(f"{m['driver_name']} ({src}, {conf})")
            details.append(f"Drivers: {', '.join(driver_strs)}")

        # SNMP info
        snmp = d.get("snmp_info")
        if snmp:
            if snmp.get("sysName"):
                details.append(f"SNMP Name: {snmp['sysName']}")
            if snmp.get("sysLocation"):
                details.append(f"Location: {snmp['sysLocation']}")

        for detail in details:
            lines.append(f"    {detail}")
        lines.append("")

    return "\n".join(lines)
