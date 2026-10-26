"""Simulation that is on comes back when the server starts again.

Before this, simulation lived only in the process: a restart (a power cut, an
update) brought a simulated room up with every device dialing its configured
address, which on a demo or training box is nothing, so the room sat offline
until somebody started simulation again by hand.

Pinned here, through a real ``Engine.start()`` with only the simulator
subprocess and its HTTP answers stubbed:

- a simulation left on comes back, and every device's FIRST connect is the
  simulator's address, never the real one
- a shutdown neither forgets it nor hands the devices back to the real
  equipment on the way down; Stop Simulation is the one thing that forgets it
- a comeback that fails, and a simulator that stops mid-run, leave the devices
  on their real addresses, say so in ``system.simulation_error``, and keep the
  record so the next start tries again
- the selection a start was given is what comes back, until a save widens the
  run to every device, as it does while running
"""

from __future__ import annotations

import asyncio
import json
import json as _json
from pathlib import Path

import pytest

import openavc.core.simulation as simulation
from openavc.core.engine import Engine
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver


class _DialRecorder(BaseDriver):
    """Connects at once, and remembers every address it was asked to dial."""

    DRIVER_INFO = {
        "id": "acme_dial_recorder",
        "name": "Acme Dial Recorder",
        "manufacturer": "Acme",
        "category": "display",
        "transport": "tcp",
        "config_schema": {
            "host": {"type": "string", "label": "Host"},
            "port": {"type": "integer", "label": "Port", "default": 5000},
        },
        "state_variables": {},
        "commands": {},
    }

    attempts: list[tuple[str, str, int]] = []

    async def connect(self) -> None:
        type(self).attempts.append(
            (self.device_id, self.config.get("host"), self.config.get("port"))
        )
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="test")

    async def disconnect(self) -> None:
        self._connected = False
        self.state.set(f"device.{self.device_id}.connected", False, source="test")

    async def send_command(self, command, params=None):
        return None


def _real(i: int) -> tuple[str, str, int]:
    return (f"d{i}", f"10.0.0.{i}", 5000)


def _simulated(i: int) -> tuple[str, str, int]:
    return (f"d{i}", "127.0.0.1", 19000 + i)


# ── The simulator, stubbed at the process and HTTP edges ────────────────────

class _Pipe:
    async def readline(self):
        return b""


class _Process:
    def __init__(self):
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.returncode = None

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class _Resp:
    def __init__(self, data):
        self.status = 200
        self._data = data

    async def json(self):
        return self._data


class _Session:
    """Answers the start's one question: which port each device got."""

    def __init__(self, rig):
        self._rig = rig

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=None):
        # A save adding a device to the running simulator.
        device_id = url.rsplit("/", 2)[-2]
        return _Resp({"port": 19000 + int(device_id[1:])})

    async def get(self, url, timeout=None):
        payload = _json.loads(self._rig.config_path().read_text())
        return _Resp({"devices": [
            {"device_id": d["device_id"], "port": 19000 + int(d["device_id"][1:])}
            for d in payload["devices"]
        ]})


class _Rig:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.record = tmp_path / "data" / simulation.RECORD_NAME
        self.launches = 0
        self.processes: list[_Process] = []
        self.fail_launch = False
        self.engine: Engine | None = None

    def config_path(self):
        return Path(self.engine.simulation._config_path)

    async def spawn(self, *args, **kwargs):
        self.launches += 1
        if self.fail_launch:
            raise OSError("Address already in use")
        process = _Process()
        self.processes.append(process)
        return process

    def write_project(self, devices) -> str:
        path = self.tmp_path / "project.avc"
        path.write_text(json.dumps({
            "openavc_version": "0.8.0",
            "project": {"id": "p", "name": "P"},
            "variables": [], "macros": [], "device_groups": [],
            "connections": {}, "scripts": [], "plugins": {},
            "devices": [
                {"id": f"d{i}", "name": f"Device {i}", "driver": "acme_dial_recorder",
                 "enabled": True, "config": {"host": f"10.0.0.{i}", "port": 5000}}
                for i in devices
            ],
            "ui": {"settings": {}, "pages": []},
            "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
        }), encoding="utf-8")
        return str(path)

    def leave_simulation_on(self, device_ids=None):
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text(json.dumps({"device_ids": device_ids}), encoding="utf-8")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rig = _Rig(tmp_path)
    monkeypatch.setattr(simulation, "_record_path", lambda: rig.record)
    monkeypatch.setattr(simulation, "_choose_ui_port", lambda preferred: preferred)
    monkeypatch.setattr(simulation.asyncio, "create_subprocess_exec", rig.spawn)

    async def ready(self, process, ui_port):
        return None

    monkeypatch.setattr(simulation.SimulationManager, "_await_simulator_ready", ready)
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: _Session(rig))
    _DialRecorder.attempts = []
    register_driver(_DialRecorder)
    try:
        yield rig
    finally:
        unregister_driver("acme_dial_recorder")
        _DialRecorder.attempts = []


async def _boot(rig, devices=(1, 2)) -> Engine:
    rig.engine = Engine(rig.write_project(list(devices)))
    await asyncio.wait_for(rig.engine.start(), timeout=20)
    return rig.engine


# ── Coming back ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_simulation_left_on_comes_back_before_any_device_dials(rig):
    rig.leave_simulation_on()
    eng = await _boot(rig)
    try:
        assert eng.simulation.active
        assert eng.state.get("system.simulation_active") is True
        # One attempt each, and each one straight at its simulator.
        assert sorted(_DialRecorder.attempts) == [_simulated(1), _simulated(2)]
        assert not eng.state.get("system.simulation_error")
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_without_the_record_the_devices_dial_their_own_addresses(rig):
    eng = await _boot(rig)
    try:
        assert not eng.simulation.active
        assert rig.launches == 0
        assert sorted(_DialRecorder.attempts) == [_real(1), _real(2)]
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_shutdown_keeps_it_on_and_never_dials_the_real_equipment(rig):
    rig.leave_simulation_on()
    eng = await _boot(rig)
    await eng.stop()
    # Nothing was handed back to the real addresses on the way down...
    assert all(host == "127.0.0.1" for _, host, _ in _DialRecorder.attempts)
    assert rig.processes[0].returncode is not None
    # ...and the next start brings simulation back.
    assert rig.record.exists()
    _DialRecorder.attempts = []
    eng = await _boot(rig)
    try:
        assert eng.simulation.active
        assert sorted(_DialRecorder.attempts) == [_simulated(1), _simulated(2)]
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_stop_simulation_is_what_forgets_it(rig):
    eng = await _boot(rig)
    try:
        await eng.simulation.start()
        assert json.loads(rig.record.read_text()) == {"device_ids": None}
        await eng.simulation.stop()
        assert not rig.record.exists()
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_the_selection_a_start_was_given_is_what_comes_back(rig):
    eng = await _boot(rig, devices=(1, 2, 3))
    await eng.simulation.start(["d2"])
    assert json.loads(rig.record.read_text()) == {"device_ids": ["d2"]}
    await eng.stop()

    _DialRecorder.attempts = []
    eng = await _boot(rig, devices=(1, 2, 3))
    try:
        assert eng.simulation.simulated_devices == ["d2"]
        assert sorted(_DialRecorder.attempts) == [_real(1), _simulated(2), _real(3)]
        # A save simulates every device the project has, so from then on a
        # restart brings back every device too.
        eng._schedule_device_bringup()
        await eng.wait_for_device_bringup()
        assert sorted(eng.simulation.simulated_devices) == ["d1", "d2", "d3"]
        assert json.loads(rig.record.read_text()) == {"device_ids": None}
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_project_with_no_devices_still_comes_back_simulating(rig):
    """What was running was a simulator with nothing in it; a device added
    later joins it instead of dialing the real equipment."""
    rig.leave_simulation_on()
    eng = await _boot(rig, devices=())
    try:
        assert eng.simulation.active
        assert not eng.state.get("system.simulation_error")
    finally:
        await eng.stop()


# ── When it cannot run ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_comeback_that_fails_says_so_and_the_devices_dial_their_own(rig):
    rig.leave_simulation_on()
    rig.fail_launch = True
    eng = await _boot(rig)
    try:
        assert not eng.simulation.active
        assert sorted(_DialRecorder.attempts) == [_real(1), _real(2)]
        error = eng.state.get("system.simulation_error")
        assert error.startswith("Simulation did not resume after the restart")
        assert "Address already in use" in error
        # Nobody turned it off, so the next start tries again.
        assert rig.record.exists()
        # Stop Simulation is how somebody says "leave it off".
        await eng.simulation.stop()
        assert not rig.record.exists()
        assert eng.state.get("system.simulation_error") == ""
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_simulator_that_stops_mid_run_says_so_and_stays_on(rig):
    """A service stop signals the simulator and the server together, so the
    simulator exiting is not somebody turning simulation off."""
    rig.leave_simulation_on()
    eng = await _boot(rig)
    try:
        eng.simulation._monitor_task.cancel()
        rig.processes[0].returncode = 0
        await eng.simulation._monitor_process()
        assert not eng.simulation.active
        assert eng.state.get("system.simulation_error").startswith("The simulator stopped")
        assert rig.record.exists()
        # A start clears the sentence.
        await eng.simulation.start()
        assert eng.state.get("system.simulation_error") == ""
    finally:
        await eng.stop()
