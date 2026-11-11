"""The AI's UI doors, pinned where they change a page's fields, its arrangements,
the containers its controls sit in, and what a deleted page or element leaves.

Each test records what a door does today: its reply, the project it stores, and
which refusal wins when an input is wrong twice. Some of it looks odd (a page
element that is not an object is skipped by one create door and crashes the
other; a box that is not an object raises a bare TypeError from the layouts
door); those are pinned as they are, not corrected here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.project_loader import ProjectConfig
from openavc.ui.page_review import do_action_dicts

from tests.test_ui_tools_lookups_pinned import _Devices, _Plugins

BOX = {"x": 10, "y": 10, "w": 40, "h": 40}


def _project() -> dict:
    """Two pages with a portrait variant, containers, masters, triggers, groups."""
    return {
        "project": {"id": "acme_room", "name": "Acme Room"},
        "devices": [{"id": "amp", "driver": "acme_amp", "name": "Amplifier"}],
        "macros": [{
            "id": "mac", "name": "Mac",
            "triggers": [{
                "id": "t1", "type": "state_change", "state_key": "var.x",
                "conditions": [
                    {"key": "system.current_page", "value": "other"},
                    {"key": "system.current_page", "value": "main"},
                    {"key": "var.mode", "value": "other"},
                ],
            }, {"id": "t2", "type": "startup"}],
        }],
        "ui": {
            "settings": {"idle_page": "other"},
            "page_groups": [
                {"name": "Both", "pages": ["main", "other"]},
                {"name": "Main", "pages": ["main"]},
            ],
            "master_elements": [
                {"id": "m_both", "type": "button", "pages": ["main", "other"]},
                {"id": "m_other", "type": "button", "pages": ["other"]},
                {"id": "m_every", "type": "button", "pages": "*"},
                {"id": "m_main", "type": "button", "pages": ["main"]},
            ],
            "pages": [
                {
                    "id": "main", "name": "Main",
                    "elements": [
                        {"id": "outer", "type": "group"},
                        {"id": "inner", "type": "group", "parent": "outer"},
                        {"id": "kid", "type": "button", "parent": "inner"},
                        {"id": "btn", "type": "button"},
                        {"id": "lbl", "type": "label"},
                        {"id": "pin", "type": "button", "locked": True},
                        {"id": "nav", "type": "page_nav", "target_page": "other"},
                        {"id": "nav_main", "type": "page_nav", "target_page": "main"},
                        {"id": "go", "type": "button", "bindings": {"do": {
                            "press": [
                                {"action": "ui.navigate", "page": "other"},
                                {"action": "macro", "macro": "mac"},
                            ],
                            "release": [{"action": "ui.navigate", "page": "other"}],
                            "hold": {"action": "ui.navigate", "page": "other"},
                            "double": {"action": "ui.navigate", "page": "main"},
                            "change": [{"action": "ui.navigate", "page": "main"}],
                        }}},
                    ],
                    "layouts": [
                        {"id": "landscape", "primary": True, "hidden": ["lbl"],
                         "placements": {
                             "outer": {"x": 0, "y": 0, "w": 50, "h": 50},
                             "inner": {"x": 50, "y": 50, "w": 50, "h": 50},
                             "kid": {"x": 10, "y": 20, "w": 40, "h": 40},
                             "btn": {"x": 60, "y": 60, "w": 20, "h": 20},
                             "pin": {"x": 80, "y": 0, "w": 10, "h": 10},
                         }},
                        {"id": "portrait", "orientation": "portrait",
                         "inherits": "landscape",
                         "placements": {"kid": {"x": 0, "y": 0, "w": 100, "h": 50}}},
                    ],
                },
                {
                    "id": "other", "name": "Other",
                    "elements": [
                        {"id": "back", "type": "page_nav", "target_page": "main"},
                        {"id": "dup", "type": "button"},
                    ],
                    "layouts": [{"id": "landscape", "primary": True}],
                },
                {
                    "id": "third", "name": "Third",
                    "elements": [{"id": "dup", "type": "button"}],
                    "layouts": [{"id": "landscape", "primary": True,
                                 "placements": {"dup": {"x": 1, "y": 1, "w": 1, "h": 1}},
                                 "hidden": ["dup"]}],
                },
            ],
        },
    }


def _engine(project: dict | None = None):
    engine = MagicMock()
    engine.project = ProjectConfig.model_validate(project or _project())
    engine.project_path = None
    engine.plugins = _Plugins()
    engine.broadcast_ws = AsyncMock()

    async def _apply_edit(mutate):
        new_project = engine.project.model_copy(deep=True)
        mutate(new_project)
        engine.project = new_project
        return 1

    engine.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    return engine


@pytest.fixture(autouse=True)
def _patch_save_project():
    with patch("openavc.core.project_loader.save_project"):
        yield


async def _call(engine, tool: str, payload: dict):
    """The door's own reply, or the exception it raises."""
    agent = MagicMock()
    agent.state = MagicMock()
    handler = AIToolHandler(agent, _Devices(), MagicMock(emit=AsyncMock()))
    with patch.object(handler, "_get_engine", return_value=engine):
        return await getattr(handler, f"_{tool}")(payload)


def _page(engine, page_id: str):
    return next(p for p in engine.project.ui.pages if p.id == page_id)


def _layout(engine, page_id: str, layout_id: str):
    return next(lay for lay in _page(engine, page_id).layouts if lay.id == layout_id)


def _el(engine, page_id: str, el_id: str):
    return next(el for el in _page(engine, page_id).elements if el.id == el_id)


def _boxes(engine, page_id: str, layout_id: str) -> dict:
    return {
        el_id: box.model_dump()
        for el_id, box in _layout(engine, page_id, layout_id).placements.items()
    }


def _dump(engine) -> dict:
    return engine.project.model_dump(mode="json")


# --- update_ui_page: the page's own fields --------------------------------------


@pytest.mark.asyncio
async def test_update_page_takes_every_field_and_names_them_in_a_fixed_order():
    engine = _engine()
    result = await _call(engine, "update_ui_page", {
        "page_id": "other",
        "grant": {"devices": ["amp"]},
        "custom_config": {"k": 1},
        "custom_file": "panel/x.html",
        "render_mode": "custom",
        "background": {"color": "#111"},
        "overlay": {"position": "top"},
        "page_type": "overlay",
        "snap": {"x": 5},
        "layouts": [{"id": "landscape", "hidden": ["dup"]}],
        "name": "Renamed",
    })
    # Handed to markup with controls still on it: the review says so.
    warnings = result.pop("warnings")
    assert len(warnings) == 1
    assert warnings[0].startswith("other shows panel/x.html, so the 2 controls on it are not ")
    assert result.pop("warning_note").startswith("The write landed")
    assert result == {
        "status": "updated", "page_id": "other",
        "changed": ["name", "layouts", "snap", "page_type", "overlay", "background",
                    "render_mode", "custom_file", "custom_config", "grant"],
        "grant": {
            "before": None,
            "after": {"devices": ["amp"], "variables": [], "macros": False, "navigate": False},
        },
    }
    page = _page(engine, "other")
    assert page.name == "Renamed"
    assert page.snap.model_dump() == {"enabled": True, "x": 5.0, "y": 12.5}
    assert page.page_type == "overlay"
    assert page.overlay.model_dump()["position"] == "top"
    assert page.background.model_dump()["color"] == "#111"
    assert (page.render_mode, page.custom_file, page.custom_config) == (
        "custom", "panel/x.html", {"k": 1},
    )
    assert page.grant.devices == ["amp"]
    assert _layout(engine, "other", "landscape").hidden == ["dup"]


@pytest.mark.asyncio
async def test_update_page_clears_overlay_and_background_with_an_empty_value():
    engine = _engine()
    await _call(engine, "update_ui_page", {
        "page_id": "other", "overlay": {"position": "top"}, "background": {"color": "#1"},
    })
    result = await _call(engine, "update_ui_page", {
        "page_id": "other", "background": {}, "overlay": None,
    })
    assert result["changed"] == ["overlay", "background"]
    assert _page(engine, "other").overlay is None
    assert _page(engine, "other").background is None
    await _call(engine, "update_ui_page", {
        "page_id": "other", "overlay": {"position": "top"}, "background": {"color": "#1"},
    })
    await _call(engine, "update_ui_page", {"page_id": "other", "overlay": {}, "background": None})
    assert _page(engine, "other").overlay is None
    assert _page(engine, "other").background is None


@pytest.mark.asyncio
async def test_update_page_replaces_a_grant_and_echoes_both_sides():
    engine = _engine()
    await _call(engine, "update_ui_page", {"page_id": "other", "grant": {"devices": ["amp"]}})
    result = await _call(engine, "update_ui_page", {"page_id": "other", "grant": None})
    assert result["grant"] == {
        "before": {"devices": ["amp"], "variables": [], "macros": False, "navigate": False},
        "after": None,
    }
    assert _page(engine, "other").grant is None


@pytest.mark.parametrize("payload, error", [
    ({"page_id": "nope", "name": "x"}, "UI page 'nope' not found"),
    ({"page_id": "other"}, "No fields to update"),
    ({"page_id": "other", "colour": "red"}, "No fields to update"),
    ({"page_id": "other", "grid": {}, "render_mode": "bad", "name": "x"},
     "Page 'other': 'grid' was removed in project format 0.8.0. Use layouts[] with "
     "placements, plus the authoring-only snap increment."),
    ({"page_id": "other", "name": "x", "layouts": "x", "render_mode": "bad"},
     "'layouts' must be an array of layout objects"),
    ({"page_id": "other", "render_mode": "Custom", "grant": "amp"},
     "Page 'other': render_mode is 'elements' (draw the page's own controls) or "
     "'custom' (give the screen to a page in the project's ui/ folder), not 'Custom'."),
])
@pytest.mark.asyncio
async def test_update_page_refuses_the_first_problem_and_stores_nothing(payload, error):
    engine = _engine()
    before = _dump(engine)
    result = await _call(engine, "update_ui_page", payload)
    assert result == {"error": error}
    assert _dump(engine) == before


@pytest.mark.asyncio
async def test_update_page_refuses_a_grant_that_is_not_shaped_right():
    engine = _engine()
    result = await _call(engine, "update_ui_page", {
        "page_id": "other", "name": "x", "grant": {"devices": "amp"},
    })
    assert result["error"].startswith(
        "Page 'other': 'grant' is not shaped right -- devices and variables are lists "
        "of ids, macros and navigate are true/false. ("
    )
    assert _page(engine, "other").name == "Other"


@pytest.mark.asyncio
async def test_update_page_lets_a_malformed_overlay_raise():
    engine = _engine()
    with pytest.raises(ValidationError):
        await _call(engine, "update_ui_page", {"page_id": "other", "overlay": {"width": "wide"}})
    with pytest.raises(TypeError):
        await _call(engine, "update_ui_page", {"page_id": "other", "background": ["x"]})


@pytest.mark.asyncio
async def test_update_page_reviews_only_a_move_or_a_hand_over():
    engine = _engine()
    await _call(engine, "add_ui_elements", {"page_id": "other", "elements": [
        {"id": "tiny", "type": "button", "placement": {"x": 0, "y": 0, "w": 1, "h": 1}},
    ]})
    renamed = await _call(engine, "update_ui_page", {"page_id": "other", "name": "N"})
    assert "warnings" not in renamed
    handed = await _call(engine, "update_ui_page", {"page_id": "other", "render_mode": "elements"})
    assert "warnings" not in handed
    moved = await _call(engine, "update_ui_page", {"page_id": "other", "layouts": [
        {"id": "landscape", "placements": {"tiny": {"x": 0, "y": 0, "w": 2, "h": 2}}},
    ]})
    assert len(moved["warnings"]) == 1
    assert moved["warnings"][0].startswith("tiny (button) is about 26x16px")


@pytest.mark.asyncio
async def test_update_page_a_render_mode_alone_is_reviewed():
    engine = _engine()
    result = await _call(engine, "update_ui_page", {"page_id": "other", "render_mode": "custom"})
    assert result["changed"] == ["render_mode"]
    assert result["warnings"] == [
        "other is set to show a page you wrote but names no file, so it still draws its "
        "controls. Choose a file in the project's ui/ folder, or set the page back to controls."
    ]


# --- add_ui_page ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_page_takes_every_page_field_and_defaults_the_rest():
    engine = _engine()
    result = await _call(engine, "add_ui_page", {
        "id": "fresh", "render_mode": "custom", "custom_file": "panel/f.html",
        "custom_config": {"a": 1}, "grant": {"macros": True}, "page_type": "overlay",
        "overlay": {"position": "top"}, "background": {"color": "#222"},
        "elements": [{"id": "e1", "type": "button",
                      "placement": {"x": 0.123456, "y": 0.00005, "w": 10, "h": 10}}],
    })
    warnings = result.pop("warnings")
    assert len(warnings) == 1
    assert warnings[0].startswith("fresh shows panel/f.html, so the 1 control on it is not drawn.")
    assert result.pop("warning_note").startswith("The write landed")
    assert result == {
        "status": "created", "id": "fresh",
        "grant": {"before": None, "after": {
            "devices": [], "variables": [], "macros": True, "navigate": False}},
    }
    page = _page(engine, "fresh")
    assert page.name == "fresh"
    assert page.snap.model_dump() == {"enabled": True, "x": 100.0 / 12, "y": 12.5}
    assert (page.render_mode, page.custom_file, page.custom_config, page.page_type) == (
        "custom", "panel/f.html", {"a": 1}, "overlay",
    )
    assert page.overlay.position == "top" and page.background.color == "#222"
    assert [(lay.id, lay.primary) for lay in page.layouts] == [("landscape", True)]
    assert _boxes(engine, "fresh", "landscape") == {
        "e1": {"x": 0.1235, "y": 0.0001, "w": 10.0, "h": 10.0},
    }


@pytest.mark.asyncio
async def test_add_page_keeps_the_name_snap_and_layouts_it_is_given():
    engine = _engine()
    await _call(engine, "add_ui_page", {
        "id": "p2", "name": "Two", "snap": {"enabled": False},
        "layouts": [{"id": "wide", "primary": True}],
    })
    page = _page(engine, "p2")
    assert page.name == "Two"
    assert page.snap.enabled is False
    assert [lay.id for lay in page.layouts] == ["wide"]


@pytest.mark.asyncio
async def test_add_page_skips_a_non_object_element_in_its_checks_and_the_model_refuses_it():
    engine = _engine()
    result = await _call(engine, "add_ui_page", {
        "id": "p3", "elements": ["junk", {"id": "e", "type": "button"}],
    })
    assert result["error"].startswith("Page 'p3' is not shaped right: ")


@pytest.mark.asyncio
async def test_add_page_answers_for_every_element_at_once():
    engine = _engine()
    result = await _call(engine, "add_ui_page", {
        "id": "p4", "elements": [
            {"id": "a", "type": "button", "grid_area": "1/1"},
            {"id": "b", "type": "button", "placement": "x"},
            {"id": "c", "type": "button", "bindings": {"do": {"press": [{"action": "nope"}]}}},
        ],
    })
    assert result["errors"][0] == (
        "Element 'a': 'grid_area' was removed in project format 0.8.0. Use placement "
        "{x, y, w, h} (percentages of the parent box)."
    )
    assert result["errors"][1] == "Element 'b': 'placement' must be an object {x, y, w, h}, got str"
    assert result["errors"][2].startswith("Element 'c': Binding validation failed")
    assert result["error"].startswith("3 elements could not be added: Element 'a'")
    assert all(p.id != "p4" for p in engine.project.ui.pages)


@pytest.mark.asyncio
async def test_add_page_refuses_retired_page_geometry_and_a_taken_id():
    engine = _engine()
    assert await _call(engine, "add_ui_page", {"id": "main"}) == {
        "error": "UI page 'main' already exists"}
    assert await _call(engine, "add_ui_page", {"id": "p5", "grid_gap": 1}) == {
        "error": "Page 'p5': 'grid_gap' was removed in project format 0.8.0. "
                 "Use nothing -- pages no longer have a gap."}
    assert await _call(engine, "add_ui_page", {"id": ""}) == {"error": "Page ID is required"}


# --- layouts, through update_ui_page --------------------------------------------


@pytest.mark.parametrize("layouts, error", [
    ({"id": "x"}, "'layouts' must be an array of layout objects"),
    (["x"], "Each layout needs an 'id'"),
    ([{"orientation": "portrait"}], "Each layout needs an 'id'"),
    ([{"id": "landscape", "placements": ["x"]}],
     "Layout 'landscape': 'placements' must be an object keyed by element id"),
    ([{"id": "landscape", "placements": ["x"], "inherits": "landscape"}],
     "Layout 'landscape': 'placements' must be an object keyed by element id"),
    ([{"id": "v2", "inherits": "v2"}], "Layout 'v2' cannot inherit from itself"),
    ([{"id": "v2", "inherits": "ghost"}],
     "Layout 'v2': inherits 'ghost', which is not an arrangement on this page. "
     "Available: landscape, portrait"),
    ([{"id": "portrait", "inherits": "portrait"}], "Layout 'portrait' cannot inherit from itself"),
    ([{"id": "v2", "orientation": "portrait"}],
     "Layout 'v2': there is already a portrait arrangement ('portrait'). Edit that one instead."),
    ([{"id": "landscape", "orientation": "portrait"}],
     "Layout 'landscape': there is already a portrait arrangement ('portrait'). Edit that one instead."),
    ([{"id": "portrait", "primary": True}],
     "Layout 'portrait': which layout is primary cannot be changed here. 'landscape' is the "
     "arrangement an unmatched screen falls back to."),
    ([{"id": "landscape", "primary": False, "inherits": "ghost"}],
     "Layout 'landscape': which layout is primary cannot be changed here. 'landscape' is the "
     "arrangement an unmatched screen falls back to."),
    ([{"id": "landscape", "inherits": "portrait"}],
     "Layout inheritance loops: 'landscape' eventually inherits from itself"),
    ([{"id": "v2", "orientation": "portrait", "inherits": "ghost"}],
     "Layout 'v2': inherits 'ghost', which is not an arrangement on this page. "
     "Available: landscape, portrait"),
    ([{"id": "landscape", "inherits": "ghost", "orientation": "portrait"}],
     "Layout 'landscape': inherits 'ghost', which is not an arrangement on this page. "
     "Available: landscape, portrait"),
    ([{"id": "landscape", "hidden": []}, "x"], "Each layout needs an 'id'"),
    ([{"id": "portrait", "orientation": "landscape", "inherits": None},
      {"id": "landscape", "orientation": "portrait"}],
     "Layout 'portrait': there is already a landscape arrangement ('landscape'). "
     "Edit that one instead."),
])
@pytest.mark.asyncio
async def test_layouts_refusals_in_order(layouts, error):
    engine = _engine()
    before = _dump(engine)
    result = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": layouts})
    assert result == {"error": error}
    assert _dump(engine) == before


@pytest.mark.asyncio
async def test_layouts_a_box_that_is_not_an_object_raises_before_the_other_checks():
    engine = _engine()
    with pytest.raises(TypeError):
        await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
            {"id": "v2", "inherits": "ghost", "placements": {"btn": 5}},
        ]})


@pytest.mark.asyncio
async def test_layouts_a_new_one_is_a_variant_of_the_primary():
    project = _project()
    project["ui"]["pages"][0]["layouts"] = [project["ui"]["pages"][0]["layouts"][0]]
    engine = _engine(project)
    result = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [{
        "id": "tall", "orientation": "portrait", "primary": True, "hidden": ["btn"],
        "placements": {"btn": {"x": 1.23456, "y": 2, "w": 3, "h": 4}}, "label": "kept",
    }]})
    assert result["changed"] == ["layouts"]
    page = _page(engine, "main")
    assert [(lay.id, lay.primary, lay.inherits, lay.orientation) for lay in page.layouts] == [
        ("landscape", True, None, "landscape"), ("tall", False, "landscape", "portrait"),
    ]
    tall = _layout(engine, "main", "tall")
    assert tall.hidden == ["btn"]
    assert _boxes(engine, "main", "tall") == {"btn": {"x": 1.2346, "y": 2.0, "w": 3.0, "h": 4.0}}
    assert tall.model_dump()["label"] == "kept"


@pytest.mark.asyncio
async def test_layouts_a_new_one_may_name_its_own_parent_or_none():
    engine = _engine()
    await _call(engine, "update_ui_page", {"page_id": "other", "layouts": [
        {"id": "tall", "orientation": "portrait", "inherits": None},
    ]})
    assert _layout(engine, "other", "tall").inherits is None
    engine = _engine()
    await _call(engine, "update_ui_page", {"page_id": "other", "layouts": [
        {"id": "tall", "orientation": "portrait", "inherits": "landscape"},
    ]})
    assert _layout(engine, "other", "tall").inherits == "landscape"
    engine = _engine()
    result = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "v3", "inherits": None, "orientation": "landscape"},
    ]})
    assert result == {"error": "Layout 'v3': there is already a landscape arrangement "
                               "('landscape'). Edit that one instead."}


@pytest.mark.asyncio
async def test_layouts_an_edit_merges_boxes_and_replaces_hides():
    engine = _engine()
    result = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "portrait", "placements": {"btn": {"x": 5, "y": 5, "w": 5, "h": 5}},
         "hidden": ["btn"], "primary": False},
        {"id": "landscape", "hidden": None, "orientation": "landscape", "primary": True},
    ]})
    assert result["changed"] == ["layouts"]
    assert _boxes(engine, "main", "portrait") == {
        "kid": {"x": 0.0, "y": 0.0, "w": 100.0, "h": 50.0},
        "btn": {"x": 5.0, "y": 5.0, "w": 5.0, "h": 5.0},
    }
    assert _layout(engine, "main", "portrait").hidden == ["btn"]
    assert _layout(engine, "main", "landscape").hidden == []


@pytest.mark.asyncio
async def test_layouts_an_edit_can_repoint_inherits_and_turn_the_page():
    engine = _engine()
    await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "portrait", "inherits": None},
    ]})
    assert _layout(engine, "main", "portrait").inherits is None
    engine = _engine()
    await _call(engine, "update_ui_page", {"page_id": "other", "layouts": [
        {"id": "landscape", "orientation": "portrait"},
    ]})
    assert _layout(engine, "other", "landscape").orientation == "portrait"


@pytest.mark.asyncio
async def test_layouts_a_loop_made_by_two_edits_is_refused():
    engine = _engine()
    result = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "v2", "orientation": "landscape", "inherits": "portrait"},
    ]})
    assert "error" in result
    project = _project()
    project["ui"]["pages"][0]["layouts"].append({"id": "v2", "inherits": "portrait"})
    engine = _engine(project)
    result = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "portrait", "inherits": "v2"},
    ]})
    assert result == {"error": "Layout inheritance loops: 'portrait' eventually inherits from itself"}


# --- update_ui_element: boxes, hides and containers ------------------------------


@pytest.mark.asyncio
async def test_element_hide_and_unhide_per_arrangement():
    engine = _engine()
    r = await _call(engine, "update_ui_element", {"element_id": "btn", "hidden": True})
    assert r["changed"] == ["hidden"]
    await _call(engine, "update_ui_element", {"element_id": "btn", "hidden": 1})
    assert _layout(engine, "main", "landscape").hidden == ["lbl", "btn"]
    await _call(engine, "update_ui_element", {"element_id": "lbl", "hidden": False})
    assert _layout(engine, "main", "landscape").hidden == ["btn"]
    await _call(engine, "update_ui_element", {"element_id": "kid", "hidden": True,
                                              "layout_id": "portrait"})
    assert _layout(engine, "main", "portrait").hidden == ["kid"]
    await _call(engine, "update_ui_element", {"element_id": "kid", "hidden": False,
                                              "layout_id": "portrait"})
    assert _layout(engine, "main", "portrait").hidden == []


@pytest.mark.asyncio
async def test_element_unhide_refused_where_an_ancestor_hides_it():
    engine = _engine()
    before = _dump(engine)
    result = await _call(engine, "update_ui_element", {
        "element_id": "lbl", "hidden": False, "layout_id": "portrait", "label": "x",
    })
    assert result == {"error": "Element 'lbl' is hidden by layout 'landscape', which "
                               "'portrait' inherits from. Unhide it there, or the whole "
                               "chain keeps it hidden."}
    assert _dump(engine) == before


@pytest.mark.asyncio
async def test_element_an_unknown_layout_is_named_with_the_choices():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {
        "element_id": "btn", "hidden": True, "layout_id": "zz",
    })
    assert result == {"error": "Layout 'zz' not found on page 'main'. Available: landscape, portrait"}


@pytest.mark.asyncio
async def test_element_a_box_in_a_variant_starts_from_the_inherited_one():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {
        "element_id": "btn", "layout_id": "portrait", "placement": {"x": 1.00004},
    })
    assert result["changed"] == ["placement"]
    assert _boxes(engine, "main", "portrait")["btn"] == {"x": 1.0, "y": 60.0, "w": 20.0, "h": 20.0}
    assert _boxes(engine, "main", "landscape")["btn"] == {"x": 60.0, "y": 60.0, "w": 20.0, "h": 20.0}
    await _call(engine, "update_ui_element", {"element_id": "lbl", "placement": {"w": 5}})
    assert _boxes(engine, "main", "landscape")["lbl"] == {"x": 0.0, "y": 0.0, "w": 5.0, "h": 100.0}


@pytest.mark.asyncio
async def test_element_moved_into_a_container_keeps_its_place_in_every_arrangement():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {"element_id": "kid", "parent": "outer"})
    assert result["changed"] == ["parent"]
    assert _el(engine, "main", "kid").parent == "outer"
    assert _boxes(engine, "main", "landscape")["kid"] == {"x": 55.0, "y": 60.0, "w": 20.0, "h": 20.0}
    assert _boxes(engine, "main", "portrait")["kid"] == {
        "x": 50.0, "y": 50.0, "w": 50.0, "h": 25.0}
    await _call(engine, "update_ui_element", {"element_id": "kid", "parent": ""})
    assert _el(engine, "main", "kid").parent is None
    assert _boxes(engine, "main", "landscape")["kid"] == {
        "x": 27.5, "y": 30.0, "w": 10.0, "h": 10.0}
    assert _boxes(engine, "main", "portrait")["kid"] == {"x": 25.0, "y": 25.0, "w": 25.0, "h": 12.5}


@pytest.mark.asyncio
async def test_element_moved_into_a_container_with_a_stated_box():
    engine = _engine()
    await _call(engine, "update_ui_element", {
        "element_id": "btn", "parent": "outer", "placement": {"x": 1, "y": 2, "w": 3, "h": 4},
    })
    assert _boxes(engine, "main", "landscape")["btn"] == {"x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0}
    assert "btn" not in _boxes(engine, "main", "portrait")


@pytest.mark.asyncio
async def test_element_reparent_leaves_what_it_cannot_measure():
    engine = _engine()
    # The same parent again: nothing converts, and the reply still names it.
    result = await _call(engine, "update_ui_element", {"element_id": "kid", "parent": "inner"})
    assert result["changed"] == ["parent"]
    assert _boxes(engine, "main", "landscape")["kid"] == {"x": 10.0, "y": 20.0, "w": 40.0, "h": 40.0}
    # A container with no box anywhere: no arrangement can be converted.
    project = _project()
    project["ui"]["pages"][0]["elements"].append({"id": "flat", "type": "group"})
    project["ui"]["pages"][0]["layouts"][0]["placements"]["flat"] = {
        "x": 0, "y": 0, "w": 0, "h": 10}
    engine = _engine(project)
    await _call(engine, "update_ui_element", {"element_id": "btn", "parent": "flat"})
    assert _el(engine, "main", "btn").parent == "flat"
    assert _boxes(engine, "main", "landscape")["btn"] == {"x": 60.0, "y": 60.0, "w": 20.0, "h": 20.0}
    # An element with no box in any arrangement gets none from the move.
    engine = _engine()
    await _call(engine, "update_ui_element", {"element_id": "lbl", "parent": "outer"})
    assert _el(engine, "main", "lbl").parent == "outer"
    assert "lbl" not in _boxes(engine, "main", "landscape")


@pytest.mark.asyncio
async def test_element_the_same_parent_again_leaves_an_unrounded_box_as_stored():
    project = _project()
    project["ui"]["pages"][0]["layouts"][0]["placements"]["kid"] = {
        "x": 10.123456, "y": 20, "w": 40, "h": 40}
    engine = _engine(project)
    await _call(engine, "update_ui_element", {"element_id": "kid", "parent": "inner"})
    assert _boxes(engine, "main", "landscape")["kid"]["x"] == 10.123456


@pytest.mark.asyncio
async def test_element_moved_into_a_container_whose_box_is_not_a_number_raises():
    # 0 x inf: the innermost container's page box comes out NaN, which no
    # area test refuses, so the conversion divides by it and the rounding raises.
    project = _project()
    page = project["ui"]["pages"][1]
    page["elements"] += [
        {"id": "g1", "type": "group"},
        {"id": "g2", "type": "group", "parent": "g1"},
        {"id": "g3", "type": "group", "parent": "g2"},
    ]
    huge = {"x": 0, "y": 0, "w": 1e300, "h": 1e300}
    page["layouts"][0]["placements"] = {
        "g1": huge, "g2": dict(huge), "g3": {"x": 0, "y": 0, "w": 0, "h": 0},
        "back": {"x": 10, "y": 10, "w": 10, "h": 10},
    }
    engine = _engine(project)
    before = _dump(engine)
    with pytest.raises(ValueError):
        await _call(engine, "update_ui_element", {"element_id": "back", "parent": "g3"})
    assert _dump(engine) == before


@pytest.mark.parametrize("parent, error", [
    ("ghost", "Container 'ghost' is not an element on page 'main'"),
    ("btn", "'btn' is a button, not a container -- only a group element can hold children"),
    ("inner", "Element 'outer' cannot be placed inside itself or its own contents"),
    ("outer", "Element 'outer' cannot be placed inside itself or its own contents"),
])
@pytest.mark.asyncio
async def test_element_container_refusals(parent, error):
    engine = _engine()
    before = _dump(engine)
    result = await _call(engine, "update_ui_element", {"element_id": "outer", "parent": parent})
    assert result == {"error": error}
    assert _dump(engine) == before


@pytest.mark.asyncio
async def test_element_locked_refuses_a_move_and_a_rehome_but_not_a_property():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {
        "element_id": "pin", "parent": "outer", "placement": {"x": 1},
    })
    assert result == {"error": "Element 'pin' is locked, so its placement and parent cannot be "
                               "changed. Send locked: false first if you meant to move it."}
    ok = await _call(engine, "update_ui_element", {"element_id": "pin", "label": "L"})
    assert ok["changed"] == ["label"]


@pytest.mark.asyncio
async def test_a_locked_control_is_moved_by_neither_door():
    """The element door and the page door's layouts write refuse a locked
    control's box in the same words; a hide is not a move, and an unlocked
    control in the same entry moves once the locked one is left out."""
    engine = _engine()
    before = _dump(engine)
    element = await _call(engine, "update_ui_element", {
        "element_id": "pin", "placement": {"x": 50},
    })
    page = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "landscape", "placements": {"btn": BOX, "pin": BOX}},
    ]})
    refusal = ("Element 'pin' is locked, so its placement cannot be changed. "
               "Send locked: false first if you meant to move it.")
    assert element == {"error": refusal}
    assert page == {"error": refusal}
    new_variant = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "wide", "orientation": "landscape", "placements": {"pin": BOX}},
    ]})
    assert new_variant == {"error": refusal}
    assert _dump(engine) == before

    hidden = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "portrait", "hidden": ["pin"], "placements": {"btn": BOX}},
    ]})
    assert hidden["changed"] == ["layouts"]
    assert _layout(engine, "main", "portrait").hidden == ["pin"]
    assert _boxes(engine, "main", "landscape")["pin"] == {"x": 80, "y": 0, "w": 10, "h": 10}


@pytest.mark.asyncio
async def test_the_layouts_write_names_every_locked_control_it_was_asked_to_move():
    project = _project()
    project["ui"]["pages"][0]["elements"][3]["locked"] = True  # btn
    engine = _engine(project)
    result = await _call(engine, "update_ui_page", {"page_id": "main", "layouts": [
        {"id": "portrait", "placements": {"kid": BOX, "btn": BOX, "pin": BOX}},
    ]})
    assert result == {"error": "Elements 'btn' and 'pin' are locked, so their placements "
                               "cannot be changed. Send locked: false first if you meant "
                               "to move them."}


@pytest.mark.asyncio
async def test_element_refusal_order_bindings_then_lock_then_parent():
    engine = _engine()
    r = await _call(engine, "update_ui_element", {
        "element_id": "pin", "parent": "ghost", "bindings": "x", "layout_id": "zz",
        "grid_area": "1",
    })
    assert r["error"].startswith("Element 'pin': 'grid_area' was removed")
    r = await _call(engine, "update_ui_element", {
        "element_id": "pin", "parent": "ghost", "bindings": "x", "layout_id": "zz",
    })
    assert r["error"].startswith("Layout 'zz' not found")
    r = await _call(engine, "update_ui_element", {
        "element_id": "pin", "parent": "ghost", "bindings": "x",
    })
    assert r == {"error": "Element 'pin': 'bindings' must be an object, got str"}
    r = await _call(engine, "update_ui_element", {"element_id": "pin", "parent": "ghost"})
    assert r["error"].startswith("Element 'pin' is locked")
    r = await _call(engine, "update_ui_element", {"element_id": "btn", "parent": "ghost",
                                                  "hidden": True, "colour": 1})
    assert r == {"error": "Container 'ghost' is not an element on page 'main'"}
    r = await _call(engine, "update_ui_element", {"element_id": "btn", "hidden": True,
                                                  "colour": 1})
    assert r == {"error": "Element 'btn': 'colour' is not a field on a UI element."}


@pytest.mark.asyncio
async def test_element_and_master_name_the_other_door():
    engine = _engine()
    assert await _call(engine, "update_ui_element", {"element_id": "m_both", "label": "x"}) == {
        "error": "'m_both' is a master element, not a page element. "
                 "Use update_master_element to change it."}
    assert await _call(engine, "update_ui_element", {"element_id": "zz", "label": "x"}) == {
        "error": "UI element 'zz' not found"}
    assert await _call(engine, "update_master_element", {"element_id": "btn", "label": "x"}) == {
        "error": "'btn' is an element on page 'main', not a master. "
                 "Use update_ui_element to change it."}
    assert await _call(engine, "update_master_element", {"element_id": "zz", "label": "x"}) == {
        "error": "Master element 'zz' not found"}
    # The first page holding the id is the one an update reaches.
    r = await _call(engine, "update_ui_element", {"element_id": "dup", "label": "first"})
    assert r["changed"] == ["label"]
    assert _el(engine, "other", "dup").label == "first"
    assert _el(engine, "third", "dup").label is None


# --- add_ui_elements: a new control's box and container --------------------------


@pytest.mark.asyncio
async def test_add_elements_boxes_go_in_the_primary_and_containers_are_checked():
    engine = _engine()
    result = await _call(engine, "add_ui_elements", {"page_id": "main", "elements": [
        {"id": "g2", "type": "group", "parent": "outer",
         "placement": {"x": 0.55555, "y": 0, "w": 50, "h": 50}},
        {"id": "c2", "type": "button", "parent": "g2"},
    ]})
    assert result["element_ids"] == ["g2", "c2"]
    assert _boxes(engine, "main", "landscape")["g2"] == {"x": 0.5556, "y": 0.0, "w": 50.0, "h": 50.0}
    assert "c2" not in _boxes(engine, "main", "landscape")
    assert "g2" not in _boxes(engine, "main", "portrait")


@pytest.mark.asyncio
async def test_add_elements_container_refusals_say_whose_and_answer_for_all():
    engine = _engine()
    result = await _call(engine, "add_ui_elements", {"page_id": "main", "elements": [
        {"id": "a", "type": "button", "parent": "ghost"},
        {"id": "b", "type": "button", "parent": "btn"},
        {"id": "c", "type": "button", "parent": "c"},
        {"id": "btn", "type": "button"},
        {"id": "d", "type": "button"},
        {"id": "d", "type": "button"},
        {"id": "e", "type": "button", "parent": "b"},
    ]})
    assert result["errors"] == [
        "Element 'a': container 'ghost' is not an element on page 'main'",
        "Element 'b': 'btn' is a button, not a container -- only a group element can hold children",
        "Element 'c': container 'c' is not an element on page 'main'",
        "Element 'btn' already exists on page 'main'",
        "Element 'd' appears twice in this batch",
        "Element 'e': container 'b' is not an element on page 'main'",
    ]
    assert result["error"].startswith("6 elements could not be added: ")


@pytest.mark.asyncio
async def test_add_elements_a_non_object_element_raises():
    engine = _engine()
    with pytest.raises(AttributeError):
        await _call(engine, "add_ui_elements", {"page_id": "main", "elements": ["junk"]})


# --- delete_ui_elements ----------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_elements_refuses_every_locked_one_and_deletes_nothing():
    project = _project()
    project["ui"]["pages"][1]["elements"].append({"id": "a_pin", "type": "button", "locked": True})
    engine = _engine(project)
    before = _dump(engine)
    result = await _call(engine, "delete_ui_elements", {"element_ids": ["pin", "btn", "a_pin"]})
    assert result == {"error": "Locked, so nothing was deleted: 'a_pin', 'pin'. Unlock with "
                               "update_ui_element locked: false first."}
    assert _dump(engine) == before


@pytest.mark.asyncio
async def test_delete_elements_rehomes_children_to_the_nearest_survivor():
    engine = _engine()
    result = await _call(engine, "delete_ui_elements", {"element_ids": ["inner"]})
    assert result == {"status": "deleted", "element_ids": ["inner"]}
    assert _el(engine, "main", "kid").parent == "outer"
    assert _boxes(engine, "main", "landscape")["kid"] == {"x": 55.0, "y": 60.0, "w": 20.0, "h": 20.0}
    assert _boxes(engine, "main", "portrait")["kid"] == {"x": 50.0, "y": 50.0, "w": 50.0, "h": 25.0}
    assert "inner" not in _boxes(engine, "main", "landscape")


@pytest.mark.asyncio
async def test_delete_elements_a_whole_chain_sends_children_to_the_page():
    engine = _engine()
    result = await _call(engine, "delete_ui_elements", {"element_ids": ["outer", "inner", "lbl"]})
    assert result["element_ids"] == ["inner", "lbl", "outer"]
    assert _el(engine, "main", "kid").parent is None
    assert _boxes(engine, "main", "landscape")["kid"] == {
        "x": 27.5, "y": 30.0, "w": 10.0, "h": 10.0}
    assert _layout(engine, "main", "landscape").hidden == []
    assert set(_boxes(engine, "main", "landscape")) == {"kid", "btn", "pin"}
    assert _boxes(engine, "main", "portrait") == {
        "kid": {"x": 25.0, "y": 25.0, "w": 25.0, "h": 12.5}}


@pytest.mark.asyncio
async def test_delete_elements_a_child_climbs_past_every_doomed_container():
    project = _project()
    page = project["ui"]["pages"][0]
    page["elements"] += [
        {"id": "top", "type": "group"},
        {"id": "mid", "type": "group", "parent": "top"},
        {"id": "low", "type": "group", "parent": "mid"},
        {"id": "leaf", "type": "button", "parent": "low"},
    ]
    page["layouts"][0]["placements"].update({
        "top": {"x": 0, "y": 0, "w": 80, "h": 80},
        "mid": {"x": 0, "y": 0, "w": 50, "h": 50},
        "low": {"x": 0, "y": 0, "w": 50, "h": 50},
        "leaf": {"x": 20, "y": 20, "w": 50, "h": 50},
    })
    engine = _engine(project)
    await _call(engine, "delete_ui_elements", {"element_ids": ["low", "mid"]})
    assert _el(engine, "main", "leaf").parent == "top"
    assert _boxes(engine, "main", "landscape")["leaf"] == {"x": 5.0, "y": 5.0, "w": 12.5, "h": 12.5}


@pytest.mark.asyncio
async def test_delete_elements_an_id_on_two_pages_is_reported_twice():
    engine = _engine()
    result = await _call(engine, "delete_ui_elements", {"element_ids": ["dup", "ghost", "aaa"]})
    assert result == {
        "status": "deleted", "element_ids": ["dup", "dup"],
        "not_found": ["aaa", "ghost"],
        "warnings": [
            "No element with id 'aaa' on any page, so nothing was deleted for it.",
            "No element with id 'ghost' on any page, so nothing was deleted for it.",
        ],
    }
    assert _boxes(engine, "third", "landscape") == {}
    assert _layout(engine, "third", "landscape").hidden == []


@pytest.mark.asyncio
async def test_delete_elements_none_found():
    engine = _engine()
    assert await _call(engine, "delete_ui_elements", {"element_ids": ["ghost"]}) == {
        "error": "No matching elements found"}
    assert await _call(engine, "delete_ui_elements", {"element_ids": []}) == {
        "error": "No element_ids provided"}


# --- delete_ui_page ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_page_scrubs_what_pointed_at_it():
    engine = _engine()
    result = await _call(engine, "delete_ui_page", {"page_id": "other"})
    assert result == {"status": "deleted", "id": "other", "impact": {"elements_removed": 2}}
    ui = engine.project.ui
    assert [p.id for p in ui.pages] == ["main", "third"]
    assert {m.id: m.pages for m in ui.master_elements} == {
        "m_both": ["main"], "m_other": "*", "m_every": "*", "m_main": ["main"],
    }
    assert _el(engine, "main", "nav").target_page == ""
    assert _el(engine, "main", "nav_main").target_page == "main"
    assert _el(engine, "main", "go").bindings == {"do": {
        "press": [{"action": "macro", "macro": "mac"}],
        "double": {"action": "ui.navigate", "page": "main"},
        "change": [{"action": "ui.navigate", "page": "main"}],
    }}
    trigger = engine.project.macros[0].triggers[0]
    assert [(c.key, c.value) for c in trigger.conditions] == [
        ("system.current_page", "main"), ("var.mode", "other"),
    ]
    assert ui.settings.idle_page == "main"
    assert [(g.name, g.pages) for g in ui.page_groups] == [("Both", ["main"]), ("Main", ["main"])]


@pytest.mark.asyncio
async def test_delete_page_with_no_elements_and_the_idle_page_elsewhere():
    project = _project()
    project["ui"]["pages"][1]["elements"] = []
    project["ui"]["settings"]["idle_page"] = "third"
    engine = _engine(project)
    result = await _call(engine, "delete_ui_page", {"page_id": "other"})
    assert result == {"status": "deleted", "id": "other"}
    assert engine.project.ui.settings.idle_page == "third"


@pytest.mark.asyncio
async def test_delete_page_the_last_page_leaves_no_idle_page():
    project = _project()
    project["ui"]["pages"] = [project["ui"]["pages"][1]]
    project["ui"]["settings"]["idle_page"] = "other"
    engine = _engine(project)
    await _call(engine, "delete_ui_page", {"page_id": "other"})
    assert engine.project.ui.pages == []
    assert engine.project.ui.settings.idle_page == ""


@pytest.mark.asyncio
async def test_delete_page_not_found_changes_nothing():
    engine = _engine()
    before = _dump(engine)
    assert await _call(engine, "delete_ui_page", {"page_id": "ghost"}) == {
        "error": "UI page 'ghost' not found"}
    assert _dump(engine) == before


@pytest.mark.asyncio
async def test_delete_page_leaves_an_unrelated_binding_shape_alone():
    project = _project()
    go = project["ui"]["pages"][0]["elements"][-1]
    go["bindings"] = {"do": ["not", "a", "map"], "show": {"key": "var.x"}}
    project["ui"]["pages"][0]["elements"].append(
        {"id": "plain", "type": "button", "bindings": {"show": {"key": "var.y"}}})
    engine = _engine(project)
    snapshot = copy.deepcopy(go["bindings"])
    await _call(engine, "delete_ui_page", {"page_id": "other"})
    assert _el(engine, "main", "go").bindings == snapshot


@pytest.mark.asyncio
async def test_delete_page_leaves_what_the_builder_delete_leaves():
    """Every place a page can be named, cleaned the way the UI Builder's page
    delete cleans it (projectRewrites.test.ts reads the same fixture), so no
    action the page review walks still moves to the page."""
    case = json.loads(
        (Path(__file__).parent / "fixtures" / "page_delete_cases.json").read_text(encoding="utf-8")
    )
    before, after, gone = case["before"], case["after"], case["page_id"]
    engine = _engine({
        "project": {"id": "acme_room", "name": "Acme Room"},
        "macros": before["macros"],
        "ui": {"settings": {}, "page_groups": before["page_groups"],
               "master_elements": before["master_elements"], "pages": before["pages"]},
    })
    result = await _call(engine, "delete_ui_page", {"page_id": gone})
    assert result["status"] == "deleted"
    ui = engine.project.ui

    def subset(model, expected: dict) -> dict:
        dump = model.model_dump()
        return {k: dump.get(k) for k in expected}

    assert [p.id for p in ui.pages] == [p["id"] for p in after["pages"]]
    for page, expected in zip(ui.pages, after["pages"]):
        assert [subset(el, exp) for el, exp in zip(page.elements, expected["elements"])] \
            == expected["elements"]
    assert [subset(m, exp) for m, exp in zip(ui.master_elements, after["master_elements"])] \
        == after["master_elements"]
    macro, expected_macro = engine.project.macros[0], after["macros"][0]
    assert [s.model_dump(exclude_unset=True) for s in macro.steps] == expected_macro["steps"]
    assert [c.model_dump(exclude_unset=True) for c in macro.triggers[0].conditions] \
        == expected_macro["triggers"][0]["conditions"]
    assert [{"name": g.name, "pages": g.pages} for g in ui.page_groups] == after["page_groups"]

    controls = [el for page in ui.pages for el in page.elements] + list(ui.master_elements)
    left = [
        action for el in controls for action in do_action_dicts(el.model_dump())
        if action.get("action") == "ui.navigate" and action.get("page") == gone
    ]
    assert left == []
