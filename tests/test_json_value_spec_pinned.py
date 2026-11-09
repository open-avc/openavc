"""What a json rule's value spec compiles to, in a flat ``set:`` and in a child.

A json response rule names what to read out of a body with a value spec: a JSON
path written plainly, or ``{key | path, type, map, contains}``. A flat ``set:``
reads one into a state variable, a ``child_set:`` entry's ``state:`` into a
child's prop. The two read the spec alike in most ways and differ in four, each
of which reaches the runtime's reply handling and the simulator:

* the name the compiled mapping carries (``state`` / ``prop``);
* a spec naming neither key nor path reads the state variable's own name in a
  flat ``set:``, and is skipped in a child;
* the key is kept as written in a flat ``set:`` and as text in a child, so a
  ``key: 0`` is read in a child and skipped by the flat reader, which skips a
  falsy key;
* ``map`` is kept as written in a flat ``set:`` (present, as None, when the spec
  has none) and only when it is an object in a child.

These pin every shape exactly, the order of each mapping's keys included, on an
invented Acme device.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.compiled_protocol import build_json_mappings, compile_driver

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


def _flat(set_map: Any, **rule: Any) -> list[dict[str, Any]]:
    """The flat mappings a json rule with this ``set:`` compiles to."""
    definition = {
        "state_variables": _STATE_VARS,
        "responses": [{"json": True, "set": set_map, **rule}],
    }
    return compile_driver(definition, {}, device_id="acme1").json_responses[0][0]


def _child(state: Any) -> list[dict[str, Any]]:
    """The props a json child_set entry with this ``state:`` compiles to."""
    definition = {
        "state_variables": {},
        "child_entity_types": {"zone": _ZONE},
        "responses": [
            {"json": True, "child_set": [{"type": "zone", "id": 1, "state": state}]}
        ],
    }
    children = compile_driver(definition, {}, device_id="acme1").json_responses[0][4]
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
    out = build_json_mappings(
        {"json": True, "mappings": [mapping], "set": {"level": "v"}}, _STATE_VARS
    )
    assert out == [mapping] and out[0] is mapping


def test_an_empty_mappings_list_lets_set_be_read():
    out = build_json_mappings({"json": True, "mappings": [], "set": {"level": "v"}}, _STATE_VARS)
    assert _items(out) == [[("state", "level"), ("key", "v"), ("type", "integer")]]


def test_explicit_mappings_are_passed_on_untyped_and_unchecked():
    entries: list[Any] = [{"state": "level", "key": "v"}, "junk", {"key": "k"}]
    out = build_json_mappings({"json": True, "mappings": entries}, _STATE_VARS)
    assert out == entries and out is not entries
    assert all(a is b for a, b in zip(out, entries))


@pytest.mark.parametrize("set_map", [None, "pwr", ["pwr"], 3])
def test_a_set_that_is_not_an_object_compiles_to_nothing(set_map):
    assert build_json_mappings({"json": True, "set": set_map}, _STATE_VARS) == []


def test_build_json_mappings_takes_any_state_variables_reading():
    out = build_json_mappings({"json": True, "set": {"power": {"key": "p"}}}, {})
    assert _items(out) == [[("state", "power"), ("key", "p"), ("type", "string"), ("map", None)]]


def test_a_converted_flat_mapping_is_a_new_dict_with_the_conversion_last():
    definition = {
        "state_variables": {"level": {"type": "integer", "scale": 0.5}},
        "responses": [{"json": True, "set": {"level": {"key": "v", "map": {"0": "1"}}}}],
    }
    out = compile_driver(definition, {}).json_responses[0][0]
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
        out = compile_driver(definition, {}).json_responses[0][0]
    else:
        definition["responses"] = [
            {"json": True, "child_set": [{"type": "zone", "id": 1, "state": {"level": spec}}]}
        ]
        out = compile_driver(definition, {}).json_responses[0][4][0]["props"]
    assert "convert" not in out[0]
