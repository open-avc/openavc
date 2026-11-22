"""A script's ``.py`` file moves with the project change that adds or drops
the script, at both doors that do (the AI's script tools and the IDE's
script routes), on a running engine whose save is made to fail.

- A create writes its file inside the change, under the project's lock,
  and a change that is not applied takes the file back: a new file is
  removed, and a file that was already at that path gets its bytes back.
- A delete removes the file only once the project file holds the change. A
  change that is not applied keeps the file, the record and the running
  script, which the failed change's undo loads again from that file.
- The assistant's own lock is taken before the project's, never inside it.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openavc.api import rest
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core import project_changes
from openavc.core.engine import Engine
from openavc.core.project_changes import ProjectNotAppliedError
from openavc.core.project_loader import load_project
from openavc.core.script_engine import ScriptEngine

LIGHTS = textwrap.dedent("""\
    from openavc import on_state_change

    @on_state_change("var.lights")
    async def lights_heard(key, old_value, new_value):
        pass
""")
NEW = "x = 1\n"


def _project() -> dict:
    return {
        "openavc_version": "0.17.0",
        "project": {"id": "p", "name": "P"},
        "scripts": [{"id": "lights", "file": "lights.py", "enabled": True}],
    }


@pytest.fixture
async def engine(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project()), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "lights.py").write_text(LIGHTS, encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    eng.scripts = ScriptEngine(eng.state, eng.events, eng.devices, tmp_path, eng.macros)
    eng.scripts.install()
    eng.scripts.load_scripts([s.model_dump() for s in eng.project.scripts])
    rest.set_engine(eng)
    try:
        yield eng
    finally:
        rest.set_engine(None)
        eng.scripts.unload_all()


@pytest.fixture
def full_disk(monkeypatch):
    """Every save of the project fails, as on a full disk."""
    async def refuse(path, project):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(project_changes, "save_project_async", refuse)


def _scripts(eng) -> Path:
    return eng.project_path.parent / "scripts"


def _what_holds(eng) -> dict:
    """The project's scripts in memory and on disk, and what runs."""
    return {
        "records": [s.id for s in eng.project.scripts],
        "on disk": [s["id"] for s in json.loads(eng.project_path.read_text())["scripts"]],
        "running": sorted(eng.scripts._scripts),
        "files": sorted(p.name for p in _scripts(eng).iterdir()),
    }


@pytest.fixture
def assistant():
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    handler._find_references = lambda kind, ref: {}
    return handler


@pytest.fixture
async def ide(engine, isolated_auth_config):
    isolated_auth_config.set("auth", "programmer_username", "engineer")
    isolated_auth_config.set("auth", "programmer_password", "disposable-script-password")
    app = FastAPI()
    app.include_router(rest.router)
    credential = base64.b64encode(b"engineer:disposable-script-password").decode()
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
        headers={"Authorization": "Basic " + credential},
    ) as client:
        yield client


# --- the assistant ------------------------------------------------------------------------------


async def test_the_assistant_writes_a_new_script_only_inside_the_change(engine, assistant):
    """Nothing is written while another change holds the project, and the
    assistant's own lock is already held by then: it is taken first."""
    async with engine._reload_lock:
        task = asyncio.create_task(assistant._run_tool(
            "create_script", assistant._create_script, {"id": "shades", "source": NEW},
        ))
        for _ in range(20):
            await asyncio.sleep(0)
        assert assistant._project_lock.locked()
        assert not (_scripts(engine) / "shades.py").exists()

    assert await task == {"status": "created", "id": "shades"}
    assert (_scripts(engine) / "shades.py").read_text(encoding="utf-8") == NEW


async def test_a_create_the_assistant_could_not_save_leaves_no_file(engine, assistant, full_disk):
    before = _what_holds(engine)

    with pytest.raises(ProjectNotAppliedError):
        await assistant._create_script({"id": "shades", "source": NEW})

    assert _what_holds(engine) == before


async def test_a_create_the_assistant_could_not_save_puts_back_the_file_it_replaced(
    engine, assistant, full_disk,
):
    (_scripts(engine) / "shades.py").write_text("kept = True\n", encoding="utf-8")

    with pytest.raises(ProjectNotAppliedError):
        await assistant._create_script({"id": "shades", "source": NEW})

    assert (_scripts(engine) / "shades.py").read_text(encoding="utf-8") == "kept = True\n"


async def test_a_delete_the_assistant_could_not_save_keeps_the_script(engine, assistant, full_disk):
    before = _what_holds(engine)

    with pytest.raises(ProjectNotAppliedError):
        await assistant._delete_script({"script_id": "lights"})

    assert _what_holds(engine) == before
    assert (_scripts(engine) / "lights.py").read_text(encoding="utf-8") == LIGHTS


async def test_the_assistant_removes_a_deleted_script_once_the_project_file_holds_it(
    engine, assistant, monkeypatch,
):
    saved = project_changes.save_project_async
    present_at_save: list[bool] = []

    async def save(path, project):
        present_at_save.append((_scripts(engine) / "lights.py").exists())
        await saved(path, project)

    monkeypatch.setattr(project_changes, "save_project_async", save)

    assert await assistant._delete_script({"script_id": "lights"}) == {"status": "deleted"}

    assert present_at_save == [True]
    assert not (_scripts(engine) / "lights.py").exists()
    assert _what_holds(engine)["records"] == []


# --- the IDE --------------------------------------------------------------------------------------


async def test_a_create_the_ide_could_not_save_leaves_no_file(engine, ide, full_disk):
    before = _what_holds(engine)

    resp = await ide.post("/api/scripts", json={
        "id": "shades", "file": "shades.py", "source": NEW,
    })

    assert resp.status_code >= 500
    assert _what_holds(engine) == before


async def test_a_delete_the_ide_could_not_save_keeps_the_script(engine, ide, full_disk):
    before = _what_holds(engine)

    resp = await ide.delete("/api/scripts/lights")

    assert resp.status_code >= 500
    assert _what_holds(engine) == before
    assert (_scripts(engine) / "lights.py").read_text(encoding="utf-8") == LIGHTS
