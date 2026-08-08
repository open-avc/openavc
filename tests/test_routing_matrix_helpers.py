"""Regression tests for the plugin routing matrix (SurfaceConfigurator).

The crosspoint matrix had two value-handling defects and two interaction
defects:

- ``getCellState`` used JS ``Boolean()`` on state values. State values are
  flat primitives and plugins report route status as strings — the Dante
  plugin writes ``"none"`` for an unsubscribed channel — so every unrouted
  crosspoint rendered as routed and the first click sent ``unroute`` to
  actually-unrouted hardware. Cells now go through an explicit truthy set.
- Row/column enumeration derived a prefix via ``pattern.replace('*', '')``
  + ``startsWith``, which only works for a trailing wildcard; a mid-string
  pattern silently produced an empty matrix. ``*`` now matches anywhere.
- An empty press-binding array counted as an assigned button (``[]`` is
  truthy) — assignment checks now use ``press?.length``.
- A rapid double-click re-read stale liveState and emitted the same
  route/unroute twice — cells are now disabled while their context action
  is in flight.

Two layers: the harness bundles the real ``routingMatrixHelpers.ts`` with
the esbuild in ``web/programmer/node_modules`` (skips when the Node
toolchain is absent), and source-level checks pin the surface editors to
the fixed shapes. Those checks read ``surface/RoutingMatrix.tsx`` for the
crosspoint ones and the whole ``surface/`` directory for the assignment
shape, which several editors draw.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "routing_matrix_helpers_harness.cjs"
HELPERS_TS = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "plugins"
    / "routingMatrixHelpers.ts"
)
SURFACE_DIR = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "plugins" / "surface"
)
MATRIX_TSX = SURFACE_DIR / "RoutingMatrix.tsx"


def _surface_sources() -> str:
    """Every surface editor concatenated — the assignment shape is drawn in
    several of them, and which file holds which is not what these pin."""
    return "\n".join(
        f.read_text(encoding="utf-8") for f in sorted(SURFACE_DIR.glob("*.ts*"))
    )
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "routing matrix harness missing"
    if not HELPERS_TS.is_file():
        return "routingMatrixHelpers.ts missing"
    if not MATRIX_TSX.is_file():
        return "RoutingMatrix.tsx missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
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
            f"routing matrix harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # The live defect: Dante's "none" must read unrouted.
    "none_is_unrouted",
    "false_string_unrouted",
    "zero_string_unrouted",
    "empty_string_unrouted",
    "off_unrouted",
    "case_and_space_insensitive",
    "connected_routed",
    "one_string_routed",
    "bool_true_routed",
    "bool_false_unrouted",
    "one_number_routed",
    "zero_number_unrouted",
    "null_unrouted",
    "undefined_unrouted",
    # Wildcard enumeration.
    "trailing_wildcard",
    "mid_string_wildcard",
    "match_keys_returned",
    "dots_are_literal",
    "no_wildcard_empty",
    "empty_pattern_empty",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_helper_scenarios(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report '{scenario}'"
    assert helper_results[scenario] is True, f"scenario '{scenario}' failed"


# ── Source-level pins ──────────────────────────────────────────────────────


def test_cell_state_goes_through_explicit_truthiness() -> None:
    src = MATRIX_TSX.read_text(encoding="utf-8")
    assert "isCellRouted(liveState[key])" in src
    # The crosspoint read specifically; plugin flags documented as booleans
    # (connected, preset_dirty, …) legitimately use Boolean().
    assert "Boolean(liveState[key])" not in src, (
        "crosspoint state must not use JS Boolean() coercion — string route "
        "status like Dante's 'none' reads as routed"
    )


def test_matrix_axes_use_wildcard_matcher() -> None:
    src = MATRIX_TSX.read_text(encoding="utf-8")
    assert "matchStateKeys(" in src
    assert 'state_pattern ?? "").replace("*"' not in src, (
        "row/col enumeration must not assume a trailing wildcard"
    )


def test_assignment_checks_use_press_length() -> None:
    src = _surface_sources()
    assert not re.search(r"bindings\?\.press\s*;", src), (
        "an empty press array ([] is truthy) must not count as assigned"
    )
    assert src.count("bindings?.press?.length") >= 2


def test_crosspoint_clicks_guard_in_flight_actions() -> None:
    src = MATRIX_TSX.read_text(encoding="utf-8")
    assert "pendingCells" in src, (
        "a rapid double-click must not emit the same route/unroute twice"
    )
