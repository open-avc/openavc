"""A system behind the release the assistant needs is offered that update
where the assistant opens, and one click installs it through the Updates view.

Boots a real ``openavc.main`` subprocess (the ``openavc_server`` fixture in
conftest.py) and drives the built Programmer in Chromium. The assistant's
cloud is not there, so its routes are answered in the browser, and so are the
updater's, so nothing is downloaded or installed.
"""

from __future__ import annotations

import json
import os

from playwright.sync_api import Page, Route, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

NEEDED = "99.0.0"
OUTDATED = {
    "available": False,
    "state": "outdated",
    "reason": f"This system runs OpenAVC v0.1.0. Update it to v{NEEDED} to use the assistant.",
    "needs_release": NEEDED,
}


def _status(can_self_update: bool) -> dict:
    return {
        "current_version": "0.1.0", "deployment_type": "linux_package",
        "can_self_update": can_self_update, "update_available": NEEDED,
        "update_channel": "stable", "update_status": "idle", "update_progress": 0,
        "update_error": "", "install_version": NEEDED,
        "rollback_available": False, "rollback_version": "",
    }


def _serve(page: Page, can_self_update: bool) -> list[str]:
    """Answer the assistant's and the updater's routes; returns the list each
    update request's path is added to."""
    asked: list[str] = []
    answers = {
        "/api/ai/status": OUTDATED,
        "/api/system/updates/status": _status(can_self_update),
        "/api/system/updates/check": {"update_available": True, "latest_version": NEEDED},
        "/api/system/updates/history": {"history": []},
        "/api/system/updates/apply": {"success": True},
    }

    def answer(route: Route) -> None:
        path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
        if path not in answers:
            route.continue_()
            return
        if path.startswith("/api/system/updates/"):
            asked.append(path)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(answers[path]))

    page.route("**/api/ai/**", answer)
    page.route("**/api/system/updates/**", answer)
    return asked


def _until(page: Page, met, timeout_ms: int = EXPECT_TIMEOUT) -> None:
    """Wait for a request the page makes in the background."""
    waited = 0
    while not met():
        assert waited < timeout_ms, "the page never made the request"
        page.wait_for_timeout(100)
        waited += 100


def test_the_assistant_offers_the_update_and_one_click_installs_it(openavc_server, page: Page):
    page.set_default_timeout(SELECT_TIMEOUT)
    asked = _serve(page, can_self_update=True)

    page.goto(f"{openavc_server.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="AI Assistant"]').click()

    expect(page.get_by_text(f"Update to v{NEEDED} to use the assistant.")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text("Installing restarts the server.")).to_be_visible()
    shot = os.environ.get("OPENAVC_E2E_SCREENSHOT_DIR")
    if shot:
        page.screenshot(path=os.path.join(shot, "ai_update_for_assistant.png"))
    page.get_by_role("button", name=f"Install v{NEEDED}").click()

    expect(page).to_have_url(f"{openavc_server.base_url}/programmer/#updates", timeout=EXPECT_TIMEOUT)
    _until(page, lambda: "/api/system/updates/apply" in asked)
    assert asked.count("/api/system/updates/apply") == 1
    assert asked.index("/api/system/updates/check") < asked.index("/api/system/updates/apply")


def test_a_system_that_does_not_update_itself_is_shown_the_updates_view(openavc_server, page: Page):
    page.set_default_timeout(SELECT_TIMEOUT)
    asked = _serve(page, can_self_update=False)

    page.goto(f"{openavc_server.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="AI Assistant"]').click()
    page.get_by_role("button", name="Open System Updates").click()

    expect(page.get_by_text("System Updates").first).to_be_visible(timeout=EXPECT_TIMEOUT)
    _until(page, lambda: "/api/system/updates/check" in asked)
    page.wait_for_timeout(500)
    assert "/api/system/updates/apply" not in asked
