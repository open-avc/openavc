"""
OpenAVC MacroEngine — executes named sequences of actions.

Macros are the bridge between the visual configurator and scripting.
They are ordered sequences of steps: send device commands, set state,
add delays, wait for state conditions, emit events, or call other macros.

This module holds the loaded macros, device groups and plugin actions, and
decides each run's start and end: whether it may start (the circular-call and
depth guards, the macro's own overlap and cooldown), whom it preempts in its
cancel_group, how it ended (``completed``, ``failed``, ``cancelled``,
``skipped``) and the lifecycle event that says so. Running the steps themselves
is ``macro_steps.MacroSteps``, which this class inherits; what a step says to
people is ``macro_text``; a group's fan-out is ``group_command``.

Every way a macro starts -- a trigger, a panel press, a script, a plugin, the
IDE, the AI, another macro -- passes through ``execute``, so a guard written
there holds for all of them.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, NamedTuple, TYPE_CHECKING

from openavc.core.event_bus import EventBus
from openavc.core.group_command import DeviceGroups, GroupCommand
from openavc.core.macro_steps import MacroSteps
from openavc.core.macro_text import StepWording
from openavc.core.state_store import StateStore
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
# and give up after the ceiling so a queued call can't wait forever: the
# start is then skipped. The trigger engine's queue wait reads these two
# numbers from here, and at the ceiling drops its fire the same way.
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
        log.error(f"Macro task failed with nobody waiting on it: {exc}")


class _StartGuards(NamedTuple):
    """A macro's own start guards, read from its definition.

    Enforced in ``execute``, the one point every invocation passes through, so
    a macro fired from a script, REST, the AI tool, a UI press or another macro
    is throttled the same as one fired from a trigger. The defaults
    (overlap=allow, cooldown 0, no cancel_group) leave concurrent runs alone.
    """

    overlap: str
    cooldown: float
    cancel_group: str | None

    @classmethod
    def of(cls, macro: dict[str, Any]) -> _StartGuards:
        return cls(
            overlap=macro.get("overlap") or "allow",
            cooldown=float(macro.get("cooldown_seconds") or 0),
            cancel_group=macro.get("cancel_group"),
        )


class MacroEngine(MacroSteps):
    """Executes named macros — ordered sequences of actions with optional delays."""

    def __init__(
        self,
        state: StateStore,
        events: EventBus,
        devices: DeviceManager,
        broadcast_ws: BroadcastWS | None = None,
        help_requests: Any = None,
        project_device_ids: Callable[[], set[str] | None] | None = None,
        project_provider: Callable[[], Any] | None = None,
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
        # Answers "is this id a device in the project", for the group fan-out.
        # A callable rather than a loaded set because the answer changes on
        # every device edit, and it asks the PROJECT rather than the device
        # manager on purpose: update_device removes and re-adds, clearing both
        # the config entry and every device.<id>.* state key while it awaits a
        # disconnect, so a runtime oracle would call a device that is merely
        # being edited a device that does not exist. Returning None means the
        # question cannot be answered here -- unwired in the plugin and test
        # harnesses, where the check is skipped rather than guessed, the same
        # posture _broadcast_ws and _help take.
        self._project_device_ids = project_device_ids
        self._project_provider = project_provider
        self._macros: dict[str, dict[str, Any]] = {}  # id -> macro config
        self._groups = DeviceGroups()
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
        # Strong references to the tasks start() runs, so a macro nobody is
        # awaiting any more is not collected out from under itself.
        self._detached: set[asyncio.Task] = set()
        self._max_depth = 10  # maximum nested macro call depth
        self._max_conditional_depth = 5  # maximum nesting of conditional steps
        # Plugin-registered actions: action_type -> (handler, plugin_id, label)
        self._plugin_actions: dict[str, tuple[PluginActionHandler, str, str]] = {}

    @property
    def _wording(self) -> StepWording:
        """Every sentence said about a step, from this engine's state, devices,
        project and plugin actions as they are now."""
        return StepWording(
            self.state, self.devices, self._project_provider, self._plugin_actions.get,
        )

    @property
    def _group_command(self) -> GroupCommand:
        """The group fan-out, on this engine's devices and groups as they are now."""
        return GroupCommand(self.devices, self.state, self._wording, self._groups)

    def is_macro_running(self, macro_id: str) -> bool:
        """Check if any invocation of the macro is currently running."""
        return bool(self._running.get(macro_id))

    def has_macro(self, macro_id: str) -> bool:
        """Is this macro loaded?

        Here rather than in the caller because ``_refuse_start`` already
        decides it for ``execute`` and ``start``, and a second reading of the
        same dict is what drifts. A caller that starts a macro in the
        background its own way (a panel press) has no other way to ask: the
        refusal ``execute`` raises happens inside a task nobody is holding.
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
        return [
            (mid, task)
            for mid, task_set in self._running.items()
            if self._macros.get(mid, {}).get("cancel_group") == group
            for task in task_set
            if task is not exclude_task
        ]

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
        self._groups.load(groups)

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

    def unregister_plugin_actions(self, plugin_id: str) -> None:
        """Remove all macro actions registered by a plugin."""
        for action_type in [
            k for k, (_, pid, _) in self._plugin_actions.items() if pid == plugin_id
        ]:
            del self._plugin_actions[action_type]

    def plugin_action_types(self) -> frozenset[str]:
        """Action types plugins have registered — valid steps beyond the built-ins.

        Anything validating a macro needs this: the built-in steps are a static
        list, but a step naming a plugin action is equally runnable, and only
        this engine knows which ones are loaded right now.
        """
        return frozenset(self._plugin_actions)

    async def execute(
        self, macro_id: str, context: dict[str, Any] | None = None,
        _call_chain: frozenset[str] | None = None,
    ) -> str:
        """
        Execute a macro by ID. Returns how the run ended.

        One of ``completed`` (every step ran), ``failed`` (at least one step
        did not, whether or not the run carried on past it), ``cancelled``
        (something cancelled it mid-run — a ``POST /cancel`` or a cancel_group
        preemption), or ``skipped`` (the macro's own overlap/cooldown guard
        refused the start).

        It REPORTS rather than raises, and that is the point. The outer
        catch-all isolates a macro's failure from whatever fired it: a trigger,
        a panel press, a script and a plugin must not inherit it. But isolation
        was being done by silence — every one of those endings returned None,
        so the operator door answered ``executed`` for all four and a run whose
        every step failed was indistinguishable from a clean one. A caller that
        ignores the return value behaves exactly as before.

        A NESTED call is different and still raises (see ``nested_call``): a
        subroutine's failure belongs to the step that awaited it.

        Args:
            macro_id: The macro to execute.
            context: Optional context dict passed through to steps.
            _call_chain: Internal — tracks the current execution chain to
                detect circular/recursive calls without blocking independent
                concurrent chains.
        """
        # An awaited subroutine must return its failure/cancellation to the
        # calling step. Only the outer invocation owns the event-only outcome;
        # swallowing it here would let dependent parent steps keep running.
        nested_call = bool(_call_chain)
        _call_chain = self._chain_into(macro_id, _call_chain)
        macro = self._macros[macro_id]
        # Publish the chain to this task's context so handler tasks spawned
        # by this macro's steps (and any macros.execute() they make) inherit
        # it — see active_call_chain().
        _chain_token = _active_call_chain.set(_call_chain)

        name = macro.get("name", macro_id)
        steps = macro.get("steps", [])
        stop_on_error = macro.get("stop_on_error", False)
        guards = _StartGuards.of(macro)
        task = asyncio.current_task()

        blocked, cancelled = await self._claim_start(macro_id, name, task, guards)
        if blocked is not None:
            _active_call_chain.reset(_chain_token)
            await self._report_skipped(macro_id, name, blocked)
            return "skipped"

        # Wait for preempted tasks to fully unwind before we start sending
        # commands — A50.
        await self._drain_cancelled(cancelled)

        log.info(f"Executing macro '{name}' ({len(steps)} steps)")
        await self.events.emit(
            f"macro.started.{macro_id}",
            {"macro_id": macro_id, "name": name, "total_steps": len(steps)},
        )

        try:
            failed_steps = await self.execute_steps(
                steps, context, macro_id, stop_on_error, _call_chain=_call_chain
            )
            return await self._report_completed(macro_id, name, task, failed_steps)
        except asyncio.CancelledError:
            log.info(f"Macro '{name}' was cancelled")
            await self._announce_end(macro_id, name, task, "cancelled")
            if nested_call:
                raise
            return "cancelled"
        except Exception as e:  # Catch-all: isolates macro execution errors
            log.exception(f"Macro '{name}' failed")
            await self._announce_end(macro_id, name, task, "error", error=str(e))
            if nested_call:
                raise
            return "failed"
        finally:
            _active_call_chain.reset(_chain_token)
            self._release(macro_id, task)

    async def _wait_for_slot(self, macro_id: str, name: str) -> str | None:
        """overlap=queue: hold this start until any in-flight invocation of the
        same macro finishes, then let it go on to claim the slot.

        The recursion guard already stops a macro queueing behind itself; this
        waits on independent concurrent invocations. Bounded so a wedged run
        can't make a queued call wait forever: past the ceiling it returns the
        reason this start is skipped. Starting it beside the run it waited for
        would send both to the equipment, which is what the setting exists to
        prevent.
        """
        waited = 0.0
        while self.is_macro_running(macro_id):
            if waited >= _QUEUE_MAX_WAIT_SECONDS:
                log.warning(
                    f"Macro '{name}' overlap=queue: gave up after "
                    f"{_QUEUE_MAX_WAIT_SECONDS:g}s waiting for the running "
                    f"invocation to finish; skipping this start"
                )
                return (
                    "overlap=queue and the running instance did not finish "
                    f"within {_QUEUE_MAX_WAIT_SECONDS:g}s"
                )
            await asyncio.sleep(_QUEUE_POLL_SECONDS)
            waited += _QUEUE_POLL_SECONDS
        return None

    async def _claim_start(
        self, macro_id: str, name: str, task: asyncio.Task | None, guards: _StartGuards,
    ) -> tuple[str | None, list[tuple[str, asyncio.Task]]]:
        """Take this run's slot, or say why it may not start. Under
        overlap=queue it first waits for the running invocation
        (``_wait_for_slot``), and a wait that reaches its ceiling is a refusal.

        Returns ``(reason, [])`` for a start the macro's own guard refuses,
        else ``(None, preempted)``: the cancel_group members it cancelled.

        Registering the task in ``_running`` and choosing which group members
        to preempt happen together under the start lock. Otherwise two macros
        in the same cancel_group reaching this point within one event-loop tick
        could both register, then each cancel the other and neither run (A49).
        The lock is released once the targets are cancelled; waiting for them
        to unwind happens outside it, so unrelated macros can still start.

        With no current task (a synthetic execute()) there is nothing to
        register: the throttle and the preemption still apply, without the lock.
        """
        if guards.overlap == "queue":
            gave_up = await self._wait_for_slot(macro_id, name)
            if gave_up is not None:
                return gave_up, []
        if task is None:
            return self._claim(macro_id, None, guards)
        async with self._start_lock:
            return self._claim(macro_id, task, guards)

    def _claim(
        self, macro_id: str, task: asyncio.Task | None, guards: _StartGuards,
    ) -> tuple[str | None, list[tuple[str, asyncio.Task]]]:
        blocked = self._throttle_reason(macro_id, guards.overlap, guards.cooldown)
        if blocked is not None:
            return blocked, []
        if task is not None:
            self._running.setdefault(macro_id, set()).add(task)
        if guards.cooldown > 0:
            self._last_started_monotonic[macro_id] = time.monotonic()
        return None, self._preempt(guards.cancel_group, task)

    def _preempt(
        self, cancel_group: str | None, task: asyncio.Task | None,
    ) -> list[tuple[str, asyncio.Task]]:
        """Cancel every other running member of ``cancel_group``."""
        if not cancel_group:
            return []
        cancelled = self._collect_group_targets(cancel_group, exclude_task=task)
        for mid, t in cancelled:
            log.info(f"Preempting macro '{mid}' (cancel_group '{cancel_group}')")
            t.cancel()
        return cancelled

    async def _report_skipped(self, macro_id: str, name: str, reason: str) -> None:
        log.info(f"Macro '{name}' skipped — {reason}")
        # A run that never starts is still an outcome somebody asked for.
        # Every other way a run ends has a lifecycle event; without this
        # one a caller waiting on the outcome (the WebSocket's
        # macro.execute acks receipt and nothing more) waits forever.
        # Emitted outside the start lock: a handler that starts a macro
        # of its own takes that lock too.
        await self.events.emit(
            f"macro.skipped.{macro_id}",
            {"macro_id": macro_id, "name": name, "reason": reason},
        )

    async def _report_completed(
        self, macro_id: str, name: str, task: asyncio.Task | None, failed_steps: int,
    ) -> str:
        outcome = "failed" if failed_steps else "completed"
        # Still `macro.completed`, and deliberately: the run DID reach the
        # end, which is what that event has always meant and what the IDE's
        # progress display keys off. What it never carried is how it went,
        # so a run that stepped over three dead devices marked itself
        # complete and said so in the same words as a clean one.
        await self._announce_end(
            macro_id, name, task, "completed",
            outcome=outcome, failed_steps=failed_steps,
        )
        log.info(
            f"Macro '{name}' completed"
            if not failed_steps
            else f"Macro '{name}' completed with {failed_steps} failed step(s)"
        )
        return outcome

    async def _announce_end(
        self, macro_id: str, name: str, task: asyncio.Task | None, ending: str,
        **fields: Any,
    ) -> None:
        """``macro.<ending>.<macro_id>``, with the run released first (see
        ``_release``)."""
        self._release(macro_id, task)
        await self.events.emit(
            f"macro.{ending}.{macro_id}", {"macro_id": macro_id, "name": name, **fields},
        )

    def _release(self, macro_id: str, task: asyncio.Task | None) -> None:
        """Stop counting ``task`` as a run of ``macro_id``. Idempotent.

        Called before a run's end is announced, not after: the handlers of
        ``macro.completed`` / ``macro.cancelled`` / ``macro.error`` run inside
        that emit, so anything they start (an event trigger, a script) or ask
        would otherwise find the finished run still in flight, and
        ``overlap: skip`` would turn a macro started again on its own end away.
        """
        if task is None:
            return
        task_set = self._running.get(macro_id)
        if task_set is not None:
            task_set.discard(task)
            if not task_set:
                self._running.pop(macro_id, None)

    def _chain_into(
        self, macro_id: str, call_chain: frozenset[str] | None,
    ) -> frozenset[str]:
        """The chain a run of ``macro_id`` runs inside, once ``_refuse_start``
        lets it in from ``call_chain``."""
        call_chain = call_chain or frozenset()
        self._refuse_start(macro_id, call_chain)
        return call_chain | {macro_id}

    def _refuse_start(self, macro_id: str, call_chain: frozenset[str]) -> None:
        """Raise ``ValueError`` for a macro that cannot start from this chain:
        one that does not exist, one already in the chain, or one past the
        nesting limit. ``execute`` asks before it runs, and ``start`` asks
        before it makes a task, where the refusal still reaches the caller."""
        if not self.has_macro(macro_id):
            raise ValueError(f"Macro '{macro_id}' not found")
        if macro_id in call_chain:
            raise ValueError(
                f"Macro '{macro_id}' blocked — circular/recursive call detected "
                f"(call chain: {' -> '.join(call_chain)} -> {macro_id})"
            )
        if len(call_chain) >= self._max_depth:
            raise ValueError(
                f"Macro '{macro_id}' blocked — max nesting depth ({self._max_depth}) reached"
            )

    def start(
        self,
        macro_id: str,
        context: dict[str, Any] | None = None,
        *,
        _call_chain: frozenset[str] | None = None,
    ) -> asyncio.Task:
        """Run a macro in its own task and return that task at once.

        The run is the engine's from here: it is held in ``_detached`` until
        it ends, its failure is logged rather than lost, and it answers to the
        same cancel and cancel_group as any other run. What started it can
        finish, time out or be reloaded without stopping it, which is the
        point -- a script's timer that awaited a long macro was cut off at the
        handler limit partway through the steps.

        Raises ``ValueError`` for a macro that does not exist or would
        re-enter ``_call_chain``, before the task exists, so the caller hears
        it. Needs a running event loop.
        """
        self._refuse_start(macro_id, _call_chain or frozenset())
        task = asyncio.create_task(
            self.execute(macro_id, context, _call_chain=_call_chain)
        )
        self._detached.add(task)
        task.add_done_callback(self._detached.discard)
        task.add_done_callback(_log_detached_failure)
        return task

    async def execute_detached(
        self,
        macro_id: str,
        context: dict[str, Any] | None = None,
        *,
        wait_seconds: float | None = None,
    ) -> str:
        """Run a macro in its own task, waiting only ``wait_seconds`` for it.

        Returns what ``execute`` reported — ``completed``, ``failed``,
        ``cancelled`` or ``skipped`` — when the macro finished inside that
        window, and ``"running"`` when it did not. The macro keeps going
        either way — nothing here cancels it.

        This is the shape the OPERATOR doors need: the IDE's run button and
        the cloud AI's ``run_macro``, where a person asked for a macro and is
        holding a socket open waiting for the answer. ``execute`` runs the
        steps in the CALLER'S task, so a `wait_until` with no timeout — a
        legal, documented shape meaning "wait for the projector" — held that
        request until the socket died, and the caller was told the macro had
        FAILED when it was running perfectly well.

        The automation doors keep awaiting ``execute`` directly: a trigger, a
        script, a plugin and a panel press have no socket to lose, and one of
        them waiting for a device is the feature. A script that must not wait
        (its handler limit would cut the macro off) calls ``start``.

        Raises ``ValueError`` for a macro that does not exist, so a caller can
        still answer 404 — ``start`` refuses before it makes the task, since
        a refusal inside the task would reach nobody.
        """
        # Read at call time, not bound as a default: the constant is the knob
        # a test turns down, and a default argument would freeze it at import.
        if wait_seconds is None:
            wait_seconds = OPERATOR_RUN_WAIT_SECONDS

        task = self.start(macro_id, context)
        try:
            # shield, so the timeout ends the WAIT and not the macro.
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_seconds)
        except asyncio.TimeoutError:
            log.info(
                f"Macro '{macro_id}' is still running after {wait_seconds:.0f}s "
                f"— answering the caller and letting it continue"
            )
            return "running"
        # The outcome the run reported, not the fact that awaiting it returned.
        # `executed` was the only word this ever said, and it said it for a
        # macro whose every step failed and for one that was cancelled out from
        # under the caller.
        return task.result()
