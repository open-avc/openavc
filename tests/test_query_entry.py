"""What one query entry is (openavc/drivers/query_entry.py), and the driver
model that reads every one of them where it sits (openavc/drivers/driver_model.py).

Every reader of an entry asks the one reading: the checker, the published
schema (built from the same table of reasons), the runtime's expansion and
the simulator's choice of what it answers. The case table below is read by
each of them, so a new form or key reaches all four or fails here.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import spec
from openavc.drivers.avcdriver_semantic import check_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_model import (
    POLL,
    PYTHON,
    READ_BACK,
    START_UP,
    THEN_QUERY,
    ModelProblem,
    query_lines,
    read_driver,
)
from openavc.drivers.query_entry import (
    EACH_CHILD,
    LINE,
    LONG_LINE,
    OSC_MESSAGE,
    SEND,
    read_query_entry,
)
from openavc.simulator.query_handlers import CHILD, FLAT, LIST, PAGE, query_kind

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO_ROOT / "openavc" / "drivers" / "avcdriver.schema.json").read_text(encoding="utf-8"))

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "author": "Acme",
    "description": "A widget for these tests.",
    "source_url": "https://example.com/acme-widget",
    "transport": "tcp",
    "delimiter": "\r",
    "default_config": {"host": "", "port": 23, "meters": True, "zone_ids": "1,2"},
    "config_schema": {
        "host": {"type": "string"},
        "port": {"type": "integer"},
        "meters": {"type": "boolean"},
        "zone_ids": {"type": "string"},
    },
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "child_entity_types": {
        "zone": {
            "label": "Zone",
            "instances": {"ids_from": "zone_ids"},
            "state_variables": {"level": {"type": "integer", "label": "Level"}},
        }
    },
    "commands": {"get_power": {"label": "Get Power", "send": "PWR?\r"}},
    "responses": [{"match": "PWR=(\\d)", "set": {"power": "$1"}}],
}


def _driver(definition: dict[str, Any]):
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {**definition["default_config"], "host": "127.0.0.1"}, state, events)
    driver._register_declared_children()
    return driver


def _with_start_up(entry: Any) -> dict[str, Any]:
    return {**copy.deepcopy(BASE), "on_connect": [entry]}


# --- the forms and their defaults ---------------------------------------------

# Each entry: the form it reads as, the lines the runtime sends for it, and
# what the simulator takes it to read.
CASES: dict[str, tuple[Any, str | None, list[str], str | None]] = {
    "a string": ("PWR?", LINE, ["PWR?"], FLAT),
    "an empty string": ("", LINE, [""], FLAT),
    "send with when": ({"send": "PWR?", "when": "meters"}, SEND, ["PWR?"], FLAT),
    "send with query_for": ({"send": "PWR?", "query_for": "power"}, SEND, ["PWR?"], FLAT),
    "send with when_state": ({"send": "PWR?", "when_state": {"power": True}}, SEND, ["PWR?"], FLAT),
    "send alone": ({"send": "\r"}, SEND, ["\r"], FLAT),
    "a list read sent once": ({"send": "LVL?", "query_for": "zone.level"}, SEND, ["LVL?"], LIST),
    "each child": ({"each_child": "zone", "send": "Z{child_id}?"}, EACH_CHILD, ["Z1?", "Z2?"], CHILD),
    "each child, paged": (
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": 2}, EACH_CHILD, ["Z1?"], PAGE,
    ),
    "each child, page_size null": (
        {"each_child": "zone", "send": "Z{child_id}?", "page_size": None}, EACH_CHILD, ["Z1?", "Z2?"], CHILD,
    ),
    "each child with no send": ({"each_child": "zone"}, EACH_CHILD, [], CHILD),
    "send that is empty": ({"send": "", "when": "meters"}, None, [], FLAT),
    "send that is not text": ({"send": 5, "when": "meters"}, None, [], FLAT),
    "an OSC message outside on_connect": ({"address": "/a", "args": []}, None, [], FLAT),
    "a mapping of nothing known": ({"foo": 1}, None, [], FLAT),
    "an empty mapping": ({}, None, [], FLAT),
    "a number": (5, None, [], FLAT),
}


@pytest.mark.parametrize("entry,form", [(c[0], c[1]) for c in CASES.values()], ids=list(CASES))
def test_an_entry_reads_as_its_form(entry: Any, form: str | None) -> None:
    assert read_query_entry(entry).form == form


@pytest.mark.parametrize("entry,lines", [(c[0], c[2]) for c in CASES.values()], ids=list(CASES))
def test_the_runtime_sends_what_the_form_says(entry: Any, lines: list[str]) -> None:
    assert _driver(copy.deepcopy(BASE))._expand_query(copy.deepcopy(entry)) == lines


@pytest.mark.parametrize("entry,kind", [(c[0], c[3]) for c in CASES.values()], ids=list(CASES))
def test_the_simulator_answers_what_the_form_says(entry: Any, kind: str | None) -> None:
    assert query_kind(entry) == kind


def test_an_osc_message_is_a_form_where_the_list_takes_one() -> None:
    read = read_query_entry({"address": "/ch/1/mix", "args": [{"type": "f", "value": 0.5}]}, osc_form=True)
    assert (read.form, read.address) == (OSC_MESSAGE, "/ch/1/mix")


def test_a_key_left_out_or_written_null_reads_as_absent() -> None:
    for entry in ({"send": "PWR?", "when": "meters"}, {"send": "PWR?", "when": "meters", "query_for": None}):
        read = read_query_entry(entry)
        assert (read.when_state, read.query_for, read.page_size) == (None, None, None)


def test_each_child_decides_the_form_by_being_there() -> None:
    read = read_query_entry({"each_child": None, "send": "Z?", "query_for": "zone.level"})
    assert (read.form, read.each_child) == (EACH_CHILD, None)
    assert query_kind({"each_child": None, "send": "Z?", "query_for": "zone.level"}) == LIST
    assert query_kind({"each_child": None, "send": "Z?"}) is None


# --- a line written the long way ------------------------------------------------


def test_a_send_with_no_reason_is_a_line_written_the_long_way() -> None:
    problem = read_query_entry({"send": "\r"}).problem
    assert problem is not None and problem.code == LONG_LINE
    assert problem.words == (
        "a mapping with only 'send' is a plain query written the long way: "
        "write it as a string, or add 'when', 'query_for' or 'when_state'"
    )
    for reason in spec.QUERY_ENTRY_REASONS:
        assert read_query_entry({"send": "\r", reason: "meters"}).problem is None


def test_the_schema_takes_a_send_entry_for_exactly_the_reasons_the_reader_does() -> None:
    validator = Draft202012Validator(SCHEMA)
    for entry in ({"send": "\r"}, *({"send": "\r", key: {"power": True} if key == "when_state" else "x"}
                                    for key in spec.QUERY_ENTRY_REASONS)):
        schema_takes = not list(validator.iter_errors(_with_start_up(entry)))
        assert schema_takes == (read_query_entry(entry).problem is None), entry


def test_a_send_only_entry_is_refused_where_it_is_written_and_still_sent_where_it_loads() -> None:
    """The start-up line written as ``{send: "\\r"}``: the published schema and
    every author's door refuse it at the entry's own place, a door that loads
    the driver names it and loads it, and the loaded driver sends the line."""
    validator = Draft202012Validator(SCHEMA)
    assert not list(validator.iter_errors(_with_start_up("\r")))
    definition = _with_start_up({"send": "\r"})
    assert [list(e.path) for e in validator.iter_errors(definition)] == [["on_connect", 0]]
    authored = check_driver_definition(definition, strict=True).errors
    assert authored.tags == ["on_connect[0]"]  # type: ignore[attr-defined]
    loaded = check_driver_definition(definition, strict=False)
    assert list(loaded.errors) == []
    assert loaded.notices.tags == ["on_connect[0]"]  # type: ignore[attr-defined]
    assert _driver(definition)._expand_query({"send": "\r"}) == ["\r"]


def test_the_long_line_is_said_in_its_own_words_once_at_each_door() -> None:
    said = (
        "on_connect[0]: a mapping with only 'send' is a plain query written the long way: "
        "write it as a string, or add 'when', 'query_for' or 'when_state'"
    )
    definition = _with_start_up({"send": "\r"})
    assert list(check_driver_definition(definition, strict=True).errors) == [said]
    assert list(check_driver_definition(definition, strict=False).notices) == [said]


# --- the driver model -------------------------------------------------------------


def test_the_model_reads_each_query_line_where_it_sits_in_report_order() -> None:
    definition = {
        **copy.deepcopy(BASE),
        "polling": {"queries": ["PWR?", {"each_child": "zone", "send": "Z{child_id}?"}]},
        "on_connect": [{"address": "/a"}, {"send": "\r"}],
        "responses": [
            {"match": "A", "set": {"power": "true"}},
            {"match": "CHG", "then_query": ["PWR?"]},
        ],
    }
    model = read_driver(definition)
    assert [(line.origin, line.place, line.entry.form) for line in model.wire.queries] == [
        (POLL, "polling.queries[0]", LINE),
        (POLL, "polling.queries[1]", EACH_CHILD),
        (START_UP, "on_connect[0]", OSC_MESSAGE),
        (START_UP, "on_connect[1]", SEND),
        (THEN_QUERY, "responses[1].then_query[0]", LINE),
    ]
    assert model.problems == (
        ModelProblem("on_connect[1]", LONG_LINE, read_query_entry({"send": "\r"}).problem.words),
    )


def test_the_model_reads_each_commands_read_back_after_the_follow_ups() -> None:
    definition = {
        **copy.deepcopy(BASE),
        "responses": [{"match": "CHG", "then_query": ["PWR?"]}],
        "commands": {
            "power_on": {"send": "PWR1\r", "read_back": "PWR?"},
            "volume": {"send": "VOL{level}\r", "read_back": ["VOL?", True, "power_on", 5]},
            "mute": {"send": "MUTE\r", "read_back": True},
            "odd": "MUTE?",
        },
    }
    model = read_driver(definition)
    assert [(line.origin, line.place, line.written, line.source) for line in model.wire.queries] == [
        (THEN_QUERY, "responses[0].then_query[0]", "PWR?", 0),
        (READ_BACK, "commands.power_on.read_back", "PWR?", "power_on"),
        (READ_BACK, "commands.volume.read_back[0]", "VOL?", "volume"),
        (READ_BACK, "commands.volume.read_back[2]", "power_on", "volume"),
    ]
    assert model.problems == ()


def test_query_lines_are_each_line_once_with_every_entry_that_sends_it() -> None:
    definition = {
        **copy.deepcopy(BASE),
        "polling": {"queries": [{"send": "VOL?", "query_for": "volume"}, "PWR?", {"send": ""}]},
        "on_connect": [{"address": "/a"}, "VOL?"],
        "responses": [{"match": "VOL", "then_query": ["VOL?", "MUTE?"]}],
        "commands": {"mute": {"send": "MUTE\r", "read_back": ["MUTE?", "VOL?"]}},
    }
    model = read_driver(definition)
    lines = query_lines(model)
    assert [(sent.text, sent.origins) for sent in lines] == [
        ("VOL?", (POLL, START_UP, THEN_QUERY, READ_BACK)),
        ("PWR?", (POLL,)),
        ("/a", (START_UP,)),
        ("MUTE?", (THEN_QUERY, READ_BACK)),
    ]
    volume = lines[0]
    assert [line.place for line in volume.lines] == [
        "polling.queries[0]", "on_connect[1]", "responses[0].then_query[0]", "commands.mute.read_back[1]",
    ]
    # Each reader states its filter: by where a line is sent from, and by the entry.
    assert [sent.text for sent in query_lines(model, (THEN_QUERY, READ_BACK))] == ["VOL?", "MUTE?"]
    no_poll = query_lines(model, where=lambda line: line.origin != POLL)
    assert [(sent.text, sent.lines[0].place) for sent in no_poll] == [
        ("/a", "on_connect[0]"), ("VOL?", "on_connect[1]"), ("MUTE?", "responses[0].then_query[1]"),
    ]
    assert query_lines(read_driver(definition, kind=PYTHON)) == ()


def test_the_model_reads_a_file_it_cannot_make_sense_of_without_raising() -> None:
    for definition in (
        None, [], {"polling": "PWR?"}, {"polling": {"queries": "PWR?"}},
        {"on_connect": {"send": "x"}}, {"responses": "x"}, {"responses": ["x", {"then_query": "Q"}]},
    ):
        model = read_driver(definition)  # type: ignore[arg-type]
        assert model.wire.queries == () and model.problems == ()


def test_a_python_driver_has_no_wire_to_read() -> None:
    model = read_driver({"id": "acme_widget", "commands": {}}, kind=PYTHON)
    assert (model.kind, model.wire, model.problems) == (PYTHON, None, ())


def test_the_model_is_frozen() -> None:
    model = read_driver(_with_start_up("PWR?"))
    with pytest.raises(AttributeError):
        model.problems = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        model.wire.queries[0].entry.send = "x"  # type: ignore[misc]


# --- what each module may import ---------------------------------------------------


def _modules_loaded(module: str) -> set[str]:
    """The openavc modules importing ``module`` loads in a fresh interpreter
    that can import nothing but the standard library (-I -S), plus yaml if
    it came in."""
    code = (
        "import sys\n"
        "sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        "import " + module + "\n"
        "names = sorted(m for m in sys.modules if m.startswith('openavc'))\n"
        "if 'yaml' in sys.modules: names.append('yaml')\n"
        "print('\\n'.join(names))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True, cwd=REPO_ROOT, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return {line for line in result.stdout.splitlines() if line}


def test_the_query_entry_reader_stays_a_leaf() -> None:
    assert _modules_loaded("openavc.drivers.query_entry") == {
        "openavc", "openavc.drivers", "openavc.drivers.spec", "openavc.drivers.query_entry",
    }


def test_the_driver_model_stays_pure() -> None:
    # The checker's closure, so the catalog's CI and the SDK can import it: no
    # logger, no yaml, no runtime, nothing outside the standard library.
    assert _modules_loaded("openavc.drivers.driver_model") == {
        "openavc", "openavc.drivers", "openavc.drivers.spec", "openavc.drivers.query_entry",
        "openavc.drivers.framing_checks", "openavc.drivers.auth_block", "openavc.drivers.driver_model",
        "openavc.drivers.usable_number", "openavc.drivers.command_form", "openavc.drivers.yaml_text",
        "openavc.drivers.reply_rule", "openavc.drivers.capture_ref", "openavc.drivers.json_value_spec",
        "openavc.drivers.pattern_fill", "openavc.drivers.enum_options", "openavc.drivers.declared_names",
        "openavc.drivers.tagged_errors",
    }
