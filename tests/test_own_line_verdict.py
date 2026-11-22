"""Every line a YAML driver sends on its own takes one step before it goes:
it waits for its slot, then its ``when_state`` gates are judged again
(``QueryEntries._line_verdict``). What a shut gate does to the rest is each
sender's own, and this file runs one table of gate states through all of
them: start-up on a byte stream, an OSC start-up item, an OSC start-up
``each_child`` entry, OSC start-up's ask of every reply address, the poll, a
reply rule's follow-up round, a command's read-back round and the liveness
probe.

In every row the device's answer to the first line shuts the gates the
column names (a standby notice, say), so each later line is judged after it.
Each sender sends three lines: the first, the one the entry's gate holds
(the second, for a sender that judges no entry), and the last; the probe
sends two, a poll line and then itself. Invented devices ("acme_*") and
synthetic payloads throughout; pacing off.
"""

from __future__ import annotations

import asyncio
import copy
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.osc import OSCTransport
from openavc.transport.osc_codec import osc_encode_message

ENTRY_GATE = {"standby": False}
POLL_GATE = {"asleep": False}

BASE: dict[str, Any] = {
    "name": "Acme Unit",
    "manufacturer": "Acme",
    "category": "utility",
    "default_config": {"host": "", "port": 23},
    "state_variables": {
        "standby": {"type": "boolean", "label": "Standby"},
        "asleep": {"type": "boolean", "label": "Asleep"},
    },
    "child_entity_types": {
        "input": {"label": "Input", "id_format": {"type": "integer"}, "state_variables": {}},
    },
    "commands": {},
    "responses": [],
    "polling": {"queries": [], "when_state": POLL_GATE},
}


def _device(transport: str, **extra: Any) -> dict[str, Any]:
    out = copy.deepcopy(BASE)
    out.update(id=f"acme_unit_{transport}", transport=transport)
    if transport != "osc":
        out["delimiter"] = "\\r"
    out.update(copy.deepcopy(extra))
    return out


class Capture:
    """Records what is sent and lets the device answer each send
    (``on_send``) before the next line is judged."""

    connected = True

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.on_send: Callable[[bytes], None] | None = None

    async def wait_idle(self) -> None:
        return None

    async def send(self, data: bytes) -> None:
        self.sent.append(data)
        if self.on_send is not None:
            self.on_send(data)


class OscCapture(OSCTransport, Capture):
    connected = True
    _tcp_mode = False
    _udp = None

    def __init__(self) -> None:
        Capture.__init__(self)

    send = Capture.send
    wait_idle = Capture.wait_idle


def _osc(address: str) -> bytes:
    return osc_encode_message(address, [])


def _driver(definition: dict[str, Any], link: Capture) -> Any:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 9000}, state, events)
    driver.transport = link
    driver._pace_lines = False
    for local_id in (1, 2):
        driver.register_child("input", local_id)
    return driver


async def _settle(driver: Any) -> None:
    for _ in range(5):
        tasks = list(driver._follow_up_tasks) + list(driver._read_back_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(frozen=True)
class Sender:
    """One sender: its device, how it is run, and its lines in order (the
    first, the one a shut gate holds, the last; the probe has no last)."""

    definition: dict[str, Any]
    run: Callable[[Any], Awaitable[None]]
    lines: tuple[bytes, ...]
    osc: bool = False


async def _start_up(driver: Any) -> None:
    await driver._run_on_connect()


async def _poll(driver: Any) -> None:
    await driver.poll()


GATED_EACH_LINE = {"each_child": "input", "send": "M{child_id}\\r", "when_state": ENTRY_GATE}
GATED_EACH_OSC = {"each_child": "input", "send": "/in/{child_id}", "when_state": ENTRY_GATE}


async def _follow_up(driver: Any) -> None:
    driver._follow_up(object(), [GATED_EACH_LINE, "AFTER\\r"], "", [])
    await _settle(driver)


async def _read_back(driver: Any) -> None:
    sends = [(f"q{n}", ("line", line)) for n, line in enumerate((b"ONE\r", b"TWO\r", b"THREE\r"))]
    await driver._read_back_round("set_level", sends)


async def _probe(driver: Any) -> None:
    await driver.poll()
    if await driver._liveness_held():
        return
    probe = asyncio.ensure_future(driver._liveness_probe())
    await asyncio.sleep(0)
    driver._liveness_note_data(b"PONG\r")
    await probe


SENDERS: dict[str, Sender] = {
    "start-up": Sender(
        _device("tcp", on_connect=[GATED_EACH_LINE, "AFTER\\r"]),
        _start_up, (b"M1\r", b"M2\r", b"AFTER\r"),
    ),
    "OSC start-up item": Sender(
        _device("osc", on_connect=["/one", {"send": "/two", "when_state": ENTRY_GATE}, "/three"]),
        _start_up, (_osc("/one"), _osc("/two"), _osc("/three")), osc=True,
    ),
    "OSC start-up each_child": Sender(
        _device("osc", on_connect=[GATED_EACH_OSC, "/after"]),
        _start_up, (_osc("/in/1"), _osc("/in/2"), _osc("/after")), osc=True,
    ),
    "OSC reply-address asks": Sender(
        _device("osc", on_connect=["/one"], responses=[
            {"address": address, "mappings": [{"arg": 0, "state": "standby", "type": "boolean"}]}
            for address in ("/two", "/three")
        ]),
        _start_up, (_osc("/one"), _osc("/two"), _osc("/three")), osc=True,
    ),
    "poll": Sender(
        _device("tcp", polling={"queries": [GATED_EACH_LINE, "AFTER\\r"], "when_state": POLL_GATE}),
        _poll, (b"M1\r", b"M2\r", b"AFTER\r"),
    ),
    "follow-up": Sender(_device("tcp"), _follow_up, (b"M1\r", b"M2\r", b"AFTER\r")),
    "read-back": Sender(_device("tcp"), _read_back, (b"ONE\r", b"TWO\r", b"THREE\r")),
    "liveness probe": Sender(
        _device(
            "tcp",
            polling={"queries": ["ONE\\r"], "when_state": POLL_GATE},
            liveness={"send": "PING\\r"},
        ),
        _probe, (b"ONE\r", b"PING\r"),
    ),
}

# What each sender does with the line a shut gate holds: sends it and the
# rest (ALL), skips that one line and goes on (SKIP), or sends nothing more
# (END). A sender that judges no entry is untouched by the entry's gate, and
# start-up judges no poll gate.
ALL, SKIP, END = "every line", "skips the held line", "sends nothing more"

# sender                       entry gate  poll gate  both    the poll's pause logged
TABLE: list[tuple[str, str, str, str, bool]] = [
    ("start-up",                  SKIP,       ALL,       SKIP,   False),
    ("OSC start-up item",         SKIP,       ALL,       SKIP,   False),
    ("OSC start-up each_child",   SKIP,       ALL,       SKIP,   False),
    ("OSC reply-address asks",    ALL,        END,       END,    True),
    ("poll",                      SKIP,       END,       END,    True),
    ("follow-up",                 SKIP,       END,       END,    False),
    ("read-back",                 ALL,        END,       END,    False),
    ("liveness probe",            ALL,        END,       END,    False),
]

# Which state variables the device's answer to the first line flips.
SHUTS = {"open": (), "entry": ("standby",), "poll": ("asleep",), "both": ("standby", "asleep")}


def _expected(lines: tuple[bytes, ...], outcome: str) -> list[bytes]:
    if outcome == END:
        return list(lines[:1])
    if outcome == SKIP:
        return [lines[0], *lines[2:]]
    return list(lines)


def _cases() -> list[Any]:
    cases = []
    for name, entry, poll, both, loud in TABLE:
        for shut, outcome in (("open", ALL), ("entry", entry), ("poll", poll), ("both", both)):
            cases.append(pytest.param(name, shut, outcome, loud, id=f"{name}-{shut}"))
    return cases


def test_the_table_names_every_sender() -> None:
    assert [row[0] for row in TABLE] == list(SENDERS)


@pytest.mark.parametrize(("name", "shut", "outcome", "loud"), _cases())
async def test_each_sender_reads_the_gates_verdict_its_own_way(
    name: str, shut: str, outcome: str, loud: bool,
) -> None:
    sender = SENDERS[name]
    link = OscCapture() if sender.osc else Capture()
    driver = _driver(sender.definition, link)
    driver.set_state("standby", False)
    driver.set_state("asleep", False)

    def answer(data: bytes) -> None:
        if len(link.sent) == 1:
            for var in SHUTS[shut]:
                driver.set_state(var, True)

    link.on_send = answer
    await sender.run(driver)
    assert link.sent == _expected(sender.lines, outcome)
    # Only the poll and the ask that stands in for it say the poll paused;
    # every other sender judges the poll's gate quietly.
    assert driver._poll_waits_on_device() is (loud and "asleep" in SHUTS[shut])


# ── Nothing judges a line's gate or paces it but the one step ──

_DRIVERS = pathlib.Path(__file__).resolve().parent.parent / "openavc" / "drivers"
_HALVES = re.compile(r"\._(pace_line|line_gate_shut)\(")


def test_only_the_one_step_paces_a_line_or_judges_its_gates() -> None:
    callers = {
        path.name
        for path in _DRIVERS.glob("*.py")
        if _HALVES.search(path.read_text(encoding="utf-8"))
    }
    assert callers == {"query_entries.py"}
