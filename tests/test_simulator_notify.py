"""A simulated device's unsolicited messages reach the driver on every transport.

Two doors emit them: a ``notifications:`` template when state changes, and a
script handler's ``notify(text)`` — the frame a real device sends *after*
acknowledging a write (a change notice, a subscription update). Both go
through one delivery path, so a device with no dedicated push channel pushes
on its control link: every connected TCP client, or the last UDP peer.

Invented device (``acme_*``) and synthetic frames throughout: this is the
simulator machinery under test, not any specific driver.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator

_BASE = {
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "author": "Test",
    "description": "Invented device for notification tests",
    "source_url": "https://example.com",
    "config_schema": {"host": {"type": "string", "required": True}},
    "default_config": {"host": "", "port": 5000},
    "state_variables": {
        "mute": {"type": "boolean", "label": "Mute"},
        "level": {"type": "integer", "label": "Level", "min": 0, "max": 100},
    },
    "commands": {
        "mute_on": {"send": "MUTE 1", "label": "Mute On", "sets": {"mute": True}},
        "set_level": {
            "send": "LEVEL {level}",
            "label": "Set Level",
            "params": {"level": {"type": "integer", "min": 0, "max": 100}},
        },
    },
    "responses": [
        {"match": r"^MUTE ([01])$", "set": {"mute": "$1"}},
        {"match": r"^LEVEL (\d+)$", "set": {"level": "$1"}},
    ],
    "simulator": {
        "initial_state": {"mute": False, "level": 50},
        "notifications": {
            # A value-specific template, the shape a boolean needs.
            "mute": {"true": "NOTIFY MUTE 1", "false": "NOTIFY MUTE 0"},
        },
        "command_handlers": [
            {
                "match": r"LEVEL (\d+)",
                "handler": (
                    "state['level'] = int(match.group(1))\n"
                    "respond('OK LEVEL\\r')\n"
                    "notify('NOTIFY LEVEL %d\\r' % state['level'])\n"
                ),
            },
        ],
    },
}


def _definition(transport: str) -> dict:
    return {
        **_BASE,
        "id": f"acme_{transport}_amp",
        "name": f"Acme {transport.upper()} Amp",
        "transport": transport,
        "delimiter": "\\r",
    }


def _free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_notify_is_in_the_script_handler_namespace_and_answers_first():
    """A handler that acknowledges and then notifies still returns its
    acknowledgement — the notice travels on the push path, not the reply."""
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition("tcp"))
    reply = sim.handle_command(b"LEVEL 42\r")
    assert reply is not None and b"OK LEVEL" in reply
    assert sim.get_state("level") == 42


@pytest.mark.asyncio
async def test_udp_simulator_pushes_to_the_last_peer():
    """A UDP device can only push to a peer it has heard from: both a
    ``notifications:`` template and a handler's ``notify()`` land on the
    last sender's socket."""
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition("udp"))
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
            # The handler's notify() follows its reply.
            transport.sendto(b"LEVEL 7\r")
            frames = {await asyncio.wait_for(recv.get(), timeout=3.0) for _ in range(2)}
            assert b"OK LEVEL\r" in frames
            assert b"NOTIFY LEVEL 7\r\r" in frames

            # A state change from outside the protocol (the Simulator UI)
            # renders the template and pushes it to the same peer.
            sim.set_state("mute", True)
            pushed = await asyncio.wait_for(recv.get(), timeout=3.0)
            assert pushed == b"NOTIFY MUTE 1\r"
        finally:
            transport.close()
    finally:
        await sim.stop()


@pytest.mark.asyncio
async def test_tcp_simulator_notify_reaches_every_connected_client():
    sim = YAMLAutoSimulator("dev1", config={}, driver_def=_definition("tcp"))
    port = _free_port(socket.SOCK_STREAM)
    await sim.start(port)
    try:
        r1, w1 = await asyncio.open_connection("127.0.0.1", port)
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        try:
            await asyncio.sleep(0.05)  # both registered before the write
            w1.write(b"LEVEL 9\r")
            await w1.drain()
            reply = await asyncio.wait_for(r1.readuntil(b"\r"), timeout=3.0)
            assert reply == b"OK LEVEL\r"
            notice_1 = await asyncio.wait_for(r1.readuntil(b"\r"), timeout=3.0)
            notice_2 = await asyncio.wait_for(r2.readuntil(b"\r"), timeout=3.0)
            assert notice_1 == b"NOTIFY LEVEL 9\r"
            assert notice_2 == b"NOTIFY LEVEL 9\r"
        finally:
            w1.close()
            w2.close()
    finally:
        await sim.stop()
