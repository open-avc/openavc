"""Where things are on a page, as the AI's UI tools write it.

A control's box does not live on the control. It lives in the page's
arrangements (layouts): the primary, which an unmatched screen falls back to
and every variant inherits from, and the variants, which store only what moved.
A child's box is a percentage of its container's. This module is the writing
half the doors in ``ui_tools`` share: the arrangements a layouts write adds and
edits, a box stored the way the Builder stores it, the containers a control may
sit in and the conversion that keeps it in place when it moves between them,
the hide each arrangement adds, and what a deleted control leaves behind; and
a master's boxes, which are the viewport's by orientation rather than any
page's. Reading the geometry is ``ui/page_geometry.py``'s.

The rules are the Builder's, so an AI edit and the same edit made on the canvas
store the same project: the rounding is ``GEOMETRY_PRECISION`` in
``pageGeometry.ts``; the move between containers is ``reparentPlacements`` in
``containerTree.ts``. What a page may hold (an arrangement's ``inherits`` and
orientation, who may hold whom) is ``openavc.ui.page_structure``'s, which each
write asks before it changes anything: a project the Builder would refuse to
create is one its editors cannot repair, so these refuse it at the door.
"""

from typing import Any

from openavc.cloud.tools import ToolEditError, merge_forward_compat
from openavc.ui.page_geometry import (
    PAGE_BOX,
    absolute_placements,
    first_by_id,
    layout_chain,
    primary_layout,
    resolved_placements,
)
from openavc.ui.page_structure import (
    Issue,
    inherits_issue,
    inherits_loops,
    layout_issues,
    orientation_issue,
    parent_issue,
    refusal,
)


def rounded_placement(box: Any) -> Any:
    """A Placement quantized to 4 decimal places, exactly as the Builder stores.

    Percentages are stored to 4dp at every write path (GEOMETRY_PRECISION in
    pageGeometry.ts); an unrounded value is the float jitter that dirties
    the save-reconcile diff and arms phantom autosaves. The Builder quantizes
    with JS Math.round, which rounds a half toward +infinity, while Python's
    round() is banker's -- so mirror the JS arithmetic, or the same reparent
    stores different bytes depending on who performed it.

    A side that is no finite number is left for the model to refuse, as it
    refuses one sent as text, naming the side.
    """
    from openavc.core.project_loader import Placement

    data = box.model_dump() if hasattr(box, "model_dump") else dict(box)
    for key in ("x", "y", "w", "h"):
        if key in data:
            data[key] = _quantized(data[key])
    return Placement(**data)


def _quantized(value: Any) -> Any:
    """A finite number to 4 decimal places, a half rounded up as JS Math.round
    does; anything else as it came, for the model to judge."""
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return math.floor(value * 10_000 + 0.5) / 10_000 if math.isfinite(value) else value


def resolve_layout(page: Any, layout_id: Any) -> Any:
    """The named arrangement, or the primary when none is named."""
    if not layout_id:
        return primary_layout(page)
    for layout in page.layouts:
        if layout.id == layout_id:
            return layout
    raise ToolEditError({
        "error": f"Layout '{layout_id}' not found on page '{page.id}'. "
                 f"Available: {', '.join(lay.id for lay in page.layouts)}"
    })


def apply_layouts(page: Any, layouts_input: Any) -> None:
    """Add or edit a page's arrangements.

    An entry naming an existing layout edits it; anything else is a new
    arrangement. Placements merge (a variant is deltas -- sending one moved
    control must not blank the rest) while `hidden` replaces, because it is a
    set and "hide exactly these" is the only unambiguous reading of a list.
    """
    from openavc.core.project_loader import normalize_primary_layout

    if not isinstance(layouts_input, list):
        raise ToolEditError({"error": "'layouts' must be an array of layout objects"})
    held = {issue.message for issue in inherits_loops(page.layouts)}
    edit = _LayoutEdit(page)
    for entry in layouts_input:
        edit.apply(entry)
    # Two edits in one call can each be legal and still tie the chains in a
    # loop (A inherits B, then B inherits A), so every chain is walked once
    # the call's edits are in. A loop the page already held is not this
    # write's to answer for.
    made = (issue for issue in inherits_loops(page.layouts) if issue.message not in held)
    _refuse(next(made, None))
    page.layouts = normalize_primary_layout(page.layouts)


def refuse_new_page_layouts(page: Any) -> None:
    """A new page's arrangements, refused for what the update door refuses
    (``layout_issues``), every problem in one reply."""
    _refuse_all(layout_issues(page))


def _refuse(issue: Issue | None, advice: str = "") -> None:
    if issue is not None:
        raise ToolEditError({"error": issue.message + advice})


def _refuse_all(issues: list[Issue]) -> None:
    if issues:
        raise ToolEditError({"error": refusal(issues)})


def _entry_boxes(entry: Any) -> tuple[str, dict]:
    """A layout entry's id and its boxes, each rounded as the Builder stores it."""
    if not isinstance(entry, dict) or not entry.get("id"):
        raise ToolEditError({"error": "Each layout needs an 'id'"})
    layout_id = entry["id"]
    placements = entry.get("placements") or {}
    if not isinstance(placements, dict):
        raise ToolEditError({
            "error": f"Layout '{layout_id}': 'placements' must be an object keyed by element id"
        })
    return layout_id, {el_id: rounded_placement(box) for el_id, box in placements.items()}


class _LayoutEdit:
    """One layouts write on one page: the arrangements it adds and edits, by id.

    ``by_id`` takes each new arrangement as it is added, so a later entry in
    the same write may inherit from it; ``primary_id`` is the primary the write
    started with.
    """

    def __init__(self, page: Any) -> None:
        self.page = page
        self.by_id = first_by_id(page.layouts)
        self.primary_id = primary_layout(page).id

    def apply(self, entry: Any) -> None:
        layout_id, boxes = _entry_boxes(entry)
        self._refuse_locked_boxes(boxes)
        existing = self.by_id.get(layout_id)
        if existing is None:
            self._add(layout_id, entry, boxes)
        else:
            self._edit(layout_id, existing, entry, boxes)

    def _add(self, layout_id: str, entry: dict, boxes: dict) -> None:
        """A new arrangement, always a variant.

        The primary is the page's fallback for an unmatched screen and the
        layout every variant inherits from. Handing that role to a new
        arrangement is a bigger change than "add a portrait version".
        """
        from openavc.core.project_loader import Layout

        fields = {k: v for k, v in entry.items() if k not in ("placements", "hidden", "primary")}
        fields.setdefault("inherits", self.primary_id)
        self._check_inherits(layout_id, fields.get("inherits"))
        new_layout = Layout(**fields, placements=boxes, hidden=entry.get("hidden") or [])
        self._check_orientation(layout_id, new_layout.orientation)
        new_layout.primary = False
        self.page.layouts.append(new_layout)
        self.by_id[layout_id] = new_layout

    def _edit(self, layout_id: str, existing: Any, entry: dict, boxes: dict) -> None:
        self._refuse_primary_change(layout_id, existing, entry)
        if "inherits" in entry:
            self._check_inherits(layout_id, entry["inherits"])
        if "orientation" in entry:
            self._check_orientation(layout_id, entry["orientation"])
        for field in ("orientation", "inherits"):
            if field in entry:
                setattr(existing, field, entry[field])
        existing.placements.update(boxes)
        if "hidden" in entry:
            existing.hidden = list(entry["hidden"] or [])

    def _refuse_locked_boxes(self, boxes: dict) -> None:
        """A box for a locked control is refused here as the element door
        refuses one (``_refuse_if_locked`` in ``ui_tools``), in its words, and
        as the canvas refuses a drag: the lock is the integrator's pin, and a
        layouts write is just another way to move the control. Every locked
        control the entry names is named. A hide does not move a control, so
        ``hidden`` is not asked."""
        locked = [
            el.id for el in self.page.elements
            if el.id in boxes and getattr(el, "locked", False)
        ]
        if not locked:
            return
        if len(locked) == 1:
            subject, verb, box, them = f"Element '{locked[0]}'", "is", "its placement", "it"
        else:
            names = ", ".join(f"'{i}'" for i in locked[:-1]) + f" and '{locked[-1]}'"
            subject, verb, box, them = f"Elements {names}", "are", "their placements", "them"
        raise ToolEditError({
            "error": f"{subject} {verb} locked, so {box} cannot be changed. "
                     f"Send locked: false first if you meant to move {them}."
        })

    def _refuse_primary_change(self, layout_id: str, existing: Any, entry: dict) -> None:
        if entry.get("primary") is not None and bool(entry["primary"]) != existing.primary:
            raise ToolEditError({
                "error": f"Layout '{layout_id}': which layout is primary cannot be changed here. "
                         f"'{self.primary_id}' is the arrangement an unmatched screen falls back to."
            })

    def _check_inherits(self, layout_id: str, target: Any) -> None:
        """Against the arrangements so far, a new one added earlier in the
        same call among them."""
        _refuse(inherits_issue(layout_id, target, self.by_id))

    def _check_orientation(self, layout_id: str, orientation: Any) -> None:
        _refuse(orientation_issue(layout_id, orientation, self.page.layouts),
                " Edit that one instead.")


def reparent_element(page: Any, element_id: str, new_parent_id: str | None) -> None:
    """Move an element into a container (or back out) without moving it on screen.

    A child's percentages are of its container, so changing the parent and
    nothing else teleports it -- 20% of a quarter-page box is not 20% of the
    page. The box is converted out to page space against the old parent and back
    in against the new one, in **every** arrangement the page carries, so the
    layout the caller is not looking at does not shift either. This is the same
    conversion the Builder does when you drag a control into a container.
    Nothing moves when the parent is already the one named.
    """
    holders = first_by_id(page.elements)
    element = holders.get(element_id)
    if element is None:
        return
    if (element.parent or None) == new_parent_id:
        return
    _refuse(parent_issue(
        element_id, new_parent_id, holders, page.id,
    ))

    rewritten = [
        (layout, box)
        for layout in page.layouts
        if (box := _converted_box(page, layout, element_id, new_parent_id))
    ]
    element.parent = new_parent_id
    for layout, box in rewritten:
        layout.placements[element_id] = rounded_placement(box)


def _converted_box(page: Any, layout: Any, element_id: str, new_parent_id: str | None) -> dict | None:
    """The element's box in one arrangement, in the new parent's percentages.

    None where there is nothing to convert: a new parent with no area there, a
    variant that inherits the element's box rather than storing its own (an
    entry invented there would pin a box that was happily inheriting from the
    primary), and an element with no box at all.
    """
    absolute = absolute_placements(page, layout.id)
    base = absolute.get(new_parent_id) if new_parent_id else dict(PAGE_BOX)
    if _no_area(base):
        return None
    if layout.id != primary_layout(page).id and element_id not in layout.placements:
        return None
    box = absolute.get(element_id)
    if box is None:
        return None
    return {
        "x": (box["x"] - base["x"]) / base["w"] * 100,
        "y": (box["y"] - base["y"]) / base["h"] * 100,
        "w": box["w"] / base["w"] * 100,
        "h": box["h"] / base["h"] * 100,
    }


def _no_area(box: Any) -> bool:
    """Missing, or a side of zero or less. A side that is not a number passes,
    and the model then refuses the box built from it."""
    return not box or box["w"] <= 0 or box["h"] <= 0


def restate_box(page: Any, layout: Any, element_id: str, partial: dict) -> None:
    """A control's box as an update states it, in one arrangement.

    Partial: the fields it names go over the box the control resolves to there
    (its own, or the one it inherits), so an omitted x or y is not snapped back
    to 0, and forward-compat keys survive. Stored in that arrangement only.
    """
    from openavc.core.project_loader import Placement

    existing = resolved_placements(page, layout.id).get(element_id, Placement())
    layout.placements[element_id] = rounded_placement(
        merge_forward_compat(existing, Placement, partial),
    )


def master_boxes(boxes: dict) -> dict:
    """A master's boxes by orientation (the viewport's, not any page's), each
    object one stored as the Builder stores it."""
    return {
        orientation: rounded_placement(box) if isinstance(box, dict) else box
        for orientation, box in boxes.items()
    }


def merge_master_boxes(master: Any, boxes: Any, what: str) -> None:
    """A master update's boxes, merged per orientation: a call that moves the
    landscape box must not blank a portrait one it said nothing about."""
    if not isinstance(boxes, dict):
        raise ToolEditError({
            "error": f"{what}: 'placements' must be an object keyed by orientation"
        })
    master.placements = {**(master.placements or {}), **master_boxes(boxes)}


def place_in_primary(page: Any, boxes: dict) -> None:
    """New controls' boxes, by id, stored in the page's primary arrangement.

    A new control exists in every arrangement the moment it exists at all, so
    its box goes in the primary, the one every other layout inherits from:
    written into a variant it would have no box anywhere else.
    """
    primary = primary_layout(page)
    for el_id, box in boxes.items():
        primary.placements[el_id] = rounded_placement(box)


def set_hidden(page: Any, layout: Any, element_id: str, hide: bool) -> None:
    """Hide a control in one arrangement, or show it there again.

    Hiding is per arrangement: a portrait variant can drop a wide banner that
    will not fit without touching the landscape panel. The runtime unions
    ``hidden`` down the inherits chain, so a variant can add a hide but never
    take back an inherited one; an unhide that would change nothing is refused,
    naming the layout the hide comes from.
    """
    if not hide:
        _refuse_inherited_hide(page, layout, element_id)
        layout.hidden = [i for i in layout.hidden if i != element_id]
    elif element_id not in layout.hidden:
        layout.hidden.append(element_id)


def _refuse_inherited_hide(page: Any, layout: Any, element_id: str) -> None:
    hider = next(
        (ancestor.id for ancestor in layout_chain(page, layout.id)
         if ancestor.id != layout.id and element_id in ancestor.hidden),
        None,
    )
    if hider is not None:
        raise ToolEditError({
            "error": f"Element '{element_id}' is hidden by layout "
                     f"'{hider}', which '{layout.id}' inherits from. "
                     f"Unhide it there, or the whole chain keeps it hidden."
        })


def remove_elements(page: Any, ids: set) -> set:
    """Delete the controls in ``ids`` from one page; the ids it held come back.

    Anything left inside a container that is going away is moved first to its
    nearest surviving container, or to the page, while the tree is still intact
    enough to measure -- the same conversion a reparent does, so nothing jumps
    when its container disappears. Every arrangement then forgets the deleted
    controls' boxes and hides, which would otherwise stay behind.
    """
    doomed = ids & {el.id for el in page.elements}
    if doomed:
        _rehome_children(page, doomed)
        page.elements = [el for el in page.elements if el.id not in ids]
        _forget_in_layouts(page, doomed)
    return doomed


def _rehome_children(page: Any, doomed: set) -> None:
    """Each surviving child of a doomed container, moved to the nearest survivor."""
    by_id = first_by_id(page.elements)
    for el in page.elements:
        if el.id in doomed or not el.parent:
            continue
        if el.parent in doomed:
            reparent_element(page, el.id, _surviving_container(by_id, el.parent, doomed))


def _forget_in_layouts(page: Any, doomed: set) -> None:
    for layout in page.layouts:
        for el_id in doomed:
            layout.placements.pop(el_id, None)
        layout.hidden = [i for i in layout.hidden if i not in doomed]


def _surviving_container(by_id: dict, parent_id: str, doomed: set) -> str | None:
    """The nearest container above ``parent_id`` that is not being deleted, or
    None for the page. Cycle-safe."""
    survivor = parent_id
    seen: set[str] = set()
    while survivor in doomed and survivor not in seen:
        seen.add(survivor)
        survivor = getattr(by_id.get(survivor), "parent", None)
    return survivor if survivor not in doomed else None
