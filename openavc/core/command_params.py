"""Whether a written-down device command can actually RUN, asked before it is sent.

The other validators answer "does this name resolve" -- is there a device called
that, does its driver have a command called that. This one answers the question
after it: the command exists, it is spelled right, and it still cannot run,
because the driver declares a parameter as required and nothing filled it in.

That gap is not academic. A fader bound to ``set_fader`` with ``params: {}``
saved cleanly, the Builder's Validate answered "No Issues", the macro editor's
"won't run as built" banner cleared the moment a command was picked, and the
runtime refused every press with ``'set_fader': 'channel' is required``. Three
authoring surfaces agreed the control was finished and the device disagreed --
and Validate is the check an integrator runs before handing a room over.

One rule, one home
------------------
The rule itself is :func:`openavc.drivers.base.missing_required_params`, next to
the runtime gate that enforces it, so a static "this is fine" and a live refusal
cannot come from two different readings of the same driver. What this module
adds is the lookup around it: which driver, which command, and -- for a group --
which devices the step actually fans out to.

Everything returns None for "no opinion", and that is load-bearing rather than
defensive. A device that is disabled, not connected yet, or backed by a driver
that is not installed declares nothing, and a page or a macro is very often
authored before the hardware is on the bench. A gate that turned an absent
driver into a complaint would fire on every project written in advance, which is
how a warning stops being read.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from openavc.drivers.base import missing_required_params
from openavc.drivers.command_files import returns_file_sentence, takes_file_sentence
from openavc.drivers.param_labels import param_label
from openavc.drivers.spec import file_param_names
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: The step/binding actions that send a driver command and so carry its params.
#:
#: ``group.command`` is a macro step only -- a panel binding cannot dispatch one
#: (``core/ui_events.DISPATCHED_ACTIONS``) -- but the rule is the same one, so it
#: is answered here rather than in a second place that would drift.
COMMAND_ACTIONS = frozenset({"device.command", "group.command"})


def _declared_command(devices: Any, device_id: str, command: str) -> Mapping[str, Any] | None:
    """A command's declaration on a device's live driver, or None for no
    opinion.

    Never raises: nothing advisory may cost a save, a lint request or a UI
    write.
    """
    if devices is None or not device_id or not command:
        return None
    try:
        driver = devices.get_driver(device_id)
        if driver is None:
            return None
        commands = (getattr(driver, "DRIVER_INFO", None) or {}).get("commands")
        if not isinstance(commands, dict):
            return None
        definition = commands.get(command)
        # A command the driver does not have is a different complaint and
        # somebody else already makes it; saying a parameter is missing from a
        # command that does not exist would bury it.
        return definition if isinstance(definition, dict) else None
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug(
            "Could not resolve '%s' on '%s'", command, device_id, exc_info=True
        )
        return None


def _declared_params(devices: Any, device_id: str, command: str) -> Mapping[str, Any] | None:
    """A command's declared parameter schema, or None for no opinion."""
    definition = _declared_command(devices, device_id, command)
    if definition is None:
        return None
    params = definition.get("params")
    return params if isinstance(params, dict) else {}


def missing_params(
    action: Mapping[str, Any],
    *,
    devices: Any,
    groups: Mapping[str, Sequence[str]] | None = None,
) -> list[str] | None:
    """The required parameters this action names a command for and leaves unset.

    None when nothing can be said: a different action, no device or command
    chosen yet, a driver that is not loaded, or a command that driver does not
    declare.

    A ``group.command`` is checked against every member whose driver answers,
    and the names are UNIONED rather than intersected. The runtime fans the step
    out and refuses it per device, so a parameter one member requires is a
    parameter that step fails on -- reporting only what every member requires
    would stay silent about a step that half works.
    """
    if not isinstance(action, Mapping):
        return None
    name = action.get("action")
    if name not in COMMAND_ACTIONS:
        return None
    command = action.get("command")
    if not isinstance(command, str) or not command:
        return None
    params = action.get("params")

    if name == "device.command":
        device_id = action.get("device")
        if not isinstance(device_id, str):
            return None
        defs = _declared_params(devices, device_id, command)
        return None if defs is None else missing_required_params(defs, params)

    group_id = action.get("group")
    if not isinstance(group_id, str) or not group_id or not groups:
        return None
    members = groups.get(group_id)
    if not isinstance(members, (list, tuple)):
        return None
    found: list[str] = []
    answered = False
    for member in members:
        defs = _declared_params(devices, str(member), command)
        if defs is None:
            continue
        answered = True
        for missing in missing_required_params(defs, params):
            if missing not in found:
                found.append(missing)
    return found if answered else None


def file_command_reason(
    action: Mapping[str, Any],
    *,
    devices: Any,
    groups: Mapping[str, Sequence[str]] | None = None,
) -> str | None:
    """Why this action's command cannot run from here at all, or None.

    A command that takes a file or hands one back runs only from Send Command
    on the device page and from a Device Audit, where a person can give or take
    the file; from a macro step or a panel binding the runtime refuses it, in
    this same sentence (``DeviceManager._check_file_door``). A group step says
    so when any member's command is one. None for no opinion, as above.
    """
    if not isinstance(action, Mapping):
        return None
    name = action.get("action")
    command = action.get("command")
    if name not in COMMAND_ACTIONS or not isinstance(command, str) or not command:
        return None
    if name == "device.command":
        candidates: Iterable[Any] = [action.get("device")]
    else:
        group_id = action.get("group")
        if not isinstance(group_id, str) or not groups:
            return None
        candidates = groups.get(group_id) or ()
    for device_id in candidates:
        definition = _declared_command(devices, str(device_id or ""), command)
        if definition is None:
            continue
        label = str(definition.get("label") or command)
        if file_param_names(definition):
            return takes_file_sentence(label)
        if definition.get("returns_file") is True:
            return returns_file_sentence(label)
    return None


def param_label_of(
    action: Mapping[str, Any],
    name: str,
    *,
    devices: Any,
    groups: Mapping[str, Sequence[str]] | None = None,
) -> str | None:
    """What the step's form calls one of its command's parameters: the label
    the driver declares, else the key made readable (``param_labels``), read
    from the same driver lookup as :func:`missing_params`. A group step takes
    the first member whose driver declares it. None when no driver does."""
    if not isinstance(action, Mapping):
        return None
    command = action.get("command")
    if not isinstance(command, str) or not command:
        return None
    if action.get("action") == "device.command":
        candidates: Iterable[Any] = [action.get("device")]
    elif action.get("action") == "group.command" and groups:
        candidates = groups.get(str(action.get("group") or ""), ())
    else:
        return None
    for device_id in candidates:
        if not isinstance(device_id, str):
            continue
        defs = _declared_params(devices, device_id, command)
        if defs and name in defs:
            return param_label(name, defs[name])
    return None


def device_groups(project: Any) -> dict[str, list[str]]:
    """``{group id: member device ids}`` off a loaded project, however it is held.

    Duck-typed because the callers hold the project in three shapes -- the
    Pydantic model the engine loaded, the model a tool is mutating, and a plain
    dict from a request body.
    """
    groups = getattr(project, "device_groups", None)
    if groups is None and isinstance(project, Mapping):
        groups = project.get("device_groups")
    resolved: dict[str, list[str]] = {}
    for group in groups if isinstance(groups, Iterable) else ():
        gid = getattr(group, "id", None)
        members = getattr(group, "device_ids", None)
        if gid is None and isinstance(group, Mapping):
            gid, members = group.get("id"), group.get("device_ids")
        if isinstance(gid, str) and gid and isinstance(members, (list, tuple)):
            resolved[gid] = [str(m) for m in members]
    return resolved


def missing_params_check(
    devices: Any, project: Any = None
) -> Callable[[Mapping[str, Any]], list[str] | None]:
    """The check as one callable, for the validators that take it injected.

    ``page_references`` and ``macro_validation`` stay free of engine imports the
    way they already are for every other lookup they need -- the caller that can
    reach the driver registry hands them a function, and the same function
    serves the macro lint, the AI's write doors and the Builder's Validate.
    """
    groups = device_groups(project) if project is not None else None
    return lambda action: missing_params(action, devices=devices, groups=groups)


def file_command_check(
    devices: Any, project: Any = None
) -> Callable[[Mapping[str, Any]], str | None]:
    """:func:`file_command_reason` as one callable, injected the way
    :func:`missing_params_check` is."""
    groups = device_groups(project) if project is not None else None
    return lambda action: file_command_reason(action, devices=devices, groups=groups)


def param_label_check(
    devices: Any, project: Any = None
) -> Callable[[Mapping[str, Any], str], str | None]:
    """:func:`param_label_of` as one callable, injected the way
    :func:`missing_params_check` is, for the macro lint's records."""
    groups = device_groups(project) if project is not None else None
    return lambda action, name: param_label_of(
        action, name, devices=devices, groups=groups,
    )
