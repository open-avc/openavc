"""Every door that changes the files beside the project says which of them
changed, and so does a replace of the whole project.

The project's uploaded images and sounds, its custom controls and its saved
themes are files, not project data, so a change to them moves nothing a
project change announces. Each tree has one announcement, whatever door
changed it:

- the images and sounds: the ``project.assets`` state key, the catalog of
  the files as they are now (a plugin such as the Audio Player reads it, and
  the Programmer's asset list loads again on it);
- the custom controls: ``ui.files`` to every client;
- the themes: ``themes.changed`` to every client, naming the theme when one
  changed.

A replace that swaps the files beside the project (a restore, a project
opened from the library, a blank project, an import of another project)
announces all three once the new project runs; one that keeps them (an
import of the same project) or that fails announces none. Each goes out
once the project files' hold is let go, so a plugin hearing it may change
the project at once.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile

from openavc.api import _engine as engine_slot
from openavc.api import assets, themes
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core import file_news, project_replace
from openavc.core.engine import Engine
from openavc.core.project_changes import ProjectNotAppliedError
from openavc.core.project_loader import load_project, project_text
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.project_reconcile import RECONCILE_STAGES
from openavc.core.project_replace import PreparedProject
from openavc.core.ws_hub import MESSAGE_AUDIENCE, PANEL, PROGRAMMER

_VARIABLES = next(stage for stage in RECONCILE_STAGES if stage.forward == "_follow_variables")

THEME = {"id": "hall-look", "name": "Hall look", "version": "1.0.0", "variables": {"panel_bg": "#111111"}}

HALL = {
    "openavc_version": CURRENT_VERSION,
    "project": {"id": "hall", "name": "Hall"},
    "ui": {"pages": [{"id": "main", "name": "Main", "elements": []}]},
}

LOBBY = {
    "openavc_version": CURRENT_VERSION,
    "project": {"id": "lobby", "name": "Lobby"},
    "ui": {"pages": [{"id": "main", "name": "Main", "elements": [
        {"id": "hero", "type": "image", "src": "assets://lobby.png"},
    ]}]},
}

TREE_FRAMES = ("ui.files", "themes.changed")


@pytest.fixture
def hall(tmp_path: Path, monkeypatch) -> Engine:
    """The Hall running, with a logo, a custom control and a theme of its
    own; each frame it pushes recorded with whether the change lock was held
    when it went out."""
    project_file = tmp_path / "project.avc"
    project_file.write_text(json.dumps(HALL), encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"PNG-hall")
    (tmp_path / "ui" / "hall").mkdir(parents=True)
    (tmp_path / "ui" / "hall" / "index.html").write_text("<p>hall</p>", encoding="utf-8")
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "hall-look.json").write_text(json.dumps(THEME), encoding="utf-8")

    engine = Engine(str(project_file))
    engine.project = load_project(engine.project_path)
    engine._start_variables()
    file_news.publish_assets(engine)

    async def variables_only(run) -> None:
        run.entered.append(_VARIABLES)
        await engine._follow_variables(run)

    engine._reconcile = variables_only
    engine.sent = []

    async def broadcast(message: dict) -> None:
        engine.sent.append((message, engine._reload_lock.locked()))

    engine.broadcast_ws = broadcast
    for slot in (engine_slot, assets, themes):
        monkeypatch.setattr(slot, "_engine", engine)
    yield engine
    engine.persister.stop()


def _catalog(engine: Engine) -> list[str]:
    """The asset names ``project.assets`` holds now."""
    return [entry["name"] for entry in json.loads(engine.state.get("project.assets"))]


def _tree_frames(engine: Engine) -> list[tuple[dict, bool]]:
    return [(message, locked) for message, locked in engine.sent if message["type"] in TREE_FRAMES]


def _lobby(*, beside: bool = True) -> PreparedProject:
    """The Lobby, which brings a logo of its own, ready to replace the
    Hall; with ``beside`` False it keeps the files beside the Hall."""
    project, notes = project_replace.read_as_loaded(json.dumps(LOBBY))

    def fill(folder: Path) -> None:
        (folder / "assets" / "lobby.png").write_bytes(b"PNG-lobby")

    return PreparedProject(project, notes, "Before opening 'Lobby'", fill=fill, beside=beside)


def _assistant(engine: Engine) -> AIToolHandler:
    handler = AIToolHandler(MagicMock(send_message=AsyncMock()), MagicMock(), MagicMock(emit=AsyncMock()))
    handler._get_engine = lambda: engine
    return handler


def _theme_upload(data: dict) -> UploadFile:
    return UploadFile(file=io.BytesIO(json.dumps(data).encode("utf-8")), filename="look.avctheme")


# ---- a replace ----


async def test_a_replace_that_swaps_the_files_announces_every_tree(hall: Engine):
    assert _catalog(hall) == ["logo.png"]

    await hall.replace_with(_lobby())

    assert hall.project.project.name == "Lobby"
    assert _catalog(hall) == ["lobby.png"]
    frames = _tree_frames(hall)
    assert [message["type"] for message, _ in frames] == ["ui.files", "themes.changed"]
    ui_files, theme = (message for message, _ in frames)
    assert ui_files["path"] == "."
    assert theme.get("theme_id") is None
    assert not any(locked for _, locked in frames), "announced inside the change lock"


async def test_a_replace_that_keeps_the_files_announces_no_tree(hall: Engine):
    await hall.replace_with(_lobby(beside=False))

    assert hall.project.project.name == "Lobby"
    assert _catalog(hall) == ["logo.png"]
    assert _tree_frames(hall) == []


async def test_a_replace_that_fails_announces_no_tree(hall: Engine):
    real = hall._apply_project_locked
    loads = []

    async def incoming_refused(*args, **kwargs):
        loads.append(args)
        if len(loads) == 1:
            raise RuntimeError("the incoming project would not load")
        return await real(*args, **kwargs)

    hall._apply_project_locked = incoming_refused
    with pytest.raises(ProjectNotAppliedError):
        await hall.replace_with(_lobby())

    assert hall.project.project.name == "Hall"
    assert _catalog(hall) == ["logo.png"]
    assert _tree_frames(hall) == []


async def test_importing_the_same_project_announces_no_tree(hall: Engine):
    project, notes = project_replace.read_as_loaded(project_text(hall.project))

    await hall.replace_with(project_replace.imported(project, notes))

    assert _catalog(hall) == ["logo.png"]
    assert _tree_frames(hall) == []


async def test_importing_another_project_announces_every_tree(hall: Engine):
    project, notes = project_replace.read_as_loaded(json.dumps(LOBBY))

    await hall.replace_with(project_replace.imported(project, notes))

    assert hall.project.project.name == "Lobby"
    assert _catalog(hall) == []
    assert [message["type"] for message, _ in _tree_frames(hall)] == ["ui.files", "themes.changed"]


# ---- the assistant's asset delete ----


async def test_the_assistants_asset_delete_republishes_the_catalog(hall: Engine):
    answer = await _assistant(hall)._delete_asset({"filename": "logo.png"})

    assert "error" not in answer
    assert _catalog(hall) == []


# ---- the theme doors ----


async def test_creating_a_theme_announces_it(hall: Engine):
    await themes.create_theme({**THEME, "id": "new-look", "name": "New look"})

    assert _tree_frames(hall) == [({"type": "themes.changed", "theme_id": "new-look"}, False)]


async def test_saving_a_theme_announces_it(hall: Engine):
    await themes.update_theme("hall-look", {**THEME, "variables": {"panel_bg": "#222222"}})

    assert _tree_frames(hall) == [({"type": "themes.changed", "theme_id": "hall-look"}, False)]


async def test_deleting_a_theme_announces_it(hall: Engine):
    await themes.delete_theme("hall-look")

    assert _tree_frames(hall) == [({"type": "themes.changed", "theme_id": "hall-look"}, False)]


async def test_importing_a_theme_announces_it(hall: Engine):
    await themes.import_theme(_theme_upload({**THEME, "id": "new-look", "name": "New look"}))

    assert _tree_frames(hall) == [({"type": "themes.changed", "theme_id": "new-look"}, False)]


async def test_a_refused_theme_save_announces_nothing(hall: Engine):
    with pytest.raises(Exception):
        await themes.create_theme(THEME)  # already there

    assert _tree_frames(hall) == []


def test_a_theme_change_reaches_every_panel_and_the_programmer():
    assert MESSAGE_AUDIENCE["themes.changed"] == frozenset({PANEL, PROGRAMMER})
