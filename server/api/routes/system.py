"""System status, configuration, ISC, cloud, updates, and simulation REST API endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.security import HTTPBasicCredentials

from server.api._engine import _get_engine
from server.api.auth import _basic, programmer_auth_satisfied
from server.api.errors import api_error as _api_error
from server.api.models import CloudPairRequest

router = APIRouter()
open_router = APIRouter()

# Strong references to fire-and-forget tasks. asyncio only holds a weak
# reference to a bare create_task(), so an unreferenced task can be garbage
# collected mid-flight; keep it alive until it finishes.
_BACKGROUND_TASKS: set = set()


# --- System ---


@open_router.get("/startup-status")
async def startup_status() -> dict[str, Any]:
    """Returns whether the engine has finished initializing."""
    return {"ready": True, "error": None}


@open_router.get("/auth/required")
async def auth_required() -> dict[str, Any]:
    """Tells the SPA which auth screen to show, if any.

    The SPA can't rely on probing a protected endpoint because browsers
    auto-attach cached HTTP Basic credentials, masking whether auth is
    actually required. This explicit signal drives the SPA:

    - state "required" → show the login screen (a credential is set)
    - state "setup"    → show the first-run "create admin password" screen
                          (shipped, unclaimed)
    - state "ok"       → skip straight to the app (dev / anonymous allowed)
    """
    from server.api.auth import auth_state
    state = auth_state()
    return {"required": state == "required", "state": state}


@open_router.post("/auth/setup")
async def auth_setup(request: Request) -> dict[str, Any]:
    """First-run claim: set the initial admin password on an unclaimed instance.

    Open (no auth) so a fresh shipped controller can be claimed, but succeeds
    only while unclaimed — once a credential exists it returns 409 and the
    caller must log in and change it through the authenticated path.
    """
    from server.api.auth import auth_state, claim_instance
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        claim_instance(body.get("password", ""), body.get("username", ""))
    except ValueError as e:
        reason = str(e)
        if reason == "already_claimed":
            raise HTTPException(
                status_code=409,
                detail="This controller is already set up. Log in instead.",
            )
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters.",
        )
    return {"ok": True, "state": auth_state()}


@open_router.get("/status")
async def get_status(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> dict[str, Any]:
    """System status, uptime, project info.

    Host/network identifiers (hostname, local IP, bind address) are returned
    only to authenticated callers. On a claimed instance an anonymous caller
    gets the non-sensitive subset so this open endpoint can't be used for LAN
    reconnaissance; on an open (dev / anonymous-allowed) instance everything
    is already public, so the full set is returned.
    """
    include_sensitive = programmer_auth_satisfied(request, credentials)
    return _get_engine().get_status(include_sensitive=include_sensitive)


@open_router.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check for monitoring and container orchestration."""
    engine = _get_engine()
    status = engine.get_status()
    devices_list = engine.devices.list_devices() if engine.devices else []
    total = len(devices_list)
    connected = sum(1 for d in devices_list if d.get("connected"))
    orphaned = sum(1 for d in devices_list if d.get("orphaned"))
    disabled = sum(1 for d in devices_list if d.get("enabled") is False)
    # Cached available-update version maintained by the periodic auto-check
    # (empty when up to date). Read from state — never triggers a network
    # check here, so monitoring callers and the tray can poll it cheaply.
    update_available = engine.state.get("system.update_available", "") if engine.state else ""
    return {
        "status": "healthy",
        "version": status.get("version", "unknown"),
        "uptime_seconds": status.get("uptime_seconds", 0),
        "update_available": update_available or "",
        "devices": {
            "total": total,
            "connected": connected,
            "disconnected": total - connected - orphaned - disabled,
            "orphaned": orphaned,
            "disabled": disabled,
        },
        "cloud": {
            "connected": status.get("cloud_connected", False),
        },
    }


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

    from server.api.auth import _deployment_is_dev
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
    from server.cloud.config import save_cloud_config
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

    # _start_cloud_agent isolates its own failures and leaves cloud_agent None;
    # reflect that so the UI doesn't report "enabled" over an agent that never
    # started.
    agent_started = engine.cloud_agent is not None

    result: dict[str, Any] = {
        "success": True,
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
    """Update system configuration sections. Body is a partial system.json structure.

    Validates TLS invariants up-front so a partial save can't lock the user
    out (e.g., saving ``enabled=true`` + ``auto_generate=false`` + empty cert
    paths would refuse to start the server on next launch, with no UI path
    back to fix it).
    """
    from server.system_config import get_system_config
    cfg = get_system_config()
    body = await request.json()

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    # Validate TLS section against the proposed post-patch state.
    if "tls" in body and isinstance(body["tls"], dict):
        current_tls = dict(cfg.section("tls") or {})
        proposed_tls = {**current_tls, **body["tls"]}
        if (
            proposed_tls.get("enabled")
            and not proposed_tls.get("auto_generate")
            and (
                not str(proposed_tls.get("cert_file") or "").strip()
                or not str(proposed_tls.get("key_file") or "").strip()
            )
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Provided-cert mode needs both a certificate and a key. "
                    "Upload a certificate, or switch back to auto-generate."
                ),
            )

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

    # If the admin password changed, re-sync the OS login on a Pi appliance
    # (no-op everywhere else). C10.
    if isinstance(body.get("auth"), dict) and "programmer_password" in body["auth"]:
        try:
            from server import host_control
            host_control.sync_os_password()
        except Exception:  # noqa: BLE001 — OS sync must never fail the save
            from server.utils.logger import get_logger
            get_logger(__name__).warning("OS password sync after change failed", exc_info=True)

    # ISC / mDNS-advertise toggles take effect immediately (no restart) by
    # reconciling the live subsystems against the just-saved config.
    if "isc" in body or "discovery" in body:
        try:
            await _get_engine().reconcile_runtime_services()
        except Exception:  # noqa: BLE001 — reconcile must never fail the save
            from server.utils.logger import get_logger
            get_logger(__name__).warning(
                "Runtime service reconcile after config change failed", exc_info=True
            )

    # Log level applies live (no restart) so the "Settings saved" toast is
    # truthful and switching to Debug actually engages verbose console logging.
    if isinstance(body.get("logging"), dict) and "level" in body["logging"]:
        from server.utils.logger import set_log_level
        set_log_level(str(cfg.get("logging", "level", "info")))

    # File-logging settings apply live too — toggling file logging off or
    # changing the rotation size/count rebuilds the file handler now, so the
    # controls aren't silent no-ops until the next restart.
    if isinstance(body.get("logging"), dict) and any(
        k in body["logging"] for k in ("file_enabled", "max_size_mb", "max_files")
    ):
        from server.utils.logger import set_file_logging
        set_file_logging()

    # Mirror the effective update channel into state so the Updates view shows
    # the new channel after a Settings change instead of a stale value until
    # restart (system.update_channel is otherwise only written at boot).
    if isinstance(body.get("updates"), dict) and "channel" in body["updates"]:
        try:
            _get_engine().state.set(
                "system.update_channel",
                cfg.get("updates", "channel", "stable"),
                source="system",
            )
        except Exception:  # noqa: BLE001 — state mirror must never fail the save
            pass

    return {"success": True, "updated_sections": updated_sections}


@router.post("/system/restart")
async def restart_system(request: Request) -> dict[str, Any]:
    """Trigger an OpenAVC process restart.

    Emits ``system.restart_requested`` on the engine event bus. The handler
    registered at startup (server/main.py) flushes logs, runs a graceful
    shutdown, and exits — service managers (NSSM / systemd / Docker) bring
    the process back. In dev mode, ``_spawn_replacement`` handles relaunch.

    Body (optional): ``{"mode": "graceful" | "hard"}``. Default is graceful,
    which delays exit ~2s to flush logs. "hard" exits immediately.
    """
    mode = "graceful"
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("mode") in ("graceful", "hard"):
            mode = body["mode"]
    except Exception:  # noqa: BLE001 — no body / invalid JSON → default mode
        pass

    engine = _get_engine()
    # Fire-and-forget: the registered handler sleeps a beat then exits the
    # process, so awaiting emit() means the HTTP response never reaches the
    # caller. Schedule the emit as a background task and respond immediately
    # — the dialog uses this 200 as its cue to start polling for the new
    # listener to come back up.
    import asyncio
    task = asyncio.create_task(engine.events.emit("system.restart_requested", {"mode": mode}))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return {
        "status": "restarting",
        "mode": mode,
        "delay_seconds": 2 if mode == "graceful" else 0,
    }


# --- HTTPS / TLS ---


@open_router.get("/certificate")
async def download_ca_certificate():
    """Serve the auto-generated CA cert so panel devices can trust it.

    No auth — this is a public certificate (it's already presented during
    every TLS handshake) and panels need to fetch it before they can speak
    HTTPS without warnings.

    404 when:
      - TLS is off (no CA exists).
      - TLS is on with a user-provided cert (caller brings their own CA).
      - TLS is on with auto_generate but the CA file is missing.
    """
    from fastapi.responses import Response
    from server.system_config import get_system_config

    # Read live config (not the import-time constants) so a just-saved TLS
    # change is reflected without waiting for a restart.
    sys_cfg = get_system_config()
    if not sys_cfg.get("tls", "enabled") or not sys_cfg.get("tls", "auto_generate"):
        raise HTTPException(status_code=404, detail="No CA certificate available")

    ca_path = sys_cfg.data_dir / "tls" / "ca.crt"
    if not ca_path.exists():
        raise HTTPException(status_code=404, detail="CA certificate not found")

    return Response(
        content=ca_path.read_bytes(),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="openavc-ca.crt"'},
    )


def _cloud_cert_block(sys_cfg) -> dict[str, Any]:
    """The trusted-certificate section of tls-status.

    Merges three sources: the installed cert files (dates, names), the live
    serving holder (is the SNI path actually answering), and the agent's
    certificate manager (in-flight phase, typed errors, retry state).
    """
    from datetime import datetime, timezone

    from server import tls as tls_module
    from server.api._engine import get_engine_optional

    engine = get_engine_optional()
    agent = getattr(engine, "cloud_agent", None) if engine else None
    manager = agent.cert_manager if agent else None

    block: dict[str, Any] = {
        "enabled": bool(sys_cfg.get("tls", "cloud_cert", False)),
        "paired": agent is not None,
        "available": bool(
            agent and agent.connected and agent.has_capability("trusted_certs")
        ),
        "active": False,
        "hostname_suffix": "",
        "expires_at": None,
        "renews_at": None,
        "phase": "idle",
        "last_error": "",
        "last_error_detail": "",
        "last_attempt_at": "",
        "retry_pending": False,
    }

    holder_state = tls_module.cloud_cert_holder().get()
    if holder_state is not None and holder_state.expires_at > datetime.now(timezone.utc):
        block["active"] = True
        block["hostname_suffix"] = holder_state.hostname_suffix

    facts = tls_module.read_cloud_cert_facts(sys_cfg.data_dir)
    if facts:
        block["hostname_suffix"] = block["hostname_suffix"] or facts["hostname_suffix"]
        block["expires_at"] = facts["expires_at"].isoformat()
        block["renews_at"] = facts["renews_at"].isoformat()

    if manager:
        mgr = manager.get_status()
        block["phase"] = mgr["phase"]
        block["last_error"] = mgr["last_error"]
        block["last_error_detail"] = mgr["last_error_detail"]
        block["last_attempt_at"] = mgr["last_attempt_at"]
        block["retry_pending"] = mgr["retry_pending"]
        block["hostname_suffix"] = block["hostname_suffix"] or mgr["hostname_suffix"]

    return block


@router.get("/system/tls-status")
async def get_tls_status() -> dict[str, Any]:
    """Surface current TLS state for the Programmer IDE's Security card.

    Shape:
      - TLS off: {"enabled": false, cloud_cert: {...}}
      - TLS on:  {enabled, port, redirect_http, mode, cert: {...},
                  cloud_cert: {...}, [error]}

    Cert dict contains: subject, issuer, expires_at (ISO 8601),
    days_until_expiry, fingerprint (sha256 hex), sans (list), warnings (list).

    Warnings may include: "expired", "expiring-soon", "hostname-mismatch".
    On cert-read failure, "error" is set at the top level and "cert" is null.

    cloud_cert covers the cloud-issued trusted certificate: enabled (the
    user opted in), paired/available (a cloud pairing exists / the session
    offers the feature), active (currently served via SNI), hostname_suffix,
    expires_at/renews_at, plus the issuance phase and last typed error. It
    is present even with TLS off — the Settings card's "get a trusted
    certificate" callout (which starts by turning HTTPS on) needs the
    pairing/enrollment state before HTTPS exists.

    Reads live config (not the import-time constants) so the Security card's
    immediate re-fetch after a PATCH reflects the just-saved cert/mode rather
    than the stale pre-restart state.
    """
    from server.system_config import get_system_config

    sys_cfg = get_system_config()
    if not sys_cfg.get("tls", "enabled"):
        return {"enabled": False, "cloud_cert": _cloud_cert_block(sys_cfg)}

    cert_file = str(sys_cfg.get("tls", "cert_file") or "")
    key_file = str(sys_cfg.get("tls", "key_file") or "")
    mode = "provided" if (cert_file and key_file) else "auto"

    if mode == "provided":
        from pathlib import Path
        cert_path = Path(cert_file)
    else:
        cert_path = sys_cfg.data_dir / "tls" / "server.crt"

    status: dict[str, Any] = {
        "enabled": True,
        "port": sys_cfg.get("tls", "port"),
        "redirect_http": sys_cfg.get("tls", "redirect_http"),
        "mode": mode,
        "cert": None,
        "cloud_cert": _cloud_cert_block(sys_cfg),
    }

    if not cert_path.exists():
        status["error"] = f"Certificate file not found: {cert_path}"
        return status

    try:
        from server import tls as tls_module
        info = tls_module.read_cert_info(cert_path)
    except (ValueError, OSError) as exc:
        status["error"] = f"Could not read certificate: {exc}"
        return status

    warnings = list(info.warnings)
    # Hostname-mismatch: compare cert SANs against the host's current identifiers.
    try:
        bind_address = sys_cfg.get("network", "bind_address", "0.0.0.0")
        hostnames, ips = tls_module.collect_local_identifiers(bind_address)
        all_current = set(hostnames) | set(ips)
        all_current.discard("127.0.0.1")
        all_current.discard("localhost")
        all_current.discard("::1")
        if all_current and not all_current.intersection(info.sans):
            warnings.append("hostname-mismatch")
    except Exception:  # noqa: BLE001 — diagnostic-only, never block the endpoint
        pass

    status["cert"] = {
        "subject": info.subject,
        "issuer": info.issuer,
        "expires_at": info.expires_at.isoformat(),
        "days_until_expiry": info.days_until_expiry,
        "fingerprint": info.fingerprint_sha256,
        "sans": info.sans,
        "warnings": warnings,
    }
    return status


@router.post("/system/tls/cloud-cert/enable")
async def enable_cloud_cert() -> dict[str, Any]:
    """Enroll for a cloud-issued trusted certificate (or retry after a failure).

    Requires HTTPS on and an active cloud pairing. Issuance runs
    asynchronously over the cloud connection — poll /api/system/tls-status
    for the result. Calling while already enabled is the manual early-retry
    path, bypassing the daily failure backoff.
    """
    from server.api._engine import get_engine_optional
    from server.system_config import get_system_config

    sys_cfg = get_system_config()
    if not sys_cfg.get("tls", "enabled"):
        raise HTTPException(
            status_code=400,
            detail="Enable HTTPS first — the trusted certificate is served "
                   "over the HTTPS listener.",
        )

    engine = get_engine_optional()
    agent = getattr(engine, "cloud_agent", None) if engine else None
    manager = agent.cert_manager if agent else None
    if manager is None:
        raise HTTPException(
            status_code=400,
            detail="Pair this system with OpenAVC Cloud first — the "
                   "certificate is issued through the cloud connection.",
        )

    started, reason = await manager.enable()
    result: dict[str, Any] = {"enabled": True, "started": started}
    if not started:
        result["reason"] = reason
        result["message"] = {
            "busy": "A certificate request is already in progress.",
            "not_connected": "Not connected to the cloud right now — "
                             "enrollment will run automatically once the "
                             "connection is back.",
            "not_available": "The cloud connection does not offer trusted "
                             "certificates.",
        }.get(reason, reason)
    return result


@router.post("/system/tls/cloud-cert/disable")
async def disable_cloud_cert() -> dict[str, Any]:
    """Turn the trusted certificate off: stop serving it, delete the files,
    and notify the cloud best-effort. Never blocked by cloud reachability
    (works even after unpairing)."""
    from server.api._engine import get_engine_optional
    from server.system_config import get_system_config

    engine = get_engine_optional()
    agent = getattr(engine, "cloud_agent", None) if engine else None
    manager = agent.cert_manager if agent else None
    if manager is not None:
        await manager.disable()
    else:
        # Not paired (anymore) — still clean up locally.
        from server import tls as tls_module

        sys_cfg = get_system_config()
        sys_cfg.set("tls", "cloud_cert", False)
        sys_cfg.save()
        tls_module.remove_cloud_cert(sys_cfg.data_dir)
    return {"enabled": False}


_UPLOAD_MAX_BYTES = 100_000  # 100 KB — real cert+key combos are 5-20 KB even with chains


@router.post("/system/tls/upload-cert")
async def upload_tls_cert(
    cert: UploadFile = File(...),
    key: UploadFile = File(...),
) -> dict[str, Any]:
    """Accept a user-provided cert + matching private key, write to data_dir/tls/.

    Lets a non-technical admin install a third-party certificate without
    touching the server's filesystem. The frontend follows this with a
    PATCH /api/system/config that points ``tls.cert_file`` / ``tls.key_file``
    at the returned paths and sets ``auto_generate=false``.

    Validation (rejects with 400 on any failure):
      * Both files non-empty.
      * Total payload <= 100 KB.
      * Cert parses as PEM-encoded X.509.
      * Key parses as a passphrase-free PEM private key.
      * Cert and key public-key DER bytes match.

    Files are written atomically (``.tmp`` + ``os.replace``) so a torn write
    can never leave half-rotated artifacts on disk. POSIX hosts get
    ``0600`` on the key file.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert_bytes = await cert.read()
    key_bytes = await key.read()

    if not cert_bytes:
        raise HTTPException(status_code=400, detail="Certificate file is empty.")
    if not key_bytes:
        raise HTTPException(status_code=400, detail="Key file is empty.")
    if len(cert_bytes) + len(key_bytes) > _UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Files too large (max {_UPLOAD_MAX_BYTES // 1000} KB combined).",
        )

    try:
        parsed_cert = x509.load_pem_x509_certificate(cert_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Certificate is not valid PEM-encoded X.509: {exc}",
        ) from exc

    try:
        parsed_key = serialization.load_pem_private_key(key_bytes, password=None)
    except TypeError as exc:
        # cryptography raises TypeError when a password is required but None was supplied.
        raise HTTPException(
            status_code=400,
            detail=(
                "Private key is passphrase-protected. Re-export it without a "
                "passphrase, or put a reverse proxy in front of OpenAVC."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Key is not a valid PEM-encoded private key: {exc}",
        ) from exc

    # Match check via SubjectPublicKeyInfo DER bytes — works uniformly for RSA, EC, Ed25519, etc.
    try:
        cert_pub_der = parsed_cert.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_pub_der = parsed_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not compare cert and key public keys: {exc}",
        ) from exc

    if cert_pub_der != key_pub_der:
        raise HTTPException(status_code=400, detail="Certificate and key do not match.")

    # Atomic write to data_dir/tls/
    import os
    from server.system_config import get_system_config

    data_dir = get_system_config().data_dir
    tls_dir = data_dir / "tls"
    try:
        tls_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"TLS data directory is not writable: {tls_dir} ({exc})",
        ) from exc

    cert_path = tls_dir / "user-cert.pem"
    key_path = tls_dir / "user-key.pem"
    cert_tmp = cert_path.with_name(cert_path.name + ".tmp")
    key_tmp = key_path.with_name(key_path.name + ".tmp")

    try:
        cert_tmp.write_bytes(cert_bytes)
        key_tmp.write_bytes(key_bytes)
        os.replace(cert_tmp, cert_path)
        os.replace(key_tmp, key_path)
    except OSError as exc:
        # Best-effort cleanup of any partial temps.
        cert_tmp.unlink(missing_ok=True)
        key_tmp.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"Could not write certificate files: {exc}",
        ) from exc

    if os.name == "posix":
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass  # non-fatal — fall through; tls.read_cert_info still works

    # Surface metadata so the UI can render the success card without a refetch.
    from server import tls as tls_module

    info = tls_module.read_cert_info(cert_path)

    # Heuristic warning when the user uploaded a CA cert instead of a server cert.
    warnings = list(info.warnings)
    try:
        bc = parsed_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        if bc.ca:
            warnings.append("is-ca-cert")
    except x509.ExtensionNotFound:
        pass

    return {
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "fingerprint": info.fingerprint_sha256,
        "subject": info.subject,
        "issuer": info.issuer,
        "expires_at": info.expires_at.isoformat(),
        "days_until_expiry": info.days_until_expiry,
        "sans": info.sans,
        "warnings": warnings,
    }


# --- Network Adapters ---


@router.get("/network/adapters")
async def get_network_adapters() -> dict[str, Any]:
    """List available network adapters for control interface selection."""
    from server.discovery.network_scanner import get_network_adapters as _get_adapters
    return {"adapters": _get_adapters()}


@router.get("/system/serial-ports")
async def get_serial_ports() -> dict[str, Any]:
    """List the serial ports on this server host for the device connection picker.

    Covers USB-to-serial adapters (with vendor / serial-number identity where the
    OS exposes it) and any built-in UARTs. The ports are the server's own — a USB
    adapter must be plugged into the machine running OpenAVC, not the browser's.
    """
    from server.transport.serial_transport import list_serial_ports
    return {"ports": list_serial_ports()}


@router.post("/system/reboot")
async def reboot_system() -> dict[str, str]:
    """Reboot the host via the root privileged helper (Pi appliance).

    The server runs with NoNewPrivileges=true, so it cannot reboot directly
    (sudo can't elevate). The root-owned helper performs the reboot; this just
    submits the request. Available only where the helper is installed.
    """
    from server import host_control
    from server.utils.logger import get_logger

    if not host_control.helper_available():
        raise HTTPException(
            status_code=501,
            detail="Reboot is not available on this deployment. Restart the device manually.",
        )

    get_logger(__name__).info("System reboot requested via API")
    if not host_control.request_reboot():
        raise HTTPException(status_code=500, detail="Could not submit reboot request.")
    return {"status": "rebooting"}


# --- SSH (Pi appliance) ---


@router.get("/system/ssh")
async def get_ssh_status() -> dict[str, Any]:
    """SSH availability and current state for the Settings toggle.

    ``supported`` is true only on a Pi appliance (the privileged helper is
    installed); the toggle is hidden otherwise. ``enabled`` reflects whether
    sshd is running now (null if it couldn't be determined).
    """
    from server import host_control
    return host_control.ssh_status()


@router.post("/system/ssh")
async def set_ssh_state(request: Request) -> dict[str, Any]:
    """Enable or disable SSH on a Pi appliance.

    SSH ships off; enabling it allows logging in as the ``openavc`` user with
    the admin password. The root helper performs the change; this waits briefly
    for its result. Returns ``{ok, enabled, pending, error}`` — ``pending`` means
    the change was submitted but unconfirmed before the timeout.
    """
    from server import host_control
    if not host_control.helper_available():
        raise HTTPException(status_code=501, detail="SSH control is not available on this deployment.")
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict) or "enabled" not in body:
        raise HTTPException(status_code=422, detail="Missing 'enabled' boolean.")
    enabled = bool(body["enabled"])
    result = await host_control.set_ssh(enabled)
    return {**result, "enabled": enabled}


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


# --- Simulation ---


@router.get("/simulation/status")
async def simulation_status() -> dict[str, Any]:
    """Get simulation status."""
    return _get_engine().simulation.status()


@router.post("/simulation/start")
async def simulation_start(request: Request) -> dict[str, Any]:
    """Start simulation for all devices (or specific device_ids).

    An empty or absent body simulates every device. A present body must be
    valid JSON; ``device_ids``, when supplied, must be a list of device-id
    strings. Malformed JSON is a 400 rather than a silent simulate-all, and a
    non-list ``device_ids`` is a 400 rather than an opaque 500.
    """
    import json
    device_ids = None
    raw = await request.body()
    if raw and raw.strip():
        try:
            body = json.loads(raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")
        if isinstance(body, dict) and body.get("device_ids") is not None:
            device_ids = body["device_ids"]
            if not isinstance(device_ids, list) or not all(
                isinstance(d, str) for d in device_ids
            ):
                raise HTTPException(
                    status_code=400,
                    detail="'device_ids' must be a list of device id strings.",
                )
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
