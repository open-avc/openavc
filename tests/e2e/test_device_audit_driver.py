"""A device audit from a device page, through the driver test, to the report.

A project device ("Lobby Display") runs a YAML driver against a small fake
device this test serves on loopback. "Audit this device" on its page opens the
wizard on its address with the pause notice up front; the network check runs;
"Which driver?" starts from the device's own driver; the Connection step
offers its saved settings and shows what connecting sends; Connect brings the
driver up, the status table and the traffic fill in live; on Commands the list
says where each command stands, one is opened and sent, its declared effect is
read back, the person says it happened and the list and the count follow, a
device setting is written, read back and put back, and
"What changed" names what the command changed; and the report's download
carries the driver section, the traffic, the command, the setting, a power
cycle and a cable pull each started and stopped, and the driver file. Finish reconnects the
project device.

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
  display_label:
    type: string
    label: Display label
device_settings:
  display_label:
    type: string
    label: Display label
    state_key: display_label
    write:
      send: "LABEL {value}\\r"
commands:
  set_input:
    label: Set Input
    send: "INPUT {input}\\r"
    params:
      input:
        type: enum
        values: [hdmi1, hdmi2]
        required: true
    sets:
      input: "{input}"
quick_actions: [set_input]
polling:
  queries:
    - "PWR?\\r"
    - "LABEL?\\r"
responses:
  - match: "PWR=(\\\\w+)"
    set:
      power: "$1"
  - match: "INPUT=(\\\\w+)"
    set:
      input: "$1"
  - match: "LABEL=(.+)"
    set:
      display_label: "$1"
"""


class _Widget(socketserver.BaseRequestHandler):
    """Answers each query with its power and a line no rule matches, an input
    change with the input it now has, and keeps a label it can be told."""

    def handle(self) -> None:
        buf = b""
        label = b"Lobby"
        try:
            while True:
                data = self.request.recv(1024)
                if not data:
                    return
                buf += data
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    if line.startswith(b"INPUT "):
                        self.request.sendall(b"INPUT=" + line[6:] + b"\r")
                    elif line.startswith(b"LABEL"):
                        if line.startswith(b"LABEL "):
                            label = line[6:]
                        self.request.sendall(b"LABEL=" + label + b"\r")
                    else:
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
    dialog.get_by_role("button", name="Save and show what connecting sends").click()
    expect(dialog.get_by_text("What connecting sends", exact=True)).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
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
    expect(dialog.get_by_text(
        "Replies that matched none of the driver's rules", exact=False,
    )).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(dialog.get_by_text("Status values (2 of 3 reported)")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Continue", exact=True).click()

    # Commands: one sent, its declared effect read back, and the person's answer.
    expect(dialog.get_by_role("heading", name="Commands", exact=True)).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    # The list says where each command stands, the driver's suggested ones marked;
    # opening one shows what it takes.
    expect(dialog.get_by_text(
        "1 command, none tried yet · Suggested: 0 of 1 tried", exact=True,
    )).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Set Input (suggested): Not tried yet").click()
    dialog.get_by_role("button", name="Select...", exact=True).click()
    page.get_by_role("option", name=re.compile("^hdmi2")).click()
    dialog.get_by_role("button", name="Send", exact=True).click()
    dialog.get_by_role("button", name="Stop watching").click(timeout=EXPECT_TIMEOUT)
    # The widget's polls answer inside the window too, so the sentence goes on.
    expect(dialog.get_by_text(
        re.compile(r"^Input is now hdmi2, as the driver says it should be"),
    )).to_be_visible(timeout=EXPECT_TIMEOUT)
    answers = dialog.get_by_role("group", name="Did Set Input happen?")
    answers.get_by_role("button", name="Yes").click()
    expect(answers.get_by_role("button", name="Yes")).to_have_attribute(
        "aria-pressed", "true", timeout=EXPECT_TIMEOUT,
    )
    expect(dialog.get_by_role("button", name="Set Input (suggested): Worked")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(dialog.get_by_text(
        "1 of 1 command tried, 1 answered · Suggested: 1 of 1 tried", exact=True,
    )).to_be_visible()

    # A device setting: written, read back, put back.
    dialog.get_by_placeholder("New value").fill("Boardroom")
    dialog.get_by_role("button", name="Write Display label, then put it back").click()
    expect(dialog.get_by_text(re.compile(
        r"^Wrote Boardroom to Display label: the device reported it back after .* "
        r"Put Display label back to Lobby: the device reported it back after"
    ))).to_be_visible(timeout=EXPECT_TIMEOUT)

    # What changed: the input, by the command; the label is back as it was.
    expect(dialog.get_by_text(
        "Input: not reported before, hdmi2 now (after 1. Set Input)",
    )).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_text(re.compile(r"^Display label:"))).to_have_count(0)
    dialog.get_by_role("button", name="Continue", exact=True).click()

    # Power and cable: a power cycle started, marked and stopped (the widget
    # stays up, so it ends as stopped, with its sentence).
    expect(dialog.get_by_role("heading", name="Power and cable", exact=True)).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Start the power cycle test").click()
    dialog.get_by_role("button", name="I turned it off").click(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_role("row", name="Turned off yes")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Stop the test").click()
    expect(dialog.get_by_text(re.compile(r"The test was stopped\.$"))).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    # And the cable pull, the same way.
    dialog.get_by_role("button", name="Start the cable pull test").click()
    dialog.get_by_role("button", name="I unplugged it").click(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_role("row", name="Cable out yes")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(dialog.get_by_role("row", name=re.compile(r"^OpenAVC noticed not yet"))).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Stop the test").click()
    expect(dialog.get_by_text(re.compile(r"The test was stopped\.$"))).to_have_count(
        2, timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="Continue", exact=True).click()

    # The report says what the driver did.
    expect(dialog.get_by_role("heading", name="Report")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_role("row", name=re.compile(r"^Driver Acme Audit Widget 1\.0\.0"))).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    # The command set the input, so the device has now reported every value.
    expect(dialog.get_by_role("row", name="Status values 3 of 3 reported")).to_be_visible()
    expect(dialog.get_by_role("row", name="Commands sent 1")).to_be_visible()
    expect(dialog.get_by_role("row", name="Did it happen 1 yes")).to_be_visible()
    expect(dialog.get_by_role("row", name="Settings written 1 (1 read back, 1 put back)")).to_be_visible()
    expect(dialog.get_by_role("row", name="Values changed Input")).to_be_visible()

    with page.expect_download(timeout=EXPECT_TIMEOUT) as info:
        dialog.get_by_role("button", name="Download report").click()
    download = info.value
    assert download.suggested_filename.startswith("openavc-device-audit-acme-w-100-")
    saved = tmp_path / download.suggested_filename
    download.save_as(saved)
    with zipfile.ZipFile(io.BytesIO(saved.read_bytes())) as zf:
        assert set(zf.namelist()) == {
            "summary.html", "report.json", "timeline.txt", "log.txt",
            f"driver/{DRIVER_FILE}",
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
    assert table["input"]["value"] == "hdmi2"
    assert "no_driver_tested" not in [x["id"] for x in report["limits"]]
    (trial,) = section["commands"]["trials"]
    assert trial["command"] == "set_input" and trial["params"] == {"input": "hdmi2"}
    assert [e["outcome"] for e in trial["effects"]] == ["confirmed"]
    assert trial["answer"]["answer"] == "yes"
    assert [bytes.fromhex(e["hex"]) for e in trial["traffic"]["entries"]
            if e["direction"] == "tx"][:1] == [b"INPUT hdmi2\r"]
    assert [c["key"] for c in section["commands"]["changed"]] == ["input"]
    cycle, pull = section["outages"]
    assert cycle["kind"] == "power_cycle" and cycle["status"] == "stopped"
    assert pull["kind"] == "cable_pull" and pull["status"] == "stopped"
    assert cycle["off_at"] is not None and pull["off_at"] is not None
    (setting,) = section["settings"]["trials"]
    assert setting["write"]["confirmed"] and setting["restore"]["confirmed"]
    assert setting["original"] == "Lobby" and setting["value"] == "Boardroom"
    assert re.search(r'tx tcp +"PWR\?\\r"', timeline)

    # Finish ends the audit and the project device comes back.
    dialog.get_by_role("button", name="Finish").click()
    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
    assert _get(base, "/api/audit/sessions/current")["session"] is None
    expect(page.get_by_text("Paused for driver testing")).to_be_hidden(timeout=EXPECT_TIMEOUT)
