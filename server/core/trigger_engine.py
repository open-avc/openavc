"""
OpenAVC TriggerEngine — fires macros from schedules, state changes, events, and startup.

Reads trigger definitions from macro configs, registers listeners, manages
execution control (debounce, delay+re-check, cooldown, overlap), and prevents
circular trigger chains.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, TYPE_CHECKING

from server.core.condition_eval import eval_operator
from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.event_bus import EventBus
    from server.core.macro_engine import MacroEngine
    from server.core.state_store import StateStore

log = get_logger(__name__)

# overlap="allow" intentionally permits concurrent runs of the same trigger,
# but a trigger whose macro re-triggers itself (via a state change / event
# feedback loop) would otherwise spawn fire tasks without bound. This caps the
# number of simultaneously-active fires per trigger as a runaway backstop —
# high enough never to interfere with real concurrent use, low enough to stop
# an accidental infinite self-trigger from pegging the event loop.
_MAX_OVERLAP_ALLOW_DEPTH = 25

# Expected cron poll interval. The loop sleeps this long between checks; a gap
# materially larger than this means the event loop fell behind (load / host
# suspend) and schedules may need catch-up.
_CRON_POLL_INTERVAL = 30

# overlap="queue" wait: poll this often for the running macro to finish, and
# give up after this long so a wedged macro can't pin a queued fire forever.
_QUEUE_POLL_INTERVAL = 1.0
_QUEUE_MAX_WAIT_SECONDS = 300.0

# How long stop() waits for cancelled pre-fire tasks to unwind — matches the
# macro engine's cancel grace so the two teardown paths behave alike.
_CANCEL_DRAIN_SECONDS = 2.0


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback to log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(f"Background task {task.get_name()!r} failed: {exc}", exc_info=exc)

try:
    from croniter import croniter

    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False


class _TriggerState:
    """Runtime state for a single trigger."""

    __slots__ = (
        "trigger",
        "macro_id",
        "macro_name",
        "last_fired",
        "debounce_task",
        "delay_task",
        "pending_queue",
    )

    def __init__(self, trigger: dict[str, Any], macro_id: str, macro_name: str):
        self.trigger = trigger
        self.macro_id = macro_id
        self.macro_name = macro_name
        self.last_fired: float = 0
        self.debounce_task: asyncio.Task | None = None
        self.delay_task: asyncio.Task | None = None
        self.pending_queue: list[asyncio.Task] = []


class TriggerEngine:
    """Manages automatic macro triggers: schedules, state changes, events, startup."""

    def __init__(
        self, state: StateStore, events: EventBus, macros: MacroEngine
    ):
        self.state = state
        self.events = events
        self.macros = macros

        self._triggers: dict[str, _TriggerState] = {}  # trigger_id -> state
        self._event_handler_ids: list[str] = []
        self._state_sub_ids: list[str] = []
        self._cron_task: asyncio.Task | None = None
        self._startup_tasks: list[asyncio.Task] = []
        # trigger_id -> count of fires currently active for it. A counter (not
        # a membership set) so overlap="allow" can permit bounded concurrent
        # runs while a true self-trigger loop is still capped.
        self._active_trigger_depth: dict[str, int] = {}
        # Strong references to fire-and-forget tasks (macro fires + status
        # emits) so the GC can't collect a still-pending task — asyncio only
        # holds a weak ref. Self-pruning via a done-callback.
        self._bg_tasks: set[asyncio.Task] = set()
        # Fire tasks currently inside MacroEngine.execute(). Once a fire
        # reaches the macro engine, the macro's lifetime belongs to the macro
        # engine's registry (cancel_all, cancel-group preemption) — stop()
        # must not kill it just because listeners are being rebuilt, which
        # happens on ANY device/connection/variable/plugin edit. The engine's
        # reconcile cancels running macros only when macro/group definitions
        # actually changed, and shutdown cancels them via macros.cancel_all().
        self._macro_exec_tasks: set[asyncio.Task] = set()
        # Cron duplicate-fire guard, instance-scoped so it survives a hot
        # reload (stop -> load_triggers -> start) instead of resetting and
        # double-firing a schedule near its scheduled minute.
        self._cron_last_fires: dict[str, datetime] = {}
        # In-process monotonic baseline per trigger for the cooldown check,
        # immune to wall-clock corrections. Survives a hot reload (same
        # process); absent only after a full restart, where the persisted
        # wall-clock timestamp is the fallback.
        self._last_fired_monotonic: dict[str, float] = {}
        self._running = False

    def _spawn(self, coro: Any) -> asyncio.Task:
        """Launch a fire-and-forget coroutine with a strong ref + error log.

        Holding the task in ``_bg_tasks`` keeps it alive (asyncio only weakly
        references tasks, so an unreferenced one can be GC'd before it runs);
        the done-callback both discards it and logs any unhandled exception.
        """
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        task.add_done_callback(_log_task_exception)
        return task

    def load_triggers(self, macros_data: list[dict[str, Any]]) -> None:
        """Load trigger definitions from all macros."""
        self._triggers.clear()
        count = 0
        for macro in macros_data:
            macro_id = macro.get("id", "")
            macro_name = macro.get("name", macro_id)
            for trigger in macro.get("triggers", []):
                trigger_id = trigger.get("id", "")
                if trigger_id:
                    ts = _TriggerState(trigger, macro_id, macro_name)
                    # Restore cooldown timestamp from state. The state store is
                    # in-memory, so this preserves cooldowns across hot-reload
                    # (load_triggers is called again on project reload) but not
                    # across full server restart — StatePersister only watches
                    # variables flagged persist=True, never system.* keys.
                    persisted = self.state.get(f"system.trigger.{trigger_id}.last_fired")
                    if persisted and isinstance(persisted, (int, float)):
                        ts.last_fired = float(persisted)
                    self._triggers[trigger_id] = ts
                    count += 1
        # Drop cron-dedup / cooldown baselines for triggers that no longer
        # exist (renamed/removed), but keep them for surviving ids so a hot
        # reload preserves both the no-double-fire and cooldown guarantees.
        live_ids = set(self._triggers)
        self._cron_last_fires = {
            k: v for k, v in self._cron_last_fires.items() if k in live_ids
        }
        self._last_fired_monotonic = {
            k: v for k, v in self._last_fired_monotonic.items() if k in live_ids
        }
        if count:
            log.info(f"Loaded {count} trigger(s)")

    async def start(self, fire_startup: bool = True) -> None:
        """Register all trigger listeners and start the cron loop.

        ``fire_startup`` gates the one-shot firing of ``startup`` triggers.
        It belongs to a genuine (re)start of the system — engine boot or a
        whole new project arriving — not to the trigger rebuild an
        incremental save performs, where re-firing would re-run power-on
        automation in a live room on every edit.
        """
        self._running = True

        has_cron = False
        for ts in self._triggers.values():
            t = ts.trigger
            if not t.get("enabled", True):
                continue
            ttype = t.get("type", "")

            if ttype == "state_change":
                state_key = t.get("state_key", "")
                if state_key:
                    sub_id = self.state.subscribe(
                        state_key,
                        lambda key, old, new, src, _ts=ts: self._on_state_change(
                            _ts, key, old, new, src
                        ),
                    )
                    self._state_sub_ids.append(sub_id)

            elif ttype == "event":
                pattern = t.get("event_pattern", "")
                if pattern:
                    handler_id = self.events.on(
                        pattern,
                        lambda evt, payload, _ts=ts: self._on_event(_ts, evt, payload),
                    )
                    self._event_handler_ids.append(handler_id)

            elif ttype == "schedule":
                has_cron = True

            elif ttype == "startup":
                if fire_startup:
                    delay = t.get("delay_seconds", 0)
                    startup_task = asyncio.create_task(self._fire_startup(ts, delay))
                    startup_task.add_done_callback(_log_task_exception)
                    self._startup_tasks.append(startup_task)

        # Start cron loop if any schedule triggers exist
        if has_cron and HAS_CRONITER:
            self._cron_task = asyncio.create_task(self._cron_loop())
            self._cron_task.add_done_callback(_log_task_exception)
            log.info("Trigger cron loop started")
        elif has_cron and not HAS_CRONITER:
            log.warning("Schedule triggers defined but croniter not installed")

        log.info("TriggerEngine started")

    async def stop(self) -> None:
        """Unregister all listeners, cancel pending (pre-fire) tasks.

        Fires that have already reached ``MacroEngine.execute()`` are
        deliberately left running: their lifetime belongs to the macro
        engine's registry, and which macros survive an edit must not depend
        on what fired them (the reconcile cancels running macros only on a
        macro/group diff; shutdown cancels them via ``macros.cancel_all()``).
        Everything this method does cancel is awaited with a short drain so
        teardown never proceeds while cancelled fires are still unwinding.
        """
        self._running = False
        cancelled: list[asyncio.Task] = []

        def _cancel(task: asyncio.Task | None) -> None:
            if task is None or task.done() or task in self._macro_exec_tasks:
                return
            task.cancel()
            cancelled.append(task)

        # Cancel cron loop
        if self._cron_task and not self._cron_task.done():
            self._cron_task.cancel()
            try:
                await self._cron_task
            except asyncio.CancelledError:
                pass
            self._cron_task = None

        # Cancel startup tasks
        for task in self._startup_tasks:
            _cancel(task)
        self._startup_tasks.clear()

        # Cancel all pending debounce/delay/queued tasks
        for ts in self._triggers.values():
            _cancel(ts.debounce_task)
            _cancel(ts.delay_task)
            for task in ts.pending_queue:
                _cancel(task)
            ts.pending_queue.clear()

        # Cancel pre-fire / status-emit tasks (snapshot first: the
        # done-callback mutates the set as each task settles).
        for task in list(self._bg_tasks):
            _cancel(task)

        # Unsubscribe state listeners
        for sub_id in self._state_sub_ids:
            self.state.unsubscribe(sub_id)
        self._state_sub_ids.clear()

        # Unregister event handlers
        for handler_id in self._event_handler_ids:
            self.events.off(handler_id)
        self._event_handler_ids.clear()

        # Drain the cancellations (same grace the macro engine gives
        # preempted macros) so nothing is still mid-unwind when the caller
        # starts deleting state keys or rebuilding trigger definitions.
        if cancelled:
            try:
                await asyncio.wait(cancelled, timeout=_CANCEL_DRAIN_SECONDS)
            except asyncio.CancelledError:
                pass

        self._active_trigger_depth.clear()
        log.info("TriggerEngine stopped")

    # --- Trigger type handlers ---

    def _on_state_change(
        self,
        ts: _TriggerState,
        key: str,
        old_value: Any,
        new_value: Any,
        source: str,
    ) -> None:
        """Called when a subscribed state key changes."""
        if not self._running:
            return
        t = ts.trigger

        # Check state_operator match
        op = t.get("state_operator", "any")
        target = t.get("state_value")
        if op != "any":
            try:
                matched = eval_operator(op, new_value, target)
            except ValueError:
                log.error(f"Trigger {t['id']} has invalid operator '{op}'")
                return
            if not matched:
                log.debug(
                    f"Trigger {t['id']} skipped — state_operator {op} not met "
                    f"(actual={new_value!r}, target={target!r})"
                )
                return

        self._initiate_fire(ts, {"key": key, "old_value": old_value, "new_value": new_value})

    async def _on_event(
        self, ts: _TriggerState, event: str, payload: dict[str, Any]
    ) -> None:
        """Called when a subscribed event fires."""
        if not self._running:
            return
        self._initiate_fire(ts, {"event": event, **payload})

    async def _fire_startup(self, ts: _TriggerState, delay: float) -> None:
        """Fire a startup trigger after optional delay."""
        if delay > 0:
            await asyncio.sleep(delay)
        if not self._running:
            return
        await self._execute_trigger(ts, {"trigger_type": "startup"})

    async def _cron_loop(self) -> None:
        """Check schedule triggers on a fixed interval, with catch-up.

        ``last_check`` is the wall-clock time of the previous poll. In steady
        state a schedule fires when its most recent occurrence (``prev``) fell
        after ``last_check`` — so even if the loop falls behind (heavy load,
        host suspend) a schedule that came due in the gap still fires once
        (catch-up), instead of being silently dropped. The cron dedup map is
        instance state (``self._cron_last_fires``) so it survives a hot reload
        and won't double-fire a schedule near its scheduled minute.
        """
        # last_check is reset per start() on purpose: after a fresh start or
        # reload we treat it like a cold start (don't replay schedules from
        # before the engine was running). Cross-reload double-fire is guarded
        # by the instance-scoped dedup map instead.
        last_check: datetime | None = None
        try:
            while True:
                await asyncio.sleep(_CRON_POLL_INTERVAL)
                if not self._running:
                    return
                now = datetime.now()
                for ts in self._triggers.values():
                    t = ts.trigger
                    if t.get("type") != "schedule" or not t.get("enabled", True):
                        continue
                    cron_expr = t.get("cron", "")
                    if not cron_expr:
                        continue
                    try:
                        prev, should_fire = self._schedule_prev_due(
                            cron_expr, now, last_check
                        )
                        if not should_fire:
                            continue
                        late = (now - prev).total_seconds()
                        if last_check is not None and late > 60:
                            log.warning(
                                f"Cron trigger '{t['id']}' caught up a delayed "
                                f"schedule ({late:.0f}s late; poll loop fell behind)"
                            )
                        # Prevent duplicate fires (cross-reload safety net)
                        last = self._cron_last_fires.get(t["id"])
                        if last and (last - prev).total_seconds() >= 0:
                            continue
                        self._cron_last_fires[t["id"]] = now
                        self._initiate_fire(ts, {"trigger_type": "schedule", "cron": cron_expr})
                    except Exception:  # Catch-all: isolates individual trigger evaluation errors
                        log.exception(f"Error checking cron trigger '{t['id']}'")
                last_check = now
        except asyncio.CancelledError:
            return

    def _schedule_prev_due(
        self, cron_expr: str, now: datetime, last_check: datetime | None
    ) -> tuple[datetime, bool]:
        """Decide whether a schedule's most recent occurrence should fire.

        Returns ``(prev_occurrence, should_fire)``. On a cold start
        (``last_check is None``) only a very recent occurrence fires, so a
        schedule due before the engine started isn't replayed. In steady
        state any occurrence that came due since the previous poll fires —
        including one the loop was too late to see within the nominal poll
        window (catch-up after falling behind), which the old fixed 60s
        staleness cutoff would have silently dropped.
        """
        prev = croniter(cron_expr, now).get_prev(datetime)
        if last_check is None:
            return prev, (now - prev).total_seconds() <= 60
        return prev, prev > last_check

    # --- Execution pipeline ---

    def _initiate_fire(self, ts: _TriggerState, context: dict[str, Any]) -> None:
        """Start the fire pipeline (debounce -> delay -> execute)."""
        t = ts.trigger
        trigger_id = t["id"]

        # 1. Enabled check
        if not t.get("enabled", True):
            return

        # 2. Cooldown check
        cooldown = t.get("cooldown_seconds", 0)
        if cooldown > 0:
            mono_base = self._last_fired_monotonic.get(trigger_id)
            if mono_base is not None:
                # In-process baseline — immune to wall-clock corrections
                # (NTP step on a Pi/embedded host after boot).
                elapsed = time.monotonic() - mono_base
            else:
                # Only a persisted wall-clock timestamp (prior session).
                # A backward clock correction makes last_fired look like the
                # future (negative elapsed); treat the cooldown as elapsed
                # rather than suppressing the trigger indefinitely.
                elapsed = time.time() - ts.last_fired
                if elapsed < 0:
                    elapsed = cooldown
            if elapsed < cooldown:
                log.debug(f"Trigger {trigger_id} skipped — cooldown ({elapsed:.1f}s < {cooldown}s)")
                self._spawn(self.events.emit(
                    "trigger.skipped",
                    {"trigger_id": trigger_id, "reason": "cooldown"},
                ))
                return

        # 3. Debounce
        debounce = t.get("debounce_seconds", 0)
        if debounce > 0:
            # Cancel any existing debounce timer
            if ts.debounce_task and not ts.debounce_task.done():
                ts.debounce_task.cancel()
            ts.debounce_task = asyncio.create_task(
                self._debounced_fire(ts, debounce, context)
            )
            self._spawn(self.events.emit(
                "trigger.pending",
                {
                    "trigger_id": trigger_id,
                    "macro_id": ts.macro_id,
                    "reason": "debounce",
                    "wait_seconds": debounce,
                },
            ))
            return

        # No debounce — proceed to delay or direct fire
        self._schedule_fire(ts, context)

    async def _debounced_fire(
        self, ts: _TriggerState, debounce: float, context: dict[str, Any]
    ) -> None:
        """Wait for debounce period, then proceed if no new triggers."""
        try:
            await asyncio.sleep(debounce)
            if self._running:
                self._schedule_fire(ts, context)
        except asyncio.CancelledError:
            return

    def _schedule_fire(self, ts: _TriggerState, context: dict[str, Any]) -> None:
        """Apply delay+re-check or fire immediately."""
        t = ts.trigger
        delay = t.get("delay_seconds", 0)

        if delay > 0:
            # Cancel any existing delay
            if ts.delay_task and not ts.delay_task.done():
                ts.delay_task.cancel()
            ts.delay_task = asyncio.create_task(
                self._delayed_fire(ts, delay, context)
            )
            self._spawn(self.events.emit(
                "trigger.pending",
                {
                    "trigger_id": t["id"],
                    "macro_id": ts.macro_id,
                    "reason": "delay",
                    "wait_seconds": delay,
                },
            ))
        else:
            self._spawn(self._execute_trigger(ts, context))

    async def _delayed_fire(
        self, ts: _TriggerState, delay: float, context: dict[str, Any]
    ) -> None:
        """Wait, then re-check conditions before firing."""
        try:
            await asyncio.sleep(delay)
            if not self._running:
                return

            t = ts.trigger

            # Re-check triggering condition for state_change triggers
            if t.get("type") == "state_change":
                state_key = t.get("state_key", "")
                op = t.get("state_operator", "any")
                target = t.get("state_value")
                if op != "any":
                    current = self.state.get(state_key)
                    try:
                        matched = eval_operator(op, current, target)
                    except ValueError:
                        log.error(f"Trigger {t['id']} has invalid operator '{op}'")
                        return
                    if not matched:
                        log.info(
                            f"Trigger {t['id']} skipped — state reverted during delay"
                        )
                        await self.events.emit(
                            "trigger.skipped",
                            {"trigger_id": t["id"], "reason": "state_reverted"},
                        )
                        return

            # Re-check guard conditions for all trigger types with delays
            conditions = t.get("conditions", [])
            if conditions and not self._check_conditions(conditions):
                log.info(f"Trigger {t['id']} skipped — guard conditions no longer met after delay")
                await self.events.emit(
                    "trigger.skipped",
                    {"trigger_id": t["id"], "reason": "guard_not_met"},
                )
                return

            await self._execute_trigger(ts, context)
        except asyncio.CancelledError:
            return

    async def _execute_trigger(
        self, ts: _TriggerState, context: dict[str, Any]
    ) -> None:
        """Final execution: check guards, overlap, circular, then fire."""
        t = ts.trigger
        trigger_id = t["id"]
        macro_id = ts.macro_id

        # 5. Guard conditions
        conditions = t.get("conditions", [])
        if not self._check_conditions(conditions):
            log.debug(f"Trigger {trigger_id} skipped — conditions not met")
            await self.events.emit(
                "trigger.skipped",
                {"trigger_id": trigger_id, "reason": "conditions_not_met"},
            )
            return

        # 6. Overlap policy
        overlap = t.get("overlap", "skip")
        if self.macros.is_macro_running(macro_id):
            if overlap == "skip":
                log.debug(f"Trigger {trigger_id} skipped — macro already running")
                await self.events.emit(
                    "trigger.skipped",
                    {"trigger_id": trigger_id, "reason": "macro_running"},
                )
                return
            elif overlap == "queue":
                # Wait for running macro to finish, then re-check and fire. The
                # task removes itself from pending_queue when it settles so the
                # queue can't grow without bound and queue_position stays the
                # count of still-pending fires (not an ever-climbing total).
                task = asyncio.create_task(self._queued_fire(ts, context))
                ts.pending_queue.append(task)
                task.add_done_callback(
                    lambda tk, _ts=ts: self._on_queued_done(_ts, tk)
                )
                self._spawn(self.events.emit(
                    "trigger.queued",
                    {
                        "trigger_id": trigger_id,
                        "macro_id": macro_id,
                        "queue_position": len(ts.pending_queue),
                    },
                ))
                return
            # overlap == "allow" falls through

        # 7. Re-entrancy guard. _active_trigger_depth counts the fires
        #    currently active for this trigger (per trigger, not per macro —
        #    two different triggers may legitimately fire the same macro).
        depth = self._active_trigger_depth.get(trigger_id, 0)
        if overlap == "allow":
            # The user opted into concurrent runs; permit re-entry but cap the
            # depth so a macro that re-triggers itself can't recurse forever.
            if depth >= _MAX_OVERLAP_ALLOW_DEPTH:
                log.warning(
                    f"Trigger {trigger_id} blocked — overlap=allow hit the max "
                    f"concurrent depth ({_MAX_OVERLAP_ALLOW_DEPTH}); likely a "
                    f"runaway self-trigger on macro '{ts.macro_name}'"
                )
                await self.events.emit(
                    "trigger.skipped",
                    {"trigger_id": trigger_id, "reason": "max_depth"},
                )
                return
        elif depth > 0:
            # skip/queue already handled the running-macro case above; a
            # non-zero depth here means this trigger re-fired itself (a
            # circular state/event feedback chain).
            log.warning(
                f"Trigger {trigger_id} blocked — circular chain detected "
                f"(trigger already active for macro '{ts.macro_name}')"
            )
            await self.events.emit(
                "trigger.skipped",
                {"trigger_id": trigger_id, "reason": "circular"},
            )
            return

        # 8. Fire
        await self._fire_macro(ts, context)

    def _on_queued_done(self, ts: _TriggerState, task: asyncio.Task) -> None:
        """Remove a settled queued-fire task from the trigger's queue."""
        try:
            ts.pending_queue.remove(task)
        except ValueError:
            pass  # already cleared by stop()
        _log_task_exception(task)

    async def _queued_fire(
        self, ts: _TriggerState, context: dict[str, Any]
    ) -> None:
        """Wait for running macro to finish, re-check conditions, then fire."""
        macro_id = ts.macro_id
        # Poll until the macro finishes, giving up after _QUEUE_MAX_WAIT_SECONDS
        # so a wedged macro can't pin the queued fire indefinitely.
        trigger_id = ts.trigger.get("id", "")
        max_polls = max(1, round(_QUEUE_MAX_WAIT_SECONDS / _QUEUE_POLL_INTERVAL))
        for _ in range(max_polls):
            await asyncio.sleep(_QUEUE_POLL_INTERVAL)
            if not self._running:
                return
            if not ts.trigger.get("enabled", True):
                log.debug(f"Trigger {trigger_id} disabled during queue wait — aborting")
                return
            if not self.macros.is_macro_running(macro_id):
                break
        else:
            log.warning(f"Trigger {ts.trigger['id']} queue timeout — macro still running")
            return

        # Re-check conditions
        conditions = ts.trigger.get("conditions", [])
        if not self._check_conditions(conditions):
            log.debug(f"Trigger {ts.trigger['id']} skipped — conditions not met after queue wait")
            return

        await self._fire_macro(ts, context)

    async def _fire_macro(
        self, ts: _TriggerState, context: dict[str, Any]
    ) -> None:
        """Actually execute the macro and emit trigger events."""
        t = ts.trigger
        trigger_id = t["id"]
        macro_id = ts.macro_id
        trigger_type = t.get("type", "unknown")

        ts.last_fired = time.time()
        # Monotonic baseline for the cooldown check — immune to wall-clock
        # jumps within this process (incl. across a hot reload). The wall-clock
        # value below is the cross-restart fallback / display value.
        self._last_fired_monotonic[trigger_id] = time.monotonic()
        # Stash cooldown timestamp in the in-memory state store so it's
        # picked up by load_triggers on a hot-reload (see paired read).
        # Not durable across server restart — see comment in load_triggers.
        self.state.set(f"system.trigger.{trigger_id}.last_fired", ts.last_fired, source="system")
        self._active_trigger_depth[trigger_id] = (
            self._active_trigger_depth.get(trigger_id, 0) + 1
        )

        log.info(
            f"Trigger {trigger_id} fired macro '{ts.macro_name}' "
            f"(type={trigger_type})"
        )
        await self.events.emit(
            "trigger.fired",
            {
                "trigger_id": trigger_id,
                "macro_id": macro_id,
                "macro_name": ts.macro_name,
                "trigger_type": trigger_type,
            },
        )

        # From here the fire is a running macro: hand its lifetime to the
        # macro engine's registry (see _macro_exec_tasks comment in __init__).
        exec_task = asyncio.current_task()
        if exec_task is not None:
            self._macro_exec_tasks.add(exec_task)
        try:
            # Pass the trigger context (event payload / state-change snapshot)
            # so the macro can resolve $trigger.<field> refs and branch on what
            # fired it. Direct/REST/script runs pass no context (refs -> None).
            await self.macros.execute(macro_id, context=context)
        except Exception:  # Catch-all: isolates macro execution errors from trigger pipeline
            log.exception(f"Trigger {trigger_id} macro execution failed")
        finally:
            if exec_task is not None:
                self._macro_exec_tasks.discard(exec_task)
            remaining = self._active_trigger_depth.get(trigger_id, 0) - 1
            if remaining > 0:
                self._active_trigger_depth[trigger_id] = remaining
            else:
                self._active_trigger_depth.pop(trigger_id, None)

    # --- Condition evaluation ---

    def _check_conditions(self, conditions: list[dict[str, Any]]) -> bool:
        """Check all guard conditions. All must pass."""
        for cond in conditions:
            key = cond.get("key", "")
            op = cond.get("operator", "eq")
            target = cond.get("value")
            actual = self.state.get(key)
            try:
                if not eval_operator(op, actual, target):
                    return False
            except ValueError:
                log.error(f"Invalid condition operator '{op}' for key '{key}'")
                return False
        return True

    # --- Status / API ---

    def list_triggers(self) -> list[dict[str, Any]]:
        """Return info about all loaded triggers for the API."""
        result = []
        for ts in self._triggers.values():
            t = ts.trigger
            result.append({
                "id": t.get("id"),
                "type": t.get("type"),
                "enabled": t.get("enabled", True),
                "macro_id": ts.macro_id,
                "macro_name": ts.macro_name,
                "last_fired": ts.last_fired if ts.last_fired > 0 else None,
                "has_pending_delay": ts.delay_task is not None and not ts.delay_task.done() if ts.delay_task else False,
                "has_pending_debounce": ts.debounce_task is not None and not ts.debounce_task.done() if ts.debounce_task else False,
            })
        return result

    async def test_trigger(self, trigger_id: str) -> bool:
        """Fire a trigger's macro immediately, bypassing conditions."""
        ts = self._triggers.get(trigger_id)
        if not ts:
            return False
        # Emit trigger.fired so the Macro editor flashes the trigger card the same
        # as a real fire — this button is the primary "test automation" affordance.
        # The only listener forwards it to WS clients; deliberately no cooldown /
        # last_fired / depth bookkeeping so a manual test stays side-effect-free.
        await self.events.emit(
            "trigger.fired",
            {
                "trigger_id": trigger_id,
                "macro_id": ts.macro_id,
                "macro_name": ts.macro_name,
                "trigger_type": "test",
            },
        )
        try:
            await self.macros.execute(ts.macro_id)
        except ValueError:
            log.warning(f"Trigger {trigger_id} test: macro '{ts.macro_id}' not found")
            return False
        return True
