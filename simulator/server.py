"""
FastAPI application for the OpenAVC Simulator.

Serves:
  - REST API at /api/* (simulator control)
  - WebSocket at /ws (real-time updates)
  - Static UI at / (when built)
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from simulator import _runtime
from simulator.api import router as api_router, ws_endpoint, set_manager
from simulator.engine import SimulatorManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: discover drivers, start requested simulators."""
    config = _runtime.startup_config
    manager = SimulatorManager()

    # Discover available simulators
    driver_paths = config.get("driver_paths", [])
    if driver_paths:
        manager.discover(driver_paths)

    # Register manager with the API
    set_manager(manager)

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

    yield

    # Shutdown
    await manager.stop_all()
    logger.info("Simulator shut down")


app = FastAPI(
    title="OpenAVC Simulator",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for development (UI may be on different port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(api_router)

# WebSocket
app.add_websocket_route("/ws", ws_endpoint)

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
