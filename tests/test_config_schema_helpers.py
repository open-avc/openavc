"""Regression tests for the Driver Builder config-field editor helpers
(configSchemaHelpers.ts).

The editor is React with no jsdom-loadable entry point, so these exercise the
pure helpers by bundling configSchemaHelpers.ts on the fly with the esbuild
already in web/programmer/node_modules and asserting on the results. Like the
other frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

Covers the config-default typing bugs fixed in ConfigSchemaEditor.tsx: the
Default Value editor used to store the raw input string regardless of the
field's declared type (an integer default exported as "5", a boolean default
of "false" was truthy), a type switch left the old wrong-typed default behind,
and marking a field secret left its default in default_config.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_config_schema_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "config_schema_helpers_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "driver-builder"
    / "configSchemaHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "config schema helpers harness missing"
    if not HELPERS.is_file():
        return "configSchemaHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
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
            f"config schema helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "integer_default_is_number",
    "boolean_false_is_falsy",
    "boolean_true_is_boolean",
    "number_and_float_coerce",
    "string_keeps_leading_zero",
    "empty_and_garbage_unset",
    "type_switch_converts_default",
    "type_switch_drops_unconvertible",
    "leaving_enum_drops_values",
    "type_switch_stringifies",
    "typed_beats_legacy_strings",
    "secret_purges_default",
    "unsecret_keeps_maps",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_config_schema_helper(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
