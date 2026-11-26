"""The socket's two macro doors: ``macro.execute`` and ``macro.cancel``.

Both answer at once and leave the work to a background task, since a macro
with delay steps would otherwise hold the client's whole message loop. A
``macro.execute`` is answered twice: its receipt, and when the run ends,
``macro.execute.result`` to the asking socket alone, with what that run (and
no other run of the same macro) said about its ending, read from the
``RunReport`` the door opens around it (``core/run_starts.py``). An ask may
name itself (``request_id``), and every answer after the receipt carries the
name, since one socket can hold several asks of one macro.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from openavc.api.ws_send import log, press_failure, send_ws, send_ws_error
from openavc.core.macro_text import no_such_macro
from openavc.core.run_starts import RunReport

# The longest ``request_id`` a ``macro.execute`` may carry.
REQUEST_ID_MAX = 64

# Strong refs for the background runs and cancels: asyncio only weakly
# references tasks, so an unreferenced one can be collected mid-run.
_runs: set[asyncio.Task] = set()


async def _named_macro(ws: WebSocket, msg_type: str, engine: Any, msg: dict[str, Any]) -> Any:
    """The macro a ``macro.execute`` or ``macro.cancel`` names, or ``""`` once
    the refusal is sent: no macro named, or one the project does not have.

    A missing macro is the sentence a bound button gets (``no_such_macro``),
    because it is the same failure: a matrix preset and a button that runs a
    macro are one thing to whoever is standing at the panel. Answered before
    any task, so the run that cannot happen never starts.
    """
    macro_id = msg.get("macro_id", "")
    if not macro_id:
        await send_ws_error(ws, msg_type, "Missing macro_id")
        return ""
    if not engine.macros.has_macro(macro_id):
        await send_ws_error(ws, msg_type, no_such_macro(macro_id))
        return ""
    return macro_id




def _echoable(request_id: Any) -> bool:
    """Whether a ``request_id`` can be echoed back exactly: none at all, or
    text of 1 to ``REQUEST_ID_MAX`` characters."""
    if request_id is None:
        return True
    if not isinstance(request_id, str):
        return False
    return 0 < len(request_id) <= REQUEST_ID_MAX


async def _asked_macro(ws: WebSocket, engine: Any, msg: dict[str, Any]) -> Any:
    """The macro a ``macro.execute`` names, or ``""`` once the refusal is
    sent: a ``request_id`` that is not text of 1 to 64 characters (it is
    echoed back exactly, so it must be something a client can compare), or
    what ``_named_macro`` refuses. All before the run, in request order."""
    if not _echoable(msg.get("request_id")):
        await send_ws_error(
            ws, "macro.execute",
            f"request_id must be text of 1 to {REQUEST_ID_MAX} characters",
        )
        return ""
    return await _named_macro(ws, "macro.execute", engine, msg)


def _run_ending(engine: Any, macro_id: str, outcome: str, report: RunReport) -> dict[str, Any]:
    """What a ``macro.execute.result`` says beyond its outcome, all of it this
    run's own: a skipped run's reason from the reason table (``code``,
    ``message``), and a failed run's ``step_errors`` and, when it stopped,
    its ``error`` and ``message``. Read in the run's own task."""
    if outcome == "skipped":
        return engine.macros.starts.take_refusal() or {}
    if outcome == "failed":
        return report.failure(macro_id)
    return {}


def _heard_by(engine: Any, client_type: str, ending: dict[str, Any]) -> dict[str, Any]:
    """A failed run's ending as this client hears it. A panel gets the copy
    the hub sends a panel of each ``macro.step_error`` and of the run's
    ``macro.error`` (``WSHub.panel_copy``: the room's sentence in ``error``),
    so asking for its own run's ending tells it nothing the room's events
    would not."""
    if client_type != "panel" or "step_errors" not in ending:
        return ending
    copy = {**ending, "step_errors": [
        engine.ws.panel_copy("macro.step_error", frame) for frame in ending["step_errors"]
    ]}
    if "error" in ending:
        copy.update(engine.ws.panel_copy(
            "macro.error", {"error": ending["error"], "message": ending.get("message")},
        ))
    return copy


async def execute_macro(
    ws: WebSocket, engine: Any, msg: dict[str, Any], client_type: str,
) -> None:
    """Acknowledge a macro at once, run it in the background, and tell this
    socket how its run ended.

    A macro with delay steps (warm-up sequences run tens of seconds) would
    otherwise hold this client's whole message loop: further taps queue in the
    socket and the panel reads as hung. Macro lifecycle events stream the
    progress to every client; ``macro.execute.result`` is this socket's own
    run's ending, the word ``execute`` reports and the rest ``_run_ending``
    adds. A run that raised around its steps is answered with an ``error``
    frame instead. The run starts before the receipt is written, so a client
    slow to take it never holds a press back; either answer waits for the
    receipt, so it always comes first.

    The ask's ``request_id``, when it has one, rides the receipt, the result
    and that error frame: one socket may hold several asks of one macro (a
    Node-RED server's flows share one), and their endings come in any order.
    """
    macro_id = await _asked_macro(ws, engine, msg)
    if not macro_id:
        return
    asked = {"request_id": msg["request_id"]} if msg.get("request_id") else {}
    acked = asyncio.Event()

    async def _run_macro() -> None:
        try:
            with engine.macros.starts.reporting() as report:
                outcome = await engine.macros.execute(macro_id)
        except Exception as e:
            # Catch-all: whatever raised around the steps reaches nobody else.
            log.exception(f"macro.execute of '{macro_id}' failed")
            await acked.wait()
            await send_ws_error(ws, "macro.execute", press_failure(e, client_type), **asked)
            return
        ending = _heard_by(engine, client_type, _run_ending(engine, macro_id, outcome, report))
        await acked.wait()
        await send_ws(ws, {
            "type": "macro.execute.result", "macro_id": macro_id, **asked,
            "outcome": outcome, **ending,
        })

    task = asyncio.create_task(_run_macro())
    _runs.add(task)
    task.add_done_callback(_runs.discard)
    try:
        await send_ws(ws, {"type": "macro.execute.ack", "macro_id": macro_id, **asked})
    finally:
        acked.set()


async def cancel_macro(ws: WebSocket, engine: Any, msg: dict[str, Any]) -> None:
    """Stop every running invocation of a macro. Open to a panel on the same
    terms as ``macro.execute``: whoever may start a thirty-second warm-up may
    stop it. The ack says whether anything was running to stop; the run
    itself reports its end as ``macro.cancelled``. The cancel waits up to a
    grace period for the steps to unwind, too long to hold this client's
    message loop, so it runs in the background like the execute does."""
    macro_id = await _named_macro(ws, "macro.cancel", engine, msg)
    if not macro_id:
        return
    running = engine.macros.is_macro_running(macro_id)
    if running:
        task = asyncio.create_task(engine.macros.cancel(macro_id))
        _runs.add(task)
        task.add_done_callback(_runs.discard)
    await send_ws(ws, {"type": "macro.cancel.ack", "macro_id": macro_id, "cancelled": running})
