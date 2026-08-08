"""Regression tests for the plugin extension renderers (PluginExtensions.tsx).

The renderers had four defects:

- The driver-id match for device panels and context actions handled only a
  single trailing ``*`` (``pattern.replace('*','')`` + ``startsWith``), so
  the documented glob syntax silently failed for leading or multiple
  wildcards and a plugin's panel/action never appeared. Driver ids now go
  through the same anchored glob compiler as state patterns.
- A status-card metric declared ``boolean`` rendered plugin-published string
  values ``'false'``/``'0'`` as ``Yes`` (JS truthiness).
- PluginLogRenderer subscribed to the hot log store array, re-rendering and
  re-filtering up to 500 entries on every log line; it now reads snapshots
  on a 1s interval.
- SurfaceViewRenderer's debounced config save had no unmount cleanup, so an
  orphaned timer could fire a config write at an arbitrary later moment; a
  pending save is now flushed deterministically on unmount (the surface
  autosaves, so dropping the edit would lose it).

Two layers: the harness bundles the real ``pluginExtensionHelpers.ts`` with
the esbuild in ``openavc/web/programmer/node_modules`` and exercises the pure logic
(skips when the Node toolchain is absent rather than failing the Python-only
CI gate), and source-level checks pin PluginExtensions.tsx to the fixed
shapes so the old patterns can't quietly come back.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_plugin_extension_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "plugin_extension_helpers_harness.cjs"
HELPERS_TS = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "plugins"
    / "pluginExtensionHelpers.ts"
)
EXTENSIONS_TSX = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "plugins"
    / "PluginExtensions.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    # A missing helpers module is the defect, not a toolchain gap.
    assert HELPERS_TS.is_file(), "pluginExtensionHelpers.ts missing"
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS_TS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"plugin extension helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # Driver-id glob: '*' anywhere, anchored, exact without '*'.
    "glob_trailing",
    "glob_trailing_no_match",
    "glob_exact",
    "glob_exact_no_match",
    "glob_leading",
    "glob_leading_no_match",
    "glob_multi",
    "glob_multi_no_match",
    "glob_anchored",
    "glob_blank_no_match",
    # Boolean metric formatting coerces string state values.
    "metric_bool_true",
    "metric_bool_false",
    "metric_string_false",
    "metric_string_zero",
    "metric_string_true",
    "metric_number_zero",
    "metric_null_dash",
    "metric_plain_passthrough",
    # Plugin log filter: scoping + recency cap.
    "log_filter_caps_50",
    "log_filter_excludes_unrelated",
    "log_filter_keeps_loader",
    "log_filter_other_plugin",
    # Cheap change detection for the interval refresh.
    "tail_same",
    "tail_diff_len",
    "tail_diff_last",
    "tail_empty_same",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_helper_scenarios(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report '{scenario}'"
    assert helper_results[scenario] is True, f"scenario '{scenario}' failed"


# ── Source-level pins ──────────────────────────────────────────────────────


def test_driver_glob_uses_shared_matcher() -> None:
    """Both driver-id match sites use the anchored glob compiler, not the
    single-'*' startsWith shortcut."""
    src = EXTENSIONS_TSX.read_text(encoding="utf-8")
    assert src.count("matchesDriverGlob(") >= 2, (
        "DevicePanelSlot and ContextActionRenderer must both use the shared matcher"
    )
    assert 'replace("*", "")' not in src, (
        "driver-id match still uses the single-wildcard startsWith shortcut"
    )


def test_plugin_log_reads_snapshots() -> None:
    """PluginLogRenderer must not subscribe to the hot logEntries array."""
    src = EXTENSIONS_TSX.read_text(encoding="utf-8")
    assert "useLogStore((s) => s.logEntries)" not in src, (
        "PluginLogRenderer still subscribes to every log line"
    )
    assert "useLogStore.getState().logEntries" in src


def test_metric_formatting_is_shared_and_coercing() -> None:
    """formatMetric comes from the helpers module (with string coercion),
    not a local truthiness version."""
    src = EXTENSIONS_TSX.read_text(encoding="utf-8")
    assert 'return value ? "Yes" : "No"' not in src, (
        "a local formatMetric still renders string 'false' as 'Yes'"
    )
    assert "formatMetric" in src


def test_surface_save_flushes_on_unmount() -> None:
    """The debounced config save is flushed (not orphaned) when the surface
    view unmounts."""
    src = EXTENSIONS_TSX.read_text(encoding="utf-8")
    assert "pendingConfig" in src
    assert "clearTimeout(saveTimer.current)" in src
