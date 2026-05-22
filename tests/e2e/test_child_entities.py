"""Playwright tests for the Programmer IDE Child Entities tab.

Covers the four scenarios from openavc-device-children-plan.md §Test plan
-> IDE (Playwright). Each test spawns a real ``server.main`` subprocess
seeded with the ``e2e_test_controller`` synthetic driver (declared in
``_controller_driver_src.py``, copied into ``driver_repo/`` for the session
by conftest). Tests navigate to the device detail view in a real Chromium,
exercise the virtualized list directly, and assert on user-visible state
plus performance markers.

Selectors come from the data-testid attributes in
``web/programmer/src/views/devices/ChildEntities.tsx``: see the docstring
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
