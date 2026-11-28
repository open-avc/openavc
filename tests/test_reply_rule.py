"""The reading of one response rule (``drivers/reply_rule.py``), the driver
model's ``wire.replies`` built from it, and the runtime's compile over the
model (``compile_driver(model, config, device_id)``).

Invented Acme devices throughout.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.compiled_protocol import compile_driver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_model import read_driver
from openavc.drivers.reply_rule import (
    JSON,
    OSC,
    REGEX,
    SkippedMapping,
    read_reply_rule,
    rule_form,
)

_STATE_VARS: dict[str, Any] = {
    "power": {"type": "boolean"},
    "level": {"type": "integer"},
    "mode": {"type": "string"},
}


@pytest.mark.parametrize(
    ("rule", "form"),
    [
        ({"match": "P(\\d)"}, REGEX),
        ({"set": {"power": "$1"}}, REGEX),
        ({"address": "/power"}, OSC),
        ({"json": True}, JSON),
        # The runtime sorts an address first, so an OSC rule marked json is OSC.
        ({"address": "/power", "json": True}, OSC),
        ({"match": "X", "json": False}, REGEX),
    ],
)
def test_a_rule_takes_the_form_the_runtime_sorts_it_into(rule, form):
    assert rule_form(rule) == form
    assert read_reply_rule(rule).form == form


def test_a_regex_rule_reads_its_mappings_then_its_set_typed_by_their_variables():
    rule = {
        "match": "P(\\d) L(\\d+)",
        "mappings": [{"group": 1, "state": "power", "map": {"1": True}}],
        "set": {"level": "$2", "mode": "fixed"},
    }
    assert read_reply_rule(rule, _STATE_VARS).mappings == (
        {"group": 1, "state": "power", "map": {"1": True}, "type": "boolean"},
        {"group": 2, "state": "level", "type": "integer"},
        {"group": 0, "state": "mode", "value": "fixed", "type": "string"},
    )


def test_a_reference_naming_no_capture_is_skipped_with_the_words_said():
    rule = {"match": "P(\\d)", "set": {"power": "$x"}, "mappings": [{"group": "$y", "state": "level"}]}
    read = read_reply_rule(rule, _STATE_VARS)
    assert read.mappings == ()
    assert read.skipped == (
        SkippedMapping(
            "level",
            "mappings: group '$y' for state 'level' is not a numeric capture ref "
            "($1, $2, ...); skipping this mapping",
        ),
        SkippedMapping(
            "power",
            "set-shorthand reference '$x' for state 'power' is not a numeric group "
            "($1, $2, ...); skipping this mapping",
        ),
    )


def test_the_parts_a_reader_takes_as_written():
    gate = {"key": "power", "equals": True}
    rule = {
        "match": "P(\\d)",
        "only_when": gate,
        "then_query": ["Q1"],
        "after_json": True,
        "require": ["status", "", "id"],
    }
    read = read_reply_rule(rule)
    assert read.written is rule
    assert read.condition is gate
    assert read.follow_ups == ("Q1",)
    assert read.after_json is True
    assert read.require == ("status", "id")


@pytest.mark.parametrize(
    "rule",
    [
        {"match": "P", "only_when": {}, "then_query": [], "require": 3},
        {"match": "P", "only_when": "power", "then_query": "Q1", "mappings": None},
    ],
)
def test_a_part_of_the_wrong_shape_reads_as_absent(rule):
    read = read_reply_rule(rule, "not a mapping")
    assert (read.condition, read.follow_ups, read.require, read.mappings) == (None, None, (), ())


def test_an_osc_rule_keeps_its_mappings_as_written_and_ignores_set():
    entry = {"state": "level", "arg": 0}
    read = read_reply_rule({"address": "/level", "mappings": [entry, "odd"], "set": {"power": "$1"}})
    assert read.mappings == (entry, "odd")
    assert read.mappings[0] is entry


def test_an_osc_mapping_with_no_type_takes_its_variables_type():
    untyped = {"state": "level", "arg": 0}
    typed = {"state": "mode", "arg": 1, "type": "integer"}
    undeclared = {"state": "other", "arg": 2}
    rule = {"address": "/acme/level", "mappings": [untyped, typed, undeclared]}
    read = read_reply_rule(rule, _STATE_VARS)
    assert read.mappings == (
        {"state": "level", "arg": 0, "type": "integer"},
        typed,
        undeclared,
    )
    # The definition's own entry is shared by every device on the driver.
    assert untyped == {"state": "level", "arg": 0}


@pytest.mark.parametrize("arg", [("i", 2), ("f", 2.0), ("s", "2")])
def test_the_driver_stores_an_untyped_osc_argument_as_its_variables_type(arg):
    definition = {
        "id": "acme_mixer", "name": "Acme Mixer", "manufacturer": "Acme",
        "category": "audio", "version": "1.0.0", "transport": "osc",
        "default_config": {"host": "", "port": 10023},
        "state_variables": {"level": {"type": "integer"}, "mode": {"type": "string"}},
        "commands": {},
        "responses": [{"address": "/acme/level", "mappings": [
            {"arg": 0, "state": "level"}, {"arg": 1, "state": "mode"},
        ]}],
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = create_configurable_driver_class(definition)("m1", {"host": "127.0.0.1", "port": 10023}, state, events)
    assert driver._apply_osc_message("/acme/level", [arg, ("s", "hdmi")])
    assert driver.get_state("level") == 2
    assert isinstance(driver.get_state("level"), int)
    assert driver.get_state("mode") == "hdmi"


def test_the_model_holds_each_rule_in_file_order_and_skips_what_is_not_a_rule():
    responses = [{"match": "A"}, "not a rule", {"address": "/b"}, {"json": True}]
    model = read_driver({"responses": responses, "state_variables": _STATE_VARS})
    assert [rule.form for rule in model.wire.replies] == [REGEX, OSC, JSON]
    assert [rule.written for rule in model.wire.replies] == [responses[0], responses[2], responses[3]]


@pytest.mark.parametrize("responses", [None, "A", {"match": "A"}])
def test_a_responses_section_that_is_not_a_list_holds_no_rule(responses):
    assert read_driver({"responses": responses}).wire.replies == ()


def test_a_python_driver_has_no_rules_to_compile():
    model = read_driver({"responses": [{"match": "A"}]}, kind="python")
    compiled = compile_driver(model, {})
    assert compiled.responses == compiled.osc_responses == compiled.json_responses == []


def test_compiling_twice_gives_new_lists_and_leaves_the_model_as_it_was():
    rule = {"match": "P(\\d)", "set": {"power": "$1"}}
    model = read_driver({"responses": [rule], "state_variables": _STATE_VARS})
    first = compile_driver(model, {}).responses[0][1]
    second = compile_driver(model, {}).responses[0][1]
    assert first == second and first is not second
    first.append("edited")
    assert model.wire.replies[0].mappings == ({"group": 1, "state": "power", "type": "boolean"},)


_DEFINITION = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "commands": {},
    "state_variables": {"power": {"type": "boolean"}},
    "responses": [{"match": "P(\\d)", "set": {"power": "$1"}}],
}


def _driver(cls, config=None):
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    return cls("acme1", config or {}, state, events)


def test_the_runtime_reads_the_driver_file_once_for_its_class():
    cls = create_configurable_driver_class(_DEFINITION)
    one, two = _driver(cls), _driver(cls)
    assert one._reply_model() is two._reply_model() is cls._driver_model
    assert cls._driver_model.definition is cls._definition


def test_a_device_with_its_own_replies_reads_its_merged_definition():
    cls = create_configurable_driver_class(_DEFINITION)
    own = {"responses": [{"match": "L(\\d+)", "mappings": [{"group": 1, "state": "level"}]}]}
    drv = _driver(cls, own)
    model = drv._reply_model()
    assert model is not cls._driver_model
    assert model.definition is drv._definition
    assert len(model.wire.replies) == 2
    assert drv._reply_model() is model


def test_a_changed_setting_compiles_again_from_the_same_model():
    definition = {**_DEFINITION, "responses": [{"match": "P{unit}(\\d)", "set": {"power": "$1"}}]}
    definition["config_schema"] = {"unit": {"type": "string", "default": "1"}}
    cls = create_configurable_driver_class(definition)
    drv = _driver(cls, {"unit": "1"})
    model = drv._reply_model()
    drv.config["unit"] = "2"
    drv._config_changed({"unit"})
    assert drv._compiled_responses[0][0].pattern == "P2(\\d)"
    assert drv._reply_model() is model


# ── a state variable named in both forms ──
# The driver guide says the mappings: entry wins. The driver used to apply the
# set: item after it, so the set: item's value was the one left in state.

_BOTH = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "commands": {},
    "state_variables": {"mode": {"type": "string"}, "level": {"type": "integer"}},
    "responses": [
        {
            "match": "M(\\d) L(\\d+)",
            "mappings": [{"group": 1, "state": "mode", "map": {"1": "High", "0": "Low"}}],
            "set": {"mode": "$1", "level": "$2"},
        }
    ],
}


async def test_a_state_named_in_both_forms_takes_the_mappings_entry():
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    drv = create_configurable_driver_class(_BOTH)("acme1", {}, state, events)
    await drv.on_data_received(b"M1 L42")
    assert state.get("device.acme1.mode") == "High"
    assert state.get("device.acme1.level") == 42


def test_the_set_item_for_a_state_the_mappings_name_is_left_out():
    rule = _BOTH["responses"][0]
    assert read_reply_rule(rule, _BOTH["state_variables"]).mappings == (
        {"group": 1, "state": "mode", "map": {"1": "High", "0": "Low"}, "type": "string"},
        {"group": 2, "state": "level", "type": "integer"},
    )


def test_the_set_item_is_left_out_even_when_the_mappings_entry_names_no_capture():
    rule = {"match": "M(\\d)", "mappings": [{"group": "$x", "state": "mode"}], "set": {"mode": "$1"}}
    read = read_reply_rule(rule, _STATE_VARS)
    assert read.mappings == ()
    assert [skipped.state for skipped in read.skipped] == ["mode"]


def test_a_skipped_mapping_is_said_at_each_compile_naming_the_device(caplog):
    model = read_driver({"responses": [{"match": "P(\\d)", "set": {"power": "$x"}}]})
    with caplog.at_level("WARNING"):
        compile_driver(model, {}, device_id="acme1")
    assert caplog.messages == [
        "[acme1] set-shorthand reference '$x' for state 'power' is not a numeric group "
        "($1, $2, ...); skipping this mapping"
    ]
