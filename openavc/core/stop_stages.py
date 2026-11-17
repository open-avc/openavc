"""How one stop runs its stages: each on its own, against one deadline.

A stop is a list of stages, written once in ``core/engine_lifecycle.py``
(``Engine.shutdown`` for a process exit, ``Engine.stop`` for the engine's own
part); this module runs that list. Each stage runs as a task of its own, so
whatever the stage does with a raise, a hang or a cancel, the stop goes on:

- a stage that raises is logged with its traceback and named, and the next
  stage runs;
- a stage still running at its bound is cancelled, left behind, named, and the
  next stage runs, even when the stage swallows the cancel;
- a stage whose turn comes after its bound is not started, and is named.

The bound is the stop's one deadline less ``EQUIPMENT_SECONDS``, until the
stop hands over (``StopRun.hand_over``) to the stages that let go of the
equipment: cancelling the macros and the file commands, disconnecting the
devices, ending the simulator. From there the bound is the deadline itself.
So a stage that hangs early uses up the run-up, never the time those stages
need. A stop with no deadline bounds nothing and still isolates every stage.

``STOP_SECONDS`` is the one bound on every stop that ends the process, however
it was asked: the lifespan's at a service stop and the restart's exit
(``openavc/restart.py``, whose watchdog fires a little after it). A service
manager given less time than this cuts the stop short.

At the end, every stage that failed, overran or did not start is named in one
warning (``report``), as a start names its failed parts.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")

# The whole stop, from the first stage to the process exit's last.
STOP_SECONDS = 8.0
# The end of it kept for the stages that let go of the equipment.
EQUIPMENT_SECONDS = 3.0


def stop_deadline() -> float:
    """The deadline for a stop that starts now, on ``time.monotonic()``."""
    return time.monotonic() + STOP_SECONDS


def _retrieve(task: asyncio.Task) -> None:
    """Done-callback for a stage left behind at its bound: whatever it ends
    with was already named as an overrun, so it is read and dropped rather
    than reported again when the task is collected."""
    if not task.cancelled():
        task.exception()


class StopRun:
    """One stop's stages against one deadline (``None``: no bound), and the
    stages that went wrong, in ``problems``."""

    def __init__(self, deadline: float | None) -> None:
        self.deadline = deadline
        self._bound = None if deadline is None else deadline - EQUIPMENT_SECONDS
        self.problems: list[str] = []

    def hand_over(self) -> None:
        """From here on the stages let go of the equipment: each may run to
        the deadline itself."""
        self._bound = self.deadline

    def _time_left(self) -> float | None:
        if self._bound is None:
            return None
        return self._bound - time.monotonic()

    def _name(self, stage: str, what: str) -> None:
        log.warning("Stopping %s: %s", stage, what)
        self.problems.append(f"{stage}: {what}")

    async def run(self, stage: str, step: Callable[[], Any]) -> None:
        """Run ``step`` (sync, or returning an awaitable) as the stage named
        ``stage``. Never raises, except the cancel of the stop itself."""
        left = self._time_left()
        if left is not None and left <= 0:
            self._name(stage, "not started, the stop ran out of time")
            return
        try:
            result = step()
        except Exception as exc:
            log.error("Stopping %s failed", stage, exc_info=exc)
            self.problems.append(f"{stage}: {exc}")
            return
        if inspect.isawaitable(result):
            await self._await_stage(stage, asyncio.ensure_future(result), left)

    async def _await_stage(self, stage: str, task: asyncio.Future, left: float | None) -> None:
        try:
            done, _pending = await asyncio.wait({task}, timeout=left)
        except asyncio.CancelledError:
            task.cancel()
            raise
        if not done:
            task.cancel()
            task.add_done_callback(_retrieve)
            self._name(stage, "did not finish in time and was left")
        elif task.cancelled():
            self._name(stage, "was cancelled")
        elif (exc := task.exception()) is not None:
            log.error("Stopping %s failed", stage, exc_info=exc)
            self.problems.append(f"{stage}: {exc}")

    @contextlib.asynccontextmanager
    async def holding(self, lock: asyncio.Lock, stage: str) -> AsyncIterator[None]:
        """Hold ``lock`` for the stages inside, waiting for it as a stage of
        its own: one not free by the bound is named, and the stages run
        without it."""
        acquired = False

        async def acquire() -> None:
            nonlocal acquired
            await lock.acquire()
            acquired = True

        await self.run(stage, acquire)
        try:
            yield
        finally:
            if acquired:
                lock.release()

    def report(self, what: str) -> None:
        """Name every stage that went wrong, in one warning."""
        if self.problems:
            log.warning(
                f"{what} with {len(self.problems)} problem(s): {'; '.join(self.problems)}"
            )
