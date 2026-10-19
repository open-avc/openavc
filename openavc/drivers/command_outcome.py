"""Report known device-command outcomes without implying safe automatic replay."""

MESSAGES = {
    "uncertain": "The product did not confirm the command finished. It may still finish, so check the product's status before sending it again.",
    "pending": "The product did not confirm the command finished. It may still finish, so check the product's status before sending it again.",
    "failed": "The command did not finish successfully. Equipment state may have changed; check its current status before trying again.",
    "rejected": "The equipment could not accept the command. Check its current status before trying again.",
}

UNCONFIRMED_AFTER_WAIT = (
    "The product did not confirm the command finished within {wait}. "
    "It may still finish, so check the product's status before sending it again."
)


def _seconds(value: float) -> str:
    return f"{value:g} second" + ("" if value == 1 else "s")


class CommandOutcomeError(RuntimeError):
    """Carry an allowlisted outcome, never raw device text or credentials.

    ``waited`` is how long the command was followed before it was reported
    unconfirmed, and says so in the sentence.
    """

    def __init__(self, outcome: str, *, waited: float | None = None):
        if outcome not in MESSAGES:
            raise ValueError("Unknown command outcome")
        self.outcome = outcome
        message = MESSAGES[outcome]
        if waited and outcome == "uncertain":
            message = UNCONFIRMED_AFTER_WAIT.format(wait=_seconds(waited))
        super().__init__(message)
