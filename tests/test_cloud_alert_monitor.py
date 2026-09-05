"""Tests for the agent-side AlertMonitor — rule evaluation, alerts, resolution."""

import asyncio
import time
import uuid

import pytest

from openavc.cloud.alert_monitor import AlertMonitor, _compare, _extract_device_id


def alerts(agent):
    """Just the alert traffic.

    The monitor also sends its project monitor manifest on start and on every
    project apply, which is a declaration rather than an event and is not what
    these tests are about.
    """
    return [
        (t, p) for t, p in agent.sent_messages
        if t in ("alert", "alert_resolved")
    ]


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

    def get_matching(self, pattern):
        from fnmatch import fnmatch

        return {k: v for k, v in self._data.items() if fnmatch(k, pattern)}

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

    assert len(alerts(agent)) == 1
    msg_type, payload = alerts(agent)[0]
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

    assert len(alerts(agent)) == 1
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

    assert len(alerts(agent)) == 1
    msg_type, payload = agent.sent_messages[0]
    assert msg_type == "alert"
    assert payload["rule_id"] == rule_id
    assert payload["device_id"] == "projector1"

    await monitor.stop()


def test_build_alert_removed_from_protocol():
    """A22: The dead `build_alert` builder is gone so no caller can produce
    an alert message without rule_id by accident.
    """
    from openavc.cloud import protocol
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

    assert len(alerts(agent)) == 1

    # Resolve
    state.set("device.projector1.lamp_hours", 1400)

    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)

    assert len(alerts(agent)) == 2
    assert alerts(agent)[1][0] == "alert_resolved"

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

    assert len(alerts(agent)) == 1

    # Now delete the rule
    monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": []})

    # Process the resolve
    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)

    assert len(alerts(agent)) == 2
    assert alerts(agent)[1][0] == "alert_resolved"

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

    monkeypatch.setattr("openavc.cloud.alert_monitor.asyncio.sleep", instant_sleep)

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

    fired = [b for b in batch if b[0] == "alert"]
    assert len(fired) == 1
    assert len(fired[0][1]["message"]) <= 2000

    await monitor.stop()


# --- Project-declared monitors (the monitor plan, §6) ---
#
# The point of every test below: the tile the Dashboard draws and the alert the
# cloud receives come from ONE declaration in the project. Nothing here teaches
# the alert monitor what a monitor is — a monitor compiles to an ordinary rule
# and is evaluated by the code that was already here.


async def _drain(monitor, agent):
    """Push whatever the sync callbacks queued through the agent."""
    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)


@pytest.mark.asyncio
async def test_the_manifest_is_sent_on_connect():
    """The cloud cannot draw a tile for a reading it has never been told about,
    and reading the list off the daily project snapshot would mean tagging
    something and waiting up to a day to see it."""
    agent = MockAgent()
    monitors = [{"key": "device.proj.lamp_hours", "label": "Lamp Hours", "unit": "hours"}]
    monitor = AlertMonitor(
        agent, MockStateStore(), MockEventBus(), monitors_provider=lambda: monitors
    )
    await monitor.start()
    await _drain(monitor, agent)

    sent = [(t, p) for t, p in agent.sent_messages if t == "monitors"]
    assert len(sent) == 1
    assert sent[0][1] == {"monitors": monitors}

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_project_save_re_sends_the_manifest_and_recompiles():
    agent = MockAgent()
    monitors: list[dict] = []
    monitor = AlertMonitor(
        agent, MockStateStore(), MockEventBus(), monitors_provider=lambda: monitors
    )
    await monitor.start()
    await _drain(monitor, agent)
    assert monitor._project_rules == []

    monitors.append({
        "key": "device.proj.lamp_hours", "label": "Lamp Hours", "normal_max": 2000,
    })
    monitor._on_project_applied_sync("system.project.reloaded", None)
    await _drain(monitor, agent)

    assert [r["id"] for r in monitor._project_rules] == [
        "monitor.device.proj.lamp_hours.above",
    ]
    assert [t for t, _ in agent.sent_messages if t == "monitors"] == [
        "monitors", "monitors",
    ]

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_project_limit_fires_through_the_ordinary_alert_path():
    """No cloud rule row behind it — exactly what "Ask for help" already does."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "device.proj.lamp_hours", "label": "Lamp Hours",
            "unit": "hours", "normal_max": 2000,
        }],
    )
    await monitor.start()

    state.set("device.proj.lamp_hours", 2400)
    await _drain(monitor, agent)

    fired = alerts(agent)
    assert len(fired) == 1
    assert fired[0][0] == "alert"
    assert fired[0][1]["rule_id"] == "monitor.device.proj.lamp_hours.above"
    assert fired[0][1]["device_id"] == "proj"
    assert "Lamp Hours" in fired[0][1]["message"]

    # ...and it resolves when the reading comes back inside normal.
    state.set("device.proj.lamp_hours", 1200)
    await _drain(monitor, agent)
    assert alerts(agent)[1][0] == "alert_resolved"

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_cloud_push_does_not_delete_what_the_project_declared():
    """The two sources are separate lists for exactly this reason."""
    agent = MockAgent()
    monitor = AlertMonitor(
        agent, MockStateStore(), MockEventBus(),
        monitors_provider=lambda: [{"key": "var.occupied",
                                    "states": {"true": {"normal": True}}}],
    )
    await monitor.start()
    assert len(monitor._project_rules) == 1

    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(rule_id="cloud-1",
                             condition={"key": "system.disk_percent",
                                        "operator": ">", "value": 90})],
    })

    assert len(monitor._project_rules) == 1
    assert len(monitor._rules) == 1
    assert {r["id"] for r in monitor._all_rules()} == {
        "cloud-1", "monitor.var.occupied",
    }

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_value_leaving_the_normal_set_fires_after_the_duration():
    """A mute held forty minutes is a story; a mute held four seconds is
    somebody pressing a button."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "device.amp.fault", "label": "Amp",
            "states": {"false": {"label": "OK", "normal": True},
                       "true": {"label": "Faulted"}},
            "duration_seconds": 600,
        }],
    )
    await monitor.start()

    state.set("device.amp.fault", True)
    await _drain(monitor, agent)
    assert alerts(agent) == []          # armed, not fired
    assert len(monitor._pattern_timers) == 1

    # Not long enough yet...
    await monitor._run_periodic_checks(time.time() + 60)
    assert alerts(agent) == []

    # ...and now it has held.
    await monitor._run_periodic_checks(time.time() + 700)
    assert len(alerts(agent)) == 1
    assert alerts(agent)[0][1]["rule_id"] == "monitor.device.amp.fault"

    # Back to normal clears it.
    state.set("device.amp.fault", False)
    await _drain(monitor, agent)
    assert alerts(agent)[1][0] == "alert_resolved"

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_number_outside_its_range_also_honours_the_duration():
    """A threshold rule used to fire the instant the value crossed, so the one
    duration control could not have meant the same thing for a number."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "device.dsp.temp_c", "label": "DSP Temp",
            "normal_max": 45, "duration_seconds": 300,
        }],
    )
    await monitor.start()

    state.set("device.dsp.temp_c", 61)
    await _drain(monitor, agent)
    assert alerts(agent) == []

    await monitor._run_periodic_checks(time.time() + 400)
    assert len(alerts(agent)) == 1
    # The value is read live at fire time, so the alert states what is true now.
    assert "61" in alerts(agent)[0][1]["message"]

    state.set("device.dsp.temp_c", 20)
    await _drain(monitor, agent)
    assert alerts(agent)[1][0] == "alert_resolved"

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_reading_that_has_never_reported_does_not_fire():
    """The tile beside it reads "no reading yet"; the alert must agree. A
    `not_in` that treated None as "not one of the good values" would fire on
    every monitored reading the moment the agent connected."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "device.amp.fault",
            "states": {"false": {"normal": True}},
        }],
    )
    await monitor.start()

    state.set("device.amp.fault", None)
    await _drain(monitor, agent)
    assert alerts(agent) == []

    await monitor.stop()


def test_compare_set_membership():
    assert _compare("hdmi1", "in", ["hdmi1", "hdmi2"]) is True
    assert _compare("hdmi3", "in", ["hdmi1", "hdmi2"]) is False
    assert _compare("hdmi3", "not_in", ["hdmi1", "hdmi2"]) is True
    # A live boolean and a project's JSON-string spelling are one value.
    assert _compare(True, "in", ["true"]) is True
    assert _compare(False, "not_in", ["true"]) is True
    # ...and everything else stays case-sensitive.
    assert _compare("mic", "in", ["Mic"]) is False
    # Absence is neither in the set nor out of it.
    assert _compare(None, "not_in", ["true"]) is False
    assert _compare(None, "in", ["true"]) is False


# --- What is firing here, told to the cloud on connect ---
#
# The dict above is in memory. A resolve is only ever sent for something still
# in it, so an alert raised before a restart had nothing left in the world that
# could clear it -- it sat in the portal amber forever and the next reboot put
# another one beside it.


def active_alert_sets(agent):
    """Just the reconcile messages."""
    return [p for t, p in agent.sent_messages if t == "active_alerts"]


@pytest.mark.asyncio
async def test_a_restarted_instance_says_nothing_is_firing():
    """The empty list is the whole point of the message, not a degenerate case
    of it: it is what a fresh process has to say, and it is what lets the cloud
    clear what that process can no longer speak for."""
    agent = MockAgent()
    monitor = AlertMonitor(agent, MockStateStore(), MockEventBus())
    await monitor.start()
    await _drain(monitor, agent)

    assert active_alert_sets(agent) == [{"alert_ids": []}]

    await monitor.stop()


@pytest.mark.asyncio
async def test_the_set_names_what_is_actually_firing():
    """A reconnect inside one process has not forgotten anything, so the alert
    it raised before the drop must be named -- or the cloud would resolve a
    fault that is still happening."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "device.dsp.temp_c", "label": "DSP Temp", "normal_max": 45,
        }],
    )
    await monitor.start()
    state.set("device.dsp.temp_c", 61)
    await _drain(monitor, agent)

    fired = [p for t, p in agent.sent_messages if t == "alert"]
    assert len(fired) == 1

    # The connection drops and comes back; the process did not.
    await monitor.stop()
    await monitor.start()
    await _drain(monitor, agent)

    assert active_alert_sets(agent)[-1] == {"alert_ids": [fired[0]["alert_id"]]}

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_recovered_reading_leaves_the_set():
    """Resolving already sent alert_resolved. The set has to agree with it, or
    a reconnect would re-open the question the resolve settled."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "device.dsp.temp_c", "label": "DSP Temp", "normal_max": 45,
        }],
    )
    await monitor.start()
    state.set("device.dsp.temp_c", 61)
    await _drain(monitor, agent)
    state.set("device.dsp.temp_c", 20)
    await _drain(monitor, agent)

    await monitor.stop()
    await monitor.start()
    await _drain(monitor, agent)

    assert active_alert_sets(agent)[-1] == {"alert_ids": []}

    await monitor.stop()


@pytest.mark.asyncio
async def test_the_set_is_sent_on_every_connect():
    """Not once per process: the cloud's copy can drift on any reconnect, and
    the cloud has no way to ask."""
    agent = MockAgent()
    monitor = AlertMonitor(agent, MockStateStore(), MockEventBus())
    for _ in range(3):
        await monitor.start()
        await _drain(monitor, agent)
        await monitor.stop()

    assert active_alert_sets(agent) == [{"alert_ids": []}] * 3


def test_the_wire_name_matches_the_cloud():
    """Mirrors openavc-cloud api/ws/protocol.py — a paired pin lives there.

    Rename it on one side and the message is simply never routed: no error, no
    log line either side reads as a fault, and every restarted instance quietly
    goes back to leaving alerts nobody can clear.
    """
    from openavc.cloud.protocol import ACTIVE_ALERTS

    assert ACTIVE_ALERTS == "active_alerts"


# --- What is already wrong when we start looking ---
#
# Evaluation is edge-triggered, and StateStore.set notifies nobody when the
# value handed to it is the one already stored. A reading that is wrong and
# STAYS wrong is therefore evaluated exactly once, when it went wrong -- so a
# process that was not running at that moment never sees it at all.


@pytest.mark.asyncio
async def test_a_fault_that_survives_a_restart_is_raised_again():
    """The half that makes the reconciliation safe.

    A restart tells the cloud nothing is firing, so the old alert resolves. If
    the reading is still outside its limits and nothing re-raised it, the room
    would read fine with the fault still happening -- worse than the stuck alert
    the reconciliation removes. Seen on the bench before this existed.
    """
    agent = MockAgent()
    state = MockStateStore()
    # Set before the monitor is watching, the way a persisted variable is
    # restored (or a device reports) before the cloud connection comes up.
    state.set("var.volume", 92)

    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "var.volume", "label": "Volume", "normal_min": 0, "normal_max": 80,
        }],
    )
    await monitor.start()
    await _drain(monitor, agent)

    fired = [p for t, p in agent.sent_messages if t == "alert"]
    assert len(fired) == 1
    assert "92" in fired[0]["message"]
    # And the set says so, so the cloud keeps it rather than resolving it.
    assert active_alert_sets(agent)[-1] == {"alert_ids": [fired[0]["alert_id"]]}

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_reading_that_is_fine_at_startup_raises_nothing():
    """The sweep is an evaluation, not an announcement."""
    agent = MockAgent()
    state = MockStateStore()
    state.set("var.volume", 30)

    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "var.volume", "normal_min": 0, "normal_max": 80,
        }],
    )
    await monitor.start()
    await _drain(monitor, agent)

    assert alerts(agent) == []
    assert active_alert_sets(agent)[-1] == {"alert_ids": []}

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_cloud_rule_pushed_onto_a_wrong_reading_fires_at_once():
    """The push lands milliseconds after connect, so its rules missed the sweep
    in start(). Nothing else would evaluate them until the key next changed --
    and a disk that is full at 92% and stays there does not change."""
    agent = MockAgent()
    state = MockStateStore()
    state.set("system.disk_percent", 92.3)

    monitor = AlertMonitor(agent, state, MockEventBus())
    await monitor.start()
    await _drain(monitor, agent)
    assert alerts(agent) == []

    monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": [{
        "id": "disk", "name": "High disk usage", "rule_type": "threshold",
        "severity": "critical", "category": "system",
        "condition": {"key": "system.disk_percent", "operator": ">", "value": 90},
    }]})
    await _drain(monitor, agent)

    assert [t for t, _ in alerts(agent)] == ["alert"]

    await monitor.stop()


@pytest.mark.asyncio
async def test_tagging_a_reading_that_is_already_wrong_fires_at_once():
    """Authoring a limit around a value that is already outside it is the
    commonest way to write one, and it used to sit there doing nothing."""
    agent = MockAgent()
    state = MockStateStore()
    state.set("device.dsp.temp_c", 61)

    monitors: list[dict] = []
    monitor = AlertMonitor(
        agent, state, MockEventBus(), monitors_provider=lambda: monitors,
    )
    await monitor.start()
    await _drain(monitor, agent)
    assert alerts(agent) == []

    monitors.append({"key": "device.dsp.temp_c", "label": "DSP Temp", "normal_max": 45})
    monitor._on_project_applied_sync("system.project.reloaded", None)
    await _drain(monitor, agent)

    assert [t for t, _ in alerts(agent)] == ["alert"]

    await monitor.stop()


@pytest.mark.asyncio
async def test_the_sweep_arms_a_duration_rather_than_firing_through_it():
    """A limit with a duration means the same thing on a restart as it does at
    any other moment: a projector that is off is correct at 3am."""
    agent = MockAgent()
    state = MockStateStore()
    state.set("device.dsp.temp_c", 61)

    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{
            "key": "device.dsp.temp_c", "normal_max": 45, "duration_seconds": 300,
        }],
    )
    await monitor.start()
    await _drain(monitor, agent)

    assert alerts(agent) == []
    assert active_alert_sets(agent)[-1] == {"alert_ids": []}

    await monitor._run_periodic_checks(time.time() + 400)
    assert len(alerts(agent)) == 1

    await monitor.stop()


@pytest.mark.asyncio
async def test_the_sweep_does_not_re_fire_what_is_already_firing():
    """A reconnect inside one process sweeps the same wrong reading again."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{"key": "var.volume", "normal_max": 80}],
    )
    await monitor.start()
    state.set("var.volume", 92)
    await _drain(monitor, agent)
    assert len(alerts(agent)) == 1

    await monitor.stop()
    await monitor.start()
    await _drain(monitor, agent)

    assert len(alerts(agent)) == 1

    await monitor.stop()


# --- When it happened, versus when the cloud heard about it ---
#
# An alert's fired_at used to be stamped by the cloud when the message arrived,
# so everything between the room seeing the fault and the cloud reading about
# it -- the send loop's flush, a replay across a reconnect -- was subtracted
# from the fault. The span the service report prints as "how fast did you deal
# with it?" is resolved_at - fired_at, so both ends have to come from the clock
# that watched the room.


class _FakeClock:
    """A clock the test drives, formatted exactly as the wire wants it."""

    def __init__(self, start: float = 1_800_000_000.0):
        self.now = start

    def iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.now, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _instant(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


@pytest.fixture
def wire_clock(monkeypatch):
    from openavc.cloud import protocol

    clock = _FakeClock()
    monkeypatch.setattr(protocol, "_now_iso", clock.iso)
    return clock


@pytest.mark.asyncio
async def test_an_alert_is_stamped_when_the_reading_went_wrong(wire_clock):
    """Not when the send loop got round to it, up to a second later."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{"key": "var.volume", "normal_max": 80}],
    )
    await monitor.start()

    went_wrong = wire_clock.iso()
    state.set("var.volume", 92)
    wire_clock.advance(1.0)  # the flush interval
    await _drain(monitor, agent)

    msg_type, payload = alerts(agent)[0]
    assert msg_type == "alert"
    assert payload["fired_at"] == went_wrong
    assert payload["fired_at"] != wire_clock.iso()

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_resolve_is_stamped_when_the_reading_came_back(wire_clock):
    """The other end of the same span, and it moves for the same reasons."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{"key": "var.volume", "normal_max": 80}],
    )
    await monitor.start()

    state.set("var.volume", 92)
    await _drain(monitor, agent)

    wire_clock.advance(120.0)
    came_back = wire_clock.iso()
    state.set("var.volume", 40)
    wire_clock.advance(1.0)
    await _drain(monitor, agent)

    msg_type, payload = alerts(agent)[-1]
    assert msg_type == "alert_resolved"
    assert payload["resolved_at"] == came_back

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_fault_that_heals_inside_one_flush_keeps_its_real_duration(wire_clock):
    """The case seen in the wild: both messages leave in the same batch,
    milliseconds apart, for a fault that lasted half a second. Read off the
    cloud's clock that is a millisecond-long repair; read off the room's it is
    what actually happened."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(
        agent, state, MockEventBus(),
        monitors_provider=lambda: [{"key": "var.volume", "normal_max": 80}],
    )
    await monitor.start()

    state.set("var.volume", 92)
    wire_clock.advance(0.5)
    state.set("var.volume", 40)
    wire_clock.advance(0.5)
    await _drain(monitor, agent)

    fired = dict(alerts(agent))["alert"]["fired_at"]
    resolved = dict(alerts(agent))["alert_resolved"]["resolved_at"]
    assert _instant(resolved) - _instant(fired) == pytest.approx(0.5, abs=0.01)

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_duration_rule_is_stamped_when_it_fired_not_when_it_armed(wire_clock):
    """A rule that deliberately waits before complaining is not complaining
    during the wait. Counting its grace window as fault time would report a
    repair nobody could have shortened."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(agent, state, MockEventBus())
    await monitor.start()

    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_type="pattern",
            condition={
                "key": "device.projector1.status",
                "value": "error",
                "duration_seconds": 300,
            },
        )]
    })

    armed = time.time()
    state.set("device.projector1.status", "error")
    await _drain(monitor, agent)
    assert alerts(agent) == []

    wire_clock.advance(400.0)
    held_long_enough = wire_clock.iso()
    await monitor._run_periodic_checks(armed + 400)

    msg_type, payload = alerts(agent)[0]
    assert msg_type == "alert"
    assert payload["fired_at"] == held_long_enough

    await monitor.stop()


# --- Why the alert ended ---
#
# The cloud counts the span from fired_at to resolved_at as the time a fault
# took to fix, on a report an integrator hands a client. That is only true when
# the reading came back. A rule that simply stopped being asked -- disabled in
# the portal, deleted, or a monitor taken out of the project -- says nothing
# about the room, and used to be indistinguishable from a repair on the wire.


@pytest.mark.asyncio
async def test_a_reading_coming_back_is_a_recovery():
    """The default, and the whole compatibility story: every resolve that is
    not something else keeps saying exactly what it said before."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(agent, state, MockEventBus())
    await monitor.start()

    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            condition={"key": "device.proj.temp", "operator": ">", "value": 80},
        )]
    })
    state.set("device.proj.temp", 90)
    await _drain(monitor, agent)
    state.set("device.proj.temp", 40)
    await _drain(monitor, agent)

    msg_type, payload = alerts(agent)[-1]
    assert msg_type == "alert_resolved"
    assert payload["reason"] == "recovered"

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_device_reporting_again_is_a_recovery():
    """The absence path sends its own resolve, straight through the agent
    rather than the queue, so it needs its own pin."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(agent, state, MockEventBus())
    await monitor.start()

    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_type="absence",
            condition={"key_prefix": "device.", "threshold_seconds": 60},
        )]
    })
    state.set("device.proj.power", "on")
    await monitor._run_periodic_checks(time.time() + 120)
    assert alerts(agent)[-1][0] == "alert"

    state.set("device.proj.power", "off")
    await monitor._run_periodic_checks(time.time())

    msg_type, payload = alerts(agent)[-1]
    assert msg_type == "alert_resolved"
    assert payload["reason"] == "recovered"

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_rule_leaving_the_pushed_set_is_not_a_repair():
    """Disabled and deleted arrive here identically -- the portal pushes only
    enabled rules -- so the instance reports the one thing it actually knows."""
    agent = MockAgent()
    state = MockStateStore()
    monitor = AlertMonitor(agent, state, MockEventBus())
    await monitor.start()

    rule_id = str(uuid.uuid4())
    monitor._on_rules_update_sync("cloud.alert_rules_update", {
        "rules": [_make_rule(
            rule_id=rule_id,
            condition={"key": "device.proj.temp", "operator": ">", "value": 80},
        )]
    })
    state.set("device.proj.temp", 90)
    await _drain(monitor, agent)
    assert alerts(agent)[-1][0] == "alert"

    # The reading never comes back. The rule just stops being pushed.
    monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": []})
    await _drain(monitor, agent)

    msg_type, payload = alerts(agent)[-1]
    assert msg_type == "alert_resolved"
    assert payload["reason"] == "rule_removed"

    await monitor.stop()


@pytest.mark.asyncio
async def test_a_monitor_removed_from_the_project_is_not_a_repair():
    """The path no cloud-side sweep could ever reach: a monitor compiles to a
    rule id of its own, so the alert carries no rule row to sweep from."""
    agent = MockAgent()
    state = MockStateStore()
    monitors = [{
        "key": "device.proj.lamp_hours", "label": "Lamp Hours", "normal_max": 2000,
    }]
    monitor = AlertMonitor(
        agent, state, MockEventBus(), monitors_provider=lambda: monitors,
    )
    await monitor.start()

    state.set("device.proj.lamp_hours", 2400)
    await _drain(monitor, agent)
    assert alerts(agent)[-1][0] == "alert"

    monitors.clear()
    monitor._on_project_applied_sync("system.project.reloaded", None)
    await _drain(monitor, agent)

    msg_type, payload = alerts(agent)[-1]
    assert msg_type == "alert_resolved"
    assert payload["reason"] == "rule_removed"

    await monitor.stop()
