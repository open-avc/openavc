"""Playwright test for a response rule's Then Query list in the Driver Builder.

A rule's ``then_query`` takes the rows Poll Queries takes. The driver is
created through the API and opened on the Behavior tab: a rule's existing
query must show the file's values, and on a rule that writes one output a new
query sent once must offer that output's variables as what it reports (its
``{child_id}`` is the output the rule wrote), with the edit reaching the
.avcdriver on disk.

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

DRIVER_ID = "e2e_then_query_driver"
DRIVER_NAME = "E2E Then Query Driver"

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "switcher",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "child_entity_types": {
        "output": {
            "label": "Output",
            "id_format": {"type": "integer", "min": 1, "max": 8},
            "state_variables": {
                "input": {"type": "integer", "label": "Input"},
                "available": {"type": "boolean", "label": "Available"},
                "name": {"type": "string", "label": "Name"},
            },
            "instances": {"count": 4},
        },
    },
    "commands": {},
    "responses": [
        {
            "match": r"^CHG$",
            "set": {},
            "then_query": [
                {"each_child": "output", "send": "IN? {child_id}\\r", "query_for": "input"},
            ],
        },
        {
            "match": r"^AVAIL (\d) YES$",
            "child_set": [{"type": "output", "id": "$1", "state": {"available": "true"}}],
        },
    ],
    "polling": {"queries": []},
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


def test_a_rules_then_query_is_shown_and_edited(openavc_server, page: Page):
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

    # The first rule's query, as the file has it.
    sends = page.locator('input[placeholder="e.g., ?VOUT{child_id}\\\\r"]')
    expect(sends).to_have_count(1, timeout=EXPECT_TIMEOUT)
    expect(sends.first).to_have_value("IN? {child_id}\\r")

    # The second rule writes one output: a query sent once names it.
    add = page.get_by_role("button", name="+ Add Then Query")
    expect(add).to_have_count(2)
    add.nth(1).click()
    expect(page.get_by_text("is the Output this rule writes")).to_be_visible()
    expect(sends).to_have_count(2)
    sends.nth(1).fill("NAME? {child_id}\\r")
    reports = page.locator("select").filter(has=page.locator('option[value="name"]'))
    reports.last.select_option("name")
    shot = os.environ.get("OPENAVC_E2E_SHOTS")
    if shot:
        page.screenshot(path=os.path.join(shot, "then_query_editor.png"), full_page=True)
    page.get_by_role("button", name="Save", exact=True).click()

    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.avcdriver"
    saved = _saved(
        driver_file, lambda d: "then_query" in d["responses"][1],
    )
    assert saved["responses"][1]["then_query"] == [
        {"send": "NAME? {child_id}\\r", "query_for": "name"},
    ]
    assert saved["responses"][0]["then_query"] == DEFINITION["responses"][0]["then_query"]
