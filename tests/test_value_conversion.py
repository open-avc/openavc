"""Value conversion: scale / offset / unknown on a YAML driver.

A device that packs a real value into its own number (gain sent as 000-060
for -18..+42 dB, a level in hundredths of a dB, 255 for "no reading")
declares the conversion on the state variable that holds the value and on
the command parameter that sets it. The runtime converts what it reads and
what it sends, the simulator holds real values and speaks the device's
numbers, and the validator keeps the keys where they work.

Invented device ("acme_receiver") and synthetic payloads throughout.
"""

import copy

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.compiled_protocol import (
    compile_driver,
    reading_from_wire,
    value_to_wire,
)
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition
from openavc.drivers.python_info import python_driver_info_issues
from openavc.drivers.spec import platform_requirements
from openavc.simulator.yaml_auto import YAMLAutoSimulator

RECEIVER = {
    "id": "acme_receiver",
    "name": "Acme Receiver",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {
        "gain": {
            "type": "integer", "label": "Gain", "unit": "dB",
            "min": -18, "max": 42, "offset": -18,
        },
        "level": {"type": "number", "label": "Level", "unit": "dB", "scale": 0.01},
        "battery_temp": {
            "type": "integer", "label": "Battery Temperature",
            "offset": -40, "unknown": [255],
        },
        "battery_minutes": {
            "type": "integer", "label": "Battery Minutes",
            "unknown": [65533, 65534, 65535],
        },
    },
    "child_entity_types": {
        "channel": {
            "label": "Channel",
            "id_format": {"type": "integer", "min": 1, "max": 4},
            "state_variables": {
                "gain": {"type": "integer", "label": "Gain", "offset": -18},
            },
            "instances": {"count": 2, "label": "Channel {id}"},
        },
    },
    "commands": {
        "set_gain": {
            "label": "Set Gain",
            "send": "SET GAIN {gain}\\r",
            "params": {"gain": {"type": "integer", "min": -18, "max": 42, "offset": -18}},
        },
        "set_gain_padded": {
            "label": "Set Gain (padded)",
            "send": "SET GAINP {gain:03d}\\r",
            "params": {"gain": {"type": "integer", "offset": -18}},
        },
        "set_level": {
            "label": "Set Level",
            "send": "SET LEVEL {level}\\r",
            "params": {"level": {"type": "number", "scale": 0.01}},
        },
        "set_channel_gain": {
            "label": "Set Channel Gain",
            "send": "SET {channel} GAIN {gain}\\r",
            "params": {
                "channel": {"type": "child_id", "child_type": "channel"},
                "gain": {"type": "integer", "offset": -18},
            },
        },
    },
    "responses": [
        {"match": r"^REP GAIN (\d+)$", "set": {"gain": "$1"}},
        {"match": r"^REP LEVEL (-?\d+)$", "set": {"level": "$1"}},
        {"match": r"^REP BATT_TEMP (\d+)$", "set": {"battery_temp": "$1"}},
        {"match": r"^REP BATT_MINS (\d+)$", "set": {"battery_minutes": "$1"}},
        {
            "match": r"^REP BATT_TEMP_C (\w+)$",
            "mappings": [
                {"group": 1, "state": "battery_temp", "map": {"COLD": -40}},
            ],
        },
        {
            "match": r"^REP (\d) GAIN (\d+)$",
            "child_set": [{"type": "channel", "id": "$1", "state": {"gain": "$2"}}],
        },
        {"json": True, "set": {"level": {"key": "level"}}},
    ],
    "device_settings": {
        "gain": {
            "type": "integer",
            "label": "Gain",
            "write": {"send": "SET GAIN {value}\\r"},
        },
    },
}


class FakeTransport:
    connected = True

    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _driver(definition=RECEIVER):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 23}, state, events)
    driver._register_declared_children()
    driver.transport = FakeTransport()
    return driver


# ── The two functions every interpreter converts through ──


def test_reading_and_writing_are_inverse():
    gain = {"offset": -18}
    assert reading_from_wire("012", gain, "integer") == (True, -6)
    assert value_to_wire(-6, gain) == 12
    level = {"scale": 0.01}
    assert reading_from_wire("-776", level, "number") == (True, -7.76)
    # 7.76 / 0.01 is 775.9999999999999 in floating point; the device gets 776.
    assert value_to_wire(-7.76, level) == -776


def test_unknown_matches_by_value_and_a_string_exactly():
    decl = {"offset": -40, "unknown": [255, "N/A"]}
    assert reading_from_wire("255", decl, "integer") == (True, None)
    assert reading_from_wire("0255", decl, "integer") == (True, None)
    assert reading_from_wire("N/A", decl, "integer") == (True, None)
    assert reading_from_wire("n/a", decl, "integer") == (False, None)
    assert value_to_wire(None, decl) == 255


def test_nothing_declared_or_not_a_number_is_left_to_the_caller():
    assert reading_from_wire("12", {}, "integer") == (False, None)
    assert reading_from_wire("12", None, "integer") == (False, None)
    assert reading_from_wire("abc", {"offset": -18}, "integer") == (False, None)
    assert value_to_wire("abc", {"offset": -18}) == "abc"
    assert value_to_wire(5, {"unknown": [255]}) == 5


def test_a_whole_number_rounds_half_away_from_zero():
    # 2.5 / 1 would round to 2 under Python's round(); a device expects 3.
    assert value_to_wire(2.5, {"offset": 0}) == 3
    assert value_to_wire(-2.5, {"offset": 0}) == -3
    assert reading_from_wire("5", {"scale": 0.5}, "integer") == (True, 3)


# ── Reading: every rule kind ──


@pytest.mark.asyncio
async def test_a_text_rule_stores_the_real_value():
    driver = _driver()
    await driver.on_data_received(b"REP GAIN 012\r")
    assert driver.get_state("gain") == -6
    await driver.on_data_received(b"REP LEVEL -776\r")
    assert driver.get_state("level") == -7.76


@pytest.mark.asyncio
async def test_an_unknown_code_stores_no_reading():
    driver = _driver()
    await driver.on_data_received(b"REP BATT_TEMP 065\r")
    assert driver.get_state("battery_temp") == 25
    await driver.on_data_received(b"REP BATT_TEMP 255\r")
    assert driver.get_state("battery_temp") is None
    # unknown alone, with no scale or offset, still clears the reading.
    await driver.on_data_received(b"REP BATT_MINS 00125\r")
    assert driver.get_state("battery_minutes") == 125
    await driver.on_data_received(b"REP BATT_MINS 65534\r")
    assert driver.get_state("battery_minutes") is None


@pytest.mark.asyncio
async def test_a_rule_map_wins_and_is_not_converted():
    driver = _driver()
    await driver.on_data_received(b"REP BATT_TEMP_C COLD\r")
    assert driver.get_state("battery_temp") == -40  # not -80
    await driver.on_data_received(b"REP BATT_TEMP_C 065\r")
    assert driver.get_state("battery_temp") == 25


@pytest.mark.asyncio
async def test_a_child_rule_stores_the_real_value():
    driver = _driver()
    await driver.on_data_received(b"REP 2 GAIN 030\r")
    assert driver.state.get("device.dev1.channel.2.gain") == 12


def test_a_json_rule_stores_the_real_value():
    driver = _driver()
    assert driver._apply_json_responses('{"level": -776}') is True
    assert driver.get_state("level") == -7.76


@pytest.mark.asyncio
async def test_an_osc_rule_stores_the_real_value():
    from openavc.transport.osc_codec import osc_encode_message

    definition = {
        "id": "acme_mixer",
        "name": "Acme Mixer",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "transport": "osc",
        "default_config": {"host": "", "port": 10023},
        "state_variables": {
            "trim": {"type": "number", "label": "Trim", "scale": 0.5, "offset": -12},
        },
        "commands": {},
        "responses": [
            {"address": "/acme/trim", "mappings": [{"arg": 0, "state": "trim"}]},
        ],
    }
    driver = _driver(definition)
    await driver._handle_osc_response(osc_encode_message("/acme/trim", [("i", 30)]))
    assert driver.get_state("trim") == 3.0


def test_compiled_rules_carry_the_conversion_without_touching_the_definition():
    definition = copy.deepcopy(RECEIVER)
    compiled = compile_driver(definition, {})
    gain_rule = compiled.responses[0][1][0]
    assert gain_rule["convert"] == {"offset": -18}
    child_prop = compiled.responses[5][2][0]["props"][0]
    assert child_prop["convert"] == {"offset": -18}
    # The definition's own dicts are shared by every device on the driver.
    assert definition == RECEIVER


# ── Writing ──


@pytest.mark.asyncio
async def test_a_command_sends_the_device_number():
    driver = _driver()
    await driver.send_command("set_gain", {"gain": -6})
    assert driver.transport.sent[-1] == b"SET GAIN 12\r"
    await driver.send_command("set_gain_padded", {"gain": -6})
    assert driver.transport.sent[-1] == b"SET GAINP 012\r"
    await driver.send_command("set_level", {"level": -7.76})
    assert driver.transport.sent[-1] == b"SET LEVEL -776\r"


@pytest.mark.asyncio
async def test_min_and_max_stay_in_real_units():
    from openavc.drivers.base import CommandParamError

    driver = _driver()
    await driver.send_command("set_gain", {"gain": 42})
    assert driver.transport.sent[-1] == b"SET GAIN 60\r"
    with pytest.raises(CommandParamError):
        await driver.send_command("set_gain", {"gain": 43})


@pytest.mark.asyncio
async def test_a_child_command_converts_only_the_converted_param():
    driver = _driver()
    await driver.send_command("set_channel_gain", {"channel": 2, "gain": 0})
    assert driver.transport.sent[-1] == b"SET 2 GAIN 18\r"


@pytest.mark.asyncio
async def test_a_device_setting_writes_through_its_state_variable():
    driver = _driver()
    await driver.set_device_setting("gain", -6)
    assert driver.transport.sent[-1] == b"SET GAIN 12\r"


# ── The simulator holds real values and speaks the device's numbers ──


def _sim(definition=RECEIVER):
    return YAMLAutoSimulator(device_id="dev1", config={}, driver_def=definition)


def test_the_simulator_stores_a_command_as_the_real_value():
    sim = _sim()
    reply = sim.handle_command(b"SET GAIN 12")
    assert sim._state.get("gain") == -6
    assert reply is not None and b"REP GAIN 12" in reply


def test_the_simulator_clamps_in_real_units():
    sim = _sim()
    sim.handle_command(b"SET GAIN 70")  # raw 70 is +52 dB, above max 42
    assert sim._state.get("gain") == 42


def test_the_simulator_reads_a_negative_device_number():
    sim = _sim()
    sim.handle_command(b"SET LEVEL -776")
    assert sim._state.get("level") == -7.76


def test_the_simulator_converts_a_setting_write():
    definition = copy.deepcopy(RECEIVER)
    definition["device_settings"]["gain"]["write"]["send"] = "GAIN={value}\\r"
    sim = _sim(definition)
    assert sim.handle_command(b"GAIN=30") is not None
    assert sim._state.get("gain") == 12


def test_the_simulator_reports_no_reading_as_the_first_unknown_code():
    sim = _sim()
    assert sim._format_state_reply("battery_temp", None) == "REP BATT_TEMP 255"
    assert sim._format_state_reply("battery_temp", 25) == "REP BATT_TEMP 65"


def test_the_simulator_child_reply_carries_the_device_number():
    sim = _sim()
    reply = sim.handle_command(b"SET 2 GAIN 30")
    assert sim._state.get("channel.2.gain") == 12
    assert reply is not None and b"REP 2 GAIN 30" in reply


def test_the_simulator_pads_a_fixed_width_reply_like_the_device():
    # A device that always sends three digits sends 030, never 30, and a
    # driver reading it with (\d{3}) rejects the shorter form.
    definition = copy.deepcopy(RECEIVER)
    definition["responses"][0]["match"] = r"^REP GAIN (\d{3})$"
    definition["responses"][3]["match"] = r"^REP BATT_MINS (\d{5})$"
    definition["responses"][5]["match"] = r"^REP (\d) GAIN (\d{3})$"
    sim = _sim(definition)
    reply = sim.handle_command(b"SET GAIN 12")
    assert reply is not None and b"REP GAIN 012" in reply
    assert sim._state.get("gain") == -6
    assert sim._format_state_reply("battery_minutes", 125) == "REP BATT_MINS 00125"
    assert sim._format_state_reply("battery_minutes", None) == "REP BATT_MINS 65533"
    reply = sim.handle_command(b"SET 2 GAIN 4")
    assert reply is not None and b"REP 2 GAIN 004" in reply
    assert sim._state.get("channel.2.gain") == -14


@pytest.mark.asyncio
async def test_a_value_goes_out_in_the_form_of_the_first_rule_that_reads_it():
    # A rule for the device's codes comes first, because the driver takes the
    # first rule that matches and the general rule would read a code as a
    # count. A count must still go out padded, the way the general rule reads
    # it, and a code the way the codes rule does: on a flat and a child
    # variable, and read back by the driver.
    definition = copy.deepcopy(RECEIVER)
    definition["child_entity_types"]["channel"]["state_variables"]["minutes"] = {
        "type": "integer", "label": "Minutes", "unknown": [65533, 65534, 65535],
    }
    definition["responses"][3:4] = [
        {"match": r"^REP BATT_MINS (6553[3-5])$", "set": {"battery_minutes": "$1"}},
        {"match": r"^REP BATT_MINS (\d{5})$", "set": {"battery_minutes": "$1"}},
        {
            "match": r"^REP (\d) MINS (6553[3-5])$",
            "child_set": [{"type": "channel", "id": "$1", "state": {"minutes": "$2"}}],
        },
        {
            "match": r"^REP (\d) MINS (\d{5})$",
            "child_set": [{"type": "channel", "id": "$1", "state": {"minutes": "$2"}}],
        },
    ]
    sim = _sim(definition)
    assert sim._format_state_reply("battery_minutes", 125) == "REP BATT_MINS 00125"
    assert sim._format_state_reply("battery_minutes", None) == "REP BATT_MINS 65533"
    assert sim._format_state_reply("channel.2.minutes", 40) == "REP 2 MINS 00040"
    assert sim._format_state_reply("channel.2.minutes", None) == "REP 2 MINS 65533"

    driver = _driver(definition)
    for line in ("REP BATT_MINS 00125", "REP 2 MINS 00040"):
        await driver.on_data_received(line.encode())
    assert driver.state.get("device.dev1.battery_minutes") == 125
    assert driver.state.get("device.dev1.channel.2.minutes") == 40


def test_a_script_handler_reports_state_in_the_device_numbers():
    # A "report everything" request answered by a handler: reply(key) is the
    # text a query of that key gets, converted and padded, so the handler
    # never repeats a conversion.
    definition = copy.deepcopy(RECEIVER)
    definition["responses"][0]["match"] = r"^REP GAIN (\d{3})$"
    definition["responses"][5]["match"] = r"^REP (\d) GAIN (\d{3})$"
    definition["simulator"] = {
        "initial_state": {"gain": -6, "channel.1.gain": 0, "battery_temp": None},
        "command_handlers": [{
            "match": "GET ALL",
            "handler": (
                "keys = ['gain', 'channel.1.gain', 'battery_temp', 'nothing']\n"
                "respond(''.join(reply(k) + '\\r' for k in keys if reply(k)))\n"
            ),
        }],
    }
    sim = _sim(definition)
    assert sim.handle_command(b"GET ALL") == b"REP GAIN 012\rREP 1 GAIN 018\rREP BATT_TEMP 255\r"


async def test_the_driver_reads_the_padded_reply_it_is_answered_with():
    definition = copy.deepcopy(RECEIVER)
    definition["responses"][0]["match"] = r"^REP GAIN (\d{3})$"
    sim = _sim(definition)
    driver = _driver(definition)
    await driver.on_data_received(sim.handle_command(b"SET GAIN 30").rstrip(b"\r"))
    assert driver.get_state("gain") == 12


# ── Validation ──


def test_the_example_driver_is_valid():
    assert validate_driver_definition(RECEIVER) == []


def _with(path, value):
    definition = copy.deepcopy(RECEIVER)
    node = definition
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return definition


def test_scale_on_a_string_variable_is_refused():
    definition = _with(
        ("state_variables", "label"),
        {"type": "string", "label": "Label", "scale": 2},
    )
    errors = validate_driver_definition(definition)
    assert any("the type must be integer, number or float to use scale" in e for e in errors), errors


def test_a_zero_scale_is_refused():
    definition = _with(("state_variables", "level", "scale"), 0)
    errors = validate_driver_definition(definition)
    assert any("scale cannot be 0" in e for e in errors), errors


def test_scale_on_an_action_param_is_refused():
    definition = _with(
        ("actions",),
        [{
            "id": "set_gain",
            "kind": "command",
            "params": {"gain": {"type": "integer", "offset": -18}},
        }],
    )
    errors = validate_driver_definition(definition)
    assert any("an action's parameters are not converted" in e for e in errors), errors


def test_a_python_driver_is_refused_the_keys():
    info = {
        "id": "acme_receiver",
        "state_variables": {"gain": {"type": "integer", "label": "Gain", "offset": -18}},
        "commands": {
            "set_gain": {"params": {"gain": {"type": "integer", "scale": 1}}},
        },
    }
    issues = python_driver_info_issues(info)
    assert any("State variable 'gain': remove offset;" in i for i in issues), issues
    assert any("param 'gain': remove scale;" in i for i in issues), issues


def test_using_the_keys_needs_the_platform_that_understands_them():
    floors = dict(platform_requirements(RECEIVER))
    assert floors["state_variables.gain.offset"] == "0.37.0"
    assert floors["state_variables.battery_temp.unknown"] == "0.37.0"
    assert floors["commands.set_level.params.level.scale"] == "0.37.0"
