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

from openavc.core.condition_eval import eval_operator
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.core.value_resolver import resolve_in_text, resolve_ref
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.device_manager import DeviceManager

log = get_logger(__name__)


PluginActionHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]]
BroadcastWS = Callable[[dict[str, Any]], Awaitable[None]]

# Grace period a cancelled macro's tasks get to unwind before cancel()/
# cancel_all() give up waiting. A macro that ignores cancellation (a tight
# loop with no await, or a step that swallows CancelledError) can still be
# sending bytes to AV hardware past this window — we surface that rather
# than silently reporting success.
_CANCEL_GRACE_SECONDS = 5.0

# A macro's optional overlap="queue" guard waits for the in-flight
# invocation to finish before starting. Poll this often for it to clear,
# and give up after the ceiling so a queued call can't wait forever.
# Mirrors the trigger engine's queue wait, so a macro's own guard behaves
# the same whichever entry point fired it.
_QUEUE_POLL_SECONDS = 1.0
_QUEUE_MAX_WAIT_SECONDS = 300.0

# How long an OPERATOR door (the IDE's run button, the cloud AI's run_macro)
# waits for a macro before answering "it is running". A macro is allowed to
# wait forever — `wait_until` with `timeout: null` is a documented shape and
# waiting for a projector is what it is for — but a request is not: held open
# it eventually dies at some client or proxy, and the caller is told a macro
# that is running perfectly well has FAILED. Well under the cloud tunnel's
# 300s read timeout, and longer than any macro whose outcome is worth
# reporting in a toast; a longer one is followed on the live progress the
# IDE already receives over the WebSocket.
OPERATOR_RUN_WAIT_SECONDS = 30.0

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


def _log_detached_failure(task: asyncio.Task) -> None:
    """Retrieve a detached macro task's exception so it is logged, not lost.

    ``execute`` handles a step failure itself, so this fires only for
    something raised around it. Nobody is awaiting the task by the time it
    can happen, and an unretrieved exception reaches the log as a bare
    "Task exception was never retrieved" with no macro id in it.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"Macro task failed after the caller stopped waiting: {exc}")


class MacroEngine:
    """Executes named macros — ordered sequences of actions with optional delays."""

    def __init__(
        self,
        state: StateStore,
        events: EventBus,
        devices: DeviceManager,
        broadcast_ws: BroadcastWS | None = None,
        help_requests: Any = None,
    ):
        self.state = state
        self.events = events
        self.devices = devices
        # Raises "Ask for help" steps. None in the plugin/test harness, where
        # the step reports that it had nowhere to send it rather than raising:
        # a macro that asks for help is usually the last thing still working.
        self._help = help_requests
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
        # macro_id -> monotonic timestamp of its last accepted start, used by
        # the optional per-macro cooldown guard. Enforced at execute() so the
        # cooldown holds from every entry point (script, REST, AI, UI, trigger,
        # macro chain), not just the trigger that declared one.
        self._last_started_monotonic: dict[str, float] = {}
        # Serializes the register-and-preempt critical section so two
        # macros in the same cancel_group started within one event-loop
        # tick can't both register before either fires preemption and
        # then cancel each other (A49).
        self._start_lock = asyncio.Lock()
        # Strong references to the tasks execute_detached starts, so a macro
        # nobody is awaiting any more is not collected out from under itself.
        self._detached: set[asyncio.Task] = set()
        self._max_depth = 10  # maximum nested macro call depth
        self._max_conditional_depth = 5  # maximum nesting of conditional steps
        # Plugin-registered actions: action_type -> (handler, plugin_id, label)
        self._plugin_actions: dict[str, tuple[PluginActionHandler, str, str]] = {}

    def is_macro_running(self, macro_id: str) -> bool:
        """Check if any invocation of the macro is currently running."""
        return bool(self._running.get(macro_id))

    def has_macro(self, macro_id: str) -> bool:
        """Is this macro loaded?

        Here rather than in the caller because ``execute`` already decides it
        one line down, and a second reading of the same dict is what drifts.
        A caller that starts a macro in the background has no other way to ask:
        the refusal ``execute`` raises happens inside a task nobody is holding.
        """
        return macro_id in self._macros

    def _throttle_reason(
        self, macro_id: str, overlap: str, cooldown: float
    ) -> str | None:
        """Return a human reason if a macro's own guard blocks this start, else None.

        Called inside the start lock so the skip / cooldown decision is atomic
        against other concurrent execute() callers. This is the macro-level
        counterpart to the trigger's overlap/cooldown: because it sits at the
        single execute() chokepoint, it applies no matter how the macro was
        fired. When a trigger also carries its own guard the two stack — the
        trigger's runs first, upstream, and this one is the floor — so the
        stricter of the two always wins.
        """
        if cooldown > 0:
            last = self._last_started_monotonic.get(macro_id)
            if last is not None and (time.monotonic() - last) < cooldown:
                return f"cooldown ({cooldown:g}s) not elapsed"
        if overlap == "skip" and self.is_macro_running(macro_id):
            return "overlap=skip and an instance is already running"
        return None

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
        # Drop cooldown baselines for macros that no longer exist so the map
        # doesn't accumulate stale ids across reloads; keep live ones so a
        # cooldown survives an edit/reload.
        self._last_started_monotonic = {
            k: v for k, v in self._last_started_monotonic.items() if k in self._macros
        }
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

    def plugin_action_types(self) -> frozenset[str]:
        """Action types plugins have registered — valid steps beyond the built-ins.

        Anything validating a macro needs this: the built-in nine are a static
        list, but a step naming a plugin action is equally runnable, and only
        this engine knows which ones are loaded right now.
        """
        return frozenset(self._plugin_actions)

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

        # A macro's own overlap/cooldown guard. Unlike the trigger's, it is
        # enforced here — the one point every invocation passes through — so a
        # macro fired from a script, REST, the AI tool, a UI press, or another
        # macro is throttled the same as one fired from a trigger. Defaults
        # (overlap=allow, cooldown=0) leave the historic concurrent behaviour
        # untouched.
        overlap = macro.get("overlap") or "allow"
        cooldown = float(macro.get("cooldown_seconds") or 0)

        task = asyncio.current_task()

        # overlap=queue: hold this start until any in-flight invocation of the
        # same macro finishes, then fall through to claim the slot. The
        # recursion guard above already stops a macro queueing behind itself;
        # this waits on independent concurrent invocations. Bounded so a
        # wedged run can't make a queued call wait forever.
        if overlap == "queue":
            waited = 0.0
            while self.is_macro_running(macro_id):
                if waited >= _QUEUE_MAX_WAIT_SECONDS:
                    log.warning(
                        f"Macro '{name}' overlap=queue: gave up after "
                        f"{_QUEUE_MAX_WAIT_SECONDS:.0f}s waiting for the running "
                        f"invocation to finish; starting anyway"
                    )
                    break
                await asyncio.sleep(_QUEUE_POLL_SECONDS)
                waited += _QUEUE_POLL_SECONDS

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
                blocked = self._throttle_reason(macro_id, overlap, cooldown)
                if blocked is not None:
                    _active_call_chain.reset(_chain_token)
                    log.info(f"Macro '{name}' skipped — {blocked}")
                    return
                self._running.setdefault(macro_id, set()).add(task)
                if cooldown > 0:
                    self._last_started_monotonic[macro_id] = time.monotonic()
                if cancel_group:
                    cancelled = self._collect_group_targets(
                        cancel_group, exclude_task=task
                    )
                    for mid, t in cancelled:
                        log.info(f"Preempting macro '{mid}' (cancel_group '{cancel_group}')")
                        t.cancel()
        else:
            # Synthetic execute() with no current task: honour the throttle
            # (skip/cooldown) but there's no task to register or preempt with.
            blocked = self._throttle_reason(macro_id, overlap, cooldown)
            if blocked is not None:
                _active_call_chain.reset(_chain_token)
                log.info(f"Macro '{name}' skipped — {blocked}")
                return
            if cooldown > 0:
                self._last_started_monotonic[macro_id] = time.monotonic()
            if cancel_group:
                # No current task — preempt anyway.
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

    async def execute_detached(
        self,
        macro_id: str,
        context: dict[str, Any] | None = None,
        *,
        wait_seconds: float | None = None,
    ) -> str:
        """Run a macro in its own task, waiting only ``wait_seconds`` for it.

        Returns ``"executed"`` when the macro finished inside that window and
        ``"running"`` when it did not. The macro keeps going either way —
        nothing here cancels it.

        This is the shape the OPERATOR doors need: the IDE's run button and
        the cloud AI's ``run_macro``, where a person asked for a macro and is
        holding a socket open waiting for the answer. ``execute`` runs the
        steps in the CALLER'S task, so a `wait_until` with no timeout — a
        legal, documented shape meaning "wait for the projector" — held that
        request until the socket died, and the caller was told the macro had
        FAILED when it was running perfectly well.

        The automation doors keep awaiting ``execute`` directly: a trigger, a
        script, a plugin and a panel press have no socket to lose, and one of
        them waiting for a device is the feature.

        Raises ``ValueError`` for a macro that does not exist, so a caller can
        still answer 404 — that refusal has to happen here rather than inside
        the task, where nobody would see it.
        """
        if not self.has_macro(macro_id):
            raise ValueError(f"Macro '{macro_id}' not found")

        # Read at call time, not bound as a default: the constant is the knob
        # a test turns down, and a default argument would freeze it at import.
        if wait_seconds is None:
            wait_seconds = OPERATOR_RUN_WAIT_SECONDS

        task = asyncio.create_task(self.execute(macro_id, context))
        self._detached.add(task)
        task.add_done_callback(self._detached.discard)
        task.add_done_callback(_log_detached_failure)
        try:
            # shield, so the timeout ends the WAIT and not the macro.
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_seconds)
        except asyncio.TimeoutError:
            log.info(
                f"Macro '{macro_id}' is still running after {wait_seconds:.0f}s "
                f"— answering the caller and letting it continue"
            )
            return "running"
        return "executed"

    async def execute_steps(
        self,
        steps: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        macro_id: str | None = None,
        stop_on_error: bool = False,
        _conditional_depth: int = 0,
        _call_chain: frozenset[str] | None = None,
        _path_prefix: list[int | str] | None = None,
    ) -> None:
        """
        Execute a list of steps sequentially.

        Each step is wrapped in try/except — errors are logged but
        execution continues to the next step (unless stop_on_error is True).

        ``_path_prefix`` is the tree location of this step list. Top-level runs
        pass ``[]``; a conditional recursing into a branch passes
        ``[*parent_path, "then"|"else"]``. Each step's full path is
        ``[*_path_prefix, i]`` — emitted as ``step_path`` on the progress event
        so the UI can track the active step through nested branches without
        guessing tree depth from ``total_steps`` deltas.
        """
        context = context or {}
        total = len(steps)
        prefix = _path_prefix or []

        for i, step in enumerate(steps):
            action = step.get("action", "")
            step_path = [*prefix, i]

            # Emit progress
            if macro_id:
                await self.events.emit(
                    f"macro.progress.{macro_id}",
                    {
                        "macro_id": macro_id,
                        "step_index": i,
                        "total_steps": total,
                        "step_path": step_path,
                        "action": action,
                        "description": step.get("description") or self._auto_description(step),
                        "status": "running",
                    },
                )

            try:
                unreported = await self._execute_step(
                    step, context, _conditional_depth, macro_id, stop_on_error,
                    _call_chain, _step_path=step_path,
                )
            except Exception as e:  # Catch-all: isolates individual step errors from halting the macro
                step_detail = self._step_error_detail(step, i, total)
                log.error(f"Macro step failed: {step_detail} — {e}")
                # Emit step-level error event so the frontend can show it
                if macro_id:
                    await self.events.emit(
                        f"macro.step_error.{macro_id}",
                        self._step_error_payload(
                            macro_id, step, i, total,
                            error=str(e),
                            message=self._step_error_message(step, e),
                            call_chain=_call_chain,
                        ),
                    )
                if stop_on_error:
                    raise RuntimeError(f"{step_detail}: {e}") from e
                # Continue to next step (don't halt the macro)
            else:
                # A step that handled its own failure and carried on. Only
                # group.command does this: it fans out and reports per device
                # rather than raising, so a group that reached nobody used to
                # end the macro on `completed` with nothing said anywhere.
                # It still does not raise, so stop_on_error is unchanged.
                if unreported is not None and macro_id:
                    error, message = unreported
                    log.error(
                        f"Macro step failed: {self._step_error_detail(step, i, total)} — {error}"
                    )
                    await self.events.emit(
                        f"macro.step_error.{macro_id}",
                        self._step_error_payload(
                            macro_id, step, i, total, error=error, message=message,
                            call_chain=_call_chain,
                        ),
                    )

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
        Any other ``$<state_key>`` reads from the state store. When a macro runs
        directly (no trigger context), ``$trigger.*`` resolves to None.

        Delegates to the shared resolver (``openavc.core.value_resolver``) so the
        $-namespaces and the unknown-state-key warning match the UI binding
        resolver. A macro has no UI event, so no ``event_ctx`` is passed —
        behavior is identical to before except an unknown state key now warns
        (it used to resolve to None silently).
        """
        return resolve_ref(value, state=self.state, trigger_ctx=context)

    def _resolve_params(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Resolve $-references in parameter values."""
        return {k: self._resolve_value(v, context) for k, v in params.items()}

    def _step_error_payload(
        self, macro_id: str, step: dict[str, Any], index: int, total: int,
        *, error: str, message: str, call_chain: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """One frame for a failed step, whoever noticed it.

        Two renderings of the failure, deliberately: ``error`` is what the
        exception said, which is what a log and the Programmer's run history
        are for, and ``message`` is the same failure written for somebody
        standing at a panel. The panel has no way to translate the first into
        the second -- it holds no device names, no host and no exception type
        -- so the sentence is built here, where all three still exist.

        ``call_chain`` is every macro this failure happened inside, this one
        included. A panel deciding whether the failure belongs to something
        somebody there pressed needs it: press "System On", which calls
        "Projector On", and ``macro_id`` names the sub-macro nobody has heard
        of. Sorted for a stable frame -- the engine tracks the chain as a set
        (concurrent chains, not one stack), so it answers "was this inside X"
        and cannot be read back as the order they were called in.
        """
        chain = set(call_chain or ()) | {macro_id}
        return {
            "macro_id": macro_id,
            "call_chain": sorted(chain),
            "step_index": index,
            "total_steps": total,
            "action": step.get("action", ""),
            "device": step.get("device", ""),
            "group": step.get("group", ""),
            "command": step.get("command", ""),
            "error": error,
            "message": message,
            "description": step.get("description") or self._auto_description(step),
        }

    def _step_error_message(self, step: dict[str, Any], exc: Exception) -> str:
        """Why the step did not do what it says, in words somebody can act on.

        The same translation, with the same device context, that a panel press
        already gets (``ui_events._command_error``) -- a macro failure and a
        direct press failure are the same failure and must not read two
        different ways because of which control ran them.
        """
        return self._device_error_message(str(step.get("device") or ""), exc)

    def _device_error_message(self, device_id: str, exc: Exception) -> str:
        """``friendly_error`` with this device's name and host filled in.

        The name matters more here than anywhere else: a macro step names the
        device by id, and the id is the one label nobody in the room has ever
        seen. Deferred import to keep this module free of API imports at load
        time, matching ``ui_events`` and the project loader.
        """
        from openavc.api.error_messages import friendly_error

        if not device_id:
            return friendly_error(exc)
        name = self.state.get(f"device.{device_id}.name") or device_id
        host = self.state.get(f"device.{device_id}.host") or ""
        return friendly_error(exc, device=str(name), host=str(host))

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
            # `or {}` for the same reason as the wait_until line below: a step
            # that never filled a field in still carries the key, holding null,
            # so the `{}` default never applied. This one runs while ANNOUNCING
            # step one, so a conditional with no condition killed the whole
            # macro before any of it ran.
            return f"Checking {(step.get('condition') or {}).get('key', '?')}"
        if action == "wait_until":
            cond = step.get("condition") or {}
            key = cond.get("key", "?")
            timeout = step.get("timeout")
            tmo = "no timeout" if timeout is None else f"{timeout}s"
            return f"Waiting for {key} ({tmo})"
        if action == "help.request":
            return "Asking for help"
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
        _step_path: list[int | str] | None = None,
    ) -> tuple[str, str] | None:
        """Execute a single macro step. ``_step_path`` is this step's tree
        location (``[*parent_path, index]``); a conditional uses it to build the
        branch prefix for its then/else sub-steps.

        Returns ``None``, or ``(error, message)`` for a failure the step
        handled itself instead of raising -- only ``group.command`` does that,
        and only when the command reached no device at all. The caller turns it
        into the same ``macro.step_error`` frame a raise would have produced.
        """
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
                return None
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
            results: list[Any] = []
            if tasks:
                log.debug(f"  Macro step: group '{group_id}'.{command} -> {len(tasks)} device(s)")
                results = await asyncio.gather(*tasks, return_exceptions=True)
            # Per-device outcome, in the order the group declares its members
            # rather than attempted-then-skipped: that is the order somebody
            # wrote them in and the order the group reads in the IDE.
            outcome: dict[str, dict[str, Any]] = {}
            for j, result in enumerate(results):
                did = sent_ids[j] if j < len(sent_ids) else "unknown"
                device_name = self.state.get(f"device.{did}.name") or did
                if isinstance(result, Exception):
                    log.error(f"  Group command error on '{device_name}': {result}")
                    outcome[did] = {
                        "device_id": did, "name": device_name, "success": False,
                        "error": str(result),
                        "message": self._device_error_message(did, result),
                    }
                else:
                    outcome[did] = {"device_id": did, "name": device_name, "success": True}
            for did in skipped_ids:
                device_name = self.state.get(f"device.{did}.name") or did
                # Synthesised so an offline member reads the same way it would
                # from a device.command step -- the panel must not learn a
                # second sentence for the same fact.
                offline = ConnectionError(f"Device '{did}' is not connected")
                outcome[did] = {
                    "device_id": did, "name": device_name, "success": False,
                    "error": "Device offline",
                    "message": self._device_error_message(did, offline),
                }
            device_results = [outcome[did] for did in device_ids if did in outcome]
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
            # A group step is a fan-out, so one dead member out of eight is not
            # a failed step -- the room did most of what was asked, and saying
            # so mid-sequence is noise at the moment somebody is starting a
            # class. Reaching NOBODY is a different thing: nothing happened,
            # and until now that was indistinguishable from success. Reported
            # as the first member's own reason, because a name somebody can go
            # and look at beats a count.
            failures = [r for r in device_results if not r["success"]]
            if failures and len(failures) == len(device_results):
                first = failures[0]
                return (str(first.get("error") or ""), str(first.get("message") or ""))
            return None

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
            # Resolved, like every other value a step carries. It used to emit
            # the payload verbatim, so a `$var.` reference in one arrived at the
            # handler as the literal string "$var.source" -- and the same step
            # written as a binding action resolves, which would have made one
            # spelling mean two things.
            payload = self._resolve_params(step.get("payload") or {}, context)
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

            step_path = _step_path or []
            if result:
                then_steps = step.get("then_steps") or []
                if then_steps:
                    log.debug(f"  Conditional: true, running {len(then_steps)} then-step(s)")
                    await self.execute_steps(
                        then_steps, context, macro_id,
                        stop_on_error=stop_on_error,
                        _conditional_depth=_conditional_depth + 1,
                        _call_chain=_call_chain,
                        _path_prefix=[*step_path, "then"],
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
                        _path_prefix=[*step_path, "else"],
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

        elif action == "help.request":
            # The message resolves $var. and $trigger. -- and unlike every
            # other macro parameter it resolves them INSIDE the sentence,
            # because this one is prose going to a person rather than a value
            # going to a device. That is what turns "someone pressed help"
            # into "Lecture Hall 2 projector failed to power on 3 times".
            message = resolve_in_text(
                step.get("message", ""), state=self.state, trigger_ctx=context,
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
