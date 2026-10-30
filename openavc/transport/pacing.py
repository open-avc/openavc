"""
THE wait a device asked for between two sends: never shorter than asked.

``asyncio.sleep`` can end early. The event loop runs a timer once its deadline
is within the resolution of its clock (``time.monotonic``), and on Windows
before Python 3.13 that clock is ``GetTickCount64``, which moves in 15.6 ms
steps: when the loop wakes for other I/O near the deadline, a sleep can return
up to a step short. Measured on Python 3.12 on Windows with an idle loop, 3 of
40 sleeps of 130 ms ended 0.1 ms short; a busy loop gives the clock more
chances. An ``inter_command_delay`` is often a manufacturer's stated minimum,
so the pacing sleeps measure the wait with ``time.perf_counter`` (the
high-resolution counter on every platform) and sleep again for whatever is
left. Waits that are not a device's minimum (retry backoff, a poll interval)
keep plain ``asyncio.sleep``.
"""

from __future__ import annotations

import asyncio
import time


def pacing_clock() -> float:
    """The clock pacing is measured on (seconds, arbitrary origin)."""
    return time.perf_counter()


async def sleep_at_least(seconds: float) -> None:
    """Sleep for ``seconds`` of real time, never less."""
    deadline = pacing_clock() + seconds
    while (left := deadline - pacing_clock()) > 0:
        await asyncio.sleep(left)


class SendLock:
    """The lock a transport serializes its writes on, which knows whether a
    send is holding it or waiting for it.

    A send holds it through its write and the ``inter_command_delay`` after,
    so the next send goes out a delay later. ``wait_idle`` is for a caller
    about to decide whether to send at all (a driver's state-gated poll
    line): it returns once no send holds the lock or waits for it, so the
    answer to a command that went out just before has had that delay to
    arrive. Taking the lock once is not enough. A command sent while the
    caller was already queued takes the lock the moment the caller lets go,
    and the caller would then decide before the command's answer could
    arrive: a poll line judged that way went out a delay after Enter Standby
    and woke the unit again. So the wait queues again behind every send it
    finds and returns only when it finds none.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._senders = 0

    async def __aenter__(self) -> None:
        self._senders += 1
        try:
            await self._lock.acquire()
        except BaseException:
            self._senders -= 1
            raise

    async def __aexit__(self, *exc: object) -> None:
        self._senders -= 1
        self._lock.release()

    async def wait_idle(self) -> None:
        """Return once no send holds the lock or waits for it."""
        while True:
            async with self._lock:
                pass
            if not self._senders:
                return
