"""THE edit rule for a project's pages: what an edit to the running project
adds to them that they may not hold, which the take-in refuses.

What is judged is the page structure (``STRUCTURE``): an element id used twice
or not spelled as one, a page id, an arrangement, a container, as
``ui/page_structure.page_issues`` finds them. An issue is the edit's when the
edit leaves more of its kind than it found and it is not one the running
project holds as it is, so a project that already holds one still takes every
other edit, and a rename or a move of what holds it makes none
(``issues_an_edit_makes``).

The take-in (``core/project_changes._take_in``) asks it of every edit
(``refuse_issues_an_edit_makes``, raising ``PageIssueError``), so the
Programmer's save and every AI tool are refused alike, in the issues' sentence;
an AI page door has already refused in its own words what it is about to
write. An import and a load never refuse: they repair what has one plain
repair (``ui/page_repair.py``), and Validate reports the rest. A binding's shape
is never the edit's: the UI Builder saves an action while it is being filled
in, and Validate reports it.

Duck-typed: a project is anything with a ``ui`` holding ``pages`` and
``master_elements``, the loader's models or the JSON.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from openavc.ui import page_structure as ps

#: The issues an edit is refused for: what the pages hold, never a binding's shape.
STRUCTURE = frozenset((
    ps.ELEMENT_ID_TAKEN, ps.ELEMENT_ID_SPELLED, ps.PAGE_ID_TAKEN, ps.PAGE_ID_NAMES_NO_PAGE,
    ps.LAYOUT_ID_REPEATED, ps.ORIENTATION_TAKEN, ps.INHERITS_SELF, ps.INHERITS_MISSING, ps.INHERITS_LOOP,
    ps.PARENT_MISSING, ps.PARENT_NOT_CONTAINER, ps.PARENT_LOOP, ps.MASTER_PARENT,
))

#: The two parts of a project's ``ui`` the structure is read from.
_PARTS = ("pages", "master_elements")


class PageIssueError(ValueError):
    """An edit that adds to the pages what they may not hold, in the issues'
    sentence (``page_structure.refusal``)."""


def issues_an_edit_makes(running: Any, proposed: Any) -> list[ps.Issue]:
    """The structure issues ``proposed`` holds that the edit from ``running``
    made (``running`` None when nothing runs). An issue is the edit's when
    ``proposed`` holds more of its kind than ``running`` does, and is not one
    ``running`` holds as it is."""
    before, after = _structure_issues(running), _structure_issues(proposed)
    grown = _grown_kinds(before, after)
    return _not_held(before, after, grown) if grown else []


def refuse_issues_an_edit_makes(running: Any, proposed: Any) -> None:
    """Raise ``PageIssueError`` when the edit from ``running`` to ``proposed``
    makes an issue (:func:`issues_an_edit_makes`). An edit that leaves the
    pages and masters as they were is not read."""
    before, after = _part(running, "ui"), _part(proposed, "ui")
    if before is not None and all(_part(before, part) == _part(after, part) for part in _PARTS):
        return
    sentence = ps.refusal(issues_an_edit_makes(running, proposed))
    if sentence is not None:
        raise PageIssueError(sentence)


def _structure_issues(project: Any) -> list[ps.Issue]:
    ui = _part(project, "ui")
    if ui is None:
        return []
    pages, masters = (_listed(_part(ui, part)) for part in _PARTS)
    return [issue for issue in ps.page_issues(pages, masters) if issue.code in STRUCTURE]


def _grown_kinds(before: list[ps.Issue], after: list[ps.Issue]) -> set[str]:
    """The codes ``after`` holds more issues of than ``before``."""
    held = Counter(issue.code for issue in before)
    return {code for code, count in Counter(issue.code for issue in after).items() if count > held[code]}


def _not_held(before: list[ps.Issue], after: list[ps.Issue], codes: set[str]) -> list[ps.Issue]:
    """Each issue of ``after`` under one of ``codes`` beyond the copies of it
    ``before`` holds."""
    unchanged = Counter(before)
    made = []
    for issue in after:
        if issue.code in codes and unchanged[issue] <= 0:
            made.append(issue)
        unchanged[issue] -= 1
    return made


def _part(holder: Any, name: str) -> Any:
    if holder is None:
        return None
    return holder.get(name) if isinstance(holder, Mapping) else getattr(holder, name, None)


def _listed(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []
