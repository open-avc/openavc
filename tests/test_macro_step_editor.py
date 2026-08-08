"""Regression tests for the macro StepEditor's value handling.

The macro step editor had four silent-mangling paths:

- The state.set value input guessed a type from how the text looked: "true"
  became a boolean and "123" a number even for declared string variables and
  for device/system/plugin state keys, so a literal string '0' or 'true'
  could never be authored. The type now follows the variable's declared type,
  and untyped state keys get an explicit type picker.
- The event.emit step had no payload field although the runtime, the project
  schema, and the script export all support one.
- Two wait_until steps with the same condition key and timeout shared an HTML
  radio-group name (value-derived), so picking fail/continue in one visually
  cleared the other; the group name is now a per-editor-instance React id.
- The delay seconds and wait_until timeout inputs used ``parseFloat(...) || 0``,
  turning blank/invalid input into 0 — for wait_until that means "time out
  immediately", which fails the macro by default.

Two layers: the harness bundles the real ``macroValueHelpers.ts`` with the
esbuild in ``web/programmer/node_modules`` and exercises the pure logic
(skips when the Node toolchain is absent rather than failing the Python-only
CI gate), and source-level checks pin StepEditor.tsx to the fixed shapes so
the old patterns can't quietly come back.
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

# Repo root = openavc/ (this file is openavc/tests/test_macro_step_editor.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "macro_value_helpers_harness.cjs"
HELPERS_TS = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "macros"
    / "macroValueHelpers.ts"
)
STEP_EDITOR_TSX = (
    OPENAVC_ROOT / "openavc" / "web"
    / "programmer"
    / "src"
    / "components"
    / "macros"
    / "StepEditor.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    # A missing helpers module is the defect, not a toolchain gap.
    assert HELPERS_TS.is_file(), "macroValueHelpers.ts missing"
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
            f"macro value helpers harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # The stored value's own type drives the editor.
    "kind_number",
    "kind_boolean",
    "kind_text",
    "kind_null_is_text",
    # Explicit type switches convert sanely.
    "convert_text_to_number",
    "convert_junk_to_number",
    "convert_text_to_boolean",
    "convert_number_to_boolean",
    "convert_number_to_text",
    "convert_null_to_text",
    # Text stays verbatim — the headline defect.
    "text_true_stays_string",
    "text_zero_stays_string",
    "text_numeric_stays_string",
    # Numbers hold blank/junk instead of snapping to 0; zero is a value.
    "number_blank_undefined",
    "number_junk_undefined",
    "number_zero_kept",
    "number_float_parses",
    "boolean_parses",
    # Payload rows: insertion order, typed values, empty cleanup.
    "payload_update_value",
    "payload_rename_key_keeps_order",
    "payload_typed_value_survives",
    "payload_empty_key_drops_row",
    "payload_remove_row",
    "payload_remove_last_returns_undefined",
    "payload_add_row",
    "payload_add_row_avoids_collision",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_helper_scenarios(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report '{scenario}'"
    assert helper_results[scenario] is True, f"scenario '{scenario}' failed"


# ── Source-level pins ──────────────────────────────────────────────────────


def test_no_falsy_numeric_coercion() -> None:
    """No `parseFloat(...) || 0`-style fallbacks: blank/invalid must not
    become 0 (for wait_until, 0 means fail-immediately)."""
    src = STEP_EDITOR_TSX.read_text(encoding="utf-8")
    assert not re.search(r"parse(Int|Float)\([^)]*\)\s*\|\|", src), (
        "a numeric step input still coerces falsy values to a default"
    )
    assert "parseNumericField" in src


def test_state_set_does_not_guess_value_type() -> None:
    """The old heuristic (text that looks like a bool/number is silently
    converted) is gone; untyped keys use the explicit type picker."""
    src = STEP_EDITOR_TSX.read_text(encoding="utf-8")
    assert 'if (v === "true") onChange({ value: true })' not in src, (
        "state.set value input still guesses the type from the text"
    )
    assert "TypedValueInput" in src
    assert "parseTypedInput" in src


def test_event_emit_authors_payload() -> None:
    """The runtime emits step payload verbatim (macro_engine event.emit) and
    the schema declares it; the editor must be able to author it."""
    src = STEP_EDITOR_TSX.read_text(encoding="utf-8")
    assert "updatePayloadRow" in src
    assert "addPayloadRow" in src
    assert "removePayloadRow" in src


def test_wait_until_radio_group_is_instance_scoped() -> None:
    """Radio group names must come from useId(), not from step values that
    two steps can share."""
    src = STEP_EDITOR_TSX.read_text(encoding="utf-8")
    assert "useId()" in src
    assert "wait_until_on_timeout_" not in src, (
        "radio group name is still derived from shared step values"
    )
