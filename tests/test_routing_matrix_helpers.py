"""The plugin routing matrix (SurfaceConfigurator's crosspoint grid).

What it decides, and the defects each check keeps fixed:

- Whether a crosspoint is routed: its row's route key (``route_state_pattern``
  with ``{row}`` filled in) names the source its column's value names,
  compared by the matrix's route rule (the Builder's ``matrixRouteMatches``)
  in the layout's ``route_match`` mode, exact when it names none. The matrix
  used to read one key per crosspoint, which the plugins publish none of, so
  every routed cell drew unrouted and a click on one routed it again.
- Row/column enumeration: ``*`` matches anywhere in the pattern; a prefix
  derivation that only worked for a trailing wildcard silently produced an
  empty matrix for a mid-string pattern.
- An empty press-binding array is not an assigned button (``[]`` is truthy);
  assignment checks use ``press?.length``.
- A rapid double-click must not emit the same route/unroute twice: a cell is
  disabled while its context action is in flight.

Two layers: the harness bundles the real ``routingMatrixHelpers.ts`` with
the esbuild in ``openavc/web/programmer/node_modules`` (skips when the Node
toolchain is absent), and source-level checks pin the surface editors to
the fixed shapes. Those checks read ``surface/RoutingMatrix.tsx`` for the
crosspoint ones and the whole ``surface/`` directory for the assignment
shape, which several editors draw. The click itself is driven in
``surface/RoutingMatrix.test.tsx``.
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
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
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
    # The layout's mode, else the one that never lights a shared number.
    "default_mode_is_exact",
    "exact_mode_read",
    "port_mode_read",
    "no_mode_is_default",
    "unknown_mode_is_default",
    "inherited_name_is_no_mode",
    "non_text_mode_is_default",
    # A crosspoint read from its row's route key and its column's label.
    "routed_by_its_label",
    "shared_number_is_another_source",
    "shared_number_matches_in_port_mode",
    "routed_to_nothing",
    "no_route_key_never_routed",
    "unpublished_row_never_routed",
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


def test_a_crosspoint_is_read_from_its_rows_route_key() -> None:
    src = MATRIX_TSX.read_text(encoding="utf-8")
    assert "crosspointRouted(" in src
    # The per-crosspoint key it used to read, which no plugin publishes.
    assert "cell_state_pattern" not in src


def test_the_crosspoint_rule_is_the_matrix_route_rule() -> None:
    """The helpers compare through the Builder's copy of the route rule,
    which tests/test_matrix_route_parity.py holds to the server's; a
    comparison of their own would be a fourth copy nothing holds."""
    src = HELPERS_TS.read_text(encoding="utf-8")
    assert 'from "../ui-builder/matrixModel"' in src
    assert "matrixRouteMatches(" in src
    assert "toLowerCase" not in src and "UNROUTED" not in src


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
