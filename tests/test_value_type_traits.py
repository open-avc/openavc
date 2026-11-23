"""The value type table (``drivers/spec.py`` ``VALUE_TYPE_TRAITS``) is THE answer
to what a state variable's, a child state variable's or a device setting's type
is and how each reader treats it.

Each row is held complete, the lists the schema, the checks and the readers
read are held to the values they had before they became views of the table,
every reader is held to read ``float`` as it reads ``number`` (``float`` is an
accepted spelling of it), and a module that lists value types for itself fails.
"""
from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

from openavc.drivers import spec
from openavc.drivers.spec import VALUE_TYPE_TRAITS, ValueTypeTraits

_VIEWS_AS_THEY_WERE = {
    "VALUE_TYPES": ("string", "integer", "number", "boolean", "enum", "float"),
    "NUMERIC_TYPES": ("integer", "number", "float"),
    "REAL_TYPES": ("number", "float"),
}

_CONTROLS = {"slider", "toggle", "choice", "text"}


@pytest.mark.parametrize("view, values", sorted(_VIEWS_AS_THEY_WERE.items()))
def test_each_view_reads_what_it_did(view, values):
    assert getattr(spec, view) == values


@pytest.mark.parametrize("name", list(VALUE_TYPE_TRAITS))
def test_each_row_answers_every_column(name):
    row = VALUE_TYPE_TRAITS[name]
    assert isinstance(row, ValueTypeTraits)
    assert isinstance(row.numeric, bool) and isinstance(row.real, bool)
    assert row.numeric or not row.real, "a real type is a number"
    assert row.osc_tag in spec.OSC_ARG_TYPES
    assert row.control in _CONTROLS
    assert isinstance(row.label, str) and row.label
    # The zero is the kind of value the type holds.
    if row.real:
        assert type(row.zero) is float
    elif row.numeric:
        assert type(row.zero) is int
    elif name == "boolean":
        assert row.zero is False
    else:
        assert row.zero == ""


def test_a_number_is_drawn_as_a_slider():
    for name, row in VALUE_TYPE_TRAITS.items():
        assert (row.control == "slider") == row.numeric, name


def test_float_is_number_in_every_column_but_its_label():
    number, float_ = VALUE_TYPE_TRAITS["number"], VALUE_TYPE_TRAITS["float"]
    assert number._replace(label="") == float_._replace(label="")


def test_a_missing_or_null_type_reads_as_string():
    assert spec.value_type_traits(None) is VALUE_TYPE_TRAITS["string"]
    assert spec.is_value_type(None)


@pytest.mark.parametrize("written", ["double", "", ["number"], 5, True])
def test_a_type_with_no_row_raises_naming_the_types(written):
    assert not spec.is_value_type(written)
    with pytest.raises(ValueError, match="'string', 'integer', 'number', 'boolean', 'enum', 'float'"):
        spec.value_type_traits(written)


def test_a_type_with_no_row_reads_as_text_where_a_driver_still_carries_one():
    """A Python driver with such a type loads (the loader warns), and the
    simulator reads a YAML file as written, so these readers draw it as the
    text it reads as rather than failing the device."""
    assert spec.value_type_traits_or_text("double") is VALUE_TYPE_TRAITS["string"]
    assert spec.value_type_traits_or_text(["number"]) is VALUE_TYPE_TRAITS["string"]
    assert spec.value_type_traits_or_text("float") is VALUE_TYPE_TRAITS["float"]


def test_the_simulator_builds_a_device_whose_type_has_no_row():
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator("acme_widget", config={}, driver_def={
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
        "state_variables": {"gain": {"type": "double", "min": 0, "max": 10}},
    })
    assert sim.SIMULATOR_INFO["initial_state"]["gain"] == ""
    assert sim.SIMULATOR_INFO["controls"] == [{"type": "indicator", "key": "gain", "label": "Gain"}]


# --- every reader reads float as number --------------------------------------


@pytest.mark.parametrize("declared", ["number", "float"])
def test_a_real_variable_gets_a_slider(declared):
    """A float variable was a readout where a number one with the same range
    was a slider."""
    from openavc.simulator.ui_info import variable_control

    control = variable_control("gain", {"type": declared, "min": 0, "max": 10}, declared, "Gain")
    assert control["type"] == "slider"
    assert control["step"] == pytest.approx(0.02)


def test_a_whole_variable_steps_by_whole_numbers():
    from openavc.simulator.ui_info import variable_control

    control = variable_control("level", {"type": "integer", "min": 0, "max": 10}, "integer", "Level")
    assert control["step"] == 1


@pytest.mark.parametrize("declared, control", [
    ("string", "indicator"), ("boolean", "toggle"), ("enum", "select"),
])
def test_the_other_types_keep_their_controls(declared, control):
    from openavc.simulator.ui_info import variable_control

    var_def = {"type": declared, "values": ["a", "b"]}
    assert variable_control("x", var_def, declared, "X")["type"] == control


@pytest.mark.parametrize("declared", ["number", "float"])
def test_a_real_variable_starts_at_zero_point_zero(declared):
    from openavc.drivers.compiled_protocol import state_var_default

    assert state_var_default({"type": declared, "min": 5}) == 0.0


def test_a_whole_variable_starts_at_its_minimum():
    from openavc.drivers.compiled_protocol import state_var_default

    assert state_var_default({"type": "integer", "min": 1.5}) == 2
    assert state_var_default({"type": "integer"}) == 0


def _yaml_driver(tmp_path, var_def: str) -> Path:
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(textwrap.dedent(f"""\
        id: acme_widget
        name: Acme Widget
        manufacturer: Acme
        category: utility
        version: 1.0.0
        transport: tcp
        state_variables:
          gain: {var_def}
        commands: {{}}
        simulator:
          initial_state: {{}}
    """), encoding="utf-8")
    return path


@pytest.mark.parametrize("var_def, announced", [
    ("{type: float, label: Gain}", "0.0"),
    ("{type: number, label: Gain}", "0.0"),
    ("{type: integer, label: Gain, min: 1.5}", "2"),
    ("{type: double, label: Gain}", "''"),
])
def test_the_validator_announces_the_simulators_own_start_value(tmp_path, var_def, announced):
    """The validator kept its own copy of the seed rule, which had no float
    branch (a float was announced as '') and read an integer's min as written
    (1.5, where the simulator starts at 2). A type the contract does not know
    is announced as text; the contract's check names it."""
    from openavc.simulator.validate import validate_yaml_driver

    result = validate_yaml_driver(_yaml_driver(tmp_path, var_def))
    coverage = [i.message for i in result.issues if i.check == "state_coverage"]
    assert coverage == [
        f"'gain' will use auto-gen default {announced}. Add to simulator.initial_state to override."
    ]


def test_an_unreadable_min_is_announced_as_zero_and_named_once(tmp_path, caplog):
    """A min written as text is refused by the contract's check, which the
    command line runs first; the validator then announces the simulator's 0,
    and the seed rule names the min once."""
    from openavc.simulator.validate import validate_yaml_driver

    caplog.set_level("WARNING")
    result = validate_yaml_driver(_yaml_driver(tmp_path, "{type: integer, label: Gain, min: low}"))
    coverage = [i.message for i in result.issues if i.check == "state_coverage"]
    assert coverage == ["'gain' will use auto-gen default 0. Add to simulator.initial_state to override."]
    named = [r.getMessage() for r in caplog.records if "non-numeric 'min'" in r.getMessage()]
    assert named == ["state variable declares a non-numeric 'min' 'low'; defaulting to 0"]


@pytest.mark.parametrize("declared, tag", [
    ("float", "f"), ("number", "f"), ("integer", "i"), ("boolean", "i"), ("string", "s"),
    # A reply mapping's type the strict check refuses still loads; it goes
    # out, as a null one does, as the float a mapping with no type is.
    ("double", "f"), (None, "f"),
])
def test_an_osc_value_goes_out_under_its_types_tag(declared, tag):
    from openavc.simulator.osc_addresses import type_tag

    assert type_tag(declared) == tag


@pytest.mark.parametrize("declared", ["number", "float"])
def test_a_real_capture_keeps_its_fraction(declared):
    from openavc.drivers.compiled_protocol import coerce_value

    assert coerce_value("1.5", declared) == 1.5
    assert coerce_value("1.5", "integer") == "1.5"  # kept as sent, and said


@pytest.mark.parametrize("declared", ["integer", "number", "float"])
def test_a_learned_number_is_bounded(declared):
    from openavc.core.learned_config import accepted_value

    assert accepted_value({"type": declared, "max": 10}, "5") == (True, 5)
    assert accepted_value({"type": declared, "max": 10}, "50") == (False, None)


@pytest.mark.parametrize("declared", ["number", "float"])
def test_the_scaffold_seeds_a_real_variable_as_a_real_number(declared):
    from openavc.simulator.scaffold import _initial_value_for

    assert _initial_value_for("gain", {"type": declared}) == "0.0"
    assert _initial_value_for("gain", {"type": declared, "min": 0, "max": 5}) == "2.5"


def test_the_scaffold_refuses_a_type_with_no_row():
    """The scaffold reads a driver no check has passed and prints a
    ValueError as its refusal."""
    from openavc.simulator.scaffold import _initial_value_for

    with pytest.raises(ValueError, match="'double' is not a value type"):
        _initial_value_for("gain", {"type": "double"})


# --- no list of its own -------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = _ROOT / "openavc"
_NUMBERS = set(spec.NUMERIC_TYPES)

# Lists naming two of integer, number and float that are not value types, and
# why.
_NOT_VALUE_TYPES = {
    # JSON Schema's own types, which share integer and number with the value
    # types: the contract's schema and its TypeScript rendering.
    "openavc/drivers/contract_gen_ts.py",
    "openavc/cloud/contract_gen.py",
    # A plugin's config and macro action parameter types.
    "openavc/core/plugin_handler_rules.py",
    # The words any declaration (a plugin's, a driver's) uses for a value that
    # arrives at float64 width, which the Builder's page review mirrors.
    "openavc/ui/page_review.py",
}


def _numeric_names(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return []
    return [
        element.value for element in node.elts
        if isinstance(element, ast.Constant) and element.value in _NUMBERS
    ]


def _python_lists() -> list[str]:
    found = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel == "openavc/drivers/spec.py" or rel in _NOT_VALUE_TYPES or "node_modules" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if len(_numeric_names(node)) >= 2:
                found.append(f"{rel}:{node.lineno} {_numeric_names(node)}")
    return found


_TS_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_TS_NAME = "|".join(spec.NUMERIC_TYPES)
# A list of two of them, or a comparison of one side against two of them.
_TS_LIST = re.compile(
    rf"[\[\(\{{]\s*([\"'])({_TS_NAME})\1\s*,\s*([\"'])({_TS_NAME})\3"
    rf"|===\s*([\"'])({_TS_NAME})\5\s*\|\|\s*[\w.?]+\s*===\s*([\"'])({_TS_NAME})\7"
)

# The Programmer's own copies, moved onto valueTypeTraits.ts one at a time; a
# file leaves this list in the change that stops it holding one.
_PROGRAMMER = "openavc/web/programmer/src"
_PROGRAMMER_COPIES = {
    f"{_PROGRAMMER}/components/driver-builder/ChildEntityTypesEditor.tsx",
    f"{_PROGRAMMER}/components/driver-builder/DeviceSettingsEditor.tsx",
    f"{_PROGRAMMER}/components/driver-builder/StateVariableEditor.tsx",
    f"{_PROGRAMMER}/components/driver-builder/WhenStateEditor.tsx",
    f"{_PROGRAMMER}/components/driver-builder/configSchemaHelpers.ts",
    f"{_PROGRAMMER}/components/driver-builder/deviceSettingsHelpers.ts",
    f"{_PROGRAMMER}/components/driver-builder/paramEdits.ts",
    f"{_PROGRAMMER}/components/driver-builder/queryEntryHelpers.ts",
    f"{_PROGRAMMER}/components/driver-builder/validateDriver.ts",
    f"{_PROGRAMMER}/components/macros/StepEditor.tsx",
    f"{_PROGRAMMER}/components/shared/DeviceSettingsSetupDialog.tsx",
    f"{_PROGRAMMER}/components/shared/MonitorControl.tsx",
    f"{_PROGRAMMER}/components/shared/ParamInput.tsx",
    f"{_PROGRAMMER}/components/shared/commandParams.ts",
    f"{_PROGRAMMER}/components/shared/paramValidation.ts",
    f"{_PROGRAMMER}/views/devices/ConfigTableEditor.tsx",
    f"{_PROGRAMMER}/views/devices/DeviceSettingsSection.tsx",
    f"{_PROGRAMMER}/views/devices/actionParamFields.tsx",
    f"{_PROGRAMMER}/views/devices/deviceConfigCoerce.ts",
    f"{_PROGRAMMER}/views/devices/deviceUtils.ts",
    f"{_PROGRAMMER}/views/variables/VariableEditor.tsx",
}

def _typescript_lists() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for app in ("programmer", "simulator"):
        src = _PACKAGE / "web" / app / "src"
        for path in sorted([*src.rglob("*.ts"), *src.rglob("*.tsx")]):
            name = path.name
            if name.endswith(".gen.ts") or ".test." in name:
                continue
            # Comments go, their line breaks stay, so a line number is the file's.
            text = _TS_COMMENT.sub(
                lambda m: "\n" * m.group(0).count("\n"), path.read_text(encoding="utf-8")
            )
            for match in _TS_LIST.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                rel = path.relative_to(_ROOT).as_posix()
                found.setdefault(rel, []).append(f"{line} {match.group(0)}")
    return found


def test_no_module_lists_value_types_for_itself():
    """A reader asks the table (spec's views or value_type_traits, or
    valueTypeTraits.ts and valueTypes.ts in the two apps) rather than listing
    the types that are numbers: a list of its own is the copy that missed
    float."""
    assert _python_lists() == []
    outside = {rel: hits for rel, hits in _typescript_lists().items() if rel not in _PROGRAMMER_COPIES}
    assert outside == {}


def test_the_programmer_copies_listed_still_hold_one():
    stale = sorted(_PROGRAMMER_COPIES - set(_typescript_lists()))
    assert stale == [], f"no longer holds a copy, so leaves _PROGRAMMER_COPIES: {stale}"


def test_the_scan_finds_a_list():
    tree = ast.parse('X = ("integer", "number")\nY = ("integer", "string")\n')
    lists = [names for node in ast.walk(tree) if len(names := _numeric_names(node)) >= 2]
    assert lists == [["integer", "number"]]
    assert _TS_LIST.search('if (t === "integer" || t === "float") {')
    assert _TS_LIST.search('const N = ["number", "integer"];')
    assert not _TS_LIST.search('t === "integer" ? 1 : 0.1')
