"""Playwright test for deleting a Python driver a project device uses, from the
Code view.

The seed project's one device (Test Controller) runs on the e2e test driver.
Its delete button in the Code view answers with the devices that use it and a
way to each, not a delete: Open Device goes to the device, where another
driver is chosen, and the driver file stays.

Boots a real ``openavc.main`` subprocess (the ``server_factory`` fixture in
conftest) and drives a real Chromium.
"""

from __future__ import annotations

import secrets

from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_ID = "e2e_test_controller"


def test_the_code_view_names_the_devices_on_a_driver_instead_of_deleting_it(
    server_factory, page: Page
):
    password = secrets.token_urlsafe(24)
    handle = server_factory(env={
        "OPENAVC_PROGRAMMER_USERNAME": "admin",
        "OPENAVC_PROGRAMMER_PASSWORD": password,
        "OPENAVC_ALLOW_ANONYMOUS": "false",
    })
    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.py"
    page.set_default_timeout(SELECT_TIMEOUT)
    page.goto(handle.base_url + "/programmer/", wait_until="domcontentloaded")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign In", exact=True).click()
    page.locator('button[aria-label="Code"]').click()

    page.get_by_title("Delete driver").click()

    dialog = page.get_by_role("alertdialog", name="Driver In Use")
    expect(dialog).to_contain_text("Test Controller", timeout=EXPECT_TIMEOUT)
    expect(page.get_by_role("alertdialog", name="Delete Driver")).to_have_count(0)

    dialog.get_by_role("button", name="Open Device").click()

    expect(dialog).to_have_count(0)
    expect(page).to_have_url(f"{handle.base_url}/programmer/#devices")
    assert driver_file.exists()
