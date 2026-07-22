"""Tests for declarative child entities in YAML drivers.

Covers the three runtime pieces — `instances:` rosters (count / count_from /
ids_from / literal ids), `child_set:` response routing (regex-captured and
OSC address-segment ids, wire-id maps), and `each_child:` query expansion
(incl. {child_id:02d} format specs) — plus the loader validation for each,
using an invented matrix switcher and an invented OSC mixer.
"""

import asyncio

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition

ACME_MATRIX = {
    "id": "acme_matrix",
    "name": "Acme Matrix",
    "manufacturer": "Acme",
    "category": "switcher",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r\\n",
    "default_config": {
        "host": "",
        "port": 23,
        "output_count": 4,
        "zone_ids": "1,2,4",
        "enable_meters": False,
    },
    "config_schema": {
        "host": {"type": "string", "required": True, "label": "IP Address"},
        "port": {"type": "integer", "default": 23, "label": "Port"},
        "output_count": {"type": "integer", "default": 4, "label": "Outputs"},
        "zone_ids": {"type": "string", "default": "1,2,4", "label": "Zone IDs"},
        "enable_meters": {
            "type": "boolean",
            "default": False,
            "label": "Enable Level Meters",
        },
    },
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
    },
    "child_entity_types": {
        "output": {
            "label": "Output",
            "id_format": {"type": "integer", "min": 1, "max": 64, "pad_width": 2},
            "state_variables": {
                "input": {"type": "integer", "label": "Routed Input"},
                "mute": {"type": "boolean", "label": "Muted"},
            },
            "instances": {"count": 2, "label": "Output {id}"},
        },
        "zone": {
            "label": "Zone",
            "id_format": {"type": "integer", "min": 1, "max": 8},
            "state_variables": {
                "volume": {"type": "integer", "label": "Volume"},
            },
            "instances": {"ids_from": "zone_ids"},
        },
    },
    "commands": {
        "route": {
            "label": "Route",
            "send": "{input}*{output}!\\r\\n",
            "params": {
                "input": {"type": "integer", "required": True},
                "output": {
                    "type": "child_id",
                    "child_type": "output",
                    "required": True,
                },
            },
        },
    },
    "responses": [
        {
            "match": r"Out(\d+) In(\d+)",
            "child_set": [
                {"type": "output", "id": "$1", "state": {"input": "$2"}},
            ],
        },
        {
            "match": r"Status=(\d+),(\d+)",
            "child_set": [
                {"type": "output", "id": 1, "state": {"input": "$1"}},
                {"type": "output", "id": 2, "state": {"input": "$2", "mute": "false"}},
            ],
        },
        {
            "match": r"Zone(\d+)Vol(\d+)",
            "child_set": [
                {"type": "zone", "id": "$1", "state": {"volume": "$2"}},
            ],
        },
        {"match": r"Pwr(\d)", "set": {"power": "$1"}},
    ],
    "polling": {
        "queries": [
            "PWR?\\r\\n",
            {"each_child": "zone", "send": "VOL? {child_id}\\r\\n"},
        ],
    },
}


class FakeTransport:
    connected = True

    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _make_driver(definition=ACME_MATRIX, config=None):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    # DeviceManager merges default_config into the device config in
    # production; unit tests supply the merged dict directly.
    merged = {"host": "127.0.0.1", "port": 23, "zone_ids": "1,2,4"}
    if config is not None:
        merged = config
    return cls("dev1", merged, state, events)


# ---------------------------------------------------------------------------
# Roster registration (instances:)
# ---------------------------------------------------------------------------


def test_count_roster_registers_children():
    driver = _make_driver()
    counts = driver._register_declared_children()
    assert counts == {"output": 2, "zone": 3}
    assert driver.list_children("output") == [1, 2]
    # Padded state keys exist with schema defaults.
    assert driver.state.get("device.dev1.output.01.input") == 0
    assert driver.state.get("device.dev1.output.02.mute") is False


def test_label_template_seeds_label():
    driver = _make_driver()
    driver._register_declared_children()
    assert driver.state.get("device.dev1.output.01.label") == "Output 1"
    # No template on zone → platform default (empty label).
    assert driver.state.get("device.dev1.zone.1.label") == ""


def test_label_template_never_overrides_project_label():
    driver = _make_driver()
    driver.set_project_child_entities({"output": {"01": {"label": "Lobby TV"}}})
    driver._register_declared_children()
    assert driver.state.get("device.dev1.output.01.label") == "Lobby TV"
    assert driver.state.get("device.dev1.output.02.label") == "Output 2"


def test_ids_from_roster_sparse():
    driver = _make_driver()
    driver._register_declared_children()
    assert driver.list_children("zone") == [1, 2, 4]


def test_count_from_config_roster():
    definition = {
        **ACME_MATRIX,
        "child_entity_types": {
            "output": {
                **ACME_MATRIX["child_entity_types"]["output"],
                "instances": {"count_from": "output_count"},
            },
        },
    }
    driver = _make_driver(definition, {"host": "h", "output_count": 3})
    assert driver._register_declared_children() == {"output": 3}
    assert driver.list_children("output") == [1, 2, 3]


def test_reconcile_deregisters_unwanted():
    driver = _make_driver()
    driver._register_declared_children()
    assert driver.list_children("zone") == [1, 2, 4]
    driver.config["zone_ids"] = "1,2"
    counts = driver._register_declared_children()
    assert counts["zone"] == 2
    assert driver.list_children("zone") == [1, 2]
    assert driver.state.get("device.dev1.zone.4.volume") is None


async def test_refresh_children_reconciles():
    driver = _make_driver()
    counts = await driver.refresh_children()
    assert counts == {"output": 2, "zone": 3}


async def test_refresh_children_unsupported_without_instances():
    definition = dict(ACME_MATRIX)
    definition["child_entity_types"] = {
        "output": {
            "label": "Output",
            "state_variables": {"input": {"type": "integer", "label": "In"}},
        }
    }
    driver = _make_driver(definition)
    with pytest.raises(NotImplementedError):
        await driver.refresh_children()


def test_bad_count_from_value_warns_not_raises():
    definition = {
        **ACME_MATRIX,
        "child_entity_types": {
            "output": {
                **ACME_MATRIX["child_entity_types"]["output"],
                "instances": {"count_from": "output_count"},
            },
        },
    }
    driver = _make_driver(definition, {"host": "h", "output_count": "lots"})
    assert driver._register_declared_children() == {}


# ---------------------------------------------------------------------------
# Response routing (child_set:)
# ---------------------------------------------------------------------------


async def test_child_set_routes_by_captured_id():
    driver = _make_driver()
    driver._register_declared_children()
    await driver.on_data_received(b"Out2 In7")
    assert driver.state.get("device.dev1.output.02.input") == 7
    assert driver.state.get("device.dev1.output.01.input") == 0


async def test_child_set_literal_ids_combined_line():
    driver = _make_driver()
    driver._register_declared_children()
    await driver.on_data_received(b"Status=5,6")
    assert driver.state.get("device.dev1.output.01.input") == 5
    assert driver.state.get("device.dev1.output.02.input") == 6
    # Static value coerces by the child prop's declared boolean type.
    assert driver.state.get("device.dev1.output.02.mute") is False


async def test_child_set_coerces_by_child_prop_type():
    driver = _make_driver()
    driver._register_declared_children()
    await driver.on_data_received(b"Zone4Vol55")
    assert driver.state.get("device.dev1.zone.4.volume") == 55


async def test_child_set_unregistered_id_skipped():
    driver = _make_driver()
    driver._register_declared_children()
    await driver.on_data_received(b"Out9 In3")  # only outputs 1-2 registered
    assert driver.state.get("device.dev1.output.09.input") is None


async def test_flat_set_still_works_alongside_child_set():
    driver = _make_driver()
    driver._register_declared_children()
    await driver.on_data_received(b"Pwr1")
    assert driver.get_state("power") is True


# ---------------------------------------------------------------------------
# Query expansion (each_child:)
# ---------------------------------------------------------------------------


async def test_poll_expands_each_child_queries():
    driver = _make_driver()
    driver._register_declared_children()
    driver.transport = FakeTransport()
    await driver.poll()
    sent = [s.decode() for s in driver.transport.sent]
    assert sent == [
        "PWR?\r\n",
        "VOL? 1\r\n",
        "VOL? 2\r\n",
        "VOL? 4\r\n",
    ]


async def test_expand_query_empty_roster_sends_nothing():
    driver = _make_driver(config={"host": "h", "zone_ids": ""})
    driver._register_declared_children()
    assert driver._expand_query({"each_child": "zone", "send": "VOL? {child_id}\r\n"}) == []


# ---------------------------------------------------------------------------
# Config-gated queries (when:)
# ---------------------------------------------------------------------------

METER_QUERY = {
    "each_child": "zone",
    "send": "METER? {child_id}\r\n",
    "when": "enable_meters",
}


def _gated_driver(enable_meters):
    return _make_driver(
        config={
            "host": "h",
            "zone_ids": "1,2,4",
            "enable_meters": enable_meters,
        }
    )


async def test_when_gate_off_expands_to_nothing():
    driver = _gated_driver(False)
    driver._register_declared_children()
    assert driver._expand_query(METER_QUERY) == []


async def test_when_gate_on_expands_per_child():
    driver = _gated_driver(True)
    driver._register_declared_children()
    assert driver._expand_query(METER_QUERY) == [
        "METER? 1\r\n",
        "METER? 2\r\n",
        "METER? 4\r\n",
    ]


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),  # the string "false" is falsy here, not Python-truthy
        ("0", False),
        ("", False),
        (None, False),  # field missing from config
        ("239.0.0.100", True),  # a non-empty value gates on "is it configured"
    ],
)
async def test_when_gate_truthiness(value, expected):
    driver = _gated_driver(value)
    driver._register_declared_children()
    assert bool(driver._expand_query(METER_QUERY)) is expected


async def test_when_gates_a_plain_send_query():
    driver = _gated_driver(True)
    entry = {"send": "PSU?\r\n", "when": "enable_meters"}
    assert driver._expand_query(entry) == ["PSU?\r\n"]
    off = _gated_driver(False)
    assert off._expand_query(entry) == []


async def test_ungated_queries_always_run():
    driver = _gated_driver(False)
    driver._register_declared_children()
    driver.transport = FakeTransport()
    await driver.poll()
    # The driver's own (ungated) queries are untouched by an off gate.
    assert [s.decode() for s in driver.transport.sent] == [
        "PWR?\r\n",
        "VOL? 1\r\n",
        "VOL? 2\r\n",
        "VOL? 4\r\n",
    ]


# ---------------------------------------------------------------------------
# Throttle scoping on a child_set rule
# ---------------------------------------------------------------------------


def _throttled_meter_driver(window=60):
    """One throttled rule routing a meter to every zone — the shape a
    per-channel meter takes (one rule, N children)."""
    import copy

    d = copy.deepcopy(ACME_MATRIX)
    d["child_entity_types"]["zone"]["state_variables"]["meter"] = {
        "type": "integer",
        "label": "Meter",
    }
    d["responses"].insert(
        0,
        {
            "match": r"Meter(\d+) (\d+)",
            "throttle": window,
            "child_set": [{"type": "zone", "id": "$1", "state": {"meter": "$2"}}],
        },
    )
    return _make_driver(d)


async def test_throttled_child_rule_buckets_per_child():
    # One child must not consume the window on behalf of the others: a rule
    # serving N children would otherwise refresh only whichever arrived first.
    driver = _throttled_meter_driver()
    driver._register_declared_children()
    await driver.on_data_received(b"Meter1 11")
    await driver.on_data_received(b"Meter2 22")
    await driver.on_data_received(b"Meter4 44")
    assert driver.state.get("device.dev1.zone.1.meter") == 11
    assert driver.state.get("device.dev1.zone.2.meter") == 22
    assert driver.state.get("device.dev1.zone.4.meter") == 44


async def test_throttled_child_rule_still_caps_one_child():
    driver = _throttled_meter_driver()
    driver._register_declared_children()
    await driver.on_data_received(b"Meter1 11")
    await driver.on_data_received(b"Meter1 99")  # inside zone 1's window
    assert driver.state.get("device.dev1.zone.1.meter") == 11


async def test_throttled_child_rule_reopens_after_window():
    driver = _throttled_meter_driver(window=0.02)
    driver._register_declared_children()
    await driver.on_data_received(b"Meter1 11")
    await asyncio.sleep(0.05)
    await driver.on_data_received(b"Meter1 99")
    assert driver.state.get("device.dev1.zone.1.meter") == 99


# ---------------------------------------------------------------------------
# Loader validation
# ---------------------------------------------------------------------------


def _errors_for(mutate):
    import copy

    definition = copy.deepcopy(ACME_MATRIX)
    mutate(definition)
    return validate_driver_definition(definition)


def test_valid_definition_passes_loader():
    assert validate_driver_definition(ACME_MATRIX) == []


def test_loader_rejects_two_roster_sources():
    def mutate(d):
        d["child_entity_types"]["output"]["instances"] = {
            "count": 2,
            "ids_from": "zone_ids",
        }

    assert any("exactly one of" in e for e in _errors_for(mutate))


def test_loader_rejects_unknown_config_field():
    def mutate(d):
        d["child_entity_types"]["output"]["instances"] = {"count_from": "nope"}

    assert any("not a declared config field" in e for e in _errors_for(mutate))


def test_loader_accepts_when_gated_queries():
    def mutate(d):
        d["polling"]["queries"].append(METER_QUERY)
        d["on_connect"] = [{"send": "SUB PSU\r\n", "when": "enable_meters"}]

    assert _errors_for(mutate) == []


def test_loader_rejects_when_naming_unknown_config_field():
    def mutate(d):
        d["polling"]["queries"].append({**METER_QUERY, "when": "enabl_meters"})

    assert any(
        "'when' field 'enabl_meters' is not a declared config field" in e
        for e in _errors_for(mutate)
    )


def test_loader_rejects_non_string_when():
    def mutate(d):
        d["polling"]["queries"].append({**METER_QUERY, "when": True})

    assert any("'when' must name a config field" in e for e in _errors_for(mutate))


def test_loader_rejects_dict_query_that_is_neither_form():
    def mutate(d):
        d["polling"]["queries"].append({"when": "enable_meters"})  # no send

    assert any("{each_child, send} or {send, when}" in e for e in _errors_for(mutate))


def test_loader_rejects_count_over_id_max():
    def mutate(d):
        d["child_entity_types"]["output"]["instances"] = {"count": 99}

    assert any("exceeds id_format.max" in e for e in _errors_for(mutate))


def test_loader_rejects_child_set_unknown_type():
    def mutate(d):
        d["responses"][0]["child_set"][0]["type"] = "widget"

    assert any("not a declared child_entity_type" in e for e in _errors_for(mutate))


def test_loader_rejects_child_set_unknown_prop():
    def mutate(d):
        d["responses"][0]["child_set"][0]["state"] = {"gain": "$2"}

    assert any("state prop 'gain'" in e for e in _errors_for(mutate))


def test_loader_rejects_capture_ref_out_of_range():
    def mutate(d):
        d["responses"][0]["child_set"][0]["state"] = {"input": "$5"}

    assert any("exceeds the pattern's" in e for e in _errors_for(mutate))


def test_loader_rejects_child_set_on_json_response():
    def mutate(d):
        d["responses"].append(
            {
                "match": "unused",
                "json": True,
                "child_set": [
                    {"type": "output", "id": 1, "state": {"input": "$1"}}
                ],
            }
        )

    assert any("not supported on json responses" in e for e in _errors_for(mutate))


def test_loader_rejects_each_child_without_placeholder():
    def mutate(d):
        d["polling"]["queries"][1] = {"each_child": "zone", "send": "VOL?\r\n"}

    assert any("must contain {child_id}" in e for e in _errors_for(mutate))


def test_loader_rejects_each_child_unknown_type():
    def mutate(d):
        d["polling"]["queries"][1] = {
            "each_child": "widget",
            "send": "VOL? {child_id}\r\n",
        }

    assert any(
        "each_child type 'widget'" in e or "each_child type" in e
        for e in _errors_for(mutate)
    )


def test_loader_rejects_string_ids_with_count():
    def mutate(d):
        d["child_entity_types"]["output"]["id_format"] = {"type": "string"}

    assert any("requires integer ids" in e for e in _errors_for(mutate))


# ---------------------------------------------------------------------------
# Wire-id maps (0-based / letter-coded protocols): child_set id {group, map}
# routes wire ids to local child ids, and a command param map: translates
# the validated value to its wire form before substitution.
# ---------------------------------------------------------------------------


def _wire_map_definition():
    import copy

    definition = copy.deepcopy(ACME_MATRIX)
    # Wire speaks 0-based outputs; children are 1-based.
    definition["responses"].append(
        {
            "match": r"WIRE(\d+):(\d+)",
            "child_set": [
                {
                    "type": "output",
                    "id": {"group": 1, "map": {"0": 1, "1": 2}},
                    "state": {"input": "$2"},
                },
            ],
        }
    )
    definition["commands"]["route_wire"] = {
        "label": "Route (wire)",
        "send": "R{output}!\\r\\n",
        "params": {
            "output": {
                "type": "child_id",
                "child_type": "output",
                "required": True,
                "map": {"1": "0", "2": "1"},
            },
        },
    }
    definition["commands"]["preset_code"] = {
        "label": "Preset",
        "send": "P{preset}\\r\\n",
        "params": {
            "preset": {
                "type": "enum",
                "values": ["day", "night"],
                "map": {"day": "01", "night": "02"},
            },
        },
    }
    return definition


async def test_child_set_id_map_routes_wire_id():
    driver = _make_driver(_wire_map_definition())
    driver._register_declared_children()
    await driver.on_data_received(b"WIRE0:7")
    assert driver.state.get("device.dev1.output.01.input") == 7
    await driver.on_data_received(b"WIRE1:3")
    assert driver.state.get("device.dev1.output.02.input") == 3


async def test_child_set_id_map_unmapped_wire_id_skips():
    driver = _make_driver(_wire_map_definition())
    driver._register_declared_children()
    await driver.on_data_received(b"WIRE9:5")
    assert driver.state.get("device.dev1.output.01.input") == 0
    assert driver.state.get("device.dev1.output.02.input") == 0
    assert driver.state.get("device.dev1.output.09.input") is None


async def test_param_wire_map_translates_child_id():
    driver = _make_driver(_wire_map_definition())
    driver._register_declared_children()
    driver.transport = FakeTransport()
    await driver.send_command("route_wire", {"output": 2})
    assert driver.transport.sent[-1] == b"R1!\r\n"


async def test_param_wire_map_translates_enum_value():
    driver = _make_driver(_wire_map_definition())
    driver.transport = FakeTransport()
    await driver.send_command("preset_code", {"preset": "night"})
    assert driver.transport.sent[-1] == b"P02\r\n"


async def test_param_wire_map_unmapped_value_passes_through():
    driver = _make_driver(_wire_map_definition())
    driver._register_declared_children()
    driver.transport = FakeTransport()
    # 3 isn't in the map — goes on the wire as-is.
    await driver.send_command("route_wire", {"output": 3})
    assert driver.transport.sent[-1] == b"R3!\r\n"


def test_loader_accepts_wire_map_shapes():
    definition = _wire_map_definition()
    assert validate_driver_definition(definition) == []


def test_loader_rejects_id_map_without_group():
    def mutate(d):
        d["responses"].append(
            {
                "match": r"XOut(\d+)",
                "child_set": [
                    {
                        "type": "output",
                        "id": {"map": {"0": 1}},
                        "state": {"input": "$1"},
                    },
                ],
            }
        )

    assert any("id group must be a capture ref" in e for e in _errors_for(mutate))


def test_loader_rejects_id_map_group_out_of_range():
    def mutate(d):
        d["responses"].append(
            {
                "match": r"XOut(\d+)",
                "child_set": [
                    {
                        "type": "output",
                        "id": {"group": 3, "map": {"0": 1}},
                        "state": {"input": "$1"},
                    },
                ],
            }
        )

    assert any("exceeds the pattern's" in e for e in _errors_for(mutate))


def test_loader_rejects_id_map_non_integer_local_id():
    def mutate(d):
        d["responses"].append(
            {
                "match": r"XOut(\d+)",
                "child_set": [
                    {
                        "type": "output",
                        "id": {"group": 1, "map": {"0": "left"}},
                        "state": {"input": "$1"},
                    },
                ],
            }
        )

    assert any("is not an integer" in e for e in _errors_for(mutate))


def test_loader_rejects_bad_param_map_shape():
    def mutate(d):
        d["commands"]["route"]["params"]["output"]["map"] = "not-a-dict"

    assert any(
        "map must be a non-empty mapping" in e for e in _errors_for(mutate)
    )

# ---------------------------------------------------------------------------
# OSC child routing: child_set on address-matched rules (id from an address
# segment or a literal, values from positional args), {child_id:02d} format
# specs in each_child templates, and the instances `ids:` literal roster.
# ---------------------------------------------------------------------------


ACME_OSC_MIXER = {
    "id": "acme_osc_mixer",
    "name": "Acme OSC Mixer",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "osc",
    "default_config": {"host": "", "port": 10023},
    "config_schema": {
        "host": {"type": "string", "required": True, "label": "IP Address"},
        "port": {"type": "integer", "default": 10023, "label": "Port"},
    },
    "state_variables": {
        "last_fader": {"type": "number", "label": "Last Fader Seen"},
    },
    "child_entity_types": {
        "channel": {
            "label": "Channel",
            "id_format": {"type": "integer", "min": 1, "max": 32, "pad_width": 2},
            "state_variables": {
                "fader": {"type": "number", "label": "Fader", "min": 0, "max": 1},
                "mute": {"type": "boolean", "label": "Mute"},
                "name": {"type": "string", "label": "Name"},
            },
            "instances": {"count": 2, "label": "Ch {id}"},
        },
        "main": {
            "label": "Main",
            "id_format": {"type": "string"},
            "state_variables": {
                "fader": {"type": "number", "label": "Fader"},
            },
            "instances": {"ids": ["st", "m"], "label": "Main {id}"},
        },
    },
    "commands": {
        "set_channel_fader": {
            "label": "Set Channel Fader",
            "address": "/ch/{channel:02d}/mix/fader",
            "args": [{"type": "f", "value": "{level}"}],
            "params": {
                "channel": {
                    "type": "child_id",
                    "child_type": "channel",
                    "required": True,
                },
                "level": {"type": "number", "min": 0, "max": 1, "required": True},
            },
        },
    },
    "responses": [
        {
            # Flat mapping and child_set coexist on one rule.
            "address": "/ch/*/mix/fader",
            "mappings": [{"arg": 0, "state": "last_fader", "type": "float"}],
            "child_set": [
                {
                    "type": "channel",
                    "id": {"segment": 1},
                    "state": {"fader": {"arg": 0}},
                },
            ],
        },
        {
            # x32 semantics: on=1 means unmuted — value map + bool coercion.
            "address": "/ch/*/mix/on",
            "child_set": [
                {
                    "type": "channel",
                    "id": {"segment": 1},
                    "state": {"mute": {"arg": 0, "map": {"0": "true", "1": "false"}}},
                },
            ],
        },
        {
            "address": "/ch/*/config/name",
            "child_set": [
                {
                    "type": "channel",
                    "id": {"segment": 1},
                    "state": {"name": {"arg": 0}},
                },
            ],
        },
        {
            # Literal string id: a fixed address routes to one child.
            "address": "/main/st/mix/fader",
            "child_set": [
                {"type": "main", "id": "st", "state": {"fader": {"arg": 0}}},
            ],
        },
        {
            # 0-based wire ids translated by the id map; unmapped ids skip.
            "address": "/wire/*/lvl",
            "child_set": [
                {
                    "type": "channel",
                    "id": {"segment": 1, "map": {"0": 1, "1": 2}},
                    "state": {"fader": {"arg": 0}},
                },
            ],
        },
    ],
    "polling": {
        "queries": [
            {"each_child": "channel", "send": "/ch/{child_id:02d}/mix/fader"},
            {"each_child": "main", "send": "/main/{child_id}/mix/fader"},
        ],
    },
}


def _make_osc_driver(definition=ACME_OSC_MIXER):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 10023}, state, events)
    driver._register_declared_children()
    return driver


def _osc(address, *args):
    from server.transport.osc_codec import osc_encode_message

    return osc_encode_message(address, list(args))


def test_ids_literal_roster_registers_string_children():
    driver = _make_osc_driver()
    assert driver.list_children("channel") == [1, 2]
    assert driver.list_children("main") == ["st", "m"]


async def test_osc_child_set_routes_by_address_segment():
    driver = _make_osc_driver()
    await driver.on_data_received(_osc("/ch/02/mix/fader", ("f", 0.5)))
    assert driver.state.get("device.dev1.channel.02.fader") == 0.5
    assert driver.state.get("device.dev1.channel.01.fader") == 0.0


async def test_osc_flat_mapping_coexists_with_child_set():
    driver = _make_osc_driver()
    await driver.on_data_received(_osc("/ch/01/mix/fader", ("f", 0.75)))
    assert driver.get_state("last_fader") == 0.75
    assert driver.state.get("device.dev1.channel.01.fader") == 0.75


async def test_osc_child_set_value_map_coerces_bool():
    driver = _make_osc_driver()
    await driver.on_data_received(_osc("/ch/01/mix/on", ("i", 0)))
    assert driver.state.get("device.dev1.channel.01.mute") is True
    await driver.on_data_received(_osc("/ch/01/mix/on", ("i", 1)))
    assert driver.state.get("device.dev1.channel.01.mute") is False


async def test_osc_child_set_string_arg_routes_name():
    driver = _make_osc_driver()
    await driver.on_data_received(_osc("/ch/02/config/name", ("s", "Vocals")))
    assert driver.state.get("device.dev1.channel.02.name") == "Vocals"


async def test_osc_child_set_literal_string_id():
    driver = _make_osc_driver()
    await driver.on_data_received(_osc("/main/st/mix/fader", ("f", 0.9)))
    assert driver.state.get("device.dev1.main.st.fader") == 0.9


async def test_osc_child_set_id_map_routes_wire_id():
    driver = _make_osc_driver()
    await driver.on_data_received(_osc("/wire/0/lvl", ("f", 0.25)))
    assert driver.state.get("device.dev1.channel.01.fader") == 0.25


async def test_osc_child_set_id_map_unmapped_wire_id_skips():
    driver = _make_osc_driver()
    await driver.on_data_received(_osc("/wire/9/lvl", ("f", 0.25)))
    assert driver.state.get("device.dev1.channel.01.fader") == 0.0
    assert driver.state.get("device.dev1.channel.02.fader") == 0.0


async def test_osc_child_set_unregistered_id_skipped():
    driver = _make_osc_driver()
    await driver.on_data_received(_osc("/ch/09/mix/fader", ("f", 0.5)))
    assert driver.state.get("device.dev1.channel.09.fader") is None


async def test_osc_child_set_out_of_range_segment_skips():
    import copy

    definition = copy.deepcopy(ACME_OSC_MIXER)
    # Segment index past the end of real addresses: entry skips, no crash.
    definition["responses"][0]["child_set"][0]["id"] = {"segment": 9}
    driver = _make_osc_driver(definition)
    await driver.on_data_received(_osc("/ch/01/mix/fader", ("f", 0.5)))
    assert driver.state.get("device.dev1.channel.01.fader") == 0.0


async def test_osc_poll_expands_each_child_with_format_spec():
    driver = _make_osc_driver()
    driver.transport = FakeTransport()
    await driver.poll()
    sent = driver.transport.sent
    assert sent[0].startswith(b"/ch/01/mix/fader\x00")
    assert sent[1].startswith(b"/ch/02/mix/fader\x00")
    assert sent[2].startswith(b"/main/st/mix/fader\x00")
    assert sent[3].startswith(b"/main/m/mix/fader\x00")


async def test_osc_child_id_param_pads_via_format_spec():
    driver = _make_osc_driver()
    driver.transport = FakeTransport()

    # OSC commands require the OSCTransport type; substitute the encoder
    # check by calling the substitution path directly.
    from server.core.device_manager import DeviceManager

    params = DeviceManager._coerce_child_id_params(
        driver, "set_channel_fader", {"channel": "02", "level": 0.5}
    )
    assert params["channel"] == 2
    address = driver._safe_substitute(
        "/ch/{channel:02d}/mix/fader", {**driver.config, **params}
    )
    assert address == "/ch/02/mix/fader"


# ---------------------------------------------------------------------------
# Loader validation — OSC child_set + ids roster + format-spec each_child
# ---------------------------------------------------------------------------


def _osc_errors_for(mutate):
    import copy

    definition = copy.deepcopy(ACME_OSC_MIXER)
    mutate(definition)
    return validate_driver_definition(definition)


def test_loader_accepts_osc_child_set_definition():
    assert validate_driver_definition(ACME_OSC_MIXER) == []


def test_loader_rejects_osc_capture_ref_id():
    def mutate(d):
        d["responses"][1]["child_set"][0]["id"] = "$1"

    assert any("no capture groups" in e for e in _osc_errors_for(mutate))


def test_loader_rejects_osc_capture_ref_prop():
    def mutate(d):
        d["responses"][1]["child_set"][0]["state"] = {"mute": "$1"}

    assert any("no capture groups" in e for e in _osc_errors_for(mutate))


def test_loader_rejects_osc_segment_past_pattern_end():
    def mutate(d):
        d["responses"][0]["child_set"][0]["id"] = {"segment": 4}

    assert any("past the end" in e for e in _osc_errors_for(mutate))


def test_loader_rejects_osc_prop_without_arg_or_value():
    def mutate(d):
        d["responses"][0]["child_set"][0]["state"] = {"fader": {"map": {"0": 1}}}

    assert any("needs {arg: N} or {value:" in e for e in _osc_errors_for(mutate))


def test_loader_rejects_osc_unknown_child_type():
    def mutate(d):
        d["responses"][0]["child_set"][0]["type"] = "widget"

    assert any(
        "not a declared child_entity_type" in e for e in _osc_errors_for(mutate)
    )


def test_loader_rejects_osc_unknown_prop():
    def mutate(d):
        d["responses"][0]["child_set"][0]["state"] = {"bogus": {"arg": 0}}

    assert any("not declared in" in e for e in _osc_errors_for(mutate))


def test_loader_rejects_ids_roster_non_list():
    def mutate(d):
        d["child_entity_types"]["main"]["instances"] = {"ids": "st,m"}

    assert any("non-empty list" in e for e in _osc_errors_for(mutate))


def test_loader_rejects_ids_roster_non_integer_for_integer_type():
    def mutate(d):
        d["child_entity_types"]["channel"]["instances"] = {"ids": [1, "left"]}

    assert any("is not an integer" in e for e in _osc_errors_for(mutate))


def test_loader_rejects_two_roster_sources_with_ids():
    def mutate(d):
        d["child_entity_types"]["main"]["instances"] = {
            "ids": ["st"],
            "count": 2,
        }

    assert any("exactly one of" in e for e in _osc_errors_for(mutate))


def test_loader_accepts_format_spec_child_id_placeholder():
    # {child_id:02d} satisfies the each_child placeholder requirement.
    assert not any(
        "must contain" in e for e in validate_driver_definition(ACME_OSC_MIXER)
    )
