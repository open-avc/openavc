"""The problem report: what it holds, what it never holds, and its routes.

The engine is a stand-in whose project holds real device models; the state
store, traffic recorder, log buffer and secret registry are the platform's
own, so what the report reads is what a running server would hand it.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openavc.api.models import ProblemReportContext, ProblemReportRequest
from openavc.api.routes import problem_reports as routes
from openavc.audit.report import ReportStore
from openavc.core.device_traffic import get_traffic_recorder, record_traffic
from openavc.core.project_loader import DeviceConfig
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.support import problem_report as pr
from openavc.support import report_upload
from openavc.utils.log_buffer import LogEntry, get_log_buffer
from openavc.utils.log_redaction import get_secret_registry

DRIVER = "acme_widget_problem"


class _AcmeWidget(BaseDriver):
    DRIVER_INFO = {
        "id": DRIVER, "name": "Acme Widget", "manufacturer": "Acme", "version": "1.2.0",
        "category": "utility", "transport": "tcp", "default_config": {"port": 7000},
        "config_schema": {
            "host": {"type": "string"},
            "access_code": {"type": "string", "secret": True},
        },
        "commands": {
            "login": {"label": "Log in", "params": {"pin": {"type": "password"}}},
        },
        "state_variables": {},
    }


_DRIVER_REGISTRY[DRIVER] = _AcmeWidget

# This device's secrets, each put somewhere a report reads.
CODE = "Zq7-access-code"       # a setting the schema marks secret
TOKEN = "tok-9f8e7d6c5b"       # a token the driver registered at runtime
PIN = "pin-48213-x"            # a command parameter of type password
STATE_SECRET = "statepw-77ab"  # a value the driver wrote under a credential's name
SERIAL = "SN-ACME-0042"

# The other device: nothing of it may appear anywhere.
OTHER = {"id": "lobby_widget", "name": "Lobby Widget", "host": "10.9.8.77",
         "secret": "other-secret-55"}


class FakeDevices:
    installed_equipment = None
    unsimulated_driver = None

    def __init__(self, info: dict[str, dict]):
        self.info = info

    def get_device_info(self, device_id):
        return self.info.get(device_id, {})


@pytest.fixture
def engine():
    state = StateStore()
    project = SimpleNamespace(
        devices=[
            DeviceConfig(id="hall_widget", driver=DRIVER, name="Hall Widget",
                         config={"host": "192.168.1.50", "port": 7000, "access_code": CODE}),
            DeviceConfig(id=OTHER["id"], driver=DRIVER, name=OTHER["name"],
                         config={"host": OTHER["host"], "access_code": OTHER["secret"]}),
        ],
        connections={},
    )
    registry = get_secret_registry()
    registry.set_config_secrets("hall_widget", [CODE])
    registry.add_runtime_secret("hall_widget", TOKEN)
    registry.set_config_secrets(OTHER["id"], [OTHER["secret"]])
    state.set("device.hall_widget.connected", True)
    state.set("device.hall_widget.power", "on")
    state.set("device.hall_widget.serial_number", SERIAL)
    state.set("device.hall_widget.session", "old-" + TOKEN)
    state.set("device.hall_widget.session", TOKEN)
    state.set("device.hall_widget.auth_token", STATE_SECRET)
    state.set("device.hall_widget.model", "Widget 3000")
    state.set(f"device.{OTHER['id']}.power", OTHER["secret"])
    state.set("system.version", "0.0.0-test")
    recorder = get_traffic_recorder()
    record_traffic("hall_widget", "tx", f"LOGIN {CODE} {TOKEN}\r".encode(), channel="tcp")
    record_traffic("hall_widget", "rx", f"OK SERIAL {SERIAL}\r".encode(), channel="tcp")
    record_traffic(OTHER["id"], "tx", f"LOGIN {OTHER['secret']}\r".encode(), channel="tcp")
    buffer = get_log_buffer()
    now = time.time()
    buffer.append(LogEntry(now, "INFO", "drivers.acme", "device",
                           f"[hall_widget] logging in with {PIN}", device="hall_widget"))
    buffer.append(LogEntry(now, "INFO", "drivers.acme", "device",
                           f"[{OTHER['id']}] {OTHER['name']} at {OTHER['host']}", device=OTHER["id"]))
    yield SimpleNamespace(
        project=project, state=state,
        devices=FakeDevices({"hall_widget": {"id": "hall_widget", "connected": True}}),
        simulation=SimpleNamespace(is_redirected=lambda device_id: False),
    )
    for device_id in ("hall_widget", OTHER["id"]):
        recorder.forget(device_id)
        registry.forget(device_id)


def _members(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}


def _build(engine, **sender) -> tuple[dict, dict[str, str]]:
    snapshot = pr.Snapshot(engine, "hall_widget", catalog=None)
    report, redactor = pr.build_report(snapshot, **sender)
    return report, _members(pr.build_zip(report, redactor, snapshot))


def _context() -> dict:
    return ProblemReportContext(
        source="command_failure", error="The device did not answer.",
        command="login", params={"pin": PIN}, status=500,
    ).model_dump()


def test_no_secret_and_nothing_of_another_device_survives_in_any_file(engine):
    _, files = _build(engine, message="It stopped answering", context=_context())
    assert set(files) >= {"summary.html", "report.json", "traffic.txt", "log.txt", "README.txt"}
    for name, text in files.items():
        for secret in (CODE, TOKEN, PIN, STATE_SECRET):
            assert secret not in text, f"{secret!r} survived in {name}"
            assert secret.encode().hex() not in text, f"{secret!r} survived as hex in {name}"
        # The test driver's "file" is this module, which names the other
        # device in its own source; the secrets above are checked in it too.
        if name.startswith("driver/"):
            continue
        for value in OTHER.values():
            assert value not in text, f"{value!r} of the other device is in {name}"


def test_state_and_both_sides_of_history_are_masked(engine):
    report, _ = _build(engine)
    # A registered token the driver also wrote to state, and a value under a
    # credential's name that nothing registered.
    assert report["state"]["device"]["session"] == "[redacted]"
    assert report["state"]["device"]["auth_token"] == "[redacted]"
    rows = [r for r in report["history"] if r["key"] == "device.hall_widget.session"]
    assert rows[-1]["old_value"] == "old-[redacted]"
    assert rows[-1]["new_value"] == "[redacted]"
    assert report["device"]["settings"]["access_code"] == "***"
    assert report["state"]["device"]["power"] == "on"


def test_a_command_password_is_masked_in_the_context_and_the_log(engine):
    report, files = _build(engine, context=_context())
    assert report["context"]["params"] == {"pin": "***"}
    assert report["context"]["status"] == 500
    assert "logging in with" in files["log.txt"]


def test_report_json_carries_what_the_receiving_service_reads(engine):
    report, files = _build(engine, message="msg", name="Pat", email="pat@example.com")
    assert json.loads(files["report.json"])["report_id"] == report["report_id"]
    assert report["report_kind"] == "problem_report" and report["report_version"] == 1
    uuid.UUID(report["report_id"])
    assert isinstance(report["created_at"], float)
    assert report["sender"] == {"message": "msg", "name": "Pat", "email": "pat@example.com",
                                "leave_out_serial": False}
    identity = report["device"]["driver_identity"]
    assert {identity["id"], identity["version"], identity["manufacturer"]} == {DRIVER, "1.2.0", "Acme"}
    assert "modified" in identity and "_contents" not in identity and "_paths" not in identity
    assert report["device"]["model"] == "Widget 3000"
    assert set(report["generator"]) >= {"openavc_version", "deployment_type", "os"}
    assert report["device"]["address"]["host"] == "192.168.1.50"


def test_every_build_has_its_own_report_id(engine):
    first, _ = _build(engine)
    second, _ = _build(engine)
    assert first["report_id"] != second["report_id"]


def test_the_window_is_the_oldest_message_and_a_rebuild_empties_it(engine):
    snapshot = pr.Snapshot(engine, "hall_widget", catalog=None)
    oldest = get_traffic_recorder().get_recent("hall_widget")[0].t
    assert pr.preview(snapshot)["window_start"] == oldest
    assert pr.preview(snapshot)["messages"] == 2
    get_traffic_recorder().forget("hall_widget")  # what a saved settings change does
    snapshot = pr.Snapshot(engine, "hall_widget", catalog=None)
    report, _ = pr.build_report(snapshot)
    assert report["traffic"] == {"window_start": None, "count": 0, "entries": []}
    assert "no_traffic" in {item["id"] for item in report["limits"]}


def test_the_serial_number_leaves_only_when_asked(engine):
    snapshot = pr.Snapshot(engine, "hall_widget", catalog=None)
    assert pr.preview(snapshot)["serial_found"] is True
    _, kept = _build(engine)
    assert SERIAL in kept["report.json"]
    _, files = _build(engine, leave_out_serial=True)
    for name, text in files.items():
        assert SERIAL not in text, name
    assert "[serial number removed]" in files["report.json"]


def test_a_device_whose_driver_is_missing_still_gets_a_report(engine):
    engine.project.devices.append(
        DeviceConfig(id="orphan", driver="acme_not_installed", name="Orphan"))
    snapshot = pr.Snapshot(engine, "orphan", catalog=None)
    report, redactor = pr.build_report(snapshot)
    assert "no_driver" in {item["id"] for item in report["limits"]}
    files = _members(pr.build_zip(report, redactor, snapshot))
    assert not any(n.startswith("driver/") for n in files)


def test_a_device_not_in_the_project_is_refused(engine):
    with pytest.raises(pr.DeviceMissing):
        pr.Snapshot(engine, "nope", catalog=None)


def test_the_store_keeps_only_problem_reports(tmp_path, engine):
    store = ReportStore(tmp_path, name_pattern=pr.NAME_RE)
    saved = pr.save_report(pr.Snapshot(engine, "hall_widget", None), store)
    assert pr.NAME_RE.match(saved["name"])
    assert saved["name"].startswith("openavc-problem-report-acme-widget-problem-")
    assert store.path(saved["name"]).stat().st_size == saved["size_bytes"]
    assert store.path("openavc-device-audit-x-20260101-0000.zip") is None


# -- routes -------------------------------------------------------------------


@pytest.fixture
def wired(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)
    routes.configure(None, ReportStore(tmp_path, name_pattern=pr.NAME_RE))
    yield engine
    routes.configure(None, None)


def test_preview_lists_what_a_report_would_hold(wired, monkeypatch):
    monkeypatch.setattr(report_upload.cfg, "REPORT_URL", "https://reports.example/api/v1/reports")
    answer = asyncio.run(routes.problem_report_preview("hall_widget"))
    assert answer["device"] == {"id": "hall_widget", "name": "Hall Widget"}
    assert answer["messages"] == 2 and answer["values"] >= 5 and answer["simulated"] is False
    assert answer["report_url"] == "https://reports.example/api/v1/reports"
    assert answer["report_host"] == "reports.example"
    assert answer["max_bytes"] == 20 * 1024 * 1024
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.problem_report_preview("nope"))
    assert exc.value.status_code == 404


def test_create_then_download_then_send_one_file(wired, monkeypatch):
    sent = []

    async def fake_upload(path, kind, report_id, **_):
        sent.append((path.name, kind, report_id))
        return report_upload.UploadResult(True, reference="R-ABC123", status=200)

    monkeypatch.setattr(report_upload, "upload_report", fake_upload)
    body = ProblemReportRequest(message="hi", context=ProblemReportContext(source="driver_line"))
    made = asyncio.run(routes.create_problem_report("hall_widget", body))
    response = asyncio.run(routes.get_problem_report(made["name"]))
    assert response.headers["cache-control"] == "no-store"
    answer = asyncio.run(routes.send_problem_report(made["name"]))
    assert answer["sent"] is True and answer["reference"] == "R-ABC123"
    assert sent == [(made["name"], "problem_report", made["report_id"])]
    with pytest.raises(HTTPException):
        asyncio.run(routes.send_problem_report("openavc-problem-report-gone-20260101-0000.zip"))
