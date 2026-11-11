"""When the AI deletes a page, what the rest of the project says about it follows.

A page is named by a page nav's target, by the ``ui.navigate`` actions in a
control's interaction slots, by each master's page list, by trigger conditions
that match on it being the current page, by the idle page and by each page
group. Each holder is one function here, so a new place a page id can sit is one
line in :func:`scrub_page_references`.

The Builder's twin is ``removePageAndScrubRefs`` in ``projectRewrites.ts`` (the
idle page is reset beside it, in ``CanvasToolbar.tsx``): a page the AI deletes
must not leave masters pinned to a ghost or nav buttons that go nowhere. Two
differences between them are known: this walks every key of a control's ``do``
map where the Builder walks its action slots, and this takes the page out of
page groups, which the Builder does not.
"""

from typing import Any

#: A slot whose every action moved to the deleted page: it is removed.
_DROP = object()


def scrub_page_references(project: Any, page_id: str) -> None:
    """Clear everything in ``project`` that pointed at the deleted ``page_id``.

    The page itself is already gone from ``project.ui.pages``.
    """
    for master in project.ui.master_elements:
        _master_without_page(master, page_id)
    for page in project.ui.pages:
        for el in page.elements:
            _control_without_page(el, page_id)
    for macro in project.macros:
        _macro_without_page(macro, page_id)
    _idle_page_without(project.ui, page_id)
    for group in project.ui.page_groups:
        _group_without_page(group, page_id)


def _master_without_page(master: Any, page_id: str) -> None:
    """A master's page list without the page. A master shown nowhere is
    invisible with no way to see why, so one left with no page shows on every
    page ("*"), the least surprising repair."""
    if isinstance(master.pages, list) and page_id in master.pages:
        remaining = [pid for pid in master.pages if pid != page_id]
        master.pages = remaining if remaining else "*"


def _control_without_page(el: Any, page_id: str) -> None:
    if el.type == "page_nav" and el.target_page == page_id:
        el.target_page = ""
    _without_moves_to(el, page_id)


def _without_moves_to(el: Any, page_id: str) -> None:
    """A control's ``do`` slots without their moves to the page, every slot and
    either shape (an array, or the legacy single object); a slot left empty is
    removed. One spelling to look for: the binding action is ``ui.navigate``,
    the same as the macro step and the WS frame."""
    bindings = el.bindings if isinstance(el.bindings, dict) else None
    do_map = bindings.get("do") if bindings else None
    if not isinstance(do_map, dict):
        return
    for slot in list(do_map.keys()):
        raw = do_map[slot]
        kept = _slot_without_moves(raw, page_id)
        if kept is _DROP:
            del do_map[slot]
        elif kept is not raw:
            do_map[slot] = kept


def _slot_without_moves(raw: Any, page_id: str) -> Any:
    """One slot without its moves to the page: ``raw`` itself when nothing in
    it moves there, ``_DROP`` when nothing else is left."""
    if isinstance(raw, list):
        kept = [a for a in raw if not _moves_to(a, page_id)]
        if len(kept) == len(raw):
            return raw
        return kept if kept else _DROP
    return _DROP if _moves_to(raw, page_id) else raw


def _moves_to(action: Any, page_id: str) -> bool:
    return (
        isinstance(action, dict)
        and action.get("action") == "ui.navigate"
        and action.get("page") == page_id
    )


def _macro_without_page(macro: Any, page_id: str) -> None:
    """Each trigger's conditions without those that matched on the page being
    the current one, whatever their operator."""
    for trigger in macro.triggers or []:
        if trigger.conditions:
            trigger.conditions = [
                c for c in trigger.conditions if not _matches_current_page(c, page_id)
            ]


def _matches_current_page(condition: Any, page_id: str) -> bool:
    return (
        getattr(condition, "key", None) == "system.current_page"
        and getattr(condition, "value", None) == page_id
    )


def _group_without_page(group: Any, page_id: str) -> None:
    if page_id in group.pages:
        group.pages = [pid for pid in group.pages if pid != page_id]


def _idle_page_without(ui: Any, page_id: str) -> None:
    """An idle page that was the deleted one falls to the first page left, or
    to none when no page is."""
    if ui.settings.idle_page == page_id:
        ui.settings.idle_page = ui.pages[0].id if ui.pages else ""
