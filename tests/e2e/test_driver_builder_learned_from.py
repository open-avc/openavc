"""Playwright test for a setting the device fills in, and labelled choices.

A driver with an enum setting whose choices carry labels and that declares
``learned_from`` is created through the API. In the Driver Builder the field
must open (labelled choices used to crash the Devices view), show its labels,
show the state variable under Filled In From, and save the picker's changes
to the .avcdriver with the labels intact. In Add Device the same setting
shows its labels and the note that it is filled in from the device.

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

DRIVER_ID = "e2e_learned_driver"
DRIVER_NAME = "E2E Learned Driver"

CHOICES = [
    {"value": "2", "label": "Two zones"},
    {"value": "4", "label": "Four zones"},
]

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23, "zones": "4"},
    "config_schema": {
        "host": {"type": "string", "required": True, "label": "Host"},
        "port": {"type": "integer", "default": 23, "label": "Port"},
        "zones": {
            "type": "enum", "label": "Zones", "default": "4",
            "values": CHOICES, "learned_from": "zones_reported",
        },
    },
    "state_variables": {
        "zones_reported": {"type": "string", "label": "Zones Reported"},
        "power": {"type": "boolean", "label": "Power"},
    },
    "commands": {},
    "responses": [{"match": r"^ZONES (\d+)$", "set": {"zones_reported": "$1"}}],
}


def _saved(handle, predicate) -> dict:
    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.avcdriver"
    deadline = time.monotonic() + 10.0
    saved: dict = {}
    while time.monotonic() < deadline:
        if driver_file.exists():
            saved = yaml.safe_load(driver_file.read_text(encoding="utf-8"))
            if predicate(saved["config_schema"]["zones"]):
                return saved
        time.sleep(0.1)
    return saved


def test_a_learned_setting_with_labelled_choices_in_the_builder_and_add_device(
    openavc_server, page: Page,
):
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    created = page.request.post(
        f"{handle.base_url}/api/driver-definitions", data=DEFINITION,
    )
    assert created.ok, created.text()

    # --- Driver Builder ---
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').click()
    page.get_by_role("tab", name="Drivers").click()
    page.get_by_role("button", name="Create", exact=True).click()
    page.locator(f'button:has-text("{DRIVER_NAME}")').first.click()
    page.get_by_role("button", name="Connection", exact=True).click()
    page.locator('button:has-text("zones")').first.click()

    picker = page.locator("#config-learned-zones")
    expect(picker).to_have_value("zones_reported", timeout=EXPECT_TIMEOUT)
    assert not errors, errors
    expect(page.get_by_text("This view crashed")).to_have_count(0)
    # The Default Value list shows the labels; the choice rows hold both halves.
    expect(page.locator("select", has=page.locator("option", has_text="Four zones")).first).to_be_visible()
    expect(page.locator('input[value="Two zones"]')).to_be_visible()

    picker.select_option("")
    page.get_by_role("button", name="Save", exact=True).click()
    saved = _saved(handle, lambda f: "learned_from" not in f)
    assert "learned_from" not in saved["config_schema"]["zones"]
    assert saved["config_schema"]["zones"]["values"] == CHOICES

    picker.select_option("zones_reported")
    page.get_by_role("button", name="Save", exact=True).click()
    saved = _saved(handle, lambda f: f.get("learned_from") == "zones_reported")
    assert saved["config_schema"]["zones"]["learned_from"] == "zones_reported"
    assert saved["config_schema"]["zones"]["values"] == CHOICES

    # --- Add Device ---
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').click()
    page.get_by_role("button", name="Add Device").first.click()
    page.get_by_placeholder("Search drivers...").fill(DRIVER_NAME)
    page.locator(f'text="{DRIVER_NAME}"').first.click()

    zones = page.get_by_label(re.compile(r"^Zones"))
    expect(zones).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(zones.locator("option")).to_have_text(["Select...", "Two zones", "Four zones"])
    expect(zones).to_have_value("4")
    expect(page.get_by_text("Filled in from the device when it connects.")).to_be_visible()
    assert not errors, errors
