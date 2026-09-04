"""System status and configuration REST endpoints.

What's left after the domain routers were split out: readiness/liveness,
the status payload, the version block, reading and patching system.json,
and asking the process to restart. The neighbouring modules own the rest —
``auth.py``, ``isc.py``, ``cloud.py``, ``tls.py``, ``host.py``,
``updates.py``, ``simulation.py``.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasicCredentials

from openavc.api._engine import _get_engine
from openavc.api.auth import _basic, programmer_auth_satisfied
from openavc.utils.request_origin import is_local_console_request

router = APIRouter()
open_router = APIRouter()

# What a secret reads as on the way out, and the value a PATCH must never
# take literally on the way back in. One constant so the two halves of that
# round trip cannot drift; the Programmer's copy of it is in
# SystemSettingsView.tsx, which strips these before saving.
REDACTED = "***"

# Strong references to fire-and-forget tasks. asyncio only holds a weak
# reference to a bare create_task(), so an unreferenced task can be garbage
# collected mid-flight; keep it alive until it finishes.
_BACKGROUND_TASKS: set = set()


@open_router.get("/startup-status")
async def startup_status() -> dict[str, Any]:
    """Returns whether the engine has finished initializing."""
    return {"ready": True, "error": None}


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


@router.get("/system/version")
async def get_system_version() -> dict[str, Any]:
    """Current version info, platform, and update channel."""
    import platform as plat
    from pathlib import Path
    from openavc.version import __version__
    from openavc.system_config import get_system_config
    cfg = get_system_config()
    return {
        "version": __version__,
        "channel": cfg.get("updates", "channel", "stable"),
        "platform": plat.system().lower(),
        "kiosk_available": Path("/opt/openavc/scripts/panel-kiosk.sh").exists(),
        "panel_dim_available": _panel_dim_available(),
    }


def _panel_dim_available() -> bool:
    """Whether this instance drives a screen that can actually be dimmed.

    The all-in-one appliance only, for now. The deployment type describes the
    SERVER, which on the appliance is the same box as the panel -- so it is a
    true answer there and nowhere else. A tablet running the panel app against
    a remote server can dim just as well, but this gate cannot see it; when
    that client learns to say so, this is the one place that changes.
    """
    try:
        from openavc.updater.platform import DeploymentType, detect_deployment_type

        return detect_deployment_type() == DeploymentType.ANDROID_APPLIANCE
    except Exception:
        return False


# Bounds applied where the settings are READ rather than where they are
# written -- a project can arrive from a cloud template, an import or a backup
# restore, so a check at one write door is not the one that holds.
_DIM_TIMEOUT_MIN, _DIM_TIMEOUT_MAX = 30, 7200
_DIM_LEVEL_MIN, _DIM_LEVEL_MAX = 1, 90
# 0 is not a dim level but a mode: blackout. The backlight goes to the panel's
# floor and the content is covered in black, which reads as off across a room
# while every touch still works. It is spelled as the bottom of the same
# control because "as dark as this panel goes" is what somebody reaching for 0
# means -- and true sleep is not available: measured on appliance hardware
# 2026-09-04, a slept screen did not wake on a real finger, only on the power
# button.
_DIM_LEVEL_BLACKOUT = 0
# The lowest brightness this will hand a panel. Not a hardware floor -- the
# panel's own is lower -- but a panel dimmer than this cannot be read, and the
# control that would undo it is on somebody's laptop rather than on the panel.
# Enforced HERE, not only in the Programmer, because this value can arrive from
# a cloud template pushed to a hundred panels at once: `ProjectSettings` only
# admits settings whose worst case a person standing in front of the panel can
# undo, and an unreadable screen is not one of those.
_BRIGHTNESS_MIN = 10


@open_router.get("/system/display-idle")
async def get_display_idle(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(_basic),
) -> dict[str, Any]:
    """The panel display policy, for whatever is drawing the panel here.

    Open to the local console because the caller is the thing showing the
    panel on this very box -- the appliance shell, over loopback, with no
    credential of its own. A remote caller still has to authenticate.

    Read from the PROJECT rather than from system.json, so a customer sets it
    once and deploys one template to a hundred panels. See
    ``ProjectSettings`` for why only settings that fail safe may live there.

    ``hold`` is resolved HERE rather than handing the shell a state key to
    evaluate: what counts as truthy is a platform rule (``condition_eval``),
    and a second implementation of it in Kotlin is a rule that can disagree
    with itself.
    """
    if not (
        is_local_console_request(request)
        or programmer_auth_satisfied(request, credentials)
    ):
        raise HTTPException(status_code=401, detail="Authentication required")

    from openavc.core.project_loader import DisplaySettings

    try:
        engine = _get_engine()
    except HTTPException:
        engine = None

    project = getattr(engine, "project", None) if engine else None
    d = getattr(getattr(project, "settings", None), "display", None) or DisplaySettings()

    def _num(value: Any, default: int, lo: int, hi: int) -> int:
        try:
            return min(max(int(value), lo), hi)
        except (TypeError, ValueError):
            return default

    level_raw = d.idle_dim_level_percent
    try:
        blackout = int(level_raw) == _DIM_LEVEL_BLACKOUT
    except (TypeError, ValueError):
        blackout = False

    hold_key = str(d.idle_dim_hold_state_key or "").strip()
    hold = False
    if hold_key and engine is not None:
        try:
            hold = bool(engine.state.get(hold_key))
        except Exception:
            # A key nothing has written yet, or an engine still starting.
            # Not-held is the safe reading: it dims, which anyone can undo by
            # touching the panel, where a stuck hold silently disables the
            # whole feature.
            hold = False

    brightness = d.brightness_percent
    if brightness is not None:
        brightness = _num(brightness, 100, _BRIGHTNESS_MIN, 100)

    return {
        "enabled": bool(d.idle_dim_enabled),
        "timeout_seconds": _num(
            d.idle_dim_timeout_seconds, 300, _DIM_TIMEOUT_MIN, _DIM_TIMEOUT_MAX
        ),
        # Blackout is its own flag rather than a level of 0 on the wire, so the
        # client never has to know that 0 is a mode. The level it gets is
        # always a real percentage it can multiply by.
        "level_percent": (
            _DIM_LEVEL_MIN if blackout
            else _num(level_raw, 20, _DIM_LEVEL_MIN, _DIM_LEVEL_MAX)
        ),
        "blackout": blackout,
        "wake_passes_touch": bool(d.idle_dim_wake_passes_touch),
        "hold_state_key": hold_key,
        "hold": hold,
        # None means "not managed": the panel keeps whatever its own
        # maintenance control set. Every panel is in that state until somebody
        # sets it, so it must not read as "set me to full".
        "brightness_percent": brightness,
    }


@router.get("/system/config")
async def get_system_config_endpoint() -> dict[str, Any]:
    """Get current system configuration (redacts secrets)."""
    from openavc.system_config import get_system_config
    cfg = get_system_config()
    data = cfg.to_dict()
    # Redact sensitive values. The password is a digest now and still redacted:
    # a digest is offline-crackable, and handing one to every authenticated
    # caller of this endpoint would put it somewhere system.json's 0600 isn't.
    if data.get("auth", {}).get("programmer_password"):
        data["auth"]["programmer_password"] = REDACTED
    if data.get("auth", {}).get("api_key"):
        data["auth"]["api_key"] = REDACTED
    if data.get("auth", {}).get("panel_lock_code"):
        data["auth"]["panel_lock_code"] = REDACTED
    if data.get("cloud", {}).get("system_key"):
        data["cloud"]["system_key"] = REDACTED
    if data.get("isc", {}).get("auth_key"):
        data["isc"]["auth_key"] = REDACTED
    return data


@router.patch("/system/config")
async def update_system_config(request: Request) -> dict[str, Any]:
    """Update system configuration sections. Body is a partial system.json structure.

    Validates TLS invariants up-front so a partial save can't lock the user
    out (e.g., saving ``enabled=true`` + ``auto_generate=false`` + empty cert
    paths would refuse to start the server on next launch, with no UI path
    back to fix it).
    """
    from openavc.system_config import get_system_config
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

    # A caller that GETs this config and PATCHes it back sends the redaction
    # marker in place of every secret it never saw. Dropping those is what
    # keeps that round trip from setting each one to a literal `***` — which,
    # now that two of them are hashed, would be an unrecoverable lockout rather
    # than a visible mistake. The Programmer strips them client-side already;
    # this is the same rule where every other client meets it.
    if isinstance(body.get("auth"), dict):
        body["auth"] = {k: v for k, v in body["auth"].items() if v != REDACTED}
    for section in ("cloud", "isc"):
        if isinstance(body.get(section), dict):
            body[section] = {
                k: v for k, v in body[section].items() if v != REDACTED
            }

    # The admin password never goes through the generic loop below: it is
    # stored as a digest, so the typed value has exactly two destinations —
    # the hash, and the one-shot OS-sync request. Taken out here so no future
    # edit to the loop can persist it as typed. `None` means the caller did not
    # send the field at all, which is different from sending "" to clear it.
    new_password: str | None = None
    if isinstance(body.get("auth"), dict) and "programmer_password" in body["auth"]:
        raw = body["auth"].pop("programmer_password")
        new_password = "" if raw is None else str(raw)

    # Same for the API key, for the same reason: it is a digest now, so the
    # typed value's only destination is the hash.
    new_api_key: str | None = None
    if isinstance(body.get("auth"), dict) and "api_key" in body["auth"]:
        raw = body["auth"].pop("api_key")
        new_api_key = "" if raw is None else str(raw)

    # Refuse a save that would leave the API key as the only credential, before
    # anything is written. Same shape and the same reason as the TLS check
    # above: a partial save that locks the user out with no UI path back is
    # worth refusing at the door, because afterwards there is no door. The rule
    # itself lives beside the credential it is about, in `openavc/api/auth.py`.
    #
    # Only when this save actually touches one of the two credentials. A box
    # already in that state — OPENAVC_API_KEY, or a system.json provisioned by
    # hand — must still be able to change its log level, and refusing every
    # unrelated save would widen the lockout instead of closing it. What covers
    # that box is the startup warning in `Engine.start`.
    from openavc.api.auth import (
        API_KEY_NEEDS_PASSWORD,
        api_key_would_be_sole_credential,
    )
    if (new_api_key is not None or new_password is not None) and (
        api_key_would_be_sole_credential(api_key=new_api_key, password=new_password)
    ):
        raise HTTPException(status_code=400, detail=API_KEY_NEEDS_PASSWORD)

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

    if new_password is not None:
        from openavc.api.auth import store_admin_password
        store_admin_password(new_password)
        if "auth" not in updated_sections:
            updated_sections.append("auth")

    if new_api_key is not None:
        from openavc.api.auth import store_api_key
        store_api_key(new_api_key)
        if "auth" not in updated_sections:
            updated_sections.append("auth")

    cfg.save()

    # If the admin password changed, re-sync the OS login on a Pi appliance
    # (no-op everywhere else). C10.
    if new_password is not None:
        try:
            from openavc import host_control
            host_control.sync_os_password(new_password)
        except Exception:  # noqa: BLE001 — OS sync must never fail the save
            from openavc.utils.logger import get_logger
            get_logger(__name__).warning("OS password sync after change failed", exc_info=True)

    # ISC / mDNS-advertise toggles take effect immediately (no restart) by
    # reconciling the live subsystems against the just-saved config.
    if "isc" in body or "discovery" in body:
        try:
            await _get_engine().reconcile_runtime_services()
        except Exception:  # noqa: BLE001 — reconcile must never fail the save
            from openavc.utils.logger import get_logger
            get_logger(__name__).warning(
                "Runtime service reconcile after config change failed", exc_info=True
            )

    # Log level applies live (no restart) so the "Settings saved" toast is
    # truthful and switching to Debug actually engages verbose console logging.
    if isinstance(body.get("logging"), dict) and "level" in body["logging"]:
        from openavc.utils.logger import set_log_level
        set_log_level(str(cfg.get("logging", "level", "info")))

    # File-logging settings apply live too — toggling file logging off or
    # changing the rotation size/count rebuilds the file handler now, so the
    # controls aren't silent no-ops until the next restart.
    if isinstance(body.get("logging"), dict) and any(
        k in body["logging"] for k in ("file_enabled", "max_size_mb", "max_files")
    ):
        from openavc.utils.logger import set_file_logging
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

    return {"status": "updated", "updated_sections": updated_sections}


@router.post("/system/restart")
async def restart_system(request: Request) -> dict[str, Any]:
    """Trigger an OpenAVC process restart.

    Emits ``system.restart_requested`` on the engine event bus. The handler
    registered at startup (openavc/main.py) flushes logs, runs a graceful
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
    task = asyncio.create_task(
        engine.events.emit("system.restart_requested", {"mode": mode, "source": "api"})
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return {
        "status": "restarting",
        "mode": mode,
        "delay_seconds": 2 if mode == "graceful" else 0,
    }
