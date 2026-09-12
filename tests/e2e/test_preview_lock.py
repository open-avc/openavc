"""Panel lock and idle behavior through the actual Programmer Preview entry."""

import pytest
from playwright.sync_api import expect


def _project(*, lock_code="1234", idle_timeout=4):
    pages = []
    for page_id, target in (("main", "details"), ("details", "main")):
        pages.append({
            "id": page_id, "name": page_id.title(), "page_type": "page",
            "elements": [{
                "id": f"{page_id}_nav", "type": "page_nav",
                "label": target.title(), "target_page": target,
            }],
            "layouts": [{
                "id": "landscape", "orientation": "landscape", "primary": True,
                "placements": {f"{page_id}_nav": {"x": 10, "y": 10, "w": 60, "h": 20}},
                "hidden": [],
            }],
        })
    return {
        "openavc_version": "0.13.0",
        "ui": {"settings": {
            "lock_code": lock_code, "idle_timeout_seconds": idle_timeout,
            "idle_page": "main" if lock_code else "details",
        }, "master_elements": [], "pages": pages},
    }


@pytest.mark.parametrize("preview", [True, False], ids=["preview", "standalone"])
def test_initial_lock_unlock_and_idle_relock(server_factory, page, preview):
    handle = server_factory(project_overrides=_project())
    if preview:
        page.goto(f"{handle.base_url}/programmer/#ui-builder")
        panel = page.frame_locator("iframe")
        expect(panel.get_by_role("button", name="Navigate to Details", exact=True)).to_be_visible()
        expect(panel.locator("#lock-overlay")).to_have_count(0)
        page.get_by_role("button", name="Preview", exact=True).click()
    else:
        page.goto(f"{handle.base_url}/panel/")
        panel = page

    # No interaction inside the runtime panel precedes this assertion.
    expect(panel.get_by_text("Panel Locked", exact=True)).to_be_visible()
    if not preview:
        # A real panel is unauthenticated, so its definition is public to the
        # network — the PIN it is about to check must not be in it, and the
        # unlock below therefore has to go to the server to succeed at all.
        settings = page.evaluate("() => window.__openavcPanel.uiSettings")
        assert settings.get("lock_code") is None
        assert settings.get("lock_enabled") is True
    panel.get_by_placeholder("Enter PIN").fill("0000")
    panel.get_by_role("button", name="Unlock", exact=True).click()
    expect(panel.get_by_text("Incorrect PIN", exact=True)).to_be_visible()
    expect(panel.locator("#lock-overlay")).to_have_count(1)
    panel.get_by_placeholder("Enter PIN").fill("1234")
    panel.get_by_role("button", name="Unlock", exact=True).click()
    expect(panel.locator("#lock-overlay")).to_have_count(0)
    panel.get_by_role("button", name="Navigate to Details", exact=True).click()
    expect(panel.get_by_role("button", name="Navigate to Main", exact=True)).to_be_visible()

    expect(panel.get_by_text("Panel Locked", exact=True)).to_be_visible(timeout=7000)
    panel.get_by_placeholder("Enter PIN").fill("1234")
    panel.get_by_role("button", name="Unlock", exact=True).click()
    expect(panel.get_by_role("button", name="Navigate to Details", exact=True)).to_be_visible()


def test_preview_starts_idle_timer_without_panel_input(server_factory, page):
    handle = server_factory(project_overrides=_project(lock_code="", idle_timeout=1))
    page.goto(f"{handle.base_url}/programmer/#ui-builder")
    panel = page.frame_locator("iframe")
    main_page = panel.get_by_role("button", name="Navigate to Details", exact=True)
    expect(main_page).to_be_visible()
    # The design canvas must stay on the selected page beyond the idle period.
    page.wait_for_timeout(1500)
    expect(main_page).to_be_visible()
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(main_page).to_be_visible()
    expect(panel.get_by_role("button", name="Navigate to Main", exact=True)).to_be_visible()
    expect(panel.locator("#lock-overlay")).to_have_count(0)
