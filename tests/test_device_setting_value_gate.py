"""Runtime value gate for device-setting writes.

The min/max/values/regex on a device_settings entry used to be enforced only
by the IDE editor — scripts, macros, cloud commands, and raw REST calls could
push anything through (including values that made a `{value:d}` format spec
fail and transmit the literal placeholder to the device). The gate lives in
DeviceManager.set_device_setting / store_pending_settings so every caller
passes through it. Uses an invented device (Acme).
"""

from __future__ import annotations

import pytest

from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import (
    BaseDriver,
    DeviceSettingValueError,
    validate_device_setting_value,
)


# ── Validator unit tests ─────────────────────────────────────────────────────


def test_boolean_accepts_real_and_tolerant_forms():
    sdef = {"type": "boolean"}
    assert validate_device_setting_value("k", sdef, True) is True
    assert validate_device_setting_value("k", sdef, 0) is False
    assert validate_device_setting_value("k", sdef, "true") is True
    assert validate_device_setting_value("k", sdef, "Off") is False


def test_boolean_rejects_junk():
    """The panasonic_awhe scenario: a junk string on a {value:d} boolean
    setting used to transmit the literal placeholder to the camera."""
    with pytest.raises(DeviceSettingValueError, match="true or false"):
        validate_device_setting_value("tally", {"type": "boolean"}, "maybe")


def test_integer_bounds_enforced_and_strings_coerced():
    sdef = {"type": "integer", "min": 0, "max": 100}
    assert validate_device_setting_value("brightness", sdef, "50") == 50
    assert validate_device_setting_value("brightness", sdef, 100) == 100
    with pytest.raises(DeviceSettingValueError, match="at most 100"):
        validate_device_setting_value("brightness", sdef, 999)
    with pytest.raises(DeviceSettingValueError, match="at least 0"):
        validate_device_setting_value("brightness", sdef, -1)
    with pytest.raises(DeviceSettingValueError, match="whole number"):
        validate_device_setting_value("brightness", sdef, "5.5")
    with pytest.raises(DeviceSettingValueError, match="whole number"):
        validate_device_setting_value("brightness", sdef, True)


def test_enum_membership_enforced():
    sdef = {"type": "enum", "values": ["auto", "manual"]}
    assert validate_device_setting_value("mode", sdef, "auto") == "auto"
    with pytest.raises(DeviceSettingValueError, match="one of"):
        validate_device_setting_value("mode", sdef, "turbo")


def test_string_regex_and_pattern_alias():
    """Settings declare `regex`; command params declare `pattern`. The
    wrong key used to be a silent no-op — both are honored now."""
    assert (
        validate_device_setting_value("name", {"type": "string", "regex": r"[A-Za-z ]+"}, "Main Rack")
        == "Main Rack"
    )
    with pytest.raises(DeviceSettingValueError, match="required format"):
        validate_device_setting_value("name", {"type": "string", "regex": r"[A-Za-z ]+"}, "rack-1")
    with pytest.raises(DeviceSettingValueError, match="required format"):
        validate_device_setting_value("name", {"type": "string", "pattern": r"[A-Za-z ]+"}, "rack-1")


def test_none_value_rejected_and_unknown_schema_passthrough():
    with pytest.raises(DeviceSettingValueError, match="required"):
        validate_device_setting_value("k", {"type": "string"}, None)
    # A non-dict schema entry can't be validated — pass through unchanged.
    assert validate_device_setting_value("k", None, "x") == "x"


# ── DeviceManager integration ───────────────────────────────────────────────


class _SettingsDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_settings",
        "name": "Acme Settings Widget",
        "transport": "tcp",
        "state_variables": {"brightness": {"type": "integer"}},
        "commands": {},
        "device_settings": {
            "brightness": {
                "type": "integer", "label": "Brightness", "state_key": "brightness",
                "min": 0, "max": 100, "default": 50, "setup": False,
            },
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
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


async def test_manager_rejects_out_of_range_setting(core):
    state, events = core
    dm = DeviceManager(state, events)
    driver = _SettingsDriver("dev1", {}, state, events)
    await driver.connect()
    dm._devices["dev1"] = driver

    with pytest.raises(DeviceSettingValueError, match="at most 100"):
        await dm.set_device_setting("dev1", "brightness", 999)
    assert driver.writes == []  # nothing reached the driver

    # A valid write is coerced (string → int) and goes through.
    await dm.set_device_setting("dev1", "brightness", "75")
    assert driver.writes == [("brightness", 75)]


async def test_pending_settings_validated_at_intake(core):
    state, events = core
    dm = DeviceManager(state, events)
    driver = _SettingsDriver("dev2", {}, state, events)
    dm._devices["dev2"] = driver
    dm._device_configs["dev2"] = {}

    with pytest.raises(DeviceSettingValueError, match="Unknown device setting"):
        await dm.store_pending_settings("dev2", {"brightnes": 50})
    with pytest.raises(DeviceSettingValueError, match="at most 100"):
        await dm.store_pending_settings("dev2", {"brightness": 200})

    await dm.store_pending_settings("dev2", {"brightness": "25"})
    assert dm._device_configs["dev2"]["pending_settings"] == {"brightness": 25}


async def test_pending_apply_failure_emits_device_error(core):
    """A queued setting the device rejects is retried, but no longer only
    warn-logged — a device.error event surfaces it."""
    state, events = core
    dm = DeviceManager(state, events)
    driver = _SettingsDriver("dev3", {}, state, events)
    await driver.connect()

    async def failing_set(key, value):
        raise RuntimeError("device rejected the value")

    driver.set_device_setting = failing_set  # type: ignore[assignment]
    dm._devices["dev3"] = driver
    dm._device_configs["dev3"] = {"pending_settings": {"brightness": 50}}

    received: list[dict] = []
    events.on("device.error.dev3", lambda name, payload: received.append(payload))

    await dm._apply_pending_settings("dev3")

    assert received and "brightness" in received[0]["error"]
    # The key stays queued for the next reconnect.
    assert dm._device_configs["dev3"]["pending_settings"] == {"brightness": 50}


# ── child_id param coercion (platform-side, was per-driver folklore) ─────────


class _ChildDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_pdu",
        "name": "Acme PDU",
        "transport": "tcp",
        "state_variables": {},
        "commands": {
            "outlet_on": {
                "label": "Outlet On",
                "params": {
                    "outlet": {"type": "child_id", "child_type": "outlet", "required": True},
                },
            },
            "rename_scene": {
                "label": "Rename Scene",
                "params": {
                    "scene": {"type": "child_id", "child_type": "scene", "required": True},
                },
            },
        },
        "child_entity_types": {
            "outlet": {
                "label": "Outlet",
                "id_format": {"type": "integer", "min": 1, "max": 8, "pad_width": 3},
                "state_variables": {"state": {"type": "boolean"}},
            },
            "scene": {
                "label": "Scene",
                "id_format": {"type": "string"},
                "state_variables": {"name": {"type": "string"}},
            },
        },
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.seen: list[tuple[str, dict]] = []

    async def connect(self):
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self):
        self._connected = False

    async def send_command(self, command, params=None):
        self.seen.append((command, dict(params or {})))
        return True


async def test_child_id_param_coerced_to_int_for_integer_types(core):
    """The UI sends the padded string ("003"); an integer-id child type gets
    a real int — the platform now does what the docs always claimed."""
    state, events = core
    dm = DeviceManager(state, events)
    driver = _ChildDriver("pdu1", {}, state, events)
    await driver.connect()
    dm._devices["pdu1"] = driver

    await dm.send_command("pdu1", "outlet_on", {"outlet": "003"})
    assert driver.seen == [("outlet_on", {"outlet": 3})]

    # String-id child types pass through untouched.
    await dm.send_command("pdu1", "rename_scene", {"scene": "Main_Gain"})
    assert driver.seen[-1] == ("rename_scene", {"scene": "Main_Gain"})


async def test_child_id_param_junk_rejected_with_actionable_error(core):
    from server.drivers.base import CommandParamError

    state, events = core
    dm = DeviceManager(state, events)
    driver = _ChildDriver("pdu2", {}, state, events)
    await driver.connect()
    dm._devices["pdu2"] = driver

    with pytest.raises(CommandParamError, match="child id number"):
        await dm.send_command("pdu2", "outlet_on", {"outlet": "left-one"})
    assert driver.seen == []
