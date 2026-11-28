"""THE answer to which pages a project names outside its pages' controls that
it does not have: the idle page, a master element's ``pages``, and a macro
step's ``ui.navigate``.

A control's page move (a Page Nav's target, a ``ui.navigate`` action) is
``ui/page_references.py``'s; a macro's lands by the same rule
(``page_rules.reaches``), and every sentence about a page that is not there
ends the same way (``no_such_page``, which that module asks too). The idle page
and a master's pages name a page and nothing else, so ``$back`` and
``$dismiss``, the panel's own moves, are no page there. An idle page that is
not there is not a page the panel cannot reach: it returns to the page standing
in for it (``page_rules.fallback_page``), which the sentence names.

Both reports give this answer, so they say the same sentence of the same
project: the Builder's Validate, which posts the UI and macros it holds,
unsaved, to ``POST /api/ui/page-issues`` and lists it (it judges none of the
three itself), and the AI's ``review_ui``. A sentence names the macro and not
the step, which the record places (``step``): Validate names the step in its
own words. Nothing refuses on it: the project save takes all three, and a macro
may be written before the page it opens.

Duck-typed, as ``page_rules.py``: a project's UI, a page, a master, a macro or
a step is a mapping or anything with attributes, and any JSON is answered
without raising, since a report is asked of whatever a client posts. A value
that is no name says nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.ui.page_rules import fallback_page, reaches

IDLE_PAGE_MISSING = "idle_page_missing"
MASTER_PAGE_MISSING = "master_page_missing"
MACRO_PAGE_MISSING = "macro_page_missing"


@dataclass(frozen=True)
class MissingPage:
    """One place outside a page's controls that names a page the project does
    not have: what (``code``), the whole of it in one sentence (``message``),
    and where. ``element`` is the master and ``macro`` the macro it is in,
    ``step`` the step's path in that macro (``steps[2].then_steps[0]``), each
    None when it is about none; the idle page is about none of them."""

    code: str
    message: str
    element: str | None = None
    macro: str | None = None
    step: str | None = None


def missing_pages(ui: Any, macros: Any = ()) -> list[MissingPage]:
    """Every page the project names outside its pages' controls that it does
    not have: the idle page, then each master's ``pages`` in turn, then each
    macro's page moves, steps in order and each before its own branches."""
    pages = [page for page in _listed_of(ui, "pages") if _named(_field(page, "id")) is not None]
    page_ids = {_field(page, "id") for page in pages}
    return [
        *_idle_page_missing(ui, pages, page_ids),
        *_master_pages_missing(_listed_of(ui, "master_elements"), page_ids),
        *_macro_pages_missing(macros, page_ids),
    ]


def no_such_page(target: str, page_ids: Iterable[str]) -> str:
    """The end of every sentence about a page that is not there: the page,
    then the pages that are."""
    return f"page '{target}', which does not exist. The pages are: {_pages_listed(page_ids)}."


def _pages_listed(page_ids: Iterable[str]) -> str:
    """The pages, named in order; none says so rather than trailing off."""
    return ", ".join(sorted(page_ids)) or "none"


def _idle_page_missing(ui: Any, pages: list, page_ids: set[str]) -> list[MissingPage]:
    """The idle page, when it names a page that is not there, with the page
    the panel returns to instead."""
    idle = _named(_field(_field(ui, "settings"), "idle_page"))
    if idle is None or idle in page_ids:
        return []
    stand_in = fallback_page(pages)
    instead = f", so an idle panel returns to '{_field(stand_in, 'id')}' instead" if stand_in else ""
    return [MissingPage(
        IDLE_PAGE_MISSING,
        f"The idle page '{idle}' does not exist{instead}. The pages are: {_pages_listed(page_ids)}.",
    )]


def _master_pages_missing(masters: list, page_ids: set[str]) -> list[MissingPage]:
    """Each page a master's ``pages`` lists that is not there. ``*`` lists
    none: it is every page."""
    found = []
    for master in masters:
        shown = _field(master, "pages")
        master_id = _said_id(_field(master, "id"))
        for target in shown if isinstance(shown, list) else []:
            target = _named(target)
            if target is not None and target not in page_ids:
                found.append(MissingPage(
                    MASTER_PAGE_MISSING,
                    f"Master element '{master_id}' shows on {no_such_page(target, page_ids)}",
                    element=master_id,
                ))
    return found


def _macro_pages_missing(macros: Any, page_ids: set[str]) -> list[MissingPage]:
    """Each macro step's ``ui.navigate`` to a page that is not there."""
    found = []
    for macro in macros if isinstance(macros, (list, tuple)) else []:
        macro_id = _said_id(_field(macro, "id"))
        for path, step in _steps_of(_field(macro, "steps")):
            target = _moved_to_no_page(step, page_ids)
            if target is not None:
                found.append(MissingPage(
                    MACRO_PAGE_MISSING,
                    f"Macro '{macro_id}' navigates to {no_such_page(target, page_ids)}",
                    macro=macro_id, step=path,
                ))
    return found


def _moved_to_no_page(step: Any, page_ids: set[str]) -> str | None:
    """The page a ``ui.navigate`` step names that the move cannot land on, or
    None: another step, one naming no page, or one that lands."""
    if _field(step, "action") != "ui.navigate":
        return None
    target = _named(_field(step, "page"))
    return None if target is None or reaches(target, page_ids) else target


def _steps_of(steps: Any) -> Iterator[tuple[str, Any]]:
    """Each step and every step its branches hold, with its path, each before
    its own branches and ``then_steps`` before ``else_steps`` (the order the
    Builder's ``placedSteps`` walks). A stack rather than recursion, so a
    macro nested past Python's own limit is walked too."""
    stack = _indexed("steps", steps)[::-1]
    while stack:
        path, step = stack.pop()
        yield path, step
        branches = [
            *_indexed(f"{path}.then_steps", _field(step, "then_steps")),
            *_indexed(f"{path}.else_steps", _field(step, "else_steps")),
        ]
        stack.extend(branches[::-1])


def _indexed(prefix: str, steps: Any) -> list[tuple[str, Any]]:
    """The steps of a list, each with its path under ``prefix``; anything else, none."""
    return [(f"{prefix}[{i}]", step) for i, step in enumerate(steps)] if isinstance(steps, list) else []


def _field(holder: Any, name: str) -> Any:
    """``holder``'s ``name``, a mapping's key or a model's attribute, else
    None. No name read here is an attribute of a JSON value that is not an
    object, so a string, number or list holds none of them."""
    if isinstance(holder, Mapping):
        return holder.get(name)
    return getattr(holder, name, None)


def _listed_of(holder: Any, name: str) -> list:
    """``holder``'s ``name`` when it is a list, else none."""
    value = _field(holder, name)
    return list(value) if isinstance(value, (list, tuple)) else []


def _named(value: Any) -> str | None:
    """The name ``value`` gives, or None: only a non-empty string names anything."""
    return value if isinstance(value, str) and value else None


def _said_id(value: Any) -> str:
    """An id as a sentence names it: itself, or its text when it is no string."""
    return value if isinstance(value, str) else "?" if value is None else str(value)
