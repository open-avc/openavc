"""
Scoped Plugin API — the plugin's only interface to the OpenAVC runtime.

Each plugin instance receives its own PluginAPI with:
- Capability enforcement (undeclared capabilities raise PluginPermissionError)
- Namespace isolation (state writes restricted to plugin.<id>.*)
- Event auto-prefixing (emitted events prefixed with plugin.<id>.)
- Automatic registration tracking for cleanup
"""

import asyncio
import uuid
from typing import Any, Callable, Coroutine

from server.utils.logger import get_logger

log = get_logger(__name__)


class PluginPermissionError(Exception):
    """Raised when a plugin attempts an action it hasn't declared."""


class PluginAPI:
    """
    Scoped API provided to each plugin instance.

    Method calls are gated by declared capabilities.
    All registrations (subscriptions, state keys, tasks) are tracked
    and automatically cleaned up on stop/uninstall.
    """

    def __init__(
        self,
        plugin_id: str,
        capabilities: list[str],
        config: dict[str, Any],
        registry,  # PluginRegistry
        state_store,  # StateStore
        event_bus,  # EventBus
        macro_engine,  # MacroEngine
        device_manager,  # DeviceManager
        platform_id: str,
        save_config_fn: Callable | None = None,
        log_fn: Callable | None = None,
        failure_reporter: Callable | None = None,
        success_reporter: Callable | None = None,
    ):
        self._plugin_id = plugin_id
        self._capabilities = set(capabilities)
        self._config = dict(config)
        self._registry = registry
        self._state = state_store
        self._events = event_bus
        self._macros = macro_engine
        self._devices = device_manager
        self._platform_id = platform_id
        self._save_config_fn = save_config_fn
        self._log_fn = log_fn
        self._failure_reporter = failure_reporter
        self._success_reporter = success_reporter
        self._periodic_tasks: dict[str, asyncio.Task] = {}

    def _require(self, capability: str) -> None:
        if capability not in self._capabilities:
            raise PluginPermissionError(
                f"Plugin '{self._plugin_id}' requires capability '{capability}' "
                f"but only declared: {sorted(self._capabilities)}"
            )

    # ──── State ────

    async def state_get(self, key: str) -> Any:
        """Read any state key. Requires: state_read."""
        self._require("state_read")
        return self._state.get(key)

    async def state_get_pattern(self, pattern: str) -> dict[str, Any]:
        """Read all state keys matching a glob pattern. Requires: state_read."""
        self._require("state_read")
        return self._state.get_matching(pattern)

    async def state_set(self, key: str, value: Any) -> None:
        """Set a state key. Requires: state_write.

        Plugins can ONLY set keys in: plugin.<plugin_id>.*
        Values must be flat primitives (str, int, float, bool, None).
        """
        self._require("state_write")

        # Namespace enforcement — auto-prefix if bare key given
        prefix = f"plugin.{self._plugin_id}."
        if not key.startswith(prefix):
            key = f"{prefix}{key}"

        # Flat primitive enforcement
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise PluginPermissionError(
                f"Plugin state values must be flat primitives, got {type(value).__name__}"
            )

        self._state.set(key, value, source=f"plugin.{self._plugin_id}")
        self._registry.track_state_key(key)

    async def variable_set(self, variable_id: str, value: Any) -> None:
        """Set a user-defined variable value. Requires: variable_write.

        Writes to var.<variable_id> in the state store. User variables are
        shared room-logic state, so writing to them is gated by a separate
        capability from plugin-namespace state_write.
        """
        self._require("variable_write")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise PluginPermissionError(
                f"Variable values must be flat primitives, got {type(value).__name__}"
            )
        key = f"var.{variable_id}"
        self._state.set(key, value, source=f"plugin.{self._plugin_id}")

    async def state_subscribe(self, pattern: str, callback: Callable) -> str:
        """Subscribe to state changes matching a glob pattern. Requires: state_read.

        Callback: async (key: str, value: Any, old_value: Any) -> None
        Returns subscription ID. Automatically unsubscribed on stop.
        """
        self._require("state_read")

        # Wrap to match internal StateStore signature (key, old_value, new_value, source)
        # and provide the plugin-facing signature (key, value, old_value)
        failure_reporter = self._failure_reporter
        success_reporter = self._success_reporter

        async def _wrapper(key: str, old_value: Any, new_value: Any, source: str) -> None:
            try:
                result = callback(key, new_value, old_value)
                if asyncio.iscoroutine(result):
                    await result
                if success_reporter:
                    success_reporter()
            except Exception:  # Catch-all: isolates plugin callback errors from engine
                log.exception(
                    f"Plugin '{self._plugin_id}' state callback error for key '{key}'"
                )
                if failure_reporter:
                    failure_reporter()

        sub_id = self._state.subscribe(pattern, _wrapper)
        self._registry.track_state_subscription(sub_id)
        return sub_id

    async def state_unsubscribe(self, subscription_id: str) -> None:
        """Remove a state subscription."""
        self._state.unsubscribe(subscription_id)
        try:
            self._registry.state_subscriptions.remove(subscription_id)
        except ValueError:
            pass

    # ──── Events ────

    async def event_emit(self, event_name: str, payload: dict | None = None) -> None:
        """Emit an event. Requires: event_emit.

        Auto-prefixed: plugin.<plugin_id>.<event_name>
        """
        self._require("event_emit")
        full_event = f"plugin.{self._plugin_id}.{event_name}"
        await self._events.emit(full_event, payload)

    async def event_subscribe(self, pattern: str, callback: Callable) -> str:
        """Subscribe to events matching a glob. Requires: event_subscribe.

        Callback: async (event_name: str, payload: dict) -> None
        Can subscribe to ANY event (not just plugin events).
        Automatically unsubscribed on stop.
        """
        self._require("event_subscribe")

        failure_reporter = self._failure_reporter
        success_reporter = self._success_reporter

        async def _wrapper(event_name: str, payload: dict[str, Any] | None) -> None:
            try:
                result = callback(event_name, payload or {})
                if asyncio.iscoroutine(result):
                    await result
                if success_reporter:
                    success_reporter()
            except Exception:  # Catch-all: isolates plugin callback errors from engine
                log.exception(
                    f"Plugin '{self._plugin_id}' event callback error for '{event_name}'"
                )
                if failure_reporter:
                    failure_reporter()

        handler_id = self._events.on(pattern, _wrapper)
        self._registry.track_event_subscription(handler_id)
        return handler_id

    async def event_unsubscribe(self, subscription_id: str) -> None:
        """Remove an event subscription."""
        self._events.off(subscription_id)
        try:
            self._registry.event_subscriptions.remove(subscription_id)
        except ValueError:
            pass

    # ──── Actions ────

    async def macro_execute(self, macro_id: str) -> None:
        """Execute a macro by ID. Requires: macro_execute."""
        self._require("macro_execute")
        await self._macros.execute(macro_id)

    async def device_command(
        self, device_id: str, command: str, params: dict | None = None
    ) -> Any:
        """Send a command to a device. Requires: device_command."""
        self._require("device_command")
        return await self._devices.send_command(device_id, command, params)

    # ──── Background Tasks ────

    def create_task(self, coro: Coroutine, name: str | None = None) -> asyncio.Task:
        """Create a managed background task. Automatically cancelled on stop."""
        task_name = f"plugin.{self._plugin_id}.{name or 'task'}"

        async def _safe_wrapper():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception:  # Catch-all: isolates plugin task errors from engine
                log.exception(f"Plugin '{self._plugin_id}' task '{task_name}' failed")

        task = asyncio.create_task(_safe_wrapper(), name=task_name)
        self._registry.track_task(task)

        def _on_done(t: asyncio.Task):
            self._registry.untrack_task(t)

        task.add_done_callback(_on_done)
        return task

    def create_periodic_task(
        self, coro_fn: Callable, interval_seconds: float, name: str | None = None
    ) -> str:
        """Create a repeating background task. Calls coro_fn() every interval.

        Automatically cancelled on stop. Returns task ID.
        """
        task_id = f"periodic_{uuid.uuid4().hex[:8]}"

        async def _periodic_loop():
            while True:
                try:
                    result = coro_fn()
                    if asyncio.iscoroutine(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:  # Catch-all: isolates plugin periodic task errors
                    log.exception(
                        f"Plugin '{self._plugin_id}' periodic task '{name or task_id}' error"
                    )
                await asyncio.sleep(interval_seconds)

        task = asyncio.create_task(
            _periodic_loop(),
            name=f"plugin.{self._plugin_id}.{name or task_id}",
        )
        self._registry.track_task(task)
        self._registry.track_periodic_task(task_id)
        self._periodic_tasks[task_id] = task

        def _on_done(t: asyncio.Task):
            self._registry.untrack_task(t)
            self._periodic_tasks.pop(task_id, None)

        task.add_done_callback(_on_done)
        return task_id

    def cancel_task(self, task_id: str) -> None:
        """Cancel a managed periodic task by ID."""
        task = self._periodic_tasks.pop(task_id, None)
        if task and not task.done():
            task.cancel()

    # ──── Configuration ────

    @property
    def config(self) -> dict:
        """This plugin's saved configuration (from the project file). Read-only."""
        return dict(self._config)

    async def save_config(self, config: dict) -> None:
        """Save updated configuration to the project file."""
        self._config = dict(config)
        if self._save_config_fn:
            await self._save_config_fn(self._plugin_id, config)

    # ──── Identity & Logging ────

    @property
    def plugin_id(self) -> str:
        """This plugin's unique ID."""
        return self._plugin_id

    @property
    def platform(self) -> str:
        """Current platform identifier (win_x64, linux_x64, linux_arm64, etc.)."""
        return self._platform_id

    _VALID_LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}

    def log(self, message: str, level: str = "info") -> None:
        """Log a message. Appears in System Log with plugin name as source."""
        if level not in self._VALID_LOG_LEVELS:
            level = "info"
        if self._log_fn:
            self._log_fn(self._plugin_id, message, level)
        else:
            getattr(log, level, log.info)(f"[Plugin:{self._plugin_id}] {message}")
