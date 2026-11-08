"""What each macro step does, and the loop that runs a list of them.

``MacroSteps`` is a mixin ``MacroEngine`` inherits: the engine decides which
runs may start and how each one ends (``macro_engine.py``); this module runs a
run's steps. ``STEP_HANDLERS`` is the one table of built-in steps the runtime
runs, one method per action, and ``tests/test_macro_step_types_parity.py``
holds it to ``macro_validation.BUILTIN_STEP_ACTIONS``, the list the validator,
the editor and the AI accept. An action missing from the table is a plugin's,
or unknown.

The rule worth knowing before changing the loop: a failed step is caught,
counted and announced, and the run carries on unless ``stop_on_error`` says
otherwise. A step inside a conditional's branch counts toward the run that owns
it, because every list in one run shares one ``StepRun``.

Everything here reads the engine it is mixed into: ``state``, ``events``,
``devices``, ``_help``, ``_broadcast_ws``, ``_project_device_ids``,
``_plugin_actions``, ``_max_conditional_depth``, ``_wording``, ``_group_command``
and ``execute`` (a ``macro`` step runs another macro through the engine's own
start, so its guards and its outcome apply).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from openavc.core.condition_eval import eval_operator
from openavc.core.group_command import reached_nobody
from openavc.core.value_resolver import resolve_in_text, resolve_ref
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.group_command import GroupCommand
    from openavc.core.macro_text import StepWording
    from openavc.core.state_store import StateStore

# The macro engine's own logger: the log view files these lines under Macros
# by that name (utils/log_buffer.py).
log = get_logger("openavc.core.macro_engine")

StepPath = list[int | str]
# (error, message) for a failure a step handled itself instead of raising.
Unreported = tuple[str, str]


@dataclass(frozen=True)
class StepRun:
    """One run's steps: what every list inside it shares.

    ``failures`` is the run's tally, one list for the whole tree, so a step
    that fails inside a conditional's branch counts toward the run rather than
    being lost at the branch boundary. ``depth`` and ``prefix`` say where the
    list being run sits: a top-level list is at depth 0 with an empty prefix,
    and each step's full path is ``[*prefix, index]`` -- emitted as
    ``step_path`` on the progress event so the UI can follow the active step
    through nested branches.
    """

    macro_id: str | None
    context: dict[str, Any]
    stop_on_error: bool
    call_chain: frozenset[str] | None
    failures: list[str]
    depth: int = 0
    prefix: tuple[int | str, ...] = ()

    def branch(self, step_path: StepPath, name: str) -> StepRun:
        """The same run, one conditional deeper, inside ``name`` of that step."""
        return replace(self, depth=self.depth + 1, prefix=(*step_path, name))


def _wait_until_args(step: dict[str, Any]) -> tuple[dict[str, Any], Any, str]:
    """A wait_until step's condition, timeout and on_timeout, each checked."""
    return _wait_condition(step), _wait_timeout(step), _wait_on_timeout(step)


def _wait_condition(step: dict[str, Any]) -> dict[str, Any]:
    condition = step.get("condition")
    if not isinstance(condition, dict) or not condition.get("key"):
        raise ValueError("wait_until step requires a condition with 'key'")
    return condition


def _wait_timeout(step: dict[str, Any]) -> Any:
    """Seconds to wait, or None to wait for ever."""
    timeout = step.get("timeout")
    if timeout is None:
        return None
    if not isinstance(timeout, (int, float)) or timeout < 0:
        raise ValueError(
            f"wait_until 'timeout' must be a non-negative number or null, got {timeout!r}"
        )
    return timeout


def _wait_on_timeout(step: dict[str, Any]) -> str:
    on_timeout = step.get("on_timeout") or "fail"
    if on_timeout not in ("fail", "continue"):
        raise ValueError(
            f"wait_until 'on_timeout' must be 'fail' or 'continue', got {on_timeout!r}"
        )
    return on_timeout


class MacroSteps:
    """Runs step lists; mixed into ``MacroEngine``."""

    if TYPE_CHECKING:
        state: StateStore
        events: EventBus
        devices: DeviceManager
        _help: Any
        _broadcast_ws: Callable[[dict[str, Any]], Awaitable[None]] | None
        _project_device_ids: Callable[[], set[str] | None] | None
        _plugin_actions: dict[str, tuple[Any, str, str]]
        _max_conditional_depth: int
        _wording: StepWording
        _group_command: GroupCommand

        async def execute(
            self, macro_id: str, context: dict[str, Any] | None = None,
            _call_chain: frozenset[str] | None = None,
        ) -> str: ...

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        macro_id: str | None = None,
        stop_on_error: bool = False,
        *,
        _call_chain: frozenset[str] | None = None,
    ) -> int:
        """
        Execute a list of steps sequentially. Returns how many steps FAILED.

        Each step is wrapped in try/except — errors are logged but
        execution continues to the next step (unless stop_on_error is True).

        The count is why this returns anything at all. With the default
        ``stop_on_error: false`` a failed step is caught here and stepped over,
        so the run reaches the end and nothing above could tell it from a clean
        one — the caller was told ``executed`` either way. Continuing is right
        and is unchanged; being quiet about it was not.

        ``_call_chain`` is the macros this run is inside, for ``execute``: a
        ``macro`` step carries it into the macro it calls, so the circular and
        depth guards see the whole chain.
        """
        run = StepRun(
            macro_id=macro_id, context=context or {}, stop_on_error=stop_on_error,
            call_chain=_call_chain, failures=[],
        )
        return await self._run_steps(steps, run)

    async def _run_steps(self, steps: list[dict[str, Any]], run: StepRun) -> int:
        """Run one list of a run's steps; return the run's failure count."""
        total = len(steps)
        for i, step in enumerate(steps):
            step_path: StepPath = [*run.prefix, i]
            await self._announce_step(step, run, i, total, step_path)
            try:
                unreported = await self._execute_step(step, run, step_path)
            except Exception as e:  # Catch-all: isolates individual step errors from halting the macro
                await self._step_raised(step, run, i, total, e)
            else:
                # A step that handled its own failure and carried on. Only
                # group.command does this: it fans out and reports per device
                # rather than raising, so a group that reached nobody used to
                # end the macro on `completed` with nothing said anywhere.
                # It still does not raise, so stop_on_error is unchanged.
                if unreported is not None and run.macro_id:
                    await self._step_reported(step, run, i, total, unreported)
        return len(run.failures)

    async def _announce_step(
        self, step: dict[str, Any], run: StepRun, i: int, total: int, step_path: StepPath,
    ) -> None:
        if not run.macro_id:
            return
        await self.events.emit(
            f"macro.progress.{run.macro_id}",
            {
                "macro_id": run.macro_id,
                "step_index": i,
                "total_steps": total,
                "step_path": step_path,
                "action": step.get("action", ""),
                "description": self._wording.describe(step),
                "status": "running",
            },
        )

    async def _step_raised(
        self, step: dict[str, Any], run: StepRun, i: int, total: int, e: Exception,
    ) -> None:
        """Count, log and announce a step that raised; end the run under
        ``stop_on_error``."""
        step_detail = self._wording.failure_context(step, i, total)
        run.failures.append(step_detail)
        log.error(f"Macro step failed: {step_detail} — {e}")
        error, message = self._wording.failure(step, e)
        # Emit step-level error event so the frontend can show it
        if run.macro_id:
            await self.events.emit(
                f"macro.step_error.{run.macro_id}",
                self._wording.error_frame(
                    run.macro_id, step, i, total,
                    error=error, message=message, call_chain=run.call_chain,
                ),
            )
        if run.stop_on_error:
            # The worded sentence, not the exception: this text is the
            # run's macro.error, and a calling macro's step error.
            raise RuntimeError(f"{step_detail}: {error}") from e

    async def _step_reported(
        self, step: dict[str, Any], run: StepRun, i: int, total: int, unreported: Unreported,
    ) -> None:
        """Count, log and announce a failure the step handled itself."""
        error, message = unreported
        step_detail = self._wording.failure_context(step, i, total)
        run.failures.append(step_detail)
        log.error(f"Macro step failed: {step_detail} — {error}")
        await self.events.emit(
            f"macro.step_error.{run.macro_id}",
            self._wording.error_frame(
                run.macro_id or "", step, i, total, error=error, message=message,
                call_chain=run.call_chain,
            ),
        )

    async def _execute_step(
        self, step: dict[str, Any], run: StepRun, step_path: StepPath,
    ) -> Unreported | None:
        """Execute a single macro step. ``step_path`` is its place in the tree
        (``[*run.prefix, index]``); a conditional builds its branches' prefix
        from it.

        Returns ``None``, or ``(error, message)`` for a failure the step
        handled itself instead of raising -- only ``group.command`` does that,
        and only when the command reached no device at all. The caller turns it
        into the same ``macro.step_error`` frame a raise would have produced.
        """
        action = step.get("action", "")
        skip_if = step.get("skip_if")
        if skip_if and self._evaluate_condition(skip_if, run.context):
            log.debug(f"  Macro step skipped (skip_if): {action}")
            return None
        handler = self.STEP_HANDLERS.get(action)
        if handler is not None:
            return await handler(self, step, run, step_path)
        await self._plugin_step(action, step, run)
        return None

    # --- conditions and $-references ---

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
        Any other ``$<state_key>`` reads from the state store, through the
        resolver the UI bindings use (``openavc.core.value_resolver``), so an
        unknown state key warns the same way. When a macro runs directly (no
        trigger context), ``$trigger.*`` resolves to None.
        """
        return resolve_ref(value, state=self.state, trigger_ctx=context)

    def _resolve_params(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Resolve $-references in parameter values."""
        return {k: self._resolve_value(v, context) for k, v in params.items()}

    # --- the steps ---

    async def _device_command(
        self, step: dict[str, Any], run: StepRun, _path: StepPath,
    ) -> None:
        device_id = step.get("device", "")
        command = step.get("command", "")
        # Skipped when the command could not go out, the same rule the send
        # applies (a device held offline until it answers a poll still takes
        # it, and so does a command that needs no connection, like a
        # Wake-on-LAN Power On).
        if step.get("skip_if_offline") and not self.devices.command_goes_out(device_id, command):
            log.debug(f"  Macro step skipped (device offline): {device_id}.{command}")
            return
        params = self._resolve_params(step.get("params") or {}, run.context)
        log.debug(f"  Macro step: {device_id}.{command}({params})")
        await self.devices.send_command(device_id, command, params)

    async def _group_command_step(
        self, step: dict[str, Any], run: StepRun, _path: StepPath,
    ) -> Unreported | None:
        group_id = step.get("group", "")
        command = step.get("command", "")
        params = self._resolve_params(step.get("params") or {}, run.context)
        device_results = await self._group_command.send(
            group_id, command, params, self._project_device_ids,
        )
        if device_results is None:
            return None
        if run.macro_id:
            await self.events.emit(
                f"macro.progress.{run.macro_id}",
                {
                    "macro_id": run.macro_id,
                    "action": "group.command",
                    "group": group_id,
                    "command": command,
                    "device_results": device_results,
                    "status": "group_complete",
                },
            )
        return reached_nobody(device_results)

    async def _delay(self, step: dict[str, Any], _run: StepRun, _path: StepPath) -> None:
        seconds = max(0, step.get("seconds", 0))
        log.debug(f"  Macro step: delay {seconds}s")
        await asyncio.sleep(seconds)

    async def _state_set(self, step: dict[str, Any], run: StepRun, _path: StepPath) -> None:
        key = step.get("key", "")
        value = self._resolve_value(step.get("value"), run.context)
        log.debug(f"  Macro step: state.set {key} = {value!r}")
        self.state.set(key, value, source="macro")

    async def _call_macro(self, step: dict[str, Any], run: StepRun, _path: StepPath) -> None:
        sub_macro_id = step.get("macro", "")
        log.debug(f"  Macro step: call macro '{sub_macro_id}'")
        outcome = await self.execute(sub_macro_id, run.context, _call_chain=run.call_chain)
        if outcome == "failed":
            # The subroutine ran to the end and its own stop_on_error said
            # to carry on past what failed, so it returned rather than
            # raising -- and that policy is ITS call, not ours to override
            # (a child's `stop_on_error: false` must not halt a parent that
            # asked to continue). But the run that called it did not get
            # what it asked for, and saying nothing is how a blocked
            # nesting-depth call could sit under ten macros each reporting
            # completed.
            #
            # Counted, and NOT announced: the failure already emitted its
            # own `macro.step_error` down where it happened, naming the
            # step and carrying the call chain up to here. A second frame
            # per level would turn one dead projector into ten, and none of
            # them would name the step that actually failed.
            run.failures.append(f"macro '{sub_macro_id}' reported failed steps")

    async def _emit_event(self, step: dict[str, Any], run: StepRun, _path: StepPath) -> None:
        event_name = step.get("event", "")
        # Resolved, like every other value a step carries, so a `$var.`
        # reference in the payload means what it means in a binding action.
        payload = self._resolve_params(step.get("payload") or {}, run.context)
        log.debug(f"  Macro step: emit '{event_name}'")
        await self.events.emit(event_name, payload)

    async def _conditional(self, step: dict[str, Any], run: StepRun, step_path: StepPath) -> None:
        condition = step.get("condition")
        if not condition:
            log.warning("  Conditional step has no condition, skipping")
            return
        if run.depth >= self._max_conditional_depth:
            raise RuntimeError(
                f"Conditional nesting depth limit ({self._max_conditional_depth}) exceeded"
            )
        result = self._evaluate_condition(condition, run.context)
        await self._announce_branch(condition, result, run)
        branch = "then" if result else "else"
        steps = step.get(f"{branch}_steps") or []
        if steps:
            said = "true" if result else "false"
            log.debug(f"  Conditional: {said}, running {len(steps)} {branch}-step(s)")
            await self._run_steps(steps, run.branch(step_path, branch))
        elif not result:
            log.debug("  Conditional: false, no else-steps")

    async def _announce_branch(
        self, condition: dict[str, Any], result: bool, run: StepRun,
    ) -> None:
        if not run.macro_id:
            return
        await self.events.emit(
            f"macro.progress.{run.macro_id}",
            {
                "macro_id": run.macro_id,
                "action": "conditional",
                "condition_result": result,
                "branch": "then" if result else "else",
                "condition_key": condition.get("key", ""),
                "condition_operator": condition.get("operator", "eq"),
                "condition_value": condition.get("value"),
                "actual_value": self._condition_actual(condition.get("key", ""), run.context),
                "status": "evaluated",
            },
        )

    async def _navigate(self, step: dict[str, Any], _run: StepRun, _path: StepPath) -> None:
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

    async def _request_help(self, step: dict[str, Any], run: StepRun, _path: StepPath) -> None:
        # The message resolves $var. and $trigger. -- and unlike every
        # other macro parameter it resolves them INSIDE the sentence,
        # because this one is prose going to a person rather than a value
        # going to a device. That is what turns "someone pressed help"
        # into "Lecture Hall 2 projector failed to power on 3 times".
        message = resolve_in_text(
            step.get("message", ""), state=self.state, trigger_ctx=run.context,
        )
        severity = str(step.get("severity") or "warning")
        cooldown = step.get("cooldown")
        if self._help is None:
            log.warning(
                "A macro asked for help and there is nothing wired to send "
                "it. The request was not raised."
            )
            return
        await self._help.raise_request(
            message=message, severity=severity, cooldown=cooldown,
        )

    async def _clear_help(self, _step: dict[str, Any], _run: StepRun, _path: StepPath) -> None:
        if self._help is None:
            log.warning(
                "A macro cleared the help request and there is nothing "
                "wired to clear."
            )
            return
        self._help.clear()

    async def _plugin_step(self, action: str, step: dict[str, Any], run: StepRun) -> None:
        plugin_action = self._plugin_actions.get(action)
        if plugin_action is None:
            raise ValueError(f"Unknown macro action: '{action}'")
        handler, plugin_id, _label = plugin_action
        params = self._resolve_params(step.get("params") or {}, run.context)
        log.debug(f"  Macro step: plugin action '{action}' ({plugin_id}) {params}")
        await handler(params, run.context)

    # --- wait_until ---

    async def _wait_until(self, step: dict[str, Any], run: StepRun, _path: StepPath) -> None:
        """Pause until a state condition becomes true, with optional timeout.

        The condition is read from the state store only: a ``trigger.`` key
        is never answered from the firing trigger's context here.
        """
        condition, timeout, on_timeout = _wait_until_args(step)
        key = condition.get("key", "")
        if self._evaluate_condition(condition):
            # Already satisfied: no subscription, no wait.
            await self._report_wait(run, {
                "condition_key": key,
                "condition_operator": condition.get("operator", "eq"),
                "condition_value": condition.get("value"),
                "status": "satisfied",
                "waited_seconds": 0.0,
            })
            return
        timed_out, elapsed = await self._wait_for(condition, timeout, run)
        if not timed_out:
            await self._report_wait(run, {
                "condition_key": key, "status": "satisfied", "waited_seconds": elapsed,
            })
            return
        await self._report_wait(run, {
            "condition_key": key, "status": "timeout",
            "waited_seconds": elapsed, "on_timeout": on_timeout,
        })
        if on_timeout == "fail":
            raise TimeoutError(
                f"wait_until timed out after {timeout}s "
                f"(condition: {key} {condition.get('operator', 'eq')} "
                f"{condition.get('value')!r})"
            )

    async def _wait_for(
        self, condition: dict[str, Any], timeout: Any, run: StepRun,
    ) -> tuple[bool, float]:
        """Wait for ``condition`` on the state store; ``(timed_out, elapsed)``."""
        key = condition.get("key", "")
        satisfied = asyncio.Event()

        def _on_change(_k: str, _old: Any, _new: Any, _src: str) -> None:
            if self._evaluate_condition(condition):
                satisfied.set()

        sub_id = self.state.subscribe(key, _on_change)
        started = time.monotonic()
        await self._report_wait(run, {
            "condition_key": key,
            "condition_operator": condition.get("operator", "eq"),
            "condition_value": condition.get("value"),
            "timeout": timeout,
            "status": "waiting",
        })
        try:
            # The value may have turned between the first check and the
            # subscription, when no change notification could reach it.
            if self._evaluate_condition(condition):
                satisfied.set()
            timed_out = not await _wait_event(satisfied, timeout)
        finally:
            self.state.unsubscribe(sub_id)
        return timed_out, time.monotonic() - started

    async def _report_wait(self, run: StepRun, frame: dict[str, Any]) -> None:
        if not run.macro_id:
            return
        await self.events.emit(
            f"macro.progress.{run.macro_id}",
            {"macro_id": run.macro_id, "action": "wait_until", **frame},
        )

    STEP_HANDLERS: dict[str, Callable[..., Awaitable[Unreported | None]]] = {
        "device.command": _device_command,
        "group.command": _group_command_step,
        "delay": _delay,
        "state.set": _state_set,
        "macro": _call_macro,
        "event.emit": _emit_event,
        "conditional": _conditional,
        "wait_until": _wait_until,
        "ui.navigate": _navigate,
        "help.request": _request_help,
        "help.clear": _clear_help,
    }


async def _wait_event(event: asyncio.Event, timeout: Any) -> bool:
    """Wait for ``event``; False when ``timeout`` (seconds, None = never) ran out."""
    if timeout is None:
        await event.wait()
        return True
    try:
        await asyncio.wait_for(event.wait(), timeout=float(timeout))
    except asyncio.TimeoutError:
        return False
    return True
