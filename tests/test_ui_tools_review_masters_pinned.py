"""The AI's whole-project review and its master-element doors, pinned.

Each test records what ``review_ui``, ``add_master_element`` and
``update_master_element`` do today: the reply in full and in order, which
refusal wins when an input is wrong twice, the theme every master review is
measured against, and what the review leaves alone.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.project_loader import ProjectConfig

from tests.test_ui_tools_lookups_pinned import _Devices, _Plugins

#: A slider master measured against the project's own thumb size says 1120px.
BIG_THUMB = "slider thumb is 1120px"
SMALL = {"x": 0, "y": 0, "w": 1, "h": 1}


def _project(*, masters=None, pages=None, theme=True) -> dict:
    settings = {"theme_overrides": {
        "element_defaults": {"slider": {"thumb_size": 80}},
    }} if theme else {}
    return {
        "project": {"id": "acme_room", "name": "Acme Room"},
        "devices": [{"id": "amp", "driver": "acme_amp", "name": "Amplifier"}],
        "ui": {
            "settings": settings,
            "master_elements": masters if masters is not None else [
                {"id": "m_slide", "type": "slider", "pages": "*",
                 "placements": {"landscape": {"x": 0, "y": 0, "w": 30, "h": 5}}},
                {"id": "m_tiny", "type": "button", "pages": ["main"], "label": "Home",
                 "placements": {"landscape": SMALL}},
                {"id": "m_fine", "type": "button", "pages": "*", "label": "Fine",
                 "placements": {"landscape": {"x": 50, "y": 0, "w": 20, "h": 10}}},
            ],
            "pages": pages if pages is not None else [
                {"id": "main", "name": "Main", "elements": [
                    {"id": "lvl", "type": "slider", "min": 0, "max": 100,
                     "bindings": {"show": {"value": {"key": "device.amp.level"}}}},
                    {"id": "btn", "type": "button", "label": "Go"},
                    {"id": "gone", "type": "button", "label": "Gone",
                     "bindings": {"do": {"press": [{"action": "macro", "macro": "nope"}]}}},
                    {"id": "fill_me", "type": "slider",
                     "bindings": {"show": {"value": {"key": "device.amp.level"}}}},
                    {"id": "cover", "type": "button", "label": "Cover"},
                ], "layouts": [{"id": "landscape", "primary": True, "placements": {
                    "lvl": {"x": 10, "y": 30, "w": 60, "h": 20},
                    "btn": {"x": 10, "y": 60, "w": 20, "h": 15},
                    "gone": {"x": 40, "y": 60, "w": 20, "h": 15},
                    "fill_me": {"x": 10, "y": 80, "w": 60, "h": 15},
                    "cover": {"x": 45, "y": 0, "w": 30, "h": 15},
                }}]},
                {"id": "clean", "name": "Clean", "elements": [
                    {"id": "ok", "type": "button", "label": "OK"},
                ], "layouts": [{"id": "landscape", "primary": True, "placements": {
                    "ok": {"x": 10, "y": 30, "w": 20, "h": 15},
                }}]},
            ],
        },
    }


def _engine(project: dict | None = None):
    engine = MagicMock()
    engine.project = ProjectConfig.model_validate(project or _project())
    engine.project_path = None
    engine.plugins = _Plugins()

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
    agent = MagicMock()
    agent.state = MagicMock()
    handler = AIToolHandler(agent, _Devices(), MagicMock(emit=AsyncMock()))
    with patch.object(handler, "_get_engine", return_value=engine):
        return await getattr(handler, f"_{tool}")(payload)


def _master(engine, element_id):
    return next(m for m in engine.project.ui.master_elements if m.id == element_id)


# --- review_ui ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["review_ui", "add_master_element", "update_master_element"])
async def test_no_project_is_refused(tool):
    engine = MagicMock()
    engine.project = None
    assert await _call(engine, tool, {"id": "x", "element_id": "x"}) == {
        "error": "No project loaded",
    }
    assert await _call(None, tool, {"id": "x", "element_id": "x"}) == {
        "error": "No project loaded",
    }


@pytest.mark.asyncio
async def test_a_page_that_is_not_there_names_the_pages_that_are():
    assert await _call(_engine(), "review_ui", {"page_id": "nope"}) == {
        "error": "UI page 'nope' not found. The pages are: main, clean",
    }
    empty = _engine(_project(pages=[]))
    assert await _call(empty, "review_ui", {"page_id": "nope"}) == {
        "error": "UI page 'nope' not found. The pages are: (none)",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"page_id": ""}, {"page_id": None}])
async def test_the_whole_project_reply_in_full_and_in_order(payload):
    engine = _engine()
    before = engine.project.model_dump(mode="json")
    result = await _call(engine, "review_ui", payload)
    assert list(result) == [
        "pages_checked", "masters_checked", "finding_count", "pages",
        "master_elements", "status", "note",
    ]
    assert result["pages_checked"] == ["main", "clean"]
    assert result["masters_checked"] == 3
    assert list(result["pages"]) == ["main"]
    main = result["pages"]["main"]
    assert any("'nope'" in m for m in main), main
    assert any(m.startswith("lvl: max 100 is above the 6 the driver declares") for m in main), main
    assert any(m.startswith("cover (button) is drawn over the master element m_fine") for m in main), main
    assert any(m.startswith("lvl") and BIG_THUMB in m for m in main), main
    masters = result["master_elements"]
    assert [m.split(" ")[0] for m in masters] == ["m_slide", "m_slide", "m_tiny", "m_tiny"]
    assert BIG_THUMB in masters[0]
    assert result["finding_count"] == len(main) + len(masters)
    assert result["status"] == "findings"
    assert result["note"].startswith("The write landed -- these are warnings")
    # Read-only: the declared range is reported, never filled in.
    assert engine.project.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_one_page_is_reviewed_without_the_masters():
    result = await _call(_engine(), "review_ui", {"page_id": "main"})
    assert list(result) == [
        "pages_checked", "masters_checked", "finding_count", "pages", "status", "note",
    ]
    assert result["pages_checked"] == ["main"]
    assert result["masters_checked"] == 0
    assert result["finding_count"] == len(result["pages"]["main"])


@pytest.mark.asyncio
async def test_a_clean_page_in_full():
    assert await _call(_engine(), "review_ui", {"page_id": "clean"}) == {
        "pages_checked": ["clean"],
        "masters_checked": 0,
        "finding_count": 0,
        "status": "clean",
        "note": (
            "Nothing to report: every control has a box it fits in, no two collide, "
            "and every binding points at something that exists."
        ),
    }


@pytest.mark.asyncio
async def test_masters_alone_can_make_a_project_dirty():
    engine = _engine(_project(pages=[]))
    result = await _call(engine, "review_ui", {})
    assert list(result) == [
        "pages_checked", "masters_checked", "finding_count", "master_elements",
        "status", "note",
    ]
    assert result["pages_checked"] == []
    assert result["finding_count"] == 4
    assert result["status"] == "findings"
    assert result["note"].startswith("The write landed -- these are warnings")


@pytest.mark.asyncio
async def test_the_master_review_takes_the_projects_theme():
    with_theme = await _call(_engine(), "review_ui", {})
    without = await _call(_engine(_project(theme=False)), "review_ui", {})
    assert BIG_THUMB in with_theme["master_elements"][0]
    assert BIG_THUMB not in without["master_elements"][0]


# --- add_master_element ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"id": ""}, {"id": None}, {"id": 0}])
async def test_a_master_needs_an_id(payload):
    assert await _call(_engine(), "add_master_element", payload) == {
        "error": "Element ID is required",
    }


@pytest.mark.asyncio
async def test_a_new_master_reply_in_full_with_its_grant_and_warnings():
    engine = _engine()
    payload = {
        "id": "m_new", "type": "slider", "pages": "*",
        "placements": {"landscape": {"x": 1.00005, "y": 0, "w": 30, "h": 5},
                       "portrait": {"x": 0, "y": 0, "w": 30.00004, "h": 5}},
        "grant": {"devices": ["amp"]},
    }
    result = await _call(engine, "add_master_element", payload)
    assert list(result) == ["status", "id", "grant", "warnings", "warning_note"]
    assert result["status"] == "created" and result["id"] == "m_new"
    assert result["grant"]["before"] is None
    assert result["grant"]["after"]["devices"] == ["amp"]
    assert any(BIG_THUMB in w for w in result["warnings"])
    stored = _master(engine, "m_new")
    assert stored.placements["landscape"].x == 1.0001
    assert stored.placements["portrait"].w == 30.0
    # The caller's own placements are not rewritten.
    assert payload["placements"]["landscape"]["x"] == 1.00005


@pytest.mark.asyncio
async def test_a_clean_new_master_reply_in_full():
    assert await _call(_engine(), "add_master_element", {
        "id": "m_new", "type": "button", "label": "Fine", "pages": "*",
        "placements": {"landscape": {"x": 50, "y": 50, "w": 20, "h": 10}},
    }) == {"status": "created", "id": "m_new"}


@pytest.mark.asyncio
async def test_an_id_already_in_use_is_refused_after_the_definition():
    engine = _engine()
    assert await _call(engine, "add_master_element", {"id": "btn", "type": "button"}) == {
        "error": "Element 'btn' already exists on page 'main'",
    }
    assert await _call(engine, "add_master_element", {"id": "m_fine", "type": "button"}) == {
        "error": "Master element 'm_fine' already exists",
    }
    retired = await _call(engine, "add_master_element", {
        "id": "btn", "type": "button", "grid_area": "a",
    })
    assert retired["error"].startswith("Master element 'btn': 'grid_area' was removed")
    page_style = await _call(engine, "add_master_element", {
        "id": "m_fine", "type": "button", "placement": SMALL,
    })
    assert page_style["error"].startswith("Master element 'm_fine': use 'placements'")
    bad_bindings = await _call(engine, "add_master_element", {
        "id": "m_fine", "type": "button",
        "bindings": {"do": {"press": [{"action": "nonsense"}]}},
    })
    assert bad_bindings == {"error": "Master element 'm_fine' already exists"}


@pytest.mark.asyncio
@pytest.mark.parametrize("placements", [["landscape"], {"landscape": "x"}, "x"])
async def test_boxes_the_model_refuses_raise(placements):
    engine = _engine()
    with pytest.raises(ValidationError):
        await _call(engine, "add_master_element", {
            "id": "m_new", "type": "button", "placements": placements,
        })
    assert [m.id for m in engine.project.ui.master_elements] == ["m_slide", "m_tiny", "m_fine"]


# --- update_master_element ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"element_id": ""}, {"element_id": None}])
async def test_an_update_needs_an_element_id(payload):
    assert await _call(_engine(), "update_master_element", payload) == {
        "error": "element_id is required",
    }


@pytest.mark.asyncio
async def test_an_update_with_nothing_to_change_is_refused():
    engine = _engine()
    before = engine.project.model_dump(mode="json")
    assert await _call(engine, "update_master_element", {"element_id": "m_fine"}) == {
        "error": "No fields to update",
    }
    assert engine.project.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_an_update_names_what_landed_in_the_order_it_landed():
    engine = _engine()
    result = await _call(engine, "update_master_element", {
        "element_id": "m_slide",
        "label": "Vol",
        "placements": {"portrait": {"x": 0, "y": 0, "w": 30.00004, "h": 5}},
        "bindings": {"do": {"press": {"action": "macro", "macro": "nope"}}},
        "style": {"color": "red"},
        "grant": {"devices": ["amp"]},
    })
    assert list(result) == [
        "status", "element_id", "changed", "grant", "warnings", "warning_note",
    ]
    assert result["changed"] == ["bindings", "placements", "label", "style", "grant"]
    assert result["grant"] == {
        "before": None,
        "after": _master(engine, "m_slide").grant.model_dump(),
    }
    assert any(BIG_THUMB in w for w in result["warnings"])
    stored = _master(engine, "m_slide")
    assert stored.placements["landscape"].w == 30
    assert stored.placements["portrait"].w == 30.0
    assert stored.bindings["do"]["press"] == [{"action": "macro", "macro": "nope"}]


@pytest.mark.asyncio
async def test_a_grant_cleared_is_echoed_both_ways():
    engine = _engine()
    await _call(engine, "update_master_element", {
        "element_id": "m_fine", "grant": {"devices": ["amp"]},
    })
    result = await _call(engine, "update_master_element", {
        "element_id": "m_fine", "grant": None,
    })
    assert result == {
        "status": "updated", "element_id": "m_fine", "changed": ["grant"],
        "grant": {"before": {"devices": ["amp"], "variables": [], "macros": False,
                             "navigate": False},
                  "after": None},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, error", [
    ({"id": "x", "zzz": 1}, "Master element 'm_fine': an id cannot be changed here"),
    ({"zzz": 1, "id": "x"}, "Master element 'm_fine': 'zzz' is not a field on a UI element."),
    ({"label": "a", "grant": "amp"}, "Master element 'm_fine': 'grant' is not shaped right"),
])
async def test_a_plain_field_refusal_is_the_first_in_input_order(payload, error):
    engine = _engine()
    before = engine.project.model_dump(mode="json")
    result = await _call(engine, "update_master_element", {"element_id": "m_fine", **payload})
    assert result["error"].startswith(error), result
    assert engine.project.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_the_element_door_refuses_an_id_change_too():
    result = await _call(_engine(), "update_ui_element", {"element_id": "btn", "id": "x"})
    assert result == {"error": (
        "Element 'btn': an id cannot be changed here -- delete it and add it "
        "again under the new id."
    )}
