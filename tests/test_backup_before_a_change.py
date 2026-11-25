"""The backup a door takes before it changes the project, and what the door
does when that backup cannot be saved.

A door that replaces the project (an import, opening a copy from the
library, a blank project, a restore) refuses, and the project file, its
scripts and its files stay as they were. The cloud AI's edits go on, and the
tool's answer says no backup was saved. Create Backup says why it could not
save one. (The cloud push's refusal is in ``test_config_push_takes_in``.) Each failure here is the disk running out of room part way through
the backup, below the backup function, so what is proved is what a full disk
does rather than what a stand-in raises.
"""

from __future__ import annotations

import errno
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from openavc.core import backup_manager
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, save_project
from openavc.core.project_migration import CURRENT_VERSION

#: What every door that replaces the project answers when its backup could
#: not be saved because the disk is full.
NOT_APPLIED = (
    "Not applied: a backup of the current project could not be saved first. "
    "The disk is full. Free up disk space and try again. The previous project is still running."
)


def _run_out_of_room(monkeypatch) -> None:
    """Every backup from here on fails part way, as on a full disk."""
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(backup_manager, "_write_archive", full)


def _backups(engine: Engine) -> list[str]:
    folder = engine.project_path.parent / "backups"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


@pytest.fixture
def engine(tmp_path, monkeypatch) -> Engine:
    eng = Engine(str(tmp_path / "project.avc"))
    eng.project = ProjectConfig(project={"id": "hall", "name": "Hall"})
    save_project(eng.project_path, eng.project)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "main.py").write_text("# the hall's script\n", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"\x89PNG-hall")
    monkeypatch.setattr(eng, "_reconcile", AsyncMock())
    eng.broadcast_ws = AsyncMock()
    return eng


def _client(engine: Engine, monkeypatch) -> httpx.AsyncClient:
    """The project routes with the server's own handlers for a change that
    did not run."""
    from openavc.api import _engine as engine_slot
    from openavc.api.errors import (
        StructuredApiError,
        add_project_change_handler,
        structured_api_error_handler,
    )
    from openavc.api.routes import project as project_routes

    monkeypatch.setattr(engine_slot, "_engine", engine)
    monkeypatch.setattr(project_routes, "_get_engine", lambda: engine)
    app = FastAPI()
    app.include_router(project_routes.router, prefix="/api")
    add_project_change_handler(app)
    app.add_exception_handler(StructuredApiError, structured_api_error_handler)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _left_as_it_was(engine: Engine, before: bytes) -> None:
    folder = engine.project_path.parent
    assert engine.project_path.read_bytes() == before
    assert engine.project.project.name == "Hall"
    assert (folder / "scripts" / "main.py").read_text(encoding="utf-8") == "# the hall's script\n"
    assert (folder / "assets" / "logo.png").read_bytes() == b"\x89PNG-hall"


# --- The capture itself ---------------------------------------------------------


async def test_a_capture_with_no_project_file_has_nothing_to_keep(tmp_path):
    assert await backup_manager.capture(tmp_path / "project.avc", "Before restore", kind=backup_manager.KIND_REPLACE) is None


async def test_a_capture_that_cannot_be_saved_says_why_and_leaves_no_part_of_it(engine, monkeypatch):
    _run_out_of_room(monkeypatch)

    with pytest.raises(backup_manager.BackupFailed) as failed:
        await backup_manager.capture(engine.project_path, "Before restore", kind=backup_manager.KIND_REPLACE)

    assert failed.value.cause.errno == errno.ENOSPC
    assert _backups(engine) == []


async def test_a_capture_names_its_backup(engine):
    taken = await backup_manager.capture(engine.project_path, "Before restore", kind=backup_manager.KIND_REPLACE)

    assert taken is not None and taken.is_file()
    assert [b.reason for b in backup_manager.list_backups(engine.project_path)] == ["Before restore"]


async def test_a_capture_says_what_kind_of_backup_it_is():
    """Which backups the start reads when the project file is missing is
    decided by the kind each door writes, so no door can leave it out."""
    import inspect

    assert inspect.signature(backup_manager.capture).parameters["kind"].default is inspect.Parameter.empty


async def test_each_door_writes_its_kind(engine, monkeypatch):
    """The periodic backup, Create Backup (whatever the person names it) and
    a door that replaces the project each say what their backup is."""
    from openavc.core.project_replace import blank

    await engine._take_periodic_backup()
    async with _client(engine, monkeypatch) as client:
        made = await client.post("/api/backups/create", json={"reason": "Before restore"})
    assert made.status_code == 200
    await engine.replace_with(blank(ProjectConfig(project={"id": "lobby", "name": "Lobby"})))

    kinds = sorted((b.reason, b.kind) for b in backup_manager.list_backups(engine.project_path))
    assert kinds == [
        ("Auto-backup", backup_manager.KIND_AUTO),
        ("Before creating blank project", backup_manager.KIND_REPLACE),
        ("Before restore", backup_manager.KIND_MANUAL),
    ]


# --- The doors that replace the project refuse ----------------------------------


async def test_an_import_whose_backup_fails_is_not_applied(engine, monkeypatch):
    before = engine.project_path.read_bytes()
    _run_out_of_room(monkeypatch)

    async with _client(engine, monkeypatch) as client:
        resp = await client.put(
            "/api/project?mode=import",
            json={"openavc_version": CURRENT_VERSION, "project": {"id": "next", "name": "Next"}},
        )

    assert resp.status_code == 500
    assert resp.json() == {"detail": NOT_APPLIED, "code": "not_applied"}
    _left_as_it_was(engine, before)


async def test_opening_a_library_copy_whose_backup_fails_is_not_applied(engine, tmp_path, monkeypatch):
    import openavc.core.project_library as library

    monkeypatch.setattr(library, "_lib_dir", lambda: tmp_path / "library")
    saved = library.import_project(
        json.dumps({"project": {"id": "lobby", "name": "Lobby"}}).encode(), "lobby.avc", "lobby",
    )
    before = engine.project_path.read_bytes()
    _run_out_of_room(monkeypatch)

    async with _client(engine, monkeypatch) as client:
        resp = await client.post(
            "/api/project/open-from-library",
            json={"library_id": saved["id"], "project_name": "Lobby", "project_id": "lobby"},
        )

    assert resp.status_code == 500
    assert resp.json() == {"detail": NOT_APPLIED, "code": "not_applied"}
    _left_as_it_was(engine, before)


async def test_a_blank_project_whose_backup_fails_is_not_applied(engine, monkeypatch):
    before = engine.project_path.read_bytes()
    _run_out_of_room(monkeypatch)

    async with _client(engine, monkeypatch) as client:
        resp = await client.post("/api/project/create-blank", json={"project_name": "Blank"})

    assert resp.status_code == 500
    assert resp.json() == {"detail": NOT_APPLIED, "code": "not_applied"}
    _left_as_it_was(engine, before)


async def test_a_restore_whose_backup_fails_is_not_applied(engine, monkeypatch):
    kept = backup_manager.create_backup(engine.project_path, "Manual backup")
    # Written since the backup: a restore would remove it.
    added = engine.project_path.parent / "scripts" / "added.py"
    added.write_text("# added since\n", encoding="utf-8")
    before = engine.project_path.read_bytes()
    engine.persister = MagicMock()
    _run_out_of_room(monkeypatch)

    async with _client(engine, monkeypatch) as client:
        resp = await client.post(f"/api/backups/{backup_manager.backup_id(engine.project_path.parent, kept)}/restore")

    assert resp.status_code == 500
    assert resp.json() == {"detail": NOT_APPLIED, "code": "not_applied"}
    _left_as_it_was(engine, before)
    assert added.is_file()
    engine.persister.stop.assert_not_called()


async def test_create_backup_says_why_it_could_not_save_one(engine, monkeypatch):
    _run_out_of_room(monkeypatch)

    async with _client(engine, monkeypatch) as client:
        resp = await client.post("/api/backups/create", json={"reason": "Manual backup"})

    assert resp.status_code == 500
    assert resp.json()["detail"] == "The backup could not be saved. The disk is full. Free up disk space and try again."
    assert _backups(engine) == []


# --- The cloud AI's edits go on and say so --------------------------------------

#: What an AI tool's answer says when no backup was saved before its change.
NO_AI_BACKUP = (
    'No backup was saved before this change, so the Backups list has no "Before AI changes" '
    "backup to go back to. The disk is full. Free up disk space and try again."
)


def _ai_handler(engine: Engine):
    from openavc.cloud.ai_tool_handler import AIToolHandler

    agent = MagicMock()
    agent.send_message = AsyncMock()
    return AIToolHandler(agent, MagicMock(), MagicMock(), project_path=str(engine.project_path))


async def test_an_ai_change_made_without_its_backup_says_so_and_the_next_one_tries_again(engine, monkeypatch):
    handler = _ai_handler(engine)
    _run_out_of_room(monkeypatch)

    async def add_variable(_tool_input):
        return {"success": True}

    answer = await handler._run_tool("add_variable", add_variable, {})

    assert answer == {"success": True, "backup_not_saved": NO_AI_BACKUP}
    # First, so a long answer cut off at its end still says it.
    assert list(answer)[0] == "backup_not_saved"
    assert handler._ai_backup_created is False

    monkeypatch.undo()
    answer = await handler._run_tool("add_variable", add_variable, {})

    assert answer == {"success": True}
    assert [b.reason for b in backup_manager.list_backups(engine.project_path)] == ["Before AI changes"]


async def test_an_ai_answer_that_is_not_an_object_still_says_no_backup_was_saved(engine, monkeypatch):
    handler = _ai_handler(engine)
    _run_out_of_room(monkeypatch)

    async def send_command(_tool_input):
        return "sent"

    answer = await handler._run_tool("send_device_command", send_command, {})

    assert answer == {"result": "sent", "backup_not_saved": NO_AI_BACKUP}



# --- The auto-backup ------------------------------------------------------------


async def test_an_auto_backup_that_cannot_be_saved_waits_the_interval_and_stays_due(engine, monkeypatch):
    import asyncio

    _run_out_of_room(monkeypatch)
    real_sleep = asyncio.sleep
    turns = {"n": 0}

    async def one_turn(_seconds):
        turns["n"] += 1
        if turns["n"] >= 2:
            raise asyncio.CancelledError()
        await real_sleep(0)

    monkeypatch.setattr("openavc.core.engine.asyncio.sleep", one_turn)
    engine._dirty_since_backup = True
    engine._last_backup_time = 0

    await engine._periodic_backup_loop()

    assert engine._dirty_since_backup is True
    assert engine._last_backup_time > 0
    assert _backups(engine) == []
