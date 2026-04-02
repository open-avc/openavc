"""
OpenAVC ScriptEngine — loads user Python scripts with hot-reload.

Scripts live in a project's scripts/ directory and use decorators from
the 'openavc' module (actually server.core.script_api injected into
sys.modules).

Lifecycle:
1. install() — inject the openavc shim into sys.modules
2. configure() — wire proxy objects to real subsystem instances
3. load_scripts() — import each script file, drain pending handlers,
   wrap with error protection, register on EventBus/StateStore
4. reload_scripts() — unregister old handlers, re-import, re-register
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import sys
import traceback
import types
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from server.core import script_api
from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus
    from server.core.macro_engine import MacroEngine
    from server.core.state_store import StateStore

log = get_logger(__name__)


class ScriptEngine:
    """Loads user scripts, registers their decorated handlers, supports hot-reload."""

    def __init__(
        self,
        state: StateStore,
        events: EventBus,
        devices: DeviceManager,
        project_dir: Path,
        macro_engine: MacroEngine | None = None,
    ):
        self.state = state
        self.events = events
        self.devices = devices
        self.macro_engine = macro_engine
        self.project_dir = project_dir
        self.scripts_dir = project_dir / "scripts"

        # Tracking for unregistration on reload
        self._event_handler_ids: list[str] = []
        self._state_sub_ids: list[str] = []
        self._loaded_modules: dict[str, str] = {}  # script_id -> module_name

    def install(self) -> None:
        """Inject the openavc shim into sys.modules and wire proxies."""
        script_api.install_module()
        script_api.configure(self.devices, self.state, self.events, self.macro_engine)
        log.info("Script API installed as 'openavc' module")

    def load_scripts(self, scripts: list[dict[str, Any]]) -> int:
        """
        Load script files and register their handlers.

        Args:
            scripts: List of script config dicts with 'id', 'file', 'enabled' keys.

        Returns:
            Number of handlers registered.
        """
        handler_count = 0
        for script_cfg in scripts:
            if not script_cfg.get("enabled", True):
                log.info(f"Script '{script_cfg['id']}' is disabled, skipping")
                continue

            script_file = script_cfg["file"]
            script_id = script_cfg["id"]
            script_path = self.scripts_dir / script_file

            if not script_path.exists():
                log.error(f"Script file not found: {script_path}")
                continue

            try:
                count = self._load_single_script(script_id, script_path)
                handler_count += count
                log.info(
                    f"Loaded script '{script_id}' ({script_file}) — "
                    f"{count} handler(s)"
                )
            except Exception:  # Catch-all: loading user scripts can raise anything
                log.exception(f"Failed to load script '{script_id}' ({script_file})")

        if handler_count:
            log.info(f"ScriptEngine: {handler_count} total handler(s) registered")
        return handler_count

    # Timeout for script top-level execution (seconds)
    SCRIPT_LOAD_TIMEOUT = 10

    def _load_single_script(self, script_id: str, script_path: Path) -> int:
        """Import a single script file and drain its handlers."""
        module_name = f"openavc.user_scripts.{script_id}"

        # Clear any stale pending handlers
        script_api.drain_pending()

        # Remove old module if reloading
        sys.modules.pop(module_name, None)

        # Read source and exec directly (bypasses .pyc caching for hot-reload)
        source = script_path.read_text(encoding="utf-8")
        code = compile(source, str(script_path), "exec")

        module = types.ModuleType(module_name)
        module.__file__ = str(script_path)
        sys.modules[module_name] = module

        # Run exec in a thread with a timeout to prevent infinite loops from
        # blocking the event loop. This protects against `while True: pass` etc.
        def _exec_script():
            exec(code, module.__dict__)

        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"script-{script_id}",
        )
        try:
            future = pool.submit(_exec_script)
            try:
                future.result(timeout=self.SCRIPT_LOAD_TIMEOUT)
            except concurrent.futures.TimeoutError:
                future.cancel()
                sys.modules.pop(module_name, None)
                raise RuntimeError(
                    f"Script '{script_id}' timed out during loading "
                    f"(>{self.SCRIPT_LOAD_TIMEOUT}s) — possible infinite loop "
                    f"in top-level code"
                )
        finally:
            pool.shutdown(wait=True)

        self._loaded_modules[script_id] = module_name

        # Drain and register handlers
        event_handlers, state_handlers = script_api.drain_pending()
        count = 0

        for pattern, handler in event_handlers:
            wrapped = self._wrap_event_handler(handler, script_id)
            handler_id = self.events.on(pattern, wrapped)
            self._event_handler_ids.append(handler_id)
            count += 1

        for pattern, handler in state_handlers:
            wrapped = self._wrap_state_handler(handler, script_id)
            sub_id = self.state.subscribe(pattern, wrapped)
            self._state_sub_ids.append(sub_id)
            count += 1

        return count

    def unload_all(self) -> None:
        """Unregister all handlers and remove loaded modules."""
        for hid in self._event_handler_ids:
            self.events.off(hid)
        for sid in self._state_sub_ids:
            self.state.unsubscribe(sid)

        # Remove script modules from sys.modules
        for module_name in self._loaded_modules.values():
            sys.modules.pop(module_name, None)

        count = len(self._event_handler_ids) + len(self._state_sub_ids)
        self._event_handler_ids.clear()
        self._state_sub_ids.clear()
        self._loaded_modules.clear()

        # Cancel all dynamic timers
        timer_count = script_api.cancel_all_timers()
        if timer_count:
            log.info(f"Cancelled {timer_count} active timer(s)")

        if count:
            log.info(f"Unloaded {count} handler(s)")

    def get_callable_functions(self) -> list[dict[str, str]]:
        """Return all callable functions from loaded scripts.

        Returns a list of dicts: {"script": script_id, "function": name, "doc": docstring}.
        Excludes private functions (starting with _) and decorated event/state handlers.
        """
        import inspect

        results: list[dict[str, str]] = []
        for script_id, module_name in self._loaded_modules.items():
            module = sys.modules.get(module_name)
            if not module:
                continue
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                # Skip private, dunder, and imported stdlib functions
                if name.startswith("_"):
                    continue
                if getattr(obj, "__module__", "") != module_name:
                    continue
                results.append({
                    "script": script_id,
                    "function": name,
                    "doc": (inspect.getdoc(obj) or "")[:200],
                })
        return results

    def reload_scripts(self, scripts: list[dict[str, Any]]) -> int:
        """Hot-reload: unload everything, then re-load scripts."""
        log.info("Reloading scripts...")
        self.unload_all()
        return self.load_scripts(scripts)

    def _wrap_event_handler(
        self, handler: Callable, script_id: str
    ) -> Callable:
        """Wrap an event handler with error protection and Event object support.

        Detects handler param count via inspect.signature():
        - 1 param: pass Event object
        - 2 params: pass (event_str, payload_dict) for backward compat
        """
        # Detect handler signature
        try:
            sig = inspect.signature(handler)
            param_count = len(sig.parameters)
        except (ValueError, TypeError):
            param_count = 2  # default to legacy signature

        events_ref = self.events

        async def wrapped(event: str, payload: dict[str, Any]) -> None:
            try:
                if param_count == 1:
                    from server.core.script_api import Event
                    evt = Event(event, payload)
                    result = handler(evt)
                else:
                    result = handler(event, payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # Catch-all: isolates user script errors from engine
                handler_name = getattr(handler, "__name__", "anonymous")
                log.exception(
                    f"Error in script '{script_id}' event handler "
                    f"for '{event}'"
                )
                try:
                    await events_ref.emit("script.error", {
                        "script_id": script_id,
                        "handler": handler_name,
                        "event": event,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    })
                except Exception:  # Catch-all: error event emission must not raise
                    pass

        wrapped.__name__ = getattr(handler, "__name__", "anonymous")
        wrapped.__qualname__ = f"{script_id}.{wrapped.__name__}"
        return wrapped

    def _wrap_state_handler(
        self, handler: Callable, script_id: str
    ) -> Callable:
        """Wrap a state-change handler with error protection.

        StateStore listeners receive (key, old_value, new_value, source).
        User handlers receive (key, old_value, new_value) — source is omitted
        for simplicity.
        """
        events_ref = self.events

        def wrapped(key: str, old_value: Any, new_value: Any, source: str) -> None:
            try:
                result = handler(key, old_value, new_value)
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                    except RuntimeError:
                        pass
            except Exception as exc:  # Catch-all: isolates user script errors from engine
                handler_name = getattr(handler, "__name__", "anonymous")
                log.exception(
                    f"Error in script '{script_id}' state handler "
                    f"for '{key}'"
                )
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(events_ref.emit("script.error", {
                        "script_id": script_id,
                        "handler": handler_name,
                        "event": f"state_change:{key}",
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }))
                except RuntimeError:
                    pass

        wrapped.__name__ = getattr(handler, "__name__", "anonymous")
        wrapped.__qualname__ = f"{script_id}.{wrapped.__name__}"
        return wrapped
