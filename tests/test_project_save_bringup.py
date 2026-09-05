"""A project save persists and answers; the fleet comes up behind it.

Pins the split introduced for the save that blocked for the whole fleet:

- ``PUT /api/project`` (``Engine.apply_project``) returns without waiting for
  a single device to be dialed -- the seam that mattered, because a client or
  proxy timeout under that window reports FAILURE for a save that is already
  on disk
- a driver gets its OWN config dict, so a runtime write into it (the
  simulation redirect, an inbound-push listener port) is not read by the next
  reconcile as a config edit that re-adds the device
- the simulation redirect lands BEFORE the first connect, so an added device
  dials the simulator on its first attempt instead of failing against its
  real address and being fixed up afterwards
- the deferred bring-up keeps bridges ahead of their dependents, drops a
  device the next save removed, never runs two passes at once, and never
  loses a round
"""

import asyncio
import json

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, load_project
from openavc.core.simulation import SimulationManager
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver


# ── Drivers under our control ───────────────────────────────────────────────

class _SlowConnectDriver(BaseDriver):
    """Connects only when the test says so, and remembers what it dialed."""

    DRIVER_INFO = {
        "id": "acme_slow_connect",
        "name": "Acme Slow Connect",
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

    #: Set by the test; every instance waits on it before connecting.
    gate: asyncio.Event | None = None
    #: (device_id, host, port) per connect attempt, across all instances.
    attempts: list[tuple[str, str, int]] = []

    async def connect(self) -> None:
        type(self).attempts.append(
            (self.device_id, self.config.get("host"), self.config.get("port"))
        )
        gate = type(self).gate
        if gate is not None:
            await gate.wait()
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="test")

    async def disconnect(self) -> None:
        self._connected = False
        self.state.set(f"device.{self.device_id}.connected", False, source="test")

    async def send_command(self, command, params=None):
        return None


class _BridgeDriver(_SlowConnectDriver):
    DRIVER_INFO = dict(
        _SlowConnectDriver.DRIVER_INFO,
        id="acme_bringup_bridge",
        name="Acme Bring-up Bridge",
        category="utility",
        bridge={"ports": [
            {"id": "serial:1", "kind": "serial", "passthrough_port": 4001,
             "label": "Serial Port 1"},
        ]},
    )
    is_bridge = True

    async def prepare_bridge_port(self, port_id, config):
        return None


@pytest.fixture
def slow_driver():
    _SlowConnectDriver.gate = None
    _SlowConnectDriver.attempts = []
    register_driver(_SlowConnectDriver)
    register_driver(_BridgeDriver)
    try:
        yield _SlowConnectDriver
    finally:
        unregister_driver("acme_slow_connect")
        unregister_driver("acme_bringup_bridge")
        _SlowConnectDriver.gate = None
        _SlowConnectDriver.attempts = []


# ── Project fixture ─────────────────────────────────────────────────────────

def _project_dict(devices=None, connections=None):
    return {
        "openavc_version": "0.8.0",
        "project": {"id": "p", "name": "P"},
        "variables": [],
        "macros": [],
        "devices": devices or [],
        "device_groups": [],
        "connections": connections or {},
        "scripts": [],
        "plugins": {},
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main",
             "snap": {"enabled": True, "x": 8.3333, "y": 12.5},
             "elements": [],
             "layouts": [{"id": "landscape", "orientation": "landscape",
                          "primary": True, "inherits": None,
                          "placements": {}, "hidden": []}]},
        ]},
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


def _dev(i, name=None, driver="acme_slow_connect"):
    return {
        "id": f"d{i}",
        "name": name or f"Device {i}",
        "driver": driver,
        "enabled": True,
        "config": {"host": f"10.0.0.{i}", "port": 5000},
    }


def _engine(tmp_path, devices=None, connections=None) -> Engine:
    path = tmp_path / "project.avc"
    path.write_text(
        json.dumps(_project_dict(devices, connections)), encoding="utf-8"
    )
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    return eng


async def _teardown(eng: Engine) -> None:
    eng._bringup_requested = False
    task = eng._bringup_task
    if task is not None:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    for did in list(eng.devices._devices):
        await eng.devices._cancel_reconnect(did)
    await eng.devices.disconnect_all()


# ── The seam: the save answers before anything is dialed ────────────────────

@pytest.mark.asyncio
async def test_a_save_answers_while_its_devices_are_still_connecting(
    tmp_path, slow_driver
):
    """The measured failure: a save that adds a device held the request for as
    long as that device's first connect took. The bytes were already on disk,
    so a client timeout under that window reported failure for a save that had
    succeeded."""
    eng = _engine(tmp_path)
    slow_driver.gate = asyncio.Event()  # nothing connects until we say so
    try:
        new = ProjectConfig(**_project_dict([_dev(1)]))
        # Would never return on the old path: the connect is inside the save.
        await asyncio.wait_for(eng.apply_project(new), timeout=5)

        assert eng.devices.get_driver("d1") is not None, "device is registered"
        assert eng.state.get("device.d1.connected") is False
        assert eng.devices.is_connect_deferred("d1")

        slow_driver.gate.set()
        await asyncio.wait_for(eng.wait_for_device_bringup(), timeout=5)
        assert eng.state.get("device.d1.connected") is True
        assert not eng.devices.is_connect_deferred("d1")
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_the_bringup_really_connects_every_device_it_deferred(
    tmp_path, slow_driver
):
    """Guard the guard: deferring is only a fix if the devices come up. A
    build that simply never dialed anything would satisfy the test above."""
    eng = _engine(tmp_path)
    try:
        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(
                **_project_dict([_dev(i) for i in range(1, 6)])
            )),
            timeout=5,
        )
        await asyncio.wait_for(eng.wait_for_device_bringup(), timeout=10)
        for i in range(1, 6):
            assert eng.state.get(f"device.d{i}.connected") is True
        assert eng.devices._deferred_connects == set()
    finally:
        await _teardown(eng)


# ── The driver's config is its own ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_driver_writing_its_own_config_does_not_rewrite_the_project_one(
    tmp_path, slow_driver
):
    """A live driver writes into ``self.config`` -- the simulation redirect
    swaps host/port, a push driver records the listener port it bound. Those
    are runtime facts, not config edits, and must not reach the dict the
    reconcile compares against the project."""
    eng = _engine(tmp_path, devices=[_dev(1)])
    try:
        await eng._sync_devices()
        driver = eng.devices.get_driver("d1")
        stored = eng.devices.get_device_config("d1")["config"]
        assert driver.config is not stored

        driver.config["host"] = "127.0.0.1"
        driver.config["port"] = 19001
        driver.config["listener_port"] = 41234

        assert stored["host"] == "10.0.0.1"
        assert stored["port"] == 5000
        assert "listener_port" not in stored
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_a_redirected_fleet_re_adds_only_the_device_that_changed(
    tmp_path, slow_driver
):
    """The rename that cost the fleet: with the redirect written into the
    shared config dict, EVERY simulated device compared as changed, so a
    one-device rename removed and re-added all of them."""
    eng = _engine(tmp_path, devices=[_dev(i) for i in range(1, 6)])
    try:
        await eng._sync_devices()
        # Exactly what SimulationManager._apply_sim_redirect does.
        for i in range(1, 6):
            drv = eng.devices.get_driver(f"d{i}")
            drv.config["host"] = "127.0.0.1"
            drv.config["port"] = 19000 + i

        re_added: list[str] = []
        original = eng.devices.update_device

        async def spy(device_id, cfg, **kwargs):
            re_added.append(device_id)
            return await original(device_id, cfg, **kwargs)

        eng.devices.update_device = spy
        renamed = [_dev(i) for i in range(1, 6)]
        renamed[0]["name"] = "Renamed"
        eng.project = ProjectConfig(**_project_dict(renamed))
        await eng._sync_devices()

        assert re_added == ["d1"]
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_a_setup_action_delta_moves_both_copies(tmp_path, slow_driver):
    """Two copies means every writer has to move both. A setup action writes a
    config delta straight into the project mid-wizard; if the stored copy is
    left behind, the reconcile it triggers reads the skew as an edit and tears
    the device down under the running handler."""
    eng = _engine(tmp_path, devices=[_dev(1)])
    try:
        await eng._sync_devices()
        eng.devices.merge_live_config("d1", {"port": 9999, "provisioned": "yes"})

        assert eng.devices.get_driver("d1").config["port"] == 9999
        assert eng.devices.get_device_config("d1")["config"]["port"] == 9999
        assert eng.devices.get_device_config("d1")["config"]["provisioned"] == "yes"
    finally:
        await _teardown(eng)


# ── Simulation: the redirect lands before the first connect ─────────────────

class _FakeResp:
    def __init__(self, status, json_data=None):
        self.status = status
        self._json = json_data or {}

    async def json(self):
        return self._json

    async def text(self):
        return ""


class _FakeSession:
    """One session for the whole sync, as the real code now uses."""

    def __init__(self, ports, started):
        self._ports = ports
        self._started = started

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=None):
        device_id = url.rsplit("/", 2)[-2]
        self._started.append(device_id)
        # Yield, so a serial implementation and a concurrent one differ in the
        # ORDER the starts interleave rather than only in wall clock.
        await asyncio.sleep(0)
        return _FakeResp(200, {"port": self._ports[device_id]})


@pytest.mark.asyncio
async def test_an_added_device_dials_the_simulator_on_its_first_attempt(
    tmp_path, slow_driver, monkeypatch
):
    """With simulation on, every device added by a save used to attempt its
    REAL address, fail, and only then be redirected -- 102 devices, 102
    warning lines, and a flap each. The redirect now runs before the connect
    because both live in the bring-up, in that order."""
    import aiohttp

    eng = _engine(tmp_path, devices=[_dev(1)])
    try:
        await eng._sync_devices()
        await eng.wait_for_device_bringup()

        sim = eng.simulation
        sim._active = True
        sim._sim_ui_url = "http://localhost:19500"
        sim._sim_ports = {"d1": 19001}
        sim._process = type("P", (), {"returncode": None})()
        eng.devices.get_driver("d1").config.update(
            {"host": "127.0.0.1", "port": 19001}
        )
        started: list[str] = []
        monkeypatch.setattr(
            aiohttp, "ClientSession",
            lambda: _FakeSession({"d2": 19002, "d3": 19003}, started),
        )

        slow_driver.attempts.clear()
        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(
                **_project_dict([_dev(1), _dev(2), _dev(3)])
            )),
            timeout=5,
        )
        await asyncio.wait_for(eng.wait_for_device_bringup(), timeout=10)

        assert sorted(started) == ["d2", "d3"]
        # One attempt each, and each one straight at its simulator.
        assert sorted(slow_driver.attempts) == [
            ("d2", "127.0.0.1", 19002),
            ("d3", "127.0.0.1", 19003),
        ]
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_the_simulator_starts_run_concurrently(tmp_path, slow_driver,
                                                     monkeypatch):
    """Each start is an HTTP round trip with a 10s timeout. Serially they are
    what made adding 102 devices a 113-second save."""
    import aiohttp

    eng = _engine(tmp_path)
    try:
        sim = eng.simulation
        sim._active = True
        sim._sim_ui_url = "http://localhost:19500"
        sim._process = type("P", (), {"returncode": None})()

        in_flight = {"now": 0, "peak": 0}

        class _CountingSession(_FakeSession):
            async def post(self, url, json=None, timeout=None):
                in_flight["now"] += 1
                in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
                try:
                    return await super().post(url, json, timeout)
                finally:
                    in_flight["now"] -= 1

        ports = {f"d{i}": 19000 + i for i in range(1, 7)}
        monkeypatch.setattr(
            aiohttp, "ClientSession", lambda: _CountingSession(ports, [])
        )

        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(
                **_project_dict([_dev(i) for i in range(1, 7)])
            )),
            timeout=5,
        )
        await asyncio.wait_for(eng.wait_for_device_bringup(), timeout=10)

        assert in_flight["peak"] == 6
    finally:
        await _teardown(eng)


# ── Bring-up bookkeeping ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_bringup_connects_bridges_before_their_dependents(
    tmp_path, slow_driver
):
    """A bridge-bound device's port prep needs its bridge live, so deferring
    the connects must not lose the ordering ``_sync_devices`` used to give."""
    devices = [
        _dev(1, name="Bridge", driver="acme_bringup_bridge"),
        _dev(2, name="Downstream"),
    ]
    devices[1]["config"] = {"bridge": "d1", "bridge_port": "serial:1",
                            "baudrate": 9600}
    eng = _engine(tmp_path)
    try:
        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(**_project_dict(devices))),
            timeout=5,
        )
        await asyncio.wait_for(eng.wait_for_device_bringup(), timeout=10)
        order = [did for did, _host, _port in slow_driver.attempts]
        assert order.index("d1") < order.index("d2")
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_a_device_removed_before_its_round_starts_is_never_dialed(
    tmp_path, slow_driver
):
    eng = _engine(tmp_path)
    try:
        await eng.devices.add_device(_dev(1), defer_connect=True)
        await eng.devices.add_device(_dev(2), defer_connect=True)
        await eng.devices.remove_device("d2")
        assert not eng.devices.is_connect_deferred("d2")

        await eng.devices.bring_up()
        assert {did for did, _h, _p in slow_driver.attempts} == {"d1"}
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_a_device_removed_mid_connect_does_not_come_up_behind_the_save(
    tmp_path, slow_driver
):
    """The removal can land while the connect is already in flight — the
    bring-up runs outside the reconcile lock. What must not happen is the
    device ending up online, or its transport being left open."""
    eng = _engine(tmp_path)
    slow_driver.gate = asyncio.Event()
    try:
        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(**_project_dict([_dev(1), _dev(2)]))),
            timeout=5,
        )
        # Second save drops d2 while the first round is still gated.
        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(**_project_dict([_dev(1)]))),
            timeout=5,
        )
        slow_driver.gate.set()
        await asyncio.wait_for(eng.wait_for_device_bringup(), timeout=10)

        assert eng.devices.get_driver("d2") is None
        assert eng.state.get("device.d2.connected") is None
        assert eng.state.get("device.d1.connected") is True
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_a_save_landing_mid_bringup_is_neither_doubled_nor_lost(
    tmp_path, slow_driver
):
    """One worker at a time, and a request made while it is busy is picked up
    on the next round rather than starting a second pass over the fleet."""
    eng = _engine(tmp_path)
    slow_driver.gate = asyncio.Event()
    try:
        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(**_project_dict([_dev(1)]))),
            timeout=5,
        )
        first_worker = eng._bringup_task
        assert first_worker is not None and not first_worker.done()

        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(**_project_dict([_dev(1), _dev(2)]))),
            timeout=5,
        )
        # Same worker, not a second concurrent one.
        assert eng._bringup_task is first_worker
        assert eng._bringup_requested is True

        slow_driver.gate.set()
        await asyncio.wait_for(eng.wait_for_device_bringup(), timeout=10)

        # d2 was registered while the worker was busy and still came up.
        assert eng.state.get("device.d2.connected") is True
        assert eng._bringup_task is None
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_a_connect_that_lands_after_the_device_was_replaced_is_discarded(
    tmp_path, slow_driver
):
    """The bring-up runs outside the reconcile lock, so a save can replace a
    driver instance while its connect is in flight. Whatever that connect
    opened belongs to nobody."""
    eng = _engine(tmp_path, devices=[_dev(1)])
    try:
        await eng._sync_devices()
        stale = eng.devices.get_driver("d1")
        # Replace the instance the way a rename does.
        await eng.devices.update_device("d1", {
            **_dev(1, name="Renamed"),
            "config": {"host": "10.0.0.1", "port": 5000},
        })
        replacement = eng.devices.get_driver("d1")
        assert replacement is not stale

        stale._connected = True
        assert await eng.devices._discard_if_superseded("d1", stale) is False
        assert stale._connected is False
        # The stale disconnect wrote "offline" under an id it no longer owns;
        # the key is put back to what the LIVE instance says.
        assert replacement._connected is True
        assert eng.state.get("device.d1.connected") is True
        assert await eng.devices._discard_if_superseded("d1", replacement) is True
    finally:
        await _teardown(eng)


@pytest.mark.asyncio
async def test_stopping_the_engine_stops_the_bringup(tmp_path, slow_driver):
    eng = _engine(tmp_path)
    slow_driver.gate = asyncio.Event()
    try:
        await asyncio.wait_for(
            eng.apply_project(ProjectConfig(**_project_dict([_dev(1)]))),
            timeout=5,
        )
        assert eng._bringup_task is not None
        await asyncio.wait_for(eng.stop(), timeout=10)
        assert eng._bringup_task is None
    finally:
        slow_driver.gate.set()
        await _teardown(eng)


# ── Simulation start / stop no longer walk the fleet one device at a time ───

@pytest.mark.asyncio
async def test_the_simulation_redirect_reconnects_devices_concurrently():
    """POST /api/simulation/start spent about a second per device inside
    _redirect_connections; 106 devices meant a 104-second request."""
    order: list[str] = []

    class _DM:
        def __init__(self):
            self._devices = {
                f"d{i}": type("D", (), {"config": {"host": "10.0.0.1",
                                                   "port": 5000},
                                        "DRIVER_INFO": {}})()
                for i in range(1, 6)
            }

        async def reconnect_device(self, device_id):
            order.append(f"start:{device_id}")
            await asyncio.sleep(0)
            order.append(f"done:{device_id}")

        def is_paused(self, device_id):
            return False

        def is_connect_deferred(self, device_id):
            return False

    mgr = SimulationManager(engine=type("E", (), {"devices": _DM()})())
    mgr._sim_ports = {f"d{i}": 19000 + i for i in range(1, 6)}
    await mgr._redirect_connections()

    # Every reconnect starts before any of them finishes.
    assert order[:5] == [f"start:d{i}" for i in range(1, 6)]
