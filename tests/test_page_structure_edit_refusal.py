"""An edit to the running project is refused when it adds to the pages what
they may not hold (``page_edits.issues_an_edit_makes``, asked by the
take-in for every edit: a save from the Programmer, an AI tool).

What the edit is judged on is the page structure: an id used twice or not
spelled as one, a page id, an arrangement, a container. An issue of a kind the
running project already holds as many of is not the edit's, so a project that
holds one still takes every other edit, a rename of its page included. A
binding the Builder is still filling in is never refused: Validate reports it.
An import, like a load, never refuses: it repairs and reports.
"""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, save_project
from openavc.core.project_migration import CURRENT_VERSION
from openavc.ui import page_edits as pe
from openavc.ui import page_structure as ps


def _project() -> dict:
    return {
        "project": {"id": "acme_room", "name": "Acme Room"},
        "ui": {
            "master_elements": [{"id": "clock", "type": "label", "pages": "*"}],
            "pages": [
                {
                    "id": "main", "name": "Main",
                    "elements": [
                        {"id": "box", "type": "group"},
                        {"id": "kid", "type": "button", "parent": "box"},
                        {"id": "power", "type": "button"},
                    ],
                    "layouts": [
                        {"id": "landscape", "primary": True},
                        {"id": "portrait", "orientation": "portrait", "inherits": "landscape"},
                    ],
                },
                {"id": "other", "name": "Other", "elements": [{"id": "back", "type": "button"}]},
            ],
        },
    }


def _main(project: dict) -> dict:
    return project["ui"]["pages"][0]


def _element(project: dict, element_id: str) -> dict:
    return next(el for page in project["ui"]["pages"] for el in page["elements"] if el["id"] == element_id)


def _parent(element_id: str, parent):
    def change(project: dict) -> None:
        _element(project, element_id)["parent"] = parent
    return change


def _add(element: dict, page: int = 0):
    def change(project: dict) -> None:
        project["ui"]["pages"][page]["elements"].append(element)
    return change


def _second_portrait(project: dict) -> None:
    _main(project)["layouts"].append({"id": "tall", "orientation": "portrait", "inherits": "landscape"})


def _inherits_nothing(project: dict) -> None:
    _main(project)["layouts"][1]["inherits"] = "gone"


def _master_parent(project: dict) -> None:
    project["ui"]["master_elements"][0]["parent"] = "box"


def _dollar_page(project: dict) -> None:
    project["ui"]["pages"].append({"id": "$back", "name": "Back", "elements": []})


#: Each edit that adds one structure issue, and the code it is refused under.
MADE = {
    "a container naming nothing": (_parent("power", "nobody"), ps.PARENT_MISSING),
    "a container that is a button": (_parent("kid", "power"), ps.PARENT_NOT_CONTAINER),
    "a group in a loop": (_add({"id": "ring", "type": "group", "parent": "ring"}), ps.PARENT_LOOP),
    "a master's container": (_master_parent, ps.MASTER_PARENT),
    "an id taken": (_add({"id": "power", "type": "label"}, page=1), ps.ELEMENT_ID_TAKEN),
    "an id not spelled as one": (_add({"id": "Big Button", "type": "button"}), ps.ELEMENT_ID_SPELLED),
    "a page id starting with $": (_dollar_page, ps.PAGE_ID_NAMES_NO_PAGE),
    "a second arrangement for one orientation": (_second_portrait, ps.ORIENTATION_TAKEN),
    "an inherits naming nothing": (_inherits_nothing, ps.INHERITS_MISSING),
}


def _made(change) -> list[ps.Issue]:
    before = ProjectConfig.model_validate(_project())
    after = _project()
    change(after)
    return pe.issues_an_edit_makes(before, ProjectConfig.model_validate(after))


@pytest.mark.parametrize("case", list(MADE))
def test_an_edit_that_adds_a_structure_issue_makes_it(case):
    change, code = MADE[case]
    assert [issue.code for issue in _made(change)] == [code]


def test_an_edit_that_adds_nothing_makes_nothing():
    assert _made(lambda project: project["project"].update(name="Renamed")) == []


def _holding(project: dict) -> dict:
    """``project`` with a structure issue of each report-only kind already in it."""
    _main(project)["elements"].append({"id": "power", "type": "label"})
    _main(project)["layouts"].append({"id": "portrait", "orientation": "portrait"})
    project["ui"]["pages"].append({"id": "$help", "name": "Help", "elements": [{"id": "Odd", "type": "button"}]})
    return project


def _made_from_holding(change) -> list[ps.Issue]:
    before = ProjectConfig.model_validate(_holding(_project()))
    after = _holding(_project())
    change(after)
    return pe.issues_an_edit_makes(before, ProjectConfig.model_validate(after))


def test_a_project_holding_issues_takes_an_edit_that_adds_none():
    assert _made_from_holding(_add({"id": "mute", "type": "button"})) == []


def test_moving_or_renaming_what_holds_an_issue_makes_none():
    def move_and_rename(project: dict) -> None:
        help_page = project["ui"]["pages"][2]
        odd = help_page["elements"].pop()
        project["ui"]["pages"][1]["elements"].append(odd)
        help_page["id"] = "$assist"
        _main(project)["id"] = "home"

    assert _made_from_holding(move_and_rename) == []


def test_another_of_a_kind_the_project_holds_is_made_and_named():
    made = _made_from_holding(_add({"id": "power", "type": "group"}, page=1))
    assert [(issue.code, issue.page, issue.element) for issue in made] == [(ps.ELEMENT_ID_TAKEN, "other", "power")]


@pytest.mark.parametrize("action", [
    {"action": "device.command", "device": "projector", "command": ""},
    {"action": ""},
    {"action": "launch_rocket"},
])
def test_a_binding_being_filled_in_is_never_refused(action):
    def half_made(project: dict) -> None:
        _element(project, "power")["bindings"] = {"do": {"press": [action]}, "show": {"value": {}}}

    assert _made(half_made) == []


# --- the doors -----------------------------------------------------------------


async def _edit_rig(tmp_path, monkeypatch, project: dict):
    from openavc.api.errors import add_project_change_handler
    from openavc.api.routes import project as routes

    engine = Engine(str(tmp_path / "project.avc"))
    engine.project = ProjectConfig(**project)
    save_project(engine.project_path, engine.project)
    monkeypatch.setattr(engine, "_reconcile", AsyncMock())
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    add_project_change_handler(app)
    return engine, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _with_loop(body: dict) -> dict:
    body = copy.deepcopy(body)
    pages = body["ui"]["pages"]
    pages[0]["elements"].append({"id": "ring", "type": "group", "parent": "ring"})
    return body


@pytest.mark.asyncio
async def test_a_save_that_adds_a_loop_is_refused_and_the_project_is_as_it_was(tmp_path, monkeypatch):
    engine, client = await _edit_rig(tmp_path, monkeypatch, _project())
    async with client:
        resp = await client.put("/api/project", json=_with_loop(engine.project.model_dump(mode="json")))
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Element 'ring' cannot be placed inside itself or its own contents"
    assert resp.json()["code"] == "refused"
    assert [el.id for el in engine.project.ui.pages[0].elements] == ["box", "kid", "power"]


@pytest.mark.asyncio
async def test_a_save_on_a_project_holding_an_issue_takes_another_edit(tmp_path, monkeypatch):
    engine, client = await _edit_rig(tmp_path, monkeypatch, _holding(_project()))
    body = engine.project.model_dump(mode="json")
    body["project"]["name"] = "Renamed"
    async with client:
        resp = await client.put("/api/project", json=body)
    assert resp.status_code == 200, resp.text
    assert engine.project.project.name == "Renamed"


@pytest.mark.asyncio
async def test_an_import_holding_a_loop_is_taken_with_its_controls_on_the_page(tmp_path, monkeypatch):
    engine, client = await _edit_rig(tmp_path, monkeypatch, _project())
    async with client:
        resp = await client.put("/api/project?mode=import", json={
            "openavc_version": CURRENT_VERSION, **_with_loop(_project()),
        })
        assert resp.status_code == 200, resp.text
        notes = (await client.get("/api/project/update-notice")).json()["notes"]
    ring = next(el for el in engine.project.ui.pages[0].elements if el.id == "ring")
    assert ring.parent is None
    assert [n["element_id"] for n in notes] == ["ring"]


@pytest.mark.asyncio
async def test_an_ai_edit_that_adds_a_masters_container_is_refused_in_its_sentence(tmp_path, monkeypatch):
    from openavc.cloud.tools import apply_tool_edit

    engine, _ = await _edit_rig(tmp_path, monkeypatch, _project())

    def mutate(project):
        project.ui.master_elements[0].parent = "box"

    result = await apply_tool_edit(engine, mutate)

    assert result == {"error": ps.master_parent_issue("clock", "box").message}
    assert engine.project.ui.master_elements[0].parent is None


@pytest.mark.asyncio
async def test_a_change_that_leaves_the_pages_as_they_were_does_not_read_them(tmp_path, monkeypatch):
    engine, _ = await _edit_rig(tmp_path, monkeypatch, _holding(_project()))
    monkeypatch.setattr(ps, "page_issues", lambda *a, **k: pytest.fail("the pages were read"))

    def mutate(project):
        project.project.name = "Renamed"

    await engine.apply_project_edit(mutate)
    assert engine.project.project.name == "Renamed"


def test_the_server_answers_a_page_structure_refusal_with_a_422():
    from openavc.api.errors import project_change_handler
    from openavc.main import app

    assert app.exception_handlers[pe.PageIssueError] is project_change_handler
