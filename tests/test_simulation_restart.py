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
  equipment on the way down, even when the stop signal reaches the simulator
  too and it exits before the devices are disconnected; Stop Simulation is the
  one thing that forgets it
- a comeback that fails, and a simulator that stops mid-run, leave the devices
  on their real addresses, say so in ``system.simulation_error``, and keep the
  record so the next start tries again
- the selection a start was given survives saves and subsequent restarts
- the simulator's settings, which carry each device's saved logins, are never
  left on disk: a server that ends without its own clean-up (an update's
  restart, a crash, a kill) leaves no login in the temp folder, and a server
  that starts removes the launch file an earlier version left there
"""

from __future__ import annotations

import asyncio
import json
import json as _json
import signal
import sys
from types import SimpleNamespace

import pytest

import openavc.core.simulation as simulation
from openavc import listen_sockets
from openavc.core.engine import Engine
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.middleware.startup_splash import RunState


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
            "password": {"type": "string", "label": "Password", "secret": True},
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


class _Stdin:
    """The simulator's standard input: what the start hands it."""

    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        assert not self.closed, "written after it was closed"
        self.data += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def settings(self) -> dict:
        assert self.closed, "the simulator reads to the end of its input"
        return _json.loads(bytes(self.data))


class _Process:
    def __init__(self):
        self.stdin = _Stdin()
        self.stdout = _Pipe()
        self.stderr = _Pipe()
        self.returncode = None

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def receives(self, sig: int) -> None:
        """A signal reaches the simulator itself, not through the server: it
        exits on it at once, as a POSIX process ended by one reports."""
        self.returncode = -sig

    async def wait(self):
        return self.returncode


class _MainServer:
    """uvicorn's server, as far as a stop goes: the one that hears the signal,
    with the config its drain is set on."""

    def __init__(self):
        self.heard: list[int] = []
        self.config = SimpleNamespace()

    def handle_exit(self, sig, frame):
        self.heard.append(sig)


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
        payload = self._rig.processes[-1].stdin.settings()
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

    async def spawn(self, *args, **kwargs):
        self.launches += 1
        if self.fail_launch:
            raise OSError("Address already in use")
        process = _Process()
        self.processes.append(process)
        return process

    def write_project(self, devices, password: str | None = None) -> str:
        config = {"port": 5000}
        if password is not None:
            config["password"] = password
        path = self.tmp_path / "project.avc"
        path.write_text(json.dumps({
            "openavc_version": "0.8.0",
            "project": {"id": "p", "name": "P"},
            "variables": [], "macros": [], "device_groups": [],
            "connections": {}, "scripts": [], "plugins": {},
            "devices": [
                {"id": f"d{i}", "name": f"Device {i}", "driver": "acme_dial_recorder",
                 "enabled": True, "config": {"host": f"10.0.0.{i}", **config}}
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


async def _boot(
    rig, devices=(1, 2), password: str | None = None, *, stopping: bool = False,
) -> Engine:
    """An engine started as the server starts it: its run-state record
    attached first (``openavc.main``), saying stopping already when a stop
    was heard before the start."""
    rig.engine = Engine(rig.write_project(list(devices), password))
    rig.run_state = RunState()
    if stopping:
        rig.run_state.stopping()
    rig.engine.attach_server(run_state=rig.run_state)
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


# How the stop reaches the server and the simulator beside it.
_SERVER_ALONE = "the stop signal reaches the server alone"
_TOGETHER = "the stop signal reaches the server and the simulator together"
_DURING_THE_STOP = "the simulator exits as the engine stops, before the devices disconnect"


@pytest.mark.asyncio
@pytest.mark.parametrize("how", [_SERVER_ALONE, _TOGETHER, _DURING_THE_STOP])
async def test_a_shutdown_keeps_it_on_and_never_dials_the_real_equipment(rig, monkeypatch, how):
    """A service manager signals the server alone (the systemd unit's
    ``KillMode=mixed``), and the server's own stop ends the simulator once the
    devices are disconnected. A signal that reaches the whole group anyway (a
    supervisor that signals every process, a unit from an earlier release)
    ends the simulator first, while the server is still draining its
    connections, and the watcher finds it gone long before the engine's stop.
    Either way that exit is the stop's: no device is pointed back at its real
    address on the way down, nothing says the simulator stopped, and
    simulation comes back with the server."""
    monkeypatch.setattr(listen_sockets, "_stop_heard", False)
    rig.leave_simulation_on()
    eng = await _boot(rig)
    simulator = rig.processes[0]
    if how == _DURING_THE_STOP:
        disconnect_all = eng.devices.disconnect_all

        async def the_simulator_exits_first():
            await _the_simulator_exits(eng, simulator)
            await disconnect_all()

        monkeypatch.setattr(eng.devices, "disconnect_all", the_simulator_exits_first)
    else:
        server = _the_main_server_hears(signal.SIGTERM, rig)
        assert server.heard == [signal.SIGTERM]
        if how == _TOGETHER:
            await _the_simulator_exits(eng, simulator)
    # The lifespan's end, once uvicorn has drained its connections.
    await eng.stop()

    # Nothing was handed back to the real addresses on the way down...
    assert all(host == "127.0.0.1" for _, host, _ in _DialRecorder.attempts)
    assert not eng.state.get("system.simulation_error")
    # ...the simulator ran until the engine's stop ended it, unless the
    # signal ended it first...
    assert simulator.returncode == (0 if how == _SERVER_ALONE else -signal.SIGTERM)
    # ...and the next start brings simulation back.
    assert rig.record.exists()
    # A new process, with a record of its own.
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
        # Saving must not pull the real devices into the simulation.
        eng._schedule_device_bringup()
        await eng.wait_for_device_bringup()
        assert eng.simulation.simulated_devices == ["d2"]
        assert json.loads(rig.record.read_text()) == {"device_ids": ["d2"]}
    finally:
        await eng.stop()

    _DialRecorder.attempts = []
    eng = await _boot(rig, devices=(1, 2, 3))
    try:
        assert sorted(_DialRecorder.attempts) == [_real(1), _simulated(2), _real(3)]
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
    """The simulator exiting while the server runs is not somebody turning
    simulation off: the devices go back to the real equipment until the next
    start, which brings simulation back."""
    rig.leave_simulation_on()
    eng = await _boot(rig)
    try:
        await _the_simulator_exits(eng, rig.processes[0])
        assert not eng.simulation.active
        assert sorted(_DialRecorder.attempts[2:]) == [_real(1), _real(2)]
        assert eng.state.get("system.simulation_error").startswith("The simulator stopped")
        assert rig.record.exists()
        # A start clears the sentence.
        await eng.simulation.start()
        assert eng.state.get("system.simulation_error") == ""
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_comeback_that_fails_as_the_server_stops_dials_nothing_real(rig, monkeypatch):
    """A stop heard while the server is still starting: the simulation that
    was on cannot come back (the same signal ended its simulator), and the
    devices it would have moved are left undialed for the stop rather than
    sent to their real equipment."""
    rig.leave_simulation_on()
    rig.fail_launch = True
    eng = await _boot(rig, stopping=True)
    try:
        assert _DialRecorder.attempts == []
        assert rig.record.exists()
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_simulator_that_exits_while_the_server_starts_is_not_taken_for_a_stop(rig):
    """The engine is not running yet while a resumed simulation is already
    being watched; a simulator that dies then is a crash like any other."""
    rig.leave_simulation_on()
    eng = await _boot(rig)
    try:
        eng._running = False  # as it reads until the start's last stages
        await _the_simulator_exits(eng, rig.processes[0])
        assert not eng.simulation.active
        assert sorted(_DialRecorder.attempts[2:]) == [_real(1), _real(2)]
        assert eng.state.get("system.simulation_error").startswith("The simulator stopped")
    finally:
        eng._running = True
        await eng.stop()


@pytest.mark.asyncio
async def test_the_simulator_runs_in_a_session_of_its_own_on_posix(rig, monkeypatch):
    """So a terminal's Ctrl+C, which goes to the whole foreground group,
    reaches the server alone, and the server's stop ends the simulator."""
    spawned = []
    spawn = rig.spawn

    async def recording(*args, **kwargs):
        spawned.append(kwargs)
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(simulation.asyncio, "create_subprocess_exec", recording)
    eng = await _boot(rig)
    try:
        await eng.simulation.start()
        (kwargs,) = spawned
        if sys.platform == "win32":
            assert "start_new_session" not in kwargs
        else:
            assert kwargs["start_new_session"] is True
    finally:
        await eng.stop()


def _the_main_server_hears(sig: int, rig) -> _MainServer:
    """A stop signal, through the hook the main server is started with
    (``python -m openavc.main``), the moment it arrives: it says stopping on
    the run-state record the engine reads."""
    server = listen_sockets.hears_the_stop(_MainServer(), rig.run_state.stopping)
    server.handle_exit(sig, None)
    return server


async def _the_simulator_exits(eng, simulator: _Process) -> None:
    """A signal ends the simulator, and the watcher, on its own next look (it
    looks once a second), finds it gone and does what it does with that."""
    watcher = eng.simulation._monitor_task
    assert watcher is not None and not watcher.done(), "nothing is watching the simulator"
    simulator.receives(signal.SIGTERM)
    await asyncio.wait_for(watcher, timeout=5)


# ── What a start leaves on disk ─────────────────────────────────────────────

_LOGIN = "acme-door-code-4417"


@pytest.mark.asyncio
async def test_no_saved_login_is_left_on_disk_once_the_simulator_has_started(
    rig, tmp_path, monkeypatch,
):
    """The simulator's settings carry each simulated device's config with its
    saved logins filled in. Handed over as a file in the temp folder, they
    stayed there whenever the server ended without its own clean-up: an
    update's restart, a crash, a kill."""
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(simulation.tempfile, "tempdir", str(temp))
    rig.leave_simulation_on()
    eng = await _boot(rig, devices=(1,), password=_LOGIN)
    try:
        assert eng.simulation.active
        # Read before any stop: a hard exit runs none of the server's clean-up.
        holding = [
            path.name for path in temp.rglob("*")
            if path.is_file() and _LOGIN.encode() in path.read_bytes()
        ]
        assert holding == []
        # The simulator still got the login, on its standard input.
        (device,) = rig.processes[0].stdin.settings()["devices"]
        assert device["config"]["password"] == _LOGIN
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_launch_file_an_earlier_version_left_is_removed_at_start(
    rig, tmp_path, monkeypatch,
):
    """An earlier version left its launch file, with the logins in it, in the
    temp folder after a hard exit; the next start removes it."""
    folder = tmp_path / "temp"
    folder.mkdir()
    monkeypatch.setattr(simulation, "_leftover_launch_folder", lambda: folder)
    left = folder / "openavc_sim_k2j4x9.json"
    left.write_text(_json.dumps({"devices": [{"config": {"password": _LOGIN}}]}))
    neighbours = [folder / "openavc_sim_notes.txt", folder / "acme_sim_1.json"]
    for path in neighbours:
        path.write_text("{}")
    eng = await _boot(rig)
    try:
        assert not left.exists()
        assert [path.exists() for path in neighbours] == [True, True]
    finally:
        await eng.stop()
