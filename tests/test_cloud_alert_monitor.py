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

    await monitor.stop()


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
    """Built-in CPU alert fires when value exceeds 95%."""
    agent = MockAgent()
    state = MockStateStore()
    events = MockEventBus()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()

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
