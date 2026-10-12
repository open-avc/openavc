"""Playwright test for state conditions in the Driver Builder.

``when_state:`` holds a poll query, the whole poll, or a start-up step back
while the device reports a state (a unit that any command wakes, left in
standby). The driver is created through the API: the conditions it declares
must show with the file's values, a new one must be addable from the row's
button on a poll query and on a start-up step, an edit of a query's send text
must keep its condition, and every change must reach the .avcdriver on disk.

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

DRIVER_ID = "e2e_when_state_driver"
DRIVER_NAME = "E2E When State Driver"
ADD = "Send only while the device reports a state"

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "switcher",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r\\n",
    "default_config": {"host": "", "port": 23},
    "config_schema": {},
    "state_variables": {
        "standby": {"type": "boolean", "label": "Standby"},
        "mode": {"type": "integer", "label": "Mode"},
    },
    "commands": {},
    "responses": [
        {"match": "^STANDBY$", "set": {"standby": "true"}},
        {"match": "^WAKE$", "set": {"standby": "false"}},
        {"match": r"^MODE (\d)$", "set": {"mode": "$1"}},
    ],
    "on_connect": ["GET VER\\r\\n"],
    "polling": {
        "when_state": {"standby": False},
        "queries": [
            "GET STANDBY\\r\\n",
            {"send": "GET MODE\\r\\n", "when_state": {"mode": [0, 2]}},
        ],
    },
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


def test_state_conditions_are_shown_edited_and_saved(openavc_server, page: Page):
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

    gates = page.get_by_test_id("when-state-editor")
    # The whole poll's condition, then the second query's.
    expect(gates).to_have_count(2, timeout=EXPECT_TIMEOUT)
    poll_gate, mode_gate = gates.nth(0), gates.nth(1)
    expect(poll_gate.get_by_label("State variable")).to_have_value("standby")
    expect(poll_gate.get_by_label("Value of standby")).to_have_value("false")
    expect(mode_gate.get_by_label("State variable")).to_have_value("mode")
    expect(mode_gate.get_by_label("Values of mode")).to_have_value("0, 2")
    shot = os.environ.get("OPENAVC_E2E_SHOTS")
    if shot:
        page.get_by_text("Pause Polling").locator("..").screenshot(
            path=os.path.join(shot, "when_state_polling.png"),
        )

    mode_gate.get_by_label("Values of mode").fill("0, 1")
    # An edit of the send text keeps the query's condition.
    sends = page.locator('input[placeholder="e.g., %1POWR ?\\\\r"]')
    sends.nth(1).fill("GET MODE ALL\\r\\n")
    # A condition added to the first query starts at its first variable.
    page.get_by_role("button", name=ADD).first.click()
    expect(gates).to_have_count(3)
    if shot:
        page.get_by_text("Poll Queries").locator("..").screenshot(
            path=os.path.join(shot, "when_state_queries.png"),
        )

    # A start-up step takes one too.
    page.get_by_role("button", name="Connection", exact=True).click()
    page.get_by_role("button", name=ADD).first.click()
    expect(page.get_by_test_id("when-state-editor")).to_have_count(1)

    page.get_by_role("button", name="Save", exact=True).click()

    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.avcdriver"
    saved = _saved(
        driver_file,
        lambda d: isinstance(d.get("on_connect", [None])[0], dict),
    )
    assert saved["polling"]["when_state"] == {"standby": False}
    assert saved["polling"]["queries"] == [
        {"send": "GET STANDBY\\r\\n", "when_state": {"standby": False}},
        {"send": "GET MODE ALL\\r\\n", "when_state": {"mode": [0, 1]}},
    ]
    assert saved["on_connect"] == [
        {"send": "GET VER\\r\\n", "when_state": {"standby": False}},
    ]
