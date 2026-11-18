"""A transport's close ends its receive-handler runs.

A handler that awaits keeps its place, and the messages after it wait their
turn (``transport/handler_runs.py``). When the connection closes, the runs
still waiting, and the one still running, belong to a connection that is
gone: a device removed or re-added must not have its old values written back
by them. So every holder of the runs ends them at its close, except a run
that is itself closing the connection, which is left to finish. Pinned on an
invented device:

  - a device removed while its handler awaits never gets a later message's
    value back;
  - each holder (the tcp, serial, udp and ssh transports, an OSC device's
    feedback socket, and each kind of push listener subscription) ends both
    the waiting runs and the running one at its close, and so does each
    listener registry's close of everything;
  - a handler that closes its own connection is not cut short;
  - a run started and closed on the same loop turn leaves its handler closed,
    never un-awaited;
  - a message that reaches a closed holder is not handled, and a UDP socket
    opened again handles messages again.
"""

from __future__ import annotations

import asyncio
import gc
import warnings
from typing import Any, Awaitable, Callable

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.transport import multicast_listener, tcp_listener, udp_listener
from openavc.transport.handler_runs import HandlerRuns
from openavc.transport.osc import OSCTransport, _OSCListenProtocol
from openavc.transport.serial_transport import SerialTransport
from openavc.transport.ssh import SSHTransport
from openavc.transport.tcp import TCPTransport
from openavc.transport.udp import UDPTransport, _log_task_exception

Deliver = Callable[[bytes], None]
Close = Callable[[], Awaitable[None]]

_PEER = ("127.0.0.1", 9000)


# --- The device ---------------------------------------------------------


class AcmeAckingDriver(BaseDriver):
    """Holds each event until the test lets it go (the shape of a driver that
    acknowledges an event before applying it), and reports power at once."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_acks",
        "name": "Acme Widget (acknowledges events)",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 23},
        "commands": {},
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
            "events": {"type": "integer", "label": "Events"},
        },
        "config_schema": {},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.holding = asyncio.Event()
        self.release = asyncio.Event()

    async def on_data_received(self, data: bytes) -> None:
        if data == b"EVT":
            self.holding.set()
            await self.release.wait()
            self.set_state("events", 1)
        elif data == b"PWR on":
            self.set_state("power", "on")

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


async def test_a_removed_device_never_gets_a_later_messages_value_back():
    async def device(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"EVT\rPWR on\r")
        await writer.drain()
        await reader.read()  # until the platform hangs up
        writer.close()

    server = await asyncio.start_server(device, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    _DRIVER_REGISTRY["acme_widget_acks"] = AcmeAckingDriver
    try:
        await dm.add_device({
            "id": "d1", "driver": "acme_widget_acks", "name": "D1",
            "config": {"host": "127.0.0.1", "port": port},
        })
        driver = dm._devices["d1"]
        await asyncio.wait_for(driver.holding.wait(), timeout=2.0)

        await dm.remove_device("d1")
        driver.release.set()
        for _ in range(20):
            await asyncio.sleep(0.01)

        assert state.get("device.d1.power") is None
        assert state.get("device.d1.events") is None
    finally:
        _DRIVER_REGISTRY.pop("acme_widget_acks", None)
        server.close()
        await server.wait_closed()


async def test_a_dropped_connection_ends_its_handler_runs_too():
    """The cleanup after a drop closes the transport, so the messages still
    waiting behind an awaiting handler, and that handler, end there."""
    async def device(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"EVT\rPWR on\r")
        await writer.drain()
        await hang_up.wait()
        writer.close()

    hang_up = asyncio.Event()
    server = await asyncio.start_server(device, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dropped = asyncio.Event()

    async def on_disconnected(event: str, payload: Any = None) -> None:
        dropped.set()

    events.on("device.disconnected.d1", on_disconnected)
    driver = AcmeAckingDriver("d1", {"host": "127.0.0.1", "port": port}, state, events)
    try:
        await driver.connect()
        await asyncio.wait_for(driver.holding.wait(), timeout=2.0)

        hang_up.set()
        await asyncio.wait_for(dropped.wait(), timeout=2.0)
        driver.release.set()
        for _ in range(20):
            await asyncio.sleep(0.01)

        assert state.get("device.d1.power") is None
        assert state.get("device.d1.events") is None
    finally:
        await driver.disconnect()
        server.close()
        await server.wait_closed()


# --- Each holder --------------------------------------------------------


def _tcp(handler: Any) -> tuple[Deliver, Close]:
    t = TCPTransport(
        "127.0.0.1", 9, on_data=handler, on_disconnect=lambda: None,
        delimiter=b"\r", timeout=1.0, inter_command_delay=0.0, name="acme_widget",
    )
    return t._deliver_message, t.close


def _serial(handler: Any) -> tuple[Deliver, Close]:
    t = SerialTransport("COM9", 9600, on_data=handler, on_disconnect=lambda: None, name="acme_widget")
    return t._deliver_message, t.close


def _udp(handler: Any) -> tuple[Deliver, Close]:
    t = UDPTransport("127.0.0.1", 9, on_data=handler, name="acme_widget")
    return (lambda data: t._deliver_message(data, ("127.0.0.1", 9))), t.close


def _ssh(handler: Any) -> tuple[Deliver, Close]:
    t = SSHTransport("127.0.0.1", 22, "acme", on_data=handler, on_disconnect=lambda: None, name="acme_widget")
    return t._deliver, t.close


def _osc_feedback(handler: Any) -> tuple[Deliver, Close]:
    t = OSCTransport("127.0.0.1", 9, listen_port=9001, on_data=handler, name="acme_widget")
    protocol = _OSCListenProtocol(handler, "acme_widget", parent=t)
    t._listen_protocol = protocol
    return (lambda data: protocol.datagram_received(data, _PEER)), t.close


def _push_callback(handler: Any) -> Callable[[bytes, tuple[str, int]], Any]:
    return lambda data, _addr: handler(data)


def _tcp_push(handler: Any) -> tuple[Deliver, Close]:
    listener = tcp_listener._PortListener(0)
    sub = tcp_listener.TcpListenerSubscription(
        listener, {"127.0.0.1"}, _push_callback(handler), None, "acme_widget",
    )
    listener.subscriptions.append(sub)
    return (lambda data: listener._dispatch(sub, data, _PEER)), sub.close


def _udp_push(handler: Any) -> tuple[Deliver, Close]:
    listener = udp_listener._PortListener(0)
    sub = udp_listener.UdpListenerSubscription(
        listener, {"127.0.0.1"}, _push_callback(handler), "acme_widget",
    )
    listener.subscriptions.append(sub)
    return (lambda data: listener.deliver(data, _PEER)), sub.close


def _multicast_push(handler: Any) -> tuple[Deliver, Close]:
    listener = multicast_listener._PortListener(0)
    sub = multicast_listener.MulticastSubscription(
        listener, "239.255.0.1", {"127.0.0.1"}, _push_callback(handler), "acme_widget",
    )
    listener.subscriptions.append(sub)
    return (lambda data: listener.deliver(data, _PEER)), sub.close


def _registry_close_all(module: Any, listener: Any) -> Close:
    registry = module._Registry()
    registry._listeners[9000] = listener
    return registry.close_all


def _tcp_push_close_all(handler: Any) -> tuple[Deliver, Close]:
    listener = tcp_listener._PortListener(0)
    sub = tcp_listener.TcpListenerSubscription(
        listener, {"127.0.0.1"}, _push_callback(handler), None, "acme_widget",
    )
    listener.subscriptions.append(sub)
    return (
        (lambda data: listener._dispatch(sub, data, _PEER)),
        _registry_close_all(tcp_listener, listener),
    )


def _udp_push_close_all(handler: Any) -> tuple[Deliver, Close]:
    listener = udp_listener._PortListener(0)
    listener.subscriptions.append(udp_listener.UdpListenerSubscription(
        listener, {"127.0.0.1"}, _push_callback(handler), "acme_widget",
    ))
    return (lambda data: listener.deliver(data, _PEER)), _registry_close_all(udp_listener, listener)


def _multicast_push_close_all(handler: Any) -> tuple[Deliver, Close]:
    listener = multicast_listener._PortListener(0)
    listener.subscriptions.append(multicast_listener.MulticastSubscription(
        listener, "239.255.0.1", {"127.0.0.1"}, _push_callback(handler), "acme_widget",
    ))
    return (lambda data: listener.deliver(data, _PEER)), _registry_close_all(multicast_listener, listener)


HOLDERS = pytest.mark.parametrize(
    "holder",
    [
        _tcp, _serial, _udp, _ssh, _osc_feedback, _tcp_push, _udp_push, _multicast_push,
        _tcp_push_close_all, _udp_push_close_all, _multicast_push_close_all,
    ],
    ids=[
        "tcp", "serial", "udp", "ssh", "osc", "tcp_listener", "udp_listener", "multicast",
        "tcp_listener_close_all", "udp_listener_close_all", "multicast_close_all",
    ],
)


async def _turns(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


@HOLDERS
async def test_a_close_ends_the_running_handler_and_the_ones_waiting(holder: Any) -> None:
    holding = asyncio.Event()
    release = asyncio.Event()
    applied: list[bytes] = []

    async def handler(data: bytes) -> None:
        if data == b"one":
            holding.set()
            await release.wait()
        applied.append(data)

    deliver, close = holder(handler)
    deliver(b"one")
    deliver(b"two")
    deliver(b"three")
    await asyncio.wait_for(holding.wait(), timeout=1.0)

    await close()
    release.set()
    await _turns()

    assert applied == []


@HOLDERS
async def test_a_handler_that_closes_its_own_connection_is_not_cut_short(holder: Any) -> None:
    applied: list[bytes] = []
    closer: dict[str, Close] = {}

    async def handler(data: bytes) -> None:
        if data == b"bye":
            await closer["close"]()
            await asyncio.sleep(0)
        applied.append(data)

    deliver, close = holder(handler)
    closer["close"] = close
    deliver(b"bye")
    deliver(b"after")
    await _turns()

    assert applied == [b"bye"]


# --- The runs themselves ------------------------------------------------


async def test_runs_closed_on_the_turn_they_were_started_leave_their_handlers_closed():
    started: list[bytes] = []

    async def handler(data: bytes) -> None:
        started.append(data)

    runs = HandlerRuns(_log_task_exception)
    first, second = handler(b"one"), handler(b"two")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runs.run(first)
        runs.run(second)
        runs.close()
        await _turns()
        gc.collect()

    assert started == []
    assert first.cr_frame is None and second.cr_frame is None  # closed, never started
    assert runs.tasks == set()
    assert not [w for w in caught if issubclass(w.category, RuntimeWarning)]


async def test_a_message_that_reaches_closed_runs_is_not_handled():
    started: list[bytes] = []

    async def handler(data: bytes) -> None:
        started.append(data)

    runs = HandlerRuns(_log_task_exception)
    runs.close()
    late = handler(b"late")
    runs.run(late)
    await _turns()

    assert started == []
    assert late.cr_frame is None
    assert runs.tasks == set()


async def test_a_udp_socket_opened_again_handles_messages_again():
    applied: list[bytes] = []

    async def handler(data: bytes) -> None:
        applied.append(data)

    t = UDPTransport("127.0.0.1", 9, on_data=handler, name="acme_widget")
    await t.open(local_addr="127.0.0.1")
    await t.close()
    await t.open(local_addr="127.0.0.1")
    try:
        t._deliver_message(b"again", ("127.0.0.1", 9))
        await _turns()
    finally:
        await t.close()

    assert applied == [b"again"]


async def test_a_closed_multicast_subscription_is_not_delivered_to():
    applied: list[bytes] = []
    listener = multicast_listener._PortListener(0)
    sub = multicast_listener.MulticastSubscription(
        listener, "239.255.0.1", {"127.0.0.1"},
        lambda data, _addr: applied.append(data), "acme_widget",
    )
    listener.subscriptions.append(sub)
    sub._closed = True  # closing: detached from the listener once its lock is free

    listener.deliver(b"late", _PEER)

    assert applied == []
