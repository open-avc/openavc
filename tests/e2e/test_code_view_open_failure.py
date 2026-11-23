"""Playwright test for a script or Python driver the Code view cannot open.

The editor never shows a failure as if it were the file: the person is told
why in a toast, nothing is selected, and so there is nothing for Save to write
over the file. A script whose file is missing from the project folder is one
real way to get there; a driver whose source read the server fails is another.

Boots a real ``openavc.main`` subprocess (the ``server_factory`` fixture in
conftest) and drives a real Chromium. Invented device ("Acme").
"""

from __future__ import annotations

import secrets

from playwright.sync_api import Page, Route, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

SCRIPT_ID = "acme_missing_script"
DRIVER_ID = "e2e_code_view_unreadable"
DRIVER_SOURCE = f'''from openavc.drivers.base import BaseDriver


class AcmeUnreadable(BaseDriver):
    DRIVER_INFO = {{"id": "{DRIVER_ID}", "name": "E2E Unreadable Widget", "transport": "tcp"}}
'''


def _signed_in(server_factory, page: Page, **kwargs):
    password = secrets.token_urlsafe(24)
    handle = server_factory(env={
        "OPENAVC_PROGRAMMER_USERNAME": "admin",
        "OPENAVC_PROGRAMMER_PASSWORD": password,
        "OPENAVC_ALLOW_ANONYMOUS": "false",
    }, **kwargs)
    page.set_default_timeout(SELECT_TIMEOUT)
    page.goto(handle.base_url + "/programmer/", wait_until="domcontentloaded")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign In", exact=True).click()
    page.locator('button[aria-label="Code"]').click()
    return handle


def _nothing_to_save(page: Page, name: str) -> None:
    expect(page.get_by_text(f"Could not open {name}", exact=False).first).to_be_visible(
        timeout=EXPECT_TIMEOUT
    )
    expect(page.get_by_text("Select a file to edit")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_role("button", name="Save", exact=True)).to_have_count(0)
    expect(page.locator(".monaco-editor")).to_have_count(0)
    expect(page.get_by_text("Error loading", exact=False)).to_have_count(0)


def test_a_script_whose_file_is_missing_is_not_opened(server_factory, page: Page):
    handle = _signed_in(server_factory, page, project_overrides={
        "scripts": [{"id": SCRIPT_ID, "file": f"{SCRIPT_ID}.py", "enabled": False}],
    })

    page.get_by_text(SCRIPT_ID, exact=False).first.click()

    _nothing_to_save(page, SCRIPT_ID)
    assert not (handle.project_path.parent / "scripts" / f"{SCRIPT_ID}.py").exists()


def test_a_driver_whose_source_cannot_be_read_is_not_opened(server_factory, page: Page):
    handle = _signed_in(server_factory, page, drivers={f"{DRIVER_ID}.py": DRIVER_SOURCE})

    def _refuse(route: Route) -> None:
        if route.request.method == "GET":
            route.fulfill(status=500, content_type="application/json",
                          body='{"detail": "The disk could not be read."}')
        else:
            route.continue_()

    page.route(f"**/api/python-drivers/{DRIVER_ID}/source", _refuse)
    page.get_by_text(DRIVER_ID, exact=False).first.click()

    _nothing_to_save(page, DRIVER_ID)
    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.py"
    assert driver_file.read_text(encoding="utf-8") == DRIVER_SOURCE
