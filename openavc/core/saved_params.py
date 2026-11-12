"""Secret command parameters saved in a macro step or a control's action.

A command parameter the device's driver marks secret (``drivers/spec.py``
``is_secret_setting``: a stream key, a pairing PIN, a parameter named
``password``) is kept in this system's login file, the way a device login is
(``core/device_logins.py``), when somebody saves it in a macro step or a
control's action. The step keeps a marker in its place that names the saved
value: ``<saved on this system #`` and 16 hex digits and ``>``. So every copy
of the project (a backup, an export, a template, what the cloud keeps, what a
panel is sent, what the AI reads) says a value is saved without carrying it.

The marker names its value because nothing else names a step. A macro step
has no id, a conditional nests its own lists, a control's interaction holds a
list of actions, and a toggle's off action, a hold's action and a matrix row's
route sit inside those. Whatever moves a step (a reorder, a duplicate, a copy
into another project, the AI writing a macro back as it read it) carries its
parameters as they are, and the name goes with them. Two steps holding the
same marker share one saved value; saving a new one in either gives that step
a new name, so the other keeps the old value.

How a value gets here
---------------------
The Programmer never puts a typed value in the project: its box sends the
value on its own (``POST /api/saved-params``) and puts the marker it gets back
in the step. Every other door (the AI's macro and control writers, a project
loaded or imported with one in the clear) is caught where every project comes
in: ``mark_clear`` gives each secret parameter held in the clear a new name,
and ``device_logins.move_logins`` files the values before the marker goes in.
A parameter whose value starts with ``$`` reads state when it runs and is not
a saved value.

How it is sent
--------------
A step or an action tells ``DeviceManager.send_command`` which of its own
parameters held a marker (``saved_kwargs``). The fill is in
``core/command_gates.py`` (``fill_saved_params``), against the live driver's
declaration: a value is filled in only for a parameter the step itself saved
and the driver marks secret. Any marker still in the parameters then is
refused before anything is sent, so a marker sent from any other door, copied
into a parameter that is not secret, reached through a variable, or with
nothing saved behind it never reaches a device, and the device is never sent
a marker as its key. The log, a simulated press and a step's progress keep the
marker: they are handed the parameters before the fill.

Which parameters are secret is read off the driver installed for each device
(a group step: any member's). A driver that is not installed says nothing, so
its steps keep their values until it is.

Stdlib and the driver registry only: ``core/device_logins.py`` imports this.
"""

from __future__ import annotations

import copy
import re
import secrets as _secrets
from collections.abc import Callable, Collection, Iterator
from typing import Any

from openavc.drivers.registry import get_driver_class
from openavc.drivers.spec import secret_settings

#: How every saved-on-this-system marker starts: a device login's
#: (``device_logins.SAVED_LOGIN``) and a saved parameter's.
SAVED_PREFIX = "<saved on this system"
MARKER_PREFIX = f"{SAVED_PREFIX} #"
_MARKER = re.compile(r"<saved on this system #([0-9a-f]{16})>")

#: The one field each saved value is filed under in the login file.
FIELD = "value"

#: The step and action types that send a device command with parameters.
COMMAND_ACTIONS = ("device.command", "group.command")

#: The project sections that hold steps and actions.
_SECTIONS = ("macros", "ui")


def marker(name: str) -> str:
    """What a parameter holds when the value named ``name`` is saved here."""
    return f"{MARKER_PREFIX}{name}>"


def new_name() -> str:
    """A name for a value about to be saved."""
    return _secrets.token_hex(8)


def name_of(value: Any) -> str | None:
    """The name ``value`` gives, when it is a saved parameter's marker."""
    if not isinstance(value, str):
        return None
    found = _MARKER.fullmatch(value)
    return found.group(1) if found else None


def is_marker(value: Any) -> bool:
    """Whether ``value`` is a saved parameter's marker."""
    return name_of(value) is not None


def is_any_marker(value: Any) -> bool:
    """Whether ``value`` is a saved-on-this-system marker of either kind."""
    return isinstance(value, str) and value.startswith(SAVED_PREFIX) and value.endswith(">")


def saved_names(params: Any) -> frozenset[str]:
    """The parameters in ``params`` (as a step or an action stores them) that
    hold a saved parameter's marker."""
    if not isinstance(params, dict):
        return frozenset()
    return frozenset(str(key) for key, value in params.items() if is_marker(value))


def filler(saved: Collection[str], lookup: Callable[[str], str | None]) -> Callable[[str, Any], str | None]:
    """What fills a parameter as it is sent: the value saved here under its
    marker's name, for a parameter the sending step saved (``saved``), and
    None for any other (``command_gates.fill_saved_params``)."""

    def fill(key: str, value: Any) -> str | None:
        name = name_of(value)
        return lookup(name) if name is not None and key in saved else None

    return fill


def not_saved_sentence(label: str, command_label: str) -> str:
    """Why a command holding a saved value's marker cannot run on this
    system: the refusal at the send and the Programmer's lint say it alike."""
    return (
        f"{label} is not saved on this system. Enter it in the macro step or control "
        f"that sends {command_label}."
    )


def saved_kwargs(params: Any) -> dict[str, Any]:
    """What a step or an action adds to ``send_command`` for the parameters it
    stores (``params``, before ``$`` resolution): nothing, unless one holds a
    saved value's marker."""
    names = saved_names(params)
    return {"saved": names} if names else {}


# --- where the parameters are ---------------------------------------------------


def _get(node: Any, key: str) -> Any:
    return node.get(key) if isinstance(node, dict) else getattr(node, key, None)


def _children(node: Any) -> list[Any]:
    """What ``node`` holds: a dict's values, a list's items, a model's fields."""
    if isinstance(node, dict):
        return list(node.values())
    if isinstance(node, (list, tuple)):
        return list(node)
    if not hasattr(type(node), "model_fields"):
        return []
    extra = getattr(node, "__pydantic_extra__", None) or {}
    return [getattr(node, name, None) for name in type(node).model_fields] + list(extra.values())


def _walk(node: Any) -> Iterator[Any]:
    """Every step or action under ``node`` (a model, a dict or a list, at any
    depth) that sends a device command with parameters."""
    if isinstance(node, (dict, list, tuple)) or hasattr(type(node), "model_fields"):
        if _get(node, "action") in COMMAND_ACTIONS and isinstance(_get(node, "params"), dict):
            yield node
    for child in _children(node):
        yield from _walk(child)


def _commands(project: Any) -> Iterator[Any]:
    for section in _SECTIONS:
        yield from _walk(_get(project, section))


def names_in(project: Any) -> set[str]:
    """The name of every saved value the steps and actions of ``project`` (a
    ProjectConfig, or a project file read as JSON) hold a marker for."""
    names: set[str] = set()
    for holder in _commands(project):
        names |= {name for value in _get(holder, "params").values() if (name := name_of(value))}
    return names


# --- moving values held in the clear ------------------------------------------------


def _drivers(project: Any) -> dict[str, str]:
    """Each device's driver, by device id."""
    pairs = ((_get(d, "id"), _get(d, "driver")) for d in _get(project, "devices") or [])
    return {device_id: driver for device_id, driver in pairs if isinstance(device_id, str) and isinstance(driver, str)}


def _groups(project: Any) -> dict[str, list[str]]:
    """Each group's members, by group id."""
    pairs = ((_get(g, "id"), _get(g, "device_ids")) for g in _get(project, "device_groups") or [])
    return {
        group_id: [m for m in members if isinstance(m, str)]
        for group_id, members in pairs if isinstance(group_id, str) and isinstance(members, list)
    }


def command_secrets(driver_id: Any, command: Any) -> frozenset[str]:
    """The parameters of ``command`` the installed driver ``driver_id`` marks
    secret; none for a driver that is not installed."""
    cls = get_driver_class(driver_id) if isinstance(driver_id, str) else None
    commands = (getattr(cls, "DRIVER_INFO", None) or {}).get("commands") if cls is not None else None
    definition = commands.get(command) if isinstance(commands, dict) else None
    return secret_settings(definition.get("params")) if isinstance(definition, dict) else frozenset()


def _held_in_the_clear(value: Any) -> bool:
    return isinstance(value, str) and value != "" and not value.startswith("$") and not is_any_marker(value)


class _Secrets:
    """Which parameters of a step or an action are secret, in one project."""

    def __init__(self, project: Any) -> None:
        self._drivers, self._groups = _drivers(project), _groups(project)

    def clear_in(self, holder: Any) -> list[str]:
        """The secret parameters ``holder`` holds in the clear, in its order."""
        if _get(holder, "action") == "device.command":
            targets = [_get(holder, "device")]
        else:
            targets = self._groups.get(_get(holder, "group"), [])
        command = _get(holder, "command")
        names: set[str] = set()
        for device_id in targets:
            names |= command_secrets(self._drivers.get(device_id), command)
        params = _get(holder, "params")
        return [key for key in params if key in names and _held_in_the_clear(params[key])]


def mark_clear(project: Any) -> tuple[Any, dict[str, str]]:
    """``project`` (a ProjectConfig, or a project file read as JSON) with each
    secret parameter its steps and actions hold in the clear replaced by a new
    marker, and the values by their new names. ``project`` itself and nothing
    when none is held in the clear. ``project`` is never changed: the caller
    files the values before taking the copy."""
    judge = _Secrets(project)
    if not any(judge.clear_in(holder) for holder in _commands(project)):
        return project, {}
    marked = project.model_copy(deep=True) if hasattr(project, "model_copy") else copy.deepcopy(project)
    values: dict[str, str] = {}
    for holder in _commands(marked):
        params = _get(holder, "params")
        for key in judge.clear_in(holder):
            name = new_name()
            values[name] = params[key]
            params[key] = marker(name)
    return marked, values


def put_back(project: dict[str, Any], values: dict[str, str]) -> bool:
    """Replace each marker in ``project``'s steps and actions (a project file
    read as JSON, changed in place) by its value in ``values``; a marker with
    none stays. True when anything changed."""
    changed = False
    for holder in _commands(project):
        params = _get(holder, "params")
        for key, value in list(params.items()):
            name = name_of(value)
            if name is not None and name in values:
                params[key] = values[name]
                changed = True
    return changed
