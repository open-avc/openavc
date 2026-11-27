"""The panel reads a device's presence word, holding the server's words.

``core/device_presence.py`` decides what a device's state adds up to and the
device manager publishes it as ``device.<id>.presence``. The panel's
availability (``web/panel/modules/availability.js``) reads that word for
whether a reading can be believed, whether a press can land and whether a
wake control keeps its reading. Three things keep it one rule:

* the word that means up there is the server's one word that takes
  commands, so a reading the panel believes and a press it leaves unmarked
  are what the command gate lets through;
* the word that keeps a wake control live is the server's ``asleep``;
* no panel module reads the parts the word is made of (``connected``,
  ``asleep``, ``paused``, ``restarting``, ``orphaned``) to decide for itself.

Read with a regex rather than executed, like ``test_ide_presence_mirror.py``:
the point is to catch one side being edited without the other. What the
panel draws for each word is ``tests/test_panel_js.py``'s
``availability_reads_the_presence_word``.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.core.device_presence import ASLEEP, PRESENCE_WORDS, TAKES_COMMANDS

REPO = Path(__file__).resolve().parents[1]
PANEL = REPO / "openavc" / "web" / "panel"
AVAILABILITY = PANEL / "modules" / "availability.js"

#: The parts of the word a module might read to decide for itself.
_PARTS = ("connected", "asleep", "paused", "restarting", "orphaned")

#: A device key for one of them read out of state (`device.${id}.asleep`), or
#: a changed key matched on its end (`key.endsWith('.connected')`).
_PART_READ = re.compile(
    r"device\.\$\{[^}]+\}\.(" + "|".join(_PARTS) + r")\b(?![_\w])"
    r"|endsWith\(\s*['\"]\.(" + "|".join(_PARTS) + r")['\"]\s*\)"
)


def _word(name: str) -> str:
    src = AVAILABILITY.read_text(encoding="utf-8")
    match = re.search(r"^const " + name + r"\s*=\s*'([^']*)';", src, re.MULTILINE)
    assert match, f"found no {name} in availability.js -- the regex is stale"
    return match.group(1)


def test_the_word_the_panel_reads_as_up_is_the_one_that_takes_commands() -> None:
    assert TAKES_COMMANDS == frozenset({_word("ONLINE")}), (
        "core/device_presence.py TAKES_COMMANDS is no longer the one word the panel "
        "reads as up (availability.js ONLINE): the panel would mark a press the "
        "server lets through, or leave one unmarked that it refuses"
    )


def test_the_word_that_keeps_a_wake_control_live_is_asleep() -> None:
    assert _word("ASLEEP") == ASLEEP
    assert ASLEEP in PRESENCE_WORDS


def test_availability_reads_the_presence_key() -> None:
    src = AVAILABILITY.read_text(encoding="utf-8")
    assert "`device.${deviceId}.presence`" in src, "availability.js no longer reads the presence key"


def test_no_panel_module_reads_the_parts_of_the_word() -> None:
    offenders = []
    for path in sorted(PANEL.rglob("*.js")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PART_READ.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert not offenders, (
        "a panel module reads a part of a device's presence word to decide for "
        "itself; read device.<id>.presence through modules/availability.js:\n"
        + "\n".join(offenders)
    )


def test_the_part_regex_still_sees_a_read() -> None:
    for line in ("state()[`device.${deviceId}.connected`] === false",
                 "return key.endsWith('.asleep') && parts.length === 3"):
        assert _PART_READ.search(line), line
    for line in ("state()[`device.${deviceId}.presence`]",
                 "'connected', 'enabled', 'name', 'offline_detail',"):
        assert not _PART_READ.search(line), line
