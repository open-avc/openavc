"""A paused device and a disabled one, as a running server publishes them.

The Dashboard's tiles and cards, the device list's filters and the device's
own page read each device's presence word, so a device Live Test pauses is
Paused on every one of them, and a device the project turns off is Disabled,
never a red dot under Offline. Three devices: one paused here, one left
running, one turned off in the project.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect


SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000


def _device(device_id: str, name: str, enabled: bool = True) -> dict:
    return {
        "id": device_id,
        "driver": "e2e_test_controller",
        "name": name,
        "config": {"initial_children": 0},
        "enabled": enabled,
        "pending_settings": {},
        "child_entities": {},
    }


@pytest.fixture
def server(server_factory):
    return server_factory(project_overrides={"devices": [
        _device("ctrl1", "Paused Controller"),
        _device("ctrl2", "Running Controller"),
        _device("ctrl3", "Spare Controller", enabled=False),
    ]})


def _tile_dot(page: Page, name: str):
    return page.locator(f'[role="button"]:has-text("{name}") span[title]').first


def test_a_paused_and_a_disabled_device_read_the_same_on_every_screen(server, page: Page):
    page.goto(f"{server.base_url}/programmer/", wait_until="domcontentloaded")
    expect(_tile_dot(page, "Running Controller")).to_have_attribute(
        "title", "Connected", timeout=SELECT_TIMEOUT,
    )
    resp = page.request.post(f"{server.base_url}/api/devices/ctrl1/pause")
    assert resp.ok, resp.text()

    # The Dashboard: amber for the pause, grey for the device turned off, and
    # neither among the disconnected.
    expect(_tile_dot(page, "Paused Controller")).to_have_attribute(
        "title", "Paused for Live Test", timeout=EXPECT_TIMEOUT,
    )
    expect(_tile_dot(page, "Spare Controller")).to_have_attribute("title", "Disabled")
    expect(page.get_by_text(re.compile(r"\d+ devices? disconnected"))).to_have_count(0)

    # The device list: a filter for each, and none of them under Offline.
    page.locator('button[aria-label="Devices"]').click()
    expect(page.get_by_role("button", name="Paused 1")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_role("button", name="Disabled 1")).to_be_visible()
    expect(page.get_by_role("button", name="Online 1")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile(r"^Offline"))).to_have_count(0)

    # The paused device's own page says the same, with its way out.
    page.locator('button:has-text("Paused Controller")').first.click()
    expect(page.locator("h2", has_text="Paused Controller")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text("Paused for Live Test in the Driver Builder.", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Resume")).to_be_visible()
