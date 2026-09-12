"""Every simulator base reports the port it actually bound.

``TCPSimulator.start`` has always read an ephemeral port back off the listening
socket, so ``start(0)`` gave a usable ``sim.port``. The datagram and HTTP bases
set ``self._port = port`` *before* binding and never looked again, so
``start(0)`` left ``sim.port`` answering ``0`` — not a port, and nothing a
caller can connect to.

That turned "pick a free port" into something only a TCP simulator could do, so
every harness that starts simulators picked from a hardcoded pool instead. The
driver repo's connect-lifecycle sweep picked 19000 upward, which is the range
the product itself hands to simulated devices, so the sweep collided with any
running OpenAVC instance that had simulation on and two drivers failed on every
run.
"""

from __future__ import annotations

import socket

import pytest

from openavc.simulator.http_simulator import HTTPSimulator
from openavc.simulator.mqtt_simulator import MQTTSimulator
from openavc.simulator.osc_simulator import OSCSimulator
from openavc.simulator.snmp_simulator import SNMPSimulator
from openavc.simulator.tcp_simulator import TCPSimulator
from openavc.simulator.udp_simulator import UDPSimulator
from openavc.simulator.websocket_simulator import WebSocketSimulator


def _simulator(base, transport):
    class _Sim(base):
        SIMULATOR_INFO = {
            "driver_id": "port_probe",
            "name": "Port probe",
            "category": "utility",
            "transport": transport,
            "default_port": 1,
        }
        OIDS = {"1.3.6.1.2.1.1.1.0": ("string", "port probe")}

        def handle_command(self, data):
            return None

        def handle_request(self, *args, **kwargs):
            return 200, {}, b"ok"

        def handle_message(self, *args, **kwargs):
            return None

    return _Sim(device_id="port-probe")


def _free_port(transport: str) -> int:
    """A port the OS says is free, from a throwaway socket.

    Deliberately not a port a simulator has just released: macOS holds a
    datagram endpoint's port briefly after close, so reusing one is a race
    this test would lose on its own machine.
    """
    kind = (
        socket.SOCK_DGRAM if transport in ("udp", "osc", "snmp")
        else socket.SOCK_STREAM
    )
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# Every base that binds a socket. The list is the point: the three that were
# broken were found one at a time by whichever driver happened to fail, so
# this pins all of them at once.
BASES = [
    pytest.param(TCPSimulator, "tcp", id="tcp"),
    pytest.param(UDPSimulator, "udp", id="udp"),
    pytest.param(OSCSimulator, "osc", id="osc"),
    pytest.param(HTTPSimulator, "http", id="http"),
    pytest.param(SNMPSimulator, "snmp", id="snmp"),
    pytest.param(MQTTSimulator, "mqtt", id="mqtt"),
    pytest.param(WebSocketSimulator, "websocket", id="websocket"),
]


def test_every_socket_binding_simulator_base_is_covered():
    """A base added later must be added here, not discovered by a driver
    failing on a busy port."""
    import inspect

    from openavc.simulator import base as base_module

    covered = {p.values[0] for p in BASES}
    missing = []
    for module_name in (
        "tcp_simulator", "udp_simulator", "osc_simulator", "http_simulator",
        "snmp_simulator", "mqtt_simulator", "websocket_simulator",
        "datagram_server",
    ):
        module = __import__(
            f"openavc.simulator.{module_name}", fromlist=["x"]
        )
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue
            if not issubclass(obj, base_module.BaseSimulator):
                continue
            if obj in covered or obj is base_module.BaseSimulator:
                continue
            missing.append(f"{module.__name__}.{obj.__name__}")
    assert not missing, (
        f"simulator base(s) not covered by the port contract: {missing}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("base,transport", BASES)
async def test_port_zero_reports_the_port_actually_bound(base, transport):
    sim = _simulator(base, transport)
    await sim.start(0)
    try:
        assert isinstance(sim.port, int)
        assert sim.port > 1024, (
            f"{transport}: start(0) left port={sim.port!r}; a caller has "
            f"nothing to connect to"
        )
    finally:
        await sim.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("base,transport", BASES)
async def test_an_explicit_port_is_reported_unchanged(base, transport):
    """The read-back must not disturb the ordinary case, which is every
    simulated device the product starts."""
    chosen = _free_port(transport)
    sim = _simulator(base, transport)
    await sim.start(chosen)
    try:
        assert sim.port == chosen
    finally:
        await sim.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("base,transport", BASES)
async def test_two_simulators_asking_for_zero_get_different_ports(
    base, transport,
):
    """The point of the exercise: a harness can start several without
    coordinating a pool, and without colliding with the product's own
    19000-19499 simulated-device range."""
    first = _simulator(base, transport)
    second = _simulator(base, transport)
    await first.start(0)
    await second.start(0)
    try:
        assert first.port != second.port
    finally:
        await first.stop()
        await second.stop()
