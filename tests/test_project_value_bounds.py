"""What a project value may hold, and every door that has to agree.

The project schema declares each bound on the field itself: a readout's
decimal places, a page transition's length, the stagger between elements, the
idle timeout. A value outside its bound is refused by the schema, so a project
saved before the bound existed is brought inside it by the format migration
when it loads (tests/test_project_migration.py has that step's own cases), and
a door that writes into the running project without going back through the
schema refuses the value itself rather than store one the next load refuses.
"""

from __future__ import annotations

import copy
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openavc.api.rest import router, set_engine
from openavc.core.project_loader import (
    MonitorConfig,
    OverlayConfig,
    Placement,
    ProjectConfig,
    UIElement,
    UISettings,
    load_project,
)
from openavc.core.project_migration import CURRENT_VERSION
from tests.test_ui_tools_doors_pinned import _call, _dump, _el, _engine


def _settings(field: str, value):
    return UISettings(**{field: value})


def _element(field: str, value):
    return UIElement(id="readout", type="label", **{field: value})


def _monitor(field: str, value):
    return MonitorConfig(key="var.level", **{field: value})


def _overlay(field: str, value):
    return OverlayConfig(**{field: value})


#: (what builds it, the field, the lowest it takes, the highest it takes)
BOUNDS = [
    (_settings, "page_transition_duration", 50, 1000),
    (_settings, "element_stagger_ms", 10, 200),
    (_element, "display_decimals", 0, 20),
    (_monitor, "display_decimals", 0, 20),
    (_overlay, "width", 5, 100),
    (_overlay, "height", 5, 100),
]


@pytest.mark.parametrize("build, field, low, high", BOUNDS)
def test_each_end_of_a_bound_is_taken(build, field, low, high):
    assert getattr(build(field, low), field) == low
    assert getattr(build(field, high), field) == high


@pytest.mark.parametrize("build, field, low, high", BOUNDS)
def test_one_past_either_end_is_refused(build, field, low, high):
    for value in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            build(field, value)


def test_the_idle_timeout_takes_zero_to_a_day():
    # Past about 24.8 days the panel's timer overflows and fires at once, so
    # the panel would go idle after every touch.
    assert UISettings(idle_timeout_seconds=0).idle_timeout_seconds == 0
    assert UISettings(idle_timeout_seconds=86400).idle_timeout_seconds == 86400
    for value in (-1, 86401, 3_000_000):
        with pytest.raises(ValidationError):
            UISettings(idle_timeout_seconds=value)


def test_the_defaults_are_inside_their_bounds():
    settings = UISettings()
    assert settings.page_transition_duration == 200
    assert settings.element_stagger_ms == 30
    assert settings.idle_timeout_seconds == 0
    # Unset decimals mean "as the control decides", not zero.
    assert UIElement(id="readout", type="label").display_decimals is None
    assert MonitorConfig(key="var.level").display_decimals is None


# --- an older project loads, brought inside the bounds ------------------------


def _older_project_out_of_bounds() -> dict:
    """A 0.16.0 project holding values the schema refuses today."""
    return {
        "openavc_version": "0.16.0",
        "project": {"id": "acme_room", "name": "Acme Room"},
        "monitors": [{"key": "var.level", "display_decimals": 40}],
        "ui": {
            "settings": {
                "page_transition": "fade",
                "page_transition_duration": 99999,
                "element_stagger_ms": -5,
                "idle_timeout_seconds": -5,
            },
            "pages": [{"id": "main", "name": "Main", "elements": [
                {"id": "level", "type": "slider", "display_decimals": 150},
                {"id": "readout", "type": "label", "display_decimals": 50},
            ]}],
        },
    }


def _assert_brought_inside(project: ProjectConfig) -> None:
    settings = project.ui.settings
    assert settings.page_transition_duration == 1000
    assert settings.element_stagger_ms == 10
    assert settings.idle_timeout_seconds == 0
    elements = {el.id: el for el in project.ui.pages[0].elements}
    assert elements["level"].display_decimals == 20
    assert elements["readout"].display_decimals == 20
    assert project.monitors[0].display_decimals == 20


def test_an_older_project_file_loads_with_its_values_brought_inside(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_older_project_out_of_bounds()), encoding="utf-8")

    project = load_project(path)

    _assert_brought_inside(project)
    # The migrated file is written back, so the next load finds it in bounds.
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["openavc_version"] == CURRENT_VERSION
    assert written["ui"]["settings"]["page_transition_duration"] == 1000
    ProjectConfig(**written)


@pytest.fixture
def client(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "project.avc").write_text("{}", encoding="utf-8")
    engine = MagicMock()
    engine.project_path = project_dir / "project.avc"
    engine._project_revision = 0
    engine.apply_project = AsyncMock(return_value=1)
    engine.broadcast_ws = AsyncMock()
    app = FastAPI()
    app.include_router(router)
    set_engine(engine)
    yield TestClient(app, raise_server_exceptions=False), engine
    set_engine(None)


def test_an_older_project_saved_through_the_api_is_brought_inside(client):
    http, engine = client
    resp = http.put("/api/project", json=_older_project_out_of_bounds())
    assert resp.status_code == 200, resp.text
    _assert_brought_inside(engine.apply_project.call_args.args[0])


def test_a_current_project_with_a_value_out_of_bounds_is_refused_by_name(client):
    http, engine = client
    body = _older_project_out_of_bounds()
    body["openavc_version"] = CURRENT_VERSION
    resp = http.put("/api/project", json=body)
    assert resp.status_code == 422
    assert "page_transition_duration" in resp.text
    engine.apply_project.assert_not_called()


# --- the AI's element doors refuse rather than store --------------------------


@pytest.mark.asyncio
async def test_an_ai_element_update_past_a_bound_is_refused_and_changes_nothing():
    engine = _engine()
    before = copy.deepcopy(_el(engine, "main", "lbl").model_dump())

    result = await _call(engine, "update_ui_element", {
        "element_id": "lbl", "display_decimals": 500,
    })

    assert "error" in result
    assert "display_decimals" in result["error"]
    assert _el(engine, "main", "lbl").model_dump() == before


@pytest.mark.asyncio
async def test_an_ai_element_update_inside_the_bound_is_taken():
    engine = _engine()
    result = await _call(engine, "update_ui_element", {
        "element_id": "lbl", "display_decimals": 20,
    })
    assert result.get("status") == "updated", result
    assert _el(engine, "main", "lbl").display_decimals == 20


@pytest.mark.asyncio
async def test_an_ai_master_update_past_a_bound_is_refused():
    engine = _engine()
    result = await _call(engine, "update_master_element", {
        "element_id": "m_both", "display_decimals": -1,
    })
    assert "error" in result
    assert "display_decimals" in result["error"]
    master = next(m for m in engine.project.ui.master_elements if m.id == "m_both")
    assert master.display_decimals is None


@pytest.mark.asyncio
async def test_an_ai_element_created_past_a_bound_is_refused_by_name():
    engine = _engine()
    result = await _call(engine, "add_ui_elements", {
        "page_id": "main",
        "elements": [{"id": "fresh", "type": "label", "display_decimals": 500}],
    })
    assert "display_decimals" in json.dumps(result)
    assert all(el.id != "fresh" for el in engine.project.ui.pages[0].elements)



# --- a box's sides are finite numbers ------------------------------------------
#
# A side the schema read as no finite number was written to the file as a bare
# `NaN` or `Infinity`, which no browser reads: the panel showed none of the
# project and the Programmer could not open it.


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "NaN", "1e999"])
def test_a_side_that_is_no_finite_number_is_refused(value):
    for side in ("x", "y", "w", "h"):
        with pytest.raises(ValidationError):
            Placement(**{side: value})


def test_a_negative_or_zero_side_is_taken():
    # A container's child may sit past its edge on purpose; a negative or zero
    # size is Validate's to report.
    box = Placement(x=-10, y=-5, w=0, h=-20)
    assert (box.x, box.y, box.w, box.h) == (-10, -5, 0, -20)


def _older_project_with_a_nan_side(version: str = "0.16.0") -> str:
    """A project file as a release wrote one: the side a bare NaN token."""
    project = {
        "openavc_version": version,
        "project": {"id": "acme_room", "name": "Acme Room"},
        "ui": {"pages": [{
            "id": "main", "name": "Main",
            "elements": [{"id": "power", "type": "button"}],
            "layouts": [{"id": "landscape", "orientation": "landscape", "primary": True,
                         "placements": {"power": {"x": 10, "y": 10, "w": "__NAN__", "h": 20}}}],
        }]},
    }
    return json.dumps(project).replace('"__NAN__"', "NaN")


def test_an_older_project_file_with_a_nan_side_loads_and_is_written_back_readable(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(_older_project_with_a_nan_side(), encoding="utf-8")

    project = load_project(path)

    box = project.ui.pages[0].layouts[0].placements["power"]
    assert (box.x, box.y, box.w, box.h) == (10, 10, 100, 20)
    written = path.read_text(encoding="utf-8")
    assert "NaN" not in written
    json.loads(written, parse_constant=lambda token: pytest.fail(f"{token} written back"))


def test_an_older_project_with_a_nan_side_saved_through_the_api_loads(client):
    http, engine = client
    resp = http.put(
        "/api/project", content=_older_project_with_a_nan_side(),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    box = engine.apply_project.call_args.args[0].ui.pages[0].layouts[0].placements["power"]
    assert box.w == 100


def test_a_current_project_with_a_nan_side_is_refused_by_name(client):
    http, engine = client
    resp = http.put(
        "/api/project", content=_older_project_with_a_nan_side(CURRENT_VERSION),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422, resp.text
    assert "placements" in resp.json()["detail"]
    assert "finite" in resp.json()["detail"]
    engine.apply_project.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), "NaN"])
async def test_an_ai_box_that_is_no_finite_number_is_refused_by_the_schema(value):
    """Refused as the schema refuses it, whichever form it came in, so the
    assistant is told which side; nothing changes."""
    engine = _engine()
    before = _dump(engine)
    with pytest.raises(ValidationError, match="finite number"):
        await _call(engine, "update_ui_element", {
            "element_id": "lbl", "placement": {"w": value},
        })
    assert _dump(engine) == before


# --- an overlay's or sidebar's box ----------------------------------------------


@pytest.mark.parametrize("field", ["width", "height"])
def test_an_overlay_box_that_is_no_finite_number_is_refused_and_a_blank_one_taken(field):
    for value in (float("nan"), float("inf"), "NaN"):
        with pytest.raises(ValidationError):
            OverlayConfig(**{field: value})
    assert getattr(OverlayConfig(), field) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field, value", [
    ("width", 0), ("width", -10), ("height", 150), ("width", float("nan")), ("height", "tall"),
])
async def test_an_ai_page_update_with_an_overlay_box_outside_its_range_is_refused_in_words(field, value):
    engine = _engine()
    before = _dump(engine)
    result = await _call(engine, "update_ui_page", {
        "page_id": "other", "name": "Renamed", "overlay": {field: value, "position": "top"},
    })
    assert result == {"error": (
        f"Page 'other': overlay {field} is a percentage of the screen, from 5 to 100, "
        f"not {value!r}. Leave it out for the default size."
    )}
    assert _dump(engine) == before


@pytest.mark.asyncio
async def test_an_ai_page_created_with_an_overlay_box_outside_its_range_is_refused_in_words():
    engine = _engine()
    result = await _call(engine, "add_ui_page", {
        "id": "dialog", "page_type": "overlay", "overlay": {"width": 40, "height": 0},
    })
    assert result == {"error": (
        "Page 'dialog': overlay height is a percentage of the screen, from 5 to 100, "
        "not 0. Leave it out for the default size."
    )}
    assert all(p.id != "dialog" for p in engine.project.ui.pages)


@pytest.mark.asyncio
async def test_an_ai_overlay_box_at_either_end_of_its_range_is_taken():
    engine = _engine()
    result = await _call(engine, "add_ui_page", {
        "id": "dialog", "page_type": "overlay", "overlay": {"width": 5, "height": 100},
    })
    assert result.get("status") == "created", result
    page = next(p for p in engine.project.ui.pages if p.id == "dialog")
    assert (page.overlay.width, page.overlay.height) == (5, 100)
    result = await _call(engine, "update_ui_page", {
        "page_id": "dialog", "overlay": {"width": 100, "height": 5},
    })
    assert result.get("changed") == ["overlay"], result
