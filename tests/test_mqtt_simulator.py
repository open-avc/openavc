"""End-to-end tests for the MQTT transport against the MQTT broker simulator.

Exercises the whole MQTT stack with the real gmqtt-backed transport talking to
the real broker simulator: CONNECT/auth, SUBSCRIBE, a PUBLISH the device reacts
to, and an unsolicited broadcast pushed back. Uses an invented device
("acme_mqtt") and synthetic topics — this validates the platform MQTT
capability, not any specific driver.
"""

from __future__ import annotations

import asyncio

import pytest

from openavc.transport.mqtt import MQTTTransport
from openavc.simulator.mqtt_simulator import MQTTSimulator


class _AcmeBroker(MQTTSimulator):
    """A tiny invented MQTT device: accepts acme/secret, echoes a volume
    command back as a state broadcast."""

    SIMULATOR_INFO = {
        "driver_id": "acme_mqtt",
        "name": "Acme MQTT Sim",
        "category": "generic",
        "transport": "mqtt",
        "default_port": 1883,
    }

    def authenticate(self, username, password):
        if username == b"acme" and password == b"secret":
            return 0
        return 5  # not authorized

    async def on_publish(self, client_id, topic, payload):
        if topic == "acme/cmd/volume":
            self.set_state("volume", int(payload))
            await self.broadcast("acme/state/volume", payload)


class _AcmeTlsBroker(_AcmeBroker):
    SIMULATOR_INFO = {**_AcmeBroker.SIMULATOR_INFO, "driver_id": "acme_mqtt_tls", "tls": True}


async def _start(sim: MQTTSimulator) -> int:
    """Start a broker on an OS-assigned port and return the real port."""
    await sim.start(0)
    return sim._server.sockets[0].getsockname()[1]


async def test_connect_subscribe_publish_broadcast_roundtrip():
    sim = _AcmeBroker("acme1")
    port = await _start(sim)
    received: asyncio.Queue = asyncio.Queue()

    try:
        t = await MQTTTransport.create(
            "127.0.0.1", port, username="acme", password="secret",
            on_message=lambda topic, payload: received.put_nowait((topic, payload)),
        )
        await t.subscribe("acme/state/#")
        await asyncio.sleep(0.1)  # let the SUBSCRIBE register

        await t.publish("acme/cmd/volume", b"42")

        topic, payload = await asyncio.wait_for(received.get(), timeout=2.0)
        assert topic == "acme/state/volume"
        assert payload == b"42"
        # The broker reacted to the command.
        assert sim.get_state("volume") == 42

        await t.close()
    finally:
        await sim.stop()


async def test_auth_rejection_raises_connection_error():
    sim = _AcmeBroker("acme2")
    port = await _start(sim)
    try:
        with pytest.raises(ConnectionError):
            await MQTTTransport.create(
                "127.0.0.1", port, username="acme", password="wrong",
            )
    finally:
        await sim.stop()


async def test_tls_roundtrip_against_self_signed_broker():
    sim = _AcmeTlsBroker("acme_tls1")
    port = await _start(sim)
    received: asyncio.Queue = asyncio.Queue()
    try:
        # Same posture a TLS device driver uses: TLS on, verification off
        # (self-signed broker cert) — exactly the Hisense VIDAA scenario.
        t = await MQTTTransport.create(
            "127.0.0.1", port, username="acme", password="secret",
            use_tls=True, verify_ssl=False,
            on_message=lambda topic, payload: received.put_nowait((topic, payload)),
        )
        await t.subscribe("acme/state/#")
        await asyncio.sleep(0.1)
        await t.publish("acme/cmd/volume", b"7")
        topic, payload = await asyncio.wait_for(received.get(), timeout=2.0)
        assert (topic, payload) == ("acme/state/volume", b"7")
        await t.close()
    finally:
        await sim.stop()
