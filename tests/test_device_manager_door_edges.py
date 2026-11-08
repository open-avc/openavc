"""The device manager's command, setting and Reconnect doors, each in the case
nothing else pins: which commands work offline, what a live setting write
answers, the read-back wait, the settings listing, and Reconnect and a setup
action standing the retry loop down.

An invented ``acme_door_widget`` driver that opens no socket stands in for
every device. It records each connect and disconnect, and a test can make its
disconnect fail.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.core.connection_fault import UNREACHABLE
from openavc.core.device_manager import DeviceManager, DeviceNotFoundError
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY

DRIVER = "acme_door_widget"

BRIGHTNESS = {
    "type": "integer", "label": "Brightness", "state_key": "level", "min": 0, "max": 100,
}
INPUT_NAME = {"type": "string", "label": "Input Name"}


class _Widget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Door Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {},
        "commands": {"power_on": {"label": "Power On", "available_offline": True}, "mute": {}},
        "state_variables": {"level": {"type": "integer"}, "input_name": {"type": "string"}},
        "device_settings": {"brightness": BRIGHTNESS, "input_name": INPUT_NAME},
        "config_schema": {},
    }
    fail_disconnect = False
    setting_answer: Any = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[str] = []

    async def connect(self) -> None:
        self.calls.append("connect")
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        self._connected = False
        if type(self).fail_disconnect:
            raise RuntimeError("socket already gone")

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def set_device_setting(self, key: str, value: Any) -> Any:
        return type(self).setting_answer

    async def stop_polling(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _driver():
    _DRIVER_REGISTRY[DRIVER] = _Widget
    _Widget.fail_disconnect = False
    _Widget.setting_answer = None
    yield
    _DRIVER_REGISTRY.pop(DRIVER, None)
    _Widget.fail_disconnect = False
    _Widget.setting_answer = None


@pytest.fixture
async def dm():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    manager = DeviceManager(state, events)
    # No host: nothing here probes for a web page or dials a real address.
    await manager.add_device({"id": "dev", "driver": DRIVER, "name": "Dev", "config": {}})
    return manager


def _widget(dm: DeviceManager) -> _Widget:
    driver = dm.get_driver("dev")
    assert isinstance(driver, _Widget)
    return driver


async def _running_retry(dm: DeviceManager) -> asyncio.Task:
    """A retry loop in flight for the device, as a drop would leave one."""
    task = asyncio.create_task(asyncio.sleep(3600))
    dm._reconnect_tasks["dev"] = task
    return task


# --- which commands work offline ----------------------------------------------


async def test_whether_a_command_works_offline_is_read_off_its_declaration(dm):
    assert dm.command_works_offline("dev", "power_on") is True
    assert dm.command_works_offline("dev", "mute") is False
    assert dm.command_works_offline("nobody", "power_on") is False
    # A panel binding is project JSON: a hand-edited one can hold anything.
    assert dm.command_works_offline("dev", None) is False
    assert dm.command_works_offline("dev", ["power_on"]) is False


# --- the setting doors ------------------------------------------------------------


async def test_a_live_setting_write_answers_with_what_the_driver_returned(dm):
    _Widget.setting_answer = {"applied": 75}

    assert await dm.set_device_setting("dev", "brightness", 75) == {"applied": 75}


async def test_waiting_for_a_read_back_from_no_such_device_is_refused(dm):
    with pytest.raises(DeviceNotFoundError):
        await dm.await_setting_readback("nobody", "brightness", 75)


async def test_a_read_back_timeout_shortens_the_window(dm):
    dm.state.set("device.dev.level", 40)

    # The unpolled window is five seconds; a zero timeout answers at once.
    answer = await asyncio.wait_for(
        dm.await_setting_readback("dev", "brightness", 75, timeout=0), 2.0,
    )

    assert answer == (False, 40)


async def test_the_settings_listing_carries_each_declaration_and_its_current_value(dm):
    dm.state.set("device.dev.level", 40)
    dm.state.set("device.dev.input_name", "HDMI 2")

    assert dm.get_device_settings("dev") == {
        # Read from the state key the setting names...
        "brightness": {**BRIGHTNESS, "current_value": 40},
        # ...or, naming none, from the key the setting is called.
        "input_name": {**INPUT_NAME, "current_value": "HDMI 2"},
    }


# --- Reconnect ----------------------------------------------------------------------


async def test_reconnect_stops_the_retry_loop_that_was_running(dm):
    task = await _running_retry(dm)
    try:
        await dm.reconnect_device("dev")

        assert task.cancelled()
        assert "dev" not in dm._reconnect_tasks
    finally:
        task.cancel()


async def test_reconnect_goes_on_when_closing_the_driver_fails(dm):
    _Widget.fail_disconnect = True
    driver = _widget(dm)

    await dm.reconnect_device("dev")

    assert driver.calls[-2:] == ["disconnect", "connect"]
    assert dm.state.get("device.dev.connected") is True
    assert "dev" not in dm._intentional_disconnect


# --- a setup action -------------------------------------------------------------


async def test_a_setup_action_stops_the_retry_loop_that_was_running(dm):
    task = await _running_retry(dm)
    try:
        await dm.begin_setup("dev")

        assert task.cancelled()
        assert "dev" not in dm._reconnect_tasks
        assert "dev" in dm._intentional_disconnect
    finally:
        task.cancel()
        await dm.end_setup("dev")


async def test_an_in_place_reconnect_clears_the_reason_then_closes_and_dials(dm):
    driver = _widget(dm)
    driver.calls.clear()
    dm.state.set("device.dev.offline_reason", UNREACHABLE)

    await dm.reconnect_in_place("dev")

    assert driver.calls == ["disconnect", "connect"]
    assert dm.state.get("device.dev.offline_reason") is None


async def test_an_in_place_reconnect_goes_on_when_closing_the_driver_fails(dm):
    _Widget.fail_disconnect = True
    driver = _widget(dm)
    driver.calls.clear()

    await dm.reconnect_in_place("dev")

    assert driver.calls == ["disconnect", "connect"]
    assert dm.state.get("device.dev.connected") is True
