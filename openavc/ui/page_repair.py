"""THE repair every load makes to a page's containers.

A saved project can hold a container link the panel draws wrongly or not at
all. Each such link has one plain repair that keeps what the panel draws, or
brings back what it drew nothing of, made in this order, each on what the one
before left:

- **A container that names nothing** (a ``parent`` naming no control on the
  page, a blank, anything that is not text) is dropped. The panel already draws
  such a control on the page (``drawElementTree`` in the panel's
  ``modules/layout.js``), so nothing on screen moves and nothing is said.
- **A loop of containers** (a control naming itself, or controls each inside
  the next until one names the first): the panel draws none of them, nor
  anything inside them. Each control on the loop moves to the page with the
  numbers it has, so it appears; what it holds stays in it. Each is said.
- **A control inside one that is not a container** (a child of a button): the
  panel draws it inside that control, its box a share of it. It moves to the
  nearest container above it, or to the page, as a delete moves what a
  container held, with its box rewritten in each arrangement to where it was
  drawn (``page_geometry.absolute_placements``), hidden in each arrangement
  that hid a control it was drawn inside, and listed straight after the
  control it was drawn under, so it draws over what it drew over. Where the
  container has no room to measure against, it goes to the page. It no longer
  hides when a control it was drawn inside hides by its ``visible_when``,
  which the note says. Each is said.
- **A master element's container**: a master is placed on the screen and the
  panel never reads its ``parent``, so it is dropped and nothing is said.

A second run finds nothing. Every load runs it, whatever the project's version
(``core/saved_values.repair_saved_values``); a save does not, and an edit that
adds one of these is refused (``page_edits.issues_an_edit_makes``). What it
leaves (a repeated id, an arrangement, a binding) Validate reports in place.
Reads any JSON without raising: only a mapping with text for an id takes part.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openavc.core.project_update import MigrationNotes
from openavc.ui.page_geometry import absolute_placements
from openavc.ui.page_structure import CONTAINER_TYPE

#: The sides of a box, and what one left out reads as (``project_loader.Placement``).
_SIDES = {"x": 0.0, "y": 0.0, "w": 100.0, "h": 100.0}

#: A box is stored to this many places, as the UI Builder stores one.
_PLACES = 4


def repair_page_structure(ui: dict, notes: MigrationNotes) -> bool:
    """``ui`` (a project's ``ui`` section) repaired in place, each repair a
    person would see said in ``notes``. True when anything was rewritten, so
    the load saves the file."""
    rewrote = False
    for page in _dicts(ui.get("pages")):
        rewrote = _Page(page, notes).repair() or rewrote
    for master in _dicts(ui.get("master_elements")):
        if master.get("parent") is not None:
            master["parent"] = None
            rewrote = True
    return rewrote


def _dicts(items: Any) -> list[dict]:
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


@dataclass
class _Moved:
    """A control leaving a control that is not a container: every control it
    is drawn inside (``chain``, innermost first) and the container it moves
    into (``into``, None for the page)."""

    element: dict
    chain: list[str]
    into: str | None

    @property
    def within(self) -> list[str]:
        """The controls it was drawn inside that it leaves, innermost first."""
        return self.chain[:self.chain.index(self.into)] if self.into in self.chain else self.chain

    @property
    def after(self) -> str:
        """The control it is listed after: the outermost one it leaves."""
        return self.within[-1]


@dataclass
class _Page:
    page: dict
    notes: MigrationNotes
    elements: list[dict] = field(init=False)

    def __post_init__(self) -> None:
        self.elements = [el for el in _dicts(self.page.get("elements")) if _text(el.get("id"))]

    def repair(self) -> bool:
        dropped = self._drop_containers_naming_nothing()
        looped = self._break_loops()
        moved = self._move_out_of_controls()
        return dropped or looped or moved

    # --- names ----------------------------------------------------------------

    def _by_id(self) -> dict[str, dict]:
        """Each id's control: the first holding it, as a press finds one."""
        by_id: dict[str, dict] = {}
        for el in self.elements:
            by_id.setdefault(el["id"], el)
        return by_id

    def _said(self, element_id: str, message: str) -> None:
        page_id = str(self.page.get("id", ""))
        name = str(self.page.get("name") or page_id)
        self.notes.changed(message, f"{name} > {element_id}", page_id=page_id, element_id=element_id)

    # --- a container naming nothing -----------------------------------------------

    def _drop_containers_naming_nothing(self) -> bool:
        ids = {el["id"] for el in self.elements}
        dropped = False
        for el in self.elements:
            parent = el.get("parent")
            if parent is not None and not _names_one_of(parent, ids):
                el["parent"] = None
                dropped = True
        return dropped

    # --- a loop ---------------------------------------------------------------------

    def _drawn(self) -> set[str]:
        """The ids the panel draws: those on the page, and inside them."""
        inside: dict[Any, list[str]] = {}
        for el in self.elements:
            inside.setdefault(el.get("parent"), []).append(el["id"])
        drawn: set[str] = set()
        waiting = list(inside.get(None, []))
        while waiting:
            element_id = waiting.pop()
            if element_id not in drawn:
                drawn.add(element_id)
                waiting.extend(inside.get(element_id, []))
        return drawn

    def _break_loops(self) -> bool:
        broke = False
        while True:
            drawn = self._drawn()
            stranded = next((el for el in self.elements if el["id"] not in drawn), None)
            if stranded is None:
                return broke
            loop = self._loop_above(stranded)
            on_loop = [el for el in self.elements if el["id"] in loop and el.get("parent") in loop]
            for el in on_loop or [stranded]:
                el["parent"] = None
                self._said(el["id"], (
                    f"Control '{el['id']}' sat inside a loop of containers, so the panel drew "
                    f"nothing of it. It is now on the page."
                ))
            broke = True

    def _loop_above(self, el: dict) -> set[str]:
        """The loop the containers above ``el`` end in. Every control above
        one the panel does not draw is undrawn too, so the walk ends in one."""
        by_id = self._by_id()
        path: list[str] = []
        cursor: str | None = el["id"]
        while cursor is not None and cursor not in path:
            path.append(cursor)
            cursor = by_id[cursor].get("parent")
        return set(path[path.index(cursor):]) if cursor is not None else {el["id"]}

    # --- a control inside one that is not a container --------------------------------

    def _move_out_of_controls(self) -> bool:
        by_id = self._by_id()
        moving = [self._moving(el, by_id) for el in self._in_draw_order()]
        moving = [move for move in moving if move is not None]
        if not moving:
            return False
        self._rewrite_boxes(moving)
        for move in moving:
            move.element["parent"] = move.into
            self._said(move.element["id"], _moved_sentence(move, by_id))
        self._list_after(moving)
        return True

    def _rewrite_boxes(self, moving: list[_Moved]) -> None:
        """Each arrangement's boxes for the controls moving, measured on the
        page as it is before they move."""
        view = _geometry_view(self.page, self.elements)
        drawn = {layout.id: absolute_placements(view, layout.id) for layout in view.layouts}
        for move in moving:
            _settle(move, drawn.values())
        for layout in _dicts(self.page.get("layouts")):
            if _text(layout.get("id")) and layout["id"] in drawn:
                _rewrite_layout(layout, drawn[layout["id"]], moving)

    def _in_draw_order(self) -> list[dict]:
        """Every control, each straight after the one it is drawn inside, and
        once: a control inside one that shares its id is reached through it."""
        inside: dict[Any, list[dict]] = {}
        for el in self.elements:
            inside.setdefault(el.get("parent"), []).append(el)
        order: list[dict] = []
        reached: set[int] = set()
        waiting = list(reversed(inside.get(None, [])))
        while waiting:
            el = waiting.pop()
            if id(el) not in reached:
                reached.add(id(el))
                order.append(el)
                waiting.extend(reversed(inside.get(el["id"], [])))
        return order

    def _moving(self, el: dict, by_id: dict[str, dict]) -> _Moved | None:
        holder = by_id.get(el.get("parent") or "")
        if holder is None or holder.get("type") == CONTAINER_TYPE:
            return None
        chain = [holder["id"]]
        above = holder.get("parent")
        while above is not None and above not in chain:
            chain.append(above)
            above = by_id[above].get("parent")
        into = next((i for i in chain[1:] if by_id[i].get("type") == CONTAINER_TYPE), None)
        return _Moved(el, chain, into)

    def _list_after(self, moving: list[_Moved]) -> None:
        """Each moved control straight after the control it was drawn under,
        in the order the panel drew them."""
        following: dict[str, list[dict]] = {}
        for move in moving:
            following.setdefault(move.after, []).append(move.element)
        taken = {id(move.element) for move in moving}
        listed: list[Any] = []
        for item in self.page["elements"]:
            if id(item) in taken:
                continue
            listed.append(item)
            if isinstance(item, dict) and _text(item.get("id")):
                listed.extend(following.pop(item["id"], []))
        for rest in following.values():
            listed.extend(rest)
        self.page["elements"] = listed


def _moved_sentence(move: _Moved, by_id: dict[str, dict]) -> str:
    holder = by_id[move.within[0]]
    where = "on the page" if move.into is None else f"in container '{move.into}'"
    said = (
        f"Control '{move.element['id']}' sat inside '{holder['id']}', a {holder.get('type')}, which "
        f"cannot hold controls. It is now {where}, where it was drawn."
    )
    shown_by = next((i for i in move.within if _shown_when(by_id[i])), None)
    return said + (f" It no longer hides when '{shown_by}' does." if shown_by else "")


def _shown_when(el: dict) -> bool:
    bindings = el.get("bindings")
    show = bindings.get("show") if isinstance(bindings, dict) else None
    return isinstance(show, dict) and bool(show.get("visible_when"))


# --- the arrangements ------------------------------------------------------------


def _rewrite_layout(layout: dict, drawn: dict, moving: Iterable[_Moved]) -> None:
    """Each moved control's box in ``layout``, in what it moves into, where
    this arrangement places it or a control it was drawn inside (anywhere
    else it reads its box from the arrangement it inherits, which this
    rewrites the same way), and hidden where one it was drawn inside is."""
    placements, hidden = layout.get("placements"), layout.get("hidden")
    for move in moving:
        if isinstance(placements, dict):
            _place(placements, move, drawn)
        if isinstance(hidden, list):
            _hide(hidden, move)


def _place(placements: dict, move: _Moved, drawn: dict) -> None:
    element_id = move.element["id"]
    box = drawn.get(element_id)
    if box is None or not any(i in placements for i in (element_id, *move.within)):
        return
    placements[element_id] = {**_dict(placements.get(element_id)), **_measured(move, box, drawn)}


def _hide(hidden: list, move: _Moved) -> None:
    element_id = move.element["id"]
    if element_id not in hidden and any(i in hidden for i in move.within):
        hidden.append(element_id)


def _settle(move: _Moved, drawn: Iterable[dict]) -> None:
    """The page in place of the container, when an arrangement draws the
    control where the container has no room to measure it against."""
    if move.into is None:
        return
    element_id = move.element["id"]
    if any(element_id in boxes and not _has_room(boxes.get(move.into)) for boxes in drawn):
        move.into = None


def _has_room(box: dict | None) -> bool:
    return box is not None and box["w"] > 0 and box["h"] > 0


def _measured(move: _Moved, box: dict, drawn: dict) -> dict:
    """``box`` (in page space) as a share of the container the control moves
    into, or as it is for the page."""
    if move.into is None:
        return _rounded(box)
    into = drawn[move.into]
    return _rounded({
        "x": (box["x"] - into["x"]) / into["w"] * 100,
        "y": (box["y"] - into["y"]) / into["h"] * 100,
        "w": box["w"] / into["w"] * 100,
        "h": box["h"] / into["h"] * 100,
    })


def _rounded(box: dict) -> dict:
    return {side: round(box[side], _PLACES) for side in _SIDES}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _geometry_view(page: dict, elements: list[dict]) -> SimpleNamespace:
    """The page as ``page_geometry`` reads one, from whatever JSON it holds:
    an arrangement with text for an id, a box that is an object, a side that
    is a finite number (else what one left out reads as)."""
    layouts = [
        SimpleNamespace(
            id=layout["id"],
            inherits=layout.get("inherits") if _text(layout.get("inherits")) else None,
            placements={key: _box(box) for key, box in _dict(layout.get("placements")).items()
                        if isinstance(box, dict)},
        )
        for layout in _first_of_each_id(_dicts(page.get("layouts")))
    ]
    controls = [SimpleNamespace(id=el["id"], parent=el.get("parent")) for el in elements]
    return SimpleNamespace(layouts=layouts, elements=controls)


def _first_of_each_id(layouts: list[dict]) -> list[dict]:
    """Each arrangement with text for an id, the first of an id the panel
    draws; one after it with the same id is drawn from it."""
    first: dict[str, dict] = {}
    for layout in layouts:
        if _text(layout.get("id")):
            first.setdefault(layout["id"], layout)
    return list(first.values())


def _box(box: dict) -> SimpleNamespace:
    return SimpleNamespace(**{side: _side(box.get(side), left_out) for side, left_out in _SIDES.items()})


def _side(value: Any, left_out: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return left_out
    return float(value) if math.isfinite(value) else left_out


def _names_one_of(parent: Any, ids: set[str]) -> bool:
    return _text(parent) and parent in ids


def _text(value: Any) -> bool:
    return isinstance(value, str) and value != ""
