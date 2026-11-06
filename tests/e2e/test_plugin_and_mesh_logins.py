"""A plugin's logins and the mesh key never reach the browser, and both pages
change them.

The project starts with a plugin's password and API key and the mesh key in
it, which the first start moves into this system's login file. The plugin's
settings show each login as "Saved on this system" with Change; a password
typed after Change goes with its own Save, the API key is kept, and Cancel
sends nothing. The Inter-System page shows the mesh key the same way, with
Show, which fetches it only when pressed, and Change.

Until Show is pressed, no response or WebSocket message the browser receives
holds any of the logins, the ones typed included.
"""

from __future__ import annotations

import json
import time

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
MARKER = "<saved on this system>"
PLUGIN_ID = "e2e_acme_bridge"
ORIGINAL_PASSWORD = "Bridge-Original-1"
ORIGINAL_KEY = "Bridge-Key-1"
CHANGED_PASSWORD = "Bridge-Changed-2"
ORIGINAL_MESH = "Mesh-Original-1"
CHANGED_MESH = "Mesh-Changed-2"
PLUGIN_LOGINS = (ORIGINAL_PASSWORD, ORIGINAL_KEY, CHANGED_PASSWORD)

PLUGIN_SOURCE = f'''\
"""An invented plugin that signs in with a password and an API key."""


class AcmeBridgePlugin:
    PLUGIN_INFO = {{
        "id": "{PLUGIN_ID}",
        "name": "Acme Bridge",
        "version": "1.0.0",
        "author": "OpenAVC",
        "description": "Signs in with a password and an API key.",
        "category": "integration",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": [],
    }}
    CONFIG_SCHEMA = {{
        "host": {{"type": "string", "label": "Host"}},
        "username": {{"type": "string", "label": "Username"}},
        "password": {{"type": "string", "label": "Password", "secret": True}},
        "connection": {{
            "type": "group",
            "label": "Connection",
            "fields": {{
                "api_key": {{"type": "string", "label": "API key", "secret": True}},
            }},
        }},
    }}

    async def start(self, api):
        self.api = api

    async def stop(self):
        pass
'''


def _server(server_factory):
    return server_factory(
        project_overrides={
            "plugins": {
                PLUGIN_ID: {
                    "enabled": True,
                    "config": {
                        "host": "192.0.2.30",
                        "username": "admin",
                        "password": ORIGINAL_PASSWORD,
                        "connection": {"api_key": ORIGINAL_KEY},
                    },
                },
            },
            "isc": {"enabled": False, "auth_key": ORIGINAL_MESH},
        },
        plugins={PLUGIN_ID: {f"{PLUGIN_ID}_plugin.py": PLUGIN_SOURCE}},
    )


def _saved(data_dir, item: str, field: str) -> str | None:
    from openavc.core.device_logins import DeviceLogins, plugin_owner

    return DeviceLogins(data_dir).secret(plugin_owner(PLUGIN_ID, item), field)


def _saved_mesh(data_dir) -> str | None:
    from openavc.core.device_logins import ISC_FIELD, ISC_OWNER, DeviceLogins

    return DeviceLogins(data_dir).secret(ISC_OWNER, ISC_FIELD)


def _wait(read, value) -> None:
    deadline = time.monotonic() + 10
    while read() != value and time.monotonic() < deadline:
        time.sleep(0.1)
    assert read() == value


class _Received:
    """Everything the browser is sent: HTTP response bodies and WebSocket frames."""

    def __init__(self, page: Page) -> None:
        self.responses: list = []
        self.frames: list[str] = []
        page.on("response", lambda response: self.responses.append(response))
        page.on("websocket", lambda ws: ws.on("framereceived", self._frame))

    def _frame(self, payload) -> None:
        self.frames.append(payload if isinstance(payload, str) else payload.decode("utf-8", "replace"))

    def assert_none_of(self, logins) -> None:
        bodies: list[str] = []
        for response in self.responses:
            try:
                bodies.append(response.text())
            except Exception:  # a body the browser did not keep (a redirect, a stream)
                continue
        # A body holding the marker was read and scanned, not skipped.
        assert any(MARKER in body for body in bodies), "no body holding the marker was read"
        for text in (*bodies, *self.frames):
            for login in logins:
                assert login not in text, f"the browser was sent a login ({login})"


def test_a_plugin_login_is_changed_without_reaching_the_browser(server_factory, page: Page):
    handle = _server(server_factory)
    received = _Received(page)
    project = json.loads(handle.project_path.read_text(encoding="utf-8"))
    config = project["plugins"][PLUGIN_ID]["config"]
    assert config["password"] == MARKER
    assert config["connection"]["api_key"] == MARKER
    assert _saved(handle.data_dir, "", "password") == ORIGINAL_PASSWORD

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Plugins"]').click(timeout=EXPECT_TIMEOUT)
    page.locator('button:has-text("Acme Bridge")').first.click(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text("Saved on this system")).to_have_count(2, timeout=EXPECT_TIMEOUT)
    expect(page.locator("input[type=password]")).to_have_count(0)

    # Cancel sends nothing and goes back.
    page.get_by_role("button", name="Change API key").click()
    page.get_by_role("button", name="Cancel").click()
    expect(page.get_by_text("Saved on this system")).to_have_count(2)

    page.get_by_role("button", name="Change Password").click()
    box = page.locator("input[type=password]")
    expect(box).to_have_count(1)
    expect(box).to_have_value("")
    box.fill(CHANGED_PASSWORD)
    page.get_by_role("button", name="Save Password").click()

    _wait(lambda: _saved(handle.data_dir, "", "password"), CHANGED_PASSWORD)
    assert _saved(handle.data_dir, "", "connection.api_key") == ORIGINAL_KEY
    expect(page.get_by_text("Saved on this system")).to_have_count(2, timeout=EXPECT_TIMEOUT)
    project = json.loads(handle.project_path.read_text(encoding="utf-8"))
    assert project["plugins"][PLUGIN_ID]["config"]["password"] == MARKER
    assert CHANGED_PASSWORD not in handle.project_path.read_text(encoding="utf-8")

    received.assert_none_of((*PLUGIN_LOGINS, ORIGINAL_MESH))


def test_the_mesh_key_is_shown_only_when_asked_and_changed(server_factory, page: Page):
    handle = _server(server_factory)
    received = _Received(page)
    assert json.loads(handle.project_path.read_text(encoding="utf-8"))["isc"]["auth_key"] == MARKER
    assert _saved_mesh(handle.data_dir) == ORIGINAL_MESH

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Inter-System"]').click(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text("Saved on this system")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text(ORIGINAL_MESH)).to_have_count(0)
    # Nothing has been asked for yet: no answer holds the key.
    received.assert_none_of((ORIGINAL_MESH, *PLUGIN_LOGINS))

    page.get_by_role("button", name="Show").click()
    expect(page.get_by_text(ORIGINAL_MESH)).to_be_visible(timeout=EXPECT_TIMEOUT)
    page.get_by_role("button", name="Hide").click()
    expect(page.get_by_text(ORIGINAL_MESH)).to_have_count(0)

    page.get_by_role("button", name="Change Shared auth key").click()
    box = page.locator("input[type=password]")
    expect(box).to_have_value("")
    box.fill(CHANGED_MESH)
    page.get_by_role("button", name="Save Shared auth key").click()

    _wait(lambda: _saved_mesh(handle.data_dir), CHANGED_MESH)
    expect(page.get_by_text("Saved on this system")).to_be_visible(timeout=EXPECT_TIMEOUT)
    text = handle.project_path.read_text(encoding="utf-8")
    assert json.loads(text)["isc"]["auth_key"] == MARKER
    assert CHANGED_MESH not in text

    page.get_by_role("button", name="Show").click()
    expect(page.get_by_text(CHANGED_MESH)).to_be_visible(timeout=EXPECT_TIMEOUT)
