"""A device away on purpose and the cloud's offline alerts.

A device put to sleep by one of its commands, inside a restart one asked
for, or paused by Live Test or a Device Audit is away on purpose: no offline
alert fires for it or its children while it is, through an edit, a driver
reload or its removal, and its return is judged once the dial that ends it
has its answer and its children have followed it back.

A pause is quiet from the first ``connected`` false its disconnect writes.
Live Test renews its pause for as long as its panel is open, so the quiet
lasts an hour (``PAUSE_ON_PURPOSE_SECONDS``); past it the rules apply again
and each alert says who holds the pause and since when.

Driven through a real DeviceManager and a real AlertMonitor, with rules that
fire at once, so any write judged as a drop shows up as an alert. The device
is invented (``acme``): a mixer with two inputs, its connect and disconnect
writing ``connected`` the way BaseDriver's do.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from openavc.cloud.alert_monitor import AlertMonitor
from openavc.cloud.alert_quiet import COMING_BACK_SECONDS
from openavc.core.connection_fault import UNREACHABLE, ConnectionFaultError
from openavc.core.device_manager import DeviceManager
from openavc.core.device_pause import PAUSED_FOR_AUDIT
from openavc.core.device_presence import PAUSE_ON_PURPOSE_SECONDS
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
    mon.read_pauses_from(dm.pause_held)
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


def _back_date(dm: DeviceManager, seconds: float) -> None:
    dm._pause_since["mixer"] -= seconds


def _since(dm: DeviceManager) -> str:
    return time.strftime("%H:%M", time.localtime(dm.pause_held("mixer").since))


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


# --- the pause and its end ------------------------------------------------------


async def test_pausing_raises_no_alert_for_the_device_or_its_children(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    assert dm.state.get("device.mixer.input.1.online") is False
    assert dm.state.get("device.mixer.presence") == "paused"
    assert await _alerts(mon) == []


async def test_a_resume_that_reconnects_raises_none(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    await dm.resume_device("mixer")
    assert dm.state.get("device.mixer.presence") == "online"
    assert not dm.is_paused("mixer")
    assert dm.pause_held("mixer") is None
    assert await _alerts(mon) == []


async def test_a_resume_that_fails_alerts_the_device_and_its_children_once(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    _Mixer.fail_next = ConnectionFaultError("", code=UNREACHABLE)
    await dm.resume_device("mixer")
    assert dm.state.get("device.mixer.presence") == "offline"
    assert await _alerts(mon) == EVERY_KEY


async def test_a_reconnect_pressed_while_paused_raises_none(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    await dm.reconnect_device("mixer")
    assert dm.state.get("device.mixer.presence") == "online"
    assert not dm.is_paused("mixer")
    assert await _alerts(mon) == []


async def test_a_pause_taken_again_while_its_resume_dials_stays(dm):
    # Live Test's keepalive landing as its panel closes: the newer pause wins,
    # as it does when it lands first.
    await dm.pause_device("mixer")
    driver = dm._devices["mixer"]

    async def repaused_mid_dial() -> None:
        await dm.pause_device("mixer")

    driver.connect = repaused_mid_dial
    await dm.resume_device("mixer")
    assert dm.is_paused("mixer")
    assert dm.pause_held("mixer") is not None
    assert "mixer" in dm._pause_deadlines


async def test_editing_a_paused_device_raises_none_and_keeps_when_it_was_paused(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    since = dm.pause_held("mixer").since
    await dm.update_device("mixer", {**CONFIG, "name": "Mixer 2"})
    assert dm.is_paused("mixer")
    assert dm.state.get("device.mixer.presence") == "paused"
    assert dm.pause_held("mixer").since == since
    assert await _alerts(mon) == []


async def test_removing_a_paused_device_raises_none(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    await dm.remove_device("mixer")
    assert dm.pause_held("mixer") is None
    assert await _alerts(mon) == []


async def test_a_device_still_connecting_long_after_its_pause_ended_is_judged(dm):
    # Nothing dials it (the resume that lifted the pause was cancelled, say):
    # it is not coming back by itself.
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    dm._end_pause("mixer")
    assert dm.state.get("device.mixer.presence") == "connecting"
    await mon._run_periodic_checks(time.time())
    assert await _alerts(mon) == []
    mon._quiet._coming_back["mixer"] -= COMING_BACK_SECONDS
    await mon._run_periodic_checks(time.time())
    assert await _alerts(mon) == EVERY_KEY


async def test_renewing_a_pause_keeps_when_it_began(dm):
    await dm.pause_device("mixer")
    since = dm.pause_held("mixer").since
    await dm.pause_device("mixer")
    assert dm.pause_held("mixer").since == since


# --- the ceiling ------------------------------------------------------------------


async def test_within_the_hour_a_tick_raises_nothing(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    _back_date(dm, PAUSE_ON_PURPOSE_SECONDS - 60)
    mon._last_state_times["mixer"] = time.time() - 600
    await mon._run_periodic_checks(time.time())
    assert await _alerts(mon) == []


async def test_past_the_hour_the_rules_apply_again_and_name_the_pause(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer")
    _back_date(dm, PAUSE_ON_PURPOSE_SECONDS + 1)
    await mon._run_periodic_checks(time.time())
    assert await _alerts(mon) == EVERY_KEY
    words = f"paused for Live Test since {_since(dm)}"
    for _, payload in mon._queued:
        assert payload["message"].endswith(f", {words}")
        assert payload["detail"]["pause"] == words
    # An alert already raised is not raised again on the next tick.
    await mon._run_periodic_checks(time.time())
    assert len(mon._queued) == 3


async def test_past_the_hour_an_absence_rule_names_the_pause(dm):
    mon = _monitor(dm)
    await dm.pause_device("mixer", purpose=PAUSED_FOR_AUDIT)
    mon._last_state_times["mixer"] = time.time() - 600
    _back_date(dm, PAUSE_ON_PURPOSE_SECONDS + 1)
    await mon._run_periodic_checks(time.time())
    absence = [p for t, p in mon._agent.sent if p["rule_id"] == "silent"]
    assert [p["message"] for p in absence] == [
        f"Not reporting: mixer not reporting for 600s, paused for a Device Audit since {_since(dm)}"
    ]


async def test_paused_inside_a_restart_alerts_past_the_hour(dm):
    # The restart window is minutes at most; a pause keeps the reconnect that
    # would clear its flag from running, so past the hour it is the pause.
    mon = _monitor(dm)
    await dm.send_command("mixer", "reboot", {})
    await dm.pause_device("mixer")
    assert dm.state.get("device.mixer.presence") == "restarting"
    assert await _alerts(mon) == []
    _back_date(dm, PAUSE_ON_PURPOSE_SECONDS + 1)
    await mon._run_periodic_checks(time.time())
    assert await _alerts(mon) == EVERY_KEY


async def test_paused_while_asleep_stays_quiet_past_the_hour(dm):
    mon = _monitor(dm)
    await dm.send_command("mixer", "power_off", {})
    driver = dm._devices["mixer"]
    await driver.disconnect()
    dm._set_offline_reason("mixer", driver, exc=ConnectionFaultError("", code=UNREACHABLE))
    await dm.pause_device("mixer")
    _back_date(dm, PAUSE_ON_PURPOSE_SECONDS + 1)
    await mon._run_periodic_checks(time.time())
    assert dm.state.get("device.mixer.presence") == "asleep"
    assert await _alerts(mon) == []
