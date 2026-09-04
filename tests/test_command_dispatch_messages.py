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


# --- a command that did something and could not finish -----------------------
#
# The generic wrapper says "Failed to send command 'x'", which is right when
# nothing happened and actively wrong when something did. A PoE power-cycle
# that cuts the port its own control session runs over restores power from a
# finally block that now has no transport -- the endpoint stays dark, and the
# operator who just watched it power down is told the command failed.

def test_partial_error_is_not_a_value_error():
    """It must not land in the device-not-found branch."""
    from openavc.drivers.base import CommandPartialError

    assert not issubclass(CommandPartialError, ValueError)
    assert issubclass(CommandPartialError, RuntimeError)


def test_partial_error_is_not_a_connection_error():
    """503 'not connected' would be wrong too -- the device answered fine
    right up until the command cut the path."""
    from openavc.drivers.base import CommandPartialError

    assert not issubclass(CommandPartialError, ConnectionError)


@pytest.mark.asyncio
async def test_the_route_surfaces_the_drivers_own_sentence(monkeypatch):
    """The message names what was left in what state, so it has to survive.

    Everything else on this path is deliberately replaced with a generic
    sentence (api_error logs the exception rather than returning it). This one
    is the exception, so a test has to hold it there.
    """
    from fastapi import HTTPException

    from openavc.api.routes import devices as devices_route
    from openavc.drivers.base import CommandPartialError

    told = ("PoE was cut on Ethernet1/0/7 and the restore could not be "
            "confirmed. That port may still be disabled.")

    class _Devices:
        async def send_command(self, *_a, **_k):
            raise CommandPartialError(told)

    class _Engine:
        devices = _Devices()

    monkeypatch.setattr(devices_route, "_get_engine", lambda: _Engine())

    body = devices_route.CommandRequest(command="poe_cycle_port", params={})
    with pytest.raises(HTTPException) as caught:
        await devices_route.send_command("switch_1", body)

    # 502, not 500: the driver's own sentence is the answer, and the API
    # convention reserves 500 for wording of ours with the exception logged.
    assert caught.value.status_code == 502
    assert caught.value.detail == told
    # The generic wrapper must NOT be what the operator sees.
    assert "Failed to send command" not in caught.value.detail


# ── A number that is not a number: NaN and infinity ─────────────────────────
#
# `float("NaN")` succeeds, and every comparison with NaN is False -- so NaN
# cleared `num < min` and `num > max` alike and went out on the wire. Observed
# on connected hardware: `set_gain` with `"NaN"` answered `success: true` and
# left the reading undefined. Infinity was caught only where a `max` happened
# to be declared, because `inf > max` IS True; a param with no max took it.
#
# The integer path was worse than a wrong value: the `f != int(f)` check runs
# outside the try, and `int(float("nan"))` raises ValueError while
# `int(float("inf"))` raises OverflowError -- so a non-finite value on an
# integer param came back as `Device 'x' not found` (the REST layer maps a bare
# ValueError that way) or as a 500. Both are answers about the wrong thing.


@pytest.mark.parametrize(
    "value", ["NaN", "nan", "-nan", float("nan"), "inf", "Infinity", "-Infinity",
              float("inf"), float("-inf")],
)
def test_a_number_param_refuses_a_value_that_is_not_a_number(value):
    """Whatever the bounds are, and whether or not there are any."""
    with pytest.raises(CommandParamError) as caught:
        normalize_and_validate_command_params(
            "set_gain",
            {"gain": {"type": "number", "min": -80, "max": 20}},
            {"gain": value},
        )
    assert "must be a number" in str(caught.value)

    # And with no max at all, which is where infinity used to get through.
    with pytest.raises(CommandParamError):
        normalize_and_validate_command_params(
            "ramp_to", {"ramp": {"type": "number"}}, {"ramp": value},
        )


@pytest.mark.parametrize("value", ["NaN", float("nan"), "inf", float("-inf")])
def test_an_integer_param_refuses_it_as_a_value_rather_than_crashing(value):
    """The refusal has to be a CommandParamError, not whatever `int()` raises.

    A ValueError out of here is answered `Device 'x' not found` by the REST
    door -- the single most misdirecting thing it could say about a device
    that is connected and rendering -- and an OverflowError misses even that
    branch and lands in the catch-all as a 500.
    """
    with pytest.raises(CommandParamError) as caught:
        normalize_and_validate_command_params(
            "route", {"input": {"type": "integer", "min": 1, "max": 8}},
            {"input": value},
        )
    assert "must be a whole number" in str(caught.value)


def test_the_numbers_that_are_numbers_still_pass():
    """The guard must not cost the ordinary values anything."""
    out = normalize_and_validate_command_params(
        "set_gain",
        {"gain": {"type": "number", "min": -80, "max": 20}, "chan": {"type": "integer"}},
        {"gain": -6.5, "chan": 5.0},
    )
    assert out == {"gain": -6.5, "chan": 5}
    # The bounds themselves, which NaN used to clear from both sides.
    for bad, why in ((-81, "at least"), (21, "at most")):
        with pytest.raises(CommandParamError, match=why):
            normalize_and_validate_command_params(
                "set_gain", {"gain": {"type": "number", "min": -80, "max": 20}},
                {"gain": bad},
            )


# ── A required param left blank ─────────────────────────────────────────────
#
# The required check read `is None`, so a blank passed it; the trim-then-skip
# below then dropped the value with no type check and no range check, and
# whatever the driver did with a missing required value came back as success.
# Measured on real hardware: `set_input_level` with `level: ""` answered
# HTTP 200 `{"success": true}` while the input stayed where it was.


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
@pytest.mark.parametrize(
    ("ptype", "extra"),
    [("number", {}), ("integer", {}), ("child_id", {"child_type": "zone"}),
     ("enum", {"values": ["a", "b"]}), ("boolean", {})],
)
def test_a_blank_is_not_a_value_for_anything_that_names_a_quantity(ptype, extra, blank):
    with pytest.raises(CommandParamError) as caught:
        normalize_and_validate_command_params(
            "set_level",
            {"level": {"type": ptype, "required": True, **extra}},
            {"level": blank},
        )
    assert str(caught.value) == "'set_level': 'level' is required"


def test_a_blank_string_is_still_a_value_and_still_clears_a_name():
    """Deliberately unchanged. For free text the platform cannot tell a blank
    somebody meant from a blank somebody left, and clearing a name by sending
    an empty one is a real thing a driver offers."""
    out = normalize_and_validate_command_params(
        "rename", {"name": {"type": "string", "required": True}}, {"name": ""},
    )
    assert out == {"name": ""}
    # A param that declares no type at all is a string param.
    assert normalize_and_validate_command_params(
        "rename", {"name": {"required": True}}, {"name": ""},
    ) == {"name": ""}


def test_a_blank_optional_is_left_exactly_as_it_was():
    """The skip this sits beside is for an optional left blank, and that is
    still what it is for -- an optional numeric left empty is not an error."""
    out = normalize_and_validate_command_params(
        "set_zone_level",
        {"zone": {"type": "child_id", "child_type": "zone", "required": True},
         "level": {"type": "number", "min": -80, "max": 10}},
        {"zone": 4, "level": ""},
    )
    assert out == {"zone": 4, "level": ""}


def test_whitespace_is_a_value_where_the_driver_says_it_is():
    """`trim: false` is declared for a payload whose edge whitespace is
    protocol-meaningful, so spaces there are content rather than nothing."""
    out = normalize_and_validate_command_params(
        "passthrough",
        {"payload": {"type": "string", "required": True, "trim": False}},
        {"payload": "  "},
    )
    assert out == {"payload": "  "}
