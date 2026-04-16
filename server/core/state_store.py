"""
OpenAVC StateStore — centralized reactive key-value store.

The single source of truth for all system state. Every device property,
user variable, and UI element value lives here.

Keys follow a namespace convention:
    device.<device_id>.<property>    — e.g., "device.projector1.power"
    var.<variable_id>                — e.g., "var.room_active"
    ui.<element_id>.<property>       — e.g., "ui.vol_slider.value"
    system.<property>                — e.g., "system.uptime"
    isc.<instance_id>.<key>          — remote instance state

All values are Python primitives: str, int, float, bool, None.
No nested objects — flat key-value only.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from fnmatch import fnmatch
from time import time
from typing import TYPE_CHECKING, Any, Callable

from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.event_bus import EventBus

log = get_logger(__name__)


@dataclass
class HistoryEntry:
    """Record of a single state change."""

    key: str
    old_value: Any
    new_value: Any
    source: str
    timestamp: float = field(default_factory=time)


class StateStore:
    """Centralized reactive key-value state store with change notification."""

    def __init__(self):
        self._store: dict[str, Any] = {}
        # pattern -> list of (subscription_id, callback)
        self._listeners: dict[str, list[tuple[str, Callable]]] = {}
        self._history: deque[HistoryEntry] = deque(maxlen=1000)
        self._event_bus: EventBus | None = None
        self._pending_event_tasks: set[asyncio.Task] = set()

    def set_event_bus(self, event_bus: EventBus) -> None:
        """Wire up the EventBus after construction (avoids circular dependency)."""
        self._event_bus = event_bus

    def get(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        return self._store.get(key, default)

    def set(self, key: str, value: Any, source: str = "system") -> None:
        """
        Set a state value. If the value actually changed:
        1. Update internal store
        2. Record in history
        3. Notify matching listeners
        4. Emit events on the EventBus
        """
        old_value = self._store.get(key)
        if old_value == value and type(old_value) is type(value):
            return  # No change, skip notifications

        self._store[key] = value
        self._history.append(HistoryEntry(key, old_value, value, source))

        if log.isEnabledFor(10):  # DEBUG = 10
            log.debug(f"State: {key} = {value!r} (was {old_value!r}, source={source})")

        # Notify listeners
        self._notify_listeners(key, old_value, value, source)

        # Emit events on the EventBus
        if self._event_bus is not None:
            payload = {
                "key": key,
                "old_value": old_value,
                "new_value": value,
                "source": source,
            }
            # Schedule event emission as a task (non-blocking)
            try:
                loop = asyncio.get_running_loop()
                for event_name in ("state.changed", f"state.changed.{key}"):
                    task = loop.create_task(self._event_bus.emit(event_name, payload))
                    self._pending_event_tasks.add(task)
                    task.add_done_callback(self._pending_event_tasks.discard)
            except RuntimeError:
                # No running event loop — skip async events (happens in sync tests)
                pass

    def bulk_set(self, updates: dict[str, Any], source: str = "system") -> None:
        """Set multiple values. Each changed key fires its own notifications."""
        for key, value in updates.items():
            self.set(key, value, source)

    def set_batch(self, updates: dict[str, Any], source: str = "system") -> None:
        """
        Atomically set multiple values — all state is updated before any
        notifications fire. Listeners and triggers see the complete batch,
        not partial intermediate states.
        """
        # Phase 1: apply all changes, collect what actually changed
        changes: list[tuple[str, Any, Any]] = []  # (key, old_value, new_value)
        for key, value in updates.items():
            old_value = self._store.get(key)
            if old_value == value and type(old_value) is type(value):
                continue
            self._store[key] = value
            self._history.append(HistoryEntry(key, old_value, value, source))
            changes.append((key, old_value, value))

        if not changes:
            return

        # Phase 2: notify listeners (state is fully updated at this point)
        for key, old_value, new_value in changes:
            if log.isEnabledFor(10):
                log.debug(f"State: {key} = {new_value!r} (was {old_value!r}, source={source})")
            self._notify_listeners(key, old_value, new_value, source)

        # Phase 3: emit events
        if self._event_bus is not None:
            try:
                loop = asyncio.get_running_loop()
                for key, old_value, new_value in changes:
                    payload = {
                        "key": key,
                        "old_value": old_value,
                        "new_value": new_value,
                        "source": source,
                    }
                    for event_name in ("state.changed", f"state.changed.{key}"):
                        task = loop.create_task(self._event_bus.emit(event_name, payload))
                        self._pending_event_tasks.add(task)
                        task.add_done_callback(self._pending_event_tasks.discard)
            except RuntimeError:
                pass

    def subscribe(self, pattern: str, callback: Callable) -> str:
        """
        Subscribe to state changes matching a glob pattern.

        Examples:
            "device.projector1.*"  — all projector1 changes
            "device.*.power"       — power state of any device
            "var.*"                — all user variables
            "*"                    — everything

        Args:
            pattern: Glob pattern to match state keys against.
            callback: Called with (key, old_value, new_value, source).
                      Can be sync or async.

        Returns:
            Subscription ID (use to unsubscribe).
        """
        sub_id = str(uuid.uuid4())
        if pattern not in self._listeners:
            self._listeners[pattern] = []
        self._listeners[pattern].append((sub_id, callback))
        log.debug(f"State subscription {sub_id[:8]}... on pattern '{pattern}'")
        return sub_id

    def delete(self, key: str, source: str = "system") -> None:
        """Remove a key from the store entirely (not just set to None).

        Unlike set(key, None), this removes the key from the store so
        get(key) returns the default.  Fires the same listener and EventBus
        notifications as set() so that downstream consumers (state relay,
        triggers, WebSocket broadcast) learn about the removal.
        """
        if key not in self._store:
            return  # Key doesn't exist — nothing to notify

        old_value = self._store.pop(key)

        self._history.append(HistoryEntry(key, old_value, None, source))

        if log.isEnabledFor(10):  # DEBUG = 10
            log.debug(f"State: {key} deleted (was {old_value!r}, source={source})")

        # Notify listeners (key is already removed from _store at this point,
        # which lets consumers distinguish delete from set-to-None by checking
        # whether the key still exists in the store)
        self._notify_listeners(key, old_value, None, source)

        # Emit events on the EventBus
        if self._event_bus is not None:
            payload = {
                "key": key,
                "old_value": old_value,
                "new_value": None,
                "source": source,
            }
            try:
                loop = asyncio.get_running_loop()
                for event_name in ("state.changed", f"state.changed.{key}"):
                    task = loop.create_task(self._event_bus.emit(event_name, payload))
                    self._pending_event_tasks.add(task)
                    task.add_done_callback(self._pending_event_tasks.discard)
            except RuntimeError:
                pass

    def unsubscribe(self, sub_id: str) -> None:
        """Remove a subscription by ID. Cleans up empty pattern entries."""
        empty_patterns: list[str] = []
        for pattern, subs in self._listeners.items():
            self._listeners[pattern] = [(sid, cb) for sid, cb in subs if sid != sub_id]
            if not self._listeners[pattern]:
                empty_patterns.append(pattern)
        for pattern in empty_patterns:
            del self._listeners[pattern]

    def get_namespace(self, prefix: str) -> dict[str, Any]:
        """
        Get all key-value pairs under a namespace prefix.

        Example: get_namespace("device.projector1") returns
                 {"power": "on", "input": "hdmi1", "connected": True}
        """
        prefix_dot = prefix if prefix.endswith(".") else prefix + "."
        result = {}
        for key, value in self._store.items():
            if key.startswith(prefix_dot):
                short_key = key[len(prefix_dot):]
                result[short_key] = value
        return result

    def get_matching(self, pattern: str) -> dict[str, Any]:
        """Get all key-value pairs where the key matches a glob pattern."""
        return {k: v for k, v in self._store.items() if fnmatch(k, pattern)}

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        """Log exceptions from completed async callback tasks."""
        if not task.cancelled() and task.exception() is not None:
            log.error(f"State callback error: {task.exception()}")

    async def flush_pending_events(self) -> None:
        """Wait for all pending event emission tasks to complete."""
        if self._pending_event_tasks:
            await asyncio.gather(*self._pending_event_tasks, return_exceptions=True)
            self._pending_event_tasks.clear()

    def snapshot(self) -> dict[str, Any]:
        """Return a complete copy of the state store."""
        return dict(self._store)

    def get_history(self, count: int = 50) -> list[dict]:
        """Return recent state change history."""
        entries = list(self._history)[-count:]
        return [
            {
                "key": e.key,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "source": e.source,
                "timestamp": e.timestamp,
            }
            for e in entries
        ]

    def _notify_listeners(
        self, key: str, old_value: Any, new_value: Any, source: str
    ) -> None:
        """Call all listeners whose pattern matches the changed key."""
        for pattern, subs in self._listeners.items():
            if fnmatch(key, pattern):
                for _sub_id, callback in subs:
                    try:
                        result = callback(key, old_value, new_value, source)
                        # If the callback is async, schedule it with error logging
                        if asyncio.iscoroutine(result):
                            try:
                                loop = asyncio.get_running_loop()
                                task = loop.create_task(result)
                                task.add_done_callback(self._log_task_exception)
                            except RuntimeError:
                                pass  # No event loop — skip
                    except Exception:  # Catch-all: isolates listener callback errors
                        log.exception(f"Error in state listener for pattern '{pattern}'")
