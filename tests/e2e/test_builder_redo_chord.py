"""Ctrl+Shift+Z redoes in the UI Builder, as Ctrl+Y does.

A browser reports a letter typed with Shift held as its capital, so the redo
chord arrives as "Z", not "z". The Builder matched the lower case only, so
Ctrl+Shift+Z (and Cmd+Shift+Z, the Mac habit) did nothing while Ctrl+Y
worked. The unit tests send "z" with Shift, which a browser never does; only
a real one says what the key is.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

RAIL_TIMEOUT = 20_000


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
    return page


@pytest.mark.parametrize("modifier", ["Control", "Meta"])
def test_shift_z_redoes_an_undone_delete(builder: Page, modifier: str) -> None:
    page = builder
    control = page.locator('[data-canvas-element="btn"]')
    page.evaluate(
        """() => { window.__keys = [];
            document.addEventListener('keydown', (e) => window.__keys.push(e.key), true); }"""
    )
    control.click()
    page.keyboard.press("Delete")
    control.wait_for(state="detached", timeout=5_000)
    page.keyboard.press(f"{modifier}+KeyZ")
    control.wait_for(state="visible", timeout=5_000)
    page.keyboard.press(f"{modifier}+Shift+KeyZ")
    keys = page.evaluate("() => window.__keys")
    try:
        control.wait_for(state="detached", timeout=5_000)
    except Exception:  # noqa: BLE001 -- the assertion below says what went wrong
        pytest.fail(f"{modifier}+Shift+Z did not redo the delete; the page heard the keys {keys}")
