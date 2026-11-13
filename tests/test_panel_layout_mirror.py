"""The panel's layout arithmetic, held to the server's copies of it.

Where an element sits is worked out in three places: the panel draws it
(`web/panel/modules/layout.js`), `ui/page_geometry.py` folds a page's layouts
for the AI's write path and the reviewer, and `ui/page_review.py` picks the
box a master element is drawn in. The Builder's `pageGeometry.ts` is the
third copy of the fold, held to the review by its own tests. Nothing held the
panel's copy to any of them: a change to how a layout inherits, or to which of
a master's boxes is drawn, could land in one and not the others, and the
review would then measure a page the panel does not draw.

Executed rather than read: `layout.js` is pure arithmetic over the
definition, so Node runs the panel's own functions over one corpus and the
answers are compared with the Python functions over the same corpus.

Two differences are documented on both sides and kept out of the corpus: a
master's box stored as null (the panel takes the first stored value, the
review the first that is set; the project model refuses a null box), and two
layouts sharing an id (the panel follows the first, the fold here the last;
the project model refuses a duplicate).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openavc.ui.page_geometry import resolved_placements
from openavc.ui.page_review import _stored_master_box
from tests import gates

LAYOUT_JS = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules" / "layout.js"

_BOX = {"x": 1, "y": 2, "w": 30, "h": 40}

#: Pages as a project stores them: which layout a screen of each orientation
#: draws, and what it inherits.
PAGES = [
    {"layouts": [{"id": "a", "primary": True, "orientation": "landscape", "placements": {"e1": _BOX}}]},
    {"layouts": [
        {"id": "a", "primary": True, "orientation": "landscape",
         "placements": {"e1": _BOX, "e2": {"x": 50, "y": 50, "w": 10, "h": 10}}},
        {"id": "b", "orientation": "portrait", "inherits": "a",
         "placements": {"e2": {"x": 5, "y": 60, "w": 90, "h": 10}}},
    ]},
    {"layouts": [
        {"id": "base", "primary": True, "orientation": "landscape", "placements": {"e1": _BOX}},
        {"id": "mid", "orientation": "square", "inherits": "base", "placements": {"e2": _BOX}},
        {"id": "top", "orientation": "portrait", "inherits": "mid", "placements": {"e1": {"x": 9}}},
    ]},
    # A cycle a hand-edited file can carry: both folds stop where they started.
    {"layouts": [
        {"id": "p", "primary": True, "orientation": "landscape", "inherits": "q", "placements": {"e1": _BOX}},
        {"id": "q", "orientation": "portrait", "inherits": "p", "placements": {"e2": _BOX}},
    ]},
    # Inheriting from nothing, and no layout for the portrait screen.
    {"layouts": [{"id": "only", "primary": True, "orientation": "landscape", "inherits": "gone",
                  "placements": {"e1": _BOX}}]},
    # The primary is not the first, and neither matches a portrait screen.
    {"layouts": [
        {"id": "first", "orientation": "landscape", "placements": {"e1": _BOX}},
        {"id": "main", "primary": True, "orientation": "landscape", "placements": {"e2": _BOX}},
    ]},
]

#: A master element's orientation-keyed boxes.
MASTERS = [
    {"landscape": _BOX, "portrait": {"x": 7, "y": 7, "w": 7, "h": 7}},
    {"landscape": _BOX},
    {"portrait": _BOX},
    {"square": _BOX},
    {"square": _BOX, "tall": {"x": 3}},
    {},
]

ORIENTATIONS = ["landscape", "portrait"]

_SCRIPT = """
const layout = await import(process.argv[1]);
const { pages, masters, orientations } = JSON.parse(process.argv[2]);
const out = { pages: [], masters: [] };
for (const page of pages) {
    for (const o of orientations) {
        const got = layout.selectLayout(page, o);
        out.pages.push({ id: got.id, placements: got.placements });
    }
}
for (const placements of masters) {
    for (const o of orientations) {
        out.masters.push(layout.masterPlacement({ placements }, o));
    }
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def panel_answers() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    corpus = json.dumps({"pages": PAGES, "masters": MASTERS, "orientations": ORIENTATIONS})
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, LAYOUT_JS.as_uri(), corpus],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _as_page(raw: dict) -> SimpleNamespace:
    layouts = [
        SimpleNamespace(id=lay["id"], inherits=lay.get("inherits"), placements=lay.get("placements", {}))
        for lay in raw["layouts"]
    ]
    return SimpleNamespace(layouts=layouts, elements=[])


def test_the_panel_folds_a_layout_the_way_the_review_does(panel_answers: dict) -> None:
    answers = iter(panel_answers["pages"])
    for raw in PAGES:
        for orientation in ORIENTATIONS:
            got = next(answers)
            assert got["placements"] == resolved_placements(_as_page(raw), got["id"]), (raw, orientation)


def test_the_panel_picks_a_masters_box_the_way_the_review_does(panel_answers: dict) -> None:
    answers = iter(panel_answers["masters"])
    for placements in MASTERS:
        for orientation in ORIENTATIONS:
            assert next(answers) == _stored_master_box(placements, orientation), (placements, orientation)


def test_the_corpus_reaches_every_fallback(panel_answers: dict) -> None:
    """Guard the guard: a corpus where every answer is the first layout proves nothing."""
    chosen = {page["id"] for page in panel_answers["pages"]}
    assert {"a", "b", "top", "only", "main", "p", "q"} <= chosen
    boxes = panel_answers["masters"]
    assert None in boxes and {"x": 7, "y": 7, "w": 7, "h": 7} in boxes and {"x": 3} not in boxes
