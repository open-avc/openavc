"""Asking for help from inside a room.

A primitive the programmer wires up, never a stock control -- so what these
pin is the behaviour a panel button, an unattended trigger and a script all
share, plus the two properties that decide whether it is a feature or just a
mail-sender:

- the room reacts immediately, whether or not the cloud is reachable, and the
  panel is told honestly which of those happened;
- an acknowledgement comes back down and lands where a label can read it.

Plus the cooldown, which is not an abuse guard -- the programmer owns the page
-- but a defence against a trigger loop firing every poll through a class.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.help_requests import (
    ACKNOWLEDGED,
    IDLE,
    KEY_ACKNOWLEDGED_AT,
    KEY_ACKNOWLEDGED_BY,
    KEY_MESSAGE,
    KEY_REQUESTED_AT,
    KEY_STATE,
    REQUESTED,
    UNREACHABLE,
    HelpRequests,
)
from openavc.core.state_store import StateStore


class _FakeEngine:
    """Only what HelpRequests reaches for."""

    def __init__(self, connected: bool = True):
        self.state = StateStore()
        self.events = EventBus()
        self.project = None
        self.cloud_agent = _FakeAgent() if connected else None


class _FakeAgent:
    def __init__(self):
        self.connected = True
        self.sent: list[tuple[str, dict]] = []

    async def send_message(self, msg_type, payload):
        self.sent.append((msg_type, payload))


@pytest.fixture
def engine():
    return _FakeEngine()


@pytest.fixture
def help_requests(engine):
    h = HelpRequests(engine)
    h.declare_keys()
    return h


def test_a_quiet_room_still_has_something_to_draw(engine, help_requests):
    """A blank label reads as a broken binding, not as nothing happening."""
    assert engine.state.get(KEY_STATE) == IDLE
    assert engine.state.get(KEY_MESSAGE) is None


@pytest.mark.asyncio
async def test_raising_sets_the_room_state_before_anything_else(engine, help_requests):
    result = await help_requests.raise_request(message="Projector will not start")

    assert result["raised"] is True
    assert engine.state.get(KEY_STATE) == REQUESTED
    assert engine.state.get(KEY_MESSAGE) == "Projector will not start"
    assert engine.state.get(KEY_REQUESTED_AT)


@pytest.mark.asyncio
async def test_a_bare_request_still_says_something(engine, help_requests):
    """The common case is a button whose whole meaning is 'send a person'."""
    await help_requests.raise_request()
    assert engine.state.get(KEY_MESSAGE) == "Someone asked for help"


@pytest.mark.asyncio
async def test_it_reaches_the_cloud_as_an_alert(engine, help_requests):
    """Alerts already carry routing, acknowledgement and history. Building a
    second mechanism beside them was the alternative and it buys nothing."""
    await help_requests.raise_request(message="Lecture Hall 2 needs someone")

    assert len(engine.cloud_agent.sent) == 1
    msg_type, payload = engine.cloud_agent.sent[0]
    assert msg_type == "alert"
    assert payload["category"] == "help"
    assert payload["message"] == "Lecture Hall 2 needs someone"
    assert payload["alert_id"].startswith("help:")


@pytest.mark.asyncio
async def test_the_portal_and_the_room_agree_on_when_it_was_pressed(engine, help_requests):
    """The cloud used to stamp its own receipt time as the fire time, so the
    portal read a moment later than the label in the room did. The instant is
    handed over rather than re-taken."""
    await help_requests.raise_request(message="Lecture Hall 2 needs someone")

    _, payload = engine.cloud_agent.sent[0]
    assert payload["fired_at"] == engine.state.get(KEY_REQUESTED_AT)


@pytest.mark.asyncio
async def test_an_unreachable_cloud_is_said_out_loud(help_requests):
    """A panel claiming help is coming when nothing left the building is worse
    than one saying it could not get out."""
    engine = _FakeEngine(connected=False)
    h = HelpRequests(engine)
    h.declare_keys()

    result = await h.raise_request(message="anyone?")

    assert result["delivered"] is False
    assert engine.state.get(KEY_STATE) == UNREACHABLE
    # The local half still happened: a local macro can respond to this.
    assert engine.state.get(KEY_MESSAGE) == "anyone?"


@pytest.mark.asyncio
async def test_a_cloud_that_throws_does_not_take_the_room_with_it(engine, help_requests):
    engine.cloud_agent.send_message = AsyncMock(side_effect=RuntimeError("socket gone"))

    result = await help_requests.raise_request(message="still pressed")

    assert result["raised"] is True
    assert result["delivered"] is False
    assert engine.state.get(KEY_STATE) == UNREACHABLE


@pytest.mark.asyncio
async def test_the_local_event_fires_for_a_local_macro(engine, help_requests):
    seen = []

    async def handler(event, payload):
        seen.append(payload)

    engine.events.on("help.requested", handler)
    await help_requests.raise_request(message="lights are out")
    await asyncio.sleep(0)

    assert seen and seen[0]["message"] == "lights are out"


# --- The cooldown, which exists for trigger loops rather than for people ----


@pytest.mark.asyncio
async def test_a_second_request_inside_the_cooldown_is_dropped(engine, help_requests):
    await help_requests.raise_request(message="first")
    result = await help_requests.raise_request(message="second")

    assert result["raised"] is False
    assert result["reason"] == "cooldown"
    assert len(engine.cloud_agent.sent) == 1
    assert engine.state.get(KEY_MESSAGE) == "first", "the room keeps the live one"


@pytest.mark.asyncio
async def test_a_zero_cooldown_always_sends(engine, help_requests):
    """A room that genuinely needs to shout twice must be able to."""
    await help_requests.raise_request(message="first", cooldown=0)
    result = await help_requests.raise_request(message="second", cooldown=0)

    assert result["raised"] is True
    assert len(engine.cloud_agent.sent) == 2


# --- The answer coming back -------------------------------------------------


@pytest.mark.asyncio
async def test_an_acknowledgement_reaches_the_panel(engine, help_requests):
    raised = await help_requests.raise_request(message="need a hand")

    await help_requests.handle_acknowledged({
        "help_id": raised["help_id"],
        "acknowledged_by": "ben@tritronicsav.com",
        "acknowledged_at": "2026-08-11T09:04:00+00:00",
    })

    assert engine.state.get(KEY_STATE) == ACKNOWLEDGED
    assert engine.state.get(KEY_ACKNOWLEDGED_BY) == "ben@tritronicsav.com"
    assert engine.state.get(KEY_ACKNOWLEDGED_AT) == "2026-08-11T09:04:00+00:00"


@pytest.mark.asyncio
async def test_a_stale_acknowledgement_cannot_overwrite_a_newer_request(
    engine, help_requests
):
    """Two requests in a morning: answering the first must not make the room
    believe the second was picked up."""
    first = await help_requests.raise_request(message="one", cooldown=0)
    await help_requests.raise_request(message="two", cooldown=0)

    await help_requests.handle_acknowledged({
        "help_id": first["help_id"], "acknowledged_by": "someone",
    })

    assert engine.state.get(KEY_STATE) == REQUESTED
    assert engine.state.get(KEY_ACKNOWLEDGED_BY) is None


@pytest.mark.asyncio
async def test_an_acknowledgement_with_no_name_still_says_something(
    engine, help_requests
):
    raised = await help_requests.raise_request(message="x")
    await help_requests.handle_acknowledged({"help_id": raised["help_id"]})
    assert engine.state.get(KEY_ACKNOWLEDGED_BY) == "OpenAVC Cloud"


@pytest.mark.asyncio
async def test_clearing_puts_the_room_back(engine, help_requests):
    await help_requests.raise_request(message="done with this")
    help_requests.clear()

    assert engine.state.get(KEY_STATE) == IDLE
    assert engine.state.get(KEY_ACKNOWLEDGED_BY) is None


# --- The macro step ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_macro_step_raises_it_and_resolves_the_message():
    """`$var.` in the message is most of the value: it turns "someone pressed
    help" into what is actually wrong."""
    from openavc.core.device_manager import DeviceManager
    from openavc.core.macro_engine import MacroEngine

    engine = _FakeEngine()
    help_requests = HelpRequests(engine)
    help_requests.declare_keys()
    macros = MacroEngine(
        engine.state, engine.events,
        DeviceManager(engine.state, engine.events),
        help_requests=help_requests,
    )
    engine.state.set("var.room_name", "Lecture Hall 2")
    macros.load_macros([{"id": "ask", "name": "Ask", "steps": [
        {"action": "help.request", "message": "$var.room_name needs someone"},
    ]}])

    await macros.execute("ask")

    assert engine.state.get(KEY_MESSAGE) == "Lecture Hall 2 needs someone"


@pytest.mark.asyncio
async def test_the_step_is_a_known_action():
    """A capability the runtime has and the validator rejects is not shipped."""
    from openavc.core.macro_validation import validate_macro_step

    assert validate_macro_step({"action": "help.request"}, "steps[0]") == []


@pytest.mark.asyncio
async def test_a_step_with_nothing_wired_says_so_rather_than_raising():
    """A macro asking for help is often the last thing still working; it must
    not die on the way out."""
    from openavc.core.device_manager import DeviceManager
    from openavc.core.macro_engine import MacroEngine

    state = StateStore()
    events = EventBus()
    macros = MacroEngine(state, events, DeviceManager(state, events))
    macros.load_macros([{"id": "ask", "name": "Ask", "steps": [
        {"action": "help.request"},
    ]}])

    await macros.execute("ask")  # does not raise
