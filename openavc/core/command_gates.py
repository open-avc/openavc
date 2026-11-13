"""The checks a command passes between the door that sent it and its driver,
read off the live driver: what DeviceManager.send_command asks before anything
reaches the wire, and how a file command is run.

In the order send_command asks them: the command is one the driver declares
(enforced only when it declares a non-empty set, since an empty or missing one
means "this driver did not say"); a file command comes only from a door that
can give or take a file, with a file in each file param; a value saved on this
system is filled in where the step that sent it saved it, and any marker left
is refused (``fill_saved_params``, ``core/saved_params.py``); each ``child_id``
param is coerced to its child type's declared id kind and checked against its
range; then every param passes the declared-schema gate
(drivers/param_validation.py). Whether the device takes the command at all
right now (connected, or the command is ``available_offline``) is the device
manager's own question, and so is what a refusal means for the device.

The rule worth knowing before changing any of them: each reads the driver
INSTANCE's DRIVER_INFO through ``command_definition``, never the class's or the
catalog's, so a command set built at runtime (discovered controls, channel
strips, the inline-protocol generics merging a device's own configured
commands) is judged on what the driver declares when the command is sent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openavc.core import saved_params

from openavc.core.device_traffic import RX, TX, get_traffic_recorder
from openavc.drivers.avcdriver_semantic import undeclared_child_type_reason
from openavc.drivers.base import (
    CommandParamError,
    UnknownCommandError,
    normalize_and_validate_command_params,
)
from openavc.drivers.child_ids import (
    child_id_kind,
    child_id_range_error,
    coerce_child_local_id,
)
from openavc.drivers.command_files import (
    CommandFile,
    DeviceFile,
    FileCommandError,
    returns_file_sentence,
    takes_file_sentence,
)
from openavc.drivers.param_labels import param_label
from openavc.drivers.spec import file_param_names, is_file_command, secret_settings


def _driver_info(driver: Any) -> dict[str, Any]:
    return getattr(driver, "DRIVER_INFO", {}) or {}


def command_definition(driver: Any, command: str) -> dict[str, Any] | None:
    """The live driver's declaration of ``command``, or None when it declares
    none (or declares it as something other than a mapping)."""
    cmd_def = (_driver_info(driver).get("commands") or {}).get(command)
    return cmd_def if isinstance(cmd_def, dict) else None


def command_label(cmd_def: dict[str, Any] | None, command: str) -> str:
    """The name a command's refusals give it: its label, else its id."""
    return str((cmd_def or {}).get("label") or command)


def _param_defs(driver: Any, command: str) -> dict[str, Any] | None:
    """The declared params of ``command``, or None when it declares none."""
    cmd_def = command_definition(driver, command)
    pdefs = cmd_def.get("params") if cmd_def is not None else None
    return pdefs if isinstance(pdefs, dict) else None


def fill_saved_params(
    driver: Any,
    command: str,
    params: dict[str, Any] | None,
    fill: Callable[[str, Any], str | None],
) -> dict[str, Any] | None:
    """``params`` with each value saved on this system filled in, or refused.

    ``fill`` answers a parameter's saved value from its marker, and only for a
    parameter whose stored value in the sending step or action was that
    marker (``saved_params.filler``); it is asked only where the driver marks
    the parameter secret. A marker of either kind it does not fill (one sent
    from another door, copied into a parameter that is not secret, reached
    through a variable, or with nothing saved behind it) raises
    CommandParamError, and nothing is sent: a device is never handed a
    marker as its key.
    """
    marked = [key for key, value in (params or {}).items() if saved_params.is_any_marker(value)]
    if not marked:
        return params
    secret = secret_settings(_param_defs(driver, command))
    filled = dict(params or {})
    for key in marked:
        value = fill(key, filled[key]) if key in secret else None
        if value is None:
            raise _not_saved_here(driver, command, key)
        filled[key] = value
    return filled


def _not_saved_here(driver: Any, command: str, key: str) -> CommandParamError:
    """Where to enter a saved value the send could not fill in."""
    label = param_label(key, (_param_defs(driver, command) or {}).get(key))
    sends = command_label(command_definition(driver, command), command)
    return CommandParamError(saved_params.not_saved_sentence(label, sends))


def check_command_declared(driver: Any, device_id: str, command: str) -> None:
    """Refuse a command the driver's own ``commands`` block never declares.

    Without it the two driver formats failed differently while looking the
    same from outside: a YAML driver logged ``Unknown command: <name>`` and
    returned success, and a Python driver raised whatever its handler raised,
    usually a ValueError, which the REST layer answered as a missing device on
    a device that was connected and rendering. Either way the author was
    pointed away from the typo. A Quick Action is the common route in, because
    an ``actions`` entry may name a command that does not exist (the catalog
    rejects that; a driver dropped straight into ``driver_repo/`` only warns at
    load, so the button still renders).

    Enforced only when the set is a **non-empty dict**: 20 of the 92 shipped
    drivers declare no literal ``commands`` block at all and hand every name
    straight to their own dispatch, and an empty set means "this driver did
    not tell us", not "this driver has no commands".
    """
    commands = _driver_info(driver).get("commands")
    if not isinstance(commands, dict) or not commands:
        return
    if command not in commands:
        raise UnknownCommandError(
            f"Command '{command}' not found on device '{device_id}'"
        )


@dataclass(frozen=True)
class FileCommand:
    """A file command that passed the file door: its id, the label its
    sentences use, and whether it hands a file back."""

    command: str
    label: str
    returns_file: bool


def check_file_door(
    driver: Any, command: str, params: dict[str, Any] | None, files_allowed: bool,
) -> FileCommand | None:
    """Refuse a file command from a door that cannot give or take a file, and
    a file param given anything but a file.

    ``files_allowed`` is the door saying it can: Send Command's background
    runs and the Device Audit. Returns the file command when it is one (the
    send then runs through :func:`send_file_command`), None otherwise.
    """
    cmd_def = command_definition(driver, command)
    if not is_file_command(cmd_def):
        return None
    file_params = file_param_names(cmd_def)
    label = command_label(cmd_def, command)
    if not files_allowed:
        raise FileCommandError(
            takes_file_sentence(label) if file_params else returns_file_sentence(label)
        )
    _check_files_given(cmd_def, label, file_params, params)
    return FileCommand(command, label, cmd_def.get("returns_file") is True)


def _check_files_given(
    cmd_def: dict[str, Any], label: str, file_params: list[str], params: Any,
) -> None:
    """Refuse a file param given something other than a file (an absent one
    is the param gate's to judge)."""
    supplied = params if isinstance(params, dict) else {}
    for name in file_params:
        value = supplied.get(name)
        if value is not None and not isinstance(value, CommandFile):
            pdef = (cmd_def.get("params") or {}).get(name) or {}
            raise FileCommandError(
                f"'{label}': {param_label(name, pdef)} takes a file chosen "
                "on the device's page."
            )


async def send_file_command(
    driver: Any, device_id: str, file_command: FileCommand, params: dict[str, Any] | None,
) -> Any:
    """Run a file command with the traffic recorder holding the file's
    direction: what the device is sent while a file goes to it, what it
    answers while a file comes back. One note entry names each file and its
    size instead (core/device_traffic.py)."""
    recorder = get_traffic_recorder()
    label = file_command.label
    sent = [
        value for value in (params or {}).values() if isinstance(value, CommandFile)
    ]
    for file in sent:
        recorder.record_note(device_id, TX, file=file.name, size=file.size, command=label)
    with recorder.hold(device_id, tx=bool(sent), rx=file_command.returns_file):
        result = await driver.send_command(file_command.command, params)
    if file_command.returns_file:
        if not isinstance(result, DeviceFile):
            raise FileCommandError(f"'{label}' finished without sending back a file.")
        recorder.record_note(
            device_id, RX, file=result.name, size=result.size, command=label,
        )
    return result


def available_offline(driver: Any, command: str) -> bool:
    """True when the command declares ``available_offline``: it may run with
    no live connection. An unknown command, or one without the flag, is not
    offline-capable (the connected gate applies)."""
    cmd_def = command_definition(driver, command)
    return cmd_def is not None and bool(cmd_def.get("available_offline"))


def validate_command_params(
    driver: Any, command: str, params: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Run a command's params through the declared-schema gate, whatever the
    driver's format.

    A YAML driver also validates internally (covering direct-call paths like
    the Driver Builder's test harness); this is the dispatch-path gate that
    makes a Python driver's declared ``min``/``max``/``pattern`` mean
    something. Commands or params without a schema entry pass through
    untouched. Runs when the caller supplied no params at all, too: a command
    invoked with nothing is exactly where a missing ``required`` param has to
    be caught.
    """
    pdefs = _param_defs(driver, command)
    if pdefs is None:
        return params
    label = command_label(command_definition(driver, command), command)
    return normalize_and_validate_command_params(command, pdefs, params, label=label)


def coerce_child_id_params(
    driver: Any, command: str, params: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Coerce ``child_id`` param values to the child type's declared id type
    before they reach the driver.

    The UI, macros, and REST supply the id as a string (often the padded
    form, e.g. "003"); an integer-id child type needs an int, so a driver can
    pass ``params["outlet"]`` straight to the child API (``int(int)`` keeps a
    hand-converting driver working). String-id types pass through untouched:
    only the integer kind has a wire form to normalize. Returns a new dict;
    the caller's is left alone.

    The coercion rule itself lives in ``drivers/child_ids``; this owns which
    params it applies to and what a failure means here.
    """
    if not params:
        return params
    pdefs = _param_defs(driver, command)
    if pdefs is None:
        return params
    child_types = _driver_info(driver).get("child_entity_types") or {}
    out = dict(params)
    for name, pdef in pdefs.items():
        if not _is_child_id_param(pdef):
            continue
        value = out.get(name)
        if value is not None:
            out[name] = _coerced_child_id(f"'{command}': '{name}'", pdef, value, child_types)
    return out


def _is_child_id_param(pdef: Any) -> bool:
    return isinstance(pdef, dict) and pdef.get("type") == "child_id"


def _declared_child_type(
    where: str, pdef: dict[str, Any], child_types: dict[str, Any],
) -> Any:
    """The definition of the child type a ``child_id`` param names.

    A name the driver never defined is the driver's own typo, not a bad value:
    the refusal says which declaration is wrong, in the sentence the catalog,
    the file checker, the simulator validator and the loader use for the
    static form of this fault.
    """
    declared_type = pdef.get("child_type")
    if isinstance(declared_type, str) and declared_type not in child_types:
        raise CommandParamError(
            f"{where}: " + undeclared_child_type_reason(declared_type, child_types)
        )
    return child_types.get(declared_type)


def _coerced_child_id(
    where: str, pdef: dict[str, Any], value: Any, child_types: dict[str, Any],
) -> Any:
    """One ``child_id`` param's value as the driver gets it; ``where`` opens
    each refusal (the command and the param)."""
    type_def = _declared_child_type(where, pdef, child_types)
    if child_id_kind(type_def) != "integer":
        return value
    coerced = coerce_child_local_id(type_def, value)
    if coerced is None:
        raise CommandParamError(f"{where} must be a child id number, got {value!r}")
    # Coercion proves the id is the right KIND; the declared range is a
    # separate fact, checked here beside the min/max checks the same gate runs
    # for ordinary numeric params. Unchecked, an out-of-range id reached the
    # wire and the command reported success.
    range_error = child_id_range_error(type_def, coerced)
    if range_error is not None:
        raise CommandParamError(f"{where} {range_error}, got {coerced}")
    return coerced
