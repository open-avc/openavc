"""Unsupported simulated devices must not fall back to their hardware paths."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.simulation import SimulationManager
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
