"""A control saved with an old Lucide icon name draws that icon on the panel.

Lucide renamed some icons (home became house, tv-2 tv-minimal, unlock
lock-open). The UI Builder previews through a library that still answers to
the old names, while the panel draws from a sprite that carries only the new
ones, so a page nav saved with ``icon: "home"`` showed a house in the Builder
and nothing on the panel. Measured in Chromium, because only a browser draws
the sprite.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

TIMEOUT = 20_000

ICONS = {"nav_home": "home", "btn_tv": "tv-2", "btn_unlock": "unlock", "btn_power": "power"}


def _project() -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {"id": "nav_home", "type": "page_nav", "label": "Home", "icon": "home",
         "target_page": "main"},
    ]
    elements += [
        {"id": eid, "type": "button", "label": name, "icon": name}
        for eid, name in ICONS.items() if eid != "nav_home"
    ]
    return {
        "openavc_version": "0.13.0", "devices": [],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "master_elements": [], "page_groups": [],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": {
                    eid: {"x": 2 + i * 20, "y": 10, "w": 18, "h": 14}
                    for i, eid in enumerate(ICONS)
                }}],
                "elements": elements,
            }],
        },
    }


#: Each control's icon box, as the panel drew it.
MEASURE = """(ids) => Object.fromEntries(ids.map((id) => {
  const use = document.querySelector(`[data-element-id="${id}"] svg use`);
  if (!use) return [id, null];
  const box = use.getBBox();
  return [id, {width: box.width, height: box.height, href: use.getAttribute('href')}];
}))"""


def test_old_icon_names_draw_on_the_panel(server_factory, page: Page) -> None:
    handle = server_factory(project_overrides=_project())
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    page.locator('[data-element-id="btn_power"] svg use').wait_for(state="attached", timeout=TIMEOUT)
    page.wait_for_function(
        """() => { const u = document.querySelector('[data-element-id="btn_power"] svg use');
                   return u && u.getBBox().width > 0; }""",
        timeout=TIMEOUT,
    )

    drawn = page.evaluate(MEASURE, list(ICONS))

    for eid, name in ICONS.items():
        box = drawn[eid]
        assert box is not None, f"{eid} ({name}) drew no icon at all: {drawn}"
        assert box["width"] > 0 and box["height"] > 0, f"{eid} ({name}) drew a blank icon: {drawn}"
    assert drawn["nav_home"]["href"].endswith("icons.svg#house"), drawn
