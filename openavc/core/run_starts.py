"""What passes between a task and the macro starts it makes: the callback its
next start calls, the reason a start that was refused gives back, and, for a
caller that asks (``reporting``), what its runs said beyond their outcome.

A macro run takes its slot in its caller's task (``MacroEngine.execute``). A
caller with a throttle of its own, a trigger's cooldown, stamps it only on a
run that starts: it awaits ``execute`` inside ``on_next_start``, and the
engine calls the callback as the run takes its slot (``_claim``, beside the
macro's own cooldown stamp), never for a start the macro refuses. A refused
start keeps its reason for its task (``refused``), which ``take_refusal``
hands back, since ``execute`` answers only ``skipped``. A caller that tells
somebody how their own run went (the WebSocket's ``macro.execute.result``)
opens a ``RunReport`` around its ``execute``: the run's step errors and why it
stopped land there, never a concurrent run's of the same macro, whose
lifecycle events reach every client with nothing to tell the two apart.

THE per-task channel for all three, ``MacroEngine.starts``. Weak, so an entry
goes with its task, and a long-lived caller's task only ever holds its latest;
a report is kept only while its caller's block is open.
"""

from __future__ import annotations

import asyncio
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from openavc.utils.logger import get_logger

log = get_logger(__name__)

StartCallback = Callable[[], None]


def call_start_hook(on_start: StartCallback | None) -> None:
    """Call a start's callback, when it has one. One that raises is logged,
    and the run goes on: a caller's stamp never stops a run."""
    if on_start is None:
        return
    try:
        on_start()
    except Exception:  # Catch-all: isolates the caller's callback from the run
        log.exception("A macro start's callback failed")


@dataclass
class RunReport:
    """What the runs a task made said beyond ``execute``'s word: every
    ``macro.step_error`` frame their steps sent, and, per macro, the ``error``
    and ``message`` of a run that stopped (its ``macro.error``'s)."""

    step_errors: list[dict[str, Any]] = field(default_factory=list)
    stops: dict[str, dict[str, str]] = field(default_factory=dict)

    def failure(self, macro_id: str) -> dict[str, Any]:
        """The fields a failed run of ``macro_id`` answers with: every step
        error inside it, a sub-macro's included (its frame names the macro it
        happened in, and is the only one: the Run Macro step above it counts
        the failure without announcing it again), and, when it stopped, why."""
        own = [frame for frame in self.step_errors if macro_id in frame.get("call_chain", ())]
        return {"step_errors": own, **self.stops.get(macro_id, {})}


class RunStarts:
    """The start callback, the refusal reason and the open report held for
    each task."""

    def __init__(self) -> None:
        self._hooks: weakref.WeakKeyDictionary[asyncio.Task, StartCallback] = (
            weakref.WeakKeyDictionary()
        )
        self._refusals: weakref.WeakKeyDictionary[asyncio.Task, dict[str, str]] = (
            weakref.WeakKeyDictionary()
        )
        self._reports: weakref.WeakKeyDictionary[asyncio.Task, RunReport] = (
            weakref.WeakKeyDictionary()
        )

    @contextmanager
    def on_next_start(self, on_start: StartCallback) -> Iterator[None]:
        """Within the block, call ``on_start`` when the next run the calling
        task starts takes its slot. The engine holds its start lock then, so
        it may not await. One no start took (a macro that does not exist
        raises first) is dropped as the block ends."""
        task = asyncio.current_task()
        if task is not None:
            self._hooks[task] = on_start
        try:
            yield
        finally:
            self.take_hook(task)

    def take_hook(self, task: asyncio.Task | None) -> StartCallback | None:
        """The callback ``task``'s start calls, removed: every start takes
        it, refused or not, so a refusal leaves none behind."""
        return self._hooks.pop(task, None) if task is not None else None

    def refused(self, reason: dict[str, str]) -> None:
        """Keep the reason (``code`` and ``message``) the calling task's start
        was refused; the last one written for the task is the one kept."""
        task = asyncio.current_task()
        if task is not None:
            self._refusals[task] = reason

    def take_refusal(self, task: asyncio.Task | None = None) -> dict[str, str] | None:
        """The reason the last start ``task`` (the caller's, by default) ran
        was refused, removed as it is read; None when it has none."""
        task = task or asyncio.current_task()
        return self._refusals.pop(task, None) if task is not None else None

    @contextmanager
    def reporting(self) -> Iterator[RunReport]:
        """Within the block, keep what the runs the calling task makes report
        beyond their outcome (``step_failed``, ``stopped``). Asked for, so a
        task that never reads a report holds none."""
        task = asyncio.current_task()
        report = RunReport()
        if task is not None:
            self._reports[task] = report
        try:
            yield report
        finally:
            if task is not None:
                self._reports.pop(task, None)

    def step_failed(self, frame: dict[str, Any]) -> None:
        """Keep a failed step's ``macro.step_error`` frame for the calling
        task's open report, if it has one."""
        report = self._open_report()
        if report is not None:
            report.step_errors.append(frame)

    def stopped(self, macro_id: str, stop: dict[str, str]) -> None:
        """Keep why a run of ``macro_id`` stopped (``error`` and ``message``)
        for the calling task's open report, if it has one."""
        report = self._open_report()
        if report is not None:
            report.stops[macro_id] = stop

    def _open_report(self) -> RunReport | None:
        task = asyncio.current_task()
        return self._reports.get(task) if task is not None else None
