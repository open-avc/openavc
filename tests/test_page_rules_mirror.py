"""The panel's page rules, held to the project schema's.

What a page is, how a way onto it opens it, which page stands in for one that
is missing, whether a master element draws on a page, and the box an overlay
draws in are the page rules
(``openavc/ui/page_rules.py``), rendered into the panel's generated module as
``PAGE_RULES``. The panel asks them through ``web/panel/modules/pages.js``,
whose functions also decide what to do with what the table says: a type it
reads, a list it falls back through, a box side it reads as a number, the types
it draws (its renderer table, ``web/panel/modules/dispatch.js``, which
``RENDERED_TYPES`` is held to). Node runs
those functions over one corpus and the answers are compared with the Python
functions over the same corpus, so a change to either lands in both.

Left out of the corpus, because the two sides answer differently today:
- a page of a type this release does not know (``modal``) in a list the panel
  falls back through: the page rules read it as a regular page, as navigation
  draws it, and ``fallbackPage`` passes over it;
- a box side written as text that starts with a number and goes on
  (``"40px"``), or as a list holding one: the panel reads that number, the
  rules the default (the project schema takes neither);
- a master's ``hidden`` written as an empty list or mapping: the panel reads it
  as hidden, the rules as shown (the project schema takes a true or false).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openavc.ui import page_rules
from openavc.ui.page_review import RENDERED_TYPES
from openavc.ui.page_rules import (
    fallback_page,
    is_navigation_word,
    master_draws_on,
    names_no_page,
    opens_over,
    overlay_box,
    page_type_of,
    stand_in,
)
from tests import gates

PANEL_MODULES = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules"

#: Stands for a key nobody wrote: it is left out.
ABSENT = "<absent>"

#: A page's type as a project, a Builder draft or a hand-edited file holds it.
WRITTEN_TYPES = [ABSENT, None, "", "page", "overlay", "sidebar", "modal", "popup", "Overlay", " sidebar", 5, True]

#: The types a page in a list the panel falls back through is written as: the
#: three words and the blanks.
KNOWN_TYPES = {"main": ABSENT, "home": "page", "blank": "", "none": None, "dialog": "overlay", "drawer": "sidebar"}

#: Page lists, by the ids of KNOWN_TYPES, in order.
PAGE_LISTS = [
    [], ["main"], ["dialog"], ["dialog", "drawer"], ["dialog", "main", "home"], ["drawer", "dialog", "home"],
    ["blank", "dialog"], ["dialog", "none"], ["home", "main"], ["drawer", "dialog", "blank", "main"],
]

#: The page asked for, for each list: one of its pages, one it does not hold, none.
WANTED = ["main", "home", "dialog", "drawer", "lobby", None]

#: What an overlay page's own box settings can say.
OVERLAY_SETTINGS = [
    ABSENT, None, {}, {"width": 50}, {"height": 20}, {"width": 50, "height": 60},
    {"width": "40", "height": " 12.5 "}, {"width": None, "height": None}, {"width": 0, "height": 0},
    {"width": 200, "height": -5}, {"width": "NaN", "height": "Infinity"}, {"width": "1e999"},
    {"width": True, "height": False}, {"width": "", "height": "abc"}, "wide", 7,
]

#: The types a box is asked of: the two that open over, and two that do not.
BOX_TYPES = ["overlay", "sidebar", "page", "modal"]

#: What a master element's type, pages and hidden can say. Types the panel
#: draws, one it does not, names every object has, and values that are no name.
MASTER_TYPES = [ABSENT, None, "", "button", "custom", "hologram", "constructor", "__proto__", 5, ["button"]]
MASTER_PAGES = [ABSENT, None, "*", "", "home", ["home"], ["other"], [], ["other", "home"], ["*"], 5]
MASTER_HIDDEN = [ABSENT, None, False, True, 0, 1, "", "yes"]

#: The pages a master is asked about.
MASTER_PAGE_IDS = ["home", "other"]


def _page(page_id: str, written: object, **more: object) -> dict:
    page = {"id": page_id, **more}
    if written != ABSENT:
        page["page_type"] = written
    return page


def _known(page_id: str) -> dict:
    return _page(page_id, KNOWN_TYPES[page_id])


def _boxed(written: str, settings: object) -> dict:
    return _page("p", written) if settings == ABSENT else _page("p", written, overlay=settings)


def _master(written_type: object, pages: object, hidden: object) -> dict:
    master: dict = {"id": "m"}
    for key, value in (("type", written_type), ("pages", pages), ("hidden", hidden)):
        if value != ABSENT:
            master[key] = value
    return master


# The corpus goes in on stdin: on the command line it is past the length
# Windows allows (WinError 206).
_SCRIPT = """
const pages = await import(process.argv[1]);
const { readFileSync } = await import('node:fs');
const corpus = JSON.parse(readFileSync(0, 'utf8'));
const out = { opens_over: [], fallback: [], stand_in: [], boxes: [], draws_on: [], draws_nothing: [], runs_markup: [] };
const id = (page) => (page ? page.id : null);
for (const page of corpus.typed) out.opens_over.push(pages.opensOver(page));
for (const list of corpus.lists) {
    out.fallback.push(id(pages.fallbackPage(list)));
    for (const wanted of corpus.wanted) out.stand_in.push(id(pages.standIn(list, wanted)));
}
for (const page of corpus.boxed) out.boxes.push(pages.overlayBox(page));
for (const master of corpus.masters) {
    for (const pageId of corpus.master_page_ids) {
        const empty = { id: pageId, elements: [] };
        out.draws_on.push(pages.masterDrawsOn(master, empty));
        out.draws_nothing.push(pages.pageDrawsNothing(empty, [master]));
        const plain = { id: pageId, elements: [{ id: 'l', type: 'label' }] };
        out.runs_markup.push(pages.pageRunsAuthorMarkup({ pages: [plain], master_elements: [master] }, pageId));
    }
}
console.log(JSON.stringify(out));
"""


def _corpus() -> dict:
    return {
        "typed": [_page("p", written) for written in WRITTEN_TYPES],
        "lists": [[_known(page_id) for page_id in ids] for ids in PAGE_LISTS],
        "wanted": WANTED,
        "boxed": [_boxed(t, settings) for t in BOX_TYPES for settings in OVERLAY_SETTINGS],
        "masters": [
            _master(t, pages, hidden) for t in MASTER_TYPES for pages in MASTER_PAGES for hidden in MASTER_HIDDEN
        ],
        "master_page_ids": MASTER_PAGE_IDS,
    }


@pytest.fixture(scope="module")
def panel_answers() -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, (PANEL_MODULES / "pages.js").as_uri()],
        input=json.dumps(_corpus()), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _id(page: dict | None) -> str | None:
    return page["id"] if page else None


def test_a_page_opens_over_the_one_on_screen_as_the_rules_say(panel_answers: dict) -> None:
    for page, got in zip(_corpus()["typed"], panel_answers["opens_over"], strict=True):
        assert got == opens_over(page), page


def test_the_panel_falls_back_to_the_page_the_rules_name(panel_answers: dict) -> None:
    for pages, got in zip(_corpus()["lists"], panel_answers["fallback"], strict=True):
        assert got == _id(fallback_page(pages)), [p["id"] for p in pages]


def test_the_page_standing_in_for_a_missing_one_is_the_rules_stand_in(panel_answers: dict) -> None:
    answers = iter(panel_answers["stand_in"])
    for pages in _corpus()["lists"]:
        for wanted in WANTED:
            assert next(answers) == _id(stand_in(pages, wanted)), ([p["id"] for p in pages], wanted)


def test_an_overlay_draws_in_the_box_the_rules_give_it(panel_answers: dict) -> None:
    for page, got in zip(_corpus()["boxed"], panel_answers["boxes"], strict=True):
        assert got == overlay_box(page), page


def _masters_asked() -> list[tuple[dict, str]]:
    return [(master, page_id) for master in _corpus()["masters"] for page_id in MASTER_PAGE_IDS]


def test_a_master_draws_on_the_pages_the_rules_say(panel_answers: dict) -> None:
    for (master, page_id), got in zip(_masters_asked(), panel_answers["draws_on"], strict=True):
        assert got is master_draws_on(master, page_id, RENDERED_TYPES), (master, page_id)


def test_a_page_with_nothing_of_its_own_draws_nothing_unless_a_master_draws_on_it(panel_answers: dict) -> None:
    """The empty-page notice: a page with no elements of its own says it has no
    controls yet unless a master draws on it."""
    for (master, page_id), got in zip(_masters_asked(), panel_answers["draws_nothing"], strict=True):
        assert got is not master_draws_on(master, page_id, RENDERED_TYPES), (master, page_id)


def test_a_plain_page_runs_author_markup_only_under_a_custom_master_that_draws_on_it(panel_answers: dict) -> None:
    """A ui/ file changing redraws a plain page only when a custom-control
    master draws on it."""
    for (master, page_id), got in zip(_masters_asked(), panel_answers["runs_markup"], strict=True):
        expected = master.get("type") == "custom" and master_draws_on(master, page_id, RENDERED_TYPES)
        assert got is expected, (master, page_id)


def test_the_corpus_reaches_every_answer(panel_answers: dict) -> None:
    """Each answer the rules can give is in the corpus, so none goes unheld."""
    assert set(panel_answers["opens_over"]) == {True, False}
    assert {got for got in panel_answers["fallback"]} >= {None, "main", "home", "dialog", "blank", "none"}
    boxes = {json.dumps(box, sort_keys=True) for box in panel_answers["boxes"]}
    defaults = {json.dumps(overlay_box(_page("p", t)), sort_keys=True) for t in ("overlay", "sidebar")}
    assert defaults < boxes
    assert set(panel_answers["draws_on"]) == set(panel_answers["runs_markup"]) == {True, False}


@pytest.mark.parametrize(
    "master, page_id, draws",
    [
        ({"type": "button", "pages": "*"}, "home", True),
        ({"type": "button"}, "home", True),
        ({"type": "button", "pages": None}, "home", False),
        ({"type": "button", "pages": ["home"]}, "home", True),
        ({"type": "button", "pages": ["other"]}, "home", False),
        ({"type": "button", "pages": []}, "home", False),
        ({"type": "button", "pages": "home"}, "home", False),
        ({"type": "button", "pages": "*", "hidden": True}, "home", False),
        ({"type": "hologram", "pages": "*"}, "home", False),
        ({"pages": "*"}, "home", False),
    ],
    ids=[
        "every page", "blank pages", "null pages", "listed", "listed elsewhere", "listed nowhere",
        "a page id as text", "hidden", "a type the panel does not draw", "no type",
    ],
)
def test_a_master_draws_on_a_page_it_names_unhidden_and_of_a_drawn_type(master, page_id, draws) -> None:
    assert master_draws_on(master, page_id, RENDERED_TYPES) is draws


# What the panel does not ask through pages.js yet: the rules alone.


def test_a_type_this_release_does_not_know_reads_as_a_regular_page() -> None:
    for written in WRITTEN_TYPES:
        page = _page("p", written)
        known = written if written in page_rules.PAGE_TYPES else "page"
        assert page_type_of(page) == known, written


def test_the_navigation_words_are_moves_and_the_prefix_names_no_page() -> None:
    assert [w for w in ("$back", "$dismiss", "$home", "back", "", None) if is_navigation_word(w)] == [
        "$back", "$dismiss",
    ]
    assert [t for t in ("$back", "$dismiss", "$home", "home", "", None, 5) if names_no_page(t)] == [
        "$back", "$dismiss", "$home",
    ]
