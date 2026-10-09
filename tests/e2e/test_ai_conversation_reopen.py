"""A reopened AI conversation shows the question and the reply, nothing else.

The cloud stores each round's tool calls and tool results as rows of their
own beside the reply, which already names its tool calls. Reopening a
conversation drew those rows as assistant bubbles of raw JSON.

Boots a real ``openavc.main`` subprocess (the ``openavc_server`` fixture in
conftest.py) and drives the built Programmer in Chromium. The assistant's
cloud is not there, so its four routes are answered in the browser.
"""

from __future__ import annotations

import json
import os

from playwright.sync_api import Page, Route, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

CALL = {"id": "tu_1", "name": "get_device_info", "input": {"device_id": "matrix"}}
NOW = "2026-10-09T12:00:00Z"


def _row(row_id: str, role: str, content: str, tool_calls=None) -> dict:
    return {
        "id": row_id, "role": role, "content": content,
        "input_tokens": 0, "output_tokens": 0,
        "tool_calls": tool_calls, "created_at": NOW,
    }


SUMMARY = {
    "id": "conv-1", "title": "Add the matrix", "system_id": None,
    "message_count": 4, "total_input_tokens": 0, "total_output_tokens": 0,
    "created_at": NOW, "updated_at": NOW,
}
DETAIL = {
    **SUMMARY,
    "messages": [
        _row("m1", "user", "Add the matrix"),
        _row("m2", "tool_use", json.dumps([CALL])),
        _row("m3", "tool_result", json.dumps(
            [{"type": "tool_result", "tool_use_id": "tu_1", "content": "{\"id\": \"matrix\"}"}]
        )),
        _row("m4", "assistant", "Added the matrix and its eight outputs.", [CALL]),
    ],
}
ANSWERS = {
    "/api/ai/status": {"available": True, "state": "available"},
    "/api/ai/conversations": [SUMMARY],
    "/api/ai/conversations/conv-1": DETAIL,
    "/api/ai/usage": {
        "requests_used": 1, "requests_limit": None, "requests_remaining": None,
        "current_period_start": NOW, "current_period_end": NOW,
    },
}


def _answer(route: Route) -> None:
    path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
    if path in ANSWERS:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(ANSWERS[path]))
    else:
        route.continue_()


def test_a_reopened_conversation_draws_no_tool_rows(openavc_server, page: Page):
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)
    page.route("**/api/ai/**", _answer)

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="AI Assistant"]').click()
    page.get_by_text("Add the matrix", exact=True).first.click()

    expect(page.get_by_text("Added the matrix and its eight outputs.")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(page.get_by_text("tool_use_id")).to_have_count(0)
    expect(page.get_by_text('"device_id": "matrix"')).to_have_count(0)
    shot = os.environ.get("OPENAVC_E2E_SCREENSHOT_DIR")
    if shot:
        page.screenshot(path=os.path.join(shot, "ai_reopened_conversation.png"))
