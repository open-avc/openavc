"""What a project's pages may hold, as ``openavc.ui.page_structure`` reports it.

Each case is one shape the project model accepts and the panel then draws
wrongly or not at all, swapped into an otherwise clean project; the report
places it (page, control, arrangement) under its own code. The same answers
come back whether the project is read as the loader's models or as the JSON
it was written in, and a clean project, or any shipped starter, reports
nothing.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from openavc.core.project_loader import ProjectConfig
from openavc.ui import page_structure as ps

TEMPLATES = Path(__file__).resolve().parent.parent / "openavc" / "templates"


def _project() -> dict:
    return {
        "project": {"id": "acme_room", "name": "Acme Room"},
        "ui": {
            "master_elements": [
                {"id": "home", "type": "button", "pages": "*"},
                {"id": "clock", "type": "label", "pages": ["main"]},
            ],
            "pages": [
                {
                    "id": "main", "name": "Main",
                    "elements": [
                        {"id": "box", "type": "group"},
                        {"id": "inner", "type": "group", "parent": "box"},
                        {"id": "kid", "type": "button", "parent": "inner"},
                        {"id": "power", "type": "button", "bindings": {
                            "do": {"press": [{"action": "state.set", "key": "var.on", "value": True}]},
                            "show": {"look": {"key": "var.on", "states": {"true": {"label": "On"}}},
                                     "visible_when": {"key": "var.mode", "operator": "=="}},
                        }},
                    ],
                    "layouts": [
                        {"id": "landscape", "primary": True},
                        {"id": "portrait", "orientation": "portrait", "inherits": "landscape"},
                    ],
                },
                {"id": "other", "name": "Other", "elements": [{"id": "back", "type": "button"}]},
            ],
        },
    }


def _main(project: dict) -> dict:
    return project["ui"]["pages"][0]


def _element(project: dict, element_id: str) -> dict:
    return next(el for page in project["ui"]["pages"] for el in page["elements"]
                if el["id"] == element_id)


def _set(element_id: str, **fields):
    def change(project: dict) -> None:
        _element(project, element_id).update(fields)
    return change


def _add_element(page: int, element: dict):
    def change(project: dict) -> None:
        project["ui"]["pages"][page]["elements"].append(element)
    return change


def _add_master(master: dict):
    def change(project: dict) -> None:
        project["ui"]["master_elements"].append(master)
    return change


def _add_page(page: dict):
    def change(project: dict) -> None:
        project["ui"]["pages"].append(page)
    return change


def _layouts(*layouts: dict):
    def change(project: dict) -> None:
        _main(project)["layouts"] = list(layouts)
    return change


def _binding(bindings: dict):
    return _set("power", bindings=bindings)


PRIMARY = {"id": "landscape", "primary": True}

#: (what is swapped in, the issues it makes: code, page, element, layout)
CASES = {
    "an element id used twice on one page": (
        _add_element(0, {"id": "power", "type": "label"}),
        [(ps.ELEMENT_ID_TAKEN, "main", "power", None)],
    ),
    "a page control with another page's control id": (
        _add_element(1, {"id": "kid", "type": "label"}),
        [(ps.ELEMENT_ID_TAKEN, "other", "kid", None)],
    ),
    "a page control with a master's id: the master is the copy no press reaches": (
        _add_element(1, {"id": "home", "type": "button"}),
        [(ps.ELEMENT_ID_TAKEN, None, "home", None)],
    ),
    "two masters with one id": (
        _add_master({"id": "clock", "type": "label"}),
        [(ps.ELEMENT_ID_TAKEN, None, "clock", None)],
    ),
    "two pages with one id": (
        _add_page({"id": "other", "name": "Again", "elements": []}),
        [(ps.PAGE_ID_TAKEN, "other", None, None)],
    ),
    "a page id that is a move": (
        _add_page({"id": "$back", "name": "Back", "elements": []}),
        [(ps.PAGE_ID_NAMES_NO_PAGE, "$back", None, None)],
    ),
    "a page id with the moves' prefix": (
        _add_page({"id": "$tab", "name": "Tab", "elements": []}),
        [(ps.PAGE_ID_NAMES_NO_PAGE, "$tab", None, None)],
    ),
    "a container cycle": (
        _set("box", parent="inner"),
        [(ps.PARENT_LOOP, "main", "box", None), (ps.PARENT_LOOP, "main", "inner", None)],
    ),
    "a control inside itself": (
        _set("box", parent="box"),
        [(ps.PARENT_LOOP, "main", "box", None)],
    ),
    "a container naming nothing": (
        _set("kid", parent="ghost"),
        [(ps.PARENT_MISSING, "main", "kid", None)],
    ),
    "a blank container": (
        _set("kid", parent=""),
        [(ps.PARENT_MISSING, "main", "kid", None)],
    ),
    "a button as a container": (
        _set("kid", parent="power"),
        [(ps.PARENT_NOT_CONTAINER, "main", "kid", None)],
    ),
    "two arrangements with one id": (
        _layouts(PRIMARY, {"id": "landscape", "orientation": "portrait"}),
        [(ps.LAYOUT_ID_REPEATED, "main", None, "landscape")],
    ),
    "two landscape arrangements": (
        _layouts(PRIMARY, {"id": "wide", "orientation": "landscape"}),
        [(ps.ORIENTATION_TAKEN, "main", None, "wide")],
    ),
    "an inherits loop": (
        _layouts({**PRIMARY, "inherits": "portrait"},
                 {"id": "portrait", "orientation": "portrait", "inherits": "landscape"}),
        [(ps.INHERITS_LOOP, "main", None, "landscape")],
    ),
    "an arrangement inheriting itself": (
        _layouts({**PRIMARY, "inherits": "landscape"}),
        [(ps.INHERITS_SELF, "main", None, "landscape")],
    ),
    "an inherits naming nothing": (
        _layouts(PRIMARY, {"id": "portrait", "orientation": "portrait", "inherits": "ghost"}),
        [(ps.INHERITS_MISSING, "main", None, "portrait")],
    ),
    "a master in a container": (
        _add_master({"id": "badge", "type": "label", "parent": "box"}),
        [(ps.MASTER_PARENT, None, "badge", None)],
    ),
    "a look's states as a list": (
        _binding({"show": {"look": {"key": "var.on", "states": [{"label": "On"}]}}}),
        [(ps.BINDING, "main", "power", None)],
    ),
    "a visible_when operator that is no operator": (
        _binding({"show": {"visible_when": {"key": "var.on", "operator": "constructor"}}}),
        [(ps.BINDING, "main", "power", None)],
    ),
    "a visible_when operator that is a list": (
        _binding({"show": {"visible_when": {"key": "var.on", "operator": ["eq"]}}}),
        [(ps.BINDING, "main", "power", None)],
    ),
    "a press mode that is an object": (
        _binding({"do": {"press": [{"action": "macro", "macro": "m", "mode": {"x": 1}}]}}),
        [(ps.BINDING, "main", "power", None)],
    ),
    "an action the runtime does not dispatch": (
        _binding({"do": {"press": [{"action": "launch_rocket"}]}}),
        [(ps.BINDING, "main", "power", None)],
    ),
    "the retired flat model": (
        _binding({"press": [{"action": "macro", "macro": "m"}]}),
        [(ps.LEGACY_BINDINGS, "main", "power", None)],
    ),
    "a state.set on a device's key": (
        _binding({"do": {"press": [{"action": "state.set", "key": "device.amp.mute"}]}}),
        [(ps.DEVICE_STATE_WRITE, "main", "power", None)],
    ),
    "a two-way value that is not a variable": (
        _binding({"show": {"value": {"key": "device.amp.level", "write_back": True}}}),
        [(ps.WRITE_BACK_NOT_VARIABLE, "main", "power", None)],
    ),
    "a master's bindings": (
        _add_master({"id": "go", "type": "button",
                     "bindings": {"do": {"press": [{"action": "launch_rocket"}]}}}),
        [(ps.BINDING, None, "go", None)],
    ),
}


def _issues(project: dict, reading: str) -> list[ps.Issue]:
    if reading == "models":
        ui = ProjectConfig.model_validate(project).ui
        return ps.page_issues(ui.pages, ui.master_elements)
    return ps.page_issues(project["ui"]["pages"], project["ui"].get("master_elements", []))


def _placed(issues: list[ps.Issue]) -> list[tuple]:
    return [(i.code, i.page, i.element, i.layout) for i in issues]


@pytest.mark.parametrize("reading", ["models", "json"])
@pytest.mark.parametrize("case", list(CASES))
def test_each_shape_the_model_accepts_is_placed_under_its_code(case, reading):
    change, expected = CASES[case]
    project = copy.deepcopy(_project())
    change(project)
    issues = _issues(project, reading)
    assert _placed(issues) == expected
    assert all(issue.message for issue in issues)


@pytest.mark.parametrize("reading", ["models", "json"])
def test_a_clean_project_reports_nothing(reading):
    assert _issues(_project(), reading) == []


@pytest.mark.parametrize("template", sorted(TEMPLATES.glob("*.avc")), ids=lambda p: p.stem)
def test_every_shipped_starter_reports_nothing(template):
    assert _issues(json.loads(template.read_text(encoding="utf-8")), "json") == []


def test_every_code_is_exercised():
    codes = {code for _, expected in CASES.values() for code, *_ in expected}
    declared = {value for name, value in vars(ps).items() if name.lower() == value}
    assert codes == declared


def test_an_orientation_left_out_is_the_schemas():
    from openavc.core.project_loader import Layout

    assert Layout(id="x").orientation == ps.ORIENTATION_LEFT_OUT


def test_the_refusal_joins_every_sentence_and_is_none_for_none():
    issues = [ps.Issue(ps.BINDING, "one"), ps.Issue(ps.BINDING, "two")]
    assert ps.refusal(issues) == "one; two"
    assert ps.refusal([]) is None


def test_an_operator_by_any_spelling_the_panel_reads_is_taken():
    for op in ("==", "equals", "eq", "!=", ">=", "", None):
        assert ps.binding_issues({"show": {"visible_when": {"key": "var.x", "operator": op}}}) == []


def test_a_loop_is_reported_once_however_many_arrangements_feed_it():
    layouts = [
        {"id": "a", "inherits": "b"}, {"id": "b", "inherits": "c"},
        {"id": "c", "inherits": "b"}, {"id": "d", "inherits": "c"},
    ]
    assert [(i.code, i.layout) for i in ps.inherits_loops(layouts)] == [(ps.INHERITS_LOOP, "b")]


def test_a_new_control_is_told_which_copy_a_press_reaches():
    holders = ps.element_id_holders(
        [{"id": "p1", "elements": [{"id": "x"}]}, {"id": "p2", "elements": [{"id": "x"}]}],
        [{"id": "x"}, {"id": "m"}],
    )
    assert holders == {"x": ["p1", "p2", None], "m": [None]}
    assert ps.id_taken("x", holders["x"]).message == "Element 'x' already exists on page 'p1'"
    assert ps.id_taken("m", holders["m"]).message == "Element 'm' already exists as a master element"
    assert ps.id_taken("m", holders["m"], master=True).message == "Master element 'm' already exists"
    assert ps.id_taken("new", holders.get("new", ())) is None
