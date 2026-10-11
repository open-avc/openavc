"""The connection test dials where the device manager dials.

``core/device_reachability`` is asked by the device page's Test Connection
(``POST /api/devices/{id}/test``) and by the AI's ``test_device_connection``.
The address is resolved the way the device manager resolves it: the driver's
default port, the device's config, then the connections table, where every
device added since the connections table existed keeps its host.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import openavc.system_config as system_config
from openavc.api.routes import devices as devices_routes
from openavc.core.device_config import connection_of, resolve_device_config
from openavc.core.device_reachability import check_reachability
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver


def _driver(driver_id: str, transport: str, default_config: dict, **extra: Any) -> type:
    class _Acme(BaseDriver):
        DRIVER_INFO: dict[str, Any] = {
            "id": driver_id,
            "name": f"Acme {driver_id}",
            "manufacturer": "Acme",
            "category": "utility",
            "transport": transport,
            "default_config": default_config,
            "commands": {},
            "state_variables": {},
            **extra,
        }

        async def send_command(self, command: str, params: dict | None = None) -> Any:
            return None

    return _Acme


@pytest.fixture
def drivers():
    registered: list[str] = []

    def add(driver_id: str, transport: str, default_config: dict, **extra: Any) -> type:
        cls = _driver(driver_id, transport, default_config, **extra)
        register_driver(cls)
        registered.append(driver_id)
        return cls

    yield add
    for driver_id in registered:
        unregister_driver(driver_id)


def _project(*devices: DeviceConfig, connections: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(devices=list(devices), connections=connections or {})


@pytest.fixture
async def listener():
    """A TCP server on loopback that counts the connections it accepts."""
    accepted: list[int] = []
    accepted_event = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.append(1)
        accepted_event.set()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield SimpleNamespace(port=port, accepted=accepted, accepted_event=accepted_event)
    finally:
        server.close()
        await server.wait_closed()


async def _test(device: DeviceConfig, project: SimpleNamespace) -> dict:
    return await check_reachability(resolve_device_config(device, project), project)


# --- the address -------------------------------------------------------------

async def test_host_from_the_connections_table_and_port_from_the_driver(drivers, listener):
    """The layout every device has had since the connections table: the host
    in the table, the port nowhere but the driver's default."""
    drivers("acme_reach_tcp", "tcp", {"port": listener.port})
    device = DeviceConfig(id="w1", driver="acme_reach_tcp", name="W1", config={})
    project = _project(device, connections={"w1": {"host": "127.0.0.1"}})

    result = await _test(device, project)

    assert result["success"] is True, result
    assert result["connection"] == {
        "transport": "tcp", "host": "127.0.0.1", "port": listener.port, "bridge": "",
    }
    await asyncio.wait_for(listener.accepted_event.wait(), timeout=5)
    assert listener.accepted == [1]


async def test_the_rest_door_dials_the_same_address(drivers, listener, monkeypatch):
    drivers("acme_reach_rest", "tcp", {"port": listener.port})
    device = DeviceConfig(id="rest_w1", driver="acme_reach_rest", name="W1", config={})
    project = _project(device, connections={"rest_w1": {"host": "127.0.0.1"}})
    monkeypatch.setattr(devices_routes, "_get_engine", lambda: SimpleNamespace(project=project))

    result = await devices_routes.test_device_connection("rest_w1")

    assert result["success"] is True, result
    assert result["connection"]["port"] == listener.port
    await asyncio.wait_for(listener.accepted_event.wait(), timeout=5)
    assert listener.accepted == [1]


async def test_the_connections_table_wins_over_the_driver_default(drivers, listener):
    drivers("acme_reach_override", "tcp", {"port": 1})
    device = DeviceConfig(id="w2", driver="acme_reach_override", name="W2", config={})
    project = _project(
        device, connections={"w2": {"host": "127.0.0.1", "port": listener.port}}
    )
    result = await _test(device, project)
    assert result["success"] is True, result
    assert result["connection"]["port"] == listener.port


async def test_the_transport_comes_from_the_driver_when_the_device_names_none(drivers):
    """A UDP driver's device is not probed with a TCP connect, and the result
    says how little a datagram check proves."""
    drivers("acme_reach_udp", "udp", {"port": 9000})
    device = DeviceConfig(id="u1", driver="acme_reach_udp", name="U1", config={})
    project = _project(device, connections={"u1": {"host": "127.0.0.1"}})

    result = await _test(device, project)

    assert result["success"] is True, result
    assert result["connection"]["transport"] == "udp"
    assert "UDP" in result["note"]


# --- what is missing ---------------------------------------------------------

async def test_no_host_says_where_to_set_one(drivers):
    drivers("acme_reach_nohost", "tcp", {"port": 7000})
    device = DeviceConfig(id="n1", driver="acme_reach_nohost", name="N1", config={})
    result = await _test(device, _project(device))
    assert result["success"] is False
    assert "No address" in result["error"]
    assert "Connection Settings" in result["error"]


async def test_no_port_anywhere_is_reported_not_guessed(drivers):
    """A driver with no default port and a device with none: nothing is
    dialed, and the reply says the driver supplies none."""
    drivers("acme_reach_noport", "tcp", {})
    device = DeviceConfig(id="n2", driver="acme_reach_noport", name="N2", config={})
    project = _project(device, connections={"n2": {"host": "192.0.2.10"}})
    result = await _test(device, project)
    assert result["success"] is False
    assert "No port" in result["error"]
    assert result["connection"]["port"] is None


async def test_a_port_that_is_not_a_number_is_named(drivers):
    drivers("acme_reach_badport", "tcp", {})
    device = DeviceConfig(id="n3", driver="acme_reach_badport", name="N3", config={})
    project = _project(device, connections={"n3": {"host": "192.0.2.10", "port": "telnet"}})
    result = await _test(device, project)
    assert result["success"] is False
    assert "'telnet' is not a number" in result["error"]


async def test_a_port_out_of_range_is_named(drivers):
    drivers("acme_reach_range", "tcp", {})
    device = DeviceConfig(id="n4", driver="acme_reach_range", name="N4", config={})
    project = _project(device, connections={"n4": {"host": "192.0.2.10", "port": 70000}})
    result = await _test(device, project)
    assert result["success"] is False
    assert "out of range" in result["error"]


# --- HTTP --------------------------------------------------------------------

class _FakeClient:
    def __init__(self, captured: dict, **kwargs: Any) -> None:
        captured.update(kwargs)
        self._captured = captured

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def head(self, url: str) -> SimpleNamespace:
        self._captured["url"] = url
        return SimpleNamespace(status_code=200)


@pytest.fixture
def captured_httpx(monkeypatch) -> dict:
    captured: dict = {}
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient(captured, **kwargs))
    return captured


@pytest.mark.parametrize(("ssl", "url"), [
    (False, "http://192.0.2.20:80"),
    (True, "https://192.0.2.20:443"),
])
async def test_http_builds_the_url_the_transport_builds(drivers, captured_httpx, ssl, url):
    drivers(f"acme_reach_http_{ssl}", "http", {})
    device = DeviceConfig(id=f"h{ssl}", driver=f"acme_reach_http_{ssl}", name="H", config={})
    project = _project(device, connections={f"h{ssl}": {"host": "192.0.2.20", "ssl": ssl}})

    result = await _test(device, project)

    assert result["success"] is True, result
    assert captured_httpx["url"] == url
    assert captured_httpx["verify"] is True
    assert result["connection"]["port"] == (443 if ssl else 80)


async def test_http_host_wins_over_a_stale_base_url(drivers, captured_httpx):
    """The transport builds its URL from host, port and ssl and never reads
    base_url, so neither does the test while a host is set."""
    drivers("acme_reach_http_base", "http", {})
    device = DeviceConfig(id="hb", driver="acme_reach_http_base", name="HB", config={})
    project = _project(device, connections={"hb": {
        "host": "192.0.2.21", "port": 8080, "base_url": "http://192.0.2.99",
    }})
    await _test(device, project)
    assert captured_httpx["url"] == "http://192.0.2.21:8080"


# --- bridges -----------------------------------------------------------------

async def test_an_ir_device_tests_the_bridge_it_sends_through(drivers, listener):
    drivers("acme_reach_bridge", "tcp", {"port": listener.port}, bridge={"ports": [
        {"id": "ir1", "kind": "ir"},
    ]})
    drivers("acme_reach_ir", "tcp", {})
    bridge = DeviceConfig(id="br", driver="acme_reach_bridge", name="Bridge", config={})
    ir = DeviceConfig(id="tv", driver="acme_reach_ir", name="TV", config={})
    project = _project(bridge, ir, connections={
        "br": {"host": "127.0.0.1"},
        "tv": {"bridge": "br", "bridge_port": "ir1"},
    })

    result = await _test(ir, project)

    assert result["success"] is True, result
    assert result["connection"] == {
        "transport": "tcp", "host": "127.0.0.1", "port": listener.port, "bridge": "br",
    }
    await asyncio.wait_for(listener.accepted_event.wait(), timeout=5)
    assert listener.accepted == [1]


async def test_a_device_whose_bridge_is_gone_says_so(drivers):
    drivers("acme_reach_orphan_ir", "tcp", {})
    ir = DeviceConfig(id="tv2", driver="acme_reach_orphan_ir", name="TV", config={})
    project = _project(ir, connections={"tv2": {"bridge": "gone", "bridge_port": "ir1"}})
    result = await _test(ir, project)
    assert result["success"] is False
    assert "'gone', which is not in the project" in result["error"]


# --- the control interface ---------------------------------------------------

class _Pinned:
    def get(self, section: str, key: str) -> Any:
        # Reach devices through, as OPENAVC_CONTROL_INTERFACE holds it: an
        # address, so no adapter is enumerated.
        assert (section, key) == ("network", "reach_devices_through")
        return {"address": "192.0.2.77"}


async def test_a_pinned_control_interface_binds_the_test_too(drivers, monkeypatch):
    monkeypatch.setattr(system_config, "get_system_config", lambda: _Pinned())
    seen: dict = {}

    async def fake_open_connection(host: str, port: int, **kwargs: Any):
        seen.update(host=host, port=port, local_addr=kwargs.get("local_addr"))
        raise OSError("stop here")

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)
    drivers("acme_reach_pinned", "tcp", {"port": 7000})
    device = DeviceConfig(id="p1", driver="acme_reach_pinned", name="P1", config={})
    project = _project(device, connections={"p1": {"host": "192.0.2.30"}})

    await _test(device, project)

    assert seen == {"host": "192.0.2.30", "port": 7000, "local_addr": ("192.0.2.77", 0)}


# --- the read-back is the port the driver dials ------------------------------

@pytest.mark.parametrize(("transport", "config"), [
    ("http", {}),
    ("http", {"ssl": True}),
    ("ssh", {}),
    ("mqtt", {}),
    ("snmp", {}),
])
async def test_a_transport_fallback_port_matches_what_the_driver_dials(
    drivers, monkeypatch, transport, config
):
    """``connection_of`` reports the port a transport falls back to; this
    builds the real transport and reads the port it was given."""
    from openavc.transport import http_client, mqtt, snmp, ssh

    dialed: dict = {}

    async def fake_create(cls, *args: Any, **kwargs: Any) -> object:
        dialed["port"] = kwargs["port"]
        return object()

    async def no_open(self, *args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(ssh.SSHTransport, "create", classmethod(fake_create))
    monkeypatch.setattr(mqtt.MQTTTransport, "create", classmethod(fake_create))
    monkeypatch.setattr(http_client.HTTPClientTransport, "open", no_open)
    monkeypatch.setattr(snmp.SNMPTransport, "open", no_open)

    cls = drivers(f"acme_reach_fb_{transport}_{bool(config)}", transport, {})
    resolved_config = {"host": "192.0.2.40", **config}
    driver = cls("fb", dict(resolved_config), StateStore(), EventBus())
    await driver._create_transport(transport)
    if transport == "http":
        dialed["port"] = int(driver.transport.base_url.rstrip("/").rsplit(":", 1)[1])
    elif transport == "snmp":
        dialed["port"] = driver.transport.port

    reported = connection_of({
        "id": "fb", "driver": cls.DRIVER_INFO["id"], "config": resolved_config,
    }).port
    assert reported == dialed["port"]


# --- what a failure says ---------------------------------------------------

async def test_an_os_error_reads_in_the_classifiers_words(drivers, monkeypatch):
    """The raw "[Errno 64] Host is down" is OS text; the device card already
    says the same thing in words an integrator acts on."""
    import errno

    async def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(errno.EHOSTDOWN, "Host is down")

    monkeypatch.setattr(asyncio, "open_connection", refuse)
    drivers("acme_reach_down", "tcp", {"port": 4000})
    device = DeviceConfig(id="d1", driver="acme_reach_down", name="D1", config={})
    project = _project(device, connections={"d1": {"host": "10.9.8.7"}})

    result = await _test(device, project)

    assert result["success"] is False
    assert result["error"] == "Can't reach 10.9.8.7:4000. Check the IP address and network."


async def test_a_timeout_names_the_address(drivers, monkeypatch):
    """The page shows the error as it is, so every failure about the
    address names it: the timeout used to rely on a prefix the page added."""
    from openavc.core import device_reachability

    async def hang(*_args: Any, **_kwargs: Any) -> Any:
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio, "open_connection", hang)
    monkeypatch.setattr(device_reachability, "_CONNECT_TIMEOUT_S", 0.05)
    drivers("acme_reach_slow", "tcp", {"port": 4000})
    device = DeviceConfig(id="d2", driver="acme_reach_slow", name="D2", config={})
    project = _project(device, connections={"d2": {"host": "10.9.8.7"}})

    result = await _test(device, project)

    assert result["error"] == (
        "No answer from 10.9.8.7:4000 within 0.05 s. Check the IP address and network."
    )


async def test_an_unexplained_error_names_the_address(drivers, monkeypatch):
    """The classifier's fallbacks keep the error's own text, which says
    nothing about where the test was dialing."""
    async def odd(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("stop here")

    monkeypatch.setattr(asyncio, "open_connection", odd)
    drivers("acme_reach_odd", "tcp", {"port": 4000})
    device = DeviceConfig(id="d3", driver="acme_reach_odd", name="D3", config={})
    project = _project(device, connections={"d3": {"host": "10.9.8.7"}})

    result = await _test(device, project)

    assert result["error"] == "Could not connect to 10.9.8.7:4000: stop here"


def _host_down(monkeypatch) -> None:
    import errno

    async def down(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(errno.EHOSTDOWN, "Host is down")

    monkeypatch.setattr(asyncio, "open_connection", down)


async def test_a_failed_ir_device_names_the_bridge_it_sends_through(drivers, monkeypatch):
    """The address in the sentence is the bridge's, which the integrator
    never entered on this device."""
    _host_down(monkeypatch)
    drivers("acme_reach_down_bridge", "tcp", {"port": 4998}, bridge={"ports": [
        {"id": "ir1", "kind": "ir"},
    ]})
    drivers("acme_reach_down_ir", "tcp", {})
    bridge = DeviceConfig(id="br2", driver="acme_reach_down_bridge", name="Rack Bridge", config={})
    ir = DeviceConfig(id="tv3", driver="acme_reach_down_ir", name="TV", config={})
    project = _project(bridge, ir, connections={
        "br2": {"host": "10.9.8.6"},
        "tv3": {"bridge": "br2", "bridge_port": "ir1"},
    })

    result = await _test(ir, project)

    assert result["error"] == (
        "Can't reach 10.9.8.6:4998. Check the IP address and network. "
        "This device sends through the bridge 'Rack Bridge'."
    )


async def test_a_failed_serial_pass_through_names_the_bridge(drivers, monkeypatch):
    _host_down(monkeypatch)
    drivers("acme_reach_pt_bridge", "tcp", {"port": 4998}, bridge={"ports": [
        {"id": "s1", "kind": "serial", "passthrough_port": 4999},
    ]})
    drivers("acme_reach_pt_dev", "serial", {})
    bridge = DeviceConfig(id="br3", driver="acme_reach_pt_bridge", name="Rack Bridge", config={})
    dev = DeviceConfig(id="pj", driver="acme_reach_pt_dev", name="Projector", config={})
    project = _project(bridge, dev, connections={
        "br3": {"host": "10.9.8.6"},
        "pj": {"bridge": "br3", "bridge_port": "s1"},
    })

    result = await _test(dev, project)

    assert result["connection"]["bridge"] == "br3"
    assert result["error"] == (
        "Can't reach 10.9.8.6:4999. Check the IP address and network. "
        "This device sends through the bridge 'Rack Bridge'."
    )


# --- a simulated device ------------------------------------------------------

async def test_a_simulated_device_tests_the_simulator_and_says_so(drivers, listener, monkeypatch):
    """While simulated, the device is connected to the simulator: testing its
    real address timed out beside a device that read online."""
    drivers("acme_reach_sim", "tcp", {"port": 9})
    device = DeviceConfig(id="sim1", driver="acme_reach_sim", name="S1", config={})
    project = _project(device, connections={"sim1": {"host": "10.255.255.1"}})
    simulation = SimpleNamespace(
        redirect_of=lambda device_id: {"host": "127.0.0.1", "port": listener.port}
        if device_id == "sim1" else None,
    )
    monkeypatch.setattr(
        devices_routes, "_get_engine",
        lambda: SimpleNamespace(project=project, simulation=simulation),
    )

    result = await devices_routes.test_device_connection("sim1")

    assert result["success"] is True, result
    assert result["connection"]["port"] == listener.port
    assert result["simulated"] is True
    assert result["note"] == (
        "This device is simulated, so the test reached the simulator, "
        "not the equipment at 10.255.255.1:9."
    )
    assert listener.accepted == [1]


def test_the_simulation_reports_where_a_redirected_device_connects():
    from openavc.core.simulation import SimulationManager

    live = SimpleNamespace(config={"host": "10.0.0.5", "port": 23})
    manager = SimulationManager(
        engine=SimpleNamespace(devices=SimpleNamespace(_devices={"tv1": live}))
    )
    assert manager.redirect_of("tv1") is None

    manager._apply_sim_redirect(live, "tv1", 19007)

    assert manager.redirect_of("tv1") == {"host": "127.0.0.1", "port": 19007}
    assert manager.redirect_of("other") is None
