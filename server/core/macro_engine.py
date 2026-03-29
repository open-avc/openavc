"""
OpenAVC MacroEngine — executes named sequences of actions.

Macros are the bridge between the visual configurator and scripting.
They are ordered sequences of steps: send device commands, set state,
add delays, emit events, or call other macros.
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.device_manager import DeviceManager

log = get_logger(__name__)


class MacroEngine:
    """Executes named macros — ordered sequences of actions with optional delays."""

    def __init__(
        self, state: StateStore, events: EventBus, devices: DeviceManager
    ):
        self.state = state
        self.events = events
        self.devices = devices
        self._macros: dict[str, dict[str, Any]] = {}  # id -> macro config
        self._running: dict[str, asyncio.Task] = {}  # id -> running task
        self._call_stack: set[str] = set()  # macro IDs currently executing (recursion guard)
        self._call_stack_lock = asyncio.Lock()  # serialize recursion checks
        self._max_depth = 10  # maximum nested macro call depth

    def is_macro_running(self, macro_id: str) -> bool:
        """Check if a macro is currently running."""
        return macro_id in self._running

    def load_macros(self, macros: list[dict[str, Any]]) -> None:
        """Register macro definitions from the project config."""
        self._macros.clear()
        for macro in macros:
            macro_id = macro.get("id", "")
            if macro_id:
                self._macros[macro_id] = macro
        log.info(f"Loaded {len(self._macros)} macro(s)")

    async def execute(self, macro_id: str, context: dict[str, Any] | None = None) -> None:
        """
        Execute a macro by ID.

        Args:
            macro_id: The macro to execute.
            context: Optional context dict passed through to steps.
        """
        macro = self._macros.get(macro_id)
        if macro is None:
            log.error(f"Macro '{macro_id}' not found")
            return

        # Recursion guard: prevent self-referencing or circular macro calls
        # Hold lock through the guard check AND adding to call_stack + starting
        # execution, to prevent two concurrent calls from both passing the check
        async with self._call_stack_lock:
            if macro_id in self._call_stack:
                log.error(
                    f"Macro '{macro_id}' blocked — circular/recursive call detected "
                    f"(call stack: {' -> '.join(self._call_stack)} -> {macro_id})"
                )
                return
            if len(self._call_stack) >= self._max_depth:
                log.error(
                    f"Macro '{macro_id}' blocked — max nesting depth ({self._max_depth}) reached"
                )
                return
            self._call_stack.add(macro_id)

            name = macro.get("name", macro_id)
            steps = macro.get("steps", [])
            stop_on_error = macro.get("stop_on_error", False)

        log.info(f"Executing macro '{name}' ({len(steps)} steps)")
        task = asyncio.current_task()
        if task is not None:
            self._running[macro_id] = task
        await self.events.emit(
            f"macro.started.{macro_id}",
            {"macro_id": macro_id, "name": name, "total_steps": len(steps)},
        )

        try:
            await self.execute_steps(steps, context, macro_id, stop_on_error)
            await self.events.emit(
                f"macro.completed.{macro_id}",
                {"macro_id": macro_id, "name": name},
            )
            log.info(f"Macro '{name}' completed")
        except Exception:  # Catch-all: isolates macro execution errors
            log.exception(f"Macro '{name}' failed")
            await self.events.emit(
                f"macro.error.{macro_id}",
                {"macro_id": macro_id, "name": name},
            )
        finally:
            async with self._call_stack_lock:
                self._call_stack.discard(macro_id)
            self._running.pop(macro_id, None)

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        macro_id: str | None = None,
        stop_on_error: bool = False,
    ) -> None:
        """
        Execute a list of steps sequentially.

        Each step is wrapped in try/except — errors are logged but
        execution continues to the next step (unless stop_on_error is True).
        """
        context = context or {}
        total = len(steps)

        for i, step in enumerate(steps):
            action = step.get("action", "")

            # Emit progress
            if macro_id:
                await self.events.emit(
                    f"macro.progress.{macro_id}",
                    {
                        "macro_id": macro_id,
                        "step_index": i,
                        "total_steps": total,
                        "action": action,
                        "status": "running",
                    },
                )

            try:
                await self._execute_step(step, context)
            except Exception:  # Catch-all: isolates individual step errors from halting the macro
                log.exception(
                    f"Error in macro step {i + 1}/{total}: {action}"
                )
                if stop_on_error:
                    raise
                # Continue to next step (don't halt the macro)

    async def _execute_step(
        self, step: dict[str, Any], context: dict[str, Any]
    ) -> None:
        """Execute a single macro step."""
        action = step.get("action", "")

        if action == "device.command":
            device_id = step.get("device", "")
            command = step.get("command", "")
            params = step.get("params") or {}
            log.debug(f"  Macro step: {device_id}.{command}({params})")
            await self.devices.send_command(device_id, command, params)

        elif action == "delay":
            seconds = max(0, step.get("seconds", 0))
            log.debug(f"  Macro step: delay {seconds}s")
            await asyncio.sleep(seconds)

        elif action == "state.set":
            key = step.get("key", "")
            value = step.get("value")
            log.debug(f"  Macro step: state.set {key} = {value!r}")
            self.state.set(key, value, source="macro")

        elif action == "macro":
            sub_macro_id = step.get("macro", "")
            log.debug(f"  Macro step: call macro '{sub_macro_id}'")
            await self.execute(sub_macro_id, context)

        elif action == "event.emit":
            event_name = step.get("event", "")
            payload = step.get("payload") or {}
            log.debug(f"  Macro step: emit '{event_name}'")
            await self.events.emit(event_name, payload)

        else:
            log.warning(f"  Unknown macro action: {action}")
