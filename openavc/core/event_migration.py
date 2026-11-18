"""
The project format's 0.18.0 step: a project's own events move out of the
platform's namespace.

From 0.18.0 a project cannot send an event named ``system.<anything>``: a
macro's Emit Event step, a control's event action and a script's
``events.emit`` refuse one (``core/event_bus.check_project_emit``), because
``system.`` is where the platform says something happened to it. Before that,
the Macro editor suggested three such names for a project's own events (Room
occupied, Room vacant, System panic), so a project may send one and listen for
it. This step renames each such event a project sends, and each Event trigger
that could only hear one, to the same name under ``custom.``, so the wiring
keeps working:

- a macro's Emit Event step, including one inside a conditional's branches;
- a control's Emit Event action, wherever an action can sit on a page or a
  master element: an interaction's list, a press mode's off and hold actions,
  a value map's per-option actions, and a matrix row's own route actions;
- an Event trigger whose pattern is under ``system.`` and matches none of the
  events the platform sends (``system.panic``, ``system.room_*``). A pattern
  that also hears the platform (``system.*``, ``system.started``) is left;
  when it heard one of the events this step renames, the note says which.

A step or a control that sends one of the platform's own events
(``system.started``, ``system.stopping``, ``system.project.reloaded``) is left
as written, since it has no name of its own to move to; it is now refused, and
the note says so. A script is code and is not rewritten: the script check marks
a line that sends a ``system.`` name, or listens for one the platform never
sends, with the name to use. Anything outside the project listening for an old
name (a Node-RED flow, another system on the API) is told by the note.

The names are the step's own, as they stood at 0.18.0, for the reason
``core/project_migration.py`` gives: a step says what it did to the projects
of its day. Each rename is a note for the Programmer's notice.
"""

from collections.abc import Iterator
from fnmatch import fnmatchcase
from typing import NamedTuple

from openavc.core.box_migration import element_places_0_17
from openavc.core.project_update import MigrationNotes

PREFIX_0_18 = "system."
OWN_PREFIX_0_18 = "custom."
# The `system.` events the platform sent at 0.18.0.
PLATFORM_EVENTS_0_18 = frozenset({"system.started", "system.stopping", "system.project.reloaded"})

# Where a press mode hangs another action off the first one.
_NESTED_ACTIONS_0_18 = ("off_action", "hold_action")
_OUTSIDE_0_18 = "A Node-RED flow or another system listening for the old name needs the new one."


class _Place0_18(NamedTuple):
    """Where a note is said: its location, and the page and control it is on."""

    location: str
    page_id: str = ""
    element_id: str = ""


class _Renames0_18:
    """One load's renames: the notes it writes, and each old name it moved
    (old -> new), which a trigger left as written may have heard."""

    def __init__(self, notes: MigrationNotes):
        self.notes = notes
        self.moved: dict[str, str] = {}

    def changed(self, message: str, place: _Place0_18) -> None:
        self.notes.changed(message, place.location, page_id=place.page_id, element_id=place.element_id)

    def found(self, message: str, place: _Place0_18) -> None:
        self.notes.found(message, place.location, page_id=place.page_id, element_id=place.element_id)


def migrate_0_17_to_0_18(data: dict, notes: MigrationNotes | None = None) -> dict:
    """Migrate from 0.17.0 to 0.18.0: the project's own ``system.`` events,
    renamed in place to ``custom.`` (this module's docstring says which and
    where), each rename a note. What is sent is renamed first, so a trigger
    left as written can say which of them it no longer hears."""
    renames = _Renames0_18(MigrationNotes() if notes is None else notes)
    macros = _dicts_0_18(data.get("macros"))
    _rename_steps_0_18(macros, renames)
    _rename_controls_0_18(data.get("ui"), renames)
    _rename_triggers_0_18(macros, renames)
    data["openavc_version"] = "0.18.0"
    return data


def _rename_steps_0_18(macros: list[dict], renames: _Renames0_18) -> None:
    for macro in macros:
        place = _Place0_18(_macro_place_0_18(macro))
        for label, step in _steps_0_18(macro.get("steps"), "Step"):
            _rename_emit_0_18(step, f"{label} (Emit Event)", renames, place)


def _rename_controls_0_18(ui, renames: _Renames0_18) -> None:
    places = element_places_0_17(ui)
    for el in _elements_0_18(ui):
        page, page_id = places.get(id(el), ("", ""))
        element_id = str(el.get("id", ""))
        place = _Place0_18(f"{page} > {element_id}", page_id, element_id)
        for action in _element_actions_0_18(el):
            _rename_emit_0_18(action, "Emit Event action", renames, place)


def _rename_triggers_0_18(macros: list[dict], renames: _Renames0_18) -> None:
    for macro in macros:
        for trigger in _dicts_0_18(macro.get("triggers")):
            place = _Place0_18(f"{_macro_place_0_18(macro)} > {trigger.get('id', '')}")
            _rename_trigger_0_18(trigger, place, renames)


def _macro_place_0_18(macro: dict) -> str:
    return f"Macros > {macro.get('name') or macro.get('id', '')}"


def _elements_0_18(ui) -> list[dict]:
    """Every page element, then every master element; anything that is not a
    dict, and a missing or null list, reads as nothing."""
    if not isinstance(ui, dict):
        return []
    elements = [el for page in _dicts_0_18(ui.get("pages")) for el in _dicts_0_18(page.get("elements"))]
    return elements + _dicts_0_18(ui.get("master_elements"))


def _in_namespace_0_18(name) -> bool:
    return isinstance(name, str) and name.strip().casefold().startswith(PREFIX_0_18)


def _own_name_0_18(name: str) -> str:
    return OWN_PREFIX_0_18 + name.strip()[len(PREFIX_0_18):]


def _rename_emit_0_18(holder: dict, what: str, renames: _Renames0_18, place: _Place0_18) -> None:
    """Rename ``holder``'s event when it is a project's own under ``system.``,
    and say so at ``place``; a platform event it sends is said and left."""
    old = holder.get("event") if holder.get("action") == "event.emit" else None
    if not _in_namespace_0_18(old):
        return
    if old.strip().casefold() in PLATFORM_EVENTS_0_18:
        renames.found(
            f"{what} sends '{old}', which only OpenAVC sends, so it is no longer sent. "
            f"Remove it, or name this project's own event under '{OWN_PREFIX_0_18}'.",
            place,
        )
        return
    new = holder["event"] = renames.moved[old] = _own_name_0_18(old)
    renames.changed(
        f"{what} now sends '{new}' in place of '{old}': names starting with "
        f"'{PREFIX_0_18}' are OpenAVC's own. {_OUTSIDE_0_18}",
        place,
    )


def _rename_trigger_0_18(trigger: dict, place: _Place0_18, renames: _Renames0_18) -> None:
    pattern = trigger.get("event_pattern")
    if trigger.get("type") != "event" or not _in_namespace_0_18(pattern):
        return
    if _only_a_project_hears_0_18(pattern):
        trigger["event_pattern"] = _own_name_0_18(pattern)
        renames.changed(
            f"Listens for '{trigger['event_pattern']}' in place of '{pattern}': a project's own "
            f"events are named under '{OWN_PREFIX_0_18}'.",
            place,
        )
        return
    folded = pattern.strip().casefold()
    for old, new in renames.moved.items():
        if fnmatchcase(old.strip().casefold(), folded):
            renames.found(
                f"Listens for '{pattern}', which no longer hears this project's '{old}': it is "
                f"sent as '{new}' now. Add an Event trigger for '{new}' if this macro should "
                f"still run on it.",
                place,
            )


def _only_a_project_hears_0_18(pattern) -> bool:
    """A pattern under ``system.`` that matches none of the platform's events."""
    if not _in_namespace_0_18(pattern):
        return False
    folded = pattern.strip().casefold()
    return not any(fnmatchcase(event, folded) for event in PLATFORM_EVENTS_0_18)


def _steps_0_18(steps, label: str) -> Iterator[tuple[str, dict]]:
    """Every step, a conditional's branches included, with the label the
    Macro editor numbers it by ("Step 2, Then step 1")."""
    for i, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        here = f"{label} {i + 1}"
        yield here, step
        for branch, word in (("then_steps", "Then step"), ("else_steps", "Else step")):
            yield from _steps_0_18(step.get(branch), f"{here}, {word}")


def _element_actions_0_18(el: dict) -> Iterator[dict]:
    """Every action dict a control can run, as the 0.18.0 runtime reached them:
    its interactions' actions, then each matrix row's own route actions."""
    bindings = el.get("bindings")
    do = bindings.get("do") if isinstance(bindings, dict) else None
    for binding in (do.values() if isinstance(do, dict) else ()):
        yield from _action_list_0_18(binding)
    config = el.get("matrix_config")
    destinations = config.get("destinations") if isinstance(config, dict) else None
    for entry in _dicts_0_18(destinations):
        yield from _action_list_0_18(_dicts_0_18(entry.get("route")))


def _action_list_0_18(binding) -> Iterator[dict]:
    """A list of actions, or the single one an interaction may hold, each
    with what nests inside it."""
    for action in binding if isinstance(binding, list) else [binding]:
        yield from _action_tree_0_18(action)


def _action_tree_0_18(action) -> Iterator[dict]:
    if not isinstance(action, dict):
        return
    yield action
    for field in _NESTED_ACTIONS_0_18:
        yield from _action_tree_0_18(action.get(field))
    mapped = action.get("map") if action.get("action") == "value_map" else None
    for value in mapped.values() if isinstance(mapped, dict) else ():
        yield from _action_tree_0_18(value)


def _dicts_0_18(items) -> list[dict]:
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
