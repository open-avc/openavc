"""Regression test for the theme path-traversal hole (audit C8).

The AI `get_theme` / `apply_theme` tools built ``<themes_dir>/<theme_id>.json``
straight from an unsanitized theme_id. The AI tools take theme_id as a raw
string, so ``theme_id='../cloud'`` read arbitrary ``.json`` files — including
``cloud.json``, the system key — and returned them to the cloud AI. The REST
theme endpoints had the same unguarded join (path param blocks ``/``, but a
Windows backslash still escapes).

These tests drive the AI tools directly and assert traversal/absolute theme
ids are rejected (no file content leaked, nothing persisted), and that the
shared REST guard ``_safe_theme_path`` rejects the same — while a real built-in
theme still loads.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from openavc.api import rest
from openavc.api.themes import _safe_theme_path
from openavc.cloud.ai_tool_handler import AIToolHandler


def _make_handler(tmp_path):
    engine = MagicMock()
    engine.project = MagicMock()
    engine.project.ui.settings.theme_id = "dark-default"
    engine.project.ui.settings.theme = "dark"
    engine.project_path = tmp_path / "project.avc"
    rest.set_engine(engine)
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    return handler, engine


@pytest.fixture
def theme_handler(tmp_path):
    handler, engine = _make_handler(tmp_path)
    try:
        yield handler, engine, tmp_path
    finally:
        rest.set_engine(None)


async def test_ai_get_theme_rejects_traversal(theme_handler):
    handler, _engine, tmp_path = theme_handler
    # Plant a secret where ../cloud would resolve from the custom themes dir
    # (project_dir/themes/../cloud.json -> project_dir/cloud.json).
    secret = tmp_path / "cloud.json"
    secret.write_text('{"system_key": "SUPERSECRET"}', encoding="utf-8")

    result = await handler._get_theme({"theme_id": "../cloud"})

    assert "error" in result
    assert "Invalid theme id" in result["error"]
    assert "SUPERSECRET" not in str(result)


async def test_ai_get_theme_rejects_absolute_path(theme_handler):
    handler, _engine, tmp_path = theme_handler
    secret = tmp_path / "cloud.json"
    secret.write_text('{"system_key": "SUPERSECRET"}', encoding="utf-8")

    result = await handler._get_theme({"theme_id": str(secret.with_suffix(""))})

    assert "error" in result
    assert "Invalid theme id" in result["error"]
    assert "SUPERSECRET" not in str(result)


async def test_ai_get_theme_allows_builtin(theme_handler):
    handler, _engine, _tmp = theme_handler
    result = await handler._get_theme({"theme_id": "dark-default"})
    assert result.get("id") == "dark-default"
    assert result.get("_source") == "builtin"


async def test_ai_apply_theme_rejects_traversal(theme_handler):
    handler, engine, _tmp = theme_handler
    result = await handler._apply_theme({"theme_id": "../cloud"})
    assert "error" in result
    assert "Invalid theme id" in result["error"]
    # The traversal string must not have been persisted as the active theme.
    assert engine.project.ui.settings.theme_id == "dark-default"


def test_rest_safe_theme_path_guard(tmp_path):
    # Valid slug resolves inside the dir.
    assert _safe_theme_path(tmp_path, "dark-default") == (tmp_path / "dark-default.json").resolve()
    # Relative traversal and absolute paths are rejected with 400.
    with pytest.raises(HTTPException) as exc:
        _safe_theme_path(tmp_path, "../cloud")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        _safe_theme_path(tmp_path, str(tmp_path.parent / "cloud"))
