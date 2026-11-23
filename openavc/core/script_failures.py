"""A loaded script's failures, reported by the script API.

The script engine keeps THE record of a loaded script's failures
(``ScriptEngine._record_runtime_error``), which the Scripts list,
``GET /api/scripts/errors`` and the AI's ``get_script_health`` read. Most doors
that run a script's code are the engine's own and record there directly. Three
are the script API's: a timer's callback, an ``events.emit`` the bus dropped at
its chain limit, and a ``state.set`` or ``state.delete`` the authored-writer
rule refused. They report through here, bound to the engine by
``ScriptEngine.install``, so a failure at any of them reaches the same record.

A failure names the load of the script that failed (its ``LoadedScript``), not
its id: an old version's run that fails after an edit was saved is not put on
the new version's row.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from openavc.core import script_runs
from openavc.utils.log_buffer import USER_SCRIPT_NAMESPACE
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.script_engine import LoadedScript

log = get_logger(__name__)

_SCRIPT_MODULES = f"{USER_SCRIPT_NAMESPACE}."


@dataclass(frozen=True)
class ScriptFailure:
    """One failure of a script's code, as the record keeps it."""

    script: LoadedScript | None
    handler: str
    event: str
    error: str
    traceback: str = ""


_record: Callable[[ScriptFailure], None] | None = None


def bind(record: Callable[[ScriptFailure], None]) -> None:
    """Name the record failures are reported to. Called by ScriptEngine.install."""
    global _record
    _record = record


def report(failure: ScriptFailure) -> None:
    """Record a failure against the script that owns it. A failure no script
    owns has no row to show on and is left to the log."""
    if _record is not None and failure.script is not None:
        _record(failure)


@dataclass(frozen=True)
class ScriptLine:
    """Where in a script's own code a call was made: the function it was in,
    and a trace of only the script's frames that ends at the call's line."""

    handler: str
    traceback: str


#: What a call made by the script's top-level code is named on its row.
TOP_LEVEL = "top level"


def script_line(call: str) -> ScriptLine:
    """The script's line on this thread's stack that made ``call``.

    Read on the thread the script runs on, before any hand-off to the loop: a
    plain ``def`` handler's frames are on its worker thread. A call with no
    line of the script under it (``after(0, events.emit, ...)``, run by a
    timer) is named by the call itself, with no trace to mark.
    """
    frames = _script_frames()
    if not frames:
        return ScriptLine(call, "")
    name = frames[-1].name
    return ScriptLine(
        TOP_LEVEL if name == "<module>" else name,
        "Traceback (most recent call last):\n" + "".join(frames.format()),
    )


def report_refused(script: LoadedScript | None, call: str, sentence: str) -> None:
    """Report a call the platform refused or dropped, against the script that
    made it: ``events.emit`` past the bus's chain limit, or a ``state.set`` or
    ``state.delete`` the authored-writer rule refuses
    (``state_store.check_authored_write``).

    Not raised into the script: the event or the write is lost either way, and
    the lines after the call run as they always have. The script's line is
    read on the calling thread (:func:`script_line`), and the record is then
    made on the loop: a plain ``def`` handler calls from its worker thread.
    """
    if script is None:
        return
    where = script_line(call)
    log.error(f"Script '{script.script_id}' {where.handler}: {sentence}")
    failure = ScriptFailure(script, where.handler, call, sentence, where.traceback)
    loop = script_runs.hand_off_loop()
    if loop is None:
        report(failure)
    else:
        script_runs.call_on_loop(loop, report, (failure,), {})


def refused(call: str, sentence: str | None) -> bool:
    """Whether a script's state write was refused (``sentence`` is the
    authored-writer rule's answer), reporting it against the running script.
    Asked on the script's own thread, before the write is handed to the loop,
    so the line recorded is the script's."""
    if sentence:
        report_refused(script_runs.current_script.get(), call, sentence)
    return bool(sentence)


def report_dropped_emit(script: LoadedScript | None, sentence: str) -> None:
    """Report an ``events.emit`` the bus dropped, against the sending script."""
    report_refused(script, "events.emit", sentence)


def _script_frames() -> traceback.StackSummary:
    """The frames of user scripts' code on the current stack, outermost first.

    A script's module is named under ``USER_SCRIPT_NAMESPACE``, the same test
    the log uses to tell a script's output from the platform's.
    """
    own = [
        (frame, lineno) for frame, lineno in traceback.walk_stack(None)
        if str(frame.f_globals.get("__name__", "")).startswith(_SCRIPT_MODULES)
    ]
    return traceback.StackSummary.extract(reversed(own))
