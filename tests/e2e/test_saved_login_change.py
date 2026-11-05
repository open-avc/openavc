"""A saved login never reaches the browser, and Edit Device changes it.

The project starts with a device's password and API key in it, which the
first start moves into this system's login file. Edit Device then shows each
as "Saved on this system" with Change. A password typed after Change replaces
the saved one and the API key is kept; Change left empty keeps the saved key.
A duplicate of the device asks for its own password, and once it is added the
Programmer holds the marker for it, not what was typed: opening it again
shows "Saved on this system".

Through all of it, no response or WebSocket message the browser receives holds
any of the logins, the ones typed included.
"""

from __future__ import annotations

import json
import socket
import time

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
MARKER = "<saved on this system>"
DRIVER_ID = "e2e_saved_login_widget"
ORIGINAL_PASSWORD = "Original-Acme-1"
ORIGINAL_KEY = "Original-Key-1"
CHANGED_PASSWORD = "Changed-Acme-2"
COPY_PASSWORD = "Copy-Acme-3"
LOGINS = (ORIGINAL_PASSWORD, ORIGINAL_KEY, CHANGED_PASSWORD, COPY_PASSWORD)

DRIVER_FILE = f"{DRIVER_ID}.avcdriver"
DRIVER_SOURCE = f"""\
id: {DRIVER_ID}
name: Acme Saved Login Widget
manufacturer: Acme
category: utility
version: 1.0.0
author: OpenAVC
description: A widget that signs in with a password and an API key.
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
  password:
    type: string
    secret: true
    label: Password
  api_key:
    type: string
    secret: true
    label: API Key
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


def _closed_port() -> int:
    """A loopback port nothing listens on: the device is refused at once."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _server(server_factory):
    return server_factory(
        project_overrides={
            "devices": [{
                "id": "widget", "driver": DRIVER_ID, "name": "Rack Widget",
                "config": {"poll_interval": 30, "api_key": ORIGINAL_KEY},
                "enabled": True, "pending_settings": {}, "child_entities": {},
            }],
            "connections": {
                "widget": {"host": "127.0.0.1", "port": _closed_port(), "password": ORIGINAL_PASSWORD},
            },
        },
        drivers={DRIVER_FILE: DRIVER_SOURCE},
    )


def _saved(data_dir, device_id: str, field: str) -> str | None:
    from openavc.core.device_logins import DeviceLogins

    return DeviceLogins(data_dir).login(device_id, DRIVER_ID, field)


def _wait_saved(data_dir, device_id: str, field: str, value: str) -> None:
    deadline = time.monotonic() + 10
    while _saved(data_dir, device_id, field) != value and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _saved(data_dir, device_id, field) == value


class _Received:
    """Everything the browser is sent: HTTP response bodies and WebSocket frames."""

    def __init__(self, page: Page) -> None:
        self.responses: list = []
        self.frames: list[str] = []
        page.on("response", lambda response: self.responses.append(response))
        page.on("websocket", lambda ws: ws.on("framereceived", self._frame))

    def _frame(self, payload) -> None:
        self.frames.append(payload if isinstance(payload, str) else payload.decode("utf-8", "replace"))

    def assert_no_login(self) -> None:
        bodies: list[str] = []
        for response in self.responses:
            try:
                bodies.append(response.text())
            except Exception:  # a body the browser did not keep (a redirect, a stream)
                continue
        # The project was read and scanned, not skipped: its body holds the marker.
        assert any(MARKER in body for body in bodies), "no project body was read"
        for text in (*bodies, *self.frames):
            for login in LOGINS:
                assert login not in text, f"the browser was sent a login ({login})"


def _open_device(page: Page, base_url: str, name: str) -> None:
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').click(timeout=EXPECT_TIMEOUT)
    page.locator(f'button:has-text("{name}")').first.click(timeout=EXPECT_TIMEOUT)


def _edit(page: Page):
    page.get_by_role("button", name="Edit", exact=True).click(timeout=EXPECT_TIMEOUT)
    dialog = page.get_by_role("dialog", name="Edit Device")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    return dialog


def test_a_saved_login_is_changed_without_reaching_the_browser(server_factory, page: Page):
    handle = _server(server_factory)
    received = _Received(page)
    project = json.loads(handle.project_path.read_text(encoding="utf-8"))
    assert project["connections"]["widget"]["password"] == MARKER
    assert _saved(handle.data_dir, "widget", "password") == ORIGINAL_PASSWORD

    _open_device(page, handle.base_url, "Rack Widget")
    dialog = _edit(page)
    expect(dialog.get_by_text("Saved on this system")).to_have_count(2, timeout=EXPECT_TIMEOUT)
    expect(dialog.locator("input[type=password]")).to_have_count(0)

    dialog.get_by_role("button", name="Change Password").click()
    box = dialog.get_by_label("Password", exact=True)
    expect(box).to_be_focused()
    expect(box).to_have_value("")
    box.fill(CHANGED_PASSWORD)
    dialog.get_by_role("button", name="Save Changes").click()
    expect(dialog).to_have_count(0, timeout=EXPECT_TIMEOUT)

    _wait_saved(handle.data_dir, "widget", "password", CHANGED_PASSWORD)
    assert _saved(handle.data_dir, "widget", "api_key") == ORIGINAL_KEY

    # Change left empty keeps the saved key.
    dialog = _edit(page)
    expect(dialog.get_by_text("Saved on this system")).to_have_count(2, timeout=EXPECT_TIMEOUT)
    dialog.get_by_role("button", name="Change API Key").click()
    expect(dialog.get_by_label("API Key", exact=True)).to_have_attribute(
        "placeholder", "Leave blank to keep the saved one",
    )
    dialog.get_by_role("button", name="Save Changes").click()
    expect(dialog).to_have_count(0, timeout=EXPECT_TIMEOUT)
    time.sleep(1)
    assert _saved(handle.data_dir, "widget", "api_key") == ORIGINAL_KEY
    assert _saved(handle.data_dir, "widget", "password") == CHANGED_PASSWORD

    text = handle.project_path.read_text(encoding="utf-8")
    for login in LOGINS:
        assert login not in text
    saved = json.loads(text)
    assert saved["connections"]["widget"]["password"] == MARKER
    assert next(d for d in saved["devices"] if d["id"] == "widget")["config"]["api_key"] == MARKER

    received.assert_no_login()


def test_a_copy_asks_for_its_own_login_and_the_browser_keeps_none(server_factory, page: Page):
    handle = _server(server_factory)
    received = _Received(page)
    _open_device(page, handle.base_url, "Rack Widget")

    page.get_by_role("button", name="Duplicate", exact=True).click(timeout=EXPECT_TIMEOUT)
    dialog = page.get_by_role("dialog", name="Add Device")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_label("Password", exact=True)).to_have_value("", timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_label("API Key", exact=True)).to_have_value("")
    expect(dialog.get_by_text("Saved on this system")).to_have_count(0)

    dialog.get_by_label("Device ID").fill("widget_copy")
    dialog.get_by_label("Password", exact=True).fill(COPY_PASSWORD)
    with page.expect_response(
        lambda r: r.url.endswith("/api/project") and r.request.method == "GET",
        timeout=EXPECT_TIMEOUT,
    ):
        dialog.get_by_role("button", name="Duplicate Device").click()
    expect(dialog).to_have_count(0, timeout=EXPECT_TIMEOUT)

    _wait_saved(handle.data_dir, "widget_copy", "password", COPY_PASSWORD)
    assert _saved(handle.data_dir, "widget_copy", "api_key") is None
    # The copy did not take the source's saved key.
    assert _saved(handle.data_dir, "widget", "api_key") == ORIGINAL_KEY

    # The Programmer holds the marker for the copy, not what was typed.
    page.locator('button:has-text("Rack Widget (Copy)")').first.click(timeout=EXPECT_TIMEOUT)
    dialog = _edit(page)
    expect(dialog.get_by_text("Saved on this system")).to_have_count(1, timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_role("button", name="Change Password")).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()

    received.assert_no_login()
