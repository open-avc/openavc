"""What the dispatch gate says when the *driver* is wrong, not the value.

Three of these faults already had a precise message on a neighbouring path and
a misleading one on the path an author's typo actually takes:

  * a command name the driver never declares reported ``Device 'x' not found``
    over REST, on a device that was connected and rendering — because
    ``DeviceManager.send_command`` raises a plain ValueError for "no such
    device" and a Python driver's handler usually raises one too. A YAML
    driver was quieter still: it logged ``Unknown command`` and reported
    success. Meanwhile an undeclared *action id* said exactly the right thing.
  * a ``child_type`` naming a type the driver never defined fell through to
    integer coercion and blamed the value the user typed
    (``'component' must be a child id number, got 'Pgm_Gain'``).
  * a missing ``required`` param produced the generic
    ``Failed to send command 'route'`` wrapper, while the min/max check on the
    same command answered ``'route': 'output' must be at most 8, got 99``.

Uses an invented device (Acme) and synthetic params throughout — this is the
platform's dispatch behaviour, not any product's.
"""

from __future__ import annotations

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import (
    child_param_reference_errors,
    undeclared_child_type_reason,
)
from openavc.drivers.base import (
    BaseDriver,
    CommandParamError,
    UnknownCommandError,
    normalize_and_validate_command_params,
)

_CHILD_TYPES = {
    "zone": {
        "label": "Zone",
        "id_format": {"type": "integer", "min": 1, "max": 32},
        "state_variables": {"level": {"type": "number"}},
    },
    "block": {
        "label": "Block",
        "id_format": {"type": "string"},
        "state_variables": {"gain": {"type": "number"}},
    },
}

_COMMANDS = {
    "power_on": {"label": "Power On"},
    "route": {
        "label": "Route",
        "params": {
            "input": {"type": "integer", "min": 1, "max": 8, "required": True},
            "output": {"type": "integer", "min": 1, "max": 8, "required": True},
            "note": {"type": "string"},
        },
    },
    "set_zone_level": {
        "label": "Set Zone Level",
        "params": {
            "zone": {"type": "child_id", "child_type": "zone", "required": True},
            "level": {"type": "number", "min": -80, "max": 10},
        },
    },
    "set_block_gain": {
        "label": "Set Block Gain",
        "params": {
            "block": {"type": "child_id", "child_type": "blck"},  # typo, on purpose
            "gain": {"type": "number"},
        },
    },
}


class _AcmeDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {},
        "child_entity_types": _CHILD_TYPES,
        "commands": _COMMANDS,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen: list[tuple[str, dict | None]] = []

    async def connect(self) -> bool:
        self.set_state("connected", True)
        return True

    async def disconnect(self) -> None:
        self.set_state("connected", False)

    async def send_command(self, command, params=None):
        self.seen.append((command, params))
        return True


class _NoCommandsDriver(_AcmeDriver):
    """The 20-of-92 shape: no literal command block, every name handled in
    code (discovered controls, channel strips, the inline-protocol generics)."""

    DRIVER_INFO = {**_AcmeDriver.DRIVER_INFO, "commands": {}}


async def _device(cls=_AcmeDriver, device_id="widget_1"):
    state, events = StateStore(), EventBus()
    dm = DeviceManager(state, events)
    driver = cls(device_id, {}, state, events)
    await driver.connect()
    dm._devices[device_id] = driver
    return dm, driver


# ── An undeclared command names the command, not the device ─────────────────


async def test_undeclared_command_names_the_command():
    dm, driver = await _device()
    with pytest.raises(UnknownCommandError) as exc:
        await dm.send_command("widget_1", "query_evrything")
    assert str(exc.value) == (
        "Command 'query_evrything' not found on device 'widget_1'"
    )
    assert driver.seen == [], "the typo must not reach the driver"


async def test_undeclared_command_is_a_valueerror_for_old_handlers():
    """Callers that predate the typed error still catch it."""
    dm, _ = await _device()
    with pytest.raises(ValueError):
        await dm.send_command("widget_1", "query_evrything")


async def test_missing_device_still_says_device():
    """The neighbouring message must not have moved: an id that names no
    device is still a device problem."""
    dm, _ = await _device()
    with pytest.raises(ValueError, match="Device 'nope' not found") as exc:
        await dm.send_command("nope", "power_on")
    assert not isinstance(exc.value, UnknownCommandError)


async def test_undeclared_command_refused_while_offline_too():
    """Checked before the connected-gate: the name is wrong either way, and
    'not connected' would send the author to look at the network."""
    dm, driver = await _device()
    await driver.disconnect()
    with pytest.raises(UnknownCommandError):
        await dm.send_command("widget_1", "query_evrything")
    # A *declared* command on an offline device still reports the connection.
    with pytest.raises(ConnectionError):
        await dm.send_command("widget_1", "power_on")


async def test_driver_declaring_no_commands_is_left_alone():
    """An empty set means 'this driver did not tell us', not 'it has none'."""
    dm, driver = await _device(_NoCommandsDriver, "widget_2")
    await dm.send_command("widget_2", "anything_at_all", {"x": 1})
    assert driver.seen == [("anything_at_all", {"x": 1})]


async def test_runtime_populated_commands_are_judged_on_the_instance():
    """A driver that builds its command set after connect is judged on what
    it actually declares by the time the command is sent, not on the class."""
    dm, driver = await _device(_NoCommandsDriver, "widget_3")
    driver.DRIVER_INFO = {
        **_NoCommandsDriver.DRIVER_INFO,
        "commands": {"discovered_gain": {"label": "Gain"}},
    }
    await dm.send_command("widget_3", "discovered_gain")
    assert driver.seen == [("discovered_gain", None)]
    with pytest.raises(UnknownCommandError, match="discovered_gian"):
        await dm.send_command("widget_3", "discovered_gian")


# ── A typo'd child_type blames the declaration, not the value ───────────────


async def test_undeclared_child_type_names_the_declaration():
    dm, driver = await _device()
    with pytest.raises(CommandParamError) as exc:
        await dm.send_command("widget_1", "set_block_gain", {"block": "Pgm_Gain"})
    assert str(exc.value) == (
        "'set_block_gain': 'block': child_type 'blck' is not a declared "
        "child_entity_type (declared: block, zone)"
    )
    assert "must be a child id number" not in str(exc.value)
    assert driver.seen == []


async def test_undeclared_child_type_wording_is_the_static_rule_s():
    """The catalog, the file checker, openavc.simulator.validate and the loader all
    report the static form of this fault; the gate meets it again at runtime
    because a driver copied into driver_repo/ only warns at load. One
    function produces the sentence, so the four doors cannot drift apart."""
    errors, _ = child_param_reference_errors(
        {
            "commands": {
                "set_block_gain": {
                    "params": {
                        "block": {"type": "child_id", "child_type": "blck"},
                    },
                },
            },
            "child_entity_types": _CHILD_TYPES,
        }
    )
    reason = undeclared_child_type_reason("blck", _CHILD_TYPES)
    assert errors == [f"commands.set_block_gain.params.block: {reason}"]
    assert reason.endswith("(declared: block, zone)")


def test_undeclared_child_type_reason_when_none_are_declared():
    assert undeclared_child_type_reason("zone", {}) == (
        "child_type 'zone' is not a declared child_entity_type "
        "(the driver declares none)"
    )


async def test_declared_child_type_still_coerces_and_range_checks():
    """The good paths this sits in front of are untouched."""
    dm, driver = await _device()
    await dm.send_command("widget_1", "set_zone_level", {"zone": "04", "level": -12})
    assert driver.seen[-1] == ("set_zone_level", {"zone": 4, "level": -12})

    with pytest.raises(CommandParamError, match="must be between 1 and 32, got 99"):
        await dm.send_command("widget_1", "set_zone_level", {"zone": 99})
    with pytest.raises(CommandParamError, match="must be a child id number"):
        await dm.send_command("widget_1", "set_zone_level", {"zone": "not_a_number"})


# ── A missing required param says which param ───────────────────────────────


async def test_missing_required_param_names_the_param():
    dm, driver = await _device()
    with pytest.raises(CommandParamError) as exc:
        await dm.send_command("widget_1", "route", {"input": 3})
    assert str(exc.value) == "'route': 'output' is required"
    assert driver.seen == []


async def test_required_param_checked_when_no_params_supplied_at_all():
    """The case the old early-return on falsy params could never reach."""
    dm, _ = await _device()
    for params in (None, {}):
        with pytest.raises(CommandParamError, match="'input' is required"):
            await dm.send_command("widget_1", "route", params)


async def test_explicit_null_counts_as_not_supplied():
    dm, _ = await _device()
    with pytest.raises(CommandParamError, match="'output' is required"):
        await dm.send_command("widget_1", "route", {"input": 1, "output": None})


async def test_supplied_required_params_pass():
    dm, driver = await _device()
    await dm.send_command("widget_1", "route", {"input": 3, "output": 7})
    assert driver.seen[-1] == ("route", {"input": 3, "output": 7})


async def test_optional_param_may_be_omitted():
    dm, driver = await _device()
    await dm.send_command("widget_1", "route", {"input": 1, "output": 2})
    assert "note" not in driver.seen[-1][1]


async def test_blank_string_is_a_supplied_value():
    """A blank is a value. This function has always treated an empty string as
    an optional left blank, and flipping that would newly reject clearing a
    name — a behaviour change the message fix does not need."""
    out = normalize_and_validate_command_params(
        "rename", {"name": {"type": "string", "required": True}}, {"name": ""}
    )
    assert out == {"name": ""}


def test_required_check_runs_before_the_value_checks():
    """So an omitted param is never reported as a bad value of some other."""
    with pytest.raises(CommandParamError, match="'output' is required"):
        normalize_and_validate_command_params(
            "route",
            {
                "input": {"type": "integer", "min": 1, "max": 8, "required": True},
                "output": {"type": "integer", "min": 1, "max": 8, "required": True},
            },
            {"input": 99},
        )


def test_no_declared_params_means_nothing_to_require():
    assert normalize_and_validate_command_params("power_on", {}, {}) == {}
    assert normalize_and_validate_command_params("power_on", {}, None) is None
