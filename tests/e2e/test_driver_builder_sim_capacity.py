"""Playwright test for a driver's declared simulator capacity in the Driver Builder.

A driver declares how much the device can take in (``simulator.capacity``):
the bytes it holds before reading them, the time it spends on each message,
and what it does when full. The driver is created through the API, opened on
the Builder's Simulation tab, and the Capacity fields must show the file's
numbers; an edit must reach the .avcdriver on disk, and clearing the box must
remove the declaration.

Boots a real ``openavc.main`` subprocess (the ``openavc_server`` fixture in
conftest) and drives a real Chromium. Invented device ("Acme").
"""

from __future__ import annotations

import time

import yaml
from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_ID = "e2e_sim_capacity_driver"
DRIVER_NAME = "E2E Sim Capacity Driver"

DEFINITION = {
    "id": DRIVER_ID,
    "name": DRIVER_NAME,
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {},
    "responses": [],
    "simulator": {"capacity": {"rx_buffer": 64, "process_ms": 10}},
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


def test_a_declared_capacity_is_shown_edited_and_removed(openavc_server, page: Page):
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
    page.get_by_role("button", name="Simulation", exact=True).click()

    declared = page.get_by_label("Declare how much this device can take in")
    expect(declared).to_be_checked(timeout=EXPECT_TIMEOUT)
    buffer = page.get_by_label("Receive buffer")
    expect(buffer).to_have_value("64")
    expect(page.get_by_label("Time per message")).to_have_value("10")

    buffer.fill("256")
    page.get_by_label("When full").select_option("silence")
    page.get_by_role("button", name="Save", exact=True).click()

    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.avcdriver"
    saved = _saved(
        driver_file,
        lambda d: d.get("simulator", {}).get("capacity", {}).get("rx_buffer") == 256,
    )
    assert saved["simulator"]["capacity"] == {
        "rx_buffer": 256, "process_ms": 10, "on_overflow": "silence",
    }

    declared.uncheck()
    expect(buffer).to_have_count(0)
    page.get_by_role("button", name="Save", exact=True).click()
    saved = _saved(driver_file, lambda d: "capacity" not in (d.get("simulator") or {}))
    assert "capacity" not in (saved.get("simulator") or {})
