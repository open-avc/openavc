"""A broadcast refused for the chosen adapter, as a panel shows it.

With an adapter chosen under Reach devices through and that adapter not
connected, a Wake-on-LAN is not sent, and the refusal tells the integrator to
connect the adapter or choose another in Settings. Nobody at a panel can do
either, so every door that answers a panel says it in words for the room,
while the Programmer, the REST door and the log keep the adapter's sentence.
"""

import json
from unittest.mock import AsyncMock

import pytest

from openavc.api import ws
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.reach_devices import AdapterUnavailable

REFUSED = AdapterUnavailable("Ethernet")
FOR_THE_ROOM = "Lobby Display can't be reached: the network connection to it is down. Contact support."


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.13.0",
        "project": {"id": "test", "name": "Test"},
        "devices": [{"id": "acme_display", "name": "Lobby Display", "driver": "acme_widget"}],
        "macros": [{"id": "wake", "name": "Wake", "steps": [
            {"action": "device.command", "device": "acme_display", "command": "power_on"},
        ]}],
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [{
            "id": "wake", "type": "button", "bindings": {"do": {"press": [{
                "action": "device.command", "device": "acme_display", "command": "power_on",
            }]}},
        }]}]},
    }))
    result = Engine(str(path))
    result.project = load_project(path)
    result.macros.load_macros([m.model_dump() for m in result.project.macros])
    result.devices.send_command = AsyncMock(side_effect=REFUSED)
    return result


@pytest.mark.asyncio
async def test_a_panel_press_says_it_for_the_room(engine):
    result = await engine.handle_ui_event("press", "wake")
    assert result[0]["sent"] is False
    assert result[0]["error"] == FOR_THE_ROOM


@pytest.mark.asyncio
async def test_a_macro_step_says_it_for_the_room_and_keeps_the_adapter_on_record(engine):
    errors = []
    engine.events.on("macro.step_error.*", lambda event, payload: errors.append(payload))
    await engine.macros.execute("wake")
    assert len(errors) == 1
    assert errors[0]["message"] == FOR_THE_ROOM
    assert errors[0]["error"] == str(REFUSED)


@pytest.mark.asyncio
@pytest.mark.parametrize("client_type,expected", [
    ("panel", FOR_THE_ROOM),
    ("programmer", str(REFUSED)),
])
async def test_a_direct_command_answers_whoever_sent_it(engine, monkeypatch, client_type, expected):
    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    await ws._handle_message(None, {"type": "command", "device_id": "acme_display",
                                   "command": "power_on"}, client_type=client_type)
    assert sent.await_args.args[1]["error"] == expected
