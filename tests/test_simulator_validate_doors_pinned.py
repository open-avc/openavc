"""Pins for what the simulator validator says about a driver's state seeds,
its declared simulator settings, a Python driver against its ``_sim.py``, and
the command line's own output.

Three surfaces, each an invented Acme driver, each expectation what the
validator says today (severity, check, message and order exact, or what it
raises), so a restructure that changes any of them fails here:

- **The YAML door** (``validate_yaml_driver``), filtered to the state coverage,
  initial-state type, capacity and single_client lines.
- **The Python door** (``validate_python_driver``), every line, from a driver
  and its companion simulator written to disk.
- **The command line** (``main``): standard output, standard error and the
  exit code, the temporary folder's path replaced by ``<tmp>``.

Some pin what looks wrong (the start value announced for a ``float`` variable,
or an integer with a fractional or text ``min``, is not the one the simulator
starts it at; a declared ``capacity: null`` is refused where the simulator
skips it); they record today's behaviour, they do not endorse it.
"""

from __future__ import annotations

import copy
import textwrap
from pathlib import Path

import pytest
import yaml

from openavc.simulator.validate import main, validate_python_driver, validate_yaml_driver


# ── The YAML door ──


_YAML_BASE: dict = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "delimiter": "\\r\\n",
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
    },
    "commands": {
        "power_on": {"send": "PWR 1\\r\\n"},
    },
    "responses": [
        {"match": "^PWR (\\d)$", "set": {"power": "$1"}},
    ],
}


def _yaml(state_variables=None, simulator=None, **changes) -> dict:
    """The base driver with these state variables, this ``simulator:`` section
    (absent when None) and top-level keys replaced (``_drop`` removes one)."""
    definition = copy.deepcopy(_YAML_BASE)
    if state_variables is not None:
        definition["state_variables"] = state_variables
    if simulator is not None:
        definition["simulator"] = simulator
    for key, value in changes.items():
        if value == "_drop":
            definition.pop(key, None)
        else:
            definition[key] = value
    return definition


def _seeded(var_def: dict, value) -> dict:
    """One variable ``v`` declared as ``var_def`` and seeded with ``value``."""
    return _yaml({"v": var_def}, {"initial_state": {"v": value}})


def _unseeded(var_def: dict) -> dict:
    """One variable ``v`` declared as ``var_def`` and left out of the seeds."""
    return _yaml({"v": var_def}, {"initial_state": {"other": 1}})


YAML_CASES: dict[str, dict] = {
    # ── state coverage ──
    "coverage_no_section_no_variables": _yaml({}),
    "coverage_no_section_with_variables": _yaml(),
    "coverage_empty_section_no_variables": _yaml({}, {}),
    "coverage_every_variable_seeded": _yaml(
        {"a": {"type": "integer"}, "b": {"type": "boolean"}},
        {"initial_state": {"a": 1, "b": True}},
    ),
    "coverage_string": _unseeded({"type": "string"}),
    "coverage_type_missing": _unseeded({"label": "V"}),
    "coverage_integer_no_min": _unseeded({"type": "integer"}),
    "coverage_integer_min": _unseeded({"type": "integer", "min": 5}),
    "coverage_integer_fractional_min": _unseeded({"type": "integer", "min": 0.5}),
    "coverage_integer_text_min": _unseeded({"type": "integer", "min": "low"}),
    "coverage_number": _unseeded({"type": "number", "min": -80}),
    "coverage_float": _unseeded({"type": "float"}),
    "coverage_boolean": _unseeded({"type": "boolean"}),
    "coverage_enum": _unseeded({"type": "enum", "values": ["hdmi1", "hdmi2"]}),
    "coverage_enum_no_values": _unseeded({"type": "enum", "values": []}),
    "coverage_enum_values_missing": _unseeded({"type": "enum"}),
    "coverage_unknown_type": _unseeded({"type": "colour"}),
    "coverage_list_type": _unseeded({"type": ["integer"]}),
    "coverage_several_in_order": _yaml(
        {"z": {"type": "integer"}, "a": {"type": "string"}, "m": {"type": "boolean"}},
        {"initial_state": {"q": 1}},
    ),
    "coverage_section_without_initial_state": _yaml(
        {"v": {"type": "integer"}}, {"delays": {"v": 1}},
    ),
    "coverage_variable_not_a_mapping": _yaml({"v": "integer"}, {"initial_state": {}}),
    "coverage_variables_a_list": _yaml(["v"], {"initial_state": {}}),
    # ── initial-state types ──
    "types_boolean_given_bool": _seeded({"type": "boolean"}, True),
    "types_boolean_given_int": _seeded({"type": "boolean"}, 1),
    "types_boolean_given_text": _seeded({"type": "boolean"}, "on"),
    "types_boolean_given_none": _seeded({"type": "boolean"}, None),
    "types_integer_given_int": _seeded({"type": "integer"}, 5),
    "types_integer_given_bool": _seeded({"type": "integer"}, True),
    "types_integer_given_float": _seeded({"type": "integer"}, 5.0),
    "types_integer_given_text": _seeded({"type": "integer"}, "5"),
    "types_number_given_int": _seeded({"type": "number"}, 3),
    "types_number_given_float": _seeded({"type": "number"}, 1.5),
    "types_number_given_bool": _seeded({"type": "number"}, False),
    "types_number_given_text": _seeded({"type": "number"}, "1.5"),
    "types_float_given_float": _seeded({"type": "float"}, 0.25),
    "types_float_given_bool": _seeded({"type": "float"}, True),
    "types_float_given_list": _seeded({"type": "float"}, [1]),
    "types_string_given_int": _seeded({"type": "string"}, 7),
    "types_type_missing_given_int": _seeded({"label": "V"}, 7),
    "types_type_missing_given_text": _seeded({"label": "V"}, "x"),
    "types_enum_value_listed": _seeded({"type": "enum", "values": ["a", "b"]}, "b"),
    "types_enum_value_unlisted": _seeded({"type": "enum", "values": ["a", "b"]}, "c"),
    "types_enum_value_listed_as_text": _seeded({"type": "enum", "values": [1, 2]}, "2"),
    "types_enum_number_listed_as_text": _seeded({"type": "enum", "values": ["1", "2"]}, 2),
    "types_enum_values_empty": _seeded({"type": "enum", "values": []}, "x"),
    "types_enum_values_missing": _seeded({"type": "enum"}, "x"),
    "types_enum_values_a_mapping": _seeded({"type": "enum", "values": {"a": 1}}, "x"),
    "types_enum_values_text": _seeded({"type": "enum", "values": "a,b"}, "a"),
    "types_unknown_type": _seeded({"type": "colour"}, 7),
    "types_list_type": _seeded({"type": ["integer"]}, "x"),
    "types_mapping_type": _seeded({"type": {"integer": 1}}, "x"),
    "types_numeric_type": _seeded({"type": 5}, "x"),
    "types_variable_not_a_mapping": _yaml({"v": "integer"}, {"initial_state": {"v": "x"}}),
    "types_variable_name_a_number": _yaml(
        {5: {"type": "integer"}}, {"initial_state": {5: "x"}},
    ),
    "types_several_in_declared_order": _yaml(
        {
            "z": {"type": "integer"},
            "a": {"type": "boolean"},
            "m": {"type": "enum", "values": ["x"]},
            "n": {"type": "number"},
        },
        {"initial_state": {"n": "1", "m": "y", "a": "no", "z": "2"}},
    ),
    "types_initial_state_a_list": _yaml(
        {"v": {"type": "integer"}}, {"initial_state": ["v"]},
    ),
    # ── a boolean named in a respond: template ──
    "bool_ref_named_twice": _yaml(simulator={"command_handlers": [
        {"receive": "P\\?", "respond": "{state.power}{state.power}"},
    ]}),
    "bool_ref_in_two_handlers": _yaml(simulator={"command_handlers": [
        {"receive": "P\\?", "respond": "P{state.power}"},
        {"receive": "Q\\?", "respond": "Q{state.power}"},
    ]}),
    "bool_ref_after_the_seed_types": _yaml(
        {"power": {"type": "boolean"}, "level": {"type": "integer"}},
        {"initial_state": {"power": "on", "level": "x"}, "command_handlers": [
            {"receive": "P\\?", "respond": "P{state.power} L{state.level}"},
        ]},
    ),
    "bool_ref_handler_not_a_mapping": _yaml(simulator={"command_handlers": [
        "P?", {"receive": "P\\?", "respond": "{state.power}"},
    ]}),
    "bool_ref_variable_not_a_mapping": _yaml(
        {"power": {"type": "boolean"}, "flag": "boolean"},
        {"initial_state": {"flag": 1}, "command_handlers": [
            {"receive": "F\\?", "respond": "{state.flag}"},
        ]},
    ),
    "bool_ref_type_a_list": _yaml(
        {"power": {"type": ["boolean"]}},
        {"command_handlers": [{"receive": "P\\?", "respond": "{state.power}"}]},
    ),
    "bool_ref_unknown_name": _yaml(simulator={"command_handlers": [
        {"receive": "P\\?", "respond": "{state.nope}"},
    ]}),
    # ── capacity ──
    "capacity_valid": _yaml(simulator={"capacity": {"rx_buffer": 256, "process_ms": 5}}),
    "capacity_missing_field": _yaml(simulator={"capacity": {"rx_buffer": 256}}),
    "capacity_bad_fields": _yaml(simulator={"capacity": {"rx_buffer": -1}}),
    "capacity_text": _yaml(simulator={"capacity": "fast"}),
    "capacity_null": _yaml(simulator={"capacity": None}),
    # ── single_client ──
    "single_client_newest": _yaml(simulator={"single_client": "newest"}),
    "single_client_reject_on_udp": _yaml(simulator={"single_client": "reject"}, transport="udp"),
    "single_client_bogus": _yaml(simulator={"single_client": "first"}),
    "single_client_bogus_on_http": _yaml(simulator={"single_client": "first"}, transport="http"),
    "single_client_null": _yaml(simulator={"single_client": None}),
    "single_client_transport_absent": _yaml(
        simulator={"single_client": "oldest"}, transport="_drop",
    ),
    "single_client_transport_null": _yaml(simulator={"single_client": "oldest"}, transport=None),
    "single_client_on_serial": _yaml(simulator={"single_client": "oldest"}, transport="serial"),
    "capacity_then_single_client": _yaml(
        simulator={"single_client": "first", "capacity": "fast"}, transport="osc",
    ),
}


_YAML_CHECKS = frozenset({"state_coverage", "type_consistency", "capacity", "single_client"})


def _yaml_issues(tmp_path: Path, name: str):
    """The pinned checks' lines, or what the validator raises."""
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(YAML_CASES[name], sort_keys=False), encoding="utf-8")
    try:
        result = validate_yaml_driver(path)
    except Exception as exc:  # noqa: BLE001 - what raises is pinned too
        return ("raises", type(exc).__name__, str(exc))
    return [
        (i.severity, i.check, i.message) for i in result.issues if i.check in _YAML_CHECKS
    ]


@pytest.mark.parametrize("name", sorted(YAML_CASES))
def test_the_yaml_door_says_what_it_said(tmp_path, name):
    assert _yaml_issues(tmp_path, name) == YAML_EXPECTED[name]


# ── The Python door ──


_DRIVER_INFO: dict = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "level": {"type": "integer", "label": "Level"},
    },
    "commands": {
        "power_on": {"label": "Power On"},
        "set_level": {"label": "Set Level"},
    },
}

_SIMULATOR_INFO: dict = {
    "driver_id": "acme_widget",
    "name": "Acme Widget Simulator",
    "transport": "tcp",
    "initial_state": {"power": False, "level": 0},
}

# Text that mentions both default command names, so the command coverage line
# is silent unless a case asks otherwise.
_HANDLED = "    def handle(self, data):\n        return ('power_on', 'set_level')\n"


def _literal(value) -> str:
    """``value`` written as Python source, with ``_Code`` values left as code."""
    if isinstance(value, _Code):
        return value.source
    if isinstance(value, dict):
        items = ", ".join(f"{_literal(k)}: {_literal(v)}" for k, v in value.items())
        return "{" + items + "}"
    return repr(value)


class _Code:
    """A value written into the source as an expression, not a literal."""

    def __init__(self, source: str):
        self.source = source


def _driver_source(info) -> str:
    return (
        "COMPUTED = {}\n\n"
        "class AcmeWidgetDriver:\n"
        f"    DRIVER_INFO = {_literal(info)}\n"
    )


def _sim_source(info, body: str = _HANDLED) -> str:
    return (
        "DEFAULTS = {'power': False}\n\n"
        "class AcmeWidgetSimulator:\n"
        f"    SIMULATOR_INFO = {_literal(info)}\n"
        f"{body}"
    )


def _pair(driver: dict | None = None, sim: dict | None = None, *, drop=(), sim_drop=(),
          body: str = _HANDLED) -> tuple[str, str]:
    """The base driver and simulator, with keys replaced and keys dropped."""
    driver_info = {**_DRIVER_INFO, **(driver or {})}
    for key in drop:
        driver_info.pop(key, None)
    sim_info = {**_SIMULATOR_INFO, **(sim or {})}
    for key in sim_drop:
        sim_info.pop(key, None)
    return _driver_source(driver_info), _sim_source(sim_info, body)


def _raw(driver: str, sim: str | None) -> tuple[str, str | None]:
    return textwrap.dedent(driver), None if sim is None else textwrap.dedent(sim)


_GOOD_DRIVER = _driver_source(_DRIVER_INFO)
_GOOD_SIM = _sim_source(_SIMULATOR_INFO)


PYTHON_CASES: dict[str, tuple[str, str | None]] = {
    # ── reading the driver ──
    "driver_clean": _pair(),
    "driver_syntax_error": _raw("class A:\n    DRIVER_INFO = {\n", _GOOD_SIM),
    "driver_info_at_module_level": _raw(
        "DRIVER_INFO = {'id': 'acme_widget', 'name': 'A'}\n", _GOOD_SIM,
    ),
    "driver_info_not_a_dict": _raw(
        "class A:\n    DRIVER_INFO = dict(id='acme_widget')\n", _GOOD_SIM,
    ),
    "driver_info_without_id": _pair(drop=("id",)),
    "driver_info_empty_id": _pair({"id": ""}),
    "driver_id_a_number": _pair({"id": 7}),
    "driver_id_computed": _pair({"id": _Code("COMPUTED")}),
    "driver_info_annotated": _raw(
        "class A:\n    DRIVER_INFO: dict = {'id': 'acme_widget', 'name': 'A'}\n", _GOOD_SIM,
    ),
    # ── finding and reading the simulator ──
    "sim_missing": _raw(_GOOD_DRIVER, None),
    "sim_syntax_error": _raw(_GOOD_DRIVER, "class S:\n    SIMULATOR_INFO = {\n"),
    "sim_info_at_module_level": _raw(
        _GOOD_DRIVER, "SIMULATOR_INFO = {'driver_id': 'acme_widget'}\n",
    ),
    "sim_no_class": _raw(_GOOD_DRIVER, "x = 1\n"),
    "sim_info_computed": _raw(
        _GOOD_DRIVER, "class S:\n    SIMULATOR_INFO = dict(DEFAULTS)\n",
    ),
    "sim_info_empty": _raw(_GOOD_DRIVER, "class S:\n    SIMULATOR_INFO = {}\n"),
    "sim_info_annotated": _raw(
        _GOOD_DRIVER,
        "class S:\n    SIMULATOR_INFO: dict = {'driver_id': 'acme_widget', 'name': 'S',"
        " 'initial_state': {'power': False, 'level': 0}}\n"
        "    def h(self):\n        return 'power_on set_level'\n",
    ),
    "sim_info_annotated_without_value": _raw(
        _GOOD_DRIVER, "class S:\n    SIMULATOR_INFO: dict\n",
    ),
    "sim_info_annotated_then_assigned": _raw(
        _GOOD_DRIVER,
        "class S:\n    SIMULATOR_INFO: dict\n    SIMULATOR_INFO = {'driver_id': 'acme_widget',"
        " 'name': 'S', 'initial_state': {'power': False, 'level': 0}}\n"
        "    def h(self):\n        return 'power_on set_level'\n",
    ),
    "sim_info_chained_assignment": _raw(
        _GOOD_DRIVER,
        "class S:\n    INFO = SIMULATOR_INFO = {'driver_id': 'acme_widget', 'name': 'S',"
        " 'initial_state': {'power': False, 'level': 0}}\n",
    ),
    "sim_info_in_second_class": _raw(
        _GOOD_DRIVER,
        "class Helper:\n    X = 1\n\nclass S:\n    SIMULATOR_INFO = {'driver_id': 'acme_widget',"
        " 'name': 'S', 'initial_state': {}}\n",
    ),
    "sim_info_first_class_wins": _raw(
        _GOOD_DRIVER,
        "class S1:\n    SIMULATOR_INFO = {'driver_id': 'one', 'name': 'S'}\n\n"
        "class S2:\n    SIMULATOR_INFO = {'driver_id': 'two', 'name': 'S'}\n",
    ),
    "sim_info_nested_class": _raw(
        _GOOD_DRIVER,
        "class Outer:\n    class S:\n        SIMULATOR_INFO = {'driver_id': 'acme_widget',"
        " 'name': 'S', 'initial_state': {}}\n",
    ),
    # ── required fields ──
    "required_driver_id_missing": _pair(sim_drop=("driver_id",)),
    "required_name_missing": _pair(sim_drop=("name",)),
    "required_initial_state_missing": _pair(sim_drop=("initial_state",)),
    "required_all_missing": _pair(sim_drop=("driver_id", "name", "initial_state")),
    # ── capacity and single_client ──
    "capacity_valid": _pair(sim={"capacity": {"rx_buffer": 64, "process_ms": 2}}),
    "capacity_bad": _pair(sim={"capacity": {"rx_buffer": 0}}),
    "capacity_computed": _pair(sim={"capacity": _Code("DEFAULTS")}),
    "capacity_none": _pair(sim={"capacity": None}),
    "single_client_valid": _pair(sim={"single_client": "reject"}),
    "single_client_bogus": _pair(sim={"single_client": "first"}),
    "single_client_computed": _pair(sim={"single_client": _Code("DEFAULTS")}),
    "single_client_on_serial": _pair({"transport": "serial"}, {"single_client": "newest"}),
    "single_client_driver_transport_absent": _pair(
        sim={"single_client": "newest"}, drop=("transport",),
    ),
    "single_client_driver_transport_computed": _pair(
        {"transport": _Code("COMPUTED")}, {"single_client": "newest"},
    ),
    "capacity_and_single_client_bad_with_missing_name": _pair(
        {"transport": "udp"},
        {"capacity": "fast", "single_client": "first", "transport": "udp"},
        sim_drop=("name",),
    ),
    # ── driver_id match ──
    "driver_id_mismatch": _pair(sim={"driver_id": "acme_other"}),
    "driver_id_mismatch_empty": _pair(sim={"driver_id": ""}),
    "driver_id_mismatch_computed": _pair(sim={"driver_id": _Code("DEFAULTS")}),
    "driver_id_mismatch_number_id": _pair({"id": 7}, {"driver_id": "acme_widget"}),
    # ── state coverage and types ──
    "state_all_seeded": _pair(),
    "state_none_seeded": _pair(sim={"initial_state": {}}),
    "state_none_seeded_no_variables": _pair({"state_variables": {}}, {"initial_state": {}}),
    "state_some_seeded": _pair(sim={"initial_state": {"power": False, "wire_code": 1}}),
    "state_many_unseeded": _pair(
        {"state_variables": {f"v{i}": {"type": "string"} for i in range(9)}},
        {"initial_state": {"v3": "x"}},
    ),
    "state_exactly_six_unseeded": _pair(
        {"state_variables": {f"v{i}": {"type": "string"} for i in range(7)}},
        {"initial_state": {"v0": "x"}},
    ),
    "state_name_a_number": _pair(
        {"state_variables": {5: {"type": "integer"}, "power": {"type": "boolean"}}},
        {"initial_state": {"power": False}},
    ),
    "state_seeds_computed": _pair(sim={"initial_state": _Code("dict(DEFAULTS)")}),
    "state_variables_computed": _pair({"state_variables": _Code("COMPUTED")}),
    "state_both_computed": _pair(
        {"state_variables": _Code("COMPUTED")}, {"initial_state": _Code("DEFAULTS")},
    ),
    "state_variables_absent": _pair(drop=("state_variables",)),
    "types_wire_values": _pair(
        {"state_variables": {
            "power": {"type": "boolean"},
            "level": {"type": "integer"},
            "gain": {"type": "number"},
            "trim": {"type": "float"},
            "mode": {"type": "enum", "values": ["a", "b"]},
            "src": {"type": "enum", "values": _Code("COMPUTED")},
        }},
        {"initial_state": {
            "power": "on", "level": True, "gain": "1.5", "trim": False, "mode": "c",
            "src": "x",
        }},
    ),
    "types_seed_computed_value": _pair(
        sim={"initial_state": {"power": _Code("DEFAULTS"), "level": 0}},
    ),
    # ── transport ──
    "transport_same": _pair({"transport": "udp"}, {"transport": "udp"}),
    "transport_serial_over_tcp": _pair({"transport": "serial"}),
    "transport_ssh_over_tcp": _pair({"transport": "ssh"}),
    "transport_serial_over_udp": _pair({"transport": "serial"}, {"transport": "udp"}),
    "transport_tcp_against_udp": _pair(sim={"transport": "udp"}),
    "transport_http_against_tcp": _pair({"transport": "http"}),
    "transport_driver_absent_sim_udp": _pair(sim={"transport": "udp"}, drop=("transport",)),
    "transport_sim_absent_driver_serial": _pair(
        {"transport": "serial"}, sim_drop=("transport",),
    ),
    "transport_driver_computed": _pair({"transport": _Code("COMPUTED")}),
    "transport_sim_computed": _pair(sim={"transport": _Code("DEFAULTS")}),
    # ── command names ──
    "commands_all_mentioned": _pair(),
    "commands_none_mentioned": _pair(body="    pass\n"),
    "commands_prefix_is_not_a_mention": _pair(
        {"commands": {"power": {}, "power_on": {}}},
        body="    def h(self):\n        return 'power_on'\n",
    ),
    "commands_name_after_an_underscore": _pair(
        {"commands": {"on": {}, "power_on": {}}},
        body="    def h(self):\n        return 'power_on'\n",
    ),
    "commands_inside_a_dotted_name": _pair(
        {"commands": {"set_bitrate": {}, "set_level": {}}},
        body="    def h(self):\n        return 'stream.set_bitrate'\n",
    ),
    "commands_six_unmentioned": _pair(
        {"commands": {f"cmd_{i}": {} for i in range(6)}}, body="    pass\n",
    ),
    "commands_five_unmentioned": _pair(
        {"commands": {f"cmd_{i}": {} for i in range(5)}}, body="    pass\n",
    ),
    "commands_name_a_number": _pair({"commands": {5: {}, "power_on": {}}}),
    "commands_computed": _pair({"commands": _Code("COMPUTED")}),
    "commands_absent": _pair(drop=("commands",)),
    "commands_empty": _pair({"commands": {}}),
    "commands_regex_characters": _pair(
        {"commands": {"go+": {}, "a.b": {}}},
        body="    def h(self):\n        return 'go+ aXb'\n",
    ),
    # ── everything at once, in order ──
    "everything_reported_in_order": _pair(
        {"transport": "serial", "state_variables": {
            "power": {"type": "boolean"}, "mode": {"type": "enum", "values": ["a"]},
        }},
        {
            "driver_id": "acme_other", "transport": "udp", "capacity": "fast",
            "single_client": "first", "initial_state": {"power": 1, "mode": "b"},
        },
        sim_drop=("name",),
        body="    pass\n",
    ),
}


def _python_issues(tmp_path: Path, name: str):
    """Every line the Python door says, or what it raises."""
    driver_source, sim_source = PYTHON_CASES[name]
    driver = tmp_path / "acme_widget.py"
    driver.write_text(driver_source, encoding="utf-8")
    if sim_source is not None:
        (tmp_path / "acme_widget_sim.py").write_text(sim_source, encoding="utf-8")
    try:
        result = validate_python_driver(driver)
    except Exception as exc:  # noqa: BLE001 - what raises is pinned too
        return ("raises", type(exc).__name__, str(exc).replace(str(tmp_path), "<tmp>"))
    return {
        "driver_id": result.driver_id,
        "issues": [
            (i.severity, i.check, i.message.replace(str(tmp_path), "<tmp>"))
            for i in result.issues
        ],
    }


@pytest.mark.parametrize("name", sorted(PYTHON_CASES))
def test_the_python_door_says_what_it_said(tmp_path, name):
    assert _python_issues(tmp_path, name) == PYTHON_EXPECTED[name]


# ── The command line ──


_CLEAN_YAML = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "author": "Acme",
    "transport": "tcp",
    "delimiter": "\\r\\n",
    "default_config": {"host": "", "port": 23, "poll_interval": 0},
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {"power_on": {"label": "Power On", "send": "PWR 1\\r\\n"}},
    "responses": [{"match": "^PWR (\\d)$", "set": {"power": "$1"}}],
    "simulator": {"initial_state": {"power": False}},
}


def _cli_yaml(**changes) -> str:
    definition = copy.deepcopy(_CLEAN_YAML)
    for key, value in changes.items():
        if value == "_drop":
            definition.pop(key, None)
        else:
            definition[key] = value
    return yaml.safe_dump(definition, sort_keys=False)


# Each case: the files to write (relative name -> text) and the argument
# list, ``{tmp}`` standing for the temporary folder.
CLI_CASES: dict[str, tuple[dict[str, str], list[str]]] = {
    "one_clean_yaml": ({"acme_widget.avcdriver": _cli_yaml()}, ["{tmp}/acme_widget.avcdriver"]),
    "one_yaml_with_infos": (
        {"acme_widget.avcdriver": _cli_yaml(
            state_variables={
                "power": {"type": "boolean", "label": "Power"},
                "level": {"type": "integer", "label": "Level"},
            },
        )},
        ["{tmp}/acme_widget.avcdriver"],
    ),
    "one_yaml_with_a_warning": (
        {"acme_widget.avcdriver": _cli_yaml(simulator={
            "initial_state": {"power": False}, "single_client": "newest",
        }, transport="udp", delimiter="_drop")},
        ["{tmp}/acme_widget.avcdriver"],
    ),
    "one_yaml_with_an_error": (
        {"acme_widget.avcdriver": _cli_yaml(simulator={
            "initial_state": {"power": False}, "capacity": "fast",
        })},
        ["{tmp}/acme_widget.avcdriver"],
    ),
    "one_yaml_failing_the_contract": (
        {"acme_widget.avcdriver": _cli_yaml(commands={"power_on": {"labl": "x", "send": "P"}})},
        ["{tmp}/acme_widget.avcdriver"],
    ),
    "one_python_pair": (
        {"acme_widget.py": _GOOD_DRIVER, "acme_widget_sim.py": _GOOD_SIM},
        ["{tmp}/acme_widget.py"],
    ),
    "a_folder_of_drivers": (
        {
            "a/acme_widget.avcdriver": _cli_yaml(),
            "b/acme_other.avcdriver": _cli_yaml(id="acme_other", simulator={
                "initial_state": {"power": False}, "capacity": "fast",
            }),
            "c/acme_widget.py": _GOOD_DRIVER,
            "c/acme_widget_sim.py": _GOOD_SIM,
            "d/acme_warn.avcdriver": _cli_yaml(id="acme_warn", simulator={
                "initial_state": {"power": False}, "single_client": "newest",
            }, transport="udp", delimiter="_drop"),
        },
        ["{tmp}"],
    ),
    "a_folder_summarised": (
        {
            "a/acme_widget.avcdriver": _cli_yaml(),
            "b/acme_other.avcdriver": _cli_yaml(id="acme_other", simulator={
                "initial_state": {"power": False}, "capacity": "fast",
            }),
            "c/acme_widget.py": _GOOD_DRIVER,
            "c/acme_widget_sim.py": _GOOD_SIM,
            "d/acme_warn.avcdriver": _cli_yaml(id="acme_warn", simulator={
                "initial_state": {"power": False}, "single_client": "newest",
            }, transport="udp", delimiter="_drop"),
            "e/acme_infos.avcdriver": _cli_yaml(id="acme_infos", state_variables={
                "power": {"type": "boolean", "label": "Power"},
                "level": {"type": "integer", "label": "Level"},
            }),
        },
        ["{tmp}", "--summary"],
    ),
    "summary_of_a_clean_driver": (
        {"acme_widget.avcdriver": _cli_yaml()}, ["--summary", "{tmp}/acme_widget.avcdriver"],
    ),
    "summary_of_a_contract_failure": (
        {"acme_widget.avcdriver": _cli_yaml(commands={"power_on": {"labl": "x", "send": "P"}})},
        ["{tmp}/acme_widget.avcdriver", "--summary"],
    ),
    "one_yaml_with_infos_only": (
        {"acme_widget.avcdriver": _cli_yaml(liveness={"send": "PWR?\\r\\n"})},
        ["{tmp}/acme_widget.avcdriver"],
    ),
    "summary_of_infos_only": (
        {"acme_widget.avcdriver": _cli_yaml(liveness={"send": "PWR?\\r\\n"})},
        ["{tmp}/acme_widget.avcdriver", "--summary"],
    ),
    "a_path_that_does_not_exist": ({}, ["{tmp}/nowhere.avcdriver"]),
    "a_folder_with_no_drivers": ({"notes.txt": "nothing here\n"}, ["{tmp}"]),
}


def _cli(tmp_path: Path, name: str, capsys) -> dict:
    files, argv = CLI_CASES[name]
    for relative, text in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    args = [a.replace("{tmp}", str(tmp_path)) for a in argv]
    with pytest.raises(SystemExit) as exit_info:
        main(args)
    captured = capsys.readouterr()
    return {
        "code": exit_info.value.code,
        "out": captured.out.replace(str(tmp_path), "<tmp>").splitlines(),
        "err": captured.err.replace(str(tmp_path), "<tmp>").splitlines(),
    }


@pytest.mark.parametrize("name", sorted(CLI_CASES))
def test_the_command_line_says_what_it_said(tmp_path, name, capsys):
    assert _cli(tmp_path, name, capsys) == CLI_EXPECTED[name]


def test_every_case_has_an_expectation():
    assert sorted(YAML_EXPECTED) == sorted(YAML_CASES)
    assert sorted(PYTHON_EXPECTED) == sorted(PYTHON_CASES)
    assert sorted(CLI_EXPECTED) == sorted(CLI_CASES)


# ── Expectations, generated on the code before the restructure ──

YAML_EXPECTED: dict = {'bool_ref_after_the_seed_types': [('info',
                                    'type_consistency',
                                    "State variable 'power' is boolean but initial_state value is "
                                    "str: 'on'. Expected when the wire form differs from the "
                                    "logical type — confirm the driver's response rules map it "
                                    'back.'),
                                   ('info',
                                    'type_consistency',
                                    "State variable 'level' is integer but initial_state value is "
                                    "str: 'x'. Expected when the wire form differs from the "
                                    "logical type — confirm the driver's response rules map it "
                                    'back.'),
                                   ('error',
                                    'type_consistency',
                                    "Handler uses {state.power} in respond: template, but 'power' "
                                    "is boolean. Python's str(True) produces 'True' not 'true'. "
                                    'Use a script handler with str(state["power"]).lower() '
                                    'instead.')],
 'bool_ref_handler_not_a_mapping': [('info',
                                     'state_coverage',
                                     "'power' will use auto-gen default False. Add to "
                                     'simulator.initial_state to override.'),
                                    ('error',
                                     'type_consistency',
                                     "Handler uses {state.power} in respond: template, but 'power' "
                                     "is boolean. Python's str(True) produces 'True' not 'true'. "
                                     'Use a script handler with str(state["power"]).lower() '
                                     'instead.')],
 'bool_ref_in_two_handlers': [('info',
                               'state_coverage',
                               "'power' will use auto-gen default False. Add to "
                               'simulator.initial_state to override.'),
                              ('error',
                               'type_consistency',
                               "Handler uses {state.power} in respond: template, but 'power' is "
                               "boolean. Python's str(True) produces 'True' not 'true'. Use a "
                               'script handler with str(state["power"]).lower() instead.'),
                              ('error',
                               'type_consistency',
                               "Handler uses {state.power} in respond: template, but 'power' is "
                               "boolean. Python's str(True) produces 'True' not 'true'. Use a "
                               'script handler with str(state["power"]).lower() instead.')],
 'bool_ref_named_twice': [('info',
                           'state_coverage',
                           "'power' will use auto-gen default False. Add to "
                           'simulator.initial_state to override.'),
                          ('error',
                           'type_consistency',
                           "Handler uses {state.power} in respond: template, but 'power' is "
                           "boolean. Python's str(True) produces 'True' not 'true'. Use a script "
                           'handler with str(state["power"]).lower() instead.'),
                          ('error',
                           'type_consistency',
                           "Handler uses {state.power} in respond: template, but 'power' is "
                           "boolean. Python's str(True) produces 'True' not 'true'. Use a script "
                           'handler with str(state["power"]).lower() instead.')],
 'bool_ref_type_a_list': [('info',
                           'state_coverage',
                           "'power' will use auto-gen default ''. Add to simulator.initial_state "
                           'to override.')],
 'bool_ref_unknown_name': [('info',
                            'state_coverage',
                            "'power' will use auto-gen default False. Add to "
                            'simulator.initial_state to override.')],
 'bool_ref_variable_not_a_mapping': ('raises',
                                     'AttributeError',
                                     "'str' object has no attribute 'get'"),
 'capacity_bad_fields': [('info',
                          'state_coverage',
                          "'power' will use auto-gen default False. Add to simulator.initial_state "
                          'to override.'),
                         ('error',
                          'capacity',
                          'simulator.capacity: capacity.process_ms is required'),
                         ('error',
                          'capacity',
                          'simulator.capacity: capacity.rx_buffer must be a whole number of bytes '
                          'from 1 to 1048576')],
 'capacity_missing_field': [('info',
                             'state_coverage',
                             "'power' will use auto-gen default False. Add to "
                             'simulator.initial_state to override.'),
                            ('error',
                             'capacity',
                             'simulator.capacity: capacity.process_ms is required')],
 'capacity_null': [('info',
                    'state_coverage',
                    "'power' will use auto-gen default False. Add to simulator.initial_state to "
                    'override.'),
                   ('error',
                    'capacity',
                    'simulator.capacity: capacity must be a mapping with rx_buffer and '
                    'process_ms')],
 'capacity_text': [('info',
                    'state_coverage',
                    "'power' will use auto-gen default False. Add to simulator.initial_state to "
                    'override.'),
                   ('error',
                    'capacity',
                    'simulator.capacity: capacity must be a mapping with rx_buffer and '
                    'process_ms')],
 'capacity_then_single_client': [('info',
                                  'state_coverage',
                                  "'power' will use auto-gen default False. Add to "
                                  'simulator.initial_state to override.'),
                                 ('error',
                                  'capacity',
                                  'simulator.capacity: capacity must be a mapping with rx_buffer '
                                  'and process_ms'),
                                 ('error',
                                  'single_client',
                                  'simulator.single_client: single_client must be newest, oldest '
                                  'or reject'),
                                 ('warning',
                                  'single_client',
                                  'simulator.single_client applies to a TCP device; a osc device '
                                  'answers its one connection')],
 'capacity_valid': [('info',
                     'state_coverage',
                     "'power' will use auto-gen default False. Add to simulator.initial_state to "
                     'override.')],
 'coverage_boolean': [('info',
                       'state_coverage',
                       "'v' will use auto-gen default False. Add to simulator.initial_state to "
                       'override.')],
 'coverage_empty_section_no_variables': [('warning',
                                          'state_coverage',
                                          'No state_variables defined')],
 'coverage_enum': [('info',
                    'state_coverage',
                    "'v' will use auto-gen default 'hdmi1'. Add to simulator.initial_state to "
                    'override.')],
 'coverage_enum_no_values': [('info',
                              'state_coverage',
                              "'v' will use auto-gen default ''. Add to simulator.initial_state to "
                              'override.')],
 'coverage_enum_values_missing': [('info',
                                   'state_coverage',
                                   "'v' will use auto-gen default ''. Add to "
                                   'simulator.initial_state to override.')],
 'coverage_every_variable_seeded': [],
 'coverage_float': [('info',
                     'state_coverage',
                     "'v' will use auto-gen default ''. Add to simulator.initial_state to "
                     'override.')],
 'coverage_integer_fractional_min': [('info',
                                      'state_coverage',
                                      "'v' will use auto-gen default 0.5. Add to "
                                      'simulator.initial_state to override.')],
 'coverage_integer_min': [('info',
                           'state_coverage',
                           "'v' will use auto-gen default 5. Add to simulator.initial_state to "
                           'override.')],
 'coverage_integer_no_min': [('info',
                              'state_coverage',
                              "'v' will use auto-gen default 0. Add to simulator.initial_state to "
                              'override.')],
 'coverage_integer_text_min': [('info',
                                'state_coverage',
                                "'v' will use auto-gen default 'low'. Add to "
                                'simulator.initial_state to override.')],
 'coverage_list_type': [('info',
                         'state_coverage',
                         "'v' will use auto-gen default ''. Add to simulator.initial_state to "
                         'override.')],
 'coverage_no_section_no_variables': [('warning', 'state_coverage', 'No state_variables defined')],
 'coverage_no_section_with_variables': [],
 'coverage_number': [('info',
                      'state_coverage',
                      "'v' will use auto-gen default 0.0. Add to simulator.initial_state to "
                      'override.')],
 'coverage_section_without_initial_state': [('info',
                                             'state_coverage',
                                             "'v' will use auto-gen default 0. Add to "
                                             'simulator.initial_state to override.')],
 'coverage_several_in_order': [('info',
                                'state_coverage',
                                "'z' will use auto-gen default 0. Add to simulator.initial_state "
                                'to override.'),
                               ('info',
                                'state_coverage',
                                "'a' will use auto-gen default ''. Add to simulator.initial_state "
                                'to override.'),
                               ('info',
                                'state_coverage',
                                "'m' will use auto-gen default False. Add to "
                                'simulator.initial_state to override.')],
 'coverage_string': [('info',
                      'state_coverage',
                      "'v' will use auto-gen default ''. Add to simulator.initial_state to "
                      'override.')],
 'coverage_type_missing': [('info',
                            'state_coverage',
                            "'v' will use auto-gen default ''. Add to simulator.initial_state to "
                            'override.')],
 'coverage_unknown_type': [('info',
                            'state_coverage',
                            "'v' will use auto-gen default ''. Add to simulator.initial_state to "
                            'override.')],
 'coverage_variable_not_a_mapping': ('raises',
                                     'AttributeError',
                                     "'str' object has no attribute 'get'"),
 'coverage_variables_a_list': ('raises',
                               'AttributeError',
                               "'list' object has no attribute 'items'"),
 'single_client_bogus': [('info',
                          'state_coverage',
                          "'power' will use auto-gen default False. Add to simulator.initial_state "
                          'to override.'),
                         ('error',
                          'single_client',
                          'simulator.single_client: single_client must be newest, oldest or '
                          'reject')],
 'single_client_bogus_on_http': [('info',
                                  'state_coverage',
                                  "'power' will use auto-gen default False. Add to "
                                  'simulator.initial_state to override.'),
                                 ('error',
                                  'single_client',
                                  'simulator.single_client: single_client must be newest, oldest '
                                  'or reject'),
                                 ('warning',
                                  'single_client',
                                  'simulator.single_client applies to a TCP device; a http device '
                                  'answers its one connection')],
 'single_client_newest': [('info',
                           'state_coverage',
                           "'power' will use auto-gen default False. Add to "
                           'simulator.initial_state to override.')],
 'single_client_null': [('info',
                         'state_coverage',
                         "'power' will use auto-gen default False. Add to simulator.initial_state "
                         'to override.'),
                        ('error',
                         'single_client',
                         'simulator.single_client: single_client must be newest, oldest or '
                         'reject')],
 'single_client_on_serial': [('info',
                              'state_coverage',
                              "'power' will use auto-gen default False. Add to "
                              'simulator.initial_state to override.'),
                             ('warning',
                              'single_client',
                              'simulator.single_client applies to a TCP device; a serial device '
                              'answers its one connection')],
 'single_client_reject_on_udp': [('info',
                                  'state_coverage',
                                  "'power' will use auto-gen default False. Add to "
                                  'simulator.initial_state to override.'),
                                 ('warning',
                                  'single_client',
                                  'simulator.single_client applies to a TCP device; a udp device '
                                  'answers its one connection')],
 'single_client_transport_absent': [('info',
                                     'state_coverage',
                                     "'power' will use auto-gen default False. Add to "
                                     'simulator.initial_state to override.')],
 'single_client_transport_null': [('info',
                                   'state_coverage',
                                   "'power' will use auto-gen default False. Add to "
                                   'simulator.initial_state to override.')],
 'types_boolean_given_bool': [],
 'types_boolean_given_int': [('info',
                              'type_consistency',
                              "State variable 'v' is boolean but initial_state value is int: 1. "
                              'Expected when the wire form differs from the logical type — confirm '
                              "the driver's response rules map it back.")],
 'types_boolean_given_none': [('info',
                               'type_consistency',
                               "State variable 'v' is boolean but initial_state value is NoneType: "
                               'None. Expected when the wire form differs from the logical type — '
                               "confirm the driver's response rules map it back.")],
 'types_boolean_given_text': [('info',
                               'type_consistency',
                               "State variable 'v' is boolean but initial_state value is str: "
                               "'on'. Expected when the wire form differs from the logical type — "
                               "confirm the driver's response rules map it back.")],
 'types_enum_number_listed_as_text': [],
 'types_enum_value_listed': [],
 'types_enum_value_listed_as_text': [],
 'types_enum_value_unlisted': [('info',
                                'type_consistency',
                                "State variable 'v' initial value 'c' not in enum values: ['a', "
                                "'b']. Expected when the wire form differs from the logical type — "
                                "confirm the driver's response rules map it back.")],
 'types_enum_values_a_mapping': [('info',
                                  'type_consistency',
                                  "State variable 'v' is an enum whose values are computed, so its "
                                  "initial value 'x' could not be checked against them.")],
 'types_enum_values_empty': [],
 'types_enum_values_missing': [],
 'types_enum_values_text': [('info',
                             'type_consistency',
                             "State variable 'v' is an enum whose values are computed, so its "
                             "initial value 'a' could not be checked against them.")],
 'types_float_given_bool': [('info',
                             'type_consistency',
                             "State variable 'v' is float but initial_state value is bool: True. "
                             'Expected when the wire form differs from the logical type — confirm '
                             "the driver's response rules map it back.")],
 'types_float_given_float': [],
 'types_float_given_list': [('info',
                             'type_consistency',
                             "State variable 'v' is float but initial_state value is list: [1]. "
                             'Expected when the wire form differs from the logical type — confirm '
                             "the driver's response rules map it back.")],
 'types_initial_state_a_list': [],
 'types_integer_given_bool': [('info',
                               'type_consistency',
                               "State variable 'v' is integer but initial_state value is bool: "
                               'True. Expected when the wire form differs from the logical type — '
                               "confirm the driver's response rules map it back.")],
 'types_integer_given_float': [('info',
                                'type_consistency',
                                "State variable 'v' is integer but initial_state value is float: "
                                '5.0. Expected when the wire form differs from the logical type — '
                                "confirm the driver's response rules map it back.")],
 'types_integer_given_int': [],
 'types_integer_given_text': [('info',
                               'type_consistency',
                               "State variable 'v' is integer but initial_state value is str: '5'. "
                               'Expected when the wire form differs from the logical type — '
                               "confirm the driver's response rules map it back.")],
 'types_list_type': [],
 'types_mapping_type': [],
 'types_number_given_bool': [('info',
                              'type_consistency',
                              "State variable 'v' is number but initial_state value is bool: "
                              'False. Expected when the wire form differs from the logical type — '
                              "confirm the driver's response rules map it back.")],
 'types_number_given_float': [],
 'types_number_given_int': [],
 'types_number_given_text': [('info',
                              'type_consistency',
                              "State variable 'v' is number but initial_state value is str: '1.5'. "
                              'Expected when the wire form differs from the logical type — confirm '
                              "the driver's response rules map it back.")],
 'types_numeric_type': [],
 'types_several_in_declared_order': [('info',
                                      'type_consistency',
                                      "State variable 'z' is integer but initial_state value is "
                                      "str: '2'. Expected when the wire form differs from the "
                                      "logical type — confirm the driver's response rules map it "
                                      'back.'),
                                     ('info',
                                      'type_consistency',
                                      "State variable 'a' is boolean but initial_state value is "
                                      "str: 'no'. Expected when the wire form differs from the "
                                      "logical type — confirm the driver's response rules map it "
                                      'back.'),
                                     ('info',
                                      'type_consistency',
                                      "State variable 'm' initial value 'y' not in enum values: "
                                      "['x']. Expected when the wire form differs from the logical "
                                      "type — confirm the driver's response rules map it back."),
                                     ('info',
                                      'type_consistency',
                                      "State variable 'n' is number but initial_state value is "
                                      "str: '1'. Expected when the wire form differs from the "
                                      "logical type — confirm the driver's response rules map it "
                                      'back.')],
 'types_string_given_int': [],
 'types_type_missing_given_int': [],
 'types_type_missing_given_text': [],
 'types_unknown_type': [],
 'types_variable_name_a_number': [],
 'types_variable_not_a_mapping': []}

PYTHON_EXPECTED: dict = {'capacity_and_single_client_bad_with_missing_name': {'driver_id': 'acme_widget',
                                                      'issues': [('error',
                                                                  'simulator_info',
                                                                  'SIMULATOR_INFO missing required '
                                                                  "field: 'name'"),
                                                                 ('error',
                                                                  'capacity',
                                                                  'SIMULATOR_INFO["capacity"]: '
                                                                  'capacity must be a mapping with '
                                                                  'rx_buffer and process_ms'),
                                                                 ('error',
                                                                  'single_client',
                                                                  'SIMULATOR_INFO["single_client"]: '
                                                                  'single_client must be newest, '
                                                                  'oldest or reject'),
                                                                 ('warning',
                                                                  'single_client',
                                                                  'SIMULATOR_INFO["single_client"] '
                                                                  'applies to a TCP device; a udp '
                                                                  'device answers its one '
                                                                  'connection')]},
 'capacity_bad': {'driver_id': 'acme_widget',
                  'issues': [('error',
                              'capacity',
                              'SIMULATOR_INFO["capacity"]: capacity.process_ms is required'),
                             ('error',
                              'capacity',
                              'SIMULATOR_INFO["capacity"]: capacity.rx_buffer must be a whole '
                              'number of bytes from 1 to 1048576')]},
 'capacity_computed': {'driver_id': 'acme_widget',
                       'issues': [('info',
                                   'capacity',
                                   'SIMULATOR_INFO["capacity"] is computed, so it could not be '
                                   'checked.')]},
 'capacity_none': {'driver_id': 'acme_widget',
                   'issues': [('error',
                               'capacity',
                               'SIMULATOR_INFO["capacity"]: capacity must be a mapping with '
                               'rx_buffer and process_ms')]},
 'capacity_valid': {'driver_id': 'acme_widget', 'issues': []},
 'commands_absent': {'driver_id': 'acme_widget', 'issues': []},
 'commands_all_mentioned': {'driver_id': 'acme_widget', 'issues': []},
 'commands_computed': {'driver_id': 'acme_widget',
                       'issues': [('info',
                                   'command_coverage',
                                   'DRIVER_INFO commands are computed, so command coverage could '
                                   'not be checked.')]},
 'commands_empty': {'driver_id': 'acme_widget', 'issues': []},
 'commands_five_unmentioned': {'driver_id': 'acme_widget',
                               'issues': [('info',
                                           'command_coverage',
                                           '5 of 5 DRIVER_INFO command name(s) do not appear in '
                                           "the simulator source: 'cmd_0', 'cmd_1', 'cmd_2', "
                                           "'cmd_3', 'cmd_4'. A simulator that dispatches on raw "
                                           'protocol bytes will never mention them, so this is '
                                           'only worth checking when the simulator does dispatch '
                                           'by name.')]},
 'commands_inside_a_dotted_name': {'driver_id': 'acme_widget',
                                   'issues': [('info',
                                               'command_coverage',
                                               '1 of 2 DRIVER_INFO command name(s) do not appear '
                                               "in the simulator source: 'set_level'. A simulator "
                                               'that dispatches on raw protocol bytes will never '
                                               'mention them, so this is only worth checking when '
                                               'the simulator does dispatch by name.')]},
 'commands_name_a_number': {'driver_id': 'acme_widget',
                            'issues': [('info',
                                        'command_coverage',
                                        '1 of 2 DRIVER_INFO command name(s) do not appear in the '
                                        "simulator source: '<unevaluated>'. A simulator that "
                                        'dispatches on raw protocol bytes will never mention them, '
                                        'so this is only worth checking when the simulator does '
                                        'dispatch by name.')]},
 'commands_name_after_an_underscore': {'driver_id': 'acme_widget',
                                       'issues': [('info',
                                                   'command_coverage',
                                                   '1 of 2 DRIVER_INFO command name(s) do not '
                                                   "appear in the simulator source: 'on'. A "
                                                   'simulator that dispatches on raw protocol '
                                                   'bytes will never mention them, so this is only '
                                                   'worth checking when the simulator does '
                                                   'dispatch by name.')]},
 'commands_none_mentioned': {'driver_id': 'acme_widget',
                             'issues': [('info',
                                         'command_coverage',
                                         '2 of 2 DRIVER_INFO command name(s) do not appear in the '
                                         "simulator source: 'power_on', 'set_level'. A simulator "
                                         'that dispatches on raw protocol bytes will never mention '
                                         'them, so this is only worth checking when the simulator '
                                         'does dispatch by name.')]},
 'commands_prefix_is_not_a_mention': {'driver_id': 'acme_widget', 'issues': []},
 'commands_regex_characters': {'driver_id': 'acme_widget',
                               'issues': [('info',
                                           'command_coverage',
                                           '1 of 2 DRIVER_INFO command name(s) do not appear in '
                                           "the simulator source: 'a.b'. A simulator that "
                                           'dispatches on raw protocol bytes will never mention '
                                           'them, so this is only worth checking when the '
                                           'simulator does dispatch by name.')]},
 'commands_six_unmentioned': {'driver_id': 'acme_widget',
                              'issues': [('info',
                                          'command_coverage',
                                          '6 of 6 DRIVER_INFO command name(s) do not appear in the '
                                          "simulator source: 'cmd_0', 'cmd_1', 'cmd_2', 'cmd_3', "
                                          "'cmd_4', … (+1 more). A simulator that dispatches on "
                                          'raw protocol bytes will never mention them, so this is '
                                          'only worth checking when the simulator does dispatch by '
                                          'name.')]},
 'driver_clean': {'driver_id': 'acme_widget', 'issues': []},
 'driver_id_a_number': {'driver_id': 'acme_widget',
                        'issues': [('error',
                                    'driver_id_match',
                                    'DRIVER_INFO id (7) != SIMULATOR_INFO driver_id '
                                    "('acme_widget')")]},
 'driver_id_computed': {'driver_id': 'acme_widget',
                        'issues': [('error',
                                    'driver_id_match',
                                    'DRIVER_INFO id (<unevaluated>) != SIMULATOR_INFO driver_id '
                                    "('acme_widget')")]},
 'driver_id_mismatch': {'driver_id': 'acme_widget',
                        'issues': [('error',
                                    'driver_id_match',
                                    "DRIVER_INFO id ('acme_widget') != SIMULATOR_INFO driver_id "
                                    "('acme_other')")]},
 'driver_id_mismatch_computed': {'driver_id': 'acme_widget',
                                 'issues': [('error',
                                             'driver_id_match',
                                             "DRIVER_INFO id ('acme_widget') != SIMULATOR_INFO "
                                             'driver_id (<unevaluated>)')]},
 'driver_id_mismatch_empty': {'driver_id': 'acme_widget', 'issues': []},
 'driver_id_mismatch_number_id': {'driver_id': 'acme_widget',
                                  'issues': [('error',
                                              'driver_id_match',
                                              'DRIVER_INFO id (7) != SIMULATOR_INFO driver_id '
                                              "('acme_widget')")]},
 'driver_info_annotated': {'driver_id': 'acme_widget', 'issues': []},
 'driver_info_at_module_level': {'driver_id': 'acme_widget',
                                 'issues': [('error',
                                             'driver_info',
                                             'acme_widget.py: no DRIVER_INFO class attribute '
                                             'found. Python drivers must define `DRIVER_INFO = '
                                             '{...}` inside a class body.')]},
 'driver_info_empty_id': {'driver_id': 'acme_widget',
                          'issues': [('error',
                                      'driver_info',
                                      'Could not extract DRIVER_INFO from driver file')]},
 'driver_info_not_a_dict': {'driver_id': 'acme_widget',
                            'issues': [('error',
                                        'driver_info',
                                        'acme_widget.py: no DRIVER_INFO class attribute found. '
                                        'Python drivers must define `DRIVER_INFO = {...}` inside a '
                                        'class body.')]},
 'driver_info_without_id': {'driver_id': 'acme_widget',
                            'issues': [('error',
                                        'driver_info',
                                        'Could not extract DRIVER_INFO from driver file')]},
 'driver_syntax_error': {'driver_id': 'acme_widget',
                         'issues': [('error',
                                     'driver_info',
                                     "acme_widget.py has a syntax error: '{' was never closed "
                                     '(<unknown>, line 2)')]},
 'everything_reported_in_order': {'driver_id': 'acme_widget',
                                  'issues': [('error',
                                              'simulator_info',
                                              "SIMULATOR_INFO missing required field: 'name'"),
                                             ('error',
                                              'capacity',
                                              'SIMULATOR_INFO["capacity"]: capacity must be a '
                                              'mapping with rx_buffer and process_ms'),
                                             ('error',
                                              'single_client',
                                              'SIMULATOR_INFO["single_client"]: single_client must '
                                              'be newest, oldest or reject'),
                                             ('warning',
                                              'single_client',
                                              'SIMULATOR_INFO["single_client"] applies to a TCP '
                                              'device; a serial device answers its one connection'),
                                             ('error',
                                              'driver_id_match',
                                              "DRIVER_INFO id ('acme_widget') != SIMULATOR_INFO "
                                              "driver_id ('acme_other')"),
                                             ('info',
                                              'type_consistency',
                                              "State variable 'power' is boolean but initial_state "
                                              'value is int: 1. Expected when the wire form '
                                              'differs from the logical type — confirm the '
                                              "driver's response rules map it back."),
                                             ('info',
                                              'type_consistency',
                                              "State variable 'mode' initial value 'b' not in enum "
                                              "values: ['a']. Expected when the wire form differs "
                                              "from the logical type — confirm the driver's "
                                              'response rules map it back.'),
                                             ('error',
                                              'transport_match',
                                              "DRIVER_INFO transport ('serial') != SIMULATOR_INFO "
                                              "transport ('udp')"),
                                             ('info',
                                              'command_coverage',
                                              '2 of 2 DRIVER_INFO command name(s) do not appear in '
                                              "the simulator source: 'power_on', 'set_level'. A "
                                              'simulator that dispatches on raw protocol bytes '
                                              'will never mention them, so this is only worth '
                                              'checking when the simulator does dispatch by '
                                              'name.')]},
 'required_all_missing': {'driver_id': 'acme_widget',
                          'issues': [('error',
                                      'simulator_info',
                                      "SIMULATOR_INFO missing required field: 'driver_id'"),
                                     ('error',
                                      'simulator_info',
                                      "SIMULATOR_INFO missing required field: 'name'"),
                                     ('error',
                                      'simulator_info',
                                      "SIMULATOR_INFO missing required field: 'initial_state'"),
                                     ('warning',
                                      'state_coverage',
                                      'SIMULATOR_INFO seeds no initial_state at all, but the '
                                      'driver declares 2 state variable(s) — the simulator models '
                                      'none of this device, so nothing it answers can exercise the '
                                      "driver's parsing.")]},
 'required_driver_id_missing': {'driver_id': 'acme_widget',
                                'issues': [('error',
                                            'simulator_info',
                                            "SIMULATOR_INFO missing required field: 'driver_id'")]},
 'required_initial_state_missing': {'driver_id': 'acme_widget',
                                    'issues': [('error',
                                                'simulator_info',
                                                'SIMULATOR_INFO missing required field: '
                                                "'initial_state'"),
                                               ('warning',
                                                'state_coverage',
                                                'SIMULATOR_INFO seeds no initial_state at all, but '
                                                'the driver declares 2 state variable(s) — the '
                                                'simulator models none of this device, so nothing '
                                                "it answers can exercise the driver's parsing.")]},
 'required_name_missing': {'driver_id': 'acme_widget',
                           'issues': [('error',
                                       'simulator_info',
                                       "SIMULATOR_INFO missing required field: 'name'")]},
 'sim_info_annotated': {'driver_id': 'acme_widget', 'issues': []},
 'sim_info_annotated_then_assigned': {'driver_id': 'acme_widget', 'issues': []},
 'sim_info_annotated_without_value': {'driver_id': 'acme_widget',
                                      'issues': [('error',
                                                  'simulator_info',
                                                  'Could not extract SIMULATOR_INFO from simulator '
                                                  'file')]},
 'sim_info_at_module_level': {'driver_id': 'acme_widget',
                              'issues': [('error',
                                          'simulator_info',
                                          'Could not extract SIMULATOR_INFO from simulator file')]},
 'sim_info_chained_assignment': {'driver_id': 'acme_widget',
                                 'issues': [('info',
                                             'command_coverage',
                                             '2 of 2 DRIVER_INFO command name(s) do not appear in '
                                             "the simulator source: 'power_on', 'set_level'. A "
                                             'simulator that dispatches on raw protocol bytes will '
                                             'never mention them, so this is only worth checking '
                                             'when the simulator does dispatch by name.')]},
 'sim_info_computed': {'driver_id': 'acme_widget',
                       'issues': [('error',
                                   'simulator_info',
                                   'Could not extract SIMULATOR_INFO from simulator file')]},
 'sim_info_empty': {'driver_id': 'acme_widget',
                    'issues': [('error',
                                'simulator_info',
                                'Could not extract SIMULATOR_INFO from simulator file')]},
 'sim_info_first_class_wins': {'driver_id': 'acme_widget',
                               'issues': [('error',
                                           'simulator_info',
                                           'SIMULATOR_INFO missing required field: '
                                           "'initial_state'"),
                                          ('error',
                                           'driver_id_match',
                                           "DRIVER_INFO id ('acme_widget') != SIMULATOR_INFO "
                                           "driver_id ('one')"),
                                          ('warning',
                                           'state_coverage',
                                           'SIMULATOR_INFO seeds no initial_state at all, but the '
                                           'driver declares 2 state variable(s) — the simulator '
                                           'models none of this device, so nothing it answers can '
                                           "exercise the driver's parsing."),
                                          ('info',
                                           'command_coverage',
                                           '2 of 2 DRIVER_INFO command name(s) do not appear in '
                                           "the simulator source: 'power_on', 'set_level'. A "
                                           'simulator that dispatches on raw protocol bytes will '
                                           'never mention them, so this is only worth checking '
                                           'when the simulator does dispatch by name.')]},
 'sim_info_in_second_class': {'driver_id': 'acme_widget',
                              'issues': [('warning',
                                          'state_coverage',
                                          'SIMULATOR_INFO seeds no initial_state at all, but the '
                                          'driver declares 2 state variable(s) — the simulator '
                                          'models none of this device, so nothing it answers can '
                                          "exercise the driver's parsing."),
                                         ('info',
                                          'command_coverage',
                                          '2 of 2 DRIVER_INFO command name(s) do not appear in the '
                                          "simulator source: 'power_on', 'set_level'. A simulator "
                                          'that dispatches on raw protocol bytes will never '
                                          'mention them, so this is only worth checking when the '
                                          'simulator does dispatch by name.')]},
 'sim_info_nested_class': {'driver_id': 'acme_widget',
                           'issues': [('warning',
                                       'state_coverage',
                                       'SIMULATOR_INFO seeds no initial_state at all, but the '
                                       'driver declares 2 state variable(s) — the simulator models '
                                       'none of this device, so nothing it answers can exercise '
                                       "the driver's parsing."),
                                      ('info',
                                       'command_coverage',
                                       '2 of 2 DRIVER_INFO command name(s) do not appear in the '
                                       "simulator source: 'power_on', 'set_level'. A simulator "
                                       'that dispatches on raw protocol bytes will never mention '
                                       'them, so this is only worth checking when the simulator '
                                       'does dispatch by name.')]},
 'sim_missing': {'driver_id': 'acme_widget',
                 'issues': [('warning',
                             'simulator_file',
                             'No simulator file found at acme_widget_sim.py')]},
 'sim_no_class': {'driver_id': 'acme_widget',
                  'issues': [('error',
                              'simulator_info',
                              'Could not extract SIMULATOR_INFO from simulator file')]},
 'sim_syntax_error': {'driver_id': 'acme_widget',
                      'issues': [('error',
                                  'simulator_info',
                                  'Could not extract SIMULATOR_INFO from simulator file')]},
 'single_client_bogus': {'driver_id': 'acme_widget',
                         'issues': [('error',
                                     'single_client',
                                     'SIMULATOR_INFO["single_client"]: single_client must be '
                                     'newest, oldest or reject')]},
 'single_client_computed': {'driver_id': 'acme_widget',
                            'issues': [('info',
                                        'single_client',
                                        'SIMULATOR_INFO["single_client"] is computed, so it could '
                                        'not be checked.')]},
 'single_client_driver_transport_absent': {'driver_id': 'acme_widget', 'issues': []},
 'single_client_driver_transport_computed': {'driver_id': 'acme_widget',
                                             'issues': [('info',
                                                         'transport_match',
                                                         'Driver transport <unevaluated> has no '
                                                         'simulator server, so it is simulated '
                                                         'over TCP. That is the supported '
                                                         'substitution, not a mismatch.')]},
 'single_client_on_serial': {'driver_id': 'acme_widget',
                             'issues': [('warning',
                                         'single_client',
                                         'SIMULATOR_INFO["single_client"] applies to a TCP device; '
                                         'a serial device answers its one connection'),
                                        ('info',
                                         'transport_match',
                                         "Driver transport 'serial' has no simulator server, so it "
                                         'is simulated over TCP. That is the supported '
                                         'substitution, not a mismatch.')]},
 'single_client_valid': {'driver_id': 'acme_widget', 'issues': []},
 'state_all_seeded': {'driver_id': 'acme_widget', 'issues': []},
 'state_both_computed': {'driver_id': 'acme_widget',
                         'issues': [('info',
                                     'state_coverage',
                                     'SIMULATOR_INFO initial_state is computed, so state coverage '
                                     'and initial-state types could not be checked.'),
                                    ('info',
                                     'state_coverage',
                                     'DRIVER_INFO state_variables are computed, so state coverage '
                                     'and initial-state types could not be checked.')]},
 'state_exactly_six_unseeded': {'driver_id': 'acme_widget',
                                'issues': [('info',
                                            'state_coverage',
                                            'SIMULATOR_INFO seeds 1 state key(s); 6 of the '
                                            "driver's 7 state variable(s) are not among them BY "
                                            'NAME: v1, v2, v3, v4, v5, v6. Usually correct — the '
                                            'simulator seeds what goes on the wire and the driver '
                                            'publishes what it computes from that, so the names '
                                            'differ. Worth a look only if the counts say the '
                                            'simulator models far less than the driver reads.')]},
 'state_many_unseeded': {'driver_id': 'acme_widget',
                         'issues': [('info',
                                     'state_coverage',
                                     "SIMULATOR_INFO seeds 1 state key(s); 8 of the driver's 9 "
                                     'state variable(s) are not among them BY NAME: v0, v1, v2, '
                                     'v4, v5, v6 (+2 more). Usually correct — the simulator seeds '
                                     'what goes on the wire and the driver publishes what it '
                                     'computes from that, so the names differ. Worth a look only '
                                     'if the counts say the simulator models far less than the '
                                     'driver reads.')]},
 'state_name_a_number': {'driver_id': 'acme_widget',
                         'issues': [('info',
                                     'state_coverage',
                                     "SIMULATOR_INFO seeds 1 state key(s); 1 of the driver's 2 "
                                     'state variable(s) are not among them BY NAME: <unevaluated>. '
                                     'Usually correct — the simulator seeds what goes on the wire '
                                     'and the driver publishes what it computes from that, so the '
                                     'names differ. Worth a look only if the counts say the '
                                     'simulator models far less than the driver reads.')]},
 'state_none_seeded': {'driver_id': 'acme_widget',
                       'issues': [('warning',
                                   'state_coverage',
                                   'SIMULATOR_INFO seeds no initial_state at all, but the driver '
                                   'declares 2 state variable(s) — the simulator models none of '
                                   "this device, so nothing it answers can exercise the driver's "
                                   'parsing.')]},
 'state_none_seeded_no_variables': {'driver_id': 'acme_widget', 'issues': []},
 'state_seeds_computed': {'driver_id': 'acme_widget',
                          'issues': [('info',
                                      'state_coverage',
                                      'SIMULATOR_INFO initial_state is computed, so state coverage '
                                      'and initial-state types could not be checked.')]},
 'state_some_seeded': {'driver_id': 'acme_widget',
                       'issues': [('info',
                                   'state_coverage',
                                   "SIMULATOR_INFO seeds 2 state key(s); 1 of the driver's 2 state "
                                   'variable(s) are not among them BY NAME: level. Usually correct '
                                   '— the simulator seeds what goes on the wire and the driver '
                                   'publishes what it computes from that, so the names differ. '
                                   'Worth a look only if the counts say the simulator models far '
                                   'less than the driver reads.')]},
 'state_variables_absent': {'driver_id': 'acme_widget', 'issues': []},
 'state_variables_computed': {'driver_id': 'acme_widget',
                              'issues': [('info',
                                          'state_coverage',
                                          'DRIVER_INFO state_variables are computed, so state '
                                          'coverage and initial-state types could not be '
                                          'checked.')]},
 'transport_driver_absent_sim_udp': {'driver_id': 'acme_widget',
                                     'issues': [('error',
                                                 'transport_match',
                                                 "DRIVER_INFO transport ('tcp') != SIMULATOR_INFO "
                                                 "transport ('udp')")]},
 'transport_driver_computed': {'driver_id': 'acme_widget',
                               'issues': [('info',
                                           'transport_match',
                                           'Driver transport <unevaluated> has no simulator '
                                           'server, so it is simulated over TCP. That is the '
                                           'supported substitution, not a mismatch.')]},
 'transport_http_against_tcp': {'driver_id': 'acme_widget',
                                'issues': [('error',
                                            'transport_match',
                                            "DRIVER_INFO transport ('http') != SIMULATOR_INFO "
                                            "transport ('tcp')")]},
 'transport_same': {'driver_id': 'acme_widget', 'issues': []},
 'transport_serial_over_tcp': {'driver_id': 'acme_widget',
                               'issues': [('info',
                                           'transport_match',
                                           "Driver transport 'serial' has no simulator server, so "
                                           'it is simulated over TCP. That is the supported '
                                           'substitution, not a mismatch.')]},
 'transport_serial_over_udp': {'driver_id': 'acme_widget',
                               'issues': [('error',
                                           'transport_match',
                                           "DRIVER_INFO transport ('serial') != SIMULATOR_INFO "
                                           "transport ('udp')")]},
 'transport_sim_absent_driver_serial': {'driver_id': 'acme_widget',
                                        'issues': [('info',
                                                    'transport_match',
                                                    "Driver transport 'serial' has no simulator "
                                                    'server, so it is simulated over TCP. That is '
                                                    'the supported substitution, not a '
                                                    'mismatch.')]},
 'transport_sim_computed': {'driver_id': 'acme_widget',
                            'issues': [('error',
                                        'transport_match',
                                        "DRIVER_INFO transport ('tcp') != SIMULATOR_INFO transport "
                                        '(<unevaluated>)')]},
 'transport_ssh_over_tcp': {'driver_id': 'acme_widget',
                            'issues': [('info',
                                        'transport_match',
                                        "Driver transport 'ssh' has no simulator server, so it is "
                                        'simulated over TCP. That is the supported substitution, '
                                        'not a mismatch.')]},
 'transport_tcp_against_udp': {'driver_id': 'acme_widget',
                               'issues': [('error',
                                           'transport_match',
                                           "DRIVER_INFO transport ('tcp') != SIMULATOR_INFO "
                                           "transport ('udp')")]},
 'types_seed_computed_value': {'driver_id': 'acme_widget',
                               'issues': [('info',
                                           'type_consistency',
                                           "State variable 'power' is boolean but initial_state "
                                           'value is _Unevaluated: <unevaluated>. Expected when '
                                           'the wire form differs from the logical type — confirm '
                                           "the driver's response rules map it back.")]},
 'types_wire_values': {'driver_id': 'acme_widget',
                       'issues': [('info',
                                   'type_consistency',
                                   "State variable 'power' is boolean but initial_state value is "
                                   "str: 'on'. Expected when the wire form differs from the "
                                   "logical type — confirm the driver's response rules map it "
                                   'back.'),
                                  ('info',
                                   'type_consistency',
                                   "State variable 'level' is integer but initial_state value is "
                                   'bool: True. Expected when the wire form differs from the '
                                   "logical type — confirm the driver's response rules map it "
                                   'back.'),
                                  ('info',
                                   'type_consistency',
                                   "State variable 'gain' is number but initial_state value is "
                                   "str: '1.5'. Expected when the wire form differs from the "
                                   "logical type — confirm the driver's response rules map it "
                                   'back.'),
                                  ('info',
                                   'type_consistency',
                                   "State variable 'trim' is float but initial_state value is "
                                   'bool: False. Expected when the wire form differs from the '
                                   "logical type — confirm the driver's response rules map it "
                                   'back.'),
                                  ('info',
                                   'type_consistency',
                                   "State variable 'mode' initial value 'c' not in enum values: "
                                   "['a', 'b']. Expected when the wire form differs from the "
                                   "logical type — confirm the driver's response rules map it "
                                   'back.'),
                                  ('info',
                                   'type_consistency',
                                   "State variable 'src' is an enum whose values are computed, so "
                                   "its initial value 'x' could not be checked against them.")]}}

CLI_EXPECTED: dict = {'a_folder_of_drivers': {'code': 1,
                         'err': [],
                         'out': ['',
                                 'WARN: acme_widget [yaml] (<tmp>/a/acme_widget.avcdriver)',
                                 '  WARN  [contract] Nothing notices if the device stops answering '
                                 'over tcp: it sends no poll at its default config, and there is '
                                 'no liveness block, so the device reads connected for as long as '
                                 'the connection stays open. Add a liveness block that asks '
                                 'something the device answers in every state.',
                                 '  INFO  [reply_framing] 1 simulator reply checked, all '
                                 "terminated with '\\\\r\\\\n'.",
                                 '',
                                 'FAIL: acme_other [yaml] (<tmp>/b/acme_other.avcdriver)',
                                 '  WARN  [contract] Nothing notices if the device stops answering '
                                 'over tcp: it sends no poll at its default config, and there is '
                                 'no liveness block, so the device reads connected for as long as '
                                 'the connection stays open. Add a liveness block that asks '
                                 'something the device answers in every state.',
                                 '  INFO  [reply_framing] 1 simulator reply checked, all '
                                 "terminated with '\\\\r\\\\n'.",
                                 '  ERROR [capacity] simulator.capacity: capacity must be a '
                                 'mapping with rx_buffer and process_ms',
                                 '',
                                 'PASS: acme_widget [python]',
                                 '',
                                 'WARN: acme_warn [yaml] (<tmp>/d/acme_warn.avcdriver)',
                                 '  WARN  [contract] Nothing notices if the device stops answering '
                                 'over udp: it sends no poll at its default config, and there is '
                                 'no liveness block, so the device reads connected for as long as '
                                 'the connection stays open. Add a liveness block that asks '
                                 'something the device answers in every state.',
                                 '  WARN  [single_client] simulator.single_client applies to a TCP '
                                 'device; a udp device answers its one connection',
                                 '',
                                 '============================================================',
                                 '4 drivers validated: 3 passed, 1 failed, 1 errors, 4 warnings, 2 '
                                 'infos']},
 'a_folder_summarised': {'code': 1,
                         'err': [],
                         'out': ['PASS acme_widget [yaml] (1 warnings, 1 infos)',
                                 'FAIL acme_other [yaml] (1 errors, 1 warnings, 1 infos)',
                                 'PASS acme_widget [python]',
                                 'PASS acme_warn [yaml] (2 warnings)',
                                 'PASS acme_infos [yaml] (1 warnings, 2 infos)',
                                 '',
                                 '============================================================',
                                 '5 drivers validated: 4 passed, 1 failed, 1 errors, 5 warnings, 4 '
                                 'infos']},
 'a_folder_with_no_drivers': {'code': 1, 'err': ['No drivers found at <tmp>'], 'out': []},
 'a_path_that_does_not_exist': {'code': 1,
                                'err': ['Error: <tmp>/nowhere.avcdriver does not exist'],
                                'out': []},
 'one_clean_yaml': {'code': 0,
                    'err': [],
                    'out': ['',
                            'WARN: acme_widget [yaml] (<tmp>/acme_widget.avcdriver)',
                            '  WARN  [contract] Nothing notices if the device stops answering over '
                            'tcp: it sends no poll at its default config, and there is no liveness '
                            'block, so the device reads connected for as long as the connection '
                            'stays open. Add a liveness block that asks something the device '
                            'answers in every state.',
                            '  INFO  [reply_framing] 1 simulator reply checked, all terminated '
                            "with '\\\\r\\\\n'.",
                            '',
                            '============================================================',
                            '1 drivers validated: 1 passed, 0 failed, 0 errors, 1 warnings, 1 '
                            'infos']},
 'one_python_pair': {'code': 0,
                     'err': [],
                     'out': ['',
                             'PASS: acme_widget [python]',
                             '',
                             '============================================================',
                             '1 drivers validated: 1 passed, 0 failed, 0 errors, 0 warnings, 0 '
                             'infos']},
 'one_yaml_failing_the_contract': {'code': 1,
                                   'err': [],
                                   'out': ['',
                                           'FAIL: acme_widget [yaml] (<tmp>/acme_widget.avcdriver)',
                                           '  ERROR [contract] commands.power_on: unknown key '
                                           "'labl' (did you mean 'label'?)",
                                           '  WARN  [contract] Nothing notices if the device stops '
                                           'answering over tcp: it sends no poll at its default '
                                           'config, and there is no liveness block, so the device '
                                           'reads connected for as long as the connection stays '
                                           'open. Add a liveness block that asks something the '
                                           'device answers in every state.',
                                           '',
                                           '============================================================',
                                           '1 drivers validated: 0 passed, 1 failed, 1 errors, 1 '
                                           'warnings, 0 infos']},
 'one_yaml_with_a_warning': {'code': 0,
                             'err': [],
                             'out': ['',
                                     'WARN: acme_widget [yaml] (<tmp>/acme_widget.avcdriver)',
                                     '  WARN  [contract] Nothing notices if the device stops '
                                     'answering over udp: it sends no poll at its default config, '
                                     'and there is no liveness block, so the device reads '
                                     'connected for as long as the connection stays open. Add a '
                                     'liveness block that asks something the device answers in '
                                     'every state.',
                                     '  WARN  [single_client] simulator.single_client applies to a '
                                     'TCP device; a udp device answers its one connection',
                                     '',
                                     '============================================================',
                                     '1 drivers validated: 1 passed, 0 failed, 0 errors, 2 '
                                     'warnings, 0 infos']},
 'one_yaml_with_an_error': {'code': 1,
                            'err': [],
                            'out': ['',
                                    'FAIL: acme_widget [yaml] (<tmp>/acme_widget.avcdriver)',
                                    '  WARN  [contract] Nothing notices if the device stops '
                                    'answering over tcp: it sends no poll at its default config, '
                                    'and there is no liveness block, so the device reads connected '
                                    'for as long as the connection stays open. Add a liveness '
                                    'block that asks something the device answers in every state.',
                                    '  INFO  [reply_framing] 1 simulator reply checked, all '
                                    "terminated with '\\\\r\\\\n'.",
                                    '  ERROR [capacity] simulator.capacity: capacity must be a '
                                    'mapping with rx_buffer and process_ms',
                                    '',
                                    '============================================================',
                                    '1 drivers validated: 0 passed, 1 failed, 1 errors, 1 '
                                    'warnings, 1 infos']},
 'one_yaml_with_infos': {'code': 0,
                         'err': [],
                         'out': ['',
                                 'WARN: acme_widget [yaml] (<tmp>/acme_widget.avcdriver)',
                                 '  WARN  [contract] Nothing notices if the device stops answering '
                                 'over tcp: it sends no poll at its default config, and there is '
                                 'no liveness block, so the device reads connected for as long as '
                                 'the connection stays open. Add a liveness block that asks '
                                 'something the device answers in every state.',
                                 "  INFO  [state_coverage] 'level' will use auto-gen default 0. "
                                 'Add to simulator.initial_state to override.',
                                 '  INFO  [reply_framing] 1 simulator reply checked, all '
                                 "terminated with '\\\\r\\\\n'.",
                                 '',
                                 '============================================================',
                                 '1 drivers validated: 1 passed, 0 failed, 0 errors, 1 warnings, 2 '
                                 'infos']},
 'one_yaml_with_infos_only': {'code': 0,
                              'err': [],
                              'out': ['',
                                      'PASS: acme_widget [yaml] (<tmp>/acme_widget.avcdriver)',
                                      '  INFO  [reply_framing] 1 simulator reply checked, all '
                                      "terminated with '\\\\r\\\\n'.",
                                      '',
                                      '============================================================',
                                      '1 drivers validated: 1 passed, 0 failed, 0 errors, 0 '
                                      'warnings, 1 infos']},
 'summary_of_a_clean_driver': {'code': 0,
                               'err': [],
                               'out': ['PASS acme_widget [yaml] (1 warnings, 1 infos)',
                                       '',
                                       '============================================================',
                                       '1 drivers validated: 1 passed, 0 failed, 0 errors, 1 '
                                       'warnings, 1 infos']},
 'summary_of_a_contract_failure': {'code': 1,
                                   'err': [],
                                   'out': ['FAIL acme_widget [yaml] (1 errors, 1 warnings)',
                                           '',
                                           '============================================================',
                                           '1 drivers validated: 0 passed, 1 failed, 1 errors, 1 '
                                           'warnings, 0 infos']},
 'summary_of_infos_only': {'code': 0,
                           'err': [],
                           'out': ['PASS acme_widget [yaml] (1 infos)',
                                   '',
                                   '============================================================',
                                   '1 drivers validated: 1 passed, 0 failed, 0 errors, 0 warnings, '
                                   '1 infos']}}
