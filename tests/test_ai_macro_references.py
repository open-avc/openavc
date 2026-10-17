"""The AI's reference check for a macro or a variable names what still uses it.

``check_references`` and the AI's ``delete_macro`` / ``delete_variable`` hand
back an impact before a delete. For a macro, the walk looked for trigger fields
no trigger has and never read another macro's steps, so a macro called from a
second macro, at the top level or inside a conditional branch, was reported as
used by nothing. For a variable, it read a trigger's ``key`` where the field is
``state_key``, so a trigger watching the variable was missed too. An empty
impact reads as an all-clear, which is worse than no answer.

Every macro, variable and page here is invented.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openavc.api import rest, ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, save_project


def _seed() -> dict:
    return {
        "project": {"id": "macro_refs", "name": "Macro References"},
        "variables": [
            {"id": "mode", "type": "string", "default": ""},
            {"id": "spare", "type": "string", "default": ""},
        ],
        "macros": [
            {"id": "lights_on", "name": "Lights On", "steps": []},
            {
                "id": "start", "name": "Start Meeting",
                "steps": [
                    {"action": "conditional",
                     "condition": {"key": "var.mode", "operator": "eq", "value": "present"},
                     "then_steps": [{"action": "macro", "macro": "lights_on"}],
                     "else_steps": [{"action": "delay", "seconds": 1}]},
                ],
                "triggers": [
                    {"id": "on_mode", "type": "state_change", "state_key": "var.mode"},
                ],
            },
            {
                "id": "shutdown", "name": "Shut Down",
                "steps": [
                    {"action": "macro", "macro": "lights_on"},
                    {"action": "state.set", "key": "var.mode", "value": "off"},
                ],
            },
            {
                "id": "evening", "name": "Evening",
                "steps": [],
                "triggers": [
                    {"id": "dusk", "type": "schedule", "cron": "0 18 * * *",
                     "conditions": [{"key": "var.mode", "operator": "eq", "value": "present"}]},
                ],
            },
        ],
        "ui": {
            "pages": [{"id": "home", "name": "Home", "elements": []}],
            "master_elements": [
                {"id": "all_on", "type": "button", "label": "All On", "pages": "*",
                 "bindings": {"do.press": [{"action": "macro", "macro": "lights_on"}]}},
            ],
        },
    }


@pytest.fixture
async def handler(tmp_path):
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig.model_validate(_seed())
    save_project(project_path, engine.project)
    (tmp_path / "scripts").mkdir()

    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield AIToolHandler(MagicMock(), engine.devices, MagicMock())
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


async def _refs(handler, ref_type: str, ref_id: str) -> dict:
    return (await handler._check_references({"type": ref_type, "id": ref_id}))["referenced_by"]


async def test_a_macro_called_from_another_macro_is_reported(handler) -> None:
    """One caller reaches it inside a conditional's then_steps, one at the top."""
    refs = await _refs(handler, "macro", "lights_on")
    assert [(m["macro_id"], m["steps"]) for m in refs["macros"]] == [
        ("start", 1), ("shutdown", 1),
    ]


async def test_a_master_element_calling_the_macro_is_reported(handler) -> None:
    """A master element is on every page; a button there is still a caller."""
    refs = await _refs(handler, "macro", "lights_on")
    assert [(b["element_id"], b["slot"]) for b in refs["bindings"]] == [("all_on", "do.press")]


async def test_an_uncalled_macro_still_reports_nothing(handler) -> None:
    assert await _refs(handler, "macro", "evening") == {}


async def test_deleting_a_called_macro_carries_the_impact(handler) -> None:
    result = await handler._delete_macro({"macro_id": "lights_on"})
    assert result["status"] == "deleted"
    assert [m["macro_id"] for m in result["impact"]["macros"]] == ["start", "shutdown"]


async def test_a_variable_watched_by_a_trigger_is_reported(handler) -> None:
    """The trigger's own key and a guard condition on another trigger."""
    refs = await _refs(handler, "variable", "mode")
    assert [(t["macro_id"], t["trigger_id"], t["field"]) for t in refs["triggers"]] == [
        ("start", "on_mode", "state_key"), ("evening", "dusk", "condition"),
    ]


async def test_a_variable_a_macro_reads_or_sets_is_reported(handler) -> None:
    refs = await _refs(handler, "variable", "mode")
    assert [(m["macro_id"], m["steps"]) for m in refs["macros"]] == [
        ("start", 1), ("shutdown", 1),
    ]


async def test_an_unused_variable_still_reports_nothing(handler) -> None:
    assert await _refs(handler, "variable", "spare") == {}
