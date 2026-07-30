"""Exit when the process that launched us is gone.

The simulator runs as a subprocess of an OpenAVC server. When that server
shuts down normally it stops the simulator from its side, and that path works.
What it cannot cover is every way a process ends without running any code of
its own: ``SIGKILL``, an OOM kill, a hard crash, and the server's own
``os._exit(0)`` restart watchdog. In all of those the simulator is reparented
to init and keeps holding its listening ports, so the next server to start
finds them taken and refuses to simulate.

So the child follows the parent instead of the parent reaping the child. This
is the only half that can work, because it is the only half still running.

Deliberately not used when the simulator is launched from a terminal: there is
no parent to follow, and the standalone build already stops itself when the
last UI client disconnects.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# How often to look. The cost is one syscall, and the window between the
# parent dying and the ports being released is what someone restarting a
# server actually waits through, so this is short on purpose.
POLL_INTERVAL_SECONDS = 2.0


def parent_is_alive(pid: int) -> bool:
    """True while process ``pid`` still exists.

    ``os.kill(pid, 0)`` asks the kernel about the process without touching it.
    ``PermissionError`` counts as alive: the process exists, it just is not
    ours to signal.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


async def watch_parent(
    pid: int,
    on_exit,
    interval: float = POLL_INTERVAL_SECONDS,
) -> None:
    """Poll ``pid`` and call ``on_exit`` once it is gone.

    ``on_exit`` is expected to ask uvicorn to shut down gracefully, so the
    device simulators get their normal teardown and the ports close cleanly.
    """
    while True:
        await asyncio.sleep(interval)
        if not parent_is_alive(pid):
            logger.warning(
                "Parent process %d is gone — shutting the simulator down so "
                "its ports are released.",
                pid,
            )
            on_exit()
            return
