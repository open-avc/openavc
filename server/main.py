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
import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

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

# Programmer static-file auth middleware (only active when password is set)
class ProgrammerAuthMiddleware(BaseHTTPMiddleware):
    """Checks HTTP Basic auth for /programmer paths."""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/programmer"):
            return await call_next(request)
        if not config.PROGRAMMER_PASSWORD:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        password = ""
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                _, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                password = ""
        # Always run constant-time comparison regardless of decode success
        if secrets.compare_digest(password, config.PROGRAMMER_PASSWORD):
            return await call_next(request)

        return Response(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="OpenAVC Programmer"'},
        )


app.add_middleware(ProgrammerAuthMiddleware)

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
    _test = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    try:
        _test.bind((config.BIND_ADDRESS, config.HTTP_PORT))
        _test.close()
    except OSError:
        _test.close()
        msg = (
            f"Port {config.HTTP_PORT} is already in use.\n"
            f"Another application (or another copy of OpenAVC) is using this port.\n\n"
            f"To fix this, change the port in Settings > System,\n"
            f"or set the OPENAVC_PORT environment variable."
        )
        print(f"\n*** {msg} ***\n")
        _write_startup_error("port_in_use", msg)
        raise SystemExit(1)

    _clear_startup_error()

    uvicorn.run(
        "server.main:app",
        host=config.BIND_ADDRESS,
        port=config.HTTP_PORT,
        reload=False,
        log_level="info",
    )
