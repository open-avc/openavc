"""The simulator validator counts the JSON keys a driver's json rules read, as the compile reads them.

A ``json: true`` rule reads a body's fields through its value specs. The
validator asks whether a simulated notification is a body the driver reads,
and it answers from the keys the compile gives the rules (``reply_rule``
and ``json_value_spec``), so a spec naming no key reads its state variable's
own name, a ``set:`` beside a non-empty ``mappings:`` list is not read, and a
shorthand that is not text is read as text, all as the runtime reads them.
Invented Acme drivers throughout.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from openavc.simulator.reply_reading import json_rule_keys
from openavc.simulator.validate import validate_yaml_driver

_IGNORED = "which matches no driver response pattern"


def _definition(responses: list[Any], notifications: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "state_variables": {
            "power": {"type": "boolean", "label": "Power"},
            "level": {"type": "integer", "label": "Level"},
        },
        "commands": {"power_on": {"send": "PWR 1\\r\\n"}},
        "responses": responses,
        "simulator": {"notifications": notifications},
    }


def _notification_lines(tmp_path, responses, notifications) -> list[str]:
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(
        yaml.safe_dump(_definition(responses, notifications), sort_keys=False),
        encoding="utf-8",
    )
    result = validate_yaml_driver(path)
    return [i.message for i in result.issues if i.check == "notifications"]


_REGEX = {"match": "^PWR (\\d)$", "set": {"power": "$1"}}


@pytest.mark.parametrize(
    "spec", [{"type": "boolean"}, {"key": "power"}, {"path": "power"}, "power"]
)
def test_a_body_the_driver_reads_by_a_set_spec_draws_no_warning(tmp_path, spec):
    responses = [{"json": True, "set": {"power": spec}}, _REGEX]
    lines = _notification_lines(tmp_path, responses, {"power": {"true": '{"power": true}'}})
    assert not [line for line in lines if _IGNORED in line]


def test_a_set_beside_a_mappings_list_reads_nothing_so_its_body_is_ignored(tmp_path):
    responses = [
        {"json": True, "set": {"power": "other"}, "mappings": [{"state": "level", "key": "v"}]},
        _REGEX,
    ]
    lines = _notification_lines(tmp_path, responses, {"power": {"true": '{"other": true}'}})
    assert [line for line in lines if _IGNORED in line]


def test_a_mappings_entry_the_driver_reads_draws_no_warning(tmp_path):
    responses = [{"json": True, "mappings": [{"state": "level", "key": "zone.v"}]}, _REGEX]
    lines = _notification_lines(tmp_path, responses, {"level": '{"zone": {"v": {value}}}'})
    assert not [line for line in lines if _IGNORED in line]


def test_the_keys_are_the_ones_the_compile_reads():
    responses = [
        {"json": True, "set": {"power": {"type": "boolean"}, "level": 5}},
        {"json": True, "set": {"power": "skipped"}, "mappings": [{"state": "level", "key": "a.b"}]},
        {"json": True, "mappings": [{"key": "no_state"}, {"state": "level", "path": "no_key"}]},
        {"json": True, "child_set": [{"type": "zone", "id": 1, "state": {"level": {"type": "integer"}, "gain": "g[0]"}}]},
        {"match": "^X$", "set": {"power": {"key": "not_json"}}},
    ]
    assert json_rule_keys(responses) == {"power", "5", "a", "g"}


@pytest.mark.parametrize(
    "rule",
    [
        {"json": True, "mappings": None, "set": {"power": "p"}},
        {"json": True, "mappings": {"power": "p"}},
        {"json": True, "mappings": ["junk", 3, None]},
        {"json": True, "set": None, "child_set": "junk"},
        {"json": True, "child_set": [None, {"state": None}, {"state": ["a"]}]},
        {"json": True, "set": {"power": {"key": None}}, "child_set": [{"state": {"level": {"key": None}}}]},
    ],
)
def test_a_rule_the_checker_refuses_is_read_without_raising(rule):
    json_rule_keys([rule])
