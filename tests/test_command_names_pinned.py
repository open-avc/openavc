"""What a command's name says when the command declares nothing.

A command with no ``sets:`` / ``query_for:`` leaves the auto-generated
simulator to guess from its name: which state variable it targets
(``infer_state_var``: one prefix and one suffix stripped, then a direct match,
then a short alias list) and, for a name that switches something, the value
it sets (``_on`` / ``enable_`` true, ``_off`` / ``disable_`` false,
``_toggle`` nothing). These pin both, including the precedence when a name
carries more than one of those words. On an invented Acme device.
"""

from __future__ import annotations

import pytest

from openavc.drivers.compiled_protocol import infer_state_var
from openavc.simulator.yaml_auto import YAMLAutoSimulator

_VARS = {
    "volume", "mute", "power", "input", "video_mute", "on", "toggle", "set_x",
    "query_input",
}


@pytest.mark.parametrize(
    "name, everything, only_mute",
    [
        ("set_volume", "volume", None),
        ("mute_on", "mute", "mute"),
        ("power_off", "power", None),
        ("query_input", "input", None),
        ("volume", "volume", None),
        # Aliases: by the stripped name, else by the whole name.
        ("route_all", "input", None),
        ("route", "input", None),
        ("get_route", "input", None),
        ("unmute", "mute", "mute"),
        ("vol", "volume", None),
        ("set_vol", "volume", None),
        ("audio_mute", "mute", "mute"),
        ("video_mute", "video_mute", None),
        ("video_mute_on", "video_mute", None),
        ("enable_mute", "mute", "mute"),
        ("mute_toggle", "mute", "mute"),
        # One prefix and one suffix at most, each the first that fits.
        ("disable_power_toggle", "power", None),
        ("set_power_on_off", None, None),
        ("x_toggle_on", None, None),
        ("query_query_input", "query_input", None),
        ("enable_set_volume", None, None),
        # A name that is all affix leaves nothing, or the affix's own word.
        ("get_", None, None),
        ("_on", None, None),
        ("set_on", "on", None),
        ("toggle", "toggle", None),
        # The whole name is never matched directly once something was stripped.
        ("set_x", None, None),
        ("nothing", None, None),
    ],
)
def test_the_state_variable_a_name_points_at(name, everything, only_mute):
    assert infer_state_var(name, _VARS) == everything
    assert infer_state_var(name, {"mute"}) == only_mute
    assert infer_state_var(name, set()) is None


_DRIVER = {
    "id": "acme_switcher",
    "transport": "tcp",
    "delimiter": "\\r",
    "state_variables": {
        "power": {"type": "boolean"},
        "mute": {"type": "boolean"},
        "volume": {"type": "integer"},
        "input": {"type": "integer"},
    },
    "commands": {
        "power_on": {"send": "PON"},
        "power_off": {"send": "POF"},
        "enable_mute": {"send": "MON"},
        "disable_mute": {"send": "MOF"},
        "mute_toggle": {"send": "MT"},
        "enable_power_off": {"send": "EPO"},
        "disable_mute_on": {"send": "DMO"},
        "set_volume": {"send": "V{level}", "params": {"level": {"type": "integer"}}},
        "query_input": {"send": "I?"},
        "route": {"send": "R{x}", "params": {"x": {"type": "integer"}}},
        "unmute": {"send": "UM"},
        "power_toggle_on": {"send": "PTO"},
    },
    "responses": [],
}


def test_what_the_simulator_sets_from_a_command_name():
    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=_DRIVER)
    got = {h.name: (h.state_changes, h.response_var) for h in sim._command_handlers}
    assert got == {
        "power_on": ({"power": True}, "power"),
        "power_off": ({"power": False}, "power"),
        "enable_mute": ({"mute": True}, "mute"),
        "disable_mute": ({"mute": False}, "mute"),
        # A toggle sets nothing; the handler reports the variable.
        "mute_toggle": ({}, "mute"),
        # The true words are checked first: an enable_ prefix beats an _off
        # suffix, and an _on suffix beats a disable_ prefix.
        "enable_power_off": ({"power": True}, "power"),
        "disable_mute_on": ({"mute": True}, "mute"),
        # A set_ name with a param takes the param's capture.
        "set_volume": ({"volume": 1}, "volume"),
        # A query with no param reports the variable it names.
        "query_input": ({}, "input"),
        "route": ({"input": 1}, "input"),
        # An alias with nothing to switch and no param reports, sets nothing.
        "unmute": ({}, "mute"),
        # Only the _on is stripped, leaving power_toggle, which names nothing.
        "power_toggle_on": ({}, None),
    }
