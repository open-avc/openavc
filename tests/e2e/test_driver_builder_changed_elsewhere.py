"""Playwright test for a driver another session changes while the Driver
Builder has it open.

The driver is created through the API and opened in the Builder. Then another
door (the API, as the assistant or a second Programmer would) saves it with a
command added. A Builder holding no unsaved edit opens the new copy by itself.
A Builder holding one is refused when it saves, and offers reload theirs, save
mine over it and keep editing; the added command stays in the file until the
person chooses.

Boots a real ``openavc.main`` subprocess (the ``openavc_server`` fixture in
conftest) and drives a real Chromium. Invented device ("Acme").
"""

from __future__ import annotations

import time

import yaml
from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_NAME = "E2E Changed Elsewhere Driver"


def _definition(driver_id: str, name: str = DRIVER_NAME, **commands) -> dict:
    return {
        "id": driver_id,
        "name": name,
        "manufacturer": "Acme",
        "category": "display",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\\r",
        "default_config": {"host": "", "port": 23},
        "commands": {"power_on": {"label": "On", "send": "PWR ON\\r"}, **commands},
    }


POWER_OFF = {"power_off": {"label": "Off", "send": "PWR OFF\\r"}}


def _stored(handle, driver_id: str) -> dict:
    path = handle.data_dir / "driver_repo" / f"{driver_id}.avcdriver"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _stored_when(handle, driver_id: str, done) -> dict:
    deadline = time.monotonic() + 10.0
    stored = _stored(handle, driver_id)
    while not done(stored) and time.monotonic() < deadline:
        time.sleep(0.1)
        stored = _stored(handle, driver_id)
    return stored


def _open_in_the_builder(page: Page, handle, driver_id: str) -> None:
    created = page.request.post(
        f"{handle.base_url}/api/driver-definitions", data=_definition(driver_id),
    )
    assert created.ok, created.text()
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').wait_for(state="visible", timeout=SELECT_TIMEOUT)
    page.locator('button[aria-label="Devices"]').click()
    page.get_by_role("tab", name="Drivers").click()
    page.get_by_role("button", name="Create", exact=True).click()
    page.locator(f'button:has-text("{DRIVER_NAME}")').first.click()
    expect(page.get_by_role("heading", name=DRIVER_NAME)).to_be_visible(timeout=EXPECT_TIMEOUT)


def _another_session_adds_power_off(page: Page, handle, driver_id: str, name: str) -> None:
    saved = page.request.put(
        f"{handle.base_url}/api/driver-definitions/{driver_id}",
        data=_definition(driver_id, name, **POWER_OFF),
    )
    assert saved.ok, saved.text()


def test_an_open_driver_with_no_edit_opens_the_copy_another_session_saved(
    openavc_server, page: Page,
):
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)
    driver_id = "e2e_changed_elsewhere_clean"
    _open_in_the_builder(page, handle, driver_id)

    _another_session_adds_power_off(page, handle, driver_id, "Their Driver")

    expect(page.get_by_role("heading", name="Their Driver")).to_be_visible(timeout=EXPECT_TIMEOUT)


def test_a_save_from_the_copy_another_session_saved_over_offers_the_choice(
    openavc_server, page: Page,
):
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)
    driver_id = "e2e_changed_elsewhere_dirty"
    _open_in_the_builder(page, handle, driver_id)
    page.get_by_placeholder("e.g., Extron SW4 HD 4K").fill("My Driver")

    _another_session_adds_power_off(page, handle, driver_id, "Their Driver")
    page.get_by_role("button", name="Save", exact=True).click()

    dialog = page.get_by_role("alertdialog", name="Changed in Another Session")
    expect(dialog).to_contain_text(
        "This driver was changed by another session since you opened it.",
        timeout=EXPECT_TIMEOUT,
    )
    assert "power_off" in _stored(handle, driver_id)["commands"]

    # Keep editing: the edit stays, and the next save asks again.
    dialog.get_by_role("button", name="Keep Editing").click()
    expect(dialog).to_be_hidden()
    expect(page.get_by_role("heading", name="My Driver")).to_be_visible()
    page.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)

    # Save mine over it: the edit is saved, over their change.
    dialog.get_by_role("button", name="Save Mine Over It").click()
    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
    stored = _stored_when(handle, driver_id, lambda d: d["name"] == "My Driver")
    assert stored["name"] == "My Driver"
    assert "power_off" not in stored["commands"]


def test_reload_theirs_discards_the_edit_and_opens_their_copy(openavc_server, page: Page):
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)
    driver_id = "e2e_changed_elsewhere_reload"
    _open_in_the_builder(page, handle, driver_id)
    page.get_by_placeholder("e.g., Extron SW4 HD 4K").fill("My Driver")

    _another_session_adds_power_off(page, handle, driver_id, "Their Driver")
    page.get_by_role("button", name="Save", exact=True).click()
    dialog = page.get_by_role("alertdialog", name="Changed in Another Session")
    dialog.get_by_role("button", name="Reload Theirs").click()

    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_role("heading", name="Their Driver")).to_be_visible(timeout=EXPECT_TIMEOUT)
    stored = _stored(handle, driver_id)
    assert stored["name"] == "Their Driver"
    assert "power_off" in stored["commands"]
