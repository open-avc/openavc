"""Whose cancel a macro run reports, and whose it passes on.

A macro run executes in its caller's task, so a cancel of that task is one of
two things. The macro engine's own cancel (``MacroEngine.cancel``,
``cancel_all``, a cancel_group preemption) is the run's outcome: the run
consumes it and ``execute`` returns ``"cancelled"`` to a caller that carries
on. Anybody else's cancel of that task (a script handler's time limit, the
macro whose event the handler was answering, a script being unloaded) stops
the caller too: the run still ends on ``macro.cancelled``, and the cancel goes
on up.

THE rule: every cancel the engine makes of a run goes through ``stop``, and a
run that ended on a cancel asks ``reported`` whether to return or to raise.
"""

from __future__ import annotations

import asyncio
import weakref


class RunCancels:
    """The cancels the macro engine made of each run's task, until consumed."""

    def __init__(self) -> None:
        self._made: weakref.WeakKeyDictionary[asyncio.Task, int] = weakref.WeakKeyDictionary()

    def stop(self, task: asyncio.Task) -> None:
        """Cancel a run's task as the engine, so the run reports it."""
        if task.cancel():
            self._made[task] = self._made.get(task, 0) + 1

    def reported(self, task: asyncio.Task | None, *, subroutine: bool) -> bool:
        """Whether a run that ended on a cancel returns it as its outcome.

        A Run Macro step's subroutine never does: the cancel goes on up to the
        step, and the outermost run decides. Otherwise the engine's own cancels
        are consumed (``uncancel``), so the caller carries on clean, and the
        run reports when none other is pending on ``task``.
        """
        if subroutine:
            return False
        if task is None:
            return True
        for _ in range(self._made.pop(task, 0)):
            task.uncancel()
        return task.cancelling() == 0
