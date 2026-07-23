"""REST API routes for device discovery."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict

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
    # Re-fold the community catalog so un-installed drivers stay matchable
    # — discovery's whole job is suggesting what to install next.
    await _engine.refresh_signal_index_with_catalog()

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
    """Build port label map from baseline + driver hints + community catalog.

    The frontend uses this to display port descriptions next to open ports.
    Vendor-specific labels come from drivers (which declare ports via
    ``tcp_probe.port`` and ``port_open``) and from the community catalog
    for un-installed drivers. The baseline labels the universal generic
    ports (SSH, Telnet, HTTP, HTTPS, HTTP-alt) — these stay generic so
    they don't get stomped by a driver that happens to probe HTTP.
    """
    from server.discovery.port_scanner import BASELINE_PORTS

    # Generic descriptions for the universal baseline. Not AV-specific
    # — any device on the LAN may listen here.
    baseline_labels: dict[int, str] = {
        22: "SSH",
        23: "Telnet",
        80: "HTTP",
        443: "HTTPS",
        8080: "HTTP alt",
    }

    labels: dict[str, str] = {}
    for port in BASELINE_PORTS:
        labels[str(port)] = baseline_labels.get(port, f"Port {port}")

    # Loaded drivers: contribute their declared probe / open-port ports
    # with the driver's display name. ``setdefault`` keeps the generic
    # baseline label for shared ports (HTTP/HTTPS/etc.).
    for hint in engine.discovery_hints:
        name = hint.driver_name or hint.driver_id
        if hint.tcp_probe is not None:
            labels.setdefault(str(hint.tcp_probe.port), name)
        for p in hint.port_open:
            labels.setdefault(str(p), name)

    # Community catalog: fill in ports for drivers the user hasn't
    # installed yet so the device card still labels them.
    community_drivers = await engine.community_index.get_drivers()
    for drv in community_drivers:
        for p in drv.get("ports", []):
            if isinstance(p, int):
                labels.setdefault(str(p), drv.get("name", f"Port {p}"))

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
    # None/omitted = keep the stored community. GET /config never returns the
    # value (it's a credential), so clients that echoed the response back
    # would otherwise overwrite the real community with the placeholder.
    snmp_community: str | None = None
    gentle_mode: bool = False
    scan_depth: ScanDepth = "standard"
    max_subnet_size: int = 20  # Min CIDR prefix (/20=4K hosts, /16=65K)
    timeout: float | None = None  # None = auto from scan_depth


class DiscoveryConfigRequest(BaseModel):
    snmp_enabled: bool = True
    # None/omitted = keep the stored community (see ScanRequest).
    snmp_community: str | None = None
    gentle_mode: bool = False
    scan_depth: ScanDepth = "standard"
    max_subnet_size: int = 20


class AddDeviceRequest(BaseModel):
    # Reject unknown fields with a 422 instead of the default extra='ignore'
    # silent drop. That default once swallowed a client sending an obsolete
    # per-device `group` field (device grouping moved to project-level
    # `device_groups` in v0.4.0), so the assignment vanished with no error.
    model_config = ConfigDict(extra="forbid")

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
    if req.snmp_community is not None:
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
    state: str | None = None,
    category: str | None = None,
    sort: str = "state",
) -> dict[str, Any]:
    """Get discovered devices.

    The Phase 6 result shape is:
      ``state`` -> identification.state (identified | possible | unknown)
      ``driver_id`` -> set when identified
      ``candidates`` -> populated when possible
      ``source`` -> the signal that produced the match
      ``evidence_log`` -> full audit trail for the "Why?" UI reveal
    """
    engine = _get_engine()
    devices = engine.get_results()

    if state:
        devices = [
            d for d in devices
            if (d.get("identification") or {}).get("state") == state
        ]

    if category:
        devices = [d for d in devices if d.get("category") == category]

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
    # Default: identified > possible > unknown (engine handles ordering)

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
        "warnings": status["warnings"],
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
    if req.snmp_community is not None:
        engine.config["snmp_community"] = req.snmp_community
    engine.config["gentle_mode"] = req.gentle_mode
    engine.config["scan_depth"] = req.scan_depth
    engine.config["max_subnet_size"] = max(8, min(24, req.max_subnet_size))
    return {"status": "ok"}


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Get current discovery settings (community string never returned)."""
    config = dict(_get_engine().config)
    # The community string is a credential: report whether one is set, never
    # the value itself. A returned value (even masked) gets loaded into the
    # settings form and echoed back on save/scan, overwriting the real one.
    config["snmp_community_set"] = bool(config.pop("snmp_community", ""))
    return config


def _sanitize_add_device_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate caller-supplied device config before it is persisted.

    ``add_device`` merges this dict straight into the saved ``.avc``, so it
    must be a flat map of JSON primitives. Keys are driver-defined (we don't
    second-guess them against a schema, which would break the deliberately
    flexible config model) but must be non-empty, reasonably short strings;
    values must be ``str``/``int``/``float``/``bool``/``None``. A nested
    object/array or non-serializable value would corrupt the project file or
    surprise the driver at runtime — reject it with a 422 instead.
    """
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="config must be an object")
    clean: dict[str, Any] = {}
    for key, value in config.items():
        if not isinstance(key, str) or not key or len(key) > 128:
            raise HTTPException(status_code=422, detail=f"Invalid config key: {key!r}")
        # bool is a subclass of int — listing it is documentation, not logic.
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise HTTPException(
                status_code=422,
                detail=f"config['{key}'] must be a string, number, boolean, or null",
            )
        if isinstance(value, str) and len(value) > 4096:
            raise HTTPException(
                status_code=422, detail=f"config['{key}'] value is too long"
            )
        clean[key] = value
    return clean


@router.post("/add-device")
async def add_device(req: AddDeviceRequest) -> dict[str, Any]:
    """Add a discovered device to the project.

    Creates a new device entry in the project from discovery results.
    The device is immediately connected.
    """
    if _app_engine is None:
        raise HTTPException(status_code=503, detail="Engine not available")
    if _app_engine.project is None:
        raise HTTPException(status_code=503, detail="No project loaded")

    discovery = _get_engine()

    # Look up the discovered device for extra info
    discovered = discovery.results.get(req.ip)

    # Build device ID from driver + IP suffix (handle IPv4 and IPv6)
    import re
    ip_suffix = re.sub(r'[.:\-]', '_', req.ip)
    raw_id = f"{req.driver_id}_{ip_suffix}"
    device_id = re.sub(r'[^a-z0-9_]', '_', raw_id.lower())

    # Idempotency guard. The id is deterministic from driver+IP, so re-adding a
    # device the project already has is the same device — not a new one. Without
    # this, the project ``devices`` list (no uniqueness check) would gain a
    # duplicate DeviceConfig while the ``connections`` dict kept only one entry,
    # and device_manager.add_device would overwrite the live driver without
    # disconnecting it, leaking its open transport. Reject the duplicate before
    # any runtime/project mutation; editing an existing device goes through
    # PUT /api/devices/{id}.
    already_added = any(
        d.id == device_id for d in _app_engine.project.devices
    ) or _app_engine.devices.get_device_config(device_id) is not None
    if already_added:
        raise HTTPException(
            status_code=409,
            detail=f"Device '{device_id}' is already in the project",
        )

    # Use provided name, or build one from discovery info
    name = req.name
    if not name and discovered:
        name = discovered.model or discovered.device_name or f"{discovered.manufacturer or 'Device'} ({req.ip})"
    if not name:
        name = f"{req.driver_id} ({req.ip})"
    # Sanitize: strip HTML, collapse whitespace, limit length
    name = re.sub(r"<[^>]+>", "", name)
    name = " ".join(name.split())[:128]

    # Layer the device's config so it lands in the project pre-filled,
    # not just at runtime:
    #   driver defaults  <  request overrides  <  discovery-provided host
    # Empty-string / None defaults are dropped so we don't write blanks
    # over fields the user can fill later (mirrors the manual Add Device
    # dialog's prefill behavior).
    from server.core.device_manager import get_driver_default_config
    from server.core.project_migration import CONNECTION_FIELDS

    driver_defaults = {
        k: v for k, v in get_driver_default_config(req.driver_id).items()
        if v is not None and v != ""
    }

    config = {**driver_defaults, "host": req.ip}
    config.update(_sanitize_add_device_config(req.config))

    # Split config into connection fields and protocol fields
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

    # Persist and reconcile through the one seam, letting the devices
    # reconcile instantiate and connect the runtime device from the resolved
    # config. The revision bump and project.reloaded broadcast mean an open
    # IDE's stale full-project PUT gets a 409 instead of silently deleting
    # the just-discovered device. The duplicate check runs again inside the
    # mutate — an add racing this one may have appended the id after the
    # idempotency guard above.
    from server.core.project_loader import DeviceConfig

    def mutate(project):
        if any(d.id == device_id for d in project.devices):
            raise HTTPException(
                status_code=409,
                detail=f"Device '{device_id}' is already in the project",
            )
        project.devices.append(DeviceConfig(**device_config))
        if conn_overrides:
            project.connections[device_id] = conn_overrides

    try:
        await _app_engine.apply_project_edit(mutate)
    except HTTPException:
        raise
    except Exception as e:
        raise _api_error(500, f"Failed to add device '{device_id}'", e)

    # Seed the Open Web UI URL from the ports the scan already found open, so the
    # button shows immediately without waiting on (or repeating) the add-time
    # probe. Only helps in auto-detect mode — seed_web_ui_url no-ops otherwise.
    if discovered and discovered.open_ports:
        from server.core.web_ui_probe import web_ui_url_from_open_ports

        seed_url = web_ui_url_from_open_ports(req.ip, discovered.open_ports)
        if seed_url:
            _app_engine.devices.seed_web_ui_url(device_id, seed_url)

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
        ident = d.get("identification") or {}
        state = ident.get("state", "unknown")
        name = d.get("model") or d.get("device_name") or "Unknown"
        mfg = d.get("manufacturer") or ""
        cat = d.get("category") or ""

        lines.append(f"  {d['ip']:<16} {state:>10}  {mfg + ' ' if mfg else ''}{name}")

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
        if ident.get("driver_id"):
            details.append(f"Driver: {ident['driver_id']} (via {ident.get('source', '?')})")
        elif ident.get("candidates"):
            details.append(f"Possible: {', '.join(ident['candidates'])}")

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
