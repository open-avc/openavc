"""Tests for conditional macro steps, skip_if guards, and skip_if_offline."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from openavc.core.condition_eval import eval_operator
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore


@pytest.fixture
def state():
    return StateStore()


@pytest.fixture
def events():
    return EventBus()


@pytest.fixture
def devices():
    d = MagicMock()
    d.send_command = AsyncMock()
    return d


@pytest.fixture
def engine(state, events, devices):
    return MacroEngine(state, events, devices)


# ===== eval_operator (shared utility) =====


def test_eval_operator_eq():
    assert eval_operator("eq", "on", "on") is True
    assert eval_operator("eq", "on", "off") is False


def test_eval_operator_ne():
    assert eval_operator("ne", "on", "off") is True
    assert eval_operator("ne", "on", "on") is False


def test_eval_operator_gt():
    assert eval_operator("gt", 10, 5) is True
    assert eval_operator("gt", 5, 10) is False
    assert eval_operator("gt", None, 5) is False


def test_eval_operator_lt():
    assert eval_operator("lt", 5, 10) is True
    assert eval_operator("lt", 10, 5) is False


def test_eval_operator_gte():
    assert eval_operator("gte", 10, 10) is True
    assert eval_operator("gte", 9, 10) is False


def test_eval_operator_lte():
    assert eval_operator("lte", 10, 10) is True
    assert eval_operator("lte", 11, 10) is False


def test_eval_operator_truthy():
    assert eval_operator("truthy", "on", None) is True
    assert eval_operator("truthy", 1, None) is True
    assert eval_operator("truthy", "", None) is False
    assert eval_operator("truthy", 0, None) is False
    assert eval_operator("truthy", None, None) is False


def test_eval_operator_falsy():
    assert eval_operator("falsy", "", None) is True
    assert eval_operator("falsy", None, None) is True
    assert eval_operator("falsy", 0, None) is True
    assert eval_operator("falsy", "on", None) is False


def test_eval_operator_aliases():
    assert eval_operator("equals", "a", "a") is True
    assert eval_operator("==", "a", "a") is True
    assert eval_operator("!=", "a", "b") is True
    assert eval_operator("greater_than", 10, 5) is True


# ===== Conditional step: true branch =====


@pytest.mark.asyncio
async def test_conditional_true_runs_then_steps(engine, state, devices):
    state.set("device.projector.power", "off")
    steps = [
        {
            "action": "conditional",
            "condition": {"key": "device.projector.power", "operator": "eq", "value": "off"},
            "then_steps": [
                {"action": "device.command", "device": "projector", "command": "power_on"},
            ],
            "else_steps": [
                {"action": "device.command", "device": "projector", "command": "power_off"},
            ],
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("projector", "power_on", {})


# ===== Conditional step: false branch =====


@pytest.mark.asyncio
async def test_conditional_false_runs_else_steps(engine, state, devices):
    state.set("device.projector.power", "on")
    steps = [
        {
            "action": "conditional",
            "condition": {"key": "device.projector.power", "operator": "eq", "value": "off"},
            "then_steps": [
                {"action": "device.command", "device": "projector", "command": "power_on"},
            ],
            "else_steps": [
                {"action": "device.command", "device": "projector", "command": "power_off"},
            ],
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("projector", "power_off", {})


# ===== Conditional step: no else =====


@pytest.mark.asyncio
async def test_conditional_false_no_else_does_nothing(engine, state, devices):
    state.set("device.projector.power", "on")
    steps = [
        {
            "action": "conditional",
            "condition": {"key": "device.projector.power", "operator": "eq", "value": "off"},
            "then_steps": [
                {"action": "device.command", "device": "projector", "command": "power_on"},
            ],
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_not_called()


# ===== Conditional: no condition =====


@pytest.mark.asyncio
async def test_conditional_no_condition_skips(engine, devices):
    steps = [{"action": "conditional", "then_steps": [
        {"action": "device.command", "device": "projector", "command": "power_on"},
    ]}]
    await engine.execute_steps(steps)
    devices.send_command.assert_not_called()


# ===== Conditional: nested =====


@pytest.mark.asyncio
async def test_conditional_nested(engine, state, devices):
    state.set("var.mode", "presentation")
    state.set("device.projector.power", "off")
    steps = [
        {
            "action": "conditional",
            "condition": {"key": "var.mode", "operator": "eq", "value": "presentation"},
            "then_steps": [
                {
                    "action": "conditional",
                    "condition": {"key": "device.projector.power", "operator": "ne", "value": "on"},
                    "then_steps": [
                        {"action": "device.command", "device": "projector", "command": "power_on"},
                    ],
                },
            ],
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("projector", "power_on", {})


# ===== Conditional: nesting depth limit =====


@pytest.mark.asyncio
async def test_conditional_nesting_limit(engine, state, devices):
    """6 levels of nesting should exceed the limit of 5."""
    state.set("var.test", True)

    def make_nested(depth: int) -> dict:
        if depth == 0:
            return {"action": "device.command", "device": "projector", "command": "power_on"}
        return {
            "action": "conditional",
            "condition": {"key": "var.test", "operator": "truthy"},
            "then_steps": [make_nested(depth - 1)],
        }

    steps = [make_nested(6)]
    await engine.execute_steps(steps)
    # The innermost command should NOT execute because depth 6 > limit 5
    devices.send_command.assert_not_called()


@pytest.mark.asyncio
async def test_conditional_within_depth_limit(engine, state, devices):
    """5 levels of nesting should be allowed (exactly at limit)."""
    state.set("var.test", True)

    def make_nested(depth: int) -> dict:
        if depth == 0:
            return {"action": "device.command", "device": "projector", "command": "power_on"}
        return {
            "action": "conditional",
            "condition": {"key": "var.test", "operator": "truthy"},
            "then_steps": [make_nested(depth - 1)],
        }

    steps = [make_nested(5)]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once()


# ===== skip_if: true (step skipped) =====


@pytest.mark.asyncio
async def test_skip_if_true_skips_step(engine, state, devices):
    state.set("device.projector.power", "on")
    steps = [
        {
            "action": "device.command",
            "device": "projector",
            "command": "power_on",
            "skip_if": {"key": "device.projector.power", "operator": "eq", "value": "on"},
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_not_called()


# ===== skip_if: false (step runs) =====


@pytest.mark.asyncio
async def test_skip_if_false_runs_step(engine, state, devices):
    state.set("device.projector.power", "off")
    steps = [
        {
            "action": "device.command",
            "device": "projector",
            "command": "power_on",
            "skip_if": {"key": "device.projector.power", "operator": "eq", "value": "on"},
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("projector", "power_on", {})


# ===== skip_if_offline: device connected =====


@pytest.mark.asyncio
async def test_skip_if_offline_connected_runs(engine, state, devices):
    state.set("device.projector.connected", True)
    steps = [
        {
            "action": "device.command",
            "device": "projector",
            "command": "power_on",
            "skip_if_offline": True,
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("projector", "power_on", {})


# ===== skip_if_offline: device disconnected =====


@pytest.mark.asyncio
async def test_skip_if_offline_disconnected_skips(engine, state, devices):
    state.set("device.projector.connected", False)
    steps = [
        {
            "action": "device.command",
            "device": "projector",
            "command": "power_on",
            "skip_if_offline": True,
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_not_called()


# ===== skip_if_offline: no state key (device not in state = offline) =====


@pytest.mark.asyncio
async def test_skip_if_offline_no_state_skips(engine, state, devices):
    # No device.unknown.connected in state at all
    steps = [
        {
            "action": "device.command",
            "device": "unknown",
            "command": "power_on",
            "skip_if_offline": True,
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_not_called()


# ===== skip_if_offline: false (default, not skipped) =====


@pytest.mark.asyncio
async def test_skip_if_offline_false_does_not_skip(engine, state, devices):
    state.set("device.projector.connected", False)
    steps = [
        {
            "action": "device.command",
            "device": "projector",
            "command": "power_on",
            "skip_if_offline": False,
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once()


# ===== Progress event includes description =====


@pytest.mark.asyncio
async def test_progress_includes_description(engine, state, events, devices):
    state.set("device.projector.connected", True)
    progress_events = []

    async def capture(event, data):
        progress_events.append(data)

    events.on("macro.progress.*", capture)

    engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [
            {"action": "device.command", "device": "projector", "command": "power_on",
             "description": "Powering on projector"},
        ],
    }])
    await engine.execute("test_macro")

    assert len(progress_events) == 1
    assert progress_events[0]["description"] == "Powering on projector"


@pytest.mark.asyncio
async def test_progress_auto_description(engine, state, events, devices):
    progress_events = []

    async def capture(event, data):
        progress_events.append(data)

    events.on("macro.progress.*", capture)

    engine.load_macros([{
        "id": "test_macro",
        "name": "Test",
        "steps": [
            {"action": "device.command", "device": "projector", "command": "power_on"},
        ],
    }])
    await engine.execute("test_macro")

    assert len(progress_events) == 1
    assert "power_on" in progress_events[0]["description"]
    assert "projector" in progress_events[0]["description"]


# ===== macro.progress step_path (explicit branch tracking) =====
#
# The progress event carries an explicit step_path — the step's tree location,
# e.g. [1, "then", 0] — so the UI tracks the active step through conditional
# branches directly instead of guessing tree depth from total_steps deltas
# (which freezes when a branch's length equals its parent's).


async def _running_paths(engine, events, macro):
    """Run a macro and return the step_path of each 'running' progress event."""
    paths: list[list] = []

    async def capture(_event, data):
        if data.get("status") == "running":
            paths.append(data["step_path"])

    events.on("macro.progress.*", capture)
    engine.load_macros([macro])
    await engine.execute(macro["id"])
    return paths


@pytest.mark.asyncio
async def test_progress_step_path_equal_length_branch(engine, state, devices):
    """The degenerate case: a branch whose length equals its parent's.

    total_steps never changes across the parent->branch boundary here (both
    lists have 3 steps), so the old delta-inference froze the highlight. The
    explicit path must still descend into the branch and return to the parent.
    """
    state.set("var.go", True)
    macro = {
        "id": "m",
        "name": "Equal-length branch",
        "steps": [
            {"action": "state.set", "key": "var.a", "value": 1},
            {
                "action": "conditional",
                "condition": {"key": "var.go", "operator": "truthy"},
                "then_steps": [
                    {"action": "device.command", "device": "d", "command": "c0"},
                    {"action": "device.command", "device": "d", "command": "c1"},
                    {"action": "device.command", "device": "d", "command": "c2"},
                ],
            },
            {"action": "state.set", "key": "var.b", "value": 2},
        ],
    }
    paths = await _running_paths(engine, engine.events, macro)
    assert paths == [
        [0],
        [1],
        [1, "then", 0],
        [1, "then", 1],
        [1, "then", 2],
        [2],
    ]


@pytest.mark.asyncio
async def test_progress_step_path_deeply_nested(engine, state, devices):
    """Nested conditionals where every list has length 1 — the delta scheme
    could never detect the branch entries (total_steps stays 1 throughout)."""
    state.set("var.go", True)
    macro = {
        "id": "m",
        "name": "Deep nest",
        "steps": [
            {
                "action": "conditional",
                "condition": {"key": "var.go", "operator": "truthy"},
                "then_steps": [
                    {
                        "action": "conditional",
                        "condition": {"key": "var.go", "operator": "truthy"},
                        "then_steps": [
                            {"action": "device.command", "device": "d", "command": "deep"},
                        ],
                    },
                ],
            },
        ],
    }
    paths = await _running_paths(engine, engine.events, macro)
    assert paths == [
        [0],
        [0, "then", 0],
        [0, "then", 0, "then", 0],
    ]


@pytest.mark.asyncio
async def test_progress_step_path_else_branch_and_return(engine, state, devices):
    """A false condition descends the else branch, then execution returns to
    the next top-level step — the path must reflect the else segment and pop
    back to the parent level."""
    state.set("var.go", False)
    macro = {
        "id": "m",
        "name": "Else + return",
        "steps": [
            {
                "action": "conditional",
                "condition": {"key": "var.go", "operator": "truthy"},
                "then_steps": [
                    {"action": "device.command", "device": "d", "command": "then0"},
                ],
                "else_steps": [
                    {"action": "device.command", "device": "d", "command": "else0"},
                ],
            },
            {"action": "state.set", "key": "var.after", "value": 1},
        ],
    }
    paths = await _running_paths(engine, engine.events, macro)
    assert paths == [
        [0],
        [0, "else", 0],
        [1],
    ]


# ===== Project loader: StepCondition model =====


def test_step_condition_model():
    from openavc.core.project_loader import StepCondition
    cond = StepCondition(key="var.x", operator="eq", value=True)
    assert cond.key == "var.x"
    assert cond.operator == "eq"
    assert cond.value is True


def test_macro_step_with_conditional_fields():
    from openavc.core.project_loader import MacroStep, StepCondition
    step = MacroStep(
        action="conditional",
        condition=StepCondition(key="var.x", operator="eq", value=True),
        then_steps=[MacroStep(action="delay", seconds=1.0)],
        else_steps=[MacroStep(action="delay", seconds=2.0)],
    )
    assert step.action == "conditional"
    assert step.condition.key == "var.x"
    assert len(step.then_steps) == 1
    assert len(step.else_steps) == 1


def test_macro_step_with_skip_if():
    from openavc.core.project_loader import MacroStep, StepCondition
    step = MacroStep(
        action="device.command",
        device="projector",
        command="power_on",
        skip_if=StepCondition(key="device.projector.power", operator="eq", value="on"),
    )
    assert step.skip_if.key == "device.projector.power"


def test_macro_step_with_skip_if_offline():
    from openavc.core.project_loader import MacroStep
    step = MacroStep(
        action="device.command",
        device="projector",
        command="power_on",
        skip_if_offline=True,
    )
    assert step.skip_if_offline is True


def test_macro_step_defaults():
    from openavc.core.project_loader import MacroStep
    step = MacroStep(action="delay", seconds=1.0)
    assert step.condition is None
    assert step.then_steps is None
    assert step.else_steps is None
    assert step.skip_if is None
    assert step.skip_if_offline is False
    assert step.description is None


# ===== Dynamic parameter references ($state_key) =====


@pytest.mark.asyncio
async def test_resolve_dynamic_params(engine, state, devices):
    """$var.x resolves to current state value."""
    state.set("var.target_volume", 75)
    steps = [
        {
            "action": "device.command",
            "device": "dsp",
            "command": "set_volume",
            "params": {"level": "$var.target_volume"},
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("dsp", "set_volume", {"level": 75})


@pytest.mark.asyncio
async def test_resolve_static_params(engine, state, devices):
    """Params without $ pass through unchanged."""
    steps = [
        {
            "action": "device.command",
            "device": "dsp",
            "command": "set_volume",
            "params": {"level": 50},
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("dsp", "set_volume", {"level": 50})


@pytest.mark.asyncio
async def test_resolve_missing_key(engine, state, devices):
    """$var.nonexistent resolves to None."""
    steps = [
        {
            "action": "device.command",
            "device": "dsp",
            "command": "set_volume",
            "params": {"level": "$var.nonexistent"},
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("dsp", "set_volume", {"level": None})


@pytest.mark.asyncio
async def test_resolve_mixed_params(engine, state, devices):
    """Mix of static and dynamic params."""
    state.set("var.source", "hdmi1")
    steps = [
        {
            "action": "device.command",
            "device": "switcher",
            "command": "set_route",
            "params": {"input": "$var.source", "output": 1},
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("switcher", "set_route", {"input": "hdmi1", "output": 1})


@pytest.mark.asyncio
async def test_resolve_non_string_passthrough(engine, state, devices):
    """Non-string params (int, bool) pass through unchanged."""
    steps = [
        {
            "action": "device.command",
            "device": "relay",
            "command": "set_channel",
            "params": {"channel": 3, "state": True},
        }
    ]
    await engine.execute_steps(steps)
    devices.send_command.assert_called_once_with("relay", "set_channel", {"channel": 3, "state": True})


@pytest.mark.asyncio
async def test_resolve_state_set_value(engine, state):
    """state.set with $ in value field resolves the reference."""
    state.set("var.source_name", "HDMI 1")
    steps = [
        {
            "action": "state.set",
            "key": "var.last_source",
            "value": "$var.source_name",
        }
    ]
    await engine.execute_steps(steps)
    assert state.get("var.last_source") == "HDMI 1"


@pytest.mark.asyncio
async def test_resolve_state_set_static_value(engine, state):
    """state.set without $ passes value unchanged."""
    steps = [
        {
            "action": "state.set",
            "key": "var.mode",
            "value": "presentation",
        }
    ]
    await engine.execute_steps(steps)
    assert state.get("var.mode") == "presentation"


@pytest.mark.asyncio
async def test_resolve_dollar_string_not_state_key(engine, state, devices):
    """A string like '$' alone or '$$' should not crash."""
    steps = [
        {
            "action": "device.command",
            "device": "dsp",
            "command": "test",
            "params": {"val": "$"},
        }
    ]
    await engine.execute_steps(steps)
    # "$" with nothing after it resolves to state key "" which returns None
    devices.send_command.assert_called_once_with("dsp", "test", {"val": None})
