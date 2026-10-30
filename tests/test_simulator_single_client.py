"""A simulated device can serve one control connection at a time.

``single_client`` says what the device does when another connection arrives:
``newest`` gives it to the new connection and stops answering the one already
open without closing it, ``oldest`` keeps the first and holds the new one open
unanswered, ``reject`` closes the new one at once. A driver may declare it
(``simulator.single_client``); it can be changed per device. Then anything
that connects to the device (a scan, a connection test, a second controller)
can take it from the driver in the simulator the way it does on the device.

Invented devices (``acme_*``) and synthetic payloads throughout.
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.spec import platform_requirements
from openavc.simulator import api as sim_api
from openavc.simulator.http_simulator import HTTPSimulator
from openavc.simulator.tcp_simulator import TCPSimulator
from openavc.simulator.yaml_auto import YAMLAutoSimulator

#: How long "nothing arrived" is waited for.
QUIET = 0.3

HOST = "127.0.0.1"


class _AcmeEcho(TCPSimulator):
    """Greets each connection with HELLO and answers every line with OK and
    the line."""

    SIMULATOR_INFO = {
        "driver_id": "acme_echo",
        "name": "Acme Echo",
        "transport": "tcp",
        "delimiter": "\r",
        "initial_state": {},
    }

    async def on_client_connected(self, client_id: str) -> bytes | None:
        return b"HELLO\r"

    def handle_command(self, data: bytes) -> bytes | None:
        return b"OK " + data + b"\r"


class _AcmeNewest(_AcmeEcho):
    SIMULATOR_INFO = {**_AcmeEcho.SIMULATOR_INFO, "driver_id": "acme_newest", "single_client": "newest"}


class _AcmeHttp(HTTPSimulator):
    SIMULATOR_INFO = {
        "driver_id": "acme_http",
        "name": "Acme HTTP",
        "transport": "http",
        "initial_state": {},
    }

    def handle_request(self, method, path, headers, body):
        return 200, {}


class _Client:
    """One connection to a simulated device."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    async def line(self) -> bytes | None:
        """The next line from the device, or None when nothing arrives."""
        try:
            return await asyncio.wait_for(self.reader.readuntil(b"\r"), QUIET)
        except asyncio.TimeoutError:
            return None

    async def ask(self, text: str) -> bytes | None:
        self.writer.write(text.encode() + b"\r")
        await self.writer.drain()
        return await self.line()

    async def closed_by_device(self) -> bool:
        """True when the device closed this connection; False when it is
        still open and simply quiet."""
        try:
            return await asyncio.wait_for(self.reader.read(1), QUIET) == b""
        except asyncio.TimeoutError:
            return False


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
    clients: list[_Client] = []

    async def connect(*, greeted: bool = True) -> _Client:
        client = _Client(*await asyncio.open_connection(HOST, sim.port))
        clients.append(client)
        if greeted:
            assert await client.line() == b"HELLO\r"
        # The device has accepted it (and judged it) before the test goes on.
        await asyncio.sleep(0.05)
        return client

    yield sim, connect
    for client in clients:
        client.writer.close()
    await sim.stop()


def _counts(sim) -> tuple[int, int]:
    info = sim.to_info_dict()["single_client"]
    return info["open"], info["ignored"]


# ── The three modes, on two real connections ────────────────────────────────

async def test_newest_a_new_connection_takes_over_and_the_first_is_not_closed(echo):
    sim, connect = echo
    sim.set_single_client("newest")
    first = await connect()
    assert await first.ask("A") == b"OK A\r"

    second = await connect()
    assert await second.ask("B") == b"OK B\r"
    assert await first.ask("C") is None
    assert not await first.closed_by_device()
    assert _counts(sim) == (2, 1)

    # It stays unanswered after the connection that took over leaves; the
    # next new connection is served.
    second.writer.close()
    assert await _until(lambda: _counts(sim) == (1, 1), 2.0)
    assert await first.ask("D") is None
    third = await connect()
    assert await third.ask("E") == b"OK E\r"


async def test_oldest_a_new_connection_is_held_open_and_never_answered(echo):
    sim, connect = echo
    sim.set_single_client("oldest")
    first = await connect()

    second = await connect(greeted=False)
    assert await second.line() is None, "a held connection was greeted"
    assert await second.ask("B") is None
    assert not await second.closed_by_device()
    assert await first.ask("A") == b"OK A\r"
    assert _counts(sim) == (2, 1)

    # The first leaving does not hand the device to the held one; the next
    # new connection is served.
    first.writer.close()
    assert await _until(lambda: _counts(sim) == (1, 1), 2.0)
    assert await second.ask("C") is None
    third = await connect()
    assert await third.ask("D") == b"OK D\r"


async def test_reject_a_new_connection_is_closed_at_once(echo):
    sim, connect = echo
    sim.set_single_client("reject")
    first = await connect()

    second = await connect(greeted=False)
    assert await second.closed_by_device()
    assert await first.ask("A") == b"OK A\r"
    info = sim.to_info_dict()["single_client"]
    assert (info["open"], info["ignored"], info["refused"]) == (1, 0, 1)

    first.writer.close()
    assert await _until(lambda: _counts(sim) == (0, 0), 2.0)
    third = await connect()
    assert await third.ask("B") == b"OK B\r"


async def test_off_every_connection_is_answered(echo):
    sim, connect = echo
    first = await connect()
    second = await connect()
    assert await first.ask("A") == b"OK A\r"
    assert await second.ask("B") == b"OK B\r"
    info = sim.to_info_dict()["single_client"]
    assert info == {
        "mode": None, "declared": None, "source": "simulator",
        "open": 2, "ignored": 0, "refused": 0,
    }


async def test_a_message_the_device_sends_unasked_reaches_only_the_served_connection(echo):
    sim, connect = echo
    sim.set_single_client("newest")
    first = await connect()
    second = await connect()
    await sim.push(b"NOTE\r")
    assert await second.line() == b"NOTE\r"
    assert await first.line() is None


async def test_a_change_judges_the_connections_already_open(echo):
    sim, connect = echo
    first = await connect()
    second = await connect()
    info = sim.set_single_client("newest")
    assert (info["mode"], info["source"], info["ignored"]) == ("newest", "edited", 1)
    assert await first.ask("A") is None
    assert await second.ask("B") == b"OK B\r"

    # Switched off, the one already unanswered stays so until it reconnects.
    sim.set_single_client(None)
    assert await first.ask("C") is None
    third = await connect()
    assert await third.ask("D") == b"OK D\r"
    assert await second.ask("E") == b"OK E\r"


@pytest.mark.parametrize("mode", ["oldest", "reject"])
async def test_the_other_modes_keep_the_first_of_the_connections_already_open(echo, mode):
    """A change closes nothing: reject acts only on a connection arriving."""
    sim, connect = echo
    first = await connect()
    second = await connect()
    sim.set_single_client(mode)
    assert await first.ask("A") == b"OK A\r"
    assert await second.ask("B") is None
    assert not await second.closed_by_device()


async def test_the_simulator_ui_hears_every_change(echo):
    sim, connect = echo
    seen: list[dict] = []
    sim.add_change_listener(
        lambda kind, data: seen.append(data) if kind == "single_client" else None
    )
    sim.set_single_client("newest")
    await connect()
    await connect()
    assert seen[0]["mode"] == "newest"
    assert seen[-1]["device_id"] == "dev1"
    assert (seen[-1]["open"], seen[-1]["ignored"]) == (2, 1)


# ── Declared by the driver ──────────────────────────────────────────────────

def test_a_declared_mode_is_in_force_from_the_start():
    sim = _AcmeNewest("dev1")
    info = sim.to_info_dict()["single_client"]
    assert (info["mode"], info["declared"], info["source"]) == ("newest", "newest", "driver")
    info = sim.set_single_client(None)
    assert (info["mode"], info["source"]) == (None, "edited")


@pytest.mark.parametrize("value", ["sometimes", True, 1])
def test_a_malformed_declaration_is_ignored(value):
    class _Odd(_AcmeEcho):
        SIMULATOR_INFO = {**_AcmeEcho.SIMULATOR_INFO, "single_client": value}

    info = _Odd("dev1").to_info_dict()["single_client"]
    assert (info["mode"], info["declared"], info["source"]) == (None, None, "simulator")


def _widget_def(transport: str = "tcp", single_client: str | None = None) -> dict[str, Any]:
    """A display whose poll asks its input every cycle."""
    simulator: dict[str, Any] = {"delays": {"command_response": 0.005}}
    if single_client is not None:
        simulator["single_client"] = single_client
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "display",
        "version": "1.0.0",
        "transport": transport,
        "delimiter": "\r",
        "default_config": {"host": "", "port": 6000},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 6000, "label": "Port"},
        },
        "state_variables": {
            "input": {"type": "integer", "label": "Input", "min": 1, "max": 8},
        },
        "commands": {
            "select_input": {
                "send": "INP {input}\r", "label": "Select Input",
                "params": {"input": {"type": "integer", "min": 1, "max": 8, "required": True}},
            },
        },
        "responses": [{"match": "^INP (\\d+)$", "set": {"input": "$1"}}],
        "polling": {"queries": [{"send": "INP?\r", "query_for": "input"}]},
        "simulator": simulator,
    }


def test_the_auto_simulator_takes_the_declaration():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_widget_def(single_client="oldest"))
    info = sim.to_info_dict()["single_client"]
    assert (info["mode"], info["source"]) == ("oldest", "driver")


@pytest.mark.parametrize("transport", ["udp", "http", "serial"])
def test_a_device_without_connections_to_choose_between_has_no_setting(transport):
    sim = YAMLAutoSimulator(
        "dev1", config={}, driver_def=_widget_def(transport, single_client="newest"),
    )
    assert sim.to_info_dict()["single_client"] is None
    assert sim.single_client_mode is None
    with pytest.raises(ValueError, match="only a TCP device"):
        sim.set_single_client("newest")


# ── The simulator API ───────────────────────────────────────────────────────

@pytest.fixture
def api_client(monkeypatch):
    sims = {"dev1": _AcmeNewest("dev1"), "web1": _AcmeHttp("web1")}

    class _Manager:
        def get_instance(self, device_id):
            return sims.get(device_id)

    monkeypatch.setattr(sim_api, "_manager", _Manager())
    app = FastAPI()
    app.include_router(sim_api.router)
    return TestClient(app), sims


def test_the_route_changes_it(api_client):
    client, sims = api_client
    res = client.post("/api/devices/dev1/single-client", json={"mode": "reject"})
    assert res.status_code == 200
    assert res.json()["single_client"]["mode"] == "reject"
    assert res.json()["single_client"]["source"] == "edited"
    res = client.post("/api/devices/dev1/single-client", json={"mode": None})
    assert res.json()["single_client"]["mode"] is None
    assert sims["dev1"].single_client_mode is None
    assert sims["dev1"].to_info_dict()["single_client"]["declared"] == "newest"


def test_the_route_refuses_what_it_cannot_do(api_client):
    client, _ = api_client
    assert client.post("/api/devices/nope/single-client", json={"mode": None}).status_code == 404
    res = client.post("/api/devices/web1/single-client", json={"mode": "newest"})
    assert res.status_code == 400
    assert "only a TCP device" in res.json()["detail"]
    res = client.post("/api/devices/dev1/single-client", json={"mode": "latest"})
    assert res.status_code == 400
    assert res.json()["detail"] == "single_client must be newest, oldest or reject"
    assert client.post("/api/devices/dev1/single-client", json={}).status_code == 422


# ── The driver contract ─────────────────────────────────────────────────────

def test_single_client_is_part_of_the_contract_and_raises_no_floor():
    definition = _widget_def(single_client="newest")
    assert validate_driver_definition(definition, strict=True) == []
    assert not [field for field, _ in platform_requirements(definition) if "single_client" in field]


def test_the_published_schema_states_the_modes():
    import json

    schema = json.loads(
        (Path(__file__).parents[1] / "openavc/drivers/avcdriver.schema.json").read_text("utf-8")
    )
    node = schema["$defs"]["simulatorSection"]["properties"]["single_client"]
    assert node["enum"] == ["newest", "oldest", "reject"]
    assert "Takes effect on platform 0.38.0" in node["description"]


def test_the_simulator_validator_reports_a_declaration_it_would_ignore(tmp_path):
    from openavc.simulator.validate import validate_python_driver, validate_yaml_driver

    def found(result, kind: str) -> list[str]:
        return [i.message for i in getattr(result, kind) if i.check == "single_client"]

    good = tmp_path / "acme_widget.avcdriver"
    good.write_text(yaml.safe_dump(_widget_def(single_client="newest")))
    result = validate_yaml_driver(good)
    assert found(result, "errors") == [] and found(result, "warnings") == []

    serial = tmp_path / "acme_serial.avcdriver"
    serial.write_text(yaml.safe_dump(_widget_def("serial", single_client="newest")))
    assert found(validate_yaml_driver(serial), "warnings") == [
        "simulator.single_client applies to a TCP device; a serial device answers its one connection",
    ]

    driver = tmp_path / "acme_panel.py"
    driver.write_text(textwrap.dedent('''
        class AcmePanelDriver:
            DRIVER_INFO = {"id": "acme_panel", "name": "Acme Panel", "transport": "tcp"}
    '''), encoding="utf-8")
    (tmp_path / "acme_panel_sim.py").write_text(textwrap.dedent('''
        class AcmePanelSimulator:
            SIMULATOR_INFO = {
                "driver_id": "acme_panel", "name": "Acme Panel Simulator",
                "transport": "tcp", "initial_state": {}, "single_client": "latest",
            }
    '''), encoding="utf-8")
    assert found(validate_python_driver(driver), "errors") == [
        'SIMULATOR_INFO["single_client"]: single_client must be newest, oldest or reject',
    ]


# ── Against the platform ────────────────────────────────────────────────────

@pytest.fixture
async def managed_widget(monkeypatch):
    """The acme widget under a real DeviceManager, polling every 100 ms
    against its simulator in newest mode; retries 50 ms apart, and a record
    of every connected / disconnected event and every connection the
    simulator accepts."""
    from openavc.core.device_manager import DeviceManager
    from openavc.drivers.registry import register_driver, unregister_driver

    monkeypatch.setattr(
        DeviceManager, "_reconnect_delay", lambda self, device_id, attempt: 0.05
    )
    definition = _widget_def(single_client="newest")
    sim = YAMLAutoSimulator("acme1", config={}, driver_def=definition)
    sim.set_state("input", 3)
    connections: list[int] = []
    original = sim._handle_client

    async def counted(reader, writer):
        connections.append(1)
        await original(reader, writer)

    sim._handle_client = counted
    await sim.start(0)
    register_driver(create_configurable_driver_class(definition))
    state, events = StateStore(), EventBus()
    dm = DeviceManager(state, events)
    seen: list[str] = []
    events.on("device.connected.*", lambda event, payload=None: seen.append("connected"))
    events.on("device.disconnected.*", lambda event, payload=None: seen.append("disconnected"))
    await dm.add_device({
        "id": "acme1", "driver": definition["id"], "name": "Acme",
        "config": {
            "host": HOST, "port": sim.port, "poll_interval": 0.1, "max_missed_polls": 3,
        },
    })
    try:
        yield dm, state, sim, seen, connections
    finally:
        await dm.disconnect_all()
        unregister_driver(definition["id"])
        await sim.stop()


async def test_a_second_connection_takes_a_newest_device_from_its_driver_until_it_reconnects(
    managed_widget,
):
    dm, state, sim, seen, connections = managed_widget
    assert await _until(lambda: state.get("device.acme1.input") == 3, 3.0)
    assert seen == ["connected"]
    assert len(connections) == 1

    # Something else connects and stays connected: the driver hears nothing
    # more, goes offline for it, and its reconnect takes the device back.
    other = _Client(*await asyncio.open_connection(HOST, sim.port))
    try:
        assert await _until(
            lambda: state.get("device.acme1.connected") is False
            and state.get("device.acme1.offline_reason") == "no_response",
            5.0,
        )
        assert await _until(
            lambda: state.get("device.acme1.connected") is True
            and state.get("device.acme1.offline_reason") is None,
            5.0,
        )
        assert seen == ["connected", "disconnected", "connected"]
        assert len(connections) == 3
        # The other connection is now the one the device ignores.
        assert await other.ask("INP?") is None
        await dm.send_command("acme1", "select_input", {"input": 5})
        assert await _until(lambda: sim.get_state("input") == 5, 2.0)
    finally:
        other.writer.close()


async def test_test_connection_does_not_take_a_newest_device_from_its_driver():
    """A connected generic TCP device whose simulated display gives the port
    to the newest connection: the device page's Test Connection answers from
    the driver's connection and opens none of its own. A connect of its own,
    which is what the test used to do, takes the display from the driver."""
    from openavc.core.device_manager import DeviceManager
    from openavc.core.device_reachability import run_connection_test
    from openavc.core.project_loader import DeviceConfig
    from openavc.drivers.driver_loader import load_builtin_drivers

    load_builtin_drivers()
    definition = yaml.safe_load(
        (Path(__file__).parents[1] / "openavc/drivers/definitions/generic_tcp.avcdriver")
        .read_text("utf-8")
    )
    sim = YAMLAutoSimulator("lobby_display", config={}, driver_def=definition)
    sim.set_single_client("newest")
    await sim.start(0)
    manager = DeviceManager(StateStore(), EventBus())
    device = DeviceConfig(
        id="lobby_display", driver="generic_tcp", name="Lobby Display",
        config={"host": HOST, "port": sim.port},
    )
    try:
        await manager.add_device(device.model_dump())
        assert manager.state.get("device.lobby_display.connected") is True
        assert await _until(lambda: _counts(sim) == (1, 0), 2.0)

        engine = SimpleNamespace(
            project=SimpleNamespace(devices=[device], connections={}), devices=manager,
        )
        result = await run_connection_test(device, engine)
        await asyncio.sleep(0.2)
        assert result["success"] is True
        assert result["in_use_by"] == "Lobby Display"
        assert _counts(sim) == (1, 0), "Test Connection opened a second connection"
        assert manager.state.get("device.lobby_display.connected") is True

        # The connect-and-close the test used to make: the display gives the
        # port to it, and the driver's connection is answered no more.
        _, writer = await asyncio.open_connection(HOST, sim.port)
        writer.close()
        assert await _until(lambda: _counts(sim) == (1, 1), 2.0)
    finally:
        await manager.disconnect_all()
        await sim.stop()
