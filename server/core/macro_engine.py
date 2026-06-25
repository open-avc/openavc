"""
OpenAVC MacroEngine — executes named sequences of actions.

Macros are the bridge between the visual configurator and scripting.
They are ordered sequences of steps: send device commands, set state,
add delays, wait for state conditions, emit events, or call other macros.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from server.core.condition_eval import eval_operator
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.device_manager import DeviceManager

log = get_logger(__name__)


PluginActionHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]]
BroadcastWS = Callable[[dict[str, Any]], Awaitable[None]]

# Grace period a cancelled macro's tasks get to unwind before cancel()/
# cancel_all() give up waiting. A macro that ignores cancellation (a tight
# loop with no await, or a step that swallows CancelledError) can still be
# sending bytes to AV hardware past this window — we surface that rather
# than silently reporting success.
_CANCEL_GRACE_SECONDS = 5.0

# The call chain of the macro currently executing in this task context.
# The in-engine ``_call_chain`` argument only covers direct macro->macro
# nesting; tasks spawned during step execution (event-bus handler dispatch,
# state-change handler dispatch — i.e. script handlers) inherit this
# ContextVar, so a script that re-enters via ``macros.execute()`` carries
# the chain across the script boundary instead of resetting the circular/
# depth guards.
_active_call_chain: ContextVar[frozenset[str]] = ContextVar(
    "openavc_macro_call_chain", default=frozenset()
)


def active_call_chain() -> frozenset[str]:
    """Return the macro call chain active in the current task context."""
    return _active_call_chain.get()


class MacroEngine:
    """Executes named macros — ordered sequences of actions with optional delays."""

    def __init__(
        self,
        state: StateStore,
        events: EventBus,
        devices: DeviceManager,
        broadcast_ws: BroadcastWS | None = None,
    ):
        self.state = state
        self.events = events
        self.devices = devices
        # Optional WebSocket broadcaster — used by the ui.navigate step.
        # None in test/plugin-harness contexts; the step logs and no-ops
        # rather than failing when not wired.
        self._broadcast_ws = broadcast_ws
        self._macros: dict[str, dict[str, Any]] = {}  # id -> macro config
        self._groups: dict[str, list[str]] = {}  # group_id -> [device_ids]
        # macro_id -> set of currently-running tasks. A set (not a single
        # task) so that overlap: allow, REST/WS racing, and concurrent
        # script/plugin/AI dispatch all leave every in-flight invocation
        # individually trackable and cancellable (A51).
        self._running: dict[str, set[asyncio.Task]] = {}
        # Serializes the register-and-preempt critical section so two
        # macros in the same cancel_group started within one event-loop
        # tick can't both register before either fires preemption and
        # then cancel each other (A49).
        self._start_lock = asyncio.Lock()
        self._max_depth = 10  # maximum nested macro call depth
        self._max_conditional_depth = 5  # maximum nesting of conditional steps
        # Plugin-registered actions: action_type -> (handler, plugin_id, label)
        self._plugin_actions: dict[str, tuple[PluginActionHandler, str, str]] = {}

    def is_macro_running(self, macro_id: str) -> bool:
        """Check if any invocation of the macro is currently running."""
        return bool(self._running.get(macro_id))

    async def cancel(self, macro_id: str) -> bool:
        """Cancel every running invocation of a macro.

        Returns True if at least one invocation was cancelled, False if
        none were running.
        """
        tasks = list(self._running.get(macro_id, set()))
        if not tasks:
            return False
        for task in tasks:
            task.cancel()
        pending: set[asyncio.Task] = set()
        try:
            _done, pending = await asyncio.wait(tasks, timeout=_CANCEL_GRACE_SECONDS)
        except asyncio.CancelledError:
            pending = set()
        if pending:
            log.warning(
                "Macro '%s' cancel: %d invocation(s) did not stop within %.0fs "
                "and may still be sending commands to AV hardware",
                macro_id, len(pending), _CANCEL_GRACE_SECONDS,
            )
        return True

    async def cancel_all(self) -> None:
        """Cancel all running macros (for system shutdown)."""
        all_tasks = [t for tasks in self._running.values() for t in tasks]
        if not all_tasks:
            return
        for task in all_tasks:
            task.cancel()
        pending: set[asyncio.Task] = set()
        try:
            _done, pending = await asyncio.wait(all_tasks, timeout=_CANCEL_GRACE_SECONDS)
        except asyncio.CancelledError:
            pending = set()
        if pending:
            log.warning(
                "Shutdown cancel_all: %d macro task(s) did not stop within %.0fs; "
                "control output to AV hardware may continue past shutdown",
                len(pending), _CANCEL_GRACE_SECONDS,
            )

    def _collect_group_targets(
        self, group: str, exclude_task: asyncio.Task | None
    ) -> list[tuple[str, asyncio.Task]]:
        """Return (macro_id, task) pairs to preempt for ``group``.

        Excludes ``exclude_task`` (the caller's own task) so a starting
        macro doesn't cancel itself. Multiple concurrent invocations of
        the same macro_id are all candidates — each task is treated
        individually.
        """
        out: list[tuple[str, asyncio.Task]] = []
        for mid, task_set in self._running.items():
            macro_config = self._macros.get(mid, {})
            if macro_config.get("cancel_group") != group:
                continue
            for task in task_set:
                if task is exclude_task:
                    continue
                out.append((mid, task))
        return out

    async def _drain_cancelled(
        self, cancelled: list[tuple[str, asyncio.Task]]
    ) -> None:
        """Wait for preempted tasks to fully unwind.

        Replaces the old single-yield ``await asyncio.sleep(0)``, which
        was not long enough for in-flight ``device.send_command`` awaits
        to settle. Without this, the new macro could start sending bytes
        on the wire while the preempted macro's tail bytes were still in
        flight — "System Off" preempting "System On" could still leave a
        partial ``power_on`` sequence on the device (A50).
        """
        if not cancelled:
            return
        tasks = [t for _, t in cancelled]
        try:
            await asyncio.wait(tasks, timeout=2.0)
        except asyncio.CancelledError:
            pass

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

    def register_plugin_action(
        self,
        action_type: str,
        handler: PluginActionHandler,
        plugin_id: str,
        label: str = "",
    ) -> None:
        """Register a plugin-provided macro action type.

        Action type must be unique. The handler is called as
        ``await handler(params, context)`` from the macro engine, with
        ``$var.foo`` references in params already resolved.
        """
        existing = self._plugin_actions.get(action_type)
        if existing is not None:
            _, owning_plugin, _ = existing
            raise ValueError(
                f"Macro action '{action_type}' is already registered by plugin "
                f"'{owning_plugin}' — cannot register for '{plugin_id}'"
            )
        self._plugin_actions[action_type] = (handler, plugin_id, label or action_type)
        log.debug(f"Registered plugin macro action: {action_type} -> {plugin_id}")

    def unregister_plugin_action(self, action_type: str) -> None:
        """Remove a plugin-registered macro action type. No-op if missing."""
        if action_type in self._plugin_actions:
            del self._plugin_actions[action_type]
            log.debug(f"Unregistered plugin macro action: {action_type}")

    def unregister_plugin_actions(self, plugin_id: str) -> None:
        """Remove all macro actions registered by a plugin."""
        for action_type in [
            k for k, (_, pid, _) in self._plugin_actions.items() if pid == plugin_id
        ]:
            del self._plugin_actions[action_type]

    def get_plugin_action(self, action_type: str) -> tuple[PluginActionHandler, str, str] | None:
        """Look up a registered plugin action. Returns (handler, plugin_id, label) or None."""
        return self._plugin_actions.get(action_type)

    async def execute(
        self, macro_id: str, context: dict[str, Any] | None = None,
        _call_chain: frozenset[str] | None = None,
    ) -> None:
        """
        Execute a macro by ID.

        Args:
            macro_id: The macro to execute.
            context: Optional context dict passed through to steps.
            _call_chain: Internal — tracks the current execution chain to
                detect circular/recursive calls without blocking independent
                concurrent chains.
        """
        macro = self._macros.get(macro_id)
        if macro is None:
            raise ValueError(f"Macro '{macro_id}' not found")

        if _call_chain is None:
            _call_chain = frozenset()

        if macro_id in _call_chain:
            raise ValueError(
                f"Macro '{macro_id}' blocked — circular/recursive call detected "
                f"(call chain: {' -> '.join(_call_chain)} -> {macro_id})"
            )
        if len(_call_chain) >= self._max_depth:
            raise ValueError(
                f"Macro '{macro_id}' blocked — max nesting depth ({self._max_depth}) reached"
            )
        _call_chain = _call_chain | {macro_id}
        # Publish the chain to this task's context so handler tasks spawned
        # by this macro's steps (and any macros.execute() they make) inherit
        # it — see active_call_chain().
        _chain_token = _active_call_chain.set(_call_chain)

        name = macro.get("name", macro_id)
        steps = macro.get("steps", [])
        stop_on_error = macro.get("stop_on_error", False)
        cancel_group = macro.get("cancel_group")

        task = asyncio.current_task()

        # Critical section: registering this task in _running and choosing
        # which group members to preempt must happen atomically against
        # other concurrent execute() callers. Otherwise two macros in the
        # same cancel_group reaching this section within one event-loop
        # tick can both register, then each cancels the other and neither
        # runs (A49). The lock is released as soon as we've called
        # ``task.cancel()`` on the targets; the drain await happens
        # outside the lock so other unrelated macros can still start.
        cancelled: list[tuple[str, asyncio.Task]] = []
        if task is not None:
            async with self._start_lock:
                self._running.setdefault(macro_id, set()).add(task)
                if cancel_group:
                    cancelled = self._collect_group_targets(
                        cancel_group, exclude_task=task
                    )
                    for mid, t in cancelled:
                        log.info(f"Preempting macro '{mid}' (cancel_group '{cancel_group}')")
                        t.cancel()
        elif cancel_group:
            # Synthetic execute() with no current task — preempt anyway.
            cancelled = self._collect_group_targets(cancel_group, exclude_task=None)
            for mid, t in cancelled:
                log.info(f"Preempting macro '{mid}' (cancel_group '{cancel_group}')")
                t.cancel()

        # Wait for preempted tasks to fully unwind before we start sending
        # commands — A50.
        await self._drain_cancelled(cancelled)

        log.info(f"Executing macro '{name}' ({len(steps)} steps)")
        await self.events.emit(
            f"macro.started.{macro_id}",
            {"macro_id": macro_id, "name": name, "total_steps": len(steps)},
        )

        try:
            await self.execute_steps(steps, context, macro_id, stop_on_error, _call_chain=_call_chain)
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
            _active_call_chain.reset(_chain_token)
            if task is not None:
                task_set = self._running.get(macro_id)
                if task_set is not None:
                    task_set.discard(task)
                    if not task_set:
                        self._running.pop(macro_id, None)

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        macro_id: str | None = None,
        stop_on_error: bool = False,
        _conditional_depth: int = 0,
        _call_chain: frozenset[str] | None = None,
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
                await self._execute_step(step, context, _conditional_depth, macro_id, stop_on_error, _call_chain)
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

    def _condition_actual(
        self, key: str, context: dict[str, Any] | None = None
    ) -> Any:
        """Read a condition's left-hand value. A ``trigger.<field>`` key reads
        from the firing trigger's context (event payload / state-change
        snapshot); any other key reads from the state store."""
        if key.startswith("trigger."):
            return (context or {}).get(key[len("trigger."):])
        return self.state.get(key)

    def _evaluate_condition(
        self, condition: dict[str, Any], context: dict[str, Any] | None = None
    ) -> bool:
        """Evaluate a step condition. The condition ``key`` may be a state key
        or ``trigger.<field>`` (resolved from the firing trigger's context)."""
        key = condition.get("key", "")
        op = condition.get("operator", "eq")
        target = condition.get("value")
        actual = self._condition_actual(key, context)
        return eval_operator(op, actual, target)

    def _resolve_value(self, value: Any, context: dict[str, Any] | None = None) -> Any:
        """Resolve a $-reference to its current value.

        ``$trigger.<field>`` reads from the firing trigger's context — the event
        payload or state-change snapshot passed into ``execute()`` — so a
        triggered macro can act on what arrived/changed (e.g. ``$trigger.data``
        for an event payload field, ``$trigger.new_value`` for a state change).
        Any other ``$<state_key>`` reads from the state store as before. When a
        macro runs directly (no trigger context), ``$trigger.*`` resolves to None.
        """
        if isinstance(value, str) and value.startswith("$"):
            ref = value[1:]
            if ref.startswith("trigger."):
                return (context or {}).get(ref[len("trigger."):])
            return self.state.get(ref)
        return value

    def _resolve_params(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Resolve $-references in parameter values."""
        return {k: self._resolve_value(v, context) for k, v in params.items()}

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
        else:
            plugin_action = self._plugin_actions.get(action)
            if plugin_action is not None:
                _handler, plugin_id, _label = plugin_action
                parts.append(f"plugin '{plugin_id}'")
        return ", ".join(parts)

    def _auto_description(self, step: dict[str, Any]) -> str:
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
        if action == "wait_until":
            cond = step.get("condition") or {}
            key = cond.get("key", "?")
            timeout = step.get("timeout")
            tmo = "no timeout" if timeout is None else f"{timeout}s"
            return f"Waiting for {key} ({tmo})"
        if action == "ui.navigate":
            page = step.get("page", "?")
            if page == "$back":
                return "Going back"
            if page == "$dismiss":
                return "Dismissing overlay"
            return f"Navigating panel to {page}"
        plugin_action = self._plugin_actions.get(action)
        if plugin_action is not None:
            _handler, _plugin_id, label = plugin_action
            return label
        return action

    async def _execute_step(
        self, step: dict[str, Any], context: dict[str, Any],
        _conditional_depth: int = 0, macro_id: str | None = None,
        stop_on_error: bool = False,
        _call_chain: frozenset[str] | None = None,
    ) -> None:
        """Execute a single macro step."""
        action = step.get("action", "")

        # Step-level skip_if guard
        skip_if = step.get("skip_if")
        if skip_if and self._evaluate_condition(skip_if, context):
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
            params = self._resolve_params(step.get("params") or {}, context)
            log.debug(f"  Macro step: {device_id}.{command}({params})")
            await self.devices.send_command(device_id, command, params)

        elif action == "group.command":
            group_id = step.get("group", "")
            command = step.get("command", "")
            params = self._resolve_params(step.get("params") or {}, context)
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
            value = self._resolve_value(step.get("value"), context)
            log.debug(f"  Macro step: state.set {key} = {value!r}")
            self.state.set(key, value, source="macro")

        elif action == "macro":
            sub_macro_id = step.get("macro", "")
            log.debug(f"  Macro step: call macro '{sub_macro_id}'")
            await self.execute(sub_macro_id, context, _call_chain=_call_chain)

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

            result = self._evaluate_condition(condition, context)

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
                        "actual_value": self._condition_actual(condition.get("key", ""), context),
                        "status": "evaluated",
                    },
                )

            if result:
                then_steps = step.get("then_steps") or []
                if then_steps:
                    log.debug(f"  Conditional: true, running {len(then_steps)} then-step(s)")
                    await self.execute_steps(
                        then_steps, context, macro_id,
                        stop_on_error=stop_on_error,
                        _conditional_depth=_conditional_depth + 1,
                        _call_chain=_call_chain,
                    )
            else:
                else_steps = step.get("else_steps") or []
                if else_steps:
                    log.debug(f"  Conditional: false, running {len(else_steps)} else-step(s)")
                    await self.execute_steps(
                        else_steps, context, macro_id,
                        stop_on_error=stop_on_error,
                        _conditional_depth=_conditional_depth + 1,
                        _call_chain=_call_chain,
                    )
                else:
                    log.debug("  Conditional: false, no else-steps")

        elif action == "wait_until":
            await self._execute_wait_until(step, macro_id)

        elif action == "ui.navigate":
            page = step.get("page", "")
            if not page:
                raise ValueError("ui.navigate step requires a 'page' value (page id, '$back', or '$dismiss')")
            log.debug(f"  Macro step: ui.navigate -> {page}")
            # Emit ui.page.<page_id> for symmetry with element press-side
            # navigation, but only for real page IDs — $back/$dismiss are
            # overlay-stack controls, not page targets.
            if page not in ("$back", "$dismiss"):
                await self.events.emit(f"ui.page.{page}")
            if self._broadcast_ws is not None:
                await self._broadcast_ws({"type": "ui.navigate", "page_id": page})
            else:
                log.warning("ui.navigate step fired but no broadcast_ws is wired — no panels notified")

        else:
            plugin_action = self._plugin_actions.get(action)
            if plugin_action is None:
                raise ValueError(f"Unknown macro action: '{action}'")
            handler, plugin_id, _label = plugin_action
            params = self._resolve_params(step.get("params") or {}, context)
            log.debug(f"  Macro step: plugin action '{action}' ({plugin_id}) {params}")
            await handler(params, context)

    async def _execute_wait_until(
        self, step: dict[str, Any], macro_id: str | None
    ) -> None:
        """Pause until a state condition becomes true, with optional timeout."""
        condition = step.get("condition")
        if not isinstance(condition, dict) or not condition.get("key"):
            raise ValueError("wait_until step requires a condition with 'key'")

        timeout = step.get("timeout")  # None = never time out
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout < 0):
            raise ValueError(
                f"wait_until 'timeout' must be a non-negative number or null, got {timeout!r}"
            )

        on_timeout = step.get("on_timeout") or "fail"
        if on_timeout not in ("fail", "continue"):
            raise ValueError(
                f"wait_until 'on_timeout' must be 'fail' or 'continue', got {on_timeout!r}"
            )

        key = condition.get("key", "")

        # Fast path — already satisfied, skip subscribe/wait entirely
        if self._evaluate_condition(condition):
            if macro_id:
                await self.events.emit(
                    f"macro.progress.{macro_id}",
                    {
                        "macro_id": macro_id,
                        "action": "wait_until",
                        "condition_key": key,
                        "condition_operator": condition.get("operator", "eq"),
                        "condition_value": condition.get("value"),
                        "status": "satisfied",
                        "waited_seconds": 0.0,
                    },
                )
            return

        satisfied = asyncio.Event()

        def _on_change(_k: str, _old: Any, _new: Any, _src: str) -> None:
            if self._evaluate_condition(condition):
                satisfied.set()

        sub_id = self.state.subscribe(key, _on_change)
        started = time.monotonic()

        if macro_id:
            await self.events.emit(
                f"macro.progress.{macro_id}",
                {
                    "macro_id": macro_id,
                    "action": "wait_until",
                    "condition_key": key,
                    "condition_operator": condition.get("operator", "eq"),
                    "condition_value": condition.get("value"),
                    "timeout": timeout,
                    "status": "waiting",
                },
            )

        try:
            # Close the TOCTOU window between the initial check and subscribe
            if self._evaluate_condition(condition):
                satisfied.set()

            if timeout is None:
                await satisfied.wait()
                timed_out = False
            else:
                try:
                    await asyncio.wait_for(satisfied.wait(), timeout=float(timeout))
                    timed_out = False
                except asyncio.TimeoutError:
                    timed_out = True
        finally:
            self.state.unsubscribe(sub_id)

        elapsed = time.monotonic() - started

        if timed_out:
            if macro_id:
                await self.events.emit(
                    f"macro.progress.{macro_id}",
                    {
                        "macro_id": macro_id,
                        "action": "wait_until",
                        "condition_key": key,
                        "status": "timeout",
                        "waited_seconds": elapsed,
                        "on_timeout": on_timeout,
                    },
                )
            if on_timeout == "fail":
                raise TimeoutError(
                    f"wait_until timed out after {timeout}s "
                    f"(condition: {key} {condition.get('operator', 'eq')} "
                    f"{condition.get('value')!r})"
                )
            # on_timeout == "continue" → fall through silently
        else:
            if macro_id:
                await self.events.emit(
                    f"macro.progress.{macro_id}",
                    {
                        "macro_id": macro_id,
                        "action": "wait_until",
                        "condition_key": key,
                        "status": "satisfied",
                        "waited_seconds": elapsed,
                    },
                )
