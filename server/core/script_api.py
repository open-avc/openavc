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
import contextlib
import sys
import traceback
from contextvars import ContextVar
from typing import Any, Callable, TYPE_CHECKING

from server.core.condition_eval import eval_operator
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


# --- Shared comparison ---


def compare(actual: Any, operator: str, target: Any = None) -> bool:
    """Compare two values with the same operator semantics macros and
    triggers use: operator aliases (``==``, ``>=``, ``equals``, ...) plus
    boolean/numeric type coercion, so ``compare("75", "gte", 50)`` is True
    and a type mismatch yields False instead of raising TypeError.

    Operators: eq, ne, gt, lt, gte, lte, truthy, falsy (plus aliases).
    For truthy/falsy the *target* argument is ignored.
    """
    return eval_operator(operator, actual, target)


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
        # Carry the macro call chain across the script boundary: a handler
        # task spawned by a running macro's steps inherits that macro's
        # chain via context, so a script that re-enters the same macro is
        # caught by the engine's circular/depth guards instead of resetting
        # them. Outside any macro this is an empty chain (normal behavior).
        from server.core.macro_engine import active_call_chain
        await self._engine.execute(macro_id, _call_chain=active_call_chain())


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


class _PluginProxy:
    """Per-plugin facade exposing methods registered via SCRIPT_API.

    Returned by ``plugins.<plugin_id>``. Methods are bound directly to the
    live plugin instance, so calling ``plugins.audio_player.play(...)``
    invokes the plugin's handler with whatever capabilities it declared.
    """

    def __init__(self, plugin_id: str):
        # Use object.__setattr__ to bypass our own __setattr__ guard.
        object.__setattr__(self, "_plugin_id", plugin_id)
        object.__setattr__(self, "_methods", {})

    def _register(self, name: str, handler: Callable) -> None:
        self._methods[name] = handler

    def _unregister(self, name: str) -> None:
        self._methods.pop(name, None)

    def _clear(self) -> None:
        self._methods.clear()

    def __getattr__(self, name: str) -> Callable:
        # Only called if normal attribute lookup fails — _methods etc. are
        # on the instance dict so they hit before this.
        try:
            return self._methods[name]
        except KeyError:
            available = sorted(self._methods)
            hint = (
                f" Available: {available}"
                if available
                else " (the plugin is installed but has no SCRIPT_API methods)"
            )
            raise AttributeError(
                f"Plugin '{self._plugin_id}' has no script method '{name}'.{hint}"
            )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Cannot assign to plugins.{self._plugin_id}.{name} — "
            f"plugin script methods are read-only"
        )

    def __dir__(self) -> list[str]:
        return sorted(self._methods)


class _PluginsProxy:
    """Registry of running plugins exposed under ``openavc.plugins``.

    Scripts call ``openavc.plugins.<plugin_id>.<method>(...)`` to invoke
    a method registered by the plugin's SCRIPT_API.
    """

    def __init__(self):
        object.__setattr__(self, "_plugins", {})

    def _get_or_create(self, plugin_id: str) -> _PluginProxy:
        proxy = self._plugins.get(plugin_id)
        if proxy is None:
            proxy = _PluginProxy(plugin_id)
            self._plugins[plugin_id] = proxy
        return proxy

    def _register_method(self, plugin_id: str, name: str, handler: Callable) -> None:
        self._get_or_create(plugin_id)._register(name, handler)

    def _unregister_plugin(self, plugin_id: str) -> None:
        proxy = self._plugins.get(plugin_id)
        if proxy is not None:
            proxy._clear()

    def __getattr__(self, plugin_id: str) -> _PluginProxy:
        try:
            proxy = self._plugins[plugin_id]
        except KeyError:
            raise AttributeError(
                f"Plugin '{plugin_id}' is not installed or not currently running"
            )
        if not proxy._methods:
            raise AttributeError(
                f"Plugin '{plugin_id}' is not currently running "
                f"(or has no SCRIPT_API methods)"
            )
        return proxy

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Cannot assign to plugins.{name} — the plugin proxy is read-only"
        )

    def __dir__(self) -> list[str]:
        return sorted(pid for pid, proxy in self._plugins.items() if proxy._methods)


# Singleton proxy instances (scripts import these directly)
devices = _DeviceProxy()
state = _StateProxy()
events = _EventProxy()
macros = _MacroProxy()
log = _LogProxy()
isc = _ISCProxy()
plugins = _PluginsProxy()

# --- Current-script context ---

# Set by ScriptEngine around each handler invocation (and during load) so the
# timer functions below can attribute a timer to the script that created it.
# This is what lets a single script be hot-reloaded without disturbing every
# other script's timers.
_current_script: ContextVar[str | None] = ContextVar("openavc_current_script", default=None)


@contextlib.contextmanager
def current_script_context(script_id: str | None):
    """Bind the current script id for the duration of a handler/timer call."""
    token = _current_script.set(script_id)
    try:
        yield
    finally:
        _current_script.reset(token)


# --- Timer functions ---

_active_timers: dict[str, asyncio.Task] = {}
# Timers created during a script's top-level load run in a worker thread with no
# running event loop, so they can't be scheduled immediately. They're parked
# here and materialized onto the loop by ScriptEngine once load completes.
_pending_timers: dict[str, Callable[[], Any]] = {}
# timer_id -> owning script_id (for per-script cancellation on reload).
_timer_owners: dict[str, str | None] = {}
_timer_counter = 0


def _next_timer_id() -> str:
    global _timer_counter
    _timer_counter += 1
    return f"timer_{_timer_counter}"


def _forget_timer(timer_id: str) -> None:
    """Drop a timer's bookkeeping from both the active and ownership maps."""
    _active_timers.pop(timer_id, None)
    _timer_owners.pop(timer_id, None)


def _register_timer(timer_id: str, coro_factory: Callable[[], Any]) -> None:
    """Schedule a timer now if a loop is running, else defer it to load-drain.

    Handlers run on the event loop thread, so their ``after``/``every`` calls
    schedule immediately and are attributed to the current script. Top-level
    ``after``/``every`` calls run in the load worker thread (no running loop),
    so they're parked in ``_pending_timers`` for ``materialize_pending_timers``
    to schedule on the loop after the script finishes importing.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _pending_timers[timer_id] = coro_factory
        return
    _active_timers[timer_id] = asyncio.ensure_future(coro_factory())
    _timer_owners[timer_id] = _current_script.get()


def materialize_pending_timers(script_id: str | None) -> int:
    """Schedule timers deferred during a script's load onto the running loop.

    Called by ScriptEngine on the loop thread right after a script imports, so
    the documented top-level ``every()``/``after()`` pattern works even though
    top-level code executes off the loop. Returns the number materialized.
    """
    if not _pending_timers:
        return 0
    count = 0
    for timer_id, factory in list(_pending_timers.items()):
        _pending_timers.pop(timer_id, None)
        try:
            _active_timers[timer_id] = asyncio.ensure_future(factory())
        except RuntimeError:
            # No running loop (sync test harness) — nothing to schedule on.
            continue
        _timer_owners[timer_id] = script_id
        count += 1
    return count


def discard_pending_timers() -> None:
    """Drop any timers parked during a load that failed (so they never run)."""
    _pending_timers.clear()


async def delay(seconds: float) -> None:
    """Async sleep — use inside async handlers: await delay(2)."""
    await asyncio.sleep(seconds)


async def _emit_timer_error(
    timer_id: str, handler_name: str, error: str, tb: str,
) -> None:
    """Emit ``script.error`` for a timer callback failure; never raises."""
    try:
        await events.emit("script.error", {
            "script_id": _timer_owners.get(timer_id) or "",
            "handler": handler_name,
            "timer_id": timer_id,
            "error": error,
            "traceback": tb,
        })
    except Exception:  # Catch-all: error event emission must not raise
        pass


async def _run_timer_callback(
    timer_id: str, kind: str, callback: Callable, args: tuple,
) -> None:
    """Run one timer callback with the same protections event handlers get:
    async bodies are bounded by the handler timeout, and any failure is
    re-emitted as ``script.error`` instead of vanishing into the log.

    Synchronous callbacks run inline on the event loop — same documented
    constraint as sync event handlers (the state/devices/events proxies
    assume the loop thread, so thread offload would break them). Keep timer
    callbacks short or make them async.
    """
    from server.core.script_engine import ScriptEngine

    handler_name = getattr(callback, "__name__", "anonymous")
    try:
        result = callback(*args)
        if asyncio.iscoroutine(result):
            await asyncio.wait_for(result, timeout=ScriptEngine.HANDLER_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        msg = (
            f"{kind}() callback '{handler_name}' timed out after "
            f"{ScriptEngine.HANDLER_TIMEOUT}s (timer {timer_id})"
        )
        _log.error(msg)
        await _emit_timer_error(timer_id, handler_name, msg, "")
    except Exception as exc:  # Catch-all: isolates user callback errors from timer
        _log.exception(f"Error in {kind}() timer {timer_id}")
        await _emit_timer_error(
            timer_id, handler_name, str(exc), traceback.format_exc()
        )


def after(seconds: float, callback: Callable, *args: Any) -> str:
    """Run *callback* once after *seconds*. Returns timer ID for cancellation."""
    timer_id = _next_timer_id()

    async def _run():
        try:
            await asyncio.sleep(seconds)
            await _run_timer_callback(timer_id, "after", callback, args)
        except asyncio.CancelledError:
            pass
        finally:
            _forget_timer(timer_id)

    _register_timer(timer_id, _run)
    return timer_id


def every(seconds: float, callback: Callable, *args: Any) -> str:
    """Run *callback* every *seconds*. Returns timer ID for cancellation."""
    timer_id = _next_timer_id()

    async def _run():
        try:
            while True:
                await asyncio.sleep(seconds)
                # _run_timer_callback isolates callback errors, so one bad
                # tick never stops the interval loop.
                await _run_timer_callback(timer_id, "every", callback, args)
        except asyncio.CancelledError:
            pass
        finally:
            _forget_timer(timer_id)

    _register_timer(timer_id, _run)
    return timer_id


def cancel_timer(timer_id: str) -> bool:
    """Cancel a timer by ID. Returns True if cancelled, False if not found."""
    # A timer parked at load time but not yet materialized: drop it so it
    # never starts.
    if _pending_timers.pop(timer_id, None) is not None:
        _timer_owners.pop(timer_id, None)
        return True
    task = _active_timers.pop(timer_id, None)
    _timer_owners.pop(timer_id, None)
    if task and not task.done():
        task.cancel()
        return True
    return False


def cancel_all_timers() -> int:
    """Cancel all active and pending timers. Returns count cancelled."""
    count = 0
    for timer_id in list(_pending_timers):
        if cancel_timer(timer_id):
            count += 1
    for timer_id in list(_active_timers):
        if cancel_timer(timer_id):
            count += 1
    return count


def cancel_script_timers(script_id: str) -> int:
    """Cancel only the timers owned by *script_id*. Returns count cancelled.

    Used by per-script hot-reload so reloading one script leaves every other
    script's timers (and their ``every()`` phase) running untouched.
    """
    count = 0
    for timer_id, owner in list(_timer_owners.items()):
        if owner == script_id and cancel_timer(timer_id):
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
