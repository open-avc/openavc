"""Tests for simulator subprocess startup log forwarding (A34).

The readiness loop in SimulationManager reads stderr chunks while waiting
for "Uvicorn running" to appear. Before A34, those chunks were collected
into a local list and then discarded the moment readiness was reached —
so any diagnostic uvicorn or simulator code emitted during startup was
invisible to the operator. Subsequent drain tasks pick up everything
AFTER the loop exits, but startup-window output is gone.
"""

import asyncio
import contextlib
import logging
import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.simulation import (
    SIMULATOR_UI_PORT,
    SimulationManager,
    _choose_ui_port,
    _port_is_bindable,
    _port_is_taken,
    _startup_failure_message,
)


@contextlib.contextmanager
def _listening_port():
    """A port with something actually serving on it, and its number.

    The readiness check confirms the simulator answers before reporting
    success, so a test that walks the success path has to give it something
    that answers. Binding a real socket is both the honest way to do that and
    the only way to prove the confirmation works at all.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        yield held.getsockname()[1]


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
        with _listening_port() as port:
            await mgr._await_simulator_ready(proc, port)

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
        with _listening_port() as port:
            await mgr._await_simulator_ready(proc, port)

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
        await mgr._await_simulator_ready(proc, SIMULATOR_UI_PORT)
    assert "ModuleNotFoundError" in str(exc.value)


@pytest.mark.asyncio
async def test_no_ready_marker_warns_but_does_not_raise(caplog):
    """The simulator might be silently up if uvicorn's banner changes
    wording — log a warning but don't raise.
    """
    proc = _FakeProcess(stderr_chunks=[])  # nothing emitted
    mgr = _manager()

    with caplog.at_level(logging.WARNING, logger="openavc.core.simulation"):
        await mgr._await_simulator_ready(proc, SIMULATOR_UI_PORT)

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
        await mgr._await_simulator_ready(proc, SIMULATOR_UI_PORT)

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
        await mgr._await_simulator_ready(proc, SIMULATOR_UI_PORT)
    assert "already in use" in str(exc.value)
    assert str(SIMULATOR_UI_PORT) in str(exc.value)


@pytest.mark.asyncio
async def test_a_clean_ready_marker_is_still_ready():
    proc = _FakeProcess(
        stderr_chunks=[b"INFO:     Uvicorn running on http://127.0.0.1:19500\n"],
    )
    with _listening_port() as port:
        await _manager()._await_simulator_ready(proc, port)  # no raise


# ── The port is chosen before anything is spawned ───────────────────────────


def test_port_probe_sees_a_listener_and_an_empty_port():
    """Deterministic, and the reason the message above is reachable at all.

    Scraping uvicorn's stderr could not be relied on: the bind error lands
    after "Application startup complete", sometimes in the same read and
    sometimes in a later one that the readiness loop had already stopped
    reading. Asking the port directly has no such ordering.
    """
    with _listening_port() as port:
        assert _port_is_taken(port) is True
    # Closed again: nothing is serving there now.
    assert _port_is_taken(port) is False


def test_bindable_and_taken_are_different_questions():
    """The gap that let a reserved port through the pre-flight.

    A port somebody is listening on is neither bindable nor free, and the two
    probes agree about it. They part company on a port that is *reserved* --
    nobody listening, still unbindable -- which is what Hyper-V, WSL and Docker
    create on Windows and what `_port_is_taken` alone called free. That case
    cannot be manufactured portably, so what is pinned here is the direction of
    the two answers on a port that IS held: bindable must say no.
    """
    with _listening_port() as port:
        assert _port_is_taken(port) is True
        assert _port_is_bindable(port) is False


def test_a_held_port_is_stepped_over_rather_than_fatal():
    """The behaviour change: an unavailable port moves the simulator, it does
    not stop it.

    Nothing user-facing depends on the number -- the browser reaches the
    simulator at /simulator/ on this server's origin -- so refusing to start
    over it turned a recoverable condition into a dead feature with no way out
    from inside the app.
    """
    with _listening_port() as held:
        chosen = _choose_ui_port(held)
    assert chosen != held
    assert chosen > held
    assert _port_is_bindable(chosen)


def test_a_whole_contiguous_block_is_stepped_over():
    """A reserved range is hundreds of ports wide, not one.

    The Windows exclusion that prompted all this was 19469-19568 -- a hundred
    consecutive ports. Walking one port and giving up would have landed right
    back inside it, so hold a genuinely contiguous run and require the walk to
    come out the far side.
    """
    holders = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1] + 1
        for offset in range(20):
            s_ = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s_.bind(("127.0.0.1", base + offset))
                s_.listen(1)
            except OSError:
                s_.close()
                break
            holders.append(s_)
        if len(holders) < 3:
            pytest.skip("could not hold a contiguous run of ports on this machine")

        held = {h.getsockname()[1] for h in holders}
        chosen = _choose_ui_port(base)
        assert chosen not in held, "the walk stopped inside the held block"
        assert chosen >= base + len(holders)
        assert _port_is_bindable(chosen)
    finally:
        for h in holders:
            h.close()


@pytest.mark.asyncio
async def test_start_refuses_when_no_port_in_the_span_will_bind(monkeypatch):
    """No subprocess and no redirected devices when there is nowhere to serve.

    The refusal survives; what changed is when it fires. It used to trip on the
    configured port being busy, which is recoverable; now it takes the whole
    search window being unusable, which is not.
    """
    spawned = []
    monkeypatch.setattr(
        "openavc.core.simulation._port_is_bindable",
        lambda port, host="127.0.0.1": False,
    )
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", lambda *a, **k: spawned.append(a)
    )

    mgr = _manager()
    mgr.engine = MagicMock()
    mgr.engine.wait_for_device_bringup = AsyncMock()
    mgr.engine.project = {"project": {"name": "Scratch"}}
    mgr.engine.devices._device_configs = {
        "dev1": {"driver": "acme_widget", "name": "Dev 1", "config": {}},
    }

    with pytest.raises(RuntimeError, match="could be opened"):
        await mgr.start()

    assert spawned == [], "nothing may be spawned when no port can be opened"
    assert mgr.active is False
    assert mgr.simulated_devices == []


@pytest.mark.asyncio
async def test_the_chosen_port_is_the_one_handed_to_the_child(monkeypatch):
    """The child must be told where to listen, and the proxy must follow it.

    A moved port that only half the system knows about is worse than not
    moving it: the simulator would come up somewhere the UI never looks.
    """
    import json

    from openavc.core import simulation

    with _listening_port() as held:
        monkeypatch.setattr(simulation, "simulator_ui_port", lambda: held)

        captured = {}

        async def _fake_spawn(*cmd, **kwargs):
            cfg_path = cmd[cmd.index("--config") + 1]
            with open(cfg_path) as fh:
                captured.update(json.load(fh))
            raise RuntimeError("stop here — the config is what we came for")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_spawn)

        mgr = _manager()
        mgr.engine = MagicMock()
        mgr.engine.wait_for_device_bringup = AsyncMock()
        mgr.engine.project = {"project": {"name": "Scratch"}}
        mgr.engine.devices._device_configs = {
            "dev1": {"driver": "acme_widget", "name": "Dev 1", "config": {}},
        }

        with pytest.raises(RuntimeError):
            await mgr.start()

    assert captured["ui_port"] != held, "the held port must not be handed on"
    assert _port_is_bindable(captured["ui_port"])
