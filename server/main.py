"""
OpenAVC — main entry point.

Starts the FastAPI server with the runtime engine.

Usage:
    python -m server.main
"""

import os
import sys

# In frozen (PyInstaller) builds, this exe doubles as the simulator entry point.
# simulation.py launches: sys.executable --simulator --config <path>
if getattr(sys, 'frozen', False) and len(sys.argv) > 1 and sys.argv[1] == '--simulator':
    sys.argv = [sys.argv[0]] + sys.argv[2:]  # strip the --simulator flag
    from simulator.__main__ import main as _sim_main
    _sim_main()
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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from server import config, runtime_flags
from server.api import rest, ws, isc_ws, discovery as discovery_api, plugins as plugins_api, assets as assets_api, themes as themes_api, ai_proxy as ai_proxy_api, ir_learn_ws
from server.api.routes import network as network_routes
from server.api.routes import pair as pair_routes
from server.api.routes import root as root_routes
from server.api.routes import setup as setup_routes
from server.core.engine import Engine
from server.discovery.engine import DiscoveryEngine
from server.utils.logger import get_logger
from server.version import __version__

log = get_logger(__name__)

# Cap inbound WebSocket frames well below uvicorn's implicit 16 MiB default.
# The /ws and /isc/ws endpoints only ever receive small control/state JSON
# (panel UI events, device commands, ISC state sync); real project, asset, and
# driver uploads go over REST, not the socket. 1 MiB is orders of magnitude
# above any legitimate frame yet a fixed, explicit ceiling on the two
# unauthenticated/pre-auth socket paths (websockets rejects an oversized frame
# with a 1009 close before buffering it). Applied to every listener that
# serves server.main:app.
_WS_MAX_SIZE = 1024 * 1024

# Set log level from config
logging.getLogger().setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

# Seed starter projects on first run
from server.core.project_library import ensure_starter_projects
ensure_starter_projects()

# Create engine
engine = Engine(config.PROJECT_PATH)

# Create discovery engine
discovery_engine = DiscoveryEngine()


async def _initialize_engine(app: FastAPI) -> None:
    """Run engine initialization in the background so HTTP serves immediately."""
    try:
        # Warn if server is network-exposed without authentication
        if config.BIND_ADDRESS in ("0.0.0.0", "::") and not config.PROGRAMMER_PASSWORD and not config.API_KEY:
            log.warning("=" * 60)
            log.warning("  WARNING: Server is bound to %s with NO authentication.", config.BIND_ADDRESS)
            log.warning("  Anyone on the network can access and control devices.")
            log.warning("  Set OPENAVC_PROGRAMMER_PASSWORD or OPENAVC_API_KEY,")
            log.warning("  or bind to 127.0.0.1 (OPENAVC_BIND=127.0.0.1).")
            log.warning("=" * 60)

        # Check if automatic rollback is needed (failed update crash detection)
        from server.system_config import get_system_config
        from server.updater.rollback import check_rollback_needed
        data_dir = get_system_config().data_dir
        if check_rollback_needed(data_dir):
            from pathlib import Path
            from server.updater.rollback import perform_rollback, restore_pre_update_data
            # Restore user data from the pre-update backup BEFORE swapping the
            # code back: the rolled-back code may predate the running version's
            # project-format migrations, so the restore must run on this side.
            restore_pre_update_data(data_dir, project_path=Path(config.PROJECT_PATH))
            success = perform_rollback(data_dir)
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
        from server.updater.rollback import clear_stale_rollback_marker
        clear_stale_rollback_marker(data_dir)

        await engine.start()

        # Load driver hints into discovery engine after drivers are registered
        from server.core.device_manager import get_driver_registry
        discovery_engine.load_driver_hints_from_registry(get_driver_registry())

        # Phase 9.7: load any sibling _discovery.py companions that ship
        # alongside built-in or community drivers.
        from server.system_config import (
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
        # dev (`python -m server.main`), where there's no service manager,
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
        if local_ip and local_ip != "127.0.0.1":
            log.info(f"  LAN access:  {scheme}://{local_ip}:{port}/panel")
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
    app.state.engine_ready = False
    app.state.engine_error = None

    # Keep plugin tokens presented via the _plugin_token query param out of
    # uvicorn's access log. Done here (not at import) so uvicorn has already
    # configured its access logger and the filter lands on the live one.
    from server.api.plugin_ext import install_access_log_redaction
    install_access_log_redaction()

    log.info("=" * 60)
    log.info("  OpenAVC starting")
    log.info("=" * 60)

    # Run engine initialization in background so HTTP is available immediately
    init_task = asyncio.create_task(_initialize_engine(app))

    yield

    # Shutdown: wait for init to finish (or cancel if still running)
    if not init_task.done():
        init_task.cancel()
        try:
            await init_task
        except asyncio.CancelledError:
            pass
    if app.state.engine_ready:
        await engine.stop()
    log.info("OpenAVC stopped")


# Create FastAPI app
app = FastAPI(
    title="OpenAVC",
    description="Open-source AV room control platform",
    version=__version__,
    lifespan=lifespan,
)

# Wire engine into API modules. rest.set_engine() populates the single shared
# slot in server.api._engine; ws.py reads that same slot, so it isn't wired
# separately here.
rest.set_engine(engine)
plugins_api.set_engine(engine)
assets_api.set_engine(engine)
themes_api.set_engine(engine)
ai_proxy_api.set_engine(engine)

# Let plugins mount HTTP routers under /api/plugins/<id>/ext/* (authed) and
# /api/plugins/<id>/guest/* (open, plugin-gated) at runtime. The loader calls
# these when such a plugin starts/stops; the app lives here.
from server.api.plugin_ext import (
    mount_plugin_guest_router,
    mount_plugin_router,
    unmount_plugin_guest_router,
    unmount_plugin_router,
)
engine.plugin_loader.set_router_hooks(
    lambda plugin_id, router, panel_paths=None: mount_plugin_router(
        app, plugin_id, router, panel_paths
    ),
    lambda plugin_id: unmount_plugin_router(app, plugin_id),
    lambda plugin_id, router, alias=None: mount_plugin_guest_router(app, plugin_id, router, alias),
    lambda plugin_id: unmount_plugin_guest_router(app, plugin_id),
)

# Wire discovery engine
discovery_api.set_discovery_engine(discovery_engine)
discovery_api.set_broadcast_fn(engine.broadcast_ws)
discovery_api.set_app_engine(engine)

# Mount routers
app.include_router(rest.open_router)
app.include_router(rest.router)
app.include_router(ws.router)
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

# Per-IP rate limiting (outermost — runs before auth)
from server.middleware.rate_limit import RateLimitMiddleware
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
from server.system_config import WEB_PANEL_DIR, WEB_PROGRAMMER_DIR
if WEB_PANEL_DIR.exists():
    app.mount("/panel", StaticFiles(directory=str(WEB_PANEL_DIR), html=True), name="panel")


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
    app.mount(
        "/programmer",
        StaticFiles(directory=str(programmer_dir), html=True),
        name="programmer",
    )


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
    invocation is reproduced exactly (`python -m server.main`, the frozen
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
    from server.system_config import get_data_dir
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


def _write_startup_error(error_type: str, message: str) -> None:
    """Write a startup error file so the tray app (or other monitors) can report it."""
    from server.system_config import get_data_dir
    import json as _json
    try:
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        error_file = data_dir / "startup-error.json"
        error_file.write_text(_json.dumps({
            "error": error_type,
            "message": message,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }), encoding="utf-8")
    except OSError:
        pass


def _clear_startup_error() -> None:
    """Remove the startup error file on successful start."""
    from server.system_config import get_data_dir
    try:
        error_file = get_data_dir() / "startup-error.json"
        if error_file.exists():
            error_file.unlink()
    except OSError:
        pass


def _preflight_port(port: int, *, retries: int) -> "OSError | None":
    """Try to bind to (BIND_ADDRESS, port). Return None on success, last error otherwise.

    During cloud-driven restart the dying parent's listening socket can take a
    moment to free after `os._exit(0)`; the retry loop tolerates that brief
    window. Normal startup uses retries=1 to surface a clean "port in use"
    error immediately.
    """
    import socket as _sock
    import time as _time
    last_err: "OSError | None" = None
    for attempt in range(retries):
        test = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        # Mirror the listening socket uvicorn actually binds (asyncio sets
        # SO_REUSEADDR on POSIX). Without it the pre-flight is stricter than
        # the real bind and false-fails on a port still in TIME_WAIT from a
        # just-exited server — exactly the rapid-restart case (crash recovery,
        # in-place updates) the appliance supervisor relies on. SO_REUSEADDR
        # still refuses a port held by a live listener, so a genuine "another
        # copy is running" conflict is still caught.
        if os.name != "nt":
            test.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        try:
            test.bind((config.BIND_ADDRESS, port))
            test.close()
            return None
        except OSError as err:
            last_err = err
            test.close()
            if attempt < retries - 1:
                _time.sleep(0.5)
    return last_err


def _certified_host_for(host: str) -> str | None:
    """The certified cloud-cert hostname for an IPv4 host, or None.

    None when no cloud cert is active (or it has expired), the host isn't a
    plain IPv4 address, or the encoded name wouldn't be covered by the cert's
    SANs. Shared by the redirect listeners and the startup banner.
    """
    import datetime as _dt
    import ipaddress as _ipaddress

    from server import tls as _tls

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

    _BAD_HOST_CHARS = (" ", "/", "\\", "@", "<", ">", "\"", "'")

    async def _push_passthrough(request: Request) -> Response:
        """Serve inbound device push directly instead of redirecting.

        Devices delivering push callbacks (webhooks, GENA NOTIFY) speak
        plain HTTP, don't follow redirects, and won't trust the self-signed
        certificate a redirect would send them to — so the push registry is
        served in-process on this listener too (it's process-global; see
        server/transport/http_listener.py).
        """
        from server.transport import http_listener

        device_id = request.path_params["device_id"]
        label = request.path_params.get("label") or ""
        body = await request.body()
        status = await http_listener.dispatch(
            device_id,
            label,
            http_listener.HTTPPushRequest(
                body=body,
                method=request.method,
                headers={k.lower(): v for k, v in request.headers.items()},
                source_ip=request.client.host if request.client else "",
                label=label,
            ),
        )
        return Response(status_code=status)

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

    return Starlette(routes=[
        Route(
            "/api/push/{device_id}",
            _push_passthrough,
            methods=["POST", "NOTIFY"],
        ),
        Route(
            "/api/push/{device_id}/{label}",
            _push_passthrough,
            methods=["POST", "NOTIFY"],
        ),
        Route(
            "/{path:path}",
            _handler,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
    ])


def _make_aux_redirect_server(app, port: int):
    """Build a best-effort auxiliary redirect listener.

    Returns (server, None) ready to serve, or (None, err) when the port is
    already taken — auxiliary listeners never block startup.

    Only the main server installs signal handlers; otherwise the servers race
    to register signal.signal handlers and the last one overrides the rest.
    Auxiliary listeners cancel via the FIRST_COMPLETED logic in the runners
    when the main server shuts down.
    """
    import contextlib as _contextlib

    err = _preflight_port(port, retries=1)
    if err is not None:
        return None, err
    aux_config = uvicorn.Config(
        app,
        host=config.BIND_ADDRESS,
        port=port,
        log_level="warning",  # quiet: every redirect logs an info line otherwise
    )
    server = uvicorn.Server(aux_config)

    @_contextlib.contextmanager
    def _no_signals():
        yield

    server.capture_signals = _no_signals
    return server, None


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
    the stdlib default. The logic lives in ``server.tls`` (one home) so the
    cloud-issued cert's SNI-selected context gets the identical guarantee;
    applied to the HTTPS listener's context in `_run_tls`.
    """
    from server import tls as tls_module

    tls_module.harden_tls_context(context)


async def _run_tls() -> None:
    """Run the TLS listener (and optional HTTP redirect listener) concurrently.

    On TLS-config failure, writes a startup-error file and exits non-zero with
    the same shape as the existing port-in-use error so the tray app and other
    monitors can surface the cause.
    """
    from server import tls as tls_module
    from server.system_config import get_system_config

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

    main_config = uvicorn.Config(
        "server.main:app",
        host=config.BIND_ADDRESS,
        port=config.TLS_PORT,
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        reload=False,
        log_level="info",
        ws_max_size=_WS_MAX_SIZE,
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
    tasks: list[asyncio.Task] = [asyncio.create_task(main_server.serve())]
    if config.TLS_PORT == 80:
        runtime_flags.port80_active = True

    if config.TLS_REDIRECT_HTTP:
        redirect_server, redirect_err = _make_aux_redirect_server(
            _build_redirect_app(config.TLS_PORT), config.HTTP_PORT
        )
        if redirect_server is not None:
            tasks.append(asyncio.create_task(redirect_server.serve()))
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
        server80, err80 = _make_aux_redirect_server(
            _build_redirect_app(config.TLS_PORT), 80
        )
        if server80 is not None:
            tasks.append(asyncio.create_task(server80.serve()))
            runtime_flags.port80_active = True
        else:
            log.warning(
                "Port-80 redirect listener could not bind (%s); "
                "typed URLs still need the port.",
                err80,
            )

    await _serve_until_first_exit(tasks)


async def _run_http() -> None:
    """Run the plain-HTTP listener plus the port-80 convenience redirect.

    Only used when the port-80 redirect is enabled with HTTPS off — the
    single-listener path in __main__ stays untouched otherwise.
    """
    main_config = uvicorn.Config(
        "server.main:app",
        host=config.BIND_ADDRESS,
        port=config.HTTP_PORT,
        reload=False,
        log_level="info",
        ws_max_size=_WS_MAX_SIZE,
    )
    main_server = uvicorn.Server(main_config)
    tasks: list[asyncio.Task] = [asyncio.create_task(main_server.serve())]
    if config.HTTP_PORT == 80:
        runtime_flags.port80_active = True

    if config.HTTP_PORT != 80:
        server80, err80 = _make_aux_redirect_server(
            _build_redirect_app(config.HTTP_PORT, scheme="http"), 80
        )
        if server80 is not None:
            tasks.append(asyncio.create_task(server80.serve()))
            runtime_flags.port80_active = True
        else:
            log.warning(
                "Port-80 redirect listener could not bind (%s); "
                "typed URLs still need the port.",
                err80,
            )

    await _serve_until_first_exit(tasks)


def main():
    # The pre-flight port is whichever port we'll actually bind primary.
    # When TLS is on with redirect, the HTTP redirect listener is best-effort
    # and pre-flighted inside _run_tls (logs a warning, never blocks startup).
    _primary_port = config.TLS_PORT if config.TLS_ENABLED else config.HTTP_PORT
    _primary_label = "HTTPS port" if config.TLS_ENABLED else "HTTP port"
    _env_var_hint = "OPENAVC_TLS_PORT" if config.TLS_ENABLED else "OPENAVC_PORT"

    _retries = 20 if os.environ.get("OPENAVC_RESTARTING") == "1" else 1
    _err = _preflight_port(_primary_port, retries=_retries)
    if _err is not None:
        msg = (
            f"{_primary_label} {_primary_port} is already in use.\n"
            f"Another application (or another copy of OpenAVC) is using this port.\n\n"
            f"To fix this, change the port in Settings > System,\n"
            f"or set the {_env_var_hint} environment variable."
        )
        print(f"\n*** {msg} ***\n")
        _write_startup_error("port_in_use", msg)
        raise SystemExit(1)
    # Clear the restart marker so a future startup error isn't masked by
    # repeated retries.
    os.environ.pop("OPENAVC_RESTARTING", None)

    _clear_startup_error()

    if config.TLS_ENABLED:
        asyncio.run(_run_tls())
    elif config.PORT80_REDIRECT:
        asyncio.run(_run_http())
    else:
        if config.HTTP_PORT == 80:
            runtime_flags.port80_active = True
        uvicorn.run(
            "server.main:app",
            host=config.BIND_ADDRESS,
            port=config.HTTP_PORT,
            reload=False,
            log_level="info",
            ws_max_size=_WS_MAX_SIZE,
        )

if __name__ == "__main__":
    main()
