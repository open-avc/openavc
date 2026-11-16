"""A crosspoint matrix's cell size of 0 is unset, everywhere it is read.

``UIElement`` in ``openavc/core/project_loader.py`` declares it (its
``ZERO_IS_BLANK``): a 0 ``style.cell_size`` is a blank one, the cell fitting
itself to the box, never a 0px cell. The generator renders that into the
project-value table; the panel's matrix (``web/panel/modules/matrix.js``,
through ``modules/project_values.js``), the Builder's floor and Cell Size box
read it there, and the AI's floor (``ui/control_minimums.py``) asks the schema.
Before, the Builder read a 0 as a 0px cell, so its floor for a two-by-two
matrix was 97x86 where the panel needs 185x174, and its Cell Size box stored a
typed 0.

The review parity corpus (``test_ui_review_parity.py``, ``matrix_cell_size_zero``)
holds the two floors to each other; ``controlMinimums.test.ts`` and
``MatrixFields.cellSize.test.tsx`` hold the Builder.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.core.project_loader import UIElement, style_is_blank
from openavc.core.project_values_gen import project_values
from openavc.ui.control_minimums import minimum_box
from tests import gates

REPO = Path(__file__).resolve().parents[1]
PANEL_MATRIX = REPO / "openavc" / "web" / "panel" / "modules" / "matrix.js"


def test_the_table_carries_the_rule() -> None:
    assert project_values()["UIElement"]["style.cell_size"] == {"zero_is_blank": True}
    assert "style.cell_size" not in project_values().get("MasterElement", {}), (
        "a master inherits the rule from UIElement and is listed once, there"
    )


@pytest.mark.parametrize(("value", "blank"), [
    (None, True), (0, True), (0.0, True), (-0.0, True), (False, True),
    (2, False), (0.5, False), (-1, False), ("0", False), ("", False), ([], False),
])
def test_the_schema_reads_a_zero_cell_size_as_blank(value, blank) -> None:
    assert style_is_blank("cell_size", value) is blank


def test_a_zero_is_blank_only_where_the_schema_says_so() -> None:
    """A 0 border is no border: only a key the element declares reads it as blank."""
    assert UIElement.ZERO_IS_BLANK == {"style": frozenset({"cell_size"})}
    assert style_is_blank("border_width", 0) is False
    assert style_is_blank("border_width", None) is True


def _two_by_two(style: dict | None = None) -> dict:
    element = {"id": "m", "type": "matrix", "matrix_config": {
        "sources": {"from": {"count": 2}}, "destinations": {"from": {"count": 2}},
    }}
    if style is not None:
        element["style"] = style
    return element


@pytest.mark.parametrize("zero", [0, 0.0, -0.0])
def test_the_ai_floor_reads_a_zero_cell_as_unset(zero) -> None:
    unset = minimum_box(_two_by_two())
    assert (unset.width_px, unset.height_px) == (185, 174)
    assert minimum_box(_two_by_two({"cell_size": zero})) == unset


# Every value a draft can hand the panel, and what the panel drew for each
# before it asked the table (``style.cell_size ? style.cell_size * remPx :
# null``): asking it moved nothing on a panel.
_PANEL_SCRIPT = r"""
const { cellSizes } = await import(process.argv[1]);
const values = {
    absent: undefined, null: null, zero: 0, minus_zero: -0, empty: '', false: false, NaN: NaN,
    two: 2, half: 0.5, minus_one: -1, text_two: '2', text_zero: '0', words: 'abc', true: true,
    infinity: Infinity,
};
const seen = (v) => (typeof v === 'number' && !Number.isFinite(v)) || Object.is(v, -0) ? String(v) : v;
console.log(JSON.stringify(Object.entries(values).map(([name, v]) => {
    const before = v ? v * 14 : null;
    const now = cellSizes({ cell_size: v }, 14).authoredPx;
    return { name, before: seen(before), now: seen(now) };
})));
"""


def test_the_panel_draws_every_cell_size_as_it_did() -> None:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _PANEL_SCRIPT, PANEL_MATRIX.as_uri()],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    drawn = json.loads(proc.stdout)
    assert len(drawn) == 15
    assert [d for d in drawn if d["before"] != d["now"]] == []
    now = {d["name"]: d["now"] for d in drawn}
    assert (now["zero"], now["minus_zero"], now["two"]) == (None, None, 28)
