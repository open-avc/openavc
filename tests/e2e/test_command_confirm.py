"""A command that erases or resets the device asks first on the device page.

The driver declares ``confirm`` on the command, with the sentence to ask.
Send Command on the device page shows that sentence before anything goes out:
Cancel sends nothing, Send sends it. A command without ``confirm`` sends at
once. The device is a small fake on loopback that records every line it gets.
"""

from __future__ import annotations

import socketserver
import threading
import time

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000

DRIVER_FILE = "e2e_confirm_widget.avcdriver"
DRIVER_SOURCE = """\
id: e2e_confirm_widget
name: Acme Confirm Widget
manufacturer: Acme
category: utility
version: 1.0.0
author: OpenAVC
description: A widget with a command that resets it.
transport: tcp
default_config:
  host: ""
  port: 23
  poll_interval: 30
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
commands:
  power_on:
    label: Power On
    send: "PWR 1\\r"
  factory_reset:
    label: Factory Reset
    send: "RESET ALL\\r"
    confirm: Erases every preset and returns the unit to DHCP.
responses:
  - match: "PWR=(\\\\w+)"
    set:
      power: "$1"
"""

RESET_SENTENCE = "Erases every preset and returns the unit to DHCP."


class _Widget(socketserver.BaseRequestHandler):
    """Records every line it is sent and answers each with its power."""

    def handle(self) -> None:
        buf = b""
        try:
            while True:
                data = self.request.recv(1024)
                if not data:
                    return
                buf += data
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    self.server.heard.append(line.decode("ascii", "replace"))  # type: ignore[attr-defined]
                    self.request.sendall(b"PWR=on\r")
        except OSError:
            return


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def widget():
    server = _Server(("127.0.0.1", 0), _Widget)
    server.heard = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _pick(page: Page, label: str) -> None:
    page.locator('[data-testid="command-picker"]').click()
    page.get_by_role("option", name=label).click()


def test_a_reset_asks_first_on_the_device_page(server_factory, widget, page: Page):
    port = widget.server_address[1]
    handle = server_factory(
        project_overrides={"devices": [{
            "id": "widget", "driver": "e2e_confirm_widget", "name": "Bench Widget",
            "config": {"host": "127.0.0.1", "port": port, "poll_interval": 30},
            "enabled": True, "pending_settings": {}, "child_entities": {},
        }]},
        drivers={DRIVER_FILE: DRIVER_SOURCE},
    )

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').click(timeout=EXPECT_TIMEOUT)
    page.locator('button:has-text("Bench Widget")').first.click(timeout=EXPECT_TIMEOUT)
    send = page.get_by_role("button", name="Send", exact=True)

    # A command with no confirm goes out at once.
    _pick(page, "Power On")
    send.click(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    deadline = time.monotonic() + 10
    while "PWR 1" not in widget.heard and time.monotonic() < deadline:
        time.sleep(0.1)
    assert "PWR 1" in widget.heard

    # The reset says the driver's own sentence first, and Cancel sends nothing.
    _pick(page, "Factory Reset")
    send.click(timeout=EXPECT_TIMEOUT)
    dialog = page.get_by_role("alertdialog", name="Factory Reset")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog).to_contain_text(RESET_SENTENCE)
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_have_count(0)
    time.sleep(1.0)
    assert "RESET ALL" not in widget.heard

    # Send in the question sends it.
    send.click(timeout=EXPECT_TIMEOUT)
    dialog.get_by_role("button", name="Send").click(timeout=EXPECT_TIMEOUT)
    deadline = time.monotonic() + 10
    while "RESET ALL" not in widget.heard and time.monotonic() < deadline:
        time.sleep(0.1)
    assert widget.heard.count("RESET ALL") == 1
