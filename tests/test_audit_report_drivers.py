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
    TEST_REPORT_URL_MAX,
    Redactor,
    _attempt_sentence,
    _driver_limits,
    _traffic_text,
    build_report,
    place_driver_files,
    render_summary,
    render_timeline,
    report_zip,
    suggested_confidence,
    driver_test_report,
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
        assert table["volume"]["sources"] == [r"a reply matching /VOL=(\d+)/"]
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
            "summary.html", "report.json", "timeline.txt", "log.txt", "README.txt",
            "driver/acme_report.avcdriver",
        }
        # The field guide names the terms a reader meets in report.json.
        for term in ("strong", "signal_index_drivers", "ping.method", "traffic.count",
                     "needs_input", "set_by_driver", "from_announcements"):
            assert term in texts["README.txt"], term
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
        assert "not reported (would be set by a reply matching" in texts["summary.html"]
    finally:
        await run.stop()
        server.close()


async def test_a_published_default_password_is_hidden_without_saying_so(driver):
    """The password typed is the driver's published default and its name:
    the traffic masks it, the driver's own file and names are left as the
    catalog publishes them, the wizard is told, and the report says nothing
    of it."""
    published = {
        **DRIVER, "id": "wattbox", "name": "wattbox",
        "default_config": {**DRIVER["default_config"], "username": "admin", "password": "wattbox"},
    }
    _DRIVER_REGISTRY["wattbox"] = create_configurable_driver_class(published)
    raw = b"id: wattbox\nname: wattbox\ndefault_config: {username: admin, password: wattbox}\n"
    choice = _choice()
    choice.driver_id = "wattbox"
    choice.identity = {
        **choice.identity, "id": "wattbox", "name": "wattbox", "source": "catalog",
        "files": [{
            "name": "wattbox.avcdriver", "sha256": hashlib.sha256(raw).hexdigest(),
            "catalog_sha256": hashlib.sha256(raw).hexdigest(), "matches_catalog": True,
        }],
        "_contents": {"wattbox.avcdriver": raw},
    }
    server, port = await _fake_device()
    session = _session()
    session.runs[0] = run = DriverRun(index=0, choice=choice)
    try:
        await set_connection(session, {
            "host": "127.0.0.1", "port": port, "username": USERNAME, "password": "wattbox",
        })
        assert run.to_dict()["published_secrets"] == ["password"]
        listen = await start_listen(session, run, **FAST)
        await _until(lambda: listen.status == DONE)
        await run.stop()
        _, data = report_zip(session)
    finally:
        _DRIVER_REGISTRY.pop("wattbox", None)
        await run.stop()
        server.close()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.read("driver/wattbox.avcdriver") == raw
        texts = {n: zf.read(n).decode("utf-8") for n in zf.namelist() if not n.startswith("driver/")}
    report = json.loads(texts["report.json"])
    (section,) = report["drivers"]
    assert section["driver"]["id"] == "wattbox" and section["driver"]["name"] == "wattbox"
    assert section["driver"]["files"][0]["redacted_in_report"] is False
    assert section["connection"]["config"]["password"] == "***"
    sent = [bytes.fromhex(e["hex"]) for e in section["attempts"][0]["traffic"]["entries"]
            if e["direction"] == "tx" and not e.get("chunk")]
    assert b"***\r" in sent and not any(b"wattbox" in b for b in sent)
    for name, text in texts.items():
        if name == "README.txt":  # the field guide names the marker; it is the same in every report
            continue
        assert "published_secrets" not in text and "[redacted]" not in text, name


def test_a_secret_that_is_a_number_or_a_word_leaves_the_json_whole():
    """Redacted as a tree, never as JSON text: a PIN that is also a port, or a
    password of "true", cannot cut a number or a literal out of report.json."""
    from openavc.audit.report import Redaction, build_zip

    record = {"port": 1515, "ok": True, "note": "PIN 1515 sent"}
    for secret in ("1515", "true"):
        redactor = Redactor([Redaction(secret)])
        data = build_zip(redactor.tree(record), redactor, log_text=f"sent {secret}\n")
        texts = _zip_texts(data)
        assert json.loads(texts["report.json"])["port"] == 1515
        assert json.loads(texts["report.json"])["ok"] is True
        first, _, rest = texts["log.txt"].partition("\n\n")
        assert first.startswith("Times are this computer's local time, UTC")
        assert rest == "sent [redacted]\n"
    assert json.loads(texts["report.json"])["note"] == "PIN 1515 sent"


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
        "declared": 4, "reported": 0, "set_by_driver": 3, "drops": 10,
        "traffic": {"sent": 1237, "received": 0, "not_captured": False},
    }
    assert _attempt_sentence(attempt) == (
        "Connected 0.1 s after starting; the device sent nothing back to 1237 messages; "
        "0 of 4 status values reported in 180 seconds of listening, 3 more set by the "
        "driver itself; the connection dropped 10 times while listening."
    )
    attempt["traffic"] = {"sent": 12, "received": 30, "not_captured": False}
    attempt.update({"drops": 0, "reported": 3, "set_by_driver": 0})
    assert _attempt_sentence(attempt) == (
        "Connected 0.1 s after starting; 3 of 4 status values reported in 180 seconds of listening."
    )


def _run_section(**over):
    """A connected run: every value reported, nothing mishandled, two commands
    (one answered Yes, one confirmed by its sets), one setting round-tripped."""
    section = {
        "driver": {"id": "acme_widget", "name": "Acme Widget", "version": "1.2.0",
                   "source": "catalog", "files": [{"name": "acme_widget.avcdriver"}]},
        "entered": {"manufacturer": "Acme", "model": "W-100", "firmware": "2.1"},
        "connection": {"transport": "tcp", "config": {"host": "10.0.0.50", "port": 4999}},
        "attempts": [{
            "connected_at": 1.0, "contract": {"counts": {}},
            "traffic": {"sent": 10, "received": 12, "not_captured": False},
            "status_table": {"variables": [
                {"name": "power", "label": "Power", "reported": True},
                {"name": "last_error", "label": "Last Error", "reported": False},
            ]},
        }],
        "commands": {"trials": [
            {"label": "Power On", "command": "power_on", "answer": {"answer": "yes"},
             "summary": "Power went from off to on."},
            {"label": "Set Volume", "command": "set_volume", "answer": None,
             "effects": [{"outcome": "confirmed"}], "summary": "Volume is now 40."},
        ]},
        "settings": {"trials": [{"label": "Name", "write": {"confirmed": True},
                                 "restore": {"confirmed": True}, "summary": "Read back."}]},
        "outages": [],
    }
    section.update(over)
    return section


def test_full_when_every_condition_holds_and_last_error_is_not_a_gap():
    confidence = suggested_confidence(_run_section())
    assert confidence["level"] == "full"
    assert all(r["held"] for r in confidence["reasons"])


def test_partial_and_none_say_what_held_them_back():
    section = _run_section()
    section["commands"]["trials"][1]["effects"] = [{"outcome": "different"}]
    confidence = suggested_confidence(section)
    assert confidence["level"] == "partial"
    assert [r["text"] for r in confidence["reasons"] if not r["held"]] == [
        "1 of 2 commands tried were confirmed. Not confirmed: Set Volume. A command counts as "
        "confirmed when the person answered Yes, the values it should set read back, or, for a "
        "status query, its value came back."
    ]
    section["commands"]["trials"][0]["answer"] = {"answer": "cant_tell"}
    assert suggested_confidence(section)["level"] is None
    silent = _run_section()
    silent["attempts"][0]["traffic"] = {"sent": 1237, "received": 0, "not_captured": False}
    texts = [r["text"] for r in suggested_confidence(silent)["reasons"] if not r["held"]]
    assert texts[:2] == [
        "The device sent nothing back to 1237 messages.",
        "None of the status values came from the device; any it shows are the driver's own.",
    ]
    assert suggested_confidence(_run_section(attempts=[{"connected_at": None}]))["level"] is None


def test_the_test_report_fills_the_catalog_form_by_its_ids():
    from urllib.parse import parse_qs, urlsplit

    section = _run_section()
    section["suggested_confidence"] = suggested_confidence(section)
    report = {"generator": {"openavc_version": "9.9.9"}, "device": {"reported": {}}}
    filled = driver_test_report(report, section)
    query = parse_qs(urlsplit(filled["url"]).query)
    assert query["template"] == ["driver-test-report.yml"]
    assert query["driver"] == ["acme_widget 1.2.0  (acme_widget.avcdriver)"]
    assert query["models"] == ["W-100"] and query["firmware"] == ["2.1"]
    assert query["transport"] == ["tcp, port 4999"]
    # The form's dropdowns take no value from a link, so the level is in the notes.
    assert "confidence" not in query
    assert "The audit suggests confidence: full." in query["notes"][0]
    assert "Power On: Power went from off to on." in query["worked"][0]
    assert "OpenAVC 9.9.9" in query["notes"][0]
    # A driver the catalog does not carry has no catalog report to open.
    section["driver"]["source"] = "imported"
    assert driver_test_report(report, section) is None


def test_the_test_report_names_no_address_and_claims_no_attachment():
    """A test report is public: the device's address, host name and MAC, and
    any other address a sentence names, are taken out; firmware that looks
    like an address is not. The notes do not say a file is attached."""
    from urllib.parse import parse_qs, urlsplit

    section = _run_section()
    section["entered"]["firmware"] = "4.0.1.12"
    section["settings"] = {"trials": [{
        "label": "Device name", "write": {"confirmed": False},
        "summary": "Could not write Lobby: Can't reach 192.168.1.75:1023 (lab-amp.local, "
                   "00:11:22:33:44:55, fe80::1).",
    }]}
    section["suggested_confidence"] = suggested_confidence(section)
    report = {
        "generator": {"openavc_version": "9.9.9"},
        "device": {"reported": {"mac": "00:11:22:33:44:55"}},
        "target": {"address": "lab-amp.local", "ip": "192.168.1.75", "hostname": "lab-amp.local"},
    }
    filled = driver_test_report(report, section)
    query = parse_qs(urlsplit(filled["url"]).query)
    text = " ".join(v for values in query.values() for v in values)
    for private in ("192.168.1.75", "lab-amp.local", "00:11:22:33:44:55", "fe80::1"):
        assert private not in text and private not in str(filled["fields"])
    assert "Can't reach [address] ([address], [address], [address])." in query["didnt"][0]
    assert query["firmware"] == ["4.0.1.12"]
    assert "attached" not in query["notes"][0]


def test_a_long_test_report_link_is_cut_to_fit():
    section = _run_section()
    section["commands"]["trials"] = [
        {"label": f"Command {n}", "answer": {"answer": "yes"}, "summary": "x" * 200}
        for n in range(80)
    ]
    section["suggested_confidence"] = suggested_confidence(section)
    filled = driver_test_report({"generator": {}, "device": {"reported": {}}}, section)
    assert len(filled["url"]) <= TEST_REPORT_URL_MAX
    assert "more+in+the+attached+report" in filled["url"] or "more%20in%20the%20attached%20report" in filled["url"]
    assert filled["fields"]["worked"].count("Command") == 80  # the record keeps everything


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
        "10.0.0.5:9: hex 01 02"
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


def test_an_answered_status_query_confirms_and_a_command_with_no_sets_says_why():
    """A status query whose value came back is confirmed; a command the driver
    says nothing about (no ``sets``) can be confirmed only by a Yes, and the
    sentence says so rather than leave it as the device's failing."""
    section = _run_section()
    section["commands"]["trials"] = [
        {"label": "Query Power", "command": "query_power", "answer": None,
         "query": {"outcome": "reported"}},
        {"label": "Mute", "command": "mute", "answer": {"answer": "cant_tell"}},
        {"label": "Set Volume", "command": "set_volume", "answer": None,
         "effects": [{"outcome": "unchanged"}]},
    ]
    reasons = [r["text"] for r in suggested_confidence(section)["reasons"]]
    assert (
        "1 of 3 commands tried were confirmed. Not confirmed: Mute, Set Volume. A command "
        "counts as confirmed when the person answered Yes, the values it should set read "
        "back, or, for a status query, its value came back. This driver does not say which "
        "value Mute sets, so only a Yes can confirm it."
    ) in reasons
    # Answered by itself: every tried command confirmed.
    section["commands"]["trials"] = section["commands"]["trials"][:1]
    assert "Every command tried was confirmed (1)." in [
        r["text"] for r in suggested_confidence(section)["reasons"]
    ]


def test_clock_times_say_their_time_zone_and_the_liveness_check_is_said():
    import re

    from openavc.audit.report import _render_driver, render_timeline, utc_offset

    offset = utc_offset(1_700_000_000.0)
    assert re.fullmatch(r"UTC[+-]\d\d:\d\d", offset)
    text = render_timeline({
        "session": {"started_at": 1_700_000_000.0},
        "target": {"address": "10.0.0.50", "ip": "10.0.0.50"},
    })
    assert f"Times are this computer's local time, {offset}." in text.splitlines()[:4]

    section = _run_section()
    section["attempts"][0].update({"poll_interval": 15.0, "liveness_every": 30.0})
    page = "".join(_render_driver(section))
    assert "Liveness check" in page
    assert "every 30.0 seconds, apart from the polls" in page


def test_the_report_says_where_each_identity_field_came_from():
    from openavc.audit.report import identity_sources

    fp = {
        "ssdp": {"model_name": "W-100", "model_number": "WebRemote1.0", "serial_number": "SN42"},
        "names": {"reverse_dns": "widget.lan"},
        "mac": {"address": "aa:bb:cc:00:11:22", "source": "arp"},
    }
    reported = {"model": "W-100", "model_number": "WebRemote1.0", "serial_number": "SN42",
                "hostname": "widget.lan", "mac": "aa:bb:cc:00:11:22", "firmware": "2.1"}
    assert identity_sources(fp, reported) == {
        "model": "the UPnP description", "model_number": "the UPnP description",
        "serial_number": "the UPnP description", "hostname": "reverse DNS",
        "mac": "this computer's ARP table",
    }


def test_the_report_says_what_the_driver_read_and_what_was_not_run():
    from openavc.audit.report import _render_driver, _render_outages

    section = _run_section()
    section["attempts"][0]["status_table"]["variables"] += [
        {"name": "model", "label": "Model", "reported": True, "value": "W-200"},
        {"name": "firmware", "label": "Firmware", "reported": True, "value": "2.1.0"},
    ]
    page = "".join(_render_driver(section))
    assert "Model, as the driver read it</th><td>W-200 (not the model entered, W-100)" in page
    assert "Firmware, as the driver read it</th><td>2.1.0</td>" in page
    assert "Front-panel check</th><td>not run" in page
    # A model written two ways is not called different; one whose number differs is.
    from openavc.audit.report import _same_name

    assert _same_name("Connect Series 352D", "Connect Series Model 352D")
    assert not _same_name("Connect Series 352", "Connect Series Model 352D")
    assert _same_name("2.1", "2.1")
    assert "The power cycle and cable pull tests were not run." in "".join(_render_outages([]))


def test_the_timeline_names_a_probe_by_its_driver_and_the_words_read_plainly():
    from openavc.audit.commands import ANSWERS
    from openavc.audit.report import render_timeline

    text = render_timeline({
        "session": {"started_at": 1_700_000_000.0},
        "target": {"address": "10.0.0.50", "ip": "10.0.0.50"},
        "footprint": {"probes": [{
            "probe_id": "custom_acme_widget_tcp", "kind": "tcp", "port": 23,
            "sent": {"hex": "50", "text": "P"}, "reply": None, "matched": False, "miss": "no_reply",
            "started_at": 1_700_000_001.0,
        }]},
    })
    assert "acme_widget identification check, TCP port 23" in text
    assert "custom_acme_widget_tcp" not in text
    assert ANSWERS["cant_tell"] == "The person said they could not tell."


def test_the_timeline_says_a_probe_was_judged_on_the_port_s_greeting():
    from openavc.audit.report import render_timeline

    text = render_timeline({
        "session": {"started_at": 1_700_000_000.0},
        "target": {"address": "10.0.0.50", "ip": "10.0.0.50"},
        "footprint": {"probes": [{
            "probe_id": "custom_acme_widget_tcp", "kind": "tcp", "port": 23,
            "sent": {"hex": "", "text": ""},
            "reply": {"hex": "41434d45", "text": "ACME"},
            "matched": True, "miss": "", "from_greeting": True,
            "started_at": 1_700_000_001.0,
        }]},
    })
    [line] = [line for line in text.splitlines() if "TCP port 23" in line]
    assert "the port's greeting" in line and "no connection of its own" in line
    assert "sent" not in line and "(matched)" in line