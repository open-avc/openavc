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

from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.event_bus import EventBus
    from server.core.macro_engine import MacroEngine
    from server.core.state_store import StateStore

log = get_logger(__name__)


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
        self._active_trigger_chain: set[str] = set()  # macro IDs currently firing from triggers
        self._running = False

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
                    # Restore persisted cooldown timestamp
                    persisted = self.state.get(f"system.trigger.{trigger_id}.last_fired")
                    if persisted and isinstance(persisted, (int, float)):
                        ts.last_fired = float(persisted)
                    self._triggers[trigger_id] = ts
                    count += 1
        if count:
            log.info(f"Loaded {count} trigger(s)")

    async def start(self) -> None:
        """Register all trigger listeners and start the cron loop."""
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
                delay = t.get("delay_seconds", 0)
                t = asyncio.create_task(self._fire_startup(ts, delay))
                t.add_done_callback(_log_task_exception)

        # Start cron loop if any schedule triggers exist
        if has_cron and HAS_CRONITER:
            self._cron_task = asyncio.create_task(self._cron_loop())
            self._cron_task.add_done_callback(_log_task_exception)
            log.info("Trigger cron loop started")
        elif has_cron and not HAS_CRONITER:
            log.warning("Schedule triggers defined but croniter not installed")

        log.info("TriggerEngine started")

    async def stop(self) -> None:
        """Unregister all listeners, cancel pending tasks."""
        self._running = False

        # Cancel cron loop
        if self._cron_task and not self._cron_task.done():
            self._cron_task.cancel()
            try:
                await self._cron_task
            except asyncio.CancelledError:
                pass
            self._cron_task = None

        # Cancel all pending debounce/delay tasks
        for ts in self._triggers.values():
            if ts.debounce_task and not ts.debounce_task.done():
                ts.debounce_task.cancel()
            if ts.delay_task and not ts.delay_task.done():
                ts.delay_task.cancel()
            for task in ts.pending_queue:
                if not task.done():
                    task.cancel()

        # Unsubscribe state listeners
        for sub_id in self._state_sub_ids:
            self.state.unsubscribe(sub_id)
        self._state_sub_ids.clear()

        # Unregister event handlers
        for handler_id in self._event_handler_ids:
            self.events.off(handler_id)
        self._event_handler_ids.clear()

        self._active_trigger_chain.clear()
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
        if op != "any" and not self._eval_operator(op, new_value, target):
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
        """Check schedule triggers every 30 seconds."""
        # Track last fire time per trigger to prevent duplicate fires
        last_fires: dict[str, datetime] = {}
        try:
            while True:
                await asyncio.sleep(30)
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
                        cron = croniter(cron_expr, now)
                        prev = cron.get_prev(datetime)
                        delta = (now - prev).total_seconds()
                        if delta > 30:
                            continue
                        # Prevent duplicate fires
                        last = last_fires.get(t["id"])
                        if last and (last - prev).total_seconds() >= 0:
                            continue
                        last_fires[t["id"]] = now
                        self._initiate_fire(ts, {"trigger_type": "schedule", "cron": cron_expr})
                    except Exception:  # Catch-all: isolates individual trigger evaluation errors
                        log.exception(f"Error checking cron trigger '{t['id']}'")
        except asyncio.CancelledError:
            return

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
            elapsed = time.time() - ts.last_fired
            if elapsed < cooldown:
                log.debug(f"Trigger {trigger_id} skipped — cooldown ({elapsed:.1f}s < {cooldown}s)")
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
        else:
            asyncio.create_task(self._execute_trigger(ts, context))

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
                    if not self._eval_operator(op, current, target):
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
        if macro_id in self.macros._running:
            if overlap == "skip":
                log.debug(f"Trigger {trigger_id} skipped — macro already running")
                await self.events.emit(
                    "trigger.skipped",
                    {"trigger_id": trigger_id, "reason": "macro_running"},
                )
                return
            elif overlap == "queue":
                # Wait for running macro to finish, then re-check and fire
                asyncio.create_task(self._queued_fire(ts, context))
                return
            # overlap == "allow" falls through

        # 7. Circular check
        if macro_id in self._active_trigger_chain:
            log.warning(
                f"Trigger {trigger_id} blocked — circular chain detected "
                f"(macro '{ts.macro_name}' already in chain)"
            )
            await self.events.emit(
                "trigger.skipped",
                {"trigger_id": trigger_id, "reason": "circular"},
            )
            return

        # 8. Fire
        await self._fire_macro(ts, context)

    async def _queued_fire(
        self, ts: _TriggerState, context: dict[str, Any]
    ) -> None:
        """Wait for running macro to finish, re-check conditions, then fire."""
        macro_id = ts.macro_id
        # Poll until macro finishes (max 5 minutes)
        for _ in range(300):
            await asyncio.sleep(1)
            if not self._running:
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
        # Persist cooldown timestamp so it survives restarts
        self.state.set(f"system.trigger.{trigger_id}.last_fired", ts.last_fired, source="system")
        self._active_trigger_chain.add(macro_id)

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

        try:
            await self.macros.execute(macro_id)
        except Exception:  # Catch-all: isolates macro execution errors from trigger pipeline
            log.exception(f"Trigger {trigger_id} macro execution failed")
        finally:
            self._active_trigger_chain.discard(macro_id)

    # --- Condition evaluation ---

    def _check_conditions(self, conditions: list[dict[str, Any]]) -> bool:
        """Check all guard conditions. All must pass."""
        for cond in conditions:
            key = cond.get("key", "")
            op = cond.get("operator", "eq")
            target = cond.get("value")
            actual = self.state.get(key)
            if not self._eval_operator(op, actual, target):
                return False
        return True

    _OPERATOR_ALIASES: dict[str, str] = {
        "equals": "eq",
        "not_equals": "ne",
        "==": "eq",
        "!=": "ne",
        ">": "gt",
        "<": "lt",
        ">=": "gte",
        "<=": "lte",
        "equal": "eq",
        "not_equal": "ne",
        "greater_than": "gt",
        "less_than": "lt",
        "greater_or_equal": "gte",
        "less_or_equal": "lte",
    }

    @staticmethod
    def _eval_operator(op: str, actual: Any, target: Any) -> bool:
        """Evaluate a comparison operator (with alias normalization)."""
        op = TriggerEngine._OPERATOR_ALIASES.get(op, op)
        if op == "eq":
            return actual == target
        if op == "ne":
            return actual != target
        if op == "gt":
            return actual is not None and target is not None and actual > target
        if op == "lt":
            return actual is not None and target is not None and actual < target
        if op == "gte":
            return actual is not None and target is not None and actual >= target
        if op == "lte":
            return actual is not None and target is not None and actual <= target
        if op == "truthy":
            return bool(actual)
        if op == "falsy":
            return not bool(actual)
        return False

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
        await self.macros.execute(ts.macro_id)
        return True
