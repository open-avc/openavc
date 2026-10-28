"""A device tile on the Dashboard opens that device, not just the Devices screen.

Two devices, and the tile clicked is the second: with one device a tile that
only switched screens would look the same as one that opened the device.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000


def _device(device_id: str, name: str) -> dict:
    return {
        "id": device_id,
        "driver": "e2e_test_controller",
        "name": name,
        "config": {"initial_children": 0},
        "enabled": True,
        "pending_settings": {},
        "child_entities": {},
    }


@pytest.fixture
def server(server_factory):
    return server_factory(project_overrides={"devices": [
        _device("ctrl1", "Test Controller"),
        _device("ctrl2", "Second Controller"),
    ]})


def test_a_device_tile_opens_that_device(server, page: Page):
    page.goto(f"{server.base_url}/programmer/", wait_until="domcontentloaded")
    tile = page.locator('[role="button"]:has-text("Second Controller")')
    tile.wait_for(state="visible", timeout=SELECT_TIMEOUT)
    tile.click()

    expect(page.locator("h2", has_text="Second Controller")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text("Select a device to view details")).to_have_count(0)
    assert page.evaluate("() => location.hash") == "#devices"


def test_a_device_tile_opens_from_the_keyboard(server, page: Page):
    page.goto(f"{server.base_url}/programmer/", wait_until="domcontentloaded")
    tile = page.locator('[role="button"]:has-text("Second Controller")')
    tile.wait_for(state="visible", timeout=SELECT_TIMEOUT)
    tile.focus()
    page.keyboard.press("Enter")

    expect(page.locator("h2", has_text="Second Controller")).to_be_visible(timeout=EXPECT_TIMEOUT)
