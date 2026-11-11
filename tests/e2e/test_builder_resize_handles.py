"""Each of the UI Builder's two resize handles moves the two panels beside it.

The Builder lays out three columns (the palette and outline, the canvas, the
Properties panel) with a resizable panel group. Preview takes both side panels
away and Ctrl+E the left one; when a side panel came back, the group had it
after the canvas in its own list, so the handles resized by a list that no
longer matched the screen: after Ctrl+E twice, dragging the right handle moved
the palette and the Properties panel and left the canvas alone.

jsdom lays nothing out and the unit tests stub the panel library, so only a
real browser can see this. Each case drags one handle and checks that the two
panels beside it, and only those, change width by the drag.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

RAIL_TIMEOUT = 20_000
DRAG = 120
SLACK = 3  # px: the library rounds sizes to a percent's fraction


def _project() -> dict[str, Any]:
    return {
        "openavc_version": "0.13.0", "devices": [],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "master_elements": [], "page_groups": [],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": {"btn": {"x": 40, "y": 40, "w": 20, "h": 20}}}],
                "elements": [{"id": "btn", "type": "button", "label": "Fine"}],
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
    page.locator('[data-canvas-element="btn"]').wait_for(state="visible", timeout=RAIL_TIMEOUT)
    _wait_for_columns(page)
    return page


def _wait_for_columns(page: Page) -> None:
    page.wait_for_function(
        "() => document.querySelectorAll('[data-panel-group] > [data-panel]').length === 3",
        timeout=RAIL_TIMEOUT,
    )


def _widths(page: Page) -> list[float]:
    """The three columns' widths, left to right as they are on screen."""
    return page.evaluate(
        """() => [...document.querySelectorAll('[data-panel-group] > [data-panel]')]
            .map((p) => p.getBoundingClientRect())
            .sort((a, b) => a.left - b.left)
            .map((r) => r.width)"""
    )


def _drag(page: Page, handle: int, dx: int) -> None:
    """Drag a handle (0 = left, 1 = right, as on screen) by dx pixels."""
    boxes = page.evaluate(
        """() => [...document.querySelectorAll('[data-panel-group] > [data-panel-resize-handle-id]')]
            .map((h) => h.getBoundingClientRect())
            .sort((a, b) => a.left - b.left)
            .map((r) => ({x: r.left + r.width / 2, y: r.top + r.height / 2}))"""
    )
    start = boxes[handle]
    page.mouse.move(start["x"], start["y"])
    page.mouse.down()
    page.mouse.move(start["x"] + dx, start["y"], steps=8)
    page.mouse.up()
    page.wait_for_timeout(100)


def _toggle(page: Page, key: str) -> None:
    """Ctrl+E (the palette) or Ctrl+P (preview)."""
    page.keyboard.press(f"Control+{key}")
    page.wait_for_timeout(150)


def _assert_drag_moves_its_neighbours(page: Page, handle: int, dx: int, when: str) -> None:
    before = _widths(page)
    _drag(page, handle, dx)
    after = _widths(page)
    change = [round(a - b) for a, b in zip(after, before)]
    left, right = handle, handle + 1
    untouched = 2 if handle == 0 else 0
    expected = [0, 0, 0]
    expected[left], expected[right] = dx, -dx
    assert all(abs(c - e) <= SLACK for c, e in zip(change, expected)), (
        f"{when}: dragging the {'left' if handle == 0 else 'right'} handle by {dx}px "
        f"changed the columns by {change} (palette, canvas, Properties); expected {expected}, "
        f"the {['palette', 'canvas', 'Properties panel'][untouched]} unchanged"
    )


def test_each_handle_moves_its_neighbours_fresh(builder: Page) -> None:
    _assert_drag_moves_its_neighbours(builder, 1, -DRAG, "fresh")
    _assert_drag_moves_its_neighbours(builder, 0, DRAG // 2, "fresh")


def test_each_handle_moves_its_neighbours_after_the_palette_comes_back(builder: Page) -> None:
    _toggle(builder, "e")
    _toggle(builder, "e")
    _wait_for_columns(builder)
    _assert_drag_moves_its_neighbours(builder, 1, -DRAG, "after Ctrl+E twice")
    _assert_drag_moves_its_neighbours(builder, 0, DRAG // 2, "after Ctrl+E twice")


def test_each_handle_moves_its_neighbours_after_preview(builder: Page) -> None:
    _toggle(builder, "p")
    _toggle(builder, "p")
    _wait_for_columns(builder)
    _assert_drag_moves_its_neighbours(builder, 1, -DRAG, "after preview on and off")
    _assert_drag_moves_its_neighbours(builder, 0, DRAG // 2, "after preview on and off")
