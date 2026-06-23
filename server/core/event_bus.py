"""
OpenAVC EventBus — async pub/sub event system.

The nervous system of OpenAVC. Everything that happens is an event:
state changes, device connections, UI interactions, scheduled tasks, etc.

Event types follow a dotted namespace convention:
    state.changed              — any state change
    state.changed.<key>        — specific key changed
    device.connected.<id>      — device came online
    device.disconnected.<id>   — transport-level loss (TCP socket closed,
                                 serial port gone, watchdog dry-poll trip)
    device.error.<id>          — protocol/parse/command failure on an
                                 otherwise-live connection (payload:
                                 {device_id, error})
    ui.press.<element_id>      — button pressed
    ui.release.<element_id>    — button released
    ui.change.<element_id>     — slider/select changed
    ui.page.<page_id>          — page navigation
    system.started             — engine started
    system.stopping            — engine shutting down
    macro.started.<macro_id>   — macro execution began
    macro.completed.<macro_id> — macro finished
    schedule.<schedule_id>     — scheduled event fired
    custom.<anything>          — user-defined events

device.disconnected vs device.error
-----------------------------------
These two are complementary, not interchangeable:

* ``device.disconnected.<id>`` fires when the transport itself fails —
  the TCP socket drops, the serial port unplugs, the poll watchdog
  trips on a UDP/HTTP/OSC device. The driver's ``connected`` state
  flips to False at the same time. Use this to drive "device offline"
  UI and recovery logic.

* ``device.error.<id>`` fires when a command or poll completes against
  a live transport but the protocol layer fails — a bad parameter, a
  decode error, a malformed response, an HTTP 5xx, a timeout waiting
  for a reply on a still-open socket. The connection is presumed
  alive; only this command went wrong. Use this for protocol-level
  alerting that's distinct from "the device is gone."

If the same exception is both (e.g. a TCP write fails because the
socket just died), only ``device.disconnected`` fires — the transport
callback owns that path.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from fnmatch import fnmatch
from typing import Any, Callable

from server.utils.logger import get_logger

log = get_logger(__name__)


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


class EventBus:
    """Async pub/sub event bus with glob pattern matching."""

    MAX_EMIT_DEPTH = 4  # prevent runaway recursive event chains (per chain)

    def __init__(self):
        # pattern -> list of (handler_id, handler_fn, once_flag)
        self._handlers: dict[str, list[tuple[str, Callable, bool]]] = {}

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """
        Emit an event. All matching handlers are called concurrently.

        Handlers that raise exceptions are caught and logged — one bad handler
        never prevents others from running. This is critical for system reliability.
        """
        depth = _emit_depth.get()
        if depth >= self.MAX_EMIT_DEPTH:
            log.warning(
                f"Event '{event}' dropped — max emit depth ({self.MAX_EMIT_DEPTH}) "
                f"reached. Possible recursive event chain."
            )
            return

        payload = payload or {}
        matching = self._find_handlers(event)

        if not matching:
            return

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
