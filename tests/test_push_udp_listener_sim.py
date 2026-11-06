"""The simulator half of ``push: {type: udp_listener}``.

Platform-feature tests with INVENTED devices (Acme) per the test policy. A
device of this kind is set to send each change, as one UDP packet, to the
controller's address and a port; in simulation the controller is this
computer, so every simulated device sends from 127.0.0.1. Pinned here:

- the listener tells simulated devices apart by the port each simulator sends
  from, and only while a device is connected to the simulator (a real device,
  or a real program on this computer, is still matched by address only);
- a YAML driver's auto-simulator sends its ``notifications:`` to the listener
  port, from its own port number, over UDP and TCP devices alike;
- a Python simulator sends any packet with ``send_to_listener``, and the SNMP
  base sends a v2c trap (``send_trap``) the codec reads back;
- end to end with polling off: two simulated devices on one listener port each
  update their own driver, through the redirect the simulation manager applies.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.simulation import SimulationManager
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.snmp_simulator import SNMPSimulator
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport import snmp_codec
from openavc.transport import udp_listener as ul


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def _send(port: int, data: bytes, source_port: int = 0) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
        tx.bind(("127.0.0.1", source_port))
        tx.sendto(data, ("127.0.0.1", port))


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


@pytest.fixture(autouse=True)
async def _clean_registry():
    yield
    await ul.close_all()


def _sensor_def(transport: str = "udp", **overrides) -> dict:
    """An invented box that sends a status line to a port set on it."""
    d: dict[str, Any] = {
        "id": f"acme_sensor_{transport}",
        "name": "Acme Sensor",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "author": "Test",
        "description": "Invented device that sends status packets to a port",
        "transport": transport,
        "delimiter": "\\r",
        "source_url": "https://example.com",
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
            "status_port": {"type": "integer", "default": 5001, "label": "Status Port"},
        },
        "default_config": {"host": "", "port": 5000, "status_port": 5001},
        "push": {"type": "udp_listener", "port": "{status_port}"},
        "commands": {},
        "state_variables": {
            "door": {"type": "string", "label": "Door"},
            "level": {"type": "integer", "label": "Level"},
        },
        "responses": [
            {"match": r"DOOR (\w+)", "set": {"door": "$1"}},
            {"match": r"LEVEL (\d+)", "set": {"level": "$1"}},
        ],
        "simulator": {
            "initial_state": {"door": "SHUT", "level": 0},
            "notifications": {"door": "DOOR {value}", "level": "LEVEL {value}"},
        },
    }
    d.update(overrides)
    return d


def _make_driver(definition: dict, config: dict, device_id: str):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(device_id, dict(config), state, events)


async def _simulated(definition: dict, listener_port: int, device_id: str):
    """Start a simulator for one device and connect its driver to it the way
    Start Simulation does: the manager's own redirect, then connect."""
    sim = YAMLAutoSimulator(
        device_id=device_id,
        # What the manager passes a simulator: the device config without
        # host and port.
        config={"status_port": listener_port},
        driver_def=definition,
    )
    await sim.start(0)
    drv = _make_driver(
        definition,
        {"host": "192.0.2.50", "port": 5000, "status_port": listener_port},
        device_id,
    )
    SimulationManager(engine=object())._apply_sim_redirect(drv, device_id, sim.port)
    await drv.connect()
    return sim, drv


# ===========================================================================
# The listener: a simulated device is told apart by its simulator's port
# ===========================================================================


@pytest.mark.asyncio
async def test_a_simulated_device_takes_only_what_its_own_port_sends():
    port = _free_udp_port()
    a_port, b_port = _free_udp_port(), _free_udp_port()
    got_a: list[bytes] = []
    got_b: list[bytes] = []
    await ul.subscribe(port, "127.0.0.1", lambda d, s: got_a.append(d), "a", source_port=a_port)
    await ul.subscribe(port, "127.0.0.1", lambda d, s: got_b.append(d), "b", source_port=b_port)
    _send(port, b"LEVEL 1\r", source_port=a_port)
    _send(port, b"LEVEL 2\r", source_port=b_port)
    await _wait_for(lambda: got_a and got_b)
    await asyncio.sleep(0.05)
    assert got_a == [b"LEVEL 1\r"]
    assert got_b == [b"LEVEL 2\r"]


@pytest.mark.asyncio
async def test_a_simulated_device_ignores_a_local_sender_on_another_port():
    port = _free_udp_port()
    got: list[bytes] = []
    await ul.subscribe(port, "127.0.0.1", lambda d, s: got.append(d), "sim", source_port=_free_udp_port())
    _send(port, b"LEVEL 9\r")  # an ephemeral port: some other program
    await asyncio.sleep(0.1)
    assert got == []


@pytest.mark.asyncio
async def test_a_real_local_device_still_gets_a_packet_from_any_port():
    """Without the simulator, nothing changes: a real program on this
    computer is matched by address, whatever port it sends from."""
    port = _free_udp_port()
    real: list[bytes] = []
    sim: list[bytes] = []
    await ul.subscribe(port, "127.0.0.1", lambda d, s: real.append(d), "real")
    sim_port = _free_udp_port()
    await ul.subscribe(port, "127.0.0.1", lambda d, s: sim.append(d), "sim", source_port=sim_port)
    _send(port, b"LEVEL 3\r")  # the real program, from an ephemeral port
    await _wait_for(lambda: real)
    _send(port, b"LEVEL 4\r", source_port=sim_port)  # the simulator
    await _wait_for(lambda: sim)
    await asyncio.sleep(0.05)
    # Each got its own: the simulated packet did not reach the real device.
    assert real == [b"LEVEL 3\r"]
    assert sim == [b"LEVEL 4\r"]


@pytest.mark.asyncio
async def test_the_port_match_never_reaches_past_the_address():
    port = _free_udp_port()
    got: list[bytes] = []
    source = _free_udp_port()
    await ul.subscribe(port, "192.0.2.10", lambda d, s: got.append(d), "far", source_port=source)
    _send(port, b"LEVEL 5\r", source_port=source)
    await asyncio.sleep(0.1)
    assert got == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, 65536, -1, True, "19000"])
async def test_a_source_port_outside_the_range_is_refused(bad):
    with pytest.raises(ValueError):
        await ul.subscribe(_free_udp_port(), "127.0.0.1", lambda d, s: None, "a", source_port=bad)


@pytest.mark.asyncio
async def test_the_redirect_decides_whether_the_driver_names_a_port():
    """A driver names its simulator's port only while redirected; after Stop
    Simulation puts the connection back, it is matched by address again."""
    definition = _sensor_def()
    port = _free_udp_port()
    drv = _make_driver(definition, {"host": "127.0.0.1", "port": 5000, "status_port": port}, "d1")
    await drv._start_push()
    assert drv._push_subscription.source_port is None
    await drv._stop_push()

    manager = SimulationManager(engine=object())
    manager._apply_sim_redirect(drv, "d1", 19123)
    await drv._start_push()
    assert drv._push_subscription.source_port == 19123
    await drv._stop_push()

    manager._restore_original_config(drv, manager._original_configs["d1"])
    await drv._start_push()
    assert drv._push_subscription.source_port is None
    await drv._stop_push()


# ===========================================================================
# The YAML auto-simulator sends its notifications to the listener
# ===========================================================================


def test_the_simulator_reads_the_listener_port_from_the_config():
    definition = _sensor_def()
    sim = YAMLAutoSimulator(device_id="s", config={"status_port": 6000}, driver_def=definition)
    assert sim._push_udp_listener == 6000
    # Unset in the device config: the driver's default.
    sim = YAMLAutoSimulator(device_id="s", config={}, driver_def=definition)
    assert sim._push_udp_listener == 5001
    assert sim.to_info_dict()["push_state"] is True


@pytest.mark.parametrize("value", ["", "abc", 0, 70000])
def test_a_listener_port_that_is_not_a_port_sends_nothing(value):
    sim = YAMLAutoSimulator(device_id="s", config={"status_port": value}, driver_def=_sensor_def())
    assert sim._push_udp_listener is None


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["udp", "tcp"])
async def test_a_notification_goes_to_the_listener_from_the_device_port(transport):
    listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen.bind(("127.0.0.1", 0))
    listen.setblocking(False)
    port = listen.getsockname()[1]
    sim = YAMLAutoSimulator(
        device_id="s", config={"status_port": port}, driver_def=_sensor_def(transport)
    )
    await sim.start(0)
    try:
        sim.set_state("door", "OPEN")
        loop = asyncio.get_running_loop()
        data, source = await asyncio.wait_for(loop.sock_recvfrom(listen, 1024), 2.0)
        assert data == b"DOOR OPEN\r"
        assert source == ("127.0.0.1", sim.port)
    finally:
        await sim.stop()
        listen.close()


@pytest.mark.asyncio
async def test_a_device_that_is_not_responding_sends_nothing():
    listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen.bind(("127.0.0.1", 0))
    listen.settimeout(0.2)
    port = listen.getsockname()[1]
    sim = YAMLAutoSimulator(device_id="s", config={"status_port": port}, driver_def=_sensor_def())
    await sim.start(0)
    try:
        sim.inject_error("communication_timeout")  # "Device stops responding"
        assert sim.has_error_behavior("no_response")
        sim.set_state("door", "OPEN")
        with pytest.raises(socket.timeout):
            listen.recvfrom(1024)
    finally:
        await sim.stop()
        listen.close()


# ===========================================================================
# End to end: driver and simulator, polling off
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["udp", "tcp"])
async def test_two_simulated_devices_on_one_port_each_update_their_own_driver(transport):
    definition = _sensor_def(transport)
    assert "polling" not in definition  # every change arrives as a push
    port = _free_udp_port()
    sim_a, drv_a = await _simulated(definition, port, "lobby")
    sim_b, drv_b = await _simulated(definition, port, "stage")
    try:
        assert drv_a._push_subscription._listener is drv_b._push_subscription._listener
        sim_a.set_state("door", "OPEN")
        await _wait_for(lambda: drv_a.state.get("device.lobby.door") == "OPEN")
        sim_b.set_state("level", 42)
        await _wait_for(lambda: drv_b.state.get("device.stage.level") == 42)
        await asyncio.sleep(0.1)
        assert drv_b.state.get("device.stage.door") != "OPEN"
        assert drv_a.state.get("device.lobby.level") != 42
    finally:
        await drv_a.disconnect()
        await drv_b.disconnect()
        await sim_a.stop()
        await sim_b.stop()
    assert port not in ul._registry._listeners


# --- a Python driver and a Python simulator: an SNMP trap ---

_TRAP_OID = "1.3.6.1.4.1.99999.0.1"
_OUTLET_OID = "1.3.6.1.4.1.99999.2.1.3.1"


class _AcmeOutletSim(SNMPSimulator):
    """Invented SNMP agent that sends a trap when its outlet is switched."""

    SIMULATOR_INFO = {"driver_id": "acme_outlet", "name": "Acme Outlet", "transport": "udp"}
    OIDS = {_OUTLET_OID: ("integer", 0, True)}

    def write_oid(self, oid, type_name, value):
        status = super().write_oid(oid, type_name, value)
        if status == 0 and oid == _OUTLET_OID:
            self.send_trap(
                _TRAP_OID, [(oid, "integer", value)],
                port=int(self.config.get("trap_port", 162)),
            )
        return status


class _AcmeOutlet(BaseDriver):
    """Invented Python driver that reads the trap with the codec."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_outlet",
        "name": "Acme Outlet",
        "manufacturer": "Acme",
        "category": "power",
        "transport": "udp",
        "default_config": {"host": "", "port": 161, "trap_port": 162},
        "push": {"type": "udp_listener", "port": "{trap_port}"},
        "commands": {},
        "state_variables": {"outlet": {"type": "integer", "label": "Outlet"}},
    }

    async def _handle_push_datagram(self, data: bytes, source: tuple[str, int]) -> None:
        trap = snmp_codec.parse_snmp_trap(data)
        if trap is not None and trap.trap_oid == _TRAP_OID:
            self.set_state("outlet", trap.value(_OUTLET_OID))

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.mark.asyncio
async def test_a_python_simulator_sends_a_trap_its_driver_reads():
    port = _free_udp_port()
    sim = _AcmeOutletSim(device_id="pdu", config={"trap_port": port})
    await sim.start(0)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = _AcmeOutlet("pdu", {"host": "192.0.2.60", "port": 161, "trap_port": port}, state, events)
    SimulationManager(engine=object())._apply_sim_redirect(drv, "pdu", sim.port)
    await drv._start_push()
    try:
        assert drv._push_subscription.source_port == sim.port
        # The driver switches the outlet; the agent answers and traps.
        request = snmp_codec.build_snmp_set("private", [(_OUTLET_OID, "integer", 1)], 7)
        _send(sim.port, request)
        await _wait_for(lambda: state.get("device.pdu.outlet") == 1)
    finally:
        await drv._stop_push()
        await sim.stop()


# ===========================================================================
# The trap codec
# ===========================================================================


def test_a_trap_reads_back_as_built():
    packet = snmp_codec.build_snmp_trap(
        "public", 12, 4321, _TRAP_OID,
        [(_OUTLET_OID, "integer", 1), ("1.3.6.1.4.1.99999.2.1.4.1", "string", "Rack A")],
    )
    assert packet[0] == 0x30
    trap = snmp_codec.parse_snmp_trap(packet)
    assert trap is not None
    assert (trap.community, trap.request_id, trap.uptime, trap.trap_oid) == (
        "public", 12, 4321, _TRAP_OID,
    )
    assert [(vb.oid, vb.type, vb.value) for vb in trap.varbinds] == [
        (_OUTLET_OID, "integer", 1),
        ("1.3.6.1.4.1.99999.2.1.4.1", "string", "Rack A"),
    ]
    assert trap.value(_OUTLET_OID) == 1
    assert trap.value("1.3.6.1.4.1.99999.9") is None


def test_the_trap_pdu_carries_tag_a7_and_the_two_standard_varbinds_first():
    packet = snmp_codec.build_snmp_trap("public", 1, 100, _TRAP_OID)
    assert bytes([snmp_codec.SNMP_TRAP_V2]) in packet
    # Built by hand with the codec's own encoders, the RFC 3416 order.
    expected = snmp_codec.ber_encode_sequence([
        snmp_codec.ber_encode_integer(1),
        snmp_codec.ber_encode_string("public"),
        snmp_codec.ber_encode_tagged(0xA7, [
            snmp_codec.ber_encode_integer(1),
            snmp_codec.ber_encode_integer(0),
            snmp_codec.ber_encode_integer(0),
            snmp_codec.ber_encode_sequence([
                snmp_codec.ber_encode_sequence([
                    snmp_codec.ber_encode_oid("1.3.6.1.2.1.1.3.0"),
                    snmp_codec.encode_typed_value("timeticks", 100),
                ]),
                snmp_codec.ber_encode_sequence([
                    snmp_codec.ber_encode_oid("1.3.6.1.6.3.1.1.4.1.0"),
                    snmp_codec.ber_encode_oid(_TRAP_OID),
                ]),
            ]),
        ]),
    ])
    assert packet == expected


def test_anything_but_a_v2c_trap_is_not_read_as_one():
    response = snmp_codec.build_snmp_response(5, "public", [(_OUTLET_OID, "integer", 1)])
    assert snmp_codec.parse_snmp_trap(response) is None
    assert snmp_codec.parse_snmp_trap(b"") is None
    assert snmp_codec.parse_snmp_trap(b"\x30\x03\x02\x01") is None
    trap = snmp_codec.build_snmp_trap("public", 1, 100, _TRAP_OID)
    v1 = trap.replace(b"\x02\x01\x01", b"\x02\x01\x00", 1)  # version 0 = v1
    assert snmp_codec.parse_snmp_trap(v1) is None
    # And a trap is not a response.
    assert snmp_codec.parse_snmp_message(trap) is None
