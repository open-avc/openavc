"""The Device Audit reads a device setting's value the way the platform
confirms a written one.

A device may report a setting's value in its own form: an option's label
("HDMI 1") for its value ("1"), "on" for true, "40" for 40. The platform
confirms a write by reading the report through the setting's declaration
(``core/pending_settings.readback_confirms``). Every place the audit asks
whether a variable a device setting reads back through holds a value asks
the same rule: the settings step (the value it already has, a write the
device never reported, the fresh read before a write), a command's declared
effect on that variable, and whether that variable changed. A variable no
setting reads back through keeps the audit's own reading.

Against an invented device whose input reads back by its label, and whose
status query can answer by the label or the value.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import pytest

from openavc.audit.changes import changed_values, moved_values
from openavc.audit.commands import CommandPass, CommandTrial
from openavc.audit.driver_choice import DriverChoice
from openavc.audit.effects import Readout, judged_effects
from openavc.audit.listen import start_listen
from openavc.audit.passes import DriverRun
from openavc.audit.session import AuditError, AuditOptions, AuditSession, AuditTarget
from openavc.audit.settings import DONE, settings_for
from openavc.core.device_traffic import get_traffic_recorder
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import get_secret_registry

LABELS = {"1": "HDMI 1", "2": "HDMI 2"}

INPUT_SETTING = {
    "type": "enum", "label": "Input", "state_key": "input",
    "values": [{"value": "1", "label": "HDMI 1"}, {"value": "2", "label": "HDMI 2"}],
    "write": {"send": "INPUT {value}\\r"},
}

DRIVER = {
    "id": "acme_inputs",
    "name": "Acme Inputs",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 60},
    "state_variables": {"input": {"type": "string", "label": "Input"}},
    "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
    "device_settings": {"input": INPUT_SETTING},
    "polling": {"queries": ["INPUT?\\r"]},
    "responses": [{"match": r"INPUT=(.+)", "set": {"input": "$1"}}],
}

# The same device with a status query for its input, which the settings step
# asks before it writes.
QUERIED = {
    **DRIVER,
    "id": "acme_inputs_queried",
    "commands": {
        **DRIVER["commands"],
        "read_input": {"label": "Read input", "send": "INPUTN?\\r", "query_for": "input"},
    },
}


@pytest.fixture
def drivers():
    for definition in (DRIVER, QUERIED):
        _DRIVER_REGISTRY[definition["id"]] = create_configurable_driver_class(definition)
    yield
    for definition in (DRIVER, QUERIED):
        _DRIVER_REGISTRY.pop(definition["id"], None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


async def _fake_device(device: dict):
    """Reports its input by its label when polled; its status query answers
    in ``device["query_form"]`` ("label" or "value"). With ``keep`` set it
    takes no write and says nothing to one. Every line it hears goes in
    ``device["heard"]``."""
    device.setdefault("input", "1")
    device.setdefault("heard", [])

    def said(form: str) -> bytes:
        value = device["input"]
        return f"INPUT={LABELS[value] if form == 'label' else value}\r".encode()

    async def handle(reader, writer):
        try:
            while True:
                line = (await reader.readuntil(b"\r")).decode().strip()
                device["heard"].append(line)
                if line == "INPUT?":
                    writer.write(said("label"))
                elif line == "INPUTN?":
                    writer.write(said(device.get("query_form", "label")))
                elif line.startswith("INPUT ") and not device.get("keep"):
                    device["input"] = line[6:]
                    writer.write(said("label"))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _until(predicate, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def _connected(port: int, driver_id: str = "acme_inputs"):
    session = AuditSession("inputs", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id=driver_id, identity={"name": "Acme Inputs", "version": "1.0.0"},
    ))
    run.config = {"host": "127.0.0.1", "port": port}
    session.runs.append(run)
    listen = await start_listen(
        session, run, min_seconds=0.3, min_cycles=1, max_seconds=5.0, flush_seconds=0.05,
    )
    await _until(lambda: listen.sandbox.device_state().get("input") is not None)
    return session, run


# -- the settings step --------------------------------------------------------------


async def test_the_value_a_setting_has_is_never_written_when_the_device_says_its_label(drivers):
    server, port = await _fake_device({})
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        assert run.listen.sandbox.device_state()["input"] == "HDMI 1"
        with pytest.raises(AuditError, match="Input is HDMI 1 already"):
            await settings.write("input", "1")
        assert settings.trials == []
    finally:
        await run.stop()
        server.close()


async def test_a_put_back_the_device_never_answered_says_it_reported_nothing_new(drivers):
    """The device takes neither write: the original is still showing as
    the put-back goes out, by its label, so the read-back proves nothing."""
    device = {"keep": True}
    server, port = await _fake_device(device)
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        run.listen.sandbox.manager._confirm_window = lambda _driver: 0.6
        trial = await settings.write("input", "2")
        await _until(lambda: trial.status == DONE)
        assert not trial.write["confirmed"]
        assert trial.restore["confirmed"] and trial.restore["unchanged"]
        assert trial.to_dict()["summary"].endswith(
            "Put Input back to HDMI 1: the device reported nothing new, so OpenAVC still "
            "shows HDMI 1 from before the write."
        )
    finally:
        await run.stop()
        server.close()


async def test_a_fresh_read_of_the_same_value_in_another_form_keeps_the_original(drivers):
    device = {"query_form": "value"}
    server, port = await _fake_device(device)
    session, run = await _connected(port, "acme_inputs_queried")
    try:
        settings = settings_for(session, run)
        trial = await settings.write("input", "2")
        await _until(lambda: trial.status == DONE)
        assert "INPUTN?" in device["heard"]
        # "1" is HDMI 1: nothing moved at the device, so what was shown stands.
        assert trial.original == "HDMI 1"
        assert trial.write["confirmed"] and trial.restore["confirmed"]
        assert device["input"] == "1"
    finally:
        await run.stop()
        server.close()


async def test_a_value_changed_at_the_device_to_the_one_asked_for_is_not_written(drivers):
    device: dict = {}
    server, port = await _fake_device(device)
    session, run = await _connected(port, "acme_inputs_queried")
    try:
        settings = settings_for(session, run)
        device["input"] = "2"  # at the device, since the last poll
        trial = await settings.write("input", "2")
        await _until(lambda: trial.status == DONE)
        assert trial.original == "HDMI 2"
        assert trial.write["error"] == (
            "Input is HDMI 2 already. Choose a different value to test writing it."
        )
        assert trial.write["sent"] is False and trial.restore is None
        assert not any(line.startswith("INPUT ") for line in device["heard"])
    finally:
        await run.stop()
        server.close()


# -- a command's declared effect --------------------------------------------------------

INFO = {
    "state_variables": {
        "input": {"type": "string", "label": "Input"},
        "source": {"type": "string", "label": "Source"},
    },
    "device_settings": {"input": INPUT_SETTING},
    "commands": {
        "set_input": {
            "label": "Set Input", "sets": {"input": "{input}"},
            "params": {"input": {"type": "enum", "values": ["1", "2"]}},
        },
        "set_source": {
            "label": "Set Source", "sets": {"source": "{source}"},
            "params": {"source": {"type": "string"}},
        },
        "input_one_by_name": {"label": "Input One By Name", "sets": {"input": "HDMI 1"}},
        "input_three": {"label": "Input Three", "sets": {"input": "3"}},
    },
}


def _trial(command: str, params: dict, before: dict) -> CommandTrial:
    entry = INFO["commands"][command]
    return CommandTrial(
        number=1, command=command, label=entry["label"], params=params, attempt=1,
        batch=False, connect_attempt=0, sent_at=100.0, returned_at=100.5,
        declared={"sets": entry["sets"]}, before=before, changes=[],
    )


def _outcome(command: str, params: dict, now: dict, before: dict, changed=()) -> str:
    trial = _trial(command, params, before)
    readout = Readout(state=now, changed=set(changed), driver=NS(DRIVER_INFO=INFO, config={}))
    (effect,) = judged_effects(INFO["commands"][command], trial, readout)
    return effect["outcome"]


def test_a_commands_effect_on_a_settings_variable_reads_through_the_setting():
    assert _outcome("set_input", {"input": "2"}, {"input": "HDMI 2"}, {"input": "HDMI 1"},
                    ("input",)) == "confirmed"
    assert _outcome("set_input", {"input": "1"}, {"input": "HDMI 1"}, {"input": "HDMI 1"}) == (
        "already"
    )
    # A value given by its label is the setting's value too.
    assert _outcome("input_one_by_name", {}, {"input": "1"}, {"input": "2"}, ("input",)) == (
        "confirmed"
    )
    assert _outcome("set_input", {"input": "2"}, {"input": "HDMI 1"}, {"input": "HDMI 1"}) == (
        "unchanged"
    )


def test_an_effect_the_setting_cannot_read_keeps_the_audits_own_reading():
    # No setting reads back through it.
    assert _outcome("set_source", {"source": "hdmi"}, {"source": "HDMI"}, {}, ("source",)) == (
        "confirmed"
    )
    assert _outcome("set_source", {"source": "1"}, {"source": "HDMI 1"}, {}, ("source",)) == (
        "different"
    )
    # A value the setting refuses: read as the audit reads any other.
    assert _outcome("input_three", {}, {"input": " 3 "}, {}, ("input",)) == "confirmed"


def _commands(state: dict, written: set[str]) -> CommandPass:
    sandbox = NS(
        started=True, device_state=lambda: dict(state), driver=NS(DRIVER_INFO=INFO, config={}),
        written_after=lambda _seq: set(written),
    )
    attempt = NS(sandbox=sandbox, status_table=lambda: {})
    run = NS(listens=[attempt], listen=attempt, index=0, settings=None,
             choice=NS(driver_id="acme", identity={}))
    return CommandPass(NS(), run)


def test_a_commands_read_back_holds_once_the_device_shows_the_settings_value():
    trial = _trial("set_input", {"input": "2"}, {"input": "HDMI 1"})
    trial.returned_seq = 0
    commands = _commands({"input": "HDMI 2"}, set())
    assert commands._read_back(trial, commands.run.listen.sandbox)
    waiting = _commands({"input": "HDMI 1"}, set())
    assert not waiting._read_back(trial, waiting.run.listen.sandbox)


# -- what changed -----------------------------------------------------------------------


def _run(now: dict, before: dict):
    listen = NS(
        sandbox=NS(started=True, driver=NS(DRIVER_INFO=INFO), device_state=lambda: dict(now)),
        status_table=lambda: {},
    )
    sent = NS(number=1, label="Set Input", sent_at=100.0, before=before, changes=[],
              already_moving=[])
    return NS(commands=NS(trials=[sent]), settings=None, listen=listen, unwatched={},
              choice=NS(driver_id="acme"))


def test_a_settings_value_said_another_way_has_not_changed():
    assert changed_values(_run({"input": "1"}, {"input": "HDMI 1"})) == []
    assert [c["key"] for c in changed_values(_run({"input": "2"}, {"input": "HDMI 1"}))] == [
        "input",
    ]
    moved = moved_values(
        [{"key": "input", "old": "HDMI 1", "new": "HDMI 2"},
         {"key": "input", "old": "HDMI 2", "new": "1"}], [], INFO,
    )
    assert moved[0]["went_back"] is True


def test_another_variable_changes_by_value_and_type():
    assert [c["key"] for c in changed_values(_run({"source": "hdmi"}, {"source": "HDMI"}))] == [
        "source",
    ]
    moved = moved_values([{"key": "source", "old": "1", "new": 1}], [], INFO)
    assert moved[0]["went_back"] is False
