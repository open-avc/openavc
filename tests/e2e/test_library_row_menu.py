"""The Project Library's row menu is reachable on every row, last one included.

The menu was a child of the row, and the library card sets `overflow: hidden`
for its rounded corners. `overflow: hidden` clips a descendant whatever its
z-index is, so on the bottom row the menu was cut off at the card's edge:
Duplicate, Export and Delete were all off-screen and only Open survived, which
is the one thing clicking the row already does. Deleting a saved project had no
other route in the UI.

Asserted by hit-testing every item -- asking the browser what is actually at
that point -- rather than by reading a rect. A clipped item still reports a
sensible bounding box; what it does not do is receive the click.
"""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 10_000

#: Enough saved projects that the last row sits at the card's bottom edge with
#: the whole menu below it. Four is already enough; more is cheap and makes the
#: geometry unambiguous.
SEEDED = ["alpha", "bravo", "charlie", "delta", "echo"]

MENU_ITEMS = ["Open", "Duplicate", "Export", "Delete"]


def _post(base_url: str, path: str, payload: dict) -> None:
    req = Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        assert resp.status < 300, resp.status


@pytest.fixture
def library_page(openavc_server, page: Page) -> Page:
    for name in SEEDED:
        _post(openavc_server.base_url, "/api/library",
              {"id": name, "name": f"Room {name}"})
    page.goto(f"{openavc_server.base_url}/programmer/#project",
              wait_until="domcontentloaded")
    expect(page.get_by_text("Project Library")).to_be_visible(timeout=EXPECT_TIMEOUT)
    return page


def _open_last_row_menu(page: Page):
    triggers = page.locator('button[aria-haspopup="menu"]')
    expect(triggers.last).to_be_visible(timeout=EXPECT_TIMEOUT)
    triggers.last.scroll_into_view_if_needed()
    triggers.last.click()
    menu = page.locator('[role="menu"]')
    expect(menu).to_be_visible(timeout=EXPECT_TIMEOUT)
    return menu


def test_every_item_on_the_last_row_can_actually_be_clicked(library_page) -> None:
    menu = _open_last_row_menu(library_page)
    for label in MENU_ITEMS:
        item = menu.get_by_text(label, exact=True)
        expect(item).to_be_visible(timeout=EXPECT_TIMEOUT)
        # The real question: is this item what the mouse would hit?
        hit = library_page.evaluate(
            """(label) => {
                const menu = document.querySelector('[role="menu"]');
                const btn = [...menu.querySelectorAll('button')]
                    .find(b => b.textContent.trim() === label);
                if (!btn) return 'no such item';
                const r = btn.getBoundingClientRect();
                const at = document.elementFromPoint(
                    r.left + r.width / 2, r.top + r.height / 2);
                return btn.contains(at) ? 'hit' : 'blocked or clipped';
            }""",
            label,
        )
        assert hit == "hit", f"{label} is not clickable on the last library row"


def test_the_menu_is_not_clipped_by_the_library_card(library_page) -> None:
    """The menu leaves the card entirely rather than relying on z-index."""
    _open_last_row_menu(library_page)
    report = library_page.evaluate(
        """() => {
            const menu = document.querySelector('[role="menu"]');
            const r = menu.getBoundingClientRect();
            let el = menu.parentElement, clipped = false;
            while (el && el !== document.documentElement) {
                const cs = getComputedStyle(el);
                if (cs.overflow !== 'visible' || cs.overflowY !== 'visible') {
                    const cr = el.getBoundingClientRect();
                    if (r.bottom > cr.bottom + 1 || r.top < cr.top - 1) clipped = true;
                    break;
                }
                el = el.parentElement;
            }
            return {
                clipped,
                onScreen: r.top >= 0 && r.bottom <= window.innerHeight,
            };
        }"""
    )
    assert not report["clipped"], "an ancestor is still cutting the menu off"
    assert report["onScreen"], "the menu runs off the viewport"


def test_a_menu_with_no_room_below_opens_upward(library_page) -> None:
    """With the trigger against the bottom of the window there is nowhere to
    drop to, so the menu goes above it instead of off-screen."""
    library_page.set_viewport_size({"width": 1280, "height": 420})
    triggers = library_page.locator('button[aria-haspopup="menu"]')
    triggers.last.scroll_into_view_if_needed()
    # Put the trigger as low as the page allows before opening.
    library_page.evaluate(
        """() => {
            const b = [...document.querySelectorAll('button[aria-haspopup=\\"menu\\"]')].pop();
            b.scrollIntoView({ block: 'end' });
        }"""
    )
    triggers.last.click()
    expect(library_page.locator('[role="menu"]')).to_be_visible(timeout=EXPECT_TIMEOUT)
    report = library_page.evaluate(
        """() => {
            const menu = document.querySelector('[role="menu"]');
            const trigger = [...document.querySelectorAll('button[aria-haspopup=\\"menu\\"]')].pop();
            const m = menu.getBoundingClientRect();
            const t = trigger.getBoundingClientRect();
            return {
                roomBelow: window.innerHeight - t.bottom,
                flipped: m.bottom <= t.top + 1,
                onScreen: m.top >= 0 && m.bottom <= window.innerHeight,
            };
        }"""
    )
    assert report["onScreen"], "the menu is off-screen with no room below"
    if report["roomBelow"] < 124:
        assert report["flipped"], "no room below, but the menu still dropped down"
