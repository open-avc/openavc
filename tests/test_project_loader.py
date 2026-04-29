"""Tests for project loader."""

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.core.project_loader import ProjectConfig, load_project, save_project


# --- Test fixture project (never use the live project.avc) ---

TEST_PROJECT = {
    "project": {"id": "test_room", "name": "Test Room"},
    "devices": [
        {
            "id": "projector1",
            "driver": "pjlink_class1",
            "name": "Test Projector",
            "config": {"host": "127.0.0.1", "port": 4352},
            "enabled": True,
        },
        {
            "id": "display1",
            "driver": "samsung_mdc",
            "name": "Test Display",
            "config": {"host": "192.168.1.10", "port": 1515},
            "enabled": True,
        },
    ],
    "variables": [
        {"id": "room_active", "type": "bool", "default": False},
        {"id": "volume", "type": "int", "default": 50},
    ],
    "macros": [
        {
            "id": "system_on",
            "name": "System On",
            "steps": [
                {"action": "state.set", "key": "var.room_active", "value": True},
                {"action": "device.command", "device": "projector1", "command": "power_on"},
            ],
        },
        {
            "id": "system_off",
            "name": "System Off",
            "steps": [
                {"action": "device.command", "device": "projector1", "command": "power_off"},
                {"action": "state.set", "key": "var.room_active", "value": False},
            ],
        },
    ],
    "ui": {
        "pages": [
            {
                "id": "main",
                "name": "Main",
                "elements": [
                    {"id": "btn_power", "type": "button", "label": "Power On"},
                    {"id": "led_status", "type": "status_led"},
                    {"id": "lbl_title", "type": "label", "text": "Test Room"},
                ],
            },
        ],
    },
}


@pytest.fixture
def test_project_path():
    """Write the test project to a temp file and return the path."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(TEST_PROJECT, f)
        tmp_path = f.name
    yield tmp_path
    Path(tmp_path).unlink(missing_ok=True)


def test_load_project(test_project_path):
    project = load_project(test_project_path)
    assert project.project.name == "Test Room"
    assert len(project.devices) == 2
    assert len(project.macros) == 2
    assert len(project.ui.pages) == 1
    assert len(project.variables) == 2


def test_device_config(test_project_path):
    project = load_project(test_project_path)
    proj = project.devices[0]
    assert proj.id == "projector1"
    assert proj.driver == "pjlink_class1"
    # Connection fields are migrated to the connections table
    assert project.connections["projector1"]["host"] == "127.0.0.1"
    assert project.connections["projector1"]["port"] == 4352


def test_macros(test_project_path):
    project = load_project(test_project_path)
    macro_ids = [m.id for m in project.macros]
    assert "system_on" in macro_ids
    assert "system_off" in macro_ids

    system_on = next(m for m in project.macros if m.id == "system_on")
    assert len(system_on.steps) == 2
    assert system_on.steps[0].action == "state.set"
    assert system_on.steps[1].action == "device.command"


def test_ui_elements(test_project_path):
    project = load_project(test_project_path)
    page = project.ui.pages[0]
    assert page.id == "main"
    element_ids = [e.id for e in page.elements]
    assert "btn_power" in element_ids
    assert "led_status" in element_ids
    assert "lbl_title" in element_ids


def test_round_trip(test_project_path):
    """Load a project, save it, load it again — should be equivalent."""
    original = load_project(test_project_path)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        tmp_path = f.name

    save_project(tmp_path, original)
    reloaded = load_project(tmp_path)

    assert reloaded.project.name == original.project.name
    assert len(reloaded.devices) == len(original.devices)
    assert len(reloaded.macros) == len(original.macros)

    Path(tmp_path).unlink()


def test_minimal_project():
    """A project with just a name and no devices should load fine."""
    minimal = {
        "project": {"id": "test", "name": "Minimal"},
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(minimal, f)
        tmp_path = f.name

    project = load_project(tmp_path)
    assert project.project.name == "Minimal"
    assert len(project.devices) == 0
    assert len(project.macros) == 0

    Path(tmp_path).unlink()


def test_invalid_project_raises():
    """Missing required 'project' field should raise ValidationError."""
    bad = {"devices": []}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(bad, f)
        tmp_path = f.name

    with pytest.raises(ValidationError):
        load_project(tmp_path)

    Path(tmp_path).unlink()


# --- Forward-compat: unknown fields survive load/save round-trip ---


def test_unknown_top_level_field_survives_round_trip():
    """A future top-level section (e.g., new in v0.5.0) round-trips through v0.4.0."""
    data = {
        "openavc_version": "0.4.0",
        "project": {"id": "fc1", "name": "FC Test"},
        "lighting_scenes": [
            {"id": "scene1", "name": "House lights up"},
        ],
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(data, f)
        tmp_path = f.name

    project = load_project(tmp_path)
    save_project(tmp_path, project)
    saved = json.loads(Path(tmp_path).read_text())

    assert "lighting_scenes" in saved
    assert saved["lighting_scenes"] == [{"id": "scene1", "name": "House lights up"}]

    Path(tmp_path).unlink()


def test_unknown_nested_field_survives_round_trip():
    """A future field on a nested model (e.g., DeviceConfig) round-trips through v0.4.0."""
    data = {
        "openavc_version": "0.4.0",
        "project": {"id": "fc2", "name": "FC Nested"},
        "devices": [
            {
                "id": "dev1",
                "name": "Future Projector",
                "driver": "pjlink_class1",
                "config": {},
                "max_concurrent_calls": 4,  # hypothetical v0.5.0 field
            },
        ],
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(data, f)
        tmp_path = f.name

    project = load_project(tmp_path)
    save_project(tmp_path, project)
    saved = json.loads(Path(tmp_path).read_text())

    saved_devices = saved.get("devices", [])
    assert len(saved_devices) == 1
    assert saved_devices[0].get("max_concurrent_calls") == 4

    Path(tmp_path).unlink()


# --- UI Overhaul model tests ---


def _load_project_dict(data: dict) -> ProjectConfig:
    """Helper to load a project from a dict."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(data, f)
        tmp_path = f.name
    project = load_project(tmp_path)
    Path(tmp_path).unlink()
    return project


def test_overlay_page():
    """Project with overlay page type loads correctly."""
    data = {
        "project": {"id": "test", "name": "Test"},
        "ui": {
            "pages": [{
                "id": "confirm",
                "name": "Confirm",
                "page_type": "overlay",
                "overlay": {
                    "width": 400,
                    "height": 300,
                    "position": "center",
                    "backdrop": "dim",
                    "dismiss_on_backdrop": True,
                    "animation": "fade",
                },
                "grid": {"columns": 4, "rows": 4},
                "elements": [],
            }],
        },
    }
    project = _load_project_dict(data)
    page = project.ui.pages[0]
    assert page.page_type == "overlay"
    assert page.overlay is not None
    assert page.overlay.width == 400
    assert page.overlay.backdrop == "dim"


def test_page_background():
    """Page with background settings loads correctly."""
    data = {
        "project": {"id": "test", "name": "Test"},
        "ui": {
            "pages": [{
                "id": "main",
                "name": "Main",
                "background": {
                    "color": "#1a1a2e",
                    "image": "assets://bg.jpg",
                    "image_opacity": 0.5,
                    "gradient": {"type": "linear", "angle": 180, "from": "#000", "to": "#333"},
                },
                "grid": {"columns": 12, "rows": 8},
                "elements": [],
            }],
        },
    }
    project = _load_project_dict(data)
    bg = project.ui.pages[0].background
    assert bg is not None
    assert bg.color == "#1a1a2e"
    assert bg.image_opacity == 0.5


def test_master_elements():
    """Project with master elements loads correctly."""
    data = {
        "project": {"id": "test", "name": "Test"},
        "ui": {
            "master_elements": [{
                "id": "nav",
                "type": "page_nav",
                "label": "Home",
                "target_page": "main",
                "pages": "*",
                "grid_area": {"col": 1, "row": 8, "col_span": 2, "row_span": 1},
                "style": {},
                "bindings": {},
            }],
            "pages": [],
        },
    }
    project = _load_project_dict(data)
    assert len(project.ui.master_elements) == 1
    assert project.ui.master_elements[0].pages == "*"


def test_page_groups():
    """Project with page groups loads correctly."""
    data = {
        "project": {"id": "test", "name": "Test"},
        "ui": {
            "page_groups": [
                {"name": "Control", "pages": ["main", "audio"]},
                {"name": "Settings", "pages": ["config"]},
            ],
            "pages": [],
        },
    }
    project = _load_project_dict(data)
    assert len(project.ui.page_groups) == 2
    assert project.ui.page_groups[0].name == "Control"


def test_ui_settings_theme():
    """UI settings with theme fields loads correctly."""
    data = {
        "project": {"id": "test", "name": "Test"},
        "ui": {
            "settings": {
                "theme_id": "midnight-blue",
                "theme_overrides": {"accent": "#ff5722"},
                "page_transition": "fade",
                "page_transition_duration": 300,
                "element_entry": "stagger",
                "element_stagger_ms": 50,
                "element_stagger_style": "scale",
            },
            "pages": [],
        },
    }
    project = _load_project_dict(data)
    s = project.ui.settings
    assert s.theme_id == "midnight-blue"
    assert s.page_transition == "fade"
    assert s.page_transition_duration == 300
    assert s.element_stagger_style == "scale"


def test_clock_element():
    """Clock element with specific properties loads."""
    data = {
        "project": {"id": "test", "name": "Test"},
        "ui": {
            "pages": [{
                "id": "p1", "name": "P1",
                "grid": {"columns": 12, "rows": 8},
                "elements": [{
                    "id": "clk1", "type": "clock",
                    "clock_mode": "meeting", "format": "h:mm A",
                    "duration_minutes": 30,
                    "grid_area": {"col": 1, "row": 1, "col_span": 3, "row_span": 1},
                    "style": {}, "bindings": {},
                }],
            }],
        },
    }
    project = _load_project_dict(data)
    el = project.ui.pages[0].elements[0]
    assert el.type == "clock"
    assert el.clock_mode == "meeting"
    assert el.duration_minutes == 30


def test_matrix_element():
    """Matrix element with config loads."""
    data = {
        "project": {"id": "test", "name": "Test"},
        "ui": {
            "pages": [{
                "id": "p1", "name": "P1",
                "grid": {"columns": 12, "rows": 8},
                "elements": [{
                    "id": "mx1", "type": "matrix",
                    "matrix_config": {
                        "input_count": 4,
                        "output_count": 2,
                        "input_labels": ["A", "B", "C", "D"],
                        "output_labels": ["X", "Y"],
                        "route_key_pattern": "dev.sw.out_*_src",
                    },
                    "grid_area": {"col": 1, "row": 1, "col_span": 6, "row_span": 5},
                    "style": {}, "bindings": {},
                }],
            }],
        },
    }
    project = _load_project_dict(data)
    el = project.ui.pages[0].elements[0]
    assert el.type == "matrix"
    assert el.matrix_config["input_count"] == 4


def test_list_element():
    """List element with items loads."""
    data = {
        "project": {"id": "test", "name": "Test"},
        "ui": {
            "pages": [{
                "id": "p1", "name": "P1",
                "grid": {"columns": 12, "rows": 8},
                "elements": [{
                    "id": "lst1", "type": "list",
                    "list_style": "selectable",
                    "item_height": 48,
                    "items": [
                        {"label": "Option A", "value": "a"},
                        {"label": "Option B", "value": "b"},
                    ],
                    "grid_area": {"col": 1, "row": 1, "col_span": 3, "row_span": 4},
                    "style": {}, "bindings": {},
                }],
            }],
        },
    }
    project = _load_project_dict(data)
    el = project.ui.pages[0].elements[0]
    assert el.type == "list"
    assert el.list_style == "selectable"
    assert len(el.items) == 2
