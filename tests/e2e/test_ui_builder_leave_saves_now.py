"""Leaving the UI Builder with an edit still waiting to save sends it at once.

A Builder edit saves two seconds after it. A move to another view inside those
two seconds used to stop on the browser's "Leave without saving?" prompt,
though the edit saved on its timer whichever answer was given. Now no prompt
stands in the way: the view changes, and the waiting save goes the moment the
Builder is left.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect  # noqa: E402

from tests.e2e._held_saves import HeldSaves  # noqa: E402

RAIL_TIMEOUT = 20_000


def _project() -> dict[str, Any]:
    return {
        "openavc_version": "0.18.0", "devices": [],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "master_elements": [], "page_groups": [],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": {"btn": {"x": 40, "y": 40, "w": 20, "h": 20}}}],
                "elements": [{"id": "btn", "type": "button", "label": "Acme"}],
            }],
        },
    }


def _open(page: Page, view: str) -> None:
    page.locator(f'nav button[aria-label="{view}"]').click()


def test_leaving_the_builder_sends_the_waiting_save_without_asking(server_factory, page: Page) -> None:
    handle = server_factory(project_overrides=_project())
    page.set_viewport_size({"width": 1600, "height": 1000})
    asked: list[str] = []
    page.on("dialog", lambda dialog: (asked.append(dialog.message), dialog.dismiss()))
    saves = HeldSaves(page)

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator("nav button").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    _open(page, "UI Builder")
    control = page.locator('[data-canvas-element="btn"]')
    control.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    control.click()
    page.keyboard.press("Delete")
    control.wait_for(state="detached", timeout=5_000)
    assert saves.routes == []

    _open(page, "Devices")

    # Well inside the two seconds the Builder's own timer would take.
    saves.wait_for(timeout_ms=1_000)
    assert asked == []
    expect(page.locator('nav button[aria-label="Devices"]')).to_have_attribute(
        "aria-current", "page"
    )
    saves.release()
    elements = None
    for _ in range(50):
        elements = page.request.get(f"{handle.base_url}/api/project").json()["ui"]["pages"][0]["elements"]
        if elements == []:
            break
        page.wait_for_timeout(100)
    assert elements == []
