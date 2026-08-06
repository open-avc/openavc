"""Where an element actually sits: layout inheritance folded down, nesting flattened.

A page's geometry is not stored where it is drawn. A box lives on one of the
page's ``layouts``, a variant layout stores only what it moved, and a child's
percentages are of its container rather than of the page. Any question of the
form "how big is this, really" has to fold all three down first, and every
surface that asks -- the AI write path, the reviewer behind it, a reparent that
must not move anything on screen -- has to fold them down the same way or they
disagree about the same file.

Duck-typed on purpose: a page here is anything with ``.layouts`` and
``.elements``, so the loader's Pydantic models and a hand-built test page both
work and this module imports nothing.

The panel runtime's ``_selectLayout`` and the Builder's ``layoutChain`` /
``absolutePlacements`` are the other two implementations of this arithmetic.
"""

from __future__ import annotations

from typing import Any

# The whole page, as a box. What a page-level element's percentages are of.
PAGE_BOX = {"x": 0.0, "y": 0.0, "w": 100.0, "h": 100.0}


def primary_layout(page: Any) -> Any:
    """The layout a geometry edit lands in when the caller names no other.

    The loader guarantees a page has exactly one primary, so this always finds
    something; the fallback only exists for a page built by hand in a test.
    """
    for layout in page.layouts:
        if layout.primary:
            return layout
    return page.layouts[0]


def layout_chain(page: Any, layout_id: str) -> list:
    """The layouts feeding a chosen one, base first.

    A variant stores only what moved, so reading its geometry means folding in
    whatever it inherits. The seen-set is a cycle guard: a hand-edited project
    can point two layouts at each other and every reader still has to answer.
    """
    by_id = {lay.id: lay for lay in page.layouts}
    chain: list = []
    seen: set[str] = set()
    cursor = by_id.get(layout_id)
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        chain.insert(0, cursor)
        cursor = by_id.get(cursor.inherits) if cursor.inherits else None
    return chain


def resolved_placements(page: Any, layout_id: str) -> dict:
    """One arrangement's boxes with its inherits chain folded down."""
    placements: dict = {}
    for layout in layout_chain(page, layout_id):
        placements.update(layout.placements)
    return placements


def absolute_placements(page: Any, layout_id: str) -> dict:
    """Every element's box in PAGE percentages, container nesting flattened.

    A child's stored percentages are of its container, so boxes under different
    parents cannot be compared -- 20% wide means two different widths on screen.
    Anything that has to reason about where things actually sit works here and
    converts back on the way out.
    """
    placements = resolved_placements(page, layout_id)
    by_id = {el.id: el for el in page.elements}
    out: dict = {}

    def resolve(el_id: str, seen: frozenset) -> dict | None:
        if el_id in out:
            return out[el_id]
        place = placements.get(el_id)
        if place is None:
            return None
        named = getattr(by_id.get(el_id), "parent", None)
        # A parent that is missing, self-referential or already on the chain is
        # treated as no parent -- a hand-edited project still has to draw.
        parent_id = named if (named and named != el_id and named in by_id and named not in seen) else None
        if not parent_id:
            out[el_id] = {"x": place.x, "y": place.y, "w": place.w, "h": place.h}
            return out[el_id]
        base = resolve(parent_id, seen | {parent_id})
        out[el_id] = {
            "x": base["x"] + (place.x / 100) * base["w"],
            "y": base["y"] + (place.y / 100) * base["h"],
            "w": (place.w / 100) * base["w"],
            "h": (place.h / 100) * base["h"],
        } if base else {"x": place.x, "y": place.y, "w": place.w, "h": place.h}
        return out[el_id]

    for el in page.elements:
        resolve(el.id, frozenset([el.id]))
    return out
