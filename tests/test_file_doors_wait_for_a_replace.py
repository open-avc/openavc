"""A file saved or deleted beside the project while a whole other project
replaces it waits for the replace.

Every door that writes or removes one of the project's files outside a
project change (an uploaded image or sound, a saved theme, a custom
control, a script's source; the Programmer's doors and the AI assistant's
alike) holds the project's change lock for its checks and its write. A
replace holds that lock from its backup to its load, so:

- a file saved while the replace takes its backup is never written into the
  folder the swap is about to carry away after the backup has read it: the
  save lands once the new project runs, or is refused by it, and a save the
  door answered as done is in the running project or in the backup;
- a delete made between the swap and the load asks the project the files on
  disk belong to whether anything still shows the file, never the project
  being replaced, so a file only the incoming project uses is not deleted.

The engine is real and so is the replace (backup, swap, load); the rest of
the reconcile (devices, plugins, scripts) is left out. The doors are called
as the routes and tools they are.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from openavc.api import _engine as engine_slot
from openavc.api import assets, themes
from openavc.api.routes import scripts, ui_files
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core import backup_manager, project_replace
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.project_reconcile import RECONCILE_STAGES
from openavc.core.project_replace import PreparedProject

_VARIABLES = next(stage for stage in RECONCILE_STAGES if stage.forward == "_follow_variables")

THEME = {"id": "hall-look", "name": "Hall look", "version": "1.0.0", "variables": {"panel_bg": "#111111"}}

HALL = {
    "openavc_version": CURRENT_VERSION,
    "project": {"id": "hall", "name": "Hall"},
    "scripts": [{"id": "main", "file": "main.py"}],
    "ui": {"pages": [{"id": "main", "name": "Main", "elements": []}]},
}

LOBBY = {
    "openavc_version": CURRENT_VERSION,
    "project": {"id": "lobby", "name": "Lobby"},
    "ui": {"pages": [{"id": "main", "name": "Main", "elements": [
        {"id": "hero", "type": "image", "src": "assets://lobby.png"},
        {"id": "map", "type": "custom", "custom_file": "lobby/index.html"},
    ]}]},
}


class _WatchedLock(asyncio.Lock):
    """The change lock, telling the test when somebody asks for it while it
    is held: a door that waits for the replace."""

    def __init__(self) -> None:
        super().__init__()
        self.waited = asyncio.Event()

    async def acquire(self) -> bool:
        if self.locked():
            self.waited.set()
        return await super().acquire()


@pytest.fixture
def hall(tmp_path: Path, monkeypatch) -> Engine:
    """The Hall running, with a script, a logo, a custom control and a theme
    of its own, every door's engine slot pointing at it."""
    project_file = tmp_path / "project.avc"
    project_file.write_text(json.dumps(HALL), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "main.py").write_text("# the hall's script\n", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"PNG-hall")
    (tmp_path / "ui" / "hall").mkdir(parents=True)
    (tmp_path / "ui" / "hall" / "index.html").write_text("<p>hall</p>", encoding="utf-8")
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "hall-look.json").write_text(json.dumps(THEME), encoding="utf-8")

    engine = Engine(str(project_file))
    engine.project = load_project(engine.project_path)
    engine._start_variables()

    async def variables_only(run) -> None:
        run.entered.append(_VARIABLES)
        await engine._follow_variables(run)

    engine._reconcile = variables_only
    engine.broadcast_ws = AsyncMock()
    engine._reload_lock = _WatchedLock()
    for slot in (engine_slot, assets, themes):
        monkeypatch.setattr(slot, "_engine", engine)
    yield engine
    engine.persister.stop()


def _assistant(engine: Engine) -> AIToolHandler:
    handler = AIToolHandler(MagicMock(send_message=AsyncMock()), MagicMock(), MagicMock(emit=AsyncMock()))
    handler._get_engine = lambda: engine
    return handler


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=name)


def _body(data: dict) -> SimpleNamespace:
    """A request whose JSON body is ``data``."""
    return SimpleNamespace(json=AsyncMock(return_value=data))


def _zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


async def _answer(door) -> object:
    """What ``door()`` answered: its result, or the refusal it raised."""
    try:
        return await door()
    except HTTPException as refused:
        return refused


def _accepted(answer: object) -> bool:
    if isinstance(answer, HTTPException):
        return False
    return not (isinstance(answer, dict) and "error" in answer)


async def _door_beside(engine: Engine, replace: asyncio.Task, door) -> object:
    """``door()`` run while ``replace`` holds the change lock: it runs to its
    answer, or to the change lock it waits on."""
    task = asyncio.create_task(_answer(door))
    waiting = asyncio.create_task(engine._reload_lock.waited.wait())
    await asyncio.wait({task, waiting}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
    waiting.cancel()
    return task


async def _during_the_backup(engine: Engine, door) -> tuple[object, Path]:
    """``door()`` sent while a replace by a blank project is taking the
    backup of the Hall, once the backup has read its files and before the
    swap. Both run to the end; returns the door's answer and the backup."""
    taken, go = threading.Event(), threading.Event()
    real = backup_manager.create_backup

    def held(*args, **kwargs):
        path = real(*args, **kwargs)
        taken.set()
        go.wait(10)
        return path

    blank = project_replace.blank(project_replace.read_as_loaded(json.dumps({
        "openavc_version": CURRENT_VERSION, "project": {"id": "blank", "name": "Blank"},
    }))[0])
    with patch.object(backup_manager, "create_backup", held):
        replace = asyncio.create_task(engine.replace_with(blank))
        await asyncio.to_thread(taken.wait, 10)
        task = await _door_beside(engine, replace, door)
        go.set()
        await replace
        answer = await task
    backups = sorted((engine.project_path.parent / "backups").glob("*.zip"))
    return answer, backups[-1]


def _lobby() -> PreparedProject:
    """The Lobby, whose page shows a logo and a custom control only it
    brings, ready to replace the Hall."""
    project, notes = project_replace.read_as_loaded(json.dumps(LOBBY))

    def fill(folder: Path) -> None:
        (folder / "assets" / "lobby.png").write_bytes(b"PNG-lobby")
        (folder / "ui" / "lobby").mkdir()
        (folder / "ui" / "lobby" / "index.html").write_text("<p>lobby</p>", encoding="utf-8")

    return PreparedProject(project, notes, "Before opening 'Lobby'", fill=fill)


async def _between_swap_and_load(engine: Engine, door) -> object:
    """``door()`` sent while the Lobby is replacing the Hall, its files
    swapped in and the Lobby not yet loaded. Both run to the end; returns the
    door's answer."""
    swapped, go_on = asyncio.Event(), asyncio.Event()
    real = engine._apply_project_locked

    async def held(*args, **kwargs):
        if not swapped.is_set():
            swapped.set()
            await go_on.wait()
        return await real(*args, **kwargs)

    engine._apply_project_locked = held
    replace = asyncio.create_task(engine.replace_with(_lobby()))
    await swapped.wait()
    assert engine.project.project.name == "Hall"
    task = await _door_beside(engine, replace, door)
    go_on.set()
    await replace
    assert engine.project.project.name == "Lobby"
    return await task


def _kept(engine: Engine, backup: Path, rel: str, content: bytes) -> bool:
    """Whether ``content`` at ``rel`` is in the running project's folder or
    in the backup the replace took."""
    live = engine.project_path.parent / rel
    if live.is_file() and live.read_bytes() == content:
        return True
    with zipfile.ZipFile(backup) as zf:
        return rel in zf.namelist() and zf.read(rel) == content


NEW_THEME = {**THEME, "id": "new-look", "name": "New look"}
CHANGED_THEME = {**THEME, "variables": {"panel_bg": "#222222"}}
CHANGED_SCRIPT = "# the hall's script, changed\n"

#: Each door that writes a file, how the test sends it, and what it writes.
WRITES = {
    "asset upload": (
        lambda engine: assets.upload_asset("default", _upload("new.png", b"PNG-new")),
        "assets/new.png", b"PNG-new",
    ),
    "custom control saved": (
        lambda engine: ui_files.write_ui_file("default", "new/index.html", _body({"content": "<p>new</p>"})),
        "ui/new/index.html", b"<p>new</p>",
    ),
    "custom control dropped": (
        lambda engine: ui_files.upload_ui_file("default", _upload("index.html", b"<p>dropped</p>"), "new"),
        "ui/new/index.html", b"<p>dropped</p>",
    ),
    "custom control archive": (
        lambda engine: ui_files.upload_ui_file(
            "default", _upload("new.zip", _zip_of({"index.html": b"<p>zipped</p>"})), "new",
        ),
        "ui/new/index.html", b"<p>zipped</p>",
    ),
    "theme created": (
        lambda engine: themes.create_theme(dict(NEW_THEME)),
        "themes/new-look.json", json.dumps(NEW_THEME, indent=4).encode(),
    ),
    "theme changed": (
        lambda engine: themes.update_theme("hall-look", json.loads(json.dumps(CHANGED_THEME))),
        "themes/hall-look.json", json.dumps(CHANGED_THEME, indent=4).encode(),
    ),
    "theme imported": (
        lambda engine: themes.import_theme(_upload("new.avctheme", json.dumps(NEW_THEME).encode()), False),
        "themes/new-look.json", json.dumps(NEW_THEME, indent=4).encode(),
    ),
    "script source saved": (
        lambda engine: scripts.save_script_source("main", _body({"source": CHANGED_SCRIPT})),
        "scripts/main.py", CHANGED_SCRIPT.encode(),
    ),
    "assistant writes a custom control": (
        lambda engine: _assistant(engine)._write_ui_file({"path": "new/index.html", "content": "<p>ai</p>"}),
        "ui/new/index.html", b"<p>ai</p>",
    ),
    "assistant changes a script's source": (
        lambda engine: _assistant(engine)._update_script_source({"script_id": "main", "source": CHANGED_SCRIPT}),
        "scripts/main.py", CHANGED_SCRIPT.encode(),
    ),
}


@pytest.mark.parametrize("door", WRITES)
async def test_a_file_saved_while_a_replace_takes_its_backup_is_not_lost(hall: Engine, door: str):
    send, rel, content = WRITES[door]
    answer, backup = await _during_the_backup(hall, lambda: send(hall))
    assert not _accepted(answer) or _kept(hall, backup, rel, content), (
        f"the {door} answered {answer!r}, but {rel} is neither in the running project nor in the backup"
    )


#: Each door that deletes a file, how the test sends it, and the file only
#: the Lobby shows.
DELETES = {
    "asset delete": (lambda engine: assets.delete_asset("default", "lobby.png"), "assets/lobby.png"),
    "custom control delete": (
        lambda engine: ui_files.delete_ui_file("default", "lobby/index.html"), "ui/lobby/index.html",
    ),
    "assistant deletes an asset": (
        lambda engine: _assistant(engine)._delete_asset({"filename": "lobby.png"}), "assets/lobby.png",
    ),
    "assistant deletes a custom control": (
        lambda engine: _assistant(engine)._delete_ui_file({"path": "lobby/index.html"}), "ui/lobby/index.html",
    ),
}


@pytest.mark.parametrize("door", DELETES)
async def test_a_delete_between_the_swap_and_the_load_reads_the_incoming_project(hall: Engine, door: str):
    send, rel = DELETES[door]
    answer = await _between_swap_and_load(hall, lambda: send(hall))
    assert not _accepted(answer), f"the {door} deleted {rel}, which the Lobby shows: {answer!r}"
    assert (hall.project_path.parent / rel).is_file()


@pytest.mark.parametrize("door", ["save to library", "replace in library"])
async def test_a_library_save_between_the_swap_and_the_load_keeps_one_project(
    hall: Engine, tmp_path: Path, monkeypatch, door: str,
):
    """Saving the running project to the library copies the project and the
    files beside it together: never the project being replaced with the
    incoming project's files."""
    from openavc.api.models import LibraryReplaceRequest, LibrarySaveRequest
    from openavc.api.routes import project as project_routes
    from openavc.core import project_library

    monkeypatch.setattr(project_library.config, "SAVED_PROJECTS_DIR", tmp_path / "library")
    monkeypatch.setattr(project_routes, "_get_engine", lambda: hall)
    if door == "replace in library":
        await project_routes.save_to_library(LibrarySaveRequest(id="kept", name="Kept"))
        send = lambda: project_routes.replace_library_project("kept", LibraryReplaceRequest(name="Kept"))  # noqa: E731
    else:
        send = lambda: project_routes.save_to_library(LibrarySaveRequest(id="kept", name="Kept"))  # noqa: E731

    answer = await _between_swap_and_load(hall, send)
    assert _accepted(answer), answer
    saved = tmp_path / "library" / "kept"
    shown = json.loads((saved / "project.avc").read_text(encoding="utf-8"))["ui"]["pages"][0]["elements"]
    assert (saved / "assets" / "lobby.png").is_file()
    assert [element["id"] for element in shown] == ["hero", "map"], (
        "the library holds the Lobby's files under a project that does not show them"
    )


# --- A backup or an export read off the event loop -------------------------------


def _paused_once(target, name: str, reading: threading.Event, go: threading.Event):
    """``target.name`` paused, the first time it is called, until ``go``,
    with ``reading`` set: its reader has read the project file and not yet
    the files beside it. Later calls (the replace's own backup) run."""
    real = getattr(target, name)
    first = threading.Lock()

    def pause() -> None:
        if first.acquire(blocking=False):
            reading.set()
            go.wait(10)

    if name == "_beside_members":
        def held(*args, **kwargs):
            pause()
            yield from real(*args, **kwargs)
    else:
        def held(*args, **kwargs):
            pause()
            return real(*args, **kwargs)
    return patch.object(target, name, held)


def _backup_by(engine: Engine, reason: str) -> bytes:
    for backup in (engine.project_path.parent / "backups").glob("*.zip"):
        with zipfile.ZipFile(backup) as zf:
            if json.loads(zf.read("backup_meta.json"))["reason"] == reason:
                return backup.read_bytes()
    raise AssertionError(f"no backup '{reason}'")


async def _manual_backup(engine: Engine) -> bytes:
    from openavc.api.routes import project as project_routes

    request = SimpleNamespace(headers={"content-length": "0"}, json=AsyncMock(return_value={}))
    with patch.object(project_routes, "_get_engine", lambda: engine):
        answer = await project_routes.create_backup_endpoint(request)
    return (engine.project_path.parent / answer["filename"]).read_bytes()


async def _periodic_backup(engine: Engine) -> bytes:
    engine._dirty_since_backup = True
    engine._last_backup_time = 0
    await engine._take_periodic_backup()
    return _backup_by(engine, "Auto-backup")


async def _assistants_first_backup(engine: Engine) -> bytes:
    handler = _assistant(engine)
    handler._project_path = engine.project_path
    assert await handler._maybe_create_pre_ai_backup() is None
    return _backup_by(engine, "Before AI changes")


async def _sealed_export(engine: Engine) -> bytes:
    from openavc.api.models import LoginsPassphraseRequest
    from openavc.api.routes import project as project_routes
    from openavc.core import project_library

    with patch.object(project_routes, "_get_engine", lambda: engine), \
            patch.object(project_library, "_sealed_logins", lambda *args: None):
        answer = await project_routes.export_current_project_with_logins(
            LoginsPassphraseRequest(passphrase="a long enough passphrase"),
        )
    return answer.body


#: Each door that reads the project's files off the event loop outside a
#: change, where it is paused, and what it leaves.
READERS = {
    "manual backup": (backup_manager, "_beside_members", _manual_backup),
    "periodic backup": (backup_manager, "_beside_members", _periodic_backup),
    "assistant's first backup": (backup_manager, "_beside_members", _assistants_first_backup),
    "export with logins": ("openavc.core.project_library", "_read_scripts_dir", _sealed_export),
}


@pytest.mark.parametrize("door", READERS)
async def test_a_backup_or_export_read_across_a_replace_holds_one_project(hall: Engine, door: str):
    """A backup or an export started before a replace, its files still being
    read when the replace comes to swap, holds the project it started on:
    the replace waits for it, never swapping the files it has yet to read."""
    import importlib

    target, name, read = READERS[door]
    if isinstance(target, str):
        target = importlib.import_module(target)
    reading, go = threading.Event(), threading.Event()
    with _paused_once(target, name, reading, go):
        reader = asyncio.create_task(read(hall))
        await asyncio.to_thread(reading.wait, 10)
        replace = asyncio.create_task(hall.replace_with(_lobby()))
        await asyncio.wait({replace}, timeout=1)
        go.set()
        archive = await reader
        await replace

    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = zf.namelist()
        project = json.loads(zf.read("project.avc"))
    assert project["project"]["name"] == "Hall"
    assert "assets/logo.png" in names and "assets/lobby.png" not in names, (
        f"the {door} holds the Hall's project file with the Lobby's files: {sorted(names)}"
    )
    assert hall.project.project.name == "Lobby"
