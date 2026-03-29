"""
OpenAVC — main entry point.

Starts the FastAPI server with the runtime engine.

Usage:
    python -m server.main
"""

import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from server import config
from server.api import rest, ws, isc_ws, discovery as discovery_api, plugins as plugins_api, assets as assets_api, themes as themes_api, ai_proxy as ai_proxy_api
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    log.info("=" * 60)
    log.info("  OpenAVC starting")
    log.info("=" * 60)
    # Warn if server is network-exposed without authentication
    if config.BIND_ADDRESS in ("0.0.0.0", "::") and not config.PROGRAMMER_PASSWORD and not config.API_KEY:
        log.warning("=" * 60)
        log.warning("  WARNING: Server is bound to %s with NO authentication.", config.BIND_ADDRESS)
        log.warning("  Anyone on the network can access and control devices.")
        log.warning("  Set OPENAVC_PROGRAMMER_PASSWORD or OPENAVC_API_KEY,")
        log.warning("  or bind to 127.0.0.1 (OPENAVC_BIND=127.0.0.1).")
        log.warning("=" * 60)
    await engine.start()
    # Load driver hints into discovery engine after drivers are registered
    from server.core.device_manager import get_driver_registry
    discovery_engine.load_driver_hints_from_registry(get_driver_registry())
    log.info(f"  Panel UI:    http://localhost:{config.HTTP_PORT}/panel")
    log.info(f"  Programmer:  http://localhost:{config.HTTP_PORT}/programmer")
    log.info(f"  REST API:    http://localhost:{config.HTTP_PORT}/api")
    log.info("=" * 60)
    yield
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
discovery_api.set_broadcast_fn(engine._broadcast_ws)
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

# Serve Panel UI static files
panel_dir = Path(__file__).resolve().parent.parent / "web" / "panel"
if panel_dir.exists():
    app.mount("/panel", StaticFiles(directory=str(panel_dir), html=True), name="panel")

# Serve Programmer UI static files (Vite build output)
programmer_dir = Path(__file__).resolve().parent.parent / "web" / "programmer" / "dist"
if programmer_dir.exists():
    app.mount(
        "/programmer",
        StaticFiles(directory=str(programmer_dir), html=True),
        name="programmer",
    )


if __name__ == "__main__":
    uvicorn.run(
        "server.main:app",
        host=config.BIND_ADDRESS,
        port=config.HTTP_PORT,
        reload=False,
        log_level="info",
    )
