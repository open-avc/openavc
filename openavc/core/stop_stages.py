"""How one stop runs its stages: each on its own, against one deadline.

A stop is a list of stages, written once in ``core/engine_lifecycle.py``
(``Engine.shutdown`` for a process exit, ``Engine.stop`` for the engine's own
part); this module runs that list. Each stage runs as a task of its own, so
whatever the stage does with a raise, a hang or a cancel, the stop goes on:

- a stage that raises is logged with its traceback and named, and the next
  stage runs;
- a stage still running at its bound is cancelled, left behind, named, and the
  next stage runs, even when the stage swallows the cancel;
- a stage whose turn comes after its bound is not started, and is named. A
  stage left at the bound has reached it, whatever the clock then reads: the
  event loop may end a wait one clock tick early (about 15 ms on Windows), and
  the stages after it must not start on that sliver.

The bound is the stop's one deadline less ``EQUIPMENT_SECONDS``, until the
stop hands over (``StopRun.hand_over``) to the stages that let go of the
equipment: cancelling the macros and the file commands, disconnecting the
devices, ending the simulator. From there the bound is the deadline itself.
So a stage that hangs early uses up the run-up, never the time those stages
need. A stop with no deadline bounds nothing and still isolates every stage.

``STOP_SECONDS`` is the one bound on every stop that ends the process, however
it was asked: the lifespan's at a service stop and the restart's exit
(``openavc/restart.py``, whose watchdog fires a little after it). A service
stop first gives the requests still open ``DRAIN_SECONDS``, so from its signal
the whole of it fits ``SIGNAL_STOP_SECONDS``. THE bound every install's
service manager is told to give a stop is ``SERVICE_STOP_SECONDS``: each unit
names it and a signal the server handles (tests/test_service_stop_bound.py
reads them), since a manager left at its own default cut the stop short.

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
# How long the requests still open at a stop signal (a download, the
# assistant's stream) get before the lifespan's stop begins: uvicorn's
# timeout_graceful_shutdown on the main listener (listen_sockets.hears_the_stop).
DRAIN_SECONDS = 1
# A stop signal to the end of the stop's last stage: the drain, the stop, and
# a second for the server's own steps around them. Docker's default wait,
# 10 s, still holds it, for a compose file that names no stop_grace_period.
SIGNAL_STOP_SECONDS = DRAIN_SECONDS + STOP_SECONDS + 1.0
# What every install's service manager gives a stop before it kills the
# server: systemd's TimeoutStopSec, NSSM's AppStopMethodConsole, Docker's
# stop_grace_period, launchd's ExitTimeOut. Twice the stop, so a stop is never
# cut short and a process that cannot exit is still ended
# (tests/test_service_stop_bound.py reads each unit).
SERVICE_STOP_SECONDS = 20


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
        self._bound_reached = False
        self.problems: list[str] = []

    def hand_over(self) -> None:
        """From here on the stages let go of the equipment: each may run to
        the deadline itself."""
        self._bound = self.deadline
        self._bound_reached = False

    def _time_left(self) -> float | None:
        if self._bound is None:
            return None
        if self._bound_reached:
            return 0.0
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
            self._bound_reached = True
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
