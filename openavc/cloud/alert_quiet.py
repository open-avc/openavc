"""Which devices the alert monitor leaves alone, and when that ends: the part
of cloud/alert_monitor.py that reads a device's presence word.

A device away on purpose (core/device_presence.py ``on_purpose``: asleep,
inside a commanded restart, or paused by Live Test or a device audit, for a
pause's first hour) raises no offline alert: a rule on one of its presence
keys neither arms nor fires. Three moments need more than the word:

- A device whose quiet ends while it dials back (a pause lifted, a Reconnect
  pressed) reads ``connecting`` until the dial has its answer, and stays quiet
  until then, ``COMING_BACK_SECONDS`` at most.
- A pause that outlasts its hour writes nothing as it passes, and the monitor
  judges only what is written, so its tick judges that device again (an
  alert already raised is not raised twice).
- An alert about a paused device says who holds the pause and since when.

The pause itself is the device manager's (core/device_pause.py
``pause_held``), handed over with ``read_pauses_from``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from openavc.core.device_presence import AWAY_ON_PURPOSE, CONNECTING, on_purpose

if TYPE_CHECKING:
    from openavc.core.device_pause import HeldPause
    from openavc.core.state_store import StateStore

# How long a device whose quiet ended stays quiet while it reads connecting:
# the dial that ends a pause or a Reconnect has its answer well within it.
COMING_BACK_SECONDS = 120.0


def _judged(word: Any) -> bool:
    """A word the rules judge: not on purpose, and not a deleted word (the
    device is being removed, or taken down to be built again)."""
    return word is not None and word not in AWAY_ON_PURPOSE


def _word_key(parts: list[str]) -> bool:
    """``device.<id>.presence``, split on its dots."""
    return len(parts) == 3 and (parts[0], parts[2]) == ("device", "presence")


class QuietDevices:
    """The devices the alert monitor leaves alone, read from state."""

    def __init__(self, state: StateStore) -> None:
        self._state = state
        self._pauses: Callable[[str], HeldPause | None] | None = None
        # Device id -> since when its word has read connecting after a quiet.
        self._coming_back: dict[str, float] = {}

    def read_pauses_from(self, pauses: Callable[[str], HeldPause | None]) -> None:
        """Ask ``pauses`` (the device manager's ``pause_held``) for a pause."""
        self._pauses = pauses

    def pause_of(self, device_id: str | None) -> HeldPause | None:
        """The device's pause, or None when it is not paused (or nothing was
        handed over to ask)."""
        if self._pauses is None or device_id is None:
            return None
        return self._pauses(device_id)

    def quiet(self, device_id: str) -> bool:
        """True while the device is away on purpose, or dialing back from it."""
        word = self._state.get(f"device.{device_id}.presence")
        if word == CONNECTING and device_id in self._coming_back:
            return True
        pause = self.pause_of(device_id)
        return on_purpose(word, pause.seconds(time.time()) if pause is not None else None)

    def quiet_ended(self, parts: list[str], old: Any, new: Any) -> bool:
        """Follow a change to a state key, split on its dots: True when it is
        the device's word, leaving its quiet for a word the rules judge, so
        its presence keys are judged again. A word that leaves for
        ``connecting`` ends nothing yet."""
        if not _word_key(parts):
            return False
        device_id = parts[1]
        was_quiet = old in AWAY_ON_PURPOSE or device_id in self._coming_back
        if new == CONNECTING and was_quiet:
            self._coming_back.setdefault(device_id, time.time())
            return False
        self._coming_back.pop(device_id, None)
        return was_quiet and _judged(new)

    def due(self, now: float) -> list[str]:
        """The devices to judge again on this tick: still connecting long
        after their quiet ended (nothing is coming), and paused past their
        hour."""
        waited = [d for d, since in self._coming_back.items() if now - since >= COMING_BACK_SECONDS]
        for device_id in waited:
            del self._coming_back[device_id]
        return waited + self._past_the_hour()

    def _past_the_hour(self) -> list[str]:
        past = []
        for key, word in self._state.get_matching("device.*.presence").items():
            parts = key.split(".")
            if _word_key(parts) and self._quiet_but_for_the_hour(parts[1], word):
                past.append(parts[1])
        return past

    def _quiet_but_for_the_hour(self, device_id: str, word: Any) -> bool:
        """The device is paused, its word is on purpose, and its pause has
        outlasted the hour that kept it quiet."""
        if word not in AWAY_ON_PURPOSE or self.pause_of(device_id) is None:
            return False
        return not self.quiet(device_id)

    def naming_the_pause(
        self, device_id: str | None, message: str, detail: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """An alert about a paused device says who holds the pause and since
        when, in its message and its detail. Its offline alerts wait for the
        hour to pass; a rule on another of its keys fires when it would."""
        pause = self.pause_of(device_id)
        if pause is None:
            return message, detail
        words = pause.sentence()
        return f"{message}, {words}", {**detail, "pause": words}
