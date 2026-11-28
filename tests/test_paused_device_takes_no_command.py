"""A paused device takes no command, one that needs no connection included.

A pause holds a device for somebody else's session: the Driver Builder's
Live Test, or a Device Audit measuring the equipment. Every command the room
sends is refused meanwhile, and a command declaring ``available_offline``
(a Wake-on-LAN Power On, which needs no connection) used to be the one that
reached it anyway: a schedule or a panel press switched the equipment on in
the middle of the audit.

The pause is asked of the pause itself, not only of the presence word: a
device put to sleep and then paused reads ``asleep`` (the word's order keeps
a sleep quiet under a pause), and its Power On is exactly the command that
must not go out.

An invented device (Acme) throughout.
"""

from __future__ import annotations

import pytest

from openavc.api.error_messages import describe_command_failure, friendly_error
from openavc.core import device_sleep
from openavc.core.connection_fault import UNREACHABLE, ConnectionFaultError
from openavc.core.device_manager import (
    PAUSED_FOR_AUDIT,
    PAUSED_FOR_LIVE_TEST,
    DeviceManager,
)
from openavc.core.device_presence import ASLEEP, PAUSED
from openavc.core.device_sleep import SleepBook
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


class _AcmeDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {},
        "commands": {
            "power_off": {"label": "Power Off", "sleeps_device": "power_on"},
            "power_on": {"label": "Power On", "available_offline": True},
            "beep": {"label": "Beep"},
        },
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.calls: list[str] = []

    async def connect(self):
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self):
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command, params=None):
        self.calls.append(command)
        return True


@pytest.fixture
def book_path(tmp_path, monkeypatch):
    path = tmp_path / device_sleep.RECORD_NAME
    monkeypatch.setattr(device_sleep, "_default_path", lambda: path)
    return path


@pytest.fixture
async def acme(book_path):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    dm._sleep = SleepBook(device_sleep._default_path)
    driver = _AcmeDriver("acme1", {}, state, events)
    dm._devices["acme1"] = driver
    await driver.connect()
    yield dm, driver, state
    dm._cancel_all_pause_expiries()


def _word(state: StateStore) -> str:
    return state.get("device.acme1.presence")


async def _put_to_sleep(dm: DeviceManager, driver: _AcmeDriver) -> None:
    """Send the sleeping command, then drop as a display in standby does."""
    await dm.send_command("acme1", "power_off")
    await driver.disconnect()
    dm._set_device_state("acme1", "connected", False)
    dm._set_offline_reason("acme1", driver, exc=ConnectionFaultError("", code=UNREACHABLE))
    driver.calls.clear()


# ── the gate ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("holder", [PAUSED_FOR_AUDIT, PAUSED_FOR_LIVE_TEST])
async def test_a_wake_command_does_not_go_out_to_a_paused_device(acme, holder):
    dm, driver, state = acme
    await dm.pause_device("acme1", purpose=holder)
    assert _word(state) == PAUSED
    assert dm.command_goes_out("acme1", "power_on") is False
    with pytest.raises(ConnectionError):
        await dm.send_command("acme1", "power_on")
    assert driver.calls == []


async def test_a_sleeping_device_paused_for_an_audit_is_not_woken(acme):
    """The word stays ``asleep`` under the pause; the gate asks the pause."""
    dm, driver, state = acme
    await _put_to_sleep(dm, driver)
    assert _word(state) == ASLEEP
    assert dm.command_goes_out("acme1", "power_on") is True  # asleep: the wake goes out
    await dm.pause_device("acme1", purpose=PAUSED_FOR_AUDIT)
    assert _word(state) == ASLEEP
    assert dm.command_goes_out("acme1", "power_on") is False
    with pytest.raises(ConnectionError):
        await dm.send_command("acme1", "power_on")
    assert driver.calls == []


async def test_once_resumed_the_device_takes_commands_again(acme):
    dm, driver, state = acme
    await dm.pause_device("acme1", purpose=PAUSED_FOR_AUDIT)
    await dm.resume_device("acme1")
    await dm.send_command("acme1", "power_on")
    await dm.send_command("acme1", "beep")
    assert driver.calls == ["power_on", "beep"]


async def test_a_refused_press_does_not_redial_a_paused_device(acme):
    dm, _driver, _state = acme
    await dm.pause_device("acme1", purpose=PAUSED_FOR_AUDIT)
    with pytest.raises(ConnectionError):
        await dm.send_command("acme1", "power_on")
    assert "acme1" not in dm._reconnect_tasks


# ── a macro's skips ask the same question ────────────────────────────────────


async def test_skip_if_offline_skips_a_wake_to_a_paused_device(acme):
    dm, driver, state = acme
    await dm.pause_device("acme1", purpose=PAUSED_FOR_AUDIT)
    engine = MacroEngine(state, dm.events, dm)
    await engine.execute_steps([{
        "action": "device.command", "device": "acme1", "command": "power_on",
        "skip_if_offline": True,
    }])
    assert driver.calls == []


async def test_a_group_command_skips_a_paused_member(acme):
    dm, driver, state = acme
    await dm.pause_device("acme1", purpose=PAUSED_FOR_AUDIT)
    engine = MacroEngine(state, dm.events, dm)
    engine.load_groups([{"id": "wall", "name": "Wall", "device_ids": ["acme1"]}])
    await engine.execute_steps([{"action": "group.command", "group": "wall", "command": "power_on"}])
    assert driver.calls == []


# ── what the refusal says ────────────────────────────────────────────────────


async def _refusal(dm: DeviceManager) -> ConnectionError:
    with pytest.raises(ConnectionError) as caught:
        await dm.send_command("acme1", "power_on")
    return caught.value


@pytest.mark.parametrize("holder,words", [
    (PAUSED_FOR_AUDIT, "Lobby Display is paused for a Device Audit."),
    (PAUSED_FOR_LIVE_TEST, "Lobby Display is paused for Live Test."),
])
async def test_the_refusal_names_the_pause(acme, holder, words):
    dm, _driver, state = acme
    await dm.pause_device("acme1", purpose=holder)
    exc = await _refusal(dm)
    for at_panel in (False, True):
        assert friendly_error(exc, device="Lobby Display", at_panel=at_panel).startswith(words)
    state.set("device.acme1.name", "Lobby Display")
    failure = describe_command_failure(exc, "acme1", "power_on", state=state)
    assert failure.programmer == str(exc)
    assert failure.panel.startswith(words)


async def test_a_sleeping_device_paused_is_refused_as_paused_not_asleep(acme):
    """"Power On wakes it" would be the answer to a refused Power On."""
    dm, driver, _state = acme
    await _put_to_sleep(dm, driver)
    await dm.pause_device("acme1", purpose=PAUSED_FOR_AUDIT)
    exc = await _refusal(dm)
    sentence = friendly_error(exc, device="Lobby Display")
    assert sentence.startswith("Lobby Display is paused for a Device Audit.")
    assert "wakes it" not in sentence
