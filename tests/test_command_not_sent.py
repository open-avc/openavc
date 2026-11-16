"""A command that went nowhere, or went out and only partly worked, says so.

The device manager's send is where a command's outcome is decided: a send
that is not a plain success raises ``CommandOutcomeError`` with its code, and
the readers that need more than "it failed" (the panel press's record, a
group step's members) read that code through ``command_outcome.outcome_of``.

The two senders that used to give up without raising -- an OSC-form or an
HTTP-form command on a connection of another kind -- and a command with
nothing to send are the "not sent" case: nothing reached the device, so no
door may call it sent, and the restart or sleep it declares is not armed.

Every device here is invented (Acme).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openavc.api.error_messages import describe_command_failure
from openavc.api.errors import StructuredApiError
from openavc.api.routes import devices as device_routes
from openavc.cloud.command_handler import CommandHandler
from openavc.core.device_errors import not_connected
from openavc.core.device_manager import DeviceManager
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.project_loader import load_project
from openavc.core.state_store import StateStore
from openavc.drivers.base import (
    BaseDriver,
    CommandParamError,
    CommandPartialError,
    UnknownCommandError,
)
from openavc.drivers.command_files import FileCommandError
from openavc.drivers.command_outcome import (
    CommandOutcomeError,
    outcome_of,
    went_out,
)
from openavc.drivers.configurable import create_configurable_driver_class

ACME_TCP = {
    "id": "acme_widget_not_sent",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "commands": {
        "power_on": {"label": "Power On", "send": "PWR1\r"},
        # Written as an HTTP request, on a TCP device.
        "reboot": {"label": "Reboot", "path": "/reboot", "restarts_device_for": 60},
        # Written as an OSC message, on a TCP device.
        "standby": {"label": "Standby", "address": "/standby", "sleeps_device": "power_on"},
        # Nothing to send at all.
        "blank": {"label": "Blank", "send": ""},
    },
    "polling": {"queries": ["reboot"]},
}

PARTLY = "Port 3 was turned off and did not come back on. Turn it on from the switch's page."


class _Wire:
    """A TCP connection that records what is written to it."""

    connected = True

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _acme(dm: DeviceManager, device_id: str = "w1") -> Any:
    cls = create_configurable_driver_class(ACME_TCP)
    config = {"host": "10.0.0.5", "port": 5000}
    driver = cls(device_id, config, dm.state, dm.events)
    driver.transport = _Wire()
    dm.state.set(f"device.{device_id}.connected", True)
    dm._devices[device_id] = driver
    dm._device_configs[device_id] = dict(config)
    return driver


def _manager() -> DeviceManager:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return DeviceManager(state=state, events=events)


# --- the send itself --------------------------------------------------------


@pytest.mark.parametrize("command", ["reboot", "standby", "blank"])
async def test_a_command_that_cannot_go_out_raises_not_sent(command):
    dm = _manager()
    driver = _acme(dm)
    with pytest.raises(CommandOutcomeError) as refused:
        await dm.send_command("w1", command)
    assert refused.value.outcome == "not_sent"
    assert driver.transport.sent == []


async def test_the_sentence_names_the_command_and_why():
    dm = _manager()
    _acme(dm)
    with pytest.raises(CommandOutcomeError) as refused:
        await dm.send_command("w1", "reboot")
    assert str(refused.value) == (
        "Reboot was not sent: it is written as an HTTP request, and this "
        "device's connection is TCP. Check the command and the device's connection."
    )
    with pytest.raises(CommandOutcomeError) as refused:
        await dm.send_command("w1", "standby")
    assert str(refused.value) == (
        "Standby was not sent: it is written as an OSC message, and this "
        "device's connection is TCP. Check the command and the device's connection."
    )
    with pytest.raises(CommandOutcomeError) as refused:
        await dm.send_command("w1", "blank")
    assert str(refused.value) == (
        "Blank was not sent: it has nothing to send. Check the command."
    )


async def test_a_command_not_sent_arms_no_restart_and_no_sleep():
    dm = _manager()
    _acme(dm)
    with pytest.raises(CommandOutcomeError):
        await dm.send_command("w1", "reboot")
    with pytest.raises(CommandOutcomeError):
        await dm.send_command("w1", "standby")
    assert dm.restart_seconds_left("w1") is None
    assert dm._sleep.get("w1") is None


async def test_a_command_not_sent_is_a_device_error():
    """The device manager publishes it as it does any send the driver raised
    on: the device's page is where its connection or its driver is fixed."""
    dm = _manager()
    _acme(dm)
    errors: list[dict] = []

    async def heard(_event: str, payload: dict) -> None:
        errors.append(payload)

    dm.events.on("device.error.w1", heard)
    with pytest.raises(CommandOutcomeError) as refused:
        await dm.send_command("w1", "reboot")
    assert errors == [{"device_id": "w1", "error": str(refused.value)}]


async def test_a_command_that_fits_still_goes_out():
    dm = _manager()
    driver = _acme(dm)
    assert await dm.send_command("w1", "power_on") is True
    assert driver.transport.sent == [b"PWR1\r"]


async def test_a_poll_line_that_cannot_go_out_is_a_device_error():
    """The poll names a command that cannot go out: the device's error says
    so every cycle, where it used to be a log line only."""
    dm = _manager()
    driver = _acme(dm)
    errors: list[dict] = []

    async def heard(_event: str, payload: dict) -> None:
        errors.append(payload)

    dm.events.on("device.error.w1", heard)
    await driver.poll()
    assert driver.transport.sent == []
    assert [e["error"] for e in errors] == [
        "Reboot was not sent: it is written as an HTTP request, and this "
        "device's connection is TCP. Check the command and the device's connection."
    ]


# --- the one table: what each failure's code says ----------------------------


@pytest.mark.parametrize(
    "exc,code,out",
    [
        (CommandOutcomeError("not_sent", message="Reboot was not sent."), "not_sent", False),
        (CommandPartialError(PARTLY), "partial", True),
        (CommandOutcomeError("uncertain"), "uncertain", True),
        (CommandOutcomeError("pending"), "pending", True),
        (CommandOutcomeError("failed"), "failed", True),
        (CommandOutcomeError("rejected"), "rejected", True),
        # A refusal before sending, and a driver's or a transport's own fault,
        # carry no code: they are failures, and nothing more is known.
        (UnknownCommandError("No command 'x'"), None, False),
        (CommandParamError("Level must be 0 to 100"), None, False),
        (FileCommandError("Runs from Send Command"), None, False),
        (not_connected("w1"), None, False),
        (TimeoutError(), None, False),
        (RuntimeError("the driver's own text"), None, False),
    ],
)
def test_the_code_each_failure_carries(exc, code, out):
    assert outcome_of(exc) == code
    assert went_out(exc) is out


def test_a_partly_done_command_keeps_the_drivers_sentence():
    exc = CommandPartialError(PARTLY)
    assert isinstance(exc, CommandOutcomeError)
    assert str(exc) == PARTLY


def test_a_code_outside_the_list_is_still_refused():
    with pytest.raises(ValueError, match="Unknown command outcome"):
        CommandOutcomeError("private: /firmware/secrets/token", message="anything")


# --- what each door says -----------------------------------------------------


async def test_the_programmer_reads_why_and_the_space_reads_whom_to_call():
    dm = _manager()
    _acme(dm)
    with pytest.raises(CommandOutcomeError) as refused:
        await dm.send_command("w1", "reboot")
    failure = describe_command_failure(
        refused.value, "w1", "reboot", state=dm.state, driver=dm.get_driver("w1"),
    )
    assert failure.programmer == str(refused.value)
    assert failure.panel == "w1 couldn't run Reboot. Contact support."


async def test_send_command_over_rest_is_refused_with_its_code(monkeypatch):
    dm = _manager()
    _acme(dm)
    engine = SimpleNamespace(state=dm.state, project=None, devices=dm)
    monkeypatch.setattr(device_routes, "_get_engine", lambda: engine)
    with pytest.raises(StructuredApiError) as caught:
        await device_routes.send_command(
            "w1", device_routes.CommandRequest(command="reboot", params={}),
        )
    assert caught.value.status_code == 409
    assert caught.value.fields == {"code": "command_incomplete", "outcome": "not_sent"}
    assert caught.value.detail.startswith("Reboot was not sent:")


async def test_a_partly_done_setting_write_keeps_its_answer(monkeypatch):
    """CommandPartialError is now a CommandOutcomeError; the setting door
    still answers it as before (502, the driver's sentence)."""
    from fastapi import HTTPException

    failure = CommandPartialError(PARTLY)
    engine = SimpleNamespace(
        state=StateStore(), project=None,
        devices=SimpleNamespace(
            set_device_setting=AsyncMock(side_effect=failure),
            get_driver=lambda _: None,
        ),
    )
    monkeypatch.setattr(device_routes, "_get_engine", lambda: engine)
    with pytest.raises(HTTPException) as caught:
        await device_routes.set_device_setting(
            "w1", "gain", device_routes.DeviceSettingRequest(value=1),
        )
    assert not isinstance(caught.value, StructuredApiError)
    assert caught.value.status_code == 502
    assert caught.value.detail == PARTLY


async def test_the_cloud_is_told_it_failed_not_ok():
    dm = _manager()
    _acme(dm)
    results: list[dict] = []
    handler = CommandHandler.__new__(CommandHandler)
    handler._devices = dm
    handler._events = dm.events

    async def send_result(request_id, success, result=None, error=None):
        results.append({"success": success, "result": result, "error": error})

    handler._send_result = send_result
    await handler._handle_device_command(
        {"device_id": "w1", "command": "reboot", "params": {}}, "r1", "someone",
    )
    assert results == [{
        "success": False, "result": None,
        "error": (
            "Reboot was not sent: it is written as an HTTP request, and "
            "this device's connection is TCP. Check the command and the device's connection."
        ),
    }]


# --- the press's record ------------------------------------------------------


class _Partly(BaseDriver):
    """Raises the outcome it is told to; sends nothing itself."""

    DRIVER_INFO = {
        "id": "acme_partly",
        "name": "Acme Partly",
        "transport": "tcp",
        "commands": {"cycle": {"label": "Cycle Port"}},
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.raises: Exception | None = None

    async def send_command(self, command, params=None):
        if self.raises is not None:
            raise self.raises
        return True


def _press_engine(tmp_path: Path, device_id: str, command: str) -> Engine:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.11.0",
        "project": {"id": "p", "name": "P", "description": ""},
        "devices": [], "macros": [], "variables": [],
        "ui": {"settings": {}, "pages": [{
            "id": "main", "name": "Main", "layouts": [],
            "elements": [{
                "id": "b1", "type": "button", "label": "Go",
                "bindings": {"do": {"press": [{
                    "action": "device.command", "device": device_id,
                    "command": command, "params": {},
                }]}},
            }],
        }]},
    }), encoding="utf-8")
    engine = Engine(path)
    engine.project = load_project(engine.project_path)
    return engine


def _partly(engine: Engine, raises: Exception) -> _Partly:
    driver = _Partly("p1", {}, engine.state, engine.events)
    engine.state.set("device.p1.connected", True)
    engine.devices._devices["p1"] = driver
    engine.devices._device_configs["p1"] = {}
    driver.raises = raises
    return driver


async def test_a_partly_done_press_is_recorded_as_sent_with_its_code(tmp_path):
    engine = _press_engine(tmp_path, "p1", "cycle")
    _partly(engine, CommandPartialError(PARTLY))
    [record] = await engine.handle_ui_event("press", "b1", {})
    assert record["sent"] is True
    assert record["outcome"] == "partial"
    assert record["error"] == PARTLY


async def test_an_unconfirmed_press_is_recorded_as_sent_with_its_code(tmp_path):
    engine = _press_engine(tmp_path, "p1", "cycle")
    _partly(engine, CommandOutcomeError("uncertain"))
    [record] = await engine.handle_ui_event("press", "b1", {})
    assert record["sent"] is True
    assert record["outcome"] == "uncertain"


async def test_a_press_that_sent_nothing_is_recorded_as_not_sent(tmp_path):
    engine = _press_engine(tmp_path, "w1", "reboot")
    _acme(engine.devices)
    [record] = await engine.handle_ui_event("press", "b1", {})
    assert record["sent"] is False
    assert record["outcome"] == "not_sent"
    assert record["error"] == "w1 couldn't run Reboot. Contact support."


async def test_a_plain_failure_is_still_recorded_as_before(tmp_path):
    engine = _press_engine(tmp_path, "p1", "cycle")
    _partly(engine, RuntimeError("the driver's own text"))
    [record] = await engine.handle_ui_event("press", "b1", {})
    assert record["sent"] is False
    assert "outcome" not in record


# --- a group step's members --------------------------------------------------


async def test_each_member_carries_its_code():
    dm = _manager()
    _acme(dm, "w1")
    _acme(dm, "w2")
    partly = _Partly("p1", {}, dm.state, dm.events)
    dm.state.set("device.p1.connected", True)
    dm._devices["p1"] = partly
    dm._device_configs["p1"] = {}
    engine = MacroEngine(dm.state, dm.events, dm)
    engine.load_groups([{"id": "wall", "name": "Wall", "device_ids": ["w1", "p1"]}])

    results = await engine._group_command.send("wall", "reboot", {}, None)
    by_id = {r["device_id"]: r for r in results}
    assert by_id["w1"]["success"] is False
    assert by_id["w1"]["outcome"] == "not_sent"
    assert by_id["w1"]["sent"] is False

    partly.raises = CommandPartialError(PARTLY)
    partly.DRIVER_INFO = {**_Partly.DRIVER_INFO, "commands": {"reboot": {"label": "Reboot"}}}
    results = await engine._group_command.send("wall", "reboot", {}, None)
    by_id = {r["device_id"]: r for r in results}
    assert by_id["p1"]["success"] is False
    assert by_id["p1"]["outcome"] == "partial"
    assert by_id["p1"]["sent"] is True
    assert by_id["p1"]["message"] == PARTLY


async def test_a_member_that_took_it_carries_no_code():
    dm = _manager()
    _acme(dm, "w1")
    engine = MacroEngine(dm.state, dm.events, dm)
    engine.load_groups([{"id": "wall", "name": "Wall", "device_ids": ["w1"]}])
    [result] = await engine._group_command.send("wall", "power_on", {}, None)
    assert result == {"device_id": "w1", "name": "w1", "success": True}
