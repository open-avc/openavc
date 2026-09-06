"""A reading the device has stopped sending says so, on the page that keeps it.

The device page holds the last value it heard while the device is unreachable,
and that is deliberate -- the panel blanks such a reading because a room-facing
control must not assert a wrong number, but this page is where somebody
diagnoses, and "it was 511 when we lost it" is often what they came for. What
was missing is the qualifier: an unmarked 511 in a LEVEL column reads as
current, and the Offline banner that would say otherwise is a hundred state
keys further up a page that has scrolled past it.

Which is why this is a browser test and not a unit test. It is a treatment on
a value in a table, and the thing it has to get right is which values -- the
platform's own keys (connected, a child's online, the fault codes) are true
right now, and calling those "last heard" would be nonsense.

The device is taken offline the way the bench observation happened: paused,
which disconnects cleanly and leaves every reading in place.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000


@pytest.fixture
def server(server_factory):
    """A connected controller with one encoder: one device-level reading
    (`temperature`) and one child reading (`level`), both real values."""
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


def test_a_connected_device_marks_nothing(server, page: Page):
    _open_device(page, server.base_url)
    page.locator('[data-testid="child-expand-001"]').click()
    expect(page.locator('[data-testid="child-row-001"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    # Everything on this page is current, so nothing claims otherwise.
    assert page.locator('[data-testid="last-heard"]').count() == 0
    assert page.locator('[data-stale="true"]').count() == 0


def test_an_unreachable_device_says_its_readings_are_the_last_ones_heard(
    server, page: Page,
):
    _open_device(page, server.base_url)
    page.locator('[data-testid="child-expand-001"]').click()
    expect(page.locator('[data-testid="child-row-001"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )

    # Pause it: a clean disconnect that suppresses auto-reconnect and leaves
    # every reading exactly where it was. The page follows over the WebSocket.
    resp = page.request.post(f"{server.base_url}/api/devices/ctrl1/pause")
    assert resp.ok, resp.text()

    # The device's own declared reading: still 41.5, and now qualified.
    temperature = page.locator('[data-testid="live-state-temperature"]')
    expect(temperature).to_contain_text("41.5", timeout=EXPECT_TIMEOUT)
    expect(temperature.locator('[data-testid="last-heard"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )

    # The platform's own keys are statements about the device that are true
    # right now -- `connected false` is not a stale reading, it is the reason
    # the others are stale.
    for key in ("connected", "enabled", "name"):
        row = page.locator(f'[data-testid="live-state-{key}"]')
        expect(row).to_be_visible(timeout=EXPECT_TIMEOUT)
        assert row.locator('[data-testid="last-heard"]').count() == 0, key

    # The child's summary column dims rather than carrying the words -- three
    # numbers in a row have no space for them, and the status mark beside them
    # says why.
    child = page.locator('[data-testid="child-row-001"]')
    expect(child.locator('[data-stale="true"]').first).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )

    # The expanded reading gets the words, and the child's own platform keys
    # do not.
    level = child.locator('tr:has(td:text-is("level"))')
    expect(level).to_contain_text("-6.5")
    expect(level.locator('[data-testid="last-heard"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    online = child.locator('tr:has(td:text-is("online"))')
    assert online.locator('[data-testid="last-heard"]').count() == 0
