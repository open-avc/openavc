"""The device manager's pause, its devices waiting for a driver, and what it
says about its devices, each in the cases nothing else pins.

An invented ``acme_pause_widget`` driver that opens no socket stands in for
every device: connecting it publishes connected, it declares one device
setting, and a test can make its next connect fail.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from openavc.core.device_manager import (
    DeviceManager,
    device_restarting,
    failed_command,
    mark_failed_command,
)
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectEndedError
from openavc.drivers.registry import _DRIVER_REGISTRY

DRIVER = "acme_pause_widget"


class _Widget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Pause Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {},
        "commands": {"power_on": {"label": "Power On"}},
        "state_variables": {},
        "config_schema": {},
        "device_settings": {
            "label_text": {"type": "string", "label": "Label", "setup": False},
        },
    }
    fail_next: BaseException | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.settings_written: list[tuple[str, Any]] = []
        self.disconnect_error: BaseException | None = None

    async def connect(self) -> None:
        failure, type(self).fail_next = type(self).fail_next, None
        if failure is not None:
            raise failure
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self) -> None:
        self._connected = False
        if self.disconnect_error is not None:
            raise self.disconnect_error

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def set_device_setting(self, key: str, value: Any) -> Any:
        self.settings_written.append((key, value))
        return True

    async def stop_polling(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _driver():
    _DRIVER_REGISTRY[DRIVER] = _Widget
    _Widget.fail_next = None
    yield
    _DRIVER_REGISTRY.pop(DRIVER, None)
    _Widget.fail_next = None


@pytest.fixture
def dm():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    manager = DeviceManager(state, events)
    yield manager
    for device_id in list(manager._pause_expiry_tasks):
        manager._cancel_pause_expiry(device_id)


def _cfg(device_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": device_id, "driver": DRIVER, "name": device_id.title(),
        "config": {"host": "192.0.2.40", "port": 4200}, **extra,
    }


# --- the pause ---------------------------------------------------------------


async def test_a_backstop_already_due_has_no_time_left_rather_than_less_than_none(dm):
    """A deadline already past is a backstop mid-fire: what a re-add carries
    is zero seconds, never a negative term."""
    await dm.add_device(_cfg("dev"))
    await dm.pause_device("dev")
    dm._pause_deadlines["dev"] = time.monotonic() - 5.0

    assert dm._pause_remaining("dev") == 0.0
    assert dm._pause_to_carry("dev") == (0.0, "live_test")


async def test_a_pause_asked_for_no_time_at_all_gets_none(dm):
    """A zero term is a term: the backstop fires at once, never the default
    ten minutes."""
    await dm.add_device(_cfg("dev"))

    await dm.pause_device("dev", ttl=0.0)

    assert dm._pause_remaining("dev") < 1.0
    await dm._pause_expiry_tasks["dev"]
    assert not dm.is_paused("dev")
    assert dm.state.get("device.dev.connected") is True


async def test_resuming_sends_the_settings_queued_while_paused(dm):
    await dm.add_device(_cfg("dev"))
    await dm.pause_device("dev")
    await dm.store_pending_settings("dev", {"label_text": "Lobby"})

    await dm.resume_device("dev")

    assert dm.get_driver("dev").settings_written == [("label_text", "Lobby")]
    assert "label_text" not in dm.get_device_config("dev").get("pending_settings", {})


async def test_a_pause_holds_even_when_closing_the_connection_raises(dm):
    await dm.add_device(_cfg("dev"))
    dm.get_driver("dev").disconnect_error = OSError("socket already gone")

    await dm.pause_device("dev")

    assert dm.is_paused("dev")
    assert dm.state.get("device.dev.connected") is False
    assert "dev" in dm._intentional_disconnect
    assert "dev" in dm._pause_expiry_tasks


async def test_a_resume_ended_by_a_newer_door_leaves_the_device_to_that_door(dm):
    """A connect ended while it waited (a Reconnect, a pause, a removal) is
    neither a success nor a failure: no reason published, no retry started."""
    await dm.add_device(_cfg("dev"))
    await dm.pause_device("dev")
    _Widget.fail_next = ConnectEndedError("ended")

    await dm.resume_device("dev")

    assert not dm.is_paused("dev")
    assert dm.state.get("device.dev.offline_reason") is None
    assert "dev" not in dm._reconnect_tasks


async def test_a_backstop_whose_resume_fails_is_logged_and_forgotten(dm, caplog):
    """The race the backstop guards: the device went while it fired."""
    dm._schedule_pause_expiry("gone", 0.0)
    task = dm._pause_expiry_tasks["gone"]

    with caplog.at_level("WARNING", logger="openavc.core.device_manager"):
        await task

    assert "[gone] Pause-expiry auto-resume failed" in caplog.text
    assert "gone" not in dm._pause_expiry_tasks
    assert "gone" not in dm._pause_deadlines


async def test_a_backstop_cancelled_from_outside_forgets_itself(dm):
    """Shutdown cancels every task without asking the manager first."""
    await dm.add_device(_cfg("dev"))
    await dm.pause_device("dev")
    task = dm._pause_expiry_tasks["dev"]
    await asyncio.sleep(0)  # the backstop is waiting out its term

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert "dev" not in dm._pause_expiry_tasks
    assert "dev" not in dm._pause_deadlines


async def test_a_replaced_backstop_leaves_its_successor_armed(dm):
    await dm.add_device(_cfg("dev"))
    await dm.pause_device("dev")
    first = dm._pause_expiry_tasks["dev"]

    await dm.pause_device("dev")
    await asyncio.gather(first, return_exceptions=True)

    assert first.cancelled()
    assert dm._pause_expiry_tasks["dev"] is not first
    assert not dm._pause_expiry_tasks["dev"].done()
    assert "dev" in dm._pause_deadlines


# --- devices waiting for their driver ---------------------------------------


async def test_reloading_one_driver_activates_only_the_orphans_waiting_for_it(dm):
    """An orphan whose own driver arrived meanwhile waits for its own retry:
    a reload of another driver is not the moment it was asked for."""
    other = "acme_pause_other"
    _DRIVER_REGISTRY.pop(DRIVER)
    await dm.add_device(_cfg("mine"))
    await dm.add_device({**_cfg("theirs"), "driver": other})
    _DRIVER_REGISTRY[DRIVER] = _Widget
    _DRIVER_REGISTRY[other] = type("_Other", (_Widget,), {
        "DRIVER_INFO": {**_Widget.DRIVER_INFO, "id": other},
    })
    try:
        activated = await dm.reload_driver(DRIVER)
    finally:
        _DRIVER_REGISTRY.pop(other, None)

    assert activated == ["mine"]
    assert dm.get_missing_drivers() == [other]


async def test_a_device_waiting_for_its_driver_keeps_its_record_and_its_queue(dm):
    """The reconcile compares the project against the records the manager
    keeps, and a setting queued for an orphan waits in that record, stored as
    given since there is no schema to check it against yet."""
    _DRIVER_REGISTRY.pop(DRIVER)
    config = _cfg("waiting")
    await dm.add_device(config)

    await dm.store_pending_settings("waiting", {"anything": 7})

    assert dm.get_device_config("waiting") is config
    assert "waiting" in dm.get_device_configs()
    assert config["pending_settings"] == {"anything": 7}


# --- what the manager says about its devices --------------------------------


async def test_a_connected_device_with_no_numeric_port_is_not_a_live_connection(dm):
    await dm.add_device(_cfg("numbered"))
    await dm.add_device(
        {**_cfg("unnumbered"), "config": {"host": "192.0.2.41", "port": "not-a-port"}}
    )

    hosts = [conn.host for conn in dm.live_connections()]

    assert hosts == ["192.0.2.40"]


class _Equipment:
    """Product hardware's own facts about the devices it installed."""

    def information(self, device_id: str) -> dict[str, Any]:
        return {"installed": device_id == "built_in"}

    def owns(self, device_id: str) -> bool:
        return device_id == "built_in"


async def test_product_hardware_adds_its_facts_to_every_listing_entry_and_the_details(dm):
    dm.installed_equipment = _Equipment()
    await dm.add_device(_cfg("built_in"))
    await dm.add_device(_cfg("added"))

    listed = {entry["id"]: entry["installed"] for entry in dm.list_devices()}

    assert listed == {"built_in": True, "added": False}
    assert dm.get_device_info("built_in")["installed"] is True


async def test_a_device_listed_live_is_not_listed_again_as_waiting(dm):
    """Added again once its driver arrived, before its waiting record went:
    the listing names it once, by its live driver."""
    _DRIVER_REGISTRY.pop(DRIVER)
    await dm.add_device(_cfg("dev"))
    _DRIVER_REGISTRY[DRIVER] = _Widget
    await dm.add_device(_cfg("dev"))

    listed = dm.list_devices()

    assert [entry["id"] for entry in listed] == ["dev"]
    assert "orphaned" not in listed[0]


async def test_a_device_that_sends_through_a_bridge_holds_no_connection_of_its_own(dm):
    await dm.add_device(
        {**_cfg("emitter"), "config": {"transport": "bridge", "host": "192.0.2.42", "port": 4998}}
    )

    assert dm.state.get("device.emitter.connected") is True
    assert dm.live_connections() == []


async def test_a_link_drops_a_setting_the_simulation_added_and_the_device_never_had(dm):
    """The real connection names a setting the redirect added as None: the
    link is built without it, not with the simulator's value."""
    await dm.add_device(_cfg("dev"))
    driver = dm.get_driver("dev")
    driver.config.update({"host": "127.0.0.1", "port": 19001, "path_prefix": "/sim"})
    dm.real_connection = lambda device_id: {"host": "192.0.2.40", "port": 4200, "path_prefix": None}

    link = dm._link_config("dev", driver)

    assert link["host"] == "192.0.2.40"
    assert link["port"] == 4200
    assert "path_prefix" not in link


# --- what a failed command carries ------------------------------------------


def test_a_restart_about_to_end_still_asks_for_a_second_of_patience():
    """The sentence says how long to wait; "0 seconds" would read as "now"
    while the device is still away."""
    exc = device_restarting("dev", 0)

    assert exc.restart_seconds == 1
    assert exc.device_id == "dev"
    assert str(exc) == "Device 'dev' is restarting (0s left)"


@pytest.mark.parametrize("carried", [None, "power_on", ("dev",), ("dev", "power_on", "x"), ["dev", "power_on"]])
def test_only_the_device_and_command_pair_reads_as_a_failed_command(carried):
    """A driver's own exception may carry an attribute by this name for its
    own reasons; only the manager's (device, command) pair counts."""
    exc = RuntimeError("driver failure")
    if carried is not None:
        exc.failed_command = carried

    assert failed_command(exc) is None


def test_a_marked_exception_names_its_device_and_command():
    exc = RuntimeError("driver failure")

    mark_failed_command(exc, "dev", "power_on")

    assert failed_command(exc) == ("dev", "power_on")


def test_an_exception_that_takes_no_attributes_is_raised_unmarked():
    class _Sealed(Exception):
        def __setattr__(self, name: str, value: Any) -> None:
            raise AttributeError(name)

    exc = _Sealed("driver failure")

    mark_failed_command(exc, "dev", "power_on")

    assert failed_command(exc) is None
