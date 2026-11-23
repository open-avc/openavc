"""A tapped list row lights at once on the served panel, and a static list
shows the row its selection reports.

A selectable list with a selection binding (its reading on another row), one
with none, and a static list bound to the same reading, on a real panel page
in Chromium. Before the device answers, the row tapped is the one lit; a list
with no binding keeps the row tapped; a static list lights the reported row
and takes no tap.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect  # noqa: E402

TIMEOUT = 20_000

ROWS = [{"value": "a", "label": "Lobby"}, {"value": "b", "label": "Board Room"},
        {"value": "c", "label": "Hall"}]


def _list(eid: str, style: str, bound: bool) -> dict[str, Any]:
    element: dict[str, Any] = {"id": eid, "type": "list", "list_style": style, "items": ROWS}
    if bound:
        element["bindings"] = {"show": {"value": {"key": "var.room"}}}
    return element


def _project() -> dict[str, Any]:
    elements = [
        _list("bound", "selectable", True),
        _list("unbound", "selectable", False),
        _list("readonly", "static", True),
    ]
    return {
        "openavc_version": "0.18.0",
        "variables": [{"id": "room", "type": "string", "default": "a"}],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "master_elements": [], "page_groups": [],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": {
                    eid["id"]: {"x": 2 + i * 32, "y": 10, "w": 30, "h": 60}
                    for i, eid in enumerate(elements)
                }}],
                "elements": elements,
            }],
        },
    }


def _row(page: Page, eid: str, value: str):
    return page.locator(f'[data-element-id="{eid}"] .list-item[data-value="{value}"]')


def _lit(page: Page, eid: str) -> list[str]:
    return page.locator(f'[data-element-id="{eid}"] .list-item.active').evaluate_all(
        "(rows) => rows.map((row) => row.dataset.value)"
    )


def test_a_tapped_row_lights_at_once(server_factory, page: Page) -> None:
    handle = server_factory(project_overrides=_project())
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    expect(_row(page, "bound", "a")).to_have_class("list-item active", timeout=TIMEOUT)

    _row(page, "bound", "b").click()
    expect(_row(page, "bound", "b")).to_have_class("list-item active", timeout=2_000)
    assert _lit(page, "bound") == ["b"]

    _row(page, "unbound", "c").click()
    expect(_row(page, "unbound", "c")).to_have_class("list-item active", timeout=2_000)
    assert _lit(page, "unbound") == ["c"]


def test_a_static_list_shows_its_reported_row_and_takes_no_tap(server_factory, page: Page) -> None:
    handle = server_factory(project_overrides=_project())
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    expect(_row(page, "readonly", "a")).to_have_class("list-item active", timeout=TIMEOUT)

    _row(page, "readonly", "c").click()
    page.wait_for_timeout(300)
    assert _lit(page, "readonly") == ["a"]
