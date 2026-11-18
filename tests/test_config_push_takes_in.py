"""A project pushed from the cloud, taken in as the Programmer's Save takes one.

The push replaces the running project. What it keeps of the project it
replaces (the copy that goes back to the cloud for a rollback, and the
backup) is taken under the project lock as the push is taken in, so an edit
saved a moment before the push still reaches both. It is read with the same
function Save reads a whole project with: migrated through the format chain,
repaired as an import is, validated, and refused when it carries no
``openavc_version``, which the chain would read as the oldest format.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from openavc.core.backup_manager import list_backups
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, save_project
from openavc.core.project_migration import CURRENT_VERSION

PUSH_BACKUP = "Before cloud config push"


def _project_dict() -> dict[str, Any]:
    return {"openavc_version": CURRENT_VERSION, "project": {"id": "hall", "name": "Hall"}}


@pytest.fixture
def engine(tmp_path, monkeypatch) -> Engine:
    eng = Engine(str(tmp_path / "project.avc"))
    eng.project = ProjectConfig(**_project_dict())
    save_project(eng.project_path, eng.project)
    monkeypatch.setattr(eng, "_reconcile", AsyncMock())
    eng.broadcast_ws = AsyncMock()
    return eng


def _handler(engine: Engine, sent: list[dict[str, Any]]):
    from openavc.cloud.command_handler import CommandHandler

    class Agent:
        async def send_message(self, msg_type, payload):
            sent.append(payload)

    return CommandHandler(
        Agent(), engine.devices, engine.events,
        apply_fn=engine.replace_project, project_path=str(engine.project_path),
    )


async def _push(engine: Engine, project_json: dict[str, Any]) -> dict[str, Any]:
    sent: list[dict[str, Any]] = []
    await _handler(engine, sent)._handle_config_push(
        {"project_json": project_json, "mode": "full_replace"}, "req-1", "tech@x.com",
    )
    return sent[-1]


def _pushed() -> dict[str, Any]:
    return {"openavc_version": CURRENT_VERSION, "project": {"id": "pushed", "name": "Pushed"}}


def _backups(engine: Engine, reason: str) -> list:
    return [b for b in list_backups(engine.project_path) if b.reason == reason]


async def _until(condition, what: str) -> None:
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


async def test_a_push_keeps_the_project_it_replaced_not_one_from_before_an_edit_saved_meanwhile(engine):
    """An edit lands while the push waits its turn: the rollback copy and
    the backup are the project the push replaced, that edit included."""
    sent: list[dict[str, Any]] = []
    handler = _handler(engine, sent)

    def rename(project):
        project.project.name = "Renamed"

    async with engine._reload_lock:
        push = asyncio.create_task(handler._handle_config_push(
            {"project_json": _pushed(), "mode": "full_replace"}, "req-1", "tech@x.com",
        ))
        # The push is waiting for the lock: an applied change raises this
        # from the moment it waits.
        await _until(lambda: engine._hook_runs > 0, "the push to wait for the lock")
        await engine._change_locked(rename, reconcile=False)
    await push

    answer = sent[-1]
    assert answer["success"] is True, answer
    assert answer["result"]["previous_project_json"]["project"]["name"] == "Renamed"
    assert [b.project_name for b in _backups(engine, PUSH_BACKUP)] == ["Renamed"]
    assert engine.project.project.name == "Pushed"


async def test_a_push_with_no_openavc_version_is_refused_and_changes_nothing(engine):
    before = engine.project_path.read_bytes()
    body = _pushed()
    del body["openavc_version"]

    answer = await _push(engine, body)

    assert answer["success"] is False
    assert answer["error"] == (
        "Not applied: the project has no openavc_version, so this system cannot tell "
        "which format it was saved in. Push the whole project as a system exports it, "
        "openavc_version included. The previous project is still running."
    )
    assert engine.project.project.name == "Hall"
    assert engine.project_path.read_bytes() == before
    assert _backups(engine, PUSH_BACKUP) == []


async def test_a_push_whose_rollback_copy_would_not_fit_is_refused_before_anything_changes(engine):
    """The copy travels back inside the answer, which must fit the cloud's
    message limit; refused first, so no system runs a push it cannot undo."""
    from openavc.cloud.protocol import MAX_EMBED_BYTES

    big = _project_dict()
    big["padding"] = "x" * (MAX_EMBED_BYTES + 1)
    engine.project_path.write_text(json.dumps(big), encoding="utf-8")
    before = engine.project_path.read_bytes()

    answer = await _push(engine, _pushed())

    assert answer["success"] is False
    assert answer["error"].startswith("rollback_snapshot_too_large: ")
    assert engine.project.project.name == "Hall"
    assert engine.project_path.read_bytes() == before
    assert _backups(engine, PUSH_BACKUP) == []


async def test_a_push_whose_backup_fails_is_not_applied(engine):
    import errno

    before = engine.project_path.read_bytes()

    def backup_fails(path, reason, **kwargs):
        raise OSError(errno.EIO, "Input/output error", str(path))

    with patch("openavc.core.backup_manager.create_backup", backup_fails):
        answer = await _push(engine, _pushed())

    assert answer["success"] is False
    assert answer["error"] == (
        "Not applied: the system could not prepare the change. The Programmer's "
        "Log has the details. The previous project is still running."
    )
    assert str(engine.project_path.parent) not in answer["error"]
    assert engine.project.project.name == "Hall"
    assert engine.project_path.read_bytes() == before


async def test_a_push_answers_the_project_it_replaced_and_runs_the_pushed_one(engine):
    answer = await _push(engine, _pushed())

    assert answer["success"] is True, answer
    assert answer["result"]["previous_project_json"]["project"]["name"] == "Hall"
    assert [b.project_name for b in _backups(engine, PUSH_BACKUP)] == ["Hall"]
    assert engine.project.project.name == "Pushed"
    saved = json.loads(engine.project_path.read_text(encoding="utf-8"))
    assert saved["project"]["name"] == "Pushed"


# --- The same read as Save ---------------------------------------------------


def _client(engine: Engine, monkeypatch) -> httpx.AsyncClient:
    from openavc.api.errors import (
        StructuredApiError,
        add_project_change_handler,
        structured_api_error_handler,
    )
    from openavc.api.routes import project as project_routes

    monkeypatch.setattr(project_routes, "_get_engine", lambda: engine)
    app = FastAPI()
    app.include_router(project_routes.router, prefix="/api")
    add_project_change_handler(app)
    app.add_exception_handler(StructuredApiError, structured_api_error_handler)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("mode", ["edit", "import"])
async def test_a_save_with_no_openavc_version_is_refused(engine, monkeypatch, mode):
    body = engine.project.model_dump(mode="json")
    del body["openavc_version"]
    before = engine.project_path.read_bytes()

    async with _client(engine, monkeypatch) as client:
        resp = await client.put(f"/api/project?mode={mode}", json=body)

    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        "Project body is missing 'openavc_version'. Send the whole "
        "document from GET /api/project, version field included."
    )
    assert engine.project_path.read_bytes() == before


async def test_save_and_a_push_read_a_whole_project_with_the_one_function(engine, monkeypatch):
    """Save and the push hand the document to the same reader, the push
    with the repair an import makes."""
    from openavc.core import sent_project

    read = []
    real = sent_project.read_sent_project

    def spy(body, *, repair):
        read.append(repair)
        return real(body, repair=repair)

    monkeypatch.setattr(sent_project, "read_sent_project", spy)
    async with _client(engine, monkeypatch) as client:
        body = engine.project.model_dump(mode="json")
        assert (await client.put("/api/project", json=body)).status_code == 200
        body = engine.project.model_dump(mode="json")
        assert (await client.put("/api/project?mode=import", json=body)).status_code == 200
    assert (await _push(engine, _pushed()))["success"] is True

    assert read == [False, True, True]
