"""
OpenAVC — main entry point.

Starts the FastAPI server with the runtime engine.

Usage:
    python -m server.main
"""

import sys

# In frozen (PyInstaller) builds, this exe doubles as the simulator entry point.
# simulation.py launches: sys.executable --simulator --config <path>
if getattr(sys, 'frozen', False) and len(sys.argv) > 1 and sys.argv[1] == '--simulator':
    sys.argv = [sys.argv[0]] + sys.argv[2:]  # strip the --simulator flag
    from simulator.__main__ import main as _sim_main
    _sim_main()
    sys.exit(0)

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from server import config
from server.api import rest, ws, isc_ws, discovery as discovery_api, plugins as plugins_api, assets as assets_api, themes as themes_api, ai_proxy as ai_proxy_api
from server.api.routes import network as network_routes
from server.api.routes import pair as pair_routes
from server.api.routes import setup as setup_routes
from server.core.engine import Engine
from server.discovery.engine import DiscoveryEngine
from server.utils.logger import get_logger
from server.version import __version__

log = get_logger(__name__)

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
            from server.updater.rollback import perform_rollback
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
            log.warning("Cloud-driven restart requested (mode=%s); exiting in 2s", mode)
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

# Wire engine into API modules
rest.set_engine(engine)
ws.set_engine(engine)
plugins_api.set_engine(engine)
assets_api.set_engine(engine)
themes_api.set_engine(engine)
ai_proxy_api.set_engine(engine)

# Let plugins mount HTTP routers under /api/plugins/<id>/ext/* at runtime.
# The loader calls these when such a plugin starts/stops; the app lives here.
from server.api.plugin_ext import mount_plugin_router, unmount_plugin_router
engine.plugin_loader.set_router_hooks(
    lambda plugin_id, router: mount_plugin_router(app, plugin_id, router),
    lambda plugin_id: unmount_plugin_router(app, plugin_id),
)

# Wire discovery engine
discovery_api.set_discovery_engine(discovery_engine)
discovery_api.set_broadcast_fn(engine.broadcast_ws)
discovery_api.set_app_engine(engine)

# Mount routers
app.include_router(rest.open_router)
app.include_router(rest.router)
app.include_router(ws.router)
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
    exe, with `--simulator`, etc.). OPENAVC_RESTARTING tells the child to
    retry its port pre-flight briefly: the parent's `os._exit(0)` releases
    the listening socket essentially immediately, but on Windows there's a
    brief window where the new bind can race the old one.

    Child stdout/stderr are redirected to ``data_dir/logs/restart-child.log``
    so a silent failure on Windows (DETACHED_PROCESS = no console) leaves a
    trail. Path + parent PID are logged before spawning so the user can
    correlate the breadcrumb with the actual relaunch.
    """
    import subprocess
    from server.system_config import get_data_dir
    try:
        cmd = list(getattr(sys, "orig_argv", None) or ([sys.executable] + sys.argv))
        env = {**os.environ, "OPENAVC_RESTARTING": "1"}

        # Capture child output so DETACHED_PROCESS on Windows doesn't swallow
        # startup errors silently. Append (not truncate) — successive restarts
        # in one dev session leave readable history.
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
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            child = subprocess.Popen(
                cmd,
                env=env,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
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


def _build_redirect_app(tls_port: int):
    """Tiny Starlette app: catch-all that 302/307 redirects to https://...:tls_port.

    Uses temporary redirects (302/307) rather than 301/308 because TLS can be
    toggled off at runtime — a permanent redirect cached by the browser would
    keep forcing HTTPS even after HTTPS is disabled, leaving users locked out
    until they manually clear the browser cache. Cache-Control: no-store
    belt-and-suspenders prevents any caching at all.

    The Host header drives the redirect target hostname so external clients
    (phones, other servers on the LAN) get redirected back to themselves, not
    to "localhost". Pathological Host values fall back to the request URL
    hostname.
    """
    from starlette.applications import Starlette
    from starlette.responses import RedirectResponse
    from starlette.routing import Route

    _BAD_HOST_CHARS = (" ", "/", "\\", "@", "<", ">", "\"", "'")

    async def _handler(request: Request) -> RedirectResponse:
        host_header = request.headers.get("host", "")
        host = host_header.split(":", 1)[0] if host_header else ""
        if not host or any(c in host for c in _BAD_HOST_CHARS):
            host = request.url.hostname or "localhost"
        target = f"https://{host}:{tls_port}{request.url.path}"
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
            "/{path:path}",
            _handler,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        ),
    ])


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
    )
    main_server = uvicorn.Server(main_config)
    tasks: list[asyncio.Task] = [asyncio.create_task(main_server.serve())]

    if config.TLS_REDIRECT_HTTP:
        redirect_err = _preflight_port(config.HTTP_PORT, retries=1)
        if redirect_err is None:
            redirect_config = uvicorn.Config(
                _build_redirect_app(config.TLS_PORT),
                host=config.BIND_ADDRESS,
                port=config.HTTP_PORT,
                log_level="warning",  # quiet: every redirect logs an info line otherwise
            )
            redirect_server = uvicorn.Server(redirect_config)
            # Only the main server installs signal handlers; otherwise both
            # servers race to register signal.signal handlers and the second
            # one overrides the first. The redirect cancels via the
            # FIRST_COMPLETED logic below when main shuts down.
            import contextlib as _contextlib

            @_contextlib.contextmanager
            def _no_signals():
                yield

            redirect_server.capture_signals = _no_signals
            tasks.append(asyncio.create_task(redirect_server.serve()))
        else:
            log.warning(
                "HTTP redirect listener could not bind to port %d (%s); "
                "old http:// links will not auto-redirect.",
                config.HTTP_PORT,
                redirect_err,
            )

    # When either task finishes (graceful shutdown, error, signal), cancel the
    # other so we don't hang in asyncio.gather.
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        # Re-raise any exception the completed task ended with.
        task.result()


if __name__ == "__main__":
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
    else:
        uvicorn.run(
            "server.main:app",
            host=config.BIND_ADDRESS,
            port=config.HTTP_PORT,
            reload=False,
            log_level="info",
        )
