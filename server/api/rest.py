"""
OpenAVC REST API endpoints.

Provides programmatic access for external integrations, testing, and
the cloud monitoring portal.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from server.api.auth import require_programmer_auth
from server.api.errors import api_error as _api_error
from server.api.models import (
    CloudPairRequest,
    CommandRequest,
    CommunityDriverInstallRequest,
    DeviceSettingRequest,
    DeviceUpdateRequest,
    DriverDefinitionRequest,
    PendingSettingsRequest,
    PythonDriverCreateRequest,
    StateSetRequest,
    ScriptCreateRequest,
    TestCommandRequest,
)
from server.core.project_loader import DeviceConfig, ProjectConfig, save_project
from server.core.project_migration import CONNECTION_FIELDS
from server.utils.log_buffer import get_log_buffer
from server.utils.logger import get_logger

log = get_logger(__name__)

# Open router — no auth required (status, templates)
open_router = APIRouter(prefix="/api")
# Protected router — requires programmer auth when configured
router = APIRouter(prefix="/api", dependencies=[Depends(require_programmer_auth)])

# The engine is injected by main.py after creation
_engine = None


def set_engine(engine) -> None:
    """Set the engine reference (called by main.py at startup)."""
    global _engine
    _engine = engine


def _get_engine():
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not started")
    return _engine


# --- Rate limiting for expensive test endpoints ---

import time as _time_mod

_test_endpoint_last_call: dict[str, float] = {}
_TEST_RATE_LIMIT_SECONDS = 2.0


def _rate_limit_test(endpoint_key: str) -> None:
    """Raise 429 if the same test endpoint was called within the rate limit window."""
    now = _time_mod.monotonic()
    last = _test_endpoint_last_call.get(endpoint_key, 0.0)
    if now - last < _TEST_RATE_LIMIT_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests — wait {_TEST_RATE_LIMIT_SECONDS:.0f}s between test calls",
        )
    _test_endpoint_last_call[endpoint_key] = now




# --- System ---


@open_router.get("/startup-status")
async def startup_status() -> dict[str, Any]:
    """Returns whether the engine has finished initializing."""
    return {"ready": True, "error": None}


@open_router.get("/status")
async def get_status() -> dict[str, Any]:
    """System status, uptime, project info."""
    return _get_engine().get_status()


@open_router.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check for monitoring and container orchestration."""
    engine = _get_engine()
    status = engine.get_status()
    devices = status.get("devices", {})
    return {
        "status": "healthy",
        "version": status.get("version", "unknown"),
        "uptime_seconds": status.get("uptime_seconds", 0),
        "devices": {
            "total": devices.get("total", 0),
            "connected": devices.get("connected", 0),
            "error": devices.get("error", 0),
        },
        "cloud": {
            "connected": status.get("cloud_connected", False),
        },
    }


# --- State ---


@router.get("/state")
async def get_state() -> dict[str, Any]:
    """Full state snapshot."""
    return _get_engine().state.snapshot()


@router.get("/state/history")
async def get_state_history(count: int = 50) -> list[dict[str, Any]]:
    """Recent state change history."""
    engine = _get_engine()
    return engine.state.get_history(min(count, 10000))


@router.get("/state/{key:path}")
async def get_state_value(key: str) -> dict[str, Any]:
    """Single state value."""
    engine = _get_engine()
    value = engine.state.get(key)
    return {"key": key, "value": value}


@router.put("/state/{key:path}")
async def set_state_value(key: str, body: StateSetRequest) -> dict[str, Any]:
    """Set a state value."""
    if not _is_flat_primitive(body.value):
        raise _api_error(
            422,
            "Value must be a flat primitive (str, int, float, bool, or null)",
        )
    engine = _get_engine()
    engine.state.set(key, body.value, source="api")
    return {"key": key, "value": body.value}


def _is_flat_primitive(value: object) -> bool:
    """Check that a value is a flat primitive (str, int, float, bool, None)."""
    return value is None or isinstance(value, (str, int, float, bool))


# --- Devices ---


@router.get("/devices")
async def list_devices() -> list[dict[str, Any]]:
    """List all devices with status."""
    return _get_engine().devices.list_devices()


@router.get("/devices/{device_id}")
async def get_device(device_id: str) -> dict[str, Any]:
    """Device detail including state and commands."""
    engine = _get_engine()
    try:
        return engine.devices.get_device_info(device_id)
    except ValueError as e:
        raise _api_error(404, f"Device '{device_id}' not found", e)


@router.put("/devices/{device_id}")
async def update_device(device_id: str, body: DeviceUpdateRequest) -> dict[str, Any]:
    """Update a device's name, driver, or config. Hot-swaps the runtime device."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    # Find the device in the project config
    device_idx = None
    for i, d in enumerate(engine.project.devices):
        if d.id == device_id:
            device_idx = i
            break
    if device_idx is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    # Build updated config — split connection fields into connections table
    existing = engine.project.devices[device_idx]
    new_name = body.name if body.name is not None else existing.name
    new_driver = body.driver if body.driver is not None else existing.driver

    # Validate driver exists
    if new_driver != existing.driver:
        from server.core.device_manager import _DRIVER_REGISTRY
        if new_driver not in _DRIVER_REGISTRY:
            raise HTTPException(status_code=422, detail=f"Driver '{new_driver}' is not installed")

    if body.config is not None:
        # Split incoming config: connection fields → connections table, rest → device.config
        protocol_config = {}
        conn_overrides = dict(engine.project.connections.get(device_id, {}))
        for key, value in body.config.items():
            if key in CONNECTION_FIELDS:
                conn_overrides[key] = value
            else:
                protocol_config[key] = value
        new_config = protocol_config
        # Remove None values from connection overrides
        conn_overrides = {k: v for k, v in conn_overrides.items() if v is not None}
        if conn_overrides:
            engine.project.connections[device_id] = conn_overrides
        elif device_id in engine.project.connections:
            del engine.project.connections[device_id]
    else:
        new_config = existing.config

    updated = DeviceConfig(
        id=device_id,
        driver=new_driver,
        name=new_name,
        config=new_config,
        enabled=existing.enabled if body.enabled is None else body.enabled,
    )

    # Update project config, save, and hot-swap device
    engine.project.devices[device_idx] = updated
    save_project(engine.project_path, engine.project)
    # Pass merged config (protocol + connection) to the device manager
    resolved = engine.resolved_device_config(updated)
    await engine.devices.update_device(device_id, resolved)
    return {"status": "updated", "device_id": device_id}


@router.delete("/devices/{device_id}")
async def delete_device(device_id: str) -> dict[str, Any]:
    """Remove a device from the project and runtime."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    # Find and remove from project config
    original_count = len(engine.project.devices)
    engine.project.devices = [d for d in engine.project.devices if d.id != device_id]
    if len(engine.project.devices) == original_count:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    # Clean up connections table entry
    engine.project.connections.pop(device_id, None)

    save_project(engine.project_path, engine.project)
    await engine.devices.remove_device(device_id)
    return {"status": "deleted", "device_id": device_id}


@router.post("/devices/{device_id}/test")
async def test_device_connection(device_id: str) -> dict[str, Any]:
    """Test network reachability of a device without using the driver."""
    _rate_limit_test(f"test_device:{device_id}")
    import asyncio as _asyncio
    import time as _time

    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    # Find device config
    device_cfg = None
    for d in engine.project.devices:
        if d.id == device_id:
            device_cfg = d
            break
    if device_cfg is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    # Merge device.config with connection table overrides (host, port, etc.)
    conn = engine.project.connections.get(device_id, {})
    cfg = {**device_cfg.config, **conn}
    host = cfg.get("host", "")
    port = cfg.get("port")
    transport = cfg.get("transport", "tcp")

    start = _time.monotonic()

    if transport == "serial":
        # Test serial port open/close
        com_port = cfg.get("port", cfg.get("com_port", ""))
        baud = cfg.get("baud_rate", cfg.get("baud", 9600))
        try:
            import serial
            ser = serial.Serial(com_port, baud, timeout=2)
            ser.close()
            latency = round((_time.monotonic() - start) * 1000, 1)
            return {"success": True, "error": None, "latency_ms": latency}
        except (OSError, ValueError) as e:
            return {"success": False, "error": str(e), "latency_ms": None}

    elif transport == "http":
        # Test HTTP HEAD request
        url = cfg.get("base_url", cfg.get("url", ""))
        if not url and host:
            scheme = "https" if cfg.get("ssl") else "http"
            url = f"{scheme}://{host}" + (f":{port}" if port else "")
        if not url:
            return {"success": False, "error": "No URL configured", "latency_ms": None}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                await client.head(url)
            latency = round((_time.monotonic() - start) * 1000, 1)
            return {"success": True, "error": None, "latency_ms": latency}
        except (httpx.HTTPError, OSError, ValueError) as e:
            return {"success": False, "error": str(e), "latency_ms": None}

    else:
        # Default: TCP connection test
        if not host:
            return {"success": False, "error": "No host configured", "latency_ms": None}
        if not port:
            return {
                "success": False,
                "error": "No port configured — set a port in the device config to test connectivity",
                "latency_ms": None,
            }
        try:
            tcp_port = int(port)
        except (ValueError, TypeError):
            return {"success": False, "error": f"Invalid port value: {port}", "latency_ms": None}
        try:
            reader, writer = await _asyncio.wait_for(
                _asyncio.open_connection(host, tcp_port), timeout=5.0
            )
            writer.close()
            await writer.wait_closed()
            latency = round((_time.monotonic() - start) * 1000, 1)

            # Try protocol probe if port has a known probe
            protocol_status = None
            try:
                from server.discovery.protocol_prober import _PORT_PROBES
                probe_fns = _PORT_PROBES.get(tcp_port, [])
                if probe_fns:
                    for fn in probe_fns:
                        result = await _asyncio.wait_for(fn(host, tcp_port), timeout=5.0)
                        if result is not None:
                            protocol_status = "verified"
                            break
                    if protocol_status is None:
                        protocol_status = "not_verified"
            except Exception:
                # Catch-all: protocol probes are best-effort, failure is non-fatal
                protocol_status = "not_verified"

            return {"success": True, "error": None, "latency_ms": latency, "protocol_status": protocol_status}
        except _asyncio.TimeoutError:
            return {"success": False, "error": "Connection timed out (5s)", "latency_ms": None}
        except OSError as e:
            return {"success": False, "error": str(e), "latency_ms": None}


@router.post("/devices/{device_id}/reconnect")
async def reconnect_device(device_id: str) -> dict[str, Any]:
    """Force reconnect a device."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")
    # Verify device exists
    if not any(d.id == device_id for d in engine.project.devices):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    await engine.devices.reconnect_device(device_id)
    return {"status": "reconnecting", "device_id": device_id}


@router.post("/devices/{device_id}/retry")
async def retry_orphaned_device(device_id: str) -> dict[str, Any]:
    """Re-attempt adding an orphaned device after its driver has been installed."""
    engine = _get_engine()
    try:
        success = await engine.devices.retry_orphaned_device(device_id)
    except ValueError as e:
        raise _api_error(404, f"Device '{device_id}' not found or not orphaned", e)
    if success:
        return {"status": "activated", "device_id": device_id}
    return {"status": "still_orphaned", "device_id": device_id,
            "detail": "Driver is still not installed"}


@router.post("/devices/{device_id}/command")
async def send_command(device_id: str, body: CommandRequest) -> dict[str, Any]:
    """Send a command to a device."""
    engine = _get_engine()
    try:
        result = await engine.devices.send_command(
            device_id, body.command, body.params
        )
        return {"success": True, "result": result}
    except ValueError as e:
        raise _api_error(404, f"Device '{device_id}' not found", e)
    except ConnectionError as e:
        raise _api_error(503, f"Device '{device_id}' is not connected", e)
    except Exception as e:
        raise _api_error(500, f"Failed to send command '{body.command}' to device '{device_id}'", e)


# --- Device Settings ---


@router.get("/devices/{device_id}/settings")
async def get_device_settings(device_id: str) -> dict[str, Any]:
    """Get device settings with current values for a device."""
    engine = _get_engine()
    try:
        settings = engine.devices.get_device_settings(device_id)
        return {"device_id": device_id, "settings": settings}
    except ValueError as e:
        raise _api_error(404, f"Device '{device_id}' not found", e)


@router.put("/devices/{device_id}/settings/{setting_key}")
async def set_device_setting(
    device_id: str, setting_key: str, body: DeviceSettingRequest
) -> dict[str, Any]:
    """Write a device setting value to the device."""
    engine = _get_engine()
    try:
        await engine.devices.set_device_setting(device_id, setting_key, body.value)
        return {"success": True, "device_id": device_id, "key": setting_key, "value": body.value}
    except ValueError as e:
        raise _api_error(404, f"Device '{device_id}' or setting '{setting_key}' not found", e)
    except ConnectionError as e:
        raise _api_error(503, f"Device '{device_id}' is not connected", e)
    except NotImplementedError as e:
        raise _api_error(501, f"Device '{device_id}' does not support writable settings", e)
    except Exception as e:
        raise _api_error(500, f"Failed to set '{setting_key}' on device '{device_id}'", e)


@router.post("/devices/{device_id}/settings/pending")
async def store_pending_settings(
    device_id: str, body: PendingSettingsRequest
) -> dict[str, Any]:
    """Store device settings to be applied when the device connects."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    try:
        await engine.devices.store_pending_settings(device_id, body.settings)
    except ValueError as e:
        raise _api_error(404, f"Device '{device_id}' not found", e)

    # Persist to project file
    found = False
    for dev in engine.project.devices:
        if dev.id == device_id:
            if not dev.pending_settings:
                dev.pending_settings = {}
            dev.pending_settings.update(body.settings)
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found in project")

    save_project(engine.project_path, engine.project)
    return {"status": "pending", "device_id": device_id, "settings": body.settings}


# --- Project ---


@router.get("/project")
async def get_project() -> dict[str, Any]:
    """Get the full project configuration (includes runtime revision counter)."""
    engine = _get_engine()
    if engine.project:
        data = engine.project.model_dump(mode="json")
        data["_revision"] = engine._project_revision
        return data
    raise HTTPException(status_code=404, detail="No project loaded")


@router.post("/project/reload")
async def reload_project() -> dict[str, Any]:
    """Reload project.avc from disk."""
    engine = _get_engine()
    await engine.reload_project()
    return {"status": "reloaded"}


@router.put("/project")
async def save_project_config(request: Request) -> dict[str, Any]:
    """Save a full project configuration, then reload.

    If the request body contains a ``_revision`` field, the server checks
    it against the current revision.  A mismatch means another client
    saved since this client last loaded — return 409 Conflict so the
    frontend can prompt the user.
    """
    # Limit request body to 10 MB to prevent memory exhaustion
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Project file too large (max 10 MB)")
    except (ValueError, TypeError):
        pass  # Malformed content-length header — let FastAPI handle the body
    engine = _get_engine()
    body = await request.json()

    # Optimistic concurrency check (14.3)
    client_revision = body.pop("_revision", None)
    if client_revision is not None:
        try:
            rev = int(client_revision)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid _revision value")
        if rev != engine._project_revision:
            raise HTTPException(
                status_code=409,
                detail="Project was modified by another session. Reload to see the latest changes.",
            )

    try:
        project = ProjectConfig(**body)
    except Exception as e:
        raise _api_error(422, "Invalid project configuration", e)
    save_project(engine.project_path, project)
    await engine.reload_project()
    return {"status": "saved", "revision": engine._project_revision}


@router.get("/project/validate-drivers")
async def validate_drivers() -> dict[str, Any]:
    """Check which drivers required by the project are available or missing."""
    from server.core.device_manager import _DRIVER_REGISTRY

    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    available = []
    missing = []
    seen: set[str] = set()
    for device in engine.project.devices:
        driver_id = device.driver
        if driver_id in seen:
            continue
        seen.add(driver_id)
        if driver_id in _DRIVER_REGISTRY:
            available.append(driver_id)
        else:
            affected = [d.id for d in engine.project.devices if d.driver == driver_id]
            missing.append({"driver_id": driver_id, "affected_devices": affected})

    return {"available": available, "missing": missing}


# --- Connections (Site Config) ---


@router.get("/connections")
async def get_connections() -> dict[str, dict[str, Any]]:
    """Get the full connection table (site-specific device connection overrides)."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")
    return dict(engine.project.connections)


@router.put("/connections/{device_id}")
async def update_connection(device_id: str, request: Request) -> dict[str, Any]:
    """Update connection overrides for a single device."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    # Verify device exists
    if not any(d.id == device_id for d in engine.project.devices):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    overrides = await request.json()
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=422, detail="Connection overrides must be a JSON object")
    engine.project.connections[device_id] = overrides
    save_project(engine.project_path, engine.project)

    # Hot-swap device with new connection info
    for i, d in enumerate(engine.project.devices):
        if d.id == device_id:
            resolved = engine.resolved_device_config(d)
            await engine.devices.update_device(device_id, resolved)
            break

    return {"status": "updated", "device_id": device_id}


@router.put("/connections")
async def update_connections_bulk(request: Request) -> dict[str, Any]:
    """Bulk-update the entire connection table."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    table = await request.json()
    if not isinstance(table, dict) or not all(isinstance(v, dict) for v in table.values()):
        raise HTTPException(status_code=422, detail="Connection table must be a JSON object of objects")
    engine.project.connections = table
    save_project(engine.project_path, engine.project)

    # Hot-reload all devices with new connections
    await engine._sync_devices()
    return {"status": "updated", "count": len(table)}


@router.delete("/connections/{device_id}")
async def delete_connection(device_id: str) -> dict[str, Any]:
    """Remove connection overrides for a device (reverts to config defaults)."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    removed = engine.project.connections.pop(device_id, None)
    if removed is None:
        raise HTTPException(status_code=404, detail=f"No connection overrides for '{device_id}'")

    save_project(engine.project_path, engine.project)

    # Re-sync the device with config defaults only
    for d in engine.project.devices:
        if d.id == device_id:
            resolved = engine.resolved_device_config(d)
            await engine.devices.update_device(device_id, resolved)
            break

    return {"status": "deleted", "device_id": device_id}


@router.get("/connections/export")
async def export_connections() -> dict[str, dict[str, Any]]:
    """Export the connection table as a site config JSON."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    # Enrich with device names for readability
    table = {}
    for device_id, conn in engine.project.connections.items():
        entry = dict(conn)
        for d in engine.project.devices:
            if d.id == device_id:
                entry["_device_name"] = d.name
                entry["_driver"] = d.driver
                break
        table[device_id] = entry
    return table


@router.post("/connections/import")
async def import_connections(request: Request) -> dict[str, Any]:
    """Import a site config JSON into the connection table."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    table = await request.json()
    if not isinstance(table, dict) or not all(isinstance(v, dict) for v in table.values()):
        raise HTTPException(status_code=422, detail="Import data must be a JSON object of objects")

    # Strip metadata fields (start with _) and apply
    cleaned: dict[str, dict[str, Any]] = {}
    for device_id, conn in table.items():
        cleaned[device_id] = {k: v for k, v in conn.items() if not k.startswith("_")}

    engine.project.connections = cleaned
    save_project(engine.project_path, engine.project)
    await engine._sync_devices()
    return {"status": "imported", "count": len(cleaned)}


# --- Drivers ---


@router.get("/drivers")
async def list_drivers() -> list[dict[str, Any]]:
    """List all available driver types with their metadata."""
    from server.core.device_manager import get_driver_registry
    return get_driver_registry()


@router.get("/drivers/{driver_id}/help")
async def get_driver_help(driver_id: str) -> dict[str, Any]:
    """Get help text (overview + setup instructions) for an installed driver."""
    from server.core.device_manager import get_driver_registry

    for drv in get_driver_registry():
        if drv.get("id") == driver_id:
            help_info = drv.get("help")
            if help_info and isinstance(help_info, dict):
                return {
                    "driver_id": driver_id,
                    "overview": help_info.get("overview", ""),
                    "setup": help_info.get("setup", ""),
                }
            raise HTTPException(status_code=404, detail="Driver has no help information")

    raise HTTPException(status_code=404, detail="Driver not found")


# --- Community / Installed Drivers ---

# Base URL for the community driver repo on GitHub
COMMUNITY_REPO_URL = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"


def _get_driver_repo_dir() -> Path:
    """Get the driver_repo/ directory path."""
    from server.system_config import DRIVER_REPO_DIR
    return DRIVER_REPO_DIR


@router.get("/drivers/community")
async def get_community_drivers() -> dict[str, Any]:
    """Fetch the community driver index from GitHub (cached)."""
    from server.discovery.community_index import CommunityIndexCache

    if not hasattr(get_community_drivers, "_cache"):
        get_community_drivers._cache = CommunityIndexCache()

    drivers = await get_community_drivers._cache.get_drivers()
    return {"drivers": drivers, "error": None if drivers else "Failed to fetch community drivers"}


@router.post("/drivers/install")
async def install_community_driver(body: CommunityDriverInstallRequest) -> dict[str, Any]:
    """Download and install a driver from the community repo."""
    import httpx
    from server.core.device_manager import register_driver
    from server.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from server.drivers.configurable import create_configurable_driver_class

    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    # Determine file type from URL
    url = body.file_url
    if url.endswith(".avcdriver"):
        ext = ".avcdriver"
    elif url.endswith(".py"):
        ext = ".py"
    else:
        raise HTTPException(status_code=422, detail="URL must point to a .avcdriver or .py file")

    # Sanitize filename from driver_id
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in body.driver_id)
    filename = f"{safe_id}{ext}"
    filepath = driver_repo / filename

    # Download the file
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            filepath.write_text(resp.text, encoding="utf-8")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"GitHub returned {e.response.status_code}")
    except httpx.RequestError as e:
        raise _api_error(502, f"Failed to download driver '{body.driver_id}'", e)

    # Register the driver
    try:
        if ext == ".avcdriver":
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="Invalid driver definition file")
            driver_class = create_configurable_driver_class(driver_def)
            register_driver(driver_class)
        else:
            driver_class = load_python_driver_file(filepath)
            if driver_class is None:
                filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="No valid driver class found in Python file")
            register_driver(driver_class)
    except HTTPException:
        raise
    except Exception as e:
        filepath.unlink(missing_ok=True)
        raise _api_error(500, f"Failed to load driver '{body.driver_id}'", e)

    # Refresh discovery engine with new driver hints
    from server.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    return {"status": "installed", "driver_id": body.driver_id, "file": filename}


@router.post("/drivers/upload")
async def upload_driver(request: Request) -> dict[str, Any]:
    """Upload a driver file (.avcdriver or .py) from the user's computer."""
    from server.core.device_manager import register_driver
    from server.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from server.drivers.configurable import create_configurable_driver_class

    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    # Accept multipart form data with a "file" field
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=422, detail="No file provided. Use 'file' field in multipart form.")

    raw_filename = upload.filename or "unknown"
    # Sanitize filename: strip directory components to prevent path traversal
    import re as _re
    from pathlib import PurePosixPath as _PurePosixPath
    filename = _PurePosixPath(raw_filename).name
    if not filename.endswith((".avcdriver", ".py")):
        raise HTTPException(status_code=422, detail="File must be .avcdriver or .py")
    # Reject filenames with suspicious characters (allow alphanumeric, hyphens, underscores, dots)
    if not _re.match(r'^[a-zA-Z0-9_\-]+\.(avcdriver|py)$', filename):
        raise HTTPException(status_code=422, detail="Invalid filename — use only letters, numbers, hyphens, and underscores")

    content = await upload.read()
    filepath = driver_repo / filename
    filepath.write_bytes(content)

    # Register the driver
    try:
        if filename.endswith(".avcdriver"):
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="Invalid driver definition file")
            driver_class = create_configurable_driver_class(driver_def)
            register_driver(driver_class)
            driver_id = driver_def.get("id", filename)
        else:
            driver_class = load_python_driver_file(filepath)
            if driver_class is None:
                filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="No valid driver class found in Python file")
            register_driver(driver_class)
            driver_id = driver_class.DRIVER_INFO.get("id", filename)
    except HTTPException:
        raise
    except Exception as e:
        filepath.unlink(missing_ok=True)
        raise _api_error(500, f"Failed to load uploaded driver '{filename}'", e)

    return {"status": "uploaded", "driver_id": driver_id, "file": filename}


@router.get("/drivers/installed")
async def list_installed_community_drivers() -> dict[str, Any]:
    """List drivers installed in driver_repo/."""
    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        return {"drivers": []}

    installed: list[dict[str, Any]] = []

    # Scan .avcdriver files
    for filepath in sorted(driver_repo.glob("*.avcdriver")):
        try:
            import yaml
            data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                installed.append({
                    "id": data.get("id", filepath.stem),
                    "name": data.get("name", filepath.stem),
                    "format": "avcdriver",
                    "filename": filepath.name,
                    "version": data.get("version", ""),
                })
        except (yaml.YAMLError, OSError):
            installed.append({
                "id": filepath.stem,
                "name": filepath.stem,
                "format": "avcdriver",
                "filename": filepath.name,
                "version": "",
            })

    # Scan .py files
    for filepath in sorted(driver_repo.glob("*.py")):
        if filepath.name.startswith("_"):
            continue
        driver_id = filepath.stem
        driver_name = filepath.stem.replace("_", " ").title()

        # Try to extract actual info from the loaded registry
        driver_version = ""
        from server.core.device_manager import _DRIVER_REGISTRY
        for reg_id, cls in _DRIVER_REGISTRY.items():
            info = cls.DRIVER_INFO
            # Match by checking if the module was loaded from this file
            if info.get("id") and filepath.stem in getattr(
                cls, "__module__", ""
            ):
                driver_id = info["id"]
                driver_name = info.get("name", driver_name)
                driver_version = info.get("version", "")
                break

        installed.append({
            "id": driver_id,
            "name": driver_name,
            "format": "python",
            "filename": filepath.name,
            "version": driver_version,
        })

    return {"drivers": installed}


@router.delete("/drivers/installed/{driver_id}")
async def uninstall_driver(driver_id: str) -> dict[str, Any]:
    """Uninstall a driver from driver_repo/ and unregister from memory."""
    from server.core.device_manager import unregister_driver

    # Safety check: don't allow uninstalling if devices are using this driver
    engine = _get_engine()
    if engine.project:
        using_devices = [
            d.id for d in engine.project.devices
            if d.driver == driver_id
        ]
        if using_devices:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot uninstall: driver is in use by device(s): {', '.join(using_devices)}",
            )

    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        raise HTTPException(status_code=404, detail="Driver not found")

    # Find the file by stem or by reading the driver ID from the file
    deleted_file = None
    for filepath in list(driver_repo.glob("*.avcdriver")) + list(driver_repo.glob("*.py")):
        if filepath.name.startswith("_"):
            continue
        if filepath.stem == driver_id:
            deleted_file = filepath
            break
        # Check actual ID inside YAML files
        try:
            if filepath.suffix == ".avcdriver":
                import yaml
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id") == driver_id:
                    deleted_file = filepath
                    break
        except (yaml.YAMLError, OSError):
            continue

    if not deleted_file:
        raise HTTPException(status_code=404, detail=f"Driver '{driver_id}' not found in driver_repo")

    deleted_file.unlink(missing_ok=True)
    unregister_driver(driver_id)

    # Refresh discovery engine so stale matches are cleared
    from server.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    return {"status": "uninstalled", "driver_id": driver_id}


@router.post("/drivers/installed/{driver_id}/update")
async def update_driver(driver_id: str, request: Request) -> dict[str, Any]:
    """Update an installed community driver to a newer version."""
    import httpx
    from server.core.device_manager import register_driver, unregister_driver
    from server.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from server.drivers.configurable import create_configurable_driver_class

    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        raise HTTPException(status_code=404, detail="Driver not found")

    body = await request.json()
    file_url = body.get("file_url")
    if not file_url:
        raise HTTPException(status_code=422, detail="file_url is required")

    # Find the existing file
    old_file = None
    for filepath in list(driver_repo.glob("*.avcdriver")) + list(driver_repo.glob("*.py")):
        if filepath.name.startswith("_"):
            continue
        if filepath.stem == driver_id:
            old_file = filepath
            break
        try:
            if filepath.suffix == ".avcdriver":
                import yaml
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id") == driver_id:
                    old_file = filepath
                    break
        except (yaml.YAMLError, OSError):
            continue

    if not old_file:
        raise HTTPException(status_code=404, detail=f"Driver '{driver_id}' not found in driver_repo")

    # Determine file type from URL
    if file_url.endswith(".avcdriver"):
        ext = ".avcdriver"
    elif file_url.endswith(".py"):
        ext = ".py"
    else:
        raise HTTPException(status_code=422, detail="URL must point to a .avcdriver or .py file")

    # Download new version
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in driver_id)
    new_filename = f"{safe_id}{ext}"
    new_filepath = driver_repo / new_filename

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            new_content = resp.text
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"GitHub returned {e.response.status_code}")
    except httpx.RequestError as e:
        raise _api_error(502, f"Failed to download driver '{driver_id}'", e)

    # Unregister old, delete old file, write new
    unregister_driver(driver_id)
    if old_file != new_filepath:
        old_file.unlink(missing_ok=True)
    new_filepath.write_text(new_content, encoding="utf-8")

    # Load and register new version
    try:
        if ext == ".avcdriver":
            driver_def = load_driver_file(new_filepath)
            if driver_def is None:
                raise HTTPException(status_code=422, detail="Invalid driver definition file")
            driver_class = create_configurable_driver_class(driver_def)
            register_driver(driver_class)
        else:
            driver_class = load_python_driver_file(new_filepath)
            if driver_class is None:
                raise HTTPException(status_code=422, detail="No valid driver class found in Python file")
            register_driver(driver_class)
    except HTTPException:
        raise
    except Exception as e:
        raise _api_error(500, f"Failed to load updated driver '{driver_id}'", e)

    from server.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    return {"status": "updated", "driver_id": driver_id, "file": new_filename}


# --- State History ---


# --- Macros ---


@router.post("/macros/{macro_id}/execute")
async def execute_macro(macro_id: str) -> dict[str, Any]:
    """Execute a macro by ID."""
    engine = _get_engine()
    try:
        await engine.macros.execute(macro_id)
    except ValueError as e:
        raise _api_error(404, str(e))
    except Exception as e:
        raise _api_error(500, f"Macro execution failed: {e}", exc=e)
    return {"status": "executed", "macro_id": macro_id}


@router.post("/macros/{macro_id}/cancel")
async def cancel_macro(macro_id: str) -> dict[str, Any]:
    """Cancel a running macro by ID."""
    engine = _get_engine()
    cancelled = await engine.macros.cancel(macro_id)
    if cancelled:
        return {"cancelled": True}
    return {"cancelled": False, "reason": "not_running"}


# --- Triggers ---


@router.get("/triggers")
async def list_triggers() -> list[dict[str, Any]]:
    """List all triggers with status."""
    engine = _get_engine()
    return engine.triggers.list_triggers()


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(trigger_id: str) -> dict[str, Any]:
    """Fire a trigger's macro immediately, bypassing conditions."""
    _rate_limit_test(f"test_trigger:{trigger_id}")
    engine = _get_engine()
    ok = await engine.triggers.test_trigger(trigger_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found")
    return {"status": "fired", "trigger_id": trigger_id}


# --- Scripts ---


def _get_scripts_dir() -> Path:
    """Get the scripts directory for the current project."""
    engine = _get_engine()
    return engine.project_path.parent / "scripts"


def _find_script_config(script_id: str) -> dict[str, Any] | None:
    """Find a script config by ID in the current project."""
    engine = _get_engine()
    if not engine.project:
        return None
    for s in engine.project.scripts:
        if s.id == script_id:
            return s.model_dump()
    return None


def _safe_script_path(scripts_dir: Path, filename: str) -> Path:
    """Resolve a script filename safely within the scripts directory."""
    resolved = (scripts_dir / filename).resolve()
    try:
        resolved.relative_to(scripts_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid script filename")
    return resolved


@router.get("/scripts/functions")
async def get_script_functions() -> list[dict[str, str]]:
    """Return all callable functions from loaded scripts."""
    engine = _get_engine()
    if not engine.scripts:
        return []
    return engine.scripts.get_callable_functions()


@router.get("/scripts/{script_id}/source")
async def get_script_source(script_id: str) -> dict[str, Any]:
    """Read a script's Python source code from disk."""
    cfg = _find_script_config(script_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' not found")
    scripts_dir = _get_scripts_dir()
    path = _safe_script_path(scripts_dir, cfg["file"])
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Script file not found: {cfg['file']}")
    source = path.read_text(encoding="utf-8")
    return {"id": script_id, "file": cfg["file"], "source": source}


@router.put("/scripts/{script_id}/source")
async def save_script_source(script_id: str, request: Request) -> dict[str, Any]:
    """Save a script's Python source code to disk."""
    cfg = _find_script_config(script_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' not found")
    body = await request.json()
    source = body.get("source", "")
    if len(source) > 1_000_000:
        raise HTTPException(status_code=413, detail="Script source too large (max 1 MB)")
    scripts_dir = _get_scripts_dir()
    path = _safe_script_path(scripts_dir, cfg["file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return {"status": "saved"}


@router.get("/scripts/references")
async def get_script_references() -> dict[str, Any]:
    """Scan all script source files for state key references."""
    import re

    engine = _get_engine()
    if not engine.project:
        return {"references": []}

    scripts_dir = _get_scripts_dir()
    references: list[dict[str, Any]] = []

    patterns = [
        (re.compile(r'state\.get\(\s*["\']((device|var|ui|system|isc)\.[^"\']+?)["\']'), "read"),
        (re.compile(r'state\.set\(\s*["\']((device|var|ui|system|isc)\.[^"\']+?)["\']'), "write"),
        (re.compile(r'@on_state_change\(\s*["\']((device|var|ui|system|isc)\.[\w.*]+)["\']'), "subscribe"),
        (re.compile(r'state\.get_namespace\(\s*["\']((device|var|ui|system|isc)\.[\w.]*)["\']'), "read"),
    ]

    for script in engine.project.scripts:
        script_data = script.model_dump()
        try:
            path = _safe_script_path(scripts_dir, script_data["file"])
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue

        for line_num, line in enumerate(source.splitlines(), 1):
            for pattern, usage_type in patterns:
                for match in pattern.finditer(line):
                    references.append({
                        "script_id": script.id,
                        "script_name": script_data.get("file", script.id),
                        "key": match.group(1),
                        "usage_type": usage_type,
                        "line": line_num,
                    })

    return {"references": references}


@router.post("/scripts")
async def create_script(request: Request) -> dict[str, Any]:
    """Create a new script entry and file."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")
    body = await request.json()
    data = ScriptCreateRequest(**body)

    # Check for duplicate ID
    for s in engine.project.scripts:
        if s.id == data.id:
            raise HTTPException(status_code=409, detail=f"Script '{data.id}' already exists")

    # Write the script file
    scripts_dir = _get_scripts_dir()
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = _safe_script_path(scripts_dir, data.file)
    path.write_text(data.source, encoding="utf-8")

    # Add to project config and save
    from server.core.project_loader import ScriptConfig
    new_script = ScriptConfig(
        id=data.id,
        file=data.file,
        enabled=data.enabled,
        description=data.description,
    )
    engine.project.scripts.append(new_script)
    save_project(engine.project_path, engine.project)
    await engine.reload_project()
    return {"status": "created", "id": data.id}


@router.delete("/scripts/{script_id}")
async def delete_script(script_id: str) -> dict[str, Any]:
    """Remove a script entry and delete its file."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    cfg = _find_script_config(script_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' not found")

    # Delete the file
    scripts_dir = _get_scripts_dir()
    path = _safe_script_path(scripts_dir, cfg["file"])
    if path.exists():
        path.unlink()

    # Remove from project config and save
    engine.project.scripts = [s for s in engine.project.scripts if s.id != script_id]
    save_project(engine.project_path, engine.project)
    await engine.reload_project()
    return {"status": "deleted"}


@router.post("/scripts/reload")
async def reload_scripts() -> dict[str, Any]:
    """Hot-reload all scripts."""
    engine = _get_engine()
    if not engine.scripts:
        raise HTTPException(status_code=503, detail="Script engine not initialized")
    scripts_data = [s.model_dump() for s in engine.project.scripts] if engine.project else []
    count = engine.scripts.reload_scripts(scripts_data)
    errors = engine.scripts.get_load_errors()
    return {"status": "reloaded", "handlers": count, "errors": errors}


@router.get("/scripts/errors")
async def get_script_errors() -> dict[str, str]:
    """Return load errors for scripts that failed to load."""
    engine = _get_engine()
    if not engine.scripts:
        return {}
    return engine.scripts.get_load_errors()


# --- Driver Definitions ---


def _get_driver_dirs() -> list[Path]:
    """Get directories containing driver definitions."""
    from server.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
    return [
        DRIVER_DEFINITIONS_DIR,
        DRIVER_REPO_DIR,
    ]


@router.get("/driver-definitions")
async def list_driver_definitions() -> list[dict]:
    """List all JSON driver definitions."""
    from server.drivers.driver_loader import list_driver_definitions as _list

    dirs = _get_driver_dirs()
    definitions = _list(dirs)
    # Strip internal _source_file from response
    for d in definitions:
        d.pop("_source_file", None)
    return definitions


@router.get("/driver-definitions/{driver_id}")
async def get_driver_definition(driver_id: str) -> dict:
    """Get a single JSON driver definition by ID."""
    from server.drivers.driver_loader import list_driver_definitions as _list

    dirs = _get_driver_dirs()
    for d in _list(dirs):
        if d.get("id") == driver_id:
            d.pop("_source_file", None)
            return d
    raise HTTPException(status_code=404, detail=f"Driver definition '{driver_id}' not found")


@router.post("/driver-definitions")
async def create_driver_definition(body: DriverDefinitionRequest) -> dict:
    """Create a new JSON driver definition."""
    from server.drivers.driver_loader import (
        list_driver_definitions as _list,
        save_driver_definition,
        validate_driver_definition,
    )
    from server.drivers.configurable import create_configurable_driver_class
    from server.core.device_manager import register_driver

    dirs = _get_driver_dirs()
    driver_def = body.model_dump(exclude_none=True)

    # Check for duplicate ID
    existing = _list(dirs)
    if any(d.get("id") == driver_def["id"] for d in existing):
        raise HTTPException(
            status_code=409,
            detail=f"Driver definition '{driver_def['id']}' already exists",
        )

    # Validate
    errors = validate_driver_definition(driver_def)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors, "message": f"{len(errors)} validation error(s) in driver definition"},
        )

    # Save to driver_repo (user/community directory)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(driver_def, save_dir)

    # Register immediately
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)

    return {"status": "created", "id": driver_def["id"]}


@router.put("/driver-definitions/{driver_id}")
async def update_driver_definition(driver_id: str, body: DriverDefinitionRequest) -> dict:
    """Update an existing JSON driver definition."""
    from server.drivers.driver_loader import (
        delete_driver_definition,
        list_driver_definitions as _list,
        save_driver_definition,
        validate_driver_definition,
    )
    from server.drivers.configurable import create_configurable_driver_class
    from server.core.device_manager import register_driver

    dirs = _get_driver_dirs()
    driver_def = body.model_dump(exclude_none=True)

    # Must already exist
    existing = _list(dirs)
    if not any(d.get("id") == driver_id for d in existing):
        raise HTTPException(
            status_code=404,
            detail=f"Driver definition '{driver_id}' not found",
        )

    # Validate
    errors = validate_driver_definition(driver_def)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors, "message": f"{len(errors)} validation error(s) in driver definition"},
        )

    # Delete old and save new
    delete_driver_definition(driver_id, dirs)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(driver_def, save_dir)

    # Re-register
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)

    return {"status": "updated", "id": driver_def["id"]}


@router.patch("/driver-definitions/{driver_id}")
async def patch_driver_definition(driver_id: str, body: dict) -> dict:
    """Partially update a driver definition (merge provided fields)."""
    from server.drivers.driver_loader import (
        delete_driver_definition,
        list_driver_definitions as _list,
        save_driver_definition,
        validate_driver_definition,
    )
    from server.drivers.configurable import create_configurable_driver_class
    from server.core.device_manager import register_driver

    dirs = _get_driver_dirs()

    # Find existing definition
    existing = _list(dirs)
    current = next((d for d in existing if d.get("id") == driver_id), None)
    if current is None:
        raise HTTPException(
            status_code=404,
            detail=f"Driver definition '{driver_id}' not found",
        )

    # Merge: shallow merge top-level keys from body into current
    merged = {**current, **body}
    # Don't allow changing ID via PATCH
    merged["id"] = driver_id

    # Validate merged result
    errors = validate_driver_definition(merged)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors, "message": f"{len(errors)} validation error(s) in driver definition"},
        )

    # Delete old and save merged
    delete_driver_definition(driver_id, dirs)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(merged, save_dir)

    # Re-register
    driver_class = create_configurable_driver_class(merged)
    register_driver(driver_class)

    return {"status": "updated", "id": driver_id}


@router.delete("/driver-definitions/{driver_id}")
async def delete_driver_definition_endpoint(driver_id: str) -> dict:
    """Delete a JSON driver definition."""
    from server.drivers.driver_loader import delete_driver_definition

    dirs = _get_driver_dirs()
    deleted = delete_driver_definition(driver_id, dirs)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Driver definition '{driver_id}' not found",
        )
    # Also unregister from runtime driver registry
    from server.core.device_manager import unregister_driver
    unregister_driver(driver_id)
    return {"status": "deleted", "id": driver_id}


@router.post("/driver-definitions/{driver_id}/test-command")
async def test_driver_command(driver_id: str, body: TestCommandRequest) -> dict:
    """Test a command against live hardware via a temporary connection."""
    _rate_limit_test(f"test_command:{driver_id}")
    import asyncio
    from server.transport.tcp import TCPTransport

    if body.transport == "http":
        return await _test_http_command(body)

    if body.transport != "tcp":
        raise HTTPException(
            status_code=422,
            detail="Only TCP and HTTP test connections are supported",
        )

    delimiter = body.delimiter.encode().decode("unicode_escape").encode()
    response_text = None
    error_text = None

    try:
        transport = await TCPTransport.create(
            host=body.host,
            port=body.port,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            delimiter=delimiter,
            timeout=body.timeout,
        )
    except ConnectionError as e:
        return {"success": False, "error": str(e), "response": None}

    try:
        cmd_data = body.command_string.encode().decode("unicode_escape").encode()
        response = await transport.send_and_wait(cmd_data, timeout=body.timeout)
        response_text = response.decode("ascii", errors="replace")
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for response"
    except (OSError, ValueError, UnicodeError) as e:
        error_text = str(e)
    finally:
        await transport.close()

    return {
        "success": error_text is None,
        "response": response_text,
        "error": error_text,
    }


async def _test_http_command(body: TestCommandRequest) -> dict:
    """Test an HTTP command against a device."""
    import httpx

    # command_string is the URL path, e.g. "/api/status" or "GET /api/power"
    cmd = body.command_string.strip()
    method = "GET"
    path = cmd
    if " " in cmd:
        parts = cmd.split(None, 1)
        if parts[0].upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            method = parts[0].upper()
            path = parts[1]

    scheme = "https" if body.port == 443 else "http"
    url = f"{scheme}://{body.host}:{body.port}{path}"

    try:
        async with httpx.AsyncClient(
            timeout=body.timeout, verify=False
        ) as client:
            resp = await client.request(method, url)
            return {
                "success": True,
                "response": f"HTTP {resp.status_code}\n{resp.text[:2000]}",
                "error": None,
            }
    except httpx.TimeoutException:
        return {"success": False, "response": None, "error": "HTTP request timed out"}
    except Exception as e:
        return {"success": False, "response": None, "error": str(e)}


# --- Python Drivers ---


def _safe_driver_path(driver_id: str) -> Path:
    """Resolve a driver ID to a safe file path in driver_repo/."""
    from server.system_config import DRIVER_REPO_DIR

    # Sanitize: only allow alphanumeric + underscore + hyphen
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', driver_id):
        raise HTTPException(status_code=400, detail="Invalid driver ID: only alphanumeric, underscore, and hyphen allowed")

    filepath = DRIVER_REPO_DIR / f"{driver_id}.py"

    # Ensure path stays within driver_repo/
    try:
        filepath.resolve().relative_to(DRIVER_REPO_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid driver ID")

    return filepath


@router.get("/python-drivers")
async def list_python_drivers() -> dict:
    """List all Python driver files in driver_repo/."""
    from server.drivers.driver_loader import list_python_drivers as _list
    from server.system_config import DRIVER_REPO_DIR

    drivers = _list([DRIVER_REPO_DIR])

    # Add devices_using info from device manager
    engine = _get_engine()
    for driver in drivers:
        driver["devices_using"] = engine.devices.get_devices_using_driver(driver["id"])

    return {"drivers": drivers}


@router.get("/python-drivers/{driver_id}/source")
async def get_python_driver_source(driver_id: str) -> dict:
    """Read the source code of a Python driver file."""
    filepath = _safe_driver_path(driver_id)

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    try:
        source = filepath.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read driver file: {e}")

    return {"id": driver_id, "filename": filepath.name, "source": source}


@router.put("/python-drivers/{driver_id}/source")
async def save_python_driver_source(driver_id: str, body: dict) -> dict:
    """Save the source code of a Python driver file."""
    filepath = _safe_driver_path(driver_id)
    source = body.get("source")
    if source is None:
        raise HTTPException(status_code=422, detail="Missing 'source' field")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    try:
        filepath.write_text(source, encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save driver file: {e}")

    return {"status": "saved", "id": driver_id}


@router.post("/python-drivers")
async def create_python_driver(body: PythonDriverCreateRequest) -> dict:
    """Create a new Python driver file."""
    from server.system_config import DRIVER_REPO_DIR

    filepath = _safe_driver_path(body.id)

    # Ensure driver_repo/ exists
    DRIVER_REPO_DIR.mkdir(parents=True, exist_ok=True)

    # Atomic creation: 'x' mode fails if the file already exists
    try:
        with open(filepath, "x", encoding="utf-8") as f:
            f.write(body.source)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Python driver '{body.id}' already exists")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to create driver file: {e}")

    # Try to load and register immediately
    from server.drivers.driver_loader import load_python_driver_file
    from server.core.device_manager import register_driver

    driver_class = load_python_driver_file(filepath)
    if driver_class:
        register_driver(driver_class)

    return {"status": "created", "id": body.id}


@router.delete("/python-drivers/{driver_id}")
async def delete_python_driver(driver_id: str) -> dict:
    """Delete a Python driver file."""
    filepath = _safe_driver_path(driver_id)

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    # Check if devices are using this driver
    engine = _get_engine()
    using = engine.devices.get_devices_using_driver(driver_id)
    if using:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete driver '{driver_id}': used by devices: {', '.join(using)}",
        )

    # Remove file
    filepath.unlink()

    # Unregister from driver registry
    from server.core.device_manager import unregister_driver
    unregister_driver(driver_id)

    # Clean up sys.modules
    import sys
    module_name = f"openavc_driver_{driver_id}"
    sys.modules.pop(module_name, None)

    log.info(f"Deleted Python driver: {driver_id}")
    return {"status": "deleted", "id": driver_id}


@router.post("/python-drivers/{driver_id}/reload")
async def reload_python_driver_endpoint(driver_id: str) -> dict:
    """Hot-reload a Python driver and reconnect affected devices."""
    filepath = _safe_driver_path(driver_id)

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    from server.drivers.driver_loader import reload_python_driver

    result = reload_python_driver(filepath)

    if result["status"] == "error":
        return result

    # Reconnect devices using this driver
    engine = _get_engine()
    new_driver_id = result["driver_id"]
    old_driver_id = result.get("old_driver_id")

    reconnected: list[str] = []

    # Reconnect devices using the new driver ID
    reconnected.extend(await engine.devices.reload_driver(new_driver_id))

    # If the driver ID changed, also reconnect devices using the old ID
    if old_driver_id and old_driver_id != new_driver_id:
        reconnected.extend(await engine.devices.reload_driver(old_driver_id))

    result["devices_reconnected"] = reconnected
    return result


# --- Logs ---


# --- Project Library ---


@open_router.get("/library")
async def list_library() -> list[dict[str, Any]]:
    """List all saved projects in the library."""
    from server.core.project_library import list_projects
    return list_projects()


@open_router.get("/library/{project_id}")
async def get_library_project(project_id: str) -> dict[str, Any]:
    """Get a saved project with script contents."""
    from server.core.project_library import get_project
    try:
        data, scripts = get_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    return {"id": project_id, "project": data, "scripts": scripts}


@router.post("/library")
async def save_to_library(request: Request) -> dict[str, Any]:
    """Save the current project to the library."""
    from server.api.models import LibrarySaveRequest
    from server.core.project_library import save_to_library as _save

    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    body = await request.json()
    data = LibrarySaveRequest(**body)
    scripts_dir = engine.project_path.parent / "scripts"

    try:
        _save(data.id, engine.project, scripts_dir, data.name, data.description)
    except ValueError as e:
        raise _api_error(409, f"Library project '{data.id}' already exists", e)

    return {"status": "created", "id": data.id}


@router.delete("/library/{project_id}")
async def delete_library_project(project_id: str) -> dict[str, Any]:
    """Delete a project from the library."""
    from server.core.project_library import delete_project
    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    return {"status": "deleted", "id": project_id}


@router.patch("/library/{project_id}")
async def update_library_project(project_id: str, request: Request) -> dict[str, Any]:
    """Update a saved project's name and/or description."""
    from server.api.models import LibraryUpdateRequest
    from server.core.project_library import update_project_meta

    body = await request.json()
    data = LibraryUpdateRequest(**body)

    try:
        update_project_meta(project_id, data.name, data.description)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")

    return {"status": "updated", "id": project_id}


@router.post("/library/{project_id}/duplicate")
async def duplicate_library_project(project_id: str, request: Request) -> dict[str, Any]:
    """Duplicate a saved project."""
    from server.api.models import LibraryDuplicateRequest
    from server.core.project_library import duplicate_project

    body = await request.json()
    data = LibraryDuplicateRequest(**body)

    try:
        duplicate_project(project_id, data.new_id, data.new_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    except ValueError as e:
        raise _api_error(409, f"Library project '{data.new_id}' already exists", e)

    return {"status": "duplicated", "id": data.new_id}


@router.get("/library/{project_id}/export")
async def export_library_project(project_id: str):
    """Download a saved project as .avc or .zip."""
    from fastapi.responses import Response
    from server.core.project_library import export_project

    try:
        content, filename, content_type = export_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")

    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/library/import")
async def import_library_project(request: Request) -> dict[str, Any]:
    """Upload a .avc or .zip file to the project library."""
    from server.core.project_library import import_project

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=422, detail="No file provided. Use 'file' field in multipart form.")

    filename = upload.filename or "unknown.avc"
    if not filename.endswith((".avc", ".zip")):
        raise HTTPException(status_code=422, detail="File must be .avc or .zip")

    content = await upload.read()
    override_id = form.get("id")

    try:
        result = import_project(content, filename, override_id)
    except ValueError as e:
        raise _api_error(422, f"Invalid project file '{filename}'", e)

    return {
        "status": "imported",
        "id": result["id"],
        "installed_drivers": result.get("installed_drivers", []),
        "missing_drivers": result.get("missing_drivers", []),
        "warnings": result.get("warnings", []),
    }


# --- Project Creation ---


@router.post("/project/open-from-library")
async def open_from_library(request: Request) -> dict[str, Any]:
    """Replace the current project with a saved project from the library."""
    from server.api.models import LibraryOpenRequest
    from server.core.project_library import open_from_library as _open, sanitize_id
    from server.core.backup_manager import create_backup

    engine = _get_engine()
    body = await request.json()
    data = LibraryOpenRequest(**body)

    project_id = sanitize_id(data.project_id or data.project_name)
    scripts_dir = engine.project_path.parent / "scripts"

    # Back up current project (including scripts) before replacing
    import asyncio
    await asyncio.to_thread(create_backup, engine.project_path.parent, f"Before opening '{data.project_name}'")

    try:
        _open(data.library_id, engine.project_path, scripts_dir,
              project_id, data.project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{data.library_id}' not found in library")

    await engine.broadcast_ws({
        "type": "project.replaced",
        "project_name": data.project_name,
        "source": "library",
    })
    await engine.reload_project()

    return {"status": "created", "project_name": data.project_name}


@router.post("/project/create-blank")
async def create_blank(request: Request) -> dict[str, Any]:
    """Reset to an empty project."""
    from server.core.project_library import create_blank_project, sanitize_id, replace_scripts
    from server.core.backup_manager import create_backup

    engine = _get_engine()
    body = await request.json()

    project_name = body.get("project_name", "New Room")
    project_id = sanitize_id(body.get("project_id") or project_name)

    # Back up current project before replacing with blank
    import asyncio
    await asyncio.to_thread(create_backup, engine.project_path.parent, "Before creating blank project")

    project = create_blank_project(project_id, project_name)
    save_project(engine.project_path, project)

    scripts_dir = engine.project_path.parent / "scripts"
    replace_scripts(scripts_dir, {})

    await engine.broadcast_ws({
        "type": "project.replaced",
        "project_name": project_name,
        "source": "blank",
    })
    await engine.reload_project()

    return {"status": "created", "project_name": project_name}


@router.get("/logs/recent")
async def get_recent_logs(count: int = 100, category: str = "") -> list[dict[str, Any]]:
    """Get recent log entries, optionally filtered by category."""
    entries = get_log_buffer().get_recent(count)
    if category:
        entries = [e for e in entries if e.get("category") == category]
    return entries


# --- Backups ---


@router.get("/backups")
async def list_backups_endpoint() -> list[dict[str, Any]]:
    """List available project backups (ZIP + legacy .avc.bak)."""
    from server.core.backup_manager import list_backups

    engine = _get_engine()
    project_dir = engine.project_path.parent
    backups = list_backups(project_dir)
    return [
        {
            "filename": b.filename,
            "reason": b.reason,
            "timestamp": b.timestamp,
            "project_name": b.project_name,
            "size": b.size_bytes,
            "format": b.format,
        }
        for b in backups
    ]


@router.post("/backups/create")
async def create_backup_endpoint(request: Request) -> dict[str, Any]:
    """Create a manual backup of the current project."""
    from server.core.backup_manager import create_backup

    engine = _get_engine()
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    reason = body.get("reason", "Manual backup")

    import asyncio
    path = await asyncio.to_thread(create_backup, engine.project_path.parent, reason)
    if not path:
        raise HTTPException(status_code=404, detail="No project to back up")
    return {"status": "created", "filename": path.name}


@router.post("/backups/{filename:path}/restore")
async def restore_backup(filename: str) -> dict[str, Any]:
    """Restore a project from a backup file (ZIP or legacy .avc.bak)."""
    from server.core.backup_manager import create_backup, restore_from_backup

    engine = _get_engine()
    project_dir = engine.project_path.parent

    # Resolve the backup path (supports both "backups/file.zip" and "file.avc.bak")
    backup_path = (project_dir / filename).resolve()

    # Security: ensure the backup is within the project directory tree
    try:
        backup_path.relative_to(project_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail=f"Backup '{filename}' not found")
    if not (backup_path.name.endswith(".zip") or backup_path.name.endswith(".avc.bak")):
        raise HTTPException(status_code=400, detail="Not a valid backup file")

    # Create a backup before restoring
    import asyncio
    await asyncio.to_thread(create_backup, project_dir, "Before restore")

    restore_from_backup(backup_path, project_dir)
    await engine.reload_project()
    return {"status": "restored", "filename": filename}


# --- ISC (Inter-System Communication) ---


@router.get("/isc/status")
async def isc_status() -> dict[str, Any]:
    """ISC status: enabled, instance info, peer summary."""
    engine = _get_engine()
    if engine.isc is None:
        return {"enabled": False}
    return engine.isc.get_status()


@router.get("/isc/instances")
async def isc_instances() -> list[dict[str, Any]]:
    """List all discovered/connected ISC peer instances."""
    engine = _get_engine()
    if engine.isc is None:
        return []
    return engine.isc.get_instances()


@router.post("/isc/send")
async def isc_send(request: Request) -> dict[str, Any]:
    """Send an event to a remote ISC peer."""
    from server.api.models import ISCSendRequest
    engine = _get_engine()
    if engine.isc is None:
        raise HTTPException(status_code=503, detail="ISC not enabled")
    body = await request.json()
    data = ISCSendRequest(**body)
    try:
        await engine.isc.send_to(data.instance_id, data.event, data.payload)
        return {"status": "sent"}
    except ConnectionError as e:
        raise _api_error(503, f"ISC peer '{data.instance_id}' is not connected", e)


@router.post("/isc/broadcast")
async def isc_broadcast(request: Request) -> dict[str, Any]:
    """Broadcast an event to all connected ISC peers."""
    engine = _get_engine()
    if engine.isc is None:
        raise HTTPException(status_code=503, detail="ISC not enabled")
    body = await request.json()
    event = body.get("event", "")
    payload = body.get("payload", {})
    if not event:
        raise HTTPException(status_code=422, detail="Missing 'event' field")
    await engine.isc.broadcast(event, payload)
    return {"status": "broadcast"}


@router.post("/isc/command")
async def isc_command(request: Request) -> dict[str, Any]:
    """Send a device command to a remote ISC peer."""
    from server.api.models import ISCCommandRequest
    engine = _get_engine()
    if engine.isc is None:
        raise HTTPException(status_code=503, detail="ISC not enabled")
    body = await request.json()
    data = ISCCommandRequest(**body)
    try:
        result = await engine.isc.send_command(
            data.instance_id, data.device_id, data.command, data.params,
        )
        return {"success": True, "result": result}
    except ConnectionError as e:
        raise _api_error(503, f"ISC peer '{data.instance_id}' is not connected", e)
    except TimeoutError as e:
        raise _api_error(504, f"Command timed out on ISC peer '{data.instance_id}'", e)
    except Exception as e:
        raise _api_error(500, f"Failed to send command to ISC peer '{data.instance_id}'", e)


# --- Cloud Connection ---


@open_router.get("/cloud/status")
async def cloud_status() -> dict[str, Any]:
    """Get cloud connection status."""
    engine = _get_engine()
    from server.cloud.config import load_cloud_config
    saved = load_cloud_config()

    if engine.cloud_agent is None:
        return {
            "enabled": saved.get("enabled", False),
            "connected": False,
            "system_id": saved.get("system_id", ""),
            "endpoint": saved.get("endpoint", ""),
        }

    status = engine.cloud_agent.get_status()
    return {
        "enabled": True,
        "connected": status.get("connected", False),
        "system_id": saved.get("system_id", ""),
        "endpoint": saved.get("endpoint", ""),
        "session_id": status.get("session_id", ""),
        "last_heartbeat": status.get("last_heartbeat", ""),
        "uptime": status.get("uptime", 0),
    }


@router.post("/cloud/pair")
async def cloud_pair(request: Request) -> dict[str, Any]:
    """Pair this instance with the OpenAVC Cloud platform."""
    engine = _get_engine()
    body = await request.json()
    data = CloudPairRequest(**body)

    # Exchange the pairing token with the cloud API
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{data.cloud_api_url}/api/v1/systems/pair",
                json={"token": data.token},
            )
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", "Pairing failed")
                except Exception:
                    detail = resp.text or "Pairing failed"
                raise HTTPException(status_code=resp.status_code, detail=detail)
            pair_data = resp.json()
    except httpx.HTTPError as e:
        raise _api_error(502, "Failed to reach cloud API for pairing", e)

    # Save cloud config locally
    from server.cloud.config import save_cloud_config
    cloud_cfg = {
        "enabled": True,
        "endpoint": pair_data["endpoint"],
        "system_key": pair_data["system_key"],
        "system_id": pair_data["system_id"],
    }
    save_cloud_config(cloud_cfg)

    # Update runtime config
    import server.config as cfg
    cfg.CLOUD_ENABLED = True
    cfg.CLOUD_ENDPOINT = pair_data["endpoint"]
    cfg.CLOUD_SYSTEM_KEY = pair_data["system_key"]
    cfg.CLOUD_SYSTEM_ID = pair_data["system_id"]

    # Start or restart the cloud agent with new credentials
    if engine.cloud_agent is not None:
        # Stop existing agent so it picks up new credentials
        await engine.cloud_agent.stop()
        engine.cloud_agent = None
    await engine._start_cloud_agent()

    return {
        "success": True,
        "system_id": pair_data["system_id"],
        "endpoint": pair_data["endpoint"],
    }


@router.post("/cloud/unpair")
async def cloud_unpair() -> dict[str, Any]:
    """Unpair this instance from the cloud platform."""
    engine = _get_engine()

    # Stop the cloud agent
    if engine.cloud_agent:
        await engine.cloud_agent.stop()
        engine.cloud_agent = None

    # Clear config
    import server.config as cfg
    cfg.CLOUD_ENABLED = False
    cfg.CLOUD_SYSTEM_KEY = ""
    cfg.CLOUD_SYSTEM_ID = ""

    from server.cloud.config import save_cloud_config
    save_cloud_config({
        "enabled": False,
        "system_key": "",
        "system_id": "",
        "endpoint": "",
    })

    return {"success": True}


# --- System Configuration ---


@router.get("/system/version")
async def get_system_version() -> dict[str, Any]:
    """Current version info, platform, and update channel."""
    import platform as plat
    from pathlib import Path
    from server.version import __version__
    from server.system_config import get_system_config
    cfg = get_system_config()
    return {
        "version": __version__,
        "channel": cfg.get("updates", "channel", "stable"),
        "platform": plat.system().lower(),
        "kiosk_available": Path("/opt/openavc/scripts/panel-kiosk.sh").exists(),
    }


@router.get("/system/config")
async def get_system_config_endpoint() -> dict[str, Any]:
    """Get current system configuration (redacts secrets)."""
    from server.system_config import get_system_config
    cfg = get_system_config()
    data = cfg.to_dict()
    # Redact sensitive values
    if data.get("auth", {}).get("programmer_password"):
        data["auth"]["programmer_password"] = "***"
    if data.get("auth", {}).get("api_key"):
        data["auth"]["api_key"] = "***"
    if data.get("auth", {}).get("panel_lock_code"):
        data["auth"]["panel_lock_code"] = "***"
    if data.get("cloud", {}).get("system_key"):
        data["cloud"]["system_key"] = "***"
    if data.get("isc", {}).get("auth_key"):
        data["isc"]["auth_key"] = "***"
    return data


@router.patch("/system/config")
async def update_system_config(request: Request) -> dict[str, Any]:
    """Update system configuration sections. Body is a partial system.json structure."""
    from server.system_config import get_system_config
    cfg = get_system_config()
    body = await request.json()

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    updated_sections = []
    for section_name, section_data in body.items():
        if not isinstance(section_data, dict):
            continue
        current = cfg.section(section_name)
        if not current:
            continue
        for key, value in section_data.items():
            if key in current:
                cfg.set(section_name, key, value)
        updated_sections.append(section_name)

    cfg.save()

    return {"success": True, "updated_sections": updated_sections}


# --- Network Adapters ---


@router.get("/network/adapters")
async def get_network_adapters() -> dict[str, Any]:
    """List available network adapters for control interface selection."""
    from server.discovery.network_scanner import get_network_adapters as _get_adapters
    return {"adapters": _get_adapters()}


@router.post("/system/reboot")
async def reboot_system() -> dict[str, str]:
    """Reboot the host machine. Only works on Linux where the openavc user has passwordless sudo reboot."""
    import asyncio
    import platform
    from pathlib import Path
    from server.utils.logger import get_logger
    log = get_logger(__name__)

    if platform.system() != "Linux":
        raise HTTPException(status_code=501, detail="Reboot is only supported on Linux deployments")

    if Path("/.dockerenv").exists():
        raise HTTPException(status_code=501, detail="Reboot is not supported in Docker containers")

    # Only allow reboot if our sudoers entry was installed (Pi image sets this up)
    if not Path("/etc/sudoers.d/openavc-reboot").exists():
        raise HTTPException(status_code=501, detail="Reboot not available. Restart the device manually.")

    log.info("System reboot requested via API")

    async def _delayed_reboot():
        await asyncio.sleep(1)
        proc = await asyncio.create_subprocess_exec(
            "sudo", "reboot",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    asyncio.create_task(_delayed_reboot())
    return {"status": "rebooting"}


# --- Update System ---


def _get_update_manager():
    engine = _get_engine()
    if engine.update_manager is None:
        from server.updater.manager import UpdateManager
        engine.update_manager = UpdateManager(state_store=engine.state)
    return engine.update_manager


@router.get("/system/updates/check")
async def check_for_updates() -> dict[str, Any]:
    """Check GitHub for available updates."""
    mgr = _get_update_manager()
    return await mgr.check_for_updates()


@router.post("/system/updates/apply")
async def apply_update() -> dict[str, Any]:
    """Download and apply an available update."""
    mgr = _get_update_manager()
    return await mgr.apply_update()


@router.post("/system/updates/rollback")
async def rollback_update() -> dict[str, Any]:
    """Rollback to the previous version."""
    mgr = _get_update_manager()
    return await mgr.rollback()


@router.get("/system/updates/status")
async def get_update_status() -> dict[str, Any]:
    """Get current update status and progress."""
    mgr = _get_update_manager()
    return mgr.get_status()


@router.get("/system/updates/history")
async def get_update_history() -> list[dict[str, Any]]:
    """List past updates with timestamps."""
    mgr = _get_update_manager()
    return mgr.get_history()


# ── Simulation ──


@router.get("/simulation/status")
async def simulation_status() -> dict[str, Any]:
    """Get simulation status."""
    return _get_engine().simulation.status()


@router.post("/simulation/start")
async def simulation_start(request: Request) -> dict[str, Any]:
    """Start simulation for all devices (or specific device_ids)."""
    device_ids = None
    try:
        body = await request.json()
        if body and "device_ids" in body:
            device_ids = body["device_ids"]
    except Exception:
        pass  # No body or invalid JSON — simulate all devices
    try:
        result = await _get_engine().simulation.start(device_ids)
        return result
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/simulation/stop")
async def simulation_stop() -> dict[str, str]:
    """Stop simulation and restore real device connections."""
    await _get_engine().simulation.stop()
    return {"status": "stopped"}
