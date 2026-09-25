"""The device audit report: the zip, what is in it, what is not, and where it is kept.

The redaction test builds a report whose observations carry a typed secret in
every form a report can hold it (plain text, raw bytes shown as hex, a web
page, a timeline line) and reads every file of the zip back. The server's own
credentials are set too, and must appear nowhere: a report never reads them.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from openavc.audit.footprint import Footprint, Limit
from openavc.audit.report import (
    REDACTED,
    REPORT_VERSION,
    SERIAL_REMOVED,
    ReportStore,
    build_report,
    render_summary,
    report_filename,
    report_zip,
    save_session_report,
)
from openavc.audit.session import AuditManager, AuditOptions, AuditTarget
from openavc.discovery.http_fetch import HttpExchange, parse_head
from openavc.discovery.port_scanner import PortGreeting
from openavc.discovery.result import DiscoveredDevice

SECRET = "widget-community-7"
SERIAL = "AW3-0042"
SERVER_SECRETS = ("server-api-key-9f8e", "programmer-pass-5d4c", "cloud-system-key-3b2a")


def _footprint(ip: str = "10.0.0.50") -> Footprint:
    fp = Footprint(address="widget.local", ip=ip)
    fp.ping = {"method": "exec", "result": "alive", "attempts": []}
    fp.port_states = {23: "open", 80: "open", 9999: "refused"}
    fp.port_list = [23, 80, 9999]
    fp.greetings[23] = PortGreeting(port=23, data=f"login as {SECRET}\r\n".encode())
    head = "HTTP/1.0 401 Unauthorized\r\nWWW-Authenticate: Basic realm=\"Widget\""
    fp.web[80] = HttpExchange(
        url=f"http://{ip}:80/", head=head, headers=parse_head(head),
        body=f"<title>Widget {SERIAL}</title><!-- {SECRET} -->".encode(),
    )
    fp.ssdp = {
        "device_types": ["urn:acme-com:device:Widget:1"],
        "description_xml": f"<serialNumber>{SERIAL}</serialNumber>",
    }
    fp.snmp = {"answered": True, "community": "community 2 of 2",
               "values": {"sysDescr": "Acme Widget", "sysContact": SECRET.upper()}}
    fp.device = DiscoveredDevice(ip=ip, manufacturer="Acme", model="Widget 3000",
                                 serial_number=SERIAL)
    fp.verdict = {
        "state": "identified",
        "sentence": "OpenAVC recognizes this device: Acme Widget.",
        "identification": {"state": "identified", "driver_id": "acme_widget"},
        "explanation": {"signals": [{"source": "probe:custom_acme_widget_tcp", "strong": True,
                                     "drivers": ["acme_widget"]}]},
        "drivers": {"acme_widget": {"name": "Acme Widget", "installed": False}},
        "checks": {"acme_widget": [{"kind": "probe", "declared": "TCP port 23", "status": "matched",
                                    "detail": "Connected; the device replied."}]},
        "catalog": {"used": "fresh", "driver_count": 2, "sha256": "ab" * 32},
    }
    fp.limits = [Limit("ipv6", "IPv6 was not checked.")]
    fp.finished_at = fp.started_at + 60
    return fp


async def _session(manager=None, **tester):
    manager = manager or AuditManager(None)
    session = await manager.start(
        AuditTarget(address="widget.local", ip="10.0.0.50"),
        AuditOptions(snmp_communities=[SECRET]),
    )
    session.footprint = _footprint()
    session.tester = dict(tester)
    session.add_timeline("check.ports", f"Heard {SECRET} on port 23.")
    return manager, session


def _files(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def _forms(value: str) -> list[str]:
    return [
        value, value.encode().hex(), value.encode().hex().upper(),
        json.dumps(value)[1:-1],
    ]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


async def test_a_typed_secret_is_in_no_file_in_any_form(monkeypatch):
    from openavc import config

    monkeypatch.setattr(config, "API_KEY", SERVER_SECRETS[0], raising=False)
    monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", SERVER_SECRETS[1], raising=False)
    monkeypatch.setenv("OPENAVC_CLOUD_SYSTEM_KEY", SERVER_SECRETS[2])

    manager, session = await _session(name="Pat", email="pat@example.com")
    try:
        name, data = report_zip(session)
    finally:
        await manager.shutdown()
    files = _files(data)
    assert set(files) == {"summary.html", "report.json", "timeline.txt"}
    for fname, text in files.items():
        for form in _forms(SECRET):
            assert form not in text, f"{fname} holds the secret as {form!r}"
        for secret in SERVER_SECRETS:
            assert secret not in text, f"{fname} holds a server secret"
    report = json.loads(files["report.json"])
    assert REDACTED in files["timeline.txt"]
    assert REDACTED in report["footprint"]["greetings"]["23"]["text"]
    # Hex of the replacement, so the hex view stays hex.
    assert REDACTED.encode().hex() in report["footprint"]["greetings"]["23"]["hex"]
    # The device's own upper-case echo is not the typed value; it stays.
    assert SECRET.upper() in files["report.json"]


async def test_the_serial_number_can_be_left_out_everywhere():
    manager, session = await _session(leave_out_serial=True)
    try:
        _, data = report_zip(session)
    finally:
        await manager.shutdown()
    for fname, text in _files(data).items():
        assert SERIAL not in text, fname
    report = json.loads(_files(data)["report.json"])
    assert report["device"]["reported"]["serial_number"] == SERIAL_REMOVED
    assert "leave_out_serial" not in report["session"]["tester"]


async def test_the_serial_number_stays_unless_asked():
    manager, session = await _session()
    try:
        report = build_report(session)
    finally:
        await manager.shutdown()
    assert report["device"]["reported"]["serial_number"] == SERIAL


# ---------------------------------------------------------------------------
# The record and its files
# ---------------------------------------------------------------------------


async def test_the_record_has_every_section():
    manager, session = await _session(name="Pat")
    try:
        report = build_report(session)
    finally:
        await manager.shutdown()
    assert report["report_version"] == REPORT_VERSION == 1
    assert set(report) >= {
        "generator", "session", "target", "device", "catalog", "footprint", "evidence",
        "verdict", "drivers", "timeline", "limits", "complete",
    }
    assert report["generator"]["openavc_version"]
    assert report["catalog"]["driver_count"] == 2
    assert "catalog" not in report["verdict"]
    assert report["complete"] is True
    assert report["session"]["tester"] == {"name": "Pat"}
    assert {limit["id"] for limit in report["limits"]} >= {"ipv6", "no_driver_tested"}
    json.dumps(report)


async def test_a_report_taken_mid_check_says_so():
    manager = AuditManager(None)
    session = await manager.start(AuditTarget(address="10.0.0.50", ip="10.0.0.50"))

    class Running:
        footprint = _footprint()

    session.check = Running()
    try:
        report = build_report(session)
    finally:
        await manager.shutdown()
    assert report["complete"] is False
    assert report["limits"][0]["id"] == "check_unfinished"


def test_the_summary_is_self_contained_and_says_the_verdict():
    report = {
        "generator": {"openavc_version": "0.0.0", "os": "TestOS"},
        "session": {"started_at": 1_700_000_000.0, "tester": {}},
        "target": {"address": "widget.local", "ip": "10.0.0.50"},
        "device": {"reported": {"manufacturer": "Acme", "model": "<Widget>"}},
        "catalog": {"used": "none"},
        "footprint": _footprint().to_dict(),
        "verdict": {"sentence": "OpenAVC can see this device but does not recognize it."},
        "limits": [{"id": "ipv6", "text": "IPv6 was not checked."}],
        "complete": True,
    }
    page = render_summary(report)
    assert "<script" not in page.lower()
    assert "OpenAVC can see this device but does not recognize it." in page
    assert "&lt;Widget&gt;" in page and "<Widget>" not in page
    assert "IPv6 was not checked." in page


def test_the_file_name_is_manufacturer_model_and_time():
    report = {
        "session": {"started_at": 1_700_000_000.0},
        "device": {"entered": {}, "reported": {"manufacturer": "Acme Co.", "model": "Widget/3000"}},
        "target": {"address": "widget.local", "ip": "10.0.0.50"},
    }
    name = report_filename(report)
    assert name.startswith("openavc-device-audit-acme-co-widget-3000-")
    assert name.endswith(".zip")
    report["device"]["reported"] = {}
    assert report_filename(report).startswith("openavc-device-audit-10-0-0-50-unidentified-")


# ---------------------------------------------------------------------------
# Recent reports
# ---------------------------------------------------------------------------


def test_the_store_keeps_the_newest_and_refuses_odd_names(tmp_path):
    import os
    import time

    store = ReportStore(tmp_path / "audit_reports", keep=3)
    names = []
    past = time.time() - 100
    for i in range(5):
        saved = store.save("openavc-device-audit-acme-widget-20260925-1200.zip", b"zip%d" % i)
        # Oldest first, each still older than the next save.
        os.utime(store.path(saved), (past + i, past + i))
        names.append(saved)
    assert names[1] == "openavc-device-audit-acme-widget-20260925-1200-2.zip"
    listed = [r["name"] for r in store.list()]
    assert listed == list(reversed(names))[:3]
    assert store.path("../secrets.zip") is None
    assert store.path("report.json") is None
    with pytest.raises(ValueError):
        store.save("../x.zip", b"")
    assert store.delete(listed[0]) is True
    assert store.delete(listed[0]) is False


async def test_a_session_saves_its_report_when_it_ends(tmp_path):
    store = ReportStore(tmp_path)
    manager = AuditManager(None)

    async def save(session):
        await save_session_report(session, store)

    manager.add_end_hook(save)
    _, session = await _session(manager)
    first = await save_session_report(session, store)
    await manager.finish(session.id)
    # One file: the save at the end replaced the earlier one.
    assert [r["name"] for r in store.list()] == [first]
    report = json.loads(_files(store.path(first).read_bytes())["report.json"])
    assert report["session"]["status"] == "finished"
    assert report["timeline"][-1]["kind"] == "session.ended"


async def test_a_session_that_never_checked_saves_nothing(tmp_path):
    store = ReportStore(tmp_path)
    manager = AuditManager(None)
    session = await manager.start(AuditTarget(address="10.0.0.50", ip="10.0.0.50"))
    assert await save_session_report(session, store) is None
    await manager.shutdown()
    assert store.list() == []
