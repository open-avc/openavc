"""A script's ``devices.send`` that did not run says so in the words Send
Command uses, and is still the exception a script catches.

Send Command, a panel press, a macro step, a peer system and the cloud all
word a failed command through ``describe_command_failure``. A script got the
driver's raw exception instead: a bare ``TimeoutError`` with no text at all, or
a driver's own message that was never written for its reader. Now the
exception a script meets carries the Programmer's sentence, keeps the type
(and the attributes) of what was raised, so ``except TimeoutError`` and
``except ConnectionError`` still catch it, and chains the original for the
traceback. The device's own log gets the details once, wherever the script
was started from.

An invented device (Acme) throughout, on the real device manager.
"""

from __future__ import annotations

import json
import logging
import textwrap
from typing import Any

import httpx
import pytest

from openavc.api.error_messages import describe_command_failure
from openavc.core import script_api
from openavc.core.device_errors import DeviceNotFoundError
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.script_engine import ScriptEngine
from openavc.drivers.base import BaseDriver, CommandParamError, CommandRefusedError, UnknownCommandError
from openavc.drivers.udp_side import WakeAddressError

PRIVATE = "private: /firmware/secrets/token"
REFUSAL = "Encoder 001 is offline"
FOR_THE_PROGRAMMER = (
    "Lobby Widget couldn't run Flash LED. The Device Log on its page has the details."
)
FOR_THE_ROOM = "Lobby Widget couldn't run Flash LED. Contact support."
NO_ANSWER = (
    "Lobby Widget (192.0.2.10) did not respond in time. Check its power, network connection "
    "and configured port, then retry."
)


class _AcmeDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {
            "flash_led": {"label": "Flash LED", "params": {}},
            "dim": {
                "label": "Dim",
                "params": {"level": {"type": "integer", "min": 0, "max": 100}},
            },
        },
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


class _Fault(Exception):
    """A driver's own exception, with a field a script may read."""

    def __init__(self, detail: str, status: int) -> None:
        super().__init__(detail)
        self.status = status


class _SaysItsOwnText(Exception):
    """A driver's exception that writes its text from its fields."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def __str__(self) -> str:
        return f"rejected with {self.secret}"


class _TakesTwo(Exception):
    """A driver's exception that cannot be built from one sentence."""

    def __new__(cls, code: int, detail: str):
        return super().__new__(cls, code, detail)

    def __init__(self, code: int, detail: str) -> None:
        super().__init__(code, detail)


@pytest.fixture
async def engine(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.13.0",
        "project": {"id": "test", "name": "Test"},
        "devices": [{"id": "acme1", "name": "Lobby Widget", "driver": "acme_widget"}],
        "ui": {"pages": [{"id": "main", "name": "Main", "elements": [
            {"id": "flash_script", "type": "button", "bindings": {"do": {"press": [
                {"action": "script.call", "function": "flash"},
            ]}}},
            {"id": "flash_caught", "type": "button", "bindings": {"do": {"press": [
                {"action": "script.call", "function": "flash_and_catch"},
            ]}}},
        ]}]},
    }))
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "room.py").write_text(textwrap.dedent("""\
        from openavc import devices, state

        async def flash():
            await devices.send("acme1", "flash_led")

        async def flash_and_catch():
            try:
                await devices.send("acme1", "flash_led")
            except TimeoutError as e:
                state.set("var.caught", "timeout: " + str(e))
            except Exception as e:
                state.set("var.caught", type(e).__name__ + ": " + str(e))
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


async def _send(device_id: str = "acme1", command: str = "flash_led",
                params: dict | None = None) -> BaseException:
    with pytest.raises(Exception) as raised:
        await script_api.devices.send(device_id, command, params)
    return raised.value


def _sentence(engine: Engine, failure: Exception, command: str = "flash_led") -> str:
    """What Send Command answers for the same failure."""
    return describe_command_failure(
        failure, "acme1", command, state=engine.state,
        driver=engine.devices.get_driver("acme1"),
    ).programmer


# --- A driver's own exception ------------------------------------------------


@pytest.mark.parametrize("failure", [
    RuntimeError(PRIVATE),
    ValueError(PRIVATE),
    TimeoutError(),
    TimeoutError(60, "Operation timed out"),
    ConnectionError(PRIVATE),
    ConnectionRefusedError(61, PRIVATE),
    httpx.ReadTimeout(PRIVATE),
    CommandRefusedError(""),
    WakeAddressError(),
], ids=lambda failure: type(failure).__name__)
async def test_a_drivers_exception_reaches_the_script_in_send_commands_words(engine, failure):
    _AcmeDriver.failure = failure
    raised = await _send()
    assert type(raised) is type(failure)
    assert str(raised) == _sentence(engine, failure)
    assert PRIVATE not in str(raised)
    assert raised.__cause__ is failure


async def test_a_silent_device_reads_as_one_that_did_not_answer(engine):
    _AcmeDriver.failure = TimeoutError()
    raised = await _send()
    assert isinstance(raised, TimeoutError)
    assert str(raised) == NO_ANSWER


async def test_a_driver_fault_names_the_device_and_the_command(engine):
    raised = await _send()
    assert str(raised) == FOR_THE_PROGRAMMER


@pytest.mark.parametrize("failure", [KeyError(PRIVATE), _SaysItsOwnText(PRIVATE)],
                         ids=lambda failure: type(failure).__name__)
async def test_a_class_that_writes_its_own_text_still_carries_the_sentence(engine, failure):
    """``KeyError`` quotes its argument and a driver's class may build its text
    from its fields; the script still gets the sentence, and an ``except`` for
    the class still catches it."""
    _AcmeDriver.failure = failure
    raised = await _send()
    assert isinstance(raised, type(failure))
    assert type(raised).__name__ == type(failure).__name__
    assert str(raised) == FOR_THE_PROGRAMMER
    assert raised.__cause__ is failure
    try:
        raise raised
    except type(failure) as caught:
        assert caught is raised


async def test_what_the_drivers_exception_carries_is_kept(engine):
    _AcmeDriver.failure = _Fault(PRIVATE, 503)
    raised = await _send()
    assert type(raised) is _Fault
    assert raised.status == 503
    assert str(raised) == FOR_THE_PROGRAMMER


async def test_an_exception_that_cannot_be_rebuilt_is_raised_as_it_was(engine):
    failure = _TakesTwo(7, PRIVATE)
    _AcmeDriver.failure = failure
    raised = await _send()
    assert raised is failure


# --- The platform's own refusals keep their exception ------------------------


async def test_a_refusal_keeps_the_devices_sentence(engine):
    failure = CommandRefusedError(REFUSAL)
    _AcmeDriver.failure = failure
    raised = await _send()
    assert raised is failure
    assert str(raised) == REFUSAL


async def test_an_offline_device_is_the_managers_connection_error(engine):
    engine.state.set("device.acme1.connected", False)
    raised = await _send()
    assert type(raised) is ConnectionError
    assert raised.device_id == "acme1"
    assert str(raised) == "Device 'acme1' is not connected"
    assert raised.__cause__ is None


async def test_a_parameter_refusal_keeps_its_type_and_its_field(engine):
    raised = await _send(command="dim", params={"level": 400})
    assert type(raised) is CommandParamError
    assert raised.__cause__ is None
    assert str(raised) == _sentence(engine, raised, "dim")


async def test_an_unknown_command_and_device_keep_theirs(engine):
    raised = await _send(command="flsh_led")
    assert type(raised) is UnknownCommandError
    assert raised.__cause__ is None
    raised = await _send(device_id="nobody")
    assert type(raised) is DeviceNotFoundError
    assert str(raised) == "Device 'nobody' not found"


# --- What the script, the room and the log see -------------------------------


async def test_a_script_that_catches_it_reads_the_sentence(engine):
    _AcmeDriver.failure = TimeoutError()
    await engine.handle_ui_event("press", "flash_caught")
    assert engine.state.get("var.caught") == f"timeout: {NO_ANSWER}"

    _AcmeDriver.failure = ValueError(PRIVATE)
    await engine.handle_ui_event("press", "flash_caught")
    assert engine.state.get("var.caught") == f"ValueError: {FOR_THE_PROGRAMMER}"


async def test_a_press_that_calls_the_script_reads_as_a_press_on_the_command(engine, caplog):
    """The room gets the sentence a button wired straight to the command gets,
    and the device's log gets the driver's text once, not a second line
    quoting the sentence."""
    with caplog.at_level(logging.INFO):
        pressed = await engine.handle_ui_event("press", "flash_script")
    assert pressed[0]["called"] is False
    assert pressed[0]["error"] == FOR_THE_ROOM
    failed = [r.getMessage() for r in caplog.records if "Command 'flash_led' failed" in r.getMessage()]
    assert failed == [f"[acme1] Command 'flash_led' failed: RuntimeError: {PRIVATE}"]


async def test_the_scripts_view_shows_the_sentence_and_the_traceback_keeps_the_detail(engine):
    errors: list[dict] = []
    engine.events.on("script.error", lambda _e, payload: errors.append(payload))
    await engine.handle_ui_event("press", "flash_script")
    assert len(errors) == 1
    assert errors[0]["error"] == FOR_THE_PROGRAMMER
    assert PRIVATE in errors[0]["traceback"]
    assert errors[0]["traceback"].rstrip().endswith(f"RuntimeError: {FOR_THE_PROGRAMMER}")
