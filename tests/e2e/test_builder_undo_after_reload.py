"""After Reload Project, Ctrl+Z in the UI Builder has nothing to put back.

Reload Project (the conflict banner's answer) replaces the tab's project with
the one on the server. The Builder's undo history was made on the project the
reload threw away, and it used to survive the reload: the next Ctrl+Z wrote
the discarded project's page back and saved it over the other session's
change. The run goes through the real conflict: an edit in the Builder,
another session saving first, the banner, the reload, then the keys.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect  # noqa: E402

from tests.e2e._held_saves import HeldSaves  # noqa: E402

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
                "elements": [{"id": "btn", "type": "button", "label": "Ours"}],
            }],
        },
    }


def _button_label(page: Page, base_url: str) -> str | None:
    project = page.request.get(f"{base_url}/api/project").json()
    for element in project["ui"]["pages"][0]["elements"]:
        if element["id"] == "btn":
            return element.get("label")
    return None


def _save_as_another_session(page: Page, base_url: str, label: str) -> None:
    answer = page.request.get(f"{base_url}/api/project")
    project = answer.json()
    project.pop("_etag", None)
    project["ui"]["pages"][0]["elements"] = [{"id": "btn", "type": "button", "label": label}]
    project["ui"]["pages"][0]["layouts"] = [
        {"id": "d", "placements": {"btn": {"x": 40, "y": 40, "w": 20, "h": 20}}}
    ]
    saved = page.request.put(
        f"{base_url}/api/project",
        headers={"If-Match": answer.headers["etag"], "Content-Type": "application/json"},
        data=json.dumps(project),
    )
    assert saved.ok, saved.text()


def test_ctrl_z_after_reload_project_leaves_the_reloaded_project(server_factory, page: Page) -> None:
    handle = server_factory(project_overrides=_project())
    page.set_viewport_size({"width": 1600, "height": 1000})
    saves = HeldSaves(page)
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator("nav button").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    page.evaluate("""() => document.querySelector('nav button[aria-label="UI Builder"]')?.click()""")
    control = page.locator('[data-canvas-element="btn"]')
    control.wait_for(state="visible", timeout=RAIL_TIMEOUT)

    # The Builder's edit, its save held while another session saves first.
    control.click()
    page.keyboard.press("Delete")
    control.wait_for(state="detached", timeout=5_000)
    saves.wait_for()
    _save_as_another_session(page, handle.base_url, "Theirs")
    saves.release()

    banner = page.get_by_text("Conflict:")
    banner.wait_for(state="visible", timeout=10_000)
    page.get_by_role("button", name="Reload Project").click()
    banner.wait_for(state="detached", timeout=10_000)
    # The canvas draws the page in its frame; the element's box above it is
    # only what is clicked.
    canvas = page.frame_locator("iframe").first
    expect(canvas.get_by_text("Theirs")).to_be_visible(timeout=10_000)

    control.click()
    page.keyboard.press("Control+KeyZ")
    # An undo saves 100 ms after it; give a wrong one time to land.
    page.wait_for_timeout(1_500)

    expect(canvas.get_by_text("Theirs")).to_be_visible()
    expect(canvas.get_by_text("Ours")).to_have_count(0)
    assert _button_label(page, handle.base_url) == "Theirs"
