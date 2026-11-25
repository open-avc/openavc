"""The socket that asked for a macro is told how its run ended.

``macro.execute`` is acked at once (a receipt), and the run's lifecycle events
reach every client. Neither told the client that asked which ending was its
own: with two panels pressing the same macro, or a run turned away while
another was going, a client could only match the room's events to its asks by
order. ``macro.execute.result`` goes to the asking socket alone when its run
ends, with the word every other door reports.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.api.ws import _handle_message
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data: dict):
        self.sent.append(data)

    async def send_text(self, text: str):
        self.sent.append(json.loads(text))

    def types(self) -> list[str]:
        return [m.get("type") for m in self.sent]

    def results(self) -> list[dict]:
        return [m for m in self.sent if m.get("type") == "macro.execute.result"]


@pytest.fixture
def engine():
    engine = MagicMock()
    engine.state = StateStore()
    engine.events = EventBus()
    engine.state.set_event_bus(engine.events)
    devices = MagicMock()
    devices.send_command = AsyncMock()
    engine.macros = MacroEngine(engine.state, engine.events, devices)
    engine.macros.load_macros([
        {"id": "clean", "name": "Clean", "steps": [
            {"action": "state.set", "key": "var.ran", "value": True}]},
        # A write no macro may make: the step fails, the run goes on to the end.
        {"id": "fails", "name": "Fails", "steps": [
            {"action": "state.set", "key": "system.not_ours", "value": 1}]},
        {"id": "warm_up", "name": "Warm Up", "overlap": "skip", "steps": [
            {"action": "delay", "seconds": 0.2}]},
        {"id": "long", "name": "Long", "steps": [{"action": "delay", "seconds": 5}]},
    ])
    engine.isc = None
    with patch("openavc.api._engine._engine", engine):
        yield engine


async def _ask(ws: FakeWS, macro_id: str, client_type: str = "panel") -> None:
    await _handle_message(ws, {"type": "macro.execute", "macro_id": macro_id}, client_type)


async def _settle(*sockets: FakeWS, count: int = 1) -> None:
    for _ in range(200):
        if all(len(ws.results()) >= count for ws in sockets):
            return
        await asyncio.sleep(0.01)


async def test_the_asking_socket_gets_its_run_s_ending_after_the_receipt(engine):
    ws = FakeWS()
    await _ask(ws, "clean")
    await _settle(ws)

    assert ws.types() == ["macro.execute.ack", "macro.execute.result"]
    assert ws.results() == [
        {"type": "macro.execute.result", "macro_id": "clean", "outcome": "completed"}
    ]


async def test_a_run_with_a_failed_step_says_failed(engine):
    ws = FakeWS()
    await _ask(ws, "fails", "programmer")
    await _settle(ws)

    assert ws.results() == [
        {"type": "macro.execute.result", "macro_id": "fails", "outcome": "failed"}
    ]


async def test_each_socket_is_told_its_own_ending(engine):
    first, second = FakeWS(), FakeWS()
    await _ask(first, "warm_up")
    await asyncio.sleep(0.05)
    await _ask(second, "warm_up")  # turned away: the first is still running
    await _settle(first, second)

    assert first.results() == [
        {"type": "macro.execute.result", "macro_id": "warm_up", "outcome": "completed"}
    ]
    assert second.results() == [
        {"type": "macro.execute.result", "macro_id": "warm_up", "outcome": "skipped"}
    ]


async def test_a_run_somebody_cancelled_says_cancelled(engine):
    asker, stopper = FakeWS(), FakeWS()
    await _ask(asker, "long")
    await asyncio.sleep(0.05)
    await _handle_message(stopper, {"type": "macro.cancel", "macro_id": "long"}, "panel")
    await _settle(asker)

    assert asker.results() == [
        {"type": "macro.execute.result", "macro_id": "long", "outcome": "cancelled"}
    ]
    assert stopper.results() == []
