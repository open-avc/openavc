"""
OpenAVC MacroEngine — executes named sequences of actions.

Macros are the bridge between the visual configurator and scripting.
They are ordered sequences of steps: send device commands, set state,
add delays, emit events, or call other macros.
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from server.core.condition_eval import eval_operator
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
        self._groups: dict[str, list[str]] = {}  # group_id -> [device_ids]
        self._running: dict[str, asyncio.Task] = {}  # id -> running task
        self._call_stack: set[str] = set()  # macro IDs currently executing (recursion guard)
        self._call_stack_lock = asyncio.Lock()  # serialize recursion checks
        self._max_depth = 10  # maximum nested macro call depth
        self._max_conditional_depth = 5  # maximum nesting of conditional steps

    def is_macro_running(self, macro_id: str) -> bool:
        """Check if a macro is currently running."""
        return macro_id in self._running

    async def cancel(self, macro_id: str) -> bool:
        """Cancel a running macro. Returns True if cancelled, False if not running."""
        task = self._running.get(macro_id)
        if task is None:
            return False
        task.cancel()
        # Wait for the task to finish its cancellation cleanup
        try:
            await asyncio.shield(asyncio.sleep(0))
        except asyncio.CancelledError:
            pass
        return True

    async def cancel_all(self) -> None:
        """Cancel all running macros (for system shutdown)."""
        for macro_id in list(self._running):
            await self.cancel(macro_id)

    async def _cancel_group(self, group: str, exclude_macro_id: str) -> None:
        """Cancel all running macros in the same cancel_group, except the one starting."""
        to_cancel = []
        for mid, task in self._running.items():
            if mid == exclude_macro_id:
                continue
            macro_config = self._macros.get(mid, {})
            if macro_config.get("cancel_group") == group:
                to_cancel.append((mid, task))
        for mid, task in to_cancel:
            log.info(f"Preempting macro '{mid}' (cancel_group '{group}')")
            task.cancel()
        # Give cancelled tasks a chance to clean up
        if to_cancel:
            await asyncio.sleep(0)

    def load_macros(self, macros: list[dict[str, Any]]) -> None:
        """Register macro definitions from the project config."""
        self._macros.clear()
        for macro in macros:
            macro_id = macro.get("id", "")
            if macro_id:
                self._macros[macro_id] = macro
        log.info(f"Loaded {len(self._macros)} macro(s)")

    def load_groups(self, groups: list[dict[str, Any]]) -> None:
        """Register device group definitions from the project config."""
        self._groups.clear()
        for group in groups:
            group_id = group.get("id", "")
            if group_id:
                self._groups[group_id] = group.get("device_ids", [])
        if self._groups:
            log.info(f"Loaded {len(self._groups)} device group(s)")

    async def execute(self, macro_id: str, context: dict[str, Any] | None = None) -> None:
        """
        Execute a macro by ID.

        Args:
            macro_id: The macro to execute.
            context: Optional context dict passed through to steps.
        """
        macro = self._macros.get(macro_id)
        if macro is None:
            raise ValueError(f"Macro '{macro_id}' not found")

        # Recursion guard: prevent self-referencing or circular macro calls
        # Hold lock through the guard check AND adding to call_stack + starting
        # execution, to prevent two concurrent calls from both passing the check
        async with self._call_stack_lock:
            if macro_id in self._call_stack:
                raise ValueError(
                    f"Macro '{macro_id}' blocked — circular/recursive call detected "
                    f"(call stack: {' -> '.join(self._call_stack)} -> {macro_id})"
                )
            if len(self._call_stack) >= self._max_depth:
                raise ValueError(
                    f"Macro '{macro_id}' blocked — max nesting depth ({self._max_depth}) reached"
                )
            self._call_stack.add(macro_id)

            name = macro.get("name", macro_id)
            steps = macro.get("steps", [])
            stop_on_error = macro.get("stop_on_error", False)
            cancel_group = macro.get("cancel_group")

        # Cancel group preemption: cancel other running macros in the same group
        if cancel_group:
            await self._cancel_group(cancel_group, macro_id)

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
        except asyncio.CancelledError:
            log.info(f"Macro '{name}' was cancelled")
            await self.events.emit(
                f"macro.cancelled.{macro_id}",
                {"macro_id": macro_id, "name": name},
            )
        except Exception as e:  # Catch-all: isolates macro execution errors
            log.exception(f"Macro '{name}' failed")
            await self.events.emit(
                f"macro.error.{macro_id}",
                {"macro_id": macro_id, "name": name, "error": str(e)},
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
        _conditional_depth: int = 0,
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
                        "description": step.get("description") or self._auto_description(step),
                        "status": "running",
                    },
                )

            try:
                await self._execute_step(step, context, _conditional_depth, macro_id)
            except Exception as e:  # Catch-all: isolates individual step errors from halting the macro
                step_detail = self._step_error_detail(step, i, total)
                log.error(f"Macro step failed: {step_detail} — {e}")
                # Emit step-level error event so the frontend can show it
                if macro_id:
                    await self.events.emit(
                        f"macro.step_error.{macro_id}",
                        {
                            "macro_id": macro_id,
                            "step_index": i,
                            "total_steps": total,
                            "action": action,
                            "device": step.get("device", ""),
                            "group": step.get("group", ""),
                            "command": step.get("command", ""),
                            "error": str(e),
                            "description": step.get("description") or self._auto_description(step),
                        },
                    )
                if stop_on_error:
                    raise RuntimeError(
                        f"Step {i + 1}/{total} failed ({step_detail}): {e}"
                    ) from e
                # Continue to next step (don't halt the macro)

    def _evaluate_condition(self, condition: dict[str, Any]) -> bool:
        """Evaluate a step condition against current state."""
        key = condition.get("key", "")
        op = condition.get("operator", "eq")
        target = condition.get("value")
        actual = self.state.get(key)
        return eval_operator(op, actual, target)

    def _resolve_value(self, value: Any) -> Any:
        """Resolve a $state_key reference to its current value."""
        if isinstance(value, str) and value.startswith("$"):
            state_key = value[1:]
            return self.state.get(state_key)
        return value

    def _resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resolve $state_key references in parameter values."""
        return {k: self._resolve_value(v) for k, v in params.items()}

    def _step_error_detail(self, step: dict[str, Any], index: int, total: int) -> str:
        """Build a descriptive error context string for a failed macro step."""
        action = step.get("action", "unknown")
        parts = [f"step {index + 1}/{total}", action]
        if action in ("device.command", "group.command"):
            command = step.get("command", "")
            if command:
                parts.append(f"command '{command}'")
        if action == "device.command":
            device_id = step.get("device", "")
            if device_id:
                device_name = self.state.get(f"device.{device_id}.name") or device_id
                parts.append(f"on '{device_name}'")
        elif action == "group.command":
            group_id = step.get("group", "")
            if group_id:
                parts.append(f"on group '{group_id}'")
        elif action == "macro":
            sub = step.get("macro", "")
            if sub:
                parts.append(f"calling '{sub}'")
        return ", ".join(parts)

    @staticmethod
    def _auto_description(step: dict[str, Any]) -> str:
        """Generate a human-readable description for a macro step."""
        action = step.get("action", "")
        if action == "device.command":
            return f"Sending {step.get('command', '?')} to {step.get('device', '?')}"
        if action == "group.command":
            return f"Sending {step.get('command', '?')} to group {step.get('group', '?')}"
        if action == "delay":
            return f"Waiting {step.get('seconds', 0)} seconds"
        if action == "state.set":
            return f"Setting {step.get('key', '?')}"
        if action == "macro":
            return f"Running macro {step.get('macro', '?')}"
        if action == "event.emit":
            return f"Emitting {step.get('event', '?')}"
        if action == "conditional":
            return f"Checking {step.get('condition', {}).get('key', '?')}"
        return action

    async def _execute_step(
        self, step: dict[str, Any], context: dict[str, Any],
        _conditional_depth: int = 0, macro_id: str | None = None,
    ) -> None:
        """Execute a single macro step."""
        action = step.get("action", "")

        # Step-level skip_if guard
        skip_if = step.get("skip_if")
        if skip_if and self._evaluate_condition(skip_if):
            log.debug(f"  Macro step skipped (skip_if): {action}")
            return

        # Device offline guard
        if action == "device.command" and step.get("skip_if_offline"):
            device_id = step.get("device", "")
            connected = self.state.get(f"device.{device_id}.connected")
            if not connected:
                log.debug(f"  Macro step skipped (device offline): {device_id}.{step.get('command', '')}")
                return

        if action == "device.command":
            device_id = step.get("device", "")
            command = step.get("command", "")
            params = self._resolve_params(step.get("params") or {})
            log.debug(f"  Macro step: {device_id}.{command}({params})")
            await self.devices.send_command(device_id, command, params)

        elif action == "group.command":
            group_id = step.get("group", "")
            command = step.get("command", "")
            params = self._resolve_params(step.get("params") or {})
            device_ids = self._groups.get(group_id)
            if device_ids is None:
                raise ValueError(f"Device group '{group_id}' not found. Check that the group exists in your project.")

            if not device_ids:
                log.debug(f"  Macro step: group '{group_id}' is empty, skipping")
                return
            # Send to all online devices concurrently
            sent_ids = []
            tasks = []
            skipped_ids = []
            for did in device_ids:
                connected = self.state.get(f"device.{did}.connected")
                if not connected:
                    log.debug(f"  Group command: skipping offline device '{did}'")
                    skipped_ids.append(did)
                    continue
                sent_ids.append(did)
                tasks.append(self.devices.send_command(did, command, params))
            if tasks:
                log.debug(f"  Macro step: group '{group_id}'.{command} -> {len(tasks)} device(s)")
                results = await asyncio.gather(*tasks, return_exceptions=True)
                # Build per-device result list for the progress event
                device_results = []
                for j, result in enumerate(results):
                    did = sent_ids[j] if j < len(sent_ids) else "unknown"
                    device_name = self.state.get(f"device.{did}.name") or did
                    if isinstance(result, Exception):
                        log.error(f"  Group command error on '{device_name}': {result}")
                        device_results.append({"device_id": did, "name": device_name, "success": False, "error": str(result)})
                    else:
                        device_results.append({"device_id": did, "name": device_name, "success": True})
                for did in skipped_ids:
                    device_name = self.state.get(f"device.{did}.name") or did
                    device_results.append({"device_id": did, "name": device_name, "success": False, "error": "Device offline"})
                if macro_id:
                    await self.events.emit(
                        f"macro.progress.{macro_id}",
                        {
                            "macro_id": macro_id,
                            "action": "group.command",
                            "group": group_id,
                            "command": command,
                            "device_results": device_results,
                            "status": "group_complete",
                        },
                    )

        elif action == "delay":
            seconds = max(0, step.get("seconds", 0))
            log.debug(f"  Macro step: delay {seconds}s")
            await asyncio.sleep(seconds)

        elif action == "state.set":
            key = step.get("key", "")
            value = self._resolve_value(step.get("value"))
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

        elif action == "conditional":
            condition = step.get("condition")
            if not condition:
                log.warning("  Conditional step has no condition, skipping")
                return

            if _conditional_depth >= self._max_conditional_depth:
                raise RuntimeError(
                    f"Conditional nesting depth limit ({self._max_conditional_depth}) exceeded"
                )

            result = self._evaluate_condition(condition)

            # Emit conditional evaluation result
            if macro_id:
                await self.events.emit(
                    f"macro.progress.{macro_id}",
                    {
                        "macro_id": macro_id,
                        "action": "conditional",
                        "condition_result": result,
                        "branch": "then" if result else "else",
                        "condition_key": condition.get("key", ""),
                        "condition_operator": condition.get("operator", "eq"),
                        "condition_value": condition.get("value"),
                        "actual_value": self.state.get(condition.get("key", "")),
                        "status": "evaluated",
                    },
                )

            if result:
                then_steps = step.get("then_steps") or []
                if then_steps:
                    log.debug(f"  Conditional: true, running {len(then_steps)} then-step(s)")
                    await self.execute_steps(
                        then_steps, context, macro_id,
                        _conditional_depth=_conditional_depth + 1,
                    )
            else:
                else_steps = step.get("else_steps") or []
                if else_steps:
                    log.debug(f"  Conditional: false, running {len(else_steps)} else-step(s)")
                    await self.execute_steps(
                        else_steps, context, macro_id,
                        _conditional_depth=_conditional_depth + 1,
                    )
                else:
                    log.debug("  Conditional: false, no else-steps")

        else:
            raise ValueError(f"Unknown macro action: '{action}'")
