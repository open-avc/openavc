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
    ProjectConfig,
    UIElement,
    UISettings,
    load_project,
)
from openavc.core.project_migration import CURRENT_VERSION
from tests.test_ui_tools_doors_pinned import _call, _el, _engine


def _settings(field: str, value):
    return UISettings(**{field: value})


def _element(field: str, value):
    return UIElement(id="readout", type="label", **{field: value})


def _monitor(field: str, value):
    return MonitorConfig(key="var.level", **{field: value})


#: (what builds it, the field, the lowest it takes, the highest it takes)
BOUNDS = [
    (_settings, "page_transition_duration", 50, 1000),
    (_settings, "element_stagger_ms", 10, 200),
    (_element, "display_decimals", 0, 20),
    (_monitor, "display_decimals", 0, 20),
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


def test_the_idle_timeout_takes_zero_and_a_long_one_and_refuses_a_negative():
    assert UISettings(idle_timeout_seconds=0).idle_timeout_seconds == 0
    assert UISettings(idle_timeout_seconds=3600).idle_timeout_seconds == 3600
    with pytest.raises(ValidationError):
        UISettings(idle_timeout_seconds=-1)


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

