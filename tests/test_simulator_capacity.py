"""A simulated device can be given a limit on how much it takes in.

With the capacity model on, a device holds ``rx_buffer`` bytes it has not
read yet, takes one message per ``process_ms``, and loses what arrives while
the buffer is full: discarded, or (``on_overflow: silence``) the device stops
answering that connection. A driver that floods a device then fails against
the simulator the way it fails against hardware. It is off until switched on
per device, and a driver may declare the numbers (``simulator.capacity``).

Invented devices (``acme_*``) and synthetic payloads throughout.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import configurable
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.spec import platform_requirements
from openavc.simulator import api as sim_api
from openavc.simulator.capacity import DEFAULT_SETTINGS, capacity_problems
from openavc.simulator.http_simulator import HTTPSimulator
from openavc.simulator.tcp_simulator import TCPSimulator
from openavc.simulator.udp_simulator import UDPSimulator
from openavc.simulator.yaml_auto import YAMLAutoSimulator

#: How long "nothing more arrived" is waited for.
QUIET = 0.3


class _AcmeEcho(TCPSimulator):
    """Answers every line with OK and the line."""

    SIMULATOR_INFO = {
        "driver_id": "acme_echo",
        "name": "Acme Echo",
        "transport": "tcp",
        "delimiter": "\r",
        "initial_state": {},
    }

    def handle_command(self, data: bytes) -> bytes | None:
        return b"OK " + data + b"\r"


class _AcmeDeclared(_AcmeEcho):
    SIMULATOR_INFO = {
        **_AcmeEcho.SIMULATOR_INFO,
        "driver_id": "acme_declared",
        "capacity": {"rx_buffer": 16, "process_ms": 5},
    }


class _AcmeUdpEcho(UDPSimulator):
    SIMULATOR_INFO = {
        "driver_id": "acme_udp_echo",
        "name": "Acme UDP Echo",
        "transport": "udp",
        "initial_state": {},
    }

    def handle_command(self, data: bytes) -> bytes | None:
        return b"OK " + data


class _AcmeHttp(HTTPSimulator):
    SIMULATOR_INFO = {
        "driver_id": "acme_http",
        "name": "Acme HTTP",
        "transport": "http",
        "initial_state": {},
    }

    def handle_request(self, method, path, headers, body):
        return 200, {}


def _lines(count: int) -> list[bytes]:
    """``count`` lines of 8 bytes each: PING 00, PING 01, ..."""
    return [f"PING {i:02}\r".encode() for i in range(count)]


async def _replies(reader: asyncio.StreamReader) -> list[bytes]:
    out = b""
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=QUIET)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        out += chunk
    return [line for line in out.split(b"\r") if line]


async def _until(predicate, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


@pytest.fixture
async def echo():
    sim = _AcmeEcho("dev1")
    await sim.start(0)
    writers: list[asyncio.StreamWriter] = []

    async def connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_connection("127.0.0.1", sim.port)
        writers.append(writer)
        return reader, writer

    yield sim, connect
    for writer in writers:
        writer.close()
    await sim.stop()


# ── TCP ─────────────────────────────────────────────────────────────────────


async def test_a_burst_over_the_buffer_loses_bytes_and_says_how_many(echo):
    sim, connect = echo
    sim.set_capacity(enabled=True, rx_buffer=32, process_ms=20)
    reader, writer = await connect()
    writer.write(b"".join(_lines(10)))
    await writer.drain()
    # 32 bytes are four whole lines; the other 48 bytes are lost.
    assert await _replies(reader) == [b"OK " + line.rstrip(b"\r") for line in _lines(4)]
    assert sim.to_info_dict()["capacity"]["bytes_lost"] == 48


async def test_the_same_lines_paced_are_all_answered(echo):
    sim, connect = echo
    sim.set_capacity(enabled=True, rx_buffer=32, process_ms=20)
    reader, writer = await connect()
    for line in _lines(10):
        writer.write(line)
        await writer.drain()
        await asyncio.sleep(0.04)
    assert len(await _replies(reader)) == 10
    assert sim.to_info_dict()["capacity"]["bytes_lost"] == 0


async def test_the_device_takes_one_message_per_process_ms(echo):
    sim, connect = echo
    sim.set_capacity(enabled=True, rx_buffer=1024, process_ms=50)
    reader, writer = await connect()
    loop = asyncio.get_running_loop()
    start = loop.time()
    writer.write(b"".join(_lines(5)))
    await writer.drain()
    arrived: list[float] = []
    buf = b""
    while len(arrived) < 5:
        buf += await asyncio.wait_for(reader.read(4096), timeout=2.0)
        while b"\r" in buf:
            _, buf = buf.split(b"\r", 1)
            arrived.append(loop.time() - start)
    # The first is taken at once, each later one 50 ms after the one before.
    assert arrived[0] < 0.04
    assert all(b - a >= 0.045 for a, b in zip(arrived, arrived[1:]))


async def test_silence_lasts_until_the_connection_closes(echo):
    sim, connect = echo
    sim.set_capacity(enabled=True, rx_buffer=32, process_ms=20, on_overflow="silence")
    reader, writer = await connect()
    writer.write(b"".join(_lines(10)))
    await writer.drain()
    assert await _replies(reader) == []
    # Nothing more on that connection, paced or not...
    writer.write(b"PING 99\r")
    await writer.drain()
    assert await _replies(reader) == []
    # ...and the next connection is answered.
    reader2, writer2 = await connect()
    writer2.write(b"PING 42\r")
    await writer2.drain()
    assert await _replies(reader2) == [b"OK PING 42"]


async def test_switching_it_on_applies_to_a_connection_already_open(echo):
    sim, connect = echo
    reader, writer = await connect()
    writer.write(b"PING 00\r")
    await writer.drain()
    assert await _replies(reader) == [b"OK PING 00"]
    sim.set_capacity(enabled=True, rx_buffer=16, process_ms=10)
    writer.write(b"".join(_lines(6)))
    await writer.drain()
    assert len(await _replies(reader)) == 2
    assert sim.to_info_dict()["capacity"]["bytes_lost"] == 32


async def test_off_it_loses_nothing(echo):
    sim, connect = echo
    reader, writer = await connect()
    writer.write(b"".join(_lines(40)))
    await writer.drain()
    assert len(await _replies(reader)) == 40
    assert sim.to_info_dict()["capacity"]["bytes_lost"] == 0


async def test_switching_it_on_starts_the_count_again(echo):
    sim, connect = echo
    sim.set_capacity(enabled=True, rx_buffer=8, process_ms=10)
    reader, writer = await connect()
    writer.write(b"".join(_lines(3)))
    await writer.drain()
    await _replies(reader)
    assert sim.to_info_dict()["capacity"]["bytes_lost"] == 16
    sim.set_capacity(enabled=False)
    assert sim.to_info_dict()["capacity"]["bytes_lost"] == 16
    sim.set_capacity(enabled=True)
    assert sim.to_info_dict()["capacity"]["bytes_lost"] == 0


async def test_a_line_by_line_reader_reads_through_the_same_buffer():
    """readline() (a sign-in, a simulator's own reader) takes one line per
    read from the same buffer, and sees the end of the stream."""
    from openavc.simulator.capacity import ReceiveLink

    sim = _AcmeEcho("dev1")
    sim.set_capacity(enabled=True, rx_buffer=64, process_ms=0)
    raw = asyncio.StreamReader()
    raw.feed_data(b"A\nB\nC\n")
    raw.feed_eof()
    link = ReceiveLink(raw, sim._capacity, sim._first_message_len)
    assert [await link.readline() for _ in range(3)] == [b"A\n", b"B\n", b"C\n"]
    assert await link.readline() == b""
    assert link.at_eof()


# ── What it says about itself ───────────────────────────────────────────────


def test_a_declared_capacity_is_offered_and_off():
    info = _AcmeDeclared("dev1").to_info_dict()["capacity"]
    assert info == {
        "enabled": False,
        "rx_buffer": 16,
        "process_ms": 5.0,
        "on_overflow": "discard",
        "source": "driver",
        "declared": {"rx_buffer": 16, "process_ms": 5.0, "on_overflow": "discard"},
        "bytes_lost": 0,
        "kind": "stream",
    }


def test_a_device_that_declares_nothing_gets_the_simulators_own_numbers():
    info = _AcmeEcho("dev1").to_info_dict()["capacity"]
    assert info["enabled"] is False
    assert info["source"] == "simulator"
    assert info["declared"] is None
    assert (info["rx_buffer"], info["process_ms"]) == (
        DEFAULT_SETTINGS.rx_buffer, DEFAULT_SETTINGS.process_ms,
    )


def test_numbers_changed_by_hand_say_so():
    sim = _AcmeDeclared("dev1")
    assert sim.set_capacity(rx_buffer=64)["source"] == "edited"
    assert sim.set_capacity(rx_buffer=16)["source"] == "driver"


def test_a_request_response_simulator_has_no_capacity():
    sim = _AcmeHttp("dev1")
    assert sim.to_info_dict()["capacity"] is None
    with pytest.raises(ValueError):
        sim.set_capacity(enabled=True)


def test_numbers_out_of_range_are_refused():
    sim = _AcmeEcho("dev1")
    with pytest.raises(ValueError, match="rx_buffer"):
        sim.set_capacity(rx_buffer=0)
    with pytest.raises(ValueError, match="on_overflow"):
        sim.set_capacity(on_overflow="truncate")
    assert sim.to_info_dict()["capacity"]["rx_buffer"] == DEFAULT_SETTINGS.rx_buffer


def test_the_shape_rule():
    assert capacity_problems({"rx_buffer": 64, "process_ms": 20}) == []
    assert capacity_problems({"rx_buffer": 64, "process_ms": 20, "on_overflow": "silence"}) == []
    assert capacity_problems({"rx_buffer": 64}) == ["capacity.process_ms is required"]
    assert capacity_problems({"rx_buffer": True, "process_ms": 1})
    assert capacity_problems({"rx_buffer": 64, "process_ms": -1})
    assert capacity_problems({"rx_buffer": 64, "process_ms": 1, "size": 3})
    assert capacity_problems([64, 20])


# ── Datagrams ───────────────────────────────────────────────────────────────


class _Collector(asyncio.DatagramProtocol):
    def __init__(self):
        self.received: list[bytes] = []

    def datagram_received(self, data, addr):
        self.received.append(data)


@pytest.fixture
async def udp_echo():
    sim = _AcmeUdpEcho("dev1")
    await sim.start(0)
    loop = asyncio.get_running_loop()
    transport, collector = await loop.create_datagram_endpoint(
        _Collector, remote_addr=("127.0.0.1", sim.port),
    )
    yield sim, transport, collector
    transport.close()
    await sim.stop()


async def test_a_datagram_burst_loses_whole_datagrams(udp_echo):
    sim, transport, collector = udp_echo
    sim.set_capacity(enabled=True, rx_buffer=20, process_ms=20)
    for i in range(10):
        transport.sendto(f"PING {i:02}\r".encode())
    await asyncio.sleep(0.5)
    lost = sim.to_info_dict()["capacity"]["bytes_lost"]
    assert lost > 0 and lost % 8 == 0
    assert len(collector.received) + lost // 8 == 10
    # Every answer is a whole datagram that arrived.
    assert all(r.startswith(b"OK PING ") and len(r) == 11 for r in collector.received)


async def test_datagrams_paced_are_all_answered(udp_echo):
    sim, transport, collector = udp_echo
    sim.set_capacity(enabled=True, rx_buffer=20, process_ms=20)
    for i in range(10):
        transport.sendto(f"PING {i:02}\r".encode())
        await asyncio.sleep(0.04)
    await asyncio.sleep(0.2)
    assert len(collector.received) == 10
    assert sim.to_info_dict()["capacity"]["bytes_lost"] == 0


async def test_a_datagram_device_stays_silent_until_capacity_is_switched_off(udp_echo):
    sim, transport, collector = udp_echo
    sim.set_capacity(enabled=True, rx_buffer=8, process_ms=50, on_overflow="silence")
    for i in range(5):
        transport.sendto(f"PING {i:02}\r".encode())
    await asyncio.sleep(0.3)
    answered = len(collector.received)
    transport.sendto(b"PING 98\r")
    await asyncio.sleep(0.2)
    assert len(collector.received) == answered
    sim.set_capacity(enabled=False)
    transport.sendto(b"PING 99\r")
    assert await _until(lambda: b"OK PING 99\r" in collector.received, 1.0)


# ── The simulator API ───────────────────────────────────────────────────────


@pytest.fixture
def api_client(monkeypatch):
    sims = {"dev1": _AcmeDeclared("dev1"), "web1": _AcmeHttp("web1")}

    class _Manager:
        def get_instance(self, device_id):
            return sims.get(device_id)

    monkeypatch.setattr(sim_api, "_manager", _Manager())
    app = FastAPI()
    app.include_router(sim_api.router)
    return TestClient(app), sims


def test_the_route_switches_it_and_changes_its_numbers(api_client):
    client, sims = api_client
    res = client.post("/api/devices/dev1/capacity", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["capacity"]["enabled"] is True
    assert res.json()["capacity"]["rx_buffer"] == 16
    res = client.post(
        "/api/devices/dev1/capacity", json={"process_ms": 40, "on_overflow": "silence"},
    )
    assert res.json()["capacity"]["process_ms"] == 40.0
    assert res.json()["capacity"]["on_overflow"] == "silence"
    assert res.json()["capacity"]["source"] == "edited"
    assert sims["dev1"]._capacity.active.process_ms == 40.0


def test_the_route_refuses_what_it_cannot_do(api_client):
    client, _ = api_client
    assert client.post("/api/devices/nope/capacity", json={}).status_code == 404
    res = client.post("/api/devices/web1/capacity", json={"enabled": True})
    assert res.status_code == 400
    res = client.post("/api/devices/dev1/capacity", json={"rx_buffer": 0})
    assert res.status_code == 400
    assert "rx_buffer" in res.json()["detail"]


# ── The driver contract ─────────────────────────────────────────────────────


def _matrix_def(outputs: int = 20, *, capacity: dict | None = None) -> dict[str, Any]:
    """An 8-input matrix whose poll asks each output's input and mute: two
    lines per output."""
    simulator: dict[str, Any] = {"delays": {"command_response": 0.005}}
    if capacity is not None:
        simulator["capacity"] = capacity
    return {
        "id": "acme_matrix",
        "name": "Acme Matrix",
        "manufacturer": "Acme",
        "category": "switcher",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 6000, "output_count": outputs},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 6000, "label": "Port"},
            "output_count": {"type": "integer", "default": outputs, "label": "Outputs"},
        },
        "state_variables": {},
        "child_entity_types": {
            "output": {
                "label": "Output",
                "label_plural": "Outputs",
                "id_format": {"type": "integer", "min": 1, "max": 32},
                "state_variables": {
                    "input": {"type": "integer", "min": 1, "max": 8, "label": "Input"},
                    "mute": {"type": "boolean", "label": "Mute"},
                },
                "instances": {"count_from": "output_count", "label": "Output {id}"},
            },
        },
        "commands": {},
        "responses": [
            {
                "match": r"^VID (\d+) (\d+)$",
                "child_set": [{"type": "output", "id": "$1", "state": {"input": "$2"}}],
            },
            {
                "match": r"^MUTE (\d+) ([01])$",
                "child_set": [{"type": "output", "id": "$1", "state": {"mute": "$2"}}],
            },
        ],
        "polling": {
            "queries": [
                {"each_child": "output", "send": "VID? {child_id}\r", "query_for": "input"},
                {"each_child": "output", "send": "MUTE? {child_id}\r", "query_for": "mute"},
            ],
        },
        "simulator": simulator,
    }


def test_simulator_capacity_is_part_of_the_contract():
    definition = _matrix_def(capacity={"rx_buffer": 64, "process_ms": 20, "on_overflow": "silence"})
    assert validate_driver_definition(definition) == []
    assert ("simulator.capacity", "0.37.0") in platform_requirements(definition)


def test_the_published_schema_states_the_shape():
    import json
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).parents[1] / "openavc/drivers/avcdriver.schema.json").read_text("utf-8")
    )
    node = schema["$defs"]["simulatorSection"]["properties"]["capacity"]
    assert node["required"] == ["rx_buffer", "process_ms"]
    assert node["additionalProperties"] is False
    assert node["properties"]["rx_buffer"]["minimum"] == 1
    assert node["properties"]["process_ms"]["minimum"] == 0
    assert node["properties"]["on_overflow"]["enum"] == ["discard", "silence"]


@pytest.mark.parametrize("capacity", [
    {"rx_buffer": 64},
    {"rx_buffer": 0, "process_ms": 20},
    {"rx_buffer": 64, "process_ms": 20, "on_overflow": "truncate"},
    {"rx_buffer": 64, "process_ms": 20, "size": 1},
])
def test_a_malformed_declaration_is_ignored_by_the_simulator(capacity):
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_matrix_def(capacity=capacity))
    info = sim.to_info_dict()["capacity"]
    assert info["declared"] is None
    assert info["source"] == "simulator"


def test_the_simulator_validator_reports_a_malformed_declaration(tmp_path):
    import textwrap

    import yaml

    from openavc.simulator.validate import validate_python_driver, validate_yaml_driver

    good = tmp_path / "acme_matrix.avcdriver"
    good.write_text(yaml.safe_dump(_matrix_def(capacity={"rx_buffer": 64, "process_ms": 20})))
    assert not [i for i in validate_yaml_driver(good).errors if i.check == "capacity"]
    bad = tmp_path / "acme_bad.avcdriver"
    bad.write_text(yaml.safe_dump(_matrix_def(capacity={"rx_buffer": 64})))
    assert [i.message for i in validate_yaml_driver(bad).errors if i.check == "capacity"] == [
        "simulator.capacity: capacity.process_ms is required",
    ]

    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        class AcmeWidgetDriver:
            DRIVER_INFO = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp"}
    '''), encoding="utf-8")
    (tmp_path / "acme_widget_sim.py").write_text(textwrap.dedent('''
        class AcmeWidgetSimulator:
            SIMULATOR_INFO = {
                "driver_id": "acme_widget", "name": "Acme Widget Simulator",
                "transport": "tcp", "initial_state": {},
                "capacity": {"rx_buffer": 64, "process_ms": 20, "on_overflow": "wait"},
            }
    '''), encoding="utf-8")
    found = [i.message for i in validate_python_driver(driver).errors if i.check == "capacity"]
    assert found == ['SIMULATOR_INFO["capacity"]: capacity.on_overflow must be discard or silence']


def test_the_auto_simulator_takes_the_declaration():
    sim = YAMLAutoSimulator(
        "dev1", config={},
        driver_def=_matrix_def(capacity={"rx_buffer": 64, "process_ms": 20}),
    )
    info = sim.to_info_dict()["capacity"]
    assert info["declared"] == {"rx_buffer": 64, "process_ms": 20.0, "on_overflow": "discard"}
    assert info["enabled"] is False


def test_an_http_auto_simulator_has_no_capacity():
    definition = {**_matrix_def(), "transport": "http"}
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=definition)
    assert sim.to_info_dict()["capacity"] is None


# ── Against the platform ────────────────────────────────────────────────────


async def _matrix_sim(outputs: int) -> YAMLAutoSimulator:
    sim = YAMLAutoSimulator(
        "acme_matrix", config={"output_count": outputs}, driver_def=_matrix_def(outputs),
    )
    await sim.start(0)
    for n in range(1, outputs + 1):
        sim.set_state(f"output.{n}.input", (n % 8) + 1)
        sim.set_state(f"output.{n}.mute", n % 2 == 0)
    sim.set_capacity(enabled=True, rx_buffer=64, process_ms=20)
    return sim


def _matrix_driver(port: int, outputs: int):
    definition = _matrix_def(outputs)
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    state.set_event_bus(EventBus())
    config = {
        **definition["default_config"], "host": "127.0.0.1", "port": port, "poll_interval": 0,
    }
    return cls("acme_matrix", config, state, EventBus())


def _filled(drv, outputs: int) -> int:
    return sum(
        drv.get_state(f"output.{n}.input") == (n % 8) + 1
        and drv.get_state(f"output.{n}.mute") is (n % 2 == 0)
        for n in range(1, outputs + 1)
    )


async def test_a_paced_poll_fills_every_output_of_a_device_that_cannot_keep_up():
    """40 poll lines at the platform's 50 ms line gap: every output fills."""
    outputs = 20
    sim = await _matrix_sim(outputs)
    drv = _matrix_driver(sim.port, outputs)
    try:
        await drv.connect()
        await drv.poll()
        assert await _until(lambda: _filled(drv, outputs) == outputs, 2.0)
        assert sim.to_info_dict()["capacity"]["bytes_lost"] == 0
    finally:
        await drv.disconnect()
        await sim.stop()


async def test_the_same_poll_unpaced_overflows_it(monkeypatch):
    """The same 40 lines with no gap: most of them never reach the device."""
    monkeypatch.setattr(configurable, "DEFAULT_LINE_GAP_S", 0)
    outputs = 20
    sim = await _matrix_sim(outputs)
    drv = _matrix_driver(sim.port, outputs)
    try:
        await drv.connect()
        await drv.poll()
        await asyncio.sleep(1.0)
        assert _filled(drv, outputs) < outputs // 2
        assert sim.to_info_dict()["capacity"]["bytes_lost"] > 0
    finally:
        await drv.disconnect()
        await sim.stop()


@pytest.fixture
async def managed_matrix(monkeypatch):
    """The acme matrix under a real DeviceManager, polling unpaced every
    100 ms against its simulator with on_overflow: silence; retries 50 ms
    apart, and a record of every connected / disconnected event."""
    from openavc.core.device_manager import DeviceManager
    from openavc.drivers.registry import register_driver, unregister_driver

    monkeypatch.setattr(configurable, "DEFAULT_LINE_GAP_S", 0)
    monkeypatch.setattr(
        DeviceManager, "_reconnect_delay", lambda self, device_id, attempt: 0.05
    )
    outputs = 8
    definition = _matrix_def(outputs)
    definition["simulator"]["delays"]["command_response"] = 0.02
    sim = YAMLAutoSimulator(
        "acme1", config={"output_count": outputs}, driver_def=definition,
    )
    await sim.start(0)
    sim.set_state("output.1.input", 5)
    sim.set_capacity(enabled=True, rx_buffer=32, process_ms=20, on_overflow="silence")
    register_driver(create_configurable_driver_class(definition))
    state, events = StateStore(), EventBus()
    dm = DeviceManager(state, events)
    seen: list[str] = []
    events.on("device.connected.*", lambda event, payload=None: seen.append("connected"))
    events.on("device.disconnected.*", lambda event, payload=None: seen.append("disconnected"))
    connections: list[int] = []
    original = sim._handle_client

    async def counted(reader, writer):
        connections.append(1)
        await original(reader, writer)

    sim._handle_client = counted
    await sim.stop()
    await sim.start(0)
    await dm.add_device({
        "id": "acme1", "driver": definition["id"], "name": "Acme",
        "config": {
            "host": "127.0.0.1", "port": sim.port, "output_count": outputs,
            "poll_interval": 0.1, "max_missed_polls": 3,
        },
    })
    try:
        yield dm, state, sim, seen, connections
    finally:
        await dm.disconnect_all()
        unregister_driver(definition["id"])
        await sim.stop()


async def test_an_overflow_that_silences_the_device_takes_it_offline_and_holds_it(managed_matrix):
    dm, state, sim, seen, connections = managed_matrix
    # `connected` goes false at once and the reason follows the disconnect
    # event a moment later, so wait for both rather than reading the second
    # straight after the first.
    assert await _until(
        lambda: state.get("device.acme1.connected") is False
        and state.get("device.acme1.offline_reason") == "no_response",
        5.0,
    )
    first_drop = len(connections)
    # Every held reconnect floods it again, and it goes silent again.
    assert await _until(lambda: len(connections) >= first_drop + 2, 6.0)
    assert state.get("device.acme1.connected") is False
    assert seen == ["connected", "disconnected"]
    # With the limit off, the next connection is answered and announced.
    sim.set_capacity(enabled=False)
    assert await _until(
        lambda: state.get("device.acme1.connected") is True
        and state.get("device.acme1.offline_reason") is None,
        5.0,
    )
    assert seen == ["connected", "disconnected", "connected"]
    assert state.get("device.acme1.output.1.input") == 5
