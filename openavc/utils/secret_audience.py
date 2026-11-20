"""THE rule for which exits may carry a secret at all.

A secret is a device's login, a parameter or setting its driver marks secret,
or the project's own (the mesh key): what ``utils/log_redaction`` registers.
An exit is a place one could leave by. :data:`MAY_CARRY_SECRET` answers, once
for each exit, whether it may carry one; an exit with no row may not. Only
three may:

  - **the device itself**, over its own connection: what the secret is for;
  - **the login file and the backups the platform writes**, owner-only on
    this system;
  - **a value a driver published into state**, as the panels, the
    Programmer and the scripts on this system read it: a camera's stream
    address carries its login so a panel can play the stream. Leaving this
    system, the same value is the cloud's row.

Every other exit may not carry one. An exit that masks with the registered
values reads them through ``SecretRegistry.masked`` or
``SecretRegistry.to_mask``, naming itself: those two are the only reads of
the registry's values outside the registry (``tests/test_secret_audience.py``
sweeps the code for any other). Not every exit asks yet: some mask by a rule
of their own (a URL's password, a credential's name), and a few still send a
secret as they were given it (a setup run's result, a refused command's
parameters). This table is the rule they are held to.

The WebSocket rows are the client types ``core/ws_hub.MESSAGE_AUDIENCE``
names (:attr:`Exit.PANEL`, :attr:`Exit.PROGRAMMER`): that table says who a
push reaches, this one whether it may carry a secret. A push of driver state
is :attr:`Exit.DRIVER_STATE`.

Stdlib only, so the redaction module can ask it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class Exit(str, Enum):
    """A place a secret could leave by."""

    # The device's own connection.
    DEVICE = "device"
    # The login file, and the backups the platform writes on its own.
    LOGIN_FILE = "login_file"
    # A value a driver published into state, read on this system.
    DRIVER_STATE = "driver_state"
    # Any other push to a panel (ws_hub.PANEL).
    PANEL = "panel"
    # Any other push or reply to the Programmer (ws_hub.PROGRAMMER): a setup
    # run's progress, a driver's dry run.
    PROGRAMMER = "programmer"
    # The log: the console, the log file, the buffer the Log view and its
    # Download read.
    LOG = "log"
    # The cloud: the state relay, alerts, command results, the AI's tools.
    CLOUD = "cloud"
    # The Device Audit and the problem report.
    REPORT = "report"
    # Words the platform writes about a device or a request: a refusal, a
    # device card's reason, an error.
    SENTENCE = "sentence"
    # The project file and every copy of it: the saved-login marker stands in
    # a secret's place.
    PROJECT_FILE = "project_file"


MAY_CARRY_SECRET: dict[Exit, bool] = {
    Exit.DEVICE: True,
    Exit.LOGIN_FILE: True,
    Exit.DRIVER_STATE: True,
    Exit.PANEL: False,
    Exit.PROGRAMMER: False,
    Exit.LOG: False,
    Exit.CLOUD: False,
    Exit.REPORT: False,
    Exit.SENTENCE: False,
    Exit.PROJECT_FILE: False,
}


def may_carry_secret(where: Any) -> bool:
    """Whether the exit ``where`` may carry a secret as it is; an exit with no
    row may not."""
    try:
        return MAY_CARRY_SECRET.get(Exit(where), False)
    except ValueError:
        return False
