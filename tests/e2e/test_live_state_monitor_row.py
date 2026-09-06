"""The Monitor controls on the Live State list fit on one line.

Once a reading is monitored a second control appears beside the Monitor
button, and the column it shares is sized to its contents. Without `nowrap`
the browser gives that column about 40px and "Set what normal looks like"
wraps one word per line, taking the row to five and misaligning the list.

This is a measurement, not a rule that can be read off the source: only a real
browser decides how a shrink-to-fit table column splits between two controls,
which is why the same fault was fixed once in the Child Entities table and
stayed live on the device's own list for as long as it did.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

MONITOR_TITLE = "Watch this reading on the Dashboard and in the cloud"
LIMITS_LABEL = "Set what normal looks like"

#: Both controls are 11px text in a 2px-padded box, so one line is about 20px
#: and the wrapped form was five of them. Anything at or over this is wrapping.
ONE_LINE_MAX_PX = 32


@pytest.fixture
def server(server_factory):
    """A connected controller with one encoder, so both tables have a row."""
    return server_factory(initial_children=1)


def _open_device(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="Devices"]').click()
    page.locator('button:has-text("Test Controller")').first.click()
    page.locator('[data-testid="child-type-tab-encoder"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )


def _assert_one_line(page: Page, row, where: str) -> None:
    """Monitor it, then read what the browser did with the second control."""
    monitor = row.locator(f'button[title="{MONITOR_TITLE}"]')
    expect(monitor).to_be_visible(timeout=EXPECT_TIMEOUT)
    monitor.click()

    limits = row.locator(f'button:has-text("{LIMITS_LABEL}")')
    expect(limits).to_be_visible(timeout=EXPECT_TIMEOUT)

    box = limits.bounding_box()
    assert box is not None, f"{where}: no box for the limits control"
    assert box["height"] < ONE_LINE_MAX_PX, (
        f"{where}: '{LIMITS_LABEL}' is {box['height']:.0f}px tall in a "
        f"{box['width']:.0f}px box -- it is wrapping, so the row is several "
        f"lines deep"
    )


def test_the_device_live_state_row_stays_one_line(server, page: Page):
    _open_device(page, server.base_url)
    row = page.locator('[data-testid="live-state-temperature"]')
    expect(row).to_be_visible(timeout=EXPECT_TIMEOUT)
    _assert_one_line(page, row, "Live State")


def test_the_child_reading_row_stays_one_line(server, page: Page):
    """The other table on the same page, which shares the cell component."""
    _open_device(page, server.base_url)
    page.locator('[data-testid="child-expand-001"]').click()
    child = page.locator('[data-testid="child-row-001"]')
    expect(child).to_be_visible(timeout=EXPECT_TIMEOUT)
    level = child.locator('tr:has(td:text-is("level"))')
    expect(level).to_be_visible(timeout=EXPECT_TIMEOUT)
    _assert_one_line(page, level, "Child Entities")
