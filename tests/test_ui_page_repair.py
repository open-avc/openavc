"""The repair every load makes to a page's containers, as
``openavc.ui.page_repair`` makes it.

A saved project can hold a container link the panel draws wrongly or not at
all: a ``parent`` naming no control on the page, a loop of controls each
inside the next, a control inside one that is not a container (a button), a
master element naming one. Each has one plain repair that keeps what the panel
draws, or brings back what it drew nothing of, and every load makes it
whatever the project's version. A repair a person would see is said in the
load's notes; the rest the page report goes on saying.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from openavc.core.project_loader import ProjectConfig, load_project, save_project
from openavc.core.project_migration import CURRENT_VERSION, migrate_project
from openavc.core.project_update import CHANGED, MigrationNotes
from openavc.ui import page_structure as ps
from openavc.ui.page_repair import repair_page_structure

TEMPLATES = Path(__file__).resolve().parent.parent / "openavc" / "templates"


def _box(x, y, w, h) -> dict:
    return {"x": x, "y": y, "w": w, "h": h}


def _ui(elements: list[dict], placements: dict, *, variants: list[dict] = (), masters: list[dict] = ()) -> dict:
    return {
        "pages": [{
            "id": "main", "name": "Main",
            "elements": elements,
            "layouts": [{"id": "landscape", "primary": True, "placements": placements}, *variants],
        }],
        "master_elements": list(masters),
    }


def _repaired(ui: dict) -> tuple[bool, MigrationNotes]:
    notes = MigrationNotes()
    return repair_page_structure(ui, notes), notes


def _page(ui: dict) -> dict:
    return ui["pages"][0]


def _el(ui: dict, element_id: str) -> dict:
    return next(el for el in _page(ui)["elements"] if el["id"] == element_id)


def _layout(ui: dict, layout_id: str = "landscape") -> dict:
    return next(lay for lay in _page(ui)["layouts"] if lay["id"] == layout_id)


def _said(notes: MigrationNotes) -> list[tuple[str, str, str, str]]:
    return [(n.kind, n.location, n.page_id, n.element_id) for n in notes.notes]


# --- a container naming nothing ------------------------------------------------


@pytest.mark.parametrize("named", ["gone", "", ["a", "list"], {"an": "object"}, 7])
def test_a_container_naming_nothing_is_dropped_and_nothing_is_said(named):
    ui = _ui([{"id": "a", "type": "button", "parent": named}], {"a": _box(10, 10, 20, 20)})
    rewrote, notes = _repaired(ui)
    assert rewrote is True
    assert _el(ui, "a").get("parent") is None
    assert _layout(ui)["placements"]["a"] == _box(10, 10, 20, 20)
    assert notes.notes == []


# --- a loop --------------------------------------------------------------------


def test_a_control_naming_itself_moves_to_the_page_and_is_said():
    ui = _ui([{"id": "a", "type": "group", "parent": "a"}], {"a": _box(10, 10, 20, 20)})
    rewrote, notes = _repaired(ui)
    assert rewrote is True
    assert _el(ui, "a").get("parent") is None
    assert _layout(ui)["placements"]["a"] == _box(10, 10, 20, 20)
    assert _said(notes) == [(CHANGED, "Main > a", "main", "a")]
    assert "panel drew nothing of it" in notes.notes[0].message


def test_containers_inside_each_other_all_move_to_the_page_and_what_they_hold_stays_in_them():
    ui = _ui(
        [
            {"id": "a", "type": "group", "parent": "b"},
            {"id": "b", "type": "group", "parent": "c"},
            {"id": "c", "type": "group", "parent": "a"},
            {"id": "kid", "type": "button", "parent": "a"},
            {"id": "free", "type": "button"},
        ],
        {"a": _box(10, 10, 50, 50), "b": _box(20, 20, 50, 50), "c": _box(5, 5, 10, 10), "kid": _box(0, 0, 50, 50)},
    )
    rewrote, notes = _repaired(ui)
    assert rewrote is True
    assert [_el(ui, i).get("parent") for i in ("a", "b", "c", "kid")] == [None, None, None, "a"]
    assert _layout(ui)["placements"]["a"] == _box(10, 10, 50, 50)
    assert _said(notes) == [(CHANGED, f"Main > {i}", "main", i) for i in ("a", "b", "c")]


def test_a_button_in_a_loop_of_buttons_moves_to_the_page_at_its_own_numbers():
    """A loop is found before a container that is not one: the panel drew
    neither button, so there is no drawn box to keep."""
    ui = _ui(
        [{"id": "a", "type": "button", "parent": "b"}, {"id": "b", "type": "button", "parent": "a"}],
        {"a": _box(10, 10, 20, 20), "b": _box(50, 50, 20, 20)},
    )
    _repaired(ui)
    assert [_el(ui, i).get("parent") for i in ("a", "b")] == [None, None]
    assert _layout(ui)["placements"] == {"a": _box(10, 10, 20, 20), "b": _box(50, 50, 20, 20)}


# --- a control inside a control that is not a container -------------------------


def test_a_control_inside_a_button_moves_to_the_page_where_it_was_drawn():
    ui = _ui(
        [{"id": "b", "type": "button"}, {"id": "c", "type": "label", "parent": "b"}],
        {"b": _box(10, 10, 40, 20), "c": _box(50, 50, 50, 50)},
    )
    rewrote, notes = _repaired(ui)
    assert rewrote is True
    assert _el(ui, "c").get("parent") is None
    assert _layout(ui)["placements"]["c"] == _box(30, 20, 20, 10)
    assert _said(notes) == [(CHANGED, "Main > c", "main", "c")]
    assert "'b'" in notes.notes[0].message
    assert "hides" not in notes.notes[0].message


def test_an_arrangement_that_moves_only_the_button_gets_the_control_where_it_drew_it():
    portrait = {"id": "portrait", "orientation": "portrait", "inherits": "landscape",
                "placements": {"b": _box(0, 0, 20, 10)}}
    tall = {"id": "tall", "orientation": "portrait", "inherits": "portrait", "placements": {}}
    ui = _ui(
        [{"id": "b", "type": "button"}, {"id": "c", "type": "label", "parent": "b"}],
        {"b": _box(10, 10, 40, 20), "c": _box(50, 50, 50, 50)},
        variants=[portrait, tall],
    )
    _repaired(ui)
    assert _layout(ui)["placements"]["c"] == _box(30, 20, 20, 10)
    assert _layout(ui, "portrait")["placements"]["c"] == _box(10, 5, 10, 5)
    assert "c" not in _layout(ui, "tall")["placements"]


def test_a_control_inside_a_button_inside_a_group_moves_into_the_group_where_it_was_drawn():
    ui = _ui(
        [
            {"id": "g", "type": "group"},
            {"id": "b", "type": "button", "parent": "g"},
            {"id": "c", "type": "label", "parent": "b"},
        ],
        {"g": _box(0, 0, 50, 50), "b": _box(20, 20, 50, 50), "c": _box(0, 0, 100, 100)},
    )
    _repaired(ui)
    assert [_el(ui, i).get("parent") for i in ("g", "b", "c")] == [None, "g", "g"]
    assert _layout(ui)["placements"]["b"] == _box(20, 20, 50, 50)
    assert _layout(ui)["placements"]["c"] == _box(20, 20, 50, 50)


def test_a_control_inside_a_button_inside_a_group_with_no_room_moves_to_the_page():
    ui = _ui(
        [
            {"id": "g", "type": "group"},
            {"id": "b", "type": "button", "parent": "g"},
            {"id": "c", "type": "label", "parent": "b"},
        ],
        {"g": _box(10, 10, 0, 50), "b": _box(20, 20, 50, 50), "c": _box(0, 0, 100, 100)},
    )
    _repaired(ui)
    assert _el(ui, "c").get("parent") is None
    assert _layout(ui)["placements"]["c"] == _box(10, 20, 0, 25)


def test_buttons_inside_buttons_each_move_out_in_the_order_they_were_drawn():
    ui = _ui(
        [
            {"id": "c", "type": "label", "parent": "b2"},
            {"id": "b1", "type": "button"},
            {"id": "b2", "type": "button", "parent": "b1"},
            {"id": "after", "type": "button"},
        ],
        {"b1": _box(0, 0, 50, 50), "b2": _box(50, 50, 50, 50), "c": _box(0, 0, 50, 50)},
    )
    _repaired(ui)
    assert [el["id"] for el in _page(ui)["elements"]] == ["b1", "b2", "c", "after"]
    assert [_el(ui, i).get("parent") for i in ("b1", "b2", "c")] == [None, None, None]
    assert _layout(ui)["placements"]["b2"] == _box(25, 25, 25, 25)
    assert _layout(ui)["placements"]["c"] == _box(25, 25, 12.5, 12.5)


def test_a_group_inside_a_button_moves_out_with_what_it_holds():
    ui = _ui(
        [
            {"id": "b", "type": "button"},
            {"id": "g", "type": "group", "parent": "b"},
            {"id": "k", "type": "button", "parent": "g"},
        ],
        {"b": _box(0, 0, 50, 50), "g": _box(50, 50, 50, 50), "k": _box(10, 10, 20, 20)},
    )
    _repaired(ui)
    assert [_el(ui, i).get("parent") for i in ("b", "g", "k")] == [None, None, "g"]
    assert _layout(ui)["placements"]["g"] == _box(25, 25, 25, 25)
    assert _layout(ui)["placements"]["k"] == _box(10, 10, 20, 20)


def test_a_control_inside_a_button_stays_hidden_where_the_button_is():
    portrait = {"id": "portrait", "orientation": "portrait", "inherits": "landscape",
                "placements": {}, "hidden": ["b"]}
    ui = _ui(
        [{"id": "b", "type": "button"}, {"id": "c", "type": "label", "parent": "b"}],
        {"b": _box(10, 10, 40, 20), "c": _box(50, 50, 50, 50)},
        variants=[portrait],
    )
    _repaired(ui)
    assert _layout(ui, "portrait")["hidden"] == ["b", "c"]
    assert _layout(ui).get("hidden", []) == []


def test_a_control_listed_before_its_button_is_drawn_over_it_after_the_move():
    ui = _ui(
        [
            {"id": "c", "type": "label", "parent": "b"},
            {"id": "g", "type": "group"},
            {"id": "b", "type": "button", "parent": "g"},
            {"id": "after", "type": "button"},
        ],
        {"g": _box(0, 0, 50, 50), "b": _box(0, 0, 100, 100), "c": _box(0, 0, 50, 50)},
    )
    _repaired(ui)
    assert [el["id"] for el in _page(ui)["elements"]] == ["g", "b", "c", "after"]


def test_the_note_says_a_moved_control_no_longer_hides_with_a_button_that_hides():
    ui = _ui(
        [
            {"id": "b", "type": "button",
             "bindings": {"show": {"visible_when": {"key": "var.on", "operator": "eq", "value": True}}}},
            {"id": "c", "type": "label", "parent": "b"},
        ],
        {"b": _box(10, 10, 40, 20), "c": _box(50, 50, 50, 50)},
    )
    _, notes = _repaired(ui)
    assert "no longer hides when 'b' does" in notes.notes[0].message


def test_a_control_inside_a_button_with_no_box_anywhere_moves_to_the_page_as_it_is():
    ui = _ui(
        [{"id": "b", "type": "button"}, {"id": "c", "type": "label", "parent": "b"}],
        {"b": _box(10, 10, 40, 20)},
    )
    _repaired(ui)
    assert _el(ui, "c").get("parent") is None
    assert "c" not in _layout(ui)["placements"]


# --- a master's container --------------------------------------------------------


def test_a_masters_container_is_dropped_and_nothing_is_said():
    ui = _ui([{"id": "g", "type": "group"}], {"g": _box(0, 0, 50, 50)},
             masters=[{"id": "clock", "type": "label", "pages": "*", "parent": "g",
                       "placements": {"landscape": _box(0, 0, 10, 10)}}])
    rewrote, notes = _repaired(ui)
    assert rewrote is True
    assert ui["master_elements"][0]["parent"] is None
    assert ui["master_elements"][0]["placements"] == {"landscape": _box(0, 0, 10, 10)}
    assert notes.notes == []


# --- what it leaves --------------------------------------------------------------


def _every_shape() -> dict:
    return _ui(
        [
            {"id": "gone_parent", "type": "button", "parent": "nobody"},
            {"id": "self", "type": "group", "parent": "self"},
            {"id": "a", "type": "group", "parent": "b"},
            {"id": "b", "type": "group", "parent": "a"},
            {"id": "btn", "type": "button"},
            {"id": "kid", "type": "label", "parent": "btn"},
            {"id": "twice", "type": "button"},
            {"id": "twice", "type": "label"},
            {"id": "bad", "type": "button", "bindings": {"show": {"look": {"key": "var.x", "states": []}}}},
        ],
        {"btn": _box(10, 10, 40, 20), "kid": _box(50, 50, 50, 50), "a": _box(0, 0, 10, 10)},
        variants=[{"id": "landscape", "orientation": "portrait"}],
        masters=[{"id": "clock", "type": "label", "pages": "*", "parent": "btn"}],
    )


def test_what_the_repairs_leave_reports_only_what_is_not_theirs():
    ui = _every_shape()
    ui["pages"].append({"id": "$back", "name": "Back", "elements": []})
    _repaired(ui)
    codes = {issue.code for issue in ps.page_issues(ui["pages"], ui["master_elements"])}
    assert codes & {ps.PARENT_MISSING, ps.PARENT_LOOP, ps.PARENT_NOT_CONTAINER, ps.MASTER_PARENT} == set()
    assert {ps.ELEMENT_ID_TAKEN, ps.LAYOUT_ID_REPEATED, ps.PAGE_ID_NAMES_NO_PAGE, ps.BINDING} <= codes


def test_a_second_repair_changes_nothing():
    ui = _every_shape()
    _repaired(ui)
    once = copy.deepcopy(ui)
    rewrote, notes = _repaired(ui)
    assert rewrote is False
    assert notes.notes == []
    assert ui == once


@pytest.mark.parametrize("template", sorted(TEMPLATES.glob("*.avc")), ids=lambda p: p.stem)
def test_every_shipped_starter_is_left_alone(template):
    ui = json.loads(template.read_text(encoding="utf-8"))["ui"]
    before = copy.deepcopy(ui)
    rewrote, notes = _repaired(ui)
    assert (rewrote, notes.notes, ui) == (False, [], before)


@pytest.mark.parametrize("ui", [
    {},
    {"pages": None, "master_elements": "x"},
    {"pages": ["not a page", {"elements": "x", "layouts": 3}]},
    {"pages": [{"id": "p", "elements": [{"id": ["a"], "parent": "b"}, {"parent": "x"}, 5],
                "layouts": [{"id": "l", "placements": {"a": [1, 2]}}]}]},
    {"pages": [{"id": "p", "elements": [{"id": "b", "type": "button"}, {"id": "c", "parent": "b"}],
                "layouts": [{"id": "l", "inherits": "m", "placements": {"b": {"x": "ten"}, "c": {}}},
                            {"id": "m", "inherits": "l", "placements": "none", "hidden": "b"}]}]},
    {"pages": [{"id": "p", "elements": [{"id": "x", "type": "button"}, {"id": "x", "type": "group"},
                                        {"id": "y", "parent": "x"}],
                "layouts": [{"id": "l", "placements": {"x": {"x": 1, "y": 1, "w": 0, "h": 0}, "y": {}}}]}]},
    {"master_elements": [{"id": "m", "parent": "p"}, "junk"]},
    {"pages": [{"id": "p", "elements": [{"id": "b", "type": "button"}, {"id": "c", "parent": "b"}],
                "layouts": [{"id": ["l"], "placements": {"c": {}}}, {"id": {"o": 1}}, {"id": "l", "hidden": [["b"]]}]}]},
    {"pages": [{"id": "p", "elements": [{"id": "x", "type": "button"}, {"id": "x", "type": "label", "parent": "x"}]}]},
    {"pages": [{"id": "p", "elements": [{"id": "x", "type": "group"}, {"id": "x", "type": "button", "parent": "x"},
                                        {"id": "k", "parent": "x"}]}]},
])
def test_any_json_is_repaired_without_raising_and_keeps_every_control_once(ui):
    def held() -> list[list[int]]:
        pages = ui.get("pages") if isinstance(ui.get("pages"), list) else []
        return [sorted(map(id, page["elements"])) for page in pages
                if isinstance(page, dict) and isinstance(page.get("elements"), list)]

    before = held()
    repair_page_structure(ui, MigrationNotes())
    assert held() == before


# --- through the load -----------------------------------------------------------


def _project(ui: dict) -> dict:
    return {"openavc_version": CURRENT_VERSION, "project": {"id": "p", "name": "P"}, "ui": ui}


def test_every_load_repairs_a_current_project_and_says_so():
    data = _project(_ui([{"id": "a", "type": "group", "parent": "a"}], {"a": _box(10, 10, 20, 20)}))
    notes = MigrationNotes()
    data, migrated = migrate_project(data, notes)
    assert migrated is True
    assert data["ui"]["pages"][0]["elements"][0]["parent"] is None
    assert [n.element_id for n in notes.notes] == ["a"]


def test_a_save_is_not_repaired():
    data = _project(_ui([{"id": "a", "type": "group", "parent": "a"}], {"a": _box(10, 10, 20, 20)}))
    _, migrated = migrate_project(data, MigrationNotes(), repair=False)
    assert migrated is False
    assert data["ui"]["pages"][0]["elements"][0]["parent"] == "a"


def test_a_project_file_holding_a_loop_loads_with_its_controls_on_the_page(tmp_path):
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project(_ui(
        [{"id": "a", "type": "group", "parent": "b"}, {"id": "b", "type": "group", "parent": "a"}],
        {"a": _box(10, 10, 50, 50), "b": _box(20, 20, 50, 50)},
    ))), encoding="utf-8")
    project = load_project(path)
    assert [el.parent for el in project.ui.pages[0].elements] == [None, None]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [el.get("parent") for el in saved["ui"]["pages"][0]["elements"]] == [None, None]


def test_a_project_with_nothing_to_repair_is_not_rewritten(tmp_path):
    project = ProjectConfig.model_validate(_project(_ui(
        [{"id": "g", "type": "group"}, {"id": "k", "type": "button", "parent": "g"}],
        {"g": _box(0, 0, 50, 50), "k": _box(10, 10, 20, 20)},
    )))
    path = tmp_path / "project.avc"
    save_project(path, project)
    data = json.loads(path.read_text(encoding="utf-8"))
    before = copy.deepcopy(data)
    _, migrated = migrate_project(data, MigrationNotes())
    assert migrated is False
    assert data == before
