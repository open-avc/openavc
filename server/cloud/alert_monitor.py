"""
OpenAVC Cloud — Agent-side alert rule evaluation.

Subscribes to the StateStore for state changes, evaluates alert rules
(pushed from the cloud), and sends alert/alert_resolved messages when
conditions are met or cleared. Also evaluates built-in system resource
alerts (disk, memory, CPU, device offline).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from fnmatch import fnmatch
from typing import Any, TYPE_CHECKING

from server.cloud.protocol import ALERT, ALERT_RESOLVED
from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.cloud.agent import CloudAgent
    from server.core.state_store import StateStore
    from server.core.event_bus import EventBus

log = get_logger(__name__)

# Built-in system resource thresholds (always active)
_BUILTIN_THRESHOLDS: dict[str, tuple[str, float, str, str]] = {
    # state_key: (category, threshold, severity, message_template)
    "system.disk_percent": ("system", 90.0, "critical", "Disk usage above 90%"),
    "system.memory_percent": ("system", 90.0, "critical", "Memory usage above 90%"),
    "system.cpu_percent": ("system", 95.0, "warning", "CPU usage sustained above 95%"),
}


class AlertMonitor:
    """
    Evaluates alert rules against local state and fires alerts upstream.

    Custom rules are received from the cloud via the ``cloud.alert_rules_update``
    event. Built-in rules for system resources are always active.
    """

    def __init__(
        self,
        agent: CloudAgent,
        state: StateStore,
        events: EventBus,
    ):
        self._agent = agent
        self._state = state
        self._events = events

        # Custom rules from cloud (list of dicts, keyed by "id")
        self._rules: list[dict[str, Any]] = []

        # Active alerts: alert_key -> agent_alert_id
        # Tracks what's currently firing to avoid re-firing and to auto-resolve
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
        self._check_task = asyncio.create_task(self._periodic_check_loop())
        self._send_task = asyncio.create_task(self._send_loop())
        log.info("Alert monitor: started")

    async def stop(self) -> None:
        """Stop the alert monitor."""
        self._running = False

        if self._sub_id:
            self._state.unsubscribe(self._sub_id)
            self._sub_id = None

        if self._rules_handler_id is not None:
            self._events.off(self._rules_handler_id)
            self._rules_handler_id = None

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

        # Evaluate threshold rules
        for rule in self._rules:
            if not rule.get("enabled", True):
                continue
            rule_type = rule.get("rule_type", "")
            if rule_type == "threshold":
                self._evaluate_threshold(rule, key, new_value)
            elif rule_type == "pattern":
                self._evaluate_pattern(rule, key, new_value)

        # Also evaluate built-in thresholds
        if key in _BUILTIN_THRESHOLDS:
            category, threshold, severity, msg_template = _BUILTIN_THRESHOLDS[key]
            alert_key = f"builtin:{key}"
            try:
                val = float(new_value)
            except (TypeError, ValueError):
                return

            if val > threshold and alert_key not in self._active_alerts:
                alert_id = str(uuid.uuid4())
                self._active_alerts[alert_key] = alert_id
                self._queue_send(ALERT, {
                    "alert_id": alert_id,
                    "severity": severity,
                    "category": category,
                    "message": f"{msg_template} (current: {val:.1f}%)",
                    "detail": {"key": key, "value": val, "threshold": threshold},
                })
            elif val <= threshold and alert_key in self._active_alerts:
                resolved_id = self._active_alerts.pop(alert_key)
                self._queue_send(ALERT_RESOLVED, {"alert_id": resolved_id})

    # --- Rule Evaluation ---

    def _evaluate_threshold(self, rule: dict, key: str, value: Any) -> None:
        """Check if a threshold rule triggers or resolves."""
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

        if triggered and alert_key not in self._active_alerts:
            alert_id = str(uuid.uuid4())
            self._active_alerts[alert_key] = alert_id
            device_id = _extract_device_id(key)
            self._queue_send(ALERT, {
                "alert_id": alert_id,
                "severity": rule.get("severity", "warning"),
                "category": rule.get("category", "device"),
                "device_id": device_id,
                "message": f"{rule['name']}: {key} {operator} {threshold} (current: {value})",
                "detail": {"rule_id": rule["id"], "key": key, "value": value, "threshold": threshold},
            })
        elif not triggered and alert_key in self._active_alerts:
            resolved_id = self._active_alerts.pop(alert_key)
            self._queue_send(ALERT_RESOLVED, {"alert_id": resolved_id})

    def _evaluate_pattern(self, rule: dict, key: str, value: Any) -> None:
        """Check pattern rules (value matches for N seconds)."""
        condition = rule.get("condition", {})
        pattern = condition.get("key", "")
        if not fnmatch(key, pattern):
            return

        expected = condition.get("value")
        alert_key = f"rule:{rule['id']}:{key}"

        if _compare(value, "=", expected):
            # Value matches — start or continue timer
            if alert_key not in self._pattern_timers:
                self._pattern_timers[alert_key] = time.time()
            # Actual trigger check happens in _periodic_check_loop
        else:
            # Value no longer matches — clear timer and resolve if active
            self._pattern_timers.pop(alert_key, None)
            if alert_key in self._active_alerts:
                resolved_id = self._active_alerts.pop(alert_key)
                self._queue_send(ALERT_RESOLVED, {"alert_id": resolved_id})

    # --- Periodic Check Loop ---

    async def _periodic_check_loop(self) -> None:
        """Runs every 30 seconds. Checks pattern durations and absence rules."""
        try:
            while self._running:
                await asyncio.sleep(30)
                now = time.time()

                # Check pattern rule timers
                for alert_key, start_time in list(self._pattern_timers.items()):
                    if alert_key in self._active_alerts:
                        continue  # Already fired

                    rule = self._find_rule_from_key(alert_key)
                    if not rule:
                        self._pattern_timers.pop(alert_key, None)
                        continue

                    duration = rule.get("condition", {}).get("duration_seconds", 0)
                    if now - start_time >= duration:
                        alert_id = str(uuid.uuid4())
                        self._active_alerts[alert_key] = alert_id
                        device_id = _extract_device_id(alert_key.split(":", 2)[-1])
                        await self._agent.send_message(ALERT, {
                            "alert_id": alert_id,
                            "severity": rule.get("severity", "warning"),
                            "category": rule.get("category", "device"),
                            "device_id": device_id,
                            "message": f"{rule['name']}: condition held for {duration}s",
                            "detail": {"rule_id": rule["id"], "duration_seconds": duration},
                        })

                # Prune stale device entries (devices not seen in 24 hours)
                stale_cutoff = now - 86400
                for dev_id in [k for k, t in self._last_state_times.items() if t < stale_cutoff]:
                    self._last_state_times.pop(dev_id, None)

                # Check absence rules
                for rule in self._rules:
                    if rule.get("rule_type") != "absence" or not rule.get("enabled", True):
                        continue

                    threshold_secs = rule.get("condition", {}).get("threshold_seconds", 120)
                    key_prefix = rule.get("condition", {}).get("key_prefix", "device.")

                    for device_id, last_time in list(self._last_state_times.items()):
                        # Only check devices matching the key_prefix (glob-style)
                        full_key = f"device.{device_id}"
                        if not fnmatch(full_key, key_prefix):
                            continue

                        alert_key = f"rule:{rule['id']}:{device_id}"

                        if now - last_time > threshold_secs and alert_key not in self._active_alerts:
                            alert_id = str(uuid.uuid4())
                            self._active_alerts[alert_key] = alert_id
                            elapsed = int(now - last_time)
                            await self._agent.send_message(ALERT, {
                                "alert_id": alert_id,
                                "severity": rule.get("severity", "warning"),
                                "category": "device",
                                "device_id": device_id,
                                "message": f"{rule['name']}: {device_id} not reporting for {elapsed}s",
                                "detail": {"rule_id": rule["id"], "threshold_seconds": threshold_secs},
                            })
                        elif now - last_time <= threshold_secs and alert_key in self._active_alerts:
                            resolved_id = self._active_alerts.pop(alert_key)
                            await self._agent.send_message(ALERT_RESOLVED, {"alert_id": resolved_id})

        except asyncio.CancelledError:
            return

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
            rules = data.get("rules", [])
        else:
            rules = []

        old_rule_ids = {r["id"] for r in self._rules}
        new_rule_ids = {r["id"] for r in rules}

        # Resolve alerts for deleted rules
        deleted_ids = old_rule_ids - new_rule_ids
        for alert_key in list(self._active_alerts.keys()):
            parts = alert_key.split(":")
            if len(parts) >= 2 and parts[0] == "rule" and parts[1] in deleted_ids:
                resolved_id = self._active_alerts.pop(alert_key)
                self._queue_send(ALERT_RESOLVED, {"alert_id": resolved_id})

        # Clear pattern timers for deleted rules
        for alert_key in list(self._pattern_timers.keys()):
            parts = alert_key.split(":")
            if len(parts) >= 2 and parts[0] == "rule" and parts[1] in deleted_ids:
                self._pattern_timers.pop(alert_key, None)

        self._rules = rules
        log.info("Alert monitor: updated to %d custom rule(s)", len(rules))

    # --- Helpers ---

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
        for r in self._rules:
            if r.get("id") == rule_id:
                return r
        return None


# --- Module-level helpers ---


def _compare(value: Any, operator: str, threshold: Any) -> bool:
    """Compare a value against a threshold using the given operator."""
    try:
        # Handle None explicitly
        if value is None:
            return operator == "=" and threshold is None
        if threshold is None:
            return operator == "!=" and value is not None

        # Handle booleans — compare as strings to avoid bool/int confusion
        if isinstance(value, bool) or isinstance(threshold, bool):
            if operator == "=":
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
        if operator == "=":
            return str(value) == str(threshold)
        if operator == "!=":
            return str(value) != str(threshold)
    except (TypeError, ValueError):
        return False
    return False


def _extract_device_id(key: str) -> str | None:
    """Extract device ID from a state key like 'device.projector1.power'."""
    parts = key.split(".")
    if len(parts) >= 2 and parts[0] == "device":
        return parts[1]
    return None
