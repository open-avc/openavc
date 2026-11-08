"""Pins for what the AI tool handler's checks and dispatch answer today.

Each test records one observable: the exact sentence a binding, an action or
a variable is refused with (the sentence goes back to the cloud and into the
chat), the order the problems are joined in, what is only logged, which tools
exist and which of them run without the project lock, and what a tool that
raised is answered with. They pin present behaviour, so a restructure that
changes any of it fails here.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.ai_tool_handler import (
    TOOL_FAILED,
    AIToolHandler,
    _normalize_bindings,
    _validate_action,
    _validate_bindings,
    _validate_variable,
    describe_tool_failure,
)
from openavc.core.ui_events import DISPATCHED_ACTIONS

FAILED = "Binding validation failed: "
INTERACTIONS = (
    "audio_mute_route, audio_route, change, hold, mute_route, press, release, "
    "route, select, submit"
)
OPS = "eq, ne, gt, lt, gte, lte, truthy, falsy"


def _refusal(*problems: str) -> str:
    return FAILED + "; ".join(problems)


# ===== a single action =====


class TestValidateAction:
    @pytest.mark.parametrize("action", [{}, {"action": ""}, {"action": None}])
    def test_no_action_name(self, action):
        assert _validate_action(action, "p") == "p: missing 'action' field"

    def test_an_action_nested_in_the_action_field_is_named(self):
        err = _validate_action({"action": {"action": "macro", "macro": "go"}}, "p")
        assert err == (
            "p: 'action' must be the action NAME, got dict. You nested a 'macro' "
            "action inside it. Write the fields flat on this object -- "
            "{\"action\": \"device.command\", \"device\": ..., \"command\": ...} "
            "-- not an action wrapped in another action."
        )

    @pytest.mark.parametrize("value, kind", [
        ({"macro": "go"}, "dict"), ({"action": 5}, "dict"), (["macro"], "list"), (7, "int"),
    ])
    def test_a_non_name_without_a_nested_name(self, value, kind):
        err = _validate_action({"action": value}, "p")
        assert err == (
            f"p: 'action' must be the action NAME, got {kind}. Write the fields "
            "flat on this object -- {\"action\": \"device.command\", \"device\": "
            "..., \"command\": ...} -- not an action wrapped in another action."
        )

    def test_an_unknown_action_lists_the_dispatched_ones(self):
        err = _validate_action({"action": "delay"}, "p")
        assert err == (
            "p: action type 'delay' is not valid. Use: "
            + ", ".join(sorted(DISPATCHED_ACTIONS))
        )

    @pytest.mark.parametrize("action, field", [
        ({"action": "macro"}, "macro"),
        ({"action": "macro", "macro": None}, "macro"),
        ({"action": "macro", "macro": ""}, "macro"),
        ({"action": "device.command"}, "device"),
        ({"action": "device.command", "device": "amp"}, "command"),
        ({"action": "device.command", "command": "on"}, "device"),
        ({"action": "state.set"}, "key"),
        ({"action": "ui.navigate", "page": ""}, "page"),
        ({"action": "script.call"}, "function"),
        ({"action": "event.emit", "event": None}, "event"),
        ({"action": "value_map"}, "map"),
    ])
    def test_the_first_blank_required_field_is_named(self, action, field):
        err = _validate_action(action, "p")
        assert err == f"p: {action['action']} action requires '{field}'"

    @pytest.mark.parametrize("value", [0, False, [], "x"])
    def test_a_falsy_value_that_is_not_blank_is_present(self, value):
        assert _validate_action({"action": "ui.navigate", "page": value}, "p") is None

    def test_state_set_on_a_device_key_is_refused(self):
        err = _validate_action({"action": "state.set", "key": "device.amp.mute"}, "p")
        assert err == (
            "p: state.set cannot target a device key ('device.amp.mute'). Device "
            "state is read-only — drive the device with a device.command action "
            "using $value instead."
        )

    @pytest.mark.parametrize("key", ["var.mode", 5, ["device.x"]])
    def test_state_set_on_anything_else_passes(self, key):
        assert _validate_action({"action": "state.set", "key": key}, "p") is None

    @pytest.mark.parametrize("vmap", [["a"], "a", 3])
    def test_value_map_needs_an_object(self, vmap):
        err = _validate_action({"action": "value_map", "map": vmap}, "p")
        assert err == "p: value_map action requires 'map' to be an object"

    def test_value_map_checks_each_mapped_action_with_its_path(self):
        action = {"action": "value_map", "map": {
            "a": {"action": "macro", "macro": "go"},
            "b": [{"action": "macro", "macro": "go"}, {"action": "state.set"}],
            "c": {"action": "bogus"},
        }}
        assert _validate_action(action, "p") == "p.map[b][1]: state.set action requires 'key'"

    def test_value_map_a_single_mapped_action_has_no_index(self):
        action = {"action": "value_map", "map": {"a": {"action": "state.set"}}}
        assert _validate_action(action, "p") == "p.map[a]: state.set action requires 'key'"

    def test_value_map_skips_what_is_not_an_action_object(self):
        action = {"action": "value_map", "map": {"a": "macro", "b": ["x", 3, None], "c": None}}
        assert _validate_action(action, "p") is None

    def test_value_map_nests(self):
        inner = {"action": "value_map", "map": {"z": {"action": "state.set", "key": "device.d.p"}}}
        action = {"action": "value_map", "map": {"a": [inner]}}
        err = _validate_action(action, "p")
        assert err.startswith("p.map[a][0].map[z]: state.set cannot target a device key")

    def test_a_valid_action(self):
        assert _validate_action(
            {"action": "device.command", "device": "amp", "command": "on"}, "p"
        ) is None


# ===== the do half =====


class TestDoBindings:
    def test_retired_flat_keys_are_listed_sorted(self):
        err = _validate_bindings({"value": {}, "press": [], "do": {}, "feedback": 1})
        assert err == (
            "Binding validation failed: bindings use the show/do model. Put "
            "interaction actions under 'do' (do.press, do.change, do.route, ...) "
            "and state reads under 'show' (show.value, show.look, show.visible_when, "
            "show.items). Remove top-level: feedback, press, value"
        )

    @pytest.mark.parametrize("key", [
        "press", "release", "hold", "change", "submit", "select", "route",
        "audio_route", "mute_route", "audio_mute_route", "value", "variable",
        "text", "feedback", "color", "items", "selected", "visible_when", "meter",
    ])
    def test_every_retired_key(self, key):
        err = _validate_bindings({key: None})
        assert err.endswith(f"Remove top-level: {key}")

    @pytest.mark.parametrize("do, kind", [([], "list"), ("x", "str"), (0, "int")])
    def test_do_must_be_an_object(self, do, kind):
        assert _validate_bindings({"do": do}) == _refusal(f"'do' must be an object, got {kind}")

    def test_do_none_is_absent(self):
        assert _validate_bindings({"do": None, "show": None}) is None

    def test_an_unknown_interaction(self):
        assert _validate_bindings({"do": {"tap": []}}) == _refusal(
            f"do.tap: not a valid interaction. Use: {INTERACTIONS}"
        )

    def test_an_unknown_interaction_is_not_checked_further(self):
        assert _validate_bindings({"do": {"tap": "x", "hold": [{"action": "macro"}]}}) == _refusal(
            f"do.tap: not a valid interaction. Use: {INTERACTIONS}",
            "do.hold[0]: macro action requires 'macro'",
        )

    @pytest.mark.parametrize("val, kind", [({"action": "macro"}, "dict"), ("x", "str"), (None, "NoneType")])
    def test_an_interaction_must_be_a_list(self, val, kind):
        assert _validate_bindings({"do": {"change": val}}) == _refusal(
            f"do.change must be an array of action objects, got {kind}"
        )

    def test_each_item_must_be_an_action_object(self):
        assert _validate_bindings({"do": {"submit": ["x", {"action": "macro"}]}}) == _refusal(
            "do.submit[0]: expected an action object, got str",
            "do.submit[1]: macro action requires 'macro'",
        )

    def test_every_interaction_is_checked_in_order(self):
        do = {
            "press": [{"action": "macro"}],
            "bogus": [],
            "release": "x",
            "hold": [{"action": "state.set"}],
        }
        assert _validate_bindings({"do": do}) == _refusal(
            "do.press[0]: macro action requires 'macro'",
            f"do.bogus: not a valid interaction. Use: {INTERACTIONS}",
            "do.release must be an array of action objects, got str",
            "do.hold[0]: state.set action requires 'key'",
        )

    def test_an_unknown_mode(self):
        b = {"do": {"press": [{"action": "macro", "macro": "go", "mode": "spin"}]}}
        assert _validate_bindings(b) == _refusal(
            "do.press[0]: mode 'spin' is not valid. Use: tap, toggle, hold_repeat, tap_hold"
        )

    @pytest.mark.parametrize("mode", ["", None, "tap", "hold_repeat"])
    def test_modes_with_nothing_to_check(self, mode):
        b = {"do": {"press": [{"action": "macro", "macro": "go", "mode": mode}]}}
        assert _validate_bindings(b) is None

    @pytest.mark.parametrize("mode", [[], {}, 0])
    def test_an_empty_mode_of_another_kind_has_nothing_to_check(self, mode):
        b = {"do": {"press": [{"action": "macro", "macro": "go", "mode": mode}]}}
        assert _validate_bindings(b) is None

    def test_a_list_mode_raises_today(self):
        # Present behaviour, not a refusal: the membership test cannot hash it.
        b = {"do": {"press": [{"action": "macro", "macro": "go", "mode": ["toggle"]}]}}
        with pytest.raises(TypeError):
            _validate_bindings(b)

    def test_toggle_needs_a_key_and_an_off_action(self):
        b = {"do": {"press": [{"action": "macro", "macro": "go", "mode": "toggle"}]}}
        assert _validate_bindings(b) == _refusal(
            "do.press[0]: toggle mode requires 'toggle_key'",
            "do.press[0]: toggle mode requires 'off_action' (an action object)",
        )

    @pytest.mark.parametrize("toggle_key", ["", None, 0])
    def test_toggle_key_must_be_filled(self, toggle_key):
        b = {"do": {"press": [{
            "action": "macro", "macro": "go", "mode": "toggle",
            "toggle_key": toggle_key, "off_action": {},
        }]}}
        assert _validate_bindings(b) == _refusal("do.press[0]: toggle mode requires 'toggle_key'")

    @pytest.mark.parametrize("off_action", [None, [], "macro"])
    def test_toggle_off_action_must_be_an_object(self, off_action):
        b = {"do": {"press": [{
            "action": "macro", "macro": "go", "mode": "toggle",
            "toggle_key": "var.x", "off_action": off_action,
        }]}}
        assert _validate_bindings(b) == _refusal(
            "do.press[0]: toggle mode requires 'off_action' (an action object)"
        )

    def test_an_empty_off_action_is_not_checked(self):
        b = {"do": {"press": [{
            "action": "macro", "macro": "go", "mode": "toggle",
            "toggle_key": "var.x", "off_action": {},
        }]}}
        assert _validate_bindings(b) is None

    def test_a_valid_toggle_and_a_valid_tap_hold(self):
        for mode, field in (("toggle", "off_action"), ("tap_hold", "hold_action")):
            b = {"do": {"press": [{
                "action": "macro", "macro": "go", "mode": mode, "toggle_key": "var.x",
                field: {"action": "macro", "macro": "stop"},
            }]}}
            assert _validate_bindings(b) is None

    def test_the_off_action_is_checked_as_an_action(self):
        b = {"do": {"press": [{
            "action": "macro", "macro": "go", "mode": "toggle",
            "toggle_key": "var.x", "off_action": {"action": "state.set"},
        }]}}
        assert _validate_bindings(b) == _refusal(
            "do.press[0].off_action: state.set action requires 'key'"
        )

    @pytest.mark.parametrize("hold_action", [None, ["x"], 1])
    def test_tap_hold_needs_a_hold_action_object(self, hold_action):
        first = {"action": "macro", "macro": "go", "mode": "tap_hold"}
        if hold_action is not None:
            first["hold_action"] = hold_action
        assert _validate_bindings({"do": {"press": [first]}}) == _refusal(
            "do.press[0]: tap_hold mode requires 'hold_action' (an action object)"
        )

    def test_the_hold_action_is_checked_as_an_action(self):
        b = {"do": {"press": [{
            "action": "macro", "macro": "go", "mode": "tap_hold",
            "hold_action": {"action": "nope"},
        }]}}
        assert _validate_bindings(b) == _refusal(
            "do.press[0].hold_action: action type 'nope' is not valid. Use: "
            + ", ".join(sorted(DISPATCHED_ACTIONS))
        )

    def test_an_empty_hold_action_is_not_checked(self):
        b = {"do": {"press": [{
            "action": "macro", "macro": "go", "mode": "tap_hold", "hold_action": {},
        }]}}
        assert _validate_bindings(b) is None

    def test_only_the_first_press_action_carries_the_mode(self):
        b = {"do": {"press": [
            {"action": "macro", "macro": "go"},
            {"action": "macro", "macro": "go", "mode": "spin"},
        ], "change": [{"action": "macro", "macro": "go", "mode": "spin"}]}}
        assert _validate_bindings(b) is None

    def test_no_mode_check_when_the_first_press_is_not_an_object(self):
        b = {"do": {"press": ["x", {"action": "macro", "macro": "go", "mode": "spin"}]}}
        assert _validate_bindings(b) == _refusal("do.press[0]: expected an action object, got str")

    def test_the_mode_problems_follow_every_interaction_problem(self):
        b = {"do": {
            "press": [{"action": "macro", "mode": "toggle"}],
            "change": [{"action": "state.set"}],
        }}
        assert _validate_bindings(b) == _refusal(
            "do.press[0]: macro action requires 'macro'",
            "do.change[0]: state.set action requires 'key'",
            "do.press[0]: toggle mode requires 'toggle_key'",
            "do.press[0]: toggle mode requires 'off_action' (an action object)",
        )


# ===== the show half =====


def _show(**slots):
    return _validate_bindings({"show": slots})


class TestShowBindings:
    @pytest.mark.parametrize("show, kind", [([], "list"), ("x", "str")])
    def test_show_must_be_an_object(self, show, kind):
        assert _validate_bindings({"show": show}) == _refusal(f"'show' must be an object, got {kind}")

    @pytest.mark.parametrize("slot", ["value", "items", "look", "visible_when"])
    @pytest.mark.parametrize("val, kind", [("x", "str"), ([1], "list"), (3, "int")])
    def test_each_slot_must_be_an_object(self, slot, val, kind):
        assert _show(**{slot: val}) == _refusal(f"show.{slot} must be an object, got {kind}")

    def test_a_none_slot_is_absent(self):
        assert _show(value=None, items=None, look=None, visible_when=None) is None

    def test_macro_progress_needs_a_macro(self):
        assert _show(value={"source": "macro_progress"}) == _refusal(
            "show.value: macro_progress source requires 'macro'"
        )
        assert _show(value={"source": "macro_progress", "macro": "m"}) is None

    @pytest.mark.parametrize("source", [None, "state", "conditional", ""])
    def test_a_state_read_needs_a_key(self, source):
        val = {} if source is None else {"source": source}
        assert _show(value=val) == _refusal("show.value: requires 'key' (the state key to read)")

    def test_conditional_text_needs_both_texts(self):
        assert _show(value={"key": "var.x", "condition": {}}) == _refusal(
            "show.value: conditional text requires 'text_true'",
            "show.value: conditional text requires 'text_false'",
        )
        assert _show(value={"key": "var.x", "condition": {}, "text_true": ""}) == _refusal(
            "show.value: conditional text requires 'text_false'",
        )

    @pytest.mark.parametrize("key", ["device.amp.level", "", None, 5])
    def test_write_back_only_on_a_variable(self, key):
        val = {"write_back": True}
        if key is not None:
            val["key"] = key
        err = _show(value=val)
        expected = (
            "show.value: write_back (two-way) is only valid for var.* keys. "
            f"'{'' if key is None else key}' is read-only — to drive a device add a do.change "
            "device.command using $value."
        )
        assert err.endswith(expected)

    def test_write_back_on_a_variable_passes(self):
        assert _show(value={"key": "var.level", "write_back": True}) is None
        assert _show(value={"key": "device.x.y", "write_back": False}) is None

    def test_the_value_problems_are_in_order(self):
        assert _show(value={"condition": {}, "write_back": 1}) == _refusal(
            "show.value: requires 'key' (the state key to read)",
            "show.value: conditional text requires 'text_true'",
            "show.value: conditional text requires 'text_false'",
            "show.value: write_back (two-way) is only valid for var.* keys. '' is "
            "read-only — to drive a device add a do.change device.command using $value.",
        )

    @pytest.mark.parametrize("source", ["macro", "states", 0])
    def test_an_unknown_source(self, source):
        assert _show(value={"source": source, "key": "var.x"}) == _refusal(
            f"show.value: source '{source}' is not valid. Use: state, macro_progress"
        )

    def test_items_need_a_key_or_a_pattern(self):
        assert _show(items={}) == _refusal(
            "show.items: requires 'key' or 'key_pattern' (the state providing items)"
        )
        assert _show(items={"key": "", "key_pattern": ""}) == _refusal(
            "show.items: requires 'key' or 'key_pattern' (the state providing items)"
        )
        assert _show(items={"key_pattern": "device.x.*"}) is None
        assert _show(items={"key": "var.list"}) is None

    def test_look_needs_a_key(self):
        assert _show(look={}) == _refusal("show.look: missing 'key' (state key to watch)")

    def test_look_states_must_be_an_object(self):
        assert _show(look={"key": "k", "states": []}) == _refusal(
            "show.look.states must be an object mapping state values to style props"
        )

    def test_look_states_name_the_first_nested_style_only(self):
        states = {"on": {"bg_color": "#fff"}, "off": {"style": {}}, "idle": {"style": {}}, "x": "y"}
        assert _show(look={"key": "k", "states": states}) == _refusal(
            "show.look.states.off: properties (label, bg_color, etc.) must be flat, "
            "not nested inside 'style'"
        )

    def test_look_states_win_over_the_other_forms(self):
        assert _show(look={"key": "k", "states": [], "condition": 1, "map": 1, "style_map": 1}) == _refusal(
            "show.look.states must be an object mapping state values to style props"
        )
        assert _show(look={"key": "k", "states": {}, "condition": 1}) is None

    def test_look_condition_must_be_an_object(self):
        assert _show(look={"key": "k", "condition": "on", "map": 1}) == _refusal(
            "show.look.condition must be an object (e.g. {\"equals\": \"on\"})"
        )
        assert _show(look={"key": "k", "condition": {}, "map": 1}) is None

    def test_look_map_must_be_an_object(self):
        assert _show(look={"key": "k", "map": [], "style_map": 1}) == _refusal(
            "show.look.map must be an object mapping values to colors"
        )
        assert _show(look={"key": "k", "map": {}, "style_map": 1}) is None

    def test_look_style_map_must_be_an_object(self):
        assert _show(look={"key": "k", "style_map": "x"}) == _refusal(
            "show.look.style_map must be an object mapping option values to styles"
        )
        assert _show(look={"key": "k", "style_map": {}}) is None
        assert _show(look={"key": "k", "other": 1}) is None

    def test_look_key_and_form_problems_together(self):
        assert _show(look={"map": 1}) == _refusal(
            "show.look: missing 'key' (state key to watch)",
            "show.look.map must be an object mapping values to colors",
        )

    def test_a_single_visible_when_needs_a_key(self):
        assert _show(visible_when={}) == _refusal(
            "show.visible_when: missing 'key' (state key to evaluate)"
        )

    @pytest.mark.parametrize("op", ["==", "equals", "is", "not_equals"])
    def test_a_single_visible_when_refuses_other_operators(self, op):
        assert _show(visible_when={"key": "k", "operator": op}) == _refusal(
            f"show.visible_when: operator '{op}' is not valid. Use: {OPS}"
        )

    @pytest.mark.parametrize("op", [None, "", "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy"])
    def test_a_single_visible_when_takes_the_canonical_operators(self, op):
        assert _show(visible_when={"key": "k", "operator": op}) is None

    @pytest.mark.parametrize("vw", [
        {"key": "k", "operator": ["eq"]}, {"any": [{"key": "k", "operator": {"eq": 1}}]},
    ])
    def test_a_list_or_object_operator_raises_today(self, vw):
        with pytest.raises(TypeError):
            _show(visible_when=vw)

    def test_a_single_visible_when_key_and_operator_problems(self):
        assert _show(visible_when={"operator": "~"}) == _refusal(
            "show.visible_when: missing 'key' (state key to evaluate)",
            f"show.visible_when: operator '~' is not valid. Use: {OPS}",
        )

    @pytest.mark.parametrize("group", ["any", "all"])
    def test_a_group_checks_each_condition(self, group):
        conditions = [{"key": "a"}, "x", {"operator": "~"}, {"key": "b", "operator": "between"}]
        assert _show(visible_when={group: conditions}) == _refusal(
            f"show.visible_when.{group}[2]: missing 'key'",
            f"show.visible_when.{group}[2]: operator '~' is not valid. Use: {OPS}",
            f"show.visible_when.{group}[3]: operator 'between' is not valid. Use: {OPS}",
        )

    def test_any_is_read_before_all(self):
        assert _show(visible_when={"any": [{"key": "a"}], "all": [{}]}) is None
        assert _show(visible_when={"all": [{}], "any": []}) is None

    @pytest.mark.parametrize("conditions", [None, {"key": "a"}, "x", 0])
    def test_a_group_that_is_not_a_list_has_nothing_to_check(self, conditions):
        assert _show(visible_when={"any": conditions}) is None

    def test_a_group_ignores_a_key_beside_it(self):
        assert _show(visible_when={"all": [{"key": "a"}], "operator": "~"}) is None

    def test_the_slots_are_checked_in_order(self):
        assert _show(
            visible_when={}, look={}, items={}, value={"source": "macro_progress"},
        ) == _refusal(
            "show.value: macro_progress source requires 'macro'",
            "show.items: requires 'key' or 'key_pattern' (the state providing items)",
            "show.look: missing 'key' (state key to watch)",
            "show.visible_when: missing 'key' (state key to evaluate)",
        )


# ===== the whole bindings dict =====


def _project(macros=(), devices=()):
    return SimpleNamespace(
        macros=[SimpleNamespace(id=m) for m in macros],
        devices=[SimpleNamespace(id=d) for d in devices],
    )


class TestWholeBindings:
    def test_do_problems_come_before_show_problems(self):
        b = {
            "show": {"value": {}, "visible_when": {"operator": "~"}},
            "do": {"press": [{"action": "macro", "mode": "tap_hold"}], "x": []},
        }
        assert _validate_bindings(b) == _refusal(
            "do.press[0]: macro action requires 'macro'",
            f"do.x: not a valid interaction. Use: {INTERACTIONS}",
            "do.press[0]: tap_hold mode requires 'hold_action' (an action object)",
            "show.value: requires 'key' (the state key to read)",
            "show.visible_when: missing 'key' (state key to evaluate)",
            f"show.visible_when: operator '~' is not valid. Use: {OPS}",
        )

    def test_a_bad_do_and_a_bad_show_both_report(self):
        assert _validate_bindings({"do": "x", "show": 1}) == _refusal(
            "'do' must be an object, got str", "'show' must be an object, got int",
        )

    def test_an_empty_bindings_dict(self):
        assert _validate_bindings({}) is None

    def test_an_unknown_top_level_key_only_warns(self, caplog):
        with caplog.at_level(logging.WARNING, logger="openavc"):
            assert _validate_bindings({"show": {}, "extra": 1, "more": 2}) is None
        assert [r.getMessage() for r in caplog.records] == [
            "Binding reference warnings: unknown top-level binding key 'extra' "
            "(expected 'show' or 'do'); unknown top-level binding key 'more' "
            "(expected 'show' or 'do')"
        ]

    def test_references_the_project_lacks_only_warn(self, caplog):
        b = {"extra": 1, "do": {
            "press": [
                {"action": "macro", "macro": "gone"},
                {"action": "device.command", "device": "amp", "command": "on"},
                {"action": "macro", "macro": "here"},
            ],
            "release": [{"action": "device.command", "device": "lost", "command": "off"}],
        }}
        with caplog.at_level(logging.WARNING, logger="openavc"):
            assert _validate_bindings(b, _project(macros=["here"], devices=["amp"])) is None
        assert [r.getMessage() for r in caplog.records] == [
            "Binding reference warnings: unknown top-level binding key 'extra' "
            "(expected 'show' or 'do'); do.press[0]: macro 'gone' not found in project; "
            "do.release[0]: device 'lost' not found in project"
        ]

    def test_nested_references_are_not_checked(self, caplog):
        b = {"do": {"change": [{"action": "value_map", "map": {
            "a": {"action": "macro", "macro": "gone"},
        }}]}}
        with caplog.at_level(logging.WARNING, logger="openavc"):
            assert _validate_bindings(b, _project()) is None
        assert caplog.records == []

    def test_no_warning_is_logged_beside_a_refusal(self, caplog):
        b = {"extra": 1, "do": {
            "press": [{"action": "macro", "macro": "gone"}],
            "change": [{"action": "state.set"}],
        }}
        with caplog.at_level(logging.WARNING, logger="openavc"):
            assert _validate_bindings(b, _project()) == _refusal(
                "do.change[0]: state.set action requires 'key'"
            )
        assert caplog.records == []

    def test_a_refusal_never_looks_the_references_up(self):
        # A macro reference that cannot be looked up (an object) would raise
        # in the reference check; a refusal is answered before it runs.
        b = {"do": {
            "press": [{"action": "macro", "macro": {"id": "m"}}],
            "change": [{"action": "state.set"}],
        }}
        assert _validate_bindings(b, _project()) == _refusal(
            "do.change[0]: state.set action requires 'key'"
        )

    def test_a_project_without_macros_or_devices_finds_nothing(self, caplog):
        b = {"do": {"press": [
            {"action": "macro", "macro": "m"},
            {"action": "device.command", "device": "d", "command": "c"},
        ]}}
        with caplog.at_level(logging.WARNING, logger="openavc"):
            assert _validate_bindings(b, object()) is None
        assert [r.getMessage() for r in caplog.records] == [
            "Binding reference warnings: do.press[0]: macro 'm' not found in project; "
            "do.press[1]: device 'd' not found in project"
        ]

    def test_no_project_no_reference_check(self, caplog):
        b = {"do": {"press": [{"action": "macro", "macro": "m"}]}}
        with caplog.at_level(logging.WARNING, logger="openavc"):
            assert _validate_bindings(b, None) is None
        assert caplog.records == []

    def test_a_show_only_binding_has_no_reference_check(self, caplog):
        with caplog.at_level(logging.WARNING, logger="openavc"):
            assert _validate_bindings({"show": {"value": {"key": "var.x"}}}, _project()) is None
        assert caplog.records == []


# ===== normalization =====


class TestNormalizeBindings:
    def test_a_single_action_is_wrapped(self):
        action = {"action": "macro", "macro": "go"}
        b = {"do": {"press": action, "change": [action], "bogus": action, "submit": "x"}}
        out = _normalize_bindings(b)
        assert out is b
        assert b["do"] == {"press": [action], "change": [action], "bogus": action, "submit": "x"}
        assert b["do"]["press"][0] is action

    @pytest.mark.parametrize("interaction", [
        "press", "release", "hold", "change", "submit", "select",
        "route", "audio_route", "mute_route", "audio_mute_route",
    ])
    def test_every_interaction_is_wrapped(self, interaction):
        b = {"do": {interaction: {"action": "macro"}}}
        assert _normalize_bindings(b)["do"][interaction] == [{"action": "macro"}]

    @pytest.mark.parametrize("b", [{}, {"do": None}, {"do": [{"action": "macro"}]}, {"show": {"value": {}}}])
    def test_nothing_else_changes(self, b):
        before = repr(b)
        assert _normalize_bindings(b) is b
        assert repr(b) == before


# ===== variables =====


class TestValidateVariable:
    @pytest.mark.parametrize("var_type", ["integer", "", "bool", None])
    def test_an_unknown_type(self, var_type):
        assert _validate_variable(var_type) == (
            f"Variable type '{var_type}' is not valid. Use: string, number, boolean"
        )

    @pytest.mark.parametrize("var_type, default, kind", [
        ("number", "5", "str"), ("number", [1], "list"),
        ("boolean", 1, "int"), ("boolean", "true", "str"),
        ("string", 5, "int"), ("string", False, "bool"),
    ])
    def test_a_default_of_the_wrong_kind(self, var_type, default, kind):
        assert _validate_variable(var_type, default) == (
            f"Variable type is '{var_type}' but default '{default}' is {kind}"
        )

    @pytest.mark.parametrize("var_type, default", [
        ("number", 5), ("number", 2.5), ("number", True),
        ("boolean", False), ("string", ""), ("string", None),
        ("number", None), ("boolean", None),
    ])
    def test_a_default_that_fits(self, var_type, default):
        # A boolean default passes as a number: bool is an int in Python.
        assert _validate_variable(var_type, default) is None

    def test_the_type_is_checked_first(self):
        assert _validate_variable("integer", "x").startswith("Variable type 'integer' is not valid")


# ===== what a tool that raised is answered with =====


class TestDescribeToolFailure:
    def test_a_wake_address_refusal_is_worded(self):
        from openavc.api.error_messages import friendly_error
        from openavc.drivers.base import WakeAddressError

        exc = WakeAddressError(field_label="MAC Address", value="zz")
        assert describe_tool_failure(exc) == friendly_error(exc)

    def test_a_device_manager_refusal_keeps_its_text(self):
        exc = ConnectionError("Device 'amp' is restarting")
        exc.device_id = "amp"
        assert describe_tool_failure(exc) == "Device 'amp' is restarting"

    def test_a_connection_error_from_elsewhere_is_not_repeated(self):
        assert describe_tool_failure(ConnectionError("10.0.0.5:23 reset")) == TOOL_FAILED

    def test_a_permission_error_is_worded(self):
        assert describe_tool_failure(PermissionError(13, "denied", "/secret")) == (
            "Permission denied. Check credentials, file permissions, or firewall rules."
        )

    @pytest.mark.parametrize("code", [errno.ENOSPC, errno.EROFS, errno.EHOSTUNREACH])
    def test_a_worded_errno(self, code):
        from openavc.api.error_messages import friendly_error

        exc = OSError(code, "x")
        assert describe_tool_failure(exc) == friendly_error(exc)
        assert "/" not in describe_tool_failure(OSError(code, "x", "/secret/path"))

    def test_another_errno_is_not_repeated(self):
        assert describe_tool_failure(OSError(errno.ENOENT, "gone", "/secret/path")) == TOOL_FAILED

    def test_a_typed_refusal_with_no_text(self):
        from openavc.drivers.base import UnknownCommandError

        assert describe_tool_failure(UnknownCommandError("  ")) == TOOL_FAILED
        assert describe_tool_failure(UnknownCommandError("No such command 'x'")) == "No such command 'x'"


# ===== which tools exist, and how each runs =====


ALL_TOOLS = {
    "get_project_summary", "get_project_state", "get_state_value", "get_state_history",
    "list_devices", "get_device_info", "list_drivers", "search_community_drivers",
    "get_community_driver_detail", "find_driver_for_device", "get_installed_drivers",
    "get_driver_definition", "get_script_source", "get_script_health", "get_logs",
    "list_triggers", "get_macro", "get_ui_page",
    "update_project_metadata", "update_device", "delete_device", "add_device",
    "add_device_group", "update_device_group", "delete_device_group",
    "add_variable", "update_variable", "delete_variable",
    "add_macro", "update_macro", "delete_macro",
    "add_ui_page", "update_ui_page", "delete_ui_page",
    "add_ui_elements", "update_ui_element", "delete_ui_elements",
    "add_master_element", "update_master_element", "delete_master_element",
    "install_community_driver", "create_driver_definition", "update_driver_definition",
    "create_script", "update_script_source", "delete_script",
    "list_plugins", "browse_community_plugins", "install_plugin", "uninstall_plugin",
    "enable_plugin", "disable_plugin", "get_plugin_config", "update_plugin_config",
    "start_discovery_scan", "get_discovery_results",
    "list_themes", "get_theme", "apply_theme",
    "list_assets", "delete_asset",
    "get_isc_status", "list_isc_peers", "send_isc_command",
    "get_device_settings", "set_device_setting",
    "list_ui_files", "read_ui_file", "write_ui_file", "delete_ui_file",
    "get_project_stylesheet", "set_project_stylesheet", "review_custom_ui",
    "simulate_ui_action", "review_ui", "check_references", "wait",
    "send_device_command", "test_device_connection", "test_driver_command",
    "execute_macro", "cancel_macro", "set_state_value", "test_trigger",
}

READ_ONLY = {
    "get_project_summary", "get_project_state", "get_state_value",
    "get_state_history", "list_devices", "get_device_info",
    "list_drivers", "search_community_drivers", "get_community_driver_detail",
    "find_driver_for_device", "get_installed_drivers",
    "get_driver_definition", "get_script_source", "get_script_health",
    "get_logs", "list_triggers", "get_macro", "get_ui_page",
    "list_plugins", "browse_community_plugins", "get_plugin_config",
    "list_themes", "get_theme", "list_assets",
    "get_isc_status", "list_isc_peers",
    "get_device_settings", "check_references",
    "get_discovery_results", "wait",
    "list_ui_files", "read_ui_file", "get_project_stylesheet",
}


def _handler(project_path=None):
    agent = MagicMock()
    agent.send_message = AsyncMock()
    return AIToolHandler(agent, MagicMock(), MagicMock(), project_path=project_path)


class TestToolTable:
    def test_every_tool(self):
        assert set(_handler()._tools) == ALL_TOOLS
        assert len(ALL_TOOLS) == 84

    def test_each_tool_is_the_method_of_its_name(self):
        handler = _handler()
        for name, method in handler._tools.items():
            assert method.__self__ is handler
            assert method.__func__ is getattr(AIToolHandler, f"_{name}"), name

    def test_the_read_only_tools(self):
        assert AIToolHandler._READ_ONLY_TOOLS == READ_ONLY

    def test_the_tools_that_run_without_the_lock(self):
        assert AIToolHandler._CONCURRENT_SAFE_TOOLS == READ_ONLY | {"execute_macro"}
        assert _handler()._CONCURRENT_SAFE_TOOLS == READ_ONLY | {"execute_macro"}

    def test_each_handler_has_its_own_table(self):
        a, b = _handler(), _handler()
        a._tools["wait"] = None
        assert b._tools["wait"] is not None

    @pytest.mark.asyncio
    async def test_a_read_tool_skips_the_lock_and_the_backup(self, tmp_path):
        handler = _handler(project_path=tmp_path / "project.avc")
        tool = AsyncMock(return_value={"ok": 1})
        with patch("openavc.core.backup_manager.create_backup") as backup:
            async with handler._project_lock:
                await asyncio.wait_for(handler._execute_tool("r", "get_logs", tool, {}), 1)
        backup.assert_not_called()
        tool.assert_awaited_once_with({})

    @pytest.mark.asyncio
    async def test_execute_macro_skips_the_lock(self, tmp_path):
        handler = _handler(project_path=tmp_path / "project.avc")
        tool = AsyncMock(return_value={"ok": 1})
        with patch("openavc.core.backup_manager.create_backup") as backup:
            async with handler._project_lock:
                await asyncio.wait_for(handler._execute_tool("r", "execute_macro", tool, {}), 1)
        backup.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["review_ui", "simulate_ui_action", "test_device_connection", "not_listed"])
    async def test_every_other_tool_takes_the_lock_and_the_backup(self, tmp_path, name):
        handler = _handler(project_path=tmp_path / "project.avc")
        seen = []

        async def tool(_input):
            seen.append(handler._project_lock.locked())
            return {}

        with patch("openavc.core.backup_manager.create_backup") as backup:
            await handler._execute_tool("r", name, tool, {})
        assert seen == [True]
        backup.assert_called_once()


# ===== the one backup before the AI's first change =====


class TestPreAiBackup:
    @pytest.mark.asyncio
    async def test_a_long_pause_starts_a_new_conversation(self, tmp_path):
        handler = _handler(project_path=tmp_path / "project.avc")
        handler._ai_backup_created = True
        handler._ai_last_write_time = time.monotonic() - 301
        with patch("openavc.core.backup_manager.create_backup") as backup:
            await handler._maybe_create_pre_ai_backup()
        backup.assert_called_once()
        assert handler._ai_backup_created is True

    @pytest.mark.asyncio
    async def test_a_short_pause_is_the_same_conversation(self, tmp_path):
        handler = _handler(project_path=tmp_path / "project.avc")
        handler._ai_backup_created = True
        handler._ai_last_write_time = time.monotonic() - 299
        with patch("openavc.core.backup_manager.create_backup") as backup:
            await handler._maybe_create_pre_ai_backup()
        backup.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_project_path_no_backup(self):
        handler = _handler()
        with patch("openavc.core.backup_manager.create_backup") as backup:
            await handler._maybe_create_pre_ai_backup()
        backup.assert_not_called()
        assert handler._ai_backup_created is False
        assert handler._ai_last_write_time > 0


# ===== the helpers the tool mixins reach =====


class TestHelpers:
    def test_no_engine_when_the_rest_module_cannot_load(self):
        with patch.dict(sys.modules, {"openavc.api.rest": None}):
            assert _handler()._get_engine() is None

    def test_the_driver_folders(self):
        from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR

        handler = _handler()
        assert handler._get_driver_repo_dir() == DRIVER_REPO_DIR
        assert handler._get_driver_dirs() == [DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR]
