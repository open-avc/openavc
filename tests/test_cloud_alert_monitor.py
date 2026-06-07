"""Tests for the agent-side AlertMonitor — rule evaluation, alerts, resolution."""

import asyncio
import uuid

import pytest

from server.cloud.alert_monitor import AlertMonitor, _compare, _extract_device_id


# --- Mock classes ---


class MockStateStore:
    """Minimal StateStore mock for testing."""

    def __init__(self):
        self._listeners: dict[str, tuple] = {}
        self._data: dict[str, any] = {}
        self._next_id = 0

    def subscribe(self, pattern, callback):
        self._next_id += 1
        sub_id = str(self._next_id)
        self._listeners[sub_id] = (pattern, callback)
        return sub_id

    def unsubscribe(self, sub_id):
        self._listeners.pop(sub_id, None)

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, source="test"):
        old = self._data.get(key)
        self._data[key] = value
        for _, (pattern, callback) in self._listeners.items():
            if pattern == "*" or key == pattern:
                callback(key, old, value, source)


class MockEventBus:
    """Minimal EventBus mock for testing."""

    def __init__(self):
        self._handlers: dict[str, list] = {}
        self._next_id = 0

    def on(self, event, handler):
        self._next_id += 1
        self._handlers.setdefault(event, []).append(handler)
        return self._next_id

    def off(self, handler_id):
        pass

    async def emit(self, event, data):
        for handler in self._handlers.get(event, []):
            handler(data)


class MockAgent:
    """Minimal CloudAgent mock that records sent messages."""

    def __init__(self):
        self.state = MockStateStore()
        self.events = MockEventBus()
        self.sent_messages: list[tuple[str, dict]] = []
        self._config = {
            "features": {"alerts": True},
        }

    async def send_message(self, msg_type, payload):
        self.sent_messages.append((msg_type, payload))


# --- Helper ---


def _make_rule(
    rule_id=None, name="Test Rule", rule_type="threshold",
    condition=None, severity="warning", category="device", enabled=True,
):
    return {
        "id": rule_id or str(uuid.uuid4()),
        "name": name,
        "rule_type": rule_type,
        "condition": condition or {},
        "severity": severity,
        "category": category,
        "enabled": enabled,
    }


# --- Unit Tests for helpers ---


def test_compare_gt():
    assert _compare(10, ">", 5) is True
    assert _compare(3, ">", 5) is False


def test_compare_lt():
    assert _compare(3, "<", 5) is True
    assert _compare(10, "<", 5) is False


def test_compare_gte():
    assert _compare(5, ">=", 5) is True
    assert _compare(4, ">=", 5) is False


def test_compare_lte():
    assert _compare(5, "<=", 5) is True
    assert _compare(6, "<=", 5) is False


def test_compare_eq():
    assert _compare("on", "=", "on") is True
    assert _compare("off", "=", "on") is False


def test_compare_neq():
    assert _compare("off", "!=", "on") is True
    assert _compare("on", "!=", "on") is False


def test_compare_double_eq():
    """== operator works the same as ="""
    assert _compare("on", "==", "on") is True
    assert _compare("off", "==", "on") is False
    assert _compare(10, "==", 10) is True
    assert _compare(None, "==", None) is True
    assert _compare(True, "==", True) is True
    assert _compare(True, "==", False) is False


def test_compare_contains():
    assert _compare("error: device offline", "contains", "offline") is True
    assert _compare("error: device offline", "contains", "timeout") is False
    assert _compare("HDMI1", "contains", "HDMI") is True
    assert _compare(12345, "contains", "234") is True  # coerced to str


def test_compare_not_contains():
    assert _compare("all systems normal", "not_contains", "error") is True
    assert _compare("error detected", "not_contains", "error") is False


def test_compare_matches():
    assert _compare("error code 42", "matches", r"code \d+") is True
    assert _compare("no match here", "matches", r"code \d+") is False
    assert _compare("HDMI1", "matches", r"^HDMI\d$") is True


def test_compare_matches_invalid_regex():
    """Invalid regex pattern returns False, doesn't crash."""
    assert _compare("test", "matches", r"[invalid") is False


def test_compare_invalid_values():
    assert _compare("abc", ">", 5) is False
    assert _compare(None, ">", 5) is False


def test_extract_device_id():
    assert _extract_device_id("device.projector1.power") == "projector1"
    assert _extract_device_id("var.something") is None
    assert _extract_device_id("device") is None


# --- Integration Tests ---


@pytest.mark.asyncio
async def test_threshold_rule_fires_alert():
    """Threshold rule fires when value exceeds threshold."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    # Push a threshold rule
    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            condition={"key": "device.projector1.lamp_hours", "operator": ">", "value": 1500},
        )]
    })

    # Simulate state change that triggers the rule
    state.set("device.projector1.lamp_hours", 1600)

    # Process pending sends
    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)

    assert len(agent.sent_messages) == 1
    msg_type, payload = agent.sent_messages[0]
    assert msg_type == "alert"
    assert payload["severity"] == "warning"
    assert "projector1" in payload["message"]
    # A22: rule_id must be a top-level field so the cloud's alert_ingester
    # can link the alert back to its AlertRule.
    assert payload["rule_id"] == rule_id

    await monitor.stop()


@pytest.mark.asyncio
async def test_pattern_alert_includes_rule_id():
    """A22: Pattern-rule alerts fired from _periodic_check_loop must include rule_id.

    Without it, the cloud's alert_ingester can't link the alert to its
    AlertRule. _run_periodic_checks is the inner helper that the loop calls
    on each tick — drive it directly so we don't have to wait 30s.
    """
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            rule_type="pattern",
            condition={
                "key": "device.projector1.state",
                "value": "warming",
                "duration_seconds": 5,
            },
        )]
    })

    # Start the pattern timer
    state.set("device.projector1.state", "warming")
    # The sync path puts a start_time in _pattern_timers; the alert itself
    # only fires from _run_periodic_checks once duration_seconds has elapsed.
    assert monitor._pattern_timers, "Pattern rule should have armed a timer"

    # Backdate the timer so the duration check passes when we tick
    for key in list(monitor._pattern_timers):
        monitor._pattern_timers[key] -= 10

    import time as _t
    await monitor._run_periodic_checks(now=_t.time())

    assert len(agent.sent_messages) == 1
    msg_type, payload = agent.sent_messages[0]
    assert msg_type == "alert"
    assert payload["rule_id"] == rule_id

    await monitor.stop()


@pytest.mark.asyncio
async def test_absence_alert_includes_rule_id():
    """A22: Absence-rule alerts fired from _periodic_check_loop must include rule_id."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            rule_type="absence",
            condition={"key_prefix": "device.*", "threshold_seconds": 60},
        )]
    })

    # Seed device activity, then pretend it stopped reporting past the
    # absence threshold (60s) but not so far back that the 24-hour stale
    # prune drops it before the absence check runs.
    import time as _t
    now = _t.time()
    state.set("device.projector1.power", "on")
    monitor._last_state_times["projector1"] = now - 120  # 2 minutes stale

    await monitor._run_periodic_checks(now=now)

    assert len(agent.sent_messages) == 1
    msg_type, payload = agent.sent_messages[0]
    assert msg_type == "alert"
    assert payload["rule_id"] == rule_id
    assert payload["device_id"] == "projector1"

    await monitor.stop()


def test_build_alert_removed_from_protocol():
    """A22: The dead `build_alert` builder is gone so no caller can produce
    an alert message without rule_id by accident.
    """
    from server.cloud import protocol
    assert not hasattr(protocol, "build_alert"), (
        "build_alert was removed in A22 — re-introducing it without rule_id "
        "would silently break alert→rule linking on the cloud side."
    )


@pytest.mark.asyncio
async def test_threshold_rule_resolves():
    """Threshold rule resolves when value drops below threshold."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            condition={"key": "device.projector1.lamp_hours", "operator": ">", "value": 1500},
        )]
    })

    # Trigger
    state.set("device.projector1.lamp_hours", 1600)

    # Process sends
    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)

    assert len(agent.sent_messages) == 1

    # Resolve
    state.set("device.projector1.lamp_hours", 1400)

    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)

    assert len(agent.sent_messages) == 2
    assert agent.sent_messages[1][0] == "alert_resolved"

    await monitor.stop()


@pytest.mark.asyncio
async def test_threshold_no_duplicate_fire():
    """Threshold rule doesn't fire twice for the same condition."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            condition={"key": "device.proj.lamp", "operator": ">", "value": 100},
        )]
    })

    # Two state changes, both above threshold
    state.set("device.proj.lamp", 150)
    state.set("device.proj.lamp", 200)

    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()

    # Should only fire once
    alerts = [b for b in batch if b[0] == "alert"]
    assert len(alerts) == 1

    await monitor.stop()


@pytest.mark.asyncio
async def test_builtin_cpu_alert():
    """CPU alert fires when cloud-pushed rule threshold is exceeded."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    # Push a CPU threshold rule (as the cloud would)
    events._handlers.get("cloud.alert_rules_update", [None])[0](
        "cloud.alert_rules_update",
        {"rules": [{
            "id": "cpu-rule-1",
            "name": "High CPU usage",
            "rule_type": "threshold",
            "condition": {"key": "system.cpu_percent", "operator": ">", "value": 95},
            "severity": "warning",
            "category": "system",
            "enabled": True,
        }]}
    )

    # Simulate CPU spike via state change
    state.set("system.cpu_percent", 97.0)

    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()

    alerts = [b for b in batch if b[0] == "alert"]
    assert len(alerts) == 1
    assert "CPU" in alerts[0][1]["message"]

    await monitor.stop()


@pytest.mark.asyncio
async def test_rules_update_resolves_deleted_rules():
    """When a rule is deleted, its active alerts are resolved."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            condition={"key": "device.proj.temp", "operator": ">", "value": 80},
        )]
    })

    # Trigger the rule
    state.set("device.proj.temp", 90)

    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)

    assert len(agent.sent_messages) == 1

    # Now delete the rule
    monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": []})

    # Process the resolve
    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)

    assert len(agent.sent_messages) == 2
    assert agent.sent_messages[1][0] == "alert_resolved"

    await monitor.stop()


@pytest.mark.asyncio
async def test_glob_pattern_matching():
    """Rules with wildcard patterns match multiple devices."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            condition={"key": "device.*.temp", "operator": ">", "value": 80},
        )]
    })

    state.set("device.display1.temp", 85)
    state.set("device.display2.temp", 90)

    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()

    alerts = [b for b in batch if b[0] == "alert"]
    assert len(alerts) == 2

    await monitor.stop()


@pytest.mark.asyncio
async def test_disabled_rule_not_evaluated():
    """Disabled rules should not trigger alerts."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            condition={"key": "device.proj.temp", "operator": ">", "value": 80},
            enabled=False,
        )]
    })

    state.set("device.proj.temp", 90)

    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]

    alerts = [b for b in batch if b[0] == "alert"]
    assert len(alerts) == 0

    await monitor.stop()


# --- Hardening regressions ---


@pytest.mark.asyncio
async def test_absence_default_key_prefix_fires():
    """An absence rule with no explicit key_prefix uses the default "device."
    which must match every device.<id> key. A literal fnmatch matched nothing,
    so the offline watchdog silently never fired.
    """
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id="abs1",
            rule_type="absence",
            condition={"threshold_seconds": 60},  # no key_prefix -> default "device."
        )]
    })

    import time as _t
    now = _t.time()
    monitor._last_state_times["projector1"] = now - 120  # 2 min stale

    await monitor._run_periodic_checks(now=now)

    alerts = [m for m in agent.sent_messages if m[0] == "alert"]
    assert len(alerts) == 1
    assert alerts[0][1]["device_id"] == "projector1"

    await monitor.stop()


@pytest.mark.asyncio
async def test_absence_threshold_beyond_24h_fires():
    """An absence threshold above the old hardcoded 24h prune horizon must still
    fire — the device entry has to survive long enough to be evaluated.
    """
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id="abs-long",
            rule_type="absence",
            condition={"key_prefix": "device.*", "threshold_seconds": 90000},  # 25h
        )]
    })

    import time as _t
    now = _t.time()
    # Stale just past the 25h threshold. The old 24h prune evicted this entry
    # before the absence check could ever see it.
    monitor._last_state_times["projector1"] = now - 90030

    await monitor._run_periodic_checks(now=now)

    alerts = [m for m in agent.sent_messages if m[0] == "alert"]
    assert len(alerts) == 1
    assert monitor._last_state_times.get("projector1") is not None  # not pruned

    await monitor.stop()


@pytest.mark.asyncio
async def test_periodic_loop_survives_tick_exception(monkeypatch):
    """An exception during a periodic tick (e.g. ConnectionClosed from a send)
    must not terminate the loop — that would silently stop all pattern and
    absence evaluation for the rest of the connection's life.
    """
    agent = MockAgent()
    monitor = AlertMonitor(agent, MockStateStore(), MockEventBus())
    monitor._running = True

    calls = []

    async def fake_checks(now):
        calls.append(now)
        if len(calls) == 1:
            raise ConnectionError("simulated send failure")
        # Stop once the loop has proven it survived the first-tick exception.
        monitor._running = False

    monitor._run_periodic_checks = fake_checks

    real_sleep = asyncio.sleep

    async def instant_sleep(_seconds):
        await real_sleep(0)

    monkeypatch.setattr("server.cloud.alert_monitor.asyncio.sleep", instant_sleep)

    await monitor._periodic_check_loop()

    # A second tick ran: the loop did not die on the first tick's exception.
    assert len(calls) >= 2


def test_unsafe_regex_rule_dropped():
    """A catastrophic-backtracking 'matches' regex pushed as a rule value is
    rejected at update time so it never runs synchronously on the event loop.
    """
    monitor = AlertMonitor(MockAgent(), MockStateStore(), MockEventBus())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id="redos",
            condition={"key": "device.*.msg", "operator": "matches", "value": "(a+)+$"},
        )]
    })
    assert monitor._rules == []


def test_safe_regex_rule_kept():
    """A well-formed 'matches' regex is preserved."""
    monitor = AlertMonitor(MockAgent(), MockStateStore(), MockEventBus())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id="ok",
            condition={"key": "device.*.msg", "operator": "matches", "value": r"error \d+"},
        )]
    })
    assert [r["id"] for r in monitor._rules] == ["ok"]


def test_malformed_rule_dropped_others_survive():
    """A rule missing 'id' is dropped without discarding the rest of the update
    (a KeyError used to swallow the whole update silently).
    """
    monitor = AlertMonitor(MockAgent(), MockStateStore(), MockEventBus())
    good = _make_rule(rule_id="good", condition={"key": "device.a.x", "operator": ">", "value": 1})
    bad = {"name": "no id", "rule_type": "threshold", "condition": {}}  # missing id
    monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": [good, bad]})
    assert {r["id"] for r in monitor._rules} == {"good"}


def test_malformed_rule_does_not_drop_whole_update():
    """The update still applies (replacing the prior rule set) rather than being
    silently dropped and leaving the stale rules in place.
    """
    monitor = AlertMonitor(MockAgent(), MockStateStore(), MockEventBus())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(rule_id="r1", condition={"key": "device.a.x", "operator": ">", "value": 1})]
    })
    assert {r["id"] for r in monitor._rules} == {"r1"}
    monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": [
        _make_rule(rule_id="r2", condition={"key": "device.a.x", "operator": ">", "value": 1}),
        {"name": "bad", "rule_type": "threshold", "condition": {}},
    ]})
    assert {r["id"] for r in monitor._rules} == {"r2"}


@pytest.mark.asyncio
async def test_alert_message_truncated_to_column_limit():
    """An alert message must not exceed the cloud's String(2000) column or
    PostgreSQL rejects the insert (22001) and the alert is silently lost.
    """
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id="r",
            condition={"key": "device.d.msg", "operator": "contains", "value": "X"},
        )]
    })

    # A device reports a very long string state into the matched key.
    state.set("device.d.msg", "X" * 5000)

    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()

    alerts = [b for b in batch if b[0] == "alert"]
    assert len(alerts) == 1
    assert len(alerts[0][1]["message"]) <= 2000

    await monitor.stop()
