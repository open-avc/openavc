"""Regression tests for the device-delete reference finder (deviceUtils).

Before deleting a device the Programmer IDE warns which macros, triggers, and
UI pages depend on it. The old finder under-reported (it only saw a step's
`device` field plus trigger `state_key`/`conditions`) and over-reported (it
substring-matched the device id against the stringified UI bindings). So it
missed `$device.<id>` macro params, `group.command` steps, conditional
branches, and `device.*` event-trigger patterns, while flagging a sibling
`device.proj10` when deleting `proj1`. The finder now walks those paths and
anchors state-key matching on segment boundaries.

Exercised via the esbuild-on-the-fly harness (findDeviceReferences takes a
type-only import that esbuild erases). Skips when the Node toolchain or esbuild
is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_device_utils_refs.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "device_utils_harness.cjs"
HELPERS = OPENAVC_ROOT / "web" / "programmer" / "src" / "views" / "devices" / "deviceUtils.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "device utils harness missing"
    if not HELPERS.is_file():
        return "deviceUtils.ts missing"
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
            f"device utils harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "l175_param_device_ref",
    "l175_group_command_step",
    "l175_event_pattern",
    "l175_nested_conditional",
    "l176_no_substring_false_positive",
    "l176_sibling_still_found",
    "l176_do_action_target_kept",
    "l176_true_positive_show_key",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_find_device_references(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
