"""IR learn WebSocket channel.

A vendor-neutral learning flow: the client opens a socket to a bridge that can
learn (an IR bridge with a learner), the platform drives the bridge's generic
``bridge_learn_*`` capability, and each captured code streams back as Pronto hex
ready to save into an IR device's code-set.

The platform never speaks a bridge's wire format here — it calls
``bridge_learn_start`` / ``bridge_learn_poll`` / ``bridge_learn_stop`` and the
bridge driver does the translation (and, for the disabled-by-any-command
constraint, opens a dedicated socket and pauses its own poll for the session).

Two capture modes (query param ``mode``):
  * ``one_off``  — return the first captured code, then stop (default).
  * ``auto``     — keep the learner open; stream every button press until the
                   client sends ``{"action": "stop"}`` or disconnects.

One learn session per bridge (a second connect is refused). The socket requires
programmer auth — learning drives hardware.

Client <- server messages (JSON ``type``):
  learn.started    {mode}
  learn.captured   {pronto}
  learn.heartbeat  {}                 (keeps a quiet auto session alive)
  learn.error      {code, message}
  learn.stopped    {reason}

Client -> server:
  {"action": "stop"}
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.api._engine import get_engine_optional
from server.api.auth import check_ws_auth, get_ws_auth_subprotocol
from server.utils.logger import get_logger

router = APIRouter()
log = get_logger(__name__)

# Bridge ids with an active learn session — one session per bridge at a time.
_active_learn: set[str] = set()

# How long each learner poll waits before the loop wakes to re-check for a stop
# request; also the heartbeat cadence granularity.
_POLL_TIMEOUT = 1.0
# Send a heartbeat after this many idle polls so a quiet auto session doesn't
# look dead to the client / any idle-timeout proxy in between.
_IDLE_POLLS_PER_HEARTBEAT = 15


@router.websocket("/api/devices/{bridge_id}/ir-learn")
async def ir_learn_endpoint(bridge_id: str, ws: WebSocket) -> None:
    """Drive a learn session on ``bridge_id`` and stream captured codes."""
    query_params = dict(ws.query_params)
    headers = dict(ws.headers)

    # Learning drives hardware — require programmer auth, same gate as the
    # Programmer WebSocket.
    if not check_ws_auth(query_params, headers):
        await ws.close(code=4001, reason="Authentication required")
        return

    subprotocol = get_ws_auth_subprotocol(headers)
    await ws.accept(subprotocol=subprotocol)

    engine = get_engine_optional()
    if engine is None:
        await _send(ws, {"type": "learn.error", "code": "engine_not_started",
                         "message": "Engine not started"})
        await ws.close(code=1011)
        return

    mode = query_params.get("mode", "one_off")
    if mode not in ("one_off", "auto"):
        mode = "one_off"

    bridge = engine.devices.get_driver(bridge_id)
    if bridge is None or not getattr(bridge, "is_bridge", False):
        await _send(ws, {"type": "learn.error", "code": "not_a_bridge",
                         "message": f"'{bridge_id}' is not an available bridge"})
        await ws.close(code=4004)
        return
    if not getattr(bridge, "can_learn", False):
        await _send(ws, {"type": "learn.error", "code": "cannot_learn",
                         "message": "This bridge cannot learn IR codes"})
        await ws.close(code=4004)
        return
    if not getattr(bridge, "_connected", False):
        await _send(ws, {"type": "learn.error", "code": "bridge_offline",
                         "message": "The bridge is offline"})
        await ws.close(code=4004)
        return

    if bridge_id in _active_learn:
        await _send(ws, {"type": "learn.error", "code": "already_learning",
                         "message": "A learn session is already running for this bridge"})
        await ws.close(code=4004)
        return
    _active_learn.add(bridge_id)

    stop = asyncio.Event()
    reader: asyncio.Task | None = None
    started = False
    reason = "complete"
    try:
        await bridge.bridge_learn_start()
        started = True
        await _send(ws, {"type": "learn.started", "mode": mode})

        # Read client control messages (a "stop" action, or a disconnect)
        # concurrently with the capture loop.
        reader = asyncio.create_task(_read_stop(ws, stop))

        idle = 0
        while not stop.is_set():
            try:
                pronto = await bridge.bridge_learn_poll(_POLL_TIMEOUT)
            except Exception as exc:
                log.warning("[%s] IR learn poll failed: %s", bridge_id, exc)
                await _send(ws, {"type": "learn.error", "code": "learn_failed",
                                 "message": str(exc)})
                reason = "error"
                break

            if pronto:
                idle = 0
                await _send(ws, {"type": "learn.captured", "pronto": pronto})
                if mode == "one_off":
                    reason = "complete"
                    break
            else:
                idle += 1
                if idle % _IDLE_POLLS_PER_HEARTBEAT == 0:
                    await _send(ws, {"type": "learn.heartbeat"})
        else:
            reason = "stopped"
    except WebSocketDisconnect:
        reason = "disconnected"
    except Exception:
        log.exception("[%s] IR learn session error", bridge_id)
        reason = "error"
    finally:
        if reader is not None and not reader.done():
            reader.cancel()
        if started:
            try:
                await bridge.bridge_learn_stop()
            except Exception:
                log.debug("[%s] bridge_learn_stop failed", bridge_id, exc_info=True)
        _active_learn.discard(bridge_id)
        await _send(ws, {"type": "learn.stopped", "reason": reason})
        try:
            await ws.close()
        except Exception:
            pass


async def _read_stop(ws: WebSocket, stop: asyncio.Event) -> None:
    """Set ``stop`` when the client asks to stop or the socket closes."""
    try:
        while not stop.is_set():
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("action") == "stop":
                stop.set()
                return
    except (WebSocketDisconnect, RuntimeError, ValueError):
        # Disconnect, or a non-JSON / closed-socket read — end the session.
        stop.set()


async def _send(ws: WebSocket, msg: dict) -> None:
    """Best-effort JSON send; a closed socket must not crash the handler."""
    try:
        await ws.send_json(msg)
    except Exception:
        pass
