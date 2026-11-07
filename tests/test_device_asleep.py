"""A device put to sleep on purpose reads as asleep, not as a fault.

A command may declare ``sleeps_device: <wake command>``. After it runs and the
device drops, the device is asleep: ``device.<id>.asleep`` true, no
``offline_reason`` (so nothing counts it as an error or alerts on it), and
"Asleep. <wake> wakes it." on the card. The wake command still goes out, opens
its own ``restarts_device_for`` window ("Waking up"), and a device that does
not come back in it reads as the real fault.

An invented device (``acme_widget``) throughout, per the core-tests rule.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from openavc.api.error_messages import friendly_error
from openavc.cloud.alert_monitor import AlertMonitor
from openavc.core import device_sleep
from openavc.core.connection_fault import AUTH_FAILED, UNREACHABLE, ConnectionFaultError
from openavc.core.device_manager import DeviceManager, device_asleep
from openavc.core.device_sleep import SleepBook, asleep_message
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import command_sleep_errors, validate_driver_definition
from openavc.ui.action_devices import annotate_action_devices

WAKE_SECONDS = 40


class _AcmeDriver:
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "transport": "tcp",
        "commands": {
            "power_off": {"label": "Power Off", "sleeps_device": "power_on"},
            "power_on": {
                "label": "Power On", "available_offline": True,
                "restarts_device_for": WAKE_SECONDS,
            },
            "set_level": {},
        },
    }

    def __init__(self) -> None:
        self.config = {"host": "10.0.0.9", "port": 4001, "transport": "tcp"}
        self._state: dict[str, Any] = {"connected": True}
        self.sent: list[str] = []
        self.transport = None
        self.last_transport_error = ""
        self.last_fault = None

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    async def send_command(self, command: str, params: dict) -> str:
        self.sent.append(command)
        return "ok"


@pytest.fixture
def book_path(tmp_path, monkeypatch):
    path = tmp_path / device_sleep.RECORD_NAME
    monkeypatch.setattr(device_sleep, "_default_path", lambda: path)
    return path


def _manager() -> tuple[DeviceManager, _AcmeDriver, StateStore]:
    state = StateStore()
    dm = DeviceManager(state=state, events=EventBus())
    dm._sleep = SleepBook(device_sleep._default_path)
    driver = _AcmeDriver()
    dm._devices["widget"] = driver
    dm._device_configs["widget"] = dict(driver.config)
    state.set("device.widget.connected", True)
    return dm, driver, state


def _drop(dm: DeviceManager, driver: _AcmeDriver, state: StateStore, code: str = UNREACHABLE) -> str:
    driver._state["connected"] = False
    state.set("device.widget.connected", False)
    return dm._set_offline_reason("widget", driver, exc=ConnectionFaultError("", code=code))


# --- the lifecycle ---------------------------------------------------------


def test_the_sleeping_command_arms_without_publishing(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    assert dm.sleep_of("widget") is not None
    assert state.get("device.widget.asleep") is None
    assert state.get("device.widget.offline_detail") is None


def test_the_drop_after_it_is_asleep_not_a_fault(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    assert state.get("device.widget.asleep") is True
    assert state.get("device.widget.wake_command") == "power_on"
    assert state.get("device.widget.offline_reason") is None
    assert state.get("device.widget.offline_detail") == "Asleep. Power On wakes it."
    assert asleep_message("Power On") == "Asleep. Power On wakes it."


def test_every_failed_reconnect_keeps_it_asleep(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    for _ in range(3):
        _drop(dm, driver, state)
    assert state.get("device.widget.asleep") is True
    assert state.get("device.widget.offline_reason") is None


def test_a_drop_without_the_sleeping_command_is_a_fault(book_path):
    dm, driver, state = _manager()
    _drop(dm, driver, state)
    assert state.get("device.widget.asleep") is None
    assert state.get("device.widget.offline_reason") == UNREACHABLE


def test_a_permanent_fault_ends_the_sleep(book_path):
    # The device answered and refused us: it is awake.
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    _drop(dm, driver, state, code=AUTH_FAILED)
    assert state.get("device.widget.asleep") is None
    assert state.get("device.widget.offline_reason") == AUTH_FAILED
    assert dm.sleep_of("widget") is None


def test_a_device_that_stayed_connected_did_not_go_to_sleep(book_path, monkeypatch):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    later = time.time() + device_sleep.DROP_WINDOW_SECONDS + 60
    monkeypatch.setattr(device_sleep.time, "time", lambda: later)
    _drop(dm, driver, state)
    assert state.get("device.widget.asleep") is None
    assert state.get("device.widget.offline_reason") == UNREACHABLE


def test_the_wake_command_goes_out_and_opens_the_waking_window(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    asyncio.run(dm.send_command("widget", "power_on", {}))
    assert driver.sent == ["power_off", "power_on"]
    assert state.get("device.widget.asleep") is None
    assert state.get("device.widget.wake_command") is None
    assert state.get("device.widget.restarting") is True
    assert state.get("device.widget.offline_reason") is None
    assert state.get("device.widget.offline_detail").startswith("Waking up. It should be back in about")
    # A failed reconnect inside the window still says waking.
    _drop(dm, driver, state)
    assert state.get("device.widget.offline_detail").startswith("Waking up.")


_STATUS_KEYS = {"restarting", "asleep", "wake_command", "offline_reason", "offline_detail"}


def _status_changes_since(state: StateStore, since: int) -> list[str]:
    changes = state.get_history(count=1000, prefix="device.widget.")[since:]
    return [c["key"].rsplit(".", 1)[1] for c in changes if c["key"].rsplit(".", 1)[1] in _STATUS_KEYS]


def test_the_sleep_writes_change_the_sleep_keys_before_restarting(book_path):
    # A state_change trigger on each of these keys fires in the order the
    # batch lists them, so the order is part of what a project sees.
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    state.set("device.widget.restarting", True)  # from a window that ran out
    before = len(state.get_history(count=1000, prefix="device.widget."))
    _drop(dm, driver, state)
    assert _status_changes_since(state, before) == [
        "asleep", "wake_command", "restarting", "offline_reason", "offline_detail",
    ]

    before = len(state.get_history(count=1000, prefix="device.widget."))
    asyncio.run(dm.send_command("widget", "power_on", {}))
    assert _status_changes_since(state, before) == [
        "asleep", "wake_command", "restarting", "offline_detail",
    ]


def test_a_wake_that_does_not_come_back_reads_as_the_real_fault(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    asyncio.run(dm.send_command("widget", "power_on", {}))
    dm._restart_windows["widget"] = time.monotonic() - 1  # the window ran out
    _drop(dm, driver, state)
    assert state.get("device.widget.restarting") is None
    assert state.get("device.widget.asleep") is None
    assert state.get("device.widget.offline_reason") == UNREACHABLE


def test_a_reconnect_ends_the_sleep(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    dm._clear_offline_reason("widget")
    assert state.get("device.widget.asleep") is None
    assert state.get("device.widget.offline_detail") is None
    assert dm.sleep_of("widget") is None


def test_another_command_to_a_sleeping_device_is_refused_in_words(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    with pytest.raises(ConnectionError) as refused:
        asyncio.run(dm.send_command("widget", "set_level", {}))
    assert friendly_error(refused.value, device="Lobby Display") == (
        "Lobby Display is asleep. Power On wakes it."
    )
    assert friendly_error(device_asleep("widget", "Power On"), device="") == (
        "The equipment is asleep. Power On wakes it."
    )


def test_a_sleep_survives_a_restart_of_openavc(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    assert json.loads(book_path.read_text())["widget"]["asleep"] is True
    dm2, driver2, state2 = _manager()  # a fresh process reading the same file
    _drop(dm2, driver2, state2)
    assert state2.get("device.widget.asleep") is True


def test_a_sleep_recorded_under_another_driver_is_forgotten(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    dm2, driver2, state2 = _manager()
    driver2.DRIVER_INFO = {**_AcmeDriver.DRIVER_INFO, "id": "acme_other"}
    _drop(dm2, driver2, state2)
    assert state2.get("device.widget.asleep") is None
    assert state2.get("device.widget.offline_reason") == UNREACHABLE


def test_removing_the_device_forgets_its_sleep(book_path):
    dm, driver, state = _manager()
    asyncio.run(dm.send_command("widget", "power_off", {}))
    asyncio.run(dm.remove_device("widget"))
    assert not book_path.exists()


def test_away_on_purpose_covers_the_arm_and_the_sleep(book_path):
    dm, driver, state = _manager()
    assert not dm.away_on_purpose("widget")
    asyncio.run(dm.send_command("widget", "power_off", {}))
    assert dm.away_on_purpose("widget")  # before the drop is even published
    _drop(dm, driver, state)
    assert dm.away_on_purpose("widget")


# --- the alert monitor ------------------------------------------------------


class _Agent:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_message(self, msg_type: str, payload: dict) -> None:
        self.sent.append((msg_type, payload))


def _monitor(state: StateStore, away=None) -> AlertMonitor:
    mon = AlertMonitor(_Agent(), state, EventBus(), away_on_purpose=away)
    mon._rules = [{
        "id": "offline", "name": "Offline", "enabled": True, "rule_type": "threshold",
        "condition": {"key": "device.*.connected", "operator": "==", "value": False},
    }]
    mon._queued: list = []
    mon._queue_send = lambda t, p: mon._queued.append((t, p))
    return mon


def test_an_asleep_device_raises_no_offline_alert_and_a_failed_wake_does(book_path):
    dm, driver, state = _manager()
    mon = _monitor(state, away=dm.away_on_purpose)
    state.subscribe("*", mon._on_state_change)
    asyncio.run(dm.send_command("widget", "power_off", {}))
    _drop(dm, driver, state)
    assert mon._queued == []
    # Woken, and it never came back: the real fault, so the rule fires now.
    asyncio.run(dm.send_command("widget", "power_on", {}))
    dm._restart_windows["widget"] = time.monotonic() - 1
    _drop(dm, driver, state)
    assert [t for t, _ in mon._queued] == ["alert"]


def test_an_ordinary_drop_still_alerts(book_path):
    dm, driver, state = _manager()
    mon = _monitor(state, away=dm.away_on_purpose)
    state.subscribe("*", mon._on_state_change)
    _drop(dm, driver, state)
    assert [t for t, _ in mon._queued] == ["alert"]


# --- the contract -----------------------------------------------------------


def _driver_def(commands: dict) -> dict:
    return {
        "id": "acme_widget", "name": "Acme Widget", "manufacturer": "Acme",
        "category": "utility", "transport": "tcp", "commands": commands,
    }


def _sleep_errors(commands: dict) -> list[str]:
    return [e for e in validate_driver_definition(_driver_def(commands)) if "sleeps_device" in e]


def test_a_well_formed_sleeping_command_validates():
    assert _sleep_errors({
        "power_off": {"send": "OFF\r", "sleeps_device": "power_on"},
        "power_on": {"send": "ON\r", "available_offline": True, "restarts_device_for": 30},
    }) == []


@pytest.mark.parametrize(("commands", "words"), [
    ({"power_off": {"send": "OFF\r", "sleeps_device": "power_onn"},
      "power_on": {"send": "ON\r", "available_offline": True, "restarts_device_for": 30}},
     "is not a declared command"),
    ({"power_off": {"send": "OFF\r", "sleeps_device": "power_off"}},
     "names this command itself"),
    ({"power_off": {"send": "OFF\r", "sleeps_device": "power_on"},
      "power_on": {"send": "ON\r", "restarts_device_for": 30}},
     "available_offline: true"),
    ({"power_off": {"send": "OFF\r", "sleeps_device": "power_on"},
      "power_on": {"send": "ON\r", "available_offline": True}},
     "declares no restarts_device_for"),
])
def test_a_sleeping_command_must_name_a_wake_that_can_wake(commands, words):
    errors = _sleep_errors(commands)
    assert len(errors) == 1 and words in errors[0], errors


def test_the_rule_is_shared_with_python_drivers():
    errors = command_sleep_errors(
        "power_off", {"sleeps_device": "power_on"}, {"power_off": {}, "power_on": {}},
    )
    assert len(errors) == 2


# --- the panel's press marks --------------------------------------------------


def test_a_wake_press_is_not_a_press_that_will_be_refused():
    ui = {"pages": [{"elements": [
        {"id": "on", "bindings": {"do": {"press": [
            {"action": "device.command", "device": "widget", "command": "power_on"}]}}},
        {"id": "vol", "bindings": {"do": {"press": [
            {"action": "device.command", "device": "widget", "command": "set_level"}]}}},
    ]}]}
    works = lambda device, command: command == "power_on"  # noqa: E731
    annotate_action_devices(ui, [], [], works_offline=works)
    on, vol = ui["pages"][0]["elements"]
    assert "action_devices" not in on and on["wake_devices"] == ["widget"]
    assert vol["action_devices"] == ["widget"] and "wake_devices" not in vol
