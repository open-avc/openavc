"""Every message a driver's checks report lands on the place it is about.

``validate_driver_issues`` pairs each message with a path, and the Driver
Builder puts the issue on the tab, and beside the control, that path names.
A check that reports about a place the walk is not standing on hands that
place back with its message; the place is never borrowed from where the walk
happened to stop last, nor cut from the message's own text.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import validate_driver_issues

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {"noop": {"send": "NOOP\r"}},
}

NOT_TEXT = "is not text. YAML reads"


def _places(definition: dict[Any, Any], needle: str, *, strict: bool) -> list[str]:
    """The path of every error whose message holds ``needle``."""
    return [
        issue["path"]
        for issue in validate_driver_issues({**BASE, **definition}, strict=strict)
        if issue["severity"] == "error" and needle in issue["message"]
    ]


# --- a name YAML read as a boolean or a number ------------------------------
#
# The message is about the map that holds the name, so it lands on that map,
# whichever of the map's other entries the walk read before it.

@pytest.mark.parametrize("definition,place", [
    pytest.param(
        {"state_variables": {"volume": {"type": "integer", "label": "V"},
                             1: {"type": "integer", "label": "One"}}},
        "state_variables",
        id="a variable after a variable",
    ),
    pytest.param(
        {"state_variables": {"power": {"type": "boolean", "label": "Power"},
                             True: {"type": "integer", "label": "On"}}},
        "state_variables",
        id="a variable after one the walk descended into",
    ),
    pytest.param(
        {"commands": {"set": {"send": "S", "params": {"level": {"type": "integer"}}},
                      0: {"send": "Z"}}},
        "commands",
        id="a command after a command with a parameter",
    ),
    pytest.param(
        {"commands": {"noop": {"send": "N"}, 5: {"send": "F"}}},
        "commands",
        id="a command after a command",
    ),
    pytest.param(
        {"commands": {"set": {"send": "S", "params": {
            "level": {"type": "integer", "label": "L"}, True: {"type": "integer"}}}}},
        "commands.set.params",
        id="a parameter after a parameter",
    ),
    pytest.param(
        {"state_variables": {False: {"type": "integer", "label": "Off"},
                             "power": {"type": "boolean", "label": "Power"}}},
        "state_variables",
        id="the first name",
    ),
])
def test_a_name_that_is_not_text_lands_on_its_map(definition, place):
    assert _places(definition, NOT_TEXT, strict=True) == [place]


def test_two_names_that_are_not_text_both_land_on_their_map():
    definition = {"config_schema": {1.5: {"type": "string"}, None: {"type": "string"}}}
    assert _places(definition, NOT_TEXT, strict=True) == ["config_schema", "config_schema"]
