"""Device simulation REST endpoints: status, start, stop.

Drives the simulator subprocess through ``openavc/core/simulation.py``, which
owns process lifecycle, port allocation, and the connection redirection that
points devices at their simulators instead of real hardware.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from openavc.api._engine import _get_engine

router = APIRouter()


@router.get("/simulation/status")
async def simulation_status() -> dict[str, Any]:
    """Get simulation status."""
    return _get_engine().simulation.status()


@router.post("/simulation/start")
async def simulation_start(request: Request) -> dict[str, Any]:
    """Start simulation for all devices (or specific device_ids).

    An empty or absent body simulates every device. A present body must be
    valid JSON; ``device_ids``, when supplied, must be a list of device-id
    strings. Malformed JSON is a 400 rather than a silent simulate-all, and a
    non-list ``device_ids`` is a 400 rather than an opaque 500.
    """
    import json
    device_ids = None
    raw = await request.body()
    if raw and raw.strip():
        try:
            body = json.loads(raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")
        if isinstance(body, dict) and body.get("device_ids") is not None:
            device_ids = body["device_ids"]
            if not isinstance(device_ids, list) or not all(
                isinstance(d, str) for d in device_ids
            ):
                raise HTTPException(
                    status_code=400,
                    detail="'device_ids' must be a list of device id strings.",
                )
    try:
        result = await _get_engine().simulation.start(device_ids)
        return result
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/simulation/stop")
async def simulation_stop() -> dict[str, str]:
    """Stop simulation and restore real device connections."""
    await _get_engine().simulation.stop()
    return {"status": "stopped"}
