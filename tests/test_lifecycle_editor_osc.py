"""Regression test for OSC argument authoring in the on_connect editor
(driver-builder/LifecycleEditor.tsx).

The Driver Builder's `on_connect` editor was string-only: an OSC device whose
bring-up message needs arguments could not have its `{address, args}` item
authored — the item was shown read-only (a disabled input holding the JSON),
even though the runtime (`configurable.py::_build_osc_args`) accepts it and the
save round-trip preserves it (`DriverDefinitionRequest` is `extra='allow'`; the
loader validates the object form with `allow_osc_dict=True`). A backend
capability the UI couldn't reach. The editor now renders the shared
OscArgsEditor for OSC "send once" items.

This bundles the real ``LifecycleEditor.tsx`` with the esbuild in
``web/programmer/node_modules`` and server-renders it: an OSC `{address, args}`
item (and a bare OSC address) must expose the args editor; non-OSC transports
must not, and a non-OSC object step stays read-only. Skips when the Node
toolchain or esbuild is absent rather than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_lifecycle_editor_osc.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "lifecycle_editor_osc_harness.cjs"
LIFECYCLE_TSX = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "components"
    / "driver-builder"
    / "LifecycleEditor.tsx"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "lifecycle-editor OSC harness missing"
    if not LIFECYCLE_TSX.is_file():
        return "driver-builder/LifecycleEditor.tsx missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(LIFECYCLE_TSX)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"lifecycle-editor OSC harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    "osc_args_item_shows_args_editor",
    "osc_bare_string_shows_args_editor",
    "osc_args_and_when_coexist",
    "non_osc_string_no_args_editor",
    "non_osc_object_stays_readonly",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_lifecycle_editor_osc(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
