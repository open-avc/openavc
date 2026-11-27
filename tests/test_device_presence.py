"""``device.<id>.presence``: one word for what a device's state adds up to.

The words and their order are core/device_presence.py's; the device manager
publishes the word in the same batch as every change to a part of it. The
readers' answers for each state are pinned in
tests/test_device_presence_readers.py.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import fields

import pytest

from openavc.core import device_presence as presence
from openavc.core.device_manager import DeviceManager
from openavc.core.device_presence import PresenceParts, presence_word


def test_the_parts_are_the_words_in_their_order():
    assert [f.name for f in fields(PresenceParts)] == list(presence.PRESENCE_WORDS[:-1])
    assert presence.PRESENCE_WORDS[-1] == presence.CONNECTING


@pytest.mark.parametrize("word", presence.PRESENCE_WORDS[:-1])
def test_each_part_alone_is_its_word(word):
    assert presence_word(PresenceParts(**{word: True})) == word


def test_nothing_true_is_connecting():
    assert presence_word(PresenceParts()) == presence.CONNECTING


@pytest.mark.parametrize(("parts", "word"), [
    # Connected wins over a restart window or a sleep armed while it is up.
    ({"online": True, "restarting": True, "asleep": True}, presence.ONLINE),
    # A device put to sleep or restarting on purpose stays so while paused.
    ({"asleep": True, "paused": True}, presence.ASLEEP),
    ({"restarting": True, "paused": True}, presence.RESTARTING),
    # A pause outranks the fault the device had when it was paused.
    ({"paused": True, "offline": True}, presence.PAUSED),
    ({"disabled": True, "offline": True}, presence.DISABLED),
])
def test_the_first_part_that_holds_names_the_word(parts, word):
    assert presence_word(PresenceParts(**parts)) == word


def test_what_each_reader_asks_is_a_set_of_words():
    words = set(presence.PRESENCE_WORDS)
    for readers_set in (presence.AWAY_ON_PURPOSE, presence.TAKES_COMMANDS, presence.IN_TROUBLE):
        assert readers_set <= words
    assert presence.AWAY_ON_PURPOSE == {presence.RESTARTING, presence.ASLEEP, presence.PAUSED}
    assert presence.TAKES_COMMANDS == {presence.ONLINE}
    assert presence.IN_TROUBLE == {presence.OFFLINE}


HOUR = presence.PAUSE_ON_PURPOSE_SECONDS


@pytest.mark.parametrize(("word", "paused_seconds", "quiet"), [
    (presence.ASLEEP, None, True),
    (presence.RESTARTING, None, True),
    (presence.PAUSED, 0.0, True),
    (presence.PAUSED, HOUR - 1, True),
    # Past the hour a pause is judged as a fault is, and only a sleep stays
    # quiet: a restart window is over long before.
    (presence.PAUSED, HOUR, False),
    (presence.RESTARTING, HOUR, False),
    (presence.ASLEEP, HOUR, True),
    (presence.OFFLINE, None, False),
    (presence.CONNECTING, 10.0, False),
    (None, None, False),
])
def test_on_purpose(word, paused_seconds, quiet):
    assert presence.on_purpose(word, paused_seconds) is quiet


def test_the_hour_outlasts_a_restart_window():
    from openavc.drivers.restart_window import RESTART_SECONDS_MAX

    assert RESTART_SECONDS_MAX < HOUR


# A write of a part of the word straight to the store, past the door that
# writes the word with it: state.set(f"device.{device_id}.paused", ...) or
# state.set(f"{prefix}connected", ...).
_PARTS = "connected|paused|enabled|orphaned|restarting|asleep|offline_reason"
_DIRECT_WRITE = re.compile(
    rf'state\.set(?:_batch)?\(\s*f"(?:device\.\{{\w+\}}\.|\{{prefix\}})(?:{_PARTS})"'
)


def test_the_device_manager_writes_each_part_through_its_door():
    modules = {inspect.getmodule(cls) for cls in DeviceManager.__mro__}
    offenders = [
        f"{module.__name__}: {match.group(0)}"
        for module in modules if module and module.__name__.startswith("openavc.")
        for match in _DIRECT_WRITE.finditer(inspect.getsource(module))
    ]
    assert offenders == []


def test_the_scan_would_see_a_direct_write():
    assert _DIRECT_WRITE.search('self.state.set(f"device.{device_id}.paused", True)')
    assert _DIRECT_WRITE.search('self.state.set(f"{prefix}connected", False)')
    assert not _DIRECT_WRITE.search('self.state.set(f"device.{device_id}.paused_for", x)')


def test_connected_is_written_when_the_word_cannot_be_worked_out():
    from openavc.core.state_store import StateStore
    from openavc.drivers.state_writes import StateWrites

    class _Driver(StateWrites):
        DRIVER_INFO: dict = {}
        device_id = "widget"

        def __init__(self) -> None:
            self.state = StateStore()
            self._presence = self._broken

        @staticmethod
        def _broken(values):
            raise RuntimeError("no word")

    driver = _Driver()
    driver._write_state({"connected": True})
    assert driver.state.get("device.widget.connected") is True
    assert not driver.state.has("device.widget.presence")
