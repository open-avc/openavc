"""Report a problem, driven through the Programmer against a stand-in report service.

The server is pointed at a small HTTP service this test runs on loopback
(``OPENAVC_REPORT_URL``), which answers the way the real one does and keeps
every request it got, so a test can say which side sent the report: the
OpenAVC server (no ``Origin`` header) or the browser (an ``Origin``).

- From a device's driver line: the dialog lists what the report includes, Send
  delivers it from the server, Download a copy hands over the same file, and
  the name and email come back next time.
- When the server's send fails as a network failure, the browser sends the
  same file itself.
- A final answer (too many reports) is shown and never tried again from the
  browser.
- From the Offline banner of a device that refuses its connection, and then
  Run a Device Audit from the Sent screen: the audit's report names the
  problem report it follows.
- On the Device Audit's last step: Send to OpenAVC sends the audit report.
"""

from __future__ import annotations

import io
import json
import re
import socket
import threading
import zipfile
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
#: The preview reads the driver library, which can take a while with no internet.
PREVIEW_TIMEOUT = 30_000
#: The audit's listening window (60 s) plus the check's own steps.
CHECK_TIMEOUT = 180_000


class _ReportService:
    """A stand-in for the report service: one route, CORS on every answer."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        #: "ok", "server_blocked" (500 to the server, 200 to a browser) or "rate_limited".
        self.mode = "ok"
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def _answer(self, status: int, body: dict[str, Any] | None = None) -> None:
                data = json.dumps(body or {}).encode()
                self.send_response(status)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._answer(204)

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                message = BytesParser(policy=email_policy).parsebytes(
                    b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body,
                )
                fields: dict[str, Any] = {}
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    fields[name] = part.get_payload(decode=True)
                origin = self.headers.get("Origin")
                service.received.append({"origin": origin, "fields": fields})
                if service.mode == "rate_limited":
                    self._answer(429, {"detail": "Too many reports."})
                elif service.mode == "server_blocked" and not origin:
                    self._answer(500, {"detail": "Unavailable."})
                else:
                    self._answer(200, {"reference": "R-BRWSR1" if origin else "R-SRVR01"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/api/v1/reports"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def report_service():
    service = _ReportService()
    yield service
    service.close()


def _report_json(zip_bytes: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return json.loads(zf.read("report.json"))


def _open_report(page: Page, base: str, device_name: str):
    page.goto(f"{base}/programmer/#devices", wait_until="domcontentloaded")
    page.locator(f'button:has-text("{device_name}")').first.click()
    page.get_by_role("button", name="Report a problem", exact=True).first.click()
    dialog = page.get_by_role("dialog", name=f"Report a problem with {device_name}")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    return dialog


def _wait_for_preview(dialog) -> None:
    dialog.get_by_text("What's included").click()
    expect(dialog.get_by_text("Nothing about your other devices or the rest of the project")).to_be_visible(
        timeout=PREVIEW_TIMEOUT,
    )


def test_a_report_from_the_driver_line_is_sent_by_the_server(
    server_factory, report_service, page: Page, tmp_path,
) -> None:
    handle = server_factory(env={"OPENAVC_REPORT_URL": report_service.url})
    dialog = _open_report(page, handle.base_url, "Test Controller")
    _wait_for_preview(dialog)
    expect(dialog.get_by_text("This device's settings, with passwords hidden")).to_be_visible()
    expect(dialog.get_by_text(re.compile(r"^OpenAVC \S+ on \S+"))).to_be_visible()

    dialog.get_by_label("What happened?").fill("The encoder count never changes.")
    dialog.get_by_label("Your name (optional)").fill("Browser Test")
    dialog.get_by_label("Your email (optional)").fill("browser@example.com")
    dialog.get_by_role("button", name="Send to OpenAVC").click()
    expect(dialog.get_by_text("Sent. Your reference is R-SRVR01.")).to_be_visible(timeout=EXPECT_TIMEOUT)

    assert len(report_service.received) == 1
    sent = report_service.received[0]
    assert sent["origin"] is None  # the OpenAVC server sent it
    report = _report_json(sent["fields"]["file"])
    assert sent["fields"]["kind"] == b"problem_report"
    assert sent["fields"]["report_id"].decode() == report["report_id"]
    assert report["sender"]["message"] == "The encoder count never changes."
    assert report["sender"]["email"] == "browser@example.com"
    assert report["context"]["source"] == "driver_line"

    with page.expect_download(timeout=EXPECT_TIMEOUT) as info:
        dialog.get_by_role("button", name="Download a copy").click()
    saved = tmp_path / info.value.suggested_filename
    info.value.save_as(saved)
    assert saved.name.startswith("openavc-problem-report-") and saved.name.endswith(".zip")
    assert saved.read_bytes() == sent["fields"]["file"]
    expect(dialog.get_by_text(f"Saved {saved.name}.")).to_be_visible(timeout=EXPECT_TIMEOUT)

    dialog.get_by_role("button", name="Close", exact=True).last.click()
    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
    page.get_by_role("button", name="Report a problem", exact=True).first.click()
    again = page.get_by_role("dialog", name="Report a problem with Test Controller")
    expect(again.get_by_label("Your name (optional)")).to_have_value("Browser Test")
    expect(again.get_by_label("Your email (optional)")).to_have_value("browser@example.com")
    expect(again.get_by_label("What happened?")).to_have_value("")


def test_the_browser_sends_the_same_file_when_the_server_cannot(
    server_factory, report_service, page: Page,
) -> None:
    report_service.mode = "server_blocked"
    handle = server_factory(env={"OPENAVC_REPORT_URL": report_service.url})
    dialog = _open_report(page, handle.base_url, "Test Controller")
    _wait_for_preview(dialog)
    dialog.get_by_role("button", name="Send to OpenAVC").click()
    expect(dialog.get_by_text("Sent. Your reference is R-BRWSR1.")).to_be_visible(timeout=EXPECT_TIMEOUT)

    server_try, browser_try = report_service.received
    assert server_try["origin"] is None
    assert browser_try["origin"] == handle.base_url
    assert browser_try["fields"]["report_id"] == server_try["fields"]["report_id"]
    assert browser_try["fields"]["file"] == server_try["fields"]["file"]
    assert browser_try["fields"]["kind"] == b"problem_report"


def test_a_final_answer_is_not_tried_again_from_the_browser(
    server_factory, report_service, page: Page,
) -> None:
    report_service.mode = "rate_limited"
    handle = server_factory(env={"OPENAVC_REPORT_URL": report_service.url})
    dialog = _open_report(page, handle.base_url, "Test Controller")
    _wait_for_preview(dialog)
    dialog.get_by_role("button", name="Send to OpenAVC").click()
    expect(dialog.get_by_text(
        "Too many reports have been sent from this network recently. Download it and email it "
        "to support@openavc.com, or send it again later."
    )).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_role("button", name="Send to OpenAVC")).to_be_hidden()
    expect(dialog.get_by_role("button", name="Download", exact=True)).to_be_enabled()
    page.wait_for_timeout(1_000)
    assert [r["origin"] for r in report_service.received] == [None]


def _closed_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_the_offline_banner_reports_and_the_audit_follows_the_report(
    server_factory, report_service, page: Page,
) -> None:
    handle = server_factory(
        env={"OPENAVC_REPORT_URL": report_service.url},
        project_overrides={
            "devices": [{
                "id": "hall_display", "driver": "generic_tcp", "name": "Hall Display",
                "config": {}, "enabled": True, "pending_settings": {}, "child_entities": {},
            }],
            "connections": {"hall_display": {"host": "127.0.0.1", "port": _closed_port()}},
        },
    )
    base = handle.base_url
    page.goto(f"{base}/programmer/#devices", wait_until="domcontentloaded")
    page.locator('button:has-text("Hall Display")').first.click()
    banner_pill = page.locator("div", has_text=re.compile(r"^Offline")).get_by_role(
        "button", name="Report a problem",
    ).last
    expect(banner_pill).to_be_visible(timeout=30_000)
    banner_pill.click()
    dialog = page.get_by_role("dialog", name="Report a problem with Hall Display")
    expect(dialog.get_by_text(re.compile(r"^Offline\. "))).to_be_visible(timeout=EXPECT_TIMEOUT)
    _wait_for_preview(dialog)
    dialog.get_by_role("button", name="Send to OpenAVC").click()
    expect(dialog.get_by_text("Sent. Your reference is R-SRVR01.")).to_be_visible(timeout=EXPECT_TIMEOUT)
    report = _report_json(report_service.received[0]["fields"]["file"])
    assert report["context"]["source"] == "offline_banner"
    assert report["state"]["device"]["offline_reason"] == "connection_refused"

    dialog.get_by_role("button", name="Run a Device Audit").click(timeout=EXPECT_TIMEOUT)
    wizard = page.get_by_role("dialog", name="Audit a device")
    expect(wizard).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(wizard.get_by_label("IP address or host name")).to_have_value("127.0.0.1")
    wizard.get_by_role("button", name="Pause and continue").click(timeout=EXPECT_TIMEOUT)
    expect(wizard.get_by_text("Checking the address")).to_be_visible(timeout=EXPECT_TIMEOUT)

    with urlopen(f"{base}/api/audit/sessions/current", timeout=10) as resp:
        session_id = json.loads(resp.read())["session"]["session_id"]
    with urlopen(f"{base}/api/audit/sessions/{session_id}/report?format=json", timeout=30) as resp:
        audit_report = json.loads(resp.read())
    assert audit_report["session"]["follows_report"] == {
        "report_id": report["report_id"], "reference": "R-SRVR01",
    }
    with urlopen(Request(f"{base}/api/audit/sessions/{session_id}?cancel=true", method="DELETE"), timeout=30):
        pass


def test_an_audit_report_is_sent_from_its_last_step(
    server_factory, report_service, page: Page,
) -> None:
    handle = server_factory(env={"OPENAVC_REPORT_URL": report_service.url})
    base = handle.base_url
    page.goto(f"{base}/programmer/#devices", wait_until="domcontentloaded")
    page.get_by_role("tab", name="Drivers", exact=True).click()
    page.get_by_role("button", name="Audit a Device", exact=True).click()
    dialog = page.get_by_role("dialog", name="Audit a device")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    dialog.get_by_label("IP address or host name").fill("127.0.0.1")
    dialog.get_by_role("button", name="Start the network check").click()
    proceed = dialog.get_by_role("button", name="Continue")
    expect(proceed).to_be_enabled(timeout=CHECK_TIMEOUT)
    proceed.click()
    dialog.get_by_role("button", name="No driver yet: skip to the report").click(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_text("Send to OpenAVC sends this report to the OpenAVC team.", exact=False)).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_label("Name", exact=True).fill("Browser Test")
    dialog.get_by_role("button", name="Send to OpenAVC").click()
    expect(dialog.get_by_text("Sent. Your reference is R-SRVR01.")).to_be_visible(timeout=EXPECT_TIMEOUT)

    sent = report_service.received[-1]
    assert sent["origin"] is None
    assert sent["fields"]["kind"] == b"device_audit"
    report = _report_json(sent["fields"]["file"])
    assert report["report_kind"] == "device_audit"
    assert sent["fields"]["report_id"].decode() == report["report_id"]
    assert report["session"]["tester"]["name"] == "Browser Test"
    expect(dialog.get_by_role("button", name="Download report")).to_be_enabled()
    dialog.get_by_role("button", name="Finish").click()
    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
