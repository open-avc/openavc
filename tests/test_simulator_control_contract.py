"""The controls the Simulator UI draws are declared once, in the driver
contract (``spec.SIMULATOR_CONTROLS``), and every reader of a ``controls``
list reads them from there.

* At an author's door (Validate, the Builder's save, ``drivers.check``, the AI's
  driver tool, the catalog) a control of a type the Simulator UI does not draw
  is refused, naming the types it draws; so is a control missing a field it
  cannot be drawn without, and a value of a kind the control cannot show. A
  door that loads a driver loads it and names the problem.
* A Python simulator's ``SIMULATOR_INFO`` controls are held to the same types.
* The simulator validator's table is the contract's, and its sentence for an
  unknown type says what the Simulator UI shows: an "Unknown control type"
  marker in the control's place.
* The Simulator UI's ``ControlDef`` union is generated from the same table
  (``tests/test_driver_contract_generated.py`` holds the file to it), and its
  renderer has a case for every declared type.

All drivers here are invented (acme_widget).
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any

import pytest

from openavc.drivers import spec
from openavc.drivers.avcdriver_semantic import check_driver_definition, validate_driver_definition
from openavc.drivers.check import check_driver_file
from openavc.drivers.driver_loader import validate_driver_issues
from openavc.drivers.simulator_info import check_simulator_info
from openavc.simulator import control_checks
from openavc.simulator.validation_result import ValidationResult

_ROOT = Path(__file__).resolve().parents[1]
_NINE = ("power", "select", "slider", "toggle", "matrix", "meters", "presets", "group", "indicator")

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "level": {"type": "integer", "label": "Level"},
        "input": {"type": "string", "label": "Input"},
    },
    "commands": {"noop": {"send": "NOOP\r"}},
}

# One control of each type, with every field it cannot be drawn without.
DRAWABLE: dict[str, dict[str, Any]] = {
    "power": {"type": "power", "key": "power"},
    "select": {"type": "select", "key": "input", "options": ["hdmi1", 2]},
    "slider": {"type": "slider", "key": "level", "min": 0, "max": 100},
    "toggle": {"type": "toggle", "key": "power", "label": "Power"},
    "matrix": {"type": "matrix", "inputs": 4, "outputs": 2, "state_pattern": "route_{output}"},
    "meters": {"type": "meters", "channels": 2, "key_pattern": "ch{ch}_level"},
    "presets": {"type": "presets", "key": "input", "count": 4},
    "group": {"type": "group", "label": "Main", "controls": [{"type": "power", "key": "power"}]},
    "indicator": {"type": "indicator", "key": "input", "label": "Input"},
}


def _with_controls(*controls: Any) -> dict[str, Any]:
    return {**BASE, "simulator": {"controls": list(controls)}}


def _strict(definition: dict[str, Any]) -> list[str]:
    return list(validate_driver_definition(definition, strict=True))


def test_the_contract_declares_the_nine_types_the_simulator_ui_draws() -> None:
    assert tuple(spec.SIMULATOR_CONTROLS) == _NINE


def test_an_unknown_control_type_is_refused_at_an_author_door_naming_the_nine() -> None:
    errors = _strict(_with_controls({"type": "meter", "key": "level"}))
    refusal = [e for e in errors if e.startswith("simulator.controls[0].type")]
    assert refusal, errors
    assert "'meter'" in refusal[0]
    for name in _NINE:
        assert f"'{name}'" in refusal[0], refusal[0]


def test_a_door_that_loads_the_driver_loads_it_and_names_the_unknown_type() -> None:
    verdict = check_driver_definition(_with_controls({"type": "meter", "key": "level"}))
    assert verdict.errors == []
    assert any(n.startswith("simulator.controls[0].type") for n in verdict.notices), verdict.notices


@pytest.mark.parametrize("ctype", _NINE)
def test_a_control_of_each_declared_type_passes(ctype: str) -> None:
    assert _strict(_with_controls(DRAWABLE[ctype])) == []


def test_a_key_the_simulator_ui_does_not_read_is_left_alone() -> None:
    control = {**DRAWABLE["toggle"], "on_value": 1, "off_value": 0}
    assert _strict(_with_controls(control)) == []


def test_a_control_missing_a_field_it_cannot_be_drawn_without_is_refused() -> None:
    errors = _strict(_with_controls({"type": "slider", "key": "level", "max": 100}))
    assert "simulator.controls[0]: missing required 'min'" in errors, errors


def test_a_control_with_no_type_is_refused() -> None:
    errors = _strict(_with_controls({"key": "level"}))
    assert "simulator.controls[0]: missing required 'type'" in errors, errors


def test_a_control_inside_a_group_is_held_to_the_same_types() -> None:
    group = {"type": "group", "label": "Main", "controls": [DRAWABLE["power"], {"type": "dial"}]}
    errors = _strict(_with_controls(group))
    assert any(e.startswith("simulator.controls[0].controls[1].type") for e in errors), errors


def test_a_select_option_the_simulator_ui_cannot_show_is_refused() -> None:
    control = {"type": "select", "key": "input", "options": [{"value": 1, "label": "One"}]}
    errors = _strict(_with_controls(control))
    assert any(e.startswith("simulator.controls[0].options[0]") for e in errors), errors


def test_the_builder_puts_the_refusal_on_the_simulator_section() -> None:
    issues = validate_driver_issues(_with_controls({"type": "meter", "key": "level"}), strict=True)
    paths = [i["path"] for i in issues if i["severity"] == "error"]
    assert any(p.startswith("simulator.controls[0]") for p in paths), issues


def test_drivers_check_refuses_an_unknown_control_type(tmp_path: Path) -> None:
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(textwrap.dedent("""\
        id: acme_widget
        name: Acme Widget
        transport: tcp
        state_variables:
          power: {type: boolean, label: Power}
        commands:
          noop: {send: "NOOP\\r"}
        simulator:
          controls:
            - {type: meter, key: power}
    """), encoding="utf-8")
    result = check_driver_file(path)
    assert any("simulator.controls[0].type" in e for e in result.errors), result.errors


def test_a_python_simulators_unknown_control_type_is_an_error() -> None:
    verdict = check_simulator_info({"driver_id": "acme_widget", "controls": [{"type": "meter", "key": "x"}]})
    assert any(e.startswith("SIMULATOR_INFO.controls[0].type") for e in verdict.errors), verdict.errors


def test_the_validators_table_is_the_contracts() -> None:
    table = control_checks._TYPES
    assert tuple(table) == tuple(spec.SIMULATOR_CONTROLS)
    for name, declared in spec.SIMULATOR_CONTROLS.items():
        assert table[name].required == tuple(declared["required"]), name
        assert table[name].has_key == ("key" in declared["fields"]), name
    assert set(control_checks._OWN_RULES) <= set(spec.SIMULATOR_CONTROLS)


def test_the_validator_says_what_the_simulator_ui_shows_for_an_unknown_type() -> None:
    result = ValidationResult("acme_widget.avcdriver", "acme_widget", "yaml")
    control_checks.check_controls(result, {"controls": [{"type": "meter", "key": "level"}]}, set())
    [message] = [i.message for i in result.issues if i.severity == "error"]
    assert "renders nothing" not in message
    assert "Unknown control type" in message
    assert ", ".join(_NINE) in message, message


def test_the_simulator_ui_draws_every_declared_type() -> None:
    """Every declared type has its case in the renderer, and the union the
    renderer switches on is the generated one."""
    controls = _ROOT / "openavc/web/simulator/src/components/controls"
    renderer = (controls / "DynamicControls.tsx").read_text(encoding="utf-8")
    cases = re.findall(r'case "([a-z_]+)":', renderer)
    assert tuple(cases) == tuple(spec.SIMULATOR_CONTROLS)
    assert 'from "../../store/controls.gen"' in renderer
    api = (_ROOT / "openavc/web/simulator/src/store/api.ts").read_text(encoding="utf-8")
    assert "ControlDef {" not in api, "the ControlDef types are generated, never written in api.ts"


def test_a_group_nested_past_what_the_check_reads_never_stops_a_load() -> None:
    """Each level of a group costs the shape walk several calls, so a group
    nested far past anything a device page holds (anchors reach hundreds) is
    more than it reads: the author's doors say it could not be checked, and a
    door that loads the driver still loads it."""
    control: dict[str, Any] = {"type": "power", "key": "power"}
    for _ in range(300):
        control = {"type": "group", "label": "G", "controls": [control]}
    definition = _with_controls(control)
    assert check_driver_definition(definition).errors == []
    [refusal] = _strict(definition)
    assert "could not be checked" in refusal


def test_a_select_over_true_and_false_passes_and_a_label_that_is_not_text_is_refused() -> None:
    """Options are values the state key holds, true and false among them (YAML
    reads a bare on/off that way); a button's label is text, and one YAML read
    as true or false is refused with the fix."""
    assert _strict(_with_controls({"type": "select", "key": "power", "options": [True, False]})) == []
    control = {"type": "select", "key": "power", "options": [0, 1], "labels": {"0": False}}
    assert _strict(_with_controls(control)) == [
        "simulator.controls[0].labels.0: must be a string; put it in quotes"
    ]
