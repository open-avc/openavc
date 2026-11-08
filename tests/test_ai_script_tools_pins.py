"""The AI's script tools, pinned where nothing else looked.

Each passes on the code as it is, so a restructure that changes an answer or
a write fails here:

- ``get_script_source``: no project, the source, a file that is gone, an
  unknown script.
- ``get_script_health``: no engine, no script runtime, the whole project, one
  script, a script with nothing wrong.
- ``create_script``: no project, an id in use, a syntax error (checked before
  the file name, and nothing written), the file name defaulting to the id, an
  empty source written without a check, what the record holds, and a
  duplicate that appears while the edit runs (the file is already written).
- ``update_script_source``: no project, an unknown script, a syntax error
  (nothing written), an empty source, the reload's answer, no script runtime.
- ``delete_script``: no project, an unknown script, a file already gone, what
  still reaches into the script riding back.

Path containment and the dead-listener warnings have their own files
(test_ai_script_tools_path_traversal.py, test_dead_event_listeners.py).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.api import rest
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.project_loader import ProjectConfig, ProjectMeta, ScriptConfig

LIGHTS = "from openavc import log\n\ndef lights_on():\n    log.info('on')\n"


@pytest.fixture
def harness(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "lights.py").write_text(LIGHTS, encoding="utf-8")
    engine = MagicMock()
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        scripts=[ScriptConfig(id="lights", file="lights.py", description="Lights")],
    )
    engine.project_path = tmp_path / "project.avc"
    engine.scripts = None
    edits: list[Any] = []

    async def apply_edit(mutate):
        new_project = engine.project.model_copy(deep=True)
        for extra in edits:
            extra(new_project)
        mutate(new_project)
        engine.project = new_project
        return 1

    engine.apply_project_edit = AsyncMock(side_effect=apply_edit)
    rest.set_engine(engine)
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    handler._find_references = lambda kind, ref: {}
    try:
        yield handler, engine, scripts_dir, edits
    finally:
        rest.set_engine(None)


def _no_project() -> AIToolHandler:
    engine = MagicMock()
    engine.project = None
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    handler._get_engine = lambda: engine
    return handler


@pytest.mark.parametrize("tool", ["_get_script_source", "_create_script", "_update_script_source", "_delete_script"])
async def test_without_a_project(tool):
    handler = _no_project()
    assert await getattr(handler, tool)({"script_id": "lights", "id": "lights"}) == {"error": "No project loaded"}
    handler._get_engine = lambda: None
    assert await getattr(handler, tool)({"script_id": "lights", "id": "lights"}) == {"error": "No project loaded"}


# --- get_script_source -------------------------------------------------------------


async def test_get_script_source(harness):
    handler, _engine, _dir, _edits = harness
    assert await handler._get_script_source({"script_id": "lights"}) == {
        "id": "lights", "file": "lights.py", "source": LIGHTS,
    }


async def test_get_script_source_of_a_file_that_is_gone(harness):
    handler, _engine, scripts_dir, _edits = harness
    (scripts_dir / "lights.py").unlink()
    assert await handler._get_script_source({"script_id": "lights"}) == {"error": "Script file not found: lights.py"}


async def test_get_script_source_of_an_unknown_script(harness):
    handler, _engine, _dir, _edits = harness
    assert await handler._get_script_source({"script_id": "ghost"}) == {"error": "Script 'ghost' not found"}


# --- get_script_health ---------------------------------------------------------------


def _runtime(errors: dict, abandoned: dict, runtime: dict) -> MagicMock:
    scripts = MagicMock()
    scripts.get_load_errors.return_value = errors
    scripts.get_abandoned_loads.return_value = abandoned
    scripts.get_runtime_errors.return_value = runtime
    return scripts


async def test_script_health_without_an_engine_or_a_runtime(harness):
    handler, engine, _dir, _edits = harness
    # Health never asks for a project: an engine with no runtime answers clean.
    projectless = _no_project()
    projectless._get_engine().scripts = None
    assert await projectless._get_script_health({}) == {"errors": {}, "abandoned": {}, "runtime": {}}
    nobody = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    nobody._get_engine = lambda: None
    assert await nobody._get_script_health({}) == {"error": "No project loaded"}
    engine.scripts = None
    assert await handler._get_script_health({"script_id": "lights"}) == {"errors": {}, "abandoned": {}, "runtime": {}}


async def test_script_health_of_the_whole_project(harness):
    handler, engine, _dir, _edits = harness
    engine.scripts = _runtime({"a": "SyntaxError"}, {"b": "timed out"}, {"a": [{"error": "boom"}]})
    assert await handler._get_script_health({}) == {
        "errors": {"a": "SyntaxError"}, "abandoned": {"b": "timed out"}, "runtime": {"a": [{"error": "boom"}]},
    }
    assert await handler._get_script_health({"script_id": None}) == {
        "errors": {"a": "SyntaxError"}, "abandoned": {"b": "timed out"}, "runtime": {"a": [{"error": "boom"}]},
    }


async def test_script_health_of_one_script(harness):
    handler, engine, _dir, _edits = harness
    engine.scripts = _runtime({"a": "SyntaxError"}, {"b": "timed out"}, {"a": [{"error": "boom"}]})
    assert await handler._get_script_health({"script_id": "a"}) == {
        "errors": {"a": "SyntaxError"}, "runtime": {"a": [{"error": "boom"}]},
    }
    assert await handler._get_script_health({"script_id": "b"}) == {"abandoned": {"b": "timed out"}}
    assert await handler._get_script_health({"script_id": "c"}) == {"errors": {}, "abandoned": {}, "runtime": {}}


# --- create_script ----------------------------------------------------------------------


async def test_create_script_names_its_file_after_its_id(harness):
    handler, engine, scripts_dir, _edits = harness

    answer = await handler._create_script({"id": "shades", "source": "x = 1\n", "description": "Shades"})

    assert answer == {"status": "created", "id": "shades"}
    assert (scripts_dir / "shades.py").read_text(encoding="utf-8") == "x = 1\n"
    record = engine.project.scripts[-1]
    assert (record.id, record.file, record.enabled, record.description) == ("shades", "shades.py", True, "Shades")


async def test_create_script_disabled_with_an_empty_source(harness):
    handler, engine, scripts_dir, _edits = harness

    answer = await handler._create_script({"id": "later", "file": "later.py", "enabled": False})

    assert answer == {"status": "created", "id": "later"}
    assert (scripts_dir / "later.py").read_text(encoding="utf-8") == ""
    assert (engine.project.scripts[-1].enabled, engine.project.scripts[-1].description) == (False, "")


async def test_create_script_creates_the_scripts_folder(harness):
    handler, _engine, scripts_dir, _edits = harness
    (scripts_dir / "lights.py").unlink()
    scripts_dir.rmdir()

    await handler._create_script({"id": "shades", "source": "x = 1\n"})

    assert (scripts_dir / "shades.py").exists()


async def test_create_script_refuses_an_id_in_use(harness):
    handler, engine, scripts_dir, _edits = harness

    answer = await handler._create_script({"id": "lights", "file": "lights2.py", "source": "x = 1\n"})

    assert answer == {"error": "Script 'lights' already exists"}
    assert not (scripts_dir / "lights2.py").exists()
    engine.apply_project_edit.assert_not_awaited()


async def test_create_script_refuses_a_syntax_error_before_the_file_name(harness):
    handler, engine, scripts_dir, _edits = harness

    answer = await handler._create_script({"id": "bad", "file": "../bad.py", "source": "def broken(:\n"})

    assert answer == {"error": "Script 'bad': Python syntax error (line 1): invalid syntax"}
    assert not (scripts_dir / "bad.py").exists()
    assert not (scripts_dir.parent / "bad.py").exists()
    engine.apply_project_edit.assert_not_awaited()


async def test_create_script_that_appears_meanwhile_leaves_the_file(harness):
    handler, engine, scripts_dir, edits = harness
    edits.append(lambda project: project.scripts.append(ScriptConfig(id="shades", file="other.py")))

    answer = await handler._create_script({"id": "shades", "source": "x = 1\n"})

    assert answer == {"error": "Script 'shades' already exists"}
    assert (scripts_dir / "shades.py").read_text(encoding="utf-8") == "x = 1\n"


# --- update_script_source --------------------------------------------------------------------


async def test_update_script_source_saves(harness):
    handler, _engine, scripts_dir, _edits = harness

    assert await handler._update_script_source({"script_id": "lights", "source": "y = 2\n"}) == {"status": "saved"}
    assert (scripts_dir / "lights.py").read_text(encoding="utf-8") == "y = 2\n"


async def test_update_script_source_answers_the_reload(harness):
    handler, engine, _dir, _edits = harness
    engine.scripts = MagicMock()
    engine.scripts.reload_script.return_value = {"status": "reloaded", "handlers": 0}

    answer = await handler._update_script_source({"script_id": "lights", "source": "y = 2\n"})

    assert answer == {"status": "saved", "reload": {"status": "reloaded", "handlers": 0}}
    engine.scripts.reload_script.assert_called_once_with(
        {"id": "lights", "file": "lights.py", "enabled": True, "description": "Lights"},
    )


async def test_update_script_source_with_an_empty_source(harness):
    handler, _engine, scripts_dir, _edits = harness

    assert await handler._update_script_source({"script_id": "lights"}) == {"status": "saved"}
    assert (scripts_dir / "lights.py").read_text(encoding="utf-8") == ""


async def test_update_script_source_recreates_a_missing_folder(harness):
    handler, _engine, scripts_dir, _edits = harness
    (scripts_dir / "lights.py").unlink()
    scripts_dir.rmdir()

    assert await handler._update_script_source({"script_id": "lights", "source": "y = 2\n"}) == {"status": "saved"}
    assert (scripts_dir / "lights.py").read_text(encoding="utf-8") == "y = 2\n"


async def test_update_script_source_refuses_a_syntax_error(harness):
    handler, _engine, scripts_dir, _edits = harness

    answer = await handler._update_script_source({"script_id": "lights", "source": "if True\n"})

    assert answer == {"error": "Script 'lights': Python syntax error (line 1): expected ':'"}
    assert (scripts_dir / "lights.py").read_text(encoding="utf-8") == LIGHTS


async def test_update_script_source_of_an_unknown_script(harness):
    handler, _engine, _dir, _edits = harness
    assert await handler._update_script_source({"script_id": "ghost", "source": "y = 2\n"}) == {
        "error": "Script 'ghost' not found",
    }


# --- delete_script ------------------------------------------------------------------------------


async def test_delete_script(harness):
    handler, engine, scripts_dir, _edits = harness

    assert await handler._delete_script({"script_id": "lights"}) == {"status": "deleted"}
    assert not (scripts_dir / "lights.py").exists()
    assert engine.project.scripts == []


async def test_delete_script_whose_file_is_gone(harness):
    handler, engine, scripts_dir, _edits = harness
    (scripts_dir / "lights.py").unlink()

    assert await handler._delete_script({"script_id": "lights"}) == {"status": "deleted"}
    assert engine.project.scripts == []


async def test_delete_script_hands_back_what_reaches_into_it(harness):
    handler, _engine, scripts_dir, _edits = harness
    asked: list[tuple[str, str]] = []

    def references(kind: str, ref: str) -> dict:
        asked.append((kind, ref))
        # Read while the file is still there.
        assert (scripts_dir / "lights.py").exists()
        return {"bindings": [{"page": "main"}]}

    handler._find_references = references

    answer = await handler._delete_script({"script_id": "lights"})

    assert answer == {"status": "deleted", "impact": {"bindings": [{"page": "main"}]}}
    assert asked == [("script", "lights")]


async def test_delete_script_of_an_unknown_script(harness):
    handler, engine, _dir, _edits = harness
    assert await handler._delete_script({"script_id": "ghost"}) == {"error": "Script 'ghost' not found"}
    engine.apply_project_edit.assert_not_awaited()


async def test_delete_script_that_went_meanwhile(harness):
    handler, engine, scripts_dir, edits = harness
    edits.append(lambda project: setattr(project, "scripts", []))

    assert await handler._delete_script({"script_id": "lights"}) == {"error": "Script 'lights' not found"}
    assert not (scripts_dir / "lights.py").exists()
