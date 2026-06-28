"""Regression coverage for the 0.6.0 -> 0.7.0 UI binding migration.

`migrate_0_6_to_0_7` rewrites every element's ad-hoc binding slots into the
unified show/do model. This is the most structurally complex migration in
`project_migration.py`, and an existing user's saved panel runs through it on
load, so it gets real binding-shape fixtures here (not just a version-bump
smoke test) to lock the behavior in.
"""

from server.core.project_migration import migrate_project


def _migrate(elements, master_elements=None):
    """Run a 0.6.0 project with these UI elements through migrate_project."""
    project = {
        "openavc_version": "0.6.0",
        "ui": {
            "pages": [{"id": "p1", "elements": elements}],
            "master_elements": master_elements or [],
        },
    }
    data, changed = migrate_project(project)
    assert changed is True
    assert data["openavc_version"] == "0.7.0"
    return data["ui"]


def _el(ui, idx=0):
    return ui["pages"][0]["elements"][idx]


def test_value_slot_becomes_read_only_show_value():
    ui = _migrate([{"id": "g", "bindings": {"value": {"key": "var.temp"}}}])
    assert _el(ui)["bindings"] == {"show": {"value": {"key": "var.temp"}}}


def test_variable_var_key_gets_write_back():
    ui = _migrate([{"id": "s", "bindings": {"variable": {"key": "var.vol"}}}])
    assert _el(ui)["bindings"]["show"]["value"] == {
        "source": "state", "key": "var.vol", "write_back": True
    }


def test_variable_device_key_degrades_to_read_only():
    # A v0.6.0 two-way bound to a device.* key never reached the device, so it
    # becomes a read-only display (no write_back).
    ui = _migrate([{"id": "s", "bindings": {"variable": {"key": "device.disp.power"}}}])
    val = _el(ui)["bindings"]["show"]["value"]
    assert val == {"source": "state", "key": "device.disp.power"}
    assert "write_back" not in val


def test_variable_overrides_value_like_old_runtime():
    # The old panel read `variable || value`; variable must win.
    ui = _migrate([{
        "id": "s",
        "bindings": {"value": {"key": "var.ignored"}, "variable": {"key": "var.vol"}},
    }])
    assert _el(ui)["bindings"]["show"]["value"] == {
        "source": "state", "key": "var.vol", "write_back": True
    }


def test_selected_becomes_two_way_show_value():
    ui = _migrate([{"id": "lst", "bindings": {"selected": {"key": "var.sel"}}}])
    assert _el(ui)["bindings"]["show"]["value"] == {
        "source": "state", "key": "var.sel", "write_back": True
    }


def test_text_items_feedback_color_visible_when():
    ui = _migrate([
        {"id": "lbl", "bindings": {"text": {"key": "var.msg"}}},
        {"id": "lst", "bindings": {"items": {"source": "state", "key": "var.list"}}},
        {"id": "btn", "bindings": {"feedback": {"key": "device.d.power", "map": {}}}},
        {"id": "led", "bindings": {"color": {"key": "var.alarm"}}},
        {"id": "any", "bindings": {"visible_when": {"key": "var.show", "op": "eq", "value": 1}}},
    ])
    assert _el(ui, 0)["bindings"]["show"]["value"] == {"key": "var.msg"}
    assert _el(ui, 1)["bindings"]["show"]["items"] == {"source": "state", "key": "var.list"}
    assert _el(ui, 2)["bindings"]["show"]["look"] == {"key": "device.d.power", "map": {}}
    assert _el(ui, 3)["bindings"]["show"]["look"] == {"key": "var.alarm"}
    assert _el(ui, 4)["bindings"]["show"]["visible_when"] == {
        "key": "var.show", "op": "eq", "value": 1
    }


def test_action_slot_single_dict_normalized_to_list():
    ui = _migrate([{
        "id": "btn",
        "bindings": {"press": {"action": "device.command", "device": "d", "command": "power_on"}},
    }])
    assert _el(ui)["bindings"]["do"]["press"] == [
        {"action": "device.command", "device": "d", "command": "power_on"}
    ]


def test_action_slot_list_preserved():
    actions = [{"action": "macro", "macro": "m1"}, {"action": "macro", "macro": "m2"}]
    ui = _migrate([{"id": "btn", "bindings": {"press": list(actions)}}])
    assert _el(ui)["bindings"]["do"]["press"] == actions


def test_matrix_route_slots_and_preset_relocation():
    ui = _migrate([{
        "id": "mtx",
        "bindings": {
            "route": {"action": "device.command", "device": "sw", "command": "route"},
            "audio_route": {"action": "device.command", "device": "sw", "command": "aroute"},
            "presets": [{"name": "Default", "macro": "m_default"}],
        },
    }])
    el = _el(ui)
    assert el["bindings"]["do"]["route"] == [
        {"action": "device.command", "device": "sw", "command": "route"}
    ]
    assert el["bindings"]["do"]["audio_route"] == [
        {"action": "device.command", "device": "sw", "command": "aroute"}
    ]
    # presets are matrix config, relocated out of bindings.
    assert "presets" not in el["bindings"]
    assert el["matrix_config"]["presets"] == [{"name": "Default", "macro": "m_default"}]


def test_orphan_meter_slot_is_dropped():
    ui = _migrate([{"id": "x", "bindings": {"meter": {"anything": 1}, "value": {"key": "var.v"}}}])
    bindings = _el(ui)["bindings"]
    assert "meter" not in bindings
    assert "meter" not in bindings.get("show", {})
    assert "meter" not in bindings.get("do", {})
    assert bindings["show"]["value"] == {"key": "var.v"}


def test_master_elements_are_migrated_too():
    ui = _migrate(
        [],
        master_elements=[{"id": "mbtn", "bindings": {"press": {"action": "macro", "macro": "m"}}}],
    )
    assert ui["master_elements"][0]["bindings"]["do"]["press"] == [
        {"action": "macro", "macro": "m"}
    ]


def test_already_migrated_project_is_not_remangled():
    # A 0.7.0 project must pass through untouched (the version gate protects the
    # non-idempotent transform from wiping already-migrated bindings).
    showdo = {"show": {"value": {"source": "state", "key": "var.v", "write_back": True}},
              "do": {"press": [{"action": "macro", "macro": "m"}]}}
    project = {
        "openavc_version": "0.7.0",
        "ui": {"pages": [{"id": "p1", "elements": [{"id": "e", "bindings": showdo}]}]},
    }
    data, changed = migrate_project(project)
    assert changed is False
    assert data["ui"]["pages"][0]["elements"][0]["bindings"] == showdo
