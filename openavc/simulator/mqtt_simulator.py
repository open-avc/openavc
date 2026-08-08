"""
MQTTSimulator — a minimal MQTT 3.1.1 broker for device simulators.

Every other simulator base (TCP/HTTP/UDP/OSC) models a device the driver sends
requests TO. MQTT inverts that: the device *is* a broker, and the driver
connects to it, SUBSCRIBEs to topics, and PUBLISHes commands. So this base is an
actual (tiny, QoS-0) MQTT broker — it accepts CONNECT/SUBSCRIBE/PUBLISH/
PINGREQ, tracks per-client subscriptions, and can push messages back on
subscribed topics.

Subclass it for a specific device (a paired ``_sim.py``):
    - override ``authenticate(username, password)`` to gate CONNECT,
    - override ``on_publish(client_id, topic, payload)`` to react to commands,
    - call ``broadcast(topic, payload)`` / ``publish_to(client_id, topic,
      payload)`` to emit the unsolicited state messages a real device pushes.

Optional TLS: set ``"tls": True`` in SIMULATOR_INFO (or config) and the broker
generates an ephemeral self-signed cert and serves over TLS, accepting (but not
requiring) a client certificate. This lets a driver that always uses TLS — like
the Hisense VIDAA TVs, which serve a self-signed broker cert and want a client
cert — connect against the simulator exactly as it would against the real
device. The cert machinery is shared with the other server bases in
``openavc/simulator/self_signed_tls.py``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from openavc.simulator.base import BaseSimulator
from openavc.simulator.self_signed_tls import build_optional_tls, remove_cert_files

logger = logging.getLogger(__name__)

# MQTT control packet types (high nibble of the fixed header byte).
_CONNECT = 1
_CONNACK = 2
_PUBLISH = 3
_PUBACK = 4
_SUBSCRIBE = 8
_SUBACK = 9
_UNSUBSCRIBE = 10
_UNSUBACK = 11
_PINGREQ = 12
_PINGRESP = 13
_DISCONNECT = 14


def _encode_remaining_length(n: int) -> bytes:
    """Encode an MQTT variable-length integer (remaining length)."""
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n > 0:
            byte |= 0x80
        out.append(byte)
        if n == 0:
            break
    return bytes(out)


def _encode_string(s: str | bytes) -> bytes:
    """Encode an MQTT UTF-8 string: 2-byte big-endian length prefix + bytes."""
    if isinstance(s, str):
        s = s.encode("utf-8")
    return len(s).to_bytes(2, "big") + s


def _read_string(buf: bytes, off: int) -> tuple[bytes, int]:
    """Read a length-prefixed MQTT string from ``buf`` at ``off``."""
    length = int.from_bytes(buf[off:off + 2], "big")
    off += 2
    value = buf[off:off + length]
    return value, off + length


def _topic_matches(topic_filter: str, topic: str) -> bool:
    """MQTT topic-filter match supporting ``+`` and ``#`` wildcards."""
    if topic_filter == topic:
        return True
    f_parts = topic_filter.split("/")
    t_parts = topic.split("/")
    for i, fp in enumerate(f_parts):
        if fp == "#":
            return True
        if i >= len(t_parts):
            return False
        if fp == "+":
            continue
        if fp != t_parts[i]:
            return False
    return len(f_parts) == len(t_parts)


class MQTTSimulator(BaseSimulator):
    """Minimal MQTT 3.1.1 broker. Subclass and override the hooks below."""

    SIMULATOR_INFO = {
        "driver_id": "generic_mqtt",
        "name": "MQTT Broker Simulator",
        "category": "generic",
        "transport": "mqtt",
        "default_port": 1883,
    }

    def __init__(self, device_id: str, config: dict | None = None):
        super().__init__(device_id, config)
        self._server: asyncio.Server | None = None
        # conn_id -> writer, conn_id -> {filter: qos}, conn_id -> metadata
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._subscriptions: dict[str, dict[str, int]] = {}
        self._client_meta: dict[str, dict] = {}
        self._tls_files: tuple[str, str] | None = None

    # ── Override points for subclasses ──

    def authenticate(self, username: bytes | None, password: bytes | None) -> int:
        """Return the CONNACK return code for these credentials.

        0 accepts; 4 = bad username/password; 5 = not authorized. Default
        accepts everything. Override to model a device that checks credentials.
        """
        return 0

    async def on_client_connected(self, client_id: str) -> None:
        """Called after a client's CONNECT is accepted. Override to push a
        device greeting/state. Default: no-op."""

    async def on_subscribe(self, client_id: str, topic: str) -> None:
        """Called when a client subscribes to ``topic``. Default: no-op."""

    async def on_publish(self, client_id: str, topic: str, payload: bytes) -> None:
        """Called when a client publishes ``payload`` to ``topic``.

        This is the main hook: a device subclass inspects the command topic and
        reacts (updates state, pushes a broadcast). Default: no-op.
        """

    # ── Push helpers for subclasses ──

    async def publish_to(self, client_id: str, topic: str, payload: bytes | str) -> None:
        """Send a PUBLISH to one specific client."""
        writer = self._clients.get(client_id)
        if writer is None:
            return
        await self._send_publish(client_id, writer, topic, payload)

    async def broadcast(self, topic: str, payload: bytes | str) -> None:
        """Send a PUBLISH to every client subscribed to a matching filter."""
        for client_id, subs in list(self._subscriptions.items()):
            if any(_topic_matches(f, topic) for f in subs):
                writer = self._clients.get(client_id)
                if writer is not None:
                    await self._send_publish(client_id, writer, topic, payload)

    # ── Lifecycle ──

    async def start(self, port: int) -> None:
        self._port = port
        ssl_ctx, self._tls_files = build_optional_tls(
            self.SIMULATOR_INFO, self.config, self.name
        )
        self._server = await asyncio.start_server(
            self._handle_client, host="127.0.0.1", port=port, ssl=ssl_ctx,
        )
        self._running = True
        logger.info(
            "%s started on port %d (driver: %s, tls=%s)",
            self.name, port, self.driver_id, ssl_ctx is not None,
        )

    async def stop(self) -> None:
        self._running = False
        self._cancel_state_machine_timers()
        for writer in list(self._clients.values()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()
        self._subscriptions.clear()
        self._client_meta.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        remove_cert_files(self._tls_files)
        self._tls_files = None
        logger.info("%s stopped", self.name)

    # ── Connection handling ──

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        client_id = uuid.uuid4().hex[:8]
        peer = writer.get_extra_info("peername")
        logger.info("%s: client connecting from %s (id=%s)", self.name, peer, client_id)
        try:
            while self._running:
                packet = await self._read_packet(reader)
                if packet is None:
                    break
                ptype, flags, body = packet
                if ptype == _CONNECT:
                    if not await self._handle_connect(client_id, writer, body):
                        break
                elif ptype == _PUBLISH:
                    await self._handle_publish(client_id, flags, body)
                elif ptype == _SUBSCRIBE:
                    await self._handle_subscribe(client_id, writer, body)
                elif ptype == _UNSUBSCRIBE:
                    await self._handle_unsubscribe(client_id, writer, body)
                elif ptype == _PINGREQ:
                    await self._write(client_id, writer, bytes([_PINGRESP << 4, 0]))
                elif ptype == _DISCONNECT:
                    break
                # Other packet types (PUBACK from client, etc.) are ignored.
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        except Exception:
            logger.exception("%s: client %s error", self.name, client_id)
        finally:
            self._clients.pop(client_id, None)
            self._subscriptions.pop(client_id, None)
            self._client_meta.pop(client_id, None)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("%s: client disconnected (id=%s)", self.name, client_id)

    async def _handle_connect(
        self, client_id: str, writer: asyncio.StreamWriter, body: bytes
    ) -> bool:
        off = 0
        _proto, off = _read_string(body, off)  # "MQTT" / "MQIsdp"
        off += 1  # protocol level
        connect_flags = body[off]
        off += 1
        off += 2  # keepalive
        mqtt_client_id, off = _read_string(body, off)
        if connect_flags & 0x04:  # will flag
            _, off = _read_string(body, off)  # will topic
            _, off = _read_string(body, off)  # will message
        username = password = None
        if connect_flags & 0x80:
            username, off = _read_string(body, off)
        if connect_flags & 0x40:
            password, off = _read_string(body, off)

        rc = self.authenticate(username, password)
        self.log_protocol("in", b"CONNECT " + mqtt_client_id, client_id)
        await self._write(client_id, writer, bytes([_CONNACK << 4, 2, 0, rc]))
        if rc != 0:
            logger.info("%s: client %s rejected (rc=%d)", self.name, client_id, rc)
            return False

        self._clients[client_id] = writer
        self._subscriptions[client_id] = {}
        self._client_meta[client_id] = {
            "mqtt_client_id": mqtt_client_id.decode("utf-8", "replace"),
            "username": username.decode("utf-8", "replace") if username else None,
        }
        await self.on_client_connected(client_id)
        return True

    async def _handle_subscribe(
        self, client_id: str, writer: asyncio.StreamWriter, body: bytes
    ) -> None:
        off = 0
        packet_id = body[off:off + 2]
        off += 2
        granted = bytearray()
        topics: list[str] = []
        while off < len(body):
            raw, off = _read_string(body, off)
            off += 1  # requested QoS byte (we always grant QoS 0)
            topic = raw.decode("utf-8", "replace")
            topics.append(topic)
            self._subscriptions.setdefault(client_id, {})[topic] = 0
            granted.append(0x00)  # grant QoS 0
        suback = bytes([_SUBACK << 4]) + _encode_remaining_length(2 + len(granted))
        suback += packet_id + bytes(granted)
        await self._write(client_id, writer, suback)
        for topic in topics:
            self.log_protocol("in", b"SUBSCRIBE " + topic.encode(), client_id)
            await self.on_subscribe(client_id, topic)

    async def _handle_unsubscribe(
        self, client_id: str, writer: asyncio.StreamWriter, body: bytes
    ) -> None:
        off = 0
        packet_id = body[off:off + 2]
        off += 2
        subs = self._subscriptions.setdefault(client_id, {})
        while off < len(body):
            raw, off = _read_string(body, off)
            subs.pop(raw.decode("utf-8", "replace"), None)
        unsuback = bytes([_UNSUBACK << 4, 2]) + packet_id
        await self._write(client_id, writer, unsuback)

    async def _handle_publish(self, client_id: str, flags: int, body: bytes) -> None:
        qos = (flags >> 1) & 0x03
        off = 0
        raw_topic, off = _read_string(body, off)
        packet_id = None
        if qos > 0:
            packet_id = body[off:off + 2]
            off += 2
        payload = body[off:]
        topic = raw_topic.decode("utf-8", "replace")
        self.log_protocol("in", raw_topic + b" " + payload, client_id)

        if qos == 1 and packet_id is not None:
            writer = self._clients.get(client_id)
            if writer is not None:
                await self._write(
                    client_id, writer, bytes([_PUBACK << 4, 2]) + packet_id
                )

        # Network conditions: silently drop if configured.
        if self._network_layer and self._network_layer.should_drop(self.device_id):
            return
        if self.has_error_behavior("no_response"):
            return

        # Route to other subscribers (a real broker fans out publishes), then
        # let the device subclass react.
        await self._route_to_subscribers(client_id, topic, payload)
        try:
            await self.on_publish(client_id, topic, payload)
        except Exception:
            logger.exception("%s: error in on_publish", self.name)

    async def _route_to_subscribers(
        self, sender_id: str, topic: str, payload: bytes
    ) -> None:
        for client_id, subs in list(self._subscriptions.items()):
            if client_id == sender_id:
                continue
            if any(_topic_matches(f, topic) for f in subs):
                writer = self._clients.get(client_id)
                if writer is not None:
                    await self._send_publish(client_id, writer, topic, payload)

    # ── Wire helpers ──

    async def _send_publish(
        self,
        client_id: str,
        writer: asyncio.StreamWriter,
        topic: str,
        payload: bytes | str,
    ) -> None:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        vh = _encode_string(topic)
        packet_body = vh + payload
        packet = (
            bytes([_PUBLISH << 4])
            + _encode_remaining_length(len(packet_body))
            + packet_body
        )
        await self._write(client_id, writer, packet)

    async def _write(
        self, client_id: str, writer: asyncio.StreamWriter, data: bytes
    ) -> None:
        try:
            writer.write(data)
            await writer.drain()
            self.log_protocol("out", data, client_id)
        except (ConnectionError, OSError):
            self._clients.pop(client_id, None)

    async def _read_packet(
        self, reader: asyncio.StreamReader
    ) -> tuple[int, int, bytes] | None:
        """Read one MQTT control packet. Returns (type, flags, body) or None."""
        first = await reader.readexactly(1)
        b0 = first[0]
        ptype = b0 >> 4
        flags = b0 & 0x0F
        multiplier = 1
        remaining = 0
        while True:
            eb = await reader.readexactly(1)
            byte = eb[0]
            remaining += (byte & 0x7F) * multiplier
            if (byte & 0x80) == 0:
                break
            multiplier *= 128
            if multiplier > 128 ** 3:
                raise ValueError("malformed remaining length")
        body = await reader.readexactly(remaining) if remaining else b""
        return ptype, flags, body
