"""The Appearance binding's colour rows fit the Properties panel.

The When active / When inactive boxes put Background and Text side by side,
two fixed-width labels and two colour pickers in a row that cannot shrink or
wrap, and that row is wider than the panel: the Text colour field ran past the
right edge, and tabbing into it scrolled the box sideways. The icon row did
the same once an icon was chosen, with its colour beside it.

Measured in Chromium, because only a browser lays the row out.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

RAIL_TIMEOUT = 20_000


def _project() -> dict[str, Any]:
    return {
        "openavc_version": "0.13.0", "devices": [],
        "variables": [{"id": "power", "type": "string", "default": "off"}],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "master_elements": [], "page_groups": [],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": {
                    "btn_look": {"x": 10, "y": 10, "w": 20, "h": 14},
                }}],
                "elements": [{
                    "id": "btn_look", "type": "button", "label": "Power",
                    "bindings": {"show": {"look": {
                        "key": "var.power",
                        "condition": {"equals": "on"},
                        "style_active": {"bg_color": "#2e7d32", "text_color": "#ffffff",
                                         "icon": "power"},
                        "style_inactive": {"bg_color": "#424242", "text_color": "#999999",
                                           "icon": "power"},
                    }}},
                }],
            }],
        },
    }


@pytest.fixture
def builder(server_factory, page: Page) -> Page:
    handle = server_factory(project_overrides=_project())
    page.set_viewport_size({"width": 1470, "height": 900})
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator("nav button").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    page.evaluate(
        """() => document.querySelector('nav button[aria-label="UI Builder"]')?.click()"""
    )
    page.locator('[data-canvas-element="btn_look"]').wait_for(
        state="visible", timeout=RAIL_TIMEOUT,
    )
    page.locator('[data-canvas-element="btn_look"]').click()
    page.get_by_text("When active (condition matches)").wait_for(
        state="visible", timeout=RAIL_TIMEOUT,
    )
    return page


#: The Appearance card (the box holding the two style boxes) and, for each
#: input inside it, how far its right edge passes the card's.
MEASURE = """() => {
  const label = [...document.querySelectorAll('label')]
    .find(e => e.textContent === 'When active (condition matches)');
  let card = label;
  while (card && getComputedStyle(card).overflow !== 'hidden') card = card.parentElement;
  if (!card) return {error: 'no card'};
  const right = card.getBoundingClientRect().right;
  const over = [...card.querySelectorAll('input')]
    .map(i => i.getBoundingClientRect().right - right)
    .filter(d => d > 0.5);
  const scrolled = [];
  for (let a = card; a; a = a.parentElement) if (a.scrollLeft) scrolled.push(a.scrollLeft);
  return {inputs: card.querySelectorAll('input').length, over, scrolled};
}"""


def test_every_field_fits_and_tabbing_never_scrolls_sideways(builder: Page) -> None:
    page = builder
    first = page.evaluate(MEASURE)
    assert "error" not in first, first
    # Background, Text and the icon colour, in each of the two boxes.
    assert first["inputs"] >= 6, first
    assert first["over"] == [], f"fields past the panel's edge: {first}"

    page.get_by_text("When active (condition matches)").scroll_into_view_if_needed()
    shot = os.environ.get("OPENAVC_E2E_SCREENSHOT_DIR")
    if shot:
        page.screenshot(path=os.path.join(shot, "appearance_rows.png"))
    page.evaluate(
        """() => {
          const label = [...document.querySelectorAll('label')]
            .find(e => e.textContent === 'When active (condition matches)');
          label.parentElement.querySelector('input').focus();
        }"""
    )
    for _ in range(first["inputs"]):
        page.keyboard.press("Tab")
        after = page.evaluate(MEASURE)
        assert after["scrolled"] == [], f"tabbing scrolled the panel sideways: {after}"
