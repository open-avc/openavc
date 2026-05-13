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
from server.api.routes import pair as pair_routes
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
                import os
                # Windows: exit 42 tells NSSM not to restart (installer handles it)
                # Linux: exit 0 triggers systemd restart, ExecStartPre applies rollback
                exit_code = 42 if sys.platform == "win32" else 0
                log.warning("Rollback initiated, exiting process (code %d)", exit_code)
                os._exit(exit_code)

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
        log.info("=" * 60)
        log.info(f"  Panel UI:    http://localhost:{config.HTTP_PORT}/panel")
        log.info(f"  Programmer:  http://localhost:{config.HTTP_PORT}/programmer")
        log.info(f"  REST API:    http://localhost:{config.HTTP_PORT}/api")
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
app.include_router(plugins_api.router)
app.include_router(assets_api.open_router)
app.include_router(assets_api.router)
app.include_router(themes_api.router)
app.include_router(ai_proxy_api.router)
app.include_router(pair_routes.router)

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
    """
    import subprocess
    try:
        cmd = list(getattr(sys, "orig_argv", None) or ([sys.executable] + sys.argv))
        env = {**os.environ, "OPENAVC_RESTARTING": "1"}
        if sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                cmd,
                env=env,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                cmd,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        log.info("Spawned replacement process for restart (no service manager detected)")
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


if __name__ == "__main__":
    import socket as _sock
    import time as _time

    # During cloud restart, the dying parent's listening socket can take a
    # moment to free after `os._exit(0)`. Retry for up to 10s so the new
    # process doesn't lose the race even when the parent's hard-exit
    # watchdog (7s) is what finally tears it down. For a normal startup
    # (no restart marker) we check once, preserving the immediate
    # "port in use" error.
    _retries = 20 if os.environ.get("OPENAVC_RESTARTING") == "1" else 1
    _bound = False
    _last_err: OSError | None = None
    for _attempt in range(_retries):
        _test = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        try:
            _test.bind((config.BIND_ADDRESS, config.HTTP_PORT))
            _bound = True
            _test.close()
            break
        except OSError as _err:
            _last_err = _err
            _test.close()
            if _attempt < _retries - 1:
                _time.sleep(0.5)
    if not _bound:
        msg = (
            f"Port {config.HTTP_PORT} is already in use.\n"
            f"Another application (or another copy of OpenAVC) is using this port.\n\n"
            f"To fix this, change the port in Settings > System,\n"
            f"or set the OPENAVC_PORT environment variable."
        )
        print(f"\n*** {msg} ***\n")
        _write_startup_error("port_in_use", msg)
        raise SystemExit(1)
    # Clear the restart marker so a future startup error isn't masked by
    # repeated retries.
    os.environ.pop("OPENAVC_RESTARTING", None)

    _clear_startup_error()

    uvicorn.run(
        "server.main:app",
        host=config.BIND_ADDRESS,
        port=config.HTTP_PORT,
        reload=False,
        log_level="info",
    )
