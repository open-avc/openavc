"""Tests for the 0.6.0 -> 0.7.0 binding-model migration.

Verifies that the ad-hoc per-control binding slots are rewritten into the
unified ``show`` / ``do`` shape, that two-way collapses to
``show.value.write_back`` only for writable ``var.*`` keys, and that the
orphan ``meter`` slot is dropped.
"""

from server.core.project_migration import (
    CURRENT_VERSION,
    migrate_0_6_to_0_7,
    migrate_project,
    _migrate_bindings_0_6_to_0_7,
)


def _wrap(bindings: dict) -> dict:
    """A minimal v0.6.0 project carrying one element with the given bindings."""
    return {
        "openavc_version": "0.6.0",
        "project": {"name": "t"},
        "ui": {"pages": [{"id": "p1", "elements": [
            {"id": "el1", "type": "button", "bindings": bindings},
        ]}]},
    }


def _el_bindings(project: dict) -> dict:
    return project["ui"]["pages"][0]["elements"][0]["bindings"]


# --- show.value flavors -------------------------------------------------

def test_value_moves_to_show_value():
    out = _migrate_bindings_0_6_to_0_7({"value": {"source": "state", "key": "device.amp.level"}})
    assert out == {"show": {"value": {"source": "state", "key": "device.amp.level"}}}


def test_variable_var_key_is_two_way():
    out = _migrate_bindings_0_6_to_0_7({"variable": {"key": "var.volume"}})
    assert out["show"]["value"] == {"source": "state", "key": "var.volume", "write_back": True}


def test_variable_device_key_degrades_to_read_only():
    # The v0.6.0 device two-way never reached the device; it becomes read-only.
    out = _migrate_bindings_0_6_to_0_7({"variable": {"key": "device.amp.level"}})
    assert out["show"]["value"] == {"source": "state", "key": "device.amp.level"}
    assert "write_back" not in out["show"]["value"]


def test_variable_overrides_value_precedence():
    # Panel read order was `variable || value`; variable wins as the read source.
    out = _migrate_bindings_0_6_to_0_7({
        "value": {"source": "state", "key": "device.amp.level"},
        "variable": {"key": "var.volume"},
    })
    assert out["show"]["value"]["key"] == "var.volume"


def test_text_moves_to_show_value():
    text = {"source": "state", "key": "device.x.status", "format": "{value}"}
    out = _migrate_bindings_0_6_to_0_7({"text": text})
    assert out["show"]["value"] == text


def test_selected_becomes_two_way_show_value():
    out = _migrate_bindings_0_6_to_0_7({"selected": {"key": "var.zone"}})
    assert out["show"]["value"] == {"source": "state", "key": "var.zone", "write_back": True}


# --- show.items / show.look / visible_when ------------------------------

def test_items_moves_to_show_items():
    items = {"source": "state", "key_pattern": "device.m.input_*_name"}
    out = _migrate_bindings_0_6_to_0_7({"items": items})
    assert out["show"]["items"] == items


def test_feedback_moves_to_show_look():
    fb = {"source": "state", "key": "device.x.power", "condition": {"equals": True},
          "style_active": {"bg_color": "#0f0"}, "style_inactive": {"bg_color": "#333"}}
    out = _migrate_bindings_0_6_to_0_7({"feedback": fb})
    assert out["show"]["look"] == fb


def test_color_moves_to_show_look():
    color = {"source": "state", "key": "device.x.power", "map": {"on": "#0f0"}, "default": "#333"}
    out = _migrate_bindings_0_6_to_0_7({"color": color})
    assert out["show"]["look"] == color


def test_visible_when_moves_under_show():
    vw = {"key": "var.mode", "operator": "eq", "value": "av"}
    out = _migrate_bindings_0_6_to_0_7({"visible_when": vw})
    assert out["show"]["visible_when"] == vw


# --- do.<interaction> ---------------------------------------------------

def test_press_list_moves_to_do():
    actions = [{"action": "macro", "macro": "m1"}, {"action": "navigate", "page": "p2"}]
    out = _migrate_bindings_0_6_to_0_7({"press": actions})
    assert out["do"]["press"] == actions


def test_single_action_object_normalized_to_list():
    out = _migrate_bindings_0_6_to_0_7({"change": {"action": "value_map", "map": {"a": {"action": "macro", "macro": "m"}}}})
    assert out["do"]["change"] == [{"action": "value_map", "map": {"a": {"action": "macro", "macro": "m"}}}]


def test_all_matrix_route_slots_migrate():
    raw = {s: [{"action": "device.command", "device": "m", "command": "route", "params": {}}]
           for s in ("route", "audio_route", "mute_route", "audio_mute_route")}
    out = _migrate_bindings_0_6_to_0_7(raw)
    assert set(out["do"]) == {"route", "audio_route", "mute_route", "audio_mute_route"}


def test_meter_orphan_dropped():
    out = _migrate_bindings_0_6_to_0_7({"meter": {"key": "device.x.level"}})
    assert out == {}


def test_empty_action_slot_dropped():
    out = _migrate_bindings_0_6_to_0_7({"press": []})
    assert "do" not in out


# --- whole-project / chain ----------------------------------------------

def test_master_elements_are_migrated():
    data = {
        "openavc_version": "0.6.0", "project": {"name": "t"},
        "ui": {"pages": [], "master_elements": [
            {"id": "m1", "type": "button", "bindings": {"press": [{"action": "macro", "macro": "x"}]}},
        ]},
    }
    out = migrate_0_6_to_0_7(data)
    assert out["ui"]["master_elements"][0]["bindings"] == {"do": {"press": [{"action": "macro", "macro": "x"}]}}


def test_version_bumped():
    out = migrate_0_6_to_0_7(_wrap({"value": {"source": "state", "key": "var.x"}}))
    assert out["openavc_version"] == "0.7.0"


def test_full_chain_from_0_6_reaches_current():
    data = _wrap({"variable": {"key": "var.volume"}, "change": [{"action": "macro", "macro": "m"}]})
    out, migrated = migrate_project(data)
    assert migrated is True
    assert out["openavc_version"] == CURRENT_VERSION == "0.7.0"
    b = _el_bindings(out)
    assert b["show"]["value"]["write_back"] is True
    assert b["do"]["change"] == [{"action": "macro", "macro": "m"}]


def test_combined_element_round_trip():
    # A select-like element: value + per-option change + feedback + visibility.
    out = _migrate_bindings_0_6_to_0_7({
        "variable": {"key": "var.input"},
        "change": {"action": "value_map", "map": {"hdmi": {"action": "device.command", "device": "m", "command": "route"}}},
        "feedback": {"source": "state", "key": "var.input", "style_map": {"hdmi": {"bg_color": "#0f0"}}},
        "visible_when": {"key": "var.mode", "operator": "eq", "value": "av"},
    })
    assert out["show"]["value"] == {"source": "state", "key": "var.input", "write_back": True}
    assert out["show"]["look"]["style_map"]["hdmi"]["bg_color"] == "#0f0"
    assert out["show"]["visible_when"]["value"] == "av"
    assert out["do"]["change"][0]["action"] == "value_map"
