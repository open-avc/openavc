"""One reading of a capture reference, asked by the runtime, the simulator and the checker.

A text rule names the capture each value and each child id is read from as
``"$N"`` or a number, alone or in a long form ``{group: ...}``. These hold the
reading itself (``capture_ref``), then hold each reader to it on an invented
Acme device: the YAML runtime writes what the reference names, the
auto-simulator answers with that capture, and the checker refuses at the
author's doors what the runtime would skip while a loaded driver keeps loading.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import check_driver_definition
from openavc.drivers.capture_ref import capture_group, names_a_capture
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import YAMLAutoSimulator

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- the reading -----------------------------------------------------------


@pytest.mark.parametrize("ref, number", [
    ("$2", 2),
    ("$0", 0),
    ("$02", 2),
    ("$12", 12),
    (2, 2),
    (0, 0),
])
def test_a_reference_in_the_contracts_forms_names_its_capture(ref: Any, number: int) -> None:
    assert capture_group(ref) == number


@pytest.mark.parametrize("ref", [
    "$x", "$", "$ 2", "$2 ", "$+2", "$-1", "$1_0", "2", "", -1, True, False, 2.0, [1],
    {"group": 1},
])
def test_anything_else_names_no_capture(ref: Any) -> None:
    assert capture_group(ref) is None


def test_an_absent_reference_reads_as_the_default_the_field_gives() -> None:
    assert capture_group(None) == 0
    assert capture_group(None, default=1) == 1
    assert capture_group(None, default=None) is None


@pytest.mark.parametrize("value, named", [
    ("$2", True), ("$x", True), ("$ 2", True), ("2", False), (2, False), (None, False),
])
def test_a_reference_is_written_as_text_that_starts_with_a_dollar(value: Any, named: bool) -> None:
    assert names_a_capture(value) is named


def test_the_reading_stays_a_leaf() -> None:
    # The checker's vendored closure asks it, so it imports nothing beyond the
    # standard library, which -I -S leaves the only thing importable.
    code = (
        "import sys\n"
        "sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        "import openavc.drivers.capture_ref\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('openavc'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == {"openavc", "openavc.drivers", "openavc.drivers.capture_ref"}


# A reader that tells a reference by its "$" itself, or reads the number after
# it, is a second reading of the rule.
_OWN_READINGS = ('startswith("$")', "startswith('$')", r'\$\d', r'\$(\d')


@pytest.mark.parametrize("folder", ["openavc/drivers", "openavc/simulator"])
def test_only_capture_ref_reads_a_reference(folder: str) -> None:
    readers = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / folder).rglob("*.py")
        if path.name != "capture_ref.py"
        and any(text in path.read_text(encoding="utf-8") for text in _OWN_READINGS)
    )
    assert readers == []


# --- an invented device ----------------------------------------------------

_CHANNELS = {
    "channel": {
        "label": "Channel",
        "id_format": {"type": "integer", "min": 1, "max": 4},
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
        "instances": {"count": 4, "label": "Channel {id}"},
    },
}


def _definition(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "acme_mixer",
        "name": "Acme Mixer",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "default_config": {"host": "", "port": 5000},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
        },
        "state_variables": {
            "level": {"type": "integer", "label": "Level"},
            "label": {"type": "string", "label": "Label"},
        },
        "child_entity_types": _CHANNELS,
        "commands": {},
        "responses": [rule],
    }


def _driver(rule: dict[str, Any]) -> Any:
    state = StateStore()
    state.set_event_bus(EventBus())
    driver = create_configurable_driver_class(_definition(rule))(
        "dev1", {"host": "127.0.0.1", "port": 5000}, state, EventBus(),
    )
    driver._register_declared_children()
    return driver


def _sim(rule: dict[str, Any]) -> YAMLAutoSimulator:
    return YAMLAutoSimulator("s1", {}, driver_def=_definition(rule))


def _flat_rule(ref: Any) -> dict[str, Any]:
    return {"match": r"O(\d+) L(\d+)", "mappings": [{"group": ref, "state": "level"}]}


def _child_rule(ref: Any) -> dict[str, Any]:
    return {"match": r"O(\d+) L(\d+)", "child_set": [
        {"type": "channel", "id": "$1", "state": {"level": {"group": ref}}},
    ]}


def _list_rule(ref: Any) -> dict[str, Any]:
    return {"match": r"LST (.*)", "child_list": {
        "type": "channel", "group": 1, "separator": ",", "element": r"(\d+)=(\d+)",
        "id": "$1", "state": {"level": {"group": ref}},
    }}


# --- the runtime -----------------------------------------------------------


@pytest.mark.parametrize("ref", ["$2", 2])
async def test_the_runtime_reads_a_flat_mapping_s_group_in_either_form(ref: Any) -> None:
    driver = _driver(_flat_rule(ref))
    await driver.on_data_received(b"O2 L33")
    assert driver.get_state("level") == 33


@pytest.mark.parametrize("ref", ["$2", 2])
async def test_the_runtime_reads_a_child_value_s_long_form_in_either_form(ref: Any) -> None:
    driver = _driver(_child_rule(ref))
    await driver.on_data_received(b"O2 L33")
    assert driver.get_child_state("channel", 2).get("level") == 33


@pytest.mark.parametrize("ref", ["$2", 2])
async def test_the_runtime_reads_a_list_item_s_long_form_in_either_form(ref: Any) -> None:
    driver = _driver(_list_rule(ref))
    await driver.on_data_received(b"LST 2=33,3=44")
    assert [driver.get_child_state("channel", n).get("level") for n in (2, 3)] == [33, 44]


async def test_a_flat_mapping_that_names_no_group_reads_the_whole_match() -> None:
    driver = _driver({"match": r"L(\d+)", "mappings": [{"state": "label"}]})
    await driver.on_data_received(b"L33")
    assert driver.get_state("label") == "L33"


@pytest.mark.parametrize("rule", [
    {"match": r"O(\d+) L(\d+)", "set": {"level": "$ 2"}},
    {"match": r"O(\d+) L(\d+)", "set": {"level": "$+2"}},
    _flat_rule(True),
    _child_rule("2"),
    {"match": r"O(\d+) L(\d+)", "child_set": [
        {"type": "channel", "id": "$1", "state": {"level": "$ 2"}},
    ]},
])
async def test_a_reference_outside_the_contract_writes_nothing_and_is_said(
    rule: dict[str, Any], caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        driver = _driver(rule)
    await driver.on_data_received(b"O2 L33")
    assert driver.get_state("level") is None
    assert driver.get_child_state("channel", 2).get("level") is None
    assert "is not a numeric" in caplog.text


# --- the simulator ---------------------------------------------------------


def test_the_simulator_answers_a_flat_mapping_s_group_in_either_form() -> None:
    for ref in ("$2", 2):
        assert _sim(_flat_rule(ref))._state_responses["level"].template == "O0 L{value}"


def test_the_simulator_answers_a_child_value_s_long_form_in_either_form() -> None:
    for ref in ("$2", 2):
        table = _sim(_child_rule(ref))._child_state_responses[("channel", "level")]
        assert table.template == "O{child_id} L{value}"


def test_the_simulator_reads_a_mapping_with_no_group_as_the_whole_match_as_the_runtime_does() -> None:
    # The runtime stores the whole match, "O2 L33", so a reply that puts the
    # value in the first capture would be read back as other text: the
    # simulator models no reply for it.
    sim = _sim({"match": r"O(\d+) L(\d+)", "mappings": [{"state": "label"}]})
    assert sim._state_responses["label"].template is None


@pytest.mark.parametrize("start", ["x", "$ 1", "$0"])
def test_the_simulator_models_no_list_whose_start_the_runtime_refuses(start: str) -> None:
    rule = {"match": r"PG(\d+) (.*)", "child_list": {
        "type": "channel", "group": 2, "start": start, "element": r"(\d+)", "state": {"level": "$1"},
    }}
    assert _sim(rule)._child_list_answers == {}


@pytest.mark.parametrize("field, ref", [("group", "$ 1"), ("id", "$+1"), ("id", 0)])
def test_the_simulator_models_no_list_whose_capture_the_runtime_refuses(field: str, ref: Any) -> None:
    spec: dict[str, Any] = {
        "type": "channel", "group": 1, "separator": ",", "element": r"(\d+)=(\d+)",
        "id": "$1", "state": {"level": "$2"},
    }
    spec[field] = ref
    assert _sim({"match": r"LST (.*)", "child_list": spec})._child_list_answers == {}


# --- the checker -----------------------------------------------------------


def _verdict(rule: dict[str, Any], strict: bool) -> tuple[list[str], list[str]]:
    verdict = check_driver_definition(_definition(rule), strict=strict)
    return list(verdict.errors), list(verdict.notices)


@pytest.mark.parametrize("rule, said", [
    ({"match": r"O(\d+) L(\d+)", "set": {"level": "$x"}},
     "Response 0: set 'level': '$x' is not a numeric capture ref ($1, $2, ...)"),
    ({"match": r"O(\d+) L(\d+)", "set": {"level": "$ 2"}},
     "Response 0: set 'level': '$ 2' is not a numeric capture ref ($1, $2, ...)"),
    ({"match": r"O(\d+) L(\d+)", "set": {"level": "$3"}},
     "Response 0: set 'level': capture ref $3 exceeds the pattern's 2 group(s)"),
    ({"match": r"O(\d+) L(\d+)", "mappings": [{"group": 3, "state": "level"}]},
     "Response 0: mappings[0] group: capture ref $3 exceeds the pattern's 2 group(s)"),
    ({"match": r"O(\d+) L(\d+)", "child_set": [
        {"type": "channel", "id": "$1", "state": {"level": "$ 2"}},
    ]}, "Response 0: child_set[0]: state 'level': '$ 2' is not a numeric capture ref ($1, $2, ...)"),
    ({"match": r"O(\d+) L(\d+)", "child_set": [
        {"type": "channel", "id": "$ 1", "state": {"level": "$2"}},
    ]}, "Response 0: child_set[0]: id: '$ 1' is not a numeric capture ref ($1, $2, ...)"),
    (_child_rule("2"), "Response 0: child_set[0]: state 'level' group: '2' is not a numeric capture ref ($1, $2, ...)"),
    (_child_rule(0), "Response 0: child_set[0]: state 'level' group: capture ref must be $1 or higher"),
    (_child_rule(3), "Response 0: child_set[0]: state 'level' group: capture ref $3 exceeds the pattern's 2 group(s)"),
])
def test_what_the_runtime_would_skip_is_refused_where_a_driver_is_written_and_named_where_one_loads(
    rule: dict[str, Any], said: str,
) -> None:
    errors, _ = _verdict(rule, strict=True)
    assert said in errors
    errors, notices = _verdict(rule, strict=False)
    assert said not in errors and said in notices


@pytest.mark.parametrize("rule", [
    {"match": r"O(\d+) L(\d+)", "set": {"level": "$2", "label": "$0"}},
    _flat_rule(2),
    _child_rule("$2"),
    _child_rule(2),
    _list_rule("$2"),
])
def test_a_reference_every_reader_reads_is_not_refused(rule: dict[str, Any]) -> None:
    errors, notices = _verdict(rule, strict=True)
    assert [e for e in errors if "capture" in e or "group" in e] == []
