"""Where an element sits, as ``openavc.ui.page_geometry`` folds a page.

A hand-edited or imported project can hold two controls, or two
arrangements, under one id. The project still loads and Validate reports it;
every reader takes the first copy, the one a press reaches
(``core/ui_events.find_element``) and the panel draws, so the AI's write
path, the review and the panel agree about the same file.
"""

from __future__ import annotations

from types import SimpleNamespace

from openavc.ui.page_geometry import absolute_placements, first_by_id, layout_chain, resolved_placements


def _box(x: float, y: float, w: float, h: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, w=w, h=h)


def _layout(layout_id: str, placements: dict, **fields) -> SimpleNamespace:
    return SimpleNamespace(id=layout_id, inherits=fields.get("inherits"), placements=placements)


def _el(element_id: str, parent: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=element_id, parent=parent)


def test_each_id_keeps_the_first_item_holding_it_in_the_order_first_met():
    items = [_el("a", "first"), _el("b"), _el("a", "second")]
    found = first_by_id(items)
    assert list(found) == ["a", "b"]
    assert found["a"] is items[0]
    assert first_by_id([{"id": "x", "n": 1}, {"id": "x", "n": 2}], lambda item: item["id"])["x"]["n"] == 1


def test_an_inherits_naming_a_repeated_arrangement_follows_the_first():
    page = SimpleNamespace(elements=[], layouts=[
        _layout("base", {"e1": _box(1, 1, 10, 10)}),
        _layout("base", {"e1": _box(2, 2, 20, 20)}),
        _layout("top", {}, inherits="base"),
    ])
    assert [lay is page.layouts[0] for lay in layout_chain(page, "top")] == [True, False]
    assert vars(resolved_placements(page, "top")["e1"]) == vars(_box(1, 1, 10, 10))
    assert vars(resolved_placements(page, "base")["e1"]) == vars(_box(1, 1, 10, 10))


def test_a_repeated_control_is_measured_in_the_container_of_its_first_copy():
    page = SimpleNamespace(
        elements=[_el("g"), _el("a", parent="g"), _el("a")],
        layouts=[_layout("main", {"g": _box(50, 50, 50, 50), "a": _box(10, 10, 10, 10)})],
    )
    assert absolute_placements(page, "main")["a"] == {"x": 55.0, "y": 55.0, "w": 5.0, "h": 5.0}


def test_a_container_named_by_a_repeated_id_is_its_first_copy():
    page = SimpleNamespace(
        elements=[_el("g"), _el("g", parent="h"), _el("h"), _el("kid", parent="g")],
        layouts=[_layout("main", {
            "g": _box(50, 50, 50, 50), "h": _box(0, 0, 10, 10), "kid": _box(0, 0, 50, 50),
        })],
    )
    assert absolute_placements(page, "main")["kid"] == {"x": 50.0, "y": 50.0, "w": 25.0, "h": 25.0}
