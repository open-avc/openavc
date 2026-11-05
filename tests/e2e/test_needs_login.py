"""A device whose login is not saved on this system says "Needs login".

The project holds the "saved on this system" marker in the device's password
and the server has no login file, which is a replacement system restored from
a copy that leaves logins out. The device list says "Needs login" on the
device, and its page has a box for the password in place of the Offline
banner. Saving a password there connects the device (a small fake on loopback
that counts its connections), the project on disk keeps the marker, and the
password is never written into it. "Use the driver's default" is offered
because the driver ships one.

A backup restored in Project with the same device names it in a notice that
stays until it is dismissed.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
MARKER = "<saved on this system>"
TYPED = "Hunter2-Acme"

DRIVER_FILE = "e2e_login_widget.avcdriver"
DRIVER_SOURCE = """\
id: e2e_login_widget
name: Acme Login Widget
manufacturer: Acme
category: utility
version: 1.0.0
author: OpenAVC
description: A widget that signs in with a password.
transport: tcp
default_config:
  host: ""
  port: 23
  password: acme-factory
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
  password:
    type: string
    secret: true
    default: acme-factory
    label: Password
delimiter: "\\r"
state_variables:
  power:
    type: boolean
    label: Power
commands:
  power_on:
    label: Power On
    send: "PWR 1\\r"
"""


@pytest.fixture
def widget():
    """A listener standing in for the device: counts the connections it takes."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.settimeout(0.2)
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def accept_loop() -> None:
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            accepted.append(conn)

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    try:
        yield sock.getsockname()[1], accepted
    finally:
        stop.set()
        thread.join(timeout=2)
        for conn in accepted:
            conn.close()
        sock.close()


def _server(server_factory, port: int):
    return server_factory(
        project_overrides={
            "devices": [{
                "id": "widget", "driver": "e2e_login_widget", "name": "Rack Widget",
                "config": {"poll_interval": 30},
                "enabled": True, "pending_settings": {}, "child_entities": {},
            }],
            "connections": {"widget": {"host": "127.0.0.1", "port": port, "password": MARKER}},
        },
        drivers={DRIVER_FILE: DRIVER_SOURCE},
    )


def _open_device(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').click(timeout=EXPECT_TIMEOUT)
    row = page.locator('button:has-text("Rack Widget")').first
    expect(row).to_contain_text("Needs login", timeout=EXPECT_TIMEOUT)
    row.click()


def test_a_login_entered_on_the_device_page_connects_it(server_factory, widget, page: Page):
    port, accepted = widget
    handle = _server(server_factory, port)
    _open_device(page, handle.base_url)

    banner = page.get_by_test_id("needs-login-banner")
    expect(banner).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(banner).to_contain_text("Needs login")
    expect(page.get_by_text("Offline", exact=True)).to_have_count(0)
    expect(banner.get_by_role("button", name="Use the driver's default")).to_be_visible()
    assert accepted == []  # nothing dialed with the marker

    box = banner.get_by_label("Password")
    expect(box).to_have_value("")
    box.fill(TYPED)
    banner.get_by_role("button", name="Save login").click()

    expect(banner).to_have_count(0, timeout=EXPECT_TIMEOUT)
    deadline = time.monotonic() + 10
    while not accepted and time.monotonic() < deadline:
        time.sleep(0.1)
    assert accepted, "the device was not dialed after its login was saved"
    expect(page.locator('button:has-text("Rack Widget")').first).not_to_contain_text("Needs login")

    project_text = handle.project_path.read_text(encoding="utf-8")
    assert TYPED not in project_text
    assert json.loads(project_text)["connections"]["widget"]["password"] == MARKER
    assert (handle.data_dir / "device_logins.json").exists()
    assert TYPED not in (handle.data_dir / "device_logins.json").read_text(encoding="utf-8")


def test_a_restored_backup_names_the_devices_that_need_their_login(server_factory, widget, page: Page):
    port, _ = widget
    handle = _server(server_factory, port)
    req = Request(
        f"{handle.base_url}/api/backups/create",
        data=json.dumps({"reason": "Before the move"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        assert resp.status < 300

    page.goto(f"{handle.base_url}/programmer/#project", wait_until="domcontentloaded")
    row = page.get_by_text("Before the move", exact=True)
    expect(row).to_be_visible(timeout=EXPECT_TIMEOUT)
    row.locator("xpath=../..").get_by_role("button", name="Restore").click()
    page.get_by_role("alertdialog").get_by_role("button", name="Restore").click(timeout=EXPECT_TIMEOUT)

    notice = page.get_by_role("alert").filter(has_text="needs its login entered")
    expect(notice).to_have_text(
        "1 device needs its login entered: Rack Widget. Open it in Devices to enter it.×",
        timeout=EXPECT_TIMEOUT,
    )
    # It stays until it is dismissed.
    time.sleep(4)
    expect(notice).to_be_visible()
    notice.get_by_role("button", name="Dismiss").click()
    expect(notice).to_have_count(0)
