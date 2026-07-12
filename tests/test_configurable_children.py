"""Tests for declarative child entities in YAML drivers.

Covers the three runtime pieces — `instances:` rosters (count / count_from /
ids_from), `child_set:` response routing, and `each_child:` query expansion —
plus the loader validation for each, using an invented matrix switcher.
"""

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
    },
    "config_schema": {
        "host": {"type": "string", "required": True, "label": "IP Address"},
        "port": {"type": "integer", "default": 23, "label": "Port"},
        "output_count": {"type": "integer", "default": 4, "label": "Outputs"},
        "zone_ids": {"type": "string", "default": "1,2,4", "label": "Zone IDs"},
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
            "string": "{input}*{output}!\\r\\n",
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
        "interval": 10,
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
        "string": "R{output}!\\r\\n",
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
        "string": "P{preset}\\r\\n",
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
