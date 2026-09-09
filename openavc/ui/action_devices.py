"""Which devices a control's ``do`` actions can actually reach.

The panel dims a control whose device is unreachable, and until this existed it
could only do that for controls that DISPLAY something: the mark is driven by
``show`` bindings, and a button carrying only ``do`` names no state key, so it
rendered exactly like a working one right up to the press that was refused.
That is the wrong way round — the action-only button is the one guaranteed to
fail, and it was the one with no warning on it.

Answering it needs the project, not the page: a press runs a macro, which runs
another macro, which commands a group, which is a list of device ids somewhere
else entirely. So it is resolved HERE, on the server, and shipped in the
resolved UI definition (``Engine.panel_ui``) as ``action_devices`` per element.
The panel reads a list and does no walking of its own. That is the same call
``resolve_ui`` already made for the matrix, and for the same reason: the panel
and the Builder canvas are one renderer in two places, so a copy each would be
two copies of one translation, drifting.

Relationship to the other two walks over this material
------------------------------------------------------
``core/device_references.py`` asks the REVERSE question -- given a device, what
still points at it -- for the delete dialog. It therefore visits every macro
independently and never needs to follow a ``macro`` step into another macro.
This one starts from one element and must, so the recursion (and its cycle
guard) lives here rather than being shared. ``ui/page_references.py`` walks the
same ``do`` slots to ask whether what they name EXISTS; this asks what they
reach. Three questions, three answers, one traversal shape -- kept together in
the comments below so a fourth is not invented by accident.

What is deliberately NOT resolved
---------------------------------
``script.call``. A script can command anything at any time, from a name it
computed. There is no static answer, and a wrong one here would mark a control
unavailable over a device the script may not touch -- or, worse, leave it
unmarked because the guess came back empty. A control whose only action is a
script is left unmarked, which is what it was before.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: The per-element key the resolved definition carries. Derived, not authored:
#: ``/api/project`` does not go through ``panel_ui``, so this never reaches the
#: Builder's save path and cannot be written back into a project file.
ACTION_DEVICES_KEY = "action_devices"

#: How deep a macro may call another macro before we stop following. Cycles are
#: already handled by the seen-set; this is the guard for a legal but absurd
#: chain, so one pathological project cannot make every panel definition
#: expensive to build.
_MAX_MACRO_DEPTH = 12


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _actions(container: Any) -> list[Mapping[str, Any]]:
    """Normalise an action slot to a list.

    An action list written as a single object rather than an array is legal in
    the wild (``page_references`` reports it as a shape finding but the engine
    still runs it), so both forms are walked here.
    """
    if isinstance(container, Mapping):
        return [container]
    if isinstance(container, (list, tuple)):
        return [a for a in container if isinstance(a, Mapping)]
    return []


def _group_members(groups: Mapping[str, Iterable[str]], group_id: Any) -> list[str]:
    if not isinstance(group_id, str):
        return []
    return [d for d in groups.get(group_id, ()) if isinstance(d, str) and d]


def _devices_in_steps(
    steps: Any,
    macros: Mapping[str, Any],
    groups: Mapping[str, Iterable[str]],
    seen: set[str],
    depth: int,
    out: set[str],
) -> None:
    """Collect device ids from a macro's step list, branches and calls included."""
    for step in _actions(steps):
        action = step.get("action")
        if action == "device.command":
            device = step.get("device")
            if isinstance(device, str) and device:
                out.add(device)
        elif action == "group.command":
            out.update(_group_members(groups, step.get("group")))
        elif action == "macro":
            _devices_in_macro(step.get("macro"), macros, groups, seen, depth + 1, out)
        # A conditional's branches are steps like any others. Its condition can
        # READ a device key, which is not the same as acting on one and is not
        # collected: a control is marked for what pressing it cannot do.
        _devices_in_steps(step.get("then_steps"), macros, groups, seen, depth, out)
        _devices_in_steps(step.get("else_steps"), macros, groups, seen, depth, out)


def _devices_in_macro(
    macro_id: Any,
    macros: Mapping[str, Any],
    groups: Mapping[str, Iterable[str]],
    seen: set[str],
    depth: int,
    out: set[str],
) -> None:
    if not isinstance(macro_id, str) or not macro_id or depth > _MAX_MACRO_DEPTH:
        return
    if macro_id in seen:
        return  # a macro that calls itself, directly or round a ring
    seen.add(macro_id)
    macro = _mapping(macros.get(macro_id))
    if macro is not None:
        _devices_in_steps(macro.get("steps"), macros, groups, seen, depth, out)


def _devices_in_action(
    action: Mapping[str, Any],
    macros: Mapping[str, Any],
    groups: Mapping[str, Iterable[str]],
    out: set[str],
) -> None:
    name = action.get("action")
    if name == "device.command":
        device = action.get("device")
        if isinstance(device, str) and device:
            out.add(device)
    elif name == "group.command":
        out.update(_group_members(groups, action.get("group")))
    elif name == "macro":
        _devices_in_macro(action.get("macro"), macros, groups, set(), 0, out)
    elif name == "value_map":
        # The engine runs each mapped entry as an action of its own, so the
        # same question applies one level down. A select whose options command
        # three switchers reaches all three.
        for inner in (_mapping(action.get("map")) or {}).values():
            nested = _mapping(inner)
            if nested is not None:
                _devices_in_action(nested, macros, groups, out)


def element_action_devices(
    element: Mapping[str, Any],
    macros: Mapping[str, Any],
    groups: Mapping[str, Iterable[str]],
) -> list[str]:
    """Sorted device ids every ``do`` slot on ``element`` can reach.

    Sorted rather than insertion-ordered so the resolved definition is stable
    between builds: it is compared and cached, and a set's iteration order
    would make an unchanged page look changed.
    """
    bindings = _mapping(element.get("bindings"))
    do_map = _mapping(bindings.get("do")) if bindings else None
    if not do_map:
        return []
    out: set[str] = set()
    for slot in do_map.values():
        for action in _actions(slot):
            _devices_in_action(action, macros, groups, out)
    return sorted(out)


def annotate_action_devices(
    ui: Any,
    macros: Iterable[Mapping[str, Any]] | None,
    device_groups: Iterable[Mapping[str, Any]] | None,
) -> Any:
    """Stamp ``action_devices`` onto every element of a dumped UI definition.

    Mutates and returns the dict it is given, matching ``resolve_ui``: callers
    hand it a fresh ``model_dump``, never the live project. Elements that reach
    no device are left without the key rather than carrying an empty list --
    absent and empty mean the same thing to the renderer, and most elements on
    most pages reach nothing.
    """
    if not isinstance(ui, dict):
        return ui
    macro_map = {
        m["id"]: m
        for m in (macros or ())
        if isinstance(m, Mapping) and isinstance(m.get("id"), str)
    }
    group_map = {
        g["id"]: g.get("device_ids") or ()
        for g in (device_groups or ())
        if isinstance(g, Mapping) and isinstance(g.get("id"), str)
    }

    def stamp(element: Any) -> None:
        if not isinstance(element, dict):
            return
        devices = element_action_devices(element, macro_map, group_map)
        if devices:
            element[ACTION_DEVICES_KEY] = devices

    for page in ui.get("pages") or ():
        if isinstance(page, dict):
            for element in page.get("elements") or ():
                stamp(element)
    for element in ui.get("master_elements") or ():
        stamp(element)
    return ui
