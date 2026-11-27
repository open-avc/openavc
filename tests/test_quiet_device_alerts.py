"""A device away on purpose and the cloud's offline alerts.

A device put to sleep by one of its commands, or inside a restart one asked
for, is away on purpose: no offline alert fires for it or its children while
it is, through an edit, a driver reload or its removal, and its return is
judged once its children have followed it back.

Driven through a real DeviceManager and a real AlertMonitor, with rules that
fire at once, so any write judged as a drop shows up as an alert. The device
is invented (``acme``): a mixer with two inputs, its connect and disconnect
writing ``connected`` the way BaseDriver's do.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.cloud.alert_monitor import AlertMonitor
from openavc.core.connection_fault import UNREACHABLE, ConnectionFaultError
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver

MIXER = "acme_mixer_pause_alerts"

DEV_OFFLINE = {
    "id": "dev_offline", "name": "Offline", "enabled": True, "rule_type": "threshold",
    "condition": {"key": "device.*.connected", "operator": "==", "value": False},
}
CHAN_OFFLINE = {
    "id": "chan_offline", "name": "Input offline", "enabled": True, "rule_type": "pattern",
    "condition": {"key": "device.*.online", "value": False},
}
SILENT = {
    "id": "silent", "name": "Not reporting", "enabled": True, "rule_type": "absence",
    "condition": {"threshold_seconds": 120},
}


class _Mixer(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": MIXER,
        "name": "Acme Mixer",
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "tcp",
        "state_variables": {},
        "commands": {
            "reboot": {"label": "Reboot", "restarts_device_for": 60},
            "power_off": {"label": "Power Off", "sleeps_device": "power_on"},
            "power_on": {
                "label": "Power On", "available_offline": True, "restarts_device_for": 40,
            },
        },
        "child_entity_types": {
            "input": {
                "label": "Input",
                "id_format": {"type": "integer", "min": 1, "max": 8},
                "state_variables": {},
            },
        },
    }
    fail_next: BaseException | None = None

    async def connect(self) -> None:
        # A dial waits on the network: whatever is due meanwhile runs.
        await asyncio.sleep(0)
        failure, type(self).fail_next = type(self).fail_next, None
        if failure is not None:
            raise failure
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return "ok"


class _Agent:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_message(self, msg_type: str, payload: dict) -> None:
        self.sent.append((msg_type, payload))


CONFIG = {"id": "mixer", "driver": MIXER, "name": "Mixer",
          "config": {"host": "192.0.2.50", "port": 4300}}


@pytest.fixture
async def dm(tmp_path, monkeypatch):
    from openavc.core import device_sleep

    monkeypatch.setattr(device_sleep, "_default_path", lambda: tmp_path / device_sleep.RECORD_NAME)
    register_driver(_Mixer)
    _Mixer.fail_next = None
    manager = DeviceManager(state=StateStore(), events=EventBus())
    manager._sleep = device_sleep.SleepBook(device_sleep._default_path)
    await manager.add_device(dict(CONFIG), defer_connect=True)
    driver = manager._devices["mixer"]
    await driver.connect()
    manager._deferred_connects.discard("mixer")
    driver.register_child("input", 1)
    driver.register_child("input", 2)
    try:
        yield manager
    finally:
        for device_id in list(manager._pause_expiry_tasks):
            manager._cancel_pause_expiry(device_id)
        await manager._cancel_reconnect("mixer")
        unregister_driver(MIXER)
        _Mixer.fail_next = None


def _monitor(dm: DeviceManager) -> AlertMonitor:
    mon = AlertMonitor(_Agent(), dm.state, EventBus())
    mon._rules = [DEV_OFFLINE, CHAN_OFFLINE, SILENT]
    mon._queued: list[tuple[str, dict]] = []
    mon._queue_send = lambda t, p: mon._queued.append((t, p))
    dm.state.subscribe("*", mon._on_state_change)
    return mon


async def _alerts(mon: AlertMonitor) -> list[tuple[str, str]]:
    """Every alert raised so far, once what the last write set off has run."""
    await asyncio.sleep(0)
    return sorted(
        (p["rule_id"], p["detail"].get("key", p.get("device_id")))
        for t, p in mon._queued + mon._agent.sent if t == "alert"
    )


EVERY_KEY = [
    ("chan_offline", "device.mixer.input.1.online"),
    ("chan_offline", "device.mixer.input.2.online"),
    ("dev_offline", "device.mixer.connected"),
]


# --- asleep ----------------------------------------------------------------------


async def _put_to_sleep(dm: DeviceManager) -> None:
    await dm.send_command("mixer", "power_off", {})
    driver = dm._devices["mixer"]
    await driver.disconnect()
    dm._set_offline_reason("mixer", driver, exc=ConnectionFaultError("", code=UNREACHABLE))
    assert dm.state.get("device.mixer.presence") == "asleep"


async def test_editing_a_sleeping_device_raises_none(dm):
    # Its driver writes connected false as it is built again; the word that
    # goes with that write is already asleep.
    await _put_to_sleep(dm)
    mon = _monitor(dm)
    _Mixer.fail_next = ConnectionFaultError("", code=UNREACHABLE)
    await dm.update_device("mixer", {**CONFIG, "name": "Mixer 2"})
    assert dm.state.get("device.mixer.presence") == "asleep"
    assert await _alerts(mon) == []


async def test_removing_a_sleeping_device_raises_none(dm):
    await _put_to_sleep(dm)
    mon = _monitor(dm)
    await dm.remove_device("mixer")
    assert await _alerts(mon) == []


def test_deleting_the_word_is_not_the_quiet_ending():
    # The device is being removed, or taken down to be built again: there is
    # nothing left to judge, or a new instance about to say its own word.
    state = StateStore()
    mon = AlertMonitor(_Agent(), state, EventBus())
    mon._rules = [DEV_OFFLINE]
    mon._queued = []
    mon._queue_send = lambda t, p: mon._queued.append((t, p))
    state.set("device.gone.presence", "asleep")
    state.set("device.gone.connected", False)
    state.subscribe("*", mon._on_state_change)
    state.delete("device.gone.presence")
    assert mon._queued == []


async def test_a_device_back_from_sleep_is_judged_with_its_children_back(dm):
    await _put_to_sleep(dm)
    mon = _monitor(dm)
    await dm.send_command("mixer", "power_on", {})
    assert dm.state.get("device.mixer.presence") == "restarting"
    await dm._devices["mixer"].connect()
    assert dm.state.get("device.mixer.presence") == "online"
    assert await _alerts(mon) == []


async def test_a_wake_that_fails_still_alerts(dm):
    await _put_to_sleep(dm)
    mon = _monitor(dm)
    await dm.send_command("mixer", "power_on", {})
    dm._restart_windows["mixer"] = 0.0
    driver = dm._devices["mixer"]
    dm._set_offline_reason("mixer", driver, exc=ConnectionFaultError("", code=UNREACHABLE))
    assert dm.state.get("device.mixer.presence") == "offline"
    assert await _alerts(mon) == EVERY_KEY
