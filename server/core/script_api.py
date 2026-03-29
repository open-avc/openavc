"""
OpenAVC Script API — the 'openavc' import shim for user scripts.

User scripts write:
    from openavc import on_event, on_state_change, devices, state, log, delay

This module provides the decorators, proxy objects, and timer functions.
ScriptEngine calls configure() to wire proxies to real subsystem instances,
then injects this module into sys.modules["openavc"].
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Callable, TYPE_CHECKING

from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus
    from server.core.macro_engine import MacroEngine
    from server.core.state_store import StateStore

_log = get_logger("openavc.script_api")


# --- Event object for handlers ---


class Event:
    """Wraps an event name + payload for single-parameter handler signatures.

    Provides attribute access to payload fields::

        @on_event("ui.press.*")
        def handle(event):
            print(event.name)        # "ui.press.btn1"
            print(event.element_id)  # "btn1"
    """

    def __init__(self, name: str, payload: dict[str, Any] | None = None):
        self.name = name
        self._payload = payload or {}

    def __getattr__(self, key: str) -> Any:
        try:
            return self._payload[key]
        except KeyError:
            raise AttributeError(f"Event has no attribute '{key}'")

    def __repr__(self) -> str:
        return f"Event({self.name!r}, {self._payload!r})"

    def get(self, key: str, default: Any = None) -> Any:
        return self._payload.get(key, default)

    @property
    def payload(self) -> dict[str, Any]:
        return dict(self._payload)


# --- Pending handler collection (drain pattern) ---

_pending_event_handlers: list[tuple[str, Callable]] = []
_pending_state_handlers: list[tuple[str, Callable]] = []


def on_event(pattern: str) -> Callable:
    """Decorator: register a handler for EventBus events matching *pattern*."""

    def decorator(fn: Callable) -> Callable:
        _pending_event_handlers.append((pattern, fn))
        return fn

    return decorator


def on_state_change(pattern: str) -> Callable:
    """Decorator: register a handler for StateStore changes matching *pattern*."""

    def decorator(fn: Callable) -> Callable:
        _pending_state_handlers.append((pattern, fn))
        return fn

    return decorator


def drain_pending() -> tuple[list[tuple[str, Callable]], list[tuple[str, Callable]]]:
    """Drain and return all pending handlers. Called by ScriptEngine after each import."""
    event_handlers = list(_pending_event_handlers)
    state_handlers = list(_pending_state_handlers)
    _pending_event_handlers.clear()
    _pending_state_handlers.clear()
    return event_handlers, state_handlers


# --- Proxy objects ---


class _DeviceProxy:
    """Proxy to DeviceManager. Lets scripts do devices.send('proj1', 'power_on')."""

    def __init__(self):
        self._manager: DeviceManager | None = None

    def _bind(self, manager: DeviceManager) -> None:
        self._manager = manager

    async def send(
        self, device_id: str, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        if self._manager is None:
            raise RuntimeError("Script API not configured — devices proxy not bound")
        return await self._manager.send_command(device_id, command, params)

    def list(self) -> list[dict[str, Any]]:
        if self._manager is None:
            return []
        return self._manager.list_devices()


class _StateProxy:
    """Proxy to StateStore. Lets scripts do state.get/set/subscribe."""

    def __init__(self):
        self._store: StateStore | None = None

    def _bind(self, store: StateStore) -> None:
        self._store = store

    def get(self, key: str, default: Any = None) -> Any:
        if self._store is None:
            return default
        return self._store.get(key, default)

    def set(self, key: str, value: Any, source: str = "script") -> None:
        if self._store is None:
            raise RuntimeError("Script API not configured — state proxy not bound")
        self._store.set(key, value, source=source)

    def delete(self, key: str) -> None:
        if self._store is None:
            raise RuntimeError("Script API not configured — state proxy not bound")
        self._store.delete(key)

    def get_namespace(self, prefix: str) -> dict[str, Any]:
        if self._store is None:
            return {}
        return self._store.get_namespace(prefix)


class _EventProxy:
    """Proxy to EventBus for emitting custom events from scripts."""

    def __init__(self):
        self._bus: EventBus | None = None

    def _bind(self, bus: EventBus) -> None:
        self._bus = bus

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if self._bus is None:
            raise RuntimeError("Script API not configured — event proxy not bound")
        await self._bus.emit(event, payload)


class _MacroProxy:
    """Proxy to MacroEngine. Lets scripts do await macros.execute('system_off')."""

    def __init__(self):
        self._engine: MacroEngine | None = None

    def _bind(self, engine: MacroEngine) -> None:
        self._engine = engine

    async def execute(self, macro_id: str) -> None:
        if self._engine is None:
            raise RuntimeError("Script API not configured — macro proxy not bound")
        await self._engine.execute(macro_id)


class _LogProxy:
    """Logger that prefixes messages with [script]."""

    def info(self, msg: str) -> None:
        _log.info(f"[script] {msg}")

    def warning(self, msg: str) -> None:
        _log.warning(f"[script] {msg}")

    def error(self, msg: str) -> None:
        _log.error(f"[script] {msg}")

    def debug(self, msg: str) -> None:
        _log.debug(f"[script] {msg}")


class _ISCProxy:
    """Proxy to ISCManager. Lets scripts communicate with other instances."""

    def __init__(self):
        self._manager = None

    def _bind(self, manager) -> None:
        self._manager = manager

    async def send_to(
        self, instance_id: str, event: str, payload: dict[str, Any] | None = None,
    ) -> None:
        """Send an event to a specific remote instance."""
        if self._manager is None:
            raise RuntimeError("ISC not enabled")
        await self._manager.send_to(instance_id, event, payload)

    async def broadcast(
        self, event: str, payload: dict[str, Any] | None = None,
    ) -> None:
        """Send an event to all connected instances."""
        if self._manager is None:
            raise RuntimeError("ISC not enabled")
        await self._manager.broadcast(event, payload)

    async def send_command(
        self,
        instance_id: str,
        device_id: str,
        command: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a device command to a remote instance and wait for the result."""
        if self._manager is None:
            raise RuntimeError("ISC not enabled")
        return await self._manager.send_command(instance_id, device_id, command, params)

    def get_instances(self) -> list[dict[str, Any]]:
        """List all discovered/connected peer instances."""
        if self._manager is None:
            return []
        return self._manager.get_instances()


# Singleton proxy instances (scripts import these directly)
devices = _DeviceProxy()
state = _StateProxy()
events = _EventProxy()
macros = _MacroProxy()
log = _LogProxy()
isc = _ISCProxy()

# --- Timer functions ---

_active_timers: dict[str, asyncio.Task] = {}
_timer_counter = 0


def _next_timer_id() -> str:
    global _timer_counter
    _timer_counter += 1
    return f"timer_{_timer_counter}"


async def delay(seconds: float) -> None:
    """Async sleep — use inside async handlers: await delay(2)."""
    await asyncio.sleep(seconds)


def after(seconds: float, callback: Callable, *args: Any) -> str:
    """Run *callback* once after *seconds*. Returns timer ID for cancellation."""
    timer_id = _next_timer_id()

    async def _run():
        try:
            await asyncio.sleep(seconds)
            result = callback(*args)
            if asyncio.iscoroutine(result):
                await result
        except asyncio.CancelledError:
            pass
        except Exception:  # Catch-all: isolates user callback errors from timer
            _log.exception(f"Error in after() timer {timer_id}")
        finally:
            _active_timers.pop(timer_id, None)

    _active_timers[timer_id] = asyncio.ensure_future(_run())
    return timer_id


def every(seconds: float, callback: Callable, *args: Any) -> str:
    """Run *callback* every *seconds*. Returns timer ID for cancellation."""
    timer_id = _next_timer_id()

    async def _run():
        try:
            while True:
                await asyncio.sleep(seconds)
                try:
                    result = callback(*args)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # Catch-all: isolates user callback errors from timer loop
                    _log.exception(f"Error in every() timer {timer_id}")
        except asyncio.CancelledError:
            pass
        finally:
            _active_timers.pop(timer_id, None)

    _active_timers[timer_id] = asyncio.ensure_future(_run())
    return timer_id


def cancel_timer(timer_id: str) -> bool:
    """Cancel a timer by ID. Returns True if cancelled, False if not found."""
    task = _active_timers.pop(timer_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False


def cancel_all_timers() -> int:
    """Cancel all active timers. Returns count cancelled."""
    count = 0
    for timer_id in list(_active_timers):
        if cancel_timer(timer_id):
            count += 1
    return count


# --- Configuration ---


def configure(
    device_manager: DeviceManager,
    state_store: StateStore,
    event_bus: EventBus,
    macro_engine: MacroEngine | None = None,
    isc_manager: Any = None,
) -> None:
    """Wire proxy objects to real subsystem instances. Called by ScriptEngine."""
    devices._bind(device_manager)
    state._bind(state_store)
    events._bind(event_bus)
    if macro_engine is not None:
        macros._bind(macro_engine)
    if isc_manager is not None:
        isc._bind(isc_manager)


def install_module() -> None:
    """Inject this module as 'openavc' into sys.modules."""
    sys.modules["openavc"] = sys.modules[__name__]
