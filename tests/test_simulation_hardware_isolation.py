"""Unsupported simulated devices must not fall back to their hardware paths."""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest
from openavc.api.error_messages import friendly_error
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.simulation import _PORT_QUERY_ATTEMPTS, SimulationManager
from openavc.core.state_store import StateStore


class Hardware:
    DRIVER_INFO = {
        "id": "acme_widget", "transport": "tcp",
        "commands": {"wake": {"params": {}, "available_offline": True}},
        "device_settings": {"level": {"type": "integer"}},
    }

    def __init__(self, state, ident):
        self.config = {"host": "192.0.2.1", "port": 9000}
        self.state, self.ident = state, ident
        self.connects = self.disconnects = self.writes = 0
        self.udp_redirect = None
        self.stop_polling = AsyncMock()
        state.set(f"device.{ident}.connected", True)

    def get_state(self, name):
        return self.state.get(f"device.{self.ident}.{name}")

    async def connect(self):
        self.connects += 1
        self.state.set(f"device.{self.ident}.connected", True)

    async def disconnect(self):
        self.disconnects += 1
        self.state.set(f"device.{self.ident}.connected", False)

    async def send_command(self, *args):
        self.writes += 1

    async def set_device_setting(self, *args):
        self.writes += 1


@pytest.fixture
def rig():
    state = StateStore()
    manager = DeviceManager(state, EventBus())
    for ident in ["missing", "ordinary", "outside"]:
        manager._devices[ident] = Hardware(state, ident)
        manager._device_configs[ident] = {"id": ident, "driver": "acme_widget", "config": {}}
    simulator = SimulationManager(SimpleNamespace(devices=manager, state=state))
    simulator._active = True
    simulator._sim_payloads = {"missing": {}, "ordinary": {}}
    simulator._sim_ports = {"ordinary": 19000}
    manager.unsimulated_driver = simulator._unsimulated_driver
    return manager, simulator


@pytest.mark.asyncio
async def test_start_isolates_missing_simulator_and_stop_restores_it(rig):
    manager, simulator = rig
    missing = manager._devices["missing"]
    await simulator._redirect_connections()
    assert missing.disconnects == 1 and missing.connects == 0
    assert manager.state.get("device.missing.offline_reason") == "no_simulator"
    assert manager.state.get("device.missing.connected") is False
    assert not manager._reconnect_tasks
    assert manager._devices["ordinary"].config["port"] == 19000
    assert manager._devices["ordinary"].connects == 1
    assert manager._devices["outside"].disconnects == 0
    await simulator._restore_connections()
    assert missing.connects == 1 and missing.get_state("connected") is True
    assert manager._devices["ordinary"].config["port"] == 9000
    assert manager._devices["outside"].connects == 0


@pytest.mark.asyncio
async def test_already_connected_and_offline_commands_cannot_reach_hardware(rig):
    manager, _ = rig
    missing = manager._devices["missing"]
    for connected in [True, False]:
        manager.state.set("device.missing.connected", connected)
        with pytest.raises(ConnectionError, match="has no simulator"):
            await manager.send_command("missing", "wake")
        with pytest.raises(ConnectionError, match="has no simulator"):
            await manager.set_device_setting("missing", "level", 4)
    assert missing.writes == 0
    await manager.send_command("outside", "wake")
    assert manager._devices["outside"].writes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["reconnect_device", "reconnect_in_place", "resume_device", "_bring_up_device"])
async def test_connection_entry_points_do_not_dial_missing_simulator(rig, method):
    manager, _ = rig
    missing = manager._devices["missing"]
    manager._prepare_bridge_for = AsyncMock()
    manager._schedule_web_ui_probe = lambda ident: None
    if method == "reconnect_in_place":
        with pytest.raises(ConnectionError, match="has no simulator"):
            await getattr(manager, method)("missing")
    else:
        await getattr(manager, method)("missing")
    assert missing.connects == 0
    manager._prepare_bridge_for.assert_not_awaited()
    assert not manager._reconnect_tasks


@pytest.mark.asyncio
async def test_bulk_connect_and_background_retry_do_not_dial(rig):
    manager, _ = rig
    missing = manager._devices["missing"]
    manager.state.set("device.missing.connected", False)
    manager._deferred_connects.add("missing")
    await manager.bring_up()
    assert manager.state.get("device.missing.offline_reason") == "no_simulator"
    manager._probe_failure = AsyncMock(return_value=None)
    await manager._reconnect_loop("missing", immediate=True)
    manager._probe_failure.assert_not_awaited()
    assert missing.connects == 0


@pytest.mark.asyncio
async def test_queued_settings_are_retained_without_writes(rig):
    manager, _ = rig
    manager._device_configs["missing"]["pending_settings"] = {"level": 4}
    await manager._apply_pending_settings("missing")
    assert manager._devices["missing"].writes == 0
    assert manager._device_configs["missing"]["pending_settings"] == {"level": 4}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [400, 404, 500, "timeout", "no_port"])
@pytest.mark.parametrize("deferred", [False, True])
async def test_failed_simulator_add_cannot_connect_new_hardware(rig, outcome, deferred):
    manager, simulator = rig
    hardware = Hardware(manager.state, "new")
    manager._devices["new"] = hardware
    manager._device_configs["new"] = {"id": "new", "driver": "acme_widget", "config": {}}
    if deferred:
        manager._deferred_connects.add("new")
        manager.state.set("device.new.connected", False)
    response = SimpleNamespace(status=200 if outcome == "no_port" else outcome,
                               text=AsyncMock(return_value="No simulator available"),
                               json=AsyncMock(return_value={"port": 0}))
    post = AsyncMock(side_effect=TimeoutError("simulator unavailable")) if outcome == "timeout" else AsyncMock(return_value=response)
    simulator._adopt_existing_sim = AsyncMock(return_value=False)
    await simulator._sync_add_device(SimpleNamespace(post=post), "http://127.0.0.1:19500", "new")
    assert simulator._unsimulated_driver("new") == "acme_widget"
    assert hardware.get_state("connected") is False
    await manager._bring_up_device("new")
    with pytest.raises(ConnectionError, match="has no simulator"):
        await manager.send_command("new", "wake")
    assert hardware.connects == 0 and hardware.writes == 0
    assert not manager._reconnect_tasks


# What the integrator is told. The device is refused on purpose; the card, a
# command and the device page's next step say why and what to do about it.


@pytest.mark.asyncio
async def test_the_card_names_the_driver_and_how_to_give_it_a_simulator(rig):
    manager, simulator = rig
    await simulator._redirect_connections()
    detail = manager.state.get("device.missing.offline_detail")
    assert "'acme_widget'" in detail
    assert "_sim.py" in detail and "stop simulation" in detail.lower()


def test_product_hardware_is_only_told_to_stop_simulation(rig):
    manager, _ = rig
    manager.installed_equipment = SimpleNamespace(owns=lambda ident: ident == "missing")
    manager._set_offline_reason("missing", None, exc=ConnectionRefusedError())
    detail = manager.state.get("device.missing.offline_detail")
    assert detail == "This device has no simulator. Stop simulation to control it."


@pytest.mark.asyncio
async def test_a_command_is_refused_in_the_cards_words(rig):
    manager, simulator = rig
    await simulator._redirect_connections()
    card = manager.state.get("device.missing.offline_detail")
    with pytest.raises(ConnectionError) as refused:
        await manager.send_command("missing", "wake")
    assert friendly_error(refused.value, device="Missing Widget") == card

    app = rest_app()  # importing main sets its own engine, so import it first
    rest.set_engine(SimpleNamespace(devices=manager, state=manager.state))
    try:
        resp = TestClient(app).post("/api/devices/missing/command", json={"command": "wake"})
    finally:
        rest.set_engine(None)
    assert resp.status_code == 503
    assert resp.json()["detail"] == card


def rest_app():
    from openavc.main import app

    return app


# Starting a run where the simulator runs none of the selected devices: its
# port list comes back empty, which is an answer, not a failed query.


class _Pipe:
    async def readline(self):
        return b""


@pytest.fixture
def start_run(rig, monkeypatch):
    import openavc.core.simulation as simulation
    from tests.test_simulation_sync import _FakeResp, _install_fake_aiohttp

    manager, _ = rig
    monkeypatch.setattr(simulation, "_choose_ui_port", lambda preferred: preferred)
    monkeypatch.setattr(simulation, "_PORT_QUERY_RETRY_S", 0)
    process = SimpleNamespace(stdout=_Pipe(), stderr=_Pipe(), returncode=None)
    monkeypatch.setattr(simulation.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    started = []

    async def start(device_ids, status, devices=()):
        engine = SimpleNamespace(devices=manager, state=manager.state, project=object())
        simulator = SimulationManager(engine)
        simulator._await_simulator_ready = AsyncMock()
        calls = _install_fake_aiohttp(
            monkeypatch, lambda m, u, j: _FakeResp(status, json_data={"devices": list(devices)}))
        started.append(simulator)
        await simulator._do_start(device_ids)
        return simulator, calls

    yield start
    for simulator in started:
        simulator._monitor_task.cancel()
        os.unlink(simulator._config_path)
    simulation.set_running_ui_port(None)


@pytest.mark.asyncio
async def test_a_run_that_simulates_nothing_points_nothing_at_a_port(rig, start_run):
    manager, _ = rig
    missing = manager._devices["missing"]
    simulator, _ = await start_run(["missing"], 200)
    assert simulator._sim_ports == {}
    assert missing.config["port"] == 9000
    assert missing.connects == 0
    assert manager.state.get("device.missing.offline_reason") == "no_simulator"


@pytest.mark.asyncio
async def test_a_simulator_that_never_answers_still_gets_sequential_ports(rig, start_run):
    manager, _ = rig
    simulator, calls = await start_run(["ordinary"], 503)
    assert len(calls) == _PORT_QUERY_ATTEMPTS
    assert simulator._sim_ports["ordinary"] == manager._devices["ordinary"].config["port"] != 9000


@pytest.mark.asyncio
async def test_no_web_page_probe_while_simulation_holds_a_device(rig, monkeypatch):
    # Connected to the simulator a device's host is 127.0.0.1, where the probe
    # finds whatever this computer serves: OpenAVC itself on 8080.
    manager, simulator = rig
    manager.redirected_to_simulator = simulator.is_redirected  # as the engine wires it
    probed = []

    async def probe(host):
        probed.append(host)
        return f"http://{host}:8080"

    monkeypatch.setattr("openavc.core.web_ui_probe.probe_web_ui", probe)

    async def settled():
        await asyncio.gather(*list(manager._web_ui_probe_tasks.values()))

    await simulator._redirect_connections()
    for ident in manager._devices:
        manager._schedule_web_ui_probe(ident)  # what each connect asks
    await settled()
    assert probed == ["192.0.2.1"], "only the device simulation leaves alone"
    assert manager._detected_web_ui_urls == {"outside": "http://192.0.2.1:8080"}

    # Handed back, each is probed at its own address, connected or not.
    await simulator._restore_connections()
    await settled()
    assert probed == ["192.0.2.1"] * 3
    assert manager._detected_web_ui_urls == {
        ident: "http://192.0.2.1:8080" for ident in ("missing", "ordinary", "outside")
    }


def test_the_engine_tells_its_devices_which_are_simulated(tmp_path):
    from openavc.core.engine import Engine

    path = tmp_path / "project.avc"
    path.write_text('{"openavc_version": "0.14.0", "project": {"id": "t", "name": "T"}}')
    engine = Engine(str(path))
    assert engine.devices.redirected_to_simulator == engine.simulation.is_redirected
