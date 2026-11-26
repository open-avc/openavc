"""What passes between a task and the macro starts it makes: the callback its
next start calls, and the reason a start that was refused gives back.

A macro run takes its slot in its caller's task (``MacroEngine.execute``). A
caller with a throttle of its own, a trigger's cooldown, stamps it only on a
run that starts: it awaits ``execute`` inside ``on_next_start``, and the
engine calls the callback as the run takes its slot (``_claim``, beside the
macro's own cooldown stamp), never for a start the macro refuses. A refused
start keeps its reason for its task (``refused``), which ``take_refusal``
hands back, since ``execute`` answers only ``skipped``.

THE per-task channel for both, ``MacroEngine.starts``. Weak, so an entry goes
with its task, and a long-lived caller's task only ever holds its latest.
"""

from __future__ import annotations

import asyncio
import weakref
from contextlib import contextmanager
from typing import Callable, Iterator

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


class RunStarts:
    """The start callback and the refusal reason held for each task."""

    def __init__(self) -> None:
        self._hooks: weakref.WeakKeyDictionary[asyncio.Task, StartCallback] = (
            weakref.WeakKeyDictionary()
        )
        self._refusals: weakref.WeakKeyDictionary[asyncio.Task, dict[str, str]] = (
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
