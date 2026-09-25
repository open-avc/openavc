"""The device audit session: one at a time, the pauses it owns, and every way out.

The pause tests run against a real ``DeviceManager`` with an invented driver,
because what matters is what the manager does with a pause (the backstop, the
reconnect), not what a fake says it did.
"""

import asyncio
from typing import Any

import pytest

from openavc.audit.session import (
    ACTIVE,
    BUSY_AUDIT,
    BUSY_SCAN,
    CANCELLED,
    EXPIRED,
    FINISHED,
    SCAN_BLOCKED,
    SHUTDOWN,
    AuditBusy,
    AuditManager,
    AuditNotFound,
    AuditOptions,
    AuditTarget,
)
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.discovery.engine import DiscoveryEngine, ScanBlocked
from openavc.drivers.base import BaseDriver


class AcmeWidgetDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget_audit",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"host": "127.0.0.1", "port": 9999},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self):
        self.connect_calls += 1
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self):
        self.disconnect_calls += 1
        self._connected = False
        self.state.set(f"device.{self.device_id}.connected", False, source="driver")

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def dm(core):
    manager = DeviceManager(*core)
    yield manager
    for device_id in list(manager._pause_expiry_tasks):
        manager._cancel_pause_expiry(device_id)


def _connected_widget(dm, core, device_id="widget1"):
    driver = AcmeWidgetDriver(device_id, {}, *core)
    driver._connected = True
    core[0].set(f"device.{device_id}.connected", True, source="test")
    dm._devices[device_id] = driver
    return driver


class FakeScanState:
    def __init__(self, scanning: bool = False):
        self.scanning = scanning
        self.scan_blocker = None

    def is_scanning(self) -> bool:
        return self.scanning


TARGET = AuditTarget(address="widget.local", ip="10.0.0.50")


# ---------------------------------------------------------------------------
# One at a time
# ---------------------------------------------------------------------------


async def test_start_opens_an_active_session_with_a_timeline():
    manager = AuditManager(None)
    session = await manager.start(TARGET, AuditOptions(extended=True))
    try:
        assert manager.current() is session
        assert session.status == ACTIVE
        assert session.steps == ["target"]
        assert session.timeline[0].kind == "session.started"
        assert session.to_dict()["target"] == {"address": "widget.local", "ip": "10.0.0.50"}
    finally:
        await manager.shutdown()


async def test_a_second_session_is_refused_in_words():
    manager = AuditManager(None)
    first = await manager.start(TARGET)
    try:
        with pytest.raises(AuditBusy) as exc:
            await manager.start(AuditTarget(address="10.0.0.51", ip="10.0.0.51"))
        assert str(exc.value) == BUSY_AUDIT
        assert manager.current() is first
    finally:
        await manager.shutdown()


async def test_a_finished_session_frees_the_slot():
    manager = AuditManager(None)
    first = await manager.start(TARGET)
    await manager.finish(first.id)
    assert first.status == FINISHED
    assert manager.current() is None
    second = await manager.start(TARGET)
    assert second.id != first.id
    await manager.shutdown()


async def test_get_refuses_an_ended_or_unknown_session():
    manager = AuditManager(None)
    session = await manager.start(TARGET)
    await manager.finish(session.id, CANCELLED)
    with pytest.raises(AuditNotFound):
        manager.get(session.id)
    with pytest.raises(AuditNotFound):
        manager.get("nope")


# ---------------------------------------------------------------------------
# Discovery and the audit refuse each other
# ---------------------------------------------------------------------------


async def test_an_audit_will_not_start_during_a_scan():
    scans = FakeScanState(scanning=True)
    manager = AuditManager(None, scans)
    with pytest.raises(AuditBusy) as exc:
        await manager.start(TARGET)
    assert str(exc.value) == BUSY_SCAN
    assert manager.current() is None


async def test_a_scan_will_not_start_during_an_audit():
    discovery = DiscoveryEngine()
    manager = AuditManager(None, discovery)
    session = await manager.start(TARGET)
    try:
        with pytest.raises(ScanBlocked) as exc:
            await discovery.start_scan(subnets=["10.0.0.0/30"])
        assert str(exc.value) == SCAN_BLOCKED
        assert not discovery.is_scanning()
    finally:
        await manager.finish(session.id)
    assert discovery.scan_blocker() is None


# ---------------------------------------------------------------------------
# Pauses the session owns
# ---------------------------------------------------------------------------


async def test_start_pauses_project_devices_and_finish_resumes_them(dm, core):
    state, _ = core
    driver = _connected_widget(dm, core)
    manager = AuditManager(dm)

    session = await manager.start(TARGET, pause=[("widget1", "Lobby Widget")])
    assert state.get("device.widget1.paused") is True
    assert driver.disconnect_calls == 1
    assert [p.to_dict() for p in session.paused] == [
        {"device_id": "widget1", "name": "Lobby Widget", "owned": True},
    ]

    await manager.finish(session.id)
    assert state.get("device.widget1.paused") is False
    assert state.get("device.widget1.connected") is True
    assert driver.connect_calls == 1
    kinds = [e.kind for e in session.timeline]
    assert kinds.index("device.paused") < kinds.index("device.resumed")


async def test_a_pause_the_audit_did_not_make_is_not_the_audits_to_resume(dm, core):
    """The Driver Builder's test panel holds its own pause; finishing an audit
    must not reconnect a device that panel is still testing against."""
    state, _ = core
    _connected_widget(dm, core)
    await dm.pause_device("widget1")
    manager = AuditManager(dm)

    session = await manager.start(TARGET, pause=[("widget1", "Lobby Widget")])
    assert session.paused[0].owned is False
    await manager.finish(session.id)
    assert state.get("device.widget1.paused") is True


async def test_a_pause_that_fails_undoes_the_ones_before_it(dm, core):
    state, _ = core
    _connected_widget(dm, core)
    manager = AuditManager(dm)

    with pytest.raises(Exception):
        await manager.start(TARGET, pause=[("widget1", "Lobby Widget"), ("gone", "Gone")])
    assert manager.current() is None
    assert state.get("device.widget1.paused") is False


async def test_the_session_keeps_its_pauses_alive(dm, core):
    """The device manager resumes a pause nobody refreshes; the session
    refreshes the ones it holds for as long as it lives."""
    state, _ = core
    _connected_widget(dm, core)
    manager = AuditManager(dm, keepalive_seconds=0.05, idle_check_seconds=0.05)
    session = await manager.start(TARGET, pause=[("widget1", "Lobby Widget")])
    first_deadline = dm._pause_deadlines["widget1"]
    await asyncio.sleep(0.2)
    assert dm._pause_deadlines["widget1"] > first_deadline
    assert state.get("device.widget1.paused") is True
    await manager.finish(session.id)


async def test_a_lost_session_is_covered_by_the_pause_backstop(dm, core):
    """A session that dies without its teardown (the process crashed, the
    watch was killed) leaves its pauses to the device manager's own backstop,
    which brings the device back with nobody calling resume."""
    state, _ = core
    driver = _connected_widget(dm, core)
    manager = AuditManager(dm, keepalive_seconds=3600, pause_ttl=0.1)
    session = await manager.start(TARGET, pause=[("widget1", "Lobby Widget")])
    assert state.get("device.widget1.paused") is True
    # Kill the session's own watch (its keepalive) without tearing down.
    for task in list(session._tasks):
        task.cancel()
    await asyncio.sleep(0.4)
    assert state.get("device.widget1.paused") is False
    assert state.get("device.widget1.connected") is True
    assert driver.connect_calls == 1


async def test_a_device_removed_from_the_project_is_let_go(dm, core):
    _connected_widget(dm, core)
    manager = AuditManager(dm, keepalive_seconds=0.05, idle_check_seconds=0.05)
    session = await manager.start(TARGET, pause=[("widget1", "Lobby Widget")])
    dm._cancel_pause_expiry("widget1")
    del dm._devices["widget1"]
    await asyncio.sleep(0.2)
    assert session.paused == []
    assert any(e.kind == "device.released" for e in session.timeline)
    await manager.finish(session.id)


# ---------------------------------------------------------------------------
# Every way out
# ---------------------------------------------------------------------------


async def test_an_idle_session_ends_and_resumes_its_devices(dm, core):
    state, _ = core
    _connected_widget(dm, core)
    manager = AuditManager(dm, idle_timeout=0.1, idle_check_seconds=0.02)
    session = await manager.start(TARGET, pause=[("widget1", "Lobby Widget")])
    await asyncio.sleep(0.4)
    assert session.status == EXPIRED
    assert manager.current() is None
    assert state.get("device.widget1.paused") is False
    assert "without activity" in session.timeline[-1].text


async def test_a_request_keeps_a_session_alive():
    manager = AuditManager(None, idle_timeout=0.15, idle_check_seconds=0.02)
    session = await manager.start(TARGET)
    for _ in range(6):
        await asyncio.sleep(0.05)
        session.touch()
    assert session.status == ACTIVE
    await manager.shutdown()
    assert session.status == SHUTDOWN


async def test_teardown_cancels_tasks_then_runs_hooks_then_end_hooks():
    manager = AuditManager(None)
    session = await manager.start(TARGET)
    order: list[str] = []

    async def forever():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append("task cancelled")
            raise

    async def stop_listeners():
        order.append("hook")

    async def end_hook(ended):
        order.append(f"end {ended.status}")

    session.track_task(asyncio.create_task(forever()))
    session.on_teardown(stop_listeners)
    manager.add_end_hook(end_hook)
    await asyncio.sleep(0)
    await manager.finish(session.id, CANCELLED)
    assert order == ["task cancelled", "hook", f"end {CANCELLED}"]


async def test_subscribers_get_this_sessions_messages_until_they_leave():
    manager = AuditManager(None)
    session = await manager.start(TARGET)
    got: list[dict] = []
    unsubscribe = session.subscribe(got.append)
    session.add_timeline("check.step", "Checked the address.")
    unsubscribe()
    session.add_timeline("check.step", "Not delivered.")
    assert [m["type"] for m in got] == ["audit.timeline"]
    assert got[0]["session_id"] == session.id
    assert got[0]["entry"]["text"] == "Checked the address."
    await manager.shutdown()


async def test_a_failing_subscriber_does_not_stop_the_others():
    manager = AuditManager(None)
    session = await manager.start(TARGET)
    got: list[dict] = []

    def broken(_msg):
        raise RuntimeError("socket gone")

    session.subscribe(broken)
    session.subscribe(got.append)
    session.add_timeline("check.step", "Still delivered.")
    assert len(got) == 1
    await manager.shutdown()


async def test_both_scan_doors_give_the_refusal_sentence(monkeypatch):
    """The Discovery tab and the cloud AI's scan tool both start scans, and a
    scan an audit blocks must read as that sentence at each, not as "a scan
    is already in progress"."""
    from fastapi import HTTPException

    from openavc.api import discovery as discovery_api
    from openavc.cloud.tools.system_tools import SystemToolsMixin

    discovery = DiscoveryEngine()
    manager = AuditManager(None, discovery)
    session = await manager.start(TARGET)
    monkeypatch.setattr(discovery_api, "_engine", discovery)
    try:
        with pytest.raises(HTTPException) as exc:
            await discovery_api.start_scan(discovery_api.ScanRequest(subnets=["10.0.0.0/30"]))
        assert exc.value.status_code == 409
        assert exc.value.detail == SCAN_BLOCKED

        result = await SystemToolsMixin._start_discovery_scan(object(), {})
        assert result == {"error": SCAN_BLOCKED}
    finally:
        await manager.finish(session.id)
