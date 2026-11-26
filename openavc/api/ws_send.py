"""Sending one WebSocket client a frame: the helpers every door on the socket
shares (``ws.py``'s message handler and the macro doors in ``ws_macros.py``).

THE producer of the ``error`` frame is here (``error_frame``): one shape,
whatever refused what, so a field cannot be gained at one site and lost at
another. ``press_failure`` is the sentence a failure is told in, for the
client that sent it.
"""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from openavc.api.error_messages import describe_press_failure
from openavc.utils.logger import get_logger

# The socket's own logger name, so these lines file where they always did.
log = get_logger("openavc.api.ws")

# A send that fails with one of these is a client gone away, not a fault.
DISCONNECTS = (WebSocketDisconnect, ConnectionError, OSError)


async def send_ws(ws: WebSocket, msg: dict[str, Any]) -> None:
    """Send a JSON message to the client, silently ignoring disconnects."""
    try:
        await ws.send_json(msg)
    except DISCONNECTS:
        pass  # Client disconnected mid-send
    except Exception:
        log.debug("WebSocket send failed", exc_info=True)


def error_frame(source_type: str | None, message: str, **about: str | None) -> dict[str, Any]:
    """An error frame — THE only producer of ``type: "error"`` answering a
    client's message. Each door sends it through its own module's sender
    (``ws._send_ws_error``, ``ws_macros``), so a test that replaces one
    module's sender sees every frame that module sends. The one the server sends on its own, a plugin's failed
    save of its settings, is broadcast to the Programmer with ``source_type``
    ``plugin.save_config`` (``core/project_logins.py``).

    One shape: ``{type, message}`` plus ``source_type`` naming the inbound
    message that failed, whenever that is known. It is omitted (not set to a
    placeholder) for connection-level failures where nothing was parsed —
    rate limiting and malformed JSON — because inventing a message type there
    would be a lie a client can't distinguish from a real one.

    A partly-failed interaction sends one too: the message itself was accepted
    and the rest of the binding ran, but one action inside it did not reach its
    device (see ``ws._run_ui_event``). ``source_type`` still names the interaction,
    because that is what the person pressed.

    ``element_id`` names the control that interaction came from, and is present
    on exactly those two: a refused interaction, and one whose action did not
    reach its device. A panel moves a control the moment it is touched, so a
    command that never ran leaves the operator's own value standing on screen
    with nothing coming to correct it — the client puts the control back, and
    this is what tells it which one. Absent for everything a control did not
    cause (a rate limit, malformed JSON, a bad ``element_id`` in the first
    place); a client falls back to whatever it last sent, which is the same
    correlation it already uses to place the message.

    ``request_id`` is the other correlation field, ``about`` like
    ``element_id``: the client's own name for a ``macro.execute`` it asked,
    on the failure of a run that raised after its receipt, which arrives out
    of order (``ws_macros.execute_macro``).

    Note the two other ways this socket reports a failure, both deliberate:
    a result ack (``command.ack``/``state.set.ack``) carries ``success: false``
    plus the correlation fields a generic error frame has no room for (which
    device, which key); and a macro's outcome travels on its *lifecycle*
    events to every client and on ``macro.execute.result`` to the client that
    asked. See the WebSocket section of the API reference for the rule.
    """
    frame: dict[str, Any] = {"type": "error", "message": message}
    if source_type:
        frame["source_type"] = source_type
    frame.update({name: value for name, value in about.items() if value})
    return frame


def press_failure(exc: Exception, client_type: str) -> str:
    """A press's own failure, in the words for the client that sent it."""
    return describe_press_failure(exc).for_reader(at_panel=client_type == "panel")
