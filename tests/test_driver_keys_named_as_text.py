"""A name in a driver definition that YAML read as a number, beside a key the
contract does not declare, is named as text in the message about that key.

YAML reads a bare ``1:`` as the integer key 1. Where a check lists the keys it
does not know, or suggests the nearest declared name, it reads every name as
text, so ``1`` and ``zz`` side by side are reported as ``1, zz``.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers import avcdriver_semantic
from openavc.drivers.avcdriver_semantic import validate_driver_issues

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "commands": {"power_on": {"label": "On", "send": "PON\r"}},
    "responses": [{"match": r"PWR=(\d)", "mappings": [{"group": 1, "state": "power"}]}],
    "state_variables": {"power": {"type": "string", "label": "Power"}},
}


def _errors(definition: dict[Any, Any], **kwargs: Any) -> list[tuple[str, str]]:
    return [
        (issue["path"], issue["message"])
        for issue in validate_driver_issues(definition, **kwargs)
        if issue["severity"] == "error"
    ]


MULTICAST = {"type": "multicast", "group": "239.1.1.1", "port": 5000}


@pytest.mark.parametrize("over,expected", [
    (
        {"push": {**MULTICAST, 1: "x", "zz": "y"}},
        ("push", "push: unknown key(s): 1, zz (known keys for type 'multicast': "),
    ),
    (
        {"push": {"type": "multicast", 1: "x"}},
        ("push", "push: unknown key(s): 1 (known keys for type 'multicast': "),
    ),
    (
        {"push": {"type": "sse", "paths": ["/events"], "session": {"key": "id", 1: "x", "zz": "y"}}},
        ("push", "push: session has unknown key(s): 1, zz (known: "),
    ),
    (
        {"push": {"type": "tcp_listener", "port": 0, "register": [{"command": "power_on", 1: "x", "zz": "y"}]}},
        ("push", "push: register[0] has unknown key(s): 1, zz (known: "),
    ),
])
def test_the_push_blocks_unknown_keys_are_named_as_text(over, expected):
    errors = _errors({**BASE, **over})
    assert any(path == expected[0] and m.startswith(expected[1]) for path, m in errors), errors


def test_a_json_child_values_unknown_keys_are_named_as_text():
    definition = {
        **BASE,
        "child_entity_types": {"zone": {"state_variables": {"level": {"type": "number", "label": "Level"}}}},
        "responses": [{
            "json": True,
            "child_set": [{"type": "zone", "id": 1, "state": {
                "level": {"key": "level", 1: "x", "zz": "y"},
            }}],
        }],
    }
    errors = _errors(definition)
    assert any("state 'level' has ['1', 'zz'], which a json rule does not read" in m for _, m in errors), errors


def test_a_routing_planes_numbered_param_is_named_as_text():
    definition = {
        **BASE,
        "commands": {
            "route": {"send": "R {output} {input}\r", "params": {
                "output": {"type": "child_id", "child_type": "output"},
                "input": {"type": "integer"},
            }},
        },
        "child_entity_types": {"output": {"state_variables": {
            "source": {"type": "integer", "label": "Source"},
        }}},
        "routing": {"planes": [{
            "destinations": "output", "route_property": "source", "command": "route",
            "destination_param": "output", "source_param": "input", "params": {1: "a"},
        }]},
    }
    errors = _errors(definition)
    assert any("params.1 is not a parameter of command 'route'" in m for _, m in errors), errors


def test_top_level_keys_that_are_not_text_beside_unknown_ones_are_named():
    definition = {**BASE, 1: "x", "zz": "y"}
    assert avcdriver_semantic.unknown_key_errors(definition) == ["unknown key '1'", "unknown key 'zz'"]
    errors = _errors(definition, strict=True)
    assert errors[-2:] == [("", "unknown key '1'"), ("", "unknown key 'zz'")]
