"""System status and configuration REST endpoints.

What's left after the domain routers were split out: readiness/liveness,
the status payload, the version block, reading and patching system.json,
and asking the process to restart. The neighbouring modules own the rest —
``auth.py``, ``isc.py``, ``cloud.py``, ``tls.py``, ``host.py``,
``updates.py``, ``simulation.py``.
"""

from typing import Any

from openavc.api.host_policy import EndpointPolicy, endpoint_policy, ProgrammerRoute, PublicProgrammerRoute
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasicCredentials

from openavc.api._engine import _get_engine
from openavc.core.host_policy import HostCategory
from openavc.api.auth import _basic, programmer_auth_satisfied
from openavc.utils.request_origin import (
    is_local_console_request,
    is_tunneled_request,
)

router = APIRouter(route_class=ProgrammerRoute)
open_router = APIRouter(route_class=PublicProgrammerRoute)

# What a secret reads as on the way out, and the value a PATCH must never
# take literally on the way back in. One constant so the two halves of that
# round trip cannot drift; the Programmer's copy of it is in
# SystemSettingsView.tsx, which strips these before saving.
REDACTED = "***"

# The key GET /api/system/config adds for the environment's overrides.
ENVIRONMENT_KEY = "_environment"

# The key it adds for who can connect as this server is running it: the
# resolved choice, any problem, and the safety net while one is armed.
LISTEN_KEY = "_listen"

# And for Reach devices through, resolved: the chosen adapter's name, the
# address device traffic binds to now, and whether it is connected.
REACH_KEY = "_reach_devices"

# Strong references to fire-and-forget tasks. asyncio only holds a weak
# reference to a bare create_task(), so an unreferenced task can be garbage
# collected mid-flight; keep it alive until it finishes.
_BACKGROUND_TASKS: set = set()


@open_router.get("/startup-status")
@endpoint_policy(EndpointPolicy(HostCategory.RUNTIME))
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

    ``tunneled`` says this request arrived over the cloud remote-UI tunnel.
    The IDE asks because every address in its Panel Access card is a LAN
    address, and a reader on the far side of the tunnel is not on that LAN.
    It is a fact about the caller's own connection rather than a host
    identifier, so it is not gated with the set above. The page cannot work
    it out for itself: in subdomain tunnel mode the browser's own origin is
    the right one to publish, and in path mode it is not.
    """
    include_sensitive = programmer_auth_satisfied(request, credentials)
    status = _get_engine().get_status(include_sensitive=include_sensitive)
    status["tunneled"] = is_tunneled_request(request)
    return status


@open_router.get("/health")
@endpoint_policy(EndpointPolicy(HostCategory.RUNTIME))
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
    from openavc.updater.host import get_firmware_host
    firmware_host = get_firmware_host()
    ownership = ({"update_owner": "firmware", "firmware_version": firmware_host.firmware_version,
                  "update_guidance": firmware_host.guidance} if firmware_host else {})
    return {
        **ownership,
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
    from openavc import listen
    from openavc.system_config import get_system_config
    cfg = get_system_config()
    # Settled first (a server started some other way settles on first ask),
    # so the body below reads the choices as they will be saved.
    listen.current()
    data = cfg.to_dict()
    # Redact sensitive values. The password is a digest now and still redacted:
    # a digest is offline-crackable, and handing one to every authenticated
    # caller of this endpoint would put it somewhere system.json's 0600 isn't.
    if data.get("auth", {}).get("programmer_password"):
        data["auth"]["programmer_password"] = REDACTED
    if data.get("auth", {}).get("api_key"):
        data["auth"]["api_key"] = REDACTED
    if data.get("cloud", {}).get("system_key"):
        data["cloud"]["system_key"] = REDACTED
    # Not a system.json section: which fields the service's environment holds,
    # and how this server was installed, so Settings can lock those fields and
    # say where they are set. PATCH ignores it if a client sends it back.
    from openavc.updater.platform import detect_deployment_type
    data[ENVIRONMENT_KEY] = {
        "deployment_type": detect_deployment_type().value,
        "overrides": cfg.env_overrides(),
    }
    data[LISTEN_KEY] = _listen_block(cfg)
    from openavc.updater.host import get_firmware_host
    if get_firmware_host():
        data[ENVIRONMENT_KEY]["configuration_owner"] = "firmware"
    # Reach devices through, resolved: the adapter's name and the address
    # device traffic binds to now. PATCH ignores it.
    import asyncio

    from openavc import reach_devices
    try:
        data[REACH_KEY] = (await asyncio.to_thread(reach_devices.current, cfg)).to_dict()
    except Exception:  # noqa: BLE001 — the rest of Settings still loads
        data[REACH_KEY] = None
    return data


def _listen_block(cfg: Any) -> dict[str, Any]:
    """Who can connect, as running: what the listeners bound (which can
    differ from the saved choice until a restart, or when the chosen adapter
    is missing), whether a restart is waiting, and the safety net."""
    from openavc import listen, listen_safety_net

    plan = listen.current()
    running = plan.to_dict()
    saved = listen.ListenChoice.from_config(cfg)
    running["restart_needed"] = (
        saved.mode != plan.choice.mode or saved.adapter != plan.choice.adapter
    )
    running["safety_net"] = listen_safety_net.status(cfg.data_dir)
    return running


def _listen_choice_from(cfg: Any, network: dict[str, Any], adapters: list) -> Any:
    """The choice a PATCH or a preview asks for, validated: the mode, and for
    one adapter an adapter this computer has (or the one already chosen).
    Raises 422 naming what is wrong."""
    from openavc import listen

    mode = network.get(listen.MODE_KEY, cfg.stored("network", listen.MODE_KEY))
    if mode not in listen.MODES:
        raise HTTPException(
            status_code=422,
            detail=(
                'Accept connections from is "any" (any network), "adapter" '
                '(one network) or "this_computer" (this computer only).'
            ),
        )
    if mode != listen.MODE_ADAPTER:
        return listen.ListenChoice(mode=mode)
    stored = cfg.stored("network", listen.ADAPTER_KEY) or {}
    identity = network.get(listen.ADAPTER_KEY, stored)
    if not isinstance(identity, dict):
        raise HTTPException(
            status_code=422,
            detail='Choose the adapter as {"name": ..., "mac": ...}.',
        )
    identity = {
        k: str(identity[k]).strip()
        for k in ("name", "mac")
        if isinstance(identity.get(k), str) and identity[k].strip()
    }
    if not identity:
        raise HTTPException(
            status_code=422,
            detail="Choose which adapter to accept connections from.",
        )
    if identity != stored and listen.match_adapter(identity, adapters) is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This computer has no adapter {identity.get('name') or identity.get('mac')}. "
                "Choose one from the list."
            ),
        )
    return listen.ListenChoice(mode=mode, adapter=identity)


def _reach_choice_from(cfg: Any, value: Any, adapters: list) -> dict[str, str]:
    """The Reach devices through choice a PATCH asks for, validated: ``{}``
    for every connected adapter, or an adapter this computer has (or the one
    already chosen). Raises 422 naming what is wrong."""
    from openavc import listen, reach_devices

    if not isinstance(value, dict):
        raise HTTPException(
            status_code=422,
            detail=(
                'Choose the adapter to reach devices through as {"name": ..., '
                '"mac": ...}, or {} for every connected adapter.'
            ),
        )
    if not value:
        return {}
    identity = {
        k: str(value[k]).strip()
        for k in ("name", "mac")
        if isinstance(value.get(k), str) and value[k].strip()
    }
    if not identity:
        raise HTTPException(
            status_code=422,
            detail=(
                'Choose the adapter to reach devices through as {"name": ..., '
                '"mac": ...}, or {} for every connected adapter.'
            ),
        )
    stored = reach_devices.clean_identity(cfg.stored("network", reach_devices.KEY))
    if identity != stored and listen.match_adapter(identity, adapters) is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This computer has no adapter {identity.get('name') or identity.get('mac')}. "
                "Choose one from the list."
            ),
        )
    return identity


def _connection_facts(request: Request) -> tuple[str, bool]:
    """The address of this computer the request arrived on, and whether it
    came from this computer's own screen or the cloud tunnel (which keep
    working under every choice)."""
    server = request.scope.get("server")
    through = str(server[0]) if server else ""
    return through, is_local_console_request(request) or is_tunneled_request(request)


def _engine_facts() -> tuple[list[str], bool]:
    """What would stop working: approved panels by their last address, and
    whether Inter-System is running."""
    try:
        engine = _get_engine()
    except HTTPException:
        return [], False
    approved: list[str] = []
    store = getattr(engine, "panel_devices", None)
    if store is not None:
        approved = [d.get("address") or "" for d in store.list_devices().get("approved", [])]
    return approved, getattr(engine, "isc", None) is not None


def cloud_remote_access_set_up() -> bool:
    """Whether remote access through OpenAVC Cloud is set up, which keeps
    working under every choice (its tunnel reaches the server on loopback)."""
    from openavc.cloud.config import remote_access_set_up

    try:
        agent = getattr(_get_engine(), "cloud_agent", None)
    except HTTPException:
        agent = None
    return remote_access_set_up(agent)


def own_screen_opens_programmer() -> bool:
    """Whether this computer's own screen can open the Programmer. Not on the
    appliance, whose screen shows only the panel. Like
    ``_panel_dim_available``, the deployment type is a true answer here
    because on the appliance the server and the screen are the same box."""
    try:
        from openavc.updater.platform import DeploymentType, detect_deployment_type

        return detect_deployment_type() != DeploymentType.ANDROID_APPLIANCE
    except Exception:
        return True


@router.post("/system/listen/preview")
async def preview_listen(request: Request) -> dict[str, Any]:
    """What saving a choice of who can connect would do, before it is saved:
    the addresses a panel could open, whether this browser keeps its
    connection (and so whether the safety net would be armed), what stops
    working, and whether remote access through OpenAVC Cloud is set up (it
    keeps working). Body: ``{"accept_connections_from": ..., "accept_connections_adapter": {...}}``."""
    import asyncio

    from openavc import listen
    from openavc.system_config import get_system_config

    cfg = get_system_config()
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    adapters = await asyncio.to_thread(listen.list_adapters)
    candidate = _listen_choice_from(cfg, body, adapters)
    through, from_this_computer = _connection_facts(request)
    approved, isc_on = _engine_facts()
    cloud = await asyncio.to_thread(cloud_remote_access_set_up)
    own_screen = await asyncio.to_thread(own_screen_opens_programmer)
    result = await asyncio.to_thread(
        listen.preview,
        candidate,
        adapters,
        connected_through=through,
        from_this_computer=from_this_computer,
        approved_panel_addresses=approved,
        isc_enabled=isc_on,
        own_screen_opens_programmer=own_screen,
        cloud_remote_access=cloud,
    )
    result["held_by"] = cfg.env_override("network", listen.MODE_KEY)
    return result


@router.post("/system/listen/confirm")
async def confirm_listen() -> dict[str, Any]:
    """Called by the Programmer when it loads. Keeps a change of who can
    connect that the safety net was watching (it can only arrive through a
    connection the new choice allows), and hands over, once, the sentence
    saying the net went back. Either may be null."""
    from openavc import listen, listen_safety_net
    from openavc.system_config import get_system_config

    data_dir = get_system_config().data_dir
    return {
        "kept": listen_safety_net.confirm(data_dir, listen.current().choice),
        "went_back": listen_safety_net.take_notice(data_dir),
    }


def _env_held_conflicts(cfg: Any, body: dict[str, Any]) -> list[str]:
    """Refusal sentences for every field in ``body`` the environment holds.

    A value equal to the one the server is running with is a client echoing
    back what it read, so it is dropped from ``body`` instead: refusing it
    would break a GET-then-PATCH round trip, and writing it would copy the
    environment's value into system.json. The two credentials are stored as
    digests, so a sent value cannot be compared and any change is refused.
    """
    from openavc.updater.host import get_firmware_host
    firmware_host = get_firmware_host()
    conflicts: list[str] = []
    for section_name, section_data in list(body.items()):
        if not isinstance(section_data, dict):
            continue
        for key in list(section_data):
            var = cfg.env_override(section_name, key)
            if not var:
                continue
            credential = section_name == "auth" and key in ("programmer_password", "api_key")
            if not credential and section_data[key] == cfg.get(section_name, key):
                del section_data[key]
                continue
            if firmware_host:
                conflicts.append(
                    f"{section_name}.{key} is managed by the product's firmware. "
                    "Contact product support if this setting needs to change."
                )
                continue
            conflicts.append(
                f"{section_name}.{key} is set by {var} in this server's environment, "
                f"so it can't be changed here. Change {var} where the server is "
                f"started, then restart it."
            )
        if not section_data:
            del body[section_name]
    return conflicts


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

    from openavc.updater.host import get_firmware_host
    firmware_host = get_firmware_host()
    if firmware_host and "updates" in body:
        raise HTTPException(status_code=409, detail=firmware_host.guidance)

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

    # Panel access takes exactly two values. Checked up front like the TLS
    # invariants: the generic loop below would write whatever it was handed,
    # and the rule that reads it fails closed, so a typo would silently lock
    # every panel out under a setting that reads as valid.
    if "panels" in body and isinstance(body["panels"], dict) and "access" in body["panels"]:
        from openavc.core.panel_devices import ACCESS_VALUES
        if body["panels"]["access"] not in ACCESS_VALUES:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Panel access is either 'approved' (approved panels only) "
                    "or 'open' (anyone on the network)."
                ),
            )
    # A trusted proxy that does not parse is dropped at runtime, so every
    # client behind it is metered as the proxy with nothing said. Refused here
    # instead, naming the entry.
    if (
        "network" in body and isinstance(body["network"], dict)
        and "trusted_proxies" in body["network"]
    ):
        from openavc.utils.request_origin import parse_trusted_proxies
        proxies = body["network"]["trusted_proxies"]
        _, bad = parse_trusted_proxies(proxies)
        if bad or not isinstance(proxies, list):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Trusted proxies is a list of addresses or ranges, such as "
                    '["10.0.0.5", "10.0.1.0/24"].'
                    + (f" Not an address or range: {', '.join(bad)}." if bad else "")
                ),
            )
    # The one-time notice flag is a switch the Programmer turns off (Dismiss);
    # anything but a bool would read as "due" forever or never.
    if (
        "panels" in body and isinstance(body["panels"], dict)
        and "upgrade_notice" in body["panels"]
        and not isinstance(body["panels"]["upgrade_notice"], bool)
    ):
        raise HTTPException(status_code=422, detail="upgrade_notice is true or false.")

    # A caller that GETs this config and PATCHes it back sends the redaction
    # marker in place of every secret it never saw. Dropping those is what
    # keeps that round trip from setting each one to a literal `***` — which,
    # now that two of them are hashed, would be an unrecoverable lockout rather
    # than a visible mistake. The Programmer strips them client-side already;
    # this is the same rule where every other client meets it.
    for section in ("auth", "cloud"):
        if isinstance(body.get(section), dict):
            body[section] = {
                k: v for k, v in body[section].items() if v != REDACTED
            }

    # A field the environment holds runs from the environment at every start,
    # so a change saved here would read back as saved and never take effect.
    # Refused before anything is written, naming the variable.
    conflicts = _env_held_conflicts(cfg, body)
    if conflicts:
        raise HTTPException(status_code=409, detail=" ".join(conflicts))

    # Who can connect: validated as a pair, written as a pair. The adapter
    # is kept only for "adapter", and the rollback mirror (bind_address) is
    # never taken from a client: it is recomputed from the choice below.
    from openavc import listen, reach_devices

    network_body = body.get("network") if isinstance(body.get("network"), dict) else None
    listen_choice = None
    reach_choice: dict[str, str] | None = None
    listen_adapters: list = []
    if network_body is not None:
        if listen.MIRROR_KEY in network_body:
            # A client echoing what it read is dropped; a change is refused,
            # because nothing reads this key any more.
            if network_body.pop(listen.MIRROR_KEY) != cfg.get("network", listen.MIRROR_KEY):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "The bind address is now Accept connections from: set "
                        "network.accept_connections_from to \"any\", \"adapter\" "
                        "or \"this_computer\" instead."
                    ),
                )
        if listen.MODE_KEY in network_body or listen.ADAPTER_KEY in network_body:
            import asyncio

            listen_adapters = await asyncio.to_thread(listen.list_adapters)
            listen_choice = _listen_choice_from(cfg, network_body, listen_adapters)
            network_body[listen.MODE_KEY] = listen_choice.mode
            network_body[listen.ADAPTER_KEY] = dict(listen_choice.adapter)
        # Reach devices through: the same adapter identity, validated the
        # same way. Its rollback mirror (control_interface) is never taken
        # from a client either.
        if reach_devices.MIRROR_KEY in network_body:
            if network_body.pop(reach_devices.MIRROR_KEY) != cfg.get(
                "network", reach_devices.MIRROR_KEY
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "The control interface is now Reach devices through: set "
                        "network.reach_devices_through to {} (every connected "
                        'adapter) or {"name": ..., "mac": ...} instead.'
                    ),
                )
        if reach_devices.KEY in network_body and (
            network_body[reach_devices.KEY] is None
            or network_body[reach_devices.KEY] == cfg.get("network", reach_devices.KEY)
        ):
            # A client echoing what it read (null is "not settled yet", never
            # a choice): nothing to change or validate.
            del network_body[reach_devices.KEY]
        if reach_devices.KEY in network_body:
            import asyncio

            if not listen_adapters:
                listen_adapters = await asyncio.to_thread(listen.list_adapters)
            reach_choice = _reach_choice_from(
                cfg, network_body[reach_devices.KEY], listen_adapters
            )
            network_body[reach_devices.KEY] = reach_choice
        if not network_body:
            del body["network"]

    # The admin password never goes through the generic loop below: it is
    # stored as a digest, so the typed value has exactly two destinations —
    # the hash, and the one-shot OS-sync request. Taken out here so no future
    # edit to the loop can persist it as typed. `None` means the caller did not
    # send the field at all, which is different from sending "" to clear it.
    #
    # Both are trimmed, and whitespace-only is refused rather than trimmed into
    # a clear — the rule and both sentences live beside the credential, in
    # `openavc/api/auth.py`. A new password also meets the same length floor
    # first-run setup applies; empty still clears it.
    from openavc.api.auth import (
        API_KEY_NEEDS_PASSWORD,
        API_KEY_ONLY_WHITESPACE,
        PASSWORD_ONLY_WHITESPACE,
        PASSWORD_TOO_SHORT,
        api_key_would_be_sole_credential,
        normalize_credential,
        password_too_short,
    )

    new_password: str | None = None
    if isinstance(body.get("auth"), dict) and "programmer_password" in body["auth"]:
        new_password = normalize_credential(body["auth"].pop("programmer_password"))
        if new_password is None:
            raise HTTPException(status_code=400, detail=PASSWORD_ONLY_WHITESPACE)
        if new_password and password_too_short(new_password):
            raise HTTPException(status_code=400, detail=PASSWORD_TOO_SHORT)

    # Same for the API key, for the same reason: it is a digest now, so the
    # typed value's only destination is the hash.
    new_api_key: str | None = None
    if isinstance(body.get("auth"), dict) and "api_key" in body["auth"]:
        new_api_key = normalize_credential(body["auth"].pop("api_key"))
        if new_api_key is None:
            raise HTTPException(status_code=400, detail=API_KEY_ONLY_WHITESPACE)

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

    safety_net = None
    if listen_choice is not None:
        listen.refresh_mirror(cfg)
        safety_net = _arm_or_disarm(cfg, request, listen_choice, listen_adapters)
    if reach_choice is not None:
        # Applies to the next connection or scan, with no restart.
        reach_devices.refresh_mirror(cfg, listen_adapters)
        reach_devices.refresh(cfg, listen_adapters)

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

    # Panel access applies live: switching to approved sends every panel
    # that was in only because the mode was open to the waiting screen. A
    # dismissed upgrade notice rides the same push, so every open Programmer
    # drops it, not only the one that pressed Dismiss.
    if isinstance(body.get("panels"), dict) and ("access" in body["panels"] or "upgrade_notice" in body["panels"]):
        try:
            await _get_engine().panel_access_changed()
        except Exception:  # noqa: BLE001 — the setting is saved; the sockets catch up on reconnect
            from openavc.utils.logger import get_logger
            get_logger(__name__).warning(
                "Applying the panel access change to live sockets failed", exc_info=True
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

    result: dict[str, Any] = {"status": "updated", "updated_sections": updated_sections}
    if listen_choice is not None:
        result["safety_net"] = safety_net
    return result


def _arm_or_disarm(cfg: Any, request: Request, choice: Any, adapters: list) -> dict | None:
    """Arm the safety net when this change cuts off the browser making it;
    clear it otherwise. Returns its status, or None."""
    from openavc import listen, listen_safety_net

    plan = listen.current()
    running = plan.choice
    if choice.mode == running.mode and choice.adapter == running.adapter:
        listen_safety_net.disarm(cfg.data_dir)
        return None
    through, from_this_computer = _connection_facts(request)
    keeps = listen.keeps_connection(listen.resolve(choice, adapters), through, from_this_computer)
    if keeps:
        listen_safety_net.disarm(cfg.data_dir)
        return None
    new_plan = listen.resolve(choice, adapters)
    listen_safety_net.arm(
        cfg.data_dir,
        running,
        choice,
        previous_name=plan.adapter_name,
        new_name=new_plan.adapter_name,
    )
    return listen_safety_net.status(cfg.data_dir)


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
