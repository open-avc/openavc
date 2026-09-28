"""Device settings: written the production way, read back, and put back.

Against a loopback fake device with two settings: a name it echoes back, and a
mode it silently keeps as it was. A write goes through the manager's own
setting gate and is confirmed by its read-back rule; the old value goes back
the same way. A setting the audit could not put back is never written, nor is
the value a setting already has; one left changed goes back when the driver
stops; and a setting and a command never run at once.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openavc.api.models import (
    AuditConnectionRequest,
    AuditDriverRequest,
    AuditSettingRequest,
    AuditStartRequest,
)
from openavc.api.routes import audit as routes
from openavc.audit.commands import commands_for
from openavc.audit.driver_choice import DriverChoice
from openavc.audit.listen import start_listen
from openavc.audit.passes import DriverRun
from openavc.audit.report import build_report, render_summary
from openavc.audit.session import AuditError, AuditOptions, AuditSession, AuditTarget
from openavc.audit.settings import DONE, settings_for
from openavc.core.device_traffic import get_traffic_recorder
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import get_secret_registry
from tests.test_audit_api import wired  # noqa: F401  (the fixture)

DRIVER = {
    "id": "acme_settings",
    "name": "Acme Settings",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 0.2},
    "state_variables": {
        "device_name": {"type": "string", "label": "Name"},
        "mode": {"type": "enum", "values": ["auto", "manual"], "label": "Mode"},
    },
    "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
    "device_settings": {
        "device_name": {
            "type": "string", "label": "Device name", "state_key": "device_name",
            "write": {"send": "NAME {value}\\r"},
        },
        "mode": {
            "type": "enum", "values": ["auto", "manual"], "label": "Mode", "state_key": "mode",
            "write": {"send": "MODE {value}\\r"},
        },
        "hidden": {"type": "string", "label": "Hidden", "write": {"send": "HIDE {value}\\r"}},
    },
    "polling": {"queries": ["NAME?\\r", "MODE?\\r"]},
    "responses": [
        {"match": r"NAME=(.+)", "set": {"device_name": "$1"}},
        {"match": r"MODE=(\w+)", "set": {"mode": "$1"}},
    ],
}

FAST = {"min_seconds": 0.3, "min_cycles": 1, "max_seconds": 5.0, "flush_seconds": 0.05}


@pytest.fixture
def driver():
    _DRIVER_REGISTRY["acme_settings"] = create_configurable_driver_class(DRIVER)
    yield
    _DRIVER_REGISTRY.pop("acme_settings", None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


async def _fake_device():
    """Keeps a name it will change and a mode it will not."""
    state = {"name": "Lobby", "mode": "auto"}

    async def handle(reader, writer):
        try:
            while True:
                line = (await reader.readuntil(b"\r")).decode().strip()
                if line.startswith("NAME "):
                    state["name"] = line[5:]
                if line.startswith(("NAME", "MODE")):
                    writer.write(f"NAME={state['name']}\rMODE={state['mode']}\r".encode())
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


async def _connected(port: int):
    session = AuditSession("set1", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id="acme_settings", identity={"name": "Acme Settings", "version": "1.0.0"},
    ))
    run.config = {"host": "127.0.0.1", "port": port}
    session.runs.append(run)
    listen = await start_listen(session, run, **FAST)
    await _until(lambda: listen.sandbox.device_state().get("mode") is not None)
    return session, run


async def test_a_setting_is_written_read_back_and_put_back(driver):
    server, port = await _fake_device()
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        catalog = {s["key"]: s for s in settings.catalog()}
        assert catalog["device_name"]["value"] == "Lobby" and catalog["device_name"]["can_write"]
        # No status value to read it by, so nothing to put it back to.
        assert not catalog["hidden"]["can_write"]
        assert "could not put it back" in catalog["hidden"]["reason"]

        trial = await settings.write("device_name", "Boardroom")
        await _until(lambda: trial.status == DONE)
        assert trial.original == "Lobby" and trial.value == "Boardroom"
        assert trial.write["confirmed"] and trial.write["value"] == "Boardroom"
        assert trial.restore["confirmed"] and trial.restore["value"] == "Lobby"
        assert not trial.restore["automatic"]
        assert run.listen.sandbox.device_state()["device_name"] == "Lobby"
        texts = [e.text for e in session.timeline]
        assert any(t.startswith("Wrote Device name = Boardroom: the device reported it back")
                   for t in texts)
        assert any(t.startswith("Put Device name back to Lobby: the device reported it back")
                   for t in texts)
    finally:
        await run.stop()
        server.close()
    summary = render_summary(build_report(session))
    assert "Device settings written" in summary
    assert build_report(session)["drivers"][0]["settings"]["trials"][0]["key"] == "device_name"


async def test_a_setting_the_device_keeps_is_reported_as_not_taken(driver):
    server, port = await _fake_device()
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        # A short read-back window: this device never changes its mode.
        run.listen.sandbox.manager._confirm_window = lambda _driver: 0.6
        trial = await settings.write("mode", "manual")
        await _until(lambda: trial.status == DONE)
        assert not trial.write["confirmed"] and trial.write["value"] == "auto"
        assert "the device still reports auto" in trial.to_dict()["summary"]
        # Its original never left, so putting it back reads back at once.
        assert trial.restore["confirmed"]
    finally:
        await run.stop()
        server.close()


async def test_what_is_never_written(driver):
    server, port = await _fake_device()
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        with pytest.raises(AuditError, match="Device name is Lobby already"):
            await settings.write("device_name", "Lobby")
        with pytest.raises(AuditError, match="could not put it back"):
            await settings.write("hidden", "x")
        with pytest.raises(AuditError, match="has no device setting named volume"):
            await settings.write("volume", 3)
        with pytest.raises(AuditError, match="must be one of|not one of|allowed"):
            await settings.write("mode", "turbo")
        # A value the device reports that the setting would not accept back.
        run.listen.sandbox.state.set(
            f"device.{run.listen.sandbox.device_id}.mode", "eco",
        )
        with pytest.raises(AuditError, match="not a value OpenAVC can write back"):
            await settings.write("mode", "manual")
        assert settings.trials == []
    finally:
        await run.stop()
        server.close()


async def test_a_setting_and_a_command_never_run_at_once(driver):
    server, port = await _fake_device()
    session, run = await _connected(port)
    try:
        commands = commands_for(session, run, window_seconds=30.0, flush_seconds=0.05)
        settings = settings_for(session, run)
        trial = await commands.send("ping")
        with pytest.raises(AuditError, match="Wait for Ping to finish before changing a setting"):
            await settings.write("device_name", "Boardroom")
        await _until(lambda: trial.status == "watching")
        commands.end_now()
        await _until(lambda: trial.status == "done")
        written = await settings.write("device_name", "Boardroom")
        with pytest.raises(AuditError, match="Wait for Device name to finish before sending"):
            await commands.send("ping")
        await _until(lambda: written.status == DONE)
    finally:
        await run.stop()
        server.close()


async def test_a_setting_left_changed_goes_back_when_the_driver_stops(driver):
    server, port = await _fake_device()
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        trial = await settings.write("device_name", "Boardroom")
        await _until(lambda: trial.write.get("confirmed"))
    finally:
        # Stopping mid-way (before its own restore) still puts it back.
        await run.stop()
        server.close()
    assert trial.status == DONE and trial.restore is not None
    assert trial.restore["automatic"] and trial.restore["confirmed"]
    assert "as the driver stopped" in trial.to_dict()["summary"]


async def test_put_it_back_retries_a_restore_that_did_not_read_back(driver):
    server, port = await _fake_device()
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        with pytest.raises(AuditError, match="nothing to put back"):
            await settings.put_back("device_name")
        trial = await settings.write("device_name", "Boardroom")
        await _until(lambda: trial.status == DONE)
        trial.restore["confirmed"] = False  # as though it had not read back
        again = await settings.put_back("device_name")
        await _until(lambda: again.status == DONE)
        assert again.restore["confirmed"]
    finally:
        await run.stop()
        server.close()


def test_the_request_declares_every_field():
    assert set(AuditSettingRequest.model_fields) == {"value"}
    with pytest.raises(ValidationError):
        AuditSettingRequest(value=1, restore=True)


async def test_the_routes(wired, driver):  # noqa: F811
    server, port = await _fake_device()
    try:
        started = await routes.start_session(AuditStartRequest(address="127.0.0.1"))
        session_id = started["session"]["session_id"]
        await routes.run_network_check(session_id)
        await _until(lambda: wired.manager.current().footprint is not None)
        await routes.set_driver(session_id, AuditDriverRequest(driver_id="acme_settings"))
        wired.engine.state.set("device.lobby.paused", True)
        await routes.set_session_connection(session_id, AuditConnectionRequest(
            config={"host": "127.0.0.1", "port": port, "poll_interval": 0.2},
        ))
        await routes.connect_and_listen(session_id)
        session = wired.manager.current()
        run = session.runs[0]
        await _until(lambda: run.listen.sandbox.device_state().get("device_name") is not None)

        result = await routes.write_setting(
            session_id, "device_name", AuditSettingRequest(value="Boardroom"),
        )
        assert result["session"]["runs"][0]["settings"]["trials"][0]["value"] == "Boardroom"
        await _until(lambda: run.settings.trials[0].status == DONE)
        with pytest.raises(HTTPException) as exc:
            await routes.put_setting_back(session_id, "device_name")
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await routes.write_setting(session_id, "hidden", AuditSettingRequest(value="x"))
        assert exc.value.status_code == 409
        await routes.end_session(session_id, cancel=True)
    finally:
        server.close()
        await wired.manager.shutdown()
