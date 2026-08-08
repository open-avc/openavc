"""Cloud pairing and connection-status REST endpoints.

Pair this instance with the OpenAVC Cloud platform, unpair it, and report
what the agent is doing. The agent itself lives in ``openavc/cloud/``; these
routes only own the pairing handshake and the local persistence around it.

``/api/cloud/status`` is open (the Programmer's connection badge polls it
before auth on a dev instance); pair and unpair are protected.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from openavc.api._engine import _get_engine
from openavc.api.errors import api_error as _api_error
from openavc.api.models import CloudPairRequest

router = APIRouter()
open_router = APIRouter()


@open_router.get("/cloud/status")
async def cloud_status() -> dict[str, Any]:
    """Get cloud connection status."""
    engine = _get_engine()
    from openavc.cloud.config import load_cloud_config
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
        # Set only when the agent gave up on something retrying can't fix, so
        # the UI can explain an offline instance instead of just reporting it.
        "stop_reason": status.get("stop_reason", ""),
        "stop_detail": status.get("stop_detail", ""),
    }


async def _validate_cloud_api_url(url: str) -> str:
    """Validate a caller-supplied cloud API base URL before the server makes an
    outbound request to it, closing the SSRF vector on an open instance.

    Rejects non-http(s) schemes and hosts that resolve into cloud-metadata
    (link-local), multicast, reserved, unspecified, or — outside a dev
    checkout — loopback address space. Private LAN ranges stay allowed so a
    self-hosted cloud on the local network can still be paired. Returns the
    URL with any trailing slash stripped.
    """
    import asyncio
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400, detail="Cloud API URL must start with http:// or https://."
        )
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="Cloud API URL is missing a host.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        infos = await asyncio.get_event_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except (OSError, socket.gaierror) as e:
        raise _api_error(400, f"Could not resolve cloud API host '{host}'.", e)

    from openavc.api.auth import _deployment_is_dev
    allow_loopback = _deployment_is_dev()
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or (ip.is_loopback and not allow_loopback)
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Cloud API URL resolves to a disallowed address ({ip}).",
            )
    return url.rstrip("/")


@router.post("/cloud/pair")
async def cloud_pair(request: Request) -> dict[str, Any]:
    """Pair this instance with the OpenAVC Cloud platform."""
    engine = _get_engine()
    body = await request.json()
    data = CloudPairRequest(**body)

    cloud_api_url = await _validate_cloud_api_url(data.cloud_api_url)

    # Exchange the pairing token with the cloud API
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{cloud_api_url}/api/v1/systems/pair",
                json={"token": data.token},
            )
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", "Pairing failed")
                except Exception:
                    detail = resp.text or "Pairing failed"
                raise HTTPException(status_code=resp.status_code, detail=detail)
            try:
                pair_data = resp.json()
            except ValueError as e:
                raise _api_error(502, "Cloud returned a non-JSON pairing response.", e)
    except httpx.HTTPError as e:
        raise _api_error(502, "Failed to reach cloud API for pairing", e)

    # Guard the cross-service contract: a 200 with a partial or renamed body
    # must surface as a clean 502, not an opaque KeyError 500.
    if not isinstance(pair_data, dict):
        raise _api_error(502, "Cloud returned an unexpected pairing response.")
    missing = [k for k in ("endpoint", "system_key", "system_id") if not pair_data.get(k)]
    if missing:
        raise _api_error(
            502, "Cloud pairing response is missing required field(s): " + ", ".join(missing)
        )

    # Save cloud config locally. The cloud has already registered this system,
    # so if persistence fails we must not claim success — surface the split
    # brain clearly and leave the runtime/agent untouched instead of a generic
    # 500 over a half-paired state.
    from openavc.cloud.config import save_cloud_config
    cloud_cfg = {
        "enabled": True,
        "endpoint": pair_data["endpoint"],
        "system_key": pair_data["system_key"],
        "system_id": pair_data["system_id"],
    }
    try:
        save_cloud_config(cloud_cfg)
    except OSError as e:
        raise _api_error(
            500,
            "Paired with the cloud but could not save credentials locally; "
            "resolve the disk/permission issue and pair again.",
            e,
        )

    # Update runtime config. The config.py constants are import-time snapshots
    # of system config, so a pairing has to write back into them by hand or the
    # running process keeps serving the pre-pairing values.
    import openavc.config as cfg
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

    # _start_cloud_agent isolates its own failures and leaves cloud_agent None;
    # reflect that so the UI doesn't report "enabled" over an agent that never
    # started.
    agent_started = engine.cloud_agent is not None

    result: dict[str, Any] = {
        "status": "paired",
        "system_id": pair_data["system_id"],
        "endpoint": pair_data["endpoint"],
        "agent_started": agent_started,
    }
    if not agent_started:
        result["warning"] = (
            "Pairing saved, but the cloud connection did not start. "
            "Check the server logs and reconnect."
        )
    return result


@router.post("/cloud/unpair")
async def cloud_unpair() -> dict[str, Any]:
    """Unpair this instance from the cloud platform."""
    engine = _get_engine()

    # Stop the cloud agent
    if engine.cloud_agent:
        await engine.cloud_agent.stop()
        engine.cloud_agent = None

    # Clear config
    import openavc.config as cfg
    cfg.CLOUD_ENABLED = False
    cfg.CLOUD_SYSTEM_KEY = ""
    cfg.CLOUD_SYSTEM_ID = ""

    from openavc.cloud.config import save_cloud_config
    save_cloud_config({
        "enabled": False,
        "system_key": "",
        "system_id": "",
        "endpoint": "",
    })

    return {"status": "unpaired"}
