"""
REST API + WebSocket for the simulator.

Endpoints:
  GET  /api/status              — overall status
  GET  /api/available           — discovered simulators
  GET  /api/devices             — running simulator instances
  GET  /api/devices/{id}        — single device state
  POST /api/devices/{id}/start  — start simulating a device
  POST /api/devices/{id}/stop   — stop simulating a device
  POST /api/devices/{id}/state  — change device state (from UI)
  POST /api/devices/{id}/errors/{mode}  — inject/clear error
  GET  /api/devices/{id}/log    — protocol log
  GET  /api/network             — get network conditions
  POST /api/network             — set network conditions
  POST /api/network/preset      — apply a named preset
  WS   /ws                      — real-time state/protocol stream
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from simulator.engine import SimulatorManager
from simulator.network_conditions import NetworkConditions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ── Request/Response Models ──

class StartRequest(BaseModel):
    driver_id: str
    port: int = 0
    config: dict[str, Any] | None = None
    device_name: str = ""
    real_host: str = ""
    real_port: int = 0


class StateUpdate(BaseModel):
    key: str
    value: Any


class ErrorAction(BaseModel):
    active: bool = True


# ── Dependency: manager is set by server.py at startup ──

_manager: SimulatorManager | None = None
_ws_clients: list[WebSocket] = []
_shutdown_task: asyncio.Task | None = None


def set_manager(manager: SimulatorManager) -> None:
    global _manager
    _manager = manager
    manager.add_change_listener(_broadcast_change)


def _get_manager() -> SimulatorManager:
    if not _manager:
        raise HTTPException(503, "Simulator engine not initialized")
    return _manager


# ── Endpoints ──

@router.get("/status")
async def get_status():
    mgr = _get_manager()
    instances = mgr.list_instances()
    return {
        "running": True,
        "version": "0.1.0",
        "device_count": len(instances),
        "available_count": len(mgr.list_available()),
        "devices": [inst.to_info_dict() for inst in instances],
    }


@router.post("/shutdown")
async def shutdown():
    """Shut down the simulator process. Called from the simulator UI or OpenAVC."""
    import os
    import signal
    mgr = _get_manager()
    await mgr.stop_all()
    # Schedule process exit after responding
    asyncio.get_event_loop().call_later(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
    return {"status": "shutting_down"}


@router.get("/available")
async def get_available():
    mgr = _get_manager()
    available = mgr.list_available()
    return {
        "simulators": [
            {
                "driver_id": info.driver_id,
                "name": info.name,
                "category": info.category,
                "transport": info.transport,
                "default_port": info.default_port,
                "source": info.source,
            }
            for info in available.values()
        ]
    }


@router.get("/devices")
async def list_devices():
    mgr = _get_manager()
    return {
        "devices": [inst.to_info_dict() for inst in mgr.list_instances()]
    }


@router.get("/devices/{device_id}")
async def get_device(device_id: str):
    mgr = _get_manager()
    inst = mgr.get_instance(device_id)
    if not inst:
        raise HTTPException(404, f"Device '{device_id}' not found")
    return inst.to_info_dict()


@router.post("/devices/{device_id}/start")
async def start_device(device_id: str, req: StartRequest):
    mgr = _get_manager()
    try:
        sim = await mgr.start_device(
            driver_id=req.driver_id,
            device_id=device_id,
            port=req.port,
            config=req.config,
            device_name=req.device_name,
            real_host=req.real_host,
            real_port=req.real_port,
        )
        return sim.to_info_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Failed to start simulator for %s", device_id)
        raise HTTPException(500, str(e))


@router.post("/devices/{device_id}/stop")
async def stop_device(device_id: str):
    mgr = _get_manager()
    try:
        await mgr.stop_device(device_id)
        return {"status": "stopped", "device_id": device_id}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/devices/{device_id}/state")
async def update_state(device_id: str, req: StateUpdate):
    mgr = _get_manager()
    inst = mgr.get_instance(device_id)
    if not inst:
        raise HTTPException(404, f"Device '{device_id}' not found")
    inst.set_state(req.key, req.value)
    return {"status": "ok", "key": req.key, "value": req.value}


@router.post("/devices/{device_id}/errors/{mode}")
async def toggle_error(device_id: str, mode: str, req: ErrorAction):
    mgr = _get_manager()
    inst = mgr.get_instance(device_id)
    if not inst:
        raise HTTPException(404, f"Device '{device_id}' not found")
    if req.active:
        inst.inject_error(mode)
    else:
        inst.clear_error(mode)
    return {
        "status": "ok",
        "mode": mode,
        "active": mode in inst.active_errors,
    }


@router.get("/devices/{device_id}/log")
async def get_log(device_id: str, limit: int = 100):
    mgr = _get_manager()
    inst = mgr.get_instance(device_id)
    if not inst:
        raise HTTPException(404, f"Device '{device_id}' not found")
    return {"log": inst.get_protocol_log(limit)}


# ── Network Conditions ──

class NetworkUpdate(BaseModel):
    latency_ms: float = 0.0
    jitter_pct: float = 0.0
    drop_rate_pct: float = 0.0
    instability: str = "off"


class PresetRequest(BaseModel):
    preset: str


@router.get("/network")
async def get_network():
    mgr = _get_manager()
    return mgr.network.to_dict()


@router.post("/network")
async def set_network(req: NetworkUpdate):
    mgr = _get_manager()
    conditions = NetworkConditions(
        latency_ms=req.latency_ms,
        jitter_pct=req.jitter_pct,
        drop_rate_pct=req.drop_rate_pct,
        instability=req.instability,
    )
    mgr.network.set_global(conditions)
    return mgr.network.to_dict()


@router.post("/network/preset")
async def set_network_preset(req: PresetRequest):
    mgr = _get_manager()
    try:
        mgr.network.set_global_preset(req.preset)
        return mgr.network.to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── WebSocket ──

async def ws_endpoint(websocket: WebSocket):
    """WebSocket for real-time state updates and protocol log streaming."""
    global _shutdown_task
    await websocket.accept()
    _ws_clients.append(websocket)
    if _shutdown_task is not None:
        _shutdown_task.cancel()
        _shutdown_task = None
        logger.info("Client reconnected, cancelled auto-shutdown")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.remove(websocket)
        if not _ws_clients:
            logger.info("Last UI client disconnected, shutting down in 5s")
            _shutdown_task = asyncio.create_task(_delayed_shutdown())


async def _delayed_shutdown() -> None:
    """Wait a grace period, then shut down the simulator process."""
    import os
    import signal
    try:
        await asyncio.sleep(5)
        logger.info("No clients reconnected, shutting down simulator")
        if _manager:
            await _manager.stop_all()
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)
    except asyncio.CancelledError:
        pass


def _broadcast_change(change_type: str, data: dict) -> None:
    """Broadcast a change event to all WebSocket clients."""
    if not _ws_clients:
        return
    message = json.dumps({
        "type": change_type,
        "timestamp": time.time(),
        **data,
    })
    # Fire-and-forget broadcast
    for ws in list(_ws_clients):
        try:
            asyncio.ensure_future(ws.send_text(message))
        except Exception:
            pass
