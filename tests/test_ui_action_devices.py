"""Which devices a control's ``do`` actions can reach.

The panel marks a control that will be refused on press, and this is where the
answer comes from. The cases that matter are the indirect ones: a button runs a
macro, which runs another macro, which commands a group. None of that is
visible from the page the renderer is handed, which is why it is resolved on
the server at all.
"""

from __future__ import annotations

from openavc.ui.action_devices import (
    ACTION_DEVICES_KEY,
    annotate_action_devices,
    element_action_devices,
)


def _el(do, **extra):
    return {"id": "btn", "type": "button", "bindings": {"do": do}, **extra}


def _devices(do, macros=None, groups=None):
    return element_action_devices(_el(do), macros or {}, groups or {})


# --- the direct cases ------------------------------------------------------


def test_a_direct_device_command():
    assert _devices({"press": [{"action": "device.command", "device": "amp"}]}) == ["amp"]


def test_an_element_with_no_do_bindings_reaches_nothing():
    assert element_action_devices({"id": "lbl", "type": "label"}, {}, {}) == []


def test_actions_that_touch_no_device_reach_nothing():
    assert _devices({"press": [
        {"action": "ui.navigate", "page": "main"},
        {"action": "state.set", "key": "var.x", "value": 1},
        {"action": "event.emit", "event": "custom.thing"},
    ]}) == []


def test_a_single_action_object_is_walked_like_a_list():
    # Legal in the wild: page_references reports the shape but the engine runs
    # it, so this has to see it too.
    assert _devices({"press": {"action": "device.command", "device": "amp"}}) == ["amp"]


def test_every_do_slot_counts_not_just_press():
    assert _devices({
        "press": [{"action": "device.command", "device": "amp"}],
        "change": [{"action": "device.command", "device": "switcher"}],
    }) == ["amp", "switcher"]


# --- groups ----------------------------------------------------------------


def test_a_group_command_reaches_every_member():
    groups = {"displays": ["front", "rear"]}
    assert _devices(
        {"press": [{"action": "group.command", "group": "displays"}]}, groups=groups
    ) == ["front", "rear"]


def test_a_group_that_does_not_exist_reaches_nothing():
    assert _devices({"press": [{"action": "group.command", "group": "ghost"}]}) == []


# --- macros, which is the whole reason this is server-side -----------------


def test_a_macro_step_is_followed():
    macros = {"on": {"id": "on", "steps": [
        {"action": "device.command", "device": "projector"},
    ]}}
    assert _devices({"press": [{"action": "macro", "macro": "on"}]}, macros) == ["projector"]


def test_a_macro_that_calls_a_macro_is_followed():
    macros = {
        "outer": {"id": "outer", "steps": [{"action": "macro", "macro": "inner"}]},
        "inner": {"id": "inner", "steps": [
            {"action": "device.command", "device": "projector"},
        ]},
    }
    assert _devices({"press": [{"action": "macro", "macro": "outer"}]}, macros) == [
        "projector"
    ]


def test_a_macro_commanding_a_group_is_followed_through_both():
    macros = {"on": {"id": "on", "steps": [
        {"action": "group.command", "group": "displays"},
    ]}}
    groups = {"displays": ["front", "rear"]}
    assert _devices(
        {"press": [{"action": "macro", "macro": "on"}]}, macros, groups
    ) == ["front", "rear"]


def test_conditional_branches_are_followed():
    macros = {"on": {"id": "on", "steps": [{
        "action": "conditional",
        "then_steps": [{"action": "device.command", "device": "a"}],
        "else_steps": [{"action": "device.command", "device": "b"}],
    }]}}
    assert _devices({"press": [{"action": "macro", "macro": "on"}]}, macros) == ["a", "b"]


def test_a_macro_that_calls_itself_terminates():
    macros = {"loop": {"id": "loop", "steps": [
        {"action": "macro", "macro": "loop"},
        {"action": "device.command", "device": "amp"},
    ]}}
    assert _devices({"press": [{"action": "macro", "macro": "loop"}]}, macros) == ["amp"]


def test_a_ring_of_macros_terminates():
    macros = {
        "a": {"id": "a", "steps": [{"action": "macro", "macro": "b"}]},
        "b": {"id": "b", "steps": [
            {"action": "macro", "macro": "a"},
            {"action": "device.command", "device": "amp"},
        ]},
    }
    assert _devices({"press": [{"action": "macro", "macro": "a"}]}, macros) == ["amp"]


def test_a_macro_that_does_not_exist_reaches_nothing():
    assert _devices({"press": [{"action": "macro", "macro": "ghost"}]}) == []


# --- value_map, the select's per-option actions ----------------------------


def test_value_map_entries_are_walked():
    assert _devices({"change": [{"action": "value_map", "map": {
        "hdmi": {"action": "device.command", "device": "switcher"},
        "usb": {"action": "macro", "macro": "usb"},
    }}]}, {"usb": {"id": "usb", "steps": [
        {"action": "device.command", "device": "dock"},
    ]}}) == ["dock", "switcher"]


# --- what is deliberately not resolved -------------------------------------


def test_a_script_call_reaches_nothing():
    # A script can command anything it computes a name for. A guess here would
    # mark a control over a device the script may never touch.
    assert _devices({"press": [{"action": "script.call", "script": "whatever"}]}) == []


# --- the whole-definition pass ---------------------------------------------


def test_annotate_stamps_pages_and_master_elements():
    ui = {
        "pages": [{"id": "main", "elements": [
            _el({"press": [{"action": "device.command", "device": "amp"}]}),
            {"id": "lbl", "type": "label"},
        ]}],
        "master_elements": [
            dict(_el({"press": [{"action": "macro", "macro": "on"}]}), id="m1"),
        ],
    }
    macros = [{"id": "on", "steps": [{"action": "device.command", "device": "proj"}]}]
    out = annotate_action_devices(ui, macros, [])

    assert out["pages"][0]["elements"][0][ACTION_DEVICES_KEY] == ["amp"]
    assert out["master_elements"][0][ACTION_DEVICES_KEY] == ["proj"]
    # An element that reaches nothing carries no key at all: absent and empty
    # mean the same thing to the renderer, and most elements reach nothing.
    assert ACTION_DEVICES_KEY not in out["pages"][0]["elements"][1]


def test_the_result_is_sorted_so_an_unchanged_page_looks_unchanged():
    ui = {"pages": [{"id": "main", "elements": [_el({"press": [
        {"action": "device.command", "device": "zebra"},
        {"action": "device.command", "device": "alpha"},
    ]})]}]}
    out = annotate_action_devices(ui, [], [])
    assert out["pages"][0]["elements"][0][ACTION_DEVICES_KEY] == ["alpha", "zebra"]
