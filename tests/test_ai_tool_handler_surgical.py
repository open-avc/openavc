"""Tests for surgical AI tool handlers — focused CRUD tools.

Tests the handlers: get_project_summary, get_macro, get_ui_page,
add_device, add/update/delete_variable, add/update/delete_macro,
add/delete_ui_page, add/update/delete_ui_elements.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.cloud.ai_tool_handler import AIToolHandler
from server.cloud.protocol import AI_TOOL_CALL, _now_iso


def _make_tool_call_msg(tool_name, tool_input=None, request_id="req-1"):
    """Build a mock AI_TOOL_CALL message."""
    return {
        "type": AI_TOOL_CALL,
        "ts": _now_iso(),
        "seq": 1,
        "session": "test",
        "payload": {
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_input": tool_input or {},
        },
    }


def _get_result_payload(mock_agent):
    """Extract the result payload from the last send_message call."""
    return mock_agent.send_message.call_args[0][1]


async def _drain():
    """Drain the background tool task that handle() spawns via create_task.

    The write tools now persist via save_project_async (asyncio.to_thread),
    so a single `await asyncio.sleep(0)` no longer settles the task before it
    has hopped to the worker thread and back. Wait for the spawned task(s) to
    actually finish so the result has been sent."""
    for _ in range(10):
        await asyncio.sleep(0)  # noqa: ASYNC110 — bounded drain, not a busy-wait
        others = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        if not others:
            return
        await asyncio.wait(others, timeout=2.0)


def _make_project():
    """Create a mock ProjectConfig with realistic data."""
    from server.core.project_loader import (
        ProjectConfig, ProjectMeta, DeviceConfig, VariableConfig,
        MacroConfig, MacroStep, TriggerConfig, UIConfig, UIPage,
        UIElement, Layout, Placement, ScriptConfig,
    )
    return ProjectConfig(
        project=ProjectMeta(id="test_project", name="Test Room"),
        devices=[
            DeviceConfig(id="projector1", driver="pjlink", name="Main Projector", config={"host": "192.168.1.10"}, group="displays"),
            DeviceConfig(id="switcher1", driver="extron_sis", name="HDMI Switch", config={"host": "192.168.1.20"}),
        ],
        variables=[
            VariableConfig(id="room_mode", type="string", default="normal", label="Room Mode", dashboard=True),
            VariableConfig(id="is_occupied", type="boolean", default=False),
        ],
        macros=[
            MacroConfig(
                id="all_off", name="All Off",
                steps=[
                    MacroStep(action="device.command", device="projector1", command="power_off"),
                    MacroStep(action="delay", seconds=2.0),
                ],
                triggers=[
                    TriggerConfig(id="trig_1", type="state_change", state_key="var.room_mode", state_value="off"),
                ],
            ),
            MacroConfig(id="presentation", name="Presentation Mode", steps=[]),
        ],
        ui=UIConfig(pages=[
            UIPage(id="main", name="Main Control", elements=[
                UIElement(id="btn_on", type="button", label="System On"),
                UIElement(id="btn_off", type="button", label="System Off"),
                UIElement(id="vol_slider", type="slider", label="Volume"),
            ], layouts=[Layout(id="landscape", orientation="landscape", primary=True, placements={
                "btn_on": Placement(x=0.625, y=1.0, w=15.9375, h=11.375),
                "btn_off": Placement(x=17.1875, y=1.0, w=15.9375, h=11.375),
                "vol_slider": Placement(x=0.625, y=26.5, w=48.4375, h=11.375),
            })]),
            UIPage(id="settings", name="Settings", elements=[]),
        ]),
        scripts=[
            ScriptConfig(id="auto_lights", file="auto_lights.py", description="Auto lighting"),
        ],
    )


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.send_message = AsyncMock()
    agent.state = MagicMock()
    agent.state.snapshot.return_value = {"device.projector1.power": "on"}
    agent.state.get.return_value = "on"
    agent.state.set = MagicMock()
    return agent


@pytest.fixture
def mock_devices():
    devices = MagicMock()
    devices.list_devices.return_value = []
    devices.add_device = AsyncMock()
    devices.remove_device = AsyncMock()
    devices.send_command = AsyncMock()
    return devices


@pytest.fixture
def mock_events():
    events = MagicMock()
    events.emit = AsyncMock()
    return events


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.project = _make_project()
    engine.project_path = MagicMock()
    engine.project_path.parent = MagicMock()
    engine.devices = MagicMock()
    engine.devices.add_device = AsyncMock()
    engine.broadcast_ws = AsyncMock()
    engine._project_revision = 0

    # The tools hand a mutate callback to apply_project_edit; mirror the
    # seam's copy-mutate-swap-and-bump contract (the reconcile itself is
    # pinned by the engine tests).
    async def _apply_edit(mutate):
        new_project = engine.project.model_copy(deep=True)
        mutate(new_project)
        engine.project = new_project
        engine._project_revision += 1
        return engine._project_revision

    engine.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    return engine


@pytest.fixture(autouse=True)
def _patch_save_project():
    """Patch save_project globally so write tools don't hit the filesystem."""
    with patch("server.core.project_loader.save_project"):
        yield


@pytest.fixture
def handler(mock_agent, mock_devices, mock_events):
    return AIToolHandler(mock_agent, mock_devices, mock_events)


# ===== READ TOOLS =====


@pytest.mark.asyncio
async def test_get_project_summary(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_project_summary")
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    result = payload["result"]

    # Project meta
    assert result["project"]["name"] == "Test Room"

    # Devices — lightweight (id/name/driver, no config)
    assert len(result["devices"]) == 2
    d = result["devices"][0]
    assert d["id"] == "projector1"
    assert d["driver"] == "pjlink"
    assert "config" not in d  # No full config in summary

    # Variables — full
    assert len(result["variables"]) == 2
    assert result["variables"][0]["id"] == "room_mode"
    assert result["variables"][0]["default"] == "normal"

    # Macros — id/name/counts only
    assert len(result["macros"]) == 2
    m = result["macros"][0]
    assert m["id"] == "all_off"
    assert m["step_count"] == 2
    assert m["trigger_count"] == 1
    assert "steps" not in m  # No full steps in summary

    # Pages — id/name/element_ids only
    assert len(result["pages"]) == 2
    p = result["pages"][0]
    assert p["id"] == "main"
    assert set(p["element_ids"]) == {"btn_on", "btn_off", "vol_slider"}

    # Scripts
    assert len(result["scripts"]) == 1
    assert result["scripts"][0]["id"] == "auto_lights"


@pytest.mark.asyncio
async def test_get_project_summary_no_project(handler, mock_agent):
    with patch.object(handler, "_get_engine", return_value=None):
        msg = _make_tool_call_msg("get_project_summary")
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "error" in payload["result"]


@pytest.mark.asyncio
async def test_get_macro(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_macro", {"macro_id": "all_off"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    result = payload["result"]
    assert result["id"] == "all_off"
    assert result["name"] == "All Off"
    assert len(result["steps"]) == 2
    assert len(result["triggers"]) == 1


@pytest.mark.asyncio
async def test_get_macro_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_macro", {"macro_id": "nonexistent"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "error" in payload["result"]


@pytest.mark.asyncio
async def test_get_ui_page(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_ui_page", {"page_id": "main"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    result = payload["result"]
    assert result["id"] == "main"
    assert result["name"] == "Main Control"
    assert len(result["elements"]) == 3
    assert result["layouts"][0]["primary"] is True


@pytest.mark.asyncio
async def test_get_ui_page_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_ui_page", {"page_id": "nonexistent"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "error" in payload["result"]


# ===== DEVICE TOOLS =====


@pytest.mark.asyncio
async def test_add_device(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_device", {
                "id": "display1",
                "driver": "samsung_mdc",
                "name": "Main Display",
                "config": {"host": "192.168.1.30", "port": 1515},
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"
    assert payload["result"]["id"] == "display1"

    # Device was added to project, with connection fields split out
    assert any(d.id == "display1" for d in mock_engine.project.devices)
    assert mock_engine.project.connections["display1"]["host"] == "192.168.1.30"

    # Applied through the seam — the devices reconcile hot-adds the runtime
    # device from the resolved config
    mock_engine.apply_project_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_device_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_device", {
            "id": "projector1",  # already exists
            "driver": "pjlink",
            "name": "Duplicate",
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "already exists" in payload["result"]["error"]


# ===== VARIABLE TOOLS =====


@pytest.mark.asyncio
async def test_add_variable(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_variable", {
                "id": "volume_level",
                "type": "number",
                "default": 50,
                "label": "Volume Level",
                "dashboard": True,
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"

    # Variable was added
    assert any(v.id == "volume_level" for v in mock_engine.project.variables)

    # Applied through the seam — the variables reconcile seeds the default
    # into state (the old manual state.set is gone)
    mock_engine.apply_project_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_variable_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_variable", {"id": "room_mode"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "already exists" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_update_variable(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("update_variable", {
                "id": "room_mode",
                "label": "Current Mode",
                "dashboard": True,
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "updated"

    # Check that the variable was updated in-place
    var = next(v for v in mock_engine.project.variables if v.id == "room_mode")
    assert var.label == "Current Mode"
    assert var.dashboard is True
    # Type should remain unchanged
    assert var.type == "string"


@pytest.mark.asyncio
async def test_update_variable_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_variable", {"id": "nonexistent"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "not found" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_delete_variable(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("delete_variable", {"id": "is_occupied"})
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "deleted"
    assert not any(v.id == "is_occupied" for v in mock_engine.project.variables)


@pytest.mark.asyncio
async def test_delete_variable_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("delete_variable", {"id": "nonexistent"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "not found" in payload["result"]["error"]


# ===== MACRO TOOLS =====


@pytest.mark.asyncio
async def test_add_macro(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_macro", {
                "id": "lights_on",
                "name": "Lights On",
                "steps": [
                    {"action": "device.command", "device": "lights1", "command": "on"},
                    {"action": "delay", "seconds": 1.0},
                ],
                "stop_on_error": True,
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"

    # Macro was added
    macro = next(m for m in mock_engine.project.macros if m.id == "lights_on")
    assert macro.name == "Lights On"
    assert len(macro.steps) == 2
    assert macro.stop_on_error is True

    # Applied through the seam (the macros reconcile registers triggers)
    mock_engine.apply_project_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_macro_with_cancel_group(handler, mock_agent, mock_engine):
    """A11: add_macro must persist cancel_group when provided."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_macro", {
                "id": "system_on",
                "name": "System On",
                "steps": [{"action": "device.command", "device": "projector1", "command": "power_on"}],
                "cancel_group": "system_power",
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    macro = next(m for m in mock_engine.project.macros if m.id == "system_on")
    assert macro.cancel_group == "system_power"


@pytest.mark.asyncio
async def test_add_macro_with_ui_navigate_step(handler, mock_agent, mock_engine):
    """M-133: the AI can author a macro containing a ui.navigate step (the
    runtime supports it; the validator used to reject it)."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_macro", {
                "id": "go_controls",
                "name": "Go To Controls",
                "steps": [
                    {"action": "device.command", "device": "projector1", "command": "power_on"},
                    {"action": "ui.navigate", "page": "controls"},
                ],
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"
    macro = next(m for m in mock_engine.project.macros if m.id == "go_controls")
    assert macro.steps[1].action == "ui.navigate"
    assert macro.steps[1].page == "controls"


@pytest.mark.asyncio
async def test_add_macro_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_macro", {"id": "all_off", "name": "Duplicate"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "already exists" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_update_macro(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("update_macro", {
                "macro_id": "all_off",
                "name": "Everything Off",
                "steps": [
                    {"action": "device.command", "device": "projector1", "command": "power_off"},
                ],
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "updated"

    macro = next(m for m in mock_engine.project.macros if m.id == "all_off")
    assert macro.name == "Everything Off"
    assert len(macro.steps) == 1
    # Triggers should remain from original since not specified in update
    assert len(macro.triggers) == 1


@pytest.mark.asyncio
async def test_update_macro_preserves_existing_cancel_group(handler, mock_agent, mock_engine):
    """A11: update_macro must keep existing cancel_group when not specified."""
    # Seed an existing cancel_group on the all_off macro.
    target = next(m for m in mock_engine.project.macros if m.id == "all_off")
    target.cancel_group = "system_power"

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            # Only change the name — cancel_group not provided.
            msg = _make_tool_call_msg("update_macro", {
                "macro_id": "all_off",
                "name": "Everything Off",
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    macro = next(m for m in mock_engine.project.macros if m.id == "all_off")
    assert macro.cancel_group == "system_power", "existing cancel_group was wiped"


@pytest.mark.asyncio
async def test_update_macro_sets_cancel_group(handler, mock_agent, mock_engine):
    """A11: update_macro must apply cancel_group when explicitly set."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("update_macro", {
                "macro_id": "all_off",
                "cancel_group": "system_power",
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    macro = next(m for m in mock_engine.project.macros if m.id == "all_off")
    assert macro.cancel_group == "system_power"


@pytest.mark.asyncio
async def test_update_macro_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_macro", {"macro_id": "nonexistent"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "not found" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_add_macro_accepts_a_plugin_registered_action(handler, mock_agent, mock_engine):
    """A plugin's macro action is as runnable as a built-in one.

    The validator's action list used to be a frozen nine, so a step naming a
    plugin action was rejected as malformed — which also made every macro
    containing one un-editable, since update_macro revalidates the steps.
    """
    mock_engine.macros.plugin_action_types.return_value = frozenset({"acme.flash"})
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_macro", {
            "id": "attention",
            "name": "Attention",
            "steps": [{"action": "acme.flash", "params": {"times": 3}}],
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True, payload["result"]
    macro = next(m for m in mock_engine.project.macros if m.id == "attention")
    assert macro.steps[0].action == "acme.flash"


@pytest.mark.asyncio
async def test_add_macro_rejects_an_unregistered_action(handler, mock_agent, mock_engine):
    """The gate still closes on a genuine typo — no plugin declares this."""
    mock_engine.macros.plugin_action_types.return_value = frozenset()
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_macro", {
            "id": "oops",
            "steps": [{"action": "devicecommand", "device": "d1", "command": "on"}],
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "not valid" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_update_macro_can_edit_a_macro_using_a_plugin_action(
    handler, mock_agent, mock_engine
):
    mock_engine.macros.plugin_action_types.return_value = frozenset({"acme.flash"})
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_macro", {
            "macro_id": "all_off",
            "steps": [
                {"action": "acme.flash", "params": {}},
                {"action": "delay", "seconds": 0.5},
            ],
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True, payload["result"]
    macro = next(m for m in mock_engine.project.macros if m.id == "all_off")
    assert len(macro.steps) == 2


@pytest.mark.asyncio
async def test_delete_macro(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("delete_macro", {"macro_id": "presentation"})
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "deleted"
    assert not any(m.id == "presentation" for m in mock_engine.project.macros)


@pytest.mark.asyncio
async def test_delete_macro_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("delete_macro", {"macro_id": "nonexistent"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "not found" in payload["result"]["error"]


# ===== UI PAGE TOOLS =====


@pytest.mark.asyncio
async def test_add_ui_page(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_ui_page", {
                "id": "lighting",
                "name": "Lighting Control",
                "snap": {"x": 12.5, "y": 20.0},
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"

    page = next(p for p in mock_engine.project.ui.pages if p.id == "lighting")
    assert page.name == "Lighting Control"
    assert page.snap.x == 12.5


@pytest.mark.asyncio
async def test_add_ui_page_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_ui_page", {"id": "main", "name": "Duplicate"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "already exists" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_delete_ui_page(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("delete_ui_page", {"page_id": "settings"})
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "deleted"
    assert not any(p.id == "settings" for p in mock_engine.project.ui.pages)


@pytest.mark.asyncio
async def test_delete_ui_page_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("delete_ui_page", {"page_id": "nonexistent"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "not found" in payload["result"]["error"]


# ===== UI ELEMENT TOOLS =====


@pytest.mark.asyncio
async def test_add_ui_elements(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_ui_elements", {
                "page_id": "main",
                "elements": [
                    {"id": "led_power", "type": "status_led", "label": "Power",
                     },
                    {"id": "lbl_status", "type": "label", "text": "Ready",
                     },
                ],
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"
    assert set(payload["result"]["element_ids"]) == {"led_power", "lbl_status"}

    # Elements were added to the page
    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    assert len(page.elements) == 5  # 3 original + 2 new


@pytest.mark.asyncio
async def test_add_ui_elements_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_ui_elements", {
            "page_id": "main",
            "elements": [
                {"id": "btn_on", "type": "button", "label": "Duplicate"},  # already exists
            ],
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "already exists" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_add_ui_elements_page_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_ui_elements", {
            "page_id": "nonexistent",
            "elements": [{"id": "btn1", "type": "button"}],
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "not found" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_update_ui_element(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("update_ui_element", {
                "element_id": "btn_on",
                "label": "Power On",
                "style": {"bg_color": "#4CAF50"},
                "bindings": {"do": {"press": [{"action": "macro", "macro": "all_on"}]}},
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "updated"

    # Find element and verify updates
    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    el = next(e for e in page.elements if e.id == "btn_on")
    assert el.label == "Power On"
    assert el.style == {"bg_color": "#4CAF50"}
    assert el.bindings["do"]["press"][0]["action"] == "macro"


@pytest.mark.asyncio
async def test_update_ui_element_placement(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("update_ui_element", {
                "element_id": "btn_on",
                "placement": {"x": 30.0, "y": 12.5, "w": 25.0, "h": 22.75},
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True

    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    el = next(e for e in page.elements if e.id == "btn_on")
    place = page.layouts[0].placements[el.id]
    assert place.x == 30.0
    assert place.y == 12.5
    assert place.w == 25.0


@pytest.mark.asyncio
async def test_update_ui_element_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_ui_element", {
            "element_id": "nonexistent",
            "label": "Nope",
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "not found" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_delete_ui_elements(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("delete_ui_elements", {
                "element_ids": ["btn_on", "btn_off"],
            })
            await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "deleted"
    assert set(payload["result"]["element_ids"]) == {"btn_on", "btn_off"}

    # Only vol_slider should remain
    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    assert len(page.elements) == 1
    assert page.elements[0].id == "vol_slider"


@pytest.mark.asyncio
async def test_delete_ui_elements_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("delete_ui_elements", {
            "element_ids": ["nonexistent1", "nonexistent2"],
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "No matching elements" in payload["result"]["error"]


# ===== UI VALIDATION & SIMULATION (H-079, M-134..M-137) =====


@pytest.mark.asyncio
async def test_update_ui_element_rejects_non_dict_bindings(handler, mock_engine):
    """H-079: a non-dict bindings value must be rejected, not assigned raw
    (UIElement has no validate_assignment, so a raw assign would persist a
    structurally invalid element)."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._update_ui_element({
            "element_id": "btn_on",
            "bindings": ["not", "a", "dict"],
        })

    assert "error" in result
    assert "must be an object" in result["error"]
    # The element's bindings were not corrupted.
    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    el = next(e for e in page.elements if e.id == "btn_on")
    assert isinstance(el.bindings, dict)


@pytest.mark.asyncio
async def test_add_ui_page_validates_inline_element_bindings(handler, mock_engine):
    """M-134: inline elements get the same binding validation as add_ui_elements."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._add_ui_page({
                "id": "bad_page",
                "name": "Bad",
                "elements": [
                    {"id": "b1", "type": "button",
                     "bindings": {"do": {"press": [{"action": "macro"}]}}},  # missing 'macro'
                ],
            })

    assert "error" in result
    assert "macro" in result["error"]
    # The invalid page was not added.
    assert not any(p.id == "bad_page" for p in mock_engine.project.ui.pages)


@pytest.mark.asyncio
async def test_add_ui_page_accepts_valid_inline_bindings(handler, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._add_ui_page({
                "id": "good_page",
                "name": "Good",
                "elements": [
                    {"id": "b1", "type": "button",
                     "bindings": {"do": {"press": {"action": "navigate", "page": "main"}}}},
                ],
            })

    assert result.get("status") == "created"
    page = next(p for p in mock_engine.project.ui.pages if p.id == "good_page")
    # Bindings were normalized (do.press wrapped as a list of action objects).
    assert isinstance(page.elements[0].bindings["do"]["press"], list)


@pytest.mark.asyncio
async def test_simulate_navigate_broadcasts_ui_navigate(handler, mock_engine):
    """M-135: simulate navigate must broadcast ui.navigate so panels switch."""
    mock_engine.events.emit = AsyncMock()
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._simulate_ui_action({"action": "navigate", "page_id": "main"})

    assert result["success"] is True
    mock_engine.broadcast_ws.assert_awaited_once_with({"type": "ui.navigate", "page_id": "main"})


@pytest.mark.asyncio
async def test_simulate_action_filters_background_state_changes(handler, mock_agent, mock_engine):
    """M-136: only changes the action plausibly caused are reported — background
    activity (heartbeat/system/cloud/ai/isc/discovered) is filtered out."""
    from server.core.state_store import StateStore

    store = StateStore()
    mock_agent.state = store  # real store: subscribe/unsubscribe + listener fire

    async def fake_handle(action, element_id, *args):
        store.set("device.projector1.power", "on", source="device.projector1")  # real effect
        store.set("system.cpu_percent", 42, source="heartbeat")                  # background noise
        store.set("var.other_tool", "x", source="ai")                            # concurrent tool

    mock_engine.handle_ui_event = fake_handle
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._simulate_ui_action({"action": "press", "element_id": "btn_on"})

    keys = {c["key"] for c in result["state_changes"]}
    assert "device.projector1.power" in keys
    assert "system.cpu_percent" not in keys
    assert "var.other_tool" not in keys


@pytest.mark.asyncio
async def test_update_ui_page_snap_partial_merge(handler, mock_engine):
    """M-137: a partial grid update keeps omitted fields + forward-compat keys."""
    from server.core.project_loader import SnapConfig

    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    page.snap = SnapConfig(enabled=True, x=12.5, y=20.0, custom_hint="keep-me")  # non-default + extra

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._update_ui_page({"page_id": "main", "snap": {"x": 25.0}})

    assert result.get("status") == "updated"
    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    assert page.snap.x == 25.0           # applied
    assert page.snap.y == 20.0           # NOT reset to the default (12.5)
    assert page.snap.model_dump().get("custom_hint") == "keep-me"  # forward-compat survived


@pytest.mark.asyncio
async def test_update_ui_element_placement_partial_merge(handler, mock_engine):
    """M-137: a partial placement update keeps omitted fields (no snap to 0)."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            # btn_on starts at col=1,row=1,col_span=2,row_span=1; move col only.
            result = await handler._update_ui_element({"element_id": "btn_on", "placement": {"x": 50.0}})

    assert result.get("status") == "updated"
    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    place = page.layouts[0].placements["btn_on"]
    assert place.x == 50.0        # applied
    assert place.w == 15.9375     # NOT reset to the default (100.0)
    assert place.y == 1.0


# ===== LAYOUT ENGINE (project format 0.8.0) =====
# Geometry moved off the element and onto the page's arrangements, so the tools
# had to learn a shape the model has never spoken. These pin the parts a schema
# alone cannot teach: where a new control's box lands, what a variant stores,
# and that reparenting converts rather than teleports.


def _page(mock_engine, page_id="main"):
    return next(p for p in mock_engine.project.ui.pages if p.id == page_id)


def _layout(page, layout_id):
    return next(lay for lay in page.layouts if lay.id == layout_id)


def _drawn(page, element_id, layout_id=None):
    """An element's box in page percentages, container nesting flattened.

    Computed here from the stored numbers rather than by calling the helper
    under test, so a reparent that quietly changed the drawn position fails.
    """
    from server.core.project_loader import Placement

    placements: dict = {}
    by_id = {lay.id: lay for lay in page.layouts}
    chain = []
    cursor = by_id.get(layout_id or next(lay.id for lay in page.layouts if lay.primary))
    while cursor is not None:
        chain.insert(0, cursor)
        cursor = by_id.get(cursor.inherits) if cursor.inherits else None
    for lay in chain:
        placements.update(lay.placements)

    elements = {el.id: el for el in page.elements}
    box = placements.get(element_id, Placement())
    parent_id = elements[element_id].parent
    x, y, w, h = box.x, box.y, box.w, box.h
    while parent_id:
        base = placements[parent_id]
        x = base.x + (x / 100) * base.w
        y = base.y + (y / 100) * base.h
        w = (w / 100) * base.w
        h = (h / 100) * base.h
        parent_id = elements[parent_id].parent
    return (round(x, 6), round(y, 6), round(w, 6), round(h, 6))


@pytest.mark.asyncio
async def test_add_ui_elements_places_into_the_primary_layout(handler, mock_engine):
    """A new control's box belongs in the primary -- every variant inherits it."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._add_ui_elements({
                "page_id": "main",
                "elements": [{
                    "id": "btn_mute", "type": "button", "label": "Mute",
                    "placement": {"x": 60.0, "y": 12.5, "w": 20.0, "h": 10.0},
                }],
            })

    assert result["status"] == "created"
    page = _page(mock_engine)
    assert any(el.id == "btn_mute" for el in page.elements)
    place = _layout(page, "landscape").placements["btn_mute"]
    assert (place.x, place.y, place.w, place.h) == (60.0, 12.5, 20.0, 10.0)
    # The box does not ride along on the element -- that is the whole 0.8.0 split.
    element = next(el for el in page.elements if el.id == "btn_mute")
    assert "placement" not in element.model_dump()


@pytest.mark.asyncio
async def test_add_ui_elements_rejects_grid_area(handler, mock_engine):
    """A stale prompt gets told, not silently parked in the forward-compat extras."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._add_ui_elements({
            "page_id": "main",
            "elements": [{"id": "btn_old", "type": "button",
                          "grid_area": {"col": 1, "row": 1, "col_span": 2, "row_span": 1}}],
        })

    assert "grid_area" in result["error"]
    assert "placement" in result["error"]
    assert not any(el.id == "btn_old" for el in _page(mock_engine).elements)


@pytest.mark.asyncio
async def test_add_ui_elements_rejects_a_container_that_is_not_there(handler, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._add_ui_elements({
            "page_id": "main",
            "elements": [{"id": "btn_lost", "type": "button", "parent": "no_such_group"}],
        })

    assert "no_such_group" in result["error"]


@pytest.mark.asyncio
async def test_add_ui_page_with_inline_placements_and_a_variant(handler, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._add_ui_page({
                "id": "lighting", "name": "Lighting",
                "elements": [{"id": "btn_scene", "type": "button",
                              "placement": {"x": 10.0, "y": 10.0, "w": 30.0, "h": 20.0}}],
                "layouts": [
                    {"id": "landscape", "orientation": "landscape", "primary": True},
                    {"id": "portrait", "orientation": "portrait", "inherits": "landscape"},
                ],
            })

    assert result["status"] == "created"
    page = _page(mock_engine, "lighting")
    assert [lay.id for lay in page.layouts] == ["landscape", "portrait"]
    assert _layout(page, "landscape").placements["btn_scene"].w == 30.0
    # The variant stores only what moved, and nothing has moved yet.
    assert _layout(page, "portrait").placements == {}


@pytest.mark.asyncio
async def test_update_ui_page_adds_a_variant_that_inherits_the_primary(handler, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._update_ui_page({
                "page_id": "main",
                "layouts": [{"id": "portrait", "orientation": "portrait"}],
            })

    assert "layouts" in result["changed"]
    page = _page(mock_engine)
    variant = _layout(page, "portrait")
    assert variant.inherits == "landscape"   # defaulted to the primary
    assert variant.primary is False
    assert sum(1 for lay in page.layouts if lay.primary) == 1


@pytest.mark.asyncio
async def test_update_ui_page_refuses_to_move_the_primary(handler, mock_engine):
    """The primary is what an unmatched screen falls back to; it is not a toggle."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._update_ui_page({
            "page_id": "main",
            "layouts": [{"id": "landscape", "primary": False}],
        })

    assert "primary" in result["error"]
    assert _layout(_page(mock_engine), "landscape").primary is True


@pytest.mark.asyncio
async def test_update_ui_element_writes_a_variant_delta_only(handler, mock_engine):
    from server.core.project_loader import Layout

    page = _page(mock_engine)
    page.layouts.append(Layout(id="portrait", orientation="portrait", inherits="landscape"))
    before = dict(_layout(page, "landscape").placements)

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._update_ui_element({
                "element_id": "vol_slider", "layout_id": "portrait",
                "placement": {"x": 5.0, "y": 70.0, "w": 90.0, "h": 10.0},
            })

    assert result["status"] == "updated"
    page = _page(mock_engine)
    assert list(_layout(page, "portrait").placements) == ["vol_slider"]
    assert _layout(page, "landscape").placements == before   # the primary did not move


@pytest.mark.asyncio
async def test_update_ui_element_variant_placement_merges_over_the_inherited_box(handler, mock_engine):
    """A partial edit in a variant keeps what it inherited, not the model defaults."""
    from server.core.project_loader import Layout

    _page(mock_engine).layouts.append(
        Layout(id="portrait", orientation="portrait", inherits="landscape")
    )

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            await handler._update_ui_element({
                "element_id": "btn_on", "layout_id": "portrait", "placement": {"y": 80.0},
            })

    place = _layout(_page(mock_engine), "portrait").placements["btn_on"]
    assert place.y == 80.0
    assert place.w == 15.9375   # inherited from landscape, not reset to 100
    assert place.x == 0.625


@pytest.mark.asyncio
async def test_update_ui_element_unknown_layout_names_the_ones_that_exist(handler, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._update_ui_element({
            "element_id": "btn_on", "layout_id": "tablet", "placement": {"x": 1.0},
        })

    assert "tablet" in result["error"]
    assert "landscape" in result["error"]


@pytest.mark.asyncio
async def test_update_ui_element_hides_in_one_arrangement_only(handler, mock_engine):
    from server.core.project_loader import Layout

    _page(mock_engine).layouts.append(
        Layout(id="portrait", orientation="portrait", inherits="landscape")
    )

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            await handler._update_ui_element({
                "element_id": "btn_off", "layout_id": "portrait", "hidden": True,
            })

    page = _page(mock_engine)
    assert _layout(page, "portrait").hidden == ["btn_off"]
    assert _layout(page, "landscape").hidden == []
    assert any(el.id == "btn_off" for el in page.elements)   # still a control, just not drawn


@pytest.mark.asyncio
async def test_update_ui_element_cannot_unhide_what_it_inherited(handler, mock_engine):
    """`hidden` unions down the chain, so say where the hide came from."""
    from server.core.project_loader import Layout

    page = _page(mock_engine)
    _layout(page, "landscape").hidden = ["btn_off"]
    page.layouts.append(Layout(id="portrait", orientation="portrait", inherits="landscape"))

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._update_ui_element({
            "element_id": "btn_off", "layout_id": "portrait", "hidden": False,
        })

    assert "landscape" in result["error"]
    assert _layout(_page(mock_engine), "landscape").hidden == ["btn_off"]


@pytest.mark.asyncio
async def test_update_ui_element_reparent_keeps_the_drawn_box(handler, mock_engine):
    """Percentages are of the parent, so a reparent converts instead of teleporting."""
    from server.core.project_loader import Placement, UIElement

    page = _page(mock_engine)
    page.elements.append(UIElement(id="grp_audio", type="group", label="Audio"))
    _layout(page, "landscape").placements["grp_audio"] = Placement(x=50.0, y=40.0, w=40.0, h=40.0)
    before = _drawn(page, "btn_off")

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._update_ui_element({
                "element_id": "btn_off", "parent": "grp_audio",
            })

    assert result["status"] == "updated"
    page = _page(mock_engine)
    assert next(el for el in page.elements if el.id == "btn_off").parent == "grp_audio"
    assert _drawn(page, "btn_off") == before          # nothing moved on screen
    assert _layout(page, "landscape").placements["btn_off"].x != before[0]  # but the numbers did


@pytest.mark.asyncio
async def test_update_ui_element_reparent_converts_every_arrangement(handler, mock_engine):
    """Nothing shifts in the layout the caller is not looking at."""
    from server.core.project_loader import Layout, Placement, UIElement

    page = _page(mock_engine)
    page.elements.append(UIElement(id="grp_audio", type="group"))
    _layout(page, "landscape").placements["grp_audio"] = Placement(x=50.0, y=40.0, w=40.0, h=40.0)
    page.layouts.append(Layout(
        id="portrait", orientation="portrait", inherits="landscape",
        placements={
            "grp_audio": Placement(x=5.0, y=5.0, w=90.0, h=30.0),
            "btn_off": Placement(x=10.0, y=60.0, w=80.0, h=8.0),
        },
    ))
    before = {lay.id: _drawn(page, "btn_off", lay.id) for lay in page.layouts}

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            await handler._update_ui_element({"element_id": "btn_off", "parent": "grp_audio"})

    page = _page(mock_engine)
    assert {lay.id: _drawn(page, "btn_off", lay.id) for lay in page.layouts} == before


@pytest.mark.asyncio
async def test_update_ui_element_reparent_with_an_explicit_box_is_taken_literally(handler, mock_engine):
    """Given both, the caller has already said where it goes in the new parent."""
    from server.core.project_loader import Placement, UIElement

    page = _page(mock_engine)
    page.elements.append(UIElement(id="grp_audio", type="group"))
    _layout(page, "landscape").placements["grp_audio"] = Placement(x=50.0, y=40.0, w=40.0, h=40.0)

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            await handler._update_ui_element({
                "element_id": "btn_off", "parent": "grp_audio",
                "placement": {"x": 0.0, "y": 0.0, "w": 100.0, "h": 25.0},
            })

    page = _page(mock_engine)
    place = _layout(page, "landscape").placements["btn_off"]
    assert (place.x, place.y, place.w, place.h) == (0.0, 0.0, 100.0, 25.0)
    assert _drawn(page, "btn_off") == (50.0, 40.0, 40.0, 10.0)   # of the container


@pytest.mark.asyncio
async def test_update_ui_element_refuses_a_container_cycle(handler, mock_engine):
    from server.core.project_loader import Placement, UIElement

    page = _page(mock_engine)
    page.elements.append(UIElement(id="grp_outer", type="group"))
    page.elements.append(UIElement(id="grp_inner", type="group", parent="grp_outer"))
    _layout(page, "landscape").placements["grp_outer"] = Placement(x=0, y=0, w=50, h=50)
    _layout(page, "landscape").placements["grp_inner"] = Placement(x=10, y=10, w=50, h=50)

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._update_ui_element({
            "element_id": "grp_outer", "parent": "grp_inner",
        })

    assert "itself" in result["error"]
    assert next(el for el in _page(mock_engine).elements if el.id == "grp_outer").parent is None


@pytest.mark.asyncio
async def test_delete_ui_elements_clears_the_layout_entries(handler, mock_engine):
    from server.core.project_loader import Layout

    page = _page(mock_engine)
    page.layouts.append(Layout(
        id="portrait", orientation="portrait", inherits="landscape", hidden=["btn_off"],
    ))

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            await handler._delete_ui_elements({"element_ids": ["btn_off"]})

    page = _page(mock_engine)
    assert "btn_off" not in _layout(page, "landscape").placements
    assert _layout(page, "portrait").hidden == []


@pytest.mark.asyncio
async def test_delete_a_container_leaves_its_contents_where_they_were(handler, mock_engine):
    from server.core.project_loader import Placement, UIElement

    page = _page(mock_engine)
    page.elements.append(UIElement(id="grp_audio", type="group"))
    _layout(page, "landscape").placements["grp_audio"] = Placement(x=50.0, y=40.0, w=40.0, h=40.0)
    child = next(el for el in page.elements if el.id == "btn_off")
    child.parent = "grp_audio"
    _layout(page, "landscape").placements["btn_off"] = Placement(x=10.0, y=10.0, w=50.0, h=25.0)
    before = _drawn(page, "btn_off")

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            await handler._delete_ui_elements({"element_ids": ["grp_audio"]})

    page = _page(mock_engine)
    assert next(el for el in page.elements if el.id == "btn_off").parent is None
    assert _drawn(page, "btn_off") == before


@pytest.mark.asyncio
async def test_add_master_element_takes_placements_keyed_by_orientation(handler, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            result = await handler._add_master_element({
                "id": "home_btn", "type": "page_nav", "target_page": "main", "pages": "*",
                "placements": {
                    "landscape": {"x": 2.0, "y": 2.0, "w": 8.0, "h": 10.0},
                    "portrait": {"x": 2.0, "y": 90.0, "w": 20.0, "h": 8.0},
                },
            })

    assert result["status"] == "created"
    master = next(el for el in mock_engine.project.ui.master_elements if el.id == "home_btn")
    assert master.placements["landscape"].w == 8.0
    assert master.placements["portrait"].y == 90.0


@pytest.mark.asyncio
async def test_add_master_element_rejects_a_page_style_placement(handler, mock_engine):
    """A master borrows no page's layout -- that is what §34.17 used to be."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        result = await handler._add_master_element({
            "id": "home_btn", "type": "page_nav",
            "placement": {"x": 2.0, "y": 2.0, "w": 8.0, "h": 10.0},
        })

    assert "placements" in result["error"]
    assert not any(el.id == "home_btn" for el in mock_engine.project.ui.master_elements)


@pytest.mark.asyncio
async def test_layout_round_trip_through_the_tool_handlers(handler, mock_engine):
    """The §11 round trip, entirely through the tools: create, add, move,
    reparent, hide in a variant, and read it back."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            assert (await handler._add_ui_page({
                "id": "av", "name": "AV",
                "elements": [
                    {"id": "grp_sources", "type": "group", "label": "Sources",
                     "placement": {"x": 5.0, "y": 5.0, "w": 45.0, "h": 60.0}},
                ],
            }))["status"] == "created"

            assert (await handler._add_ui_elements({
                "page_id": "av",
                "elements": [
                    {"id": "btn_hdmi", "type": "button", "label": "HDMI",
                     "parent": "grp_sources",
                     "placement": {"x": 5.0, "y": 5.0, "w": 90.0, "h": 20.0}},
                    {"id": "lbl_banner", "type": "label", "text": "Welcome",
                     "placement": {"x": 55.0, "y": 5.0, "w": 40.0, "h": 15.0}},
                    {"id": "sld_volume", "type": "slider", "label": "Volume",
                     "placement": {"x": 55.0, "y": 30.0, "w": 40.0, "h": 10.0}},
                ],
            }))["status"] == "created"

            # Move one, in the primary.
            await handler._update_ui_element({
                "element_id": "sld_volume", "placement": {"y": 45.0},
            })
            # Reparent one, which must not move it.
            page = _page(mock_engine, "av")
            drawn_before = _drawn(page, "sld_volume")
            await handler._update_ui_element({
                "element_id": "sld_volume", "parent": "grp_sources",
            })
            # Author a portrait arrangement and hide the banner in it only.
            await handler._update_ui_page({
                "page_id": "av",
                "layouts": [{"id": "portrait", "orientation": "portrait"}],
            })
            await handler._update_ui_element({
                "element_id": "grp_sources", "layout_id": "portrait",
                "placement": {"x": 5.0, "y": 5.0, "w": 90.0, "h": 40.0},
            })
            await handler._update_ui_element({
                "element_id": "lbl_banner", "layout_id": "portrait", "hidden": True,
            })

            read_back = await handler._get_ui_page({"page_id": "av"})

    # Read it back the way the model would.
    assert [el["id"] for el in read_back["elements"]] == [
        "grp_sources", "btn_hdmi", "lbl_banner", "sld_volume",
    ]
    landscape = next(lay for lay in read_back["layouts"] if lay["primary"])
    portrait = next(lay for lay in read_back["layouts"] if lay["id"] == "portrait")
    assert landscape["orientation"] == "landscape"
    assert portrait["inherits"] == "landscape"
    # The controls are shared; only the boxes that moved are stored in the variant.
    assert set(landscape["placements"]) == {"grp_sources", "btn_hdmi", "lbl_banner", "sld_volume"}
    assert set(portrait["placements"]) == {"grp_sources"}
    assert portrait["hidden"] == ["lbl_banner"]
    assert landscape["hidden"] == []
    # The move landed, the reparent did not move it, and the child is a child.
    assert next(el for el in read_back["elements"] if el["id"] == "sld_volume")["parent"] == "grp_sources"
    assert _drawn(_page(mock_engine, "av"), "sld_volume") == drawn_before


# ===== SCHEDULE TOOLS =====


# ===== SEAM BEHAVIOR =====
# Every mutating tool routes through engine.apply_project_edit exactly once.
# The edit seam itself applies with EDIT origin and no expected_revision
# (hardcoded in the engine, pinned by the engine tests). The scoped
# reconcile replaces the old full reload_fn: an AI macro edit no longer
# re-fires startup triggers, and variable edits actually take effect
# (seeding, persister keys, orphan sweep).


def _assert_edit_origin_apply(mock_engine):
    mock_engine.apply_project_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_variable_tools_apply_through_seam(handler, mock_agent, mock_engine):
    """Variable tools apply through the seam once, EDIT origin."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_variable", {"id": "test_var"})
            await handler.handle(msg)
        await _drain()

    _assert_edit_origin_apply(mock_engine)


@pytest.mark.asyncio
async def test_device_add_applies_through_seam(handler, mock_agent, mock_engine):
    """add_device applies through the seam (the devices reconcile hot-adds)."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_device", {
                "id": "test_dev",
                "driver": "test",
                "name": "Test Device",
            })
            await handler.handle(msg)
        await _drain()

    _assert_edit_origin_apply(mock_engine)


@pytest.mark.asyncio
async def test_macro_tools_apply_through_seam(handler, mock_agent, mock_engine):
    """Macro tools apply through the seam — EDIT origin, so trigger
    registration happens without re-firing startup triggers."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_macro", {"id": "test_macro", "name": "Test"})
            await handler.handle(msg)
        await _drain()

    _assert_edit_origin_apply(mock_engine)


@pytest.mark.asyncio
async def test_ui_tools_apply_through_seam(handler, mock_agent, mock_engine):
    """UI tools apply through the seam (broadcast-only reconcile)."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_ui_elements", {
                "page_id": "main",
                "elements": [{"id": "new_btn", "type": "button"}],
            })
            await handler.handle(msg)
        await _drain()

    _assert_edit_origin_apply(mock_engine)


# ===== DISPATCH TABLE =====


def test_all_surgical_tools_registered():
    """All 19 new tools are registered in the dispatch table."""
    agent = MagicMock()
    agent.send_message = AsyncMock()
    agent.state = MagicMock()
    devices = MagicMock()
    events = MagicMock()
    handler = AIToolHandler(agent, devices, events)

    expected = {
        "get_project_summary", "get_macro", "get_ui_page",
        "add_device", "add_variable", "update_variable", "delete_variable",
        "add_macro", "update_macro", "delete_macro",
        "add_ui_page", "delete_ui_page", "add_ui_elements",
        "update_ui_element", "delete_ui_elements",
    }
    for name in expected:
        assert name in handler._tools, f"Tool '{name}' not registered in dispatch table"


# ===== STATE VALUE / SCRIPT SCAN / REVISION / FORWARD-COMPAT GUARDS =====


@pytest.mark.asyncio
async def test_set_state_value_rejects_non_primitive(handler, mock_agent):
    """A dict/list value must be rejected at the AI boundary — the store
    drops non-primitives silently, so without this the tool reports success
    for a write that never happened."""
    for bad in ({"nested": 1}, [1, 2, 3]):
        mock_agent.state.set.reset_mock()
        msg = _make_tool_call_msg("set_state_value", {"key": "var.x", "value": bad})
        await handler.handle(msg)
        await _drain()
        payload = _get_result_payload(mock_agent)
        assert payload["success"] is False
        assert "flat primitive" in payload["result"]["error"]
        mock_agent.state.set.assert_not_called()


@pytest.mark.asyncio
async def test_set_state_value_accepts_primitives(handler, mock_agent):
    for good in ("on", 42, 1.5, True, None):
        mock_agent.state.set.reset_mock()
        msg = _make_tool_call_msg("set_state_value", {"key": "var.x", "value": good})
        await handler.handle(msg)
        await _drain()
        payload = _get_result_payload(mock_agent)
        assert payload["success"] is True
        mock_agent.state.set.assert_called_once_with("var.x", good, source="ai")


def test_find_references_scans_scripts_beside_project(handler, mock_engine, tmp_path):
    """Script references must be found relative to the loaded project file,
    not a hardcoded projects/default path that only exists in dev."""
    project_dir = tmp_path / "deployed_site"
    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "auto_lights.py").write_text(
        'devices.send("projector1", "power_on")', encoding="utf-8"
    )
    mock_engine.project_path = project_dir / "site.avc"

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        refs = handler._find_references("device", "projector1")

    assert refs.get("scripts") == [{"script_id": "auto_lights", "file": "auto_lights.py"}]


def test_find_references_skips_escaping_script_paths(handler, mock_engine, tmp_path):
    """A script entry whose file escapes the scripts dir is skipped, not read."""
    from server.core.project_loader import ScriptConfig

    project_dir = tmp_path / "deployed_site"
    (project_dir / "scripts").mkdir(parents=True)
    # A file OUTSIDE the scripts dir that does contain the reference
    (tmp_path / "outside.py").write_text("projector1", encoding="utf-8")
    mock_engine.project_path = project_dir / "site.avc"
    mock_engine.project.scripts = [
        ScriptConfig(id="evil", file="../../outside.py", description=""),
    ]

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        refs = handler._find_references("device", "projector1")

    assert "scripts" not in refs


@pytest.mark.asyncio
async def test_update_macro_preserves_forward_compat_fields(handler, mock_agent, mock_engine):
    """Editing a macro must not strip extra='allow' fields a newer platform
    version stored on it."""
    from server.core.project_loader import MacroConfig

    mock_engine.project.macros[1] = MacroConfig(**{
        "id": "presentation",
        "name": "Presentation Mode",
        "steps": [],
        "future_field": "from-a-newer-version",
    })

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_macro", {
            "macro_id": "presentation",
            "name": "Renamed Mode",
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    updated = mock_engine.project.macros[1]
    assert updated.name == "Renamed Mode"
    assert updated.model_dump().get("future_field") == "from-a-newer-version"


@pytest.mark.asyncio
async def test_plugin_config_update_bumps_revision(handler, mock_agent, mock_engine):
    """Plugin config updates apply through the seam; without the revision bump
    an open IDE's stale ETag still matches and its next save clobbers the edit."""
    from server.core.project_loader import PluginConfig

    mock_engine.project.plugins = {"some_plugin": PluginConfig(enabled=True, config={})}
    # Async loader surface broad enough for either restart shape (stop/start
    # or a hot-apply restart_or_apply path).
    mock_engine.plugin_loader = MagicMock()
    mock_engine.plugin_loader.is_running.return_value = False
    mock_engine.plugin_loader.restart_or_apply = AsyncMock()
    mock_engine.plugin_loader.stop_plugin = AsyncMock()
    mock_engine.plugin_loader.start_plugin = AsyncMock(return_value=True)

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_plugin_config", {
            "plugin_id": "some_plugin",
            "config": {"volume": 5},
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    # restart_or_apply ran first (runtime-first, then apply — the reconcile
    # sees the running config already current), and the seam bumped once.
    mock_engine.plugin_loader.restart_or_apply.assert_awaited_once()
    mock_engine.apply_project_edit.assert_awaited_once()
    assert mock_engine._project_revision == 1
    assert mock_engine.project.plugins["some_plugin"].config == {"volume": 5}


@pytest.mark.asyncio
async def test_disable_plugin_bumps_revision(handler, mock_agent, mock_engine):
    from server.core.project_loader import PluginConfig

    mock_engine.project.plugins = {"some_plugin": PluginConfig(enabled=True, config={})}
    mock_engine.plugin_loader = MagicMock()
    mock_engine.plugin_loader.stop_plugin = AsyncMock()

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("disable_plugin", {"plugin_id": "some_plugin"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    # Seam handoff: enabled=False persisted through the edit seam; the
    # plugins reconcile (pinned in the engine tests) stops the running plugin.
    mock_engine.apply_project_edit.assert_awaited_once()
    assert mock_engine.project.plugins["some_plugin"].enabled is False


class _StubPlugin:
    PLUGIN_INFO = {"id": "stub_plugin", "name": "Stub", "version": "1.0.0"}
    CONFIG_SCHEMA = {}


def _plugin_loader_mock(start_ok=True):
    loader = MagicMock()
    loader.start_plugin = AsyncMock(return_value=start_ok)
    loader.stop_plugin = AsyncMock()
    loader.restart_or_apply = AsyncMock()
    loader.get_health = AsyncMock(
        return_value={"status": "error", "message": "start() raised RuntimeError"}
    )
    return loader


@pytest.mark.asyncio
async def test_enable_plugin_rolls_back_on_start_failure(handler, mock_agent, mock_engine):
    """A failed enable must not persist enabled=True — start_plugins() retries
    every enabled entry at startup, so a broken plugin would retry on every
    boot (the REST enable endpoint rolls back; the AI tool must match)."""
    from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY
    from server.core.project_loader import PluginConfig

    mock_engine.project.plugins = {
        "stub_plugin": PluginConfig(enabled=False, config={"keep": "me"})
    }
    mock_engine.plugin_loader = _plugin_loader_mock(start_ok=False)

    with patch.dict(_PLUGIN_CLASS_REGISTRY, {"stub_plugin": _StubPlugin}), \
         patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("enable_plugin", {"plugin_id": "stub_plugin"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    # The failure is reported as a failure (error key -> is_error classifier)
    assert payload["success"] is False
    # enabled=True was rolled back before the apply; config preserved
    assert mock_engine.project.plugins["stub_plugin"].enabled is False
    assert mock_engine.project.plugins["stub_plugin"].config == {"keep": "me"}
    # The rolled-back state was still applied through the seam (persist + bump)
    mock_engine.apply_project_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_enable_plugin_first_time_failure_keeps_entry_disabled(
    handler, mock_agent, mock_engine
):
    """First-time enable that fails persists the new entry disabled, so the
    default config is kept for a later fix-and-retry but never auto-started."""
    from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

    mock_engine.project.plugins = {}
    mock_engine.plugin_loader = _plugin_loader_mock(start_ok=False)

    with patch.dict(_PLUGIN_CLASS_REGISTRY, {"stub_plugin": _StubPlugin}), \
         patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("enable_plugin", {"plugin_id": "stub_plugin"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert mock_engine.project.plugins["stub_plugin"].enabled is False


@pytest.mark.asyncio
async def test_enable_plugin_success_persists_enabled(handler, mock_agent, mock_engine):
    from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY
    from server.core.project_loader import PluginConfig

    mock_engine.project.plugins = {
        "stub_plugin": PluginConfig(enabled=False, config={"keep": "me"})
    }
    mock_engine.plugin_loader = _plugin_loader_mock(start_ok=True)

    with patch.dict(_PLUGIN_CLASS_REGISTRY, {"stub_plugin": _StubPlugin}), \
         patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("enable_plugin", {"plugin_id": "stub_plugin"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert mock_engine.project.plugins["stub_plugin"].enabled is True
    mock_engine.apply_project_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_plugin_config_rejects_missing_config(handler, mock_agent, mock_engine):
    """Omitting 'config' must be an error, not a silent wipe-to-{} + restart."""
    from server.core.project_loader import PluginConfig

    mock_engine.project.plugins = {
        "some_plugin": PluginConfig(enabled=True, config={"brightness": 80})
    }
    mock_engine.plugin_loader = _plugin_loader_mock()

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_plugin_config", {"plugin_id": "some_plugin"})
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert payload.get("error")
    # Config untouched, no restart, no apply (no save, no bump)
    assert mock_engine.project.plugins["some_plugin"].config == {"brightness": 80}
    mock_engine.plugin_loader.restart_or_apply.assert_not_awaited()
    mock_engine.apply_project_edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_plugin_config_rejects_non_dict_config(handler, mock_agent, mock_engine):
    from server.core.project_loader import PluginConfig

    mock_engine.project.plugins = {
        "some_plugin": PluginConfig(enabled=True, config={"brightness": 80})
    }
    mock_engine.plugin_loader = _plugin_loader_mock()

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_plugin_config", {
            "plugin_id": "some_plugin",
            "config": "not-an-object",
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert mock_engine.project.plugins["some_plugin"].config == {"brightness": 80}
    mock_engine.plugin_loader.restart_or_apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_plugin_config_allows_explicit_empty_object(
    handler, mock_agent, mock_engine
):
    """An explicit {} is a legitimate complete config (schema with no required
    fields) — only the *omitted* key is rejected."""
    from server.core.project_loader import PluginConfig

    mock_engine.project.plugins = {
        "some_plugin": PluginConfig(enabled=True, config={"brightness": 80})
    }
    mock_engine.plugin_loader = _plugin_loader_mock()

    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_plugin_config", {
            "plugin_id": "some_plugin",
            "config": {},
        })
        await handler.handle(msg)
        await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert mock_engine.project.plugins["some_plugin"].config == {}
    mock_engine.plugin_loader.restart_or_apply.assert_awaited_once()


# ===== STATE READ TOOLS — cloud exclusion filter + count validation =====
#
# Tool results ship to cloud.openavc.com and persist in AI conversation
# history, so the read tools must apply the same exclusion the state relay
# does: system.cloud.* (session id!) and isc.* peer state stay on the box.


def _make_state_store():
    """Real StateStore seeded with normal + cloud-excluded keys."""
    from server.core.state_store import StateStore
    store = StateStore()
    store.set("device.projector1.power", "on", source="test")
    store.set("var.room_mode", "normal", source="test")
    store.set("plugin.weather.status", "ok", source="test")
    store.set("system.cloud.status", "connected", source="cloud")
    store.set("system.cloud.session_id", "sess-secret-123", source="cloud")
    store.set("isc.peer1.volume", 42, source="isc")
    return store


@pytest.mark.asyncio
async def test_get_project_state_excludes_cloud_internal_and_isc(handler, mock_agent):
    mock_agent.state = _make_state_store()

    msg = _make_tool_call_msg("get_project_state")
    await handler.handle(msg)
    await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    result = payload["result"]
    assert result["device.projector1.power"] == "on"
    assert result["var.room_mode"] == "normal"
    # plugin.* is relayed to the cloud by the state relay, so it stays visible
    assert result["plugin.weather.status"] == "ok"
    assert not any(k.startswith("system.cloud.") for k in result)
    assert not any(k.startswith("isc.") for k in result)


@pytest.mark.asyncio
async def test_get_state_value_refuses_cloud_internal_and_isc(handler, mock_agent):
    mock_agent.state = _make_state_store()

    for key in ("system.cloud.session_id", "isc.peer1.volume"):
        msg = _make_tool_call_msg("get_state_value", {"key": key})
        await handler.handle(msg)
        await _drain()
        payload = _get_result_payload(mock_agent)
        assert payload["success"] is False
        assert "sess-secret-123" not in str(payload)

    msg = _make_tool_call_msg("get_state_value", {"key": "device.projector1.power"})
    await handler.handle(msg)
    await _drain()
    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"] == {"key": "device.projector1.power", "value": "on"}


@pytest.mark.asyncio
async def test_get_state_history_excludes_cloud_internal_and_isc(handler, mock_agent):
    mock_agent.state = _make_state_store()

    msg = _make_tool_call_msg("get_state_history", {"count": 50})
    await handler.handle(msg)
    await _drain()

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    keys = [entry["key"] for entry in payload["result"]]
    assert "device.projector1.power" in keys
    assert not any(k.startswith("system.cloud.") for k in keys)
    assert not any(k.startswith("isc.") for k in keys)
    assert "sess-secret-123" not in str(payload)


@pytest.mark.asyncio
async def test_get_state_history_validates_count(handler, mock_agent):
    # No cloud-excluded keys here — the count assertions below need the
    # returned length to reflect count alone, not the exclusion filter.
    from server.core.state_store import StateStore
    store = StateStore()
    for name, value in (("a", 1), ("b", 2), ("c", 3)):
        store.set(f"var.{name}", value, source="test")
    mock_agent.state = store

    # Non-numeric count -> structured error, not an exception
    msg = _make_tool_call_msg("get_state_history", {"count": "lots"})
    await handler.handle(msg)
    await _drain()
    payload = _get_result_payload(mock_agent)
    assert payload["success"] is False
    assert "integer" in payload["error"]

    # count=0 -> empty list, NOT the whole history ([-0:] slice bug)
    msg = _make_tool_call_msg("get_state_history", {"count": 0})
    await handler.handle(msg)
    await _drain()
    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"] == []

    # Numeric strings / floats coerce instead of crashing
    for count in ("2", 2.7):
        msg = _make_tool_call_msg("get_state_history", {"count": count})
        await handler.handle(msg)
        await _drain()
        payload = _get_result_payload(mock_agent)
        assert payload["success"] is True
        assert len(payload["result"]) == 2
