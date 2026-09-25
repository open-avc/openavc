"""A panel on the network waits until it is approved, in a real browser.

Every other e2e server listens on loopback, and a loopback peer is the box's
own screen, which the panel gate admits without approval. So the servers here
bind the host's network address and carry a password: what a tablet in the
space meets. Chromium loads the waiting screen with its code, an approval
through the API lets the page in without a reload, a press then reaches the
instance over the admitted socket, and a revoke sends the page back to a new
code. The admin-password route on the panel, an instance set to admit anyone,
and the Programmer's Preview (whose socket carries the session token instead
of a cookie) are driven the same way.
"""

from __future__ import annotations

import base64
import json
import re
import socket
import time
from typing import Any, Callable
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect

from tests import gates

# Skip-gate only: the browser comes from pytest-playwright's session fixtures.
pytest.importorskip("playwright.sync_api")

PASSWORD = "e2e-panel-approval"
SPACE = "Executive Boardroom"
EXPECT_TIMEOUT = 10_000
READY_TIMEOUT = 15_000


def _lan_address() -> str | None:
    """The address a device on the network reaches this host at. A UDP
    connect picks the interface the default route uses and sends nothing."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        address = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if address.startswith("127.") else address


def _panel_project() -> dict[str, Any]:
    """One button that writes a variable: the round trip that proves the
    socket was admitted, with nothing on the end of a wire."""
    return {
        "openavc_version": "0.13.0",
        "project": {
            "id": "panel_approval_e2e",
            "name": SPACE,
            "description": "",
            "created": "2026-01-01T00:00:00",
            "modified": "2026-01-01T00:00:00",
        },
        "variables": [
            {"id": "last_press", "type": "string", "default": "none",
             "label": "Last button pressed"},
        ],
        "ui": {
            "settings": {"theme": "dark-default"},
            "pages": [{
                "id": "main",
                "name": "Main",
                "page_type": "page",
                "layouts": [{
                    "id": "landscape",
                    "orientation": "landscape",
                    "primary": True,
                    "inherits": None,
                    "placements": {
                        "btn_laptop": {"x": 4.0, "y": 4.0, "w": 30.0, "h": 14.0},
                    },
                    "hidden": [],
                }],
                "elements": [{
                    "id": "btn_laptop",
                    "type": "button",
                    "label": "Laptop",
                    "parent": None,
                    "bindings": {"do": {"press": [
                        {"action": "state.set",
                         "key": "var.last_press", "value": "laptop"},
                    ]}},
                }],
                "master_elements": [],
            }],
            "master_elements": [],
            "page_groups": [],
        },
    }


def _lan_server(server_factory, **env: str):
    address = _lan_address()
    if address is None:
        gates.skip_or_fail(gates.E2E, "this host has no network address to bind")
    return server_factory(
        project_overrides=_panel_project(),
        bind=address,
        env={
            "OPENAVC_PROGRAMMER_PASSWORD": PASSWORD,
            # A source checkout admits anyone until a credential is set. The
            # password above sets one; this pins the posture regardless.
            "OPENAVC_ALLOW_ANONYMOUS": "false",
            **env,
        },
    )


def _api(base_url: str, method: str, path: str, body: dict | None = None) -> Any:
    """The Programmer's side of the API, with the admin credential."""
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(
        f"{base_url}{path}", data=data, method=method,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _state(base_url: str, key: str) -> Any:
    return _api(base_url, "GET", f"/api/state/{key}").get("value")


def _eventually(condition: Callable[[], bool], message: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.1)
    raise AssertionError(message)


def _code_on_screen(page: Page) -> str:
    code = page.locator("#panel-access-overlay .panel-access-code").inner_text()
    assert re.fullmatch(r"\d{3}-\d{3}", code), code
    return code


# ---------------------------------------------------------------------------
# Waiting, approved, revoked
# ---------------------------------------------------------------------------

def test_a_panel_on_the_network_waits_is_let_in_and_can_be_sent_back(
    server_factory, page: Page,
) -> None:
    handle = _lan_server(server_factory)
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")

    overlay = page.locator("#panel-access-overlay")
    expect(overlay).to_be_visible(timeout=READY_TIMEOUT)
    expect(overlay).to_contain_text("Waiting for approval")
    expect(overlay).to_contain_text(f"before it can control {SPACE}")
    code = _code_on_screen(page)
    # Nothing behind it: no control drawn, no socket admitted, and not the
    # offline overlay either (this page is refused, not cut off).
    expect(page.locator('[data-element-id="btn_laptop"]')).to_have_count(0)
    expect(page.locator("#offline-overlay")).to_be_hidden()

    listed = _api(handle.base_url, "GET", "/api/panel/devices")
    assert [d["code"] for d in listed["pending"]] == [code]
    device_id = listed["pending"][0]["id"]

    _api(handle.base_url, "POST", f"/api/panel/devices/{device_id}/approve",
         {"name": "Test tablet"})
    # The next poll carries the approval; the page goes on without a reload.
    expect(overlay).to_have_count(0, timeout=EXPECT_TIMEOUT)
    button = page.locator('[data-element-id="btn_laptop"]')
    expect(button).to_be_visible(timeout=READY_TIMEOUT)
    assert _state(handle.base_url, "var.last_press") == "none"
    button.click()
    _eventually(lambda: _state(handle.base_url, "var.last_press") == "laptop",
                "the press never reached the instance over the admitted socket")
    listed = _api(handle.base_url, "GET", "/api/panel/devices")
    assert listed["pending"] == []
    assert [d["name"] for d in listed["approved"]] == ["Test tablet"]

    # Revoked: the socket is closed with 4010, the page asks again and gets
    # a fresh code.
    _api(handle.base_url, "DELETE", f"/api/panel/devices/{device_id}")
    expect(overlay).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(overlay).to_contain_text("Waiting for approval")
    assert _code_on_screen(page) != code
    expect(page.locator("#offline-overlay")).to_be_hidden()
    listed = _api(handle.base_url, "GET", "/api/panel/devices")
    assert listed["approved"] == []
    assert len(listed["pending"]) == 1


# ---------------------------------------------------------------------------
# The admin password, typed on the panel
# ---------------------------------------------------------------------------

def test_the_admin_password_approves_from_the_panel(server_factory, page: Page) -> None:
    handle = _lan_server(server_factory)
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    overlay = page.locator("#panel-access-overlay")
    expect(overlay).to_be_visible(timeout=READY_TIMEOUT)

    overlay.get_by_role("link", name="Approve with the admin password").click()
    password = overlay.get_by_label("Admin password")
    password.fill("not-it")
    overlay.get_by_role("button", name="Approve", exact=True).click()
    expect(overlay.locator(".panel-access-error")).to_have_text(
        "That password is not correct.", timeout=EXPECT_TIMEOUT,
    )
    # Still waiting, and the page (not a browser dialog) said so.
    expect(overlay).to_be_visible()

    password.fill(PASSWORD)
    overlay.get_by_role("button", name="Approve", exact=True).click()
    expect(overlay).to_have_count(0, timeout=EXPECT_TIMEOUT)
    expect(page.locator('[data-element-id="btn_laptop"]')).to_be_visible(timeout=READY_TIMEOUT)
    listed = _api(handle.base_url, "GET", "/api/panel/devices")
    assert [d["approved_by"] for d in listed["approved"]] == ["admin password on the panel"]


# ---------------------------------------------------------------------------
# Anyone on the network
# ---------------------------------------------------------------------------

def test_an_instance_open_to_anyone_shows_no_waiting_screen(server_factory, page: Page) -> None:
    handle = _lan_server(server_factory, OPENAVC_PANEL_ACCESS="open")
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    expect(page.locator('[data-element-id="btn_laptop"]')).to_be_visible(timeout=READY_TIMEOUT)
    expect(page.locator("#panel-access-overlay")).to_have_count(0)
    assert _api(handle.base_url, "GET", "/api/panel/devices")["pending"] == []


# ---------------------------------------------------------------------------
# The Programmer's Preview
# ---------------------------------------------------------------------------

def test_the_programmer_preview_carries_the_session_instead_of_a_cookie(
    server_factory, page: Page,
) -> None:
    """The Builder's Preview embeds this same page. It never checks in and
    never waits: its socket carries the Programmer's session token, which
    the gate admits as a credential."""
    handle = _lan_server(server_factory)
    page.goto(f"{handle.base_url}/programmer/#ui-builder")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign In", exact=True).click()

    panel = page.frame_locator("iframe")
    expect(panel.locator('[data-element-id="btn_laptop"]')).to_be_visible(timeout=READY_TIMEOUT)
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(panel.locator('[data-element-id="btn_laptop"]')).to_be_visible(timeout=READY_TIMEOUT)

    frame = page.frame(url=re.compile(r"/panel/"))
    assert frame is not None
    _eventually(
        lambda: frame.evaluate(
            "() => window.__openavcPanel && window.__openavcPanel.ws"
            " && window.__openavcPanel.ws.readyState"
        ) == 1,
        "the Preview's socket never opened",
        timeout=10.0,
    )
    expect(panel.locator("#panel-access-overlay")).to_have_count(0)
    # It asked for nothing: the IDE's own preview is never a device waiting
    # in the Programmer's list.
    assert _api(handle.base_url, "GET", "/api/panel/devices")["pending"] == []
