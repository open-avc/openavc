"""Every simulated device can be made to stop responding.

Two halves. Every simulator offers a "Device stops responding" error mode
(``communication_timeout``, behaviour ``no_response``) whether or not its
driver declares one, so a liveness or reconnect test never needs the network
drop controls; a driver that declares its own mode with that name, or any
``no_response`` mode, keeps its own instead. And while such a mode is active
the device sends NOTHING: no reply, no greeting or sign-in prompt, no push on
any channel. A platform that counts any inbound byte as proof of life would
otherwise read a hung device as alive.

Invented devices (``acme_*``) and synthetic payloads throughout: this is the
platform's simulator machinery under test, not any specific driver.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import websockets

from openavc.simulator.http_simulator import HTTPSimulator
from openavc.simulator.mqtt_simulator import MQTTSimulator
from openavc.simulator.tcp_simulator import TCPSimulator
from openavc.simulator.websocket_simulator import WebSocketSimulator
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport.osc_codec import osc_encode_message

BUILT_IN = "communication_timeout"

#: How long "nothing arrived" is waited for. Every send under test is local
#: and immediate when it happens, so this is generous.
QUIET = 0.4


_BASE = {
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "author": "Test",
    "description": "Invented device for stops-responding tests",
    "source_url": "https://example.com",
    "config_schema": {"host": {"type": "string", "required": True}},
    "default_config": {"host": "", "port": 5000},
    "state_variables": {
        "mute": {"type": "boolean", "label": "Mute"},
    },
    "commands": {
        "mute_on": {"send": "MUTE 1", "label": "Mute On", "sets": {"mute": True}},
    },
    "responses": [
        {"match": r"^MUTE ([01])$", "set": {"mute": "$1"}},
    ],
}


def _definition(transport: str, simulator: dict | None = None, **extra) -> dict:
    return {
        **_BASE,
        "id": f"acme_{transport}_amp",
        "name": f"Acme {transport.upper()} Amp",
        "transport": transport,
        "delimiter": "\\r",
        "simulator": {
            "initial_state": {"mute": False},
            "push_state": True,
            "notifications": {
                "mute": {"true": "NOTIFY MUTE 1", "false": "NOTIFY MUTE 0"},
            },
            **(simulator or {}),
        },
        **extra,
    }


class _AcmeGreeter(TCPSimulator):
    """Sends a banner on connect and answers every line with OK."""

    SIMULATOR_INFO = {
        "driver_id": "acme_greeter",
        "name": "Acme Greeter",
        "transport": "tcp",
        "delimiter": "\r",
        "initial_state": {},
    }

    async def on_client_connected(self, client_id: str) -> bytes | None:
        return b"HELLO\r"

    def handle_command(self, data: bytes) -> bytes | None:
        return b"OK\r"


async def _nothing_from(reader: asyncio.StreamReader) -> None:
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader.read(1), timeout=QUIET)


async def _nothing_from_queue(queue: asyncio.Queue) -> None:
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=QUIET)


# ── The mode every simulator offers ─────────────────────────────────────────


def test_a_simulator_that_declares_no_error_modes_still_offers_it():
    sim = _AcmeGreeter("dev1")
    assert sim.available_errors[BUILT_IN]["behavior"] == "no_response"
    assert sim.to_info_dict()["available_errors"] == {
        BUILT_IN: {"description": "Device stops responding", "builtin": True},
    }


def test_a_yaml_driver_without_error_modes_offers_it():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition("tcp"))
    assert BUILT_IN in sim.to_info_dict()["available_errors"]


def test_declared_modes_are_listed_first_and_the_built_in_last():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition(
        "tcp", {"error_modes": {
            "amp_fault": {"description": "Amp fault", "set_state": {"mute": True}},
        }},
    ))
    listed = sim.to_info_dict()["available_errors"]
    assert list(listed) == ["amp_fault", BUILT_IN]
    assert "builtin" not in listed["amp_fault"]


def test_a_declared_mode_of_the_same_name_wins():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition(
        "tcp", {"error_modes": {
            BUILT_IN: {"behavior": "no_response", "description": "Amp stops responding"},
        }},
    ))
    assert sim.to_info_dict()["available_errors"] == {
        BUILT_IN: {"description": "Amp stops responding"},
    }


def test_a_declared_no_response_mode_under_another_name_replaces_it():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition(
        "tcp", {"error_modes": {
            "hung": {"behavior": "no_response", "description": "Amp hangs"},
        }},
    ))
    assert list(sim.to_info_dict()["available_errors"]) == ["hung"]
    sim.inject_error(BUILT_IN)          # not offered, so ignored
    assert sim.active_errors == set()


def test_the_built_in_mode_injects_and_clears_like_a_declared_one():
    sim = _AcmeGreeter("dev1")
    sim.inject_error(BUILT_IN)
    assert sim.active_errors == {BUILT_IN}
    assert sim.has_error_behavior("no_response") is True
    assert sim.to_info_dict()["active_errors"] == [BUILT_IN]
    sim.clear_error(BUILT_IN)
    assert sim.has_error_behavior("no_response") is False


# ── TCP: replies, greeting, sign-in, pushes ─────────────────────────────────


async def test_tcp_greeting_and_replies_wait_until_the_device_answers_again():
    sim = _AcmeGreeter("dev1")
    await sim.start(0)
    try:
        sim.inject_error(BUILT_IN)
        reader, writer = await asyncio.open_connection("127.0.0.1", sim.port)
        try:
            await _nothing_from(reader)             # no banner
            writer.write(b"PING\r")
            await writer.drain()
            await _nothing_from(reader)             # no reply
            assert any(e["direction"] == "in" for e in sim.get_protocol_log())

            sim.clear_error(BUILT_IN)
            assert await asyncio.wait_for(reader.readuntil(b"\r"), 3) == b"HELLO\r"
            writer.write(b"PING\r")
            await writer.drain()
            assert await asyncio.wait_for(reader.readuntil(b"\r"), 3) == b"OK\r"
        finally:
            writer.close()
    finally:
        await sim.stop()


async def test_tcp_sign_in_prompt_waits_until_the_device_answers_again():
    definition = {
        **_BASE,
        "id": "acme_secure",
        "name": "Acme Secure",
        "transport": "tcp",
        "auth": {
            "type": "telnet_login",
            "username_prompt": "login: ",
            "password_prompt": "Password: ",
            "line_ending": "\r\n",
            "timeout_seconds": 2,
        },
    }
    sim = YAMLAutoSimulator("dev1", config={"username": "admin"}, driver_def=definition)
    await sim.start(0)
    try:
        sim.inject_error(BUILT_IN)
        reader, writer = await asyncio.open_connection("127.0.0.1", sim.port)
        try:
            await _nothing_from(reader)
            sim.clear_error(BUILT_IN)
            assert await asyncio.wait_for(reader.readuntil(b": "), 3) == b"login: "
        finally:
            writer.close()
    finally:
        await sim.stop()


async def test_tcp_pushes_stop_on_every_path():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition("tcp"))
    await sim.start(0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", sim.port)
        try:
            await asyncio.sleep(0.05)               # registered before the push
            sim.inject_error(BUILT_IN)
            sim.set_state("mute", True)             # push_state + notification
            await sim.push(b"RAW\r")
            for client_id in list(sim._clients):
                await sim.push_to(client_id, b"RAW\r")
            await _nothing_from(reader)

            sim.clear_error(BUILT_IN)
            sim.set_state("mute", False)
            pushed = await asyncio.wait_for(reader.readuntil(b"\r"), 3)
            assert b"MUTE" in pushed
        finally:
            writer.close()
    finally:
        await sim.stop()


async def test_dial_back_and_multicast_notifications_stop():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition("tcp"))
    dialed: list[bytes] = []

    async def on_conn(reader, writer):
        dialed.append(await reader.read())
        writer.close()

    server = await asyncio.start_server(on_conn, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class _Socket:
        sent: list[bytes] = []

        def sendto(self, data, addr):
            self.sent.append(data)

        def close(self):
            pass

    try:
        sim.inject_error(BUILT_IN)
        sim._push_tcp_subscribers[("127.0.0.1", port)] = 0
        sim._emit_push_tcp(b"NOTIFY\r")
        await asyncio.gather(*sim._push_tcp_tasks, return_exceptions=True)
        await asyncio.sleep(QUIET)
        assert dialed == []

        sim._push_multicast = ("239.0.0.100", 17000)
        sim._mcast_sock = _Socket()
        sim._deliver_notification("NOTIFY")
        assert sim._mcast_sock.sent == []
    finally:
        server.close()
        await sim.stop()


# ── Datagram transports: replies and pushes ─────────────────────────────────


async def _datagram_client(port: int) -> tuple[asyncio.DatagramTransport, asyncio.Queue]:
    loop = asyncio.get_running_loop()
    recv: asyncio.Queue = asyncio.Queue()

    class _Client(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            recv.put_nowait(data)

    transport, _ = await loop.create_datagram_endpoint(
        _Client, remote_addr=("127.0.0.1", port)
    )
    return transport, recv


async def test_udp_pushes_stop():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition("udp"))
    await sim.start(0)
    try:
        transport, recv = await _datagram_client(sim.port)
        try:
            transport.sendto(b"MUTE 1\r")           # the device learns its peer
            await asyncio.wait_for(recv.get(), 3)
            await asyncio.sleep(0.1)
            while not recv.empty():                 # its notice of the change
                recv.get_nowait()
            sim.inject_error(BUILT_IN)
            sim.set_state("mute", False)
            sim._deliver_notification("NOTIFY")
            sim.send_datagram(b"REPLY\r", sim._last_client_addr)
            await _nothing_from_queue(recv)
        finally:
            transport.close()
    finally:
        await sim.stop()


async def test_osc_pushes_stop():
    definition = {
        **_BASE,
        "id": "acme_osc_mixer",
        "name": "Acme OSC Mixer",
        "transport": "osc",
        "default_config": {"host": "", "port": 10023},
        "state_variables": {
            "main_fader": {"type": "number", "label": "Main Fader", "min": 0, "max": 1},
        },
        "commands": {},
        "responses": [
            {"address": "/main/fader",
             "mappings": [{"state": "main_fader", "arg": 0, "type": "float"}]},
        ],
        "simulator": {"push_state": True},
    }
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=definition)
    await sim.start(0)
    try:
        transport, recv = await _datagram_client(sim.port)
        try:
            transport.sendto(osc_encode_message("/main/fader", [("f", 0.5)]))
            await asyncio.wait_for(recv.get(), 3)
            sim.inject_error(BUILT_IN)
            sim.set_state("main_fader", 0.25)
            await sim.push_message("/main/fader", [("f", 0.75)])
            await _nothing_from_queue(recv)
        finally:
            transport.close()
    finally:
        await sim.stop()


# ── HTTP: event streams and callbacks ───────────────────────────────────────


class _AcmeStream(HTTPSimulator):
    SIMULATOR_INFO = {
        "driver_id": "acme_stream",
        "name": "Acme Stream",
        "transport": "http",
        "initial_state": {},
    }
    sse_paths = ["/events"]

    def handle_request(self, method, path, headers, body):
        return 200, {"ok": True}


async def test_an_open_event_stream_gets_no_events():
    sim = _AcmeStream("dev1")
    await sim.start(0)
    try:
        frames: asyncio.Queue = asyncio.Queue()

        async def listen():
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET", f"http://127.0.0.1:{sim.port}/events",
                    headers={"Accept": "text/event-stream"},
                ) as resp:
                    async for chunk in resp.aiter_text():
                        frames.put_nowait(chunk)

        task = asyncio.ensure_future(listen())
        try:
            for _ in range(100):
                if sim._sse_clients:
                    break
                await asyncio.sleep(0.02)
            assert sim._sse_clients
            sim.inject_error(BUILT_IN)
            sim.push_sse_event('{"mute": true}')
            await _nothing_from_queue(frames)
        finally:
            task.cancel()
    finally:
        await sim.stop()


async def test_an_event_stream_subscription_is_not_answered():
    sim = _AcmeStream("dev1")
    await sim.start(0)
    try:
        sim.inject_error(BUILT_IN)
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.TimeoutException):
                await client.get(
                    f"http://127.0.0.1:{sim.port}/events",
                    headers={"Accept": "text/event-stream"},
                    timeout=QUIET,
                )
        assert not sim._sse_clients
    finally:
        await sim.stop()


async def test_http_callbacks_are_not_delivered():
    sim = _AcmeStream("dev1")
    connections: list[bool] = []

    async def on_conn(reader, writer):
        connections.append(True)
        writer.close()

    server = await asyncio.start_server(on_conn, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        sim.inject_error(BUILT_IN)
        status = await sim.post_http_callback(f"http://127.0.0.1:{port}/cb", "{}")
        await asyncio.sleep(0.1)
        assert status is None
        assert connections == []
    finally:
        server.close()


# ── WebSocket and MQTT ──────────────────────────────────────────────────────


class _AcmeSocket(WebSocketSimulator):
    SIMULATOR_INFO = {
        "driver_id": "acme_socket",
        "name": "Acme Socket",
        "transport": "tcp",
        "default_port": 0,
        "initial_state": {},
    }

    async def handle_message(self, client, message: str) -> None:
        await self.send(client, f"echo:{message}")


async def test_websocket_pushes_stop_and_a_new_session_is_not_answered():
    sim = _AcmeSocket("dev1")
    await sim.start(0)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{sim.port}", open_timeout=3) as ws:
            sim.inject_error(BUILT_IN)
            await sim.broadcast("push")
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), QUIET)
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            async with websockets.connect(
                f"ws://127.0.0.1:{sim.port}", open_timeout=QUIET,
            ):
                pass
    finally:
        await sim.stop()


class _AcmeBroker(MQTTSimulator):
    SIMULATOR_INFO = {
        "driver_id": "acme_mqtt",
        "name": "Acme MQTT",
        "transport": "mqtt",
        "default_port": 1883,
    }


#: MQTT 3.1.1 CONNECT, clean session, keep-alive 60 s, client id "t".
_CONNECT = bytes.fromhex("100d00044d5154540402003c000174")


async def test_mqtt_broker_does_not_acknowledge_a_connect():
    sim = _AcmeBroker("dev1")
    await sim.start(0)
    try:
        sim.inject_error(BUILT_IN)
        reader, writer = await asyncio.open_connection("127.0.0.1", sim.port)
        try:
            writer.write(_CONNECT)
            await writer.drain()
            await _nothing_from(reader)
        finally:
            writer.close()

        sim.clear_error(BUILT_IN)
        reader, writer = await asyncio.open_connection("127.0.0.1", sim.port)
        try:
            writer.write(_CONNECT)
            await writer.drain()
            connack = await asyncio.wait_for(reader.readexactly(4), 3)
            assert connack[0] == 0x20
        finally:
            writer.close()
    finally:
        await sim.stop()
