"""The AI's page tools, on a project that holds a repeated id.

A hand-edited or imported project can hold two controls, or two
arrangements, under one id; it still loads, and Validate reports it. A tool
that edits one reads and writes the first copy, the one a press reaches and
the panel draws (``ui/page_geometry.first_by_id``), so what it measures is
what it changes.
"""

from __future__ import annotations

from openavc.cloud.tools.ui_layouts import apply_layouts, reparent_element
from openavc.cloud.tools.ui_tools import _NewControls
from openavc.core.project_loader import ProjectConfig


def _project(elements: list, layouts: list | None = None) -> ProjectConfig:
    return ProjectConfig.model_validate({
        "project": {"id": "acme_room", "name": "Acme Room"},
        "ui": {"pages": [{
            "id": "main", "name": "Main", "elements": elements,
            "layouts": layouts or [{"id": "landscape", "primary": True}],
        }]},
    })


def _sides(box) -> tuple:
    return (box.x, box.y, box.w, box.h)


def test_a_move_into_a_container_moves_the_first_copy_of_a_repeated_id():
    page = _project(
        [{"id": "g", "type": "group"}, {"id": "a", "type": "button"}, {"id": "a", "type": "label"}],
        [{"id": "landscape", "primary": True, "placements": {
            "g": {"x": 50, "y": 50, "w": 50, "h": 50}, "a": {"x": 60, "y": 60, "w": 10, "h": 10},
        }}],
    ).ui.pages[0]
    reparent_element(page, "a", "g")
    assert [el.parent for el in page.elements] == [None, "g", None]
    assert _sides(page.layouts[0].placements["a"]) == (20, 20, 20, 20)


def test_a_layouts_write_edits_the_first_of_two_arrangements_sharing_an_id():
    page = _project([{"id": "a", "type": "button"}], [
        {"id": "landscape", "primary": True},
        {"id": "tall", "orientation": "portrait", "inherits": "landscape"},
        {"id": "tall", "orientation": "portrait", "inherits": "landscape"},
    ]).ui.pages[0]
    apply_layouts(page, [{"id": "tall", "placements": {"a": {"x": 1, "y": 2, "w": 3, "h": 4}}}])
    assert _sides(page.layouts[1].placements["a"]) == (1, 2, 3, 4)
    assert "a" not in page.layouts[2].placements


def test_a_new_control_may_sit_in_a_container_whose_id_a_later_control_repeats():
    project = _project([{"id": "g", "type": "group"}, {"id": "g", "type": "button"}])
    batch = _NewControls(project, project.ui.pages[0])
    batch.accept([{"id": "kid", "type": "button", "parent": "g"}])
    assert [element.id for element, _ in batch.accepted] == ["kid"]
