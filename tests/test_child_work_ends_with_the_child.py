"""A child's deferred reply work ends with the child.

A YAML reply rule leaves work behind it: a ``then_query`` round still to go
out, a throttle window, a write held for that window's end. That work is
for the children the reply wrote, as each was registered when it came in.
A child that leaves the roster takes its part with it, and a child that
leaves and comes back starts with none of the old one's: no follow-up line
for it goes out, no old window holds its first reply, no old held value
lands on it. The other children a round or a hold was for keep theirs.

Driven through the reply door (``on_data_received``, one frame at a time,
as a transport delivers a read) on an invented Acme matrix whose output
count the device reports, with a capturing transport and pacing off.
"""

from __future__ import annotations

import asyncio
from typing import Any

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class


def _matrix(window: float = 30) -> dict[str, Any]:
    return {
        "id": "acme_matrix",
        "name": "Acme Matrix",
        "manufacturer": "Acme",
        "category": "switchers",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 23, "outputs": 8, "poll_interval": 0},
        "config_schema": {
            "outputs": {"type": "integer", "label": "Outputs", "default": 8},
        },
        "state_variables": {
            "output_count": {"type": "integer", "label": "Output Count"},
            "power": {"type": "string", "label": "Power"},
        },
        "child_entity_types": {
            "output": {
                "label": "Output",
                "id_format": {"type": "integer", "min": 1, "max": 8},
                "instances": {"count_from": "outputs", "count_from_state": "output_count"},
                "state_variables": {
                    "available": {"type": "boolean", "label": "Available"},
                    "level": {"type": "integer", "label": "Level"},
                    "input": {"type": "integer", "label": "Input"},
                },
            },
        },
        "commands": {},
        "responses": [
            # An output's microphone back in range; read what it carries.
            {
                "match": r"^AVAIL(\d) YES$",
                "child_set": [{"type": "output", "id": "$1", "state": {"available": "true"}}],
                "then_query": ["NAME? {child_id}\r", "INFO? {child_id}\r"],
            },
            # A tie made at the front panel: it names both outputs it moved.
            {
                "match": r"^SWAP(\d),(\d)$",
                "child_set": [
                    {"type": "output", "id": "$1", "state": {"available": "true"}},
                    {"type": "output", "id": "$2", "state": {"available": "true"}},
                ],
                "then_query": ["IN? {child_id}\r"],
            },
            # A meter, one window per output.
            {
                "match": r"^LVL(\d) (\d+)$",
                "child_set": [{"type": "output", "id": "$1", "state": {"level": "$2"}}],
                "throttle": window,
            },
            # The same reading, as the poll's reply: never throttled.
            {
                "match": r"^LEVEL(\d)=(\d+)$",
                "child_set": [{"type": "output", "id": "$1", "state": {"level": "$2"}}],
            },
            # A meter page: one reply carries two outputs, one window for both.
            {
                "match": r"^PAIR(\d) (\d+) (\d) (\d+)$",
                "child_set": [
                    {"type": "output", "id": "$1", "state": {"level": "$2"}},
                    {"type": "output", "id": "$3", "state": {"level": "$4"}},
                ],
                "throttle": window,
            },
            {"match": r"^OUTS=(\d)$", "set": {"output_count": "$1"}},
            # A JSON meter for outputs 1 and 2; a body carries one or both.
            {
                "json": True,
                "throttle": window,
                "child_set": [
                    {"type": "output", "id": 1, "state": {"level": "lvl1"}},
                    {"type": "output", "id": 2, "state": {"level": "lvl2"}},
                ],
            },
        ],
    }


class Capture:
    def __init__(self) -> None:
        self.connected = True
        self.sent: list[bytes] = []
        self.after_send: Any = None

    async def wait_idle(self) -> None:
        return None

    async def send(self, data: bytes) -> None:
        self.sent.append(data)
        if self.after_send is not None:
            self.after_send(data)


def _driver(definition: dict[str, Any]) -> tuple[Any, Capture]:
    assert validate_driver_definition(definition) == []
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls(
        "dev1", {"host": "127.0.0.1", "port": 9000, "outputs": 8, "poll_interval": 0},
        state, events,
    )
    driver._register_declared_children()
    link = Capture()
    driver.transport = link
    driver._pace_lines = False
    return driver, link


async def _frames(driver: Any, *lines: str) -> None:
    for line in lines:
        await driver.on_data_received(line.encode())


async def _settle(driver: Any) -> None:
    for _ in range(10):
        await asyncio.sleep(0)
        tasks = list(driver._follow_up_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _level(driver: Any, n: int) -> Any:
    return driver.get_child_state("output", n).get("level")


def _close_windows(driver: Any) -> None:
    states = [e[3] for e in driver._compiled_responses] + [e[1] for e in driver._json_responses]
    for tstate in states:
        for timer in (tstate or {}).get("timers", {}).values():
            timer.cancel()


# ── then_query ──


async def test_a_follow_up_for_a_child_the_same_read_removed_does_not_go_out() -> None:
    """One read: output 8's notice, then the count that takes output 8 away.
    The round the notice started goes out after both, and nothing in it is
    for an output that is gone."""
    driver, link = _driver(_matrix())
    await _frames(driver, "AVAIL8 YES", "OUTS=4")
    assert not driver.is_child_registered("output", 8)
    await _settle(driver)
    assert link.sent == []


async def test_a_child_removed_mid_round_is_not_read_further() -> None:
    """Output 3 leaves after its first follow-up line went out: the second
    line, for output 3, is not sent."""
    driver, link = _driver(_matrix())

    def shrink(data: bytes) -> None:
        if data == b"NAME? 3\r":
            driver.set_state("output_count", 2)

    link.after_send = shrink
    await _frames(driver, "AVAIL3 YES")
    await _settle(driver)
    assert link.sent == [b"NAME? 3\r"]


async def test_the_other_child_of_a_round_is_still_read() -> None:
    """One notice names outputs 2 and 7; output 7 leaves before the round
    goes out. Output 2 is still read, output 7 is not."""
    driver, link = _driver(_matrix())
    await _frames(driver, "SWAP2,7", "OUTS=4")
    await _settle(driver)
    assert link.sent == [b"IN? 2\r"]


async def test_a_child_back_on_the_roster_is_read_at_once() -> None:
    """Output 8's round settled (a second notice inside the settle window
    sends nothing), then output 8 leaves and comes back: its first notice
    is a new registration's, and is read at once."""
    driver, link = _driver(_matrix())
    await _frames(driver, "AVAIL8 YES")
    await _settle(driver)
    assert link.sent == [b"NAME? 8\r", b"INFO? 8\r"]
    await _frames(driver, "AVAIL8 YES")
    await _settle(driver)
    assert link.sent == [b"NAME? 8\r", b"INFO? 8\r"]

    await _frames(driver, "OUTS=4", "OUTS=8", "AVAIL8 YES")
    await _settle(driver)
    assert link.sent == [b"NAME? 8\r", b"INFO? 8\r"] * 2


async def test_a_settle_hold_taken_before_its_child_existed_does_not_hold_it() -> None:
    """Output 3's notice while the device reports two outputs reads nothing
    for it; once output 3 is on the roster, its notice is read at once."""
    driver, link = _driver(_matrix())
    await _frames(driver, "OUTS=2", "AVAIL3 YES")
    await _settle(driver)
    assert link.sent == []
    await _frames(driver, "OUTS=8", "AVAIL3 YES")
    await _settle(driver)
    assert link.sent == [b"NAME? 3\r", b"INFO? 3\r"]


# ── throttle ──


def _windows(driver: Any) -> list[dict[str, Any]]:
    """The throttled rules' window states: the meter's, then the page's."""
    return [e[3] for e in driver._compiled_responses if e[3]]


async def test_a_child_back_on_the_roster_has_its_first_reply_written_at_once() -> None:
    """Output 3's meter opened a 30 s window; output 3 leaves and comes
    back. Its first reading is the new registration's, written at once."""
    driver, _link = _driver(_matrix())
    try:
        await _frames(driver, "LVL3 5")
        assert _level(driver, 3) == 5
        await _frames(driver, "OUTS=2", "OUTS=8", "LVL3 7")
        assert _level(driver, 3) == 7
    finally:
        _close_windows(driver)


async def test_a_window_a_reply_opened_before_its_child_existed_does_not_hold_it() -> None:
    """A reading for output 3 while the device reports two outputs writes
    nothing; once output 3 is on the roster, its first reading is written
    at once."""
    driver, _link = _driver(_matrix())
    try:
        await _frames(driver, "OUTS=2", "LVL3 5", "OUTS=8", "LVL3 6")
        assert _level(driver, 3) == 6
    finally:
        _close_windows(driver)


async def test_a_held_reading_for_a_child_that_left_is_dropped_with_its_timer() -> None:
    driver, _link = _driver(_matrix())
    try:
        await _frames(driver, "LVL3 5", "LVL3 6")
        meter = _windows(driver)[0]
        assert "output:3" in meter["timers"]
        await _frames(driver, "OUTS=2")
        assert meter.get("pending", {}) == {}
        assert meter.get("timers", {}) == {}
    finally:
        _close_windows(driver)


async def test_a_reply_for_another_child_already_on_the_roster_is_held() -> None:
    """A JSON meter whose bodies carry output 1's reading, then output 2's:
    both outputs were on the roster when the first body opened the window,
    so the second body is held for it."""
    driver, _link = _driver(_matrix())
    try:
        await _frames(driver, '{"lvl1": 5}', '{"lvl2": 6}')
        assert _level(driver, 1) == 5
        assert _level(driver, 2) is None
        await _frames(driver, '{"lvl1": 7}')
        assert _level(driver, 1) == 5
    finally:
        _close_windows(driver)


async def test_a_held_reading_does_not_land_on_the_child_that_came_back() -> None:
    """A reading held for output 3's window; output 3 leaves and comes back
    and the poll reads it again, at the value it had when the reading was
    held. The window ends: the old output's held reading does not replace
    the new one's."""
    driver, _link = _driver(_matrix(window=0.05))
    try:
        await _frames(driver, "LVL3 5", "LVL3 6")
        await _frames(driver, "OUTS=2", "OUTS=8", "LEVEL3=5")
        await asyncio.sleep(0.15)
        assert _level(driver, 3) == 5
    finally:
        _close_windows(driver)


async def test_the_other_child_of_a_held_page_still_lands() -> None:
    """A held page carries outputs 2 and 7; output 7 leaves. Output 2's
    held reading still lands when the window ends."""
    driver, _link = _driver(_matrix(window=0.05))
    try:
        await _frames(driver, "PAIR2 1 7 1", "PAIR2 4 7 9", "OUTS=4")
        await asyncio.sleep(0.15)
        assert _level(driver, 2) == 4
        assert not driver.is_child_registered("output", 7)
    finally:
        _close_windows(driver)
