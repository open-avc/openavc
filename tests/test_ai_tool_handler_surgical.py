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


def _make_project():
    """Create a mock ProjectConfig with realistic data."""
    from server.core.project_loader import (
        ProjectConfig, ProjectMeta, DeviceConfig, VariableConfig,
        MacroConfig, MacroStep, TriggerConfig, UIConfig, UIPage,
        UIElement, GridArea, GridConfig, ScriptConfig,
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
            UIPage(id="main", name="Main Control", grid=GridConfig(columns=12, rows=8), elements=[
                UIElement(id="btn_on", type="button", label="System On", grid_area=GridArea(col=1, row=1, col_span=2, row_span=1)),
                UIElement(id="btn_off", type="button", label="System Off", grid_area=GridArea(col=3, row=1, col_span=2, row_span=1)),
                UIElement(id="vol_slider", type="slider", label="Volume", grid_area=GridArea(col=1, row=3, col_span=6, row_span=1)),
            ]),
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
    return engine


@pytest.fixture(autouse=True)
def _patch_save_project():
    """Patch save_project globally so write tools don't hit the filesystem."""
    with patch("server.core.project_loader.save_project"):
        yield


@pytest.fixture
def handler(mock_agent, mock_devices, mock_events):
    reload_fn = AsyncMock()
    return AIToolHandler(mock_agent, mock_devices, mock_events, reload_fn=reload_fn)


# ===== READ TOOLS =====


@pytest.mark.asyncio
async def test_get_project_summary(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_project_summary")
        await handler.handle(msg)
        await asyncio.sleep(0)

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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert "error" in payload["result"]


@pytest.mark.asyncio
async def test_get_macro(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_macro", {"macro_id": "all_off"})
        await handler.handle(msg)
        await asyncio.sleep(0)

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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert "error" in payload["result"]


@pytest.mark.asyncio
async def test_get_ui_page(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_ui_page", {"page_id": "main"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    result = payload["result"]
    assert result["id"] == "main"
    assert result["name"] == "Main Control"
    assert len(result["elements"]) == 3
    assert result["grid"]["columns"] == 12


@pytest.mark.asyncio
async def test_get_ui_page_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("get_ui_page", {"page_id": "nonexistent"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"
    assert payload["result"]["id"] == "display1"

    # Device was added to project
    assert any(d.id == "display1" for d in mock_engine.project.devices)

    # Hot-add was called
    mock_engine.devices.add_device.assert_called_once()


@pytest.mark.asyncio
async def test_add_device_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_device", {
            "id": "projector1",  # already exists
            "driver": "pjlink",
            "name": "Duplicate",
        })
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"

    # Variable was added
    assert any(v.id == "volume_level" for v in mock_engine.project.variables)

    # Default value set in state
    mock_agent.state.set.assert_called_with("var.volume_level", 50, source="config")


@pytest.mark.asyncio
async def test_add_variable_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_variable", {"id": "room_mode"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
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
        await asyncio.sleep(0)

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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert "not found" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_delete_variable(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("delete_variable", {"id": "is_occupied"})
            await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "deleted"
    assert not any(v.id == "is_occupied" for v in mock_engine.project.variables)


@pytest.mark.asyncio
async def test_delete_variable_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("delete_variable", {"id": "nonexistent"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"

    # Macro was added
    macro = next(m for m in mock_engine.project.macros if m.id == "lights_on")
    assert macro.name == "Lights On"
    assert len(macro.steps) == 2
    assert macro.stop_on_error is True

    # Reload was called (macros need trigger registration)
    handler._reload_fn.assert_called_once()


@pytest.mark.asyncio
async def test_add_macro_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_macro", {"id": "all_off", "name": "Duplicate"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "updated"

    macro = next(m for m in mock_engine.project.macros if m.id == "all_off")
    assert macro.name == "Everything Off"
    assert len(macro.steps) == 1
    # Triggers should remain from original since not specified in update
    assert len(macro.triggers) == 1


@pytest.mark.asyncio
async def test_update_macro_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_macro", {"macro_id": "nonexistent"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert "not found" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_delete_macro(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("delete_macro", {"macro_id": "presentation"})
            await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "deleted"
    assert not any(m.id == "presentation" for m in mock_engine.project.macros)


@pytest.mark.asyncio
async def test_delete_macro_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("delete_macro", {"macro_id": "nonexistent"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert "not found" in payload["result"]["error"]


# ===== UI PAGE TOOLS =====


@pytest.mark.asyncio
async def test_add_ui_page(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_ui_page", {
                "id": "lighting",
                "name": "Lighting Control",
                "grid": {"columns": 6, "rows": 4},
            })
            await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "created"

    page = next(p for p in mock_engine.project.ui.pages if p.id == "lighting")
    assert page.name == "Lighting Control"
    assert page.grid.columns == 6


@pytest.mark.asyncio
async def test_add_ui_page_duplicate(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_ui_page", {"id": "main", "name": "Duplicate"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert "already exists" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_delete_ui_page(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("delete_ui_page", {"page_id": "settings"})
            await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "deleted"
    assert not any(p.id == "settings" for p in mock_engine.project.ui.pages)


@pytest.mark.asyncio
async def test_delete_ui_page_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("delete_ui_page", {"page_id": "nonexistent"})
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
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
                     "grid_area": {"col": 1, "row": 5}},
                    {"id": "lbl_status", "type": "label", "text": "Ready",
                     "grid_area": {"col": 3, "row": 5, "col_span": 2}},
                ],
            })
            await handler.handle(msg)
        await asyncio.sleep(0)

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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert "already exists" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_add_ui_elements_page_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("add_ui_elements", {
            "page_id": "nonexistent",
            "elements": [{"id": "btn1", "type": "button"}],
        })
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert "not found" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_update_ui_element(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("update_ui_element", {
                "element_id": "btn_on",
                "label": "Power On",
                "style": {"bg_color": "#4CAF50"},
                "bindings": {"press": [{"action": "macro", "macro": "all_on"}]},
            })
            await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True
    assert payload["result"]["status"] == "updated"

    # Find element and verify updates
    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    el = next(e for e in page.elements if e.id == "btn_on")
    assert el.label == "Power On"
    assert el.style == {"bg_color": "#4CAF50"}
    assert el.bindings["press"][0]["action"] == "macro"


@pytest.mark.asyncio
async def test_update_ui_element_grid_area(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("update_ui_element", {
                "element_id": "btn_on",
                "grid_area": {"col": 5, "row": 2, "col_span": 3, "row_span": 2},
            })
            await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert payload["success"] is True

    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    el = next(e for e in page.elements if e.id == "btn_on")
    assert el.grid_area.col == 5
    assert el.grid_area.row == 2
    assert el.grid_area.col_span == 3


@pytest.mark.asyncio
async def test_update_ui_element_not_found(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        msg = _make_tool_call_msg("update_ui_element", {
            "element_id": "nonexistent",
            "label": "Nope",
        })
        await handler.handle(msg)
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert "not found" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_delete_ui_elements(handler, mock_agent, mock_engine):
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("delete_ui_elements", {
                "element_ids": ["btn_on", "btn_off"],
            })
            await handler.handle(msg)
        await asyncio.sleep(0)

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
        await asyncio.sleep(0)

    payload = _get_result_payload(mock_agent)
    assert "No matching elements" in payload["result"]["error"]


# ===== SCHEDULE TOOLS =====


# ===== RELOAD BEHAVIOR =====


@pytest.mark.asyncio
async def test_variable_tools_no_reload(handler, mock_agent, mock_engine):
    """Variable tools should NOT trigger a reload."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_variable", {"id": "test_var"})
            await handler.handle(msg)
        await asyncio.sleep(0)

    handler._reload_fn.assert_not_called()


@pytest.mark.asyncio
async def test_device_add_no_reload(handler, mock_agent, mock_engine):
    """add_device should NOT trigger a reload (uses hot-add)."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_device", {
                "id": "test_dev",
                "driver": "test",
                "name": "Test Device",
            })
            await handler.handle(msg)
        await asyncio.sleep(0)

    handler._reload_fn.assert_not_called()


@pytest.mark.asyncio
async def test_macro_tools_trigger_reload(handler, mock_agent, mock_engine):
    """Macro tools should trigger a reload for trigger registration."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_macro", {"id": "test_macro", "name": "Test"})
            await handler.handle(msg)
        await asyncio.sleep(0)

    handler._reload_fn.assert_called_once()


@pytest.mark.asyncio
async def test_ui_tools_trigger_reload(handler, mock_agent, mock_engine):
    """UI tools should trigger a reload for binding registration."""
    with patch.object(handler, "_get_engine", return_value=mock_engine):
        with patch("server.core.project_loader.save_project"):
            msg = _make_tool_call_msg("add_ui_elements", {
                "page_id": "main",
                "elements": [{"id": "new_btn", "type": "button", "grid_area": {"col": 1, "row": 7}}],
            })
            await handler.handle(msg)
        await asyncio.sleep(0)

    handler._reload_fn.assert_called_once()


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
