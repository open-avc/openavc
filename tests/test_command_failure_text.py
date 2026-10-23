"""A device command's failure, as every door that sent it answers.

Send Command keeps the text of an exception a driver raised out of its answer:
that text was written for whoever wrote the driver, not for the reader, and may
hold what should stay private. The answer names the device and the command and
points at the Device Log, where the raw text is logged under the device's id.
The other doors to the same command (a panel press, a macro step, a group
command, the WebSocket ``command`` frame, a peer system, the cloud and the
cloud AI) answer the same way, a panel in words for the room. A refusal the
device worded for the reader (``CommandRefusedError``) keeps its sentence at
every door.

An invented device (Acme) throughout, on the real device manager.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.api import ws
from openavc.api.error_messages import friendly_error
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.cloud.command_handler import CommandHandler
from openavc.core.engine import Engine
from openavc.core.isc import ISCManager
from openavc.core.project_loader import load_project
from openavc.drivers.base import BaseDriver, CommandRefusedError
from openavc.utils.log_buffer import BufferHandler, LogBuffer

PRIVATE = "private: /firmware/secrets/token"
REFUSAL = "Encoder 001 is offline"
FOR_THE_ROOM = "Lobby Widget couldn't run Flash LED. Contact support."
FOR_THE_PROGRAMMER = (
    "Lobby Widget couldn't run Flash LED. The Device Log on its page has the details."
)


class _AcmeDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"flash_led": {"label": "Flash LED", "params": {}}},
    }

    #: What the next command raises.
    failure: Exception = RuntimeError(PRIVATE)

    async def connect(self) -> None:
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        raise self.failure


def _conditionals(depth: int) -> list[dict[str, Any]]:
    """``depth`` conditionals, each inside the one before."""
    steps: list[dict[str, Any]] = []
    for _ in range(depth):
        steps = [{
            "action": "conditional",
            "condition": {"key": "var.always", "operator": "eq", "value": True},
            "then_steps": steps,
        }]
    return steps


@pytest.fixture
async def engine(tmp_path):
    flash = {"action": "device.command", "device": "acme1", "command": "flash_led"}
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.13.0",
        "project": {"id": "test", "name": "Test"},
        "devices": [{"id": "acme1", "name": "Lobby Widget", "driver": "acme_widget"}],
        "device_groups": [{"id": "widgets", "name": "Widgets", "device_ids": ["acme1"]}],
        "macros": [
            {"id": "flash", "name": "Flash", "steps": [flash]},
            {"id": "flash_or_stop", "name": "Flash or stop", "stop_on_error": True,
             "steps": [flash]},
            {"id": "outer", "name": "Outer", "stop_on_error": True,
             "steps": [{"action": "macro", "macro": "flash_or_stop"}]},
            {"id": "flash_all", "name": "Flash all", "steps": [
                {"action": "group.command", "group": "widgets", "command": "flash_led"},
            ]},
            {"id": "too_deep", "name": "Too deep", "stop_on_error": True,
             "steps": _conditionals(7)},
        ],
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [{
            "id": "flash", "type": "button",
            "bindings": {"do": {"press": [dict(flash)]}},
        }]}]},
    }))
    result = Engine(str(path))
    result.project = load_project(path)
    result.macros.load_macros([m.model_dump() for m in result.project.macros])
    result.macros.load_groups([g.model_dump() for g in result.project.device_groups])
    result.state.set("var.always", True)
    driver = _AcmeDriver("acme1", {}, result.state, result.events)
    await driver.connect()
    result.devices._devices["acme1"] = driver
    result.state.set("device.acme1.name", "Lobby Widget")
    result.state.set("device.acme1.host", "192.0.2.10")
    _AcmeDriver.failure = RuntimeError(PRIVATE)
    yield result
    _AcmeDriver.failure = RuntimeError(PRIVATE)


def _collect(engine: Engine, pattern: str) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    engine.events.on(pattern, lambda _event, payload: seen.append(payload))
    return seen


def _assert_private(*texts: Any) -> None:
    for text in texts:
        assert PRIVATE not in json.dumps(text, default=str)


# --- Every door keeps the driver's text out ---------------------------------


async def test_a_panel_press_answers_for_the_room(engine, monkeypatch):
    result = await engine.handle_ui_event("press", "flash")
    assert result[0]["sent"] is False
    assert result[0]["error"] == FOR_THE_ROOM

    # The frame the pressing panel gets.
    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    await ws._handle_message(None, {"type": "ui.press", "element_id": "flash"},
                             client_type="panel")
    frames = [call.args[1] for call in sent.await_args_list]
    _assert_private(frames)
    assert frames[-1]["message"] == FOR_THE_ROOM


async def test_a_macro_step_answers_both_readers(engine):
    errors = _collect(engine, "macro.step_error.*")
    assert await engine.macros.execute("flash") == "failed"
    assert len(errors) == 1
    assert errors[0]["message"] == FOR_THE_ROOM
    assert errors[0]["error"] == FOR_THE_PROGRAMMER


async def test_stop_on_error_wraps_the_sentence_not_the_exception(engine):
    errors = _collect(engine, "macro.error.*")
    step_errors = _collect(engine, "macro.step_error.*")
    assert await engine.macros.execute("flash_or_stop") == "failed"
    assert len(errors) == 1
    _assert_private(errors, step_errors)
    assert errors[0]["error"].endswith(FOR_THE_PROGRAMMER)

    # The exception the run raises with stop_on_error, as a caller of the
    # steps meets it.
    macro = engine.macros._macros["flash_or_stop"]
    with pytest.raises(RuntimeError) as raised:
        await engine.macros.execute_steps(macro["steps"], stop_on_error=True)
    _assert_private(str(raised.value))
    assert str(raised.value).endswith(FOR_THE_PROGRAMMER)


async def test_a_macro_that_calls_one_that_stopped_carries_only_the_sentence(engine):
    errors = _collect(engine, "macro.error.*")
    step_errors = _collect(engine, "macro.step_error.*")
    assert await engine.macros.execute("outer") == "failed"
    assert [e["macro_id"] for e in errors] == ["flash_or_stop", "outer"]
    assert len(step_errors) == 2
    _assert_private(errors, step_errors)


async def test_a_group_command_answers_both_readers(engine):
    progress = _collect(engine, "macro.progress.*")
    step_errors = _collect(engine, "macro.step_error.*")
    assert await engine.macros.execute("flash_all") == "failed"
    results = [p for p in progress if p.get("status") == "group_complete"]
    assert len(results) == 1
    member = results[0]["device_results"][0]
    assert member["error"] == FOR_THE_PROGRAMMER
    assert member["message"] == FOR_THE_ROOM
    assert len(step_errors) == 1
    assert step_errors[0]["error"] == FOR_THE_PROGRAMMER
    assert step_errors[0]["message"] == FOR_THE_ROOM


@pytest.mark.parametrize("client_type,expected", [
    ("panel", FOR_THE_ROOM),
    ("programmer", FOR_THE_PROGRAMMER),
])
async def test_the_websocket_command_answers_whoever_sent_it(
    engine, monkeypatch, client_type, expected,
):
    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    await ws._handle_message(None, {"type": "command", "device_id": "acme1",
                                    "command": "flash_led"}, client_type=client_type)
    ack = sent.await_args.args[1]
    assert ack["type"] == "command.ack"
    assert ack["success"] is False
    assert ack["error"] == expected


def _isc(engine: Engine) -> tuple[ISCManager, AsyncMock]:
    isc = ISCManager(
        engine.state, engine.events, engine.devices, [], "key", "here", "Here", 8080,
        allowed_remote_commands=["*"],
    )
    conn = MagicMock()
    conn.send = AsyncMock()
    isc._connections["peer"] = conn
    return isc, conn.send


async def test_a_peer_system_gets_the_programmers_sentence(engine):
    isc, send = _isc(engine)
    await isc._handle_remote_command("peer", {
        "id": "r1", "device": "acme1", "command": "flash_led", "params": {},
    })
    answer = send.await_args.args[0]
    assert answer["success"] is False
    assert answer["error"] == FOR_THE_PROGRAMMER


def _cloud(engine: Engine) -> tuple[CommandHandler, AsyncMock]:
    agent = MagicMock()
    agent.send_message = AsyncMock()
    return CommandHandler(agent, engine.devices, engine.events), agent.send_message


async def test_the_cloud_gets_the_programmers_sentence(engine):
    handler, send = _cloud(engine)
    await handler._handle_device_command(
        {"device_id": "acme1", "command": "flash_led", "params": {}}, "r1", "someone",
    )
    payload = send.await_args.args[1]
    assert payload["success"] is False
    assert payload["error"] == FOR_THE_PROGRAMMER


def _ai(engine: Engine) -> tuple[AIToolHandler, AsyncMock]:
    agent = MagicMock()
    agent.send_message = AsyncMock()
    return AIToolHandler(agent, engine.devices, engine.events), agent.send_message


async def test_the_cloud_ai_gets_the_programmers_sentence(engine):
    handler, send = _ai(engine)
    await handler._execute_tool(
        "r1", "send_device_command", handler._tools["send_device_command"],
        {"device_id": "acme1", "command": "flash_led"},
    )
    payload = send.await_args.args[1]
    assert payload["success"] is False
    _assert_private(payload)
    assert payload["error"] == FOR_THE_PROGRAMMER


# --- ...and logs it where the sentence says -----------------------------------


async def test_the_raw_text_is_in_the_devices_own_log(engine, caplog):
    buffer = LogBuffer(maxlen=50)
    handler = BufferHandler(buffer)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO):
            await engine.handle_ui_event("press", "flash")
        # A busy system: the shared ring turns over many times before
        # anybody opens the device's page.
        noise = logging.getLogger("openavc.core.engine")
        for i in range(200):
            noise.warning(f"unrelated line {i}")
    finally:
        root.removeHandler(handler)

    line = f"[acme1] Command 'flash_led' failed: RuntimeError: {PRIVATE}"
    assert line in [r.getMessage() for r in caplog.records]
    assert line in [e["message"] for e in buffer.get_recent(100, device="acme1")]


# --- A sentence written for the reader still reaches every door --------------


async def test_a_refusal_keeps_its_sentence_at_every_door(engine, monkeypatch):
    _AcmeDriver.failure = CommandRefusedError(REFUSAL)

    pressed = await engine.handle_ui_event("press", "flash")
    assert pressed[0]["error"] == REFUSAL

    step_errors = _collect(engine, "macro.step_error.*")
    await engine.macros.execute("flash")
    assert step_errors[0]["message"] == REFUSAL
    assert step_errors[0]["error"] == REFUSAL

    monkeypatch.setattr(ws, "get_engine_optional", lambda: engine)
    sent = AsyncMock()
    monkeypatch.setattr(ws, "_send_ws", sent)
    for client_type in ("panel", "programmer"):
        await ws._handle_message(None, {"type": "command", "device_id": "acme1",
                                        "command": "flash_led"}, client_type=client_type)
        assert sent.await_args.args[1]["error"] == REFUSAL

    isc, isc_send = _isc(engine)
    await isc._handle_remote_command("peer", {
        "id": "r1", "device": "acme1", "command": "flash_led", "params": {},
    })
    assert isc_send.await_args.args[0]["error"] == REFUSAL

    cloud, cloud_send = _cloud(engine)
    await cloud._handle_device_command(
        {"device_id": "acme1", "command": "flash_led", "params": {}}, "r1", "someone",
    )
    assert cloud_send.await_args.args[1]["error"] == REFUSAL

    ai, ai_send = _ai(engine)
    await ai._execute_tool(
        "r1", "send_device_command", ai._tools["send_device_command"],
        {"device_id": "acme1", "command": "flash_led"},
    )
    assert ai_send.await_args.args[1]["error"] == REFUSAL


# --- The platform's own sentences are not a driver's -------------------------


async def test_the_conditional_depth_limit_keeps_its_sentence(engine):
    step_errors = _collect(engine, "macro.step_error.*")
    errors = _collect(engine, "macro.error.*")
    assert await engine.macros.execute("too_deep") == "failed"
    assert "Conditional nesting depth limit (5) exceeded" in step_errors[-1]["error"]
    assert "Conditional nesting depth limit (5) exceeded" in step_errors[-1]["message"]
    assert "Conditional nesting depth limit (5) exceeded" in errors[0]["error"]


def test_friendly_error_still_passes_a_platform_runtime_error_through():
    """Its callers that are not device commands depend on it."""
    sentence = "Conditional nesting depth limit (5) exceeded"
    assert friendly_error(RuntimeError(sentence)) == sentence
