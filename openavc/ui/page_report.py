"""THE reading of what a report says a project's pages hold that they may not.

Two reports say it, and say it alike: the Builder's Validate, which posts the
pages it holds to ``POST /api/ui/page-issues``, and the AI's ``review_ui``.
Both list ``ui/page_structure.page_issues``, in its order, with one
difference from a door's reading. Three binding issues are also findings of
the page review about the same control, in its own words and with what
follows: an action with no name and one nothing dispatches
(``binding_not_dispatched``), and a value, look or items binding with no key
(``binding_without_key``, or ``binding_not_rendered`` on a type that never
reads the slot). Both reports list the page review too, so on a control the
review says one of those of, the report leaves the issue to it
(``SAID_BY_THE_REVIEW``), and one mistake is one sentence. Where the review is
silent (a value_map option written as a list of actions, which it does not
read; a type it does not know) the issue stays.

A control is told apart by where an issue places it, its page and its id, so
two controls sharing an id on one page (itself an issue) share what the
review said of either.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from openavc.ui.page_review import binding_findings, do_action_findings
from openavc.ui.page_structure import (
    ACTION_UNKNOWN,
    ACTION_UNNAMED,
    SHOW_WITHOUT_KEY,
    Issue,
    page_issues,
    placed_controls,
)

#: Each issue the page review also says of the same control, with the kinds of
#: review finding that say it (``page_review.do_action_findings`` and
#: ``binding_findings``). A test holds each pair to a case both say.
SAID_BY_THE_REVIEW: dict[str, frozenset[str]] = {
    ACTION_UNNAMED: frozenset({"binding_not_dispatched"}),
    ACTION_UNKNOWN: frozenset({"binding_not_dispatched"}),
    SHOW_WITHOUT_KEY: frozenset({"binding_without_key", "binding_not_rendered"}),
}

_NONE: frozenset[str] = frozenset()


def reported_issues(pages: Iterable[Any], masters: Iterable[Any] = ()) -> list[Issue]:
    """The project's page issues as a report says them: ``page_issues``, but
    one the page review says of the same control is left to the review."""
    pages = list(pages)
    masters = list(masters)
    issues = page_issues(pages, masters)
    asked = {(issue.page, issue.element) for issue in issues if issue.code in SAID_BY_THE_REVIEW}
    if not asked:
        return issues
    said = _review_kinds(pages, masters, asked)
    return [
        issue for issue in issues
        if not SAID_BY_THE_REVIEW.get(issue.code, _NONE) & said.get((issue.page, issue.element), _NONE)
    ]


def _review_kinds(
    pages: list, masters: list, asked: set[tuple[Any, Any]],
) -> dict[tuple[Any, Any], frozenset[str]]:
    """The kinds of finding the page review gives the controls ``asked``
    about (by page and id) about their bindings."""
    kinds: dict[tuple[Any, Any], frozenset[str]] = {}
    for page_id, element_id, element in placed_controls(pages, masters):
        if (page_id, element_id) not in asked:
            continue
        dump = _as_mapping(element)
        found = frozenset(finding.kind for finding in do_action_findings(dump) + binding_findings(dump))
        kinds[(page_id, element_id)] = kinds.get((page_id, element_id), _NONE) | found
    return kinds


def _as_mapping(element: Any) -> Mapping[str, Any]:
    """A control as the page review reads one. Only a control holding
    bindings is asked about, so it is a mapping or holds attributes."""
    if isinstance(element, Mapping):
        return element
    dump = getattr(element, "model_dump", None)
    return dump() if callable(dump) else dict(vars(element))
