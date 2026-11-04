"""Playwright test for a command's Reads Its Value Back field in the Driver Builder.

A command's ``read_back`` lists the queries sent after it. The driver is
created through the API and opened on the Behavior tab: ticking the field on
a command and typing a query must reach the .avcdriver on disk, and the Test
tab's wire preview must show the query after the command's own wire.

Boots a real ``openavc.main`` subprocess (the ``openavc_server`` fixture in
conftest) and drives a real Chromium. Invented device ("Acme").
"""

from __future__ import annotations

import os
import time

import yaml
from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_ID = "e2e_read_back_driver"
DRIVER_NAME = "E2E Read Back Driver"

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "display",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {"volume": {"type": "integer", "label": "Volume"}},
    "commands": {
        "set_volume": {
            "label": "Set Volume",
            "send": "SV{level:03d}\\r",
            "params": {"level": {"type": "integer", "label": "Level", "default": 35}},
        },
    },
    "responses": [{"match": r"^VOL(\d+)$", "set": {"volume": "$1"}}],
    "polling": {"queries": ["GV\\r"]},
}


def _saved(driver_file, done) -> dict:
    deadline = time.monotonic() + 10.0
    saved: dict = {}
    while time.monotonic() < deadline:
        if driver_file.exists():
            saved = yaml.safe_load(driver_file.read_text(encoding="utf-8"))
            if done(saved):
                break
        time.sleep(0.1)
    return saved


def test_a_commands_read_back_is_edited_and_previewed(openavc_server, page: Page):
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)

    created = page.request.post(
        f"{handle.base_url}/api/driver-definitions", data=DEFINITION,
    )
    assert created.ok, created.text()

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="Devices"]').click()
    page.get_by_role("tab", name="Drivers").click()
    page.get_by_role("button", name="Create", exact=True).click()
    page.locator(f'button:has-text("{DRIVER_NAME}")').first.click()
    page.get_by_role("button", name="Behavior", exact=True).click()

    page.get_by_role("button", name="set_volume").click()
    page.get_by_label("Reads its value back").check()
    query = page.get_by_label("Read-back query 1", exact=True)
    expect(query).to_be_visible(timeout=EXPECT_TIMEOUT)
    query.fill("GV\\r")
    shot = os.environ.get("OPENAVC_E2E_SHOTS")
    if shot:
        page.screenshot(path=os.path.join(shot, "read_back_editor.png"), full_page=True)

    # The Test tab's dry run lists it after the command's own wire.
    page.get_by_role("button", name="Test", exact=True).click()
    preview = page.get_by_test_id("read-back-preview")
    expect(preview).to_contain_text("then reads the value back with", timeout=EXPECT_TIMEOUT)
    expect(preview).to_contain_text("GV\\r")
    expect(page.get_by_text("SV035\\r")).to_be_visible()
    if shot:
        page.screenshot(path=os.path.join(shot, "read_back_preview.png"), full_page=True)

    page.get_by_role("button", name="Save", exact=True).click()
    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.avcdriver"
    saved = _saved(
        driver_file, lambda d: "read_back" in d["commands"]["set_volume"],
    )
    assert saved["commands"]["set_volume"]["read_back"] == "GV\\r"
