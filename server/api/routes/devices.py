"""Device CRUD, commands, settings, and connection REST API endpoints."""

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.api._engine import _get_engine, _rate_limit_test
from server.api.errors import api_error as _api_error
from server.api.models import (
    ChildEntityPatchRequest,
    CommandRequest,
    DeviceSettingRequest,
    DeviceUpdateRequest,
    InstallMissingDriversRequest,
    PendingSettingsRequest,
)
from server.core.project_loader import ChildEntityConfig, DeviceConfig, save_project
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


@router.get("/devices/missing-drivers")
async def list_missing_drivers() -> dict[str, Any]:
    """Drivers that orphaned project devices need, annotated with community matches.

    The IDE polls this on project open / reload. Each entry's
    ``community_match`` is populated when the driver exists in the public
    catalog (``raw.githubusercontent.com/open-avc/openavc-drivers``); when
    null, the user must reassign the device or upload a driver manually.

    Declared before /devices/{device_id} so the literal path wins routing.
    """
    from server.discovery.community_index import CommunityIndexCache
    from server.api.routes.drivers import COMMUNITY_REPO_URL

    engine = _get_engine()
    missing_ids = engine.devices.get_missing_drivers()
    if not missing_ids:
        return {"missing": []}

    devices_per_driver: dict[str, list[str]] = {did: [] for did in missing_ids}
    for dev in engine.devices.list_devices():
        if dev.get("orphaned") and dev.get("driver") in devices_per_driver:
            devices_per_driver[dev["driver"]].append(dev["id"])

    if not hasattr(list_missing_drivers, "_cache"):
        list_missing_drivers._cache = CommunityIndexCache()
    catalog = await list_missing_drivers._cache.get_drivers()
    catalog_by_id: dict[str, dict[str, Any]] = {
        entry.get("id", ""): entry for entry in catalog if entry.get("id")
    }

    result: list[dict[str, Any]] = []
    for driver_id in missing_ids:
        match = catalog_by_id.get(driver_id)
        community_match: dict[str, Any] | None = None
        if match and match.get("file"):
            community_match = {
                "id": match["id"],
                "name": match.get("name", match["id"]),
                "manufacturer": match.get("manufacturer", ""),
                "category": match.get("category", ""),
                "file_url": f"{COMMUNITY_REPO_URL}/{match['file']}",
                "min_platform_version": match.get("min_platform_version"),
            }
        result.append({
            "driver_id": driver_id,
            "device_ids": devices_per_driver.get(driver_id, []),
            "community_match": community_match,
        })
    return {"missing": result}


@router.post("/devices/install-missing")
async def install_missing_drivers(body: InstallMissingDriversRequest) -> dict[str, Any]:
    """Bulk-install community drivers requested by IDs, then activate orphans.

    Each ID is looked up in the community catalog. Failures don't abort the
    batch; per-driver outcomes are returned so the UI can show which
    succeeded vs. failed.
    """
    from server.api.models import CommunityDriverInstallRequest
    from server.api.routes.drivers import COMMUNITY_REPO_URL, install_community_driver
    from server.discovery.community_index import CommunityIndexCache

    if not body.driver_ids:
        return {"installed": [], "failed": [], "activated_devices": []}

    cache = CommunityIndexCache()
    catalog = await cache.get_drivers()
    catalog_by_id: dict[str, dict[str, Any]] = {
        entry.get("id", ""): entry for entry in catalog if entry.get("id")
    }

    installed: list[str] = []
    failed: list[dict[str, Any]] = []

    for driver_id in body.driver_ids:
        match = catalog_by_id.get(driver_id)
        if not match or not match.get("file"):
            failed.append({"driver_id": driver_id, "error": "Not in community catalog"})
            continue
        try:
            await install_community_driver(CommunityDriverInstallRequest(
                driver_id=driver_id,
                file_url=f"{COMMUNITY_REPO_URL}/{match['file']}",
                min_platform_version=match.get("min_platform_version"),
            ))
            installed.append(driver_id)
        except HTTPException as e:
            failed.append({"driver_id": driver_id, "error": str(e.detail)})
        except Exception as e:
            failed.append({"driver_id": driver_id, "error": str(e)})

    # install_community_driver already retries orphans on each call; one
    # final sweep catches any orphan whose driver was registered out of
    # order during the batch.
    engine = _get_engine()
    activated = await engine.devices.retry_all_orphans()

    return {
        "installed": installed,
        "failed": failed,
        "activated_devices": activated,
    }


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
        # Preserve queued device settings — re-connect applies them via
        # _apply_pending_settings(). Constructing a fresh DeviceConfig from
        # the request body alone would silently drop them on every edit.
        pending_settings=existing.pending_settings,
        # Same for child-entity metadata (user labels / per-child config):
        # keep the existing map unless the request explicitly supplies a new
        # one. A plain name/driver/config edit must not wipe it on disk or
        # re-seed the live driver with an empty map.
        child_entities=(
            body.child_entities if body.child_entities is not None
            else existing.child_entities
        ),
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
        serial_port = cfg.get("port", "")
        baud = cfg.get("baudrate", 9600)
        try:
            import serial
            ser = serial.Serial(serial_port, baud, timeout=2)
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
            return {"success": True, "error": None, "latency_ms": latency}
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


@router.post("/devices/{device_id}/pause")
async def pause_device(device_id: str) -> dict[str, Any]:
    """Pause a production device — disconnect cleanly and suppress auto-reconnect.

    Used by the driver test panel before opening a competing TCP session
    against the same host:port on single-session devices (A81). The device
    stays paused until ``/devices/{id}/resume`` is called.
    """
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")
    if not any(d.id == device_id for d in engine.project.devices):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    try:
        await engine.devices.pause_device(device_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"status": "paused", "device_id": device_id}


@router.post("/devices/{device_id}/resume")
async def resume_device(device_id: str) -> dict[str, Any]:
    """Resume a paused device — clear the pause flag and reconnect."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")
    if not any(d.id == device_id for d in engine.project.devices):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    try:
        await engine.devices.resume_device(device_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"status": "resuming", "device_id": device_id}


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


# --- Child Entities ---
#
# A "child entity" is a sub-unit owned by a device (an encoder/decoder on
# an AV-over-IP controller, a zone on a DSP, a video wall slot on a
# presentation switcher). Drivers declare types in
# DRIVER_INFO["child_entity_types"] and register live instances via
# BaseDriver.register_child(). The platform owns the state-key shape:
#   device.<parent_id>.<child_type>.<local_id_padded>.<property>
# These endpoints expose registered children + project-side label/config
# to the IDE and integrators without the IDE needing to assemble the
# state-key namespace itself. See openavc-device-children-plan.md §5.


def _project_device_entry(engine: Any, device_id: str):
    """Return the project-side DeviceConfig for ``device_id``, or None."""
    if not getattr(engine, "project", None):
        return None
    for d in engine.project.devices:
        if d.id == device_id:
            return d
    return None


def _project_child_entry(
    project_device: Any, child_type: str, padded_id: str,
) -> dict[str, Any]:
    """Return ``{"label", "config"}`` for one project-side child entry.

    Falls back to defaults when the project doesn't have an entry, so
    callers can always read ``label`` / ``config`` from the dict without
    a None check.
    """
    if project_device is None:
        return {"label": "", "config": {}}
    type_map = project_device.child_entities.get(child_type, {})
    entry = type_map.get(padded_id)
    if entry is None:
        return {"label": "", "config": {}}
    return {
        "label": entry.label,
        "config": dict(entry.config),
    }


def _build_child_entry(
    driver: Any, project_device: Any, child_type: str, local_id: int,
) -> dict[str, Any]:
    """Build the response shape for one registered child.

    Combines the driver-padded id, the driver-owned live state, and the
    project-owned label + config. ``label`` in the top-level response is
    the project-canonical value; the same key inside ``state`` is the
    runtime mirror (synced on register_child / PATCH).
    """
    padded = driver.format_child_id(child_type, local_id)
    project_entry = _project_child_entry(project_device, child_type, padded)
    return {
        "local_id": local_id,
        "local_id_padded": padded,
        "label": project_entry["label"],
        "config": project_entry["config"],
        "registered": True,
        "state": driver.get_child_state(child_type, local_id),
    }


def _ensure_driver_for_children(engine: Any, device_id: str):
    """Return the live driver for child-entity routes, or raise 404 / 503.

    GET endpoints tolerate orphan/disabled devices by returning empty
    payloads; this helper is only used by routes that need to introspect
    or mutate the driver's child registry.
    """
    project_device = _project_device_entry(engine, device_id)
    if project_device is None:
        raise HTTPException(
            status_code=404, detail=f"Device '{device_id}' not found",
        )
    driver = engine.devices.get_driver(device_id)
    if driver is None:
        raise HTTPException(
            status_code=503,
            detail=f"Device '{device_id}' has no live driver "
                   f"(orphaned or disabled)",
        )
    return driver, project_device


@router.get("/devices/{device_id}/children")
async def list_child_entities(device_id: str) -> dict[str, Any]:
    """List every child entity grouped by type, with the per-type schema
    and current registered children.

    Returns an empty payload (200, no children) when the device exists in
    the project but has no live driver (orphan/disabled) or the driver
    didn't declare ``child_entity_types``. Returns 404 only when
    ``device_id`` itself isn't in the project.
    """
    engine = _get_engine()
    project_device = _project_device_entry(engine, device_id)
    if project_device is None:
        raise HTTPException(
            status_code=404, detail=f"Device '{device_id}' not found",
        )

    driver = engine.devices.get_driver(device_id)
    if driver is None:
        return {
            "device_id": device_id,
            "child_entity_types": {},
            "children": {},
        }

    types = driver.get_child_entity_types()
    children: dict[str, list[dict[str, Any]]] = {}
    for ctype in types:
        ids = driver.list_children(ctype)
        children[ctype] = [
            _build_child_entry(driver, project_device, ctype, lid)
            for lid in ids
        ]
    return {
        "device_id": device_id,
        "child_entity_types": types,
        "children": children,
    }


@router.get("/devices/{device_id}/children/{child_type}")
async def list_child_entities_by_type(
    device_id: str, child_type: str,
) -> dict[str, Any]:
    """List registered children of one type."""
    driver, project_device = _ensure_driver_for_children(
        _get_engine(), device_id,
    )
    types = driver.get_child_entity_types()
    if child_type not in types:
        raise HTTPException(
            status_code=404,
            detail=f"Driver '{driver.DRIVER_INFO.get('id', '?')}' does not "
                   f"declare child type '{child_type}'",
        )
    ids = driver.list_children(child_type)
    entries = [
        _build_child_entry(driver, project_device, child_type, lid)
        for lid in ids
    ]
    return {
        "device_id": device_id,
        "child_type": child_type,
        "schema": types[child_type],
        "children": entries,
    }


@router.get("/devices/{device_id}/children/{child_type}/{local_id}")
async def get_child_entity(
    device_id: str, child_type: str, local_id: int,
) -> dict[str, Any]:
    """Return one registered child's full state + project metadata."""
    driver, project_device = _ensure_driver_for_children(
        _get_engine(), device_id,
    )
    types = driver.get_child_entity_types()
    if child_type not in types:
        raise HTTPException(
            status_code=404,
            detail=f"Driver '{driver.DRIVER_INFO.get('id', '?')}' does not "
                   f"declare child type '{child_type}'",
        )
    try:
        # Validate range up-front so an out-of-range path component reads
        # as 404 instead of falling through to "not registered".
        driver.format_child_id(child_type, local_id)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    if not driver.is_child_registered(child_type, local_id):
        raise HTTPException(
            status_code=404,
            detail=f"Child {child_type} {local_id} is not currently "
                   f"registered on device '{device_id}'",
        )
    entry = _build_child_entry(driver, project_device, child_type, local_id)
    return {"device_id": device_id, "child_type": child_type, **entry}


@router.patch("/devices/{device_id}/children/{child_type}/{local_id}")
async def update_child_entity(
    device_id: str,
    child_type: str,
    local_id: int,
    body: ChildEntityPatchRequest,
) -> dict[str, Any]:
    """Update a child's user label and/or freeform config.

    Persists to the project file (so the label survives reload even if
    the child isn't currently registered) and, when the child is live,
    mirrors the label into its ``label`` state key so subscribers see
    the change without waiting for the next reload.
    """
    if body.label is None and body.config is None:
        raise HTTPException(
            status_code=422,
            detail="At least one of 'label' or 'config' must be provided",
        )

    engine = _get_engine()
    driver, project_device = _ensure_driver_for_children(engine, device_id)

    types = driver.get_child_entity_types()
    if child_type not in types:
        raise HTTPException(
            status_code=404,
            detail=f"Driver '{driver.DRIVER_INFO.get('id', '?')}' does not "
                   f"declare child type '{child_type}'",
        )
    try:
        padded = driver.format_child_id(child_type, local_id)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    # Persist to project file. Merge — don't overwrite the whole entry —
    # so a label-only PATCH doesn't wipe an existing config dict, and
    # vice versa.
    type_map = project_device.child_entities.setdefault(child_type, {})
    existing = type_map.get(padded)
    new_label = (
        body.label if body.label is not None
        else (existing.label if existing else "")
    )
    new_config = (
        dict(body.config) if body.config is not None
        else (dict(existing.config) if existing else {})
    )
    type_map[padded] = ChildEntityConfig(label=new_label, config=new_config)

    save_project(engine.project_path, engine.project)

    # Sync the new project metadata back into the driver so future
    # register_child calls (re-registration after deregister, etc.) seed
    # the latest label without a project reload.
    driver.set_project_child_entities({
        ctype: {pid: {"label": cfg.label, "config": dict(cfg.config)}
                for pid, cfg in pid_map.items()}
        for ctype, pid_map in project_device.child_entities.items()
    })

    # Live state mirror — only if the child is currently registered.
    # An unregistered project entry still gets persisted above; the live
    # state key is created on the next register_child.
    if body.label is not None and driver.is_child_registered(child_type, local_id):
        driver.set_child_state(child_type, local_id, "label", new_label)

    entry = _build_child_entry(driver, project_device, child_type, local_id)
    return {"device_id": device_id, "child_type": child_type, **entry}


@router.post("/devices/{device_id}/children/refresh")
async def refresh_child_entities(device_id: str) -> dict[str, Any]:
    """Ask the driver to re-discover its child entities from the device.

    Returns 501 if the driver doesn't implement ``refresh_children``,
    503 if the device isn't currently connected, or 404 if the device
    isn't in the project. Drivers that implement ``refresh_children``
    are expected to reconcile their child set via ``register_child`` /
    ``deregister_child`` so subscribers see the diff atomically.
    """
    engine = _get_engine()
    driver, _ = _ensure_driver_for_children(engine, device_id)
    if not driver.get_state("connected"):
        raise HTTPException(
            status_code=503,
            detail=f"Device '{device_id}' is not connected",
        )
    try:
        result = await driver.refresh_children()
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from None
    except Exception as e:
        raise _api_error(
            500, f"Failed to refresh children on device '{device_id}'", e,
        )
    return {"status": "refreshed", "device_id": device_id, "result": result}


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
