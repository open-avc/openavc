"""Device CRUD, commands, settings, and connection REST API endpoints."""

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.api._engine import _get_engine, _rate_limit_test
from server.api.errors import api_error as _api_error
from server.api.models import (
    CommandRequest,
    DeviceSettingRequest,
    DeviceUpdateRequest,
    PendingSettingsRequest,
)
from server.core.project_loader import DeviceConfig, save_project
from server.core.project_migration import CONNECTION_FIELDS

router = APIRouter()

_MAX_NAME_LEN = 128


def _sanitize_device_name(name: str) -> str:
    """Strip HTML tags, collapse whitespace, enforce length limit."""
    name = re.sub(r"<[^>]+>", "", name)
    name = " ".join(name.split())
    return name[:_MAX_NAME_LEN]


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
    new_name = _sanitize_device_name(body.name) if body.name is not None else existing.name
    if not new_name:
        raise HTTPException(status_code=422, detail="Device name cannot be blank")
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

    elif transport in ("udp", "osc"):
        # UDP/OSC is connectionless — verify the host resolves and socket opens
        import socket as _socket
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            sock.settimeout(2)
            sock.connect((host, int(port)))
            sock.close()
            latency = round((_time.monotonic() - start) * 1000, 1)
            return {"success": True, "error": None, "latency_ms": latency}
        except (OSError, ValueError, TypeError) as e:
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

    # Hot-reload devices with new connections
    await engine.reload_project()
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
    await engine.reload_project()
    return {"status": "imported", "count": len(cleaned)}
