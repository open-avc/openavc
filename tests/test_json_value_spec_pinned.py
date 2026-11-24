"""What a json rule's value spec compiles to, in a flat ``set:``, a ``mappings:`` list and a child.

A json response rule names what to read out of a body with a value spec: a JSON
path written plainly, or ``{key | path, type, map, contains}``. A flat ``set:``
reads one into a state variable, a ``mappings:`` entry likewise into the
variable its ``state:`` names, a ``child_set:`` entry's ``state:`` into a
child's prop. The three read the spec alike in most ways and differ in a few,
each of which reaches the runtime's reply handling and the simulator:

* the name the compiled mapping carries (``state`` / ``prop``);
* a spec naming neither key nor path reads the state variable's own name in a
  flat ``set:``, reads no field in a ``mappings:`` entry (which reads ``key:``
  alone, never ``path:``), and is skipped in a child;
* the key is kept as written in the flat forms and as text in a child, so a
  ``key: 0`` is read in a child and skipped by the flat reader, which skips a
  falsy key;
* ``map`` is kept as written in a flat ``set:`` (present, as None, when the spec
  has none), as written when one is written in a ``mappings:`` entry, and only
  when it is an object in a child.

These pin every shape exactly, the order of each mapping's keys included, on an
invented Acme device.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.compiled_protocol import compile_driver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_model import read_driver
from openavc.drivers.reply_rule import read_reply_rule

_STATE_VARS: dict[str, Any] = {
    "power": {"type": "boolean"},
    "level": {"type": "integer"},
    "label": {"type": "string"},
    "untyped": {"label": "No type"},
    "odd": "not a definition",
}

_ZONE = {
    "label": "Zone",
    "state_variables": {
        "level": {"type": "integer"},
        "name": {"type": "string"},
        "untyped": {"label": "No type"},
        "odd": "not a definition",
    },
}


def _json_mappings(rule: dict[str, Any], state_variables: Any) -> list[dict[str, Any]]:
    """The flat mappings a json rule reads (``reply_rule``)."""
    return list(read_reply_rule(rule, state_variables).mappings)


def _flat(set_map: Any, **rule: Any) -> list[dict[str, Any]]:
    """The flat mappings a json rule with this ``set:`` compiles to."""
    definition = {
        "state_variables": _STATE_VARS,
        "responses": [{"json": True, "set": set_map, **rule}],
    }
    return compile_driver(read_driver(definition), {}, device_id="acme1").json_responses[0][0]


def _child(state: Any) -> list[dict[str, Any]]:
    """The props a json child_set entry with this ``state:`` compiles to."""
    definition = {
        "state_variables": {},
        "child_entity_types": {"zone": _ZONE},
        "responses": [
            {"json": True, "child_set": [{"type": "zone", "id": 1, "state": state}]}
        ],
    }
    children = compile_driver(read_driver(definition), {}, device_id="acme1").json_responses[0][4]
    return children[0]["props"] if children else []


def _items(mappings: list[dict[str, Any]]) -> list[list[tuple[str, Any]]]:
    """Each mapping as its items in order, so key order is compared too."""
    return [list(mapping.items()) for mapping in mappings]


# ── The shorthand: a path written plainly ──


@pytest.mark.parametrize(
    "written, key",
    [
        ("pwr", "pwr"),
        ("a.b.0", "a.b.0"),
        (5, "5"),
        (None, "None"),
        (True, "True"),
        (["a"], "['a']"),
        ("", ""),
    ],
)
def test_the_shorthand_is_read_as_text_in_both_forms(written, key):
    assert _items(_flat({"level": written})) == [
        [("state", "level"), ("key", key), ("type", "integer")]
    ]
    assert _items(_child({"level": written})) == [
        [("prop", "level"), ("key", key), ("type", "integer")]
    ]


@pytest.mark.parametrize("name", ["untyped", "odd", "nowhere"])
def test_a_variable_with_no_type_reads_as_string_in_both_forms(name):
    assert _items(_flat({name: "k"})) == [[("state", name), ("key", "k"), ("type", "string")]]
    assert _items(_child({name: "k"})) == [[("prop", name), ("key", "k"), ("type", "string")]]


def test_a_child_prop_the_type_does_not_declare_but_the_platform_reserves_is_typed():
    assert _items(_child({"online": "z.on"})) == [
        [("prop", "online"), ("key", "z.on"), ("type", "boolean")]
    ]


# ── The long form: what both forms share ──


def test_the_long_form_in_a_flat_set_carries_every_field_in_order():
    spec = {"key": "p", "type": "string", "map": {"1": "on"}, "contains": "x"}
    assert _items(_flat({"power": spec})) == [
        [
            ("state", "power"),
            ("key", "p"),
            ("type", "string"),
            ("map", {"1": "on"}),
            ("contains", "x"),
        ]
    ]


def test_the_long_form_in_a_child_carries_every_field_in_order():
    spec = {"key": "p", "type": "string", "map": {"1": "on"}, "contains": "x"}
    assert _items(_child({"level": spec})) == [
        [
            ("prop", "level"),
            ("key", "p"),
            ("type", "string"),
            ("map", {"1": "on"}),
            ("contains", "x"),
        ]
    ]


def test_the_map_object_is_the_definitions_own_in_both_forms():
    value_map = {"1": "on"}
    assert _flat({"power": {"key": "p", "map": value_map}})[0]["map"] is value_map
    assert _child({"level": {"key": "p", "map": value_map}})[0]["map"] is value_map


@pytest.mark.parametrize("needle", ["x", 0, False, None, ["a"]])
def test_contains_is_carried_as_written_whatever_it_holds(needle):
    assert _flat({"power": {"key": "p", "contains": needle}})[0]["contains"] == needle
    assert _child({"level": {"key": "p", "contains": needle}})[0]["contains"] == needle


def test_no_contains_carries_none_in_either_form():
    assert "contains" not in _flat({"power": {"key": "p"}})[0]
    assert "contains" not in _child({"level": {"key": "p"}})[0]


@pytest.mark.parametrize("written", ["integer", ["integer"], None, 7])
def test_a_written_type_wins_over_the_declared_one_as_written(written):
    assert _flat({"power": {"key": "p", "type": written}})[0]["type"] == written
    assert _child({"level": {"key": "p", "type": written}})[0]["type"] == written


def test_key_wins_over_path_and_path_is_read_alone_in_both_forms():
    assert _flat({"power": {"key": "a", "path": "b"}})[0]["key"] == "a"
    assert _flat({"power": {"path": "b"}})[0]["key"] == "b"
    assert _child({"level": {"key": "a", "path": "b"}})[0]["key"] == "a"
    assert _child({"level": {"path": "b"}})[0]["key"] == "b"


# ── The four differences ──


def test_a_flat_spec_naming_no_key_reads_its_variables_own_name():
    assert _items(_flat({"power": {"type": "boolean"}})) == [
        [("state", "power"), ("key", "power"), ("type", "boolean"), ("map", None)]
    ]


def test_a_child_spec_naming_no_key_is_skipped_with_its_entry():
    assert _child({"level": {"type": "integer"}}) == []
    assert _items(_child({"level": {"type": "integer"}, "name": "n"})) == [
        [("prop", "name"), ("key", "n"), ("type", "string")]
    ]


@pytest.mark.parametrize("spec", [{"key": None}, {"path": None}, {"key": None, "path": "b"}])
def test_a_null_key_is_kept_in_a_flat_set_and_skipped_in_a_child(spec):
    assert _items(_flat({"power": spec})) == [
        [("state", "power"), ("key", None), ("type", "boolean"), ("map", None)]
    ]
    assert _child({"level": spec}) == []


@pytest.mark.parametrize("key", [0, 5, False, "", ["a"], 2.5])
def test_a_key_is_kept_as_written_in_a_flat_set_and_as_text_in_a_child(key):
    flat = _flat({"power": {"key": key}})[0]["key"]
    assert flat == key and type(flat) is type(key)
    assert _child({"level": {"key": key}})[0]["key"] == str(key)


@pytest.mark.parametrize("path", [0, False, ""])
def test_a_path_is_kept_as_written_in_a_flat_set_and_as_text_in_a_child(path):
    flat = _flat({"power": {"path": path}})[0]["key"]
    assert flat == path and type(flat) is type(path)
    assert _child({"level": {"path": path}})[0]["key"] == str(path)


@pytest.mark.parametrize("value_map", [None, [1, 2], "on", 3, {}])
def test_a_map_is_kept_as_written_in_a_flat_set(value_map):
    spec: dict[str, Any] = {"key": "p", "map": value_map}
    assert _items(_flat({"power": spec})) == [
        [("state", "power"), ("key", "p"), ("type", "boolean"), ("map", value_map)]
    ]


def test_a_flat_spec_with_no_map_still_carries_the_key():
    assert _items(_flat({"power": {"key": "p"}})) == [
        [("state", "power"), ("key", "p"), ("type", "boolean"), ("map", None)]
    ]


@pytest.mark.parametrize("value_map", [None, [1, 2], "on", 3])
def test_a_map_that_is_not_an_object_is_left_out_of_a_child(value_map):
    assert _items(_child({"level": {"key": "p", "map": value_map}})) == [
        [("prop", "level"), ("key", "p"), ("type", "integer")]
    ]


def test_an_empty_map_object_is_kept_in_a_child():
    assert _items(_child({"level": {"key": "p", "map": {}}})) == [
        [("prop", "level"), ("key", "p"), ("type", "integer"), ("map", {})]
    ]


# ── A json rule's flat mappings: mappings: or set: ──


def test_set_items_compile_in_the_order_written():
    out = _flat({"label": "l", "power": "p", "level": {"path": "v"}})
    assert [m["state"] for m in out] == ["label", "power", "level"]


def test_explicit_mappings_win_and_set_is_not_read():
    mapping = {"state": "power", "key": "p"}
    out = _json_mappings(
        {"json": True, "mappings": [mapping], "set": {"level": "v"}}, _STATE_VARS
    )
    assert _items(out) == [[("state", "power"), ("key", "p"), ("type", "boolean")]]


def test_an_empty_mappings_list_lets_set_be_read():
    out = _json_mappings({"json": True, "mappings": [], "set": {"level": "v"}}, _STATE_VARS)
    assert _items(out) == [[("state", "level"), ("key", "v"), ("type", "integer")]]


# ── A json rule's mappings: list, read through the value spec (LISTED) ──
#
# Its entries were passed on as written, so one with no type: read as text
# whatever its variable declared. They now compile through the value spec,
# typed by their variable as a set: item is, and differ from a set: item in
# the three ways the list form has always read: only key: names the field (no
# path alias), an entry naming none reads no field, and a map is carried only
# when one is written.


def _listed(*entries: Any) -> list[dict[str, Any]]:
    return _json_mappings({"json": True, "mappings": list(entries)}, _STATE_VARS)


@pytest.mark.parametrize("name, declared", [("level", "integer"), ("power", "boolean")])
def test_a_mappings_entry_with_no_type_takes_its_variables_declared_type(name, declared):
    assert _items(_listed({"state": name, "key": "v"})) == [
        [("state", name), ("key", "v"), ("type", declared)]
    ]


@pytest.mark.parametrize("name", ["untyped", "odd", "nowhere", "label"])
def test_a_mappings_entry_for_a_variable_with_no_type_reads_as_string(name):
    assert _listed({"state": name, "key": "v"})[0]["type"] == "string"


@pytest.mark.parametrize("written", ["string", ["integer"], None, 7])
def test_a_mappings_entrys_written_type_wins_as_written(written):
    assert _listed({"state": "level", "key": "v", "type": written})[0]["type"] == written


def test_a_mappings_entry_carries_every_field_in_order():
    entry = {"state": "power", "key": "p", "type": "string", "map": {"1": "on"}, "contains": "x"}
    assert _items(_listed(entry)) == [
        [
            ("state", "power"),
            ("key", "p"),
            ("type", "string"),
            ("map", {"1": "on"}),
            ("contains", "x"),
        ]
    ]


def test_a_mappings_entry_with_no_map_carries_none():
    assert "map" not in _listed({"state": "level", "key": "v"})[0]


@pytest.mark.parametrize("value_map", [None, [1, 2], "on", {}])
def test_a_mappings_entrys_map_is_kept_as_written(value_map):
    assert _listed({"state": "level", "key": "v", "map": value_map})[0]["map"] == value_map


@pytest.mark.parametrize("key", [0, "", None, ["a"]])
def test_a_mappings_entrys_key_is_kept_as_written(key):
    out = _listed({"state": "level", "key": key})[0]["key"]
    assert out == key and type(out) is type(key)


def test_a_mappings_entry_naming_no_key_reads_no_field():
    assert _items(_listed({"state": "level"})) == [
        [("state", "level"), ("key", None), ("type", "integer")]
    ]


def test_a_mappings_entrys_path_names_no_field():
    assert _listed({"state": "level", "path": "v"})[0]["key"] is None


def test_a_mappings_entrys_fields_a_json_rule_does_not_read_are_left_out():
    out = _listed({"state": "level", "key": "v", "group": 1, "value": 3, "arg": 0})
    assert _items(out) == [[("state", "level"), ("key", "v"), ("type", "integer")]]


def test_an_entry_that_names_no_state_variable_compiles_to_nothing():
    entries: list[Any] = ["junk", {"key": "k"}, {"state": "", "key": "k"}, {"state": 5, "key": "k"}]
    assert _listed(*entries, {"state": "level", "key": "v"}) == [
        {"state": "level", "key": "v", "type": "integer"}
    ]


def test_a_mappings_list_that_names_no_state_variable_still_keeps_set_from_being_read():
    rule = {"json": True, "mappings": ["junk", {"key": "k"}], "set": {"level": "v"}}
    assert _json_mappings(rule, _STATE_VARS) == []


def test_a_mappings_entry_compiles_to_a_new_dict():
    entry = {"state": "level", "key": "v", "map": {"0": "1"}}
    out = _listed(entry)[0]
    assert out is not entry and entry == {"state": "level", "key": "v", "map": {"0": "1"}}
    assert out["map"] is entry["map"]


async def test_a_mappings_entry_with_no_type_stores_its_variables_type_from_a_reply(state, events):
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "http",
        "state_variables": {"level": {"type": "integer"}, "power": {"type": "boolean"}},
        "commands": {},
        "responses": [
            {"json": True, "mappings": [{"state": "level", "key": "v"}, {"state": "power", "key": "p"}]}
        ],
    }
    state.set_event_bus(events)
    driver = create_configurable_driver_class(definition)(
        "acme1", {"host": "127.0.0.1", "port": 80}, state, events
    )
    await driver.on_data_received(b'{"v": 42, "p": "on"}')
    assert driver.get_state("level") == 42 and type(driver.get_state("level")) is int
    assert driver.get_state("power") is True


@pytest.mark.parametrize("set_map", [None, "pwr", ["pwr"], 3])
def test_a_set_that_is_not_an_object_compiles_to_nothing(set_map):
    assert _json_mappings({"json": True, "set": set_map}, _STATE_VARS) == []


def test_a_json_rule_takes_any_state_variables_reading():
    out = _json_mappings({"json": True, "set": {"power": {"key": "p"}}}, {})
    assert _items(out) == [[("state", "power"), ("key", "p"), ("type", "string"), ("map", None)]]


def test_a_converted_flat_mapping_is_a_new_dict_with_the_conversion_last():
    definition = {
        "state_variables": {"level": {"type": "integer", "scale": 0.5}},
        "responses": [{"json": True, "set": {"level": {"key": "v", "map": {"0": "1"}}}}],
    }
    out = compile_driver(read_driver(definition), {}).json_responses[0][0]
    assert _items(out) == [
        [
            ("state", "level"),
            ("key", "v"),
            ("type", "integer"),
            ("map", {"0": "1"}),
            ("convert", {"scale": 0.5}),
        ]
    ]


@pytest.mark.parametrize("form", ["flat", "child"])
def test_a_contains_mapping_takes_no_conversion(form):
    definition: dict[str, Any] = {
        "state_variables": {"level": {"type": "integer", "scale": 0.5}},
        "child_entity_types": {
            "zone": {"state_variables": {"level": {"type": "integer", "scale": 0.5}}}
        },
    }
    spec = {"key": "v", "contains": 3}
    if form == "flat":
        definition["responses"] = [{"json": True, "set": {"level": spec}}]
        out = compile_driver(read_driver(definition), {}).json_responses[0][0]
    else:
        definition["responses"] = [
            {"json": True, "child_set": [{"type": "zone", "id": 1, "state": {"level": spec}}]}
        ]
        out = compile_driver(read_driver(definition), {}).json_responses[0][4][0]["props"]
    assert "convert" not in out[0]
