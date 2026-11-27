"""What a device's state adds up to, in one word: THE presence rule.

``device.<id>.presence`` holds one word from ``PRESENCE_WORDS``. The device
manager writes it (core/device_away.py) in the same batch as every change to
a part it is made of, so whoever judges ``connected`` the moment it is
written reads the word that goes with it. Each reader asks this module what
a word means rather than combining the parts itself: the alert monitor
whether the absence is on purpose, the cloud heartbeat whether the device is
in trouble, the command gate whether a command goes out.

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
marks a press that reaches nothing up, and ``asleep`` keeps a wake control's
reading; tests/test_panel_presence_mirror.py holds its two words to these.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

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

# Absence we asked for: no offline alert fires for it.
AWAY_ON_PURPOSE: frozenset[str] = frozenset({RESTARTING, ASLEEP})
# A command sent now goes out. So does one declaring available_offline, and
# any command while a held connection is open (see above).
TAKES_COMMANDS: frozenset[str] = frozenset({ONLINE})
# Counted as a device in trouble: a fault is what keeps it away.
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
