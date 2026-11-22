"""The Programmer says what the server's start went on past.

A start that goes on without part of itself (the cloud connection, the update
checks) keeps each part on its run-state record, and ``/api/startup-status``
lists them as ``problems``. The Programmer shows each one's sentence when it
opens, so a space running without its cloud connection says so somewhere other
than the server log. The status is answered here with two problems, since a
test server cannot be made to fail those parts for real.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, Route, expect

pytest.importorskip("playwright.sync_api")

TIMEOUT = 20_000

CLOUD = ("The connection to OpenAVC Cloud did not start, so this system cannot be "
         "reached or managed from the cloud. Check the server log for the reason.")
UPDATES = ("The update checks did not start, so this server does not look for "
           "updates. Check the server log for the reason.")


def test_the_programmer_says_what_the_start_went_on_past(server_factory, page: Page) -> None:
    handle = server_factory()

    def answer(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "phase": "ready", "ready": True, "version": "0.41.0", "error": None,
            "problems": [
                {"stage": "cloud_agent", "detail": CLOUD},
                {"stage": "update_checks", "detail": UPDATES},
            ],
            "held": False, "retry_in": None,
        }))

    page.route("**/api/startup-status", answer)
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")

    notice = page.get_by_text("Some parts of OpenAVC did not start.", exact=False)
    expect(notice).to_be_visible(timeout=TIMEOUT)
    expect(notice).to_contain_text(CLOUD)
    expect(notice).to_contain_text(UPDATES)
