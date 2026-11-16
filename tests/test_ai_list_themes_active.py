"""The AI's theme list and project summary name the theme the project draws.

A project with no theme id draws the theme its light or dark setting names
(``UISettings.drawn_theme_id``); the list told the assistant no theme was in
use at all, and the summary left its active theme out, while the panel drew
Light Modern or Dark Default.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openavc.api import rest
from openavc.api import themes as themes_api
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.project_loader import ProjectConfig, UISettings


@pytest.fixture
def handler_for(tmp_path):
    def make(settings: dict) -> AIToolHandler:
        engine = MagicMock()
        engine.project = MagicMock()
        engine.project.ui.settings = UISettings.model_validate(settings)
        engine.project_path = tmp_path / "project.avc"
        rest.set_engine(engine)
        themes_api.set_engine(engine)
        return AIToolHandler(MagicMock(), MagicMock(), MagicMock())

    yield make
    rest.set_engine(None)
    themes_api.set_engine(None)


@pytest.mark.parametrize(
    ("settings", "active"),
    [
        ({"theme": "light", "theme_id": ""}, "light-modern"),
        ({"theme": "dark", "theme_id": ""}, "dark-default"),
        ({"theme": "light", "theme_id": "luxury"}, "luxury"),
    ],
    ids=["a light project with no id", "a dark project with no id", "a project with an id"],
)
async def test_the_theme_the_project_draws_is_the_active_one(handler_for, settings, active) -> None:
    themes = await handler_for(settings)._list_themes({})
    assert [t["id"] for t in themes if t["active"]] == [active]


@pytest.mark.parametrize(
    ("settings", "active"),
    [
        ({"theme": "light", "theme_id": ""}, "light-modern"),
        ({"theme": "dark"}, "dark-default"),
        ({"theme": "dark", "theme_id": "slate"}, "slate"),
    ],
    ids=["a light project with no id", "a dark project with no id", "a project with an id"],
)
async def test_the_project_summary_names_the_theme_the_project_draws(handler_for, settings, active) -> None:
    handler = handler_for(settings)
    engine = rest._engine
    engine.project = ProjectConfig.model_validate({"project": {"id": "room", "name": "Room"}, "ui": {"settings": settings}})
    summary = await handler._get_project_summary({})
    assert summary["active_theme"] == active
