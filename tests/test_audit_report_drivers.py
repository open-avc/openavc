"""The report's driver sections: what each driver run did, byte for byte.

A driver signs in to a loopback fake device and listens; the report then has
to say exactly which driver file ran (and carry it under ``driver/``), the
connection with its credentials masked, every connect-and-listen attempt with
its status table, contract faults, the unprompted-reply hint and every
traffic entry as hex and text, and not one of the typed secrets anywhere in
the zip.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
from types import SimpleNamespace

import pytest

from openavc.audit.driver_choice import DriverChoice
from openavc.audit.listen import DONE, start_listen
from openavc.audit.passes import DriverRun, set_connection
from openavc.audit.report import (
    Redactor,
    _attempt_sentence,
    _driver_limits,
    _traffic_text,
    build_report,
    place_driver_files,
    render_summary,
    render_timeline,
    report_zip,
)
from openavc.audit.session import AuditOptions, AuditSession, AuditTarget
from openavc.core.device_traffic import get_traffic_recorder
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.utils.log_redaction import get_secret_registry

PASSWORD = "hunter22"
USERNAME = "operator7"

DRIVER = {
    "id": "acme_report",
    "name": "Acme Report",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 0.1},
    "config_schema": {
        "host": {"type": "string"},
        "port": {"type": "integer"},
        "username": {"type": "string"},
        "password": {"type": "string", "secret": True},
    },
    "auth": {
        "type": "telnet_login",
        "username_prompt": "login: ",
        "password_prompt": "Password: ",
        "line_ending": "\r",
    },
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "volume": {"type": "integer", "label": "Volume"},
    },
    "commands": {},
    "polling": {"queries": ["PWR?\\r"]},
    "responses": [
        {"match": r"PWR=(\w+)", "set": {"power": "$1"}},
        {"match": r"VOL=(\d+)", "set": {"volume": "$1"}},
    ],
}

# The file as it sits on disk; it names the factory password, so its copy in
# the report is redacted and says so.
DRIVER_FILE = (
    "id: acme_report\n"
    "name: Acme Report\n"
    f"# The factory sign-in is {USERNAME} / {PASSWORD}.\n"
).encode("utf-8")

FAST = {"min_seconds": 0.6, "min_cycles": 3, "max_seconds": 5.0, "flush_seconds": 0.05}


@pytest.fixture
def driver():
    _DRIVER_REGISTRY["acme_report"] = create_configurable_driver_class(DRIVER)
    yield
    _DRIVER_REGISTRY.pop("acme_report", None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


async def _fake_device():
    """Asks for a sign-in, then answers each power query with its power and
    a lamp-hours line the driver has no rule for."""

    async def handle(reader, writer):
        try:
            writer.write(b"login: ")
            await writer.drain()
            await reader.readuntil(b"\r")
            writer.write(b"Password: ")
            await writer.drain()
            await reader.readuntil(b"\r")
            while True:
                await reader.readuntil(b"\r")
                writer.write(b"PWR=on\rLAMP=450\r")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _choice() -> DriverChoice:
    return DriverChoice(
        driver_id="acme_report", manufacturer="Acme", model="W-100", firmware="2.04",
        identity={
            "id": "acme_report", "name": "Acme Report", "version": "1.0.0",
            "format": "avcdriver", "source": "imported",
            "files": [{
                "name": "acme_report.avcdriver",
                "sha256": hashlib.sha256(DRIVER_FILE).hexdigest(),
                "catalog_sha256": None, "matches_catalog": None,
            }],
            "_contents": {"acme_report.avcdriver": DRIVER_FILE},
        },
        model_listing={"listed": False, "confidence": None},
        verdict_agreement="no_verdict",
    )


def _session() -> AuditSession:
    session = AuditSession("rpt1", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    session.device_entered = {"manufacturer": "Acme", "model": "W-100", "firmware": "2.04"}
    session.runs.append(DriverRun(index=0, choice=_choice()))
    return session


async def _until(predicate, timeout: float = 8.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def _zip_texts(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


async def test_a_driver_run_is_the_whole_story_and_no_secret_leaves(driver):
    server, port = await _fake_device()
    session = _session()
    run = session.runs[0]
    try:
        await set_connection(session, {
            "host": "127.0.0.1", "port": port, "username": USERNAME, "password": PASSWORD,
        })
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.status == DONE)
        await run.stop()

        report = build_report(session)
        (section,) = report["drivers"]
        assert report["device"]["entered"] == {
            "manufacturer": "Acme", "model": "W-100", "firmware": "2.04",
        }
        # Which file ran, and where the zip holds it.
        (file,) = section["driver"]["files"]
        assert file["in_report"] == "driver/acme_report.avcdriver"
        assert file["redacted_in_report"] is True
        assert "_contents" not in section["driver"]
        assert section["model_listing"] == {"listed": False, "confidence": None}
        # The connection as the person set it, credentials masked.
        assert section["connection"]["config"]["password"] == "***"
        assert section["connection"]["config"]["port"] == port

        (attempt,) = section["attempts"]
        assert attempt["status"] == DONE
        assert attempt["declared"] == 2 and attempt["reported"] == 1
        table = {v["name"]: v for v in attempt["status_table"]["variables"]}
        # As it stood when the driver stopped (its state went with it), and
        # the audit taking the driver down is not a drop.
        assert table["power"]["value"] is True
        assert attempt["drops"] == 0
        assert "listen.dropped" not in [e["kind"] for e in report["timeline"]]
        assert table["volume"]["reported"] is False
        assert table["volume"]["sources"] == [r"reply matching /VOL=(\d+)/"]
        # LAMP=450 matched no rule: kept in full, and counted.
        assert attempt["contract"]["counts"]["unmatched_response"] >= 1
        assert any(e["kind"] == "unmatched_response" for e in attempt["contract"]["events"])
        assert any(c["key"] == "power" for c in attempt["state_changes"])
        # Every entry: channel, direction, hex and text; the prompt the device
        # sent before anything was asked is flagged as unprompted.
        entries = attempt["traffic"]["entries"]
        assert all({"channel", "direction", "hex", "text", "seq", "t"} <= set(e) for e in entries)
        frames = [e for e in entries if not e.get("chunk")]
        assert frames[0]["direction"] == "rx" and frames[0]["text"] == "login: "
        assert frames[0]["seq"] in attempt["unprompted_replies"]["seq"]
        assert any(e.get("chunk") for e in entries)  # raw receive chunks too
        sent = [bytes.fromhex(e["hex"]) for e in frames if e["direction"] == "tx"]
        assert b"***\r" in sent and b"PWR?\r" in sent
        assert attempt["traffic"]["count"] == len(frames)
        # A driver ran, so the report does not say none did.
        assert "no_driver_tested" not in [limit["id"] for limit in report["limits"]]

        name, data = report_zip(session)
        assert name.startswith("openavc-device-audit-acme-w-100-")
        texts = _zip_texts(data)
        assert set(texts) == {
            "summary.html", "report.json", "timeline.txt", "log.txt",
            "driver/acme_report.avcdriver",
        }
        for secret in (PASSWORD, USERNAME):
            for form in (secret, secret.encode().hex(), secret.encode().hex().upper()):
                for member, text in texts.items():
                    assert form not in text, (secret, form, member)
        assert "[redacted]" in texts["driver/acme_report.avcdriver"]
        assert json.loads(texts["report.json"])["drivers"][0]["run"] == 0
        timeline = texts["timeline.txt"]
        # The kind column is padded to the widest kind in the file.
        assert re.search(r'tx tcp +"PWR\?\\r"', timeline)
        assert re.search(r'rx tcp +"login: "', timeline)
        assert "Driver test: Acme Report 1.0.0" in texts["summary.html"]
        assert "not reported (set by reply matching" in texts["summary.html"]
    finally:
        await run.stop()
        server.close()


def test_no_driver_said_so_and_nothing_tested():
    session = AuditSession("rpt2", AuditTarget("10.0.0.5", "10.0.0.5"), AuditOptions())
    session.no_driver = True
    report = build_report(session)
    assert report["drivers"] == [] and report["session"]["no_driver"] is True
    (limit,) = [x for x in report["limits"] if x["id"] == "no_driver_tested"]
    assert limit["text"].startswith("The person said there is no driver for this device yet")


def test_a_driver_chosen_but_never_connected_is_named():
    session = _session()
    report = build_report(session)
    ids = [x["id"] for x in report["limits"]]
    assert "no_driver_tested" in ids and "driver_not_connected" in ids
    (section,) = report["drivers"]
    assert section["attempts"] == [] and section["driver"]["files"][0]["in_report"] is None
    assert place_driver_files(session, Redactor([])) == []
    assert "The audit did not connect this driver." in render_summary(report)


def test_an_updated_driver_keeps_both_files():
    def run(index: int, data: bytes):
        choice = SimpleNamespace(file_contents={"acme.avcdriver": data})
        return SimpleNamespace(index=index, choice=choice, listens=[object()])

    session = SimpleNamespace(runs=[run(0, b"v: 1\n"), run(1, b"v: 2\n"), run(2, b"v: 1\n")])
    placed = place_driver_files(session, Redactor([]))
    assert [p.path for p in placed] == [
        "driver/acme.avcdriver", "driver/run-2/acme.avcdriver", "driver/acme.avcdriver",
    ]


def _limit_attempt(not_captured=False, truncated=None, callbacks=(), traffic=(), counts=None):
    observer = SimpleNamespace(
        truncated_at=truncated, traffic=list(traffic), event_counts=dict(counts or {}),
    )
    return SimpleNamespace(
        to_dict=lambda: {"traffic": {"not_captured": not_captured}},
        sandbox=SimpleNamespace(observer=observer, push_callbacks=lambda: list(callbacks)),
    )


def test_limits_for_traffic_the_audit_could_not_keep():
    def attempt(not_captured: bool, truncated: float | None):
        return _limit_attempt(not_captured, truncated)

    run = SimpleNamespace(
        index=0, choice=SimpleNamespace(identity={"name": "Acme Socket"}, driver_id="x"),
        listens=[attempt(True, None), attempt(False, 1.0)],
    )
    limits = _driver_limits(SimpleNamespace(runs=[run], no_driver=False))
    assert [(x["id"], x["run"]) for x in limits] == [
        ("traffic_not_captured", 0), ("traffic_truncated", 0),
    ]
    assert limits[0]["text"] == (
        "Acme Socket manages its own connection, so its traffic was not captured."
    )


def test_a_push_the_device_never_sent_is_a_limit_naming_where(monkeypatch):
    """The driver asked the device to send its events to OpenAVC, and none
    came: the report says where it asked, and why when OpenAVC listens only
    on this machine."""
    from openavc import config

    url = "http://192.168.1.20:8080/api/push/audit-x"
    heard = SimpleNamespace(channel="http_listener", direction="rx")
    run = SimpleNamespace(
        index=0, choice=SimpleNamespace(identity={"name": "Acme Speaker"}, driver_id="x"),
        listens=[_limit_attempt(callbacks=[url])],
    )
    monkeypatch.setattr(config, "BIND_ADDRESS", "127.0.0.1")
    [limit] = _driver_limits(SimpleNamespace(runs=[run], no_driver=False))
    assert limit["id"] == "push_never_arrived" and limit["callbacks"] == [url]
    assert limit["text"].startswith(f"Acme Speaker asked the device to send its events to {url}")
    assert "listening on 127.0.0.1 only" in limit["text"]

    monkeypatch.setattr(config, "BIND_ADDRESS", "0.0.0.0")
    [limit] = _driver_limits(SimpleNamespace(runs=[run], no_driver=False))
    assert "firewall" in limit["text"]

    run.listens = [_limit_attempt(callbacks=[url], traffic=[heard])]
    assert _driver_limits(SimpleNamespace(runs=[run], no_driver=False)) == []


def test_kept_events_and_log_lines_say_when_they_stopped_keeping():
    from openavc.audit.observe import EVENT_DETAIL_KEPT

    run = SimpleNamespace(
        index=0, choice=SimpleNamespace(identity={"name": "Acme Socket"}, driver_id="x"),
        listens=[_limit_attempt(counts={"unmatched_response": EVENT_DETAIL_KEPT + 1})],
    )
    log = SimpleNamespace(dropped=3, keep=5000)
    ids = [x["id"] for x in _driver_limits(SimpleNamespace(runs=[run], no_driver=False, log=log))]
    assert ids == ["events_capped", "log_capped"]


def test_the_session_log_keeps_lines_about_this_audit_only():
    import logging

    from openavc.audit.session import SessionLog

    handler = SessionLog("audit-abc", ["192.168.1.1", "widget.local"], keep=3)
    other = logging.getLogger("openavc.transport.tcp")

    def emit(logger, text):
        handler.handle(logger.makeRecord(logger.name, logging.INFO, "", 0, text, (), None))

    emit(other, "[audit-abc] Connected via tcp")
    emit(other, "TCP connection lost to 192.168.1.1:23")
    emit(other, "TCP connection lost to 192.168.1.10:23")  # another device
    emit(other, "[audit-abcd] not this session's device")
    emit(logging.getLogger("audit.session"), "Device audit abc ended")
    emit(other, "Resolved widget.local")
    assert len(handler.lines) == 3 and handler.dropped == 1
    assert "192.168.1.10" not in handler.text() and "audit-abcd" not in handler.text()


def test_the_summary_says_when_the_device_sent_nothing_back():
    """The BLU-100 case: connected, three values, all the driver's own, no
    byte from the device, a drop a minute. "Connected; 3 of 4 reported" alone
    read as a working device."""
    attempt = {
        "status": "done", "started_at": 100.0, "connected_at": 100.1, "finished_at": 280.1,
        "declared": 4, "reported": 3, "drops": 10,
        "traffic": {"sent": 1237, "received": 0, "not_captured": False},
    }
    assert _attempt_sentence(attempt) == (
        "Connected 0.1 s after starting; the device sent nothing back to 1237 messages; "
        "3 of 4 status values reported in 180 seconds of listening, all by the driver itself; "
        "the link dropped 10 times."
    )
    attempt["traffic"] = {"sent": 12, "received": 30, "not_captured": False}
    attempt["drops"] = 0
    assert _attempt_sentence(attempt) == (
        "Connected 0.1 s after starting; 3 of 4 status values reported in 180 seconds of listening."
    )


def test_traffic_lines_say_what_the_bytes_do_not():
    def entry(channel, direction, text="", meta=None):
        data = text.encode("latin-1")
        return {"channel": channel, "direction": direction, "hex": data.hex(), "text": text,
                "meta": meta or {}}

    assert _traffic_text(entry("http", "tx", "", {"method": "GET", "target": "/api/status"})) == (
        "GET /api/status"
    )
    assert _traffic_text(entry("http", "rx", '{"power":"on"}', {
        "method": "GET", "target": "/api/status", "status": 200, "reason": "OK",
    })) == '200 OK for GET /api/status, body "{"power":"on"}"'
    assert _traffic_text(entry("http", "rx", "", {
        "method": "GET", "target": "/x", "error": "timed out",
    })) == "no response to GET /x: timed out"
    assert _traffic_text(entry("udp", "rx", "\x01\x02", {"peer": "10.0.0.5:9"})) == (
        "10.0.0.5:9: hex 0102"
    )
    assert _traffic_text(entry("mqtt", "tx", "on", {"topic": "acme/power"})) == (
        'topic acme/power: "on"'
    )


def test_the_timeline_labels_each_driver_when_there_are_several():
    report = {
        "target": {}, "session": {}, "generator": {},
        "drivers": [
            {"driver": {"name": "One"}, "attempts": [{"traffic": {"entries": [
                {"t": 2.0, "direction": "tx", "channel": "tcp", "hex": "41", "text": "A"},
                {"t": 2.5, "direction": "rx", "channel": "tcp", "hex": "42", "text": "B",
                 "chunk": True},
            ]}}]},
            {"driver": {"name": "Two"}, "attempts": [{"traffic": {"entries": [
                {"t": 1.0, "direction": "tx", "channel": "tcp", "hex": "43", "text": "C"},
            ]}}]},
        ],
    }
    lines = [line for line in render_timeline(report).splitlines() if " tcp " in line]
    assert [line.split("  ")[-1] for line in lines] == ['[Two] "C"', '[One] "A"']
