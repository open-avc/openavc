"""The shape walk (``drivers/shape_checks.py``) held to the published driver
schema it is generated beside, and the verdict it gives at each kind of door.

Both are read off ``spec.FIELDS`` and ``spec.DEFS``. Over every definition
the validation corpora hold, the built-in drivers and the shapes below:

* the walk never refuses a value the schema takes, and every place it names
  is one the schema refuses at or above;
* every refusal the schema makes of a kind, a value set or a required key
  that the walk does not make is one of the kinds it leaves
  (``shape_checks.LEFT_KEYS``) to the definition walk and the catalog;
* every key a registry node carries is one the walk reads or one it leaves.

Then what the walk says at an author's door (an error) and at a door that
loads a driver (a notice, the driver still loading), and a null top-level
section read as absent by the checker, the schema and the runtime alike.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from openavc.drivers import discovery_block, spec
from openavc.drivers.avcdriver_semantic import check_driver_definition, unknown_key_errors
from openavc.drivers.shape_checks import LEFT_FIELDS, LEFT_KEYS, READ_KEYS, shape_refusals

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = json.loads((_ROOT / "openavc/drivers/avcdriver.schema.json").read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA)

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {"noop": {"send": "NOOP\r"}},
}
TEXT_RULE = {"match": "P(\\d)", "set": {"power": "$1"}}

# Shapes the bug reports and the design review name, each over BASE.
SHAPES: dict[str, dict[str, Any]] = {
    "id_a_number": {"id": 5},
    "name_a_number": {"name": 5},
    "version_a_number": {"version": 1.0},
    "actions_a_number": {"actions": 5},
    "mappings_a_number": {"responses": [{**TEXT_RULE, "mappings": 5, "then_query": ["P?\r"]}]},
    "variable_type_a_list": {"state_variables": {"power": {"type": ["number"], "label": "Power"}}},
    "polling_null": {"polling": None},
    "polling_queries_text": {"polling": {"queries": "PWR?\r"}},
    "on_connect_text": {"on_connect": "PWR?\r"},
    "on_connect_send_only": {"on_connect": [{"send": "\r"}]},
    "config_schema_text": {"config_schema": "port"},
    "simulator_initial_state_a_list": {"simulator": {"initial_state": [["power", True]]}},
    "simulator_control_unknown_type": {"simulator": {"controls": [{"type": "meter", "key": "power"}]}},
    "simulator_control_without_type": {"simulator": {"controls": [{"key": "power"}]}},
    "simulator_control_missing_a_field": {"simulator": {"controls": [{"type": "slider", "key": "power"}]}},
    "simulator_control_option_a_mapping": {
        "simulator": {"controls": [{"type": "select", "key": "power", "options": [{"value": 1}]}]},
    },
    "simulator_control_label_a_boolean": {
        "simulator": {"controls": [{"type": "select", "key": "power", "options": [0, 1], "labels": {"0": False}}]},
    },
    "simulator_control_in_a_group": {
        "simulator": {"controls": [{"type": "group", "controls": [{"type": "power", "key": "power"}, {"type": 5}]}]},
    },
    "simulator_controls_every_type": {"simulator": {"controls": [
        {"type": "power", "key": "power", "extra": 1},
        {"type": "select", "key": "power", "options": [True, False, "on", 2], "labels": {"True": "On"}},
        {"type": "slider", "key": "power", "min": 0, "max": 9.5, "step": 0.5, "unit": "dB"},
        {"type": "toggle", "key": "power", "label": "Power"},
        {"type": "matrix", "inputs": 4.0, "outputs": 2, "state_pattern": "r{output}", "input_labels": ["A"]},
        {"type": "meters", "channels": 2, "key_pattern": "c{ch}", "mute_pattern": "m{ch}"},
        {"type": "presets", "key": "power", "names": ["A", "B"]},
        {"type": "group", "label": "G", "controls": []},
        {"type": "indicator", "key": "power", "color_map": {"on": "#0f0"}},
    ]}},
    "default_config_text": {"default_config": "port=23"},
    "bridge_ports_a_mapping": {"bridge": {"ports": {"a": 1}}},
    "mapping_state_a_list": {"responses": [{"match": "P(\\d)", "mappings": [{"group": 1, "state": ["power"]}]}]},
    "setup_kind_in_yaml": {"actions": [{"id": "a", "kind": "setup", "label": "A"}]},
    "every_null_section": {name: None for name in spec.NULL_AS_ABSENT},
}

# What the schema refuses that the walk leaves: these JSON Schema keywords
# are LEFT_KEYS' (the root's required fields are the definition walk's).
_LEFT_KEYWORDS = frozenset({
    "pattern", "format", "minimum", "maximum", "exclusiveMinimum", "minLength",
    "minItems", "minProperties", "propertyNames", "not", "additionalProperties",
    "if", "then", "else", "allOf", "dependentRequired",
})
_CATALOG_ONLY = frozenset(spec.CATALOG_REQUIRED_FIELDS)


def _corpus() -> dict[str, Any]:
    cases = json.loads((_ROOT / "tests/fixtures/driver_validation_cases.json").read_text(encoding="utf-8"))
    corpus = {f"message:{k}": v for k, v in cases.items()}
    from tests import test_definition_walk_pinned as walk_pins

    corpus.update({f"walk:{k}": walk_pins._definition(k) for k in walk_pins.CASES})
    corpus.update({f"shape:{k}": {**BASE, **v} for k, v in SHAPES.items()})
    for path in sorted((_ROOT / "openavc/drivers/definitions").glob("*.avcdriver")):
        corpus[f"builtin:{path.stem}"] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {k: v for k, v in corpus.items() if isinstance(v, dict)}


CORPUS = _corpus()


def _place(path: Any) -> str:
    out = ""
    for part in path:
        out = f"{out}[{part}]" if isinstance(part, int) else (f"{out}.{part}" if out else str(part))
    return out


def _related(a: str, b: str) -> bool:
    return any(x == y or x.startswith(y + ".") or x.startswith(y + "[") or not y for x, y in ((a, b), (b, a)))


def _schema_refusals(definition: dict[str, Any]) -> list[Any]:
    """The schema's refusals, less a catalog-only field left out."""
    return [
        e for e in _VALIDATOR.iter_errors(definition)
        if not (e.validator == "required" and not e.absolute_path
                and any(f"'{f}'" in e.message for f in _CATALOG_ONLY))
    ]


def _left_only(error: Any) -> bool:
    """Whether the schema refused only by keywords the walk leaves. Under a
    choice of forms: whether some form of the value's kind refused it only
    so (the walk takes the value as that form); a value more than one form
    takes is no refusal the walk makes (it never judges that a choice is
    exclusive)."""
    if {"then", "else"} & set(error.absolute_schema_path):
        return True  # a raw rule (cross-field, push's per-type keys)
    if error.validator not in ("oneOf", "anyOf"):
        return error.validator in _LEFT_KEYWORDS
    if not error.context:
        return True
    by_form: dict[Any, list[Any]] = {}
    for sub in error.context:
        by_form.setdefault(sub.relative_schema_path[0], []).append(sub)
    return any(
        all(_left_only(s) for s in subs)
        for subs in by_form.values()
        if not any(s.validator == "type" and not s.relative_path for s in subs)
    )


@pytest.mark.parametrize("case", sorted(CORPUS))
def test_the_walk_refuses_only_what_the_schema_refuses(case: str) -> None:
    definition = spec.without_null_sections(CORPUS[case])
    schema_places = [_place(e.absolute_path) for e in _schema_refusals(CORPUS[case])]
    for loc, message in shape_refusals(definition):
        assert any(_related(loc, place) for place in schema_places), (loc, message, schema_places)


@pytest.mark.parametrize("case", sorted(CORPUS))
def test_what_the_schema_refuses_and_the_walk_does_not_is_left_on_purpose(case: str) -> None:
    definition = spec.without_null_sections(CORPUS[case])
    walk_places = [loc for loc, _ in shape_refusals(definition)]
    for error in _schema_refusals(CORPUS[case]):
        place = _place(error.absolute_path)
        if error.validator == "required" and not error.absolute_path:
            continue  # the definition walk's "Missing required field"
        if error.absolute_path and error.absolute_path[0] in LEFT_FIELDS:
            continue  # the discovery parser's
        if _left_only(error):
            continue
        assert any(_related(place, loc) for loc in walk_places), (place, error.validator, error.message)


def test_every_registry_key_is_read_or_left_on_purpose() -> None:
    used: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            used.update(node)
            for key in ("fields", "extra", "items", "prop_names"):
                sub = node.get(key)
                if key == "fields" and isinstance(sub, dict):
                    for child in sub.values():
                        visit(child)
                elif isinstance(sub, dict):
                    visit(sub)
            for key in ("one_of", "any_of", "all_of"):
                for sub in node.get(key, ()):
                    visit(sub)

    for node in [*spec.FIELDS.values(), *spec.DEFS.values()]:
        visit(node)
    assert used <= READ_KEYS | LEFT_KEYS, sorted(used - READ_KEYS - LEFT_KEYS)
    assert not READ_KEYS & LEFT_KEYS


def test_the_built_in_drivers_are_the_shape_they_declare() -> None:
    for case, definition in CORPUS.items():
        if case.startswith("builtin:"):
            assert shape_refusals(definition) == [], case


# --- what each kind of door says ---------------------------------------------


@pytest.mark.parametrize(
    ("over", "said"),
    [
        ({"name": 5}, "name: must be a string; put it in quotes"),
        ({"on_connect": "PWR?\r"}, "on_connect: must be a list; write a single entry as a one-item list"),
        ({"polling": {"queries": "PWR?\r"}}, "polling.queries: must be a list; write a single entry as a one-item list"),
        ({"config_schema": "port"}, "config_schema: must be a mapping"),
        ({"simulator": {"initial_state": [["power", True]]}}, "simulator.initial_state: must be a mapping"),
        (
            {"simulator": {"controls": [{"type": "meter", "key": "power"}]}},
            "simulator.controls[0].type: must be 'power', 'select', 'slider', 'toggle', 'matrix', "
            "'meters', 'presets', 'group' or 'indicator', not 'meter'",
        ),
        ({"on_connect": [{"send": "\r"}]}, "on_connect[0]: is not in any of the forms it can take"),
    ],
)
def test_an_author_door_refuses_a_wrong_shape_and_a_load_door_names_it(
    over: dict[str, Any], said: str
) -> None:
    definition = {**BASE, **over}
    assert list(check_driver_definition(definition, strict=True).errors) == [said]
    loaded = check_driver_definition(definition, strict=False)
    assert list(loaded.errors) == []
    assert list(loaded.notices) == [said]


def test_a_part_that_already_said_it_keeps_its_own_words() -> None:
    """A wrong kind the definition walk names in words that name the fix is
    said once, in those words."""
    definition = {**BASE, "actions": [{"id": "a", "kind": "setup", "label": "A"}]}
    errors = check_driver_definition(definition, strict=True).errors
    assert list(errors) == [
        "actions[0]: kind 'setup' requires a Python driver (a run_setup_action handler); "
        "YAML drivers support kind 'command' only"
    ]


def test_an_id_that_is_not_text_is_refused_at_every_door() -> None:
    for strict in (True, False):
        verdict = check_driver_definition({**BASE, "id": 5}, strict=strict)
        assert list(verdict.errors) == ["id: must be a string; put it in quotes"]
        assert verdict.errors.tags == ["id"]  # type: ignore[attr-defined]


def test_a_key_the_contract_does_not_declare_is_no_notice_at_a_load_door() -> None:
    """A correct driver written for a newer platform carries keys this one
    does not know, so at a load door an undeclared key is only logged (by the
    loader), and the platform floor is not asked; the author's doors refuse
    both."""
    definition = {**BASE, "lable": "x", "min_platform_version": "0.1.0", "polling": {"when_state": {"power": True}}}
    loaded = check_driver_definition(definition, strict=False)
    assert (list(loaded.errors), list(loaded.notices)) == ([], [])
    refused = check_driver_definition(definition, strict=True).errors
    assert refused.tags == ["", "min_platform_version"]  # type: ignore[attr-defined]


# --- a null section is absent everywhere -------------------------------------


@pytest.mark.parametrize("name", spec.NULL_AS_ABSENT)
def test_a_null_section_is_absent_to_the_checker_and_the_schema(name: str) -> None:
    definition = {**BASE, name: None}
    for strict in (True, False):
        verdict = check_driver_definition(definition, strict=strict)
        assert (list(verdict.errors), list(verdict.notices)) == ([], [])
    assert _schema_refusals({**definition, **{f: "x" for f in _CATALOG_ONLY}, "version": "1.0.0",
                             "category": "utility", "source_url": "https://example.com"}) == []


@pytest.mark.parametrize("name", [n for n, node in spec.FIELDS.items() if "req" in node])
def test_a_required_field_left_null_is_not_read_as_absent(name: str) -> None:
    assert name not in spec.NULL_AS_ABSENT
    assert spec.without_null_sections({**BASE, name: None}) == {**BASE, name: None}


def test_the_null_read_leaves_the_definition_it_was_handed_alone() -> None:
    definition = {**BASE, "polling": None}
    read = spec.without_null_sections(definition)
    assert "polling" not in read and definition["polling"] is None
    assert spec.without_null_sections(BASE) is BASE


class _Recorder:
    def __init__(self) -> None:
        self.connected = True
        self.sent: list[Any] = []

    async def send(self, data: Any) -> None:
        self.sent.append(data)


def _driver(over: dict[str, Any]) -> Any:
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.configurable import create_configurable_driver_class

    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class({**BASE, **over})
    driver = cls("acme_1", {"host": "127.0.0.1", "port": 23}, state, events)
    driver.transport = _Recorder()
    return driver


def test_a_null_polling_section_polls_nothing_and_never_raises() -> None:
    driver = _driver({"polling": None})
    asyncio.run(driver.poll())
    assert driver.transport.sent == []


@pytest.mark.parametrize(
    "over",
    [{"on_connect": "PWR?\r"}, {"polling": {"queries": "PWR?\r"}}, {"on_connect": None}],
)
def test_a_start_up_or_poll_written_as_one_string_sends_nothing(over: dict[str, Any]) -> None:
    driver = _driver(over)
    asyncio.run(driver._run_on_connect())
    asyncio.run(driver.poll())
    assert driver.transport.sent == []


@pytest.mark.parametrize("name", ["avcdriver.schema.json", "pythondriver.schema.json"])
def test_the_published_schemas_are_valid_json_schema(name: str) -> None:
    """A field the generator lets take a null stays a schema an editor and
    the catalog can load (a kind named twice is not one)."""
    schema = json.loads((_ROOT / "openavc/drivers" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


def test_the_discovery_block_is_left_to_its_reader() -> None:
    """The shape walk says nothing a discovery block's reader says: every
    copy of the checker runs that reader, so it speaks for the block."""
    definition = {**BASE, "discovery": {"tcp_probe": {"port": "x"}}}
    assert shape_refusals(definition) == []
    assert list(check_driver_definition(definition, strict=True).errors) == [
        "discovery.tcp_probe.port must be an integer"
    ]


def test_the_schema_refuses_a_key_a_probe_s_second_step_does_not_read() -> None:
    """An editor and the catalog's schema run refuse a misspelled key in a
    tcp_probe's ``then:`` as the block's reader does, so a typo is never
    accepted there and the check it meant silently left out."""
    def with_then(then: dict[str, Any]) -> dict[str, Any]:
        probe = {"port": 23, "send_ascii": "ID?\r", "expect": "ACME", "then": then}
        return {**BASE, "discovery": {"tcp_probe": probe}}

    then = {"send_ascii": "BIG?\r", "expect_silence": True}
    assert _schema_refusals(with_then(then)) == []
    definition = with_then({**then, "expect_silense": True})
    refusals = _schema_refusals(definition)
    assert len(refusals) == 1
    deepest = best_match(refusals)
    assert _place(deepest.absolute_path) == "discovery.tcp_probe.then"
    assert "expect_silense" in deepest.message
    assert list(check_driver_definition(definition, strict=True).errors) == [
        "discovery.tcp_probe.then has unknown keys: ['expect_silense']"
    ]


def test_a_discovery_block_for_a_newer_release_is_not_judged_by_its_keys() -> None:
    """Only the block's reader honours ``requires:``, so a key a newer
    release reads is refused by nothing on an older one: the unknown-key
    check leaves the block to that reader, as the shape walk does."""
    then = {"send_ascii": "BIG?\r", "expect_silence": True, "a_newer_key": True}
    probe = {"port": 23, "send_ascii": "ID?\r", "expect": "ACME", "then": then}
    definition = {**BASE, "discovery": {"requires": "99.0.0", "tcp_probe": probe}}
    assert unknown_key_errors(definition) == []
    verdict = check_driver_definition(definition, platform_version="0.40.0", strict=True)
    assert list(verdict.errors) == []


def test_a_probe_s_second_step_declares_the_keys_its_reader_reads() -> None:
    """The contract's ``then:`` and the reader name the same keys, so the
    schema refuses exactly what the reader refuses."""
    declared = spec.DEFS["tcpProbe"]["fields"]["then"]
    assert declared.get("extra") is False
    assert set(declared["fields"]) == set(discovery_block._KNOWN_FOLLOW_UP_KEYS)


def test_a_python_driver_s_schema_takes_no_null_the_registry_does_not_declare() -> None:
    """DRIVER_INFO is read as written, so the null-as-absent read is the
    YAML schema's alone."""
    python = json.loads((_ROOT / "openavc/drivers/pythondriver.schema.json").read_text(encoding="utf-8"))
    for name in spec.NULL_AS_ABSENT:
        declared = spec.FIELDS[name].get("type")
        declared = (declared,) if isinstance(declared, str) else tuple(declared or ())
        if "null" in declared:
            continue
        rendered = python["properties"][name]
        kinds = rendered.get("type", [])
        assert "null" not in (kinds if isinstance(kinds, list) else [kinds]), name
        assert {"type": "null"} not in rendered.get("anyOf", []), name
