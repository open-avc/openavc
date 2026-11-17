"""What a device keeps when the device manager replaces it with a fresh driver
instance: an edit, a driver reload, its driver arriving, or a retry after its
driver could not set it up.

A replacement is the same device on a new instance. It keeps why it is away on
purpose (a sleep, a commanded-restart window) while it stays on the driver
whose commands declared them, its pause, and its queued settings, which meet
the driver's declaration before any is sent. A removal forgets all of it.

An invented ``acme_replace_widget`` driver that opens no socket stands in for
every device: a test can make its next connect fail as an absent device's
does.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core import device_sleep
from openavc.core.connection_fault import UNREACHABLE
from openavc.core.device_manager import DeviceManager
from openavc.core.device_sleep import SleepBook
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectionFaultError
from openavc.drivers.registry import _DRIVER_REGISTRY

DRIVER = "acme_replace_widget"
OTHER = "acme_replace_other"


class _Widget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Replace Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {},
        "commands": {
            "power_off": {"label": "Power Off", "sleeps_device": "power_on"},
            "power_on": {"label": "Power On", "available_offline": True},
            "reboot": {"label": "Reboot", "restarts_device_for": 60},
        },
        "state_variables": {},
        "config_schema": {},
        "device_settings": {
            "level": {"type": "integer", "label": "Level", "min": 0, "max": 100, "setup": False},
        },
    }
    fail_next: BaseException | None = None
    written: list[tuple[str, Any]] = []

    async def connect(self) -> None:
        failure, type(self).fail_next = type(self).fail_next, None
        if failure is not None:
            raise failure
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def set_device_setting(self, key: str, value: Any) -> Any:
        _Widget.written.append((key, value))
        return True

    async def stop_polling(self) -> None:
        return None


class _Other(_Widget):
    DRIVER_INFO = {**_Widget.DRIVER_INFO, "id": OTHER, "name": "Acme Replace Other"}


@pytest.fixture(autouse=True)
def _drivers():
    _DRIVER_REGISTRY[DRIVER] = _Widget
    _DRIVER_REGISTRY[OTHER] = _Other
    _Widget.fail_next = None
    _Widget.written = []
    yield
    _DRIVER_REGISTRY.pop(DRIVER, None)
    _DRIVER_REGISTRY.pop(OTHER, None)
    _Widget.fail_next = None


@pytest.fixture
async def dm(tmp_path, monkeypatch):
    path = tmp_path / device_sleep.RECORD_NAME
    monkeypatch.setattr(device_sleep, "_default_path", lambda: path)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    manager = DeviceManager(state, events)
    manager._sleep = SleepBook(device_sleep._default_path)
    yield manager
    await manager.disconnect_all()


def _cfg(**extra: Any) -> dict[str, Any]:
    return {
        "id": "w", "driver": DRIVER, "name": "Widget",
        "config": {"host": "192.0.2.50", "port": 4300}, **extra,
    }


def _absent() -> ConnectionFaultError:
    """What connecting to a device that is not on the network raises."""
    return ConnectionFaultError("", code=UNREACHABLE)


async def _gone_away_after(dm: DeviceManager, command: str) -> None:
    """Send ``command`` to a connected widget, then lose it, as a device that
    went to sleep or restarted does."""
    await dm.send_command("w", command, {})
    driver = dm.get_driver("w")
    driver._connected = False
    dm.state.set("device.w.connected", False, source="driver")
    dm._set_offline_reason("w", driver, exc=_absent())


async def _asleep(dm: DeviceManager) -> None:
    await dm.add_device(_cfg())
    await _gone_away_after(dm, "power_off")
    assert dm.state.get("device.w.asleep") is True


async def _asleep_since_a_restart(dm: DeviceManager) -> None:
    """Asleep in device_sleep.json, as a server restarted overnight finds it,
    with its driver not installed: the device waits for it."""
    dm._sleep.arm("w", "power_on", "Power On", DRIVER)
    dm._sleep.mark_asleep("w")
    _DRIVER_REGISTRY.pop(DRIVER)
    await dm.add_device(_cfg())
    _DRIVER_REGISTRY[DRIVER] = _Widget


async def _edit(dm: DeviceManager) -> None:
    await dm.update_device("w", _cfg(name="Widget, renamed"))


async def _reload(dm: DeviceManager) -> None:
    await dm.reload_driver(DRIVER)


async def _activate(dm: DeviceManager) -> None:
    assert await dm.retry_orphaned_device("w")


def _reads_asleep(dm: DeviceManager) -> None:
    state = dm.state
    assert dm.runs("w")
    assert state.get("device.w.asleep") is True
    assert state.get("device.w.wake_command") == "power_on"
    assert state.get("device.w.offline_reason") is None
    assert state.get("device.w.offline_detail") == "Asleep. Power On wakes it."
    assert dm.away_on_purpose("w")


# --- why it is away on purpose -----------------------------------------------


@pytest.mark.parametrize(("setup", "replace"), [
    (_asleep, _edit),
    (_asleep, _reload),
    (_asleep_since_a_restart, _activate),
], ids=["an edit", "a driver reload", "its driver arriving"])
async def test_a_sleeping_device_replaced_on_its_driver_stays_asleep(dm, setup, replace):
    """A display put in standby by its Power Off, or a computer told to shut
    down, is still asleep after the replacement, so its card says what wakes
    it and nothing alerts on it."""
    await setup(dm)
    _Widget.fail_next = _absent()

    await replace(dm)

    _reads_asleep(dm)


async def test_a_device_restarting_on_command_is_still_restarting_after_an_edit(dm):
    await dm.add_device(_cfg())
    await _gone_away_after(dm, "reboot")
    assert dm.state.get("device.w.restarting") is True
    _Widget.fail_next = _absent()

    await _edit(dm)

    assert dm.state.get("device.w.restarting") is True
    assert dm.state.get("device.w.offline_reason") is None
    assert dm.restart_seconds_left("w") is not None


async def test_an_edit_that_moves_the_device_to_another_driver_ends_its_sleep(dm):
    """The sleep was declared by the old driver's commands; the new driver's
    commands are not the ones that put it to sleep."""
    await _asleep(dm)
    _Widget.fail_next = _absent()

    await dm.update_device("w", _cfg(driver=OTHER))

    assert dm.sleep_of("w") is None
    assert dm.state.get("device.w.asleep") is None
    assert dm.state.get("device.w.offline_reason") == UNREACHABLE


async def test_removing_a_sleeping_device_forgets_its_sleep_and_restart_window(dm):
    await dm.add_device(_cfg())
    await _gone_away_after(dm, "reboot")
    dm._sleep.arm("w", "power_on", "Power On", DRIVER)

    await dm.remove_device("w")

    assert dm.sleep_of("w") is None
    assert dm.restart_seconds_left("w") is None


async def test_a_paused_device_replaced_stays_paused_with_the_same_holder(dm):
    """The pause belongs to whoever took it (the Live Test, an audit); an
    edit is not them releasing it."""
    await dm.add_device(_cfg())
    await dm.pause_device("w", purpose="audit")

    await _edit(dm)

    assert dm.is_paused("w")
    assert dm.state.get("device.w.paused_for") == "audit"
    assert not dm.state.get("device.w.connected")


# --- its queued settings -----------------------------------------------------


async def test_a_queue_stored_while_the_driver_was_missing_meets_it_before_anything_is_sent(dm):
    """A setting queued while the device waited for its driver was stored as
    given: there was nothing to check it against. When the driver arrives the
    queue is checked against what it declares before anything goes out. A
    good value goes out in its declared type; one the driver refuses is taken
    off the queue, and the device's error event says why."""
    _DRIVER_REGISTRY.pop(DRIVER)
    await dm.add_device(_cfg())
    await dm.store_pending_settings("w", {"level": "70", "levle": 5})
    await dm.store_pending_settings("w", {"label": "Lobby"})
    errors: list[str] = []
    saved: list[dict[str, Any]] = []
    dm.events.on("device.error.w", lambda _e, payload: errors.append(payload["error"]))
    dm.events.on("device.pending_settings_applied", lambda _e, payload: saved.append(payload))
    _DRIVER_REGISTRY[DRIVER] = _Widget

    await _activate(dm)

    assert _Widget.written == [("level", 70)]
    assert dm.get_device_config("w").get("pending_settings") is None
    assert saved[-1]["remaining"] == {}
    assert sorted(saved[-1]["refused"]) == ["label", "levle"]
    assert errors == [
        "Pending setting 'levle' was taken off the queue without being sent: "
        "Unknown device setting 'levle' for device 'w'",
        "Pending setting 'label' was taken off the queue without being sent: "
        "Unknown device setting 'label' for device 'w'",
    ]


async def test_a_queued_value_its_driver_refuses_is_not_retried_at_every_connect(dm):
    """Out of the declared range, the value can never go out on this driver:
    it leaves the queue once, saying so, instead of failing on every
    connection for good."""
    _DRIVER_REGISTRY.pop(DRIVER)
    await dm.add_device(_cfg())
    await dm.store_pending_settings("w", {"level": 250})
    errors: list[str] = []
    dm.events.on("device.error.w", lambda _e, payload: errors.append(payload["error"]))
    _DRIVER_REGISTRY[DRIVER] = _Widget

    await _activate(dm)

    assert _Widget.written == []
    assert dm.get_device_config("w").get("pending_settings") is None
    assert errors == [
        "Pending setting 'level' was taken off the queue without being sent: "
        "'level' must be at most 100, got 250",
    ]
