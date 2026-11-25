"""One enum option is one value, and a driver file lists each once.

An enum's ``values:`` list is the set of values a state variable holds, a
parameter sends or a setting writes. Two entries are one option when they are
the same JSON value (``enum_options.option_identity``): the same type and value, so 1
and 1.0 are one number, a boolean is never a number, and text is compared
exactly. A list that names one option twice offers a second entry nobody can
tell from the first, so the author's doors refuse it, naming the list and both
positions (``enum_options``), for a YAML driver and a Python driver alike; a
driver already installed with one still loads, and the load names it.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import check_driver_definition, validate_driver_definition
from openavc.drivers.python_info import (
    check_python_driver_info,
    extract_python_driver_info_from_source,
    python_driver_reference_skips,
    python_driver_refusals,
)
from openavc.drivers.enum_options import option_identity
from openavc.drivers.spec import DEFS

# --- the identity -------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b",
    [
        (1, 1.0),
        ("hdmi1", "hdmi1"),
        (True, True),
        (None, None),
        (1, {"value": 1, "label": "One"}),
        ({"value": "a", "label": "A"}, {"value": "a", "label": "Another"}),
    ],
)
def test_these_are_one_option(a: Any, b: Any) -> None:
    assert option_identity(a) is not None
    assert option_identity(a) == option_identity(b)


@pytest.mark.parametrize(
    "a, b",
    [
        (True, 1),
        (False, 0),
        (True, 1.0),
        ("1", 1),
        ("a", "A"),
        ("a", "a "),
        ("true", True),
        (None, "null"),
        ({"value": 1, "label": "Same"}, {"value": 2, "label": "Same"}),
    ],
)
def test_these_are_two_options(a: Any, b: Any) -> None:
    assert option_identity(a) != option_identity(b)


@pytest.mark.parametrize("entry", [{"label": "Only a label"}, object(), {"value": object()}])
def test_an_entry_naming_no_json_value_names_no_option(entry: Any) -> None:
    assert option_identity(entry) is None


# --- a YAML driver --------------------------------------------------------------


def _widget(**sections: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"noop": {"send": "NOOP\r"}},
    }
    definition.update(sections)
    return definition


_EVERY_LIST: dict[str, dict[str, Any]] = {
    "state_variables.input.values": {
        "state_variables": {"input": {"type": "enum", "label": "Input", "values": ["hdmi1", "hdmi1"]}},
    },
    "child_entity_types.zone.state_variables.source.values": {
        "child_entity_types": {
            "zone": {"state_variables": {"source": {"type": "enum", "values": ["a", "a"]}}},
        },
    },
    "commands.set_input.params.input.values": {
        "commands": {
            "set_input": {
                "send": "IN {input}\r",
                "params": {"input": {"type": "enum", "values": ["hdmi1", "hdmi1"]}},
            },
        },
    },
    "actions[0].params.input.values": {
        "commands": {"noop": {"send": "NOOP\r"}},
        "actions": [
            {
                "id": "pick",
                "label": "Pick",
                "kind": "command",
                "command": "noop",
                "params": {"input": {"type": "enum", "values": ["hdmi1", "hdmi1"]}},
            },
        ],
    },
    "config_schema.mode.values": {
        "config_schema": {"mode": {"type": "enum", "label": "Mode", "values": ["a", "a"]}},
    },
    "config_schema.presets.columns.kind.values": {
        "config_schema": {
            "presets": {
                "type": "table",
                "label": "Presets",
                "columns": {"kind": {"type": "enum", "label": "Kind", "values": ["a", "a"]}},
            },
        },
    },
    "device_settings.mode.values": {
        "device_settings": {
            "mode": {"type": "enum", "label": "Mode", "values": ["a", "a"], "write": {"send": "MODE {value}\r"}},
        },
    },
}


@pytest.mark.parametrize("place", sorted(_EVERY_LIST))
def test_every_list_a_driver_declares_is_held_to_it(place: str) -> None:
    errors = validate_driver_definition(_widget(**_EVERY_LIST[place]), strict=True)
    said = [e for e in errors if e.startswith(f"{place}[1]:")]
    assert said, errors
    assert "values[0]" in said[0]


def test_every_values_list_the_contract_declares_is_walked() -> None:
    # A new kind of entry that carries a values: list joins the walk.
    holders = sorted(
        name for name, node in DEFS.items()
        if "values" in (node.get("fields") or {})
    )
    assert holders == [
        "actionParamEntry",
        "childStateVariableEntry",
        "configSchemaEntry",
        "deviceSettingEntry",
        "paramEntry",
        "stateVariableEntry",
    ]
    columns = DEFS["configSchemaEntry"]["fields"]["columns"]["extra"]["fields"]
    assert "values" in columns


def test_a_number_written_two_ways_is_refused_naming_both_positions() -> None:
    definition = _widget(
        commands={
            "set_mode": {
                "send": "MODE {mode}\r",
                "params": {"mode": {"type": "enum", "values": [1, "2", 1.0]}},
            },
        },
    )
    errors = validate_driver_definition(definition, strict=True)
    said = [e for e in errors if "values[2]" in e]
    assert said == [
        "commands.set_mode.params.mode.values[2]: 1.0 is the same value as values[0] (1), "
        "since a number is one value however it is written; list each value once. "
        "YAML reads bare on/off/yes/no/true/false as booleans and bare digits as "
        "numbers. Put it in quotes."
    ], errors


def test_a_labelled_entry_is_its_value() -> None:
    definition = _widget(
        state_variables={
            "input": {
                "type": "enum",
                "label": "Input",
                "values": ["hdmi1", {"value": "hdmi2", "label": "Rear"}, {"value": "hdmi1", "label": "Front"}],
            },
        },
    )
    errors = validate_driver_definition(definition, strict=True)
    assert [e for e in errors if "values[" in e] == [
        'state_variables.input.values[2]: "hdmi1" is the same value as values[0]; list each value once'
    ]


def test_yaml_on_and_yes_are_one_true_and_the_sentence_says_why() -> None:
    definition = _widget(
        state_variables={"mode": {"type": "enum", "label": "Mode", "values": [True, True]}},
    )
    errors = validate_driver_definition(definition, strict=True)
    said = [e for e in errors if "values[1]" in e]
    assert said and said[0].startswith("state_variables.mode.values[1]: true is the same value as values[0];")
    assert "Put it in quotes." in said[0]


@pytest.mark.parametrize(
    "values",
    [
        [True, 1],
        ["1", 1],
        ["a", "A"],
        [{"value": 1, "label": "Same"}, {"value": 2, "label": "Same"}],
        [{"label": "No value"}, {"label": "No value"}],
        [],
    ],
)
def test_values_that_are_two_options_pass(values: list[Any]) -> None:
    definition = _widget(
        state_variables={"mode": {"type": "enum", "label": "Mode", "values": values}},
    )
    errors = validate_driver_definition(definition, strict=True)
    assert not [e for e in errors if "is the same value as" in e], errors


def test_a_loaded_driver_with_a_repeat_still_loads_and_is_named() -> None:
    definition = _widget(
        state_variables={"input": {"type": "enum", "label": "Input", "values": ["a", "b", "a"]}},
    )
    verdict = check_driver_definition(definition)
    assert not [e for e in verdict.errors if "is the same value as" in e]
    assert [n for n in verdict.notices if n.startswith("state_variables.input.values[2]:")]


def test_the_builder_anchors_it_at_the_list() -> None:
    from openavc.drivers.avcdriver_semantic import validate_driver_issues

    definition = _widget(
        state_variables={"input": {"type": "enum", "label": "Input", "values": ["a", "a"]}},
    )
    issues = validate_driver_issues(definition, strict=True)
    assert [i["path"] for i in issues if "is the same value as" in i["message"]] == [
        "state_variables.input.values[1]"
    ]


# --- a Python driver -------------------------------------------------------------


def _info(**sections: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "author": "Acme",
        "description": "A widget.",
        "transport": "tcp",
    }
    info.update(sections)
    return info


def test_a_python_driver_is_refused_at_the_authors_doors_and_named_at_load() -> None:
    info = _info(
        commands={"set_mode": {"label": "Mode", "params": {"mode": {"type": "enum", "values": [1, 2, 1.0]}}}},
    )
    refusals = python_driver_refusals(info)
    assert (
        "commands.set_mode.params.mode.values[2]: 1.0 is the same value as values[0] (1), "
        "since a number is one value however it is written; list each value once"
    ) in refusals
    loaded = check_python_driver_info(info)
    assert not [e for e in loaded.errors if "is the same value as" in e]
    assert [n for n in loaded.notices if n.startswith("commands.set_mode.params.mode.values[2]:")]


@pytest.mark.parametrize(
    "section",
    [
        {"config_schema": {"mode": {"type": "enum", "label": "Mode", "values": ["a", "a"]}}},
        {"device_settings": {"mode": {"type": "enum", "label": "Mode", "values": ["a", "a"]}}},
        {"state_variables": {"mode": {"type": "enum", "label": "Mode", "values": ["a", "a"]}}},
    ],
)
def test_a_python_driver_s_other_lists_are_held_to_it(section: dict[str, Any]) -> None:
    assert [r for r in python_driver_refusals(_info(**section)) if "is the same value as values[0]" in r]


def test_a_list_built_in_code_is_skipped_and_the_skip_is_named() -> None:
    source = (
        "INPUTS = ['a', 'b']\n"
        "class AcmeWidget:\n"
        "    DRIVER_INFO = {\n"
        "        'id': 'acme_widget', 'name': 'Acme Widget', 'manufacturer': 'Acme',\n"
        "        'category': 'utility', 'version': '1.0.0', 'author': 'Acme',\n"
        "        'description': 'A widget.', 'transport': 'tcp',\n"
        "        'state_variables': {\n"
        "            'built': {'type': 'enum', 'label': 'Built', 'values': list(INPUTS)},\n"
        "            'partly': {'type': 'enum', 'label': 'Partly', 'values': [*INPUTS, 'a', 'a']},\n"
        "        },\n"
        "    }\n"
    )
    info, _ = extract_python_driver_info_from_source(source, "acme_widget.py")
    assert not [r for r in python_driver_refusals(info) if "is the same value as" in r]
    skips = python_driver_reference_skips(info)
    assert any("state_variables.partly" in line for line in skips), skips
    assert not any(".values" in line for line in skips), skips
