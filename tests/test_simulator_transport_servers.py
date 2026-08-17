"""Every transport a driver can declare is served by ONE server.

The YAML auto-generator used to carry its own UDP, OSC and HTTP servers
alongside ``openavc/simulator/udp_simulator.py``, ``osc_simulator.py`` and
``http_simulator.py`` — the same code twice, which drifted: the Python bases
learned optional TLS and the generated ones never did, so the HTTPS-only
devices whose drivers are YAML (the default format) could not reach their own
simulators. These tests pin the convergence from both ends: the structural
claim that there is one implementation, and a live round trip per transport
proving the generated path still answers on each.

Invented device ("acme_*") and synthetic payloads throughout: this is the
platform's simulator machinery under test, not any specific driver.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from openavc.transport.osc_codec import osc_decode_message, osc_encode_message
from openavc.simulator.datagram_server import DatagramServerMixin
from openavc.simulator.http_simulator import HTTPServerMixin, HTTPSimulator
from openavc.simulator.osc_simulator import OSCDispatchMixin, OSCSimulator
from openavc.simulator.udp_simulator import UDPSimulator
from openavc.simulator.yaml_auto import YAMLAutoSimulator

# ── Fixtures: one invented driver per transport ─────────────────────────────

_BASE = {
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "author": "Test",
    "description": "Invented device for transport tests",
    "source_url": "https://example.com",
}


def _udp_def() -> dict:
    return {
        **_BASE,
        "id": "acme_udp_amp",
        "name": "Acme UDP Amp",
        "transport": "udp",
        "delimiter": "\\r",
        "config_schema": {"host": {"type": "string", "required": True}},
        "default_config": {"host": "", "port": 5000},
        "state_variables": {
            "power": {"type": "enum", "label": "Power", "values": ["ON", "OFF"]},
        },
        "commands": {
            "power_on": {"send": "PWR ON", "label": "Power On", "sets": {"power": "ON"}},
        },
        "responses": [
            {"match": "^PWR (ON|OFF)$", "set": {"power": "$1"}},
        ],
    }


def _osc_def() -> dict:
    return {
        **_BASE,
        "id": "acme_osc_mixer",
        "name": "Acme OSC Mixer",
        "transport": "osc",
        "config_schema": {"host": {"type": "string", "required": True}},
        "default_config": {"host": "", "port": 10023},
        "state_variables": {
            "main_fader": {"type": "number", "label": "Main Fader",
                           "min": 0, "max": 1},
        },
        "commands": {},
        "responses": [
            {
                "address": "/main/fader",
                "mappings": [{"state": "main_fader", "arg": 0, "type": "float"}],
            },
        ],
    }


def _http_def(ssl: bool = False) -> dict:
    return {
        **_BASE,
        "id": "acme_http_panel",
        "name": "Acme HTTP Panel",
        "transport": "http",
        "config_schema": {"host": {"type": "string", "required": True}},
        "default_config": {"host": "", "port": 4003, "ssl": ssl},
        "state_variables": {
            "power": {"type": "boolean", "label": "Power"},
        },
        "commands": {
            "get_power": {
                "label": "Get Power",
                "method": "GET",
                "path": "/api/power",
                "query_for": "power",
            },
        },
        "responses": [],
        "simulator": {
            "command_handlers": [
                {"match": r"^GET /api/power$", "respond": '{"power": true}'},
            ],
        },
    }


def _free_port(kind: int = socket.SOCK_STREAM) -> int:
    """Ask the OS for a free port IN THE PROTOCOL THE CALLER WILL SERVE.

    TCP and UDP have independent port spaces, so a port the kernel calls free
    on one says nothing about the other. This asked UDP for every port and
    then handed some of them to the HTTP tests below, which is a TCP bind — so
    the HTTP simulator could be told to bind a TCP port that was already in
    use. It fails as `[Errno 98] address already in use`, only on a runner busy
    enough to have the collision, which is why it read as CI flake rather than
    as the wrong question being asked (it turned main red on 2026-08-16).
    """
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


# ── Structural: one implementation, not two ─────────────────────────────────


def test_generated_simulator_runs_the_same_servers_as_the_python_bases():
    """The claim this whole item is about — the generated simulator IS the
    bases, not a second copy of them."""
    assert issubclass(UDPSimulator, DatagramServerMixin)
    assert issubclass(OSCSimulator, OSCDispatchMixin)
    assert issubclass(HTTPSimulator, HTTPServerMixin)
    # And the generated one carries every one of them.
    assert issubclass(YAMLAutoSimulator, DatagramServerMixin)
    assert issubclass(YAMLAutoSimulator, OSCDispatchMixin)
    assert issubclass(YAMLAutoSimulator, HTTPServerMixin)


@pytest.mark.parametrize(
    "method",
    ["start_datagram_server", "stop_datagram_server", "send_datagram",
     "start_http_server", "stop_http_server", "push_sse_event",
     "post_http_callback", "_serve_sse"],
)
def test_transport_plumbing_is_not_redefined_by_the_generator(method):
    """Each server method resolves to the shared definition. Overriding one
    here is how the two paths diverged before — the hooks meant for the
    generator are respond_http / decode_request_path / dispatch_datagram /
    handle_message, and they are deliberately not in this list."""
    assert method not in vars(YAMLAutoSimulator)


def test_response_corruption_has_one_home():
    """``corrupt_bytes`` was defined three times, byte-identically, and
    imported across module boundaries as a private."""
    from openavc.simulator import network_conditions, osc_simulator, tcp_simulator, udp_simulator

    assert callable(network_conditions.corrupt_bytes)
    for module in (tcp_simulator, udp_simulator, osc_simulator):
        assert not hasattr(module, "_corrupt_bytes"), (
            f"{module.__name__} kept a private copy of the corruption helper"
        )


# ── Live round trip per transport ───────────────────────────────────────────


async def test_udp_driver_round_trips_through_the_shared_datagram_server():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_udp_def())
    port = _free_port(socket.SOCK_DGRAM)
    await sim.start(port)
    try:
        loop = asyncio.get_running_loop()
        recv: asyncio.Queue = asyncio.Queue()

        class _Client(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                recv.put_nowait(data)

        transport, _ = await loop.create_datagram_endpoint(
            _Client, remote_addr=("127.0.0.1", port)
        )
        try:
            transport.sendto(b"PWR ON\r")
            reply = await asyncio.wait_for(recv.get(), timeout=3.0)
        finally:
            transport.close()

        assert b"PWR ON" in reply
        assert sim.get_state("power") == "ON"
        # The datagram server remembered the sender, so an unsolicited push
        # has somewhere to go.
        assert sim._last_client_addr is not None
    finally:
        await sim.stop()


async def test_osc_driver_round_trips_through_the_shared_datagram_server():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_osc_def())
    port = _free_port(socket.SOCK_DGRAM)
    await sim.start(port)
    try:
        loop = asyncio.get_running_loop()
        recv: asyncio.Queue = asyncio.Queue()

        class _Client(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                recv.put_nowait(data)

        transport, _ = await loop.create_datagram_endpoint(
            _Client, remote_addr=("127.0.0.1", port)
        )
        try:
            transport.sendto(osc_encode_message("/main/fader", [("f", 0.5)]))
            reply = await asyncio.wait_for(recv.get(), timeout=3.0)
        finally:
            transport.close()

        address, args = osc_decode_message(reply)
        assert address == "/main/fader"
        assert args and args[0][1] == pytest.approx(0.5)
        assert sim.get_state("main_fader") == pytest.approx(0.5)
    finally:
        await sim.stop()


async def test_http_driver_round_trips_through_the_shared_web_server():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_http_def())
    port = _free_port()
    await sim.start(port)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{port}/api/power")
            missing = await client.get(f"http://127.0.0.1:{port}/api/nope")
        assert r.status_code == 200
        assert r.json() == {"power": True}
        # No handler matched — the generated simulator answers 404, the way it
        # always has.
        assert missing.status_code == 404
    finally:
        await sim.stop()


# ── The TLS gap the duplication was hiding ──────────────────────────────────


def test_https_yaml_driver_asks_its_simulator_for_tls():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_http_def(ssl=True))
    assert sim.SIMULATOR_INFO.get("tls") is True
    # Reported to the platform, which uses it to decide whether the device
    # keeps its https connection while redirected.
    assert sim.to_info_dict()["tls"] is True


def test_plain_http_yaml_driver_does_not():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_http_def(ssl=False))
    assert sim.SIMULATOR_INFO.get("tls") is not True
    assert sim.to_info_dict()["tls"] is False


def test_device_config_ssl_wins_over_the_driver_default():
    """The device's own setting is what the driver will dial, so it is what
    the simulator serves."""
    sim = YAMLAutoSimulator(
        "dev1", config={"ssl": True}, driver_def=_http_def(ssl=False)
    )
    assert sim.SIMULATOR_INFO.get("tls") is True


async def test_https_yaml_driver_is_served_over_tls():
    """The end an HTTPS-only device's driver actually sees: its own scheme,
    answered by the generated simulator."""
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_http_def(ssl=True))
    port = _free_port()
    await sim.start(port)
    try:
        # The posture such a driver runs with: TLS on, verification off.
        async with httpx.AsyncClient(verify=False) as client:
            r = await client.get(f"https://127.0.0.1:{port}/api/power")
        assert r.status_code == 200
        assert r.json() == {"power": True}
    finally:
        await sim.stop()
    # The ephemeral cert does not outlive the simulator.
    assert sim._tls_files is None


# ── Shared pipeline behaviour, on every datagram transport ──────────────────


@pytest.mark.parametrize("definition", [_udp_def(), _osc_def()])
async def test_no_response_error_mode_silences_every_datagram_transport(definition):
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=definition)
    sim._error_modes["dead"] = {"behavior": "no_response", "description": "dead"}
    port = _free_port(socket.SOCK_DGRAM)
    await sim.start(port)
    try:
        sim.inject_error("dead")
        loop = asyncio.get_running_loop()
        recv: asyncio.Queue = asyncio.Queue()

        class _Client(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                recv.put_nowait(data)

        transport, _ = await loop.create_datagram_endpoint(
            _Client, remote_addr=("127.0.0.1", port)
        )
        try:
            transport.sendto(osc_encode_message("/main/fader", [("f", 0.5)]))
            transport.sendto(b"PWR ON\r")
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(recv.get(), timeout=0.5)
        finally:
            transport.close()
        # The frame is still logged — a silent device received it, it just
        # didn't answer.
        assert any(e["direction"] == "in" for e in sim.get_protocol_log())
    finally:
        await sim.stop()


async def test_stopping_a_datagram_simulator_cancels_its_state_machine_timers():
    """Only TCP used to do this, so a stopped UDP or OSC simulator could still
    fire an auto-transition into a dead instance."""
    definition = {
        **_udp_def(),
        "simulator": {
            "state_machines": {
                "power": {
                    "states": ["off", "warming", "on"],
                    "initial": "off",
                    "transitions": [
                        {"from": "off", "trigger": "on", "to": "warming"},
                        {"from": "warming", "after_seconds": 30, "to": "on"},
                    ],
                }
            }
        },
    }
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=definition)
    port = _free_port(socket.SOCK_DGRAM)
    await sim.start(port)
    machine = sim._state_machines["power"]
    try:
        sim.transition("power", "on")
        await _wait_for(lambda: bool(machine._timer_tasks))
        armed = list(machine._timer_tasks)
    finally:
        await sim.stop()

    assert armed, "the 30 s auto-transition should have been armed"
    await asyncio.sleep(0)
    assert all(t.cancelled() for t in armed)
