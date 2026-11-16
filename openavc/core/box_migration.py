"""
The project format's 0.17.0 step, its repair of saved boxes: a page's or a
master element's box with a side that is no finite number, and an overlay's or
sidebar's box outside the range the schema took at 0.17.0.

A side that is no finite number (NaN or infinity, as a number or as text the
schema read as one) is removed, so it loads as the default the panel drew for
it: x and y 0, w and h 100. An overlay's ``width`` and ``height`` are brought
to 5..100, the nearer end; one that is no finite number is removed, so it
loads as the panel's default box. A negative side of a box is left as written.

The numbers are the step's own, as they stood at 0.17.0, not read from the
project models: a step says what it did to the projects of its day
(``core/project_migration.py``, whose ``migrate_0_16_to_0_17`` calls this and
says what the whole step does). A list that is missing, null or not the shape
it should be reads as nothing.
"""

import math

# An overlay's or sidebar's width and height, a percentage of the screen.
_OVERLAY_BOX_0_17 = (5, 100)


def repair_boxes_0_17(ui: dict) -> None:
    """Every box and overlay of the project's ``ui``, repaired in place."""
    for box in _boxes_0_17(ui):
        _drop_non_finite_sides_0_17(box)
    for overlay in _overlays_0_17(ui):
        _clamp_overlay_box_0_17(overlay)


def _number_0_17(value) -> float | None:
    """A measurement as the schema read it before this step: a number, true
    or false, or text that reads as one ("NaN" and "1e999" included). None for
    anything it refused, which it still does."""
    if isinstance(value, bool | int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _dicts_0_17(items) -> list[dict]:
    """The objects in a list as written; a missing or null list reads as none."""
    return [item for item in items or [] if isinstance(item, dict)]


def _placements_0_17(holder: dict) -> list[dict]:
    placements = holder.get("placements")
    return _dicts_0_17(placements.values()) if isinstance(placements, dict) else []


def _boxes_0_17(ui: dict) -> list[dict]:
    """Every stored box: each placement of every page arrangement, then each
    of every master element's. A list that is missing, null or not the shape
    it should be reads as nothing."""
    pages = _dicts_0_17(ui.get("pages"))
    holders = [layout for page in pages for layout in _dicts_0_17(page.get("layouts"))]
    holders += _dicts_0_17(ui.get("master_elements"))
    return [box for holder in holders for box in _placements_0_17(holder)]


def _drop_non_finite_sides_0_17(box: dict) -> None:
    """A side that is no finite number goes, so the box loads with the
    default the panel drew for it (x and y 0, w and h 100)."""
    for side in ("x", "y", "w", "h"):
        number = _number_0_17(box.get(side))
        if number is not None and not math.isfinite(number):
            box.pop(side)


def _overlays_0_17(ui: dict) -> list[dict]:
    """Every page's overlay settings, read as the boxes are."""
    pages = _dicts_0_17(ui.get("pages"))
    return [page["overlay"] for page in pages if isinstance(page.get("overlay"), dict)]


def _clamp_overlay_box_0_17(overlay: dict) -> None:
    """A width or height outside 5..100 to the nearer end; one that is no
    finite number goes, so it loads as the default box the panel drew."""
    for side in ("width", "height"):
        number = _number_0_17(overlay.get(side))
        if number is not None:
            _overlay_side_inside_0_17(overlay, side, number)


def _overlay_side_inside_0_17(overlay: dict, side: str, number: float) -> None:
    low, high = _OVERLAY_BOX_0_17
    if not math.isfinite(number):
        overlay.pop(side)
    elif not low <= number <= high:
        overlay[side] = low if number < low else high
