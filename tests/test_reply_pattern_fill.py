"""A config value goes into a reply pattern as literal text, read one way by every reader.

A response rule's ``match:`` may name a config field (``{unit}LVL(\\d+)``,
``{set_id:02d}PWR (on|off)``): the device's own address, a prefix it echoes.
The value is the device's text, not regex, so it is inserted escaped
(``pattern_fill.fill_pattern``): a unit called ``A.1`` matches ``A.1`` and not
``AX1``, and one holding ``(`` still compiles. The running driver, the
auto-generated simulator and the simulator validator fill every pattern
through that one function; the checker fills each with the driver's declared
defaults and refuses, at the author's doors, a pattern that does not compile
once they are in. A pattern that does not compile as written is refused at
every door, as it was.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import check_driver_definition, validate_driver_definition
from openavc.drivers.compiled_protocol import compile_driver, safe_substitute
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import read_driver_file
from openavc.drivers.driver_model import read_driver
from openavc.drivers.pattern_fill import fill_pattern
from openavc.simulator.driver_sends import filled_rules
from openavc.simulator.reply_reading import compile_response_patterns
from openavc.simulator.validate import validate_yaml_driver
from openavc.simulator.yaml_auto import YAMLAutoSimulator

# --- the fill ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value, filled",
    [
        ("A.1", r"A\.1LVL(\d+)"),
        ("(", r"\(LVL(\d+)"),
        ("Z+[1]", r"Z\+\[1\]LVL(\d+)"),
        ("7", r"7LVL(\d+)"),
    ],
)
def test_a_config_value_is_inserted_as_literal_text(value: str, filled: str) -> None:
    assert fill_pattern(r"{unit}LVL(\d+)", {"unit": value}) == filled


@pytest.mark.parametrize("value", [5, "5", 5.0])
def test_a_format_spec_formats_the_value_as_a_send_does(value: Any) -> None:
    # The send path's retries hold here too: a saved "5" and a scaled 5.0 pad
    # to "05", the address a display's every reply carries.
    assert fill_pattern("{set_id:02d}PWR (on|off)", {"set_id": value}) == "05PWR (on|off)"
    assert safe_substitute("{set_id:02d}PWR", {"set_id": value}) == "05PWR"


def test_a_placeholder_left_as_written_is_left_byte_for_byte() -> None:
    # A name the config does not hold, and a spec that cannot format the
    # value, stay verbatim, as on the send path: not escaped.
    assert fill_pattern(r"{other}(\d+)", {"unit": "A"}) == r"{other}(\d+)"
    assert fill_pattern(r"{unit:02d}(\d+)", {"unit": "A.1"}) == r"{unit:02d}(\d+)"


def test_a_send_still_fills_the_value_as_it_is() -> None:
    # Only a pattern escapes: what goes on the wire is the device's text.
    assert safe_substitute("{unit}LVL?", {"unit": "A.1"}) == "A.1LVL?"


# --- the running driver -----------------------------------------------------


def _widget(responses: list[dict[str, Any]], **default_config: Any) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "display",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 5000, **default_config},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
            "unit": {"type": "string", "label": "Unit"},
        },
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
        "responses": responses,
    }


_LEVEL_RULE = {"match": r"{unit}LVL(\d+)", "set": {"level": "$1"}}


def test_the_driver_compiles_a_rule_whose_config_value_holds_a_parenthesis() -> None:
    compiled = compile_driver(read_driver(_widget([_LEVEL_RULE])), {"unit": "("}, "acme1")
    assert len(compiled.responses) == 1
    pattern = compiled.responses[0][0]
    assert pattern.search("(LVL42").group(1) == "42"


def test_the_driver_reads_a_dot_in_a_config_value_as_a_dot() -> None:
    compiled = compile_driver(read_driver(_widget([_LEVEL_RULE])), {"unit": "A.1"}, "acme1")
    pattern = compiled.responses[0][0]
    assert pattern.search("A.1LVL42")
    assert pattern.search("AX1LVL42") is None


# --- the checker ------------------------------------------------------------

# A placeholder straight after a backslash compiles as written (an escaped
# brace) and not once the default is in: ``\x`` is an escape with no digits.
_AFTER_A_BACKSLASH = {"match": r"A\{unit}LVL(\d+)", "set": {"level": "$1"}}
_REFUSED = "responses[0].match: with the default settings filled in"


def test_a_pattern_that_does_not_compile_under_the_defaults_is_refused_where_written() -> None:
    definition = _widget([_AFTER_A_BACKSLASH], unit="x")
    errors = validate_driver_definition(definition, strict=True)
    assert any(e.startswith(_REFUSED) for e in errors), errors


def test_such_a_pattern_is_a_notice_where_a_driver_loads(tmp_path: Path) -> None:
    definition = _widget([_AFTER_A_BACKSLASH], unit="x")
    verdict = check_driver_definition(definition)
    assert not any(_REFUSED in e for e in verdict.errors), verdict.errors
    assert any(e.startswith(_REFUSED) for e in verdict.notices), verdict.notices

    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    read = read_driver_file(path)
    assert read.definition is not None, read.reasons
    assert any(_REFUSED in n for n in read.notices), read.notices


def test_a_default_holding_regex_characters_compiles_and_is_not_refused() -> None:
    definition = _widget([_LEVEL_RULE], unit="A.1 (x)")
    assert not any("responses[0].match" in e for e in validate_driver_definition(definition, strict=True))


def test_a_pattern_refused_as_written_is_said_once() -> None:
    # An unclosed group is refused at every door already; the fill does not
    # say it a second time.
    definition = _widget([{"match": r"{unit}LVL(\d+", "set": {"level": "$1"}}], unit="x")
    errors = validate_driver_definition(definition, strict=True)
    assert [e for e in errors if e.startswith("Response 0: invalid regex")], errors
    assert not any(e.startswith(_REFUSED) for e in errors), errors


def test_a_field_with_no_default_leaves_the_placeholder_and_is_not_refused() -> None:
    definition = _widget([_AFTER_A_BACKSLASH])
    assert not any(e.startswith(_REFUSED) for e in validate_driver_definition(definition, strict=True))


# --- the simulator and its validator -----------------------------------------


def test_the_validator_compiles_a_pattern_whose_config_value_holds_a_parenthesis() -> None:
    patterns = compile_response_patterns([_LEVEL_RULE], {"unit": "("})
    assert [p.pattern for p in patterns] == [r"\(LVL(\d+)"]


def test_the_simulator_fills_a_pattern_escaped_and_an_osc_address_as_it_is() -> None:
    rules = [_LEVEL_RULE, {"address": "/ws/{unit}/level", "set": {"level": "$1"}}]
    filled = filled_rules(rules, {"unit": "A-1.5"})
    assert filled[0]["match"] == re.escape("A-1.5") + r"LVL(\d+)"
    assert filled[1]["address"] == "/ws/A-1.5/level"


_ODD_UNIT = "A-1.5 (x)"


def _round_trip_widget() -> dict[str, Any]:
    definition = _widget([_LEVEL_RULE], unit=_ODD_UNIT)
    definition["commands"] = {
        "set_level": {
            "send": "{unit}LVL{level}\r",
            "params": {"level": {"type": "integer", "min": 0, "max": 100, "required": True}},
            "sets": {"level": "{level}"},
            "query_for": "level",
        },
    }
    definition["polling"] = {"interval": 5, "queries": [{"send": "{unit}LVL?\r", "query_for": "level"}]}
    return definition


async def test_the_simulator_answers_what_the_driver_reads_back() -> None:
    definition = _round_trip_widget()
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("w1", {**definition["default_config"], "host": "127.0.0.1"}, state, events)
    sim = YAMLAutoSimulator("w1", {}, driver_def=definition)

    sim.handle_command(driver.build_wire("set_level", {"level": 42}))
    reply = sim.handle_command(f"{_ODD_UNIT}LVL?\r".encode())
    assert reply == f"{_ODD_UNIT}LVL42\r".encode()
    for line in reply.split(b"\r"):
        if line:
            await driver.on_data_received(line)
    assert driver.get_state("level") == 42


def test_the_validator_passes_a_driver_whose_default_holds_regex_characters(tmp_path: Path) -> None:
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(_round_trip_widget(), sort_keys=False), encoding="utf-8")
    result = validate_yaml_driver(path)
    assert [issue.message for issue in result.errors] == []
