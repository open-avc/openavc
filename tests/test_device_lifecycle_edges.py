"""The device manager's lifecycle at its edges: a driver reload, an edit, an
orphan's activation, Reconnect, the listings and the connect and drop handlers,
each in the cases nothing else pins.

An invented ``acme_lifecycle_widget`` driver that opens no socket stands in for
every device: connecting it publishes connected, and a test can make its next
connect fail or end.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from openavc.core.connection_fault import UNREACHABLE
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.installed_equipment import InstalledEquipmentError
from openavc.core.state_store import StateStore
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver, ConnectEndedError
from openavc.drivers.registry import _DRIVER_REGISTRY

DRIVER = "acme_lifecycle_widget"
OTHER = "acme_lifecycle_other"


class _Widget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Lifecycle Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {},
        "commands": {"power_on": {"label": "Power On"}, "mute": {}},
        "state_variables": {},
        "config_schema": {},
    }
    fail_next: BaseException | None = None

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

    async def stop_polling(self) -> None:
        return None


class _Other(_Widget):
    DRIVER_INFO = {**_Widget.DRIVER_INFO, "id": OTHER}


@pytest.fixture(autouse=True)
def _drivers():
    _DRIVER_REGISTRY[DRIVER] = _Widget
    _DRIVER_REGISTRY[OTHER] = _Other
    _Widget.fail_next = None
    yield
    _DRIVER_REGISTRY.pop(DRIVER, None)
    _DRIVER_REGISTRY.pop(OTHER, None)
    _Widget.fail_next = None


@pytest.fixture
def dm():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return DeviceManager(state, events)


def _cfg(device_id: str, driver: str = DRIVER, **extra: Any) -> dict[str, Any]:
    return {
        "id": device_id, "driver": driver, "name": device_id.title(),
        "config": {"host": "192.0.2.30", "port": 4100}, **extra,
    }


async def _orphan(dm: DeviceManager, device_id: str, driver: str = DRIVER) -> None:
    """Add a device whose driver is not installed yet."""
    saved = _DRIVER_REGISTRY.pop(driver)
    await dm.add_device(_cfg(device_id, driver))
    _DRIVER_REGISTRY[driver] = saved


# --- a driver reload ---------------------------------------------------------


async def test_reloading_a_driver_the_manufacturer_manages_is_refused(dm, monkeypatch):
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", {DRIVER})
    with pytest.raises(InstalledEquipmentError):
        await dm.reload_driver(DRIVER)


async def test_a_reload_with_nothing_to_re_add_asks_for_no_round(dm):
    rounds: list[int] = []
    dm.request_bringup = lambda: rounds.append(1)

    assert await dm.reload_driver(DRIVER) == []
    assert rounds == []


async def test_a_reload_activates_the_orphans_waiting_for_that_driver(dm):
    await dm.add_device(_cfg("live"))
    await _orphan(dm, "waiting")

    reconnected = await dm.reload_driver(DRIVER)

    assert reconnected == ["live", "waiting"]
    assert dm.get_driver("waiting") is not None
    assert dm.get_missing_drivers() == []


async def test_a_reload_leaves_other_drivers_and_disabled_devices_alone(dm):
    await dm.add_device(_cfg("mine"))
    await dm.add_device(_cfg("theirs", OTHER))
    await dm.add_device(_cfg("off", enabled=False))
    theirs = dm.get_driver("theirs")

    assert await dm.reload_driver(DRIVER) == ["mine"]
    assert dm.get_driver("theirs") is theirs
    assert dm.get_driver("off") is None


async def test_one_device_failing_to_re_add_does_not_stop_the_rest(dm, monkeypatch):
    await dm.add_device(_cfg("first"))
    await dm.add_device(_cfg("second"))
    remove = dm.remove_device

    async def remove_device(device_id: str) -> None:
        if device_id == "first":
            raise RuntimeError("stuck")
        await remove(device_id)

    monkeypatch.setattr(dm, "remove_device", remove_device)

    assert await dm.reload_driver(DRIVER) == ["second"]


async def test_one_orphan_failing_to_activate_does_not_stop_the_rest(dm, monkeypatch):
    await _orphan(dm, "first")
    await _orphan(dm, "second")
    retry = dm.retry_orphaned_device

    async def retry_orphaned_device(device_id: str, **kwargs: Any) -> bool:
        if device_id == "first":
            raise RuntimeError("stuck")
        return await retry(device_id, **kwargs)

    monkeypatch.setattr(dm, "retry_orphaned_device", retry_orphaned_device)

    assert await dm.reload_driver(DRIVER) == ["second"]
    assert await dm.retry_all_orphans() == []

    await _orphan(dm, "third")
    assert await dm.retry_all_orphans() == ["third"]


# --- an edit -----------------------------------------------------------------


async def test_an_edit_that_disables_a_paused_device_ends_the_pause(dm):
    await dm.add_device(_cfg("dev"))
    await dm.pause_device("dev", ttl=30)

    await dm.update_device("dev", _cfg("dev", enabled=False))

    assert dm._pause_remaining("dev") is None
    assert not dm.is_paused("dev")


async def test_an_edit_can_leave_the_device_for_the_bring_up_round(dm):
    await dm.add_device(_cfg("dev"))

    await dm.update_device("dev", _cfg("dev"), defer_connect=True)

    assert dm.is_connect_deferred("dev")


async def test_editing_a_disabled_device_drops_its_old_entry_first(dm, monkeypatch):
    await dm.add_device(_cfg("dev", enabled=False))

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("driver would not build")

    monkeypatch.setitem(_DRIVER_REGISTRY, DRIVER, broken)
    with pytest.raises(RuntimeError):
        await dm.update_device("dev", _cfg("dev"))

    assert dm.get_device_config("dev") is None


# --- an orphan's activation --------------------------------------------------


async def test_an_orphan_whose_driver_is_still_missing_is_not_re_added(dm):
    await _orphan(dm, "dev", OTHER)
    seen: list[str] = []
    dm.events.on("device.orphaned", lambda e, p: seen.append(p["device_id"]))
    saved = _DRIVER_REGISTRY.pop(OTHER)
    try:
        assert await dm.retry_orphaned_device("dev") is False
        assert await dm.retry_all_orphans() == []
    finally:
        _DRIVER_REGISTRY[OTHER] = saved
    assert seen == []


async def test_activating_every_orphan_can_leave_them_for_the_bring_up_round(dm):
    await _orphan(dm, "dev")

    assert await dm.retry_all_orphans(defer_connect=True) == ["dev"]
    assert dm.is_connect_deferred("dev")


# --- Reconnect ---------------------------------------------------------------


async def test_reconnect_starts_clean_and_flushes_the_queue(dm, monkeypatch):
    await dm.add_device(_cfg("dev"))
    for key, value in (
        ("reconnect_failed", True), ("reconnect_attempt", 3), ("offline_reason", UNREACHABLE),
    ):
        dm.state.set(f"device.dev.{key}", value)
    applied = AsyncMock()
    refreshed = Mock()
    monkeypatch.setattr(dm, "_apply_pending_settings", applied)
    monkeypatch.setattr(dm, "_refresh_usb_serial_port", refreshed)
    # The widget connects without announcing it, so nothing else clears the reason.

    await dm.reconnect_device("dev")

    for key in ("reconnect_failed", "reconnect_attempt", "offline_reason"):
        assert dm.state.get(f"device.dev.{key}") is None
    applied.assert_awaited_once_with("dev")
    refreshed.assert_called_once_with("dev", dm.get_driver("dev"))
    assert "dev" not in dm._intentional_disconnect


async def test_a_failed_reconnect_reads_offline(dm):
    await dm.add_device(_cfg("dev"))
    _Widget.fail_next = ConnectionRefusedError(61, "Connection refused")

    await dm.reconnect_device("dev")

    assert dm.state.get("device.dev.connected") is False
    assert dm.state.get("device.dev.offline_reason") is not None
    assert "dev" not in dm._intentional_disconnect
    await dm._cancel_reconnect("dev")


async def test_a_reconnect_ended_by_another_door_is_not_a_failure(dm):
    await dm.add_device(_cfg("dev"))
    _Widget.fail_next = ConnectEndedError("ended")

    await dm.reconnect_device("dev")

    assert dm.state.get("device.dev.offline_reason") is None
    assert dm.state.get("device.dev.connected") is True
    assert "dev" not in dm._reconnect_tasks


# --- the listings ------------------------------------------------------------


async def test_the_listing_names_each_kind_of_device_in_full(dm, monkeypatch):
    await dm.add_device(_cfg("live"))
    await dm.add_device(_cfg("off", enabled=False))
    await _orphan(dm, "lost", OTHER)

    listed = {entry["id"]: entry for entry in dm.list_devices()}

    assert listed["live"]["commands"] == ["power_on", "mute"]
    connection = dm.get_device_info("off")["connection"]
    assert listed["off"] == {
        "id": "off", "name": "Off", "driver": DRIVER, "connected": False,
        "connection": connection, "enabled": False,
    }
    assert set(listed["lost"]) == {
        "id", "name", "driver", "connected", "connection", "orphaned", "orphan_reason",
    }
    assert [entry["id"] for entry in dm.list_devices()] == ["live", "lost", "off"]


async def test_a_device_whose_info_fails_is_still_listed(dm, monkeypatch):
    await dm.add_device(_cfg("live"))

    def broken(device_id: str) -> dict[str, Any]:
        raise RuntimeError("no info")

    monkeypatch.setattr(dm, "get_device_info", broken)

    assert dm._list_devices() == [{"id": "live", "name": "live", "connected": False}]


async def test_device_info_for_a_disabled_and_an_orphaned_device(dm):
    await dm.add_device(_cfg("off", enabled=False))
    await _orphan(dm, "lost", OTHER)

    off = dm.get_device_info("off")
    lost = dm.get_device_info("lost")

    assert off["state"] == dm.state.get_namespace("device.off")
    assert off["commands"] == {} and off["driver_info"] == {}
    assert "orphaned" not in off
    assert lost["orphaned"] is True
    assert lost["orphan_reason"]
    assert list(lost) == [
        "id", "name", "driver", "connected", "connection", "orphaned", "orphan_reason",
        "state", "commands", "driver_info", "config",
    ]


# --- what adding a device publishes ------------------------------------------


async def test_adding_publishes_each_kind_of_device(dm):
    await dm.add_device(_cfg("live"))
    await dm.add_device(_cfg("off", enabled=False))
    await _orphan(dm, "lost", OTHER)
    await dm.add_device(_cfg("later"), defer_connect=True)

    assert dm.state.get("device.live.enabled") is True
    assert dm.state.get("device.off.name") == "Off"
    assert dm.state.get("device.lost.connected") is False
    assert dm.state.get("device.later.connected") is False


async def test_a_device_re_added_while_paused_is_prepared_but_not_dialed(dm, monkeypatch):
    await dm.add_device(_cfg("dev"))
    await dm.pause_device("dev", ttl=30)
    prepared = AsyncMock()
    probed = Mock()
    monkeypatch.setattr(dm, "_prepare_bridge_for", prepared)
    monkeypatch.setattr(dm, "_schedule_web_ui_probe", probed)
    dm.state.set("device.dev.connected", True)

    await dm.update_device("dev", _cfg("dev"))

    prepared.assert_awaited_once()
    probed.assert_called_once_with("dev")
    assert dm.state.get("device.dev.connected") is False
    assert dm.is_paused("dev")
    dm._cancel_pause_expiry("dev")


# --- the connect and drop handlers ------------------------------------------


async def test_the_handlers_ignore_an_event_with_no_device_id(dm):
    assert await dm._on_device_connected("device.connected", {}) is None
    assert await dm._on_device_disconnected("device.disconnected", {}) is None


async def test_a_disabled_device_dropping_starts_no_reconnect_loop(dm):
    await dm.add_device(_cfg("dev"))
    dm._device_configs["dev"] = _cfg("dev", enabled=False)
    dm.get_driver("dev")._connected = False
    dm.get_driver("dev").set_state("connected", False)

    await dm._on_device_disconnected("device.disconnected.dev", {})

    assert "dev" not in dm._reconnect_tasks


async def test_a_connect_clears_a_stale_reason_only_when_nothing_else_owns_it(dm, monkeypatch):
    await dm.add_device(_cfg("dev"))
    probed = Mock()
    monkeypatch.setattr(dm, "_schedule_web_ui_probe", probed)
    driver = dm.get_driver("dev")

    # A reconnect loop is running: it clears the reason itself.
    dm.state.set("device.dev.offline_reason", UNREACHABLE)
    dm._reconnect_tasks["dev"] = Mock()
    await dm._on_device_connected("device.connected.dev", {})
    assert dm.state.get("device.dev.offline_reason") == UNREACHABLE
    del dm._reconnect_tasks["dev"]

    # Held open but not answering yet: still offline.
    driver.set_state("connected", False)
    await dm._on_device_connected("device.connected.dev", {})
    assert dm.state.get("device.dev.offline_reason") == UNREACHABLE

    driver.set_state("connected", True)
    await dm._on_device_connected("device.connected.dev", {})
    assert dm.state.get("device.dev.offline_reason") is None
    assert probed.call_args_list[-1].args == ("dev",)
