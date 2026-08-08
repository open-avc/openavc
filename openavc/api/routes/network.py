"""Host network configuration REST endpoints.

Backed by ``server/system/network.py``. Every route 404s when no backend is
available (Windows, Docker, generic servers), which is also the signal the
UI surfaces use to hide themselves.

Auth is ``require_local_or_programmer_auth``: the device's own screen
(loopback — the /setup page or a kiosk maintenance view) may configure the
network without credentials so an appliance can be bootstrapped onto a
network it isn't on yet; remote callers need programmer credentials.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from openavc.api.auth import require_local_or_programmer_auth
from openavc.api.errors import api_error
from openavc.api.models import (
    HostnameRequest,
    NetworkIPv4Request,
    WifiConnectRequest,
    WifiRadioRequest,
)
from openavc.system import network as host_network

router = APIRouter(
    prefix="/api/system/network",
    dependencies=[Depends(require_local_or_programmer_auth)],
)


async def _backend() -> host_network.NetworkBackend:
    backend = await asyncio.to_thread(host_network.get_backend)
    if backend is None:
        raise HTTPException(
            status_code=404,
            detail="Host network configuration is not available on this deployment.",
        )
    return backend


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a backend result to the API's command-result shape.

    ``server/system/network.py`` speaks ``ok`` internally (it mirrors the
    privileged helper's own protocol); the REST surface says ``success`` for
    calls that can fail without being an HTTP error. Everything else on the
    backend result rides along untouched (``error``, ``rolled_back``, ``reboot``).
    """
    out = dict(payload)
    if "ok" in out:
        out["success"] = bool(out.pop("ok"))
    return out


@router.get("")
async def network_status() -> dict[str, Any]:
    """Interfaces, active connections, addresses, hostname, capabilities."""
    backend = await _backend()
    try:
        return await backend.get_status()
    except RuntimeError as e:
        raise api_error(500, "Could not read the host network configuration.", e)


@router.post("/ipv4")
async def network_set_ipv4(body: NetworkIPv4Request) -> dict[str, Any]:
    """Switch a connection between DHCP and static IPv4.

    ``confirmed: false`` validates only and returns warnings for the UI's
    confirmation step. ``confirmed: true`` applies, with automatic rollback
    if the connection fails to activate.
    """
    backend = await _backend()

    if body.method not in ("auto", "manual"):
        raise HTTPException(status_code=400, detail="method must be 'auto' or 'manual'.")

    address = gateway = None
    dns: list[str] = []
    warnings: list[str] = []
    if body.method == "manual":
        try:
            address, gateway, dns, warnings = host_network.validate_static_ipv4(
                body.address or "", body.gateway, body.dns
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if not body.confirmed:
        return {"success": True, "applied": False, "warnings": warnings}

    result = await backend.set_ipv4(
        body.connection, body.method, address=address, gateway=gateway, dns=dns
    )
    result["warnings"] = warnings
    result["applied"] = True
    return _result(result)


@router.post("/wifi/scan")
async def network_wifi_scan() -> dict[str, Any]:
    backend = await _backend()
    try:
        return {"networks": await backend.wifi_scan()}
    except RuntimeError as e:
        raise api_error(500, "Could not scan for WiFi networks.", e)


@router.post("/wifi/connect")
async def network_wifi_connect(body: WifiConnectRequest) -> dict[str, Any]:
    backend = await _backend()
    ssid = body.ssid.strip()
    if not ssid:
        raise HTTPException(status_code=400, detail="ssid is required.")
    return _result(await backend.wifi_connect(ssid, body.psk or None))


@router.post("/wifi/radio")
async def network_wifi_set_radio(body: WifiRadioRequest) -> dict[str, Any]:
    """Turn the WiFi radio on or off."""
    backend = await _backend()
    return _result(await backend.wifi_set_enabled(body.enabled))


@router.post("/hostname")
async def network_set_hostname(body: HostnameRequest) -> dict[str, Any]:
    backend = await _backend()
    try:
        name = host_network.validate_hostname(body.hostname)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _result(await backend.set_hostname(name))
