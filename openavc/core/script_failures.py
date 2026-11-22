"""A loaded script's failures, reported by the script API.

The script engine keeps THE record of a loaded script's failures
(``ScriptEngine._record_runtime_error``), which the Scripts list,
``GET /api/scripts/errors`` and the AI's ``get_script_health`` read. Most doors
that run a script's code are the engine's own and record there directly. Two
are the script API's: a timer's callback, and an ``events.emit`` the bus
dropped at its chain limit. They report through here, bound to the engine by
``ScriptEngine.install``, so a failure at either reaches the same record.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Callable

from openavc.utils.log_buffer import USER_SCRIPT_NAMESPACE
from openavc.utils.logger import get_logger

log = get_logger(__name__)

_SCRIPT_MODULES = f"{USER_SCRIPT_NAMESPACE}."


@dataclass(frozen=True)
class ScriptFailure:
    """One failure of a script's code, as the record keeps it."""

    script_id: str | None
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
    if _record is not None and failure.script_id:
        _record(failure)


def report_dropped_emit(script_id: str | None, sentence: str) -> None:
    """Report an ``events.emit`` the bus dropped, against the sending script.

    Not raised into the script: the event is lost either way, and the lines
    after the emit run as they always have. The trace holds only the script's
    own frames and ends at the line that sent it, so the editor can mark it.
    An emit a timer ran for the script (``after(0, events.emit, ...)``) has no
    such line, and is named by the call itself.
    """
    if not script_id:
        return
    frames = _script_frames()
    handler = frames[-1].name if frames else "events.emit"
    trace = (
        "Traceback (most recent call last):\n" + "".join(frames.format())
        if frames else ""
    )
    log.error(f"Script '{script_id}' {handler}: {sentence}")
    report(ScriptFailure(script_id, handler, "events.emit", sentence, trace))


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
