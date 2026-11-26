"""The socket that asked for a macro is told how its run ended.

``macro.execute`` is acked at once (a receipt), and the run's lifecycle events
reach every client. Neither told the client that asked which ending was its
own: with two panels pressing the same macro, or a run turned away while
another was going, a client could only match the room's events to its asks by
order. ``macro.execute.result`` goes to the asking socket alone when its run
ends, with the word every other door reports.

One socket can hold several asks of the same macro at once (every Node-RED
flow on a server shares one), so an ask may carry a ``request_id`` that its
receipt, its result and a failure after the receipt all echo. The result says
the rest of the run's own ending too: a skipped run's reason from the reason
table, a failed run's own step errors (never another run's of the same macro),
and why a run that stopped did.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.api.ws import _handle_message
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.macro_text import skip_reason
from openavc.core.state_store import StateStore
from openavc.core.ws_hub import WSHub


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
        {"id": "stops", "name": "Stops", "stop_on_error": True, "steps": [
            {"action": "device.command", "device": "projector", "command": "power_on"},
            {"action": "state.set", "key": "var.after", "value": True}]},
        {"id": "calls_fails", "name": "Calls Fails", "steps": [
            {"action": "macro", "macro": "fails"}]},
        # Runs side by side (the default overlap); only the first command fails.
        {"id": "power", "name": "Power", "steps": [
            {"action": "delay", "seconds": 0.1},
            {"action": "device.command", "device": "projector", "command": "power_on"}]},
    ])
    engine.isc = None
    engine.ws = WSHub(engine.state)
    with patch("openavc.api._engine._engine", engine):
        yield engine


async def _ask(
    ws: FakeWS, macro_id: str, client_type: str = "panel", **extra,
) -> None:
    await _handle_message(
        ws, {"type": "macro.execute", "macro_id": macro_id, **extra}, client_type,
    )


def _step_errors(events: EventBus) -> list[dict]:
    """Every ``macro.step_error`` the room hears, in order."""
    seen: list[dict] = []

    async def _on(_event, payload):
        seen.append(payload)

    events.on("macro.step_error.*", _on)
    return seen


async def _until(predicate, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


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


async def test_a_run_with_a_failed_step_says_failed_and_which_step(engine):
    room = _step_errors(engine.events)
    ws = FakeWS()
    await _ask(ws, "fails", "programmer")
    await _settle(ws)

    # The run carried on past it: the step's own frame, and no reason it stopped.
    assert len(room) == 1
    assert ws.results() == [
        {"type": "macro.execute.result", "macro_id": "fails", "outcome": "failed",
         "step_errors": room}
    ]


async def test_a_sub_macro_s_failed_step_is_the_run_s(engine):
    room = _step_errors(engine.events)
    ws = FakeWS()
    await _ask(ws, "calls_fails", "programmer")
    await _settle(ws)

    # One frame, sent where it happened, naming the sub-macro and its caller.
    [frame] = room
    assert (frame["macro_id"], frame["call_chain"]) == ("fails", ["calls_fails", "fails"])
    [result] = ws.results()
    assert (result["outcome"], result["step_errors"]) == ("failed", room)


async def test_a_run_that_stopped_says_why(engine):
    room = _step_errors(engine.events)
    stopped: list[dict] = []

    async def _on_error(_event, payload):
        stopped.append(payload)

    engine.events.on("macro.error.*", _on_error)
    engine.macros.devices.send_command = AsyncMock(side_effect=RuntimeError("no answer"))
    ws = FakeWS()
    await _ask(ws, "stops", "programmer")
    await _settle(ws)

    [result] = ws.results()
    assert result["outcome"] == "failed"
    assert result["step_errors"] == room
    # The words the room's macro.error carried, for this run.
    assert (result["error"], result["message"]) == (stopped[0]["error"], stopped[0]["message"])
    assert engine.state.get("var.after") is None


async def test_a_panel_is_told_a_failure_in_the_room_s_words(engine):
    # What the hub sends a panel of macro.error and macro.step_error: the
    # sentence for the room, never the Programmer's text (here a driver's).
    engine.macros.devices.send_command = AsyncMock(
        side_effect=RuntimeError("telnet: bad password for admin"),
    )
    ws = FakeWS()
    await _ask(ws, "stops", "panel")
    await _settle(ws)

    [result] = ws.results()
    assert "bad password" not in json.dumps(result)
    assert result["error"] == result["message"]
    [step] = result["step_errors"]
    assert step["error"] == step["message"]


async def test_another_run_s_step_errors_stay_off_this_result(engine):
    engine.macros.devices.send_command = AsyncMock(
        side_effect=[RuntimeError("no answer"), None],
    )
    first, second = FakeWS(), FakeWS()
    await _ask(first, "power", "programmer")
    await _until(lambda: engine.macros.is_macro_running("power"))
    await _ask(second, "power", "programmer")
    await _settle(first, second)

    [failed] = first.results()
    assert failed["outcome"] == "failed"
    assert [e["step_index"] for e in failed["step_errors"]] == [1]
    assert second.results() == [
        {"type": "macro.execute.result", "macro_id": "power", "outcome": "completed"}
    ]


async def test_a_skipped_run_says_the_macro_s_reason(engine):
    first, second = FakeWS(), FakeWS()
    await _ask(first, "warm_up")
    await _until(lambda: engine.macros.is_macro_running("warm_up"))
    await _ask(second, "warm_up")
    await _settle(first, second)

    assert second.results() == [
        {"type": "macro.execute.result", "macro_id": "warm_up", "outcome": "skipped",
         **skip_reason("macro_overlap")}
    ]


async def test_the_ask_s_request_id_comes_back_on_its_receipt_and_its_result(engine):
    ws = FakeWS()
    await _ask(ws, "clean", "programmer", request_id="flow-7")
    await _settle(ws)

    assert ws.sent == [
        {"type": "macro.execute.ack", "macro_id": "clean", "request_id": "flow-7"},
        {"type": "macro.execute.result", "macro_id": "clean", "request_id": "flow-7",
         "outcome": "completed"},
    ]


async def test_two_asks_on_one_socket_are_each_told_their_own(engine):
    ws = FakeWS()
    await _ask(ws, "warm_up", "programmer", request_id="a")
    await _until(lambda: engine.macros.is_macro_running("warm_up"))
    await _ask(ws, "warm_up", "programmer", request_id="b")
    await _settle(ws, count=2)

    # The second ask is turned away first; its id, not its order, says so.
    assert [(r["request_id"], r["outcome"]) for r in ws.results()] == [
        ("b", "skipped"), ("a", "completed"),
    ]


@pytest.mark.parametrize("bad", [7, "", "x" * 65, ["a"]])
async def test_a_request_id_that_is_not_short_text_is_refused_before_the_run(engine, bad):
    ws = FakeWS()
    await _ask(ws, "clean", "programmer", request_id=bad)
    await asyncio.sleep(0.05)

    assert ws.types() == ["error"]
    assert ws.sent[0]["source_type"] == "macro.execute"
    assert "request_id" in ws.sent[0]["message"]
    assert engine.state.get("var.ran") is None


async def test_a_run_that_raised_names_the_ask_it_answers(engine):
    engine.macros.execute = AsyncMock(side_effect=RuntimeError("boom"))
    ws = FakeWS()
    await _ask(ws, "clean", "programmer", request_id="r1")
    assert await _until(lambda: "error" in ws.types())

    assert ws.types() == ["macro.execute.ack", "error"]
    assert ws.sent[1]["source_type"] == "macro.execute"
    assert ws.sent[1]["request_id"] == "r1"


async def test_each_socket_is_told_its_own_ending(engine):
    first, second = FakeWS(), FakeWS()
    await _ask(first, "warm_up")
    await _until(lambda: engine.macros.is_macro_running("warm_up"))
    await _ask(second, "warm_up")  # turned away: the first is still running
    await _settle(first, second)

    assert first.results() == [
        {"type": "macro.execute.result", "macro_id": "warm_up", "outcome": "completed"}
    ]
    assert [r["outcome"] for r in second.results()] == ["skipped"]


async def test_a_run_somebody_cancelled_says_cancelled(engine):
    asker, stopper = FakeWS(), FakeWS()
    await _ask(asker, "long")
    await _until(lambda: engine.macros.is_macro_running("long"))
    await _handle_message(stopper, {"type": "macro.cancel", "macro_id": "long"}, "panel")
    await _settle(asker)

    assert asker.results() == [
        {"type": "macro.execute.result", "macro_id": "long", "outcome": "cancelled"}
    ]
    assert stopper.results() == []


class SlowToAck(FakeWS):
    """A client slow to take the receipt: its write is held until released."""

    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()

    async def send_json(self, data: dict):
        if data.get("type") == "macro.execute.ack":
            await self.release.wait()
        self.sent.append(data)


async def test_a_client_slow_to_take_the_receipt_does_not_hold_the_run_back(engine):
    ws = SlowToAck()
    asked = asyncio.create_task(_ask(ws, "clean"))

    # The run went ahead while the receipt was still being written...
    assert await _until(lambda: engine.state.get("var.ran") is True)
    assert ws.sent == []
    # ...and its ending waited, so the receipt still comes first.
    ws.release.set()
    await asked
    await _settle(ws)
    assert ws.types() == ["macro.execute.ack", "macro.execute.result"]
