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

# CA trust store for stdlib `ssl` in the frozen macOS app. A PyInstaller .app has
# no OpenSSL-readable CA bundle, and macOS keeps its roots in the Keychain rather
# than in files OpenSSL reads. So `ssl.create_default_context()`, used by the
# `websockets` client for the cloud agent and the remote-UI tunnel, fails every
# wss:// handshake with CERTIFICATE_VERIFY_FAILED. (httpx is unaffected because it
# trusts certifi directly, which is why pairing's REST call succeeds while the
# agent stays disconnected.) Point stdlib ssl at the bundled certifi store so the
# default context can verify cloud.openavc.com. Linux reads /etc/ssl/certs and
# Windows reads its system store, so this is scoped to frozen macOS. An explicit
# SSL_CERT_FILE already in the environment always wins.
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
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from openavc import config, runtime_flags
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


async def _initialize_engine(app: FastAPI) -> None:
    """Run engine initialization in the background so HTTP serves immediately."""
    try:
        # Who can connect: main() resolved it before binding, and a server
        # started some other way resolves it here. It feeds a warning and the
        # banner, so it never blocks startup.
        try:
            plan = config.listen_plan()
        except Exception:
            log.exception("Could not resolve who can connect")
            plan = None

        # Warn if server is network-exposed without authentication
        if (
            plan is not None and plan.reaches_network
            and not config.PROGRAMMER_PASSWORD and not config.API_KEY
        ):
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

        from openavc.system_config import get_system_config
        from openavc.updater.host import get_firmware_host
        firmware_host = get_firmware_host()
        if not firmware_host:
            # Check if automatic rollback is needed (failed update crash detection)
            from openavc.updater.rollback import check_rollback_needed
            data_dir = get_system_config().data_dir
            if check_rollback_needed(data_dir):
                from pathlib import Path
                from openavc.updater.rollback import perform_rollback, restore_pre_update_data
                # Restore user data from the pre-update backup BEFORE swapping the
                # code back: the rolled-back code may predate the running version's
                # project-format migrations, so the restore must run on this side.
                restore_pre_update_data(data_dir, project_path=Path(config.PROJECT_PATH))
                success = perform_rollback(data_dir, automatic=True)
                if success:
                    # Windows: exit 42 tells NSSM not to restart (installer handles it)
                    # Linux: exit 0 triggers systemd restart, ExecStartPre applies rollback
                    exit_code = 42 if sys.platform == "win32" else 0
                    log.warning("Rollback initiated, exiting process (code %d)", exit_code)
                    os._exit(exit_code)

            # Defense in depth: drop any stale Linux apply-update.json left behind by
            # a failed/aborted apply, so update-helper.sh (root, ExecStartPre) can't
            # re-consume it on an unrelated restart. On systemd the helper runs
            # before us, so this mainly covers non-systemd / crash-before-helper.
            stale_apply = data_dir / "apply-update.json"
            if stale_apply.exists():
                try:
                    stale_apply.unlink()
                    log.warning("Removed stale apply-update.json at startup")
                except OSError:
                    log.exception("Could not remove stale apply-update.json at startup")

            # Same defense for a leftover apply-rollback marker: one that survives
            # to this point was never consumed by the pre-start wrapper, and would
            # silently downgrade the install on a later unrelated restart.
            from openavc.updater.rollback import clear_stale_rollback_marker
            clear_stale_rollback_marker(data_dir)

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

        # Load driver hints into discovery engine after drivers are registered
        discovery_engine.load_driver_hints_from_registry(list_registered_drivers())

        # Phase 9.7: load any sibling _discovery.py companions that ship
        # alongside built-in or community drivers.
        from openavc.system_config import (
            DRIVER_DEFINITIONS_DIR,
            DRIVER_REPO_DIR,
        )
        discovery_engine.load_discovery_companions_from_dirs(
            [DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR],
        )

        # Wire cloud-driven restart: the cloud agent emits
        # system.restart_requested after rate-limiting and sending its
        # command_result. The on-host service manager (NSSM / systemd /
        # Docker restart policy) brings the process back up — except in
        # dev (`python -m openavc.main`), where there's no service manager,
        # so we spawn a replacement ourselves before exiting.
        async def _on_restart_requested(_event: str, data: dict) -> None:
            mode = (data or {}).get("mode", "graceful")
            # source distinguishes a local UI/API restart from a cloud-driven
            # one — the event fires from both, so a hardcoded label misled
            # anyone reading the log during a Settings-triggered restart.
            source = (data or {}).get("source", "unknown")
            log.warning(
                "Restart requested (source=%s, mode=%s); exiting in 2s", source, mode,
            )
            # Brief delay so the command_result WS frame and log line flush
            # before the process exits.
            await asyncio.sleep(0 if mode == "hard" else 2)

            # Hard watchdog: a daemon thread that calls os._exit(0)
            # unconditionally after 7s. Required because EventBus.emit nests
            # asyncio.gather() calls when handlers themselves emit events,
            # and cancelling that chain at shutdown can recurse past Python's
            # stack limit (RecursionError in _GatheringFuture.cancel). When
            # that happens, the cancellation itself can't complete, so
            # `await engine.stop()` hangs forever AND `asyncio.wait_for(...)`
            # would also hang (timeout fires but the coroutine can't be
            # cancelled). A separate thread doesn't need the event loop to
            # be healthy — it just exits the process. Service managers
            # (NSSM/systemd/Docker) then bring us back up.
            import threading
            threading.Timer(7.0, lambda: os._exit(0)).start()

            # Spawn the replacement BEFORE attempting graceful shutdown so a
            # dev session is guaranteed to come back up even if the watchdog
            # fires. The child waits for our port to free (OPENAVC_RESTARTING).
            # In service-managed deployments, the manager handles relaunch.
            if not _is_service_managed():
                _spawn_replacement()

            # Best-effort graceful shutdown. The wait_for timeout protects
            # against the common case (cancellable hangs); the threading
            # watchdog above protects against the recursion-in-cancel case.
            try:
                if app.state.engine_ready:
                    await asyncio.wait_for(engine.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                log.warning("Graceful shutdown exceeded 5s — forcing exit")
            except Exception:
                log.exception("Error during graceful shutdown before restart")
            os._exit(0)

        engine.events.on("system.restart_requested", _on_restart_requested)

        # Follow the chosen adapter: back, gone, or on a new DHCP address,
        # the server restarts through the same path and listens again where
        # the choice now points (openavc/listen.py).
        from openavc import listen

        from openavc import listen_safety_net

        async def _restart_to_follow_adapter(_reason: str) -> None:
            await engine.events.emit(
                "system.restart_requested", {"mode": "graceful", "source": "listen"}
            )

        # The same ten-second tick checks the safety net's deadline: a change
        # that cut off the browser making it goes back by itself unless the
        # Programmer is opened through the new choice (listen_safety_net.py).
        # It also re-reads the Reach devices through adapter's address, off
        # the event loop, so device traffic follows a DHCP renewal with no
        # restart (openavc/reach_devices.py).
        from openavc import reach_devices

        async def _safety_net_tick() -> None:
            try:
                if reach_devices.choice():
                    await asyncio.to_thread(reach_devices.refresh)
            except Exception:  # the safety net below must still tick
                log.debug("Reach devices through: refresh failed", exc_info=True)
            await listen_safety_net.tick(get_system_config(), _restart_to_follow_adapter)

        app.state.listen_watcher = asyncio.create_task(
            listen.ListenWatcher(_restart_to_follow_adapter, on_tick=_safety_net_tick).run()
        )

        app.state.engine_ready = True
        if config.TLS_ENABLED:
            scheme = "https"
            port = config.TLS_PORT
        else:
            scheme = "http"
            port = config.HTTP_PORT
        log.info("=" * 60)
        log.info(f"  Panel UI:    {scheme}://localhost:{port}/panel")
        log.info(f"  Programmer:  {scheme}://localhost:{port}/programmer")
        log.info(f"  REST API:    {scheme}://localhost:{port}/api")
        local_ip = str(engine.get_status().get("local_ip") or "")
        if plan is None:
            pass
        elif plan.problem is not None:
            # The chosen adapter is missing, so this listens on loopback
            # only. One sentence, the same one every surface shows.
            log.warning(f"  LAN access:  none. {plan.problem.message}")
        elif plan.local_only:
            # Every URL below this point is built from a LAN address, and the
            # kernel refuses all of them on a loopback-only listener. Saying
            # so is the only useful line here: this banner is the sole
            # instruction a manual/checkout run ever gets, and the Dashboard's
            # Panel Access card already tells the same truth from the other
            # side.
            log.info(
                "  LAN access:  none. Accepting connections from this computer "
                "only; change Settings > Network > Accept connections from and "
                "restart to reach it from the network."
            )
        elif local_ip and local_ip != "127.0.0.1":
            only = f"  ({plan.adapter_name} only)" if plan.mode == "adapter" else ""
            log.info(f"  LAN access:  {scheme}://{local_ip}:{port}/panel{only}")
            if runtime_flags.port80_active:
                log.info(f"  Short URL:   http://{local_ip}/panel")
            certified = _certified_host_for(local_ip) if config.TLS_ENABLED else None
            if certified:
                log.info(
                    f"  Trusted URL: https://{certified}:{config.TLS_PORT}/panel"
                    "  (no browser warnings)"
                )
        if config.TLS_ENABLED and config.TLS_REDIRECT_HTTP:
            log.info(
                f"  HTTP redirect: http://localhost:{config.HTTP_PORT} "
                f"-> https://localhost:{config.TLS_PORT}"
            )
        log.info("=" * 60)
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

# --- Startup splash page (served while engine initializes) ---

_STARTUP_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenAVC</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e; color: #fff;
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; overflow: hidden;
  }
  .container { text-align: center; }
  .logo {
    font-size: 2rem; font-weight: 700; letter-spacing: 0.02em;
    margin-bottom: 2rem; opacity: 0.95;
  }
  .logo span { color: #8AB493; }
  .spinner {
    width: 36px; height: 36px; margin: 0 auto 1.5rem;
    border: 3px solid rgba(255,255,255,0.1);
    border-top-color: #8AB493;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .message { font-size: 1rem; opacity: 0.6; }
  .error {
    color: #F44336; opacity: 1; margin-top: 1rem;
    max-width: 500px; word-break: break-word;
  }
</style>
</head>
<body>
<div class="container">
  <div class="logo">Open<span>AVC</span></div>
  <div class="spinner" id="spinner"></div>
  <div class="message" id="message">Starting up...</div>
</div>
<script>
(function() {
  var msg = document.getElementById('message');
  var spinner = document.getElementById('spinner');
  function check() {
    fetch('/api/startup-status').then(function(r) { return r.json(); }).then(function(d) {
      if (d.ready) {
        location.reload();
      } else if (d.error) {
        spinner.style.display = 'none';
        msg.textContent = 'Startup failed';
        var err = document.createElement('div');
        err.className = 'error';
        err.textContent = d.error;
        msg.parentNode.appendChild(err);
      } else {
        setTimeout(check, 500);
      }
    }).catch(function() {
      setTimeout(check, 1000);
    });
  }
  setTimeout(check, 500);
})();
</script>
</body>
</html>
"""


class StartupSplashMiddleware(BaseHTTPMiddleware):
    """Serves a loading page for UI routes while the engine is initializing."""

    async def dispatch(self, request: Request, call_next):
        if getattr(request.app.state, "engine_ready", True):
            return await call_next(request)

        path = request.url.path

        # Startup status endpoint — always available
        if path == "/api/startup-status":
            return JSONResponse({
                "ready": False,
                "error": request.app.state.engine_error,
            })

        # Block API routes during startup (engine.project is None, etc.)
        if path.startswith("/api/"):
            return JSONResponse(
                {"detail": "Server is starting up"},
                status_code=503,
            )

        # Serve splash page for all UI routes
        return HTMLResponse(_STARTUP_PAGE, status_code=503)


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


@app.middleware("http")
async def _panel_no_cache(request, call_next):
    """Force browsers to revalidate /panel assets via ETag (no stale-cache trap).

    Without this, the panel iframe inside the Theme Studio happily serves
    cached panel.css / panel.js for hours, masking deployed fixes. ETag
    revalidation keeps cache cheap (304 on no-change) without ever serving
    truly stale content.
    """
    response = await call_next(request)
    if request.url.path.startswith("/panel"):
        response.headers["Cache-Control"] = "no-cache"
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


def _is_service_managed() -> bool:
    """True when a host service manager (NSSM / systemd / Docker) will relaunch us.

    When True, a plain `os._exit(0)` is enough to "restart" — the manager
    spawns a replacement. When False (typical dev session), we must spawn
    a replacement ourselves before exiting or the user is left with a
    dead server.

    Detection signals:
      OPENAVC_SERVICE_MANAGED=1  — set by the installer/service unit (canonical)
      INVOCATION_ID env var      — set by systemd
      PID 1 or /.dockerenv       — running inside a Docker container
    """
    if os.environ.get("OPENAVC_SERVICE_MANAGED") == "1":
        return True
    if os.environ.get("INVOCATION_ID"):
        return True
    try:
        if os.getpid() == 1:
            return True
    except OSError:
        pass
    if os.path.exists("/.dockerenv"):
        return True
    return False


def _spawn_replacement() -> None:
    """Launch a fresh copy of this process, detached from the current one.

    Used during cloud-driven restart in dev sessions where no service
    manager will relaunch us. The child uses `sys.orig_argv` so the original
    invocation is reproduced exactly (`python -m openavc.main`, the frozen
    exe, with `--simulator`, etc.) — except argv[0], which is replaced with
    `sys.executable` on non-frozen runs. On macOS framework builds (Homebrew)
    the interpreter re-execs itself through Python.app at startup, so
    orig_argv[0] is the bare framework binary with no trace of the venv
    (the __PYVENV_LAUNCHER__ handoff var is stripped from the environment
    too) — a child spawned from it can't import anything installed in the
    venv. sys.executable still points at the venv interpreter, and on
    Windows/Linux the two agree, so preferring it is a no-op there.
    OPENAVC_RESTARTING tells the child to
    retry its port pre-flight briefly: the parent's `os._exit(0)` releases
    the listening socket essentially immediately, but on Windows there's a
    brief window where the new bind can race the old one.

    Child stdout/stderr are redirected to ``data_dir/logs/restart-child.log``
    so a silent failure on Windows (the child has no console window) leaves a
    trail. Path + parent PID are logged before spawning so the user can
    correlate the breadcrumb with the actual relaunch.

    Windows child gets CREATE_NO_WINDOW — a hidden console — NOT
    DETACHED_PROCESS (no console at all). The difference matters: console
    children of the replacement (ping during a discovery scan, ssh, pip)
    attach to whatever console their parent has. With a hidden console they
    stay hidden; with no console, Windows gives each one its own visible
    console window — a discovery scan then pops one window per address.
    """
    import subprocess
    from openavc.system_config import get_data_dir
    try:
        cmd = list(getattr(sys, "orig_argv", None) or ([sys.executable] + sys.argv))
        # Relaunch through the interpreter that's actually running us, not
        # whatever argv[0] claims. On macOS the framework build re-execs
        # through Python.app, leaving an orig_argv[0] that knows nothing of
        # the venv — the replacement would die on its first import. Frozen
        # exes keep their argv untouched (sys.executable IS the bundle exe).
        if cmd and sys.executable and not getattr(sys, "frozen", False):
            cmd[0] = sys.executable
        env = {**os.environ, "OPENAVC_RESTARTING": "1"}

        # Capture child output so the console-less child on Windows doesn't
        # swallow startup errors silently. Append (not truncate) — successive
        # restarts in one dev session leave readable history.
        try:
            log_dir = get_data_dir() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            child_log_path = log_dir / "restart-child.log"
            child_log = open(child_log_path, "a", buffering=1, encoding="utf-8", errors="replace")
            child_log.write(
                f"\n--- spawning replacement (parent pid={os.getpid()}, "
                f"argv={cmd}) at {__import__('datetime').datetime.now().isoformat()} ---\n"
            )
            child_log.flush()
        except OSError as exc:
            log.warning("Could not open restart-child log: %s; child output will be lost", exc)
            child_log = subprocess.DEVNULL
            child_log_path = None

        if sys.platform == "win32":
            child = subprocess.Popen(
                cmd,
                env=env,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=child_log,
                stderr=subprocess.STDOUT,
            )
        else:
            child = subprocess.Popen(
                cmd,
                env=env,
                start_new_session=True,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=child_log,
                stderr=subprocess.STDOUT,
            )
        if child_log_path is not None:
            log.info(
                "Spawned replacement process pid=%s; child output -> %s",
                child.pid,
                child_log_path,
            )
        else:
            log.info("Spawned replacement process pid=%s", child.pid)
    except Exception:
        log.exception("Failed to spawn replacement process; exiting without restart")


# The data folder's status/ subfolder holds what the Windows tray app and the
# macOS menu bar app read. Both run as the signed-in user. On Windows the
# installer leaves this one folder readable (and only readable) by every local
# account, while the rest of the data folder belongs to the service and
# administrators (installer/secure-data-dir.bat); on macOS the server runs as
# root, so server.json is written readable by everyone (STATUS_FILE_MODE).
# Nothing secret may be written here. installer/tray.py and
# installer/menubar.py name the same folder and files.
STATUS_DIR_NAME = "status"
STARTUP_ERROR_FILE_NAME = "startup-error.json"
SERVER_ADDRESS_FILE_NAME = "server.json"
STATUS_FILE_MODE = 0o644


def _status_dir():
    from openavc.system_config import get_data_dir
    return get_data_dir() / STATUS_DIR_NAME


def _write_startup_error(error_type: str, message: str) -> None:
    """Write a startup error file so the tray app (or other monitors) can report it."""
    import json as _json
    try:
        status_dir = _status_dir()
        status_dir.mkdir(parents=True, exist_ok=True)
        error_file = status_dir / STARTUP_ERROR_FILE_NAME
        error_file.write_text(_json.dumps({
            "error": error_type,
            "message": message,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }), encoding="utf-8")
    except OSError:
        pass


def _clear_startup_error() -> None:
    """Remove the startup error file on successful start."""
    try:
        error_file = _status_dir() / STARTUP_ERROR_FILE_NAME
        if error_file.exists():
            error_file.unlink()
    except OSError:
        pass


def _write_server_address() -> None:
    """Record where this server listens, for the tray app and the menu bar.

    The tray cannot read system.json, and it cannot ask the server without
    already knowing the port. These are the values the server binds,
    environment overrides included, rewritten at every start once the
    sockets are bound, so a port or a listen choice changed in Settings
    reaches the tray with the restart that applies it. ``listen`` carries the
    addresses listened on and, when the chosen adapter is missing, the
    sentence that says so.
    """
    import json as _json
    from openavc.utils.fileio import atomic_write_text
    plan = config.listen_plan()
    try:
        status_dir = _status_dir()
        status_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(status_dir / SERVER_ADDRESS_FILE_NAME, _json.dumps({
            "http_port": config.HTTP_PORT,
            "tls_enabled": config.TLS_ENABLED,
            "tls_port": config.TLS_PORT,
            "listen": {
                "mode": plan.mode,
                "addresses": list(plan.hosts),
                "problem": plan.problem.message if plan.problem else None,
            },
        }), mode=STATUS_FILE_MODE)
    except OSError:
        pass


def _certified_host_for(host: str) -> str | None:
    """The certified cloud-cert hostname for an IPv4 host, or None.

    None when no cloud cert is active (or it has expired), the host isn't a
    plain IPv4 address, or the encoded name wouldn't be covered by the cert's
    SANs. Shared by the redirect listeners and the startup banner.
    """
    import datetime as _dt
    import ipaddress as _ipaddress

    from openavc import tls as _tls

    state = _tls.cloud_cert_holder().get()
    if state is None:
        return None
    if state.expires_at <= _dt.datetime.now(_dt.timezone.utc):
        return None
    try:
        ip = _ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.version != 4:
        return None
    name = f"{str(ip).replace('.', '-')}.{state.hostname_suffix}"
    return name if state.matches(name) else None


# How long the probe page waits for the certified origin before falling back
# to the bare-IP HTTPS URL. LAN round-trips answer in milliseconds; the
# timeout only matters when the name is blackholed (rebind-protecting router)
# rather than failing fast (NXDOMAIN, no internet).
_PROBE_TIMEOUT_MS = 2500

# Whether the certified hostname resolves is only observable from the
# client's own device — its resolver, its router's rebind protection, its
# venue's internet — so the server can never pick the right redirect target
# on the client's behalf. This page probes the certified origin from the
# browser and picks the first target that actually works. Fully inline (no
# external assets: the client may be about to discover it has no working
# DNS). The probe endpoint is GET /api/health: open (no auth) and on the
# rate limiter's open tier. mode "no-cors" makes any completed response —
# opaque, any status — count as reachable; only network-level failure
# (resolution, TCP, TLS) or the timeout rejects. location.replace() keeps
# this page out of browser history so Back never lands on it.
_PROBE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Connecting&hellip;</title>
<style>
:root { color-scheme: light dark; }
body { font: 16px/1.5 system-ui, -apple-system, sans-serif; margin: 0;
       min-height: 100vh; display: grid; place-items: center; }
main { text-align: center; padding: 2rem; opacity: 0;
       animation: reveal 0.3s ease 0.4s forwards; }
@keyframes reveal { to { opacity: 1; } }
a { color: inherit; }
</style>
</head>
<body>
<main>
<p>Connecting&hellip;</p>
<noscript>
<p><a href="__CERT_URL__">Continue with a secure connection</a></p>
<p><a href="__BARE_URL__">Use the direct address</a> (your browser may warn
about the certificate)</p>
</noscript>
</main>
<script>
(function () {
  "use strict";
  var certOrigin = __CERT_ORIGIN_JS__;
  var bareOrigin = __BARE_ORIGIN_JS__;
  var suffix = location.pathname + location.search;
  function go(url) { location.replace(url); }
  if (!window.fetch || !window.AbortController) {
    go(certOrigin + suffix);
    return;
  }
  var ctrl = new AbortController();
  var timer = setTimeout(function () { ctrl.abort(); }, __TIMEOUT_MS__);
  fetch(certOrigin + "/api/health",
        { mode: "no-cors", cache: "no-store", signal: ctrl.signal })
    .then(function () { clearTimeout(timer); go(certOrigin + suffix); })
    .catch(function () { clearTimeout(timer); go(bareOrigin + suffix); });
})();
</script>
</body>
</html>
"""


def _probe_page_response(
    certified_host: str, bare_host: str, target_port: int, request: Request
) -> HTMLResponse:
    """The smart-redirect probe page for a browser navigation.

    Embeds both candidate targets: the certified origin (probed first, green
    lock) and the bare-IP HTTPS origin (fallback, self-signed interstitial —
    a working page instead of a dead browser error). The script rebuilds
    path + query from its own location; the noscript links carry them
    HTML-escaped (the path is request-controlled input).
    """
    import html as _html
    import json as _json

    suffix = request.url.path
    if request.url.query:
        suffix += f"?{request.url.query}"
    cert_origin = f"https://{certified_host}:{target_port}"
    bare_origin = f"https://{bare_host}:{target_port}"
    page = (
        _PROBE_PAGE
        .replace("__CERT_URL__", _html.escape(cert_origin + suffix, quote=True))
        .replace("__BARE_URL__", _html.escape(bare_origin + suffix, quote=True))
        .replace("__CERT_ORIGIN_JS__", _json.dumps(cert_origin))
        .replace("__BARE_ORIGIN_JS__", _json.dumps(bare_origin))
        .replace("__TIMEOUT_MS__", str(_PROBE_TIMEOUT_MS))
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


def _build_redirect_app(target_port: int, scheme: str = "https"):
    """Tiny Starlette app: catch-all that 302/307 redirects to {scheme}://...:target_port.

    Serves two listeners: the HTTP port when HTTPS is on (scheme "https"),
    and the optional port-80 convenience listener (scheme "https" when HTTPS
    is on, "http" otherwise) so typed URLs can drop the port entirely.

    Uses temporary redirects (302/307) rather than 301/308 because TLS can be
    toggled off at runtime — a permanent redirect cached by the browser would
    keep forcing HTTPS even after HTTPS is disabled, leaving users locked out
    until they manually clear the browser cache. Cache-Control: no-store
    belt-and-suspenders prevents any caching at all (including of the
    certified-hostname target below, which must not outlive the cert).

    The Host header drives the redirect target hostname so external clients
    (phones, other servers on the LAN) get redirected back to themselves, not
    to "localhost". Pathological Host values fall back to the request URL
    hostname.

    When a cloud-issued trusted certificate is active and the client
    addressed us by plain IPv4, the target hostname becomes the certified
    name instead — the IP dash-encoded under the cert's wildcard
    (192.168.1.20 -> 192-168-1-20.<label>.<zone>) — so the client lands on
    HTTPS with no browser warning. The Host header is the reachability
    proof: the client just reached that IP over HTTP, so the encoded name
    resolves to an address known-good for this client. Everything else
    (real hostnames, IPv6, no/expired cloud cert) keeps the bare-host
    target and today's self-signed interstitial behavior.

    Browser navigations (GET with text/html in Accept) don't get a blind
    302 to the certified name: whether that name resolves is only knowable
    on the client (its resolver, rebind-protecting router, venue internet),
    and a browser that can't resolve it shows a dead error page with no way
    back. They get the probe page instead, which tries the certified origin
    and falls back to bare-IP HTTPS. Non-browser clients (no text/html) and
    non-GET methods keep the plain 302/307; with no active cloud cert every
    response is byte-identical to the pre-probe behavior.
    """
    from starlette.applications import Starlette
    from starlette.responses import RedirectResponse, Response
    from starlette.routing import Route

    from openavc.transport import http_listener

    _BAD_HOST_CHARS = (" ", "/", "\\", "@", "<", ">", "\"", "'")

    async def _handler(request: Request) -> Response:
        host_header = request.headers.get("host", "")
        if host_header.startswith("["):
            # Bracketed IPv6 ("[::1]:8080") — splitting on ":" would mangle
            # it; keep the brackets, URLs need them around IPv6 hosts.
            end = host_header.find("]")
            host = host_header[: end + 1] if end != -1 else ""
        else:
            host = host_header.split(":", 1)[0] if host_header else ""
        if not host or any(c in host for c in _BAD_HOST_CHARS):
            host = request.url.hostname or "localhost"
        if scheme == "https":
            # Certified names only make sense on the HTTPS target — on plain
            # HTTP there is no certificate to match.
            certified = _certified_host_for(host)
            if certified is not None:
                if request.method == "GET" and "text/html" in request.headers.get(
                    "accept", ""
                ):
                    return _probe_page_response(
                        certified, host, target_port, request
                    )
                host = certified
        target = f"{scheme}://{host}:{target_port}{request.url.path}"
        if request.url.query:
            target += f"?{request.url.query}"
        status = 302 if request.method in ("GET", "HEAD") else 307
        return RedirectResponse(
            target,
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )

    # Device push is served here rather than redirected: see
    # http_listener.push_routes.
    return Starlette(routes=[
        *http_listener.push_routes(),
        Route(
            "/{path:path}",
            _handler,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
    ])


def _build_http_listener_app(main_app, redirect_app):
    """The HTTP port while HTTPS is on: serve this machine, redirect the rest.

    The redirect exists to move *network* clients onto TLS. A client on this
    same machine has nothing to gain from it and quite a lot to lose, because
    the certificate is self-signed by default:

    - The appliance panel's own screen is a WebView on 127.0.0.1. Redirected
      to HTTPS it hits a certificate it does not trust, and its live-state
      WebSocket has no error hook to answer at all — so the panel draws and
      then sits there offline. Its supervisor is on the same loopback.
    - On any deployment, HTTPS with a broken certificate otherwise leaves no
      way back in except editing system.json by hand. Here the box itself
      always has one.

    Nothing is exposed by this. The socket peer really is loopback (a header
    cannot fake it), so anything reaching this branch is already on the
    machine, where it could read the certificate and the config off disk
    anyway. The weaker ``scope_peer_is_loopback`` is deliberately the right
    question: ``is_local_console_request`` subtracts tunneled traffic, which
    matters when loopback GRANTS something, and this grants nothing — the
    tunnel connects straight to the TLS port when HTTPS is on
    (``cloud/tunnel.py`` ``_loopback_origin``), so it never arrives here.

    Lifespan is answered by the redirect app, never forwarded: the main app's
    lifespan starts the engine, and a second listener running it would start
    everything twice.
    """
    from openavc.utils.request_origin import scope_peer_is_loopback

    async def listener(scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope_peer_is_loopback(scope):
            await main_app(scope, receive, send)
            return
        await redirect_app(scope, receive, send)

    return listener


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


async def _run_tls(sockets: list) -> None:
    """Run the TLS listener (and optional HTTP redirect listener) concurrently.

    ``sockets`` are the HTTPS port's, already bound by ``main()``. On
    TLS-config failure, writes a startup-error file and exits non-zero with
    the same shape as the port-in-use error so the tray app and other
    monitors can surface the cause.
    """
    from openavc import listen_sockets
    from openavc import tls as tls_module
    from openavc.system_config import get_system_config

    try:
        cert_path, key_path = tls_module.load_or_generate(
            config, get_system_config().data_dir
        )
    except tls_module.TLSConfigError as exc:
        msg = (
            "HTTPS is enabled but the TLS listener cannot start:\n"
            f"  {exc.reason}\n\n"
            "Fix the certificate configuration in Settings > Security,\n"
            "or disable HTTPS (Settings > Security, or OPENAVC_TLS_ENABLED=false)."
        )
        print(f"\n*** {msg} ***\n", file=sys.stderr)
        _write_startup_error("tls_error", msg)
        raise SystemExit(1) from exc
    listen_sockets.push_listeners.use_tls(str(cert_path), str(key_path))

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
    # then reuses it rather than rebuilding) and attach the SNI callback:
    # names on the cloud-issued cert get that cert, everything else —
    # including all bare-IP HTTPS, which sends no SNI — keeps the self-signed
    # leaf. The callback is installed even with no cloud cert on disk (empty
    # holder = no-op) so a first enrollment while running takes effect
    # without a restart.
    main_config.load()
    if main_config.ssl is not None:
        _harden_tls_context(main_config.ssl)
        if config.TLS_CLOUD_CERT:
            tls_module.load_cloud_cert(get_system_config().data_dir)
        main_config.ssl.sni_callback = tls_module.make_sni_callback(
            tls_module.cloud_cert_holder()
        )
    main_server = uvicorn.Server(main_config)
    tasks: list[asyncio.Task] = [asyncio.create_task(main_server.serve(sockets=sockets))]
    if config.TLS_PORT == 80:
        runtime_flags.port80_active = True

    if config.TLS_REDIRECT_HTTP:
        redirect_serve, redirect_err = _make_aux_redirect_server(
            _build_http_listener_app(app, _build_redirect_app(config.TLS_PORT)),
            config.HTTP_PORT,
        )
        if redirect_serve is not None:
            tasks.append(asyncio.create_task(redirect_serve))
            if config.HTTP_PORT == 80:
                runtime_flags.port80_active = True
        else:
            log.warning(
                "HTTP redirect listener could not bind to port %d (%s); "
                "old http:// links will not auto-redirect.",
                config.HTTP_PORT,
                redirect_err,
            )

    # The port-80 listener lets typed URLs drop the port entirely. Skip when
    # a listener above already owns 80.
    if (
        config.PORT80_REDIRECT
        and config.TLS_PORT != 80
        and not (config.TLS_REDIRECT_HTTP and config.HTTP_PORT == 80)
    ):
        serve80, err80 = _make_aux_redirect_server(
            _build_redirect_app(config.TLS_PORT), 80
        )
        if serve80 is not None:
            tasks.append(asyncio.create_task(serve80))
            runtime_flags.port80_active = True
        else:
            log.warning(
                "Port-80 redirect listener could not bind (%s); "
                "typed URLs still need the port.",
                err80,
            )

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
    if config.HTTP_PORT == 80:
        runtime_flags.port80_active = True

    if config.HTTP_PORT != 80:
        serve80, err80 = _make_aux_redirect_server(
            _build_redirect_app(config.HTTP_PORT, scheme="http"), 80
        )
        if serve80 is not None:
            tasks.append(asyncio.create_task(serve80))
            runtime_flags.port80_active = True
        else:
            log.warning(
                "Port-80 redirect listener could not bind (%s); "
                "typed URLs still need the port.",
                err80,
            )

    await _serve_until_first_exit(tasks)


def main():
    from openavc import listen, listen_sockets

    # Who can connect, settled and resolved against the adapters present now
    # (openavc/listen.py), before anything binds.
    plan = listen.start()

    # The main listener's port. When TLS is on with redirect, the HTTP
    # redirect listener is best-effort and bound inside _run_tls (logs a
    # warning, never blocks startup).
    _primary_port = config.TLS_PORT if config.TLS_ENABLED else config.HTTP_PORT
    _primary_label = "HTTPS port" if config.TLS_ENABLED else "HTTP port"
    _env_var_hint = "OPENAVC_TLS_PORT" if config.TLS_ENABLED else "OPENAVC_PORT"

    # During a restart the previous process can hold the port for a moment
    # after it exits; the bind retries through that window.
    _retries = 20 if os.environ.get("OPENAVC_RESTARTING") == "1" else 1
    try:
        inherited = os.environ.get("OPENAVC_LISTEN_FD")
        if inherited is not None:
            sockets = listen_sockets.inherited_for_plan(inherited, plan, _primary_port)
        else:
            sockets, plan = listen_sockets.bind_for_plan(plan, _primary_port, retries=_retries)
    except listen_sockets.ListenBindError as exc:
        msg = exc.message(_primary_label, _env_var_hint)
        print(f"\n*** {msg} ***\n")
        _write_startup_error(exc.error_type, msg)
        raise SystemExit(1)
    try:
        listen.set_current(plan)
        if plan.problem is not None:
            log.warning(plan.problem.message)
        # A change that cut off the browser making it starts its clock now, when
        # the server is listening on it (openavc/listen_safety_net.py).
        from openavc import listen_safety_net
        from openavc.system_config import get_system_config

        listen_safety_net.note_listening(get_system_config().data_dir, plan.choice)
        # Clear the restart marker so a future startup error isn't masked by
        # repeated retries.
        os.environ.pop("OPENAVC_RESTARTING", None)

        _clear_startup_error()
        _write_server_address()
        listen_sockets.push_listeners.enable(config.HTTP_PORT)

        if config.TLS_ENABLED:
            asyncio.run(_run_tls(sockets))
        elif config.PORT80_REDIRECT:
            asyncio.run(_run_http(sockets))
        else:
            if config.HTTP_PORT == 80:
                runtime_flags.port80_active = True
            # Server.run picks uvicorn's event loop (uvloop where installed), as
            # uvicorn.run did before the sockets were bound here.
            uvicorn.Server(_main_http_config()).run(sockets=sockets)
    finally:
        listen_sockets.close_all(sockets)

if __name__ == "__main__":
    main()
