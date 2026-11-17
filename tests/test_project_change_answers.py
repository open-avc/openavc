"""What every door that changes the project answers about the change.

A change the running system could not take is put back: the project before
it runs on and its file is not written. Every door says so in the same words,
"Not applied:" with why and that the previous project is still running: Save
in the Programmer, a section route (a script, the ISC key, a plugin's entry),
a reload, the cloud AI's edit tools and a cloud config push. Nobody is handed
a bare server error, and nobody off this system is handed the exception's own
text or a path on it. A change that runs with a part it could not start (a
device, a plugin, a script) is applied, and Save and the AI's tools name each
such part with the sentence its own surface shows.
"""

from __future__ import annotations

import ast
import errno
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from openavc.api.error_messages import describe_change_failure
from openavc.core.engine import Engine
from openavc.core.project_changes import ProjectNotAppliedError
from openavc.core.project_loader import ProjectConfig, ProjectSaveError, save_project
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.project_reconcile import FailedPart

#: Text that must never reach a reader: it stands for an exception's own
#: words and a path on this system.
PRIVATE = "acme-private-7731"

NOT_TAKEN = (
    "Not applied: the running system could not take the change. The "
    "Programmer's Log has the details. The previous project is still running."
)
DISK_FULL = (
    "Not applied: the project file could not be written. The disk is full. "
    "Free up disk space and try again. The previous project is still running."
)


def _reconcile_fails(engine: Engine, monkeypatch) -> None:
    async def reconcile(run):
        raise RuntimeError(f"stage blew up at /srv/{PRIVATE}")

    monkeypatch.setattr(engine, "_reconcile", reconcile)


def _write_fails(engine: Engine, monkeypatch) -> None:
    async def no_room(path, project):
        raise ProjectSaveError(OSError(errno.ENOSPC, "No space left on device",
                                       f"/srv/{PRIVATE}/project.avc"))

    monkeypatch.setattr("openavc.core.project_changes.save_project_async", no_room)


FAILURES = {"reconcile": (_reconcile_fails, NOT_TAKEN), "write": (_write_fails, DISK_FULL)}


def _project_dict() -> dict[str, Any]:
    return {
        "openavc_version": CURRENT_VERSION,
        "project": {"id": "hall", "name": "Hall"},
        "plugins": {"acme_tool": {"enabled": False, "config": {}}},
    }


@pytest.fixture
def engine(tmp_path, monkeypatch) -> Engine:
    eng = Engine(str(tmp_path / "project.avc"))
    eng.project = ProjectConfig(**_project_dict())
    save_project(eng.project_path, eng.project)
    monkeypatch.setattr(eng, "_reconcile", AsyncMock())
    eng.broadcast_ws = AsyncMock()
    return eng


def _client(engine: Engine, monkeypatch) -> httpx.AsyncClient:
    """The Programmer's routes for the doors below, with the server's own
    handlers for a change that did not run."""
    from openavc.api import _engine as engine_slot
    from openavc.api import plugins as plugin_routes
    from openavc.api.errors import (
        StructuredApiError,
        add_project_change_handler,
        structured_api_error_handler,
    )
    from openavc.api.routes import isc as isc_routes
    from openavc.api.routes import project as project_routes

    monkeypatch.setattr(engine_slot, "_engine", engine)
    monkeypatch.setattr(project_routes, "_get_engine", lambda: engine)
    monkeypatch.setattr(isc_routes, "_get_engine", lambda: engine)
    monkeypatch.setattr(plugin_routes, "_engine", engine)
    app = FastAPI()
    app.include_router(project_routes.router, prefix="/api")
    app.include_router(isc_routes.router, prefix="/api")
    app.include_router(plugin_routes.router)
    add_project_change_handler(app)
    app.add_exception_handler(StructuredApiError, structured_api_error_handler)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _renamed(engine: Engine) -> dict[str, Any]:
    body = engine.project.model_dump(mode="json")
    body["project"]["name"] = "Renamed"
    return body


# --- The doors, each answering the same failure -----------------------------


async def _http(engine: Engine, monkeypatch, door: str) -> httpx.Response:
    async with _client(engine, monkeypatch) as client:
        if door == "save":
            return await client.put("/api/project", json=_renamed(engine))
        if door == "isc_key":
            return await client.put("/api/isc/auth-key", json={"auth_key": "a-new-mesh-key"})
        if door == "plugin_entry":
            return await client.delete("/api/plugins/acme_tool/config")
    raise AssertionError(door)


HTTP_DOORS = ["save", "isc_key", "plugin_entry"]


@pytest.mark.parametrize("failure", list(FAILURES))
@pytest.mark.parametrize("door", HTTP_DOORS)
async def test_a_route_says_the_change_was_not_applied_and_why(engine, monkeypatch, door, failure):
    fail, says = FAILURES[failure]
    before = engine.project_path.read_bytes()
    fail(engine, monkeypatch)

    resp = await _http(engine, monkeypatch, door)

    assert resp.status_code == 500
    assert resp.json() == {"detail": says, "code": "not_applied"}
    assert PRIVATE not in resp.text
    assert engine.project.project.name == "Hall"
    assert "acme_tool" in engine.project.plugins
    assert engine.project_path.read_bytes() == before


async def test_a_reload_that_the_running_system_cannot_take_says_so(engine, monkeypatch):
    _reconcile_fails(engine, monkeypatch)
    async with _client(engine, monkeypatch) as client:
        resp = await client.post("/api/project/reload")
    assert resp.status_code == 500
    assert resp.json() == {"detail": NOT_TAKEN, "code": "not_applied"}


async def test_a_reload_over_the_socket_says_so_in_the_same_words(engine, monkeypatch):
    from openavc.api.ws import _handle_message

    class Socket:
        def __init__(self):
            self.sent: list[dict] = []

        async def send_json(self, data):
            self.sent.append(data)

    _reconcile_fails(engine, monkeypatch)
    socket = Socket()
    with patch("openavc.api._engine._engine", engine):
        await _handle_message(socket, {"type": "project.reload"}, "programmer")

    assert socket.sent[-1]["type"] == "error"
    assert socket.sent[-1]["message"] == NOT_TAKEN


def test_the_server_answers_every_change_that_did_not_run_in_its_words():
    from openavc.api.errors import project_change_failures, project_change_handler
    from openavc.main import app

    for failure in project_change_failures():
        assert app.exception_handlers[failure] is project_change_handler


async def test_a_refused_save_says_it_was_refused_not_that_another_session_saved(engine, monkeypatch):
    """A refusal and a revision conflict can share a status; the code
    beside the sentence tells them apart."""
    from openavc.core.installed_equipment import InstalledEquipmentError

    def refuse(current, proposed, *, reassigned=None):
        raise InstalledEquipmentError("This server's own display stays in the project.")

    monkeypatch.setattr(engine.installed_equipment, "validate_edit", refuse)
    resp = await _http(engine, monkeypatch, "save")
    assert resp.status_code == 409
    assert resp.json() == {
        "detail": "This server's own display stays in the project.", "code": "refused",
    }


async def test_a_save_against_a_moved_revision_says_conflict(engine, monkeypatch):
    async with _client(engine, monkeypatch) as client:
        resp = await client.put("/api/project", json=_renamed(engine), headers={"If-Match": '"stale"'})
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


def _ai(engine: Engine):
    from openavc.cloud.ai_tool_handler import AIToolHandler

    agent = MagicMock()
    agent.send_message = AsyncMock()
    handler = AIToolHandler(agent, engine.devices, engine.events)
    handler._get_engine = lambda: engine
    return handler, agent.send_message


async def _ai_edit(engine: Engine) -> dict[str, Any]:
    handler, send = _ai(engine)
    await handler._execute_tool(
        "r1", "add_variable", handler._tools["add_variable"],
        {"id": "room_mode", "type": "string", "default": "off"},
    )
    return send.await_args.args[1]


@pytest.mark.parametrize("failure", list(FAILURES))
async def test_the_ai_is_told_the_change_was_not_applied_and_why(engine, monkeypatch, failure):
    fail, says = FAILURES[failure]
    fail(engine, monkeypatch)

    payload = await _ai_edit(engine)

    assert payload["success"] is False
    assert payload["error"] == says
    assert PRIVATE not in json.dumps(payload)
    assert [v.id for v in engine.project.variables] == []


async def _push(engine: Engine, project_json: dict[str, Any]) -> dict[str, Any]:
    from openavc.cloud.command_handler import CommandHandler

    sent = []

    class Agent:
        async def send_message(self, msg_type, payload):
            sent.append(payload)

    handler = CommandHandler(
        Agent(), engine.devices, engine.events,
        apply_fn=engine.replace_project, project_path=str(engine.project_path),
    )
    await handler._handle_config_push(
        {"project_json": project_json, "mode": "full_replace"}, "req-1", "tech@x.com",
    )
    return sent[-1]


@pytest.mark.parametrize("failure", list(FAILURES))
async def test_a_config_push_the_system_cannot_take_says_so_in_the_same_words(engine, monkeypatch, failure):
    fail, says = FAILURES[failure]
    fail(engine, monkeypatch)

    payload = await _push(engine, _renamed(engine))

    assert payload["success"] is False
    assert payload["error"] == says
    assert PRIVATE not in json.dumps(payload)
    assert engine.project.project.name == "Hall"


async def test_a_pushed_project_that_is_not_valid_names_the_field_and_not_its_value(engine):
    body = _renamed(engine)
    body["project"]["name"] = {"secret": PRIVATE}

    payload = await _push(engine, body)

    assert payload["success"] is False
    assert payload["error"].startswith("Invalid project schema: project.name: ")
    assert PRIVATE not in json.dumps(payload)


@pytest.mark.parametrize("failure", list(FAILURES))
async def test_every_door_says_the_same_failure_in_the_same_words(tmp_path, monkeypatch, failure):
    """The step's own claim: one failure, one sentence, at every door."""
    fail, _says = FAILURES[failure]
    answers = {}
    for door in [*HTTP_DOORS, "ai", "push"]:
        eng = Engine(str(tmp_path / door / "project.avc"))
        eng.project_path.parent.mkdir()
        eng.project = ProjectConfig(**_project_dict())
        save_project(eng.project_path, eng.project)
        eng.broadcast_ws = AsyncMock()
        monkeypatch.setattr(eng, "_reconcile", AsyncMock())
        fail(eng, monkeypatch)
        if door == "ai":
            answers[door] = (await _ai_edit(eng))["error"]
        elif door == "push":
            answers[door] = (await _push(eng, _renamed(eng)))["error"]
        else:
            answers[door] = (await _http(eng, monkeypatch, door)).json()["detail"]
    assert len(set(answers.values())) == 1, answers


# --- What the one wording says --------------------------------------------


async def test_a_conflict_and_a_refusal_keep_their_own_sentence():
    from openavc.core.installed_equipment import InstalledEquipmentError
    from openavc.core.project_changes import ProjectRevisionConflictError
    from openavc.core.saved_values import UnknownWordError

    conflict = describe_change_failure(ProjectRevisionConflictError("Reload to see the latest."))
    assert (conflict.status, conflict.code, conflict.sentence) == (
        409, "conflict", "Reload to see the latest.")
    refused = describe_change_failure(InstalledEquipmentError("This server's display stays."))
    assert (refused.status, refused.code, refused.sentence) == (
        409, "refused", "This server's display stays.")
    unknown = describe_change_failure(UnknownWordError("Trigger type 'x' is not known."))
    assert (unknown.status, unknown.code) == (422, "refused")
    assert describe_change_failure(RuntimeError(PRIVATE)) is None


@pytest.mark.parametrize("cause,why", [
    (OSError(errno.EROFS, "Read-only file system", f"/srv/{PRIVATE}"),
     "the project file could not be written. The file system is read-only."),
    (PermissionError(errno.EACCES, "Permission denied", f"/srv/{PRIVATE}"),
     "the project file could not be written. Permission denied. Ensure the "
     "project directory has write permissions."),
    (OSError(errno.EIO, "Input/output error", f"/srv/{PRIVATE}"),
     "the project file could not be written. Check disk space and directory permissions."),
])
async def test_a_write_that_failed_says_why_without_the_path(cause, why):
    failure = describe_change_failure(ProjectNotAppliedError(ProjectSaveError(cause)))
    assert failure.sentence == f"Not applied: {why} The previous project is still running."


async def test_the_change_failure_is_the_cause_raised_from_the_apply(engine, monkeypatch):
    """The typed failure keeps what went wrong, for the log and the tests."""
    _reconcile_fails(engine, monkeypatch)
    with pytest.raises(ProjectNotAppliedError) as caught:
        await engine.apply_project(ProjectConfig(**_renamed(engine)))
    assert isinstance(caught.value.cause, RuntimeError)
    assert caught.value.__cause__ is caught.value.cause


async def test_a_refusal_before_the_change_is_taken_in_is_not_wrapped(engine):
    from openavc.core.saved_values import UnknownWordError

    body = _renamed(engine)
    body["macros"] = [{"id": "m", "name": "M", "steps": [], "triggers": [
        {"id": "t", "type": "a_word_from_later", "enabled": True},
    ]}]
    with pytest.raises(UnknownWordError):
        await engine.apply_project(ProjectConfig(**body))


# --- A change that runs with a part it could not start ---------------------


def _part_fails(engine: Engine, monkeypatch) -> None:
    async def reconcile(run):
        run.failed.append(FailedPart(
            "device", "proj1", "driver_missing",
            "Projector 1 cannot start: its driver is not installed.",
        ))

    monkeypatch.setattr(engine, "_reconcile", reconcile)


NOT_RUNNING = [{
    "kind": "device", "id": "proj1", "reason": "driver_missing",
    "detail": "Projector 1 cannot start: its driver is not installed.",
}]


async def test_save_names_each_part_the_change_could_not_start(engine, monkeypatch):
    _part_fails(engine, monkeypatch)
    resp = await _http(engine, monkeypatch, "save")
    assert resp.status_code == 200
    assert resp.json() == {"status": "saved", "not_running": NOT_RUNNING}
    assert engine.project.project.name == "Renamed"


async def test_a_save_that_runs_whole_names_nothing(engine, monkeypatch):
    resp = await _http(engine, monkeypatch, "save")
    assert resp.json() == {"status": "saved"}


async def test_the_ai_is_told_its_edit_ran_and_which_part_did_not_start(engine, monkeypatch):
    _part_fails(engine, monkeypatch)

    payload = await _ai_edit(engine)

    assert payload["success"] is True
    assert payload["result"]["not_running"] == NOT_RUNNING
    assert [v.id for v in engine.project.variables] == ["room_mode"]


async def test_an_ai_edit_that_runs_whole_names_nothing(engine):
    payload = await _ai_edit(engine)
    assert payload["success"] is True
    assert "not_running" not in payload["result"]


# --- No door words the failure itself ---------------------------------------

_APPLY_CALLS = {"apply_project", "apply_project_edit", "replace_project",
                "reassign_installed_equipment", "reload_project"}
_DOOR_TREES = ("api", "cloud")


def _broad_handlers_around_an_apply(source: str) -> list[int]:
    """Lines of each ``except Exception`` whose ``try`` awaits a project
    change directly, and so would answer the change's failure itself."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try):
            continue
        applies = any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr in _APPLY_CALLS
            for stmt in node.body for call in ast.walk(stmt)
        )
        for handler in node.handlers if applies else ():
            caught = handler.type
            if isinstance(caught, ast.Name) and caught.id in {"Exception", "BaseException"}:
                found.append(handler.lineno)
    return found


async def test_no_door_answers_a_failed_change_in_its_own_words():
    """A door that catches everything around a project change would answer
    a change that was not applied with its own sentence; only the one
    wording may. The two that must catch everything (the AI's dispatcher,
    the cloud's push) reach it through describe_change_failure."""
    root = Path(__file__).resolve().parents[1] / "openavc"
    allowed = {("cloud", "command_handler.py"), ("api", "ws.py")}
    offenders = {}
    for tree in _DOOR_TREES:
        for path in sorted((root / tree).rglob("*.py")):
            lines = _broad_handlers_around_an_apply(path.read_text(encoding="utf-8"))
            if lines and (tree, path.name) not in allowed:
                offenders[str(path.relative_to(root))] = lines
    assert offenders == {}
