"""The panel's layout arithmetic, held to the server's copies of it.

Where an element sits is worked out in more than one place: the panel draws
it (`web/panel/modules/layout.js`), `ui/page_geometry.py` folds a page's
layouts for the AI's write path and the reviewer, and `ui/page_review.py`
reads which elements an arrangement hides, picks the box a master element is
drawn in, defaults a box's missing coordinates and decides which pages a
master draws on (`namesPage` in `web/panel/modules/pages.js`). The Builder's
`pageGeometry.ts` is another copy of the fold. Nothing held the panel's copy
to any of them: a change to how a layout inherits, or to which of a master's
boxes is drawn, could land in one and not the others, and the review would
then measure a page the panel does not draw.

Executed rather than read: the panel's choices are plain functions of the
definition, so Node runs the panel's own functions over one corpus and the
answers are compared with the Python functions over the same corpus.

Left out of the corpus, because the two sides answer differently today:
- a master's box stored as null: the panel takes the first stored value, the
  review the first that is set (`_stored_master_box` says so; the project
  model refuses a null box);
- two layouts sharing an id: the panel follows the first, the fold here the
  last (the project model refuses a duplicate);
- a box whose width or height is 0: the panel draws it zero wide, the review
  measures the whole box;
- a coordinate that is not a number: the panel draws the default, the review
  drops the box.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openavc.ui.page_geometry import resolved_placements
from openavc.ui.page_review import _Arrangement, _drawn_box, _stored_master_box, master_draws_on
from tests import gates

PANEL_MODULES = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules"

_BOX = {"x": 1, "y": 2, "w": 30, "h": 40}

#: Pages as a project stores them: which layout a screen of each orientation
#: draws, what it inherits, and what it hides.
PAGES = [
    {"layouts": [{"id": "a", "primary": True, "orientation": "landscape", "placements": {"e1": _BOX}}]},
    {"layouts": [
        {"id": "a", "primary": True, "orientation": "landscape", "hidden": ["e3"],
         "placements": {"e1": _BOX, "e2": {"x": 50, "y": 50, "w": 10, "h": 10}}},
        {"id": "b", "orientation": "portrait", "inherits": "a", "hidden": ["e4"],
         "placements": {"e2": {"x": 5, "y": 60, "w": 90, "h": 10}}},
    ]},
    {"layouts": [
        {"id": "base", "primary": True, "orientation": "landscape", "hidden": ["e5"], "placements": {"e1": _BOX}},
        {"id": "mid", "orientation": "square", "inherits": "base", "placements": {"e2": _BOX}},
        {"id": "top", "orientation": "portrait", "inherits": "mid", "hidden": ["e1"], "placements": {"e1": {"x": 9}}},
    ]},
    # A cycle a hand-edited file can carry: both folds stop where they started.
    {"layouts": [
        {"id": "p", "primary": True, "orientation": "landscape", "inherits": "q", "hidden": ["x"],
         "placements": {"e1": _BOX}},
        {"id": "q", "orientation": "portrait", "inherits": "p", "hidden": ["y"], "placements": {"e2": _BOX}},
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

#: Stored boxes with coordinates missing, as numbers and as numeric text.
BOXES = [{}, {"x": 5}, {"w": 30}, {"y": 12, "h": 40}, {"x": "12", "w": "40.5"},
         {"x": 0, "y": 0, "w": 50, "h": 50}, _BOX]

#: What a master's `pages` can say, asked of two pages.
MASTER_PAGES = ["*", ["home"], ["other"], ["home", "other"], [], "home", None, {"home": True}]
PAGE_IDS = ["home", "other"]

ORIENTATIONS = ["landscape", "portrait"]

_SCRIPT = """
const layout = await import(process.argv[1]);
const pages = await import(process.argv[2]);
const corpus = JSON.parse(process.argv[3]);
const out = { pages: [], masters: [], boxes: [], names: [] };
for (const page of corpus.pages) {
    for (const o of corpus.orientations) {
        const got = layout.selectLayout(page, o);
        out.pages.push({ id: got.id, placements: got.placements, hidden: [...got.hidden].sort() });
    }
}
for (const placements of corpus.masters) {
    for (const o of corpus.orientations) out.masters.push(layout.masterPlacement({ placements }, o));
}
for (const box of corpus.boxes) out.boxes.push(layout.boxOf(box));
for (const on of corpus.master_pages) {
    for (const id of corpus.page_ids) out.names.push(pages.namesPage({ pages: on }, { id }));
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def panel_answers() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    corpus = json.dumps({
        "pages": PAGES, "masters": MASTERS, "orientations": ORIENTATIONS, "boxes": BOXES,
        "master_pages": MASTER_PAGES, "page_ids": PAGE_IDS,
    })
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT,
         (PANEL_MODULES / "layout.js").as_uri(), (PANEL_MODULES / "pages.js").as_uri(), corpus],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _as_page(raw: dict) -> SimpleNamespace:
    layouts = [
        SimpleNamespace(id=lay["id"], inherits=lay.get("inherits"), placements=lay.get("placements", {}),
                        hidden=lay.get("hidden"), orientation=lay.get("orientation"))
        for lay in raw["layouts"]
    ]
    return SimpleNamespace(layouts=layouts, elements=[])


def test_the_panel_folds_a_layout_the_way_the_review_does(panel_answers: dict) -> None:
    answers = iter(panel_answers["pages"])
    for raw in PAGES:
        page = _as_page(raw)
        for orientation in ORIENTATIONS:
            got = next(answers)
            chosen = next(lay for lay in page.layouts if lay.id == got["id"])
            assert got["placements"] == resolved_placements(page, got["id"]), (raw, orientation)
            assert got["hidden"] == sorted(_Arrangement.of(page, chosen, "").hidden), (raw, orientation)


def test_the_panel_picks_a_masters_box_the_way_the_review_does(panel_answers: dict) -> None:
    answers = iter(panel_answers["masters"])
    for placements in MASTERS:
        for orientation in ORIENTATIONS:
            assert next(answers) == _stored_master_box(placements, orientation), (placements, orientation)


def test_the_panel_fills_a_missing_coordinate_the_way_the_review_does(panel_answers: dict) -> None:
    for box, got in zip(BOXES, panel_answers["boxes"], strict=True):
        assert got == _drawn_box(box), box


def test_a_master_draws_on_the_pages_the_review_says(panel_answers: dict) -> None:
    answers = iter(panel_answers["names"])
    for on in MASTER_PAGES:
        for page_id in PAGE_IDS:
            assert next(answers) == master_draws_on(on, page_id), (on, page_id)


def test_the_corpus_reaches_every_fallback(panel_answers: dict) -> None:
    """Guard the guard: a corpus where every answer is the first layout proves nothing."""
    chosen = {page["id"] for page in panel_answers["pages"]}
    assert {"a", "b", "top", "only", "main", "p", "q"} <= chosen
    assert ["e3", "e4"] in [page["hidden"] for page in panel_answers["pages"]]
    boxes = panel_answers["masters"]
    assert None in boxes and {"x": 7, "y": 7, "w": 7, "h": 7} in boxes and {"x": 3} not in boxes
    assert {"x": 0, "y": 0, "w": 100, "h": 100} in panel_answers["boxes"]
    assert True in panel_answers["names"] and False in panel_answers["names"]
