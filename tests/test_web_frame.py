"""Behaviour tests for the panel's web element.

The web element is a window onto somebody else's page: the url drawn in the
same sandboxed frame the plugin and custom elements use, **with no bridge** --
the remote page receives nothing and listens for nothing, and a show.value
binding switches the source at runtime. These run the real panel (panel.js
bundled with esbuild) in a jsdom document via ``tests/fixtures/web_harness.cjs``,
because "the sandbox attribute is the whole isolation story" and "a no-bridge
frame registers no listener" are claims about behaviour, not source text.

web 元素的行为测试：把他人页面画进与 plugin/custom 相同的沙箱帧，**无桥**——
远程页面收不到任何东西也不监听任何东西，show.value 绑定在运行时换源。测试
经 tests/fixtures/web_harness.cjs 在 jsdom 文档里运行真实面板（esbuild 打包
panel.js），因为"沙箱属性是全部隔离手段"与"无桥帧不注册监听器"是关于行为
而非源码文本的声明。

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

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "web_harness.cjs"
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
        return "web harness missing"
    if not PANEL_JS.is_file():
        return "panel.js missing"
    return None


@pytest.fixture(scope="module")
def web_results() -> dict:
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
        raise AssertionError(f"web harness crashed (rc={proc.returncode}):\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "app_booted",
    "no_url_draws_placeholder",
    "frame_renders_with_sandbox",
    "ticked_tokens_reach_the_attribute",
    "empty_state_keeps_the_element_url",
    "state_set_switches_the_source",
    "state_at_draw_time_lands",
    "no_bridge_frame_listens_for_nothing",
    "edit_mode_stays_live",
    "non_http_override_and_binding_refused",
    "ui_override_switches_the_source",
    "override_beats_binding_beats_static",
    "override_clear_puts_the_element_url_back",
    "override_on_non_web_is_ignored",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_web_frame_behaviour(web_results: dict, scenario: str) -> None:
    result = web_results.get(scenario)
    assert result is not None, f"harness did not run scenario {scenario}"
    assert result["pass"], result["detail"]


def test_every_scenario_reported(web_results: dict) -> None:
    """A scenario added to the harness must be listed here to be enforced."""
    assert set(web_results) == set(SCENARIOS), (
        f"harness scenarios and enforced list disagree: "
        f"{sorted(set(web_results) ^ set(SCENARIOS))}"
    )
