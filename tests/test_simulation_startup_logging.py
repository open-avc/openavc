"""Tests for simulator subprocess startup log forwarding (A34).

The readiness loop in SimulationManager reads stderr chunks while waiting
for "Uvicorn running" to appear. Before A34, those chunks were collected
into a local list and then discarded the moment readiness was reached —
so any diagnostic uvicorn or simulator code emitted during startup was
invisible to the operator. Subsequent drain tasks pick up everything
AFTER the loop exits, but startup-window output is gone.
"""

import asyncio
import logging
import socket
from unittest.mock import MagicMock

import pytest

from openavc.core.simulation import (
    SIMULATOR_UI_PORT,
    SimulationManager,
    _port_is_taken,
    _startup_failure_message,
)


class _FakeStream:
    """Async stream that returns a fixed sequence of byte chunks."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, n: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        # Drain: nothing more to read, simulate a small wait.
        await asyncio.sleep(0.01)
        return b""


class _FakeProcess:
    """Stand-in for asyncio.subprocess.Process — enough for the readiness loop."""

    def __init__(self, stderr_chunks: list[bytes], returncode: int | None = None):
        self.stderr = _FakeStream(stderr_chunks)
        self.stdout = _FakeStream([])
        self.returncode = returncode


def _manager() -> SimulationManager:
    # _await_simulator_ready never touches self.engine, so any sentinel is
    # safe. Mock it to avoid pulling in the rest of the stack.
    return SimulationManager(engine=object())


@pytest.mark.asyncio
async def test_startup_stderr_lines_are_logged(caplog):
    """Regression for A34: each stderr line emitted during the readiness
    window must be forwarded to the openavc logger, not discarded.
    """
    proc = _FakeProcess(
        stderr_chunks=[
            b"INFO:     Started server process\n",
            b"INFO:     Waiting for application startup\n",
            b"INFO:     Uvicorn running on http://127.0.0.1:19500\n",
        ],
    )
    mgr = _manager()

    with caplog.at_level(logging.INFO, logger="openavc.core.simulation"):
        await mgr._await_simulator_ready(proc)

    log_messages = [r.getMessage() for r in caplog.records]
    assert any("Started server process" in m for m in log_messages)
    assert any("Waiting for application startup" in m for m in log_messages)
    assert any("Uvicorn running" in m for m in log_messages)


@pytest.mark.asyncio
async def test_startup_ignores_blank_lines(caplog):
    """Blank lines from the subprocess shouldn't clutter the log."""
    proc = _FakeProcess(
        stderr_chunks=[b"\n\nINFO: actual content\n\nUvicorn running\n"],
    )
    mgr = _manager()

    with caplog.at_level(logging.INFO, logger="openavc.core.simulation"):
        await mgr._await_simulator_ready(proc)

    log_messages = [r.getMessage() for r in caplog.records if "simulator.stderr" in r.getMessage()]
    # Only the two non-blank lines forwarded.
    assert sum(1 for m in log_messages if m.strip()) == 2


@pytest.mark.asyncio
async def test_process_exit_during_startup_raises_with_output():
    """If the subprocess exits during the readiness window, the error
    message must include captured stderr — the operator needs to know
    what went wrong.
    """
    proc = _FakeProcess(
        stderr_chunks=[b"FATAL: ModuleNotFoundError: no module named 'aiohttp'\n"],
        returncode=1,
    )
    mgr = _manager()

    with pytest.raises(RuntimeError, match="exited with code 1") as exc:
        await mgr._await_simulator_ready(proc)
    assert "ModuleNotFoundError" in str(exc.value)


@pytest.mark.asyncio
async def test_no_ready_marker_warns_but_does_not_raise(caplog):
    """The simulator might be silently up if uvicorn's banner changes
    wording — log a warning but don't raise.
    """
    proc = _FakeProcess(stderr_chunks=[])  # nothing emitted
    mgr = _manager()

    with caplog.at_level(logging.WARNING, logger="openavc.core.simulation"):
        await mgr._await_simulator_ready(proc)

    assert any("readiness not confirmed" in r.getMessage() for r in caplog.records)


# ── Port-in-use is the one failure worth naming ─────────────────────────────
#
# The simulator UI port is fixed, and the simulator can outlive a server that
# exits without stopping it, so "something already holds 19500" is by far the
# most common way this fails. It used to surface as a truncated uvicorn blob
# that never said 19500 was ours, what held it, or what to do.


@pytest.mark.asyncio
async def test_port_in_use_explains_the_port_and_the_fix():
    proc = _FakeProcess(
        stderr_chunks=[
            b"ERROR:    [Errno 48] error while attempting to bind on address "
            b"('127.0.0.1', 19500): address already in use\n",
        ],
        returncode=1,
    )
    mgr = _manager()

    with pytest.raises(RuntimeError) as exc:
        await mgr._await_simulator_ready(proc)

    message = str(exc.value)
    assert str(SIMULATOR_UI_PORT) in message
    assert "Simulator UI" in message
    assert "another OpenAVC instance" in message
    assert "start simulation again" in message
    # The raw blob is what it replaces, not something it wraps.
    assert "Errno 48" not in message
    assert "exited with code" not in message


@pytest.mark.parametrize(
    "stderr",
    [
        "[Errno 48] ...: address already in use",          # macOS
        "[Errno 98] ...: address already in use",          # Linux
        "[Errno 10048] only one usage of each socket "     # Windows
        "address (protocol/network address/port) is normally permitted",
    ],
)
def test_every_platform_s_bind_failure_is_recognised(stderr):
    """Neither the errno number nor the wording is shared across platforms,
    and local gates only ever run on one of them."""
    assert "Simulator UI" in _startup_failure_message(1, stderr)


def test_an_unrecognised_failure_keeps_the_raw_output():
    """A cause we cannot name is still best served by what the process said."""
    message = _startup_failure_message(3, "Traceback: KeyError: 'devices'")
    assert "exited with code 3" in message
    assert "KeyError: 'devices'" in message


@pytest.mark.asyncio
async def test_bind_failure_beats_the_ready_marker_in_the_same_chunk():
    """The ordering that made this a false success rather than an error.

    Uvicorn prints "Application startup complete" and *then* the bind error,
    and both arrive in one read. Taking the marker at face value returned
    success, after which the caller queried port 19500 and was answered by the
    other instance's simulator — so this instance reported it was simulating
    while its devices pointed at a simulator it did not own.
    """
    proc = _FakeProcess(
        stderr_chunks=[
            b"INFO:     Application startup complete.\n"
            b"ERROR:    [Errno 48] error while attempting to bind on address "
            b"('127.0.0.1', 19500): address already in use\n"
            b"INFO:     Waiting for application shutdown.\n",
        ],
    )
    mgr = _manager()

    with pytest.raises(RuntimeError) as exc:
        await mgr._await_simulator_ready(proc)
    assert "already in use" in str(exc.value)
    assert str(SIMULATOR_UI_PORT) in str(exc.value)


@pytest.mark.asyncio
async def test_a_clean_ready_marker_is_still_ready():
    proc = _FakeProcess(
        stderr_chunks=[b"INFO:     Uvicorn running on http://127.0.0.1:19500\n"],
    )
    await _manager()._await_simulator_ready(proc)  # no raise


# ── The port is checked before anything is spawned ──────────────────────────


def test_port_probe_sees_a_listener_and_an_empty_port():
    """Deterministic, and the reason the message above is reachable at all.

    Scraping uvicorn's stderr could not be relied on: the bind error lands
    after "Application startup complete", sometimes in the same read and
    sometimes in a later one that the readiness loop had already stopped
    reading. Asking the port directly has no such ordering.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert _port_is_taken(port) is True
    # Closed again: nothing is serving there now.
    assert _port_is_taken(port) is False


@pytest.mark.asyncio
async def test_start_refuses_before_spawning_when_the_ui_port_is_held(monkeypatch):
    """No subprocess, no redirected devices, and a message that names the
    port — rather than a success whose device ports point at somebody else's
    simulator."""
    spawned = []
    monkeypatch.setattr(
        "openavc.core.simulation._port_is_taken", lambda port, host="127.0.0.1": True
    )
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", lambda *a, **k: spawned.append(a)
    )

    mgr = _manager()
    mgr.engine = MagicMock()
    mgr.engine.project = {"project": {"name": "Scratch"}}
    mgr.engine.devices._device_configs = {
        "dev1": {"driver": "acme_widget", "name": "Dev 1", "config": {}},
    }

    with pytest.raises(RuntimeError, match="already in use"):
        await mgr.start()

    assert spawned == [], "nothing may be spawned once the port is known to be held"
    assert mgr.active is False
    assert mgr.simulated_devices == []
