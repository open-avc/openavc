"""A navigation tap in Programmer Preview completes one server round trip.

The panel used to navigate locally and then repeat the move when its WebSocket
reply arrived. Overlay counts and Back history expose what ordinary page changes
hide. This runs against the actual server, not a socket stub that discards replies.
"""

from playwright.sync_api import expect


def _page(page_id, elements, page_type="page"):
    return {
        "id": page_id, "name": page_id.title(), "page_type": page_type,
        "elements": elements,
        "layouts": [{
            "id": "landscape", "orientation": "landscape", "primary": True,
            "placements": {
                element["id"]: {"x": 10, "y": 10 + index * 25, "w": 60, "h": 20}
                for index, element in enumerate(elements)
            }, "hidden": [],
        }],
        "overlay": {"width": 60, "height": 60, "dismiss_on_backdrop": True},
    }


def _nav(element_id, label, target):
    return {"id": element_id, "type": "page_nav", "label": label, "target_page": target}


def test_preview_navigation_opens_and_dismisses_once(server_factory, page):
    handle = server_factory(project_overrides={
        "openavc_version": "0.13.0",
        "ui": {"settings": {}, "master_elements": [], "pages": [
            _page("main", [_nav("details_nav", "Details", "details")]),
            _page("details", [
                _nav("help_nav", "Help", "help"),
                _nav("previous_nav", "Previous", "$back"),
            ]),
            _page("help", [_nav("close_nav", "Close", "$back")], "overlay"),
        ]},
    })
    page.goto(f"{handle.base_url}/programmer/#ui-builder")
    page.get_by_role("button", name="Preview", exact=True).click()
    panel = page.frame_locator("iframe")
    panel.get_by_role("button", name="Navigate to Details", exact=True).click()
    panel.get_by_role("button", name="Navigate to Help", exact=True).click()
    expect(panel.locator(".panel-overlay")).to_have_count(1)
    panel.get_by_role("button", name="Navigate to Close", exact=True).click()
    expect(panel.locator(".panel-overlay")).to_have_count(0)
    expect(panel.get_by_role("button", name="Navigate to Help", exact=True)).to_be_visible()
    panel.get_by_role("button", name="Navigate to Previous", exact=True).click()
    expect(panel.get_by_role("button", name="Navigate to Details", exact=True)).to_be_visible()
