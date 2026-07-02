"""Regression tests for the UI Builder icon picker (iconPickerHelpers.ts).

The picker is React with no jsdom-loadable entry point, so these exercise the
pure helpers by bundling iconPickerHelpers.ts on the fly with the esbuild
already in web/programmer/node_modules and asserting on the results. Like the
other frontend-logic suites it skips when the Node toolchain or esbuild isn't
present rather than failing the Python-only CI gate.

The panel runtime renders icons by direct sprite lookup (icons.svg#<name>), so
every name the picker can store must exist as a symbol id in
web/panel/icons.svg. Covers the two bugs fixed in IconPicker.tsx: the All tab
re-derived kebab names from lucide-react's PascalCase exports with a regex
that got every digit-containing name wrong (Building2 -> "building2" while the
sprite id is "building-2"), and the curated category lists carried names that
upstream renames removed from the sprite (tv-2, unlock, home, alert-triangle,
help-circle) — both looked correct in the builder preview while rendering a
blank icon on the panel.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_icon_picker.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "icon_picker_harness.cjs"
HELPERS = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "ui-builder"
    / "iconPickerHelpers.ts"
)
SPRITE = OPENAVC_ROOT / "web" / "panel" / "icons.svg"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "icon picker harness missing"
    if not HELPERS.is_file():
        return "iconPickerHelpers.ts missing"
    if not SPRITE.is_file():
        return "panel sprite (web/panel/icons.svg) missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS), str(SPRITE)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"icon picker harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "sprite_and_list_nonempty",
    "all_tab_within_sprite",
    "all_tab_covers_sprite",
    "digit_names_use_sprite_ids",
    "curated_within_sprite",
    "all_tab_resolves_in_builder",
    "curated_resolves_in_builder",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_icon_picker(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
