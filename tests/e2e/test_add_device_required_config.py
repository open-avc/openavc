"""Playwright test for a setting a driver marks required, in Add Device.

A driver whose Model is required with no default is created through the API.
Add Device refuses it blank and names it, then saves once a Model is picked.
The same device switched to "Through a bridge" saves with no IP address,
before any bridge exists: the address is asked only of a network connection.

Boots a real ``openavc.main`` subprocess (the ``openavc_server`` fixture in
conftest) and drives a real Chromium. Invented device ("Acme").
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_ID = "e2e_required_driver"
DRIVER_NAME = "E2E Required Driver"

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "transports": ["tcp", "serial"],
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23},
    "config_schema": {
        "host": {"type": "string", "required": True, "label": "IP Address"},
        "port": {"type": "integer", "default": 23, "label": "Port"},
        "model": {
            "type": "enum", "required": True, "label": "Model",
            "values": [{"value": "w12", "label": "W12"}, {"value": "w20", "label": "W20"}],
        },
    },
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {},
}


def _device(page: Page, base_url: str, device_id: str) -> dict | None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        project = page.request.get(f"{base_url}/api/project").json()
        for device in project["devices"]:
            if device["id"] == device_id:
                return {**device, "connection": project["connections"].get(device_id, {})}
        time.sleep(0.1)
    return None


def _type(field, text: str) -> None:
    """React's controlled inputs take keystrokes; fill() can skip onChange."""
    field.click(click_count=3)
    field.press_sequentially(text, delay=20)


def _open_add_device(page: Page):
    page.get_by_role("button", name="Add Device").first.click()
    dialog = page.locator('h3:has-text("Add Device")').locator("xpath=..")
    dialog.get_by_placeholder("Search drivers...").fill(DRIVER_NAME)
    dialog.locator(f'text="{DRIVER_NAME}"').first.click()
    return dialog


def test_add_device_refuses_a_blank_required_model_and_names_it(openavc_server, page: Page):
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    created = page.request.post(f"{handle.base_url}/api/driver-definitions", data=DEFINITION)
    assert created.ok, created.text()

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').click()

    # --- Over the network: Model left blank is refused, by name ---
    dialog = _open_add_device(page)
    _type(dialog.get_by_label("Device ID"), "acme_cam_net")
    _type(dialog.get_by_placeholder("192.168.1.100"), "10.0.0.5")
    dialog.get_by_role("button", name="Add Device").click()
    refusal = dialog.get_by_text("Model is required.", exact=True)
    expect(refusal).to_be_visible(timeout=EXPECT_TIMEOUT)
    # The line is at the top of a dialog that scrolls; it is brought into view.
    expect(refusal).to_be_in_viewport()
    assert _device(page, handle.base_url, "acme_cam_net") is None

    dialog.get_by_label(re.compile(r"^Model")).select_option("w12")
    dialog.get_by_role("button", name="Add Device").click()
    expect(page.locator('h3:has-text("Add Device")')).to_have_count(0, timeout=EXPECT_TIMEOUT)
    saved = _device(page, handle.base_url, "acme_cam_net")
    assert saved is not None
    assert saved["config"]["model"] == "w12"
    assert saved["connection"]["host"] == "10.0.0.5"

    # --- Through a bridge, before any bridge exists: no IP address asked ---
    dialog = _open_add_device(page)
    _type(dialog.get_by_label("Device ID"), "acme_cam_bridged")
    dialog.get_by_role("button", name="Through a bridge").click()
    dialog.get_by_label(re.compile(r"^Model")).select_option("w20")
    dialog.get_by_role("button", name="Add Device").click()
    expect(page.locator('h3:has-text("Add Device")')).to_have_count(0, timeout=EXPECT_TIMEOUT)
    saved = _device(page, handle.base_url, "acme_cam_bridged")
    assert saved is not None
    assert saved["config"]["model"] == "w20"
    assert "host" not in saved["connection"]
    assert not errors, errors
