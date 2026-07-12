"""Regression tests for the Variables / Device States cross-reference helpers
(variablesShared.helpers.ts).

The "Used By" panels scan the project for references to a variable or device
state key. Three defects this covers:

* Touch-panel button bindings (press/release/change) are authored as ARRAYS of
  actions, but the scanner read them as single objects, so every var/state key
  referenced from a button's Set Variable action was silently omitted — an
  integrator deleting such a variable saw "referenced in 0 places" and broke the
  panel. The scanner now normalizes both array and legacy-object shapes.
* ``globMatch`` built a RegExp from a script-derived key while escaping only "."
  and "*", so a key with other regex metacharacters crashed the view
  (SyntaxError) or hung it (ReDoS). The pattern is now fully escaped.
* Wildcard script references only annotated keys already seeded by macros/UI, so
  device-only keys never picked up a wildcard subscription. ``collectWildcardMatches``
  resolves a pattern against an arbitrary candidate set including device keys.

This bundles the real helper with the esbuild in web/programmer/node_modules and
asserts the behavior. Skips when the Node toolchain or esbuild is absent rather
than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_variables_shared_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "variables_shared_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "views"
    / "variables"
    / "variablesShared.helpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "variables shared helpers harness missing"
    if not HELPERS.is_file():
        return "variablesShared.helpers.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"variables helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # H-126 — event bindings are arrays of actions.
    "h126_array_press_var_found",
    "h126_array_multi_action",
    "h126_array_value_map",
    "h126_array_allkeys_device",
    "h126_legacy_object_still_works",
    "h126_two_way_binding",
    # M-176 — globMatch escapes regex metacharacters.
    "m176_metachar_no_crash",
    "m176_no_redos",
    # Backlog 67 — globMatch mirrors the runtime fnmatch semantics.
    "fn_star_spans_dots",
    "fn_question_single_char",
    "fn_char_class",
    "fn_unbalanced_bracket_no_throw",
    # L-103 — wildcard matches device-only candidate keys.
    "l103_wildcard_matches_device_keys",
    "l103_wildcard_segment_scoped",
    # Backlog 67 item 2 — var.* wildcard matches the project's variables.
    "varmap_wildcard_matches_vars",
    # M-277 — plugin-action params (not just device/group) resolve $var refs.
    "m277_plugin_action_params_resolve_vars",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_variables_shared_helpers(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
