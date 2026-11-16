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

Each side removed and each overlay side moved is a note for the Programmer's
notice, and so is a negative width or height, which the step leaves for
Validate to report.
"""

import math
from dataclasses import dataclass

from openavc.core.project_update import MigrationNotes

# An overlay's or sidebar's width and height, a percentage of the screen.
OVERLAY_BOX_0_17 = (5, 100)

# Each side of a box as the UI Builder names it, and what the panel drew for
# one that is missing.
_SIDES_0_17 = {"x": ("X", 0), "y": ("Y", 0), "w": ("Width", 100), "h": ("Height", 100)}


def repair_boxes_0_17(
    ui: dict, notes: MigrationNotes, overlay_box: tuple[float, float] = OVERLAY_BOX_0_17,
) -> None:
    """Every box and overlay of the project's ``ui``, repaired in place;
    ``overlay_box`` is the range an overlay's sides are brought to (the
    repair every load makes passes today's, ``core/saved_values.py``)."""
    for place in _boxes_0_17(ui):
        _drop_non_finite_sides_0_17(place, notes)
    for page in _overlay_pages_0_17(ui):
        _clamp_overlay_box_0_17(page, notes, overlay_box)


def element_places_0_17(ui) -> dict[int, tuple[str, str]]:
    """Where each page and master element is, for a note: ``id(element)`` to
    the start of its location (the page's name, or "Master Elements") and the
    page's id ("" for a master)."""
    if not isinstance(ui, dict):
        return {}
    places: dict[int, tuple[str, str]] = {}
    for page in _dicts_0_17(ui.get("pages")):
        for el in _dicts_0_17(page.get("elements")):
            places[id(el)] = _page_place_0_17(page)
    for mel in _dicts_0_17(ui.get("master_elements")):
        places[id(mel)] = ("Master Elements", "")
    return places


def _page_place_0_17(page: dict) -> tuple[str, str]:
    page_id = str(page.get("id", ""))
    return str(page.get("name") or page_id), page_id


def say_number_0_17(number: float) -> str:
    """A number in a note as a person typed it: 5, not 5.0."""
    return str(int(number)) if number.is_integer() else str(number)


def number_0_17(value) -> float | None:
    """A measurement as the schema read it before this step: a number, true
    or false, or text that reads as one ("NaN" and "1e999" included). None for
    anything it refused, which it still does. The step's repair of a
    trigger's times reads them the same way (``core/bound_migration.py``)."""
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


@dataclass
class _Box:
    """One stored box and where it is: the layout it belongs to and the
    control it places (``at``: its page id and element id, for a note)."""

    box: dict
    layout_id: str
    location: str
    at: dict


def _boxes_0_17(ui: dict) -> list[_Box]:
    """Every stored box: each placement of every page arrangement, then each
    of every master element's. A list that is missing, null or not the shape
    it should be reads as nothing."""
    boxes = [box for page in _dicts_0_17(ui.get("pages")) for box in _page_boxes_0_17(page)]
    return boxes + [box for mel in _dicts_0_17(ui.get("master_elements")) for box in _master_boxes_0_17(mel)]


def _page_boxes_0_17(page: dict) -> list[_Box]:
    name, page_id = _page_place_0_17(page)
    return [
        _Box(box, str(layout.get("id", "")), f"{name} > {element_id}",
             {"page_id": page_id, "element_id": element_id})
        for layout in _dicts_0_17(page.get("layouts"))
        for element_id, box in _placements_0_17(layout)
    ]


def _master_boxes_0_17(mel: dict) -> list[_Box]:
    element_id = str(mel.get("id", ""))
    return [
        _Box(box, layout_id, f"Master Elements > {element_id}", {"page_id": "", "element_id": element_id})
        for layout_id, box in _placements_0_17(mel)
    ]


def _placements_0_17(holder: dict) -> list[tuple[str, dict]]:
    placements = holder.get("placements")
    if not isinstance(placements, dict):
        return []
    return [(str(key), box) for key, box in placements.items() if isinstance(box, dict)]


def _drop_non_finite_sides_0_17(place: _Box, notes: MigrationNotes) -> None:
    """A side that is no finite number goes, so the box loads with the
    default the panel drew for it (x and y 0, w and h 100). A negative width
    or height stays, and is noted for Validate."""
    for side, (name, default) in _SIDES_0_17.items():
        number = number_0_17(place.box.get(side))
        if number is None:
            continue
        if not math.isfinite(number):
            place.box.pop(side)
            notes.changed(
                f'{name} in layout "{place.layout_id}" was not a number, so it is now {default}%.',
                place.location, **place.at,
            )
        elif side in ("w", "h") and number < 0:
            notes.found(
                f'{name} in layout "{place.layout_id}" is {say_number_0_17(number)}%, so the control does not draw.',
                place.location, **place.at,
            )


def _overlay_pages_0_17(ui: dict) -> list[dict]:
    """Every page with overlay settings, read as the boxes are."""
    return [page for page in _dicts_0_17(ui.get("pages")) if isinstance(page.get("overlay"), dict)]


def _clamp_overlay_box_0_17(page: dict, notes: MigrationNotes, box: tuple[float, float]) -> None:
    """A width or height outside ``box`` (5..100 at 0.17.0) to the nearer end;
    one that is no finite number goes, so it loads as the default box the
    panel drew."""
    name, page_id = _page_place_0_17(page)
    for side in ("width", "height"):
        number = number_0_17(page["overlay"].get(side))
        if number is None:
            continue
        said = _overlay_side_inside_0_17(page["overlay"], side, number, box)
        if said:
            notes.changed(said, f"{name} > page", page_id=page_id)


def _overlay_side_inside_0_17(overlay: dict, side: str, number: float, box: tuple[float, float]) -> str:
    """The side brought inside the box; what changed, or "" for nothing."""
    label = side.capitalize()
    low, high = box
    if not math.isfinite(number):
        overlay.pop(side)
        return f"{label} was not a number, so the page opens at its default size."
    if low <= number <= high:
        return ""
    overlay[side] = low if number < low else high
    return f"{label} changed from {say_number_0_17(number)}% to {overlay[side]}%, the range the UI Builder offers."
