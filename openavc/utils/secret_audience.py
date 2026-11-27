"""THE rule for which exits may carry a secret at all.

A secret is a device's login, a parameter or setting its driver marks secret,
or the project's own (the mesh key): what ``utils/log_redaction`` registers.
An exit is a place one could leave by. :data:`MAY_CARRY_SECRET` answers, once
for each exit, whether it may carry one; an exit with no row may not. Only
five may:

  - **the device itself**, over its own connection: what the secret is for;
  - **the login file and the backups the platform writes**, owner-only on
    this system;
  - **a value a driver published into state**, as the panels, the
    Programmer and the scripts on this system read it: a camera's stream
    address carries its login so a panel can play the stream. Leaving this
    system, the same value is the cloud's row;
  - **the pages the project defines**, as the panels on this system draw
    them: an element's address carries a login as its author typed it, for
    the same reason;
  - **an event someone published on this system** (a project's own event, a
    panel's press or entry, a driver's or a plugin's), as its clients hear
    it: a code typed on a keypad reaches the flow that checks it. The
    platform's own words about a failure, on the same stream, may not.

Every other exit may not carry one. An exit asks for what it may carry,
naming itself: ``SecretRegistry.masked`` or ``SecretRegistry.to_mask`` for
the registered values, which one collector gathers from every place a secret
is held (``utils/log_redaction``), or the rules built on them in
``utils/secret_exits``, for a state value, a text and a config nothing
declares the secrets of. Those are the only reads of the registry's values
and the only masking outside those two modules
(``tests/test_secret_audience.py`` sweeps the code for any other). A setup
run's progress and result leave by the Programmer's row, but a secret
parameter the run was given is not registered until the run saves it, so a
wrong value is still sent as it was typed.

A command's secret parameter is the send's to keep in
(``core/command_gates.py``): it holds the declaration, so a refusal names the
parameter without its value, a door logs and announces the command with it
masked, and the device's masking learns the value before the checks run.

The WebSocket rows are the client types ``core/ws_hub.MESSAGE_AUDIENCE``
names (:attr:`Exit.PANEL`, :attr:`Exit.PROGRAMMER`): that table says who a
push reaches, this one whether it may carry a secret, and the hub asks both
for every client it sends to (``ws_hub.frame_as``). A push of live state is
:attr:`Exit.DRIVER_STATE` and one of the pages :attr:`Exit.PANEL_PAGES`
(``ws_hub.SECRET_EXITS``), and an event someone published
:attr:`Exit.PUBLISHED_EVENT`; every other push, and every reply to a
client's own message, leaves by its client's row.

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
    # The pages the project defines, as this system's panels draw them.
    PANEL_PAGES = "panel_pages"
    # An event someone published on this system, as its clients hear it.
    PUBLISHED_EVENT = "published_event"
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
    Exit.PANEL_PAGES: True,
    Exit.PUBLISHED_EVENT: True,
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
