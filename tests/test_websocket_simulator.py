"""Tests for the WebSocketSimulator base (a platform capability).

Exercises the base with an invented echo device — request/response over a
WebSocket, device-initiated broadcast push, state mutation, plain-TCP
reachability (what a self-managed driver's _verify_reachable does), and the
non-WebSocket GET fallback. No real device or driver is named.
"""

from __future__ import annotations

import asyncio
import socket

import websockets

from simulator.websocket_simulator import WebSocketSimulator


class _EchoWSSimulator(WebSocketSimulator):
    """Invented device: echoes each frame, counts them, and pushes on change."""

    SIMULATOR_INFO = {
        "driver_id": "acme_ws_echo",
        "name": "Acme WS Echo",
        "transport": "tcp",
        "default_port": 0,
        "initial_state": {"count": 0},
    }

    async def handle_message(self, client, message: str) -> None:
        await self.send(client, f"echo:{message}")
        self.set_state("count", self.get_state("count", 0) + 1)
        await self.broadcast(f"push:{self.get_state('count')}")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _started_sim():
    sim = _EchoWSSimulator("dev1")
    await sim.start(_free_port())
    return sim


async def test_request_response_and_state():
    sim = await _started_sim()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{sim.port}", open_timeout=3) as ws:
            await ws.send("hello")
            frames = {await asyncio.wait_for(ws.recv(), 2) for _ in range(2)}
        assert "echo:hello" in frames
        assert "push:1" in frames
        assert sim.get_state("count") == 1
    finally:
        await sim.stop()


async def test_broadcast_reaches_every_client():
    sim = await _started_sim()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{sim.port}", open_timeout=3) as a, \
                websockets.connect(f"ws://127.0.0.1:{sim.port}", open_timeout=3) as b:
            # Drain the connect races, then have A trigger a broadcast.
            await a.send("x")
            # A sees its own echo plus the push; B sees only the push.
            a_frames = {await asyncio.wait_for(a.recv(), 2) for _ in range(2)}
            b_frame = await asyncio.wait_for(b.recv(), 2)
        assert "echo:x" in a_frames
        assert any(f.startswith("push:") for f in a_frames)
        assert b_frame.startswith("push:")
    finally:
        await sim.stop()


async def test_plain_tcp_connect_is_reachable():
    # A self-managed driver's _verify_reachable() opens a bare TCP connection.
    sim = await _started_sim()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", sim.port), 3
        )
        writer.close()
    finally:
        await sim.stop()


async def test_non_websocket_get_returns_426():
    sim = await _started_sim()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", sim.port)
        writer.write(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        await writer.drain()
        status = await asyncio.wait_for(reader.readline(), 3)
        assert b"426" in status
        writer.close()
    finally:
        await sim.stop()


async def test_stop_closes_clients():
    sim = await _started_sim()
    async with websockets.connect(f"ws://127.0.0.1:{sim.port}", open_timeout=3) as ws:
        await ws.send("hi")
        # "hi" yields two frames (echo + push); drain both so neither is left
        # buffered to satisfy the post-stop recv below.
        await asyncio.wait_for(ws.recv(), 2)
        await asyncio.wait_for(ws.recv(), 2)
        await sim.stop()
        # The server closed the session; the next recv raises ConnectionClosed.
        with_error = False
        try:
            await asyncio.wait_for(ws.recv(), 2)
        except (websockets.ConnectionClosed, asyncio.TimeoutError):
            with_error = True
        assert with_error
    assert not sim.running
