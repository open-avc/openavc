"""A Stop asked while a simulation is still starting always ends stopped.

A start takes seconds: it waits for a project save's devices to come up,
spawns the simulator, waits for it to answer, and moves every device over. A
Stop asked in any of those windows used to answer "stopped" and then be undone
when the start finished: simulation came back on with its record, so it also
came back at the next restart. Or the Stop killed the simulator while the
system still said simulation was on.

Pinned here, through a real ``Engine`` with only the simulator subprocess and
its HTTP answers stubbed, for each window a start passes through:

- the Stop answers only once the start has ended, and the start says it was
  stopped rather than that simulation is running
- nothing is left on: no simulator process, no record, no state saying it runs
- every device is back on its own address, and the last address it dialed was
  its own
- the engine stopping during a start leaves no simulator running either
- a second Start while the first is still waiting is refused
"""

from __future__ import annotations

import asyncio

import pytest

import openavc.core.simulation as simulation
from tests.test_simulation_restart import _DialRecorder, _boot, _real, rig  # noqa: F401

_DEVICES = (1, 2)


async def _until(predicate, timeout: float = 5.0) -> None:
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def _hold_the_spawn(held_rig, monkeypatch) -> asyncio.Event:
    """Park the start in the spawn until the returned event is set."""
    released = asyncio.Event()
    spawn = held_rig.spawn

    async def held(*args, **kwargs):
        held_rig.spawn_waiting = True
        await released.wait()
        return await spawn(*args, **kwargs)

    held_rig.spawn_waiting = False
    monkeypatch.setattr(simulation.asyncio, "create_subprocess_exec", held)
    return released


def _hold_the_redirect(monkeypatch) -> asyncio.Event:
    """Park the start in the redirect, each device connected to its
    simulator, until the returned event is set."""
    released = asyncio.Event()
    connect = _DialRecorder.connect

    async def held(self):
        await connect(self)
        if self.config.get("host") == "127.0.0.1":
            await released.wait()

    monkeypatch.setattr(_DialRecorder, "connect", held)
    return released


async def _stop_during(eng, released: asyncio.Event) -> tuple[object, object]:
    """Ask a Stop while the start is held, then let the start go on. Returns
    what the start and the Stop answered."""
    stop = asyncio.ensure_future(eng.simulation.stop())
    for _ in range(20):
        await asyncio.sleep(0)
    released.set()
    return stop


def _assert_stopped(stopped_rig, eng) -> None:
    sim = eng.simulation
    assert not sim.active
    assert sim.status()["active"] is False
    assert sim.status()["starting"] is False
    assert not eng.state.get("system.simulation_active")
    assert not stopped_rig.record.exists()
    assert all(process.returncode is not None for process in stopped_rig.processes)
    for i in _DEVICES:
        device_id, host, port = _real(i)
        config = eng.devices.get_driver(device_id).config
        assert (config["host"], config["port"]) == (host, port)
        last = [attempt for attempt in _DialRecorder.attempts if attempt[0] == device_id][-1]
        assert last == _real(i)


def _assert_the_start_was_stopped(answer) -> None:
    assert isinstance(answer, RuntimeError), answer
    assert "stopped before it finished starting" in str(answer)


@pytest.mark.asyncio
async def test_a_stop_while_the_start_waits_for_the_devices_ends_stopped(rig, monkeypatch):  # noqa: F811
    eng = await _boot(rig, devices=_DEVICES)
    try:
        released = asyncio.Event()
        bringup = eng.wait_for_device_bringup

        async def held_bringup():
            await released.wait()
            await bringup()

        monkeypatch.setattr(eng, "wait_for_device_bringup", held_bringup)
        start = asyncio.ensure_future(eng.simulation.start())
        await asyncio.sleep(0)
        stop = await _stop_during(eng, released)
        started, stopped = await asyncio.wait_for(
            asyncio.gather(start, stop, return_exceptions=True), 10,
        )
        assert stopped is None
        _assert_stopped(rig, eng)
        _assert_the_start_was_stopped(started)
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_stop_during_the_spawn_ends_stopped(rig, monkeypatch):  # noqa: F811
    eng = await _boot(rig, devices=_DEVICES)
    try:
        released = _hold_the_spawn(rig, monkeypatch)
        start = asyncio.ensure_future(eng.simulation.start())
        await _until(lambda: rig.spawn_waiting)
        stop = await _stop_during(eng, released)
        started, stopped = await asyncio.wait_for(
            asyncio.gather(start, stop, return_exceptions=True), 10,
        )
        assert stopped is None
        _assert_stopped(rig, eng)
        _assert_the_start_was_stopped(started)
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_stop_during_the_redirect_ends_stopped(rig, monkeypatch):  # noqa: F811
    eng = await _boot(rig, devices=_DEVICES)
    try:
        released = _hold_the_redirect(monkeypatch)
        start = asyncio.ensure_future(eng.simulation.start())
        await _until(lambda: any(host == "127.0.0.1" for _, host, _ in _DialRecorder.attempts))
        stop = await _stop_during(eng, released)
        started, stopped = await asyncio.wait_for(
            asyncio.gather(start, stop, return_exceptions=True), 10,
        )
        assert stopped is None
        _assert_stopped(rig, eng)
        _assert_the_start_was_stopped(started)
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_the_engine_stopping_during_a_start_leaves_no_simulator_running(rig, monkeypatch):  # noqa: F811
    eng = await _boot(rig, devices=_DEVICES)
    released = _hold_the_spawn(rig, monkeypatch)
    start = asyncio.ensure_future(eng.simulation.start())
    await _until(lambda: rig.spawn_waiting)
    stop = asyncio.ensure_future(eng.stop())
    for _ in range(20):
        await asyncio.sleep(0)
    released.set()
    started, stopped = await asyncio.wait_for(
        asyncio.gather(start, stop, return_exceptions=True), 10,
    )
    assert stopped is None
    assert not eng.simulation.active
    assert all(process.returncode is not None for process in rig.processes)
    assert isinstance(started, RuntimeError), started
    # Nothing was ever moved to the simulator, so nothing is handed back on
    # the way down: each device dialed its own address once, at boot.
    assert sorted(_DialRecorder.attempts) == [_real(i) for i in _DEVICES]


@pytest.mark.asyncio
async def test_a_second_start_while_the_first_waits_is_refused(rig, monkeypatch):  # noqa: F811
    eng = await _boot(rig, devices=_DEVICES)
    try:
        released = asyncio.Event()
        bringup = eng.wait_for_device_bringup

        async def held_bringup():
            await released.wait()
            await bringup()

        monkeypatch.setattr(eng, "wait_for_device_bringup", held_bringup)
        first = asyncio.ensure_future(eng.simulation.start())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="already starting"):
            await asyncio.wait_for(eng.simulation.start(), 2)
        released.set()
        await asyncio.wait_for(first, 10)
        assert eng.simulation.active
        assert rig.launches == 1
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_shutdown_does_not_wait_for_a_hand_back_already_under_way(rig, monkeypatch):  # noqa: F811
    """A service stop signals the simulator and the server together, so the
    monitor may already be handing the devices back when the engine's stop
    reaches the simulation. The stop ends that hand-back rather than waiting
    for every device to reach its real address."""
    rig.leave_simulation_on()
    eng = await _boot(rig, devices=_DEVICES)
    never = asyncio.Event()
    connect = _DialRecorder.connect

    async def held(self):
        await connect(self)
        if self.config.get("host") != "127.0.0.1":
            await never.wait()

    monkeypatch.setattr(_DialRecorder, "connect", held)
    rig.processes[0].returncode = -15
    await _until(lambda: any(host != "127.0.0.1" for _, host, _ in _DialRecorder.attempts))
    await asyncio.wait_for(eng.stop(), 5)
    assert not eng.simulation.active
    assert rig.record.exists()


async def _cancel_during_the_redirect(eng, monkeypatch) -> list:
    """Cancel a start held in the redirect; return the dials made after the
    redirect began."""
    released = _hold_the_redirect(monkeypatch)
    start = asyncio.ensure_future(eng.simulation.start())
    await _until(lambda: any(host == "127.0.0.1" for _, host, _ in _DialRecorder.attempts))
    began = len(_DialRecorder.attempts)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(start, 10)
    released.set()
    return _DialRecorder.attempts[began:]


@pytest.mark.asyncio
async def test_a_start_cancelled_while_the_server_runs_puts_the_devices_back(rig, monkeypatch):  # noqa: F811
    eng = await _boot(rig, devices=_DEVICES)
    try:
        after = await _cancel_during_the_redirect(eng, monkeypatch)
        assert sorted(after) == [_real(i) for i in _DEVICES]
        assert not eng.simulation.active
        assert eng.simulation.status()["starting"] is False
        assert all(process.returncode is not None for process in rig.processes)
        for i in _DEVICES:
            device_id, host, port = _real(i)
            config = eng.devices.get_driver(device_id).config
            assert (config["host"], config["port"]) == (host, port)
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_start_cancelled_while_the_server_stops_dials_nothing_real(rig, monkeypatch):  # noqa: F811
    """What Engine.stop does to a resume: it marks itself stopping, then
    cancels the device bring-up the resume runs in."""
    eng = await _boot(rig, devices=_DEVICES)
    eng._stopping = True
    after = await _cancel_during_the_redirect(eng, monkeypatch)
    assert after == []
    assert not eng.simulation.active
    assert all(process.returncode is not None for process in rig.processes)
    await eng.stop()
