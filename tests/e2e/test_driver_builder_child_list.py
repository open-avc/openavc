"""Playwright test for a list read in the Driver Builder.

A response rule spreads a list in one reply across children (``child_list``),
a per-child poll query goes out once per page of children (``page_size``), and
a query sent once can report a child variable as ``<type>.<variable>``. The
driver is created through the API and opened on the Behavior tab: the list
rule's fields must show the file's values, edits must reach the .avcdriver on
disk, a page size must survive an edit of the query's send text, and the
list read must be offered as what a query sent once reports.

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

DRIVER_ID = "e2e_child_list_driver"
DRIVER_NAME = "E2E Child List Driver"

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "switcher",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r\\n",
    "default_config": {"host": "", "port": 23, "output_count": 4},
    "config_schema": {
        "output_count": {"type": "integer", "default": 4, "label": "Outputs"},
    },
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "child_entity_types": {
        "output": {
            "label": "Output",
            "id_format": {"type": "integer", "min": 1, "max": 32},
            "state_variables": {"input": {"type": "integer", "label": "Input"}},
            "instances": {"count_from": "output_count"},
        },
    },
    "commands": {},
    "responses": [{
        "match": r"^Page(\d+) ((?:\d\d ?)+)$",
        "child_list": {
            "type": "output", "group": "$2", "element": r"(\d\d)",
            "start": "$1", "state": {"input": "$1"},
        },
    }],
    "polling": {"queries": [
        {"each_child": "output", "page_size": 16, "send": "PAGE? {child_id}\\r",
         "query_for": "input"},
        "TIES?\\r",
    ]},
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


def test_a_list_read_is_shown_edited_and_offered(openavc_server, page: Page):
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

    editor = page.get_by_test_id("child-list-editor")
    expect(editor).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_label("List capture")).to_have_value("$2")
    expect(page.get_by_label("Item pattern")).to_have_value(r"(\d\d)")
    expect(page.get_by_label("First child")).to_have_value("$1")
    expect(page.get_by_label("input value")).to_have_value("$1")
    shot = os.environ.get("OPENAVC_E2E_SHOTS")
    if shot:
        editor.screenshot(path=os.path.join(shot, "child_list_editor.png"))

    page.get_by_label("Item separator").fill(" ")
    page.get_by_label("Children per query").fill("8")
    # An edit of the send text keeps the page size.
    sends = page.locator('input[placeholder="e.g., ?VOUT{child_id}\\\\r"]')
    sends.first.fill("PAGE?{child_id}\\r")
    page.get_by_role("button", name="Save", exact=True).click()

    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.avcdriver"
    saved = _saved(
        driver_file,
        lambda d: d["polling"]["queries"][0].get("page_size") == 8
        and d["responses"][0]["child_list"].get("separator") == " ",
    )
    assert saved["responses"][0]["child_list"] == {
        "type": "output", "group": "$2", "element": r"(\d\d)", "start": "$1",
        "separator": " ", "state": {"input": "$1"},
    }
    assert saved["polling"]["queries"][0] == {
        "each_child": "output", "send": "PAGE?{child_id}\\r", "query_for": "input",
        "page_size": 8,
    }

    # The list read is offered as what a query sent once reports.
    reports = page.locator("select").filter(has=page.locator('option[value="output.input"]'))
    expect(reports).to_have_count(1)
    reports.select_option("output.input")
    page.get_by_role("button", name="Save", exact=True).click()
    saved = _saved(driver_file, lambda d: isinstance(d["polling"]["queries"][1], dict))
    assert saved["polling"]["queries"][1] == {"send": "TIES?\\r", "query_for": "output.input"}
