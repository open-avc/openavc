"""A press whose own handling failed, worded from the record every command
door answers with (``CommandFailure``).

These are the failures that are not one of the binding's actions: the
interaction itself raised, the server failed on the message carrying it, or a
preset's macro raised before it ran. Somebody at a panel is told what a
control's script call that raised tells them; the Programmer and the cloud AI
are told the exception's type and where the details are. Neither half quotes
the exception, whose text was written for nobody reading it and may hold a
value that must stay private.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import openavc.api.ws as ws_module
from openavc.api.error_messages import describe_press_failure
from openavc.api.ws import _dispatch_text, _handle_message
from openavc.core.script_engine import COULD_NOT_FINISH

from tests.test_websocket_protocol import FakeWS, _make_engine

#: A value that must never reach a reader.
SECRET = "password=hunter2"


def _programmer(type_name: str) -> str:
    return f"Server error: {type_name}. The Programmer's Log has the details."


# --- the record ---------------------------------------------------------------


def test_the_room_is_told_what_a_raised_script_call_tells_it():
    assert describe_press_failure(KeyError(SECRET)).panel == COULD_NOT_FINISH


def test_the_programmer_is_told_the_type_and_where_the_details_are():
    failure = describe_press_failure(KeyError(SECRET))
    assert failure.programmer == _programmer("KeyError")


def test_an_exception_with_no_text_still_reads():
    assert describe_press_failure(TimeoutError()).programmer == _programmer("TimeoutError")


# --- a panel interaction that raised -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["panel", "programmer"])
async def test_a_press_that_raised_says_the_rooms_sentence(client, caplog):
    """Whoever sent it, a press is answered in the room's words, as a press
    whose bound command failed already is. The details go to the log."""
    ws = FakeWS()
    engine = _make_engine()
    engine.handle_ui_event = AsyncMock(side_effect=KeyError(SECRET))
    with patch("openavc.api._engine._engine", engine), caplog.at_level(logging.ERROR):
        await _handle_message(ws, {"type": "ui.press", "element_id": "power"}, client)
    assert ws.sent == [{
        "type": "error", "message": COULD_NOT_FINISH,
        "source_type": "ui.press", "element_id": "power",
    }]
    logged = [r for r in caplog.records if "ui.press" in r.getMessage()]
    assert logged and logged[0].exc_info is not None


@pytest.mark.asyncio
async def test_a_moved_control_whose_value_raised_says_the_rooms_sentence():
    ws = FakeWS()
    engine = _make_engine()
    engine.handle_ui_event = AsyncMock(side_effect=ValueError(SECRET))
    with patch("openavc.api._engine._engine", engine):
        await _handle_message(ws, {"type": "ui.change", "element_id": "gain", "value": 3}, "panel")
        for _ in range(5):
            await asyncio.sleep(0)
    assert ws.sent == [{
        "type": "error", "message": COULD_NOT_FINISH,
        "source_type": "ui.change", "element_id": "gain",
    }]


# --- a message the server failed on --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("client, expected", [
    ("panel", COULD_NOT_FINISH),
    ("programmer", _programmer("RuntimeError")),
])
async def test_a_message_the_server_failed_on_is_worded_for_its_reader(client, expected):
    ws = FakeWS()
    engine = _make_engine()
    with patch("openavc.api._engine._engine", engine), patch.object(
        ws_module, "_handle_message", side_effect=RuntimeError(SECRET),
    ):
        await _dispatch_text(ws, '{"type": "ui.press", "element_id": "b1"}', client)
    assert ws.sent == [{"type": "error", "message": expected, "source_type": "ui.press"}]


# --- a preset whose macro raised -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("client, expected", [
    ("panel", COULD_NOT_FINISH),
    ("programmer", _programmer("RuntimeError")),
])
async def test_a_preset_whose_macro_raised_is_worded_for_its_reader(client, expected, caplog):
    """``execute`` reports a step's failure itself; what reaches here raised
    around the steps."""
    ws = FakeWS()
    engine = _make_engine()
    engine.macros.has_macro = MagicMock(return_value=True)
    engine.macros.execute = AsyncMock(side_effect=RuntimeError(SECRET))
    with patch("openavc.api._engine._engine", engine), caplog.at_level(logging.ERROR):
        await _handle_message(ws, {"type": "macro.execute", "macro_id": "warmup"}, client)
        for _ in range(10):
            await asyncio.sleep(0)
    assert [m for m in ws.sent if m["type"] == "error"] == [
        {"type": "error", "message": expected, "source_type": "macro.execute"},
    ]
    logged = [r for r in caplog.records if "macro.execute" in r.getMessage()]
    assert logged and logged[0].exc_info is not None
