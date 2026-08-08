"""Regression test for the AI script-tool path-traversal hole (audit C7).

The cloud AI script tools (`_create_script`, `_get_script_source`,
`_update_script_source`, `_delete_script`) built file paths straight from the
`file`/`filename` arg (or the stored `ScriptConfig.file`) and joined them under
the project's ``scripts/`` directory with no containment check — unlike the REST
endpoints, which guard via ``_safe_script_path``. That gave the semi-trusted
cloud AI a write/read/delete primitive anywhere on disk (e.g.
``file='../../evil.py'``).

These tests drive the AI tools directly and assert traversal/absolute paths are
rejected with nothing written, read, or deleted outside ``scripts/`` — while a
normal filename still works.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.api import rest
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.project_loader import ProjectConfig, ProjectMeta, ScriptConfig


def _make_handler(tmp_path, scripts=None):
    engine = MagicMock()
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        scripts=scripts or [],
    )
    engine.project_path = tmp_path / "project.avc"

    # Mirror the seam's contract — the tools hand a mutate callback to
    # apply_project_edit, which copies the current project, applies the
    # mutation, and swaps it in (no reconcile in this unit harness).
    async def _apply_edit(mutate):
        new_project = engine.project.model_copy(deep=True)
        mutate(new_project)
        engine.project = new_project
        return 1

    engine.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    rest.set_engine(engine)
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    return handler, engine


@pytest.fixture
def script_handler(tmp_path):
    handler, engine = _make_handler(tmp_path)
    try:
        yield handler, engine, tmp_path
    finally:
        rest.set_engine(None)


async def test_create_script_rejects_relative_traversal(script_handler):
    handler, engine, tmp_path = script_handler
    result = await handler._create_script(
        {"id": "evil", "file": "../../evil.py", "source": "x = 1\n"}
    )
    assert "error" in result
    assert "Invalid script filename" in result["error"]
    # Nothing written inside or outside the scripts dir.
    assert not (tmp_path / "scripts" / "evil.py").exists()
    assert not (tmp_path.parent / "evil.py").exists()
    # Script not registered in the project.
    assert engine.project.scripts == []


async def test_create_script_rejects_absolute_path(script_handler):
    handler, engine, tmp_path = script_handler
    abs_target = tmp_path.parent / "abs_evil.py"
    result = await handler._create_script(
        {"id": "evil2", "file": str(abs_target), "source": "x = 1\n"}
    )
    assert "error" in result
    assert "Invalid script filename" in result["error"]
    assert not abs_target.exists()
    assert engine.project.scripts == []


async def test_create_script_allows_normal_filename(script_handler):
    handler, engine, tmp_path = script_handler
    result = await handler._create_script(
        {"id": "good", "file": "good.py", "source": "x = 1\n"}
    )
    assert result == {"status": "created", "id": "good"}
    written = tmp_path / "scripts" / "good.py"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == "x = 1\n"
    assert [s.id for s in engine.project.scripts] == ["good"]


async def test_create_script_rejects_nested_subpath(script_handler):
    handler, engine, tmp_path = script_handler
    result = await handler._create_script(
        {"id": "nested", "file": "sub/evil.py", "source": "x = 1\n"}
    )
    assert "error" in result
    assert "Invalid script filename" in result["error"]
    assert not (tmp_path / "scripts" / "sub").exists()
    assert engine.project.scripts == []


async def test_create_script_rejects_non_py_extension(script_handler):
    handler, engine, tmp_path = script_handler
    result = await handler._create_script(
        {"id": "shellish", "file": "evil.sh", "source": "x = 1\n"}
    )
    assert "error" in result
    assert "Invalid script filename" in result["error"]
    assert not (tmp_path / "scripts" / "evil.sh").exists()
    assert engine.project.scripts == []


async def test_read_update_delete_reject_poisoned_stored_path(tmp_path):
    """Defense in depth: even a project entry whose ``file`` escapes (e.g. a
    hand-crafted .avc) must not become a read/write/delete primitive."""
    (tmp_path / "scripts").mkdir()
    outside = tmp_path.parent / "secret.py"
    outside.write_text("SECRET = 1\n", encoding="utf-8")
    poisoned = ScriptConfig(id="poison", file="../../secret.py")
    handler, _engine = _make_handler(tmp_path, scripts=[poisoned])
    try:
        read = await handler._get_script_source({"script_id": "poison"})
        assert "Invalid script filename" in read.get("error", "")
        assert "SECRET" not in str(read)

        upd = await handler._update_script_source(
            {"script_id": "poison", "source": "x = 2\n"}
        )
        assert "Invalid script filename" in upd.get("error", "")
        assert outside.read_text(encoding="utf-8") == "SECRET = 1\n"  # untouched

        dele = await handler._delete_script({"script_id": "poison"})
        assert "Invalid script filename" in dele.get("error", "")
        assert outside.exists()  # not unlinked
    finally:
        rest.set_engine(None)
        outside.unlink(missing_ok=True)
