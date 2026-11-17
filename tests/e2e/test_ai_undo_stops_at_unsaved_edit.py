"""The assistant's Undo writes nothing while the tab holds an edit not saved yet.

Undo puts back the project as it was before a reply, then reloads the tab's
copy from the server. An edit still on its way to the server is in neither,
so the reload used to drop it with nothing said. The run sends a message (the
cloud is not there, so its routes are answered in the browser), makes an
edit in the UI Builder whose save is held, then clicks Undo on the reply.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, Route, expect  # noqa: E402

from tests.e2e._held_saves import HeldSaves  # noqa: E402

RAIL_TIMEOUT = 20_000
EXPECT_TIMEOUT = 10_000
NOW = "2026-11-17T12:00:00Z"
REPLY = "Added the projector."
STOPPED = "Nothing was undone: the project has a change that is not saved yet."

ANSWERS = {
    "/api/ai/status": {"available": True, "state": "available"},
    "/api/ai/conversations": [],
    "/api/ai/usage": {
        "requests_used": 1, "requests_limit": None, "requests_remaining": None,
        "current_period_start": NOW, "current_period_end": NOW,
    },
}
STREAM = (
    f"event: text_delta\ndata: {json.dumps({'text': REPLY})}\n\n"
    f"event: done\ndata: {json.dumps({'conversation_id': 'conv-1'})}\n\n"
)


def _answer(route: Route) -> None:
    path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
    if path == "/api/ai/chat":
        route.fulfill(status=200, content_type="text/event-stream", body=STREAM)
    elif path in ANSWERS:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(ANSWERS[path]))
    else:
        route.continue_()


def _project() -> dict[str, Any]:
    return {
        "openavc_version": "0.13.0", "devices": [],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "master_elements": [], "page_groups": [],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": {"btn": {"x": 40, "y": 40, "w": 20, "h": 20}}}],
                "elements": [{"id": "btn", "type": "button", "label": "Kept"}],
            }],
        },
    }


def _open(page: Page, view: str) -> None:
    page.locator(f'nav button[aria-label="{view}"]').click()


def test_undo_on_a_reply_stops_at_an_edit_still_saving(server_factory, page: Page) -> None:
    handle = server_factory(project_overrides=_project())
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.route("**/api/ai/**", _answer)
    saves = HeldSaves(page)

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator("nav button").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)

    _open(page, "AI Assistant")
    page.locator("textarea").last.fill("Add a projector")
    page.locator("textarea").last.press("Enter")
    expect(page.get_by_text(REPLY)).to_be_visible(timeout=EXPECT_TIMEOUT)
    undo = page.get_by_role("button", name="Undo", exact=True)
    expect(undo).to_be_visible()

    _open(page, "UI Builder")
    control = page.locator('[data-canvas-element="btn"]')
    control.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    control.click()
    page.keyboard.press("Delete")
    control.wait_for(state="detached", timeout=5_000)
    saves.wait_for()

    # Leaving the Builder with the edit unsaved asks first; the answer is Leave.
    page.once("dialog", lambda dialog: dialog.accept())
    _open(page, "AI Assistant")
    undo.click()

    expect(page.get_by_text(STOPPED)).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text(REPLY)).to_be_visible()
    expect(undo).to_be_visible()
    # Undo sent nothing of its own: the one save is the Builder's.
    page.wait_for_timeout(500)
    assert len(saves.routes) == 1

    saves.release()
