"""Regression tests for the device-config coercion helper (deviceConfigCoerce.ts).

H-061: the Add and Edit device dialogs share coerceConfigValue so they can't
drift — the Add dialog used to store an object-typed field (e.g. the generic_tcp
`commands` map) as a raw string, which then broke command sending at runtime.

The dialogs are React/TypeScript with no jsdom entry point, so this transpiles
the pure helper on the fly with the esbuild already in web/programmer/node_modules
(mirrors test_theme_studio_colors.py) and asserts on the results. Skips when the
Node toolchain or esbuild isn't present rather than failing the Python-only gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_device_config_coerce.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "device_config_coerce_harness.cjs"
SOURCE = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "views" / "devices" / "deviceConfigCoerce.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "device-config coerce harness missing"
    if not SOURCE.is_file():
        return "deviceConfigCoerce.ts missing"
    return None


@pytest.fixture(scope="module")
def results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(SOURCE)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"coerce harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(f"bad harness output: {proc.stdout!r}") from exc


@pytest.mark.parametrize(
    "case",
    [
        "object_valid",
        "object_invalid_json",
        "object_array_rejected",
        "object_number_rejected",
        "boolean",
        "numbers",
        "numberish_string_kept",
        "text_raw",
        "string_passthrough",
        "untyped_json_object",
    ],
)
def test_coerce_case(results: dict, case: str):
    assert results[case]["pass"], results[case]["detail"]
