"""
OpenAVC Cloud — Agent-side alert rule evaluation.

Subscribes to the StateStore for state changes, evaluates alert rules
(pushed from the cloud), and sends alert/alert_resolved messages when
conditions are met or cleared. System resource alerts (disk, memory, CPU)
are managed as cloud-configurable rules, not hardcoded thresholds.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from fnmatch import fnmatch
from typing import Any, TYPE_CHECKING

from openavc.cloud.protocol import (
    ACTIVE_ALERTS, ALERT, ALERT_RESOLVED, MONITORS,
    build_active_alerts_payload, build_alert_payload,
    build_alert_resolved_payload, build_monitors_payload,
)
from openavc.core.monitors import compile_alert_rules
from openavc.utils.logger import get_logger
from openavc.utils.regex_safety import regex_safety_error

if TYPE_CHECKING:
    from openavc.cloud.agent import CloudAgent
    from openavc.core.state_store import StateStore
    from openavc.core.event_bus import EventBus

log = get_logger(__name__)


class AlertMonitor:
    """
    Evaluates alert rules against local state and fires alerts upstream.

    All rules (including system resource alerts) are received from the cloud
    via the ``cloud.alert_rules_update`` event and are fully configurable
    from the cloud portal.
    """

    def __init__(
        self,
        agent: CloudAgent,
        state: StateStore,
        events: EventBus,
        monitors_provider: Callable[[], list[dict[str, Any]]] | None = None,
    ):
        self._agent = agent
        self._state = state
        self._events = events
        # Reads the project's monitor list. A callable rather than the project
        # itself because the project is replaced wholesale on every save, and
        # this must always see the one that is running.
        self._monitors_provider = monitors_provider

        # Custom rules from cloud (list of dicts, keyed by "id")
        self._rules: list[dict[str, Any]] = []

        # Rules compiled from the project's monitor list. Kept SEPARATE from
        # the cloud's on purpose: a cloud push replaces its own set wholesale,
        # and folding these in would mean every push silently deleted what the
        # project declared. Two sources, one evaluator, neither editing the
        # other's rows.
        self._project_rules: list[dict[str, Any]] = []
        self._project_handler_id: Any = None

        # Active alerts: alert_key -> agent_alert_id
        # Tracks what's currently firing to avoid re-firing and to auto-resolve.
        #
        # In memory, and deliberately: what is firing is a fact about the
        # readings this instance can see right now, and after a restart it can
        # see them again within a poll. Persisting it would preserve a claim
        # about a room from before the process that made it — a projector
        # swapped overnight would come back "still failing".
        #
        # But the CLOUD's copy outlives the restart, and it has no way to learn
        # that this dict is empty: a resolve is only ever sent for a key found
        # here, so an alert raised before a restart could never be cleared by
        # anybody. That is what _send_active_alert_set closes, on every connect.
        self._active_alerts: dict[str, str] = {}

        # Pattern rule timers: alert_key -> first_match_epoch
        self._pattern_timers: dict[str, float] = {}

        # Device activity tracking for absence rules
        self._last_state_times: dict[str, float] = {}

        # Pending alert sends (from sync callback, processed async)
        self._pending_sends: list[tuple[str, dict[str, Any]]] = []
        self._pending_lock = asyncio.Lock()

        # Subsystem state
        self._sub_id: str | None = None
        self._rules_handler_id: Any = None
        self._check_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start monitoring state changes and evaluating alert rules."""
        if self._running:
            return

        self._running = True
        self._sub_id = self._state.subscribe("*", self._on_state_change)
        self._rules_handler_id = self._events.on(
            "cloud.alert_rules_update", self._on_rules_update_sync
        )
        # A monitor is declared in the project, so it changes when the project
        # does -- not on the daily snapshot, which is up to 24 hours stale and
        # would make "tag it and see it" a lie.
        self._project_handler_id = self._events.on(
            "system.project.reloaded", self._on_project_applied_sync
        )
        self._check_task = asyncio.create_task(self._periodic_check_loop())
        self._send_task = asyncio.create_task(self._send_loop())
        self._reload_project_rules()
        self._queue_send(MONITORS, build_monitors_payload(self._read_monitors()))
        # _reload_project_rules has already swept the project's; these are the
        # cloud's, held in memory across a reconnect. Both happen BEFORE the set
        # is sent, and that order is the whole of it: the set has to say what is
        # wrong now, or the cloud resolves a fault that is still happening.
        self._raise_what_is_already_wrong(self._rules)
        self._send_active_alert_set()
        if not self._rules and not self._project_rules:
            log.info("Alert monitor: started (no rules loaded, waiting for cloud push)")
        else:
            log.info(
                "Alert monitor: started with %d cloud rule(s) and %d from the project",
                len(self._rules), len(self._project_rules),
            )

    async def stop(self) -> None:
        """Stop the alert monitor."""
        self._running = False

        if self._sub_id:
            self._state.unsubscribe(self._sub_id)
            self._sub_id = None

        if self._rules_handler_id is not None:
            self._events.off(self._rules_handler_id)
            self._rules_handler_id = None

        if self._project_handler_id is not None:
            self._events.off(self._project_handler_id)
            self._project_handler_id = None

        for task in [self._check_task, self._send_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._check_task = None
        self._send_task = None
        log.info("Alert monitor: stopped")

    # --- Reconciliation on connect ---

    def _raise_what_is_already_wrong(self, rules: list[dict[str, Any]]) -> None:
        """Evaluate these rules against the state as it stands, now.

        Everything else here is edge-triggered: a rule is evaluated when its key
        CHANGES, and ``StateStore.set`` notifies nobody when the value it is
        handed is the one already stored. A reading that is wrong and stays
        wrong therefore produces exactly one evaluation, at the moment it went
        wrong — and a process that was not running for that moment never sees
        it.

        Two things need this and one of them is new. A rule authored (or pushed)
        while a reading is already outside it used to sit there doing nothing
        until the value happened to move. And a restart now clears the room's
        open alerts from the cloud (`_send_active_alert_set`), so without this
        a fault that survives a reboot would be resolved and never raised
        again — a room reading fine while the projector is still cooking. That
        would be a worse bug than the one the reconciliation fixes, which is why
        the two land together.

        Absence rules are deliberately not swept: "nothing has been heard from
        this device" is measured from when this process started hearing things,
        and the periodic loop already owns that clock.
        """
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            rule_type = rule.get("rule_type", "")
            if rule_type not in ("threshold", "pattern"):
                continue
            pattern = rule.get("condition", {}).get("key", "")
            if not isinstance(pattern, str) or not pattern:
                continue
            for key, value in self._state.get_matching(pattern).items():
                if rule_type == "threshold":
                    self._evaluate_threshold(rule, key, value)
                else:
                    self._evaluate_pattern(rule, key, value)

    def _send_active_alert_set(self) -> None:
        """Tell the cloud everything this instance has firing, right now.

        Sent on every connect, because that is every point at which the two
        sides can have drifted apart. The drift that matters is a restart: this
        instance's record of what is firing is in memory, a resolve is only ever
        sent for something in it, so an alert raised before a restart has
        nothing left in the world that could clear it. It sits in the portal
        amber forever, and the next reboot adds another one beside it.

        The list is what is firing, not what changed — the cloud resolves what
        it still holds open and this does not name. So the empty list a restarted
        instance sends is the whole point of the message, not a degenerate case
        of it: it says "nothing is wrong in this room".

        What makes that safe is `_raise_what_is_already_wrong` having run first:
        anything still outside its limits has been re-raised by then and IS
        named here, so a room whose fault survived the reboot comes back with
        one alert rather than none.
        """
        self._queue_send(
            ACTIVE_ALERTS,
            build_active_alerts_payload(sorted(self._active_alerts.values())),
        )

    # --- State Change Handler (synchronous) ---

    def _on_state_change(
        self, key: str, old_value: Any, new_value: Any, source: str
    ) -> None:
        """Synchronous callback from StateStore. Evaluates threshold/pattern rules."""
        # Skip cloud-internal and ISC state
        if key.startswith("system.cloud.") or key.startswith("isc."):
            return

        # Track device activity for absence detection
        if key.startswith("device."):
            parts = key.split(".")
            if len(parts) >= 2:
                self._last_state_times[parts[1]] = time.time()

        # Evaluate threshold and pattern rules. Two sources — the cloud's
        # fleet-wide rules and the project's own monitors — one evaluator.
        for rule in self._all_rules():
            if not rule.get("enabled", True):
                continue
            rule_type = rule.get("rule_type", "")
            if rule_type == "threshold":
                self._evaluate_threshold(rule, key, new_value)
            elif rule_type == "pattern":
                self._evaluate_pattern(rule, key, new_value)

    # --- Rule Evaluation ---

    def _all_rules(self) -> list[dict[str, Any]]:
        """Every rule this instance evaluates, from both sources."""
        return self._rules + self._project_rules

    def _evaluate_threshold(self, rule: dict, key: str, value: Any) -> None:
        """Check if a threshold rule triggers or resolves.

        Supports hysteresis via an optional ``resolve_value`` in the condition.
        When present, the alert fires when ``value <op> threshold`` but only
        resolves when the value crosses ``resolve_value`` in the opposite
        direction.  For example, fire at disk > 90% but resolve at disk < 85%.

        An optional ``duration_seconds`` means the same thing it means on a
        pattern rule: the reading has to stay wrong that long before anybody is
        told. A projector that is off is correct at 3am and a problem ten
        minutes into a lecture, and the difference is time, not the value.
        """
        condition = rule.get("condition", {})
        pattern = condition.get("key", "")
        if not fnmatch(key, pattern):
            return

        operator = condition.get("operator", ">")
        threshold = condition.get("value")
        if threshold is None:
            return

        triggered = _compare(value, operator, threshold)
        alert_key = f"rule:{rule['id']}:{key}"

        if triggered:
            if alert_key in self._active_alerts:
                return
            if _duration_of(rule) > 0:
                # Arm it; the periodic loop fires once it has held.
                self._pattern_timers.setdefault(alert_key, time.time())
                return
            self._queue_send(ALERT, self._threshold_alert(rule, key, value))
        else:
            self._pattern_timers.pop(alert_key, None)
            if alert_key not in self._active_alerts:
                return
            # Hysteresis: use resolve_value if set, otherwise same as threshold
            resolve_value = condition.get("resolve_value", threshold)
            if _check_resolved(value, operator, resolve_value):
                resolved_id = self._active_alerts.pop(alert_key)
                self._queue_send(ALERT_RESOLVED, build_alert_resolved_payload(resolved_id))

    def _threshold_alert(self, rule: dict, key: str, value: Any) -> dict[str, Any]:
        """Register a threshold alert as active and build its payload."""
        condition = rule.get("condition", {})
        operator = condition.get("operator", ">")
        threshold = condition.get("value")
        alert_key = f"rule:{rule['id']}:{key}"
        alert_id = str(uuid.uuid4())
        self._active_alerts[alert_key] = alert_id
        return build_alert_payload(
            alert_id=alert_id,
            rule_id=rule["id"],
            severity=rule.get("severity", "warning"),
            category=rule.get("category", "device"),
            device_id=_extract_device_id(key),
            message=_clip_message(
                f"{rule['name']}: {key} {operator} {threshold} (current: {value})"
            ),
            detail={"rule_id": rule["id"], "key": key, "value": value,
                    "threshold": threshold},
        )

    def _evaluate_pattern(self, rule: dict, key: str, value: Any) -> None:
        """Check pattern rules (value matches, held for N seconds).

        ``condition.operator`` defaults to ``=`` — the original spelling, and
        what every cloud-authored pattern rule uses. A project monitor compiles
        to ``not_in`` against the set of values its author called normal, which
        is the same question asked the way a room asks it: not "did it become
        this bad value" but "did it leave the good ones".
        """
        condition = rule.get("condition", {})
        pattern = condition.get("key", "")
        if not fnmatch(key, pattern):
            return

        expected = condition.get("value")
        operator = condition.get("operator", "=")
        alert_key = f"rule:{rule['id']}:{key}"

        if _compare(value, operator, expected):
            if alert_key in self._active_alerts:
                return
            duration = _duration_of(rule)
            if duration > 0:
                # Value matches — start or continue the timer. The trigger
                # check happens in _periodic_check_loop.
                self._pattern_timers.setdefault(alert_key, time.time())
                return
            # No duration means immediately, and immediately must not mean
            # "within the next 30 seconds, when the loop next ticks".
            self._pattern_timers.pop(alert_key, None)
            self._queue_send(ALERT, self._pattern_alert(rule, key, value, 0))
        else:
            # Value no longer matches — clear timer and resolve if active
            self._pattern_timers.pop(alert_key, None)
            if alert_key in self._active_alerts:
                resolved_id = self._active_alerts.pop(alert_key)
                self._queue_send(ALERT_RESOLVED, build_alert_resolved_payload(resolved_id))

    def _pattern_alert(
        self, rule: dict, key: str, value: Any, duration: float
    ) -> dict[str, Any]:
        """Register a pattern alert as active and build its payload."""
        alert_key = f"rule:{rule['id']}:{key}"
        alert_id = str(uuid.uuid4())
        self._active_alerts[alert_key] = alert_id
        held = f" for {duration:g}s" if duration else ""
        return build_alert_payload(
            alert_id=alert_id,
            rule_id=rule["id"],
            severity=rule.get("severity", "warning"),
            category=rule.get("category", "device"),
            device_id=_extract_device_id(key),
            message=_clip_message(
                f"{rule['name']}: {key} is {value}{held}"
            ),
            detail={"rule_id": rule["id"], "key": key, "value": value,
                    "duration_seconds": duration},
        )

    # --- Periodic Check Loop ---

    async def _periodic_check_loop(self) -> None:
        """Runs every 30 seconds. Checks pattern durations and absence rules."""
        while self._running:
            try:
                await asyncio.sleep(30)
                await self._run_periodic_checks(time.time())
            except asyncio.CancelledError:
                return
            except Exception:
                # A send failure (e.g. ConnectionClosed) or any other error
                # during a tick must not kill the loop — that would silently
                # stop ALL pattern-duration and device-absence evaluation for
                # the rest of this connection's life. Log and keep ticking.
                log.exception("Alert monitor: periodic check iteration failed")

    async def _run_periodic_checks(self, now: float) -> None:
        """Run one iteration of the periodic checks.

        Split out from `_periodic_check_loop` so tests can drive a single
        evaluation without waiting on the 30-second sleep.
        """
        # Check armed timers — a threshold or a pattern that has been wrong
        # since it armed and now has to have held long enough.
        for alert_key, start_time in list(self._pattern_timers.items()):
            if alert_key in self._active_alerts:
                continue  # Already fired

            rule = self._find_rule_from_key(alert_key)
            if not rule:
                self._pattern_timers.pop(alert_key, None)
                continue

            duration = _duration_of(rule)
            if now - start_time < duration:
                continue

            # The value is read LIVE rather than remembered from arming time,
            # so the alert states what is true when it is sent.
            key = alert_key.split(":", 2)[-1]
            value = self._state.get(key)
            if rule.get("rule_type") == "threshold":
                payload = self._threshold_alert(rule, key, value)
            else:
                payload = self._pattern_alert(rule, key, value, duration)
            await self._agent.send_message(ALERT, payload)

        # Prune stale device entries. The horizon must outlast the largest
        # absence threshold, or a device would be evicted before its absence
        # rule can fire (the absence check below iterates _last_state_times).
        # A hardcoded 24h floor made any threshold above 86400s unreachable.
        max_absence = 0
        for rule in self._all_rules():
            if rule.get("rule_type") == "absence" and rule.get("enabled", True):
                try:
                    max_absence = max(
                        max_absence,
                        int(rule.get("condition", {}).get("threshold_seconds", 120)),
                    )
                except (TypeError, ValueError):
                    pass
        # +60s slack so at least one 30s tick runs after the threshold elapses
        # but before the entry is pruned.
        stale_cutoff = now - max(86400, max_absence + 60)
        for dev_id in [k for k, t in self._last_state_times.items() if t < stale_cutoff]:
            self._last_state_times.pop(dev_id, None)

        # Check absence rules
        for rule in self._all_rules():
            if rule.get("rule_type") != "absence" or not rule.get("enabled", True):
                continue

            threshold_secs = rule.get("condition", {}).get("threshold_seconds", 120)
            key_prefix = rule.get("condition", {}).get("key_prefix") or "device."
            if not isinstance(key_prefix, str):
                key_prefix = "device."

            for device_id, last_time in list(self._last_state_times.items()):
                # Only check devices matching the key_prefix. The field is a
                # prefix by name and by the portal default ("device."); treat a
                # bare prefix as startswith and a glob (containing * ? [) as
                # fnmatch — a literal fnmatch of "device." matched nothing.
                full_key = f"device.{device_id}"
                if not _matches_key_prefix(full_key, key_prefix):
                    continue

                alert_key = f"rule:{rule['id']}:{device_id}"

                if now - last_time > threshold_secs and alert_key not in self._active_alerts:
                    alert_id = str(uuid.uuid4())
                    self._active_alerts[alert_key] = alert_id
                    elapsed = int(now - last_time)
                    await self._agent.send_message(ALERT, build_alert_payload(
                        alert_id=alert_id,
                        rule_id=rule["id"],
                        severity=rule.get("severity", "warning"),
                        category="device",
                        device_id=device_id,
                        message=_clip_message(
                            f"{rule['name']}: {device_id} not reporting for {elapsed}s"
                        ),
                        detail={"rule_id": rule["id"], "threshold_seconds": threshold_secs},
                    ))
                elif now - last_time <= threshold_secs and alert_key in self._active_alerts:
                    resolved_id = self._active_alerts.pop(alert_key)
                    await self._agent.send_message(ALERT_RESOLVED, build_alert_resolved_payload(resolved_id))

    # --- Send Loop (processes queued sends from sync callback) ---

    async def _send_loop(self) -> None:
        """Process pending alert sends queued by the sync state callback."""
        try:
            while self._running:
                await asyncio.sleep(1)

                if not self._pending_sends:
                    continue

                async with self._pending_lock:
                    batch = self._pending_sends[:]
                    self._pending_sends.clear()

                for msg_type, payload in batch:
                    try:
                        await self._agent.send_message(msg_type, payload)
                    except Exception:
                        # Catch-all: isolates send failures so remaining alerts are still delivered
                        log.exception("Alert monitor: failed to send %s", msg_type)

        except asyncio.CancelledError:
            return

    # --- Rules Update ---

    def _on_rules_update_sync(self, event: str, data: Any) -> None:
        """Handle rules pushed from cloud (may be called sync or async)."""
        if isinstance(data, dict):
            raw_rules = data.get("rules", [])
        else:
            raw_rules = []

        self._rules = self._swap_rules(self._rules, raw_rules)
        # The push lands milliseconds after connect, so a rule that arrives here
        # missed the sweep in start(). Nothing else would evaluate it until its
        # key next changed.
        self._raise_what_is_already_wrong(self._rules)
        log.info("Alert monitor: updated to %d cloud rule(s)", len(self._rules))

    def _swap_rules(
        self, old: list[dict[str, Any]], raw_new: Any
    ) -> list[dict[str, Any]]:
        """Replace one source's rule set, clearing what its removed rules left.

        Both sources go through here — the cloud's push and the project's
        monitor list — so a rule that goes away resolves its firing alert and
        drops its armed timer the same way whichever door removed it. A rule
        deleted while its alert was still open would otherwise sit in the
        portal forever with nothing left that could ever resolve it.
        """
        rules = self._sanitize_rules(raw_new)

        # Safe now that _sanitize_rules guarantees every stored/incoming rule
        # is a dict with a string "id": a malformed rule used to raise KeyError
        # here, BEFORE self._rules was reassigned, and the event bus swallowed
        # it — silently dropping the whole update and keeping the stale rule set.
        deleted_ids = {r["id"] for r in old} - {r["id"] for r in rules}

        for alert_key in list(self._active_alerts.keys()):
            parts = alert_key.split(":")
            if len(parts) >= 2 and parts[0] == "rule" and parts[1] in deleted_ids:
                resolved_id = self._active_alerts.pop(alert_key)
                self._queue_send(ALERT_RESOLVED, build_alert_resolved_payload(resolved_id))

        for alert_key in list(self._pattern_timers.keys()):
            parts = alert_key.split(":")
            if len(parts) >= 2 and parts[0] == "rule" and parts[1] in deleted_ids:
                self._pattern_timers.pop(alert_key, None)

        return rules

    # --- Project monitors ---

    def _read_monitors(self) -> list[dict[str, Any]]:
        """The project's monitor list, or nothing if there is no provider."""
        if self._monitors_provider is None:
            return []
        try:
            monitors = self._monitors_provider()
        except Exception:
            log.exception("Alert monitor: could not read the project's monitors")
            return []
        return [m for m in monitors if isinstance(m, dict)]

    def _reload_project_rules(self) -> None:
        """Recompile what the project declares, and swap it in."""
        compiled = compile_alert_rules(self._read_monitors())
        self._project_rules = self._swap_rules(self._project_rules, compiled)
        # Tagging a reading whose value is already outside the limits just typed
        # is the commonest way to author one, and it fired nothing until the
        # value next moved. This is also what covers the project half on
        # connect, since start() calls straight through here.
        self._raise_what_is_already_wrong(self._project_rules)

    def _on_project_applied_sync(self, event: str, data: Any) -> None:
        """The project changed: recompile its limits and re-send the manifest.

        The manifest is what lets the cloud card draw a tile at all, so it must
        ride the project change rather than the daily snapshot — "tag it and see
        it" is the whole experience, and a 24-hour wait is not that.
        """
        if not self._running:
            return
        self._reload_project_rules()
        self._queue_send(MONITORS, build_monitors_payload(self._read_monitors()))

    # --- Helpers ---

    def _sanitize_rules(self, rules: Any) -> list[dict[str, Any]]:
        """Filter cloud-pushed rules down to a safe, well-formed set.

        Drops anything that isn't a dict or is missing a usable string ``id``
        (the evaluation paths index ``rule['id']`` / ``rule['name']`` directly,
        so a malformed rule would otherwise crash rule processing). Also drops
        a ``matches`` rule whose regex is invalid or prone to catastrophic
        backtracking — that regex runs synchronously on the event loop on every
        state change, so a ReDoS pattern pushed as a rule value would stall
        device control, the receive loop, and heartbeats. A missing ``name``
        falls back to the id. Dropped rules are logged, not silently ignored.
        """
        valid: list[dict[str, Any]] = []
        for rule in rules if isinstance(rules, list) else []:
            if not isinstance(rule, dict):
                log.warning("Alert monitor: dropping non-dict rule: %r", rule)
                continue
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not rule_id:
                log.warning(
                    "Alert monitor: dropping rule with missing/invalid id: %r", rule
                )
                continue
            condition = rule.get("condition")
            if isinstance(condition, dict) and condition.get("operator") == "matches":
                # _compare runs re.search(str(threshold), ...); validate that.
                err = regex_safety_error(
                    f"alert rule {rule_id}", str(condition.get("value", ""))
                )
                if err:
                    log.warning("Alert monitor: dropping rule %s — %s", rule_id, err)
                    continue
            if not rule.get("name"):
                rule = {**rule, "name": rule_id}
            valid.append(rule)
        return valid

    def _queue_send(self, msg_type: str, payload: dict[str, Any]) -> None:
        """Queue a message to be sent asynchronously (safe to call from sync context)."""
        max_pending = 100
        if len(self._pending_sends) >= max_pending:
            # Drop oldest to prevent unbounded growth
            self._pending_sends.pop(0)
        self._pending_sends.append((msg_type, payload))

    def _find_rule_from_key(self, alert_key: str) -> dict | None:
        """Extract rule ID from alert_key and look up the rule."""
        parts = alert_key.split(":")
        if len(parts) < 2 or parts[0] != "rule":
            return None
        rule_id = parts[1]
        for r in self._all_rules():
            if r.get("id") == rule_id:
                return r
        return None


# --- Module-level helpers ---

# The cloud stores alert messages in a String(2000) column. An over-length
# message (e.g. a device reporting a long string state into a matched key)
# makes PostgreSQL reject the insert (error 22001) and silently drops the
# alert — it passes on SQLite but fails in production. Clip before sending.
_MAX_MESSAGE_LEN = 2000


def _clip_message(message: str) -> str:
    """Truncate an alert message to the cloud's column limit."""
    if len(message) <= _MAX_MESSAGE_LEN:
        return message
    return message[: _MAX_MESSAGE_LEN - 1] + "…"


def _matches_key_prefix(key: str, key_prefix: str) -> bool:
    """Match a state key against an absence rule's ``key_prefix``.

    Treat a glob (containing ``*``, ``?`` or ``[``) as fnmatch; otherwise treat
    it as a literal prefix (startswith). A bare ``fnmatch`` matched the portal
    default ``"device."`` against nothing, so every absence rule using the
    default silently never fired.
    """
    if any(c in key_prefix for c in "*?["):
        return fnmatch(key, key_prefix)
    return key.startswith(key_prefix)


def _duration_of(rule: dict[str, Any]) -> float:
    """How long a rule's condition must hold before it fires. 0 = at once."""
    try:
        return max(0.0, float(rule.get("condition", {}).get("duration_seconds") or 0))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _norm_for_set(value: Any) -> str:
    """A value as the string a set comparison agrees on.

    Same rule as ``core.monitors._norm`` and for the same reason: a driver
    reports a Python ``True``, a project spells the same thing ``"true"``, and
    the two must be one value from either direction. Everything else stays
    case-sensitive — "Mic" and "mic" are different inputs on plenty of frames.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    lowered = text.lower()
    return lowered if lowered in ("true", "false") else text


def _compare(value: Any, operator: str, threshold: Any) -> bool:
    """Compare a value against a threshold using the given operator."""
    import re

    try:
        # Set membership comes first: it is the one pair of operators whose
        # threshold is a LIST, so the scalar coercions below would all be
        # comparing against "['a', 'b']".
        if operator in ("in", "not_in"):
            # A key that has never reported is neither in the set nor out of
            # it. `not_in` would otherwise fire on every monitored reading the
            # moment the agent connects, before the device has said anything —
            # and the tile beside it correctly reads "no reading yet". Absence
            # is what the absence rule type is for.
            if value is None:
                return False
            options = threshold if isinstance(threshold, (list, tuple, set)) else [threshold]
            present = _norm_for_set(value) in {_norm_for_set(o) for o in options}
            return present if operator == "in" else not present

        # Handle None explicitly
        if value is None:
            return operator in ("=", "==") and threshold is None
        if threshold is None:
            return operator == "!=" and value is not None

        # Handle booleans — compare as strings to avoid bool/int confusion
        if isinstance(value, bool) or isinstance(threshold, bool):
            if operator in ("=", "=="):
                return str(value).lower() == str(threshold).lower()
            if operator == "!=":
                return str(value).lower() != str(threshold).lower()
            return False

        if operator in (">", "<", ">=", "<="):
            v = float(value)
            t = float(threshold)
            if operator == ">":
                return v > t
            if operator == "<":
                return v < t
            if operator == ">=":
                return v >= t
            if operator == "<=":
                return v <= t
        if operator in ("=", "=="):
            return str(value) == str(threshold)
        if operator == "!=":
            return str(value) != str(threshold)
        if operator == "contains":
            return str(threshold) in str(value)
        if operator == "not_contains":
            return str(threshold) not in str(value)
        if operator == "matches":
            return bool(re.search(str(threshold), str(value)))
    except (TypeError, ValueError, re.error):
        return False
    return False


def _check_resolved(value: Any, fire_operator: str, resolve_value: Any) -> bool:
    """Check if a value has crossed the resolve threshold (opposite of fire).

    For ``>``, resolved means ``value < resolve_value``.
    For ``<``, resolved means ``value > resolve_value``.
    For equality operators, resolved means the value no longer matches.
    """
    _INVERSE = {
        ">": "<",
        ">=": "<=",
        "<": ">",
        "<=": ">=",
    }
    inverse_op = _INVERSE.get(fire_operator)
    if inverse_op:
        return _compare(value, inverse_op, resolve_value)
    # For non-numeric operators (=, !=, contains, etc.) resolve when no longer triggered
    return not _compare(value, fire_operator, resolve_value)


def _extract_device_id(key: str) -> str | None:
    """Extract device ID from a state key like 'device.projector1.power'."""
    parts = key.split(".")
    if len(parts) >= 2 and parts[0] == "device":
        return parts[1]
    return None
