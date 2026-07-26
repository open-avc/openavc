"""Regression tests for the binding editor's Test button (testActionParams.ts).

Command params in UI bindings can hold $-references — the interaction
tokens ($value, $input, $output, $mute) and state refs ($var.volume). At
runtime the engine resolves each param via the shared resolver
(server/core/value_resolver.py) before the command reaches the device. The
Test button sent params raw: for a text-protocol driver the literal
"$value" was formatted into the command and transmitted to live AV
hardware as a malformed control command — the standard shape for every
slider/select change binding.

Test now resolves state refs from the IDE's live state mirror and refuses
to send when a param needs an interaction token (no value exists outside a
real panel event) or names a state key with no current value (the runtime
would send None), telling the user which param blocked it and why.

Two layers: the harness bundles the real ``testActionParams.ts`` with the
esbuild in ``web/programmer/node_modules`` (skips when the Node toolchain
is absent rather than failing the Python-only CI gate), and a source-level
check pins PressBindingEditor to routing the Test send through the helper.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_binding_test_params.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

BINDING_EDITOR_DIR = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "ui-builder" / "BindingEditor"
)
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "test_action_params_harness.cjs"
HELPERS_TS = BINDING_EDITOR_DIR / "testActionParams.ts"
EDITOR_TSX = BINDING_EDITOR_DIR / "PressBindingEditor.tsx"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "test action params harness missing"
    if not HELPERS_TS.is_file():
        return "testActionParams.ts missing"
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
            f"test action params harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # The defect: unresolved $-tokens must never reach the device.
    "event_tokens_block_the_send",
    "state_refs_resolve_from_live_state",
    "missing_state_ref_blocks_the_send",
    # Contract guards.
    "static_params_pass_through",
    "mixed_params_resolve_per_param",
    "blocked_messages_name_param_and_token",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_binding_test_params(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


def test_editor_routes_test_send_through_resolver() -> None:
    """PressBindingEditor's Test must resolve params before sending.

    A raw ``sendCommand(..., action.params)`` here transmits literal
    "$value" strings to live hardware.
    """
    source = EDITOR_TSX.read_text(encoding="utf-8")
    assert "resolveTestParams(" in source
    assert "testBlockedMessage(" in source
    assert "sendCommand(String(action.device), String(action.command), result.params)" in source
