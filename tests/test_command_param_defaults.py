"""A command parameter's declared ``default`` is filled in at the runtime gate.

The Programmer's forms start from the default, so a panel button or macro step
built there already carries it. A script, a REST call or the AI assistant that
leaves the parameter out used to reach a YAML send template with the
placeholder still in it: Volume Up sent ``VOL+{step}`` to the device. The gate
every caller passes through (``normalize_and_validate_command_params``) now
gives an optional parameter its default, for both driver formats, and checks
it like a supplied value.

Every device, driver and command below is invented.
"""

from __future__ import annotations

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import (
    BaseDriver,
    CommandParamError,
    normalize_and_validate_command_params,
)
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition

VOLUME_UP = {
    "label": "Volume Up",
    "send": "VOL+{step}\r",
    "params": {
        "step": {"type": "integer", "min": 1, "max": 10, "default": 2, "label": "Step"},
    },
}

ACME_WIDGET = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\r",
    "default_config": {},
    "state_variables": {},
    "commands": {
        "volume_up": VOLUME_UP,
        "set_input": {
            "label": "Set Input",
            "send": "IN{input}\r",
            "params": {
                "input": {
                    "type": "enum", "values": ["hdmi1", "hdmi2"], "default": "hdmi1",
                    "map": {"hdmi1": "1", "hdmi2": "2"}, "label": "Input",
                },
            },
        },
    },
}


class _Transport:
    connected = True

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _yaml_driver() -> object:
    assert validate_driver_definition(ACME_WIDGET) == []
    cls = create_configurable_driver_class(ACME_WIDGET)
    driver = cls("widget_1", {"host": "127.0.0.1", "port": 4000}, StateStore(), EventBus())
    driver.transport = _Transport()
    return driver


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [None, {}, {"step": None}, {"step": ""}])
async def test_a_yaml_send_template_gets_the_default(params) -> None:
    driver = _yaml_driver()
    await driver.send_command("volume_up", params)
    assert driver.transport.sent == [b"VOL+2\r"]


@pytest.mark.asyncio
async def test_a_supplied_value_still_wins() -> None:
    driver = _yaml_driver()
    await driver.send_command("volume_up", {"step": 5})
    assert driver.transport.sent == [b"VOL+5\r"]


@pytest.mark.asyncio
async def test_a_default_goes_through_the_wire_map() -> None:
    """Filled in as the user-facing value, then translated like one."""
    driver = _yaml_driver()
    await driver.send_command("set_input")
    assert driver.transport.sent == [b"IN1\r"]


class _AcmePython(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_python",
        "name": "Acme Python",
        "transport": "tcp",
        "state_variables": {},
        "commands": {"volume_up": {"label": "Volume Up", "params": VOLUME_UP["params"]}},
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.seen: list[dict | None] = []

    async def connect(self) -> bool:
        self.set_state("connected", True)
        return True

    async def disconnect(self) -> None:
        self.set_state("connected", False)

    async def send_command(self, command, params=None):
        self.seen.append(params)
        return True


@pytest.mark.asyncio
async def test_a_python_driver_gets_the_default_through_the_device_manager() -> None:
    state, events = StateStore(), EventBus()
    dm = DeviceManager(state, events)
    driver = _AcmePython("widget_1", {}, state, events)
    await driver.connect()
    dm._devices["widget_1"] = driver
    await dm.send_command("widget_1", "volume_up")
    await dm.send_command("widget_1", "volume_up", {"step": 7})
    assert driver.seen == [{"step": 2}, {"step": 7}]


# --- The gate itself ---


def test_a_required_param_with_a_default_is_still_refused_when_left_out() -> None:
    """The default on a required param is where the Programmer's form starts;
    leaving the param out is refused, never quietly sent as that value."""
    defs = {"level": {"type": "number", "required": True, "default": 0.75}}
    with pytest.raises(CommandParamError, match="'set_level': 'level' is required"):
        normalize_and_validate_command_params("set_level", defs, {})


def test_a_blank_free_text_value_is_kept() -> None:
    """A blank is a value on free text (clearing a name), so it is not
    replaced; on any other type it is nothing, and the default fills it."""
    defs = {
        "name": {"type": "string", "default": "Room"},
        "level": {"type": "integer", "default": 3},
    }
    out = normalize_and_validate_command_params("rename", defs, {"name": "", "level": " "})
    assert out == {"name": "", "level": 3}


def test_a_default_is_checked_like_a_supplied_value() -> None:
    defs = {"step": {"type": "integer", "max": 10, "default": 99}}
    with pytest.raises(CommandParamError, match="'step' must be at most 10, got 99"):
        normalize_and_validate_command_params("volume_up", defs, None)


def test_nothing_to_fill_leaves_none_as_none() -> None:
    """A handler written for ``params is None`` sees it as before when the
    command declares no default."""
    defs = {"step": {"type": "integer"}}
    assert normalize_and_validate_command_params("volume_up", defs, None) is None


def test_the_callers_dict_is_not_changed() -> None:
    supplied: dict = {}
    out = normalize_and_validate_command_params("volume_up", VOLUME_UP["params"], supplied)
    assert out == {"step": 2}
    assert supplied == {}
