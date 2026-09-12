"""Wall-clock budgets for the Child Entities list at full scale.

These are stopwatch assertions, and they are out of the CI gate on purpose —
the same call `tests/perf/` already makes for the pure-Python benchmarks, and
for the same reason: a shared runner's speed is not the thing under test. The
longtask budget in particular is measured against the Long Tasks API, whose
smallest reportable entry is exactly the 50ms the budget allows, so a busy
machine trips it by the narrowest margin the browser can express. That happened
on `main` in September 2026 with nothing wrong: the re-run passed untouched,
having cost a quarter of an hour to learn nothing.

What is *functionally* true at 1500 children — the roster mounts, the tab counts
them, and the filter still finds a row deep in the list — is asserted in
`tests/e2e/test_child_entities.py`, in the gate, where a real regression in
virtualization shows up as a failure that means something.

Run them deliberately, on a machine that is otherwise idle:

    pytest tests/e2e/perf -o addopts=""

The numbers are the acceptance criterion for the virtualized list: opening must
not do work proportional to N, and no interaction may block the main thread past
a frame budget. Treat a failure here as a question ("is the machine busy, or did
the render path regress?"), never as a gate.
"""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.test_child_entities import EXPECT_TIMEOUT, _open_device


# Opening 1500 children covers the WS handshake, the initial GET /children and
# the first render. Generous on purpose: this catches initial work that scales
# with N, not a slow second.
OPEN_BUDGET_S = 12.0

# The Long Tasks API reports nothing below 50ms, so this is both the budget and
# the floor of what can be observed. An entry at exactly 50 is the smallest
# violation the browser is able to report.
LONGTASK_BUDGET_MS = 50


def test_opening_fifteen_hundred_children_is_not_proportional_to_n(
    server_factory, page: Page,
) -> None:
    handle = server_factory(initial_children=1500)
    # Wiring 1500 register_child calls on connect plus the initial round trip
    # takes longer than the 100-child case.
    page.set_default_timeout(30_000)

    t_open_start = time.monotonic()
    _open_device(page, handle.base_url, "Test Controller")
    open_elapsed = time.monotonic() - t_open_start

    assert open_elapsed < OPEN_BUDGET_S, (
        f"Opening device with 1500 children took {open_elapsed:.1f}s "
        f"(budget: {OPEN_BUDGET_S:.0f}s). If this regressed, the virtualization "
        f"is doing initial work proportional to N — it shouldn't."
    )


def test_scrolling_fifteen_hundred_children_never_blocks_a_frame(
    server_factory, page: Page,
) -> None:
    handle = server_factory(initial_children=1500)
    page.set_default_timeout(30_000)
    _open_device(page, handle.base_url, "Test Controller")

    encoder_tab = page.locator('[data-testid="child-type-tab-encoder"]')
    expect(encoder_tab).to_contain_text("1500", timeout=EXPECT_TIMEOUT)

    # Install a longtask observer. Any entry that shows up after the
    # interactions below means the virtualization or render path blocked the
    # main thread past the budget.
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

    # A sequence of scrolls plus a filter. Each step gives the browser a moment
    # to render so PerformanceObserver can flush entries.
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
    overruns = [t for t in long_tasks if t["duration"] >= LONGTASK_BUDGET_MS]
    assert not overruns, (
        f"Main thread blocked >{LONGTASK_BUDGET_MS}ms during virtualization "
        f"interaction: {overruns}"
    )
