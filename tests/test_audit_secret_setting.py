"""A device setting the driver marks secret, in a Device Audit.

Against a loopback fake door controller whose access code is a device setting
the driver marks secret, under a name no credential has (``door_code``). The
device reports the code in its replies, so the code is in its traffic, its
status values and the setting's own record. Nothing the audit sends the
wizard and nothing in the report carries it: the setting's value reads
``***`` wherever the audit writes it as that setting's value, and every other
place the value turns up (a reply, a status value, a sentence) masks it as
the device's other secrets are masked. A code under three characters is
masked only where the audit writes it as the setting's value or as the status
value it reads through. Uses an invented device (Acme).
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest

from openavc.audit.driver_choice import DriverChoice
from openavc.audit.listen import start_listen
from openavc.audit.passes import DriverRun
from openavc.audit.report import report_zip
from openavc.audit.session import AuditError, AuditOptions, AuditSession, AuditTarget
from openavc.audit.settings import DONE, settings_for
from openavc.core.device_traffic import get_traffic_recorder
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import REDACTED, get_secret_registry

CODE = "Vault-4821-q"
NEW_CODE = "Vault-7730-z"

DRIVER = {
    "id": "acme_door",
    "name": "Acme Door",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 0.2},
    "state_variables": {
        "door_code": {"type": "string", "label": "Door code"},
        "mode": {"type": "enum", "values": ["auto", "manual"], "label": "Mode"},
    },
    "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
    "device_settings": {
        "door_code": {
            "type": "string", "label": "Door code", "secret": True, "state_key": "door_code",
            "write": {"send": "CODE {value}\\r"},
        },
    },
    "polling": {"queries": ["CODE?\\r"]},
    "responses": [
        {"match": r"CODE=(\S+)", "set": {"door_code": "$1"}},
        {"match": r"MODE=(\w+)", "set": {"mode": "$1"}},
    ],
}

FAST = {"min_seconds": 0.3, "min_cycles": 1, "max_seconds": 5.0, "flush_seconds": 0.05}


@pytest.fixture
def driver():
    _DRIVER_REGISTRY["acme_door"] = create_configurable_driver_class(DRIVER)
    yield
    _DRIVER_REGISTRY.pop("acme_door", None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


async def _fake_door(code: str = CODE):
    """Reports its code (and a mode) to every query, and keeps a code
    written to it."""
    state = {"code": code}

    async def handle(reader, writer):
        try:
            while True:
                line = (await reader.readuntil(b"\r")).decode().strip()
                if line.startswith("CODE "):
                    state["code"] = line[5:]
                if line.startswith("CODE"):
                    writer.write(
                        f"CODE={state['code']}\rMODE=auto\r".encode(),
                    )
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


async def _connected(port: int, heard: list | None = None):
    session = AuditSession("door1", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    if heard is not None:
        session.subscribe(heard.append)
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id="acme_door", identity={"name": "Acme Door", "version": "1.0.0"},
    ))
    run.config = {"host": "127.0.0.1", "port": port}
    session.runs.append(run)
    listen = await start_listen(session, run, **FAST)
    await _until(lambda: listen.sandbox.device_state().get("door_code") is not None)
    # Let the wizard hear what the device reported.
    await asyncio.sleep(0.2)
    return session, run


def _zip_texts(session: AuditSession) -> dict[str, str]:
    _name, data = report_zip(session)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}


def _assert_absent(texts: dict[str, str], *values: str) -> None:
    for name, text in texts.items():
        if name.startswith("driver/"):
            continue  # the driver's own file: the test's declaration, no secret
        for value in values:
            assert value not in text, f"{value!r} survived in {name}"
            assert value.encode().hex() not in text, f"{value!r} survived as hex in {name}"


async def test_a_code_the_device_only_reports_is_in_no_file_of_the_report(driver):
    server, port = await _fake_door()
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        catalog = {s["key"]: s for s in settings.catalog()}
        assert catalog["door_code"]["value"] == REDACTED
        table = run.listen.to_dict()["status_table"]
        assert {s["key"]: s["value"] for s in table["settings"]}["door_code"] == REDACTED
        assert {v["name"]: v["value"] for v in table["variables"]}["door_code"] == REDACTED
    finally:
        await run.stop()
        server.close()
    _assert_absent(_zip_texts(session), CODE)


async def test_a_code_is_out_of_the_report_past_the_changes_it_keeps(driver, monkeypatch):
    """The report's list learns each value as the device reports it, not
    from the state changes the step keeps, which stop at a cap."""
    from openavc.audit import listen as listen_module

    monkeypatch.setattr(listen_module, "CHANGES_KEPT", 0)
    server, port = await _fake_door()
    session, run = await _connected(port)
    try:
        assert run.listen.changes == []
    finally:
        await run.stop()
        server.close()
    _assert_absent(_zip_texts(session), CODE)


async def test_a_code_written_and_put_back_never_reaches_the_wizard(driver):
    heard: list[dict] = []
    server, port = await _fake_door()
    session, run = await _connected(port, heard)
    try:
        settings = settings_for(session, run)
        trial = await settings.write("door_code", NEW_CODE)
        await _until(lambda: trial.status == DONE)
        assert trial.write["confirmed"] and trial.restore["confirmed"]
        shown = trial.to_dict()
        assert (shown["original"], shown["value"]) == (REDACTED, REDACTED)
        assert shown["write"]["value"] == REDACTED and shown["restore"]["value"] == REDACTED
        assert shown["summary"].startswith(f"Wrote {REDACTED} to Door code: the device reported it back")
        await asyncio.sleep(0.2)
        # What the REST routes answer with.
        answered = json.dumps(session.to_dict())
    finally:
        await run.stop()
        server.close()
    # The device's replies reached the wizard live, with the code masked.
    replies = [
        entry["text"] for message in heard if message["type"] == "audit.traffic"
        for entry in message["entries"] if entry["direction"] == "rx"
    ]
    assert any("CODE=***" in text for text in replies), replies
    for value in (CODE, NEW_CODE):
        assert value not in answered, f"{value!r} is in the session's answer"
        for message in heard:
            assert value not in json.dumps(message), f"{value!r} reached the wizard in {message['type']}"
    _assert_absent(_zip_texts(session), CODE, NEW_CODE)


async def test_a_refusal_names_the_setting_never_its_code(driver):
    server, port = await _fake_door()
    session, run = await _connected(port)
    try:
        settings = settings_for(session, run)
        with pytest.raises(AuditError) as same:
            await settings.write("door_code", CODE)
        assert CODE not in str(same.value)
        assert str(same.value) == f"Door code is {REDACTED} already. Choose a different value to test writing it."
    finally:
        await run.stop()
        server.close()


async def test_a_short_code_reads_masked_as_the_setting_s_value(driver):
    """Under the three characters the report searches for, a code is still
    never written as the setting's value."""
    heard: list[dict] = []
    server, port = await _fake_door(code="Q7")
    session, run = await _connected(port, heard)
    try:
        settings = settings_for(session, run)
        trial = await settings.write("door_code", "Z9")
        await _until(lambda: trial.status == DONE)
        shown = trial.to_dict()
        assert (shown["original"], shown["value"]) == (REDACTED, REDACTED)
        assert {s["key"]: s["value"] for s in settings.catalog()}["door_code"] == REDACTED
    finally:
        await run.stop()
        server.close()
    for message in heard:
        if message["type"] in ("audit.settings", "audit.timeline"):
            text = json.dumps(message)
            assert '"Q7"' not in text and '"Z9"' not in text, message
            assert " Q7" not in text and " Z9" not in text, message
