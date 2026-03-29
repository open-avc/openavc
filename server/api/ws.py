"""
OpenAVC WebSocket handler.

Handles real-time bidirectional communication between the backend
and all connected UIs (panel and programmer).

Protocol: JSON messages with a "type" field.
"""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.api.auth import check_ws_auth, get_ws_auth_subprotocol
from server.utils.log_buffer import get_log_buffer, LogEntry
from server.utils.logger import get_logger

router = APIRouter()
log = get_logger(__name__)

# Engine reference, set by main.py
_engine = None

# Per-client log subscriptions: ws -> (sub_id, task)
_log_subscriptions: dict[int, tuple[str, asyncio.Task]] = {}


def set_engine(engine) -> None:
    global _engine
    _engine = engine


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Main WebSocket endpoint for panel and programmer UIs."""
    query_params = dict(ws.query_params)
    headers = dict(ws.headers)
    requested_type = query_params.get("client", "panel")

    # Only allow known client types
    if requested_type not in ("panel", "programmer"):
        await ws.close(code=4003, reason="Unknown client type")
        return

    # Derive client type from authentication level, not client declaration
    is_authenticated = check_ws_auth(query_params, headers)
    if requested_type == "programmer" and not is_authenticated:
        await ws.close(code=4001, reason="Authentication required")
        return
    # Panel clients cannot escalate to programmer even if they have auth
    client_type = requested_type if is_authenticated else "panel"

    # Echo back auth subprotocol if client used Sec-WebSocket-Protocol
    subprotocol = get_ws_auth_subprotocol(headers)
    await ws.accept(subprotocol=subprotocol)

    if _engine is None:
        await ws.close(code=1011, reason="Engine not started")
        return

    _engine.add_ws_client(ws)
    ws_id = id(ws)
    ping_task = None

    async def _ping_loop() -> None:
        """Send heartbeat pings every 30 seconds."""
        try:
            while True:
                await asyncio.sleep(30)
                await ws.send_json({"type": "ping"})
        except (asyncio.CancelledError, Exception):
            return  # Catch-all: ping loop must exit silently on any error

    try:
        # Send initial state snapshot (optionally filtered by namespace prefixes)
        namespaces_param = query_params.get("namespaces", "")
        full_state = _engine.state.snapshot()
        if namespaces_param:
            prefixes = tuple(ns.strip() + "." for ns in namespaces_param.split(",") if ns.strip())
            state_snapshot = {k: v for k, v in full_state.items() if k.startswith(prefixes)}
        else:
            state_snapshot = full_state
        await ws.send_json({
            "type": "state.snapshot",
            "state": state_snapshot,
        })

        # Send UI definition
        if _engine.project:
            await ws.send_json({
                "type": "ui.definition",
                "ui": _engine.project.ui.model_dump(mode="json"),
            })

        # Start heartbeat
        ping_task = asyncio.create_task(_ping_loop())

        # Message loop
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
                await _handle_message(ws, msg, client_type)
            except json.JSONDecodeError:
                log.warning(f"Invalid JSON from WebSocket: {text[:100]}")
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
            except Exception as exc:
                # Catch-all: isolates arbitrary message-handler errors from the connection loop
                log.exception("Error handling WebSocket message")
                try:
                    await ws.send_json({"type": "error", "message": f"Server error: {type(exc).__name__}"})
                except Exception:
                    pass  # Catch-all: client may have disconnected during error reply

    except WebSocketDisconnect:
        pass
    except Exception:
        # Catch-all: isolates unexpected WebSocket errors so cleanup always runs
        log.exception("WebSocket error")
    finally:
        if ping_task and not ping_task.done():
            ping_task.cancel()
        _cleanup_log_subscription(ws_id)
        _engine.remove_ws_client(ws)


def _cleanup_log_subscription(ws_id: int) -> None:
    """Cancel and remove a log subscription for a disconnected client."""
    sub = _log_subscriptions.pop(ws_id, None)
    if sub:
        sub_id, task = sub
        task.cancel()
        get_log_buffer().unsubscribe(sub_id)


async def _log_stream_task(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Background task: read log entries from queue and push to WebSocket."""
    try:
        while True:
            entry: LogEntry = await queue.get()
            await ws.send_json({
                "type": "log.entry",
                **entry.to_dict(),
            })
    except asyncio.CancelledError:
        return
    except Exception:
        return  # Catch-all: WS send failure ends the stream task silently


async def _send_ws_error(
    ws: WebSocket, source_type: str, message: str
) -> None:
    """Send an error response back to the client."""
    try:
        await ws.send_json({
            "type": "error",
            "source_type": source_type,
            "message": message,
        })
    except Exception:
        pass  # Catch-all: client may have disconnected


def _is_flat_primitive(value: Any) -> bool:
    """Check that a value is a flat primitive (str, int, float, bool, None)."""
    return value is None or isinstance(value, (str, int, float, bool))


# Message types that panel clients are allowed to send.
# Panel can interact with UI elements, navigate pages, and subscribe to logs.
# It cannot directly set state, execute macros, reload projects, or use ISC.
_PANEL_ALLOWED_TYPES = frozenset({
    "ui.press", "ui.release", "ui.hold", "ui.toggle_off", "ui.change",
    "ui.route", "ui.submit",
    "ui.page", "command", "log.subscribe", "log.unsubscribe", "pong",
})


async def _handle_message(
    ws: WebSocket, msg: dict[str, Any], client_type: str = "panel"
) -> None:
    """Dispatch a WebSocket message by type."""
    msg_type = msg.get("type", "")

    # Restrict panel clients to UI interactions only
    if client_type == "panel" and msg_type not in _PANEL_ALLOWED_TYPES:
        await _send_ws_error(ws, msg_type, "Panel clients cannot send this message type")
        return

    if msg_type == "ui.press":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        try:
            await _engine.handle_ui_event("press", element_id)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.press failed: {e}")
            await _send_ws_error(ws, msg_type, str(e))

    elif msg_type == "ui.release":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        try:
            await _engine.handle_ui_event("release", element_id)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.release failed: {e}")
            await _send_ws_error(ws, msg_type, str(e))

    elif msg_type == "ui.hold":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        try:
            await _engine.handle_ui_event("hold", element_id)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.hold failed: {e}")
            await _send_ws_error(ws, msg_type, str(e))

    elif msg_type == "ui.toggle_off":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        try:
            await _engine.handle_ui_event("toggle_off", element_id)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.toggle_off failed: {e}")
            await _send_ws_error(ws, msg_type, str(e))

    elif msg_type == "ui.change":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        value = msg.get("value")
        if not _is_flat_primitive(value):
            await _send_ws_error(ws, msg_type, "Value must be a flat primitive (str, int, float, bool, or null)")
            return
        try:
            await _engine.handle_ui_event("change", element_id, {"value": value})
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.change failed: {e}")
            await _send_ws_error(ws, msg_type, str(e))

    elif msg_type == "ui.route":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        input_idx = msg.get("input")
        output_idx = msg.get("output")
        try:
            await _engine.handle_ui_event("route", element_id, {
                "input": input_idx, "output": output_idx,
            })
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.route failed: {e}")
            await _send_ws_error(ws, msg_type, str(e))

    elif msg_type == "ui.submit":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        value = msg.get("value")
        try:
            await _engine.handle_ui_event("submit", element_id, {"value": value})
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.submit failed: {e}")
            await _send_ws_error(ws, msg_type, str(e))

    elif msg_type == "ui.page":
        page_id = msg.get("page_id", "")
        if not page_id:
            await _send_ws_error(ws, msg_type, "Missing page_id")
            return
        await _engine.events.emit(f"ui.page.{page_id}")
        # Broadcast page navigation to all connected clients (including sender)
        await _engine._broadcast_ws({"type": "ui.navigate", "page_id": page_id})

    elif msg_type == "command":
        device_id = msg.get("device_id", "")
        command = msg.get("command", "")
        if not device_id or not command:
            await _send_ws_error(ws, msg_type, "Missing device_id or command")
            return
        params = msg.get("params", {})
        try:
            await _engine.devices.send_command(device_id, command, params)
        except Exception as e:
            # Catch-all: driver command handlers can raise arbitrary exceptions
            log.error(f"Command failed: {e}")
            await _send_ws_error(ws, msg_type, f"Command failed: {e}")

    elif msg_type == "state.set":
        key = msg.get("key", "")
        if not key:
            await _send_ws_error(ws, msg_type, "Missing key")
            return
        value = msg.get("value")
        if not _is_flat_primitive(value):
            await _send_ws_error(ws, msg_type, "Value must be a flat primitive (str, int, float, bool, or null)")
            return
        try:
            _engine.state.set(key, value, source="ws")
        except Exception as e:
            # Catch-all: state listeners (scripts/triggers) can raise arbitrary exceptions
            log.error(f"state.set failed: {e}")
            await _send_ws_error(ws, msg_type, str(e))

    elif msg_type == "macro.execute":
        macro_id = msg.get("macro_id", "")
        if not macro_id:
            await _send_ws_error(ws, msg_type, "Missing macro_id")
            return
        try:
            await _engine.macros.execute(macro_id)
        except Exception as e:
            # Catch-all: macro steps run arbitrary actions (commands, scripts, state changes)
            log.error(f"macro.execute failed: {e}")
            await _send_ws_error(ws, msg_type, f"Macro failed: {e}")

    elif msg_type == "project.reload":
        try:
            await _engine.reload_project()
        except Exception as e:
            # Catch-all: reload involves file I/O, JSON parsing, driver init, plugin loading
            log.error(f"project.reload failed: {e}")
            await _send_ws_error(ws, msg_type, f"Reload failed: {e}")

    elif msg_type == "log.subscribe":
        ws_id = id(ws)
        # Only one subscription per client
        _cleanup_log_subscription(ws_id)
        buf = get_log_buffer()
        # Send recent history first
        await ws.send_json({
            "type": "log.history",
            "entries": buf.get_recent(100),
        })
        # Start streaming
        sub_id, queue = buf.subscribe()
        task = asyncio.create_task(_log_stream_task(ws, queue))
        _log_subscriptions[ws_id] = (sub_id, task)

    elif msg_type == "log.unsubscribe":
        _cleanup_log_subscription(id(ws))

    elif msg_type == "pong":
        pass  # Heartbeat response — no action needed

    elif msg_type == "isc.send":
        # Send event to a specific ISC peer
        if _engine.isc:
            instance_id = msg.get("instance_id", "")
            event = msg.get("event", "")
            payload = msg.get("payload", {})
            if not instance_id or not event:
                await _send_ws_error(ws, msg_type, "Missing instance_id or event")
                return
            try:
                await _engine.isc.send_to(instance_id, event, payload)
            except (ConnectionError, OSError) as e:
                log.warning(f"ISC send failed: {e}")
                await _send_ws_error(ws, msg_type, f"ISC send failed: {e}")

    elif msg_type == "isc.broadcast":
        # Broadcast event to all ISC peers
        if _engine.isc:
            event = msg.get("event", "")
            payload = msg.get("payload", {})
            if not event:
                await _send_ws_error(ws, msg_type, "Missing event")
                return
            try:
                await _engine.isc.broadcast(event, payload)
            except (ConnectionError, OSError) as e:
                log.warning(f"ISC broadcast failed: {e}")
                await _send_ws_error(ws, msg_type, f"ISC broadcast failed: {e}")

    else:
        log.debug(f"Unknown WS message type: {msg_type}")
