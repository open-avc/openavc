"""Watching a per-channel reading, through both doors that offer it.

Most of what is worth watching on multi-channel gear is per-child — a level
per channel, a fault per amplifier output, signal presence per input — and
neither door could express it. The Child Entities table, which is where those
readings are actually read, had no Monitor control at all; the State tab had
one, but handed it a child key with no declaration, so a reading declared
``number, -80..0 dB`` was offered "tick the values that mean everything is
fine" and could not be given a range.

Both halves are user-visible treatment, which the default run and vitest
cannot see: this is the only place the real form is opened in a real browser
and read.

The synthetic controller's ``encoder`` type declares ``level`` for exactly this
(``_controller_driver_src.py``): a number, -80 to 0, in dB, labelled.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

MONITOR_TITLE = "Watch this reading on the Dashboard and in the cloud"
#: The numeric form's own hint. The undeclared form says "Tick the values..."
#: in the same slot, so the two are never both right.
RANGE_HINT = "Leave both blank to show the reading without judging it."
TICK_HINT = "Tick the values that mean everything is fine"


@pytest.fixture
def server(server_factory):
    """One connected controller with one encoder, so there is a child reading."""
    return server_factory(initial_children=1)


def _open_devices(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="Devices"]').click()
    page.locator('button:has-text("Test Controller")').first.wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button:has-text("Test Controller")').first.click()
    page.locator('[data-testid="child-type-tab-encoder"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )


def test_a_child_reading_can_be_watched_from_the_table_it_is_read_in(
    server, page: Page,
):
    """Expand an encoder, tag its level, and get the range form for it."""
    _open_devices(page, server.base_url)

    page.locator('[data-testid="child-expand-001"]').click()
    row = page.locator('[data-testid="child-row-001"]')
    level = row.locator('tr:has(td:text-is("level"))')
    expect(level).to_be_visible(timeout=EXPECT_TIMEOUT)

    # The control exists at all — it did not.
    monitor = level.locator(f'button[title="{MONITOR_TITLE}"]')
    expect(monitor).to_be_visible(timeout=EXPECT_TIMEOUT)
    monitor.click()

    # Tagged, and the row now offers the limits it could not before.
    expect(row.locator('button:has-text("Set what normal looks like")')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    row.locator('button:has-text("Set what normal looks like")').click()

    # The numeric form, pre-filled with what the driver declared about THIS
    # reading. Under the fault, both bounds were absent and a tick-list stood
    # in their place.
    expect(row.locator('input[placeholder="-80"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(row.locator("text=" + RANGE_HINT)).to_be_visible(timeout=EXPECT_TIMEOUT)
    assert row.locator(f"text={TICK_HINT}").count() == 0

    # "Shown as" names the channel, not just the reading: eight encoders
    # carrying one label is a Dashboard nobody can read.
    expect(
        row.locator('input[placeholder="Encoder 1 · Output Level"]'),
    ).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(row.locator('input[placeholder="dB"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )

    # And the row itself now says something in it is watched, so it can be
    # found again without opening every child.
    expect(page.locator('[data-testid="child-monitored-001"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )


def test_the_state_tab_knows_what_a_child_reading_is(server, page: Page):
    """The other door: State -> Device States, on the same key."""
    page.goto(f"{server.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="State"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="State"]').click()
    page.locator('button:has-text("Device States")').first.click()
    page.locator('text=Test Controller').first.click()

    row = page.locator('div:has(> div > div > code:text-is("encoder.001.level"))').last
    expect(row).to_be_visible(timeout=EXPECT_TIMEOUT)
    # The declaration reached the row: a type badge and a name, neither of
    # which a child key used to get.
    expect(row).to_contain_text("number", timeout=EXPECT_TIMEOUT)
    expect(row).to_contain_text("Encoder 1 · Output Level")

    row.click()
    detail = page.locator(f'button[title="{MONITOR_TITLE}"]')
    expect(detail).to_be_visible(timeout=EXPECT_TIMEOUT)
    detail.click()
    page.locator('button:has-text("Set what normal looks like")').click()

    expect(page.locator('input[placeholder="-80"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(page.locator("text=" + RANGE_HINT)).to_be_visible(timeout=EXPECT_TIMEOUT)
    assert page.locator(f"text={TICK_HINT}").count() == 0
