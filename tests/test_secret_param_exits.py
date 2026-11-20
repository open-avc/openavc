"""A secret command parameter's value, at every exit a command has.

A parameter the driver marks secret (a stream key, a PIN) or names
``password`` is for the device. The send holds the command's declaration,
so it decides what else the value may reach:

  - a value that does not fit is refused by the parameter's name and the rule
    it broke, never with the value, so the sentence a panel, a macro step, the
    Programmer, a peer system, the cloud and the cloud AI receive holds none;
  - what the cloud door and the AI door log and announce of a command shows a
    secret parameter as ``***``, and so does a macro step's log line;
  - the value is registered with the device's masking before the command is
    checked, so the device's traffic, a reply or a driver's error that echoes
    it, and any log line, mask it as they mask the device's password.

The refusal and the announcements read the declaration, not the value, so a
value too short for the masking (under four characters) or a number is kept
out as well. An invented device (Acme) throughout, on the real device manager.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.api import ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.command_handler import CommandHandler
from openavc.core.engine import Engine
from openavc.core.isc import ISCManager
from openavc.core.project_loader import load_project
from openavc.drivers.base import (
    BaseDriver,
    CommandParamError,
    DeviceSettingValueError,
    normalize_and_validate_command_params,
    validate_device_setting_value,
)
from openavc.utils.log_redaction import REDACTED, get_secret_registry
from openavc.utils.secret_audience import Exit

# Under the masking's four-character floor: only the declaration keeps it out.
SHORT = "q7z"
# A stream key that fits, and one long enough to register that does not.
KEY = "LIVE-ABCD1234"
WRONG_KEY = "live-wxyz9876"
PIN = 123456

_COMMANDS: dict[str, Any] = {
    "set_stream_key": {
        "label": "Set Stream Key",
        "params": {
            "stream_key": {
                "type": "string", "secret": True, "required": True,
                "pattern": "[A-Z0-9-]{8,16}",
            },
        },
    },
    "set_pin": {
        "label": "Set PIN",
        "params": {"pin": {"type": "integer", "secret": True, "max": 9999}},
    },
    "sign_in": {
        "label": "Sign In",
        "params": {"password": {"type": "string", "pattern": "[a-z]{6}"}},
    },
    "set_level": {
        "label": "Set Level",
        "params": {"level": {"type": "integer", "max": 2000}},
    },
}


class _AcmeDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": _COMMANDS,
    }

    #: Whether the next command fails, quoting the key it was sent.
    echo_failure = False

    async def connect(self) -> None:
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        if self.echo_failure:
            raise RuntimeError(f"device rejected {(params or {}).get('stream_key')}")
        return None


def _step(value: Any) -> dict[str, Any]:
    return {"action": "device.command", "device": "acme1",
            "command": "set_stream_key", "params": {"stream_key": value}}


@pytest.fixture
def registry():
    reg = get_secret_registry()
    reg.clear()
    yield reg
    reg.clear()


@pytest.fixture
async def engine(tmp_path, registry):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.13.0",
        "project": {"id": "test", "name": "Test"},
        "devices": [{"id": "acme1", "name": "Lobby Widget", "driver": "acme_widget"}],
        "macros": [
            {"id": "short_key", "name": "Short key", "steps": [_step(SHORT)]},
            {"id": "good_key", "name": "Good key", "steps": [_step(KEY)]},
        ],
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [
            {"id": "key", "type": "button",
             "bindings": {"do": {"press": [_step(SHORT)]}}},
            {"id": "unset_key", "type": "button",
             "bindings": {"do": {"press": [_step("$var.unset_key")]}}},
            {"id": "ghost_level", "type": "button",
             "bindings": {"do": {"press": [{
                 "action": "device.command", "device": "ghost",
                 "command": "set_level", "params": {"level": 40},
             }]}}},
        ]}]},
    }))
    result = Engine(str(path))
    result.project = load_project(path)
    result.macros.load_macros([m.model_dump() for m in result.project.macros])
    driver = _AcmeDriver("acme1", {}, result.state, result.events)
    await driver.connect()
    result.devices._devices["acme1"] = driver
    result.state.set("device.acme1.name", "Lobby Widget")
    _AcmeDriver.echo_failure = False
    yield result
    _AcmeDriver.echo_failure = False


def _collect(engine: Engine, pattern: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    engine.events.on(pattern, lambda _event, payload: seen.append(payload))
    return seen


def _assert_kept_out(value: Any, *texts: Any) -> None:
    for text in texts:
        assert str(value) not in json.dumps(text, default=str)


# --- The refusal ---------------------------------------------------------------


@pytest.mark.parametrize("command,params,sentence", [
    ("set_stream_key", {"stream_key": SHORT},
     "'set_stream_key': 'stream_key' does not match the required format ([A-Z0-9-]{8,16})"),
    ("set_pin", {"pin": PIN}, "'set_pin': 'pin' must be at most 9999"),
    ("set_pin", {"pin": SHORT}, "'set_pin': 'pin' must be a whole number"),
    ("sign_in", {"password": SHORT},
     "'sign_in': 'password' does not match the required format ([a-z]{6})"),
])
def test_a_refused_secret_parameter_is_named_without_its_value(command, params, sentence):
    with pytest.raises(CommandParamError) as refused:
        normalize_and_validate_command_params(
            command, _COMMANDS[command]["params"], params, label=_COMMANDS[command]["label"],
        )
    assert str(refused.value) == sentence


def test_any_other_refusal_still_quotes_the_value():
    with pytest.raises(CommandParamError) as refused:
        normalize_and_validate_command_params(
            "set_level", _COMMANDS["set_level"]["params"], {"level": 2500}, label="Set Level",
        )
    assert str(refused.value) == "'set_level': 'level' must be at most 2000, got 2500"


@pytest.mark.parametrize("key,definition,value,sentence", [
    ("pin", {"type": "integer", "secret": True, "max": 9999}, PIN, "'pin' must be at most 9999"),
    ("pin", {"type": "integer", "secret": True}, SHORT, "'pin' must be a whole number"),
    ("code", {"type": "string", "secret": True, "regex": "[0-9]{4}"}, SHORT,
     "'code' does not match the required format ([0-9]{4})"),
    ("code", {"type": "string", "secret": True, "values": ["1111", "2222"]}, SHORT,
     "'code' must be one of: 1111, 2222"),
])
def test_a_refused_secret_setting_is_named_without_its_value(key, definition, value, sentence):
    with pytest.raises(DeviceSettingValueError) as refused:
        validate_device_setting_value(key, definition, value)
    assert str(refused.value) == sentence


def test_any_other_setting_refusal_still_quotes_the_value():
    with pytest.raises(DeviceSettingValueError) as refused:
        validate_device_setting_value("volume", {"type": "integer", "max": 100}, 150)
    assert str(refused.value) == "'volume' must be at most 100, got 150"


# --- Every door's answer -------------------------------------------------------


async def test_a_panel_press_s_failure_holds_no_value(engine, monkeypatch):
    pressed = await engine.handle_ui_event("press", "key")
    assert pressed[0]["sent"] is False
    _assert_kept_out(SHORT, pressed)

    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    await ws._handle_message(None, {"type": "ui.press", "element_id": "key"},
                             client_type="panel")
    frames = [call.args[1] for call in sent.await_args_list]
    assert frames[-1]["type"] == "error"
    assert "'stream_key' does not match the required format" in frames[-1]["message"]
    _assert_kept_out(SHORT, frames)


async def test_a_macro_step_s_failure_holds_no_value(engine):
    errors = _collect(engine, "macro.step_error.*")
    assert await engine.macros.execute("short_key") == "failed"
    assert "'stream_key' does not match the required format" in errors[0]["message"]
    assert "'stream_key' does not match the required format" in errors[0]["error"]
    _assert_kept_out(SHORT, errors)


@pytest.mark.parametrize("client_type", ["panel", "programmer"])
async def test_a_command_frame_s_answer_holds_no_value(engine, monkeypatch, client_type):
    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    await ws._handle_message(None, {
        "type": "command", "device_id": "acme1", "command": "set_stream_key",
        "params": {"stream_key": SHORT},
    }, client_type=client_type)
    ack = sent.await_args.args[1]
    assert ack["success"] is False
    _assert_kept_out(SHORT, ack)


async def test_a_peer_system_s_answer_holds_no_value(engine):
    isc = ISCManager(
        engine.state, engine.events, engine.devices, [], "key", "here", "Here", 8080,
        allowed_remote_commands=["*"],
    )
    conn = MagicMock()
    conn.send = AsyncMock()
    isc._connections["peer"] = conn
    await isc._handle_remote_command("peer", {
        "id": "r1", "device": "acme1", "command": "set_stream_key",
        "params": {"stream_key": SHORT},
    })
    answer = conn.send.await_args.args[0]
    assert answer["success"] is False
    _assert_kept_out(SHORT, answer)


# --- A press's record ---------------------------------------------------------------
# The record goes back to whoever pressed, the cloud AI's simulated press
# included.


async def test_a_press_s_record_shows_a_secret_parameter_masked(engine):
    for dry_run in (True, False):
        pressed = await engine.handle_ui_event("press", "key", dry_run=dry_run)
        assert pressed[0]["params"] == {"stream_key": REDACTED}


async def test_a_press_s_preview_still_shows_a_value_that_resolved_to_nothing(engine):
    previewed = await engine.handle_ui_event("press", "unset_key", dry_run=True)
    assert previewed[0]["params"] == {"stream_key": None}


async def test_a_press_on_a_device_with_no_running_driver_shows_no_value(engine):
    # No declaration to read, so no value is shown.
    previewed = await engine.handle_ui_event("press", "ghost_level", dry_run=True)
    assert previewed[0]["params"] == {"level": REDACTED}


# --- What the cloud and AI doors log and announce --------------------------------


def _cloud(engine: Engine) -> tuple[CommandHandler, AsyncMock]:
    agent = MagicMock()
    agent.send_message = AsyncMock()
    return CommandHandler(agent, engine.devices, engine.events), agent.send_message


@pytest.mark.parametrize("value", [SHORT, KEY])
async def test_the_cloud_door_shows_a_secret_parameter_masked(engine, caplog, value):
    announced = _collect(engine, "cloud.command")
    handler, send = _cloud(engine)
    with caplog.at_level(logging.INFO):
        await handler._handle_device_command(
            {"device_id": "acme1", "command": "set_stream_key",
             "params": {"stream_key": value}}, "r1", "someone",
        )
    assert announced[0]["params"] == {"stream_key": REDACTED}
    lines = [r.getMessage() for r in caplog.records]
    assert any("Cloud command: someone" in line for line in lines)
    _assert_kept_out(value, lines, announced, send.await_args.args[1])


async def test_the_cloud_door_shows_no_value_for_a_device_it_cannot_read(engine, caplog):
    announced = _collect(engine, "cloud.command")
    handler, _send = _cloud(engine)
    with caplog.at_level(logging.INFO):
        await handler._handle_device_command(
            {"device_id": "ghost", "command": "set_stream_key",
             "params": {"stream_key": KEY}}, "r1", "someone",
        )
    assert announced[0]["params"] == {"stream_key": REDACTED}
    _assert_kept_out(KEY, [r.getMessage() for r in caplog.records])


async def test_the_cloud_door_shows_any_other_parameter_as_sent(engine):
    announced = _collect(engine, "cloud.command")
    handler, _send = _cloud(engine)
    await handler._handle_device_command(
        {"device_id": "acme1", "command": "set_level", "params": {"level": 40}},
        "r1", "someone",
    )
    assert announced[0]["params"] == {"level": 40}


@pytest.mark.parametrize("value", [SHORT, KEY])
async def test_the_ai_door_announces_a_secret_parameter_masked(engine, value):
    announced = _collect(engine, "ai.device_command")
    agent = MagicMock()
    agent.send_message = AsyncMock()
    handler = AIToolHandler(agent, engine.devices, engine.events)
    await handler._execute_tool(
        "r1", "send_device_command", handler._tools["send_device_command"],
        {"device_id": "acme1", "command": "set_stream_key", "params": {"stream_key": value}},
    )
    assert announced[0]["params"] == {"stream_key": REDACTED}
    _assert_kept_out(value, announced, agent.send_message.await_args.args[1])


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def test_a_macro_step_s_log_line_shows_a_secret_parameter_masked(engine):
    # On the logger itself: another test may have stopped it propagating.
    logger = logging.getLogger("openavc.core.macro_engine")
    handler, level = _Lines(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        assert await engine.macros.execute("good_key") == "completed"
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    assert any("Macro step: acme1.set_stream_key" in line for line in handler.lines)
    _assert_kept_out(KEY, handler.lines)


# --- The device's masking learns the value ----------------------------------------


async def test_a_sent_secret_parameter_is_masked_in_the_device_s_traffic(engine, registry):
    await engine.devices.send_command("acme1", "set_stream_key", {"stream_key": KEY})
    assert registry.masked(Exit.LOG, f"SET {KEY}", "acme1") == f"SET {REDACTED}"
    assert registry.masked(Exit.LOG, f"SET {KEY}") == f"SET {REDACTED}"


async def test_a_refused_secret_parameter_is_masked_too(engine, registry):
    with pytest.raises(CommandParamError):
        await engine.devices.send_command("acme1", "set_stream_key", {"stream_key": WRONG_KEY})
    assert registry.masked(Exit.LOG, f"SET {WRONG_KEY}") == f"SET {REDACTED}"


async def test_a_numeric_secret_parameter_is_masked_as_its_digits(engine, registry):
    await engine.devices.send_command("acme1", "set_pin", {"pin": 4321})
    assert registry.masked(Exit.LOG, "PIN 4321", "acme1") == f"PIN {REDACTED}"


async def test_a_driver_s_error_that_echoes_the_value_is_announced_masked(engine):
    errors = _collect(engine, "device.error.*")
    _AcmeDriver.echo_failure = True
    with pytest.raises(RuntimeError):
        await engine.devices.send_command("acme1", "set_stream_key", {"stream_key": KEY})
    assert errors[0]["error"] == f"device rejected {REDACTED}"


async def test_any_other_parameter_is_not_registered(engine, registry):
    await engine.devices.send_command("acme1", "set_level", {"level": 1234})
    with pytest.raises(CommandParamError):
        await engine.devices.send_command("acme1", "set_level", {"level": 4321})
    assert registry.masked(Exit.LOG, "LEVEL 1234 4321") == "LEVEL 1234 4321"


def test_a_value_already_known_keeps_the_compiled_masking(registry):
    registry.add_runtime_secret("acme1", KEY)
    assert registry.masked(Exit.LOG, KEY, "acme1") == REDACTED
    compiled = registry._device_patterns["acme1"]
    registry.add_runtime_secret("acme1", KEY)
    assert registry._device_patterns.get("acme1") is compiled
