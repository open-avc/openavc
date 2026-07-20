"""
OpenAVC WebSocket handler.

Handles real-time bidirectional communication between the backend
and all connected UIs (panel and programmer).

Protocol: JSON messages with a "type" field.
"""

import asyncio
import json
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.api._engine import get_engine_optional
from server.api._engine import set_engine as _shared_set_engine
from server.api.auth import check_ws_auth, get_ws_auth_subprotocol
from server.api.error_messages import friendly_error
from server.utils.log_buffer import get_log_buffer, LogEntry
from server.utils.logger import get_logger

router = APIRouter()
log = get_logger(__name__)

# Cap on simultaneous WebSocket connections. Panel connections are
# unauthenticated, so without a ceiling a misbehaving or malicious client
# could exhaust server resources by opening connections in a loop. Generous
# for real deployments (one instance serves one space's panels plus the
# Programmer IDE).
MAX_WS_CONNECTIONS = 100
_ws_connection_count = 0

# Per-client rate limit: at most this many messages per sliding window.
WS_RATE_LIMIT_MAX_MESSAGES = 200
WS_RATE_LIMIT_WINDOW_SEC = 1.0

# Exceptions that mean the peer went away mid-send. These are expected and
# swallowed silently; anything else is a real send failure and gets logged.
_WS_DISCONNECT_EXCEPTIONS = (WebSocketDisconnect, ConnectionError, OSError)

# The engine lives in a single shared slot in server.api._engine. This handler
# reads it through get_engine_optional() rather than holding its own reference,
# so there's no second slot that could fall out of sync with the REST one.


def set_engine(engine) -> None:
    """Wire the engine into the single shared slot in server.api._engine.

    Kept as a module-level entry point so callers that wire ws.py directly
    (tests) still work, but it targets the one shared slot — not a ws-local
    copy — so the REST and WebSocket views can't desync.
    """
    _shared_set_engine(engine)


# Per-client log subscriptions: ws -> (sub_id, task)
_log_subscriptions: dict[int, tuple[str, asyncio.Task]] = {}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Main WebSocket endpoint for panel and programmer UIs."""
    global _ws_connection_count
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

    # Connection cap: reject before accepting the handshake. The counter is
    # incremented before any await so concurrent handshakes can't slip past
    # the check, and decremented in the outer finally on every exit path.
    if _ws_connection_count >= MAX_WS_CONNECTIONS:
        log.warning(
            f"WebSocket connection rejected: {MAX_WS_CONNECTIONS} connections already open"
        )
        await ws.close(code=1013, reason="Too many connections")
        return
    _ws_connection_count += 1
    try:
        await _run_ws_connection(ws, query_params, headers, client_type)
    finally:
        _ws_connection_count -= 1


async def _run_ws_connection(
    ws: WebSocket, query_params: dict, headers: dict, client_type: str
) -> None:
    """Accept the handshake and run one client's message loop until disconnect."""
    # Echo back auth subprotocol if client used Sec-WebSocket-Protocol
    subprotocol = get_ws_auth_subprotocol(headers)
    await ws.accept(subprotocol=subprotocol)

    engine = get_engine_optional()
    if engine is None:
        await ws.close(code=1011, reason="Engine not started")
        return

    ws_id = id(ws)
    ping_task = None

    async def _ping_loop() -> None:
        """Send heartbeat pings every 30 seconds."""
        try:
            while True:
                await asyncio.sleep(30)
                await ws.send_json({"type": "ping"})
        except asyncio.CancelledError:
            return
        except _WS_DISCONNECT_EXCEPTIONS:
            return  # Peer went away — the receive loop handles cleanup
        except Exception:
            log.debug("WebSocket ping loop ended on send failure", exc_info=True)

    try:
        namespaces_param = query_params.get("namespaces", "")
        ns_prefixes: tuple[str, ...] | None = None
        if namespaces_param:
            ns_prefixes = tuple(ns.strip() + "." for ns in namespaces_param.split(",") if ns.strip())

        # Register BEFORE taking the snapshot, with delivery deferred:
        # changes flushed while the snapshot is being built and sent buffer
        # into this client's queue instead of being silently missed (the old
        # snapshot-then-register order lost anything flushed in between).
        # Buffered updates may partially predate the snapshot; replaying
        # them after it is safe because state messages carry full per-key
        # values — the client converges on the latest.
        engine.add_ws_client(ws, ns_prefixes=ns_prefixes, defer_delivery=True)

        full_state = engine.state.snapshot()
        if ns_prefixes:
            state_snapshot = {k: v for k, v in full_state.items() if k.startswith(ns_prefixes)}
        else:
            state_snapshot = full_state
        await ws.send_json({
            "type": "state.snapshot",
            "state": state_snapshot,
        })

        # Send UI definition
        if engine.project:
            await ws.send_json({
                "type": "ui.definition",
                "ui": engine.project.ui.model_dump(mode="json"),
            })

        # Baseline is on the wire — release queued broadcasts
        engine.mark_ws_client_ready(ws)

        # Start heartbeat
        ping_task = asyncio.create_task(_ping_loop())

        # Per-client rate limiting: sliding window of accepted-message times.
        # Bounded by the check itself (never grows past the limit).
        _msg_times: deque[float] = deque()

        # Message loop
        while True:
            text = await ws.receive_text()

            # Rate limit check: prune timestamps that fell out of the window,
            # then reject if the window is at capacity. Rejected messages are
            # not counted, so a flood can't extend its own penalty.
            now = time.monotonic()
            while _msg_times and now - _msg_times[0] >= WS_RATE_LIMIT_WINDOW_SEC:
                _msg_times.popleft()
            if len(_msg_times) >= WS_RATE_LIMIT_MAX_MESSAGES:
                await ws.send_json({"type": "error", "message": "Rate limit exceeded"})
                continue
            _msg_times.append(now)

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
        engine.remove_ws_client(ws)


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
    except _WS_DISCONNECT_EXCEPTIONS:
        return  # Peer went away — subscription cleanup happens on disconnect
    except Exception:
        log.debug("WebSocket log stream ended on send failure", exc_info=True)


async def _send_ws(ws: WebSocket, msg: dict[str, Any]) -> None:
    """Send a JSON message to the client, silently ignoring disconnects."""
    try:
        await ws.send_json(msg)
    except _WS_DISCONNECT_EXCEPTIONS:
        pass  # Client disconnected mid-send
    except Exception:
        log.debug("WebSocket send failed", exc_info=True)


async def _send_ws_error(
    ws: WebSocket, source_type: str, message: str
) -> None:
    """Send an error response back to the client."""
    await _send_ws(ws, {
        "type": "error",
        "source_type": source_type,
        "message": message,
    })


def _is_flat_primitive(value: Any) -> bool:
    """Check that a value is a flat primitive (str, int, float, bool, None)."""
    return value is None or isinstance(value, (str, int, float, bool))


# Message types that panel clients are allowed to send.
# Panel can interact with UI elements, navigate pages, subscribe to logs,
# execute macros (presets), and set state (plugin iframes).
_PANEL_ALLOWED_TYPES = frozenset({
    "ui.press", "ui.release", "ui.hold", "ui.toggle_off", "ui.change",
    "ui.select", "ui.route", "ui.submit",
    "ui.page", "command", "macro.execute", "state.set",
    "log.subscribe", "log.unsubscribe", "pong",
})

# Key namespaces panel clients are allowed to write via state.set.
# Panel state.set exists for plugin iframes and panel-driven user variables.
# Panels are unauthenticated, so writes to device/system/isc/ui/trigger keys
# (which would let a panel defeat trigger cooldowns, fool skip_if_offline guards,
# or pollute ISC mesh state) are rejected. Programmer clients are unaffected.
_PANEL_STATE_SET_PREFIXES = ("var.", "plugin.")


async def _handle_message(
    ws: WebSocket, msg: dict[str, Any], client_type: str = "panel"
) -> None:
    """Dispatch a WebSocket message by type."""
    msg_type = msg.get("type", "")
    engine = get_engine_optional()

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
            await engine.handle_ui_event("press", element_id)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.press failed: {e}")
            await _send_ws_error(ws, msg_type, friendly_error(e))

    elif msg_type == "ui.release":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        try:
            await engine.handle_ui_event("release", element_id)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.release failed: {e}")
            await _send_ws_error(ws, msg_type, friendly_error(e))

    elif msg_type == "ui.hold":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        try:
            await engine.handle_ui_event("hold", element_id)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.hold failed: {e}")
            await _send_ws_error(ws, msg_type, friendly_error(e))

    elif msg_type == "ui.toggle_off":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        try:
            await engine.handle_ui_event("toggle_off", element_id)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.toggle_off failed: {e}")
            await _send_ws_error(ws, msg_type, friendly_error(e))

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
            await engine.handle_ui_event("change", element_id, {"value": value})
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.change failed: {e}")
            await _send_ws_error(ws, msg_type, friendly_error(e))

    elif msg_type == "ui.select":
        # List item selection — drives the list element's do.select action
        # binding (and its two-way show.value.write_back write in the engine).
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        value = msg.get("value")
        if not _is_flat_primitive(value):
            await _send_ws_error(ws, msg_type, "Value must be a flat primitive (str, int, float, bool, or null)")
            return
        try:
            await engine.handle_ui_event("select", element_id, {"value": value})
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.select failed: {e}")
            await _send_ws_error(ws, msg_type, friendly_error(e))

    elif msg_type == "ui.route":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        input_idx = msg.get("input")
        output_idx = msg.get("output")
        if not _is_flat_primitive(input_idx) or not _is_flat_primitive(output_idx):
            await _send_ws_error(ws, msg_type, "Input and output must be flat primitives")
            return
        audio_flag = bool(msg.get("audio"))
        mute_val = msg.get("mute")
        # Dispatch to one of four binding slots based on the message shape:
        #   - audio=true AND mute present -> audio_mute_route binding ($output, $mute)
        #   - mute present (bool)         -> mute_route binding ($output, $mute)
        #   - audio=true                  -> audio_route binding ($input, $output)
        #   - otherwise                   -> route binding ($input, $output)
        if mute_val is not None and audio_flag:
            event_type = "audio_mute_route"
            data = {"output": output_idx, "mute": bool(mute_val)}
        elif mute_val is not None:
            event_type = "mute_route"
            data = {"output": output_idx, "mute": bool(mute_val)}
        elif audio_flag:
            event_type = "audio_route"
            data = {"input": input_idx, "output": output_idx}
        else:
            event_type = "route"
            data = {"input": input_idx, "output": output_idx}
        try:
            await engine.handle_ui_event(event_type, element_id, data)
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.route failed: {e}")
            await _send_ws_error(ws, msg_type, friendly_error(e))

    elif msg_type == "ui.submit":
        element_id = msg.get("element_id", "")
        if not element_id:
            await _send_ws_error(ws, msg_type, "Missing element_id")
            return
        value = msg.get("value")
        if not _is_flat_primitive(value):
            await _send_ws_error(ws, msg_type, "Value must be a flat primitive (str, int, float, bool, or null)")
            return
        try:
            await engine.handle_ui_event("submit", element_id, {"value": value})
        except Exception as e:
            # Catch-all: UI events dispatch to scripts/macros/drivers which can raise anything
            log.error(f"ui.submit failed: {e}")
            await _send_ws_error(ws, msg_type, friendly_error(e))

    elif msg_type == "ui.page":
        page_id = msg.get("page_id", "")
        if not page_id:
            await _send_ws_error(ws, msg_type, "Missing page_id")
            return
        await engine.events.emit(f"ui.page.{page_id}")
        # Confirm navigation to sender only — each panel manages its own page
        await ws.send_json({"type": "ui.navigate", "page_id": page_id})

    elif msg_type == "command":
        device_id = msg.get("device_id", "")
        command = msg.get("command", "")
        if not device_id or not command:
            await _send_ws_error(ws, msg_type, "Missing device_id or command")
            return
        params = msg.get("params", {})
        try:
            await engine.devices.send_command(device_id, command, params)
            await _send_ws(ws, {
                "type": "command.ack",
                "device_id": device_id,
                "command": command,
                "success": True,
            })
        except Exception as e:
            # Catch-all: driver command handlers can raise arbitrary exceptions
            log.error(f"Command failed: {e}")
            device_name = engine.state.get(f"device.{device_id}.name") or device_id
            host = engine.state.get(f"device.{device_id}.host") or ""
            error_msg = friendly_error(e, device=device_name, host=host)
            await _send_ws(ws, {
                "type": "command.ack",
                "device_id": device_id,
                "command": command,
                "success": False,
                "error": error_msg,
            })

    elif msg_type == "state.set":
        key = msg.get("key", "")
        if not key:
            await _send_ws_error(ws, msg_type, "Missing key")
            return
        value = msg.get("value")
        if not _is_flat_primitive(value):
            await _send_ws_error(ws, msg_type, "Value must be a flat primitive (str, int, float, bool, or null)")
            return
        if client_type == "panel" and not key.startswith(_PANEL_STATE_SET_PREFIXES):
            allowed = ", ".join(p + "*" for p in _PANEL_STATE_SET_PREFIXES)
            await _send_ws_error(ws, msg_type, f"Panel clients can only set keys under: {allowed}")
            return
        try:
            engine.state.set(key, value, source="ws")
            await _send_ws(ws, {
                "type": "state.set.ack",
                "key": key,
                "success": True,
            })
        except Exception as e:
            # Catch-all: state listeners (scripts/triggers) can raise arbitrary exceptions
            log.error(f"state.set failed: {e}")
            await _send_ws(ws, {
                "type": "state.set.ack",
                "key": key,
                "success": False,
                "error": friendly_error(e),
            })

    elif msg_type == "macro.execute":
        macro_id = msg.get("macro_id", "")
        if not macro_id:
            await _send_ws_error(ws, msg_type, "Missing macro_id")
            return
        try:
            await engine.macros.execute(macro_id)
        except Exception as e:
            # Catch-all: macro steps run arbitrary actions (commands, scripts, state changes)
            log.error(f"macro.execute failed: {e}")
            await _send_ws_error(ws, msg_type, f"Macro failed: {friendly_error(e)}")

    elif msg_type == "project.reload":
        try:
            await engine.reload_project()
        except Exception as e:
            # Catch-all: reload involves file I/O, JSON parsing, driver init, plugin loading
            log.error(f"project.reload failed: {e}")
            await _send_ws_error(ws, msg_type, f"Reload failed: {friendly_error(e)}")

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
        if engine.isc:
            instance_id = msg.get("instance_id", "")
            event = msg.get("event", "")
            payload = msg.get("payload", {})
            if not instance_id or not event:
                await _send_ws_error(ws, msg_type, "Missing instance_id or event")
                return
            try:
                await engine.isc.send_to(instance_id, event, payload)
            except (ConnectionError, OSError) as e:
                log.warning(f"ISC send failed: {e}")
                await _send_ws_error(ws, msg_type, f"ISC send failed: {e}")

    elif msg_type == "isc.broadcast":
        # Broadcast event to all ISC peers
        if engine.isc:
            event = msg.get("event", "")
            payload = msg.get("payload", {})
            if not event:
                await _send_ws_error(ws, msg_type, "Missing event")
                return
            try:
                await engine.isc.broadcast(event, payload)
            except (ConnectionError, OSError) as e:
                log.warning(f"ISC broadcast failed: {e}")
                await _send_ws_error(ws, msg_type, f"ISC broadcast failed: {e}")

    else:
        log.debug(f"Unknown WS message type: {msg_type}")
