"""A driver's own error text, wherever else it used to reach a reader.

``tests/test_command_failure_text.py`` pins the rule for every door that sends
a device command: the platform's refusals keep their sentence, and anything
else a driver raises is logged under the device's id and answered with the
device's and the command's names. Here, the same rule at the doors that are
not a command:

  * a device setting write, from the device's page and from the cloud AI;
  * the cloud AI's catch-all, for whatever a tool lets escape;
  * a control that calls a script, whose function sends the command;
  * ``script.error``, which only the Programmer reads;
  * ``device.error.<id>``, as a panel's event subscription receives it.

An invented device (Acme) throughout, on the real device manager.
"""

from __future__ import annotations

import json
import logging
import textwrap
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openavc.api import ws
from openavc.api.routes import devices as device_routes
from openavc.cloud.ai_tool_handler import TOOL_FAILED, AIToolHandler
from openavc.core.device_manager import DeviceNotFoundError
from openavc.core.engine import Engine
from openavc.core.isc import ISCRemoteError
from openavc.core.project_loader import DeviceConfig, load_project
from openavc.core.script_engine import ScriptEngine
from openavc.drivers.base import (
    BaseDriver,
    CommandRefusedError,
    UnknownCommandError,
)
from openavc.utils.community_integrity import CommunityArtifactError
from openavc.utils.log_buffer import BufferHandler, LogBuffer

PRIVATE = "private: /firmware/secrets/token"
REFUSAL = "Encoder 001 is offline"
FOR_THE_ROOM = "Lobby Widget couldn't run Flash LED. Contact support."
SETTING_FAILED = (
    "Lobby Widget couldn't change Brightness. The Device Log on its page has the details."
)


class _AcmeDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"flash_led": {"label": "Flash LED", "params": {}}},
        "device_settings": {
            "brightness": {"label": "Brightness", "type": "integer", "min": 0, "max": 100},
        },
    }

    #: What the next command or setting write raises.
    failure: Exception = RuntimeError(PRIVATE)

    async def connect(self) -> None:
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        raise self.failure

    async def set_device_setting(self, key: str, value: Any) -> Any:
        raise self.failure


@pytest.fixture
async def engine(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.13.0",
        "project": {"id": "test", "name": "Test"},
        "devices": [{"id": "acme1", "name": "Lobby Widget", "driver": "acme_widget"}],
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [
            {"id": "flash", "type": "button", "bindings": {"do": {"press": [
                {"action": "device.command", "device": "acme1", "command": "flash_led"},
            ]}}},
            {"id": "flash_script", "type": "button", "bindings": {"do": {"press": [
                {"action": "script.call", "function": "flash"},
            ]}}},
        ]}]},
    }))
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "room.py").write_text(textwrap.dedent("""\
        from openavc import devices

        async def flash():
            await devices.send("acme1", "flash_led")
    """))
    result = Engine(str(path))
    result.project = load_project(path)
    se = ScriptEngine(result.state, result.events, result.devices, tmp_path)
    se.install()
    se.load_scripts([{"id": "room", "file": "room.py", "enabled": True}])
    result.scripts = se
    driver = _AcmeDriver("acme1", {}, result.state, result.events)
    await driver.connect()
    result.devices._devices["acme1"] = driver
    result.state.set("device.acme1.name", "Lobby Widget")
    result.state.set("device.acme1.host", "192.0.2.10")
    _AcmeDriver.failure = RuntimeError(PRIVATE)
    yield result
    _AcmeDriver.failure = RuntimeError(PRIVATE)


def _assert_private(*texts: Any) -> None:
    for text in texts:
        assert PRIVATE not in json.dumps(text, default=str)


class _FakeWS:
    """Records what the hub's writer task sends this client."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        pass

    def frames(self, type_: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == type_]


@pytest.fixture
async def sockets(engine):
    """A panel and a Programmer, each connected to the engine's real hub."""
    panel, programmer = _FakeWS(), _FakeWS()
    engine.ws.add_client(panel, client_type="panel")
    engine.ws.add_client(programmer, client_type="programmer")
    yield panel, programmer
    for client in (panel, programmer):
        ws._cleanup_event_subscription(id(client), engine)
        engine.ws.remove_client(client)


# --- A device setting write --------------------------------------------------


async def _put_setting(engine, monkeypatch, value: Any = 50) -> HTTPException:
    monkeypatch.setattr(device_routes, "_get_engine", lambda: engine)
    with pytest.raises(HTTPException) as caught:
        await device_routes.set_device_setting(
            "acme1", "brightness", device_routes.DeviceSettingRequest(value=value),
        )
    return caught.value


def _ai(engine: Engine) -> tuple[AIToolHandler, AsyncMock]:
    agent = MagicMock()
    agent.send_message = AsyncMock()
    handler = AIToolHandler(agent, engine.devices, engine.events)
    handler._get_engine = lambda: engine
    return handler, agent.send_message


async def _ai_setting(engine: Engine, value: Any = 50) -> dict[str, Any]:
    handler, send = _ai(engine)
    await handler._execute_tool(
        "r1", "set_device_setting", handler._tools["set_device_setting"],
        {"device_id": "acme1", "setting_key": "brightness", "value": value},
    )
    return send.await_args.args[1]


async def test_the_device_page_names_the_device_and_the_setting(engine, monkeypatch):
    answer = await _put_setting(engine, monkeypatch)
    assert answer.status_code == 500
    assert answer.detail == SETTING_FAILED


async def test_the_cloud_ai_gets_the_same_sentence_for_a_setting(engine):
    payload = await _ai_setting(engine)
    assert payload["success"] is False
    _assert_private(payload)
    assert payload["error"] == SETTING_FAILED


async def test_a_drivers_connection_error_reads_as_not_connected(engine, monkeypatch):
    _AcmeDriver.failure = ConnectionError(PRIVATE)
    answer = await _put_setting(engine, monkeypatch)
    assert answer.status_code == 503
    assert answer.detail == "Device 'acme1' is not connected"
    payload = await _ai_setting(engine)
    _assert_private(payload)
    assert payload["error"] == "Device 'acme1' is not connected"


async def test_a_setting_failure_is_in_the_devices_own_log(engine, monkeypatch, caplog):
    buffer = LogBuffer(maxlen=50)
    handler = BufferHandler(buffer)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO):
            await _put_setting(engine, monkeypatch)
    finally:
        root.removeHandler(handler)

    line = f"[acme1] Setting 'brightness' failed: RuntimeError: {PRIVATE}"
    assert line in [r.getMessage() for r in caplog.records]
    assert line in [e["message"] for e in buffer.get_recent(100, device="acme1")]


async def test_the_platforms_setting_refusals_keep_their_sentence(engine, monkeypatch):
    # A value the setting does not accept: the device manager's own gate.
    answer = await _put_setting(engine, monkeypatch, value=999)
    assert answer.status_code == 400
    assert "at most 100" in answer.detail
    assert "at most 100" in (await _ai_setting(engine, value=999))["error"]

    # The device saying no, in a sentence written for the reader.
    _AcmeDriver.failure = CommandRefusedError(REFUSAL)
    answer = await _put_setting(engine, monkeypatch)
    assert answer.status_code == 409
    assert answer.detail == REFUSAL
    assert (await _ai_setting(engine))["error"] == REFUSAL

    # The device manager's own "not connected".
    await engine.devices._devices["acme1"].disconnect()
    engine.state.set("device.acme1.connected", False)
    answer = await _put_setting(engine, monkeypatch)
    assert answer.status_code == 503
    assert answer.detail == "Device 'acme1' is not connected"
    assert (await _ai_setting(engine))["error"] == "Device 'acme1' is not connected"


# --- The cloud AI's catch-all --------------------------------------------------


async def _escaped(engine: Engine, exc: Exception) -> dict[str, Any]:
    """What the cloud is sent for a tool that raised ``exc``."""
    handler, send = _ai(engine)

    async def tool(_input: dict) -> Any:
        raise exc

    await handler._execute_tool("r1", "list_devices", tool, {})
    return send.await_args.args[1]


@pytest.mark.parametrize("exc", [
    RuntimeError(PRIVATE),
    ValueError(PRIVATE),
    KeyError(PRIVATE),
    TypeError(PRIVATE),
    ConnectionError(PRIVATE),
    TimeoutError(PRIVATE),
    OSError(PRIVATE),
    FileNotFoundError(2, "No such file or directory", f"/srv/{PRIVATE}"),
    httpx.ConnectError(PRIVATE),
])
async def test_an_exception_the_platform_did_not_word_stays_local(engine, exc, caplog):
    with caplog.at_level(logging.INFO):
        payload = await _escaped(engine, exc)
    assert payload["success"] is False
    _assert_private(payload)
    assert payload["error"] == TOOL_FAILED
    # In the record's message, which is what the Log view shows.
    assert any(PRIVATE in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("exc,sentence", [
    (DeviceNotFoundError("Device 'acme9' not found"), "Device 'acme9' not found"),
    (UnknownCommandError("Command 'flash' is not declared by this driver"),
     "Command 'flash' is not declared by this driver"),
    (ISCRemoteError("The command is not on this system's allowed list"),
     "The command is not on this system's allowed list"),
    (CommunityArtifactError("This plugin was updated a few minutes ago and cannot "
                            "be installed yet. Try again in five minutes."),
     "Try again in five minutes."),
    (ConnectionRefusedError(61, "Connection refused"), "Could not connect"),
])
async def test_the_platforms_own_sentence_still_reaches_the_model(engine, exc, sentence):
    payload = await _escaped(engine, exc)
    assert payload["success"] is False
    assert sentence in payload["error"]


async def test_a_validation_error_keeps_the_platforms_sentence_and_nothing_else(engine):
    """What the model needs to correct its next call, without pydantic's echo
    of the input or its documentation link."""
    try:
        DeviceConfig(id="lobby.widget", name="Lobby Widget", driver="acme_widget")
    except ValidationError as exc:
        payload = await _escaped(engine, exc)
    assert "id: Value error, Device ID 'lobby.widget' must not contain dots" in payload["error"]
    assert "input_value" not in payload["error"]
    assert "errors.pydantic.dev" not in payload["error"]


# --- A control that calls a script ---------------------------------------------


async def test_a_script_call_words_a_driver_fault_for_the_room(engine, monkeypatch):
    pressed = await engine.handle_ui_event("press", "flash_script")
    assert pressed[0]["called"] is False
    assert pressed[0]["error"] == FOR_THE_ROOM

    # The frame the pressing panel gets.
    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    await ws._handle_message(None, {"type": "ui.press", "element_id": "flash_script"},
                             client_type="panel")
    frames = [call.args[1] for call in sent.await_args_list]
    _assert_private(frames)
    assert frames[-1]["message"] == FOR_THE_ROOM


async def test_a_drivers_connection_error_through_a_script_keeps_its_text_out(engine):
    """The device manager's own refusals carry ``device_id`` and keep their
    text; a ConnectionError a driver raised does not, and must not read as
    one of them."""
    _AcmeDriver.failure = ConnectionError(PRIVATE)
    pressed = await engine.handle_ui_event("press", "flash_script")
    _assert_private(pressed)
    assert pressed[0]["error"] == "Lobby Widget is not connected."


async def test_a_refusal_through_a_script_keeps_its_sentence(engine):
    _AcmeDriver.failure = CommandRefusedError(REFUSAL)
    pressed = await engine.handle_ui_event("press", "flash_script")
    assert pressed[0]["error"] == REFUSAL


async def test_a_script_calls_driver_fault_is_in_the_devices_own_log(engine, caplog):
    with caplog.at_level(logging.INFO):
        await engine.handle_ui_event("press", "flash_script")
    line = f"[acme1] Command 'flash_led' failed: RuntimeError: {PRIVATE}"
    assert line in [r.getMessage() for r in caplog.records]


# --- script.error is the Programmer's ------------------------------------------


async def test_script_error_reaches_the_programmer_and_no_panel(engine, sockets):
    panel, programmer = sockets
    engine.events.on("script.error", engine._on_script_error)
    await engine.handle_ui_event("press", "flash_script")
    await engine.ws.flush_sends()

    assert panel.frames("script.error") == []
    _assert_private(panel.sent)
    errors = programmer.frames("script.error")
    assert len(errors) == 1
    assert errors[0]["handler"] == "flash"
    assert PRIVATE in errors[0]["error"]
    assert "Traceback" in errors[0]["traceback"]


# --- device.error, as an event subscription receives it ------------------------


async def test_a_panel_subscriber_gets_device_errors_worded_for_the_room(engine, sockets):
    panel, programmer = sockets
    for client, client_type in ((panel, "panel"), (programmer, "programmer")):
        ws._subscribe_events(client, engine, ("device.*",), client_type)
    on_the_bus: list[dict] = []
    engine.events.on("device.error.*", lambda _e, payload: on_the_bus.append(payload))

    await engine.handle_ui_event("press", "flash")
    await engine.ws.flush_sends()

    seen = [f for f in panel.frames("event") if f["event"] == "device.error.acme1"]
    assert len(seen) == 1
    _assert_private(panel.sent)
    assert seen[0]["payload"] == {
        "device_id": "acme1",
        "error": "Lobby Widget reported an error. Contact support.",
    }

    # The Programmer, and anything else on the bus (a trigger's
    # $trigger.error), keep the text: the panel's copy is a new payload.
    mine = [f for f in programmer.frames("event") if f["event"] == "device.error.acme1"]
    assert PRIVATE in mine[0]["payload"]["error"]
    assert PRIVATE in on_the_bus[0]["error"]


async def test_a_queued_setting_that_failed_is_worded_for_the_room_too(engine, sockets):
    panel, _programmer = sockets
    ws._subscribe_events(panel, engine, ("device.error.*",), "panel")
    await engine.events.emit("device.error.acme1", {
        "device_id": "acme1",
        "error": f"Pending setting 'brightness' failed to apply: {PRIVATE}",
        "source": "pending_settings",
    })
    await engine.ws.flush_sends()

    frames = panel.frames("event")
    assert len(frames) == 1
    _assert_private(panel.sent)
    assert frames[0]["payload"] == {
        "device_id": "acme1",
        "error": "Lobby Widget reported an error. Contact support.",
        "source": "pending_settings",
    }


async def test_a_catalog_lookup_that_failed_keeps_the_library_text_local(engine, monkeypatch):
    async def refuse(self, url, **_kwargs):
        raise httpx.ConnectError(PRIVATE)

    monkeypatch.setattr(httpx.AsyncClient, "get", refuse)
    handler, send = _ai(engine)
    await handler._execute_tool(
        "r1", "find_driver_for_device", handler._tools["find_driver_for_device"],
        {"manufacturer": "Acme", "model": "Widget 3000"},
    )
    payload = send.await_args.args[1]
    assert payload["success"] is False
    _assert_private(payload)
    assert payload["error"] == "Failed to fetch the community device catalog"
