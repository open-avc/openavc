"""Playwright test for a device setting's wire value map in the Driver Builder.

A boolean setting whose device writes ON / OFF declares the words in ``map``.
The driver is created through the API, opened in the Driver Builder, and the
On and Off rows must show the words from the file; an edited word must reach
the .avcdriver on disk, still a quoted string the platform reads back.

Boots a real ``openavc.main`` subprocess (the ``openavc_server`` fixture in
conftest) and drives a real Chromium. Invented device ("Acme").
"""

from __future__ import annotations

import re
import time

import yaml
from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_ID = "e2e_setting_map_driver"
DRIVER_NAME = "E2E Setting Map Driver"

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {
        "high_density": {"type": "boolean", "label": "High Density"},
    },
    "commands": {},
    "responses": [{
        "match": r"^REP HD (\w+)$",
        "mappings": [{
            "group": 1, "state": "high_density", "type": "boolean",
            "map": {"ON": True, "OFF": False},
        }],
    }],
    "device_settings": {
        "high_density": {
            "type": "boolean",
            "label": "High Density",
            "state_key": "high_density",
            "default": False,
            "map": {"true": "ON", "false": "OFF"},
            "write": {"send": "SET HD {value}\\r"},
        },
    },
}


def _open_driver(page: Page, base_url: str) -> None:
    """Devices -> Drivers -> Create, then open the driver by name."""
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="Devices"]').click()
    page.get_by_role("tab", name="Drivers").click()
    page.get_by_role("button", name="Create", exact=True).click()
    page.locator(f'button:has-text("{DRIVER_NAME}")').first.click()
    page.get_by_role("button", name="Behavior", exact=True).click()
    header = page.get_by_role("button", name=re.compile(r"^Device Settings\b"))
    header.wait_for(state="visible", timeout=SELECT_TIMEOUT)
    if header.get_attribute("aria-expanded") == "false":
        header.click()
    page.get_by_test_id("device-setting-high_density").click()


def test_a_boolean_setting_map_is_edited_by_value_and_saved(
    openavc_server, page: Page,
):
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)

    created = page.request.post(
        f"{handle.base_url}/api/driver-definitions", data=DEFINITION,
    )
    assert created.ok, created.text()

    _open_driver(page, handle.base_url)

    on_word = page.get_by_label("What is sent for On")
    off_word = page.get_by_label("What is sent for Off")
    expect(on_word).to_have_value("ON", timeout=EXPECT_TIMEOUT)
    expect(off_word).to_have_value("OFF", timeout=EXPECT_TIMEOUT)

    off_word.fill("STANDBY")
    page.get_by_role("button", name="Save", exact=True).click()

    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.avcdriver"
    deadline = time.monotonic() + 10.0
    saved: dict = {}
    while time.monotonic() < deadline:
        if driver_file.exists():
            saved = yaml.safe_load(driver_file.read_text(encoding="utf-8"))
            if saved["device_settings"]["high_density"].get("map", {}).get(
                "false"
            ) == "STANDBY":
                break
        time.sleep(0.1)
    assert saved["device_settings"]["high_density"]["map"] == {
        "true": "ON", "false": "STANDBY",
    }

    # Clearing both words removes the map from the driver.
    on_word.fill("")
    off_word.fill("")
    page.get_by_role("button", name="Save", exact=True).click()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        saved = yaml.safe_load(driver_file.read_text(encoding="utf-8"))
        if "map" not in saved["device_settings"]["high_density"]:
            break
        time.sleep(0.1)
    assert "map" not in saved["device_settings"]["high_density"]
