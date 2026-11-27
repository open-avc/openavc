"""THE page rules: what a page is, how a way onto it opens it, which page
stands in for one that is missing, whether a master element draws on a page,
the navigation words, and the box an overlay draws in when it sets none.

Every surface that shows, writes or reviews a page asks these questions: the
panel's navigation, its idle return and a ``?page=`` link; the Builder's
canvas, Preview and page delete; the setup screen; the AI's page doors and its
page review. Written once each, they agree on what draws.

- **A page's type** is ``page``, ``overlay`` or ``sidebar``. A regular page
  replaces the one on screen and goes into the history ``$back`` walks; an
  overlay or a sidebar opens over it, in a box of its own. A type this release
  does not know, or none, reads as ``page``, which is what navigation draws it
  as.
- **The stand-in** for a page that is missing (the idle page, a ``?page=``
  link, a page deleted) is the first regular page, else the first page.
- **A master element draws on a page** when its ``pages`` names the page
  (``*``, or a list of page ids; left out, the schema's ``*``), it is not
  hidden, and its type is one the panel draws.
- **The navigation words** are ``$back`` and ``$dismiss``, the moves the panel
  makes itself; a target starting with ``$`` names no page.
- **An overlay's box**, as a percentage of the screen, is the one it sets, and
  for a side it leaves blank the type's default: a dialog 31.25 by 37.5 (400 by
  300 on the 1280 by 800 reference), a sidebar 25 wide. A sidebar is the
  screen's height, whatever its own height says.

``page_rules_table()`` is rendered into the panel's and the Builder's generated
project-value modules as ``PAGE_RULES`` (``core/project_values_gen.py``), and
the panel's ``web/panel/modules/pages.js`` is held to the functions here by
``tests/test_page_rules_mirror.py``, run under Node.

Duck-typed and import-free, as ``page_geometry.py``: a page or a master is a
mapping or anything with attributes, so the loader's models, a Builder draft
and a hand-built test page all work. The types the panel draws are handed in
(``RENDERED_TYPES`` in ``ui/page_review.py``), so this module imports nothing
and the project schema can import it.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from typing import Any

PAGE = "page"
OVERLAY = "overlay"
SIDEBAR = "sidebar"

#: Every page type this release draws, the regular page first.
PAGE_TYPES = (PAGE, OVERLAY, SIDEBAR)

#: The types that open over the page on screen, in a box of their own.
OPENS_OVER = (OVERLAY, SIDEBAR)

#: What a type this release does not know, or none, reads as.
UNKNOWN_TYPE_READS_AS = PAGE

#: What a master's ``pages`` says to draw on every page, and its blank.
EVERY_PAGE = "*"

#: The moves the panel makes itself, never page ids.
NAVIGATION_WORDS = ("$back", "$dismiss")

#: A navigation target starting with this names no page.
NAVIGATION_PREFIX = "$"

#: The box each page type that opens over draws in for a side it leaves blank,
#: as a percentage of the screen. A sidebar has no height of its own.
OVERLAY_BOX = {
    OVERLAY: {"width": 31.25, "height": 37.5},
    SIDEBAR: {"width": 25},
}

#: A sidebar's height, whatever its own height says: the screen's.
SIDEBAR_HEIGHT = 100


def _read(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def page_type_of(page: Any) -> str:
    """How ``page`` is shown: its type when this release knows it, else a
    regular page."""
    written = _read(page, "page_type")
    return written if isinstance(written, str) and written in PAGE_TYPES else UNKNOWN_TYPE_READS_AS


def opens_over(page: Any) -> bool:
    """Whether ``page`` opens over the page on screen: an overlay or a sidebar."""
    return page_type_of(page) in OPENS_OVER


def is_regular_page(page: Any) -> bool:
    """Whether ``page`` is shown in place of the page on screen."""
    return not opens_over(page)


def fallback_page(pages: Sequence[Any]) -> Any:
    """The page that stands in for one that is missing: the first regular
    page, else the first page (None: there are no pages)."""
    return next((p for p in pages if is_regular_page(p)), pages[0] if pages else None)


def stand_in(pages: Sequence[Any], page_id: Any) -> Any:
    """The page drawn for ``page_id``: that page, else ``fallback_page``."""
    for page in pages:
        if _read(page, "id") == page_id:
            return page
    return fallback_page(pages)


def names_page(pages: Any, page_id: str) -> bool:
    """Whether a master's ``pages`` names this page: ``*``, or a list holding
    its id. Anything else names none."""
    return pages == EVERY_PAGE or (isinstance(pages, list) and page_id in pages)


def master_draws_on(master: Any, page_id: str, drawn_types: Collection[str]) -> bool:
    """Whether ``master`` draws on the page ``page_id``: its ``pages`` names
    the page (left out, ``*``, the schema's default), it is not hidden, and
    its type is one of ``drawn_types``, the types the panel draws. The panel's
    ``masterDrawsOn`` (``web/panel/modules/pages.js``) asks the same, for its
    page draw, its empty-page notice and its redraw on a ui/ file change."""
    written_type = _read(master, "type")
    return (
        not _read(master, "hidden")
        and isinstance(written_type, str)
        and written_type in drawn_types
        and names_page(_read(master, "pages", EVERY_PAGE), page_id)
    )


def any_master_draws(masters: Sequence[Any], pages: Sequence[Any], drawn_types: Collection[str]) -> bool:
    """Whether one of ``masters`` draws on one of ``pages`` (``master_draws_on``)."""
    return any(master_draws_on(master, _read(page, "id"), drawn_types) for master in masters for page in pages)


def is_navigation_word(target: Any) -> bool:
    """Whether ``target`` is one of the moves the panel makes itself."""
    return target in NAVIGATION_WORDS


def names_no_page(target: Any) -> bool:
    """Whether a navigation ``target`` is no page id: it starts with ``$``,
    the navigation words' prefix."""
    return isinstance(target, str) and target.startswith(NAVIGATION_PREFIX)


def _side(value: Any, default: float) -> float:
    """A box side as the panel reads it: a finite number, or numeric text, else
    ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return default
    return value if isinstance(value, (int, float)) and math.isfinite(value) else default


def overlay_box(page: Any) -> dict[str, float]:
    """The box an overlay or sidebar page draws in, ``{"width", "height"}`` as
    percentages of the screen: each side it sets, else its type's default. A
    page that does not open over draws no box and is read as a dialog."""
    overlay = _read(page, "overlay") or {}
    if page_type_of(page) == SIDEBAR:
        return {"width": _side(_read(overlay, "width"), OVERLAY_BOX[SIDEBAR]["width"]), "height": SIDEBAR_HEIGHT}
    box = OVERLAY_BOX[OVERLAY]
    return {side: _side(_read(overlay, side), box[side]) for side in ("width", "height")}


def page_rules_table() -> dict[str, Any]:
    """The rules as data, for the panel and the Builder."""
    return {
        "page_types": list(PAGE_TYPES),
        "opens_over": list(OPENS_OVER),
        "unknown_type_reads_as": UNKNOWN_TYPE_READS_AS,
        "every_page": EVERY_PAGE,
        "navigation_words": list(NAVIGATION_WORDS),
        "navigation_prefix": NAVIGATION_PREFIX,
        "overlay_box": {kind: dict(box) for kind, box in OVERLAY_BOX.items()},
        "sidebar_height": SIDEBAR_HEIGHT,
    }
