"""A command declaration, read: THE answer to whether the platform may send a
command on the person's behalf, with nothing asked of them.

``read_command(name, cdef)`` reads one entry of a driver's ``commands``: a
YAML definition's or a ``DRIVER_INFO``'s, which share the shape, so a Python
driver's commands (those it builds once connected included) read the same
way. ``CommandModel.sendable_without_asking`` holds when the command needs
nothing from the person and a single Send would not warn about it first:

* no required parameter (``needs_input``): a required one is never filled
  from its default, so it always needs a value somebody gives
  (``param_validation.missing_required_params``, asked with nothing given);
* no file to give or take (``moves_a_file``, ``spec.is_file_command``);
* no confirmation of its own (``confirm``);
* no restart (``restarts_device_for``).

Who asks: the Device Audit's command list (``audit/command_catalog``) serves
the answer on each entry, joined there with a device-page action's own
confirmation (actions resolve against one device's config, which this reading
never sees). "Run all status queries", the read before a setting is written
and the IDE's count of the queries that run together all read that field, so
nothing a single Send would warn about is sent unasked.

``confirm`` and ``restarts_device_for`` are read as the command list has
served them to the wizard's Send warning, so the warning and this answer
cannot disagree about a command.

Never logs and never raises: a declaration that is not a mapping reads as one
that declares nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openavc.drivers.param_validation import missing_required_params
from openavc.drivers.spec import is_file_command


@dataclass(frozen=True)
class CommandModel:
    """One command, read. ``confirm`` is the sentence the driver asks before
    the command runs ("" for none); ``restarts_device_for`` the whole seconds
    the driver says it takes the device away (0 for none)."""

    name: str
    needs_input: bool
    moves_a_file: bool
    confirm: str
    restarts_device_for: int

    @property
    def sendable_without_asking(self) -> bool:
        """No value or file to give, and nothing to warn about first."""
        return not (self.needs_input or self.moves_a_file or self.confirm or self.restarts_device_for)


def read_command(name: str, cdef: Any) -> CommandModel:
    """The command ``name`` as its declaration ``cdef`` says."""
    if not isinstance(cdef, dict):
        cdef = {}
    params = cdef.get("params")
    return CommandModel(
        name=name,
        needs_input=bool(missing_required_params(params, {})),
        moves_a_file=is_file_command(cdef),
        confirm=_confirm_text(cdef, name),
        restarts_device_for=_restart_seconds(cdef),
    )


def _confirm_text(cdef: dict[str, Any], name: str) -> str:
    """The command's own ``confirm``: its sentence, a plain one for
    ``confirm: true``, or "" for none."""
    confirm = cdef.get("confirm")
    if isinstance(confirm, str) and confirm:
        return confirm
    if confirm is True:
        return f"The driver asks for a confirmation before {cdef.get('label') or name} runs."
    return ""


def _restart_seconds(cdef: dict[str, Any]) -> int:
    try:
        return int(cdef.get("restarts_device_for") or 0)
    except (TypeError, ValueError):
        return 0
