"""THE rule for what a project's pages may hold: their structure, and the
shape of the free-form parts of a control's bindings.

Structure:

- **An element id is used once** across every page and master. A press
  reaches the first control holding it, pages in order and then the masters
  (``core/ui_events.find_element``), so a second control with it would run
  the first one's actions (``element_id_holders``, ``id_taken``).
- **A page id is used once, and names a page.** One starting with ``$`` is
  read by the panel as one of its own moves (``page_rules.names_no_page``),
  so a Page Nav to that page never lights (``page_id_issue``).
- **A page's arrangements** (layouts): one per id, and one per orientation,
  since the runtime draws the first match and a second is unreachable; an
  ``inherits`` that names another arrangement of the page; no loop
  (``inherits_issue``, ``orientation_issue``, ``inherits_loops``; all of them
  for one page, ``layout_issues``).
- **A control's container** (``parent``) is a group on the same page, and
  never the control itself or anything inside it (``parent_issue``; every
  control of a page, ``parent_issues``).
- **A master has no container.** It is placed on the screen by orientation,
  never inside a page's group (``master_parent_issue``).

The shape of a binding (``binding_issues``): the show/do model, each ``do``
interaction a list of action objects the runtime dispatches with the fields
each needs, a press mode the panel knows with what it needs, and each
``show`` slot an object of the form the panel reads (a look's ``states`` a
map, a ``visible_when`` operator by any spelling ``condition_eval`` takes). A
mode or an operator that is not a name is refused by its field, never hashed.
Three of a binding's issues are not about its shape but what it may do, each
with a code of its own: the retired flat model (``LEGACY_BINDINGS``), a
``state.set`` on a device's key (``DEVICE_STATE_WRITE``, device state is
read-only), and a two-way value that is not a variable
(``WRITE_BACK_NOT_VARIABLE``). Three more have a code of their own because
the page review says them too, so a report can leave them to it
(``page_report``): an action
with no name (``ACTION_UNNAMED``), one nothing dispatches (``ACTION_UNKNOWN``),
and a value, look or items binding with no key (``SHOW_WITHOUT_KEY``).

Every answer is an :class:`Issue`: a code, the sentence, and where it is.
``page_issues`` is the whole project's, the one traversal, written over the
same questions the AI's page doors ask before they build, so a door and a
report cannot come to different conclusions. It reads a project as the panel
and the runtime do: an interaction written as one action object rather than a
list runs as a list of one, so it is no issue in a project that holds it
(a door wraps it before it asks). ``refusal`` joins issues into the sentence a
door refuses with. Nothing here refuses, and the project model does not ask
it: a whole-project save or load never refuses a project for an issue it
already holds, and a door asks only about what it is writing, so a project
that already holds one still takes every other edit.

What a report says (the Builder's Validate, through
``POST /api/ui/page-issues``, and the AI's ``review_ui``) is
``ui/page_report.py``'s: these issues, less the three binding issues the page
review says of the same control in its own words. A door, which refuses
before anything is reviewed, says those itself.

Duck-typed, as ``page_geometry.py`` and ``page_rules.py``: a page, layout,
control or master is a mapping or anything with attributes, and any JSON is
read without raising (an id that is a list or an object is read as its JSON
text, ``_id``), since a report is asked of whatever a client posts. The names a
binding is checked against come from their homes, never a copy kept here: the
interactions are ``page_references.ACTION_SLOTS`` and the actions
``ui_events.DISPATCHED_ACTIONS``. Those modules reach the device manager and
the project loader, so they are imported when a binding is first checked,
which keeps this module importable by the project schema.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from typing import Any

from openavc.core.condition_eval import canonical_operator
from openavc.ui.page_rules import NAVIGATION_PREFIX, NAVIGATION_WORDS, names_no_page

ELEMENT_ID_TAKEN = "element_id_taken"
PAGE_ID_TAKEN = "page_id_taken"
PAGE_ID_NAMES_NO_PAGE = "page_id_names_no_page"
LAYOUT_ID_REPEATED = "layout_id_repeated"
ORIENTATION_TAKEN = "orientation_taken"
INHERITS_SELF = "inherits_self"
INHERITS_MISSING = "inherits_missing"
INHERITS_LOOP = "inherits_loop"
PARENT_MISSING = "parent_missing"
PARENT_NOT_CONTAINER = "parent_not_container"
PARENT_LOOP = "parent_loop"
MASTER_PARENT = "master_parent"
BINDING = "binding"
ACTION_UNNAMED = "action_unnamed"
ACTION_UNKNOWN = "action_unknown"
SHOW_WITHOUT_KEY = "show_without_key"
LEGACY_BINDINGS = "legacy_bindings"
DEVICE_STATE_WRITE = "device_state_write"
WRITE_BACK_NOT_VARIABLE = "write_back_not_variable"

#: The type of control that holds others.
CONTAINER_TYPE = "group"

#: An arrangement's orientation when it writes none: the schema's default
#: (``project_loader.Layout``), which a test holds this to.
ORIENTATION_LEFT_OUT = "landscape"


@dataclass(frozen=True)
class Issue:
    """One thing a page may not hold: what (``code``), the whole of it in one
    sentence (``message``), and where. ``page`` is the page it is on (None
    for a master), ``element`` the control and ``layout`` the arrangement it
    is about, each None when it is about none."""

    code: str
    message: str
    page: str | None = None
    element: str | None = None
    layout: str | None = None


def refusal(issues: Iterable[Issue]) -> str | None:
    """The issues as the one sentence a door refuses with, None when there
    are none."""
    messages = [issue.message for issue in issues]
    return "; ".join(messages) if messages else None


def _read(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _listed(obj: Any, key: str) -> list:
    value = _read(obj, key)
    return list(value) if isinstance(value, (list, tuple)) else []


def _id(obj: Any, key: str = "id") -> Any:
    """The id ``obj`` holds under ``key``, as one that can be looked up: itself,
    or for a list or an object (which no model reading holds, but posted JSON
    can) its JSON text."""
    value = _read(obj, key)
    try:
        hash(value)
    except TypeError:
        return json.dumps(value, sort_keys=True, default=str)
    return value


# --- element ids -------------------------------------------------------------


def element_id_holders(pages: Iterable[Any], masters: Iterable[Any]) -> dict[Any, list[str | None]]:
    """Every control's id, with where each control holding it is, in the
    order a press looks: the id of each page holding one, page by page, then
    None for each master."""
    holders: dict[Any, list[str | None]] = {}
    for page in pages:
        for element in _listed(page, "elements"):
            holders.setdefault(_id(element), []).append(_id(page))
    for master in masters:
        holders.setdefault(_id(master), []).append(None)
    return holders


def id_taken(element_id: Any, holders: Sequence[str | None], *, master: bool = False) -> Issue | None:
    """Why a new control (a master, when ``master``) cannot have
    ``element_id``, or None when it can. ``holders`` is where the controls
    already holding it are (:func:`element_id_holders`); a page holding it is
    named first, since that is the copy a press reaches."""
    if not holders:
        return None
    on_page = next((page_id for page_id in holders if page_id is not None), None)
    if on_page is not None:
        message = f"Element '{element_id}' already exists on page '{on_page}'"
    elif master:
        message = f"Master element '{element_id}' already exists"
    else:
        message = f"Element '{element_id}' already exists as a master element"
    return Issue(ELEMENT_ID_TAKEN, message, element=element_id)


def _element_id_issues(pages: Sequence[Any], masters: Sequence[Any]) -> list[Issue]:
    """Each control a press never reaches because one before it has its id."""
    seen: dict[Any, list[str | None]] = {}
    issues: list[Issue] = []
    places = [(page, element) for page in pages for element in _listed(page, "elements")]
    places += [(None, master) for master in masters]
    for page, element in places:
        element_id = _id(element)
        page_id = None if page is None else _id(page)
        issue = id_taken(element_id, seen.get(element_id, ()), master=page is None)
        if issue is not None:
            issues.append(replace(issue, page=page_id))
        seen.setdefault(element_id, []).append(page_id)
    return issues


# --- page ids ----------------------------------------------------------------


def page_id_issue(page_id: Any, other_ids: Collection[Any]) -> Issue | None:
    """Why a page cannot have ``page_id`` beside pages with ``other_ids``, or
    None when it can: another page has it, or it starts with ``$``."""
    if page_id in other_ids:
        return Issue(PAGE_ID_TAKEN, f"UI page '{page_id}' already exists", page=page_id)
    if names_no_page(page_id):
        moves = ", ".join(NAVIGATION_WORDS)
        return Issue(
            PAGE_ID_NAMES_NO_PAGE,
            f"UI page '{page_id}': a page id cannot start with '{NAVIGATION_PREFIX}', which "
            f"marks the panel's own moves ({moves}). Use lowercase letters, digits and "
            f"underscores.",
            page=page_id,
        )
    return None


def _page_id_issues(pages: Sequence[Any]) -> list[Issue]:
    issues: list[Issue] = []
    seen: list[Any] = []
    for page in pages:
        page_id = _id(page)
        issue = page_id_issue(page_id, seen)
        if issue is not None:
            issues.append(issue)
        seen.append(page_id)
    return issues


# --- arrangements ------------------------------------------------------------


def inherits_issue(layout_id: Any, target: Any, layout_ids: Iterable[Any]) -> Issue | None:
    """Why arrangement ``layout_id`` cannot inherit from ``target`` on a page
    whose arrangements are ``layout_ids``, or None when it can. One that
    inherits nothing (None) is fine; one that names nothing on the page, a
    blank among them, would silently become its own root, every box it does
    not store falling to the default."""
    if target is None:
        return None
    if target == layout_id:
        return Issue(INHERITS_SELF, f"Layout '{layout_id}' cannot inherit from itself", layout=layout_id)
    others = [other for other in dict.fromkeys(layout_ids) if other != layout_id]
    if target in others:
        return None
    available = f" Available: {', '.join(map(str, others))}" if others else ""
    return Issue(
        INHERITS_MISSING,
        f"Layout '{layout_id}': inherits '{target}', which is not an arrangement on this "
        f"page, so each control it does not place itself takes its default box.{available}",
        layout=layout_id,
    )


def orientation_issue(layout_id: Any, orientation: Any, others: Iterable[Any]) -> Issue | None:
    """Why arrangement ``layout_id`` cannot be the ``orientation`` one beside
    the arrangements ``others``, or None when it can: another of them is."""
    taken = next(
        (_id(other) for other in others
         if _orientation(other) == orientation and _id(other) != layout_id),
        None,
    )
    if taken is None:
        return None
    return Issue(
        ORIENTATION_TAKEN,
        f"Layout '{layout_id}': there is already a {orientation} arrangement ('{taken}').",
        layout=layout_id,
    )


def _orientation(layout: Any) -> Any:
    return _read(layout, "orientation", ORIENTATION_LEFT_OUT)


def inherits_loops(layouts: Sequence[Any]) -> list[Issue]:
    """Each loop the arrangements' ``inherits`` make, once, at its first
    member in page order. The renderers cycle-guard, so nothing crashes, but
    the arrangements on a loop stop inheriting. One that inherits itself is
    :func:`inherits_issue`'s."""
    by_id = {_id(layout): layout for layout in layouts}
    order = {layout_id: position for position, layout_id in enumerate(by_id)}
    found: set[frozenset] = set()
    issues: list[Issue] = []
    for layout in layouts:
        loop = _loop_from(_id(layout), lambda node: _id(by_id.get(node), "inherits"))
        if len(loop) < 2 or frozenset(loop) in found:
            continue
        found.add(frozenset(loop))
        first = min(loop, key=lambda node: order.get(node, 0))
        issues.append(Issue(
            INHERITS_LOOP,
            f"Layout inheritance loops: '{first}' eventually inherits from itself",
            layout=first,
        ))
    return issues


def _loop_from(start: Any, next_of: Any) -> list:
    """The loop the chain from ``start`` ends in (``next_of`` gives each
    link's next), empty when it ends."""
    path: list = []
    node = start
    while node and node not in path:
        path.append(node)
        node = next_of(node)
    return path[path.index(node):] if node else []


def _repeated_layout_ids(page_id: Any, layouts: Sequence[Any]) -> list[Issue]:
    counts: dict[Any, int] = {}
    for layout in layouts:
        layout_id = _id(layout)
        counts[layout_id] = counts.get(layout_id, 0) + 1
    return [
        Issue(LAYOUT_ID_REPEATED, f"Layout '{layout_id}' appears more than once on page '{page_id}'",
              layout=layout_id)
        for layout_id, count in counts.items() if count > 1
    ]


def layout_issues(page: Any) -> list[Issue]:
    """Everything wrong with one page's arrangements, on that page: an id
    used twice; then each arrangement's ``inherits`` and orientation, in page
    order, the orientation against the arrangements before it; then each
    loop."""
    page_id = _id(page)
    layouts = _listed(page, "layouts")
    ids = [_id(layout) for layout in layouts]
    issues = _repeated_layout_ids(page_id, layouts)
    for position, layout in enumerate(layouts):
        layout_id = _id(layout)
        found = (
            inherits_issue(layout_id, _id(layout, "inherits"), ids),
            orientation_issue(layout_id, _orientation(layout), layouts[:position]),
        )
        issues.extend(issue for issue in found if issue is not None)
    issues.extend(inherits_loops(layouts))
    return [replace(issue, page=page_id) for issue in issues]


# --- containers --------------------------------------------------------------


def parent_issue(
    element_id: Any, parent_id: Any, holders: Mapping[Any, Any], page_id: Any,
) -> Issue | None:
    """Why ``element_id`` cannot sit in ``parent_id`` on page ``page_id``,
    whose controls by id are ``holders``, or None when it can, or when it has
    none (None). The container must be a control on the page, a group, and
    neither the control itself nor inside it; a blank names no control."""
    if parent_id is None:
        return None
    holder = holders.get(parent_id)
    if holder is None:
        return Issue(
            PARENT_MISSING,
            f"Element '{element_id}': container '{parent_id}' is not an element on page '{page_id}'",
            page=page_id, element=element_id,
        )
    holder_type = _read(holder, "type")
    if holder_type != CONTAINER_TYPE:
        return Issue(
            PARENT_NOT_CONTAINER,
            f"Element '{element_id}': '{parent_id}' is a {holder_type}, not a container -- only "
            f"a {CONTAINER_TYPE} element can hold children",
            page=page_id, element=element_id,
        )
    if parent_id == element_id or _inside(holders, parent_id, element_id):
        return Issue(
            PARENT_LOOP,
            f"Element '{element_id}' cannot be placed inside itself or its own contents",
            page=page_id, element=element_id,
        )
    return None


def _inside(holders: Mapping[Any, Any], candidate: Any, ancestor: Any) -> bool:
    """Whether ``candidate`` sits somewhere inside ``ancestor``. Cycle-safe."""
    seen: set = set()
    cursor = holders.get(candidate)
    while cursor is not None and _id(cursor) not in seen:
        seen.add(_id(cursor))
        parent = _id(cursor, "parent")
        if parent == ancestor:
            return True
        cursor = holders.get(parent) if parent else None
    return False


def parent_issues(page: Any) -> list[Issue]:
    """Every control of one page whose container is not one it may sit in."""
    page_id = _id(page)
    elements = _listed(page, "elements")
    holders = {_id(element): element for element in elements}
    found = (
        parent_issue(_id(element), _id(element, "parent"), holders, page_id)
        for element in elements
    )
    return [issue for issue in found if issue is not None]


def master_parent_issue(master_id: Any, parent: Any) -> Issue | None:
    """Why a master cannot have ``parent`` as its container: it has none, so
    anything but None is refused."""
    if parent is None:
        return None
    return Issue(
        MASTER_PARENT,
        f"Master element '{master_id}': a master element is placed on the screen, not inside "
        f"a container, so it cannot have a 'parent'.",
        element=master_id,
    )


# --- the whole project -------------------------------------------------------


def page_issues(pages: Iterable[Any], masters: Iterable[Any] = ()) -> list[Issue]:
    """Everything the project's pages and masters hold that they may not.

    The page ids first, then the controls a press never reaches, then each
    page's arrangements, containers and bindings, page by page, then each
    master's container and bindings. A binding issue names its control.
    """
    pages = list(pages)
    masters = list(masters)
    issues = _page_id_issues(pages) + _element_id_issues(pages, masters)
    for page in pages:
        page_id = _id(page)
        issues += layout_issues(page) + parent_issues(page)
        for element in _listed(page, "elements"):
            issues += _placed_bindings(element, page_id)
    for master in masters:
        found = master_parent_issue(_id(master), _read(master, "parent"))
        issues += [found] if found is not None else []
        issues += _placed_bindings(master, None)
    return issues


def placed_controls(pages: Iterable[Any], masters: Iterable[Any] = ()) -> list[tuple[Any, Any, Any]]:
    """Every control, page by page and then the masters, as ``(page, id,
    control)`` with the page id and the control's id read as an
    :class:`Issue` places them (the page None for a master)."""
    placed = [(_id(page), _id(element), element) for page in pages for element in _listed(page, "elements")]
    return placed + [(None, _id(master), master) for master in masters]


def _placed_bindings(element: Any, page_id: Any) -> list[Issue]:
    """One control's binding issues, placed on it (on a master when
    ``page_id`` is None) and naming it."""
    bindings = _read(element, "bindings")
    if not isinstance(bindings, Mapping):
        return []
    element_id = _id(element)
    named = f"Element '{element_id}'" if page_id is not None else f"Master element '{element_id}'"
    return [
        replace(issue, message=f"{named}: {issue.message}", page=page_id, element=element_id)
        for issue in binding_issues(_as_dispatched(bindings))
    ]


def _as_dispatched(bindings: Mapping[str, Any]) -> Mapping[str, Any]:
    """``bindings`` read as the runtime runs them: each ``do`` interaction
    written as one action object is the list of one it runs
    (``ui_events``). A copy; the bindings are not changed."""
    do = bindings.get("do")
    if not isinstance(do, Mapping) or not any(isinstance(actions, Mapping) for actions in do.values()):
        return bindings
    listed = {name: [actions] if isinstance(actions, Mapping) else actions for name, actions in do.items()}
    return {**bindings, "do": listed}


# --- bindings ----------------------------------------------------------------

#: The flat slot names the show/do model retired. Any of them at the top of a
#: control's bindings means the retired model is being written.
LEGACY_FLAT_KEYS = frozenset((
    "press", "release", "hold", "change", "submit", "select", "route",
    "audio_route", "mute_route", "audio_mute_route",
    "value", "variable", "text", "feedback", "color", "items",
    "selected", "visible_when", "meter",
))

#: The operators a ``visible_when`` is told to use, by their own names.
VISIBLE_WHEN_OPS_TEXT = "eq, ne, gt, lt, gte, lte, truthy, falsy"

#: The press modes the panel knows, and the sentence naming them.
PRESS_MODES = frozenset(("tap", "toggle", "hold_repeat", "tap_hold"))
PRESS_MODES_TEXT = "tap, toggle, hold_repeat, tap_hold"

#: What each action needs besides its name.
ACTION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "macro": ("macro",),
    "device.command": ("device", "command"),
    "state.set": ("key",),
    "ui.navigate": ("page",),
    "script.call": ("function",),
    "event.emit": ("event",),
    "value_map": ("map",),
}


@cache
def interactions() -> frozenset[str]:
    """The ``do`` interactions: ``page_references.ACTION_SLOTS``."""
    from openavc.ui.page_references import ACTION_SLOTS

    return frozenset(ACTION_SLOTS)


@cache
def action_names() -> frozenset[str]:
    """The actions a ``do`` interaction may run: the runtime's own set,
    ``ui_events.DISPATCHED_ACTIONS``. The page move is ``ui.navigate`` here,
    as in a macro step; a control surface's ``navigate`` moves the deck's own
    pages and is not a panel binding."""
    from openavc.core.ui_events import DISPATCHED_ACTIONS

    return DISPATCHED_ACTIONS


def _shape(message: str, code: str = BINDING) -> Issue:
    return Issue(code, message)


def binding_issues(bindings: Mapping[str, Any]) -> list[Issue]:
    """Everything wrong with one control's bindings, the ``do`` half's before
    the ``show`` half's. A ``do`` action written as one object rather than a
    list is refused here; a door that takes one wraps it first. The retired
    flat model is the whole answer when any of its keys is written. A top-level
    key other than ``show`` and ``do`` is not an issue."""
    legacy = sorted(key for key in bindings if key in LEGACY_FLAT_KEYS)
    if legacy:
        return [Issue(
            LEGACY_BINDINGS,
            "bindings use the show/do model. Put interaction actions under 'do' (do.press, "
            "do.change, do.route, ...) and state reads under 'show' (show.value, show.look, "
            "show.visible_when, show.items). Remove top-level: " + ", ".join(legacy),
        )]
    issues: list[Issue] = []
    do = _binding_half(bindings, "do", issues)
    if do is not None:
        _check_do(do, issues)
    show = _binding_half(bindings, "show", issues)
    if show is not None:
        _check_show(show, issues)
    return issues


def _binding_half(bindings: Mapping[str, Any], name: str, issues: list[Issue]) -> Mapping | None:
    """``bindings[name]`` when it is an object; None when it is absent, or
    (the issue added) when it is anything else."""
    half = bindings.get(name)
    if half is None or isinstance(half, Mapping):
        return half
    issues.append(_shape(f"'{name}' must be an object, got {type(half).__name__}"))
    return None


def action_issue(action: Mapping[str, Any], path: str) -> Issue | None:
    """The first thing wrong with one action object at ``path``, or None."""
    action_type = action.get("action", "")
    if not action_type:
        return _shape(f"{path}: missing 'action' field", ACTION_UNNAMED)
    if not isinstance(action_type, str):
        return _shape(_not_an_action_name(action_type, path), ACTION_UNNAMED)
    if action_type not in action_names():
        return _shape(
            f"{path}: action type '{action_type}' is not valid. "
            f"Use: {', '.join(sorted(action_names()))}",
            ACTION_UNKNOWN,
        )
    for field in ACTION_REQUIRED_FIELDS.get(action_type, ()):
        if action.get(field) is None or action.get(field) == "":
            return _shape(f"{path}: {action_type} action requires '{field}'; without it the action does nothing")
    check = _ACTION_TYPE_CHECKS.get(action_type)
    return check(action, path) if check else None


def _not_an_action_name(action_type: Any, path: str) -> str:
    """The sentence for an ``action`` field that holds something other than a
    name. Holding another action object is a documented anti-pattern, and the
    nesting is invisible in a diff, so the nested action's name is said."""
    nested = action_type.get("action") if isinstance(action_type, Mapping) else None
    inner = f" You nested a '{nested}' action inside it." if isinstance(nested, str) else ""
    return (
        f"{path}: 'action' must be the action NAME, got "
        f"{type(action_type).__name__}.{inner} Write the fields flat on this "
        f"object -- {{\"action\": \"device.command\", \"device\": ..., "
        f"\"command\": ...}} -- not an action wrapped in another action."
    )


def _state_set_issue(action: Mapping[str, Any], path: str) -> Issue | None:
    """Device state is read-only: a ``state.set`` never writes a device's key.
    A device is driven with a ``device.command`` using ``$value``."""
    key = action.get("key", "")
    if isinstance(key, str) and key.startswith("device."):
        return Issue(
            DEVICE_STATE_WRITE,
            f"{path}: state.set cannot target a device key ('{key}'). Device state is "
            f"read-only: drive the device with a device.command action using $value instead.",
        )
    return None


def _value_map_issue(action: Mapping[str, Any], path: str) -> Issue | None:
    """Each option maps to an action, or a list of them, each checked as one.
    The first problem is the answer."""
    vmap = action.get("map")
    if not isinstance(vmap, Mapping):
        return _shape(f"{path}: value_map action requires 'map' to be an object")
    for option, mapped in vmap.items():
        for sub_path, sub in _mapped_actions(mapped, f"{path}.map[{option}]"):
            issue = action_issue(sub, sub_path)
            if issue is not None:
                return issue
    return None


def _mapped_actions(mapped: Any, path: str) -> list[tuple[str, Mapping]]:
    """The action objects one value_map option maps to, each with its path;
    anything else is skipped."""
    if isinstance(mapped, list):
        return [(f"{path}[{j}]", sub) for j, sub in enumerate(mapped) if isinstance(sub, Mapping)]
    return [(path, mapped)] if isinstance(mapped, Mapping) else []


#: What one action type is checked for beyond its required fields.
_ACTION_TYPE_CHECKS = {"state.set": _state_set_issue, "value_map": _value_map_issue}


def _check_do(do: Mapping[str, Any], issues: list[Issue]) -> None:
    """The ``do`` half: every interaction's actions, then the first press
    action's mode."""
    for interaction, actions in do.items():
        issues.extend(_interaction_issues(interaction, actions))
    first = _first_press_action(do.get("press"))
    if first is not None:
        _check_press_mode(first, issues)


def _interaction_issues(interaction: str, actions: Any) -> list[Issue]:
    if interaction not in interactions():
        return [_shape(
            f"do.{interaction}: not a valid interaction. Use: " + ", ".join(sorted(interactions()))
        )]
    if not isinstance(actions, list):
        return [_shape(
            f"do.{interaction} must be an array of action objects, got {type(actions).__name__}"
        )]
    found = (_action_item_issue(item, f"do.{interaction}[{i}]") for i, item in enumerate(actions))
    return [issue for issue in found if issue is not None]


def _action_item_issue(item: Any, path: str) -> Issue | None:
    if not isinstance(item, Mapping):
        return _shape(f"{path}: expected an action object, got {type(item).__name__}")
    return action_issue(item, path)


def _first_press_action(press: Any) -> Mapping | None:
    """The action a button's mode is read from: the first under ``do.press``."""
    if not isinstance(press, list) or not press:
        return None
    first = press[0]
    return first if isinstance(first, Mapping) else None


def _not_a_mode(mode: Any) -> bool:
    """A mode given that is not one: a name the panel does not know, or a list
    or object. None or empty is ``tap``."""
    if not mode:
        return False
    return not isinstance(mode, str) or mode not in PRESS_MODES


def _check_press_mode(first: Mapping[str, Any], issues: list[Issue]) -> None:
    """What the button's mode needs: a toggle its key and off action, a
    tap-and-hold its hold action."""
    mode = first.get("mode", "tap")
    if _not_a_mode(mode):
        issues.append(_shape(f"do.press[0]: mode '{mode}' is not valid. Use: {PRESS_MODES_TEXT}"))
    elif mode == "toggle":
        if not first.get("toggle_key"):
            issues.append(_shape(
                "do.press[0]: toggle mode requires 'toggle_key' (the State key to watch); without it "
                "the button acts as a tap"
            ))
        _check_mode_action(first, mode, "off_action", issues)
    elif mode == "tap_hold":
        _check_mode_action(first, mode, "hold_action", issues)


#: A mode's second action by its field: what the Builder calls it, and the
#: press that runs it.
_MODE_ACTIONS = {"off_action": "Off Action", "hold_action": "Long Press Action"}
_MODE_PRESSES = {"off_action": "press while on", "hold_action": "long press"}


def _check_mode_action(first: Mapping[str, Any], mode: str, field: str, issues: list[Issue]) -> None:
    """A mode's second action (``off_action``, ``hold_action``) is an action
    object, checked as an action unless it is empty."""
    action = first.get(field)
    if not isinstance(action, Mapping):
        issues.append(_shape(
            f"do.press[0]: {mode} mode requires '{field}' (the {_MODE_ACTIONS[field]}, an action object); "
            f"without it a {_MODE_PRESSES[field]} does nothing"
        ))
    elif action:
        issue = action_issue(action, f"do.press[0].{field}")
        if issue is not None:
            issues.append(issue)


def _check_show(show: Mapping[str, Any], issues: list[Issue]) -> None:
    """The ``show`` half: each slot written, an object checked by its slot's
    rule."""
    for slot, check in _SHOW_CHECKS:
        value = show.get(slot)
        if value is None:
            continue
        if isinstance(value, Mapping):
            check(value, issues)
        else:
            issues.append(_shape(f"show.{slot} must be an object, got {type(value).__name__}"))


def _check_shown_value(val: Mapping[str, Any], issues: list[Issue]) -> None:
    """``show.value``: the thing the control IS (numeric / selection / label text)."""
    source = val.get("source", "state")
    if source == "macro_progress":
        if not val.get("macro"):
            issues.append(_shape("show.value: macro_progress source requires 'macro'"))
    elif source in ("state", "conditional", ""):
        _check_state_read(val, issues)
    else:
        issues.append(_shape(
            f"show.value: source '{source}' is not valid. Use: state, macro_progress"
        ))


def _check_state_read(val: Mapping[str, Any], issues: list[Issue]) -> None:
    if not val.get("key"):
        issues.append(_shape("show.value: requires 'key' (the state key to read)", SHOW_WITHOUT_KEY))
    if "condition" in val:
        issues.extend(
            _shape(f"show.value: conditional text requires '{field}'")
            for field in ("text_true", "text_false") if field not in val
        )
    if val.get("write_back"):
        key = val.get("key", "")
        if not (isinstance(key, str) and key.startswith("var.")):
            issues.append(Issue(
                WRITE_BACK_NOT_VARIABLE,
                "show.value: write_back (two-way) is only valid for var.* keys. "
                f"'{key}' is read-only. To drive a device, add a do.change "
                "device.command using $value.",
            ))


def _check_items(itm: Mapping[str, Any], issues: list[Issue]) -> None:
    """``show.items``: list row population."""
    if not (itm.get("key") or itm.get("key_pattern")):
        issues.append(_shape(
            "show.items: requires 'key' or 'key_pattern' (the state providing items)", SHOW_WITHOUT_KEY,
        ))


#: The forms a look takes, in the order they are looked for. Only the first one
#: present is checked, and it must be an object.
_LOOK_FORMS = {
    "states": "show.look.states must be an object mapping state values to style props",
    "condition": "show.look.condition must be an object (e.g. {\"equals\": \"on\"})",
    "map": "show.look.map must be an object mapping values to colors",
    "style_map": "show.look.style_map must be an object mapping option values to styles",
}


def _check_look(look: Mapping[str, Any], issues: list[Issue]) -> None:
    """``show.look``: state-driven appearance (feedback / LED color map /
    per-option style)."""
    if not look.get("key"):
        issues.append(_shape("show.look: missing 'key' (state key to watch)", SHOW_WITHOUT_KEY))
    form = next((name for name in _LOOK_FORMS if name in look), None)
    if form is None:
        return
    if not isinstance(look[form], Mapping):
        issues.append(_shape(_LOOK_FORMS[form]))
    elif form == "states":
        issues.extend(_nested_style_issues(look["states"]))


def _nested_style_issues(states: Mapping[str, Any]) -> list[Issue]:
    """A state's properties are flat; the first state nesting them inside a
    'style' object is named."""
    for state_name, props in states.items():
        if isinstance(props, Mapping) and "style" in props:
            return [_shape(
                f"show.look.states.{state_name}: properties (label, bg_color, etc.) "
                f"must be flat, not nested inside 'style'"
            )]
    return []


def _check_visible_when(vw: Mapping[str, Any], issues: list[Issue]) -> None:
    """``show.visible_when``: a single condition, or any:[] (OR) / all:[] (AND)
    groups of them. A group that is not a list has nothing to check."""
    group = "any" if "any" in vw else ("all" if "all" in vw else None)
    if group is None:
        issues.extend(_condition_issues(vw, "show.visible_when", " (state key to evaluate)"))
        return
    conditions = vw[group] if isinstance(vw[group], list) else []
    for i, cond in enumerate(conditions):
        if isinstance(cond, Mapping):
            issues.extend(_condition_issues(cond, f"show.visible_when.{group}[{i}]"))


def _condition_issues(cond: Mapping[str, Any], path: str, key_note: str = "") -> list[Issue]:
    """A condition's key, and its operator by any spelling the panel reads
    (``condition_eval.canonical_operator``); a blank one reads as ``eq``."""
    issues = []
    if not cond.get("key"):
        issues.append(_shape(f"{path}: missing 'key'{key_note}"))
    op = cond.get("operator")
    if op and canonical_operator(op) is None:
        issues.append(_shape(
            f"{path}: operator '{op}' is not valid, so the panel reads that condition as false. "
            f"Use: {VISIBLE_WHEN_OPS_TEXT}"
        ))
    return issues


#: The ``show`` slots in the order they are checked, each with its check.
_SHOW_CHECKS = (
    ("value", _check_shown_value),
    ("items", _check_items),
    ("look", _check_look),
    ("visible_when", _check_visible_when),
)
