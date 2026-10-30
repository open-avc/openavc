"""Nothing outside Discovery opens a port a connected device's driver holds.

Many displays and projectors serve one control connection at a time and give
the port to the newest, so a second connection takes it from the driver. A
Discovery scan already leaves those ports alone (``discovery/port_guard.py``);
these are the other doors that dial a device:

- the device page's Test Connection (``POST /api/devices/{id}/test``) and the
  AI's ``test_device_connection``, both ``core/device_reachability``: a device
  that is connected is answered from its connection, and a port another
  connected device holds is answered naming that device;
- the cloud diagnostics ``tcp_check`` and ``port_scan``
  (``cloud/command_handler.py``): a held port is reported open and in use by
  the device, without connecting;
- the AI's ``test_driver_command`` (``cloud/tools/device_tools.py``), which
  sends raw text to an address: a held port is refused, naming the device.

The device is an invented Acme Widget whose driver really connects, through
a real ``DeviceManager``, to a loopback port that counts the connections it
accepts and keeps them open, the way a single-session display would.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openavc.api.routes import devices as devices_routes
from openavc.cloud.command_handler import (
    CommandHandler,
    _diagnostic_port_scan,
    _diagnostic_tcp_check,
)
from openavc.cloud.protocol import DIAGNOSTIC_RESULT
from openavc.cloud.tools.device_tools import DeviceToolsMixin
from openavc.core.device_manager import DeviceManager
from openavc.core.device_reachability import run_connection_test
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig
from openavc.core.state_store import StateStore
from openavc.discovery.port_guard import PortGuard
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver

HOST = "127.0.0.1"


class AcmeWidgetDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {},
        "commands": {},
        "state_variables": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class AcmeBridgeDriver(AcmeWidgetDriver):
    DRIVER_INFO: dict[str, Any] = {
        **AcmeWidgetDriver.DRIVER_INFO,
        "id": "acme_bridge",
        "name": "Acme Bridge",
        "bridge": {"ports": [{"id": "ir1", "kind": "ir"}]},
    }


class _Port:
    """A loopback TCP port that counts the connections it accepts and keeps
    each open until the client closes it."""

    def __init__(self) -> None:
        self.accepted = 0
        self.port = 0
        self._server: asyncio.AbstractServer | None = None
        self._writers: list[asyncio.StreamWriter] = []

    async def __aenter__(self) -> "_Port":
        self._server = await asyncio.start_server(self._handle, HOST, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        assert self._server is not None
        self._server.close()
        # The driver's connection is still open; the server waits for it.
        for writer in self._writers:
            writer.close()
        await asyncio.wait_for(self._server.wait_closed(), timeout=5)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.accepted += 1
        self._writers.append(writer)
        try:
            while await reader.read(1024):
                pass
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()


@pytest.fixture
def acme_widget():
    register_driver(AcmeWidgetDriver)
    register_driver(AcmeBridgeDriver)
    yield
    unregister_driver("acme_widget")
    unregister_driver("acme_bridge")


@pytest.fixture
async def manager(acme_widget):
    manager = DeviceManager(StateStore(), EventBus())
    yield manager
    await manager.disconnect_all()


def _device(device_id: str, name: str, port: int, host: str = HOST) -> DeviceConfig:
    return DeviceConfig(
        id=device_id, driver="acme_widget", name=name,
        config={"host": host, "port": port},
    )


async def _connect(manager: DeviceManager, device: DeviceConfig) -> None:
    await manager.add_device(device.model_dump())
    assert manager.state.get(f"device.{device.id}.connected") is True


async def _settle() -> None:
    """Long enough for a connection the test opened to be accepted."""
    await asyncio.sleep(0.2)


def _engine(
    manager: DeviceManager, *devices: DeviceConfig, connections: dict | None = None,
) -> SimpleNamespace:
    project = SimpleNamespace(devices=list(devices), connections=connections or {})
    return SimpleNamespace(project=project, devices=manager)


# ---------------------------------------------------------------------------
# Test Connection and the AI's test_device_connection
# ---------------------------------------------------------------------------


class TestTestConnection:

    async def test_a_connected_device_is_answered_from_its_connection(self, manager):
        async with _Port() as held:
            widget = _device("lobby_widget", "Lobby Widget", held.port)
            await _connect(manager, widget)
            assert held.accepted == 1

            result = await run_connection_test(widget, _engine(manager, widget))
            await _settle()

        assert held.accepted == 1, "the test opened a second connection to a held port"
        assert result["success"] is True
        assert result["latency_ms"] is None
        assert result["in_use_by"] == "Lobby Widget"
        assert result["connection"]["host"] == HOST
        assert result["connection"]["port"] == held.port
        assert result["note"] == (
            "The device is connected now. The test did not open a second connection to it."
        )
        assert manager.state.get("device.lobby_widget.connected") is True

    async def test_a_port_another_connected_device_holds_names_that_device(self, manager):
        async with _Port() as held:
            lobby = _device("lobby_widget", "Lobby Widget", held.port)
            spare = DeviceConfig(
                id="spare_widget", driver="acme_widget", name="Spare Widget",
                enabled=False, config={"host": "localhost", "port": held.port},
            )
            await _connect(manager, lobby)

            # Set up by host name, held by address: looked up before comparing.
            result = await run_connection_test(spare, _engine(manager, lobby, spare))
            await _settle()

        assert held.accepted == 1
        assert result["success"] is True
        assert result["in_use_by"] == "Lobby Widget"
        assert result["note"].startswith("Lobby Widget is connected to this address now.")

    async def test_an_ir_device_whose_bridge_is_connected_names_the_bridge(self, manager):
        async with _Port() as held:
            bridge = DeviceConfig(
                id="rack_bridge", driver="acme_bridge", name="Rack Bridge",
                config={"host": HOST, "port": held.port},
            )
            await _connect(manager, bridge)
            ir = DeviceConfig(id="lobby_ir", driver="acme_widget", name="Lobby IR", config={})
            engine = _engine(manager, bridge, ir, connections={
                "lobby_ir": {"bridge": "rack_bridge", "bridge_port": "ir1"},
            })

            result = await run_connection_test(ir, engine)
            await _settle()

        assert held.accepted == 1
        assert result["success"] is True
        assert result["in_use_by"] == "Rack Bridge"
        assert result["connection"]["bridge"] == "rack_bridge"

    async def test_a_port_nobody_holds_is_dialed_as_before(self, manager):
        async with _Port() as held, _Port() as free:
            lobby = _device("lobby_widget", "Lobby Widget", held.port)
            spare = DeviceConfig(
                id="spare_widget", driver="acme_widget", name="Spare Widget",
                enabled=False, config={"host": HOST, "port": free.port},
            )
            await _connect(manager, lobby)

            result = await run_connection_test(spare, _engine(manager, lobby, spare))
            await _settle()

        assert free.accepted == 1
        assert held.accepted == 1
        assert result["success"] is True
        assert isinstance(result["latency_ms"], float)
        assert "in_use_by" not in result

    async def test_a_simulated_connected_device_reports_the_simulator(self, manager):
        async with _Port() as simulator:
            widget = _device("lobby_widget", "Lobby Widget", simulator.port)
            await _connect(manager, widget)
            # The project names the equipment; the simulation redirects it.
            equipment = _device("lobby_widget", "Lobby Widget", 4321, host="10.77.0.5")
            engine = _engine(manager, equipment)
            engine.simulation = SimpleNamespace(
                redirect_of=lambda device_id: {"host": HOST, "port": simulator.port},
            )

            result = await run_connection_test(equipment, engine)
            await _settle()

        assert simulator.accepted == 1
        assert result["simulated"] is True
        assert result["connection"]["port"] == simulator.port
        assert "10.77.0.5:4321" in result["note"]

    async def test_the_rest_door_does_not_dial_a_connected_device(self, manager, monkeypatch):
        async with _Port() as held:
            widget = _device("lobby_widget", "Lobby Widget", held.port)
            await _connect(manager, widget)
            monkeypatch.setattr(devices_routes, "_get_engine", lambda: _engine(manager, widget))
            monkeypatch.setattr(devices_routes, "_rate_limit_test", lambda key: None)

            result = await devices_routes.test_device_connection("lobby_widget")
            await _settle()

        assert held.accepted == 1
        assert result["in_use_by"] == "Lobby Widget"

    async def test_the_ai_door_does_not_dial_a_connected_device(self, manager):
        async with _Port() as held:
            widget = _device("lobby_widget", "Lobby Widget", held.port)
            await _connect(manager, widget)
            tools = DeviceToolsMixin()
            tools._get_engine = lambda: _engine(manager, widget)

            result = await tools._test_device_connection({"device_id": "lobby_widget"})
            await _settle()

        assert held.accepted == 1
        assert result["in_use_by"] == "Lobby Widget"


# ---------------------------------------------------------------------------
# The cloud diagnostics
# ---------------------------------------------------------------------------


class _Agent:
    _config: dict = {}

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_message(self, msg_type: str, payload: dict) -> None:
        self.sent.append((msg_type, payload))


async def _diagnostic(manager: DeviceManager, action: str, target: str, params: dict) -> dict:
    agent = _Agent()
    handler = CommandHandler(agent, manager, manager.events)
    await handler.handle({"type": "diagnostic", "payload": {
        "request_id": "diag-1", "action": action, "target": target, "params": params,
        "user_id": "u", "user_name": "tester",
    }})
    assert [t for t, _ in agent.sent] == [DIAGNOSTIC_RESULT]
    payload = agent.sent[0][1]
    assert payload["success"] is True, payload
    return payload["result"]


class TestCloudDiagnostics:

    async def test_tcp_check_reports_a_held_port_in_use_without_connecting(self, manager):
        async with _Port() as held:
            await _connect(manager, _device("lobby_widget", "Lobby Widget", held.port))

            result = await _diagnostic(manager, "tcp_check", HOST, {"port": held.port})
            await _settle()

        assert held.accepted == 1
        assert result == {"open": True, "host": HOST, "port": held.port,
                          "in_use_by": "Lobby Widget"}

    async def test_tcp_check_looks_a_host_name_up_first(self, manager):
        async with _Port() as held:
            await _connect(manager, _device("lobby_widget", "Lobby Widget", held.port))

            result = await _diagnostic(manager, "tcp_check", "localhost", {"port": held.port})
            await _settle()

        assert held.accepted == 1
        assert result["in_use_by"] == "Lobby Widget"
        assert result["host"] == "localhost"

    async def test_tcp_check_dials_a_port_nobody_holds(self, manager):
        async with _Port() as held, _Port() as free:
            await _connect(manager, _device("lobby_widget", "Lobby Widget", held.port))

            result = await _diagnostic(manager, "tcp_check", HOST, {"port": free.port})
            await _settle()

        assert free.accepted == 1
        assert result == {"open": True, "host": HOST, "port": free.port}

    async def test_port_scan_lists_a_held_port_open_and_in_use(self, manager):
        async with _Port() as held, _Port() as free:
            await _connect(manager, _device("lobby_widget", "Lobby Widget", held.port))

            result = await _diagnostic(
                manager, "port_scan", HOST, {"ports": [held.port, free.port]},
            )
            await _settle()

        assert held.accepted == 1
        assert free.accepted == 1
        assert result == {
            "host": HOST,
            "scanned": 2,
            "open": sorted([held.port, free.port]),
            "in_use_by": {str(held.port): "Lobby Widget"},
        }

    async def test_port_scan_with_nothing_held_is_unchanged(self, manager):
        async with _Port() as free:
            result = await _diagnostic(manager, "port_scan", HOST, {"ports": [free.port]})
            await _settle()

        assert free.accepted == 1
        assert result == {"host": HOST, "scanned": 1, "open": [free.port]}

    async def test_the_module_functions_take_the_guard_directly(self):
        async with _Port() as held:
            guard = PortGuard({(HOST, held.port): "Lobby Widget"})
            check = await _diagnostic_tcp_check(HOST, {"port": held.port}, guard=guard)
            scan = await _diagnostic_port_scan(HOST, {"ports": [held.port]}, guard=guard)
            await _settle()

        assert held.accepted == 0
        assert check["in_use_by"] == "Lobby Widget"
        assert scan["in_use_by"] == {str(held.port): "Lobby Widget"}


# ---------------------------------------------------------------------------
# The AI's raw command
# ---------------------------------------------------------------------------


class TestRawCommand:

    def _tools(self, manager: DeviceManager) -> DeviceToolsMixin:
        tools = DeviceToolsMixin()
        tools._get_engine = lambda: _engine(manager)
        return tools

    async def test_a_held_port_is_refused_naming_the_device(self, manager):
        async with _Port() as held:
            await _connect(manager, _device("lobby_widget", "Lobby Widget", held.port))

            result = await self._tools(manager)._test_driver_command({
                "host": "localhost", "port": held.port, "command_string": "PWR?", "timeout": 1,
            })
            await _settle()

        assert held.accepted == 1
        assert result["success"] is False
        assert result["in_use_by"] == "Lobby Widget"
        assert result["error"].startswith(f"Lobby Widget is connected to localhost:{held.port} now")
        assert "send_device_command" in result["error"]
        assert manager.state.get("device.lobby_widget.connected") is True

    async def test_a_port_nobody_holds_is_sent_to_as_before(self, manager):
        async with _Port() as held, _Port() as free:
            await _connect(manager, _device("lobby_widget", "Lobby Widget", held.port))

            result = await self._tools(manager)._test_driver_command({
                "host": HOST, "port": free.port, "command_string": "PWR?", "timeout": 1,
            })
            await _settle()

        # The loopback port never answers, so the send times out; it was dialed.
        assert free.accepted == 1
        assert held.accepted == 1
        assert result["error"] == "Timeout waiting for response"
        assert "in_use_by" not in result
