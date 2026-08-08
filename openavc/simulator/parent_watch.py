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
import sys

logger = logging.getLogger(__name__)

# How often to look. The cost is one syscall, and the window between the
# parent dying and the ports being released is what someone restarting a
# server actually waits through, so this is short on purpose.
POLL_INTERVAL_SECONDS = 2.0


def parent_is_alive(pid: int) -> bool:
    """True while process ``pid`` still exists. Never touches it.

    The two platforms need genuinely different calls, and using the POSIX one
    on Windows is not a degraded check — it is catastrophic. ``os.kill(pid, 0)``
    is a pure liveness probe on POSIX, but CPython implements ``os.kill`` on
    Windows as ``OpenProcess`` followed by ``TerminateProcess(handle, sig)``,
    so signal 0 does not ask whether a process is alive: **it kills it**, with
    exit code 0. A watchdog polling its parent that way would have shut the
    OpenAVC server down a couple of seconds after simulation started.
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        return _parent_is_alive_windows(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists, it just is not ours to signal.
        return True
    except OSError:
        return True
    return True


def _parent_is_alive_windows(pid: int) -> bool:
    """Windows liveness via a handle wait, which only reads.

    ``SYNCHRONIZE`` is the least privilege that permits a wait, and a zero
    timeout makes it a poll rather than a block. A process object becomes
    signalled once the process exits, so ``WAIT_TIMEOUT`` is the answer that
    means "still running".

    A failed ``OpenProcess`` is read the same way the POSIX branch reads its
    errors: access-denied means the process is there but not ours to look at,
    so it counts as alive. Only "no such process" counts as gone. Getting that
    backwards would shut the simulator down while its server was still running.
    """
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    ERROR_INVALID_PARAMETER = 87

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        # 87 is what Windows returns for a pid that does not exist.
        return ctypes.get_last_error() != ERROR_INVALID_PARAMETER
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


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
