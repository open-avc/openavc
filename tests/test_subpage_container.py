"""Behaviour tests for the panel's sub-page container.

The container is a window onto a sub-page: it draws one sub-page's elements
inside its box, a ``ui.subpage`` frame swaps which one shows, and a sub-page
is never a navigation destination. These run the real panel (panel.js and its
modules bundled with esbuild) in a jsdom document via
``tests/fixtures/subpage_harness.cjs``, because "the switch lives on the
surface the container was drawn on, even when it arrives outside a draw
pass" is a lifetime claim, not a regex.

子页面容器的行为测试：容器把某个子页面的元素绘制进自己的框内，ui.subpage
帧切换显示的子页面，而子页面永远不是导航目的地。测试通过
tests/fixtures/subpage_harness.cjs 在 jsdom 文档里运行真实面板（esbuild 打
包 panel.js 及其 modules），因为"切换到达时即使不在绘制期，其 Surface 仍
随容器绘制所在的宿主结束"是生命周期声明，正则读不出来。

Like the other TypeScript harnesses these skip when Node/esbuild/jsdom aren't
installed, and fail instead when a run promised them (OPENAVC_REQUIRE_NODE=1).
Run them locally after `npm ci` in openavc/web/programmer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "subpage_harness.cjs"
PANEL_JS = OPENAVC_ROOT / "openavc" / "web" / "panel" / "panel.js"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not (NODE_MODULES / "esbuild").is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not (NODE_MODULES / "jsdom").is_dir():
        return "jsdom not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "subpage harness missing"
    if not PANEL_JS.is_file():
        return "panel.js missing"
    return None


@pytest.fixture(scope="module")
def subpage_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(PANEL_JS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(f"subpage harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "app_booted",
    "initial_draw_shows_default",
    "switch_frame_swaps_content",
    "switch_to_a_regular_page_is_refused",
    "switch_to_unknown_id_is_ignored",
    "navigation_to_a_subpage_does_nothing",
    "host_surface_end_takes_the_container_down",
    "switch_outside_a_draw_lives_on_the_original_host",
    "container_without_default_receives_first_switch",
    "stale_memory_falls_back_to_default",
    "cycle_draws_empty_instead_of_overflowing",
    "twin_instances_live_and_switch_together",
    "fallback_skips_subpages_on_the_glass",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_subpage_container_behaviour(subpage_results: dict, scenario: str) -> None:
    result = subpage_results.get(scenario)
    assert result is not None, f"harness did not run scenario {scenario}"
    assert result["pass"], result["detail"]


def test_every_scenario_reported(subpage_results: dict) -> None:
    """A scenario added to the harness must be listed here to be enforced."""
    assert set(subpage_results) == set(SCENARIOS), (
        f"harness scenarios and enforced list disagree: "
        f"{sorted(set(subpage_results) ^ set(SCENARIOS))}"
    )
