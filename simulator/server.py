"""
FastAPI application for the OpenAVC Simulator.

Serves:
  - REST API at /api/* (simulator control)
  - WebSocket at /ws (real-time updates)
  - Static UI at / (when built)
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import PlainTextResponse

from simulator import _runtime
from simulator.api import router as api_router, ws_endpoint, set_auto_shutdown, set_manager
from simulator.engine import SimulatorManager

logger = logging.getLogger(__name__)

# The simulator's control API is unauthenticated (a dev tool bound to loopback)
# and can shut the process down or mutate device state. Without a guard, a page
# the AV designer merely visits while the simulator runs could drive it with a
# cross-origin POST, or reach it via DNS rebinding. This guard blocks both while
# leaving all legitimate local access — same-origin, a loopback dev server like
# Vite, and `--host <LAN-IP>` — untouched.

# Loopback host names allowed for both the target (Host) and a browser Origin.
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}

# CORS is restricted to loopback dev origins so no cross-origin page can read
# an API response (the guard below stops it driving writes and web sockets).
_LOOPBACK_ORIGIN_RE = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


def _hostname(value: str | None) -> str | None:
    """Extract the hostname from a Host header or an Origin URL (port stripped)."""
    if not value:
        return None
    # Host headers have no scheme; prefix "//" so urlparse treats it as authority.
    return urlparse(value if "//" in value else "//" + value).hostname


def _host_allowed(hostname: str | None) -> bool:
    """A target Host is allowed if it is loopback or any IP literal.

    A DNS-rebinding attack must put its own domain in the Host header (the
    browser sends the site's name, not the rebound 127.0.0.1), so allowing IP
    literals while rejecting names blocks rebinding without the middleware
    needing to know which address the simulator was bound to.
    """
    if hostname is None:  # non-browser client omitting Host — not the threat
        return True
    name = hostname.lower()
    if name in _LOOPBACK_NAMES:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


class LoopbackGuardMiddleware:
    """Reject browser-driven cross-site / DNS-rebinding access to the dev API."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            host = _hostname(headers.get("host"))
            if not _host_allowed(host):
                await self._reject(scope, receive, send)
                return
            origin = headers.get("origin")
            if origin is not None:
                # A present Origin must be a loopback site or match the target
                # host (same-origin, e.g. a LAN-bound instance). "null" and any
                # cross-site origin are rejected.
                origin_host = _hostname(origin)
                if origin_host not in _LOOPBACK_NAMES and origin_host != host:
                    await self._reject(scope, receive, send)
                    return
        await self.app(scope, receive, send)

    async def _reject(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            response = PlainTextResponse(
                "Forbidden: the simulator API only accepts loopback requests.",
                status_code=403,
            )
            await response(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: discover drivers, start requested simulators."""
    config = _runtime.startup_config
    manager = SimulatorManager(port_range_start=config.get("device_port_base"))

    # Discover available simulators
    driver_paths = config.get("driver_paths", [])
    if driver_paths:
        manager.discover(driver_paths)

    # Register manager with the API
    set_manager(manager)
    set_auto_shutdown(config.get("auto_shutdown", True))

    # Start requested devices (from config file)
    for device in config.get("devices", []):
        try:
            await manager.start_device(
                driver_id=device["driver_id"],
                device_id=device["device_id"],
                device_name=device.get("device_name", ""),
                real_host=device.get("real_host", ""),
                real_port=device.get("real_port", 0),
                port=device.get("port", 0),
                config=device.get("config"),
                child_entities=device.get("child_entities"),
            )
        except Exception:
            logger.exception(
                "Failed to start simulator for %s (driver=%s)",
                device.get("device_id"),
                device.get("driver_id"),
            )

    instances = manager.list_instances()
    if instances:
        logger.info(
            "Simulator ready — %d device(s) running:",
            len(instances),
        )
        for inst in instances:
            logger.info(
                "  %s (%s) on port %d",
                inst.device_id, inst.driver_id, inst.port,
            )
    else:
        available = manager.list_available()
        logger.info(
            "Simulator ready — %d driver(s) available, no devices started. "
            "Use the API to start simulation.",
            len(available),
        )

    # Follow the launching server, if there is one. Only openavc passes a
    # parent_pid; started from a terminal there is nobody to follow.
    watch_task = None
    parent_pid = config.get("parent_pid")
    if parent_pid:
        from simulator.parent_watch import watch_parent

        def _request_shutdown() -> None:
            server = _runtime.uvicorn_server
            if server is not None:
                server.should_exit = True

        watch_task = asyncio.ensure_future(
            watch_parent(int(parent_pid), _request_shutdown),
        )

    yield

    # Shutdown
    if watch_task is not None and not watch_task.done():
        watch_task.cancel()
        with suppress(asyncio.CancelledError):
            await watch_task
    await manager.stop_all()
    logger.info("Simulator shut down")


app = FastAPI(
    title="OpenAVC Simulator",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for development (the UI dev server may be on a different loopback port).
# Restricted to loopback origins so no cross-origin page can read API responses.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_LOOPBACK_ORIGIN_RE,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reject cross-site / DNS-rebinding access to the unauthenticated control API.
app.add_middleware(LoopbackGuardMiddleware)

# API routes
app.include_router(api_router)

# WebSocket
app.add_api_websocket_route("/ws", ws_endpoint)

# Static UI (if built)
# In frozen (PyInstaller) builds, resources are inside sys._MEIPASS.
# Otherwise, resolve relative to the simulator package.
if getattr(sys, "frozen", False):
    _sim_base = Path(sys._MEIPASS)
else:
    _sim_base = Path(__file__).parent.parent
ui_dir = _sim_base / "web" / "simulator" / "dist"
if not ui_dir.exists():
    ui_dir = _sim_base / "web" / "dist"
if ui_dir.exists():
    app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
