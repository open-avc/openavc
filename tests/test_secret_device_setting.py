"""A device setting its driver marks secret: never queued, never shown.

A setting written while the device is away waits in the device's queue, which
is saved in the project, so it travels in every backup, export and copy the
cloud keeps. A secret setting (an admin PIN) is a login: the queue refuses it
at its intake, and refuses one already queued (a project that came in holding
it) before it is sent. A live write sends it to the device, and the device's
masking learns its value first, as it learns a secret command parameter's.
Every door asks one rule whether a setting is secret
(``drivers/secret_params.is_secret_device_setting``). Uses an invented device
(Acme).
"""

from __future__ import annotations

import logging

import pytest

from openavc.core import pending_settings
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.pending_settings import PendingClear, SecretSettingNotQueued
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, DeviceSettingValueError
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.drivers.secret_params import is_secret_device_setting
from openavc.utils.log_redaction import REDACTED, get_secret_registry
from openavc.utils.secret_audience import Exit

PIN = "4821"
REFUSAL = "Admin PIN is a login. Enter it on the device's page while the device is online"


class _AcmeLock(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_lock",
        "name": "Acme Lock",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "device_settings": {
            "admin_pin": {
                "type": "string", "secret": True, "label": "Admin PIN",
                "regex": r"^\d{4,8}$", "setup": True,
            },
            "gain": {"type": "integer", "label": "Gain", "min": 0, "max": 100},
            "room_name": {"type": "string", "label": "Room Name"},
        },
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.writes: list[tuple[str, object]] = []

    async def connect(self):
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self):
        self._connected = False

    async def send_command(self, command, params=None):
        return True

    async def set_device_setting(self, key, value):
        self.writes.append((key, value))
        return True


@pytest.fixture
def manager():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    yield DeviceManager(state, events)
    get_secret_registry().forget("lock1")


async def _running(dm: DeviceManager, queue: dict | None = None) -> _AcmeLock:
    driver = _AcmeLock("lock1", {}, dm.state, dm.events)
    await driver.connect()
    dm._devices["lock1"] = driver
    dm._device_configs["lock1"] = {"driver": "acme_lock", **({"pending_settings": queue} if queue else {})}
    return driver


# --- The one rule ---------------------------------------------------------------


def test_the_driver_s_mark_decides_a_secret_setting():
    assert is_secret_device_setting(_AcmeLock, "admin_pin")
    assert not is_secret_device_setting(_AcmeLock, "gain")
    assert not is_secret_device_setting(_AcmeLock, "not_declared")


def test_a_setting_named_password_declares_itself_on_any_device():
    assert is_secret_device_setting(_AcmeLock, "password")
    assert is_secret_device_setting(None, "password")
    assert not is_secret_device_setting(None, "admin_pin")


# --- The queue never takes one ----------------------------------------------------


async def test_the_queue_refuses_a_secret_setting_naming_it_not_its_value(manager):
    await _running(manager)

    with pytest.raises(SecretSettingNotQueued) as refused:
        await manager.store_pending_settings("lock1", {"admin_pin": PIN})

    assert str(refused.value) == REFUSAL
    assert isinstance(refused.value, DeviceSettingValueError)  # the route's 400
    assert manager.queued_settings("lock1") == {}


async def test_a_batch_holding_a_secret_queues_nothing(manager):
    await _running(manager)

    with pytest.raises(SecretSettingNotQueued):
        await manager.store_pending_settings("lock1", {"gain": 10, "admin_pin": PIN})

    assert manager.queued_settings("lock1") == {}


async def test_the_queue_refuses_one_for_a_device_its_driver_is_not_running(manager):
    """A device whose driver is installed but not running (disabled, or its
    driver failed) is judged on the installed driver's declaration."""
    register_driver(_AcmeLock)
    try:
        manager._device_configs["lock1"] = {"driver": "acme_lock"}
        with pytest.raises(SecretSettingNotQueued):
            await manager.store_pending_settings("lock1", {"admin_pin": PIN})
    finally:
        unregister_driver("acme_lock")
    assert manager.queued_settings("lock1") == {}


async def test_a_secret_already_queued_is_never_sent(manager, caplog):
    """A project that came in holding one (written before the queue refused
    them, or by hand): the pass leaves it unsent, takes it off the queue, so
    out of the project, and says on the device why, without the value."""
    driver = await _running(manager, {"admin_pin": PIN, "gain": 10})
    errors: list[dict] = []
    manager.events.on("device.error.lock1", lambda _name, payload: errors.append(payload))

    with caplog.at_level(logging.DEBUG):
        await manager._apply_pending_settings("lock1")

    assert driver.writes == [("gain", 10)]
    assert manager.queued_settings("lock1") == {}
    assert [e["error"] for e in errors] == [
        f"Pending setting 'admin_pin' was not sent: {REFUSAL}. It is no longer queued."
    ]
    assert PIN not in caplog.text


# --- Where a queued value is shown -----------------------------------------------


async def test_the_settings_read_shows_a_queued_secret_masked(manager):
    await _running(manager, {"admin_pin": PIN, "gain": 10})

    assert manager.queued_settings_shown("lock1") == {"admin_pin": REDACTED, "gain": 10}


async def test_the_cleared_announcement_shows_a_queued_secret_masked(manager):
    await _running(manager, {"admin_pin": PIN})
    announced: list[dict] = []
    manager.events.on(
        "device.pending_settings_applied", lambda _name, payload: announced.append(payload),
    )

    await manager.announce_cleared(PendingClear("lock1", {"gain": 10}))

    assert announced[0]["remaining"] == {"admin_pin": REDACTED}


# --- A live write -------------------------------------------------------------------


async def test_a_live_write_sends_it_and_the_device_s_masking_learns_it(manager):
    driver = await _running(manager)

    await manager.set_device_setting("lock1", "admin_pin", PIN)

    assert driver.writes == [("admin_pin", PIN)]
    registry = get_secret_registry()
    assert registry.masked(Exit.LOG, f"sent {PIN}", "lock1") == f"sent {REDACTED}"


async def test_a_refused_live_write_is_masked_too(manager):
    """The value as given is learned before the checks, so one the
    declaration refuses is masked in whatever echoes it."""
    driver = await _running(manager)

    with pytest.raises(DeviceSettingValueError):
        await manager.set_device_setting("lock1", "admin_pin", "48a21")

    assert driver.writes == []
    assert get_secret_registry().masked(Exit.LOG, "got 48a21", "lock1") == f"got {REDACTED}"


async def test_a_plain_setting_teaches_the_masking_nothing(manager):
    await _running(manager)

    await manager.set_device_setting("lock1", "room_name", PIN)

    assert get_secret_registry().masked(Exit.LOG, f"room {PIN}", "lock1") == f"room {PIN}"


# --- The REST doors ---------------------------------------------------------------


@pytest.fixture
def route(manager, monkeypatch):
    from openavc.api.routes import devices as devices_route

    class _Engine:
        devices = manager
        project = object()

    monkeypatch.setattr(devices_route, "_get_engine", lambda: _Engine())
    return devices_route


async def test_the_settings_read_answers_a_queued_secret_masked(manager, route):
    await _running(manager, {"admin_pin": PIN, "gain": 10})

    answer = await route.get_device_settings("lock1")

    assert answer["pending"] == {"admin_pin": REDACTED, "gain": 10}


async def test_a_live_write_answers_a_secret_masked(manager, route):
    from openavc.api.models import DeviceSettingRequest

    await _running(manager)

    answer = await route.set_device_setting("lock1", "admin_pin", DeviceSettingRequest(value=PIN))
    plain = await route.set_device_setting("lock1", "gain", DeviceSettingRequest(value=40))

    assert answer["value"] == REDACTED
    assert plain["value"] == 40


async def test_the_queue_route_answers_a_secret_with_its_refusal(manager, route):
    from fastapi import HTTPException

    from openavc.api.models import PendingSettingsRequest

    await _running(manager)

    with pytest.raises(HTTPException) as refused:
        await route.store_pending_settings("lock1", PendingSettingsRequest(settings={"admin_pin": PIN}))

    assert (refused.value.status_code, refused.value.detail) == (400, REFUSAL)


def test_the_queue_module_asks_the_one_rule():
    """The queue reads no secret mark of its own."""
    assert pending_settings.is_secret_device_setting is is_secret_device_setting
