"""What a device's state adds up to, in one word: THE presence rule.

``device.<id>.presence`` holds one word from ``PRESENCE_WORDS``. The device
manager writes it (core/device_away.py) in the same batch as every change to
a part it is made of, so whoever judges ``connected`` the moment it is
written reads the word that goes with it. Each reader asks this module what
a word means rather than combining the parts itself: the alert monitor
whether the absence is on purpose, the cloud heartbeat whether the device is
in trouble (``in_trouble``), the command gate whether a command goes out.

The words, first match wins:

* ``disabled``: the project turns it off.
* ``waiting_for_driver``: its driver is not installed.
* ``online``: connected.
* ``restarting``: inside a restart a command asked for (or the wake after a
  sleep), the window open or its flag still published.
* ``asleep``: put to sleep by a command declaring ``sleeps_device``.
* ``paused``: held disconnected by Live Test or a device audit.
* ``offline``: a fault is published (``offline_reason``), the connection's
  or the driver's or the settings'.
* ``connecting``: none of these: not connected yet, and no fault is
  published (just added, the first connect in flight, or the moment between
  a drop and its classification).

Restarting and asleep come before paused, so a device asleep or restarting
on purpose stays quiet while Live Test or an audit pauses it.

A pause is away on purpose for an hour (``PAUSE_ON_PURPOSE_SECONDS``), not
for as long as it lasts: Live Test renews its pause while its panel is open,
so a laptop left open in the rack would keep the device's offline alerts
quiet for good. ``on_purpose`` is the whole question, asked with how long
the device has been paused; past the hour the alert monitor judges it as it
judges a fault, and says who holds the pause, and the heartbeat counts it as
a device in trouble (``in_trouble``).

A paused device takes no command, one that needs no connection included
(``TAKES_NO_COMMAND``). Because restarting and asleep come first, the word
does not show a pause under them, and the command gate asks the pause as
well as the word.

A device that went silent and whose port still opens is reconnected, held
offline until it answers, and dropped again on a cycle. The word stays
``offline`` for the whole spell: whether a connection is open at this moment
is the connection's, not the device's state, and a word following it would
change every cycle. The command gate asks the driver for it besides the word
(core/device_commands.py ``accepts_commands``), so the Power On that wakes a
device gone quiet in standby still reaches it.

Pure, with no imports from the platform, so a copy elsewhere can be held to
it. The Programmer's copy is web/programmer/src/components/shared/
devicePresence.ts, which its status dot, device list, device page, Dashboard
and pickers read the word through; tests/test_ide_presence_mirror.py holds
it to these words. The panel reads the word in web/panel/modules/
availability.js: every word but ``online`` greys a control's reading and
marks a press that reaches nothing up, ``asleep`` keeps a wake control's
reading, and ``TAKES_NO_COMMAND`` marks a wake press too;
tests/test_panel_presence_mirror.py holds its words to these.
The cloud service shows the word through the Programmer's copy, run as it
stands, and holds what its assistant is taught about the word to these words.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

DISABLED = "disabled"
WAITING_FOR_DRIVER = "waiting_for_driver"
ONLINE = "online"
RESTARTING = "restarting"
ASLEEP = "asleep"
PAUSED = "paused"
OFFLINE = "offline"
CONNECTING = "connecting"

PRESENCE_WORDS: tuple[str, ...] = (
    DISABLED, WAITING_FOR_DRIVER, ONLINE, RESTARTING, ASLEEP, PAUSED, OFFLINE, CONNECTING,
)

# Absence we asked for: no offline alert fires for it (a pause for an hour,
# ``on_purpose``).
AWAY_ON_PURPOSE: frozenset[str] = frozenset({RESTARTING, ASLEEP, PAUSED})
# How long a pause stays away on purpose. A Device Audit ends after 30 idle
# minutes and its longest step (the outage watch) runs 15; Live Test holds
# its pause for as long as its panel is open.
PAUSE_ON_PURPOSE_SECONDS = 3600.0
# A command sent now goes out. So does one declaring available_offline, and
# any command while a held connection is open (see above), except under
# TAKES_NO_COMMAND.
TAKES_COMMANDS: frozenset[str] = frozenset({ONLINE})
# No command sent now goes out, one declaring available_offline (a
# Wake-on-LAN Power On) included: no driver runs the device (turned off in
# the project, its driver not installed), or it is held for Live Test or a
# Device Audit, and a wake would switch the equipment on under it. A device
# asleep or restarting when it was paused keeps that word, so the command
# gate asks the pause itself as well (core/device_commands.py
# ``command_goes_out``); the panel, which reads the word alone, marks a wake
# press under these words.
TAKES_NO_COMMAND: frozenset[str] = frozenset({DISABLED, WAITING_FOR_DRIVER, PAUSED})
# Counted as a device in trouble: a fault is what keeps it away. So is a
# pause past its hour, which the word does not show (``in_trouble``).
IN_TROUBLE: frozenset[str] = frozenset({OFFLINE})


@dataclass(frozen=True)
class PresenceParts:
    """The facts the word is made of, each already read by the caller. The
    fields are in the words' order: the first one true names the word."""

    disabled: bool = False
    waiting_for_driver: bool = False
    online: bool = False
    restarting: bool = False
    asleep: bool = False
    paused: bool = False
    offline: bool = False


def presence_word(parts: PresenceParts) -> str:
    """The word for these parts: the first of them that holds, else
    ``connecting``."""
    return next((f.name for f in fields(parts) if getattr(parts, f.name)), CONNECTING)


def on_purpose(word: Any, paused_seconds: float | None = None) -> bool:
    """Whether the absence ``word`` names is on purpose, so no offline alert
    fires for it. ``paused_seconds`` is how long the device has been paused,
    None when it is not.

    Past ``PAUSE_ON_PURPOSE_SECONDS`` only a sleep keeps a paused device
    quiet. A commanded restart lasts minutes at most (the contract holds it to
    600 s), and its flag outlives it only because the pause stops the
    reconnect that would clear it."""
    if word not in AWAY_ON_PURPOSE:
        return False
    if paused_seconds is None or paused_seconds < PAUSE_ON_PURPOSE_SECONDS:
        return True
    return word == ASLEEP


def in_trouble(word: Any, paused_seconds: float | None = None) -> bool:
    """Whether the device counts as in trouble: a fault keeps it away
    (``IN_TROUBLE``), or an absence that was on purpose no longer is, a
    pause past its hour (``on_purpose``), which the alert monitor then judges
    as it judges a fault. ``paused_seconds`` is as ``on_purpose`` takes it."""
    if word in IN_TROUBLE:
        return True
    return word in AWAY_ON_PURPOSE and not on_purpose(word, paused_seconds)
