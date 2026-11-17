"""What a device's bridge binding came to, recorded once and read the same by every door.

A device's connection can name a bridge (``bridge``, a project device id) and a
port on it (``bridge_port``). The resolver works out what that comes to (the
bridge's serial pass-through, IR sent through the bridge, or a connection that
cannot be completed, and why) and records it in the resolved config. The
device card, the connect door, Test Connection, a device audit and the USB
adapter follow-up all read that record, so a binding that did not resolve gets
one answer from every one of them, and nothing is dialled in its place.

Invented devices only (core-test rule): an Acme bridge with a serial
pass-through, an IR emitter, a relay and a serial port with no pass-through,
and Acme devices of three kinds bound to it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openavc.audit.origin import BRIDGED, device_target
from openavc.core import bridge_binding as binding_rule
from openavc.core.bridge_binding import bridge_binding
from openavc.core.connection_fault import INVALID_CONFIG
from openavc.core.device_config import connection_of, dialled_record, resolve_device_config
from openavc.core.device_manager import DeviceManager
from openavc.core.device_reachability import run_connection_test
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.transport import serial_transport

HOST = "127.0.0.1"
PASSTHROUGH = 4999

_BRIDGE_PORTS = [
    {"id": "serial_1", "kind": "serial", "label": "Serial 1", "passthrough_port": PASSTHROUGH},
    {"id": "ir_1", "kind": "ir", "label": "IR 1"},
    {"id": "relay_1", "kind": "relay", "label": "Relay 1"},
    {"id": "serial_bare", "kind": "serial", "label": "Serial (no pass-through)"},
]


def _driver(driver_id: str, transport: str, **extra: Any) -> type:
    class _Acme(BaseDriver):
        DRIVER_INFO: dict[str, Any] = {
            "id": driver_id,
            "name": f"Acme {driver_id}",
            "manufacturer": "Acme",
            "category": "utility",
            "transport": transport,
            "default_config": {},
            "config_schema": {},
            "commands": {},
            "state_variables": {},
            **extra,
        }

        async def send_command(self, command: str, params: dict | None = None) -> Any:
            return None

    return _Acme


_DRIVERS = (
    _driver("acme_bb_bridge", "tcp", bridge={"ports": _BRIDGE_PORTS}),
    _driver("acme_bb_serial", "serial"),
    _driver("acme_bb_tcp", "tcp"),
    _driver("acme_bb_ir", "bridge", ir_device=True),
)


@pytest.fixture(autouse=True)
def drivers():
    for cls in _DRIVERS:
        register_driver(cls)
    yield
    for cls in _DRIVERS:
        unregister_driver(cls.DRIVER_INFO["id"])


# The device kinds bound to the bridge, each with the port of its own kind.
KINDS = {
    "serial": ("acme_bb_serial", "serial_1"),
    "tcp": ("acme_bb_tcp", "serial_1"),
    "ir": ("acme_bb_ir", "ir_1"),
}


def _project(*devices: DeviceConfig, connections: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(devices=list(devices), connections=connections or {})


def _bound(kind: str, broken: str) -> tuple[SimpleNamespace, DeviceConfig, str]:
    """A project with the Rack Bridge and the Screen bound to one of its ports,
    the binding broken the way ``broken`` names. Returns the project, the
    Screen, and the name a sentence calls the bridge by."""
    driver, port = KINDS[kind]
    screen = DeviceConfig(id="screen", driver=driver, name="Screen", config={})
    rack = DeviceConfig(id="rack", driver="acme_bb_bridge", name="Rack Bridge", config={})
    binding: dict[str, Any] = {"bridge": "rack", "bridge_port": port}
    if kind == "serial":
        # A port left behind from before the device was put on the bridge.
        binding["port"] = "/dev/ttyUSB0"
    connections = {"rack": {"host": "192.0.2.40"}, "screen": binding}
    devices = [rack, screen]
    label = "Rack Bridge"
    if broken == binding_rule.BRIDGE_MISSING:
        binding["bridge"] = "ghost"
        devices = [screen]
        label = "ghost"
    elif broken == binding_rule.BRIDGE_DRIVER_MISSING:
        rack.driver = "acme_bb_not_installed"
    elif broken == binding_rule.PORT_UNKNOWN:
        binding["bridge_port"] = "serial_7"
    elif broken == binding_rule.NO_HOST:
        connections["rack"] = {}
    elif broken == binding_rule.NO_PORT:
        binding["bridge_port"] = ""
    elif broken == binding_rule.NO_ROUTE:
        binding["bridge_port"] = "relay_1"
    return _project(*devices, connections=connections), screen, label


# The pass-through needs the bridge's address; an IR port is reached through the
# bridge's own connection, so an IR device does not.
BROKEN = [
    (kind, broken)
    for kind in KINDS
    for broken in (
        binding_rule.BRIDGE_MISSING,
        binding_rule.BRIDGE_DRIVER_MISSING,
        binding_rule.PORT_UNKNOWN,
        binding_rule.NO_HOST,
        binding_rule.NO_PORT,
        binding_rule.NO_ROUTE,
    )
    if not (kind == "ir" and broken == binding_rule.NO_HOST)
]

SENTENCES = {
    binding_rule.BRIDGE_MISSING: (
        "This device connects through the bridge 'ghost', which is not in the "
        "project. In Edit Device, choose another bridge."
    ),
    binding_rule.BRIDGE_DRIVER_MISSING: (
        "The driver for the bridge 'Rack Bridge' is not installed. Install it "
        "from the bridge's device page."
    ),
    binding_rule.PORT_UNKNOWN: (
        "The bridge 'Rack Bridge' has no port 'serial_7'. In Edit Device, choose "
        "the port on the bridge that the device is wired to."
    ),
    binding_rule.NO_HOST: (
        "The bridge 'Rack Bridge' has no IP address. Enter one in the bridge's "
        "Edit Device."
    ),
    binding_rule.NO_PORT: (
        "No bridge port is chosen for this device. In Edit Device, choose the "
        "port on the bridge that the device is wired to."
    ),
    binding_rule.NO_ROUTE: (
        "The port 'relay_1' on the bridge 'Rack Bridge' cannot carry this "
        "device's connection. In Edit Device, choose another port on the bridge."
    ),
}


# --- the record ---------------------------------------------------------------


@pytest.mark.parametrize("kind,broken", BROKEN)
def test_a_binding_that_did_not_resolve_is_recorded_with_why(kind, broken):
    project, screen, _label = _bound(kind, broken)
    binding = bridge_binding(resolve_device_config(screen, project)["config"])
    assert binding.route == broken
    assert binding.unresolved
    assert not binding.through


def test_a_serial_pass_through_is_recorded():
    project, screen, _label = _bound("serial", "")
    cfg = resolve_device_config(screen, project)["config"]
    binding = bridge_binding(cfg)
    assert binding.route == binding_rule.PASSTHROUGH
    assert (binding.bridge, binding.port) == ("rack", "serial_1")
    assert (cfg["transport"], cfg["host"], cfg["port"]) == ("tcp", "192.0.2.40", PASSTHROUGH)


def test_an_ir_port_is_recorded_as_sending_through_the_bridge():
    project, screen, _label = _bound("ir", "")
    binding = bridge_binding(resolve_device_config(screen, project)["config"])
    assert binding.route == binding_rule.EMITTER
    assert binding.emits


def test_an_ir_device_with_no_bridge_chosen_says_so():
    screen = DeviceConfig(id="screen", driver="acme_bb_ir", name="Screen", config={})
    cfg = resolve_device_config(screen, _project(screen))["config"]
    binding = bridge_binding(cfg)
    assert binding.route == binding_rule.NO_BRIDGE
    assert binding.problem() == (
        "No bridge is chosen for this device. In Edit Device, choose the bridge "
        "and the port the device is wired to."
    )


def test_a_device_with_no_binding_records_none():
    screen = DeviceConfig(id="screen", driver="acme_bb_tcp", name="Screen", config={})
    project = _project(screen, connections={"screen": {"host": "192.0.2.9"}})
    cfg = resolve_device_config(screen, project)["config"]
    assert binding_rule.ROUTE_KEY not in cfg
    assert not bridge_binding(cfg).bound


def test_an_outcome_saved_into_the_project_is_never_believed():
    """The resolver is the record's only writer: one that found its way into a
    saved project is worked out again, never read."""
    screen = DeviceConfig(id="screen", driver="acme_bb_serial", name="Screen", config={})
    project = _project(screen, connections={"screen": {
        "port": "COM3", binding_rule.ROUTE_KEY: binding_rule.PASSTHROUGH,
    }})
    cfg = resolve_device_config(screen, project)["config"]
    assert binding_rule.ROUTE_KEY not in cfg
    assert not bridge_binding(cfg).bound


# --- one answer from every door -------------------------------------------------


@pytest.mark.parametrize("kind,broken", BROKEN)
def test_the_connection_names_the_bridge_and_no_address(kind, broken):
    """Not a direct serial device on a port nobody chose, and not bridged at
    an address of its own: set to a bridge, connecting nowhere."""
    project, screen, _label = _bound(kind, broken)
    conn = connection_of(resolve_device_config(screen, project))
    assert conn.bridge == ("ghost" if broken == binding_rule.BRIDGE_MISSING else "rack")
    assert conn.host == ""
    assert conn.port is None


@pytest.mark.parametrize("kind,broken", BROKEN)
def test_an_audit_refuses_it_as_bridged(kind, broken):
    project, screen, label = _bound(kind, broken)
    target = device_target(project, "screen")
    assert target["auditable"] is False
    assert target["reason"] == BRIDGED.format(name="Screen", bridge=label)


@pytest.mark.parametrize("broken", [b for k, b in BROKEN if k == "serial"])
def test_the_usb_adapter_is_not_followed(monkeypatch, broken):
    """A device set to go through a bridge is not on this computer's serial
    port, whatever its stored adapter says."""
    project, screen, _label = _bound("serial", broken)
    project.connections["screen"]["usb_serial"] = "ACME0001"
    monkeypatch.setattr(serial_transport, "_attached_ports", lambda: [
        {"device": "/dev/ttyUSB1", "serial_number": "ACME0001"},
    ])
    cfg = resolve_device_config(screen, project)["config"]
    assert cfg.get("port") == "/dev/ttyUSB0"
    assert serial_transport.require_usb_adapter(cfg, "serial") is cfg


@pytest.mark.parametrize("kind,broken", BROKEN)
async def test_the_card_and_test_connection_say_the_same_sentence(kind, broken):
    """The connect door refuses it before anything is dialled, the card says
    why, and Test Connection says the same words."""
    project, screen, label = _bound(kind, broken)
    manager = DeviceManager(StateStore(), EventBus())
    if broken != binding_rule.BRIDGE_MISSING:
        manager.state.set("device.rack.name", label)
    try:
        await manager.add_device(resolve_device_config(screen, project))
        driver = manager.get_driver("screen")
        assert driver is not None
        assert driver.transport is None, "an unresolved binding was dialled"
        assert manager.state.get("device.screen.connected") is False
        assert manager.state.get("device.screen.offline_reason") == INVALID_CONFIG
        card = manager.state.get("device.screen.offline_detail")
        assert card == SENTENCES[broken]
        # Retrying cannot complete the connection, so it stops at once.
        assert manager.state.get("device.screen.reconnect_failed") is True

        result = await run_connection_test(screen, SimpleNamespace(project=project))
        assert result["success"] is False
        assert result["error"] == card
    finally:
        await manager.disconnect_all()


async def test_a_simulated_device_is_not_refused_for_its_binding():
    """A running simulation points the device at the simulator, where its
    bridge binding no longer applies (``dialled_record``): it connects."""
    accepted: list[int] = []

    async def handle(reader, writer):
        accepted.append(1)
        await reader.read()
        writer.close()

    server = await asyncio.start_server(handle, HOST, 0)
    sim_port = server.sockets[0].getsockname()[1]
    project, screen, _label = _bound("tcp", binding_rule.BRIDGE_MISSING)
    manager = DeviceManager(StateStore(), EventBus())
    try:
        resolved = resolve_device_config(screen, project)
        await manager.add_device(resolved, defer_connect=True)
        redirect = {"host": HOST, "port": sim_port, "transport": "tcp"}
        manager.simulation_redirect = lambda asked: redirect if asked == "screen" else None
        # What SimulationManager._apply_sim_redirect does to the live driver.
        manager.get_driver("screen").config.update(redirect)
        assert not bridge_binding(dialled_record(resolved, redirect)["config"]).bound
        await manager.bring_up()
        assert manager.state.get("device.screen.connected") is True
        assert manager.state.get("device.screen.offline_reason") in (None, "")
    finally:
        await manager.disconnect_all()
        server.close()
        await server.wait_closed()


# --- Test Connection of an IR device tests its bridge where it connects now --------


async def test_an_ir_device_tests_its_simulated_bridge_at_the_simulator():
    """The bridge an IR device sends through is simulated and connected to the
    simulator: Test Connection answers from that connection, names the
    simulator's address and the bridge, and opens no second connection."""
    accepted: list[int] = []

    async def handle(reader, writer):
        accepted.append(1)
        await reader.read()
        writer.close()

    server = await asyncio.start_server(handle, HOST, 0)
    sim_port = server.sockets[0].getsockname()[1]
    rack = DeviceConfig(
        id="rack", driver="acme_bb_bridge", name="Rack Bridge",
        config={"host": HOST, "port": sim_port},
    )
    screen = DeviceConfig(id="screen", driver="acme_bb_ir", name="Screen", config={})
    manager = DeviceManager(StateStore(), EventBus())
    try:
        await manager.add_device(rack.model_dump())
        assert manager.state.get("device.rack.connected") is True
        # The project names the equipment; the simulation redirects the bridge.
        manager._device_configs["rack"]["config"] = {"host": "10.77.0.5", "port": 4998}
        manager.simulation_redirect = (
            lambda asked: {"host": HOST, "port": sim_port} if asked == "rack" else None
        )
        equipment = DeviceConfig(
            id="rack", driver="acme_bb_bridge", name="Rack Bridge",
            config={"host": "10.77.0.5", "port": 4998},
        )
        engine = SimpleNamespace(
            project=_project(equipment, screen, connections={
                "screen": {"bridge": "rack", "bridge_port": "ir_1"},
            }),
            devices=manager,
        )

        result = await run_connection_test(screen, engine)
        await asyncio.sleep(0.2)
    finally:
        await manager.disconnect_all()
        server.close()
        await server.wait_closed()

    assert accepted == [1], "the test opened a second connection to the simulator"
    assert result["success"] is True, result
    assert result["in_use_by"] == "Rack Bridge"
    assert result["connection"]["port"] == sim_port
    assert result["connection"]["bridge"] == "rack"
    assert result["simulated"] is True
    assert result["note"] == (
        "Rack Bridge is simulated and connected to the simulator now, not to the "
        "equipment at 10.77.0.5:4998. The test did not open a second connection to it."
    )


async def test_an_ir_device_tests_its_bridge_where_its_driver_dials_now():
    """The bridge runs and is not connected: the test dials where its driver
    dials now (the simulator), not the address the project sets."""
    accepted: list[int] = []

    async def handle(reader, writer):
        accepted.append(1)
        writer.close()

    server = await asyncio.start_server(handle, HOST, 0)
    sim_port = server.sockets[0].getsockname()[1]
    equipment = DeviceConfig(
        id="rack", driver="acme_bb_bridge", name="Rack Bridge",
        config={"host": "10.77.0.5", "port": 4998},
    )
    screen = DeviceConfig(id="screen", driver="acme_bb_ir", name="Screen", config={})
    manager = DeviceManager(StateStore(), EventBus())
    try:
        await manager.add_device(equipment.model_dump(), defer_connect=True)
        manager.simulation_redirect = (
            lambda asked: {"host": HOST, "port": sim_port} if asked == "rack" else None
        )
        engine = SimpleNamespace(
            project=_project(equipment, screen, connections={
                "screen": {"bridge": "rack", "bridge_port": "ir_1"},
            }),
            devices=manager,
        )
        result = await run_connection_test(screen, engine)
    finally:
        await manager.disconnect_all()
        server.close()
        await server.wait_closed()

    assert result["success"] is True, result
    assert accepted == [1]
    assert result["connection"]["port"] == sim_port
    assert result["connection"]["bridge"] == "rack"
    assert result["simulated"] is True
    assert "10.77.0.5:4998" in result["note"]
