"""A device whose driver cannot set it up: kept with a reason, listed and
counted, tried again when its driver changes, at a device sync and on
Reconnect, and cleaned up by its removal.

An invented ``acme_build_widget`` driver stands in for every device. Its
constructor raises while ``_Widget.refuse`` holds an exception (after the base
constructor has created its state keys and registered its login), or while
``_Widget.refuse_first`` does (before the base constructor runs at all).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from openavc.api.routes import system as system_routes
from openavc.core.connection_fault import DRIVER_FAILED, is_permanent_fault
from openavc.core.device_errors import DeviceNotFoundError, DriverBuildError
from openavc.core.device_manager import DeviceManager
from openavc.core.device_sync import update_changed_devices
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import SecretRedactionFilter, get_secret_registry

DRIVER = "acme_build_widget"
OTHER = "acme_build_other"
PASSWORD = "acme-secret-9471"


class _Widget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Build Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {},
        "commands": {"power_on": {"label": "Power On"}},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "config_schema": {"password": {"type": "string", "secret": True}},
    }
    refuse: BaseException | None = None
    refuse_first: BaseException | None = None

    def __init__(self, device_id, config, state, events):
        if type(self).refuse_first is not None:
            raise type(self).refuse_first
        super().__init__(device_id, config, state, events)
        if type(self).refuse is not None:
            raise type(self).refuse

    async def connect(self) -> None:
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _Other(_Widget):
    DRIVER_INFO = {**_Widget.DRIVER_INFO, "id": OTHER}


@pytest.fixture(autouse=True)
def _drivers():
    _DRIVER_REGISTRY[DRIVER] = _Widget
    _DRIVER_REGISTRY[OTHER] = _Other
    _Widget.refuse = _Widget.refuse_first = None
    yield
    _DRIVER_REGISTRY.pop(DRIVER, None)
    _DRIVER_REGISTRY.pop(OTHER, None)
    _Widget.refuse = _Widget.refuse_first = None
    get_secret_registry().forget("dev")
    get_secret_registry().forget("live")


@pytest.fixture
def dm():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return DeviceManager(state, events)


def _cfg(device_id: str = "dev", driver: str = DRIVER, **extra: Any) -> dict[str, Any]:
    return {
        "id": device_id, "driver": driver, "name": device_id.title(),
        "config": {"host": "192.0.2.40", "port": 4100, "password": PASSWORD}, **extra,
    }


async def _broken(dm: DeviceManager, device_id: str = "dev", error: BaseException | None = None) -> None:
    """Add a device its driver cannot set up."""
    _Widget.refuse = error or TypeError("responses must be a list")
    with pytest.raises(DriverBuildError):
        await dm.add_device(_cfg(device_id))
    _Widget.refuse = None


def _keys(dm: DeviceManager, device_id: str = "dev") -> dict[str, Any]:
    return dm.state.get_namespace(f"device.{device_id}.")


# --- kept, with why --------------------------------------------------------


async def test_a_device_its_driver_cannot_set_up_is_listed_with_why(dm):
    _Widget.refuse = TypeError("responses must be a list")
    with pytest.raises(DriverBuildError) as raised:
        await dm.add_device(_cfg())
    assert raised.value.device_id == "dev"
    assert "responses must be a list" in str(raised.value)

    listed = {entry["id"]: entry for entry in dm.list_devices()}
    assert listed["dev"] == {
        "id": "dev", "name": "Dev", "driver": DRIVER, "status": "driver_failed",
        "connected": False, "connection": dm.get_device_info("dev")["connection"],
    }
    info = dm.get_device_info("dev")
    assert info["commands"] == {} and info["driver_info"] == {}
    assert dm.get_driver("dev") is None

    keys = _keys(dm)
    assert keys["offline_reason"] == DRIVER_FAILED
    assert "responses must be a list" in keys["offline_detail"]
    assert DRIVER in keys["offline_detail"]
    # Not retried on its own, and not read as disabled.
    assert keys["reconnect_failed"] is True
    assert keys["enabled"] is True
    assert keys["connected"] is False
    assert keys["name"] == "Dev"


async def test_the_reason_is_a_permanent_one(dm):
    assert is_permanent_fault(DRIVER_FAILED)


async def test_what_the_constructor_created_goes_with_it(dm):
    """The base constructor creates a key for every declared reading before a
    subclass can raise; a device that never ran has no readings to offer."""
    await _broken(dm)
    assert "power" not in _keys(dm)


async def test_a_login_in_the_error_is_masked_on_the_card_and_in_the_log(dm):
    """Raised before the base constructor registered the device's login."""
    lines: list[str] = []

    class _Lines(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    handler = _Lines()
    handler.addFilter(SecretRedactionFilter())
    logger = logging.getLogger("openavc.core.device_manager")
    logger.addHandler(handler)
    _Widget.refuse_first = ValueError(f"cannot sign in with {PASSWORD}")
    try:
        with pytest.raises(DriverBuildError) as raised:
            await dm.add_device(_cfg())
    finally:
        logger.removeHandler(handler)
    detail = _keys(dm)["offline_detail"]
    assert "cannot sign in with" in detail
    assert PASSWORD not in detail
    assert any("cannot sign in with" in line for line in lines)
    assert not any(PASSWORD in line for line in lines)
    # What a caller logs about it is masked the same way.
    assert PASSWORD not in get_secret_registry().redact_any(str(raised.value))


async def test_health_counts_it_as_a_device_that_is_not_connected(dm, monkeypatch):
    await dm.add_device(_cfg("live"))
    await _broken(dm)

    class _Engine:
        devices = dm
        state = dm.state

        def get_status(self) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(system_routes, "_get_engine", lambda: _Engine())
    health = await system_routes.health_check()
    assert health["devices"] == {
        "total": 2, "connected": 1, "disconnected": 1, "orphaned": 0, "disabled": 0,
    }


# --- tried again -------------------------------------------------------------


async def test_a_fixed_driver_brings_it_back_when_reloaded(dm):
    await _broken(dm)

    assert await dm.reload_driver(DRIVER) == ["dev"]

    assert dm.get_driver("dev") is not None
    keys = _keys(dm)
    assert keys["connected"] is True
    assert keys.get("offline_reason") is None
    assert not keys.get("reconnect_failed")


async def test_a_reload_that_still_fails_keeps_it_listed_and_does_not_name_it(dm):
    await _broken(dm)
    _Widget.refuse = TypeError("still broken")

    assert await dm.reload_driver(DRIVER) == []

    assert [entry["id"] for entry in dm.list_devices()] == ["dev"]
    assert "still broken" in _keys(dm)["offline_detail"]


async def test_another_drivers_reload_leaves_it_alone(dm):
    await _broken(dm)
    await dm.reload_driver(OTHER)
    assert dm.get_driver("dev") is None
    assert _keys(dm)["offline_reason"] == DRIVER_FAILED


async def test_reconnect_tries_to_set_it_up_again(dm):
    await _broken(dm)

    await dm.reconnect_device("dev")

    assert dm.get_driver("dev") is not None
    assert _keys(dm)["connected"] is True
    assert _keys(dm).get("offline_reason") is None


async def test_a_reconnect_that_fails_again_says_why_rather_than_raising(dm):
    await _broken(dm)
    _Widget.refuse = TypeError("still broken")

    await dm.reconnect_device("dev")

    assert dm.get_driver("dev") is None
    assert "still broken" in _keys(dm)["offline_detail"]


async def test_a_device_sync_tries_it_again_with_nothing_changed(dm):
    """Before the device had a record, every device sync added it again,
    because the sync adds whatever the manager does not have."""
    await _broken(dm)

    await update_changed_devices(dm, {"dev"}, {"dev": _cfg()}, defer_connect=True)

    assert dm.get_driver("dev") is not None
    assert dm.is_connect_deferred("dev")


async def test_an_orphan_whose_driver_arrives_but_cannot_set_it_up_says_so(dm):
    saved = _DRIVER_REGISTRY.pop(DRIVER)
    await dm.add_device(_cfg())
    _DRIVER_REGISTRY[DRIVER] = saved
    _Widget.refuse = TypeError("responses must be a list")

    with pytest.raises(DriverBuildError):
        await dm.retry_orphaned_device("dev")

    listed = {entry["id"]: entry for entry in dm.list_devices()}
    assert "orphaned" not in listed["dev"]
    assert _keys(dm)["offline_reason"] == DRIVER_FAILED


# --- an edit -----------------------------------------------------------------


async def test_an_edit_its_driver_cannot_set_up_keeps_the_device_listed(dm):
    await dm.add_device(_cfg())
    _Widget.refuse = TypeError("responses must be a list")

    with pytest.raises(DriverBuildError):
        await dm.update_device("dev", _cfg(name="Renamed"))

    assert [entry["id"] for entry in dm.list_devices()] == ["dev"]
    assert _keys(dm)["name"] == "Renamed"
    assert _keys(dm)["offline_reason"] == DRIVER_FAILED


async def test_an_edit_of_a_device_that_failed_tries_again(dm):
    await _broken(dm)

    await dm.update_device("dev", _cfg(name="Fixed"))

    assert dm.get_driver("dev") is not None
    assert _keys(dm).get("offline_reason") is None
    assert not _keys(dm).get("reconnect_failed")


async def test_an_edit_that_disables_it_leaves_only_the_disabled_device(dm):
    await _broken(dm)

    await dm.update_device("dev", _cfg(enabled=False))

    listed = dm.list_devices()
    assert listed[0]["enabled"] is False
    keys = _keys(dm)
    assert keys["enabled"] is False
    assert keys.get("offline_reason") is None
    assert not keys.get("reconnect_failed")


# --- removed -----------------------------------------------------------------


async def test_removing_it_leaves_nothing_behind(dm):
    await _broken(dm)

    await dm.remove_device("dev")

    assert dm.list_devices() == []
    with pytest.raises(DeviceNotFoundError):
        dm.get_device_info("dev")
    assert _keys(dm) == {}
    assert PASSWORD in get_secret_registry().redact_for("dev", f"x {PASSWORD}")
    # Not tried again by a later reload either.
    assert await dm.reload_driver(DRIVER) == []
    assert dm.get_device_configs() == {}
