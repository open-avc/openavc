"""The upload door: one send, the answer classified, nothing on the network.

Every request goes to an ``httpx.MockTransport``; the default report URL is a
real service, so no test may reach it.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
import zipfile
from email.parser import BytesParser
from email.policy import HTTP

import httpx
import pytest

from openavc.support import report_upload as ru

REPORT_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _url(monkeypatch):
    monkeypatch.setattr(ru.cfg, "REPORT_URL", "https://reports.example/api/v1/reports")
    monkeypatch.setattr(ru.cfg, "CLOUD_ENABLED", False)


def _zip(tmp_path, report: dict | None = None, name="openavc-problem-report-x-20260101-0000.zip"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("report.json", json.dumps(report if report is not None else {
            "report_version": 1, "report_kind": "problem_report", "report_id": REPORT_ID,
        }))
    path = tmp_path / name
    path.write_bytes(buf.getvalue())
    return path


def _send(path, handler):
    return asyncio.run(ru.upload_report(
        path, "problem_report", REPORT_ID, transport=httpx.MockTransport(handler),
    ))


def _form(request: httpx.Request) -> dict[str, bytes]:
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(head + request.content)
    return {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
            for part in message.iter_parts()}


def test_a_stored_report_answers_its_reference_and_the_form_is_complete(tmp_path):
    path = _zip(tmp_path)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"reference": "R-7K3QXM"})

    result = _send(path, handler)
    assert result == ru.UploadResult(True, reference="R-7K3QXM", status=200)
    form = _form(seen[0])
    assert form["file"] == path.read_bytes()
    assert form["report_id"].decode() == REPORT_ID
    assert form["kind"] == b"problem_report"
    assert form["openavc_version"]
    assert "x-signature" not in seen[0].headers


@pytest.mark.parametrize("status, failure", [
    (400, ru.REFUSED), (413, ru.TOO_LARGE), (429, ru.RATE_LIMITED), (507, ru.STORAGE_FULL),
    (404, ru.NOT_CONFIGURED), (405, ru.NOT_CONFIGURED), (500, ru.NETWORK), (502, ru.NETWORK),
])
def test_each_answer_is_classified_and_sent_once(tmp_path, status, failure):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"detail": "A sentence."})

    result = _send(_zip(tmp_path), handler)
    assert (result.sent, result.failure, result.status) == (False, failure, status)
    assert len(calls) == 1


@pytest.mark.parametrize("error", [
    httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
    httpx.ConnectError("Name or service not known"),
    httpx.ConnectTimeout("timed out"),
    httpx.ReadTimeout("timed out"),
    httpx.ProxyError("407 Proxy Authentication Required"),
])
def test_a_network_failure_says_try_another_way(tmp_path, error):
    def handler(request):
        raise error

    assert _send(_zip(tmp_path), handler) == ru.UploadResult(False, failure=ru.NETWORK)


def test_a_file_over_the_limit_is_refused_before_any_connection(tmp_path, monkeypatch):
    path = _zip(tmp_path)
    monkeypatch.setattr(ru, "MAX_REPORT_BYTES", path.stat().st_size - 1)

    def handler(request):
        raise AssertionError("no request may be made")

    assert _send(path, handler) == ru.UploadResult(False, failure=ru.TOO_LARGE)


def test_the_real_limit_is_the_services_file_cap():
    assert ru.MAX_REPORT_BYTES == 20 * 1024 * 1024


def test_sending_is_off_without_a_url(tmp_path, monkeypatch):
    monkeypatch.setattr(ru.cfg, "REPORT_URL", "")
    assert _send(_zip(tmp_path), lambda r: httpx.Response(200)) == ru.UploadResult(
        False, failure=ru.NOT_CONFIGURED,
    )


def _pair(monkeypatch):
    monkeypatch.setattr(ru.cfg, "CLOUD_ENABLED", True)
    monkeypatch.setattr(ru.cfg, "CLOUD_SYSTEM_ID", "sys-123")
    monkeypatch.setattr(ru.cfg, "CLOUD_SYSTEM_KEY", "00" * 32)


def test_a_paired_system_signs_the_exact_bytes_it_sends(tmp_path, monkeypatch):
    from openavc.cloud.crypto import compute_hmac, derive_auth_key

    _pair(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"reference": "R-AAAAAA"})

    assert _send(_zip(tmp_path), handler).sent
    request = seen[0]
    assert request.headers["x-system-id"] == "sys-123"
    message = ("sys-123" + request.headers["x-timestamp"]
               + hashlib.sha256(request.content).hexdigest()).encode()
    expected = compute_hmac(derive_auth_key(bytes(32), "sys-123"), message)
    assert request.headers["x-signature"] == expected


def test_a_refused_signature_is_sent_once_more_unsigned(tmp_path, monkeypatch):
    _pair(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request)
        if "x-signature" in request.headers:
            return httpx.Response(401, json={"detail": "Timestamp too old"})
        return httpx.Response(200, json={"reference": "R-BBBBBB"})

    result = _send(_zip(tmp_path), handler)
    assert result == ru.UploadResult(True, reference="R-BBBBBB", status=200)
    assert len(seen) == 2 and "x-signature" not in seen[1].headers
    assert seen[1].content == seen[0].content


def test_an_unsigned_401_is_final(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401)

    assert _send(_zip(tmp_path), handler).failure == ru.REFUSED
    assert len(calls) == 1


def test_report_identity_reads_kind_and_id_from_the_zip(tmp_path):
    assert ru.report_identity(_zip(tmp_path)) == ("problem_report", REPORT_ID)
    old_audit = _zip(tmp_path, {"report_version": 1, "report_id": REPORT_ID}, name="a.zip")
    assert ru.report_identity(old_audit) == ("device_audit", REPORT_ID)
    with pytest.raises(ru.ReportUnreadable):
        ru.report_identity(_zip(tmp_path, {"report_kind": "device_audit"}, name="b.zip"))
    with pytest.raises(ru.ReportUnreadable):
        ru.report_identity(_zip(tmp_path, {"report_kind": "other", "report_id": REPORT_ID}, name="c.zip"))
    (tmp_path / "d.zip").write_bytes(b"not a zip")
    with pytest.raises(ru.ReportUnreadable):
        ru.report_identity(tmp_path / "d.zip")

