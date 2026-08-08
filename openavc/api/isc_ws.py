"""
OpenAVC ISC WebSocket endpoint.

Accepts inbound WebSocket connections from peer OpenAVC instances.
The ISCManager handles authentication, message processing, and
connection lifecycle; this module only owns the FastAPI plumbing.
"""

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from openavc.utils.logger import get_logger

router = APIRouter()
log = get_logger(__name__)

# Per-peer rate limiting for ISC messages. The sliding windows are keyed by
# peer id and survive reconnects, so a peer can't reset its budget by cycling
# the connection.
_ISC_MAX_MESSAGES_PER_MINUTE = 300
_ISC_RATE_WINDOW = 60.0
_peer_msg_times: dict[str, list[float]] = {}


def _sweep_stale_windows(now: float) -> None:
    """Drop rate-limit entries for peers with no traffic inside the window.

    Peer ids are authenticated but still peer-chosen strings, so without a
    sweep the map would grow one entry per id ever seen.
    """
    stale = [
        peer_id for peer_id, times in _peer_msg_times.items()
        if not times or now - times[-1] >= _ISC_RATE_WINDOW
    ]
    for peer_id in stale:
        del _peer_msg_times[peer_id]

# ISCManager reference — set by main.py after engine starts
_isc_manager = None


def set_isc_manager(manager) -> None:
    """Wire the ISCManager (called by main.py / engine integration)."""
    global _isc_manager
    _isc_manager = manager


@router.websocket("/isc/ws")
async def isc_websocket_endpoint(ws: WebSocket) -> None:
    """Accept a peer ISC WebSocket connection."""
    await ws.accept()

    if _isc_manager is None:
        await ws.close(code=4000, reason="ISC not enabled")
        return

    # --- Read hello message ---
    try:
        text = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        hello = json.loads(text)
    except asyncio.TimeoutError:
        await ws.close(code=4001, reason="Hello timeout")
        return
    except (json.JSONDecodeError, WebSocketDisconnect):
        await ws.close(code=4001, reason="Invalid hello message")
        return

    if hello.get("type") != "isc.hello":
        await ws.close(code=4002, reason="Expected isc.hello")
        return

    # --- Authenticate and register ---
    peer_id = await _isc_manager.accept_inbound(ws, hello)
    if peer_id is None:
        # Rejected — accept_inbound already sent the reject message
        try:
            await ws.close()
        except Exception:
            pass  # Catch-all: socket may already be closed
        return

    # Everything after registration runs inside try/finally so any failure
    # (including between registration and the loop) still unregisters the
    # peer — a leaked entry would block a legitimate reconnection.
    conn = None
    try:
        # Capture the exact PeerConnection instance so peer_disconnected can
        # identity-check it: an orphan socket's late disconnect must not pop
        # a fresh reconnection that's taken its place (A55).
        conn = _isc_manager.get_connection(peer_id)

        _sweep_stale_windows(time.monotonic())

        # --- Message loop with rate limiting ---
        while True:
            text = await ws.receive_text()

            # Rate limit: sliding window, keyed by peer id (reconnect-proof)
            now = time.monotonic()
            times = _peer_msg_times.setdefault(peer_id, [])
            times[:] = [t for t in times if now - t < _ISC_RATE_WINDOW]
            if len(times) >= _ISC_MAX_MESSAGES_PER_MINUTE:
                log.warning("ISC: Rate limit exceeded for peer %s, dropping message", peer_id[:8])
                continue
            times.append(now)

            msg = json.loads(text)
            await _isc_manager.handle_message(peer_id, msg)
    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        log.debug(f"ISC: Malformed JSON from {peer_id[:8]}")
    except Exception:
        # Catch-all: any unexpected error ends the peer connection gracefully
        log.debug(f"ISC: Peer {peer_id[:8]} connection ended")
    finally:
        await _isc_manager.peer_disconnected(peer_id, conn=conn)
