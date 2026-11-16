"""THE outcome of a device command that was not a plain success.

``DeviceManager.send_command`` either returns (the command went out and
nothing went wrong) or raises. A send that went nowhere, or went out and could
not be finished or confirmed, raises :class:`CommandOutcomeError` with its
code; every other raise (a refusal before sending, a driver's or a transport's
own fault) carries no code and is a plain failure. The codes:

* ``not_sent``: nothing reached the device. A YAML command written in a form
  the device's connection cannot carry (an OSC message on a TCP device, an
  HTTP request on a serial one), or one with nothing to send.
* ``partial``: the command changed the device and then stopped partway
  (:class:`CommandPartialError`, raised by a driver with its own sentence).
* ``uncertain``, ``pending``: it went out and the product did not confirm it
  finished.
* ``failed``, ``rejected``: it went out and the product said it did not
  finish, or could not accept it.

The readers that need more than "it failed" ask :func:`outcome_of`,
:func:`went_out` or :func:`outcome_fields`, never the exception's class: the
panel press's record and a group step's members (``sent`` and ``outcome``). The sentence is safe to show: an allowlisted one, the
platform's own (``not_sent`` names the command), or the driver's
(``partial``); never the device's raw text or a credential.
"""

from __future__ import annotations

from typing import Any

MESSAGES = {
    "uncertain": "The product did not confirm the command finished. It may still finish, so check the product's status before sending it again.",
    "pending": "The product did not confirm the command finished. It may still finish, so check the product's status before sending it again.",
    "failed": "The command did not finish successfully. Equipment state may have changed; check its current status before trying again.",
    "rejected": "The equipment could not accept the command. Check its current status before trying again.",
    "not_sent": "The command was not sent. Check the command and the device's connection.",
    "partial": "The command did part of its work and then stopped. Check the equipment's current state before trying again.",
}

UNCONFIRMED_AFTER_WAIT = (
    "The product did not confirm the command finished within {wait}. "
    "It may still finish, so check the product's status before sending it again."
)

#: The outcomes after which the command had reached the equipment.
WENT_OUT = frozenset({"uncertain", "pending", "failed", "rejected", "partial"})

#: The outcomes whose sentence somebody at a panel can act on. A command that
#: was not sent is the setup's fault, which only the Programmer can correct,
#: so the panel says whom to call instead (api/error_messages.py).
FOR_THE_PANEL = frozenset(MESSAGES) - {"not_sent"}

#: What each form a YAML command can take is called in a sentence, by the
#: transport it needs.
_FORMS = {"osc": "an OSC message", "http": "an HTTP request"}

#: A transport as a sentence names it.
_TRANSPORTS = {"tcp": "TCP", "udp": "UDP", "http": "HTTP", "osc": "OSC", "serial": "serial"}


def _seconds(value: float) -> str:
    return f"{value:g} second" + ("" if value == 1 else "s")


class CommandOutcomeError(RuntimeError):
    """A command that was not a plain success, with its code (``outcome``).

    ``waited`` is how long the command was followed before it was reported
    unconfirmed, and says so in the sentence. ``message`` replaces the
    allowlisted sentence with the platform's or the driver's own; the code is
    refused unless it is one of :data:`MESSAGES`.
    """

    def __init__(
        self, outcome: str, *, waited: float | None = None, message: str | None = None,
    ):
        if outcome not in MESSAGES:
            raise ValueError("Unknown command outcome")
        self.outcome = outcome
        if not message:
            message = MESSAGES[outcome]
            if waited and outcome == "uncertain":
                message = UNCONFIRMED_AFTER_WAIT.format(wait=_seconds(waited))
        super().__init__(message)

    @property
    def for_the_panel(self) -> bool:
        """Whether somebody at a panel can act on the sentence."""
        return self.outcome in FOR_THE_PANEL


class CommandPartialError(CommandOutcomeError):
    """The command changed the device and then could not finish or confirm it.

    Distinct from an outright failure because the two ask different things of
    whoever reads them: a failed command left nothing behind, while this one
    did something and stopped partway, so a person has to go and look.

    The case that named it: a PoE power-cycle cuts a port, waits, and restores
    it. When the port being cycled is the one carrying the control session,
    the restore has no path left to travel down -- the endpoint stays dark
    while the operator is told the command failed, which reads as "nothing
    happened" and is the opposite of what is true.

    A RuntimeError (through CommandOutcomeError), so it cannot land in the
    device-not-found branch. The message is user-facing and actionable --
    surface it verbatim, including what was left in what state. Import it
    from ``openavc.drivers.base``.
    """

    def __init__(self, message: str = ""):
        super().__init__("partial", message=message)


def not_sent(
    command: str, label: str | None, *, form: str | None, transport: str | None,
) -> CommandOutcomeError:
    """A YAML command that cannot go out on this device's connection.

    ``form`` is the transport the command is written for (``osc``, ``http``),
    or None for a command with nothing to send; ``transport`` the device's.
    """
    name = label or command
    if form is None:
        sentence = f"{name} was not sent: it has nothing to send. Check the command."
    else:
        on = _TRANSPORTS.get(str(transport), str(transport))
        sentence = (
            f"{name} was not sent: it is written as {_FORMS[form]}, and this device's "
            f"connection is {on}. Check the command and the device's connection."
        )
    return CommandOutcomeError("not_sent", message=sentence)


def outcome_of(exc: BaseException) -> str | None:
    """The code a failed send carries, or None for a plain failure."""
    return exc.outcome if isinstance(exc, CommandOutcomeError) else None


def went_out(exc: BaseException) -> bool:
    """Whether the failed send had reached the equipment."""
    return outcome_of(exc) in WENT_OUT


def outcome_fields(exc: BaseException) -> dict[str, Any]:
    """What a record of a failed send says of where it got: ``sent`` (it
    reached the equipment) and, when the failure carries a code, ``outcome``."""
    code = outcome_of(exc)
    fields: dict[str, Any] = {"sent": code in WENT_OUT}
    if code is not None:
        fields["outcome"] = code
    return fields
