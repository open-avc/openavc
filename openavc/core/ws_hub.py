"""
WebSocket push hub — the engine's fan-out path to connected panels and IDEs.

Two halves of one pipeline:

  * **Client fan-out.** Every client gets a bounded send queue drained by its
    own writer task, so a broadcast never awaits a client's TCP send and one
    wedged panel can't stall macros, triggers, or the state flush loop.
  * **State coalescing.** State changes arrive far faster than a panel needs
    them, so they batch into a 50 ms window and go out as one
    ``state.update`` / ``state.delete`` message.

THE rule for which clients a server push reaches, and what a panel receives
of it, is the table below. Every message ``broadcast`` sends is looked up in
``MESSAGE_AUDIENCE`` by its ``type``, and a type with no row reaches the
Programmer only; no sender chooses its own audience. The event stream (a
client subscribed to bus events by name, ``api/ws.py``) asks the same table
through ``event_reach`` and ``WSHub.panel_copy``: a bus event the engine also
pushes (``BUS_PUSHES``) answers by its type's row, any other by its
namespace's row in ``EVENT_AUDIENCE``, so a client hears a thing on both
paths or on neither. ``PANEL_COPIES`` names what a panel gets instead of a
payload written for the Programmer, on both paths.

THE WebSocket exit's secret rule is ``frame_as``, which every frame a client
is sent passes through, on the broadcast, the event stream and a reply to the
client's own message: the frame leaves as its exit may carry a secret
(``utils/secret_audience``), and its exit is its type's row in
``SECRET_EXITS`` (live state, the pages), else the client's own type, the
row the audience table names. Neither a panel nor the Programmer may carry
one, so a plugin's start failure, a macro step's exception and a script's
traceback reach the Programmer with every registered secret and an address's
password masked, as the log writes them; the names a client routes a frame by
(``ROUTING_NAMES``) stay as they are.

Owned by the engine (``engine.ws``). ``Engine.broadcast_ws`` forwards here
and stays the platform-wide entry point for sending a message to panels.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
from fnmatch import fnmatch, translate
from typing import Any, Callable

from openavc.core.macro_text import MACRO_STOPPED
from openavc.core.state_store import StateStore
from openavc.utils.logger import get_logger
from openavc.utils.secret_audience import Exit
from openavc.utils.secret_exits import message_as

log = get_logger(__name__)

# Sentinel used to distinguish deleted keys (absent from the store) from
# keys that exist with a None value. Used by on_state_change.
_STATE_MISSING = object()

# Per-client WS send queue depth. At the state flush loop's 20 msg/s ceiling
# this is >10s of buffered updates — a client that far behind is wedged, not
# slow, and is dropped to reconnect with a fresh snapshot.
_WS_SEND_QUEUE_MAX = 256

# State flush cadence: 50ms = max 20 updates/sec to each client.
_STATE_FLUSH_INTERVAL = 0.05


# --- Who each push reaches -----------------------------------------------------
#
# The WebSocket handler (api/ws.py) makes every client one of two types, from
# its credential and never from what it asked to be: the Programmer, or a
# panel. An integration such as Node-RED is whichever of the two it connected
# as, and a socket through Remote Access gets what its type gets.

PANEL = "panel"
PROGRAMMER = "programmer"
CLIENT_TYPES = frozenset({PANEL, PROGRAMMER})

_EVERYONE = frozenset({PANEL, PROGRAMMER})
_PROGRAMMER_ONLY = frozenset({PROGRAMMER})

# One row per message type the server pushes. A type with no row reaches the
# Programmer only, and the log says so once.
MESSAGE_AUDIENCE: dict[str, frozenset[str]] = {
    # Live state, every 50 ms; a client's namespace filter narrows it further.
    "state.update": _EVERYONE,
    "state.delete": _EVERYONE,
    # What a panel draws, and the page it shows.
    "ui.definition": _EVERYONE,
    "ui.navigate": _EVERYONE,
    "ui.files": _EVERYONE,
    # A saved theme changed: a panel drawing it fetches it again.
    "themes.changed": _EVERYONE,
    # The project's revision after a save, a reload or a whole project
    # replaced.
    "project.reloaded": _EVERYONE,
    # A macro's run (a panel's button follows its progress). A failure reaches
    # a panel as the room's sentence (``PANEL_COPIES``).
    "macro.started": _EVERYONE,
    "macro.progress": _EVERYONE,
    "macro.completed": _EVERYONE,
    "macro.cancelled": _EVERYONE,
    "macro.skipped": _EVERYONE,
    "macro.error": _EVERYONE,
    "macro.step_error": _EVERYONE,
    # A trigger firing, waiting, queued, skipped or done: the IDE's
    # bookkeeping, which no panel draws.
    "trigger.fired": _PROGRAMMER_ONLY,
    "trigger.pending": _PROGRAMMER_ONLY,
    "trigger.queued": _PROGRAMMER_ONLY,
    "trigger.skipped": _PROGRAMMER_ONLY,
    "trigger.completed": _PROGRAMMER_ONLY,
    # A plugin starting, stopping, failing, or named by the project and absent.
    # A failure's text can carry a local path or an address.
    "plugin.started": _PROGRAMMER_ONLY,
    "plugin.stopped": _PROGRAMMER_ONLY,
    "plugin.error": _PROGRAMMER_ONLY,
    "plugin.missing": _PROGRAMMER_ONLY,
    # A plugin's own save of its settings that failed, named by its
    # ``source_type``. Every other ``error`` frame answers one client's message
    # and is sent to that client alone (``api/ws.py``).
    "error": _PROGRAMMER_ONLY,
    # A script's exception and its traceback; a panel draws nothing from it.
    "script.error": _PROGRAMMER_ONLY,
    # A panel waiting for approval, with its code and address.
    "panel.devices.changed": _PROGRAMMER_ONLY,
    # The drivers on this server changed, so the IDE's lists reload.
    "drivers.changed": _PROGRAMMER_ONLY,
    # A setup action's progress, with the handler's own result message.
    "action.progress": _PROGRAMMER_ONLY,
    # A device command run from the IDE, and its result.
    "command.run": _PROGRAMMER_ONLY,
    # A Discovery scan: each device's whole record (address, MAC, serial
    # number, firmware, open ports), which over REST takes a Programmer
    # sign-in.
    "discovery.started": _PROGRAMMER_ONLY,
    "discovery.phase": _PROGRAMMER_ONLY,
    "discovery.update": _PROGRAMMER_ONLY,
    "discovery.complete": _PROGRAMMER_ONLY,
}

# The bus events the engine pushes as messages of their own
# (``Engine._bridge_events_to_ws`` subscribes each): the pattern, as the bus
# matches it, and the type the push carries. On the event stream, a subscribed
# event that matches one answers by that type's row above.
BUS_PUSHES: dict[str, str] = {
    **{f"macro.{kind}.*": f"macro.{kind}" for kind in (
        "started", "progress", "completed", "cancelled", "error", "step_error",
        "skipped")},
    **{name: name for name in (
        "trigger.fired", "trigger.skipped", "trigger.pending", "trigger.queued",
        "trigger.completed", "script.error",
        "plugin.started", "plugin.stopped", "plugin.error", "plugin.missing")},
}

# The event stream's rows for every other bus event, by namespace (the
# longest that fits wins). A namespace with no row reaches the Programmer
# only: a plugin's own events, a peer instance's, the cloud's, the AI's carry
# other systems' payloads.
EVENT_AUDIENCE: dict[str, frozenset[str]] = {
    # A state change is the state stream's (``state.update``), so no client
    # hears it here: a ``*`` subscription would double the hot path.
    "state.": frozenset(),
    # What a panel already reflects, and what a project's author sends.
    "custom.": _EVERYONE,
    "ui.": _EVERYONE,
    "device.": _EVERYONE,
    "macro.": _EVERYONE,
    "system.": _EVERYONE,
}

# The namespaces a panel hears on the event stream, in the table's order (the
# Node-RED package's `event in` node warns from a copy of this list).
PANEL_EVENT_NAMESPACES = tuple(
    namespace for namespace, reach in EVENT_AUDIENCE.items() if PANEL in reach)


def _exact_or_namespace(table: dict[str, Any], name: str) -> Any:
    """The row ``name`` has in ``table``: its own, else the longest namespace
    row (a key ending in ``.``) it falls under, else None."""
    if name in table:
        return table[name]
    end = len(name)
    while (end := name.rfind(".", 0, end)) != -1:
        row = table.get(name[:end + 1])
        if row is not None:
            return row
    return None


# Every pattern above as one expression, built once at import: each event
# stream subscriber asks inside every emit on the bus, a state change
# included, and a name the engine does not push is then answered by one match.
# Folded the way ``fnmatch`` folds names (``os.path.normcase``).
_ANY_BUS_PUSH = re.compile(
    "|".join(translate(os.path.normcase(pattern)) for pattern in BUS_PUSHES))


def bus_push_type(event: str) -> str | None:
    """The type the engine pushes this bus event as, or None when it is not
    pushed (matched the way the bus matches its subscriptions)."""
    if not _ANY_BUS_PUSH.match(os.path.normcase(event)):
        return None
    for pattern, msg_type in BUS_PUSHES.items():
        if fnmatch(event, pattern):
            return msg_type
    return None


def event_reach(event: str) -> frozenset[str]:
    """The client types that hear a bus event on the event stream: its push's
    row when the engine pushes it, else its namespace's row, else the
    Programmer only."""
    msg_type = bus_push_type(event)
    if msg_type is not None:
        return MESSAGE_AUDIENCE.get(msg_type, _PROGRAMMER_ONLY)
    reach = _exact_or_namespace(EVENT_AUDIENCE, event)
    return _PROGRAMMER_ONLY if reach is None else reach


# --- What a client may carry of a secret ---------------------------------------
#
# A frame leaves as its exit may carry a secret (``utils/secret_audience``):
# its type's row here, else its client's own type, which is the exit's row
# for a panel and for the Programmer, neither of which may carry one. These
# are what this system's panels draw, which may: the live state (a driver's
# stream address holds its login so a panel can play it) and the pages the
# project defines (an element's address as its author typed it), with the
# page a panel is sent to.

SECRET_EXITS: dict[str, Exit] = {
    "state.snapshot": Exit.DRIVER_STATE,
    "state.update": Exit.DRIVER_STATE,
    "state.delete": Exit.DRIVER_STATE,
    "ui.definition": Exit.PANEL_PAGES,
    "ui.navigate": Exit.PANEL_PAGES,
}

# The fields a client routes a frame by, at any depth: its type, an event's
# name, what failed and who asked (``source_type``, ``request_id``), and the
# names the project gives the things it is about. A name is no secret even
# when a password is spelled the same, and masking one would lose the frame:
# a panel finds the run it started by ``macro_id`` and ``call_chain``, and the
# Node-RED package settles a request by its ids and command.
ROUTING_NAMES = frozenset({
    "type", "event", "source_type", "request_id",
    "macro_id", "call_chain", "page_id", "element_id",
    "device_id", "command", "plugin_id", "script_id",
})


def frame_as(message: dict[str, Any], client_type: Any) -> Any:
    """``message`` as a client of ``client_type`` may carry it: its type's
    row in ``SECRET_EXITS``, else the client's own row, every other text in
    it masked where that exit may not carry a secret. A copy; never changes
    ``message``."""
    where = SECRET_EXITS.get(message.get("type"), client_type)
    return message_as(where, message, names=ROUTING_NAMES)


def frame_text(message: dict[str, Any], client_type: Any) -> str:
    """``frame_as`` encoded as it goes on the wire."""
    return json.dumps(frame_as(message, client_type), default=str)


# --- What a panel receives of it ----------------------------------------------
#
# A row here means a panel gets a copy made for the room instead of the
# payload, on whichever path the thing arrives: keyed by a pushed type (which
# answers for the bus event it is pushed from too), or by an event namespace
# ending in ``.``. Each copy is ``(name, payload, hub) -> dict``, sync, and
# returns a new dict: the bus hands every subscriber the same payload, and a
# trigger reading ``$trigger.error`` keeps the text.

_DEVICE_ERROR_PREFIX = "device.error."


def _device_error_for_the_room(name: str, payload: Any, hub: WSHub) -> dict[str, Any]:
    """A device error's ``error`` is a driver's exception text or the
    platform's words for the Programmer, written for neither the room nor a
    control a panel runs, so the panel's copy names the device instead."""
    given = payload if isinstance(payload, dict) else {}
    device_id = str(given.get("device_id") or name[len(_DEVICE_ERROR_PREFIX):])
    copy: dict[str, Any] = {
        "device_id": device_id,
        "error": f"{hub.device_label(device_id)} reported an error. Contact support.",
    }
    if isinstance(given.get("source"), str):
        copy["source"] = given["source"]
    return copy


def _macro_failure_for_the_room(name: str, payload: Any, hub: WSHub) -> dict[str, Any]:
    """A macro failure's ``error`` is the Programmer's words (a step's place,
    its command, an exception's text), so a panel's copy carries the sentence
    written for the room in its place: ``message``, else ``MACRO_STOPPED``.
    Every other field stays, since a panel routes on ``type`` and finds the
    run it started by ``macro_id`` and ``call_chain``."""
    copy = dict(payload) if isinstance(payload, dict) else {}
    sentence = copy.get("message")
    if not isinstance(sentence, str) or not sentence:
        sentence = MACRO_STOPPED
    copy["error"] = copy["message"] = sentence
    return copy


PANEL_COPIES: dict[str, Callable[[str, Any, "WSHub"], dict[str, Any]]] = {
    _DEVICE_ERROR_PREFIX: _device_error_for_the_room,
    "macro.error": _macro_failure_for_the_room,
    "macro.step_error": _macro_failure_for_the_room,
}


def _panel_copy_of(name: str) -> Callable[[str, Any, WSHub], dict[str, Any]] | None:
    """The copy a panel gets of ``name`` (a pushed type or a bus event), or None
    when a panel gets the payload itself."""
    return _exact_or_namespace(PANEL_COPIES, bus_push_type(name) or name)


def _filtered_state_text(message: dict[str, Any], ns: tuple[str, ...],
                         client_type: str | None) -> str | None:
    """A ``state.update`` or ``state.delete`` cut to the keys under ``ns`` (a
    client's namespace filter), or None when none of its keys are."""
    if message.get("type") == "state.update":
        changes = {k: v for k, v in message.get("changes", {}).items() if k.startswith(ns)}
        cut: dict[str, Any] = {"type": "state.update", "changes": changes}
        return frame_text(cut, client_type) if changes else None
    keys = [k for k in message.get("keys", []) if k.startswith(ns)]
    return frame_text({"type": "state.delete", "keys": keys}, client_type) if keys else None


class _Outgoing:
    """One broadcast's message, encoded once for each form a client gets: the
    message, a panel's copy (its type's row in ``PANEL_COPIES``), or a state
    batch cut to a client's namespace filter, each as its client may carry
    it (``frame_as``)."""

    def __init__(self, message: dict[str, Any], hub: WSHub) -> None:
        self._message = message
        self._hub = hub
        msg_type = message.get("type")
        # state.update and state.delete carry per-key payloads; a client with
        # ns_prefixes should only receive keys under those namespaces.
        self._filterable = msg_type in ("state.update", "state.delete")
        self._room_copy = (_exact_or_namespace(PANEL_COPIES, msg_type)
                           if isinstance(msg_type, str) else None)
        # Each form's text, by the client type it was encoded for.
        self._texts: dict[tuple[bool, str | None], str] = {}

    @functools.cached_property
    def _panel_copy(self) -> dict[str, Any]:
        return self._room_copy(self._message["type"], self._message, self._hub)

    def _text(self, copied: bool, client_type: str | None) -> str:
        text = self._texts.get((copied, client_type))
        if text is None:
            message = self._panel_copy if copied else self._message
            text = self._texts[(copied, client_type)] = frame_text(message, client_type)
        return text

    def text_for(self, client_type: str | None, ns: tuple[str, ...] | None) -> str | None:
        """What this client is sent, or None when nothing of it is its."""
        if self._room_copy is not None and client_type == PANEL:
            return self._text(True, client_type)
        if ns and self._filterable:
            return _filtered_state_text(self._message, ns, client_type)
        return self._text(False, client_type)


def _log_task_exception(task: asyncio.Task) -> None:
    """Log an exception from a fire-and-forget task instead of swallowing it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"Background task failed: {exc}", exc_info=exc)


class WSHub:
    """Registry of connected WebSocket clients + the batched push loop."""

    def __init__(self, state: StateStore, project: Callable[[], Any] | None = None):
        self._state = state
        # The project now, for the names a panel's copy uses (None: none loaded).
        self._project = project or (lambda: None)

        # WebSocket clients (set of WebSocket connections)
        self._clients: set = set()
        # Per-client namespace filters: id(ws) -> tuple of prefix strings
        self._ns_filters: dict[int, tuple[str, ...]] = {}
        # What each client is and how it got in: id(ws) -> "panel" |
        # "programmer", and id(ws) -> (admission kind, panel device id or
        # None). A broadcast reaches the client types its message type's row
        # names, and a panel device's sockets, or every socket a now-closed
        # mode admitted, can be closed by what admitted them.
        self._client_types: dict[int, str] = {}
        self._admissions: dict[int, tuple[str, str | None]] = {}
        # Per-client bounded send queues + their writer tasks: id(ws) -> ...
        # Broadcasts enqueue and return; each writer drains its own client,
        # so one wedged client can never stall the emit path (macros,
        # triggers, state flushes). Overflow drops the client (see broadcast).
        self._send_queues: dict[int, asyncio.Queue] = {}
        self._writers: dict[int, asyncio.Task] = {}
        # Per-client delivery gates: a writer sends nothing until its event
        # is set. Lets the connection handler register (and start buffering)
        # BEFORE taking the state snapshot, then release delivery once the
        # snapshot is on the wire — closing the gap where changes flushed
        # mid-handshake reached only already-registered clients.
        self._ready_events: dict[int, asyncio.Event] = {}
        # Strong refs for short-lived cleanup tasks (socket closes, cancelled
        # writers unwinding) — asyncio only weakly references tasks, so an
        # unreferenced one can be GC'd before it runs.
        self._cleanup_tasks: set[asyncio.Task] = set()

        # State batching for WebSocket push
        self._state_batch: dict[str, Any] = {}
        # Keys that were deleted (rather than set) since the last flush.
        # Tracked separately so the flush loop can emit a state.delete WS
        # message — clients can't tell delete from set-to-None otherwise.
        self._state_deleted_keys: set[str] = set()
        self._batch_task: asyncio.Task | None = None
        self._running = False
        # Message types sent with no row in MESSAGE_AUDIENCE, logged once each.
        self._unrowed: set[Any] = set()

    # --- Client registry ---

    @property
    def client_count(self) -> int:
        """Connected clients — reported in system status and cloud heartbeats."""
        return len(self._clients)

    def add_client(self, ws, ns_prefixes: tuple[str, ...] | None = None,
                   *, defer_delivery: bool = False, client_type: str = PANEL,
                   admitted_by: str = "", panel_device: str | None = None) -> None:
        """Register a WebSocket client with optional namespace filter.

        Each client gets a bounded send queue drained by its own writer
        task, so broadcast never awaits a client's TCP send directly.

        With ``defer_delivery=True`` broadcasts buffer into the queue but
        nothing is delivered until ``mark_client_ready()``. The connection
        handler registers before snapshotting so changes flushed
        mid-handshake buffer here instead of being missed, then releases
        delivery once the snapshot is on the wire.

        ``client_type`` is what the handler decided the client is (never what
        it asked to be); ``admitted_by`` and ``panel_device`` record how a
        panel got past the panel gate, so a revoke or a mode change can find
        exactly the sockets it should close.
        """
        self._clients.add(ws)
        if ns_prefixes:
            self._ns_filters[id(ws)] = ns_prefixes
        self._client_types[id(ws)] = client_type
        self._admissions[id(ws)] = (admitted_by, panel_device)
        queue: asyncio.Queue = asyncio.Queue(maxsize=_WS_SEND_QUEUE_MAX)
        self._send_queues[id(ws)] = queue
        ready = asyncio.Event()
        if not defer_delivery:
            ready.set()
        self._ready_events[id(ws)] = ready
        writer = asyncio.create_task(self._send_loop(ws, queue, ready))
        writer.add_done_callback(_log_task_exception)
        self._writers[id(ws)] = writer
        log.info(f"WebSocket client connected ({len(self._clients)} total)")

    def mark_client_ready(self, ws) -> None:
        """Release a ``defer_delivery`` client's writer once its snapshot has
        been sent. Queued updates may partially predate the snapshot; replay
        is safe because state messages carry full per-key values (the client
        converges on the latest, never regresses past it)."""
        ready = self._ready_events.get(id(ws))
        if ready is not None:
            ready.set()

    def remove_client(self, ws) -> None:
        """Unregister a WebSocket client."""
        if ws not in self._clients and id(ws) not in self._writers:
            return  # Already dropped (send failure / overflow) — keep idempotent
        self._drop_client(ws, cancel_writer=True)
        log.info(f"WebSocket client disconnected ({len(self._clients)} total)")

    def _drop_client(self, ws, *, cancel_writer: bool) -> None:
        """Remove a client from every registry; optionally cancel its writer.

        ``cancel_writer=False`` is for the writer task removing its own
        client on a send failure — it is about to exit on its own.
        """
        self._clients.discard(ws)
        self._ns_filters.pop(id(ws), None)
        self._client_types.pop(id(ws), None)
        self._admissions.pop(id(ws), None)
        self._send_queues.pop(id(ws), None)
        self._ready_events.pop(id(ws), None)
        writer = self._writers.pop(id(ws), None)
        if writer is not None and cancel_writer and not writer.done():
            writer.cancel()
            # Keep a strong ref while the cancelled task unwinds.
            self._cleanup_tasks.add(writer)
            writer.add_done_callback(self._cleanup_tasks.discard)

    async def _send_loop(
        self, ws, queue: asyncio.Queue, ready: asyncio.Event
    ) -> None:
        """Drain one client's send queue for the life of its connection.

        A send failure means the peer is gone or the transport broke: drop
        the client here; the connection handler's own remove_client on
        unwind is a no-op by then.
        """
        try:
            await ready.wait()
            while True:
                text = await queue.get()
                try:
                    await ws.send_text(text)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._drop_client(ws, cancel_writer=False)
            log.info(
                f"WebSocket client dropped on send failure "
                f"({len(self._clients)} total)"
            )
        finally:
            # Mark anything left undelivered as done so flush_sends()
            # joiners can't hang on a dead client's queue.
            while True:
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

    def _close_client(self, ws) -> None:
        """Best-effort close of an overflowed client's socket (fire-and-forget)."""

        async def _close() -> None:
            try:
                await ws.close(code=1013)  # 1013 = try again later
            except Exception:
                pass  # Peer already gone — nothing to close

        task = asyncio.create_task(_close())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def flush_sends(self) -> None:
        """Wait until every currently-queued WS message has been sent.

        Used by the shutdown flush (best-effort, with a timeout) and by
        tests that need delivery to have happened before asserting.
        """
        queues = list(self._send_queues.values())
        if queues:
            await asyncio.gather(*(q.join() for q in queues))

    async def close_clients(
        self, *, admitted_by: str | None = None, panel_device: str | None = None,
        code: int, reason: str,
    ) -> int:
        """Close every client that matches, and forget it. Returns how many.

        ``admitted_by`` selects the sockets the panel gate let in by that
        rule (``"open"`` when the mode switches to approved); ``panel_device``
        selects one device's sockets (a revoke). The connection handler's own
        unwind finds the client already gone and stays idempotent.
        """
        targets = []
        for ws in list(self._clients):
            kind, device = self._admissions.get(id(ws), ("", None))
            if admitted_by is not None and kind != admitted_by:
                continue
            if panel_device is not None and device != panel_device:
                continue
            targets.append(ws)
        for ws in targets:
            self._drop_client(ws, cancel_writer=True)
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                pass  # Peer already gone — nothing to close
        if targets:
            log.info(
                f"Closed {len(targets)} WebSocket client(s) with {code} "
                f"({len(self._clients)} total)"
            )
        return len(targets)

    async def close_panel_device(self, device_id: str) -> int:
        """Close the sockets one approved panel device holds (a revoke)."""
        from openavc.core.panel_devices import (
            CLOSE_CODE_NOT_APPROVED,
            CLOSE_REASON_NOT_APPROVED,
        )

        return await self.close_clients(
            panel_device=device_id,
            code=CLOSE_CODE_NOT_APPROVED,
            reason=CLOSE_REASON_NOT_APPROVED,
        )

    # --- Broadcast ---

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Queue a JSON message for delivery to all connected WS clients.

        Never awaits a client send: messages go onto per-client bounded
        queues, so a slow or wedged client can't stall the caller (macro
        and trigger event emits, the 50ms state flush loop). A client
        whose queue overflows is dropped and its socket closed — panels
        auto-reconnect and resnapshot, which is cheaper than letting one
        sick client apply backpressure engine-wide.

        Which clients it reaches is the message type's row in
        ``MESSAGE_AUDIENCE``; a type with no row reaches the Programmer only.
        A type with a row in ``PANEL_COPIES`` reaches a panel as that copy.
        """
        reach = self._reach(message.get("type"))
        if not self._clients:
            return
        outgoing = _Outgoing(message, self)
        for ws in list(self._clients):
            client_type = self._client_types.get(id(ws))
            if client_type not in reach:
                continue
            text = outgoing.text_for(client_type, self._ns_filters.get(id(ws)))
            if text is not None:
                self._enqueue(ws, text)

    def _reach(self, msg_type: Any) -> frozenset[str]:
        """The client types a message of this type reaches: its row, or the
        Programmer only for a type with none."""
        reach = MESSAGE_AUDIENCE.get(msg_type)
        if reach is None:
            self._no_row(msg_type)
            return _PROGRAMMER_ONLY
        return reach

    def _no_row(self, msg_type: Any) -> None:
        """Log, once per type, a push sent with no row in ``MESSAGE_AUDIENCE``."""
        if msg_type in self._unrowed:
            return
        self._unrowed.add(msg_type)
        log.warning(
            f"A '{msg_type}' message has no row in the WebSocket audience "
            "table, so only the Programmer receives it"
        )

    def panel_copy(self, name: str, payload: Any) -> Any:
        """What a panel receives of ``payload`` under ``name`` (a pushed type or
        a bus event): its row's copy in ``PANEL_COPIES``, or the payload."""
        room_copy = _panel_copy_of(name)
        return payload if room_copy is None else room_copy(name, payload, self)

    def device_label(self, device_id: str) -> str:
        """The name a panel's copy calls a device by."""
        from openavc.api.error_messages import device_error_label

        return device_error_label(device_id, self._state, self._project())

    def client_type_of(self, ws) -> str | None:
        """The type this client connected as, or None for one the hub does
        not hold."""
        return self._client_types.get(id(ws))

    def send(self, ws, message: dict[str, Any]) -> None:
        """Queue a JSON message for ONE client, on the same terms as broadcast.

        The event stream's bus handler calls this from inside every emit on the
        bus, so it decides and enqueues and never awaits a socket. A client the
        hub no longer holds (already dropped) is skipped; one whose queue
        overflows is dropped exactly as it would be under ``broadcast``.
        ``default=str`` because the payload is somebody else's dict -- a value
        that is not JSON must not take the whole stream down. It leaves as
        this client may carry it (``frame_as``).
        """
        self._enqueue(ws, frame_text(message, self._client_types.get(id(ws))))

    def _enqueue(self, ws, text: str) -> None:
        queue = self._send_queues.get(id(ws))
        if queue is None:
            return
        try:
            queue.put_nowait(text)
        except asyncio.QueueFull:
            log.warning(
                "WebSocket client send queue overflowed "
                f"({_WS_SEND_QUEUE_MAX} messages) — dropping client so it "
                "reconnects with a fresh snapshot"
            )
            self._drop_client(ws, cancel_writer=True)
            self._close_client(ws)

    # --- State batching ---

    def on_state_change(
        self, key: str, old_value: Any, new_value: Any, source: str
    ) -> None:
        """Collect state changes into a batch for WebSocket push.

        Safe in single-threaded asyncio: this sync callback runs atomically
        between awaits of the flush loop. The lock is acquired in the flush
        loop to guard the read-clear operation.

        Distinguishes deletion from set-to-None by probing the store: when
        StateStore.delete() fires the listener, the key has already been
        removed. Deletes go to _state_deleted_keys; sets go to _state_batch.
        Either action clears the key from the other bucket so a delete-then-set
        (or set-then-delete) within one window resolves to the latest action.
        """
        is_deleted = new_value is None and self._state.get(key, _STATE_MISSING) is _STATE_MISSING
        if is_deleted:
            self._state_batch.pop(key, None)
            self._state_deleted_keys.add(key)
        else:
            self._state_deleted_keys.discard(key)
            self._state_batch[key] = new_value

    def start_state_flush(self) -> None:
        """Start the periodic state flush loop (engine startup)."""
        self._running = True
        self._batch_task = asyncio.create_task(self._flush_state_batch_loop())
        self._batch_task.add_done_callback(_log_task_exception)

    async def stop_state_flush(self) -> None:
        """Stop the flush loop; its unwind does a final best-effort flush."""
        self._running = False
        task = self._batch_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._batch_task = None

    async def _flush_state_batch_loop(self) -> None:
        """Periodically flush batched state changes to WebSocket clients."""
        try:
            while self._running:
                await asyncio.sleep(_STATE_FLUSH_INTERVAL)
                if not self._state_batch and not self._state_deleted_keys:
                    continue
                await self.flush_state_batch()
        except asyncio.CancelledError:
            pass
        finally:
            # Best-effort flush of any remaining batch during shutdown.
            if self._state_batch or self._state_deleted_keys:
                try:
                    await self.flush_state_batch()
                    # The flush only enqueues; give the per-client writers a
                    # moment to actually deliver before the process exits.
                    await asyncio.wait_for(self.flush_sends(), timeout=1.0)
                except Exception:  # Errors are non-critical at shutdown
                    pass

    async def flush_state_batch(self) -> None:
        """Drain _state_batch and _state_deleted_keys into WS messages.

        Emits a state.update for set keys and a state.delete for deleted
        keys. Atomic swap of the buffers ensures on_state_change calls
        between the two broadcasts land in the next flush window.
        """
        # Swap out the buffers atomically — sync on_state_change can't
        # interleave with us between awaits, but this pattern is safe if
        # callers ever change.
        batch = self._state_batch
        self._state_batch = {}
        deleted = self._state_deleted_keys
        self._state_deleted_keys = set()

        if batch:
            await self.broadcast({
                "type": "state.update",
                "changes": batch,
            })
        if deleted:
            await self.broadcast({
                "type": "state.delete",
                "keys": sorted(deleted),
            })
