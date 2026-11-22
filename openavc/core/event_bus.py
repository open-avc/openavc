"""
OpenAVC EventBus — async pub/sub event system.

The nervous system of OpenAVC. Everything that happens is an event:
state changes, device connections, UI interactions, macro runs, etc.

Event names follow a dotted namespace convention. Every name the platform
sends is in one of two tables below, and nowhere else: ``EVENTS``, the ones a
project listens for (the script editor's completions are generated from it and
the scripting docs list it), and ``PLATFORM_FEEDS``, the ones the platform
sends for its own screens and services. tests/test_event_catalog.py holds both
to every emit in the code, so a name nothing sends cannot be listed and a new
emit cannot be left off.

device.disconnected vs device.error
-----------------------------------
These two are complementary, not interchangeable:

* ``device.disconnected.<id>`` fires when the transport itself fails —
  the TCP socket drops, the serial port unplugs, the poll watchdog
  trips on a UDP/HTTP/OSC device. The driver's ``connected`` state
  flips to False at the same time. Use this to drive "device offline"
  UI and recovery logic.

* ``device.error.<id>`` fires when a command or poll completes against
  a live transport but the protocol layer fails — a command the device
  rejects, a decode error, a malformed response, an HTTP 5xx, a timeout
  waiting for a reply on a still-open socket. The connection is presumed
  alive; only this command went wrong. Use this for protocol-level
  alerting that's distinct from "the device is gone." A command the
  platform refuses before sending (core/device_commands.py: a parameter
  the declaration refuses, an undeclared name, offline) emits neither.

If the same exception is both (e.g. a TCP write fails because the
socket just died), only ``device.disconnected`` fires — the transport
callback owns that path.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from fnmatch import fnmatch, fnmatchcase
from typing import Any, Callable, NamedTuple

from openavc.core.ws_hub import PANEL, PROGRAMMER, event_reach
from openavc.utils.logger import get_logger

log = get_logger(__name__)


# --- The event doors' policy ---------------------------------------------------
#
# Two remote doors reach the bus from outside the process -- the WebSocket's
# event stream and its `event.emit` message, and `POST /api/events` -- and both
# ask here what an outside caller may emit, the way every remote door into the
# state store asks `state_store.check_state_write`. One rule, so the two doors
# cannot reach different conclusions about the same name.

# Who a subscription reaches is not decided here: it is the WebSocket audience
# table's (`core/ws_hub`), the one that decides who a server push reaches, so
# a client hears a thing on the event stream exactly when it would get the
# push. `event_visible` asks it.

# What ANY external caller may emit. Every other namespace is owned by a
# subsystem -- a driver's lifecycle, a plugin, the cloud, a peer -- and a
# spoofed `device.disconnected.x` or `cloud.command` is a lie those subsystems
# act on. The event analogue of `PANEL_WRITABLE_PREFIXES`, and it does not
# widen with a credential: the Programmer has no reason to forge a device
# event either.
EXTERNAL_EMIT_PREFIXES = ("custom.",)


def event_visible(event: str, *, panel: bool) -> bool:
    """Whether an external subscriber with this posture receives ``event``:
    its row in the WebSocket audience table (``ws_hub.event_reach``)."""
    return (PANEL if panel else PROGRAMMER) in event_reach(event)


def check_event_emit(event: Any, payload: Any) -> str | None:
    """One policy for every remote door that emits onto the bus.

    Returns a user-facing reason, or None when the emit is allowed. REST turns
    it into a 422, the WebSocket into an error frame -- the same sentence
    through both, so a caller cannot succeed at one door and fail at the other.
    """
    if not isinstance(event, str) or not event.strip():
        return "Missing event name"
    prefix = next((p for p in EXTERNAL_EMIT_PREFIXES if event.startswith(p)), None)
    if prefix is None or not event[len(prefix):].strip():
        names = ", ".join(f"'{p}<name>'" for p in EXTERNAL_EMIT_PREFIXES)
        return f"An event emitted over the API must be named {names}"
    if payload is not None and not isinstance(payload, dict):
        return "Event payload must be a JSON object"
    return None


# --- The events a project hears ------------------------------------------------
#
# THE list of event names, by family: what a script's `@on_event`, an event
# trigger or an outside listener can be told, written with `<...>` where the
# sender puts an id. `script_api_gen` renders `EVENTS` into the script editor's
# completions; docs/scripting-guide.md and docs/scripting-api-reference.md list
# the same names, which tests/test_event_catalog.py checks, along with every
# emit in the code against the two tables.


class EventFamily(NamedTuple):
    """One family of events: its name, with ``<...>`` for the sender's id,
    and what it means, in the words the script editor shows."""

    name: str
    description: str


EVENTS: tuple[EventFamily, ...] = (
    EventFamily("ui.press.<element_id>", "A button was pressed"),
    EventFamily("ui.release.<element_id>", "A button was released"),
    EventFamily("ui.hold.<element_id>", "A button was held past its hold time"),
    EventFamily("ui.toggle_off.<element_id>", "A toggle button was turned off"),
    EventFamily("ui.change.<element_id>", "A slider or select changed value"),
    EventFamily("ui.select.<element_id>", "A list row was selected"),
    EventFamily("ui.route.<element_id>", "A matrix route was made"),
    EventFamily("ui.audio_route.<element_id>", "An audio-breakaway route was made"),
    EventFamily("ui.mute_route.<element_id>", "A matrix output was muted or unmuted"),
    EventFamily(
        "ui.audio_mute_route.<element_id>",
        "An audio-breakaway output was muted or unmuted",
    ),
    EventFamily("ui.submit.<element_id>", "A text input or keypad was submitted"),
    EventFamily("ui.page.<page_id>", "A panel went to this page"),
    EventFamily("device.connected.<device_id>", "The device connected"),
    EventFamily("device.disconnected.<device_id>", "The device's connection was lost"),
    EventFamily(
        "device.error.<device_id>",
        "A command or poll failed while the device stayed connected",
    ),
    EventFamily("macro.started.<macro_id>", "The macro started"),
    EventFamily("macro.completed.<macro_id>", "The macro finished"),
    EventFamily("macro.cancelled.<macro_id>", "The macro was cancelled"),
    EventFamily(
        "macro.step_error.<macro_id>",
        "A step of the macro failed, whether or not the macro stopped there",
    ),
    EventFamily(
        "macro.error.<macro_id>",
        "The macro stopped because a step failed (Stop on Error)",
    ),
    EventFamily(
        "macro.skipped.<macro_id>",
        "The macro did not start: its Overlap or Cooldown setting refused the run",
    ),
    EventFamily("system.started", "The system started. Comes once per server start"),
    EventFamily("system.stopping", "The system is shutting down"),
    EventFamily("system.project.reloaded", "The project was saved or reloaded"),
    EventFamily("help.requested", "A macro's Ask for Help step asked for help"),
    EventFamily("help.acknowledged", "Someone acknowledged the help request in the cloud"),
    EventFamily("isc.peer_connected", "Another OpenAVC instance connected"),
    EventFamily("isc.peer_disconnected", "Another OpenAVC instance disconnected"),
    EventFamily("isc.<instance_id>.<event>", "An event sent by another OpenAVC instance"),
    EventFamily("plugin.<plugin_id>.<event>", "An event sent by a plugin"),
    EventFamily("custom.<anything>", "Your own event, sent by an Emit Event step or events.emit()"),
)

#: What the platform sends for its own screens and services, and does not
#: offer a project. A script still hears one it names; none is a way to
#: build a space. A state change has `@on_state_change` instead.
PLATFORM_FEEDS: tuple[str, ...] = (
    "state.changed",
    "state.changed.<key>",
    "macro.progress.<macro_id>",
    "trigger.fired",
    "trigger.skipped",
    "trigger.pending",
    "trigger.queued",
    "trigger.completed",
    "script.error",
    "plugin.started",
    "plugin.stopped",
    "plugin.error",
    "plugin.missing",
    "plugin.auto_disabled",
    "plugin.<plugin_id>.action.<action_id>",
    "simulation.stopped",
    "simulation.crashed",
    "device.orphaned",
    "device.pending_settings_applied",
    "log.plugin",
    "ai.device_command",
    "cloud.command",
    "cloud.config_push",
    "cloud.diagnostic",
    "cloud.restart",
    "cloud.software_update",
    "cloud.panel_software_update",
    "cloud.alert_rules_update",
)


def event_pattern(name: str) -> str:
    """A family's name as the pattern that hears all of it: each ``<...>``
    becomes ``*``, the way ``@on_event`` and an event trigger match."""
    parts = ("*" if part.startswith("<") and part.endswith(">") else part
             for part in name.split("."))
    return ".".join(parts)


# --- The platform's own events -----------------------------------------------
#
# THE rule for the event doors a project reaches: a macro's Emit Event step, a
# control's event action and a script's `events.emit`. They may send any name
# but the platform's. A `system.` event is the platform saying something
# happened to it: `system.started` runs every Startup trigger,
# `system.stopping` every stopping one, and `system.project.reloaded` has the
# cloud's alert monitor read its rules again. A project that sent one would be
# telling all of them something that did not happen, so the whole namespace is
# refused there, not only the names below: an event the platform adds later is
# protected the day it is added. The remote doors above are narrower still
# (`custom.` only), and a plugin's emits are prefixed with its own id.
#
# Refused by name as the bus matches it: `fnmatch` folds case where the file
# system does (Windows), so `System.Started` would reach a `system.started`
# trigger there, and a padded name is no different to the person who wrote it.

PLATFORM_EVENT_PREFIX = "system."

#: The `system.` events the platform sends today, as `EVENTS` lists them.
#: Listening for one of these is how a project hears the platform; listening
#: for any other `system.` name hears nothing, since no project can send one.
#: Pinned against every emit in the code by tests/test_project_event_doors.py.
PLATFORM_EVENTS = frozenset(
    family.name for family in EVENTS if family.name.startswith(PLATFORM_EVENT_PREFIX))


def is_platform_event(event: Any) -> bool:
    """Whether ``event`` is in the platform's namespace, as the bus matches it."""
    return isinstance(event, str) and event.strip().casefold().startswith(PLATFORM_EVENT_PREFIX)


def project_event_name(event: str) -> str:
    """The name a project gives its own event that it wrote under ``system.``:
    the same name under ``custom.``, the namespace the API's doors take."""
    return EXTERNAL_EMIT_PREFIXES[0] + event.strip()[len(PLATFORM_EVENT_PREFIX):]


def check_project_emit(event: Any) -> str | None:
    """Why a project's door does not send ``event``, or None when it may.

    The one sentence for every door a project reaches and for the checks that
    mark it before it runs (the macro editor's, the UI Builder's, the script
    editor's, and the AI's tools, which refuse it): what was refused, and the
    name to use instead.
    """
    if not is_platform_event(event):
        return None
    return (
        f"Event '{event}' is not sent: names starting with '{PLATFORM_EVENT_PREFIX}' are "
        f"OpenAVC's own. Name this event '{project_event_name(event)}'."
    )


def silent_platform_pattern(pattern: Any) -> bool:
    """Whether listening for ``pattern`` can hear nothing: it is in the
    platform's namespace and matches none of the events the platform sends,
    and no project can send one there (``check_project_emit``)."""
    if not is_platform_event(pattern):
        return False
    folded = pattern.strip().casefold()
    return not any(fnmatchcase(event, folded) for event in PLATFORM_EVENTS)


def silent_pattern_sentence(pattern: str) -> str:
    """What the checks say about a pattern ``silent_platform_pattern`` finds."""
    *first, last = sorted(PLATFORM_EVENTS)
    sent = f"{', '.join(first)} and {last}"
    return (
        f"Nothing sends '{pattern}': under '{PLATFORM_EVENT_PREFIX}' OpenAVC sends only {sent}. "
        f"A project's own event is named '{project_event_name(pattern)}'."
    )


# Recursion depth of a SINGLE emit chain (emit -> handler -> emit -> ...), not
# the number of emits running concurrently. A ContextVar gives us exactly this:
# asyncio.create_task / gather copy the current context, so independent
# concurrent emits (many devices connecting at once, several macros emitting
# progress, the per-transaction state.changed dispatch) each start from depth 0
# in their own context, while a handler that re-emits inherits depth+1. The old
# shared instance counter was incremented across the gather() await, so it
# conflated breadth with depth and silently dropped legitimate high-fan-out
# events once enough were in flight at the same time. Mirrors the same idiom
# used for the macro call chain (macro_engine._active_call_chain).
_emit_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "openavc_event_emit_depth", default=0
)


def detach_emit_chain() -> None:
    """Reset the emit-depth counter for a long-lived background loop.

    Copying the context is what makes the counter measure real recursion
    rather than breadth (see above), and it is right for a task that finishes
    inside the chain that spawned it. It is wrong for a task that outlives it:
    a loop started from inside a handler inherits that handler's depth and
    never gives it back, so every event it emits for the rest of its life is
    charged to a chain that has long since unwound.

    That ratchets, and a flapping device is how it shows up. The watchdog drops
    a dead link and emits ``device.disconnected.<id>`` from inside an emit
    chain; the reconnect loop a handler of that event spawns inherits its
    depth; the loop's own reconnect emits the next disconnect one level deeper
    again. After a few cycles the device crosses ``MAX_EMIT_DEPTH`` and the bus
    starts silently dropping its ``device.connected.<id>`` and
    ``state.changed.device.<id>.connected`` events — so triggers, panel
    indicators, and anything else watching that device's connection state stop
    being told anything at all, permanently, with only a debug-level warning
    about a "possible recursive event chain" to show for it.

    A loop that runs for the lifetime of a device is a root, not a
    continuation. Call this once at the top of one.
    """
    _emit_depth.set(0)


class EventBus:
    """Async pub/sub event bus with glob pattern matching."""

    MAX_EMIT_DEPTH = 4  # prevent runaway recursive event chains (per chain)

    def __init__(self):
        # pattern -> list of (handler_id, handler_fn, once_flag)
        self._handlers: dict[str, list[tuple[str, Callable, bool]]] = {}

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> bool:
        """
        Emit an event. All matching handlers are called concurrently.

        Handlers that raise exceptions are caught and logged — one bad handler
        never prevents others from running. This is critical for system reliability.

        Returns False when the event was dropped at ``MAX_EMIT_DEPTH`` and True
        otherwise, handlers or none. A caller that answers to someone for the
        emit (a script's ``events.emit``) reads it; the rest ignore it.
        """
        depth = _emit_depth.get()
        if depth >= self.MAX_EMIT_DEPTH:
            log.warning(
                f"Event '{event}' dropped — max emit depth ({self.MAX_EMIT_DEPTH}) "
                f"reached. Possible recursive event chain."
            )
            return False

        payload = payload or {}
        matching = self._find_handlers(event)

        if not matching:
            return True

        log.debug(f"Event: {event} -> {len(matching)} handler(s)")

        # Unregister once-handlers BEFORE the await below: a concurrent re-emit
        # of the same event between here and gather() must not find them still
        # registered and fire them a second time. We already captured them in
        # ``matching``, so they still run exactly once this time.
        for handler_id, _handler, once in matching:
            if once:
                self.off(handler_id)

        # Run all matching handlers concurrently. The depth bumps live in this
        # task's context, so handler tasks spawned by gather() inherit depth+1
        # (true recursion) while unrelated concurrent emits keep their own.
        token = _emit_depth.set(depth + 1)
        try:
            tasks = [
                self._call_handler(handler, event, payload)
                for _handler_id, handler, _once in matching
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    log.error(f"Event handler error during '{event}': {r}")
        finally:
            _emit_depth.reset(token)
        return True

    def dropped_sentence(self, event: str) -> str:
        """Why ``emit`` returned False for ``event``, for whoever sent it."""
        return (
            f"Event '{event}' was not sent: a chain of events, each sent by a "
            f"handler of the one before, stops after {self.MAX_EMIT_DEPTH}. "
            f"Look for handlers that send each other's events in a loop."
        )

    def on(self, event_pattern: str, handler: Callable) -> str:
        """
        Register a handler for events matching a pattern.

        Supports glob wildcards:
            "device.connected.*"  — any device connection
            "ui.press.*"          — any button press
            "*"                   — everything

        Args:
            event_pattern: Glob pattern to match event names.
            handler: Called with (event_name, payload). Can be sync or async.

        Returns:
            Handler ID (use to unregister with off()).
        """
        handler_id = str(uuid.uuid4())
        if event_pattern not in self._handlers:
            self._handlers[event_pattern] = []
        self._handlers[event_pattern].append((handler_id, handler, False))
        log.debug(f"Handler {handler_id[:8]}... registered for '{event_pattern}'")
        return handler_id

    def once(self, event_pattern: str, handler: Callable) -> str:
        """Register a handler that fires only once, then auto-removes."""
        handler_id = str(uuid.uuid4())
        if event_pattern not in self._handlers:
            self._handlers[event_pattern] = []
        self._handlers[event_pattern].append((handler_id, handler, True))
        return handler_id

    def off(self, handler_id: str) -> None:
        """Unregister a handler by ID. Cleans up empty pattern entries."""
        empty_patterns: list[str] = []
        for pattern in self._handlers:
            self._handlers[pattern] = [
                (hid, h, once)
                for hid, h, once in self._handlers[pattern]
                if hid != handler_id
            ]
            if not self._handlers[pattern]:
                empty_patterns.append(pattern)
        for pattern in empty_patterns:
            del self._handlers[pattern]

    def handler_count(self) -> int:
        """Total number of registered handlers (for debugging)."""
        return sum(len(handlers) for handlers in self._handlers.values())

    def _find_handlers(self, event: str) -> list[tuple[str, Callable, bool]]:
        """Find all handlers whose pattern matches the event name."""
        matching = []
        for pattern, handlers in self._handlers.items():
            if fnmatch(event, pattern):
                matching.extend(handlers)
        return matching

    @staticmethod
    async def _call_handler(
        handler: Callable, event: str, payload: dict[str, Any]
    ) -> None:
        """Safely call a handler, catching and logging any exceptions."""
        try:
            result = handler(event, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # Catch-all: isolates subscriber callback errors
            log.exception(f"Error in event handler for '{event}'")
