"""Playwright tests for the Programmer IDE Child Entities tab.

Covers the four Child Entities tab scenarios. Each test spawns a real ``openavc.main`` subprocess
seeded with the ``e2e_test_controller`` synthetic driver (declared in
``_controller_driver_src.py``, copied into ``driver_repo/`` for the session
by conftest). Tests navigate to the device detail view in a real Chromium,
exercise the virtualized list directly, and assert on user-visible state
plus performance markers.

Selectors come from the data-testid attributes in
``openavc/web/programmer/src/views/devices/ChildEntities.tsx``: see the docstring
on that component for the canonical list.
"""

from __future__ import annotations

import json
import time

import pytest
from playwright.sync_api import Page, expect


# Programmer IDE is served at /programmer; the React app lazy-loads the
# Devices view on demand, so each navigation gets a generous timeout.
SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000


def _open_device(page: Page, base_url: str, device_name: str) -> None:
    """Navigate to the Programmer IDE, open Devices, click the test device."""
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    # Wait for sidebar to render. The Programmer SPA reads its initial
    # /api/project state via WS; on a fresh subprocess, that can take a
    # second or two.
    page.locator('button[aria-label="Devices"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="Devices"]').click()
    page.locator(f'button:has-text("{device_name}")').first.wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator(f'button:has-text("{device_name}")').first.click()
    # Child Entities heading is the anchor we wait on — the encoder tab
    # only appears once the list response has populated.
    page.locator('[data-testid="child-type-tab-encoder"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Test 1 — 100 children: virtualization + filter
# ---------------------------------------------------------------------------

def test_one_hundred_children_renders_and_filters(server_factory, page: Page):
    """100 encoders register on connect. The virtualized list renders only
    the visible window, scrolling reveals later rows, and the search box
    filters by id/label/name.
    """
    handle = server_factory(initial_children=100)
    _open_device(page, handle.base_url, "Test Controller")

    # Tab shows the correct count.
    encoder_tab = page.locator('[data-testid="child-type-tab-encoder"]')
    expect(encoder_tab).to_contain_text("100", timeout=EXPECT_TIMEOUT)

    # Row 1 is visible (top of the list).
    expect(page.locator('[data-testid="child-row-001"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    # Row 100 isn't rendered yet — virtualization only mounts the window.
    assert page.locator('[data-testid="child-row-100"]').count() == 0

    # Scroll the virtualizer to the bottom and verify row 100 mounts.
    scroller = page.locator('[data-testid="child-virtual-scroller"]')
    scroller.evaluate("(el) => { el.scrollTop = el.scrollHeight; }")
    expect(page.locator('[data-testid="child-row-100"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    # After scrolling to the bottom, the top row has been recycled out
    # of the DOM — proves the virtualization window is working.
    assert page.locator('[data-testid="child-row-001"]').count() == 0

    # Reset scroll so the filter test below sees the same DOM regardless
    # of where the previous step left us.
    scroller.evaluate("(el) => { el.scrollTop = 0; }")

    # Filter by a specific id: only that row's child-row testid appears.
    search = page.locator('[data-testid="device-filter"]')
    search.fill("042")
    expect(page.locator('[data-testid="child-row-042"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    # Sanity: a row that doesn't match the filter is gone.
    assert page.locator('[data-testid="child-row-001"]').count() == 0

    # Clear filter — row 1 is reachable again (scroll to top first).
    search.fill("")
    scroller.evaluate("(el) => { el.scrollTop = 0; }")
    expect(page.locator('[data-testid="child-row-001"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Test 2 — 1500 children: virtualization stays responsive
# ---------------------------------------------------------------------------

def test_fifteen_hundred_children_stays_responsive(server_factory, page: Page):
    """Chazy max per-type is ~1500. The list must mount in a reasonable
    time and scrolling/filtering must not produce any longtask
    (>50ms main-thread block) per the plan's acceptance criterion.
    """
    handle = server_factory(initial_children=1500)
    # Increase navigation budget — wiring 1500 register_child calls on
    # connect plus the initial GET /children round trip takes longer than
    # the 100-child case.
    page.set_default_timeout(30_000)

    t_open_start = time.monotonic()
    _open_device(page, handle.base_url, "Test Controller")
    open_elapsed = time.monotonic() - t_open_start

    # Total time from goto() through "encoders tab visible" includes
    # WS handshake + initial fetch + render. 12s is generous; if this
    # regresses, the virtualization is doing initial work proportional
    # to N (it shouldn't).
    assert open_elapsed < 12.0, (
        f"Opening device with 1500 children took {open_elapsed:.1f}s "
        f"(budget: 12s)"
    )

    encoder_tab = page.locator('[data-testid="child-type-tab-encoder"]')
    expect(encoder_tab).to_contain_text("1500", timeout=EXPECT_TIMEOUT)

    # Install a longtask observer. The Long Tasks API only reports tasks
    # over 50ms — if any entry shows up after our interactions, the
    # virtualization or render path is blocking the main thread past
    # the budget.
    page.evaluate(
        """
        () => {
            window.__longTasks = [];
            try {
                const obs = new PerformanceObserver((list) => {
                    for (const entry of list.getEntries()) {
                        window.__longTasks.push({
                            name: entry.name,
                            duration: entry.duration,
                            startTime: entry.startTime,
                        });
                    }
                });
                obs.observe({entryTypes: ['longtask']});
                window.__longTaskObs = obs;
            } catch (e) {
                window.__longTaskUnsupported = String(e);
            }
        }
        """
    )

    scroller = page.locator('[data-testid="child-virtual-scroller"]')

    # Drive a sequence of scrolls + a filter. Each step gives the browser
    # a moment to render so PerformanceObserver can flush entries.
    for top in (0, 5000, 25000, 0, 50000):
        scroller.evaluate(f"(el) => {{ el.scrollTop = {top}; }}")
        page.wait_for_timeout(120)

    search = page.locator('[data-testid="device-filter"]')
    search.fill("Encoder 750")
    page.wait_for_timeout(200)
    expect(page.locator('[data-testid="child-row-750"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    search.fill("")
    page.wait_for_timeout(200)

    long_tasks = page.evaluate("window.__longTasks || []")
    unsupported = page.evaluate("window.__longTaskUnsupported || null")
    if unsupported:
        pytest.skip(f"Long Tasks API unavailable: {unsupported}")
    overruns = [t for t in long_tasks if t["duration"] >= 50]
    assert not overruns, (
        f"Main thread blocked >50ms during virtualization interaction: "
        f"{overruns}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Edit a child label, reload, verify persistence
# ---------------------------------------------------------------------------

def test_label_edit_persists_across_reload(server_factory, page: Page):
    """Clicking a label opens an inline input. Saving (blur or Enter)
    PATCHes the project file. After a full page reload, the new label is
    rendered from the persisted project metadata, not a stale store.
    """
    handle = server_factory(initial_children=5)
    _open_device(page, handle.base_url, "Test Controller")

    # Pick row 3 — middle of the small list. The label cell is a
    # button until edited; click it to start editing.
    page.locator('[data-testid="child-label-003"]').click()
    label_input = page.locator('[data-testid="child-label-input-003"]')
    expect(label_input).to_be_visible(timeout=EXPECT_TIMEOUT)
    label_input.fill("Lobby Encoder")
    label_input.press("Enter")

    # The PATCH happens async via onBlur/Enter; wait for the underlying
    # request to settle. Asserting on the button text round-trips through
    # state + project save, which gives us a deterministic anchor.
    expect(page.locator('[data-testid="child-label-003"]')).to_have_text(
        "Lobby Encoder", timeout=EXPECT_TIMEOUT,
    )

    # Confirm the project file on disk picked it up (the IDE writes
    # through the server's PATCH endpoint, which calls save_project).
    project_data = json.loads(handle.project_path.read_text(encoding="utf-8"))
    device_entry = next(d for d in project_data["devices"] if d["id"] == "ctrl1")
    assert device_entry["child_entities"]["encoder"]["003"]["label"] == \
        "Lobby Encoder"

    # Full reload — destroys all in-memory store state. The label must
    # come back from the project file via the /children REST call.
    page.reload(wait_until="domcontentloaded")
    _open_device(page, handle.base_url, "Test Controller")
    expect(page.locator('[data-testid="child-label-003"]')).to_have_text(
        "Lobby Encoder", timeout=EXPECT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Test 4 — Driver-side add then remove updates UI without page refresh
# ---------------------------------------------------------------------------

def test_driver_add_and_remove_updates_ui(server_factory, page: Page):
    """The driver's control-file watcher applies add/remove ops at
    runtime. The IDE re-fetches via the Refresh from Device button so
    new children appear and removed children disappear without a full
    browser reload.
    """
    handle = server_factory(initial_children=2)
    _open_device(page, handle.base_url, "Test Controller")

    encoder_tab = page.locator('[data-testid="child-type-tab-encoder"]')
    expect(encoder_tab).to_contain_text("2", timeout=EXPECT_TIMEOUT)
    expect(page.locator('[data-testid="child-row-002"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    assert page.locator('[data-testid="child-row-007"]').count() == 0

    # Tell the driver to add encoder 7. The control-file watcher polls
    # every 200ms; give it a beat before asking the IDE to refresh.
    handle.write_ops([{
        "op": "add", "child_type": "encoder", "local_id": 7,
        "initial_state": {
            "name": "Added Encoder 7", "ip": "10.0.0.7", "signal_present": True,
        },
    }])
    time.sleep(0.5)

    page.locator('[data-testid="child-driver-refresh"]').click()
    expect(encoder_tab).to_contain_text("3", timeout=EXPECT_TIMEOUT)
    expect(page.locator('[data-testid="child-row-007"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )

    # Now remove encoder 1 the same way.
    handle.write_ops([{
        "op": "remove", "child_type": "encoder", "local_id": 1,
    }])
    time.sleep(0.5)
    page.locator('[data-testid="child-driver-refresh"]').click()
    expect(encoder_tab).to_contain_text("2", timeout=EXPECT_TIMEOUT)
    # Row 1 is gone from the DOM after the re-fetch settles.
    expect(page.locator('[data-testid="child-row-001"]')).to_have_count(
        0, timeout=EXPECT_TIMEOUT,
    )
    # Row 7 is still there (only encoder 1 was removed).
    expect(page.locator('[data-testid="child-row-007"]')).to_be_visible()


# ── A sub-unit that is not answering ────────────────────────────────────────
#
# The reason this exists: an MXNet decoder stopped passing video, the tab said
# `online: false` in a monospace column, and an afternoon went into hunting a
# power fault that was not there. These drive the real server and a real
# Chromium, because the whole point is what a person SEES on the page.

def test_a_wedged_endpoint_is_visible_without_opening_anything(
    page: Page, server_factory,
) -> None:
    """The device page itself says which sub-units are in trouble.

    Not a column inside a collapsed panel: the banner is on the page the way
    an offline device's reason is on its card.
    """
    handle = server_factory(initial_children=4)
    _open_device(page, handle.base_url, "Test Controller")

    # Nothing wrong: no banner at all. A healthy device must not carry one,
    # the way a connected device carries no offline banner.
    expect(page.locator('[data-testid="child-trouble-banner"]')).to_have_count(0)

    handle.write_ops([{
        "op": "fault", "child_type": "encoder", "local_id": 3,
        "code": "service_fault",
    }])

    banner = page.locator('[data-testid="child-trouble-banner"]')
    expect(banner).to_be_visible(timeout=EXPECT_TIMEOUT)
    # Counted against the roster, named the way the list names it, and worded
    # for what is actually wrong. This endpoint IS answering -- its service is
    # what has hung -- so "not answering" would send somebody to the wrong
    # remedy, which is the entire reason the code exists.
    expect(banner).to_contain_text("1 of 4 encoder is reachable, but not running")
    expect(banner).to_contain_text("Encoder 3")


def test_an_empty_slot_is_not_reported_as_a_fault(
    page: Page, server_factory,
) -> None:
    """A position with nothing in it is marked, and counted nowhere.

    Some rosters are slots rather than channels -- the extension positions on
    a chained mixer, the card slots on a frame. Reporting those as down would
    trade one false alarm (green dots on hardware that does not exist) for
    another, so the mark, the badge and the banner have to disagree here on
    purpose.
    """
    handle = server_factory(initial_children=4)
    _open_device(page, handle.base_url, "Test Controller")

    handle.write_ops([{
        "op": "fault", "child_type": "encoder", "local_id": 2,
        "code": "not_fitted",
    }])

    row = page.locator('[data-testid="child-row-002"]')
    dot = row.locator('[data-testid="child-presence-dot"]')
    expect(dot).to_have_attribute("data-reason", "not_fitted", timeout=EXPECT_TIMEOUT)
    # Not in service, and not trouble: two different questions.
    expect(dot).to_have_attribute("data-ok", "false")
    expect(dot).to_have_attribute("data-trouble", "false")

    # No badge, no banner, and it stays where the roster put it.
    expect(page.locator('[data-testid="child-type-down-encoder"]')).to_have_count(0)
    expect(page.locator('[data-testid="child-trouble-banner"]')).to_have_count(0)
    first_row = page.locator('[data-testid^="child-row-"]').first
    expect(first_row).to_have_attribute("data-testid", "child-row-001")


def test_the_row_and_the_tab_agree_with_the_banner(
    page: Page, server_factory,
) -> None:
    """Three surfaces, one rule -- a red row with a calm tab, or a banner over
    a list showing nothing wrong, is worse than any of them alone."""
    handle = server_factory(initial_children=4)
    _open_device(page, handle.base_url, "Test Controller")

    handle.write_ops([{
        "op": "fault", "child_type": "encoder", "local_id": 3,
        "code": "not_responding",
    }])

    expect(page.locator('[data-testid="child-type-down-encoder"]')).to_contain_text(
        "1 down", timeout=EXPECT_TIMEOUT,
    )
    # The bad row is lifted to the top of the list, whatever its roster order.
    first_row = page.locator('[data-testid^="child-row-"]').first
    expect(first_row).to_have_attribute(
        "data-testid", "child-row-003", timeout=EXPECT_TIMEOUT,
    )
    dot = first_row.locator('[data-testid="child-presence-dot"]')
    expect(dot).to_have_attribute("data-ok", "false")
    expect(dot).to_have_attribute("data-reason", "not_responding")
    # The taxonomy's sentence reached the browser, not just the code.
    assert "power" in (dot.get_attribute("title") or "").lower()


def test_a_recovered_endpoint_clears_everything(
    page: Page, server_factory,
) -> None:
    """A fault nothing ever clears makes one transient outage look permanent
    for as long as the system stays up."""
    handle = server_factory(initial_children=4)
    _open_device(page, handle.base_url, "Test Controller")

    handle.write_ops([{
        "op": "fault", "child_type": "encoder", "local_id": 2,
        "code": "not_responding",
    }])
    expect(page.locator('[data-testid="child-trouble-banner"]')).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )

    handle.write_ops([{
        "op": "fault", "child_type": "encoder", "local_id": 2, "code": "",
    }])

    expect(page.locator('[data-testid="child-trouble-banner"]')).to_have_count(
        0, timeout=EXPECT_TIMEOUT,
    )
    expect(page.locator('[data-testid="child-type-down-encoder"]')).to_have_count(0)
