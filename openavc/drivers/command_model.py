"""A command declaration, read: THE answer to whether the platform may send a
command on the person's behalf, with nothing asked of them, and THE reading of
what a command asks before a person sends it by hand.

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
* no restart (``restarts_device_for``, read by ``restart_window``, the
  reading the runtime's commanded-restart window opens).

Who asks: the Device Audit's command list (``audit/command_catalog``) serves
the answer on each entry, joined there with a device-page action's own
confirmation (actions resolve against one device's config, which this reading
never sees). "Run all status queries", the read before a setting is written
and the IDE's count of the queries that run together all read that field, so
nothing a single Send would warn about is sent unasked.

``confirm_sentence`` is THE reading of a ``confirm``, a command's or a
device-page action's: the driver's sentence trimmed, nothing for a blank one,
and one question for ``true`` (``CONFIRM_QUESTION``, naming what is sent).
``CommandModel.confirm`` is a command's; the actions resolver words a button's
with the button's label (``actions.resolve_device_actions``), the audit's
command list and Send warning read ``CommandModel.confirm``, and the device
page's Send is served ``command_confirms``. The Driver Builder, which asks of
a draft no server has read, has the one copy (``commandConfirmMessage`` in
``components/shared/commandParams.ts``), held to this one by
``tests/fixtures/command_confirm_cases.json``.

``restarts_device_for`` is ``restart_window``'s reading, so the window the
audit watches is the one the runtime opens.

Never logs and never raises: a declaration that is not a mapping reads as one
that declares nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openavc.drivers.param_validation import missing_required_params
from openavc.drivers.restart_window import restart_seconds
from openavc.drivers.spec import is_file_command


#: What ``confirm: true`` asks: one plain question naming what is sent.
CONFIRM_QUESTION = "Send {label}?"

#: The spaces and line breaks around a sentence that are not part of it: the
#: set JavaScript's ``trim()`` removes, so the Driver Builder's copy reads a
#: draft alike. Python's own ``strip()`` would also take U+001C to U+001F and
#: U+0085, and leave U+FEFF.
_AROUND = (
    "\t\n\v\f\r \u00a0\u1680"
    + "".join(chr(code) for code in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


@dataclass(frozen=True)
class CommandModel:
    """One command, read. ``confirm`` is what a person sending it by hand is
    asked first (``confirm_sentence``; None for nothing); ``restarts_device_for``
    the whole seconds sending it takes the device away, from 1 to 600 (None for
    none)."""

    name: str
    needs_input: bool
    moves_a_file: bool
    confirm: str | None
    restarts_device_for: int | None

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
        confirm=_own_confirm(name, cdef),
        restarts_device_for=restart_seconds(cdef.get("restarts_device_for")),
    )


def confirm_sentence(confirm: Any, label: Any) -> str | None:
    """What a ``confirm`` asks before ``label`` is sent: the driver's sentence
    trimmed, ``CONFIRM_QUESTION`` for ``true``, or None for nothing (false,
    left out, blank, or not a flag or a sentence)."""
    if confirm is True:
        return CONFIRM_QUESTION.format(label=label)
    return driver_sentence(confirm)


def driver_sentence(confirm: Any) -> str | None:
    """The sentence a ``confirm`` gives in the driver's own words, trimmed;
    None for ``true``, for a blank one and for anything not text. A setup
    wizard shows it in place of its own description, which ``true`` keeps."""
    if isinstance(confirm, str):
        return confirm.strip(_AROUND) or None
    return None


def sets_its_own_confirm(confirm: Any) -> bool:
    """Whether a device-page action's ``confirm`` decides for its button:
    ``false`` (ask nothing), ``true`` or a sentence. Left out, null, blank or
    not a flag or a sentence, the promoted command's own ``confirm`` asks."""
    return isinstance(confirm, bool) or driver_sentence(confirm) is not None


def command_confirms(commands: Any) -> dict[str, str]:
    """What each of a driver's commands asks before a person sends it by hand,
    by name, for the commands that ask (the device page's Send)."""
    if not isinstance(commands, dict):
        return {}
    asked = {name: _own_confirm(name, cdef) for name, cdef in commands.items() if isinstance(cdef, dict)}
    return {name: sentence for name, sentence in asked.items() if sentence}


def _own_confirm(name: str, cdef: dict[str, Any]) -> str | None:
    """The command's own ``confirm``, worded with its label (else its name)."""
    return confirm_sentence(cdef.get("confirm"), cdef.get("label") or name)
