"""The simulator follows the server that launched it.

Why this exists: the graceful shutdown path already stops the simulator from
the server side, and it works. What it cannot cover is a server that ends
without running any code of its own — SIGKILL, an OOM kill, a hard crash, or
the restart flow's own ``os._exit(0)`` watchdog. In every one of those the
simulator is reparented to init and keeps its listening ports, and the next
server to start finds them taken.

So these pin the half that is still running when that happens: the child.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from simulator.parent_watch import parent_is_alive, watch_parent


def test_own_process_reads_as_alive():
    assert parent_is_alive(os.getpid()) is True


def test_reaped_child_reads_as_gone():
    """A pid that has exited and been waited on must read as gone.

    Uses a real process rather than a fabricated pid: on a busy machine a
    made-up number can belong to something, which would make the assertion
    lie in exactly the direction that matters.
    """
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert parent_is_alive(proc.pid) is False


@pytest.mark.parametrize("pid", [0, -1])
def test_nonsense_pids_are_not_alive(pid):
    """Absent or malformed config must not read as a live parent.

    A missing ``parent_pid`` becoming "the parent is alive" would silently
    disable the watchdog, which is the failure this whole module exists to
    prevent, so it fails the safe way instead.
    """
    assert parent_is_alive(pid) is False


@pytest.mark.asyncio
async def test_watch_calls_back_once_the_parent_is_gone():
    alive = {"value": True}
    called = []

    def fake_alive(_pid):
        return alive["value"]

    import simulator.parent_watch as pw

    original = pw.parent_is_alive
    pw.parent_is_alive = fake_alive
    try:
        task = asyncio.ensure_future(
            watch_parent(12345, lambda: called.append(True), interval=0.01),
        )
        await asyncio.sleep(0.05)
        assert called == [], "should not fire while the parent is alive"
        alive["value"] = False
        await asyncio.wait_for(task, timeout=2.0)
        assert called == [True]
    finally:
        pw.parent_is_alive = original


@pytest.mark.asyncio
async def test_watch_stays_quiet_while_the_parent_lives():
    called = []
    task = asyncio.ensure_future(
        watch_parent(os.getpid(), lambda: called.append(True), interval=0.01),
    )
    await asyncio.sleep(0.08)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert called == []


def test_the_server_passes_its_own_pid_to_the_simulator():
    """The watchdog is inert unless the launcher actually tells it who to follow.

    Pinned at the source-text level because the alternative is spawning a real
    simulator subprocess in a unit test; the live proof that this wiring works
    is a killed server and an ``lsof`` reading.
    """
    from pathlib import Path

    import server.core.simulation as simulation

    source = Path(simulation.__file__).read_text(encoding="utf-8")
    assert '"parent_pid": os.getpid()' in source
