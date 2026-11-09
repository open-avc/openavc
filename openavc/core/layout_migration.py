"""
Project format 0.7.0 -> 0.8.0: a page's grid becomes percentage layouts.

A 0.7.0 page laid its elements out on a CSS grid of cells, and a group was a
frame painted behind elements that were really its peers. 0.8.0 places each
element as a percentage rect of its parent box, and a group is a real
container. `convert_grid_layout` rewrites a 0.7.0 ``ui`` section into that
shape so a panel looks identical at the 1280x800 reference screen it was
implicitly designed against: each cell becomes the rect CSS Grid actually drew
there (`_Grid`), a group adopts what it fully contains, overlay and sidebar
boxes become viewport percentages, px style measurements become rem, spacers
go, and the project's orientation moves onto each page's one layout. Masters,
which have no grid of their own, are converted against the first page they
target. ``core/project_migration.py`` runs it as part of the 0.8.0 step.

The rule worth knowing before changing anything here: this is a frozen step.
It says what 0.8.0 did to the projects of its day, so every number in it is the
0.7.0 panel's (the 14 px base, the 8 px gap, the overlay and sidebar sizes),
never read from today's renderer; ``ui/control_minimums.py`` keeps its own
reference size and rem base for the live panel, and the two are not joined.
Its two log lines carry the migration chain's logger name.
"""

from typing import NamedTuple

from openavc.utils.logger import get_logger

log = get_logger("openavc.core.project_migration")

# The reference screen the old grid was implicitly designed against. Converting
# against it makes a migrated panel pixel-identical here, and proportionally
# identical on any screen of the same shape -- which the grid never was.
REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 800

# Old base font size. The 0.8.0 root scale (1.75vmin) resolves to exactly this
# at the reference size, so dividing a px value by it preserves appearance.
REM_BASE_PX = 14

# .panel-page's padding and default cell gap, both driven by --panel-grid-gap.
DEFAULT_GRID_GAP = 8

# Style keys whose stored value was a raw px number.
_STYLE_PX_KEYS = (
    "font_size", "border_radius", "border_width",
    "padding", "padding_vertical", "padding_horizontal",
    "margin", "margin_vertical", "margin_horizontal",
    "letter_spacing", "cell_size", "icon_size", "thumb_size",
)

# Same, but stored on the element rather than inside its style dict.
_ELEMENT_PX_KEYS = ("icon_size", "item_height", "thumb_size")

# Overlay/sidebar box defaults, in px, as panel.js hardcoded them.
_OVERLAY_DEFAULT_W = 400
_OVERLAY_DEFAULT_H = 300
_SIDEBAR_DEFAULT_W = 320

# The grid a page had when it said nothing.
_DEFAULT_COLUMNS = 12
_DEFAULT_ROWS = 8

# What renderSpacer drew a spacer with; any of these makes it a real box.
_SPACER_VISUAL_KEYS = ("bg_color", "background_image", "background_gradient", "border_width")


def _round4(value: float) -> float:
    """Percentages are stored to 4 decimal places, everywhere, always.

    Without one canonical rounding, a px->%->px round-trip drifts by a
    millionth, the save-reconcile diff sees a change that isn't one, and the
    panel arms a phantom autosave every time somebody opens a page.
    """
    return round(value + 0.0, 4)


def _num(value, default=None):
    """Return value as a float if it is a real number, else default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _at_least_one(value, default: int) -> int:
    """A grid count or cell coordinate as the 0.7.0 loader read it: a real
    number, truncated, and never under one."""
    return max(1, int(_num(value, default) or default))


class _Cell(NamedTuple):
    """One element's grid_area, 1-based, as the 0.7.0 loader read it."""

    col: int
    row: int
    col_span: int
    row_span: int

    @classmethod
    def of(cls, area) -> "_Cell":
        if not isinstance(area, dict):
            area = {}
        return cls(*(
            _at_least_one(area.get(key), 1)
            for key in ("col", "row", "col_span", "row_span")
        ))

    @property
    def size(self) -> int:
        return self.col_span * self.row_span

    def contains(self, other: "_Cell") -> bool:
        """True when other's cell rect sits entirely inside this one."""
        return (
            other.col >= self.col
            and other.row >= self.row
            and other.col + other.col_span <= self.col + self.col_span
            and other.row + other.row_span <= self.row + self.row_span
        )


class _Grid(NamedTuple):
    """The grid one 0.7.0 box laid its cells in.

    `percent_rect` reproduces what CSS Grid actually laid out, rather than the
    naive col/columns fraction: `.panel-page` insets its content by one gap on
    every side (``pad``) and puts another gap between cells, so a naive
    conversion is off by half a gap in position and a whole gap in width at
    wide spans.
    """

    columns: int
    rows: int
    box_w: float
    box_h: float
    pad: float
    gap: float

    def percent_rect(self, cell: _Cell) -> dict:
        cell_w = (self.box_w - 2 * self.pad - (self.columns - 1) * self.gap) / self.columns
        cell_h = (self.box_h - 2 * self.pad - (self.rows - 1) * self.gap) / self.rows
        return {
            "x": _round4((self.pad + (cell.col - 1) * (cell_w + self.gap)) / self.box_w * 100),
            "y": _round4((self.pad + (cell.row - 1) * (cell_h + self.gap)) / self.box_h * 100),
            "w": _round4((cell.col_span * cell_w + (cell.col_span - 1) * self.gap) / self.box_w * 100),
            "h": _round4((cell.row_span * cell_h + (cell.row_span - 1) * self.gap) / self.box_h * 100),
        }


class _LayoutSettings(NamedTuple):
    """What the whole ui section says about every page's conversion."""

    orientation: str
    pad: float


def convert_grid_layout(ui: dict) -> None:
    """Rewrite a 0.7.0 ui section's grid pages and masters as 0.8.0 layouts,
    in place."""
    settings = _take_layout_settings(ui)
    pages = [p for p in ui.get("pages", []) if isinstance(p, dict)]
    # Masters are converted against a page's grid, but each page loses `grid`
    # as it converts -- so remember each one first.
    page_grids: dict[str, tuple[int, int]] = {}
    for page in pages:
        size = _grid_size(page)
        if page.get("id"):
            page_grids[page["id"]] = size
        _convert_page(page, size, settings)
    first_page_id = pages[0].get("id") if pages else None
    _convert_masters(ui, first_page_id, page_grids, settings)


def _take_layout_settings(ui: dict) -> _LayoutSettings:
    """The project's orientation, which leaves ui.settings for each page's
    layout, and the page padding, which the theme's grid gap set."""
    settings = ui.get("settings") if isinstance(ui.get("settings"), dict) else {}
    orientation = settings.get("orientation") or "landscape"
    if orientation not in ("landscape", "portrait"):
        orientation = "landscape"
    settings.pop("orientation", None)

    overrides = settings.get("theme_overrides")
    pad = DEFAULT_GRID_GAP
    if isinstance(overrides, dict):
        pad = _num(overrides.get("grid_gap"), DEFAULT_GRID_GAP) or DEFAULT_GRID_GAP
    return _LayoutSettings(orientation, pad)


def _grid_size(page: dict) -> tuple[int, int]:
    grid = page.get("grid") if isinstance(page.get("grid"), dict) else {}
    return (
        _at_least_one(grid.get("columns"), _DEFAULT_COLUMNS),
        _at_least_one(grid.get("rows"), _DEFAULT_ROWS),
    )


def _convert_page(page: dict, size: tuple[int, int], settings: _LayoutSettings) -> None:
    """One page's elements placed by percentage on its one layout."""
    # page.grid_gap only ever overrode the gap between cells, never the
    # page padding -- that stayed on the CSS variable.
    gap = _num(page.pop("grid_gap", None), settings.pad) or settings.pad
    box_w, box_h = _page_box(page)
    grid = _Grid(*size, box_w, box_h, settings.pad, gap)

    page["elements"] = _convert_spacers(page.get("elements", []))
    elements = [el for el in page["elements"] if isinstance(el, dict)]
    rects = {
        el["id"]: grid.percent_rect(_Cell.of(el.get("grid_area")))
        for el in elements if el.get("id")
    }
    parent_of = _adopt_into_containers(elements, page.get("id", "?"))
    placements = _place_elements(elements, rects, parent_of)

    page.pop("grid", None)
    page["snap"] = {
        "enabled": True,
        "x": _round4(100.0 / _DEFAULT_COLUMNS),
        "y": _round4(100.0 / _DEFAULT_ROWS),
    }
    page["layouts"] = [{
        "id": settings.orientation,
        "orientation": settings.orientation,
        "primary": True,
        "inherits": None,
        "placements": placements,
        "hidden": [],
    }]
    _overlay_box_to_percent(page)


def _page_box(page: dict) -> tuple[float, float]:
    """The px box a page's grid was laid out inside, at the reference size.

    A full page is the viewport. An overlay or sidebar is its own smaller box,
    which is the second, separate source of rigidity 0.8.0 removes.
    """
    page_type = page.get("page_type", "page")
    overlay = page.get("overlay") if isinstance(page.get("overlay"), dict) else {}
    if page_type == "sidebar":
        return (
            _num(overlay.get("width"), _SIDEBAR_DEFAULT_W) or _SIDEBAR_DEFAULT_W,
            REFERENCE_HEIGHT,
        )
    if page_type == "overlay":
        return (
            _num(overlay.get("width"), _OVERLAY_DEFAULT_W) or _OVERLAY_DEFAULT_W,
            _num(overlay.get("height"), _OVERLAY_DEFAULT_H) or _OVERLAY_DEFAULT_H,
        )
    return REFERENCE_WIDTH, REFERENCE_HEIGHT


def _convert_spacers(elements: list) -> list:
    """Drop invisible spacers; keep visible ones as empty labels.

    A spacer's only job was occupying grid cells, and there are no cells any
    more. But renderSpacer applies style, so one with a background or border was
    a decorative rectangle somebody drew on purpose -- that keeps its box.
    """
    kept = []
    for el in elements:
        if not isinstance(el, dict) or el.get("type") != "spacer":
            kept.append(el)
            continue
        style = el.get("style") if isinstance(el.get("style"), dict) else {}
        if not any(style.get(key) for key in _SPACER_VISUAL_KEYS):
            continue
        el["type"] = "label"
        el["text"] = ""
        el["label"] = None
        kept.append(el)
    return kept


def _adopt_into_containers(elements: list, page_id: str) -> dict:
    """Decide which elements become children of which group.

    Groups used to be decorative frames painted *behind* their apparent
    children, who were really peers. 0.8.0 makes them real containers, so the
    migration has to guess the hierarchy the author drew. It only adopts on
    full containment -- a merely overlapping element stays a page-level peer --
    and the innermost group wins.
    """
    groups = [el for el in elements if isinstance(el, dict) and el.get("type") == "group"]
    if not groups:
        return {}

    cells = {id(el): _Cell.of(el.get("grid_area")) for el in elements if isinstance(el, dict)}
    # Smallest first, so the innermost containing group is found first; the
    # sort is stable, so equal sizes keep their order on the page.
    ranked = sorted(groups, key=lambda group: cells[id(group)].size)
    parent_of = _innermost_containers(elements, ranked, cells)
    _drop_parent_cycles(parent_of)

    if parent_of:
        log.info(
            "Project migration 0.8.0: page '%s' adopted %d element(s) into "
            "container(s) %s -- drag them out in the Outline panel if that is "
            "not the grouping you meant",
            page_id, len(parent_of), sorted(set(parent_of.values())),
        )
    return parent_of


def _innermost_containers(elements: list, ranked: list, cells: dict) -> dict:
    """Each element's id against the id of the first group in ranked that
    fully contains it, for the elements some group contains."""
    parent_of: dict[str, str] = {}
    for el in elements:
        if not isinstance(el, dict) or not el.get("id"):
            continue
        group_id = _innermost_group(el, ranked, cells)
        if group_id:
            parent_of[el["id"]] = group_id
    return parent_of


def _innermost_group(el: dict, ranked: list, cells: dict):
    """The id of the first group in ranked that fully contains el, other than
    el itself; a group with no id adopts nothing."""
    cell = cells[id(el)]
    for group in ranked:
        group_id = group.get("id")
        if group is el or not group_id:
            continue
        if cells[id(group)].contains(cell):
            return group_id
    return None


def _drop_parent_cycles(parent_of: dict) -> None:
    """Identical-rect groups can point at each other. Walk each chain and drop
    any link that closes a loop."""
    for el_id in list(parent_of):
        seen = {el_id}
        cursor = parent_of.get(el_id)
        while cursor is not None:
            if cursor in seen:
                del parent_of[el_id]
                break
            seen.add(cursor)
            cursor = parent_of.get(cursor)


def _place_elements(elements: list, rects: dict, parent_of: dict) -> dict:
    """Each element's placement, against its container's box when it was
    adopted; the element loses its grid_area and gains its parent."""
    placements = {}
    for el in elements:
        el_id = el.get("id")
        if not el_id or el_id not in rects:
            continue
        el.pop("grid_area", None)
        _style_units_to_rem(el)
        parent_id = parent_of.get(el_id)
        el["parent"] = parent_id
        rect = rects[el_id]
        if parent_id and parent_id in rects:
            rect = _relative_to_parent(rect, rects[parent_id])
        placements[el_id] = rect
    return placements


def _relative_to_parent(child: dict, parent: dict) -> dict:
    """Re-express a child's page-percentage rect against its container's box."""
    p_w = parent["w"] or 100.0
    p_h = parent["h"] or 100.0
    return {
        "x": _round4((child["x"] - parent["x"]) / p_w * 100),
        "y": _round4((child["y"] - parent["y"]) / p_h * 100),
        "w": _round4(child["w"] / p_w * 100),
        "h": _round4(child["h"] / p_h * 100),
    }


def _style_units_to_rem(el: dict) -> None:
    """Rewrite one element's px measurements as rem, in place.

    rem (not em) is what makes this exact: em would resolve against the
    element's own font size, so dividing by 14 would only be right for elements
    whose font size happens to be 14.
    """
    style = el.get("style")
    if isinstance(style, dict):
        _keys_to_rem(style, _STYLE_PX_KEYS)
    _keys_to_rem(el, _ELEMENT_PX_KEYS)


def _keys_to_rem(holder: dict, keys: tuple) -> None:
    for key in keys:
        if key in holder:
            holder[key] = _px_to_rem(holder[key])


def _px_to_rem(value):
    """Convert one stored px number to rem. Non-numbers pass through."""
    px = _num(value)
    if px is None:
        return value
    return _round4(px / REM_BASE_PX)


def _overlay_box_to_percent(page: dict) -> None:
    """The overlay box itself stops being px: a share of the reference screen."""
    overlay = page.get("overlay")
    if not isinstance(overlay, dict):
        return
    for key, reference in (("width", REFERENCE_WIDTH), ("height", REFERENCE_HEIGHT)):
        px = _num(overlay.get(key))
        if px is not None:
            overlay[key] = _round4(px / reference * 100)


def _convert_masters(
    ui: dict, first_page_id, page_grids: dict, settings: _LayoutSettings,
) -> None:
    """Convert master elements, which have no grid of their own.

    A master carries one grid_area but renders into whatever grid the current
    page has, so the same master genuinely landed at different pixel rects on
    different pages. There is no correct conversion, only a least-surprising
    one: use the grid of the first page it targets, which is the page the
    author was almost certainly looking at. Every project whose pages share the
    default 12x8 -- which is all of them in practice -- converts identically
    whichever page is picked. Afterwards the ambiguity is gone for good. A
    master sits on the reference screen with the page padding as its gap.
    """
    for master in [m for m in ui.get("master_elements", []) if isinstance(m, dict)]:
        reference_id = _reference_page(master.get("pages", "*"), first_page_id, page_grids)
        columns, rows = page_grids.get(reference_id, (_DEFAULT_COLUMNS, _DEFAULT_ROWS))
        log.info(
            "Project migration 0.8.0: master element '%s' converted against "
            "page '%s' (%dx%d grid)",
            master.get("id", "?"), reference_id or "<none, used 12x8>", columns, rows,
        )
        grid = _Grid(columns, rows, REFERENCE_WIDTH, REFERENCE_HEIGHT, settings.pad, settings.pad)
        rect = grid.percent_rect(_Cell.of(master.get("grid_area")))
        master.pop("grid_area", None)
        _style_units_to_rem(master)
        master["parent"] = None
        master["placements"] = {settings.orientation: rect}
        master["hidden"] = False


def _reference_page(targets, first_page_id, page_grids: dict):
    """The page whose grid a master is converted against: the first page it
    targets that exists, the first page for every page, or none."""
    if targets == "*" or not targets:
        return first_page_id
    if isinstance(targets, str):
        return targets if targets in page_grids else None
    if isinstance(targets, list):
        return next((target for target in targets if target in page_grids), None)
    return None
