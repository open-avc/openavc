"""The places outside a page's controls that name a page the project does not
have: the idle page, a master element's pages, and a macro's page move.

A control's page move is ``reference_findings``'; these three are the
project's, so ``missing_pages`` reads the project's UI and its macros. The
Builder's Validate (through ``POST /api/ui/page-issues``) and the AI's
``review_ui`` both list them, so they say the same thing of the same project.
"""

from __future__ import annotations

import pytest

from openavc.core.project_loader import ProjectConfig
from openavc.ui.missing_pages import (
    IDLE_PAGE_MISSING,
    MACRO_PAGE_MISSING,
    MASTER_PAGE_MISSING,
    MissingPage,
    missing_pages,
)


def _ui(*, idle_page=None, masters=(), pages=None) -> dict:
    settings = {} if idle_page is None else {"idle_page": idle_page}
    return {
        "settings": settings,
        "pages": pages if pages is not None else [
            {"id": "main", "name": "Main", "elements": []},
            {"id": "setup", "name": "Setup", "elements": []},
        ],
        "master_elements": list(masters),
    }


def _navigate(page, **more) -> dict:
    return {"action": "ui.navigate", "page": page, **more}


def test_an_idle_page_that_is_not_there_names_the_page_the_panel_goes_to_instead():
    assert missing_pages(_ui(idle_page="ghost")) == [MissingPage(
        IDLE_PAGE_MISSING,
        "The idle page 'ghost' does not exist, so an idle panel returns to 'main' instead. "
        "The pages are: main, setup.",
    )]


def test_the_page_standing_in_for_the_idle_page_is_the_first_regular_one():
    pages = [
        {"id": "confirm", "page_type": "overlay", "elements": []},
        {"id": "home", "elements": []},
    ]
    [said] = missing_pages(_ui(idle_page="ghost", pages=pages))
    assert said.message == (
        "The idle page 'ghost' does not exist, so an idle panel returns to 'home' instead. "
        "The pages are: confirm, home."
    )


def test_an_idle_page_with_no_pages_to_stand_in_says_so():
    [said] = missing_pages(_ui(idle_page="main", pages=[]))
    assert said.message == "The idle page 'main' does not exist. The pages are: none."


@pytest.mark.parametrize("idle_page", [None, "", "main", "setup", 3, ["main"]])
def test_an_idle_page_that_is_there_or_names_nothing_says_nothing(idle_page):
    assert missing_pages(_ui(idle_page=idle_page)) == []


@pytest.mark.parametrize("word", ["$back", "$dismiss"])
def test_the_panels_own_moves_are_no_idle_page(word):
    [said] = missing_pages(_ui(idle_page=word))
    assert said.code == IDLE_PAGE_MISSING


def test_each_page_a_master_shows_on_that_is_not_there_is_named():
    masters = [
        {"id": "nav", "type": "button", "pages": ["main", "ghost", "setup", "gone"]},
        {"id": "everywhere", "type": "label", "pages": "*"},
        {"id": "fine", "type": "label", "pages": ["setup"]},
    ]
    assert missing_pages(_ui(masters=masters)) == [
        MissingPage(
            MASTER_PAGE_MISSING,
            "Master element 'nav' shows on page 'ghost', which does not exist. "
            "The pages are: main, setup.",
            element="nav",
        ),
        MissingPage(
            MASTER_PAGE_MISSING,
            "Master element 'nav' shows on page 'gone', which does not exist. "
            "The pages are: main, setup.",
            element="nav",
        ),
    ]


@pytest.mark.parametrize("pages", [None, "*", "main", [], [3, None, ""], {"main": True}])
def test_a_master_whose_pages_name_no_page_id_says_nothing(pages):
    master = {"id": "m", "type": "label"} if pages is None else {"id": "m", "type": "label", "pages": pages}
    assert missing_pages(_ui(masters=[master])) == []


def test_a_master_listing_a_navigation_word_is_named():
    [said] = missing_pages(_ui(masters=[{"id": "m", "type": "label", "pages": ["$back"]}]))
    assert said.code == MASTER_PAGE_MISSING


def test_a_macros_page_move_to_a_page_that_is_not_there_is_named_where_it_is():
    macros = [
        {"id": "evening", "name": "Evening", "steps": [
            {"action": "delay", "seconds": 1},
            _navigate("ghost"),
            {"action": "conditional", "condition": {"key": "var.x", "operator": "truthy"},
             "then_steps": [_navigate("main"), _navigate("gone")],
             "else_steps": [{"action": "conditional", "then_steps": [_navigate("lost")]}]},
        ]},
        {"id": "morning", "steps": [_navigate("$back"), _navigate("$dismiss"), _navigate("")]},
    ]
    found = missing_pages(_ui(), macros)
    assert [(f.code, f.macro, f.step) for f in found] == [
        (MACRO_PAGE_MISSING, "evening", "steps[1]"),
        (MACRO_PAGE_MISSING, "evening", "steps[2].then_steps[1]"),
        (MACRO_PAGE_MISSING, "evening", "steps[2].else_steps[0].then_steps[0]"),
    ]
    assert found[0].message == (
        "Macro 'evening' navigates to page 'ghost', which does not exist. The pages are: main, setup."
    )


def test_the_idle_page_comes_first_then_the_masters_then_the_macros():
    ui = _ui(idle_page="ghost", masters=[{"id": "m", "type": "label", "pages": ["ghost"]}])
    found = missing_pages(ui, [{"id": "mac", "steps": [_navigate("ghost")]}])
    assert [f.code for f in found] == [IDLE_PAGE_MISSING, MASTER_PAGE_MISSING, MACRO_PAGE_MISSING]


def test_a_loaded_project_is_read_as_the_json_it_came_from():
    raw = {
        "project": {"id": "acme_room", "name": "Acme Room"},
        "ui": _ui(idle_page="ghost", masters=[{"id": "m", "type": "label", "pages": ["ghost"]}]),
        "macros": [{"id": "mac", "name": "Mac", "steps": [
            {"action": "conditional", "condition": {"key": "var.x", "operator": "truthy"},
             "then_steps": [_navigate("ghost")]},
        ]}],
    }
    project = ProjectConfig.model_validate(raw)
    assert missing_pages(project.ui, project.macros) == missing_pages(raw["ui"], raw["macros"])
    assert len(missing_pages(project.ui, project.macros)) == 3


@pytest.mark.parametrize(("ui", "macros"), [
    ([], "x"),
    ({"pages": "x", "master_elements": 3, "settings": []}, [None, 3, "x"]),
    ({"pages": [None, 3, {"id": ["main"]}, {"id": {"a": 1}}], "settings": {"idle_page": {"a": 1}},
      "master_elements": [None, {"id": ["m"], "pages": [["ghost"], {"a": 1}, "ghost"]}]},
     [{"id": ["mac"], "steps": "x"}, {"steps": [None, 3, {"action": ["ui.navigate"], "page": "x"},
                                                {"action": "ui.navigate", "page": ["x"]},
                                                {"action": "conditional", "then_steps": "x",
                                                 "else_steps": [None, _navigate("ghost")]}]}]),
])
def test_any_json_is_answered(ui, macros):
    assert all(isinstance(f, MissingPage) for f in missing_pages(ui, macros))


def test_steps_nested_past_the_stack_are_still_walked():
    step: dict = _navigate("ghost")
    for _ in range(1200):
        step = {"action": "conditional", "then_steps": [step]}
    [said] = missing_pages(_ui(), [{"id": "deep", "steps": [step]}])
    assert said.step.count("then_steps") == 1200
