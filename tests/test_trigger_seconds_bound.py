"""A trigger's delay, debounce and cooldown are a number of seconds, 0 or more.

The project schema declares the bound on the trigger model, so a project save
refuses a value outside it; the macro lint reports one where the editor draws
the trigger and where the AI assistant is told why its macro was refused; and
the trigger editor's boxes read the same bound from the generated table.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.protocol import AI_TOOL_CALL, _now_iso
from openavc.core import project_loader
from openavc.core.macro_validation import macro_issues, validate_macro
from openavc.core.project_loader import ProjectConfig, ProjectMeta, TriggerConfig

FIELDS = ("delay_seconds", "debounce_seconds", "cooldown_seconds")
OUT_OF_BOUND = [-5, -0.5, float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", OUT_OF_BOUND)
def test_the_schema_refuses_a_time_below_zero_or_no_finite_number(field, value):
    with pytest.raises(ValidationError) as caught:
        TriggerConfig(id="t", type="startup", **{field: value})
    assert [e["loc"] for e in caught.value.errors()] == [(field,)]


@pytest.mark.parametrize("field", FIELDS)
def test_zero_and_more_are_taken(field):
    assert getattr(TriggerConfig(id="t", type="startup", **{field: 0}), field) == 0
    assert getattr(TriggerConfig(id="t", type="startup", **{field: 2.5}), field) == 2.5
    assert getattr(TriggerConfig(id="t", type="startup"), field) == 0


def test_the_bound_is_the_named_floor():
    for field in FIELDS:
        info = TriggerConfig.model_fields[field]
        floors = [getattr(rule, "ge", None) for rule in info.metadata]
        assert project_loader.SECONDS_MIN in floors, field


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [*OUT_OF_BOUND, "soon", True])
def test_the_macro_lint_reports_it_on_the_trigger(field, value):
    trigger = {"id": "t", "type": "startup", field: value}
    issues = macro_issues([], [trigger])
    assert [(i["scope"], i["index"], i["path"]) for i in issues] == [("trigger", 0, "triggers[0]")]
    assert issues[0]["message"] == (
        f"'{field}' must be a number of seconds, {project_loader.SECONDS_MIN} or more"
    )


def test_the_macro_lint_takes_zero_more_and_a_missing_one():
    triggers = [
        {"id": "a", "type": "startup", "delay_seconds": 0, "debounce_seconds": 0.5,
         "cooldown_seconds": 60},
        {"id": "b", "type": "startup"},
    ]
    assert macro_issues([], triggers) == []


def test_each_time_out_of_bound_is_its_own_finding():
    trigger = {"id": "t", "type": "startup", "delay_seconds": -1, "cooldown_seconds": -1}
    refusal = validate_macro([], [trigger])
    assert refusal == (
        "Macro validation failed: "
        "triggers[0]: 'delay_seconds' must be a number of seconds, 0 or more; "
        "triggers[0]: 'cooldown_seconds' must be a number of seconds, 0 or more"
    )


# --- The AI assistant's macro doors --------------------------------------------------


def _tool_call(tool_name: str, tool_input: dict) -> dict:
    return {
        "type": AI_TOOL_CALL, "ts": _now_iso(), "seq": 1, "session": "test",
        "payload": {"request_id": "req-1", "tool_name": tool_name, "tool_input": tool_input},
    }


async def _drain() -> None:
    for _ in range(10):
        await asyncio.sleep(0)  # noqa: ASYNC110 — bounded drain, not a busy-wait
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not others:
            return
        await asyncio.wait(others, timeout=2.0)


@pytest.fixture
def engine():
    engine = MagicMock()
    engine.project = ProjectConfig(
        project=ProjectMeta(id="room", name="Room"),
        macros=[{"id": "existing", "name": "Existing",
                 "triggers": [{"id": "t_existing", "type": "startup"}]}],
    )
    engine._project_revision = 0

    async def _apply_edit(mutate):
        new_project = engine.project.model_copy(deep=True)
        mutate(new_project)
        engine.project = new_project
        engine._project_revision += 1
        return engine._project_revision

    engine.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    return engine


async def _ask(engine, tool_name: str, tool_input: dict) -> dict:
    agent = MagicMock()
    agent.send_message = AsyncMock()
    events = MagicMock()
    events.emit = AsyncMock()
    handler = AIToolHandler(agent, MagicMock(), events)
    with patch.object(handler, "_get_engine", return_value=engine), \
            patch("openavc.core.project_loader.save_project"):
        await handler.handle(_tool_call(tool_name, tool_input))
        await _drain()
    return agent.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_the_ai_is_told_why_a_new_macro_is_refused(engine):
    payload = await _ask(engine, "add_macro", {
        "id": "warm_up", "name": "Warm up",
        "triggers": [{"id": "t_warm", "type": "startup", "cooldown_seconds": -5}],
    })
    assert "'cooldown_seconds' must be a number of seconds, 0 or more" in str(payload)
    assert [m.id for m in engine.project.macros] == ["existing"]


@pytest.mark.asyncio
async def test_the_ai_is_told_why_a_changed_macro_is_refused(engine):
    payload = await _ask(engine, "update_macro", {
        "macro_id": "existing",
        "triggers": [{"id": "t_existing", "type": "startup", "delay_seconds": -1}],
    })
    assert "'delay_seconds' must be a number of seconds, 0 or more" in str(payload)
    assert engine.project.macros[0].triggers[0].delay_seconds == 0
