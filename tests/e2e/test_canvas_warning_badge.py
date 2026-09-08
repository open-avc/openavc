"""A control's warning badge can be read from the canvas.

The canvas marks a control the page review has something to say about with an
orange "!" in its corner, and puts the review's sentence in the badge's title.
The badge used to be drawn with ``pointer-events: none``, so the mouse never
reached it and the title never showed: the Builder said a control had a problem
and offered no way to ask what it was, short of the Validate list. Somebody
enlarged a flagged matrix to make the badge go away, and it stayed, because its
warning was about a missing route key and not about size, and nothing on the
canvas would say so.

Playwright's hover is the right instrument: it refuses to hover an element that
is not the hit target at its own centre, which is exactly the failure. The
second test is the reason the badge was ever made inert -- it must not swallow
the press that selects and drags the control -- so it is pinned alongside.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

RAIL_TIMEOUT = 20_000


def _project() -> dict[str, Any]:
    """One select starved of the room its native control needs (44x84px)."""
    return {
        "openavc_version": "0.13.0", "devices": [],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "master_elements": [], "page_groups": [],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": {
                    "sel": {"x": 10, "y": 10, "w": 10, "h": 5},
                    "btn": {"x": 40, "y": 40, "w": 20, "h": 20},
                }}],
                "elements": [
                    {"id": "sel", "type": "select", "label": "Source",
                     "options": [{"label": "A", "value": "a"}]},
                    {"id": "btn", "type": "button", "label": "Fine"},
                ],
            }],
        },
    }


@pytest.fixture
def builder(server_factory, page: Page) -> Page:
    handle = server_factory(project_overrides=_project())
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator("nav button").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    page.evaluate(
        """() => document.querySelector('nav button[aria-label="UI Builder"]')?.click()"""
    )
    page.locator('[data-canvas-element="sel"]').wait_for(state="visible", timeout=RAIL_TIMEOUT)
    return page


def test_the_badge_can_be_hovered_and_says_why(builder: Page) -> None:
    page = builder
    badge = page.locator('[data-canvas-element="sel"] [title]').first
    badge.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    # Refuses, after retrying, if the badge is not what the pointer lands on.
    badge.hover(timeout=5_000)
    title = badge.get_attribute("title") or ""
    assert "too small" in title, (
        f"the badge's title does not carry the review's sentence: {title!r}"
    )
    assert page.evaluate(
        "el => getComputedStyle(el).pointerEvents", badge.element_handle()
    ) != "none", "the badge is drawn with pointer-events: none, so its title can never show"


def test_pressing_the_badge_still_selects_the_control(builder: Page) -> None:
    """The badge sits inside the control's hit box, so a press on it has to
    reach the control -- otherwise a flagged control has a dead corner."""
    page = builder
    badge = page.locator('[data-canvas-element="sel"] [title]').first
    badge.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    badge.click(timeout=5_000)
    page.wait_for_timeout(300)
    selected_id = page.evaluate(
        """() => {
            const inputs = [...document.querySelectorAll('input')];
            const idBox = inputs.find(i => i.value === 'sel');
            return idBox ? idBox.value : null;
        }"""
    )
    assert selected_id == "sel", "pressing the badge did not select the control under it"


def test_a_control_with_nothing_wrong_has_no_badge(builder: Page) -> None:
    assert builder.locator('[data-canvas-element="btn"] [title]').count() == 0
