"""A device audit from a device page, through the driver test, to the report.

A project device ("Lobby Display") runs a YAML driver against a small fake
device this test serves on loopback. "Audit this device" on its page opens the
wizard on its address with the pause notice up front; the network check runs;
"Which driver?" starts from the device's own driver; the Connection step
offers its saved settings and shows what connecting sends; Connect brings the
driver up, the status table and the traffic fill in live; and the report's
download carries the driver section, the traffic, and the driver file. Finish
reconnects the project device.

Slow for the same reason as ``test_device_audit.py``: the network check runs
for real, listening for a minute from the session's start.
"""

from __future__ import annotations

import io
import json
import re
import socketserver
import threading
import zipfile
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
#: The listening window (60 s) plus the check's own steps, with room to spare.
CHECK_TIMEOUT = 180_000

DRIVER_FILE = "e2e_audit_widget.avcdriver"
DRIVER_SOURCE = """\
id: e2e_audit_widget
name: Acme Audit Widget
manufacturer: Acme
category: utility
version: 1.0.0
author: OpenAVC
description: A widget the device audit's browser test talks to.
transport: tcp
compatible_models:
  - manufacturer: Acme
    models:
      - W-100
    confidence: untested
default_config:
  host: ""
  port: 23
  poll_interval: 1
config_schema:
  host:
    type: string
    required: true
    label: IP Address
  port:
    type: integer
    default: 23
    label: Port
delimiter: "\\r"
state_variables:
  power:
    type: boolean
    label: Power
  input:
    type: string
    label: Input
commands: {}
polling:
  queries:
    - "PWR?\\r"
responses:
  - match: "PWR=(\\\\w+)"
    set:
      power: "$1"
  - match: "INPUT=(\\\\w+)"
    set:
      input: "$1"
"""


class _Widget(socketserver.BaseRequestHandler):
    """Answers each query with its power and a line no rule matches."""

    def handle(self) -> None:
        buf = b""
        try:
            while True:
                data = self.request.recv(1024)
                if not data:
                    return
                buf += data
                while b"\r" in buf:
                    _line, buf = buf.split(b"\r", 1)
                    self.request.sendall(b"PWR=on\rLAMP=450\r")
        except OSError:
            return


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def widget():
    server = _Server(("127.0.0.1", 0), _Widget)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _lobby(port: int) -> dict:
    return {
        "id": "lobby", "driver": "e2e_audit_widget", "name": "Lobby Display",
        "config": {"host": "127.0.0.1", "port": port, "poll_interval": 1},
        "enabled": True, "pending_settings": {}, "child_entities": {},
    }


def _get(base: str, path: str) -> dict:
    with urlopen(f"{base}{path}", timeout=10) as resp:
        return json.loads(resp.read())


def test_an_audit_from_a_device_page_tests_its_driver_and_reports_it(
    server_factory, widget, page: Page, tmp_path,
) -> None:
    handle = server_factory(
        project_overrides={"devices": [_lobby(widget)]},
        drivers={DRIVER_FILE: DRIVER_SOURCE},
    )
    base = handle.base_url

    page.goto(f"{base}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').click(timeout=EXPECT_TIMEOUT)
    page.locator('button:has-text("Lobby Display")').first.click(timeout=EXPECT_TIMEOUT)
    page.get_by_role("button", name="Audit this device").click(timeout=EXPECT_TIMEOUT)

    dialog = page.get_by_role("dialog", name="Audit a device")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_label("IP address or host name")).to_have_value("127.0.0.1")
    # The device it came from is paused, and the notice says so before anything is sent.
    expect(dialog.get_by_text(
        "Lobby Display in this project uses this device. OpenAVC pauses it while the "
        "audit runs and reconnects it when you finish."
    )).to_be_visible(timeout=EXPECT_TIMEOUT)
    dialog.get_by_role("button", name="Pause and continue").click()

    proceed = dialog.get_by_role("button", name="Continue")
    expect(proceed).to_be_enabled(timeout=CHECK_TIMEOUT)
    proceed.click()

    # Which driver: the one the device uses, already chosen.
    expect(dialog.get_by_text("The driver Lobby Display uses is selected below.")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    widget_radio = dialog.get_by_role("radio", name=re.compile("Acme Audit Widget"))
    expect(widget_radio).to_be_checked()
    # The project does not record the model; the person picks it, then the driver.
    dialog.get_by_label("Model", exact=True).select_option("W-100")
    widget_radio.check()
    dialog.get_by_label("Firmware version (optional)").fill("2.04")
    dialog.get_by_role("button", name="Continue", exact=True).click()

    # The connection: its saved settings, and what connecting sends.
    expect(dialog.get_by_role("heading", name="Connection", exact=True)).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(dialog.get_by_label("Use Lobby Display's saved settings")).to_be_checked(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Show what connecting sends").click()
    expect(dialog.get_by_text("What connecting sends")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_text('"PWR?\\r"')).to_be_visible()
    dialog.get_by_role("button", name="Continue", exact=True).click()

    # Connect and listen: the driver comes up, values and traffic arrive live.
    expect(dialog.get_by_role("heading", name="Connect and listen")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Connect", exact=True).click()
    expect(dialog.get_by_text(re.compile(r"^Connected\. Listening"))).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    traffic = dialog.get_by_role("log", name="Traffic")
    expect(traffic.get_by_text('"PWR?\\r"').first).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(traffic.get_by_text('"LAMP=450"').first).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_text("Replies no response rule matched", exact=False)).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(dialog.get_by_text("Status values (1 of 2 reported)")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Continue", exact=True).click()

    # The report says what the driver did.
    expect(dialog.get_by_role("heading", name="Report")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_role("row", name=re.compile(r"^Driver Acme Audit Widget 1\.0\.0"))).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(dialog.get_by_role("row", name="Status values 1 of 2 reported")).to_be_visible()

    with page.expect_download(timeout=EXPECT_TIMEOUT) as info:
        dialog.get_by_role("button", name="Download report").click()
    download = info.value
    assert download.suggested_filename.startswith("openavc-device-audit-acme-w-100-")
    saved = tmp_path / download.suggested_filename
    download.save_as(saved)
    with zipfile.ZipFile(io.BytesIO(saved.read_bytes())) as zf:
        assert set(zf.namelist()) == {
            "summary.html", "report.json", "timeline.txt", f"driver/{DRIVER_FILE}",
        }
        report = json.loads(zf.read("report.json"))
        timeline = zf.read("timeline.txt").decode("utf-8")
        assert zf.read(f"driver/{DRIVER_FILE}").decode("utf-8") == DRIVER_SOURCE
    assert report["session"]["origin"]["device_id"] == "lobby"
    assert report["device"]["entered"] == {
        "manufacturer": "Acme", "model": "W-100", "firmware": "2.04",
    }
    (section,) = report["drivers"]
    assert section["driver"]["id"] == "e2e_audit_widget"
    assert section["connection"]["saved_from"] == "Lobby Display"
    (attempt,) = section["attempts"]
    assert attempt["connected_at"] is not None
    sent = [
        bytes.fromhex(e["hex"]) for e in attempt["traffic"]["entries"]
        if e["direction"] == "tx" and e["channel"] == "tcp"
    ]
    assert b"PWR?\r" in sent
    table = {v["name"]: v for v in attempt["status_table"]["variables"]}
    assert table["power"]["value"] is True
    assert table["input"]["reported"] is False
    assert "no_driver_tested" not in [x["id"] for x in report["limits"]]
    assert re.search(r'tx tcp +"PWR\?\\r"', timeline)

    # Finish ends the audit and the project device comes back.
    dialog.get_by_role("button", name="Finish").click()
    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
    assert _get(base, "/api/audit/sessions/current")["session"] is None
    expect(page.get_by_text("Paused for driver testing")).to_be_hidden(timeout=EXPECT_TIMEOUT)
