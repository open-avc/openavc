"""When the AI deletes a page, what the rest of the project says about it follows.

A page is named by a page nav's target, by the ``ui.navigate`` actions a
control or a master keeps (in every ``do`` slot, in the off and long press
actions an action carries, in a value_map's options and in a matrix row's own
route: the places the page review walks, ``do_action_dicts``), by each master's
page list, by a macro's ``ui.navigate`` steps, by trigger conditions that match
on it being the current page, by the idle page and by each page group. Each
holder is one function here, so a new place a page id can sit is one line in
:func:`scrub_page_references`.

The Builder's twin is ``removePageAndScrubRefs`` in ``projectRewrites.ts`` (the
idle page is reset beside it, in ``CanvasToolbar.tsx``): a page the AI deletes
must not leave masters pinned to a ghost or nav buttons that go nowhere, and an
AI delete and a Builder delete leave the same project
(``tests/fixtures/page_delete_cases.json`` holds both to it).
"""

from typing import Any

from openavc.ui.page_review import _NESTED_ACTION_FIELDS

#: A slot whose every action moved to the deleted page: it is removed.
_DROP = object()


def scrub_page_references(project: Any, page_id: str) -> None:
    """Clear everything in ``project`` that pointed at the deleted ``page_id``.

    The page itself is already gone from ``project.ui.pages``.
    """
    for master in project.ui.master_elements:
        _master_without_page(master, page_id)
        _control_without_page(master, page_id)
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
    _rows_without_moves(el, page_id)


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
    it moves there, ``_DROP`` when nothing else is left. What each action kept
    carries is cleaned in place."""
    if isinstance(raw, list):
        kept = _actions_without_moves(raw, page_id)
        if kept is raw:
            return raw
        return kept if kept else _DROP
    if _moves_to(raw, page_id):
        return _DROP
    _carried_without_moves(raw, page_id)
    return raw


def _actions_without_moves(actions: list, page_id: str) -> list:
    """A list of actions without its moves to the page, ``actions`` itself
    when it held none; what each action kept carries is cleaned in place.
    Whatever is not an action is kept."""
    kept = [a for a in actions if not _moves_to(a, page_id)]
    for action in kept:
        _carried_without_moves(action, page_id)
    return actions if len(kept) == len(actions) else kept


def _carried_without_moves(action: Any, page_id: str) -> None:
    """The moves to the page dropped from what an action carries: its off and
    long press actions, then a value_map's options."""
    if isinstance(action, dict):
        _nested_without_moves(action, page_id)
        _options_without_moves(action, page_id)


def _nested_without_moves(action: dict, page_id: str) -> None:
    """Its off and long press actions, each removed when it moved to the page."""
    for field in _NESTED_ACTION_FIELDS:
        if _moves_to(action.get(field), page_id):
            del action[field]
        else:
            _carried_without_moves(action.get(field), page_id)


def _options_without_moves(action: dict, page_id: str) -> None:
    """A value_map's options, each one that moved to the page removed."""
    mapped = action.get("map") if action.get("action") == "value_map" else None
    if not isinstance(mapped, dict):
        return
    for option in [o for o, a in mapped.items() if _moves_to(a, page_id)]:
        del mapped[option]
    for option_action in mapped.values():
        _carried_without_moves(option_action, page_id)


def _rows_without_moves(el: Any, page_id: str) -> None:
    """A matrix's rows without their own route's moves to the page. A row whose
    route held nothing else keeps an empty one, which the panel reads as a row
    made to do nothing; taking the route away would hand the row to the
    matrix's own route instead."""
    for row in _routed_rows(el):
        row["route"] = _actions_without_moves(row["route"], page_id)


def _routed_rows(el: Any) -> list:
    """A matrix's rows that carry their own route. Only a list of rows: a
    generated axis has no route of its own."""
    config = getattr(el, "matrix_config", None) if getattr(el, "type", "") == "matrix" else None
    rows = config.get("destinations") if isinstance(config, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("route"), list)]


def _moves_to(action: Any, page_id: str) -> bool:
    return (
        isinstance(action, dict)
        and action.get("action") == "ui.navigate"
        and action.get("page") == page_id
    )


def _macro_without_page(macro: Any, page_id: str) -> None:
    """The macro's steps without those that move to the page, its branches
    included, and each trigger's conditions without those that matched on the
    page being the current one, whatever their operator."""
    if macro.steps:
        macro.steps = _steps_without_page(macro.steps, page_id)
    for trigger in macro.triggers or []:
        if trigger.conditions:
            trigger.conditions = [
                c for c in trigger.conditions if not _matches_current_page(c, page_id)
            ]


def _steps_without_page(steps: list, page_id: str) -> list:
    """Steps without those that move to the page, ``steps`` itself when none
    did; each branch kept is cleaned in place."""
    kept = [step for step in steps if not _step_moves_to(step, page_id)]
    for step in kept:
        _branches_without_page(step, page_id)
    return steps if len(kept) == len(steps) else kept


def _step_moves_to(step: Any, page_id: str) -> bool:
    return getattr(step, "action", None) == "ui.navigate" and getattr(step, "page", None) == page_id


def _branches_without_page(step: Any, page_id: str) -> None:
    for branch in ("then_steps", "else_steps"):
        if getattr(step, branch, None):
            setattr(step, branch, _steps_without_page(getattr(step, branch), page_id))


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
