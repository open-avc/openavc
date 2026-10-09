"""The Driver Builder shows the validator's polling warnings on their tabs.

A serial driver that polls 40 lines every second with no liveness block draws
both: the poll outlasts its interval (Behavior tab) and nothing notices the
device going quiet over serial (Connection tab). Neither blocks a save.

Boots a real ``openavc.main`` subprocess (the ``openavc_server`` fixture in
conftest.py) and drives the built Programmer in Chromium.
"""

from __future__ import annotations

import os

from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_ID = "e2e_poll_warning_driver"
DRIVER_NAME = "E2E Poll Warning Driver"

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "serial",
    "delimiter": "\\r",
    "default_config": {"port": "SIM:acme", "poll_interval": 1},
    "state_variables": {"power": {"type": "string", "label": "Power"}},
    "commands": {},
    "responses": [{
        "match": r"^PWR=(\w+)$",
        "mappings": [{"group": 1, "state": "power", "type": "string"}],
    }],
    "polling": {"queries": [f"Q{n}\\r" for n in range(40)]},
}


def test_poll_warnings_show_on_their_tabs(openavc_server, page: Page):
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
    expect(page.get_by_text("Each poll sends 40 lines, 2 s at 50 ms apart")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    shot = os.environ.get("OPENAVC_E2E_SCREENSHOT_DIR")
    if shot:
        page.screenshot(path=os.path.join(shot, "builder_poll_warning.png"))

    page.get_by_role("button", name="Connection", exact=True).click()
    expect(
        page.get_by_text("Nothing notices if the device stops answering over serial")
    ).to_be_visible(timeout=EXPECT_TIMEOUT)
    if shot:
        page.screenshot(path=os.path.join(shot, "builder_silence_warning.png"))
