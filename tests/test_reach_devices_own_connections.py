"""Reach devices through, for the connections a driver opens itself.

Settings > Network > Reach devices through pins device traffic to one
adapter's address (``openavc/reach_devices.py``). The platform's own
transports bind to it, and so must everything else that reaches a device: an
``httpx.AsyncClient`` a driver builds (bound with nothing in the driver),
a socket, WebSocket or datagram endpoint the driver opens through
``BaseDriver.local_address()``, the ssh and mqtt transports, ``send_udp``,
the reachability probes, and the equipment driver's remote connection.

How the bind is seen: the "device" listens on this machine's own network
address, and the pinned address is 127.0.0.1. (A pinned network address would
not show anything, because a connection from this machine to itself leaves
from that address either way.) What a connection bound to 127.0.0.1 does
next depends on the operating system, so the module asks once, with a trial
connection, and every test expects the answer it got:

- Linux and macOS route it over loopback: a bound connection arrives from
  127.0.0.1, an unbound one from the network address.
- Windows refuses it ("the network location cannot be reached"): a socket
  bound to one adapter's address leaves only through that adapter, which is
  the very thing Reach devices through relies on. There a bound connection
  is refused for that reason and nothing arrives, while the same connection
  with nothing pinned arrives from the network address.

Skipped only where the machine has no network address at all. A connection
to a loopback host is never bound, which the address nobody holds
(192.0.2.1, reserved for documentation) shows: binding to it fails, so a
connection that succeeds was not bound.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import inspect
import os
import socket
import ssl
import textwrap

import httpx
import pytest

from openavc import reach_devices
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport.http_client import ReachDevicesTransport

PINNED = "127.0.0.1"
NOBODYS_ADDRESS = "192.0.2.1"


def _own_network_address() -> str | None:
    """A non-loopback IPv4 this machine holds, or None.

    The adapter list leaves out virtual adapters, and a CI runner's only one
    can be virtual; the address this machine routes the internet from is the
    fallback.
    """
    from openavc import listen
    from openavc.discovery.network_scanner import get_default_route_ip, get_interface_ips

    for ip in [*get_interface_ips(), get_default_route_ip()]:
        if ip and listen.address_available(ip):
            return ip
    return None


def _pinned_reaches(address: str) -> bool:
    """Whether a connection bound to ``PINNED`` reaches ``address`` on this machine."""
    with socket.socket() as device, socket.socket() as dialer:
        device.bind((address, 0))
        device.listen(1)
        dialer.bind((PINNED, 0))
        dialer.settimeout(2)
        try:
            dialer.connect(device.getsockname())
        except OSError:
            return False
        return True


OWN = _own_network_address()
needs_network = pytest.mark.skipif(OWN is None, reason="this machine has no network address")

#: Whether a connection pinned to 127.0.0.1 reaches OWN (the module docstring).
REACHES = OWN is not None and _pinned_reaches(OWN)
#: Who a device on OWN sees connect when the connection is pinned to 127.0.0.1.
BOUND_PEERS = [PINNED] if REACHES else []
#: A pin a connection to OWN gets through on: 127.0.0.1 where that reaches
#: OWN, else OWN itself. For the tests whose subject is not the bind.
REACHING = PINNED if REACHES else OWN

_UNREACHABLE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH}
#: ERROR_NETWORK_UNREACHABLE and ERROR_HOST_UNREACHABLE, which Windows raises
#: from an overlapped connect instead of the socket errors.
_UNREACHABLE_WINERRORS = {1231, 1232}


def unreachable(exc: BaseException) -> bool:
    """Whether ``exc``, or anything it wraps, says the network was unreachable."""
    pending, seen = [exc], set()
    while pending:
        node = pending.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, OSError) and (
            node.errno in _UNREACHABLE_ERRNOS
            or getattr(node, "winerror", None) in _UNREACHABLE_WINERRORS
        ):
            return True
        pending += [node.__cause__, node.__context__, *getattr(node, "exceptions", ())]
    return False


@contextlib.contextmanager
def bound():
    """Around a connection to OWN pinned to 127.0.0.1: where that cannot reach
    OWN, the connection must be refused as unreachable, which only a bound
    one is."""
    if REACHES:
        yield
        return
    with pytest.raises(Exception) as refused:
        yield
    assert unreachable(refused.value), repr(refused.value)


@pytest.fixture
def pin(monkeypatch):
    """Pin Reach devices through to an address for the test."""

    def set_to(address: str) -> None:
        monkeypatch.setattr(reach_devices, "address", lambda: address)

    set_to("")
    return set_to


class _AcmeOwnConnections(BaseDriver):
    DRIVER_INFO = {"id": "acme_own_links", "name": "Acme Own Links", "transport": "tcp"}

    async def send_command(self, command, params=None):
        return None

    def build_client(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(**kwargs)


def _driver(host: str) -> _AcmeOwnConnections:
    state = StateStore()
    state.set_event_bus(EventBus())
    return _AcmeOwnConnections("acme1", {"host": host, "port": 1}, state, EventBus())


class _Device:
    """A TCP device on ``host`` that answers HTTP and notes who connected."""

    def __init__(self, host: str, tls: ssl.SSLContext | None = None) -> None:
        self.host, self.tls = host, tls
        self.peers: list[str] = []
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    async def __aenter__(self) -> "_Device":
        async def handle(reader, writer):
            self.peers.append(writer.get_extra_info("peername")[0])
            try:
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                )
                await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        self._server = await asyncio.start_server(handle, self.host, 0, ssl=self.tls)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        scheme = "https" if self.tls else "http"
        return f"{scheme}://{self.host}:{self.port}"


@pytest.fixture
def self_signed():
    from openavc.simulator.self_signed_tls import _generate_self_signed

    cert, key = _generate_self_signed()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    yield ctx
    for path in (cert, key):
        os.unlink(path)


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_local_address_is_the_pinned_address_for_a_device_on_the_network(pin):
    pin("10.20.0.5")
    drv = _driver("10.20.0.80")
    assert drv.local_address() == "10.20.0.5"
    assert drv.local_address("10.20.0.81") == "10.20.0.5"


def test_local_address_is_none_with_nothing_pinned(pin):
    assert _driver("10.20.0.80").local_address() is None


def test_local_address_is_none_for_a_device_on_this_computer(pin):
    pin("10.20.0.5")
    assert _driver("127.0.0.1").local_address() is None
    assert _driver("localhost").local_address() is None
    assert _driver("10.20.0.80").local_address("127.0.0.1") is None


@needs_network
async def test_a_socket_opened_through_the_helper_leaves_from_the_pinned_address(pin):
    pin(PINNED)
    async with _Device(OWN) as device:
        drv = _driver(OWN)
        ip = drv.local_address()
        with bound():
            _, writer = await asyncio.open_connection(
                OWN, device.port, local_addr=(ip, 0) if ip else None,
            )
            writer.close()
        await asyncio.sleep(0.05)
    assert device.peers == BOUND_PEERS


@needs_network
async def test_a_websocket_opened_through_the_helper_leaves_from_the_pinned_address(pin):
    websockets = pytest.importorskip("websockets")
    pin(PINNED)
    peers: list[str] = []

    async def handler(ws):
        peers.append(ws.remote_address[0])
        await ws.close()

    async with websockets.serve(handler, OWN, 0) as server:
        port = server.sockets[0].getsockname()[1]
        drv = _driver(OWN)
        ip = drv.local_address()
        with bound():
            async with websockets.connect(
                f"ws://{OWN}:{port}/", local_addr=(ip, 0) if ip else None,
            ) as ws:
                with pytest.raises(websockets.ConnectionClosed):
                    await ws.recv()
    assert peers == BOUND_PEERS


@needs_network
async def test_a_datagram_endpoint_opened_through_the_helper_leaves_from_the_pinned_address(pin):
    pin(PINNED)
    loop = asyncio.get_running_loop()
    received: asyncio.Future = loop.create_future()

    class Device(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            if not received.done():
                received.set_result(addr[0])

    class Sender(asyncio.DatagramProtocol):
        def __init__(self):
            self.errors: list[Exception] = []

        def error_received(self, exc):
            self.errors.append(exc)

    device, _ = await loop.create_datagram_endpoint(Device, local_addr=(OWN, 0))
    port = device.get_extra_info("sockname")[1]
    drv = _driver(OWN)
    ip = drv.local_address()
    sender, sending = await loop.create_datagram_endpoint(
        Sender, local_addr=(ip or "0.0.0.0", 0),
    )
    try:
        sender.sendto(b"hello", (OWN, port))
        if REACHES:
            assert await asyncio.wait_for(received, 2) == PINNED
        else:
            # A datagram send that fails reaches the protocol, not the caller.
            await asyncio.sleep(0.3)
            assert not received.done()
            assert any(unreachable(e) for e in sending.errors), sending.errors
    finally:
        sender.close()
        device.close()


# ---------------------------------------------------------------------------
# A driver's own httpx client
# ---------------------------------------------------------------------------


@needs_network
async def test_a_drivers_own_client_leaves_from_the_pinned_address(pin):
    pin(PINNED)
    async with _Device(OWN) as device:
        drv = _driver(OWN)
        async with drv.build_client(base_url=device.url) as client:
            assert isinstance(client._transport, ReachDevicesTransport)
            with bound():
                assert (await client.get("/")).text == "ok"
    assert device.peers == BOUND_PEERS


@needs_network
async def test_with_nothing_pinned_the_client_is_the_one_the_driver_asked_for(pin):
    async with _Device(OWN) as device:
        drv = _driver(OWN)
        async with drv.build_client(base_url=device.url) as client:
            assert type(client._transport) is httpx.AsyncHTTPTransport
            assert (await client.get("/")).text == "ok"
    assert device.peers == [OWN]


def test_a_device_on_this_computer_keeps_the_client_it_asked_for(pin):
    pin(PINNED)
    client = _driver("127.0.0.1").build_client(base_url="http://127.0.0.1:1")
    assert type(client._transport) is httpx.AsyncHTTPTransport


@pytest.mark.parametrize("own", [
    {"transport": httpx.MockTransport(lambda request: httpx.Response(200))},
    {"mounts": {"http://": httpx.AsyncHTTPTransport()}},
    {"proxy": "http://10.20.0.9:3128"},
])
def test_a_client_given_its_own_way_out_is_left_alone(pin, own):
    pin("10.20.0.5")
    client = _driver("10.20.0.80").build_client(**own)
    assert not isinstance(client._transport, ReachDevicesTransport)


def test_a_client_built_outside_any_driver_is_left_alone(pin):
    pin("10.20.0.5")
    client = httpx.AsyncClient(base_url="http://10.20.0.80")
    assert type(client._transport) is httpx.AsyncHTTPTransport


def test_the_client_settings_reach_the_pinned_connections(pin):
    pin("10.20.0.5")
    limits = httpx.Limits(max_connections=3)
    client = _driver("10.20.0.80").build_client(
        verify=False, limits=limits, trust_env=False, http1=True, timeout=5.0,
    )
    transport = client._transport
    assert isinstance(transport, ReachDevicesTransport)
    assert transport._settings == {
        "verify": False, "limits": limits, "trust_env": False, "http1": True,
    }
    pool = transport.pool_for("10.20.0.80")
    assert pool._pool._local_address == "10.20.0.5"
    assert pool._pool._max_connections == 3
    assert pool._pool._ssl_context.verify_mode == ssl.CERT_NONE


def test_a_setting_the_driver_left_out_is_the_one_httpx_would_have_used():
    """The transport is built from only what the driver passed, so the
    transport's own defaults must be the client's."""
    client = inspect.signature(httpx.AsyncClient.__init__.__wrapped__).parameters
    transport = inspect.signature(httpx.AsyncHTTPTransport.__init__).parameters
    for name in ("verify", "cert", "trust_env", "http1", "http2", "limits"):
        assert client[name].default == transport[name].default, name


@needs_network
async def test_a_self_signed_device_connects_when_the_driver_trusts_it(pin, self_signed):
    pin(REACHING)
    async with _Device(OWN, tls=self_signed) as device:
        drv = _driver(OWN)
        async with drv.build_client(base_url=device.url, verify=False) as client:
            assert isinstance(client._transport, ReachDevicesTransport)
            assert (await client.get("/")).text == "ok"
    assert device.peers == [REACHING]


@needs_network
async def test_a_self_signed_device_is_still_refused_when_the_driver_verifies(pin, self_signed):
    pin(REACHING)
    async with _Device(OWN, tls=self_signed) as device:
        drv = _driver(OWN)
        async with drv.build_client(base_url=device.url) as client:
            assert isinstance(client._transport, ReachDevicesTransport)
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                await client.get("/")


@needs_network
async def test_each_request_asks_where_to_leave_from(pin):
    """A changed choice, or a DHCP renewal, reaches the next connection
    without the driver rebuilding its client; a host on this computer is
    never bound."""
    async with _Device(OWN) as device, _Device("127.0.0.1") as local:
        pin(PINNED)
        async with _driver(OWN).build_client(base_url=device.url) as client:
            with bound():
                await client.get("/")
            pin(OWN)
            await client.get("/")
            pin(NOBODYS_ADDRESS)
            assert (await client.get(f"{local.url}/")).text == "ok"
    assert device.peers == [*BOUND_PEERS, OWN]
    assert local.peers == ["127.0.0.1"]


async def test_closing_the_client_closes_every_pool(pin):
    pin("10.20.0.5")
    client = _driver("10.20.0.80").build_client()
    transport = client._transport
    pools = [transport.pool_for("10.20.0.80"), transport.pool_for("127.0.0.1")]
    closed: list[object] = []
    for pool in pools:
        async def aclose(pool=pool):
            closed.append(pool)
        pool.aclose = aclose
    await client.aclose()
    assert closed == pools


# ---------------------------------------------------------------------------
# The platform's other connections to a device
# ---------------------------------------------------------------------------


@needs_network
async def test_verify_reachable_dials_from_the_pinned_address(pin):
    async with _Device(OWN) as device:
        drv = _driver(OWN)
        assert await drv._verify_reachable(OWN, device.port)
        pin(PINNED)
        assert await drv._verify_reachable(OWN, device.port) is REACHES
        pin(NOBODYS_ADDRESS)
        assert not await drv._verify_reachable(OWN, device.port)
        await asyncio.sleep(0.05)
    assert device.peers == [OWN, *BOUND_PEERS]


@needs_network
async def test_send_udp_leaves_from_the_pinned_address(pin):
    pin(PINNED)
    loop = asyncio.get_running_loop()
    received: asyncio.Future = loop.create_future()

    class Device(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            if not received.done():
                received.set_result(addr[0])

    device, _ = await loop.create_datagram_endpoint(Device, local_addr=(OWN, 0))
    port = device.get_extra_info("sockname")[1]
    try:
        drv = _driver(OWN)
        await drv.send_udp(b"wake", port=port)
        if REACHES:
            assert await asyncio.wait_for(received, 2) == PINNED
        else:
            await asyncio.sleep(0.3)
            assert not received.done()
            pin("")
            await drv.send_udp(b"wake", port=port)
            assert await asyncio.wait_for(received, 2) == OWN
    finally:
        device.close()


async def test_send_udp_to_the_simulator_is_not_bound(pin):
    pin(NOBODYS_ADDRESS)
    opened: list[str | None] = []
    drv = _driver("10.20.0.80")

    class Capture:
        async def send_to(self, data, host, port):
            pass

        async def close(self):
            pass

    async def open_udp(local_addr=None):
        opened.append(local_addr)
        return Capture()

    drv._open_udp = open_udp
    drv.udp_redirect = ("127.0.0.1", 19100)
    await drv.send_udp(b"x", port=9)
    await drv.send_udp(b"x", port=9, broadcast=True)
    assert opened == [None, None]


async def test_a_bound_udp_socket_sends_multicast_through_the_same_adapter(pin):
    from openavc.transport.udp import UDPTransport

    udp = UDPTransport(name="acme1")
    await udp.open(local_addr="127.0.0.1")
    try:
        sock = udp._transport.get_extra_info("socket")
        chosen = sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, 4)
        assert socket.inet_ntoa(chosen) == "127.0.0.1"
    finally:
        await udp.close()


def test_ssh_binds_after_the_devices_own_options():
    from openavc.transport.ssh import SSHTransport

    def argv(**kwargs):
        transport = SSHTransport(
            "10.20.0.80", 22, "admin", lambda data: None, lambda: None,
            ssh_binary="ssh", **kwargs,
        )
        return transport.build_argv()

    assert not any(arg.startswith("BindAddress=") for arg in argv())
    bound = argv(local_address="10.20.0.5", extra_ssh_options=["BindAddress=10.20.0.6"])
    options = [bound[i + 1] for i, arg in enumerate(bound) if arg == "-o"]
    assert options.index("BindAddress=10.20.0.6") < options.index("BindAddress=10.20.0.5")


@pytest.mark.parametrize("kind", ["ssh", "mqtt"])
async def test_the_ssh_and_mqtt_transports_are_given_the_pinned_address(pin, monkeypatch, kind):
    from openavc.transport.mqtt import MQTTTransport
    from openavc.transport.ssh import SSHTransport

    given: dict = {}

    async def create(cls, *args, **kwargs):
        given.update(kwargs)
        return object()

    target = SSHTransport if kind == "ssh" else MQTTTransport
    monkeypatch.setattr(target, "create", classmethod(create))
    pin("10.20.0.5")
    drv = _driver("10.20.0.80")
    await drv._create_transport(kind)
    assert given["local_address"] == "10.20.0.5"
    given.clear()
    drv = _driver("127.0.0.1")
    await drv._create_transport(kind)
    assert given["local_address"] is None


@needs_network
async def test_mqtt_connects_from_the_pinned_address():
    """A broker on this machine's network address that answers CONNECT."""
    from openavc.transport.mqtt import MQTTTransport

    peers: list[str] = []

    async def broker(reader, writer):
        peers.append(writer.get_extra_info("peername")[0])
        try:
            await reader.read(1024)
            writer.write(b"\x20\x02\x00\x00")  # CONNACK, accepted
            await writer.drain()
            await reader.read(1024)
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(broker, OWN, 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with bound():
            transport = await MQTTTransport.create(OWN, port, local_address=PINNED, name="acme1")
            try:
                assert transport.connected
            finally:
                await transport.close()
    finally:
        server.close()
        await server.wait_closed()
    assert peers == BOUND_PEERS


def test_the_mqtt_dial_copies_the_gmqtt_it_was_written_against():
    """``_bound_client_class`` repeats gmqtt's own dialing step with a local
    address added. If an upgrade changes that step, the copy is stale: read
    the new gmqtt and update the override, then this text."""
    from gmqtt import Client
    from gmqtt.mqtt.connection import MQTTConnection

    def body(fn) -> str:
        return textwrap.dedent(inspect.getsource(fn)).strip()

    assert body(Client._create_connection) == textwrap.dedent('''\
        async def _create_connection(self, host, port, ssl, clean_session, keepalive):
            # important for reconnects, make sure u know what u are doing if wanna change :(
            self._exit_reconnecting_state()
            self._clear_topics_aliases()
            connection = await MQTTConnection.create_connection(host, port, ssl, clean_session, keepalive, logger=self._logger)
            connection.set_handler(self)
            return connection''')
    assert body(MQTTConnection.create_connection.__func__) == textwrap.dedent('''\
        @classmethod
        async def create_connection(cls, host, port, ssl, clean_session, keepalive, loop=None, logger=None):
            loop = loop or asyncio.get_event_loop()
            transport, protocol = await loop.create_connection(MQTTProtocol, host, port, ssl=ssl)
            return MQTTConnection(transport, protocol, clean_session, keepalive, logger=logger)''')


@needs_network
async def test_the_web_ui_probe_dials_from_the_pinned_address(pin):
    from openavc.core.web_ui_probe import probe_web_ui

    async with _Device(OWN) as device:
        candidates = ((device.port, "http"),)
        found = f"http://{OWN}:{device.port}"
        assert await probe_web_ui(OWN, candidates) == found
        pin(NOBODYS_ADDRESS)
        assert await probe_web_ui(OWN, candidates) is None
        pin(PINNED)
        assert await probe_web_ui(OWN, candidates) == (found if REACHES else None)
        await asyncio.sleep(0.05)
    assert device.peers == [OWN, *BOUND_PEERS]


@needs_network
async def test_the_reconnect_probe_dials_from_the_pinned_address(pin):
    from openavc.core.device_manager import DeviceManager

    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    manager = DeviceManager(state, events)
    async with _Device(OWN) as device:
        drv = _driver(OWN)
        drv.config.update(port=device.port, transport="http")
        assert await manager._probe_reachable("acme1", drv)
        pin(NOBODYS_ADDRESS)
        assert not await manager._probe_reachable("acme1", drv)
        pin(PINNED)
        assert await manager._probe_reachable("acme1", drv) is REACHES
        await asyncio.sleep(0.05)
    assert device.peers == [OWN, *BOUND_PEERS]


@needs_network
async def test_the_equipment_drivers_remote_connection_leaves_from_the_pinned_address(
    pin, monkeypatch, tmp_path, self_signed,
):
    from unittest.mock import AsyncMock

    from aiohttp import web

    from openavc.drivers.equipment import EquipmentDriver

    remotes: list[str] = []

    async def description(request):
        remotes.append(request.remote)
        info = dict(EquipmentDriver.DRIVER_INFO, id="acme_equipment")
        info["state_variables"] = {}
        info["config_schema"] = {}
        return web.json_response({
            "protocol": "openavc-device-1", "identity": "unit",
            "session": "session", "capabilities": info,
        })

    app = web.Application()
    app.router.add_get("/v1/description", description)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, OWN, 0, ssl_context=self_signed)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr("openavc.drivers.equipment.get_data_dir", lambda: tmp_path)
    pin(PINNED)
    driver = EquipmentDriver("equipment", {
        "host": OWN, "port": port, "username": "engineer", "password": "fixture",
        "verify_ssl": False,
    }, StateStore(), EventBus())
    monkeypatch.setattr(driver, "_refresh", AsyncMock())
    try:
        with bound():
            await driver.connect()
    finally:
        await driver.disconnect()
        await runner.cleanup()
    assert remotes == BOUND_PEERS
