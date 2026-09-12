"""Disabled equipment keeps its configured label in every panel error path."""

import json
from unittest.mock import AsyncMock

import pytest

from openavc.api import ws
from openavc.api.error_messages import friendly_error
from openavc.core.device_manager import DeviceNotFoundError
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.13.0",
        "project": {"id": "test", "name": "Test"},
        "devices": [{"id": "acme_camera", "name": "Ceiling Camera",
                     "driver": "acme_widget", "enabled": False}],
        "macros": [{"id": "standby", "name": "Standby", "stop_on_error": True,
                    "steps": [{"action": "device.command", "device": "acme_camera",
                               "command": "power_off"}]}],
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [{
            "id": "standby", "type": "button", "bindings": {"do": {"press": [{
                "action": "device.command", "device": "acme_camera", "command": "power_off",
            }]}},
        }]}]},
    }))
    result = Engine(str(path))
    result.project = load_project(path)
    result.macros.load_macros([m.model_dump() for m in result.project.macros])
    # No runtime registration or device.* state: exactly what disabling does.
    assert result.state.get("device.acme_camera.name") is None
    return result


@pytest.mark.asyncio
async def test_direct_panel_press_names_disabled_equipment(engine):
    result = await engine.handle_ui_event("press", "standby")
    assert result[0]["sent"] is False
    assert result[0]["error"] == "Ceiling Camera is unavailable. Contact support."


@pytest.mark.asyncio
async def test_macro_failure_keeps_technical_detail_out_of_panel_message(engine):
    errors = []
    engine.events.on("macro.step_error.*", lambda event, payload: errors.append(payload))
    assert await engine.macros.execute("standby") == "failed"
    assert len(errors) == 1
    assert errors[0]["message"] == "Ceiling Camera is unavailable. Contact support."
    assert "acme_camera" in errors[0]["error"]


@pytest.mark.asyncio
async def test_direct_command_ack_uses_the_same_label(engine, monkeypatch):
    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    await ws._handle_message(None, {"type": "command", "device_id": "acme_camera",
                                   "command": "power_off"}, client_type="programmer")
    assert sent.await_args.args[1]["error"] == "Ceiling Camera is unavailable. Contact support."


def test_missing_device_has_a_useful_fallback_and_other_value_errors_stay_specific():
    assert friendly_error(DeviceNotFoundError("Device 'private_id' not found")) == (
        "The requested equipment is unavailable. Contact support."
    )
    assert friendly_error(ValueError("Command 'wake' not found")) == "Command 'wake' not found"
