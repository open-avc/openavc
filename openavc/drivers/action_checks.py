"""A driver's Quick Actions, checked when it loads.

Two blocks put buttons on a device's page: ``quick_actions``, a list of
command ids, and ``actions``, the full form (``kind: command`` promotes a
command, ``kind: link`` opens a URL, ``kind: setup`` runs a Python driver's
provisioning wizard). ``validate_actions`` is the rule both kinds of driver
share: the walk asks it for a YAML definition and ``python_info`` for a
Python driver's ``DRIVER_INFO``, so one broken block reads the same in the
catalog, at load and in the Driver Builder. ``yaml_action_errors`` is what a
YAML definition is held to on top of it.

Both blocks promote commands, and a promoted command is judged in one place
(``_Commands.promotion_errors``): it must be declared, unless the reader could see only
part of the command set (the computed-keys marker), and it must not take or
hand back a file, because a button has no file to give. ``quick_actions``
names the command; a ``kind: command`` action names it in ``command``, or by
its own id.

The runtime twin is ``actions.resolve_device_actions``, which builds the buttons
from the same blocks for any driver that loads, and a Python driver whose block
these checks refuse still loads (the loader only warns). It drops only what it
cannot draw or run (an entry that is not a mapping, a missing or duplicate id,
an unknown kind, a file command, a quick id that is not declared) and makes do
with the rest, so a field added here is read there too.

Purity: standard library plus the contract's own modules, as for every
module the validator imports; it is vendored with them.
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.condition_checks import condition_block_errors
from openavc.drivers.declared_names import UNEVALUATED_KEY
from openavc.drivers.param_aid_checks import param_aid_errors
from openavc.drivers.spec import (
    ACTION_KINDS,
    AVAILABILITIES,
    PYTHON_ONLY_ACTION_KINDS,
    is_file_command,
)

# A promoted button has no file to give and nowhere to put one that comes
# back: the command runs from Send Command on the device page instead.
_FILE_ACTION_REASON = (
    "takes or hands back a file, so it cannot be a Quick Action; it runs "
    "from Send Command on the device page"
)


class _Commands:
    """The commands a button may promote, read once per definition.

    A Python driver may merge commands in from a module constant or build
    them at runtime, and the reader marks the block when it could not see
    every key. The visible set is then a subset of the real one, so a name
    missing from it proves nothing, and checking anyway reports a working
    driver as broken (``sony_bravia`` merges ``**_IRCC_COMMANDS``: 15 keys
    visible, an action's target among the ones that are not). The skip is
    named by ``python_info.python_driver_reference_skips``. A YAML definition
    reaches it only with a command literally named like the marker.
    """

    def __init__(self, driver_def: dict[str, Any]) -> None:
        commands = driver_def.get("commands")
        self._commands = commands if isinstance(commands, dict) else None
        names = set(commands.keys()) if isinstance(commands, dict) else set()
        self._declared = set() if UNEVALUATED_KEY in names else names

    def undeclared(self, name: str) -> bool:
        """Whether ``name`` is provably not a command (never, with none to see)."""
        return bool(self._declared) and name not in self._declared

    def takes_a_file(self, name: str) -> bool:
        return self._commands is not None and is_file_command(self._commands.get(name))

    def promotion_errors(
        self, where: str, name: str, *, named: str, judge_declared: bool = True,
    ) -> list[str]:
        """Why the command ``name`` cannot be a button, if it cannot.

        ``named`` is how the message names it. ``judge_declared`` is off where
        an entry's own fields leave its target unsettled; a file command is
        refused all the same.
        """
        if judge_declared and self.undeclared(name):
            return [f"{where}: {named} is not a declared command"]
        if self.takes_a_file(name):
            return [f"{where}: {named} {_FILE_ACTION_REASON}"]
        return []


def _quick_action_errors(quick: Any, commands: _Commands) -> list[str]:
    if quick is None:
        return []
    if not isinstance(quick, list):
        return ["quick_actions: must be a list of command ids"]
    errors: list[str] = []
    for i, cid in enumerate(quick):
        where = f"quick_actions[{i}]"
        if not isinstance(cid, str) or not cid:
            errors.append(f"{where}: must be a non-empty command id string")
        else:
            errors.extend(commands.promotion_errors(where, cid, named=f"'{cid}'"))
    return errors


# --- one actions entry's own fields, in the order they are reported --------


def _kind(entry: dict[str, Any]) -> Any:
    """An entry's kind: a button promotes a command unless it says otherwise."""
    return entry.get("kind", "command")


def _kind_errors(where: str, entry: dict[str, Any]) -> list[str]:
    kind = _kind(entry)
    if kind in ACTION_KINDS:
        return []
    return [f"{where}: unknown kind '{kind}' (expected one of {list(ACTION_KINDS)})"]


def _label_errors(where: str, entry: dict[str, Any]) -> list[str]:
    label = entry.get("label")
    if label is None or isinstance(label, str):
        return []
    return [f"{where}: 'label' must be a string"]


def _icon_errors(where: str, entry: dict[str, Any]) -> list[str]:
    icon = entry.get("icon")
    if icon is None or isinstance(icon, str):
        return []
    return [f"{where}: 'icon' must be a string (lucide icon name)"]


def _availability_errors(where: str, entry: dict[str, Any]) -> list[str]:
    availability = entry.get("availability")
    if availability is None or availability in AVAILABILITIES:
        return []
    return [f"{where}: 'availability' must be one of {list(AVAILABILITIES)}"]


def _confirm_errors(where: str, entry: dict[str, Any]) -> list[str]:
    confirm = entry.get("confirm")
    if confirm is None or isinstance(confirm, (bool, str)):
        return []
    return [f"{where}: 'confirm' must be a boolean or a message string"]


def _params_errors(where: str, entry: dict[str, Any]) -> list[str]:
    params = entry.get("params")
    if params is None or isinstance(params, dict):
        return []
    return [f"{where}: 'params' must be a mapping"]


def _url_errors(where: str, entry: dict[str, Any]) -> list[str]:
    """A link's URL, which no other kind takes."""
    url = entry.get("url")
    if url is None:
        return []
    if _kind(entry) != "link":
        return [f"{where}: 'url' is only valid on a kind:link action"]
    if isinstance(url, str) and url:
        return []
    return [f"{where}: 'url' must be a non-empty string"]


def _visible_when_errors(where: str, entry: dict[str, Any]) -> list[str]:
    return condition_block_errors(where, entry.get("visible_when"))


_FIELD_CHECKS = (
    _kind_errors,
    _label_errors,
    _icon_errors,
    _availability_errors,
    _confirm_errors,
    _params_errors,
    _url_errors,
    _visible_when_errors,
)


class _Entries:
    """One ``actions`` list, checked entry by entry: the ids seen so far, and
    the commands its entries may promote."""

    def __init__(self, commands: _Commands) -> None:
        self._commands = commands
        self._seen: set[str] = set()

    def errors(self, index: int, entry: Any) -> list[str]:
        where = f"actions[{index}]"
        if not isinstance(entry, dict):
            return [f"{where}: must be a mapping"]
        errors = self._id_errors(where, entry.get("id"))
        for check in _FIELD_CHECKS:
            errors.extend(check(where, entry))
        errors.extend(self._promoted_command_errors(where, entry))
        return errors

    def _id_errors(self, where: str, action_id: Any) -> list[str]:
        if not isinstance(action_id, str) or not action_id:
            return [f"{where}: missing required 'id' (non-empty string)"]
        duplicate = action_id in self._seen
        self._seen.add(action_id)
        return [f"{where}: duplicate action id '{action_id}'"] if duplicate else []

    def _promoted_command_errors(self, where: str, entry: dict[str, Any]) -> list[str]:
        """The command a ``kind: command`` action promotes: its ``command``,
        else its own id.

        An id that is not text ends it: nothing is promoted. An empty id, or a
        ``command`` that is not text, leaves the target unsettled, so whether
        it is declared is not judged; it is still refused if the name it falls
        back to is a file command.
        """
        action_id = entry.get("id")
        if _kind(entry) != "command" or not isinstance(action_id, str):
            return []
        command_id = entry.get("command")
        errors: list[str] = []
        judge_declared = bool(action_id)
        if judge_declared and _not_text(command_id):
            errors.append(f"{where}: 'command' must be a string")
            judge_declared = False
        target = command_id or action_id
        if isinstance(target, str):
            errors.extend(
                self._commands.promotion_errors(
                    where, target, named=f"command '{target}'", judge_declared=judge_declared,
                )
            )
        return errors


def _not_text(value: Any) -> bool:
    """Set, and not text."""
    return value is not None and not isinstance(value, str)


def validate_actions(driver_def: dict[str, Any]) -> list[str]:
    """Check the ``quick_actions`` and ``actions`` blocks of a driver: the
    rules a YAML definition and a Python driver's ``DRIVER_INFO`` share.

    Returns the error strings, ``quick_actions`` first, each entry's in the
    order its fields are listed above (empty when valid).
    """
    commands = _Commands(driver_def)
    errors = _quick_action_errors(driver_def.get("quick_actions"), commands)
    actions = driver_def.get("actions")
    if actions is None:
        return errors
    if not isinstance(actions, list):
        return [*errors, "actions: must be a list"]
    entries = _Entries(commands)
    for i, entry in enumerate(actions):
        errors.extend(entries.errors(i, entry))
    return errors


def yaml_action_errors(where: str, entry: Any) -> list[str]:
    """What one ``actions`` entry of a YAML definition is held to on top of
    ``validate_actions``.

    A YAML driver is interpreted by ``ConfigurableDriver``, which has no
    ``run_setup_action`` handler, so a Python-only kind could never do
    anything there. And an entry's ``params`` get the form aids a command's
    params get; a Python driver's are not checked for them at load.
    """
    if not isinstance(entry, dict):
        return []
    errors: list[str] = []
    kind = _kind(entry)
    if kind in PYTHON_ONLY_ACTION_KINDS:
        # The sentence is setup's (its handler): a second Python-only kind
        # needs its own.
        errors.append(
            f"{where}: kind '{kind}' requires a Python driver "
            f"(a run_setup_action handler); YAML drivers support "
            f"kind 'command' only"
        )
    params = entry.get("params")
    if isinstance(params, dict):
        errors.extend(param_aid_errors(where, params))
    return errors
