"""The device audit report: the zip, what is in it, what is not, and where it is kept.

The redaction test builds a report whose observations carry a typed secret in
every form a report can hold it (plain text, raw bytes shown as hex, a web
page, a timeline line) and reads every file of the zip back. The server's own
credentials are set too, and must appear nowhere: a report never reads them.
"""

from __future__ import annotations

import io
import json
import logging
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
        value.encode().hex(" "), value.encode().hex(" ").upper(),
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
        # A line OpenAVC logs about the audit lands in log.txt, redacted too.
        logging.getLogger("openavc.audit.test").warning("The device said %s", SECRET)
        name, data = report_zip(session)
    finally:
        await manager.shutdown()
    files = _files(data)
    assert set(files) == {"summary.html", "report.json", "timeline.txt", "log.txt", "README.txt"}
    assert REDACTED in files["log.txt"]
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


def test_a_numeric_secret_is_masked_without_cutting_into_numbers():
    """A PIN is hex digits: masked where it stands alone, never inside a
    timestamp (which would break the JSON), a hash or a hex dump; its own
    bytes in a hex dump are masked by their hex form."""
    from openavc.audit.report import Redaction, Redactor

    redactor = Redactor([Redaction("7391")])
    record = {
        "t": "1790643142.167391", "sha256": "ab7391cd", "hex": "20" + "7391".encode().hex(),
        "text": "CODE 7391\r", "note": "PIN=7391, then 17391",
    }
    out = redactor.tree(record)
    assert out["t"] == "1790643142.167391" and out["sha256"] == "ab7391cd"
    assert out["hex"] == "20" + "[redacted]".encode().hex()
    assert out["text"] == "CODE [redacted]\r"
    assert out["note"] == "PIN=[redacted], then 17391"
    # In a JSON text a number keeps its digits, so the file still parses.
    text = redactor.text(json.dumps({"t": 1790643142.167391, "pin": "7391"}))
    assert json.loads(text) == {"t": 1790643142.167391, "pin": "[redacted]"}


def test_a_secret_run_into_other_characters_is_masked_in_the_traffic():
    """The audit's traffic masks a secret wherever it appears: glued to a
    command, in Latin-1, and percent-encoded in a URL or a form body. The
    server log keeps its word boundaries."""
    from openavc.audit.observe import audit_traffic_redactor
    from openavc.utils.log_redaction import compile_secret_bytes_pattern

    redactor = audit_traffic_redactor({"1234", "café", "p@ss w0rd"})
    assert redactor.data(b"CODE1234\r") == b"CODE***\r"
    assert redactor.data(b"PASS caf\xe9\r") == b"PASS ***\r"
    assert redactor.data("PASS café\r".encode()) == b"PASS ***\r"
    assert redactor.data(b"GET /x?pw=p%40ss%20w0rd") == b"GET /x?pw=***"
    assert redactor.data(b"pw=p%40ss+w0rd&x=1") == b"pw=***&x=1"
    assert redactor.data(b"pw=p%40ss%20w0rd".lower()) == b"pw=***"
    assert redactor.value({"target": "/x?pw=p%40ss%20w0rd"}) == {"target": "/x?pw=***"}
    assert compile_secret_bytes_pattern(["1234"]).search(b"CODE1234") is None


def test_the_record_masks_a_glued_or_encoded_secret_and_leaves_numbers_whole():
    from openavc.audit.report import Redaction, Redactor

    redactor = Redactor([Redaction("1234"), Redaction("p@ss w0rd"), Redaction("café")])
    out = redactor.tree({
        "note": "CODE1234 then 12345 and 1.1234",
        "url": "/x?pw=p%40ss%20w0rd",
        "text": "PASS caf\u00c3\u00a9",  # UTF-8 bytes shown as Latin-1
        "hex": "ab1234cd" + "1234".encode().hex(),
        "sha256": "001234ff",
    })
    assert out["note"] == "CODE[redacted] then 12345 and 1.1234"
    assert out["url"] == "/x?pw=[redacted]"
    assert out["text"] == "PASS [redacted]"
    assert out["hex"] == "ab1234cd" + "[redacted]".encode().hex()
    assert out["sha256"] == "001234ff"


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


async def test_every_serial_number_heard_is_left_out():
    """A driver's own serial value and a second one from SNMP go too, not
    only the one the network check put on the device."""
    from types import SimpleNamespace

    from openavc.audit.report import redactions_for, serial_numbers

    manager, session = await _session(leave_out_serial=True)
    try:
        session.footprint.snmp = {
            "answered": True, "values": {"entPhysicalSerialNum": "SNMP-55501"},
            "walk": [{"oid": "1.3.6.1.2.1.47.1.1.1.1.11.1", "value": "WALK-60001"}],
        }
        attempt = SimpleNamespace(
            to_dict=lambda: {
                "status_table": {"variables": [
                    {"name": "serial_number", "label": "Serial", "value": "DRV-77102"},
                    {"name": "power", "label": "Power", "value": True},
                ]},
                "traffic": {"entries": [{"text": "not walked"}]},
            },
            changes=[{"key": "display.1.serial", "old": None, "new": "CHD-30004"}],
            sandbox=SimpleNamespace(observer=SimpleNamespace(secrets=set())),
        )
        session.runs.append(SimpleNamespace(listens=[attempt], commands=None, settings=None))
        found = serial_numbers(session)
        assert {SERIAL, "DRV-77102", "CHD-30004"} <= found
        assert {"SNMP-55501", "WALK-60001"} <= found
        assert {r.value for r in redactions_for(session)} >= {SERIAL, "DRV-77102", "CHD-30004"}
        session.tester = {}
        assert not {SERIAL, "DRV-77102"} & {r.value for r in redactions_for(session)}
    finally:
        session.runs.clear()
        await manager.shutdown()


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


async def test_the_model_name_and_model_number_are_reported_apart():
    """An SSDP description's modelName and modelNumber stay two fields: a
    driver's SSDP ``model:`` filter reads modelName alone, so a joined
    "Widget 3000 WebRemote1.0" in the title would be copied into one."""
    manager, session = await _session()
    session.footprint.ssdp = {
        "device_types": ["urn:acme-com:device:Widget:1"],
        "model_name": "Widget 3000", "model_number": "WebRemote1.0",
    }
    try:
        report = build_report(session)
    finally:
        await manager.shutdown()
    reported = report["device"]["reported"]
    assert reported["model"] == "Widget 3000"
    assert reported["model_number"] == "WebRemote1.0"
    assert report_filename(report).startswith("openavc-device-audit-acme-widget-3000-")
    html = render_summary(report)
    assert "<title>Device audit: Acme Widget 3000</title>" in html
    identity = html.split("<h2>What the device reported</h2>")[1].split("</table>")[0]
    assert "<th>Model</th><td>Widget 3000</td>" in identity
    assert "<th>Model number</th><td>WebRemote1.0</td>" in identity

    # A number the model already holds is not said twice.
    session.footprint.ssdp["model_number"] = "3000"
    assert build_report(session)["device"]["reported"]["model_number"] is None


def test_every_sentence_writes_bytes_one_way():
    """A sentence writes bytes as hex pairs, ``hex aa 0b 01``; a ``hex`` field
    holds them unspaced. A long run is cut at the same number of bytes."""
    from openavc.audit.report import _quote, hex_pairs

    assert hex_pairs("aa0b01000c") == "aa 0b 01 00 0c"
    assert _quote({"hex": "aa0b01000c", "text": "\xaa\x0b\x01\x00\x0c"}) == "hex aa 0b 01 00 0c"
    long = {"hex": "00" * 100, "text": "\x00" * 100}
    assert _quote(long, limit=8) == "hex 00 00 00 00..."
    assert _quote({"hex": "414243", "text": "ABC"}) == '"ABC"'


def test_a_binary_probe_reply_is_said_by_its_pattern_or_its_bytes():
    """A checksummed binary reply holds a printable byte or two; the line
    never draws those as the reply (it read "N ]" for a NAK). Same rule as
    the IDE's evidence lines."""
    from openavc.audit.report import _signal_text

    def line(**data):
        return _signal_text({"evidence": {"data": {"kind": "probe", "port": 1515, **data}}})

    binary = {"text": "\xaa\xff\x01\x03N\x0b\x01]", "hex": "aaff01034e0b015d"}
    assert line(response=binary, matched_pattern="hex aa ff") == (
        "TCP probe on port 1515 matched hex aa ff"
    )
    assert line(response=binary) == "TCP probe on port 1515 returned hex aa ff 01 03 4e 0b 01 5d"
    assert line(response={"text": "\xff\xfb\x01login: "}) == 'TCP probe on port 1515 returned "login:"'
    assert line(response={"text": '/acme/model "W-100"\n'}) == (
        "TCP probe on port 1515 returned '/acme/model \"W-100\"'"
    )


def test_a_manufacturer_the_driver_supplies_reads_as_the_drivers():
    from openavc.audit.report import _signal_text

    def line(**data):
        return _signal_text({"evidence": {"data": {"kind": "vendor_string", "value": "acme", **data}}})

    assert line(source_probe_id="custom_acme_widget_tcp", from_kind="probe") == (
        'Manufacturer "acme" named in a probe reply'
    )
    assert line(source_probe_id="custom_acme_widget_tcp", from_kind="probe", from_driver=True) == (
        'Manufacturer "acme" named by the driver when its probe matched'
    )


async def test_a_report_taken_mid_check_says_so():
    manager = AuditManager(None)
    session = await manager.start(AuditTarget(address="10.0.0.50", ip="10.0.0.50"))

    class Running:
        status = "running"
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
    # It answered over SSDP, so the missing mDNS is said.
    assert "mDNS: none heard." in page


def test_the_summary_says_why_only_with_the_signals_that_point_somewhere():
    """A signal no driver uses is not listed as a reason; the leftover ones are
    one line, so an SSDP device's dozen service types do not read as
    invitations to claim them."""
    report = {
        "generator": {}, "session": {"started_at": 1_700_000_000.0, "tester": {}},
        "target": {"address": "widget.local"}, "device": {"reported": {}},
        "catalog": {"used": "none"}, "complete": True, "limits": [],
        "footprint": {"ssdp": {
            "device_types": ["urn:acme-com:device:Widget:1"],
            "location": "http://10.0.0.50:1400/b.xml",
            "raw_headers": [{"location": "http://10.0.0.50:1400/a.xml"},
                            {"location": "http://10.0.0.50:1400/b.xml"}],
        }},
        "verdict": {
            "sentence": "OpenAVC recognizes this device: Acme Widget.",
            "drivers": {"acme_widget": {"name": "Acme Widget"}},
            "explanation": {"signals": [
                {"source": "ssdp:urn:acme-com:device:Widget:1", "strong": True,
                 "drivers": ["acme_widget"]},
                {"source": "ssdp:upnp:rootdevice", "strong": True, "drivers": []},
                {"source": "oui:aa:bb:cc", "strong": False, "drivers": []},
                {"source": "vendor_string:acme", "strong": False, "drivers": ["acme_widget"],
                 "evidence": {"data": {"kind": "vendor_string", "value": "acme",
                                       "source_probe_id": "greeting:23"}}},
                # The driver's own probe named it: not something the device was seen to say.
                {"source": "vendor_string:acme corp", "strong": False, "drivers": [],
                 "evidence": {"data": {"kind": "vendor_string", "value": "acme corp",
                                       "from_kind": "probe", "from_driver": True}}},
            ]},
        },
    }
    page = render_summary(report)
    # Each signal in words: what was seen, never its id.
    assert "SSDP announcement for urn:acme-com:device:Widget:1 identifies Acme Widget" in page
    assert ("Also seen, and no catalog driver uses them: SSDP announcement for "
            "upnp:rootdevice; MAC address prefix aa:bb:cc seen.") in page
    assert "<code>ssdp:" not in page
    assert ("Manufacturer &quot;acme&quot; named in the greeting on port 23 suggests "
            "Acme Widget") in page
    assert "no driver claims it" not in page
    assert ("http://10.0.0.50:1400/a.xml; http://10.0.0.50:1400/b.xml (the one read)") in page


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
