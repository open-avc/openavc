"""THE order a transport runs its receive handler in: one message at a time,
in the order the messages arrived.

A driver's ``on_data_received`` is a coroutine, and the transport that frames
a message cannot await it inline (a slow handler would stall reading, and the
write-drain and silence clocks would read that as the device). So each
message's handler runs as a task. Started as tasks of their own, a handler
that awaited before it had applied its message (an acknowledgement sent back,
a telnet refusal, a sign-in step) let the next message's task run first: two
quick changes to one value landed at the older one, and a reply could be read
before the line that opened it. Here each run starts only once the one before
it has ended, so a handler that awaits keeps its place. A run with nothing
before it starts on the same loop turn a task of its own would, so a handler
that does not await runs exactly as it always did.

Only the handler waits its turn. Reading and framing go on meanwhile, and so
does a ``send_and_wait`` answer, which reaches its queue from the reader, not
through the handler: a handler that sends while a command waits on its answer
is held by the send lock only until that answer arrives. What holds every
later message is a handler that awaits something only a LATER message brings
(a reply its own receive path would resolve): that never arrives now, so a
handler must not wait on one (``docs/creating-drivers.md`` says so).

A run that raises is reported by the transport's own callback, and the next
one still runs. A run cancelled while it waits for its turn closes its
handler unstarted, so nothing is left un-awaited.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine


async def _after(previous: asyncio.Task[Any] | None, handler: Coroutine[Any, Any, Any]) -> None:
    """Run ``handler`` once ``previous`` has ended, however it ended."""
    try:
        if previous is not None and not previous.done():
            await asyncio.wait((previous,))
    except BaseException:
        handler.close()
        raise
    await handler


class HandlerRuns:
    """One transport's receive-handler runs, in arrival order.

    ``tasks`` holds every run until it ends (a task nothing references can be
    collected mid-await). ``on_done`` is the transport's done-callback, which
    logs a run that raised.
    """

    def __init__(self, on_done: Callable[[asyncio.Task[Any]], None]) -> None:
        self._on_done = on_done
        self.tasks: set[asyncio.Task[Any]] = set()
        self._last: asyncio.Task[Any] | None = None

    def run(self, result: object) -> None:
        """Take what the handler returned for one message. A coroutine runs
        once the run before it has ended; anything else has already run."""
        if not asyncio.iscoroutine(result):
            return
        task = asyncio.create_task(_after(self._last, result))
        self._last = task
        self.tasks.add(task)
        task.add_done_callback(self._ended)
        task.add_done_callback(self._on_done)

    def _ended(self, task: asyncio.Task[Any]) -> None:
        self.tasks.discard(task)
        if self._last is task:
            self._last = None
