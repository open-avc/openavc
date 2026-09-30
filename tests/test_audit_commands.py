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
        "set_input": {
            "label": "Set Input",
            "send": "INP {source}\\r",
            "params": {"source": {"type": "enum", "values": ["hdmi1", "hdmi2"], "required": True}},
            "sets": {"input": "{source}"},
        },
        "reboot": {"label": "Reboot", "send": "REBOOT\\r", "restarts_device_for": 5},
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


async def _fake_device():
    """A device with a power flag and a volume, answering the driver's words."""
    state = {"power": "off", "volume": "20"}

    async def handle(reader, writer):
        try:
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
                elif line == "REBOOT":
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
        "power_on", "set_volume", "query_power", "query_volume", "set_code", "set_input", "reboot",
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
        assert any(m["type"] == "audit.commands" for m in heard)
        assert "commands" in session.steps

        # The platform's parameter check refuses before anything is sent.
        refused = await commands.send("set_volume", {"level": 150})
        await _until(lambda: refused.status == DONE)
        assert "at most 100" in refused.error and refused.error_type == "CommandParamError"
        assert refused.attempt == 2
        assert commands.to_dict()["trials"][1]["traffic"]["sent"] == 0
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
            "state": "power", "state_key": "power", "value": False, "changed": True,
            "outcome": "reported",
        }
    finally:
        await run.stop()
        server.close()


async def test_a_secret_parameter_stays_out_of_every_record(driver):
    server, port = await _fake_device()
    session, run, _ = await _connected(port)
    try:
        commands = commands_for(session, run, **WINDOW)
        trial = await commands.send("set_code", {"pin": "tulip-7391"})
        await _until(lambda: trial.status == DONE)
        view = commands.to_dict()["trials"][0]
        assert view["params"] == {"pin": "***"}
        assert "tulip-7391" not in str(view)
        assert all("tulip-7391" not in e.text for e in session.timeline)
    finally:
        await run.stop()
        server.close()
    report = build_report(session)
    assert report["drivers"][0]["commands"]["trials"][0]["command"] == "set_code"
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
            "state": "power", "state_key": "power", "expected": True, "has_value": True,
            "value": True, "outcome": "confirmed",
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
        assert "input changed to hdmi1, not hdmi2" in session.timeline[-1].text
        # The same again: it is still hdmi1.
        again = await _sent(commands, "set_input", again=wrong.number)
        assert again.params == {"source": "hdmi2"}
        assert again.effects[0]["outcome"] == "unchanged"
    finally:
        await run.stop()
        server.close()
    # The summary says what the timeline says.
    summary = render_summary(build_report(session))
    assert "Power is now true, as the driver says it should be." in summary
    assert "4. Set Input (source hdmi2), again" in summary


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
        await _until(lambda: "presets" in session.runs[0].listen.sandbox.device_state())

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
             "by": {"number": 2, "label": "Set Input"}},
            {"key": "power", "label": "Power", "before": None, "now": True,
             "by": {"number": 1, "label": "Power On"}},
        ]
        # A value that moved while nothing was watched still counts, by nobody.
        run.listen.sandbox.state.set(f"device.{run.listen.sandbox.device_id}.volume", 35)
        assert {"key": "volume", "label": "Volume", "before": 20, "now": 35, "by": None} in (
            changed_values(run)
        )
        assert commands.to_dict()["changed"] == changed_values(run)
    finally:
        await run.stop()
        server.close()
    # Kept as it stood when the driver stopped, for the report and its summary.
    record = build_report(session)["drivers"][0]["commands"]
    assert [c["key"] for c in record["changed"]] == ["input", "power", "volume"]
    summary = render_summary(build_report(session))
    assert "What the audit changed" in summary
    assert "not reported before, true now (after 1. Power On)" in summary
