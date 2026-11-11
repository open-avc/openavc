"""
OpenAVC — main entry point.

Starts the FastAPI server with the runtime engine.

Usage:
    python -m openavc.main
"""

import os
import sys

# In frozen (PyInstaller) builds, this exe doubles as the simulator entry point.
# simulation.py launches: sys.executable --simulator --config <path>
if getattr(sys, 'frozen', False) and len(sys.argv) > 1 and sys.argv[1] == '--simulator':
    sys.argv = [sys.argv[0]] + sys.argv[2:]  # strip the --simulator flag
    from openavc.simulator.__main__ import main as _sim_main
    _sim_main()
    sys.exit(0)

# Asking the binary its version must not start anything. There was no handling
# for this at all, so `--version` fell through to main(), which pre-flights the
# listening port -- on a box where OpenAVC is already running (the normal case
# when you are checking what is installed) it printed "port 8080 is already in
# use" and wrote a startup-error marker, instead of answering the question.
if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
    from openavc.version import __version__ as _v
    print(_v)
    sys.exit(0)

# Going back to an earlier version where the app has no Rollback (Docker: the
# image tag changed back by hand). An earlier version reads every login from
# the project file, which since the login file holds "saved on this system" in
# their place; this writes each saved login back into the project and the
# Project Library copies, as Rollback does, and exits without starting the
# server. Run with OpenAVC stopped: a running server would move the logins
# straight back out on its next save.
if len(sys.argv) > 1 and sys.argv[1] == "--put-back-logins":
    from openavc.cli_put_back import main as _put_back_main
    sys.exit(_put_back_main())

# CA trust store for stdlib `ssl` in the frozen macOS app. A PyInstaller .app has
# no OpenSSL-readable CA bundle, and macOS keeps its roots in the Keychain rather
# than in files OpenSSL reads, so `ssl.create_default_context()` fails every
# verified handshake with CERTIFICATE_VERIFY_FAILED. OpenAVC's own internet
# connections (the cloud agent and its tunnels included) verify with
# utils/outbound.py instead, which asks the Keychain; this keeps any other
# default context (a device connection that verifies its certificate) working
# by pointing stdlib ssl at the bundled certifi store. Linux reads
# /etc/ssl/certs and Windows reads its system store, so this is scoped to frozen
# macOS. An explicit SSL_CERT_FILE already in the environment always wins.
if (
    getattr(sys, 'frozen', False)
    and sys.platform == 'darwin'
    and not os.environ.get('SSL_CERT_FILE')
):
    try:
        import certifi
        os.environ['SSL_CERT_FILE'] = certifi.where()
    except Exception:  # pragma: no cover - never let CA setup block startup
        pass

import asyncio
import importlib
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import JSONResponse

from openavc import config, restart, runtime_flags, status_folder
from openavc.api import rest, ws, isc_ws, discovery as discovery_api, plugins as plugins_api, assets as assets_api, themes as themes_api, ai_proxy as ai_proxy_api, ir_learn_ws
from openavc.api import simulator_proxy
from openavc.api.host_policy import (
    EndpointPolicy, HostPolicyMiddleware, PolicyMount,
    configure_policy, endpoint_policy, validate_policies,
)
from openavc.core.host_policy import HostCategory, HostPolicy
from openavc.api.routes import network as network_routes
from openavc.api.routes import pair as pair_routes
from openavc.api.routes import root as root_routes
from openavc.api.routes import setup as setup_routes
from openavc.audit.session import AuditManager
from openavc.core.engine import Engine
from openavc.discovery.engine import DiscoveryEngine
from openavc.redirects import build_http_listener_app, build_redirect_app, certified_host_for
from openavc.drivers.registry import list_registered_drivers
from openavc.utils.logger import get_logger
from openavc.utils.request_origin import UVICORN_FORWARDED_ALLOW_IPS
from openavc.version import __version__

log = get_logger(__name__)

# Cap inbound WebSocket frames well below uvicorn's implicit 16 MiB default.
# The /ws and /isc/ws endpoints only ever receive small control/state JSON
# (panel UI events, device commands, ISC state sync); real project, asset, and
# driver uploads go over REST, not the socket. 1 MiB is orders of magnitude
# above any legitimate frame yet a fixed, explicit ceiling on the two
# unauthenticated/pre-auth socket paths (websockets rejects an oversized frame
# with a 1009 close before buffering it). Applied to every listener that
# serves openavc.main:app.
_WS_MAX_SIZE = 1024 * 1024

# The peers uvicorn takes X-Forwarded-For from, applied to every listener that
# serves openavc.main:app. Passed explicitly because uvicorn otherwise reads
# them from its own FORWARDED_ALLOW_IPS variable, and set to "*" there a LAN
# client could write X-Forwarded-For: 127.0.0.1 and be taken for the machine's
# own screen. The reasoning, and how this composes with trust_forwarded_for,
# is in openavc/utils/request_origin.py.
_FORWARDED_ALLOW_IPS = list(UVICORN_FORWARDED_ALLOW_IPS)

# Set log level from config
logging.getLogger().setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

# Seed starter projects on first run
from openavc.core.project_library import ensure_starter_projects
ensure_starter_projects()

# Create engine
engine = Engine(config.PROJECT_PATH)

# Create discovery engine
discovery_engine = DiscoveryEngine()

# One device audit at a time; it and a Discovery scan refuse each other.
audit_manager = AuditManager(engine.devices, discovery_engine)


def _resolve_listen_plan():
    """Who can connect: main() resolved it before binding, and a server
    started some other way resolves it here. It feeds a warning and the
    banner, so a failure is logged and never blocks startup (None)."""
    try:
        return config.listen_plan()
    except Exception:
        log.exception("Could not resolve who can connect")
        return None


def _open_without_password(plan) -> bool:
    if plan is None or not plan.reaches_network:
        return False
    return not (config.PROGRAMMER_PASSWORD or config.API_KEY)


def _warn_if_open_without_password(plan) -> None:
    """Warn when the network can connect and there is no admin password."""
    if not _open_without_password(plan):
        return
    log.warning("=" * 60)
    where = (
        "any network" if plan.mode == "any"
        else f"the {plan.adapter_name or 'chosen'} network"
    )
    log.warning("  WARNING: Accepting connections from %s with NO authentication.", where)
    log.warning("  Anyone on the network can access and control devices.")
    log.warning("  Set an admin password (open the Programmer), or choose")
    log.warning("  This computer only in Settings > Network > Accept connections from.")
    log.warning("=" * 60)


def _settle_updates_before_start(data_dir) -> None:
    """The update work before the engine starts, on an install whose updates
    are OpenAVC's own (no firmware host): leftover Windows update tasks
    deleted, a failed update rolled back, stale update records swept."""
    _remove_leftover_windows_update_tasks()
    _roll_back_a_failed_update(data_dir)
    _sweep_stale_update_records(data_dir)


def _remove_leftover_windows_update_tasks() -> None:
    """Windows: delete the update tasks earlier versions left registered,
    which a restart can run again, installing an old version. First, before
    the automatic rollback can schedule a task of its own."""
    if sys.platform != "win32":
        return
    from openavc.updater.platform import DeploymentType, detect_deployment_type
    if detect_deployment_type() == DeploymentType.WINDOWS_INSTALLER:
        from openavc.updater.rollback import remove_leftover_update_tasks
        remove_leftover_update_tasks()


def _roll_back_a_failed_update(data_dir) -> None:
    """An update whose new version kept failing to start (the crash
    detection in updater/rollback.py) is undone here, and the process exits
    for the service manager to start the old version."""
    from openavc.updater.rollback import check_rollback_needed
    if not check_rollback_needed(data_dir):
        return
    from pathlib import Path
    from openavc.updater.rollback import perform_rollback, restore_pre_update_data
    # Restore user data from the pre-update backup BEFORE swapping the
    # code back: the rolled-back code may predate the running version's
    # project-format migrations, so the restore must run on this side.
    restore_pre_update_data(data_dir, project_path=Path(config.PROJECT_PATH))
    # The earlier version reads each device login from the project,
    # and the Project Library is not in the pre-update backup.
    from openavc.core.device_logins import put_back_for_rollback
    put_back_for_rollback(data_dir, Path(config.PROJECT_PATH))
    if perform_rollback(data_dir, automatic=True):
        # Windows: exit 42 tells NSSM not to restart (installer handles it)
        # Linux: exit 0 triggers systemd restart, ExecStartPre applies rollback
        exit_code = 42 if sys.platform == "win32" else 0
        log.warning("Rollback initiated, exiting process (code %d)", exit_code)
        os._exit(exit_code)


def _sweep_stale_update_records(data_dir) -> None:
    """Defense in depth: drop a stale Linux apply-update.json left behind by
    a failed/aborted apply, so update-helper.sh (root, ExecStartPre) can't
    re-consume it on an unrelated restart. On systemd the helper runs before
    us, so this mainly covers non-systemd / crash-before-helper. The same for
    a leftover apply-rollback marker: one that survives to this point was
    never consumed by the pre-start wrapper, and would silently downgrade
    the install on a later unrelated restart."""
    stale_apply = data_dir / "apply-update.json"
    if stale_apply.exists():
        try:
            stale_apply.unlink()
            log.warning("Removed stale apply-update.json at startup")
        except OSError:
            log.exception("Could not remove stale apply-update.json at startup")

    from openavc.updater.rollback import clear_stale_rollback_marker
    clear_stale_rollback_marker(data_dir)


def _load_discovery_hints() -> None:
    """Discovery's signals, once the engine has registered the drivers: each
    driver's hints, and the _discovery.py companions that ship beside
    built-in or community drivers."""
    discovery_engine.load_driver_hints_from_registry(list_registered_drivers())
    from openavc.system_config import (
        DRIVER_DEFINITIONS_DIR,
        DRIVER_REPO_DIR,
    )
    discovery_engine.load_discovery_companions_from_dirs(
        [DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR],
    )


def _watch_listen_choice() -> asyncio.Task:
    """Follow the chosen adapter: back, gone, or on a new DHCP address, the
    server restarts through the restart request and listens again where the
    choice now points (openavc/listen.py).

    The same ten-second tick checks the safety net's deadline: a change that
    cut off the browser making it goes back by itself unless the Programmer
    is opened through the new choice (listen_safety_net.py). It also re-reads
    the Reach devices through adapter's address, off the event loop, so
    device traffic follows a DHCP renewal with no restart
    (openavc/reach_devices.py).
    """
    from openavc import listen, listen_safety_net, reach_devices
    from openavc.system_config import get_system_config

    async def restart_to_follow_adapter(_reason: str) -> None:
        await engine.events.emit(
            "system.restart_requested", {"mode": "graceful", "source": "listen"}
        )

    async def safety_net_tick() -> None:
        try:
            if reach_devices.choice():
                await asyncio.to_thread(reach_devices.refresh)
        except Exception:  # the safety net below must still tick
            log.debug("Reach devices through: refresh failed", exc_info=True)
        await listen_safety_net.tick(get_system_config(), restart_to_follow_adapter)

    return asyncio.create_task(
        listen.ListenWatcher(restart_to_follow_adapter, on_tick=safety_net_tick).run()
    )


def _log_banner(plan) -> None:
    """The addresses to open, logged once the engine is ready: the only
    instruction a manual or checkout run ever gets."""
    if config.TLS_ENABLED:
        scheme, port = "https", config.TLS_PORT
    else:
        scheme, port = "http", config.HTTP_PORT
    log.info("=" * 60)
    log.info(f"  Panel UI:    {scheme}://localhost:{port}/panel")
    log.info(f"  Programmer:  {scheme}://localhost:{port}/programmer")
    log.info(f"  REST API:    {scheme}://localhost:{port}/api")
    _log_lan_access(plan, scheme, port)
    if config.TLS_ENABLED and config.TLS_REDIRECT_HTTP:
        log.info(
            f"  HTTP redirect: http://localhost:{config.HTTP_PORT} "
            f"-> https://localhost:{config.TLS_PORT}"
        )
    log.info("=" * 60)


def _log_lan_access(plan, scheme: str, port: int) -> None:
    local_ip = str(engine.get_status().get("local_ip") or "")
    if plan is None:
        return
    if plan.problem is not None:
        # The chosen adapter is missing, so this listens on loopback
        # only. One sentence, the same one every surface shows.
        log.warning(f"  LAN access:  none. {plan.problem.message}")
    elif plan.local_only:
        # Every URL below this point is built from a LAN address, and the
        # kernel refuses all of them on a loopback-only listener. Saying
        # so is the only useful line here: the Dashboard's Panel Access
        # card already tells the same truth from the other side.
        log.info(
            "  LAN access:  none. Accepting connections from this computer "
            "only; change Settings > Network > Accept connections from and "
            "restart to reach it from the network."
        )
    elif local_ip and local_ip != "127.0.0.1":
        _log_lan_urls(plan, local_ip, scheme, port)


def _log_lan_urls(plan, local_ip: str, scheme: str, port: int) -> None:
    only = f"  ({plan.adapter_name} only)" if plan.mode == "adapter" else ""
    log.info(f"  LAN access:  {scheme}://{local_ip}:{port}/panel{only}")
    if runtime_flags.port80_active:
        log.info(f"  Short URL:   http://{local_ip}/panel")
    certified = certified_host_for(local_ip) if config.TLS_ENABLED else None
    if certified:
        log.info(
            f"  Trusted URL: https://{certified}:{config.TLS_PORT}/panel"
            "  (no browser warnings)"
        )


async def _initialize_engine(app: FastAPI) -> None:
    """Run engine initialization in the background so HTTP serves immediately.

    Every step is inside the one ``try``: a failure anywhere is left in
    ``app.state.engine_error`` for the splash page and the log, never raised.
    """
    try:
        plan = _resolve_listen_plan()
        _warn_if_open_without_password(plan)

        from openavc.system_config import get_system_config
        from openavc.updater.host import get_firmware_host
        firmware_host = get_firmware_host()
        if not firmware_host:
            data_dir = get_system_config().data_dir
            _settle_updates_before_start(data_dir)

        await engine.start()

        # The engine is up on this version, which is the only evidence that an
        # update worked: the code imported, the project loaded, devices and
        # plugins came up, and the HTTP listener was already serving before
        # any of it. Confirm here rather than on a timer — a timer measures
        # whether anybody restarted the box, and an appliance is restarted by
        # its own supervisor and power-cycled in the room it lives in.
        if not firmware_host:
            from openavc.updater.rollback import confirm_startup
            confirm_startup(data_dir)

        _load_discovery_hints()

        # A restart request (Settings, the cloud agent, the listen watcher
        # below) ends this process: openavc/restart.py.
        async def _on_restart_requested(_event: str, data: dict) -> None:
            await restart.exit_for_restart(data, engine, app.state)

        engine.events.on("system.restart_requested", _on_restart_requested)
        app.state.listen_watcher = _watch_listen_choice()

        app.state.engine_ready = True
        _log_banner(plan)
    except Exception as e:
        app.state.engine_error = str(e)
        log.exception("Engine startup failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    validate_policies(app)
    app.state.host_policy_started = True
    app.state.engine_ready = False
    app.state.engine_error = None

    # Keep plugin tokens presented via the _plugin_token query param out of
    # uvicorn's access log. Done here (not at import) so uvicorn has already
    # configured its access logger and the filter lands on the live one.
    from openavc.api.plugin_ext import install_access_log_redaction
    install_access_log_redaction()

    log.info("=" * 60)
    log.info("  OpenAVC starting")
    log.info("=" * 60)

    # Run engine initialization in background so HTTP is available immediately
    init_task = asyncio.create_task(_initialize_engine(app))

    try:
        yield
    finally:
        watcher = getattr(app.state, "listen_watcher", None)
        if watcher is not None:
            watcher.cancel()
        from openavc.listen_sockets import push_listeners
        await push_listeners.stop()
        # Shutdown: wait for init to finish (or cancel if still running)
        if not init_task.done():
            init_task.cancel()
            try:
                await init_task
            except asyncio.CancelledError:
                pass
        # End an audit first: it holds project devices paused and its own
        # listeners open, and both are released before the engine goes down.
        try:
            await audit_manager.shutdown()
        except Exception:
            log.exception("Ending the device audit at shutdown failed")
        # Startup may acquire device/plugin clients before readiness.
        # The cancelled initializer has unwound; tear down those resources too.
        try:
            await engine.stop()
        finally:
            await simulator_proxy.aclose_client()
        log.info("OpenAVC stopped")


# Create FastAPI app
app = FastAPI(
    title="OpenAVC",
    description="Open-source AV room control platform",
    version=__version__,
    lifespan=lifespan,
)


def configure_host_policy(provider: HostPolicy) -> None:
    """Install the host's provider before startup; ordinary hosting is permissive."""
    configure_policy(app, provider)
    engine.host_policy = provider


configure_host_policy(engine.host_policy)
# FastAPI installs its OpenAPI/documentation routes during construction. They
# belong to the Programmer surface and retain their normal document responses.
for _documentation_route in app.routes:
    endpoint_policy(EndpointPolicy(HostCategory.PROGRAMMER))(_documentation_route.endpoint)

# Wire engine into API modules. rest.set_engine() populates the single shared
# slot in openavc.api._engine; ws.py reads that same slot, so it isn't wired
# separately here.
rest.set_engine(engine)
plugins_api.set_engine(engine)
assets_api.set_engine(engine)
themes_api.set_engine(engine)
ai_proxy_api.set_engine(engine)

# Let plugins mount HTTP routers under /api/plugins/<id>/ext/* (authed) and
# /api/plugins/<id>/guest/* (open, plugin-gated) at runtime. The loader calls
# these when such a plugin starts/stops; the app lives here.
from openavc.api.plugin_ext import (
    mount_plugin_guest_router,
    mount_plugin_router,
    unmount_plugin_guest_router,
    unmount_plugin_router,
)
engine.plugin_loader.set_router_hooks(
    lambda plugin_id, router, panel_paths=None, media_paths=None: mount_plugin_router(
        app, plugin_id, router, panel_paths, media_paths
    ),
    lambda plugin_id: unmount_plugin_router(app, plugin_id),
    lambda plugin_id, router, alias=None: mount_plugin_guest_router(app, plugin_id, router, alias),
    lambda plugin_id: unmount_plugin_guest_router(app, plugin_id),
)

# Wire discovery engine. A scan leaves alone every port a connected project
# device's driver holds.
discovery_api.set_discovery_engine(discovery_engine)
discovery_engine.held_connections = engine.devices.live_connections

# Wire the device audit (it pauses project devices and asks discovery).
from openavc.api.routes import audit as audit_routes
audit_routes.configure(audit_manager, discovery_engine)
# Problem reports check a device's driver files against the same library.
from openavc.api.routes import problem_reports as problem_report_routes
problem_report_routes.configure(discovery_engine)
discovery_api.set_broadcast_fn(engine.broadcast_ws)
discovery_api.set_app_engine(engine)

# Mount routers
app.include_router(rest.open_router)
app.include_router(rest.router)
app.include_router(ws.router)
app.include_router(simulator_proxy.router)        # authed: /simulator/api/*
app.include_router(simulator_proxy.open_router)   # open shell, declared after
app.include_router(simulator_proxy.ws_router)
app.include_router(ir_learn_ws.router)
app.include_router(isc_ws.router)
app.include_router(discovery_api.router)
app.include_router(plugins_api.open_router)
app.include_router(plugins_api.router)
app.include_router(assets_api.open_router)
app.include_router(assets_api.router)
app.include_router(themes_api.open_router)
app.include_router(themes_api.router)
app.include_router(ai_proxy_api.router)
app.include_router(pair_routes.router)
app.include_router(setup_routes.router)
app.include_router(root_routes.router)
app.include_router(network_routes.router)

# Return request-validation (422) failures as a single-string {"detail": ...}
# instead of FastAPI's raw list-of-dicts body, so a malformed request reads like
# every other API error instead of a wall of Pydantic JSON.
from openavc.api.errors import (
    StructuredApiError,
    request_validation_exception_handler,
    structured_api_error_handler,
)

app.add_exception_handler(RequestValidationError, request_validation_exception_handler)

from openavc.core.installed_equipment import InstalledEquipmentError


@app.exception_handler(InstalledEquipmentError)
async def installed_equipment_error(request, error):
    return JSONResponse(status_code=409, content={"detail": str(error)})

# The few errors that carry machine-readable fields put them *beside* the string
# detail rather than inside it, so every client still finds a readable sentence
# at {"detail": ...}. See openavc/api/errors.py for the contract.
app.add_exception_handler(StructuredApiError, structured_api_error_handler)

# CORS — allow same-origin and localhost by default.
# Additional origins can be set via OPENAVC_CORS_ORIGINS (comma-separated).
_cors_origins = [
    f"http://localhost:{config.HTTP_PORT}",
    f"http://127.0.0.1:{config.HTTP_PORT}",
]
_extra_origins = os.environ.get("OPENAVC_CORS_ORIGINS", "")
if _extra_origins:
    _cors_origins.extend(o.strip() for o in _extra_origins.split(",") if o.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# /programmer static files are served without auth — the SPA renders a login
# screen and gates itself. API routes remain protected by `require_programmer_auth`,
# which the SPA satisfies by sending an Authorization header on every fetch.
# Removing the static-file middleware lets the JS run so it can show a login
# form (instead of the browser's native Basic auth dialog, which can't pass
# credentials to WebSocket upgrades on most browsers).

# Which Programmer tab a request came from, so its own project.reloaded
# echo is recognisable (core/client_origin.py).
from openavc.middleware.client_origin import ClientOriginMiddleware
app.add_middleware(ClientOriginMiddleware)

# Per-IP rate limiting (outermost — runs before auth)
from openavc.middleware.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# Served while the engine initializes.
from openavc.middleware.startup_splash import StartupSplashMiddleware
app.add_middleware(StartupSplashMiddleware)

# Serve Panel UI static files
from openavc.system_config import WEB_PANEL_DIR, WEB_PROGRAMMER_DIR


def _panel_host_category(scope):
    path = _panel_files.get_path(scope)
    if path in (".", "index.html"):
        query = Request(scope).query_params
        if query.get("edit") == "1" or query.get("preview") == "1":
            return HostCategory.PROGRAMMER
        return HostCategory.PANEL
    return HostCategory.RENDER


if WEB_PANEL_DIR.exists():
    _panel_files = StaticFiles(directory=str(WEB_PANEL_DIR), html=True)
    app.router.routes.append(PolicyMount(
        "/panel", _panel_files,
        EndpointPolicy(_panel_host_category), name="panel",
    ))


def revalidate_header(path: str, content_type: str) -> str | None:
    """The Cache-Control for a response the browser must check before reusing.

    ``no-cache`` makes the browser revalidate through the ETag every time: a
    304 when nothing changed, so it stays cheap, and never a stale copy.

    /panel: everything. The panel iframe inside the Theme Studio otherwise
    serves cached panel.css / panel.js for hours, masking deployed fixes.

    /programmer and /simulator: the HTML page only. Its asset names change with
    every build, so the page is what must be fresh. Sent without a header it
    gets reused on the browser's own guess (a tenth of its age, so days for a
    page installed weeks ago): a Simulator window opened after an update ran
    the previous build, and once the old asset names are gone the page is
    blank.
    """
    if path.startswith("/panel"):
        return "no-cache"
    if path.startswith(("/programmer", "/simulator")) and content_type.startswith("text/html"):
        return "no-cache"
    return None


@app.middleware("http")
async def _revalidate_pages(request, call_next):
    response = await call_next(request)
    header = revalidate_header(request.url.path, response.headers.get("content-type", ""))
    if header:
        response.headers["Cache-Control"] = header
    return response

# Serve Programmer UI static files (Vite build output)
programmer_dir = WEB_PROGRAMMER_DIR
if programmer_dir.exists():
    app.router.routes.append(PolicyMount(
        "/programmer",
        StaticFiles(directory=str(programmer_dir), html=True),
        EndpointPolicy(HostCategory.PROGRAMMER),
        name="programmer",
    ))

# Added last so admission covers splash, cache and other response middleware as
# well as route dispatch. SDK hosts replace the provider, not this middleware.
app.add_middleware(HostPolicyMiddleware)


def _make_aux_redirect_server(app, port: int):
    """Build a best-effort auxiliary redirect listener on the listen plan's
    addresses (the ones the main listener settled on).

    Returns (serve coroutine, None) ready to run, or (None, err) when a socket
    cannot be bound — auxiliary listeners never block startup.

    Only the main server installs signal handlers (``listen_sockets.quiet_server``).
    Auxiliary listeners cancel via the FIRST_COMPLETED logic in the runners
    when the main server shuts down.
    """
    from openavc import listen_sockets

    try:
        sockets, _ = listen_sockets.bind_for_plan(config.listen_plan(), port, fallback=False)
    except listen_sockets.ListenBindError as exc:
        return None, exc.err
    aux_config = uvicorn.Config(
        app,
        port=port,
        log_level="warning",  # quiet: every redirect logs an info line otherwise
        # This listener serves the real app to loopback clients, panel
        # WebSocket included, so it needs the main server's frame ceiling —
        # uvicorn's default is smaller and would cut a large frame off on
        # the box's own screen only.
        ws_max_size=_WS_MAX_SIZE,
        forwarded_allow_ips=_FORWARDED_ALLOW_IPS,
    )
    server = listen_sockets.quiet_server(uvicorn.Server(aux_config))
    return server.serve(sockets=sockets), None


async def _serve_until_first_exit(tasks: list) -> None:
    """Run listener tasks until one finishes, then cancel the rest.

    When any task finishes (graceful shutdown, error, signal), the others are
    cancelled so we don't hang in asyncio.gather; the completed task's
    exception (if any) is re-raised.
    """
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


def _harden_tls_context(context) -> None:
    """Pin a TLS 1.2 floor and a modern cipher suite on a built SSLContext.

    TLS 1.0/1.1 are never negotiated — a guarantee of ours, not an accident of
    the stdlib default. The logic lives in ``openavc.tls`` (one home) so the
    cloud-issued cert's SNI-selected context gets the identical guarantee;
    applied to the HTTPS listener's context in `_run_tls`.
    """
    from openavc import tls as tls_module

    tls_module.harden_tls_context(context)


def _mark_if_port_80(port: int) -> None:
    """A listener is up on port 80, so typed URLs can drop the port (the
    banner's Short URL and the status routes read this)."""
    if port == 80:
        runtime_flags.port80_active = True


def _start_aux_listener(tasks: list, app, port: int, refusal: str) -> None:
    """Start a best-effort auxiliary listener beside the main one: its task
    joins ``tasks``. One that cannot bind is a warning, ``refusal`` with the
    bind error in its ``%s``, never a failed start."""
    serve, err = _make_aux_redirect_server(app, port)
    if serve is None:
        log.warning(refusal, err)
        return
    tasks.append(asyncio.create_task(serve))
    _mark_if_port_80(port)


def _start_port80_redirect(tasks: list, target_port: int, scheme: str) -> None:
    """The port-80 listener lets typed URLs drop the port entirely."""
    _start_aux_listener(
        tasks, build_redirect_app(target_port, scheme=scheme), 80,
        "Port-80 redirect listener could not bind (%s); typed URLs still need the port.",
    )


def _port80_redirect_beside_https() -> bool:
    """The port-80 redirect is wanted and no HTTPS-side listener owns 80."""
    if not config.PORT80_REDIRECT or config.TLS_PORT == 80:
        return False
    return not (config.TLS_REDIRECT_HTTP and config.HTTP_PORT == 80)


def _certificate_files():
    """The HTTPS certificate and key, generated when there are none. One that
    cannot be used stops the start the way a port that cannot be bound does:
    the sentence on stderr and in the startup-error file the tray app and
    other monitors read, then exit 1."""
    from openavc import tls as tls_module
    from openavc.system_config import get_system_config

    try:
        return tls_module.load_or_generate(config, get_system_config().data_dir)
    except tls_module.TLSConfigError as exc:
        msg = (
            "HTTPS is enabled but the TLS listener cannot start:\n"
            f"  {exc.reason}\n\n"
            "Fix the certificate configuration in Settings > Security,\n"
            "or disable HTTPS (Settings > Security, or OPENAVC_TLS_ENABLED=false)."
        )
        print(f"\n*** {msg} ***\n", file=sys.stderr)
        status_folder.write_startup_error("tls_error", msg)
        raise SystemExit(1) from exc


def _main_https_config(cert_path, key_path) -> "uvicorn.Config":
    main_config = uvicorn.Config(
        "openavc.main:app",
        port=config.TLS_PORT,
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        reload=False,
        log_level="info",
        ws_max_size=_WS_MAX_SIZE,
        forwarded_allow_ips=_FORWARDED_ALLOW_IPS,
    )
    # uvicorn builds the SSLContext from the cert/key when the config loads;
    # load it here so we can harden the built context directly (Server.serve()
    # then reuses it rather than rebuilding) and attach the SNI callback.
    main_config.load()
    if main_config.ssl is not None:
        _secure_https_context(main_config.ssl)
    return main_config


def _secure_https_context(context) -> None:
    """Harden the HTTPS context and choose its certificate by name: names on
    the cloud-issued cert get that cert, everything else (including all
    bare-IP HTTPS, which sends no SNI) keeps the self-signed leaf. The
    callback is installed even with no cloud cert on disk (empty holder =
    no-op) so a first enrollment while running takes effect without a
    restart."""
    from openavc import tls as tls_module
    from openavc.system_config import get_system_config

    _harden_tls_context(context)
    if config.TLS_CLOUD_CERT:
        tls_module.load_cloud_cert(get_system_config().data_dir)
    context.sni_callback = tls_module.make_sni_callback(tls_module.cloud_cert_holder())


def _served_app():
    """The app the main listener serves: the one its import string names.

    Run as ``python -m openavc.main``, this file executes twice: as
    ``__main__``, which runs ``main()``, and as ``openavc.main``, which uvicorn
    imports for ``"openavc.main:app"``. Each copy builds its own ``app``, and
    only the served copy's lifespan runs, so only it starts the engine, shows
    the startup splash and carries the plugin routes. A listener handed an
    app directly takes this one, never the module-level ``app`` of the copy
    that is running ``main()``.
    """
    return importlib.import_module("openavc.main").app


async def _run_tls(sockets: list) -> None:
    """Run the HTTPS listener and the listeners beside it concurrently: the
    HTTP port's (this machine served the same app as HTTPS, the rest
    redirected) when the redirect is on, and the port-80 redirect.

    ``sockets`` are the HTTPS port's, already bound by ``main()``. The
    auxiliary listeners are best-effort: one that cannot bind logs a warning
    and never blocks startup.
    """
    from openavc import listen_sockets

    cert_path, key_path = _certificate_files()
    listen_sockets.push_listeners.use_tls(str(cert_path), str(key_path))

    main_server = uvicorn.Server(_main_https_config(cert_path, key_path))
    tasks: list[asyncio.Task] = [asyncio.create_task(main_server.serve(sockets=sockets))]
    _mark_if_port_80(config.TLS_PORT)

    if config.TLS_REDIRECT_HTTP:
        _start_aux_listener(
            tasks,
            build_http_listener_app(_served_app(), build_redirect_app(config.TLS_PORT)),
            config.HTTP_PORT,
            f"HTTP redirect listener could not bind to port {config.HTTP_PORT} (%s); "
            "old http:// links will not auto-redirect.",
        )
    if _port80_redirect_beside_https():
        _start_port80_redirect(tasks, config.TLS_PORT, "https")

    await _serve_until_first_exit(tasks)


def _main_http_config() -> "uvicorn.Config":
    return uvicorn.Config(
        "openavc.main:app",
        port=config.HTTP_PORT,
        reload=False,
        log_level="info",
        ws_max_size=_WS_MAX_SIZE,
        forwarded_allow_ips=_FORWARDED_ALLOW_IPS,
    )


async def _run_http(sockets: list) -> None:
    """Run the plain-HTTP listener plus the port-80 convenience redirect.

    Only used when the port-80 redirect is enabled with HTTPS off; the
    single-listener path in ``main()`` serves alone otherwise.
    """
    main_server = uvicorn.Server(_main_http_config())
    tasks: list[asyncio.Task] = [asyncio.create_task(main_server.serve(sockets=sockets))]
    _mark_if_port_80(config.HTTP_PORT)
    if config.HTTP_PORT != 80:
        _start_port80_redirect(tasks, config.HTTP_PORT, "http")

    await _serve_until_first_exit(tasks)


def _bind_primary_port(plan):
    """The main listener's sockets and the plan they were bound for: adopted
    from a firmware supervisor (OPENAVC_LISTEN_FD) or bound here. A port that
    cannot be bound stops the start: the sentence on the console and in the
    startup-error file, then exit 1.

    The main listener's port is the HTTPS one when HTTPS is on; the HTTP
    port's listener beside it is best-effort and bound inside ``_run_tls``.
    During a restart the previous process can hold the port for a moment
    after it exits, so the bind retries through that window.
    """
    from openavc import listen_sockets

    port = config.TLS_PORT if config.TLS_ENABLED else config.HTTP_PORT
    retries = 20 if os.environ.get("OPENAVC_RESTARTING") == "1" else 1
    try:
        inherited = os.environ.get("OPENAVC_LISTEN_FD")
        if inherited is not None:
            return listen_sockets.inherited_for_plan(inherited, plan, port), plan
        return listen_sockets.bind_for_plan(plan, port, retries=retries)
    except listen_sockets.ListenBindError as exc:
        if config.TLS_ENABLED:
            msg = exc.message("HTTPS port", "OPENAVC_TLS_PORT")
        else:
            msg = exc.message("HTTP port", "OPENAVC_PORT")
        print(f"\n*** {msg} ***\n")
        status_folder.write_startup_error(exc.error_type, msg)
        raise SystemExit(1)


def _settle_listening(plan) -> None:
    """Now listening: the plan is the current one, a missing adapter is said
    once, the safety net's clock starts, and the restart marker is cleared
    so a future startup error isn't masked by repeated retries."""
    from openavc import listen, listen_safety_net
    from openavc.system_config import get_system_config

    listen.set_current(plan)
    if plan.problem is not None:
        log.warning(plan.problem.message)
    # A change that cut off the browser making it starts its clock now, when
    # the server is listening on it (openavc/listen_safety_net.py).
    listen_safety_net.note_listening(get_system_config().data_dir, plan.choice)
    os.environ.pop("OPENAVC_RESTARTING", None)


def _serve(sockets: list) -> None:
    """Serve the bound sockets until the server stops: HTTPS and the
    listeners beside it, plain HTTP beside the port-80 redirect, or plain
    HTTP alone."""
    if config.TLS_ENABLED:
        asyncio.run(_run_tls(sockets))
    elif config.PORT80_REDIRECT:
        asyncio.run(_run_http(sockets))
    else:
        _mark_if_port_80(config.HTTP_PORT)
        # Server.run picks uvicorn's event loop (uvloop where installed), as
        # uvicorn.run did before the sockets were bound here.
        uvicorn.Server(_main_http_config()).run(sockets=sockets)


def main():
    from openavc import listen, listen_sockets, open_files

    # Before anything opens a connection: a service manager's default soft
    # limit (1024 under systemd, 256 under launchd) caps connected devices.
    before, after = open_files.raise_open_file_limit() or (None, None)
    if after != before:
        log.info("Open files limit raised from %s to %s", before, after)

    # Who can connect, settled and resolved against the adapters present now
    # (openavc/listen.py), before anything binds.
    plan = listen.start()
    sockets, plan = _bind_primary_port(plan)
    try:
        _settle_listening(plan)
        status_folder.clear_startup_error()
        status_folder.write_server_address()
        listen_sockets.push_listeners.enable(config.HTTP_PORT)
        _serve(sockets)
    finally:
        listen_sockets.close_all(sockets)

if __name__ == "__main__":
    main()
