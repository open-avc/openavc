"""A control's id is spelled one way: a lowercase letter, then lowercase
letters, digits and underscores.

The rule is ``ui/page_structure.py``'s. The Builder's rename reads the same
pattern and sentence from the generated table, and runs the same cases
(``tests/fixtures/element_id_cases.json``, read by ``elementId.test.ts``).
Every door that creates a control asks it: the AI's three create doors refuse a
new control's id that breaks it, naming every offender in one reply, and check
nothing but the id being created. An id a saved project already holds is
reported, never refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.core.project_values_gen import TS_ARTIFACT, element_id_rule, render
from openavc.ui import page_report as pr
from openavc.ui import page_structure as ps

from tests.test_ui_tools_doors_pinned import BOX, _call, _engine, _page

CASES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "element_id_cases.json").read_text(encoding="utf-8")
)["cases"]

SENTENCE = ps.ELEMENT_ID_SENTENCE


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_each_case_is_judged_as_the_corpus_says(case):
    assert ps.is_element_id(case["id"]) is case["expected"]


@pytest.mark.parametrize("value", [None, 5, 5.0, True, ["a"], {"a": 1}])
def test_anything_but_text_is_not_an_id_and_never_raises(value):
    assert ps.is_element_id(value) is False


def test_no_id_is_left_to_the_schema():
    assert ps.element_id_issue(None) is None


def test_the_sentence_names_the_control_and_the_kind():
    assert ps.element_id_issue("Btn").message == f"Element 'Btn': {SENTENCE}"
    assert ps.element_id_issue("Btn", master=True).message == f"Master element 'Btn': {SENTENCE}"
    assert ps.element_id_issue("btn") is None


def test_the_builder_is_served_the_rule_whole():
    rule = element_id_rule()
    assert rule == {"pattern": "^[a-z][a-z0-9_]*$", "sentence": SENTENCE}
    assert f"export const ELEMENT_ID_RULE = {json.dumps(rule, indent=2)} as const;" in render(TS_ARTIFACT)


def _ui(pages: list, masters: list | None = None) -> tuple[list, list]:
    return pages, masters or []


def test_a_saved_id_that_breaks_it_is_reported_on_its_control():
    pages = [{"id": "main", "elements": [
        {"id": "ok", "type": "button"},
        {"id": "Upper-Case", "type": "button"},
    ]}]
    masters = [{"id": "a b", "type": "label"}]
    issues = pr.reported_issues(pages, masters)
    assert [(i.code, i.page, i.element, i.message) for i in issues] == [
        (ps.ELEMENT_ID_SPELLED, "main", "Upper-Case", f"Element 'Upper-Case': {SENTENCE}"),
        (ps.ELEMENT_ID_SPELLED, None, "a b", f"Master element 'a b': {SENTENCE}"),
    ]


def test_an_id_misspelled_and_repeated_is_both():
    pages = [{"id": "main", "elements": [{"id": "Btn", "type": "button"}, {"id": "Btn", "type": "label"}]}]
    assert [i.code for i in ps.page_issues(pages)] == [
        ps.ELEMENT_ID_SPELLED, ps.ELEMENT_ID_SPELLED, ps.ELEMENT_ID_TAKEN,
    ]


@pytest.mark.parametrize("element_id", [5, ["a"], {"a": 1}, None])
def test_a_report_reads_any_posted_id_without_raising(element_id):
    issues = ps.page_issues([{"id": "main", "elements": [{"id": element_id, "type": "button"}]}])
    codes = [i.code for i in issues]
    assert codes == ([] if element_id is None else [ps.ELEMENT_ID_SPELLED])


# --- the AI's create doors ---------------------------------------------------


@pytest.mark.asyncio
async def test_add_elements_refuses_every_id_that_breaks_it_in_one_reply():
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_ui_elements", {"page_id": "main", "elements": [
        {"id": "Upper-Case", "type": "button", "placement": dict(BOX)},
        {"id": "fine_one", "type": "button", "placement": dict(BOX)},
        {"id": "a b", "type": "label", "placement": dict(BOX)},
    ]})
    problems = [f"Element 'Upper-Case': {SENTENCE}", f"Element 'a b': {SENTENCE}"]
    assert result == {
        "error": "2 elements could not be added: " + "; ".join(problems),
        "errors": problems,
    }
    assert engine.project.model_dump() == before


@pytest.mark.asyncio
async def test_add_page_refuses_an_inline_control_whose_id_breaks_it():
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_ui_page", {"id": "fresh", "elements": [
        {"id": "ok", "type": "button", "placement": dict(BOX)},
        {"id": "Power", "type": "button", "placement": dict(BOX)},
    ]})
    assert result == {
        "error": f"1 element could not be added: Element 'Power': {SENTENCE}",
        "errors": [f"Element 'Power': {SENTENCE}"],
    }
    assert engine.project.model_dump() == before


@pytest.mark.asyncio
async def test_add_master_refuses_an_id_that_breaks_it():
    engine = _engine()
    before = engine.project.model_dump()
    result = await _call(engine, "add_master_element", {"id": "Home", "type": "button", "pages": "*"})
    assert result == {"error": f"Master element 'Home': {SENTENCE}"}
    assert engine.project.model_dump() == before


@pytest.mark.asyncio
async def test_a_good_id_lands_inside_a_container_whose_saved_id_breaks_it():
    """Only the id being created is asked about: a project that already holds
    a misspelled id still takes every other edit."""
    project = json.loads(json.dumps(_engine().project.model_dump(mode="json")))
    page = project["ui"]["pages"][0]
    page["elements"].append({"id": "Old-Group", "type": "group"})
    engine = _engine(project)
    result = await _call(engine, "add_ui_elements", {"page_id": "main", "elements": [
        {"id": "new_btn", "type": "button", "parent": "Old-Group", "placement": dict(BOX)},
    ]})
    assert result["status"] == "created"
    assert [el.id for el in _page(engine, "main").elements][-1] == "new_btn"


@pytest.mark.asyncio
async def test_a_control_with_no_id_is_not_told_the_spelling():
    engine = _engine()
    result = await _call(engine, "add_ui_elements", {"page_id": "main", "elements": [
        {"type": "button", "placement": dict(BOX)},
    ]})
    assert SENTENCE not in result["error"]
