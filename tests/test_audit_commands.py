"""Commands: each of the driver's commands sent through the production door.

Against a loopback fake device that understands a few commands: the command
list as the live driver declares it, one Send and what came back, the
platform's own gates (a parameter out of range never reaches the device), one
command at a time, the status queries run together, a secret parameter kept
out of every record, and the report's commands section. Then the window: the
declared effect checked against what the device reports, a refusal, a command
that sent nothing, a restart timed, "Wait longer", "Stop watching" and "Try
again".
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openavc.api.models import (
    AuditAnswerRequest,
    AuditCommandRequest,
    AuditConnectionRequest,
    AuditDriverRequest,
    AuditStartRequest,
)
from openavc.api.routes import audit as routes
from openavc.audit.commands import (
    DONE,
    changed_values,
    commands_for,
    moved_values,
    same_value,
    trial_sentence,
)
from openavc.audit.driver_choice import DriverChoice
from openavc.audit.listen import DONE as LISTEN_DONE
from openavc.audit.listen import start_listen
from openavc.audit.passes import DriverRun
from openavc.audit.report import build_report, render_summary
from openavc.audit.session import AuditError, AuditOptions, AuditSession, AuditTarget
from openavc.core.device_traffic import RX, get_traffic_recorder
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import get_secret_registry
from tests.test_audit_api import wired  # noqa: F401  (the fixture)

DRIVER = {
    "id": "acme_commands",
    "name": "Acme Commands",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 30},
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "volume": {"type": "integer", "label": "Volume"},
        "input": {"type": "enum", "values": ["hdmi1", "hdmi2"], "label": "Input"},
        "last_error": {"type": "string", "label": "Last error"},
    },
    "commands": {
        "power_on": {"label": "Power On", "send": "PWR 1\\r", "sets": {"power": True}},
        "set_volume": {
            "label": "Set Volume",
            "send": "VOL {level}\\r",
            "params": {"level": {"type": "integer", "min": 0, "max": 100, "required": True}},
            "sets": {"volume": "{level}"},
        },
        "query_power": {"label": "Query Power", "send": "PWR?\\r", "query_for": "power"},
        "query_volume": {"label": "Query Volume", "send": "VOL?\\r"},
        "set_code": {
            "label": "Set Code",
            "send": "CODE {pin}\\r",
            "params": {"pin": {"type": "string", "secret": True, "required": True}},
        },
        "set_psk": {
            "label": "Set PSK",
            "send": "PSK {psk}\\r",
            # Not marked secret: its name says it is a credential.
            "params": {"psk": {"type": "string", "required": True}},
        },
        "set_input": {
            "label": "Set Input",
            "send": "INP {source}\\r",
            "params": {"source": {"type": "enum", "values": ["hdmi1", "hdmi2"], "required": True}},
            "sets": {"input": "{source}"},
        },
        "reboot": {"label": "Reboot", "send": "REBOOT\\r", "restarts_device_for": 5},
        # Drops the link with no restart declared (a unit that resets its
        # control port on a command, or a network blip in the window).
        "blip": {"label": "Blip", "send": "BLIP\\r"},
    },
    "actions": [
        {"id": "reboot", "kind": "command", "confirm": "The device restarts."},
    ],
    "quick_actions": ["power_on", "set_volume", "reboot"],
    "polling": {"queries": ["query_volume"]},
    "responses": [
        {"match": r"PWR=(\w+)", "set": {"power": "$1"}},
        {"match": r"VOL=(\d+)", "set": {"volume": "$1"}},
        {"match": r"INP=(\w+)", "set": {"input": "$1"}},
        {"match": r"ERR", "set": {"last_error": "refused"}},
    ],
}

FAST = {"min_seconds": 0.3, "min_cycles": 1, "max_seconds": 5.0, "flush_seconds": 0.05}
WINDOW = {"window_seconds": 0.3, "query_window_seconds": 0.2, "flush_seconds": 0.05}


@pytest.fixture
def driver():
    _DRIVER_REGISTRY["acme_commands"] = create_configurable_driver_class(DRIVER)
    yield
    _DRIVER_REGISTRY.pop("acme_commands", None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


async def _fake_device(reset_next_connection: bool = False):
    """A device with a power flag and a volume, answering the driver's words.

    ``reset_next_connection``: after a BLIP, close the next connection too,
    once, after its first line (a device still booting resets the reconnect).
    """
    state = {"power": "off", "volume": "20", "reset_next": False}

    async def handle(reader, writer):
        try:
            if state["reset_next"]:
                state["reset_next"] = False
                await reader.readuntil(b"\r")
                writer.close()
                return
            while True:
                line = (await reader.readuntil(b"\r")).decode().strip()
                reply = b""
                if line == "PWR 1":
                    state["power"] = "on"
                    reply = b"PWR=on\r"
                elif line == "PWR?":
                    reply = f"PWR={state['power']}\r".encode()
                elif line.startswith("VOL "):
                    state["volume"] = line[4:]
                    reply = f"VOL={state['volume']}\r".encode()
                elif line == "VOL?":
                    reply = f"VOL={state['volume']}\r".encode()
                elif line.startswith("CODE "):
                    reply = b"ERR\r"
                elif line.startswith("INP "):
                    # This unit has one input: it answers hdmi1 whatever it is asked.
                    reply = b"INP=hdmi1\rLAMP=450\r"
                elif line in ("REBOOT", "BLIP"):
                    state["reset_next"] = reset_next_connection and line == "BLIP"
                    writer.close()
                    return
                if reply:
                    writer.write(reply)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def _until(predicate, timeout: float = 8.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def _connected(port: int):
    session = AuditSession("cmd1", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    heard: list[dict] = []
    session.subscribe(heard.append)
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id="acme_commands", identity={"name": "Acme Commands", "version": "1.0.0"},
    ))
    run.config = {"host": "127.0.0.1", "port": port}
    session.runs.append(run)
    listen = await start_listen(session, run, **FAST)
    await _until(lambda: listen.connected_at is not None)
    # The connect-time poll's answer lands before a test sends anything, so a
    # slow runner cannot count it in the first command's window.
    await _until(lambda: listen.sandbox.device_state().get("volume") is not None)
    return session, run, heard


def test_the_command_list_is_the_drivers_own(driver):
    from openavc.audit.commands import command_catalog

    catalog = {c["name"]: c for c in command_catalog(_DRIVER_REGISTRY["acme_commands"])}
    assert list(catalog) == [
        "power_on", "set_volume", "query_power", "query_volume", "set_code", "set_psk", "set_input",
        "reboot",
        "blip",
    ]
    assert catalog["query_power"]["query"] and catalog["query_power"]["query_for"] == "power"
    # Polled by name: a status query though it declares no query_for.
    assert catalog["query_volume"]["query"] and catalog["query_volume"]["polled"]
    assert not catalog["power_on"]["query"]
    assert catalog["power_on"]["sets"] == {"power": True}
    assert catalog["set_volume"]["needs_input"] and not catalog["power_on"]["needs_input"]
    # The driver's own confirmation, from the action that wraps the command.
    assert catalog["reboot"]["confirm"] == "The device restarts."
    assert catalog["reboot"]["restarts_device_for"] == 5 and catalog["power_on"]["confirm"] == ""
    # Suggested: the commands on a device page, less one that confirms or restarts.
    assert [n for n, c in catalog.items() if c["suggested"]] == ["power_on", "set_volume"]


async def test_a_command_is_sent_through_the_production_door(driver):
    server, port = await _fake_device()
    session, run, heard = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        trial = await commands.send("set_volume", {"level": 40})
        # Sending a command ends the listening window, which promised none.
        assert run.listen.status == LISTEN_DONE
        assert "listen.ended_early" in [e.kind for e in session.timeline]
        with pytest.raises(AuditError, match="Wait for Set Volume"):
            await commands.send("power_on")
        await _until(lambda: trial.status == DONE)

        view = commands.to_dict()["trials"][0]
        assert view["error"] == "" and view["attempt"] == 1 and not view["batch"]
        assert view["traffic"]["sent"] == 1 and view["traffic"]["received"] == 1
        texts = [e["text"] for e in view["traffic"]["entries"]]
        assert texts == ["VOL 40\r", "VOL=40"]  # a reply as framed: no delimiter
        assert run.listen.sandbox.device_state()["volume"] == 40
        kinds = [e.kind for e in session.timeline]
        assert "command.sent" in kinds and "command.done" in kinds
        # Each value is named as its field is: `level` declares no label.
        sent = next(e for e in session.timeline if e.kind == "command.sent")
        assert sent.text == "Sent Set Volume (Level 40)."
        assert any(m["type"] == "audit.commands" for m in heard)
        assert "commands" in session.steps

        # The platform's parameter check refuses before anything is sent.
        refused = await commands.send("set_volume", {"level": 150})
        # Refused at once, and done by the time send answers: the route's copy
        # of the state is never older than what was published.
        assert refused.status == DONE
        assert "at most 100" in refused.error and refused.error_type == "CommandParamError"
        assert refused.attempt == 2
        assert commands.to_dict()["trials"][1]["traffic"]["sent"] == 0
        # Nothing left for the device, so there was no window to watch.
        assert refused.ends_at == refused.returned_at
    finally:
        await run.stop()
        server.close()


async def test_the_status_queries_run_together(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        await commands.run_queries()
        await _until(lambda: commands.batch["status"] == "done")
        assert commands.batch["sent"] == 2 and commands.batch["total"] == 2
        assert [t.command for t in commands.trials] == ["query_power", "query_volume"]
        assert all(t.batch and t.status == DONE for t in commands.trials)
        assert run.listen.sandbox.device_state()["power"] is False
        # A status query's declared variable is read back from its reply.
        assert commands.trials[0].query == {
            "state": "power", "state_key": "power", "label": "Power", "value": False,
            "changed": True, "outcome": "reported",
        }
    finally:
        await run.stop()
        server.close()


@pytest.mark.parametrize("command,param", [("set_code", "pin"), ("set_psk", "psk")])
async def test_a_secret_parameter_stays_out_of_every_record(driver, command, param):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        trial = await commands.send(command, {param: "tulip-7391"})
        await _until(lambda: trial.status == DONE)
        view = commands.to_dict()["trials"][0]
        assert view["params"] == {param: "***"}
        assert "tulip-7391" not in str(view)
        assert all("tulip-7391" not in e.text for e in session.timeline)
    finally:
        await run.stop()
        server.close()
    report = build_report(session)
    assert report["drivers"][0]["commands"]["trials"][0]["command"] == command
    assert "tulip-7391" not in str(report)


async def test_a_reply_before_the_send_is_not_the_commands_on_a_coarse_clock(driver):
    """``time.time()`` ticks every 15.6 ms on Windows before Python 3.13, so a
    reply that landed just before a send can carry the send's own timestamp;
    the window is cut by position, so it stays out."""
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        last = run.listen.sandbox.observer.frames()[-1]
        assert last.direction == RX  # VOL=20, the connect-time poll's answer
        last.t = time.time() + 0.05  # stamped as a coarse clock can
        commands = commands_for(session, run, **WINDOW)
        trial = await commands.send("set_volume", {"level": 40})
        await _until(lambda: trial.status == DONE)
        view = commands.to_dict()["trials"][0]
        assert [e["text"] for e in view["traffic"]["entries"]] == ["VOL 40\r", "VOL=40"]
        assert view["sent_nothing"] is False
    finally:
        await run.stop()
        server.close()


async def test_nothing_is_sent_before_the_driver_connects(driver):
    session = AuditSession("cmd2", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(driver_id="acme_commands"))
    with pytest.raises(AuditError, match="Connect the driver first"):
        await commands_for(session, run).send("power_on")


async def test_stopping_the_driver_ends_the_command_being_watched(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, window_seconds=30.0, flush_seconds=0.05)
        trial = await commands.send("power_on")
        await _until(lambda: trial.returned_at is not None)
    finally:
        await run.stop()
        server.close()
    assert trial.status == DONE and trial.finished_at is not None
    # The command list outlives the driver, for the report.
    assert [c["name"] for c in commands.catalog()][0] == "power_on"
    record = build_report(session)["drivers"][0]["commands"]
    assert record["trials"][0]["traffic"]["entries"][0]["text"] == "PWR 1\r"


def test_the_request_declares_every_field():
    assert set(AuditCommandRequest.model_fields) == {"params", "again"}
    assert set(AuditAnswerRequest.model_fields) == {"trial", "answer", "note"}
    with pytest.raises(ValidationError):
        AuditAnswerRequest(trial=1, answer="maybe")
    with pytest.raises(ValidationError):
        AuditCommandRequest(params={}, retry=True)


async def test_the_routes(wired, driver):  # noqa: F811
    server, port = await _fake_device()
    try:
        started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
        session_id = started["session"]["session_id"]
        await routes.run_network_check(session_id)
        await _until(lambda: wired.manager.current().footprint is not None)
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_commands"))
        with pytest.raises(HTTPException) as exc:
            await routes.send_command(session_id, "power_on", AuditCommandRequest())
        assert exc.value.status_code == 409
        wired.engine.state.set("device.lobby.paused", True)
        await routes.set_session_connection(session_id, AuditConnectionRequest(
            config={"host": "127.0.0.1", "port": port},
        ))
        await routes.connect_and_listen(session_id)
        session = wired.manager.current()
        await _until(lambda: session.runs[0].listen.connected_at is not None)

        result = await routes.send_command(session_id, "power_on", AuditCommandRequest())
        commands = result["session"]["runs"][0]["commands"]
        assert commands["trials"][0]["command"] == "power_on"
        assert commands["current"] == 1
        with pytest.raises(HTTPException) as exc:
            await routes.run_status_queries(session_id)
        assert exc.value.status_code == 409 and "Wait for Power On" in exc.value.detail
        first = session.runs[0].commands.trials[0]
        await _until(lambda: first.status == "watching")
        await routes.wait_longer(session_id)
        assert first.extended == pytest.approx(10.0)
        await routes.stop_watching(session_id)
        await _until(lambda: first.status == DONE)
        assert first.stopped_early
        with pytest.raises(HTTPException) as exc:
            await routes.stop_watching(session_id)
        assert exc.value.status_code == 409

        # Try again names an earlier send of this command, not another's.
        with pytest.raises(HTTPException) as exc:
            await routes.send_command(session_id, "set_volume", AuditCommandRequest(again=1))
        assert exc.value.status_code == 409
        result = await routes.send_command(session_id, "power_on", AuditCommandRequest(again=1))
        assert result["session"]["runs"][0]["commands"]["trials"][1]["attempt"] == 2
        answered = await routes.answer_command(
            session_id, AuditAnswerRequest(trial=1, answer="yes", note="  It came on.  "),
        )
        assert answered["session"]["runs"][0]["commands"]["trials"][0]["answer"]["note"] == "It came on."
        with pytest.raises(HTTPException) as exc:
            await routes.answer_command(session_id, AuditAnswerRequest(trial=9, answer="yes"))
        assert exc.value.status_code == 409
        await routes.end_session(session_id, cancel=True)
        assert all(t.status == DONE for t in session.runs[0].commands.trials)
    finally:
        server.close()
        await wired.manager.shutdown()


async def _sent(commands, name, params=None, *, again=None):
    trial = await (commands.send_again(again) if again else commands.send(name, params))
    await _until(lambda: trial.status == DONE)
    return trial


async def test_the_declared_effect_is_checked_against_what_the_device_reports(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        first = await _sent(commands, "power_on")
        assert first.effects == [{
            "state": "power", "state_key": "power", "label": "Power", "expected": True,
            "has_value": True, "value": True, "outcome": "confirmed",
        }]
        assert first.sent_nothing is False
        # Again: it already was on, so the audit cannot tell it did anything.
        second = await _sent(commands, "power_on")
        assert second.effects[0]["outcome"] == "already"
        assert second.attempt == 2
        assert second.since_previous["command"] == "power_on"
        assert second.since_previous["seconds"] > 0

        # A device that answers something else: it changed, but not to that.
        wrong = await _sent(commands, "set_input", {"source": "hdmi2"})
        assert wrong.effects[0]["outcome"] == "different"
        assert wrong.effects[0]["value"] == "hdmi1"
        assert wrong.refusals["unmatched"] == 1
        assert wrong.refusals["unmatched_examples"] == ["LAMP=450"]
        assert "Input changed to hdmi1, not hdmi2" in session.timeline[-1].text
        # The same again: it is still hdmi1.
        again = await _sent(commands, "set_input", again=wrong.number)
        assert again.params == {"source": "hdmi2"}
        assert again.effects[0]["outcome"] == "unchanged"
    finally:
        await run.stop()
        server.close()
    # The summary says what the timeline says.
    summary = render_summary(build_report(session))
    assert "Power is now Yes, as the driver says it should be." in summary
    assert "4. Set Input (Source hdmi2), again" in summary


def test_a_childs_declared_effect_is_read_from_the_childs_padded_key():
    """The form sends a channel as text ("1"); the channel's state lives under
    its padded id (channel.01). Seen on the bench: an amplifier's Unmute read
    "the device has not reported Channel 1 Mute; Channel 01 Mute went from
    true to false", because the read-back watched channel.1.mute."""
    from openavc.audit.commands import declared_effects
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver

    class AcmeAmp(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_amp", "name": "Acme Amp", "transport": "tcp",
            "child_entity_types": {
                "channel": {
                    "label": "Channel",
                    "id_format": {"type": "integer", "min": 1, "max": 8, "pad_width": 2},
                    "state_variables": {"mute": {"type": "boolean"}},
                },
                "input": {
                    "label": "Input", "id_format": {"type": "string"},
                    "state_variables": {"gain": {"type": "number"}},
                },
            },
            "commands": {},
        }

        async def send_command(self, command, params=None):
            return None

    amp = AcmeAmp("amp1", {}, StateStore(), EventBus())
    mute = {"sets": {"mute": True}, "params": {
        "channel": {"type": "child_id", "child_type": "channel", "required": True},
    }}
    for given in ("1", " 1 ", 1):
        assert [e["state_key"] for e in declared_effects(mute, {"channel": given}, amp)] == [
            "channel.01.mute",
        ]
    gain = {"sets": {"gain": "{level}"}, "params": {
        "name": {"type": "child_id", "child_type": "input", "required": True},
        "level": {"type": "number"},
    }}
    assert declared_effects(gain, {"name": "mic_a", "level": 3}, amp)[0]["state_key"] == (
        "input.mic_a.gain"
    )


async def test_a_refusal_of_the_drivers_own_request_is_not_the_commands(driver):
    """A poll or a follow-up query the driver sends inside a command's window
    and the device refuses: said apart, with what the driver had just sent,
    and never as the command refused. Also when the refusal is the same text
    as before, so the driver's write changes nothing a subscriber hears."""
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    sandbox = run.listen.sandbox
    try:
        commands = commands_for(
            session, run, window_seconds=1.0, query_window_seconds=0.2, flush_seconds=0.05,
        )
        # A command the device refuses: last_error reads "refused".
        refused = await _sent(commands, "set_code", {"pin": "tulip-7391"})
        assert refused.refusals["last_error"] == "refused"

        for _ in range(2):  # a new refusal, then the same text written again
            trial = await commands.send("power_on")
            await _until(lambda: trial.status == "watching")
            # The driver asks something on its own (a poll would) and is refused.
            await sandbox.manager.send_command(sandbox.device_id, "set_code", {"pin": "0000"})
            await _until(lambda: trial.status == DONE)
            assert trial.refusals["device_errors"] == 0
            assert trial.refusals["last_error"] is None
            assert trial.refusals["last_error_writes"] == 0
            [later] = trial.refusals["later"]
            assert later["error"] == "refused" and later["count"] == 1
            assert later["request"] == '"CODE 0000\\r"'
            sentence = trial_sentence(commands._trial_view(trial, every_entry=True))
            assert "the device refused it" not in sentence
            assert "the device refused a request the driver sent on its own" in sentence
            assert sentence.endswith('("CODE 0000\\r"): refused.')
            assert trial.effects[0]["outcome"] in ("confirmed", "already")
    finally:
        await run.stop()
        server.close()


async def test_a_refusal_is_caught(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        refused = await _sent(commands, "set_code", {"pin": "tulip-7391"})
        assert refused.error == ""  # the driver took it; the device did not
        assert refused.refusals["last_error"] == "refused"
        assert refused.refusals["last_error_writes"] == 1
        assert "the device refused it: refused" in session.timeline[-1].text
        # Try again reuses the secret value the browser never had.
        again = await _sent(commands, "set_code", again=refused.number)
        assert again.params == {"pin": "tulip-7391"} and again.to_dict()["params"] == {"pin": "***"}
    finally:
        await run.stop()
        server.close()


async def test_a_command_that_sent_nothing_is_flagged(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        await _until(lambda: run.listen.sandbox.observer.frames())  # the first poll
        live = run.listen.sandbox.driver
        original = live.send_command

        async def quiet(command, params=None):
            if command == "power_on":
                return None  # "handled", and nothing sent
            return await original(command, params)

        live.send_command = quiet
        trial = await _sent(commands, "power_on")
        assert trial.sent_nothing is True
        assert "contract.command_sent_nothing" in [e.kind for e in session.timeline]
        assert "nothing was sent" in trial_sentence(trial.to_dict())
    finally:
        await run.stop()
        server.close()


async def test_a_restart_is_timed_against_the_declared_window(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(
            session, run, **WINDOW, restart_margin_seconds=10.0, after_reconnect_seconds=0.2,
        )
        trial = await commands.send("reboot")
        await _until(lambda: trial.status == DONE, timeout=15.0)
        restart = trial.restart
        assert restart["declared_seconds"] == 5
        assert restart["went_away_after"] is not None
        assert restart["back_after"] is not None and restart["within_declared"] is True
        assert restart["away_for"] <= restart["back_after"]
        assert "came back" in trial_sentence(trial.to_dict())
    finally:
        await run.stop()
        server.close()


async def test_a_drop_inside_the_window_is_said_as_a_drop(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, window_seconds=8.0, flush_seconds=0.05)
        trial = await commands.send("blip")
        await _until(lambda: trial.back_at is not None, timeout=8.0)
        commands.end_now()
        await _until(lambda: trial.status == DONE)
        assert trial.restart is None
        [drop] = trial.drops
        assert drop["after"] >= 0 and drop["back_after"] is not None
        sentence = trial_sentence(trial.to_dict())
        assert "the connection dropped" in sentence and "the driver reconnected" in sentence
        # The reconnect count is the platform's, not a value the command moved.
        view = commands.to_dict()["trials"][-1]
        assert not any("reconnect" in m["key"] for m in view["moved"])
    finally:
        await run.stop()
        server.close()


async def test_every_drop_in_the_window_is_said(driver):
    """A device that resets the reconnect too (one still booting does) drops
    twice in the window; both are recorded and said."""
    server, port = await _fake_device(reset_next_connection=True)
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, window_seconds=15.0, flush_seconds=0.05)
        trial = await commands.send("blip")
        await _until(
            lambda: len(trial.gone_and_back) == 2 and trial.gone_and_back[1][1] is not None,
            timeout=15.0,
        )
        commands.end_now()
        await _until(lambda: trial.status == DONE)
        first, second = trial.drops
        assert second["after"] >= first["after"] + first["back_after"]
        assert second["back_after"] is not None
        sentence = trial_sentence(trial.to_dict())
        assert "the connection dropped" in sentence and "it dropped again" in sentence
    finally:
        await run.stop()
        server.close()


def test_the_drops_sentence():
    from openavc.audit.commands import _drops_text

    assert _drops_text([{"after": 41.9, "back_after": 8.3}]) == (
        "the connection dropped 41.9 s after it was sent and the driver reconnected 8.3 s later"
    )
    assert _drops_text([
        {"after": 41.9, "back_after": 8.3}, {"after": 54.9, "back_after": None},
    ]) == (
        "the connection dropped 41.9 s after it was sent and the driver reconnected 8.3 s later; "
        "it dropped again 4.7 s after that and had not come back when the audit stopped watching"
    )


def test_a_value_that_ends_where_it_began_is_not_what_the_command_changed():
    changes = [
        {"t": 1, "key": "online", "old": True, "new": False},
        {"t": 2, "key": "online", "old": False, "new": True},
        {"t": 2, "key": "volume", "old": 20, "new": 40},
        {"t": 3, "key": "reconnect_attempt", "old": None, "new": 1},
    ]
    moved = moved_values(changes, [], {})
    assert [(m["key"], m["went_back"]) for m in moved] == [("online", True), ("volume", False)]
    sentence = trial_sentence({"moved": moved, "traffic": {"received": 1}})
    assert "went from 20 to 40" in sentence and "online" not in sentence.lower()
    only_back = trial_sentence({"moved": moved[:1], "traffic": {"received": 0}})
    assert only_back.startswith("values changed and went back to where they were")


async def test_wait_longer_and_stop_watching(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, window_seconds=30.0, max_window_seconds=40.0,
                                flush_seconds=0.05)
        with pytest.raises(AuditError, match="No command is being watched"):
            commands.extend()
        trial = await commands.send("power_on")
        await _until(lambda: trial.status == "watching")
        before = trial.ends_at
        commands.extend()
        assert trial.ends_at == pytest.approx(before + 10.0)
        assert trial.extended == pytest.approx(10.0)
        # 30 seconds and one more wait reach the 40-second ceiling.
        with pytest.raises(AuditError, match="for 40 seconds at most"):
            commands.extend()
        commands.end_now()
        await _until(lambda: trial.status == DONE)
        assert trial.stopped_early
        assert trial.effects[0]["outcome"] == "confirmed"
    finally:
        await run.stop()
        server.close()


METER_DRIVER = {
    "id": "acme_meter",
    "name": "Acme Meter",
    "manufacturer": "Acme",
    "category": "audio",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 30},
    "state_variables": {
        "mute": {"type": "boolean", "label": "Mute"},
        "level": {"type": "integer", "label": "Level"},
    },
    "commands": {"mute_on": {"label": "Mute", "send": "MUTE 1\\r"}},
    "responses": [
        {"match": r"MUTE=(\d)", "set": {"mute": "$1"}},
        {"match": r"LVL=(\d+)", "set": {"level": "$1"}},
    ],
}


async def _metering_device():
    """Pushes a level that never sits still, and mutes when told."""

    async def handle(reader, writer):
        async def meter():
            n = 0
            while True:
                n += 1
                writer.write(f"LVL={n}\r".encode())
                await writer.drain()
                await asyncio.sleep(0.05)

        task = asyncio.create_task(meter())
        writer.write(b"MUTE=0\r")
        try:
            while True:
                line = (await reader.readuntil(b"\r")).decode().strip()
                if line == "MUTE 1":
                    writer.write(b"MUTE=1\r")
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            task.cancel()
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_what_a_command_moved_is_told_from_what_moves_anyway():
    """A level meter moves before, during and after a Mute: the command's own
    change is what its sentence and "what changed" name, and the meter is
    shown apart, as changing without the audit."""
    from openavc.audit.commands import moved_values, state_label

    _DRIVER_REGISTRY["acme_meter"] = create_configurable_driver_class(METER_DRIVER)
    server, port = await _metering_device()
    session = AuditSession("cmd9", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id="acme_meter", identity={"name": "Acme Meter", "version": "1.0.0"},
    ))
    run.config = {"host": "127.0.0.1", "port": port}
    session.runs.append(run)
    try:
        listen = await start_listen(session, run, **FAST)
        # The meter moves while nothing is watched.
        await _until(lambda: len(run.unwatched.get("level") or ()) >= 3)
        commands = commands_for(session, run, **WINDOW)
        trial = await commands.send("mute_on")
        await _until(lambda: trial.status == DONE)
        assert trial.already_moving == ["level"]
        view = commands.to_dict()["trials"][0]
        moved = {m["key"]: m for m in view["moved"]}
        assert moved["mute"] == {
            "key": "mute", "label": "Mute", "first": False, "last": True, "times": 1,
            "already_moving": False, "went_back": False,
        }
        assert moved["level"]["already_moving"] and moved["level"]["times"] > 1
        assert view["summary"] == "Mute went from No to Yes."
        # After the window the meter keeps going: it changed without the audit.
        await _until(lambda: any(
            c["key"] == "level" and c["on_its_own"] for c in changed_values(run)
        ))
        mute = next(c for c in changed_values(run) if c["key"] == "mute")
        assert not mute["on_its_own"] and mute["by"]["label"] == "Mute"
        assert listen.sandbox.started
    finally:
        await run.stop()
        server.close()
        _DRIVER_REGISTRY.pop("acme_meter", None)
        get_traffic_recorder().clear()

    # Only a command whose window saw nothing but the meter says so.
    assert trial_sentence({"moved": [{"key": "level", "already_moving": True}],
                           "traffic": {"received": 4}}) == (
        "only values that were already changing moved; the device sent 4 replies."
    )
    assert trial_sentence({"moved": [], "traffic": {"received": 0}}) == (
        "no status value changed, and nothing came back."
    )
    # A child's value is named with its type and id.
    zoned = {"child_entity_types": {"zone": {
        "label": "Zone", "state_variables": {"gain": {"type": "integer", "label": "Gain"}},
    }}}
    assert state_label(zoned, "zone.2.gain") == "Zone 2 Gain"
    assert state_label(zoned, "zone.2.trim") == "Zone 2 trim"
    assert state_label(METER_DRIVER, "level") == "Level"
    assert state_label(METER_DRIVER, "custom_key") == "custom_key"
    # The platform's own keys are not what a command moved.
    assert moved_values(
        [{"key": "connected", "old": True, "new": False}], [], METER_DRIVER,
    ) == []


def test_a_value_already_changing_is_not_left_changed_by_the_command():
    """A clock that ticked inside a command's window, and was already ticking
    when the command went out, is not what the command left changed, even
    before it has ticked again with nothing watched. Seen on the bench: an
    amplifier's uptime listed as changed by Set Output Level."""
    from collections import deque
    from types import SimpleNamespace as NS

    trial = NS(
        number=3, label="Set Output Level", sent_at=100.0,
        before={"level": 0.0, "uptime": "1 min"},
        changes=[{"key": "level"}, {"key": "uptime"}], already_moving=["uptime"],
    )
    listen = NS(sandbox=NS(
        started=True, driver=NS(DRIVER_INFO={}),
        device_state=lambda: {"level": -10.0, "uptime": "2 min"},
    ))
    run = NS(
        commands=NS(trials=[trial]), settings=None, listen=listen,
        unwatched={"uptime": deque([40.0])}, choice=NS(driver_id="acme"),
    )
    changed = {c["key"]: c for c in changed_values(run)}
    assert not changed["level"]["on_its_own"]
    assert changed["uptime"]["on_its_own"]


def test_a_value_seen_changing_on_its_own_in_any_window_is_not_left_changed():
    """A meter already moving when the first Mute went out, that moved again
    in the last Mute's window and not since: it still changes without the
    audit. Seen on the bench: an amplifier's other channel's output voltage
    listed as left changed by the last Mute, to be set back by hand."""
    from types import SimpleNamespace as NS

    first = NS(
        number=1, label="Mute Channel", sent_at=100.0, before={"volts": 0.019},
        changes=[{"key": "volts"}], already_moving=["volts"],
    )
    last = NS(
        number=5, label="Mute Channel", sent_at=200.0, before={"volts": 0.0190},
        changes=[{"key": "volts"}], already_moving=[],
    )
    listen = NS(sandbox=NS(
        started=True, driver=NS(DRIVER_INFO={}), device_state=lambda: {"volts": 0.0184},
    ))
    run = NS(
        commands=NS(trials=[first, last]), settings=None, listen=listen,
        unwatched={}, choice=NS(driver_id="acme"),
    )
    (volts,) = changed_values(run)
    assert volts["on_its_own"]
    assert volts["by"]["number"] == 5


def test_a_value_the_device_spells_its_own_way_still_counts():
    assert same_value(True, "on") and same_value(True, 1) and not same_value(True, "off")
    assert same_value(40, "40") and same_value("hdmi1", "HDMI1")
    assert not same_value(40, None) and not same_value(True, "standby")


async def test_the_person_says_what_the_device_did(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        with pytest.raises(AuditError, match="no command number 1"):
            commands.answer(1, "yes")
        trial = await _sent(commands, "set_input", {"source": "hdmi2"})
        commands.answer(trial.number, "no", "The screen stayed on HDMI 1.")
        assert trial.answer["answer"] == "no" and trial.answer["note"] == "The screen stayed on HDMI 1."
        assert session.timeline[-1].text == (
            "Set Input: The person said the device did not do it. Note: The screen stayed on HDMI 1."
        )
        # Answering again replaces it; the timeline keeps both.
        commands.answer(trial.number, "partly")
        assert trial.answer["answer"] == "partly" and trial.answer["note"] == ""
        assert commands.to_dict()["trials"][0]["answer"]["answer"] == "partly"
    finally:
        await run.stop()
        server.close()
    summary = render_summary(build_report(session))
    assert "The person said the device partly did it." in summary


class _AcmeZones:
    """A driver with zones and a preset list the device publishes: what a
    command's child picker and state-fed picker read."""

    @staticmethod
    def cls():
        from openavc.drivers.base import BaseDriver

        class AcmeZones(BaseDriver):
            DRIVER_INFO = {
                "id": "acme_zones", "name": "Acme Zones", "transport": "tcp",
                "state_variables": {"presets": {"type": "string"}},
                "child_entity_types": {
                    "zone": {
                        "label": "Zone", "id_format": {"type": "integer", "min": 1, "max": 8},
                        "state_variables": {"name": {"type": "string"}},
                        "label_field": "name",
                    },
                },
                "commands": {
                    "set_zone_volume": {"label": "Set Zone Volume", "params": {
                        "zone": {"type": "child_id", "child_type": "zone", "required": True},
                    }},
                    "recall_preset": {"label": "Recall Preset", "params": {
                        "preset": {"type": "string", "options_state": "presets"},
                    }},
                },
            }

            async def _create_transport(self, transport_type):
                pass  # its own session: nothing to dial

            def _link_alive(self):
                return True

            async def _initial_sync(self):
                self.register_child("zone", 2, initial_state={"name": "Lobby"})
                self.set_state("presets", '["Morning", "Evening"]')

            async def send_command(self, command, params=None):
                return None

        return AcmeZones


async def test_the_pickers_read_the_audited_driver(wired):  # noqa: F811
    _DRIVER_REGISTRY["acme_zones"] = _AcmeZones.cls()
    try:
        started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
        session_id = started["session"]["session_id"]
        await routes.run_network_check(session_id)
        await _until(lambda: wired.manager.current().footprint is not None)
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_zones"))
        wired.engine.state.set("device.lobby.paused", True)
        await routes.set_session_connection(session_id, AuditConnectionRequest(
            config={"host": "127.0.0.1", "port": 9},
        ))
        await routes.connect_and_listen(session_id)
        session = wired.manager.current()
        await _until(lambda: session.runs[0].listen.connected_at is not None)
        # Wait for the value, not the key: every declared variable exists from
        # construction, holding None, and connected_at is set before
        # _initial_sync runs. The preset list is written after the zone is
        # registered, so once it holds a value both pickers have something.
        await _until(
            lambda: session.runs[0].listen.sandbox.device_state().get("presets") is not None
        )

        listed = await routes.list_audit_children(session_id, "zone")
        assert [(c["local_id"], c["display_name"]) for c in listed["children"]] == [(2, "Lobby")]
        with pytest.raises(HTTPException) as exc:
            await routes.list_audit_children(session_id, "speaker")
        assert exc.value.status_code == 404
        commands = session.runs[0].commands.to_dict()
        assert commands["picker_state"] == {"presets": '["Morning", "Evening"]'}
        await routes.end_session(session_id, cancel=True)
    finally:
        _DRIVER_REGISTRY.pop("acme_zones", None)
        await wired.manager.shutdown()


async def test_what_changed_lists_each_value_with_what_it_was(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        assert changed_values(run) == []  # nothing sent, nothing to compare
        await _until(lambda: run.listen.sandbox.device_state().get("volume") == 20)
        await _sent(commands, "power_on")
        await _sent(commands, "set_input", {"source": "hdmi2"})
        changed = changed_values(run)
        assert changed == [
            {"key": "input", "label": "Input", "before": None, "now": "hdmi1",
             "by": {"number": 2, "label": "Set Input"}, "on_its_own": False},
            {"key": "power", "label": "Power", "before": None, "now": True,
             "by": {"number": 1, "label": "Power On"}, "on_its_own": False},
        ]
        # A value that moved while nothing was watched still counts, by nobody,
        # and it changed without the audit.
        run.listen.sandbox.state.set(f"device.{run.listen.sandbox.device_id}.volume", 35)
        assert {
            "key": "volume", "label": "Volume", "before": 20, "now": 35, "by": None,
            "on_its_own": True,
        } in changed_values(run)
        assert commands.to_dict()["changed"] == changed_values(run)
    finally:
        await run.stop()
        server.close()
    # Kept as it stood when the driver stopped, for the report and its summary.
    record = build_report(session)["drivers"][0]["commands"]
    assert [c["key"] for c in record["changed"]] == ["input", "power", "volume"]
    summary = render_summary(build_report(session))
    assert "What the audit changed" in summary
    assert "not reported before, Yes now (after 1. Power On)" in summary
    assert "Also different now, but changing without the audit: Volume" in summary


async def test_a_later_refusal_on_the_same_clock_tick_is_still_not_the_commands(driver, monkeypatch):
    """Windows before Python 3.13 ticks time.time() every 15.6 ms, so the
    driver's next request, its refusal and the command's return can share one
    timestamp. The command's own exchange is cut by position, not by clock."""
    import openavc.audit.commands as commands_mod
    import openavc.audit.sandbox as sandbox_mod
    import openavc.core.device_traffic as traffic_mod
    from types import SimpleNamespace

    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    sandbox = run.listen.sandbox
    real = time.time
    frozen = {"at": None}

    def clock() -> float:
        return frozen["at"] if frozen["at"] is not None else real()

    fake = SimpleNamespace(time=clock, monotonic=time.monotonic, sleep=time.sleep)
    for mod in (commands_mod, sandbox_mod, traffic_mod):
        monkeypatch.setattr(mod, "time", fake)
    try:
        commands = commands_for(
            session, run, window_seconds=1.0, query_window_seconds=0.2, flush_seconds=0.05,
        )
        frozen["at"] = real()  # one tick from here to the refusal
        trial = await commands.send("power_on")
        await _until(lambda: trial.status == "watching")
        await sandbox.manager.send_command(sandbox.device_id, "set_code", {"pin": "0000"})
        await _until(lambda: any(w.get("error") for w in trial.error_writes) or bool(
            [1 for w in sandbox.state.error_writes if w[2]]))
        frozen["at"] = None  # the clock moves on; the window closes
        await _until(lambda: trial.status == DONE)
        assert trial.refusals["last_error"] is None
        assert [later["error"] for later in trial.refusals["later"]] == ["refused"]
    finally:
        await run.stop()
        server.close()
