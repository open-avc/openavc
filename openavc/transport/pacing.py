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
