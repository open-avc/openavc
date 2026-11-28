"""The Programmer reads a device's presence word through one module, holding the server's words.

``core/device_presence.py`` decides what a device's state adds up to and the
device manager publishes it as ``device.<id>.presence``. The Programmer's
status dot, device page banner, device list filters, Dashboard, child-entity
helper and device pickers read that word through ``devicePresence.ts`` and
draw it their own way. Two things keep it one rule:

* the word list there is the server's, in the server's order, and a word
  the IDE draws as away on purpose is never one the server counts as trouble
  or takes commands for;
* no other module in the Programmer reads the parts the word is made of
  (``asleep``, ``paused``, ``restarting``, ``orphaned``) to decide for itself,
  which is how one paused device read amber on its page, "offline" in the
  list and red on the Dashboard.

Read with a regex rather than executed, like ``test_theme_studio_names_mirror.py``:
the point is to catch one side being edited without the other.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.core.device_presence import (
    AWAY_ON_PURPOSE,
    IN_TROUBLE,
    PRESENCE_WORDS,
    TAKES_COMMANDS,
    TAKES_NO_COMMAND,
)

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "openavc" / "web" / "programmer" / "src"
READER = SRC / "components" / "shared" / "devicePresence.ts"

#: The parts of the word a module might read to decide for itself.
_PARTS = ("asleep", "paused", "restarting", "orphaned")

#: A live-state key for one of them (``device.${id}.paused``) or a fetched
#: state's field (``state?.asleep``); ``paused_for`` and the like are not.
_PART_READ = re.compile(
    r"(?:device\.\$\{[^}]+\}\.|state\??\.)(" + "|".join(_PARTS) + r")\b(?![_\w])"
)

#: Modules that read a part for a question the word does not answer, and why.
_READS_A_PART = {
    # Which drivers to offer to install: a fact about the driver, asked of
    # every orphaned device whatever else it is.
    "components/shared/MissingDriversWatcher.tsx",
    # Offers a script the key's name to type (`device.<id>.asleep` is in
    # live state only while the device sleeps); it reads no value.
    "components/scripts/scriptCompletions.ts",
}


def _array(name: str) -> list[str]:
    src = READER.read_text(encoding="utf-8")
    match = re.search(r"export const " + name + r"[^=]*=\s*(?:new Set<[^>]+>\()?\[([^\]]*)\]", src)
    assert match, f"found no {name} in devicePresence.ts -- the regex is stale"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def test_the_ide_holds_the_servers_words_in_the_servers_order() -> None:
    assert tuple(_array("PRESENCE_WORDS")) == PRESENCE_WORDS


def test_what_the_ide_draws_as_asked_for_is_never_trouble_and_never_takes_a_command() -> None:
    asked_for = set(_array("AWAY_ASKED_FOR"))
    assert asked_for <= set(PRESENCE_WORDS)
    assert AWAY_ON_PURPOSE <= asked_for, "the IDE draws as a fault a word the server keeps quiet"
    assert not asked_for & IN_TROUBLE
    assert not asked_for & TAKES_COMMANDS


def test_the_words_a_converted_macro_skips_a_wake_on_are_the_servers() -> None:
    """A macro turned into a script skips a wake command to a device the
    command gate refuses it to (components/macros/macroHelpers.ts)."""
    words = _array("TAKES_NO_COMMAND")
    assert frozenset(words) == TAKES_NO_COMMAND
    assert words == [w for w in PRESENCE_WORDS if w in TAKES_NO_COMMAND], "not in the server's order"


def test_no_other_programmer_module_reads_the_parts_of_the_word() -> None:
    found = []
    for path in sorted(SRC.rglob("*.ts*")):
        rel = path.relative_to(SRC).as_posix()
        if ".test." in rel or rel.startswith("test/") or rel in _READS_A_PART:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PART_READ.search(line):
                found.append(f"{rel}:{number}: {line.strip()}")
    assert not found, (
        "Read the device's presence word (components/shared/devicePresence.ts) instead:\n"
        + "\n".join(found)
    )


def test_the_tripwire_sees_the_reads_it_is_there_to_catch() -> None:
    for line in (
        "liveState[`device.${d.id}.asleep`] === true",
        "s.liveState[`device.${deviceId}.paused`]",
        "deviceInfo?.state?.restarting",
        "info.state.orphaned",
    ):
        assert _PART_READ.search(line), line
    for line in (
        "s.liveState[`device.${deviceId}.paused_for`]",
        "liveState[`device.${id}.connected`]",
        'presence === "asleep"',
    ):
        assert not _PART_READ.search(line), line
