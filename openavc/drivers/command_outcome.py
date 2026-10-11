"""Report known device-command outcomes without implying safe automatic replay."""

MESSAGES = {
    "uncertain": "The product has not confirmed completion. Inspect readback; do not replay this command.",
    "pending": "The product has not confirmed completion. Inspect readback; do not replay this command.",
    "failed": "The command did not finish successfully. Equipment state may have changed; check its current status before trying again.",
    "rejected": "The equipment could not accept the command. Check its current status before trying again.",
}


class CommandOutcomeError(RuntimeError):
    """Carry an allowlisted outcome, never raw device text or credentials."""

    def __init__(self, outcome: str):
        if outcome not in MESSAGES:
            raise ValueError("Unknown command outcome")
        self.outcome = outcome
        super().__init__(MESSAGES[outcome])
