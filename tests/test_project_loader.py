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


def test_round_trip_child_entities_preserved():
    """A device's child_entities (user labels + per-child config) survives
    load -> save -> load exactly.

    P4 acceptance: the project layer is the source of truth for user labels
    and freeform per-child config, so the round-trip must be lossless.
    """
    data = {
        "openavc_version": "0.5.0",
        "project": {"id": "ce_rt", "name": "Child Entity Round Trip"},
        "devices": [
            {
                "id": "ctrl1",
                "driver": "fake_controller",
                "name": "Matrix",
                "child_entities": {
                    "encoder": {
                        "005": {
                            "label": "Lobby TX",
                            "config": {"room": "Lobby", "rack_u": 12},
                        },
                        "017": {"label": "Stage Left"},
                    },
                    "decoder": {
                        "01": {"label": "Lobby RX"},
                    },
                },
            },
        ],
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(data, f)
        tmp_path = f.name

    project = load_project(tmp_path)
    # Loaded model matches what we put in.
    dev = project.devices[0]
    assert "encoder" in dev.child_entities
    assert dev.child_entities["encoder"]["005"].label == "Lobby TX"
    assert dev.child_entities["encoder"]["005"].config == {
        "room": "Lobby", "rack_u": 12,
    }
    assert dev.child_entities["encoder"]["017"].label == "Stage Left"
    assert dev.child_entities["decoder"]["01"].label == "Lobby RX"

    # Round trip: save then reload, confirm bit-for-bit identity.
    save_project(tmp_path, project)
    reloaded = load_project(tmp_path)
    rdev = reloaded.devices[0]
    assert rdev.child_entities["encoder"]["005"].label == "Lobby TX"
    assert rdev.child_entities["encoder"]["005"].config == {
        "room": "Lobby", "rack_u": 12,
    }
    assert rdev.child_entities["encoder"]["017"].label == "Stage Left"
    assert rdev.child_entities["decoder"]["01"].label == "Lobby RX"

    # And the on-disk JSON keeps the same shape (no flattening, no nesting
    # gymnastics) so external tools and a future agent can read it back.
    on_disk = json.loads(Path(tmp_path).read_text())
    assert on_disk["devices"][0]["child_entities"] == {
        "encoder": {
            "005": {
                "label": "Lobby TX",
                "config": {"room": "Lobby", "rack_u": 12},
            },
            "017": {"label": "Stage Left", "config": {}},
        },
        "decoder": {
            "01": {"label": "Lobby RX", "config": {}},
        },
    }

    Path(tmp_path).unlink()


def test_child_entities_defaults_to_empty_on_existing_device():
    """A device that doesn't supply child_entities loads with the field
    defaulted to an empty dict — preserves backward compatibility with
    pre-v0.5 project files that haven't yet migrated."""
    data = {
        "openavc_version": "0.5.0",
        "project": {"id": "p", "name": "P"},
        "devices": [
            {"id": "proj1", "driver": "pjlink", "name": "Projector"},
        ],
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(data, f)
        tmp_path = f.name

    project = load_project(tmp_path)
    assert project.devices[0].child_entities == {}
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


def test_installer_seed_project_matches_v04_schema():
    """Regression for A5: the starter project bundled with every fresh
    install must validate against the v0.4.0 loader schema with NO extra
    fields at the top level, ui.settings, ui.pages[0], ui.pages[0].grid,
    or isc. Extras are silently preserved by extra='allow', which means
    bad field names accumulate forever and the documented field names
    (element_stagger_ms, background, shared_state, etc.) get loader
    defaults instead of seed values.
    """
    seed_path = Path(__file__).parent.parent / "installer" / "seed" / "default" / "project.avc"
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    project = ProjectConfig(**data)

    # Top-level
    extras = set(data.keys()) - set(ProjectConfig.model_fields.keys())
    assert extras == set(), f"Top-level extras: {extras}"
    # device_groups required at top level for v0.4.0
    assert "device_groups" in data

    # ui.settings — must use element_stagger_ms, not legacy element_entry_*
    ui_settings_extras = set(data["ui"]["settings"].keys()) - set(
        type(project.ui.settings).model_fields.keys()
    )
    assert ui_settings_extras == set(), f"ui.settings extras: {ui_settings_extras}"

    # ui.pages[0] — no `icon`, `group`, `overlays`, or page-level background_*
    page_extras = set(data["ui"]["pages"][0].keys()) - set(
        type(project.ui.pages[0]).model_fields.keys()
    )
    assert page_extras == set(), f"ui.pages[0] extras: {page_extras}"

    # ui.pages[0].grid — only columns, rows
    grid_extras = set(data["ui"]["pages"][0]["grid"].keys()) - set(
        type(project.ui.pages[0].grid).model_fields.keys()
    )
    assert grid_extras == set(), f"grid extras: {grid_extras}"

    # isc — uses shared_state, auth_key, enabled (not instance_name, shared_variables)
    isc_extras = set(data["isc"].keys()) - set(type(project.isc).model_fields.keys())
    assert isc_extras == set(), f"isc extras: {isc_extras}"


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


# --- Glob-metachar rejection in ids (L-083 / L-084) ---
#
# An id carrying an fnmatch metacharacter (* ? [) becomes part of a state key
# and its per-key state.changed event; the fnmatch-based subscription dispatch
# then mis-routes or drops notifications for it. Reject these at authoring time.


@pytest.mark.parametrize("bad_id", ["enc[oder]", "dev*", "q?", "a[1]"])
def test_device_id_rejects_glob_metachars(bad_id):
    from server.core.project_loader import DeviceConfig

    with pytest.raises(ValidationError):
        DeviceConfig(id=bad_id, driver="x", name="X")


@pytest.mark.parametrize("bad_id", ["a[1]", "v*", "q?"])
def test_variable_id_rejects_glob_metachars(bad_id):
    from server.core.project_loader import VariableConfig

    with pytest.raises(ValidationError):
        VariableConfig(id=bad_id, type="string")


@pytest.mark.parametrize("bad_id", ["btn[1]", "slider*", "x?"])
def test_ui_element_id_rejects_glob_metachars(bad_id):
    from server.core.project_loader import UIElement

    with pytest.raises(ValidationError):
        UIElement(id=bad_id, type="button")


def test_ids_accept_normal_names():
    """Ordinary identifiers (incl. the numeric/underscore child-style names)
    still validate — the guard only rejects dots and glob metachars."""
    from server.core.project_loader import DeviceConfig, UIElement, VariableConfig

    DeviceConfig(id="encoder_5", driver="x", name="X")
    VariableConfig(id="room_active", type="boolean")
    UIElement(id="vol_slider", type="slider")


@pytest.mark.parametrize("bad_id", [".", "a.b", "grp*", "t?", "m[1]"])
def test_all_state_key_models_reject_dots_and_glob(bad_id):
    """Every id that becomes a state-key segment rejects both dots and glob
    metachars, through the one shared validator, so the rule can't drift
    between models. UIPage previously had no id guard and UIElement rejected
    glob but not dots."""
    from server.core.project_loader import (
        DeviceGroup,
        MacroConfig,
        TriggerConfig,
        UIElement,
        UIPage,
    )

    with pytest.raises(ValidationError):
        DeviceGroup(id=bad_id, name="X")
    with pytest.raises(ValidationError):
        TriggerConfig(id=bad_id, type="event")
    with pytest.raises(ValidationError):
        MacroConfig(id=bad_id, name="X")
    with pytest.raises(ValidationError):
        UIElement(id=bad_id, type="button")
    with pytest.raises(ValidationError):
        UIPage(id=bad_id, name="X")


# --- ScriptConfig.id validation parity with the REST create path ----------


@pytest.mark.parametrize(
    "bad_id",
    ["My Script", "scripts/evil", "room.logic", "UPPER", "", "has-dash", "a b"],
)
def test_script_config_id_rejects_unsafe(bad_id):
    """The project-load path must reject script ids the REST create path
    forbids (api/models.py ScriptCreateRequest: ^[a-z0-9_]+$). Pre-fix the
    loader only rejected dots, so an imported project could smuggle ids with
    slashes/spaces/uppercase/dashes that become odd sys.modules keys and
    thread names."""
    from server.core.project_loader import ScriptConfig

    with pytest.raises(ValidationError):
        ScriptConfig(id=bad_id, file="x.py")


def test_script_config_id_accepts_safe():
    from server.core.project_loader import ScriptConfig

    s = ScriptConfig(id="room_logic_2", file="room_logic_2.py")
    assert s.id == "room_logic_2"


# --- save_project_async offloads the blocking save to a worker thread ------


@pytest.mark.asyncio
async def test_save_project_async_persists(tmp_path):
    """The async wrapper writes the same file the sync save_project does
    (it runs the blocking body via asyncio.to_thread)."""
    from server.core.project_loader import save_project_async

    proj = ProjectConfig(**TEST_PROJECT)
    path = tmp_path / "p.avc"
    await save_project_async(path, proj)
    assert path.exists()
    assert load_project(path).project.id == "test_room"


# --- driver source classification resolves by declared id, not stem -------


def test_driver_source_resolves_by_declared_id_not_stem(tmp_path, monkeypatch):
    """A repo driver whose filename stem differs from its declared id must
    be classified 'community', not mis-stamped 'builtin' (which would drop
    it from the export bundle). Uploads keep their original filename, so
    stem != id is a real case."""
    import server.system_config as sc
    from server.core.project_loader import _get_driver_source

    defs = tmp_path / "definitions"
    defs.mkdir()
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(sc, "DRIVER_DEFINITIONS_DIR", defs)
    monkeypatch.setattr(sc, "DRIVER_REPO_DIR", repo)

    (repo / "uploaded_file.avcdriver").write_text(
        "id: acme_widget\nname: Acme Widget\ntransport: tcp\n", encoding="utf-8"
    )
    assert _get_driver_source("acme_widget") == "community"


def test_driver_source_stem_match_still_classifies_community(tmp_path, monkeypatch):
    import server.system_config as sc
    from server.core.project_loader import _get_driver_source

    defs = tmp_path / "definitions"
    defs.mkdir()
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(sc, "DRIVER_DEFINITIONS_DIR", defs)
    monkeypatch.setattr(sc, "DRIVER_REPO_DIR", repo)

    (repo / "acme_widget.avcdriver").write_text(
        "id: acme_widget\nname: Acme Widget\ntransport: tcp\n", encoding="utf-8"
    )
    assert _get_driver_source("acme_widget") == "community"


def test_driver_source_reflects_the_current_definitions_tree(tmp_path, monkeypatch):
    """The built-in id scan is cached, so pin that it can never go stale.

    The same driver id must classify as 'builtin' or 'community' purely by
    whether the definitions tree currently serves it — a cached answer from an
    earlier tree must not leak. A stale answer here would mis-stamp a driver's
    source, and source drives which driver files the export bundler carries.
    """
    import server.system_config as sc
    from server.core.project_loader import _get_driver_source

    served = tmp_path / "served"
    served.mkdir()
    (served / "acme_widget.avcdriver").write_text(
        "id: acme_widget\nname: Acme Widget\ntransport: tcp\n", encoding="utf-8"
    )
    monkeypatch.setattr(sc, "DRIVER_DEFINITIONS_DIR", served)
    assert _get_driver_source("acme_widget") == "builtin"

    # A different definitions tree that does not serve it.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(sc, "DRIVER_DEFINITIONS_DIR", empty)
    assert _get_driver_source("acme_widget") == "community"

    # Adding the driver to the tree in place must be picked up too.
    (empty / "acme_widget.avcdriver").write_text(
        "id: acme_widget\nname: Acme Widget\ntransport: tcp\n", encoding="utf-8"
    )
    assert _get_driver_source("acme_widget") == "builtin"
