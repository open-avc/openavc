"""Playwright test for a Python driver another session saves while the Code
view has it open with an unsaved edit.

The Code view's saves need a Programmer password, so the server is started
with one. A Python driver is placed in the driver folder and opened in the
Code view; a line is typed into the editor; then the file is saved over by
someone else. Save is refused with the choice of reload theirs, save mine over
it and keep editing, and the file keeps the other change until the person
chooses.

Boots a real ``openavc.main`` subprocess (the ``server_factory`` fixture in
conftest) and drives a real Chromium. Invented device ("Acme").
"""

from __future__ import annotations

import secrets
import time

from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_ID = "e2e_code_view_widget"
SOURCE = f'''from openavc.drivers.base import BaseDriver


class AcmeCodeViewWidget(BaseDriver):
    DRIVER_INFO = {{"id": "{DRIVER_ID}", "name": "E2E Code View Widget", "transport": "tcp"}}
'''
THEIRS = SOURCE + "\n# their change\n"


def _driver_file(handle):
    return handle.data_dir / "driver_repo" / f"{DRIVER_ID}.py"


def _opened_with_an_edit(server_factory, page: Page):
    password = secrets.token_urlsafe(24)
    handle = server_factory(env={
        "OPENAVC_PROGRAMMER_USERNAME": "admin",
        "OPENAVC_PROGRAMMER_PASSWORD": password,
        "OPENAVC_ALLOW_ANONYMOUS": "false",
    })
    _driver_file(handle).parent.mkdir(parents=True, exist_ok=True)
    _driver_file(handle).write_text(SOURCE, encoding="utf-8")
    page.set_default_timeout(SELECT_TIMEOUT)
    page.goto(handle.base_url + "/programmer/", wait_until="domcontentloaded")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign In", exact=True).click()
    page.locator('button[aria-label="Code"]').click()
    page.get_by_text(DRIVER_ID, exact=False).first.click()
    editor = page.locator(".monaco-editor").first
    expect(editor).to_contain_text("AcmeCodeViewWidget", timeout=EXPECT_TIMEOUT)
    editor.locator(".view-line").first.click()
    page.keyboard.press("Home")
    page.keyboard.type("# my edit")
    page.keyboard.press("Enter")
    return handle


def _file_when(handle, done) -> str:
    deadline = time.monotonic() + 10.0
    text = _driver_file(handle).read_text(encoding="utf-8")
    while not done(text) and time.monotonic() < deadline:
        time.sleep(0.1)
        text = _driver_file(handle).read_text(encoding="utf-8")
    return text


def test_a_code_view_save_from_a_copy_saved_over_offers_the_choice(server_factory, page: Page):
    handle = _opened_with_an_edit(server_factory, page)
    _driver_file(handle).write_text(THEIRS, encoding="utf-8")

    page.get_by_role("button", name="Save", exact=True).click()

    dialog = page.get_by_role("alertdialog", name="Changed in Another Session")
    expect(dialog).to_contain_text(
        "This driver was changed by another session since you opened it.",
        timeout=EXPECT_TIMEOUT,
    )
    assert _driver_file(handle).read_text(encoding="utf-8") == THEIRS

    dialog.get_by_role("button", name="Save Mine Over It").click()

    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
    saved = _file_when(handle, lambda text: "# my edit" in text)
    assert saved.startswith("# my edit")
    assert "# their change" not in saved


def test_reload_theirs_opens_the_copy_saved_now(server_factory, page: Page):
    handle = _opened_with_an_edit(server_factory, page)
    _driver_file(handle).write_text(THEIRS, encoding="utf-8")

    page.get_by_role("button", name="Save & Reload Driver", exact=True).click()
    dialog = page.get_by_role("alertdialog", name="Changed in Another Session")
    dialog.get_by_role("button", name="Reload Theirs").click()

    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
    editor = page.locator(".monaco-editor").first
    expect(editor).to_contain_text("# their change", timeout=EXPECT_TIMEOUT)
    expect(editor).not_to_contain_text("# my edit")
    assert _driver_file(handle).read_text(encoding="utf-8") == THEIRS
