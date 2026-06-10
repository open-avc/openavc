"""Regression tests for the Add/Edit Device dialog config logic
(deviceConfigCoerce.ts).

Three defects are pinned here. Secret-flagged config fields (the Driver
Builder's Secret checkbox, e.g. generic_http's passwords and API keys) used
to fall through to the plaintext input — configFieldKind must route them to
the masked password widget. Declared string/password values were
number/JSON-sniffed, so an all-numeric PIN like "0123" persisted as the
number 123 and codes past 2^53 lost digits — coerceConfigValue must keep
them exactly as typed, and untyped fields may only number-coerce when the
round-trip is lossless. And the Add dialog stored host/port/credentials in
device.config — splitConnectionFields must produce the same
connections-table split the device-update API applies.

Bundled with the esbuild in web/programmer/node_modules; skips when the
Node toolchain is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_device_config_dialog.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "device_config_dialog_harness.cjs"
MODULE = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "views"
    / "devices"
    / "deviceConfigCoerce.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "device config dialog harness missing"
    if not MODULE.is_file():
        return "deviceConfigCoerce.ts missing"
    return None


@pytest.fixture(scope="module")
def dialog_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(MODULE)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"device config dialog harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h101_secret_fields_render_masked",
    "h101_normal_fields_unchanged",
    "h102_declared_string_password_never_sniffed",
    "h102_untyped_lossless_number_only",
    "h102_existing_coercions_unchanged",
    "m157_connection_fields_split",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_device_config_dialog(dialog_results: dict, scenario: str) -> None:
    assert scenario in dialog_results, f"harness did not report {scenario}"
    outcome = dialog_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
