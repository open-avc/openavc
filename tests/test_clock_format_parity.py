"""The format a clock with none written draws is the schema's, everywhere.

``UIElement`` in ``openavc/core/project_loader.py`` decides it: a blank
``format`` follows the clock's ``clock_mode`` (its ``BLANK_FROM``), a mode it
does not name reading as the time's, and the three durations reading none, a
duration always being hours, minutes and seconds. The generator renders that
into the project-value table; the panel's clock (``web/panel/modules/clock.js``,
for a saved project and a Builder draft alike) and the Builder's Format
placeholder (``ClockFields.tsx``) read it there. Before, each wrote the table
again by hand, and the Builder offered a format for the three durations that
the panel never reads.

One case table (``tests/fixtures/clock_format_cases.json``) runs through the
schema and the panel here, and through the Builder in
``ClockFields.clock.test.tsx``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.core.project_loader import UIElement, blank_drawn
from openavc.core.project_values_gen import project_values
from tests import gates

REPO = Path(__file__).resolve().parents[1]
CASES = json.loads((REPO / "tests" / "fixtures" / "clock_format_cases.json").read_text(encoding="utf-8"))["cases"]
PANEL_MODULES = REPO / "openavc" / "web" / "panel" / "modules"
CLOCK_FIELDS = (
    REPO / "openavc" / "web" / "programmer" / "src" / "components" / "ui-builder" / "PropertySections" / "ClockFields.tsx"
)

def _element(case: dict) -> dict:
    element = {"id": "c", "type": "clock"}
    if "mode" in case:
        element["clock_mode"] = case["mode"]
    return element


@pytest.mark.parametrize("case", [c for c in CASES if not c.get("draft_only")], ids=lambda c: c["name"])
def test_the_schema_answers_each_case(case: dict) -> None:
    element = UIElement.model_validate(_element(case))
    assert element.format is None, "a blank format is kept blank; only what it draws is answered"
    assert blank_drawn(UIElement, "format", element.clock_mode) == case["expected"]


_PANEL_SCRIPT = """
const { ClockControl } = await import(process.argv[1]);
const elements = JSON.parse(process.argv[2]);
const ticker = { meetingStart: (id, now) => now };
console.log(JSON.stringify(elements.map((e) => {
    const format = new ClockControl(e, () => undefined, ticker).format;
    return format === undefined ? '<undefined>' : format;
})));
"""


def test_the_panel_clock_draws_each_case_as_the_schema_says() -> None:
    """Run under Node: the format the panel's clock takes for every case, the
    drafts the loader would refuse among them. A duration's is null: it is
    never read."""
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _PANEL_SCRIPT,
         (PANEL_MODULES / "clock.js").as_uri(), json.dumps([_element(c) for c in CASES])],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    drawn = json.loads(proc.stdout)
    assert {c["name"]: d for c, d in zip(CASES, drawn)} == {c["name"]: c["expected"] for c in CASES}


def test_the_table_carries_the_rule() -> None:
    entry = project_values()["UIElement"]["format"]
    assert entry == {
        "default": None,
        "blank_from": {"clock_mode": {
            "time": "h:mm A", "date": "MMM D, YYYY", "datetime": "MMM D, YYYY h:mm A",
            "countdown": None, "elapsed": None, "meeting": None,
        }},
        "otherwise": "time",
    }


def _code_lines(path: Path) -> list[str]:
    comment = ("*", "/*", "//", "{/*")
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith(comment)]


@pytest.mark.parametrize("path", [PANEL_MODULES / "clock.js", CLOCK_FIELDS], ids=lambda p: p.name)
def test_no_reader_writes_a_default_format_of_its_own(path: Path) -> None:
    """The formats the rule gives are written once, in the schema. The panel's
    "12" and "24" shorthands are words an author types, not a blank's draw, and
    stay where they are."""
    _, by_mode, _ = UIElement.BLANK_FROM["format"]
    formats = {f for f in by_mode.values() if f}
    stray = sorted({
        f for line in _code_lines(path) if "FORMAT_SHORTHANDS = " not in line
        for f in formats if f"'{f}'" in line or f'"{f}"' in line
    })
    assert not stray, f"{path.name} writes {stray}; read the format a blank clock draws from the project-value table"
    assert "DEFAULT_FORMATS" not in path.read_text(encoding="utf-8")
