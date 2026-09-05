"""A macro that never starts, and a macro somebody stops, both report an outcome.

Every way a run ends has a lifecycle event a client can wait on -- except,
until now, the run that never began. A macro's own guard (`overlap: skip`, a
`cooldown_seconds`) turns a start away inside `execute()`, which logged a line
and returned. The WebSocket's `macro.execute` had already acked receipt, so a
client waiting for `macro.completed` / `macro.error` / `macro.cancelled` (the
Node-RED macro node does exactly that) waited forever, for a run that was
never going to happen. `macro.skipped` is that outcome, with the reason.

`macro.cancel` is the door the socket lacked: REST could stop a running macro
and the socket could not. It is open to a panel on the same terms as
`macro.execute` -- whoever may start a warm-up may stop it -- and acks whether
anything was running; the run itself ends on `macro.cancelled`.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.api.ws import _PANEL_ALLOWED_TYPES, _handle_message
from openavc.core.engine import Engine
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

    def frames(self, type_: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == type_]


def _engine():
    """A real macro engine on a real bus behind a mock engine."""
    engine = MagicMock()
    engine.state = StateStore()
    engine.events = EventBus()
    engine.state.set_event_bus(engine.events)
    devices = MagicMock()
    devices.send_command = AsyncMock()
    engine.macros = MacroEngine(engine.state, engine.events, devices)
    engine.isc = None
    return engine


def _lifecycle(events: EventBus) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []

    async def _on(event, payload):
        seen.append((event, payload))

    events.on("macro.*", _on)
    return seen


def _slow(macro_id: str, seconds: float, **extra) -> dict:
    return {
        "id": macro_id,
        "name": macro_id.replace("_", " ").title(),
        "steps": [{"action": "delay", "seconds": seconds}],
        **extra,
    }


@pytest.fixture
def engine():
    return _engine()


# --- The run that never starts -----------------------------------------------


@pytest.mark.asyncio
async def test_a_start_the_macro_s_own_guard_turns_away_is_an_outcome_not_silence(engine):
    seen = _lifecycle(engine.events)
    engine.macros.load_macros([_slow("warm_up", 0.3, overlap="skip")])

    first = asyncio.create_task(engine.macros.execute("warm_up"))
    await asyncio.sleep(0.05)
    await engine.macros.execute("warm_up")  # turned away: one is running

    skipped = [p for e, p in seen if e == "macro.skipped.warm_up"]
    assert len(skipped) == 1
    assert skipped[0]["macro_id"] == "warm_up"
    assert skipped[0]["name"] == "Warm Up"
    assert "overlap=skip" in skipped[0]["reason"]
    # The first run is untouched and still ends the ordinary way.
    assert engine.macros.is_macro_running("warm_up")
    await first
    assert [e for e, _ in seen if e.startswith("macro.completed.")] == ["macro.completed.warm_up"]


@pytest.mark.asyncio
async def test_a_cooldown_says_which_guard_it_was(engine):
    seen = _lifecycle(engine.events)
    engine.macros.load_macros([_slow("preset", 0.0, cooldown_seconds=60)])
    await engine.macros.execute("preset")
    await engine.macros.execute("preset")
    skipped = [p for e, p in seen if e == "macro.skipped.preset"]
    assert len(skipped) == 1
    assert "cooldown" in skipped[0]["reason"]


@pytest.mark.asyncio
async def test_the_engine_forwards_skipped_as_a_frame_like_every_other_outcome():
    """The frame set a socket client waits on is whatever `Engine` relays from
    the bus. `macro.skipped` has to be in it, or a client is back to waiting."""
    fake_self = MagicMock()
    fake_self.broadcast_ws = AsyncMock()
    await Engine._on_macro_event(
        fake_self, "macro.skipped.warm_up",
        {"macro_id": "warm_up", "name": "Warm Up", "reason": "cooldown (60s) not elapsed"},
    )
    fake_self.broadcast_ws.assert_awaited_once_with({
        "type": "macro.skipped", "macro_id": "warm_up", "name": "Warm Up",
        "reason": "cooldown (60s) not elapsed",
    })


@pytest.mark.asyncio
async def test_over_the_socket_the_second_ask_is_acked_and_then_skipped(engine):
    """The whole sequence a client sees: receipt for both, one start, one skip."""
    seen = _lifecycle(engine.events)
    engine.macros.load_macros([_slow("warm_up", 0.2, overlap="skip")])
    ws = FakeWS()
    with patch("openavc.api._engine._engine", engine):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "warm_up"})
        await asyncio.sleep(0.05)
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "warm_up"})
        await asyncio.sleep(0.3)
    assert len(ws.frames("macro.execute.ack")) == 2
    assert [e for e, _ in seen if e.startswith("macro.started.")] == ["macro.started.warm_up"]
    assert [e for e, _ in seen if e.startswith("macro.skipped.")] == ["macro.skipped.warm_up"]


# --- Stopping a run ------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_over_the_socket_acks_and_the_run_reports_cancelled(engine):
    seen = _lifecycle(engine.events)
    engine.macros.load_macros([_slow("warm_up", 5.0)])
    ws = FakeWS()
    with patch("openavc.api._engine._engine", engine):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "warm_up"})
        await asyncio.sleep(0.05)
        assert engine.macros.is_macro_running("warm_up")
        await _handle_message(ws, {"type": "macro.cancel", "macro_id": "warm_up"})
        for _ in range(50):
            if any(e == "macro.cancelled.warm_up" for e, _ in seen):
                break
            await asyncio.sleep(0.02)
    assert ws.frames("macro.cancel.ack") == [
        {"type": "macro.cancel.ack", "macro_id": "warm_up", "cancelled": True}
    ]
    assert any(e == "macro.cancelled.warm_up" for e, _ in seen)
    assert not engine.macros.is_macro_running("warm_up")


@pytest.mark.asyncio
async def test_cancel_of_a_macro_that_is_not_running_says_so(engine):
    engine.macros.load_macros([_slow("warm_up", 5.0)])
    ws = FakeWS()
    with patch("openavc.api._engine._engine", engine):
        await _handle_message(ws, {"type": "macro.cancel", "macro_id": "warm_up"})
    assert ws.frames("macro.cancel.ack") == [
        {"type": "macro.cancel.ack", "macro_id": "warm_up", "cancelled": False}
    ]


@pytest.mark.asyncio
async def test_cancel_refuses_what_execute_refuses(engine):
    engine.macros.load_macros([])
    ws = FakeWS()
    with patch("openavc.api._engine._engine", engine):
        await _handle_message(ws, {"type": "macro.cancel"})
        await _handle_message(ws, {"type": "macro.cancel", "macro_id": "nope"})
    errors = ws.frames("error")
    assert [e["source_type"] for e in errors] == ["macro.cancel", "macro.cancel"]
    assert errors[0]["message"] == "Missing macro_id"
    assert errors[1]["message"] == "No macro named 'nope'."
    assert not ws.frames("macro.cancel.ack")


def test_a_panel_may_stop_what_it_may_start():
    assert "macro.execute" in _PANEL_ALLOWED_TYPES
    assert "macro.cancel" in _PANEL_ALLOWED_TYPES
