"""
OpenAVC EventBus — async pub/sub event system.

The nervous system of OpenAVC. Everything that happens is an event:
state changes, device connections, UI interactions, scheduled tasks, etc.

Event types follow a dotted namespace convention:
    state.changed              — any state change
    state.changed.<key>        — specific key changed
    device.connected.<id>      — device came online
    device.disconnected.<id>   — device went offline
    device.error.<id>          — device error
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
"""

from __future__ import annotations

import asyncio
import uuid
from fnmatch import fnmatch
from typing import Any, Callable

from server.utils.logger import get_logger

log = get_logger(__name__)


class EventBus:
    """Async pub/sub event bus with glob pattern matching."""

    MAX_EMIT_DEPTH = 4  # prevent runaway recursive event chains

    def __init__(self):
        # pattern -> list of (handler_id, handler_fn, once_flag)
        self._handlers: dict[str, list[tuple[str, Callable, bool]]] = {}
        self._emit_depth = 0

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """
        Emit an event. All matching handlers are called concurrently.

        Handlers that raise exceptions are caught and logged — one bad handler
        never prevents others from running. This is critical for system reliability.
        """
        if self._emit_depth >= self.MAX_EMIT_DEPTH:
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

        # Collect handlers to remove after (once-handlers)
        to_remove: list[str] = []

        # Run all matching handlers concurrently (with depth tracking)
        self._emit_depth += 1
        try:
            tasks = []
            for handler_id, handler, once in matching:
                if once:
                    to_remove.append(handler_id)
                tasks.append(self._call_handler(handler, event, payload))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        log.error(f"Event handler error during '{event}': {r}")
        finally:
            self._emit_depth -= 1

        # Remove once-handlers that fired
        for handler_id in to_remove:
            self.off(handler_id)

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
