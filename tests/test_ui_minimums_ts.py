"""The generated control-minimum table must be loadable TypeScript.

``tests/test_ui_minimums_generated.py`` proves the file's CONTENT is current by
re-rendering and byte-comparing it. That says nothing about whether the file is
valid TypeScript: nothing imports it until the Builder does (Phase 8 of the AI
fix plan), so a broken emit would sit in the tree undetected until then, and
then look like a Builder bug rather than a generator bug.

So this bundles it with the real esbuild and reads the exported values back,
the same way the other TypeScript harnesses in this suite do. It also checks
the shapes the consumers depend on: a plain constant row, a row that scales
with an authored internal, and the one that grows when it draws a caption.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "ui_minimums_harness.cjs"
ARTIFACT = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "api" / "uiMinimums.gen.ts"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "ui minimums harness missing"
    if not ARTIFACT.is_file():
        return "uiMinimums.gen.ts missing (run python -m openavc.ui.minimums_gen)"
    return None


@pytest.fixture(scope="module")
def harness_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(ARTIFACT)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"ui minimums harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


def _value(results: dict, name: str):
    entry = results[name]
    assert entry["ok"], f"{name} threw: {entry.get('error')}"
    return entry["value"]


def test_the_table_loads_and_lists_every_type(harness_results: dict) -> None:
    from openavc.ui.control_minimums import RULES

    assert _value(harness_results, "exports_the_table") == sorted(RULES)
    assert _value(harness_results, "exports_the_type_list") == sorted(RULES)


def test_the_reference_screen_survives_generation(harness_results: dict) -> None:
    from openavc.ui.control_minimums import REFERENCE_HEIGHT_PX, REFERENCE_WIDTH_PX

    assert _value(harness_results, "reference_screen") == {
        "widthPx": REFERENCE_WIDTH_PX,
        "heightPx": REFERENCE_HEIGHT_PX,
    }
    assert _value(harness_results, "rem_base") == 14


def test_a_constant_row_carries_its_internals(harness_results: dict) -> None:
    """Read from the rules rather than typed again here.

    These floors move whenever a machine measures one higher (see
    control_minimums, "When two machines disagree"), and the literals that
    used to be here went stale the first time that happened. What the
    byte-compare in test_ui_minimums_generated.py cannot say is whether
    TypeScript can still READ the row, which is what this asks.
    """
    from openavc.ui.control_minimums import RULES

    row = _value(harness_results, "fader_row")
    assert row["baseWidthPx"] == RULES["fader"].base_width_px
    assert row["baseHeightPx"] == RULES["fader"].base_height_px
    parts = {i["part"] for i in row["internals"]}
    assert parts == {"fader-handle", "fader-scale"}


def test_the_scaling_row_carries_its_measured_coefficients(harness_results: dict) -> None:
    scaling = _value(harness_results, "slider_scaling")
    assert scaling["property"] == "thumb_size"
    assert scaling["defaultPx"] == 44
    assert scaling["widthCoefficient"] == 1.0
    assert scaling["heightCoefficient"] == 1.0
    assert scaling["fromTheme"] is True


def test_the_caption_bonus_survives_generation(harness_results: dict) -> None:
    assert _value(harness_results, "status_led_caption_bonus") == 9


def test_the_matrix_row_declares_no_scaling(harness_results: dict) -> None:
    """It scrolls internally, so a bigger grid does not need a bigger box.

    Pinned on this side too, because the obvious model -- cell size times
    inputs -- is wrong and was written down as fact before anyone measured it.
    """
    assert _value(harness_results, "matrix_does_not_scale") is None


def test_every_number_reaches_typescript_with_its_provenance(harness_results: dict) -> None:
    assert _value(harness_results, "every_internal_has_a_source") == []
