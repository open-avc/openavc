"""
OpenAVC MQTT Transport — async MQTT client for pub/sub device protocols.

Unlike TCP/Serial/UDP, MQTT is not a byte stream and not request/response: a
driver CONNECTs to a broker, SUBSCRIBEs to topics, and PUBLISHes to topics.
Inbound messages arrive unsolicited on subscribed topics. So, like the HTTP
transport, this class breaks the byte-stream model and exposes its own verbs
(``publish`` / ``subscribe``) plus a settable ``on_message(topic, payload)``
callback, while still implementing the common transport lifecycle
(``open``/``close``/``verify``/``connected``/``last_error``) so the device
manager's connect + reconnect machinery and the shared connection-fault
classifier work unchanged.

Designed for devices and integrations that speak MQTT natively: Hisense VIDAA
TVs (an embedded broker on the TV), building-management gateways, IoT sensors,
lighting bridges, and Home Assistant style mirrors.

This is a Python-driver transport (like SSH): it has no declarative YAML
authoring surface, because topic-based pub/sub doesn't map onto the
request/response shape the ``.avcdriver`` schema expresses. A driver selects it
with ``DRIVER_INFO["transport"] = "mqtt"`` and drives it from Python.

Backed by gmqtt (MIT, pure-Python, asyncio-native).

TLS:
    - Plain (use_tls=False) for local brokers.
    - TLS with verification (use_tls=True, verify_ssl=True).
    - TLS without verification (verify_ssl=False) — required by many AV devices
      that ship a self-signed broker cert. When verification is off the cipher
      security level is also lowered so old/weak self-signed certs (e.g. the
      mosquitto 1.4.2 embedded in Hisense TVs) still negotiate.
    - Optional client certificate (client_cert/client_key) — required by some
      devices for inbound connections.

Protocol version:
    Defaults to MQTT 3.1.1, which is what the overwhelming majority of devices
    speak (a 5.0 CONNECT to a 3.1.1-only broker is rejected). Override with
    protocol_version="5.0" for brokers that require it.
"""

from __future__ import annotations

import inspect
import ssl
import time
import uuid
from typing import Any, Awaitable, Callable

from server.utils.logger import get_logger

log = get_logger(__name__)

# A message handler the driver registers: called with (topic, payload_bytes).
# May be sync or async; an async handler is awaited.
MessageHandler = Callable[[str, bytes], Awaitable[None] | None]

# CONNACK return codes (MQTT 3.1.1) → human strings. The wording is chosen so
# the shared connection-fault classifier recognises the auth cases.
_CONNACK_MESSAGES = {
    1: "connection refused: unacceptable protocol version",
    2: "connection refused: client identifier rejected",
    3: "connection refused: server unavailable",
    4: "connection refused: bad username or password",
    5: "connection refused: not authorized",
}


class MQTTTransport:
    """
    Async MQTT client transport.

    Lifecycle: build with :meth:`create` (which connects and waits for the
    broker's CONNACK, raising on failure so the device manager classifies it),
    then ``publish`` / ``subscribe``. Inbound messages are delivered to the
    ``on_message`` callback. ``close`` disconnects cleanly.
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        *,
        client_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        verify_ssl: bool = True,
        client_cert: str | None = None,
        client_key: str | None = None,
        ca_cert: str | None = None,
        ciphers: str | None = None,
        keepalive: int = 60,
        protocol_version: str = "3.1.1",
        clean_session: bool = True,
        on_message: MessageHandler | None = None,
        on_disconnect: Callable[[], Any] | None = None,
        name: str | None = None,
    ):
        """
        Args:
            host: Broker host (the device's IP/hostname).
            port: Broker TCP port.
            client_id: MQTT client identifier. A random one is generated when
                omitted. (This is the MQTT-level id, distinct from any
                app/topic name a protocol embeds in its topics.)
            username/password: Broker credentials (omit for anonymous).
            use_tls: Wrap the connection in TLS.
            verify_ssl: Verify the broker's TLS certificate. Set False for the
                self-signed certs common on AV devices.
            client_cert/client_key: Paths to a client certificate + private key
                (PEM) for devices that require client-cert auth.
            ca_cert: Optional CA bundle path to verify the broker against.
            ciphers: Optional OpenSSL cipher string. Defaults to a permissive
                level when verify_ssl is False so weak self-signed certs work.
            keepalive: MQTT keepalive interval (seconds).
            protocol_version: "3.1.1" (default) or "5.0".
            clean_session: MQTT clean-session flag.
            on_message: Callback ``(topic, payload_bytes)`` for inbound
                messages. Sync or async. Settable after construction too.
            on_disconnect: Called with no args on an unexpected disconnect, so
                the device manager can drive its reconnect loop.
            name: Label for logs (defaults to ``host:port``).
        """
        self.host = host
        self.port = int(port)
        self.client_id = client_id or f"openavc-{uuid.uuid4().hex[:8]}"
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.verify_ssl = verify_ssl
        self.client_cert = client_cert
        self.client_key = client_key
        self.ca_cert = ca_cert
        self.ciphers = ciphers
        self.keepalive = int(keepalive)
        self.protocol_version = protocol_version
        self.clean_session = clean_session
        self.on_message: MessageHandler | None = on_message
        self.on_disconnect: Callable[[], Any] | None = on_disconnect
        self._name = name or f"{host}:{self.port}"

        self._client: Any = None  # gmqtt.Client
        self._connack_rc: int | None = None
        self._closing = False
        self.last_data_received: float = 0.0
        self._last_error = ""

    @classmethod
    async def create(cls, host: str, port: int = 1883, **kwargs: Any) -> "MQTTTransport":
        """Construct and connect, returning a ready transport.

        Raises on connect/auth failure (with ``last_error`` populated) so the
        device manager's reconnect loop can classify the fault.
        """
        transport = cls(host, port, **kwargs)
        await transport.open()
        return transport

    # --- Lifecycle -------------------------------------------------------

    async def open(self) -> None:
        """Connect to the broker and wait for CONNACK."""
        if self._client is not None and self.connected:
            return

        try:
            from gmqtt import Client as MQTTClient
        except ImportError as e:  # pragma: no cover - dependency is declared
            self._last_error = "gmqtt not installed"
            raise ConnectionError(
                "MQTT transport requires the 'gmqtt' package"
            ) from e

        self._closing = False
        self._connack_rc = None

        client = MQTTClient(self.client_id, clean_session=self.clean_session)
        # The platform owns reconnect (device_manager backoff + offline_reason
        # classification). Disable gmqtt's own reconnect so a drop surfaces as
        # one on_disconnect and stays down until the platform reconnects.
        client.set_config({"reconnect_retries": 0})

        if self.username:
            client.set_auth_credentials(self.username, self.password or None)

        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        ssl_param = self._build_ssl_context()
        version = self._gmqtt_version()

        self._client = client
        try:
            await client.connect(
                self.host,
                self.port,
                ssl=ssl_param,
                keepalive=self.keepalive,
                version=version,
            )
        except Exception as e:
            # Prefer a CONNACK-derived message (more specific: which auth/proto
            # failure) over a bare exception when we captured one.
            rc_msg = _CONNACK_MESSAGES.get(self._connack_rc or 0)
            self._last_error = rc_msg or str(e) or type(e).__name__
            self._client = None
            raise

        # A non-zero CONNACK that gmqtt didn't raise on.
        if self._connack_rc not in (0, None):
            self._last_error = _CONNACK_MESSAGES.get(
                self._connack_rc, f"connection refused: CONNACK rc={self._connack_rc}"
            )
            await self._safe_disconnect()
            self._client = None
            raise ConnectionError(self._last_error)

        log.info(
            f"[{self._name}] MQTT connected "
            f"(tls={self.use_tls}, verify={self.verify_ssl}, "
            f"client_cert={'yes' if self.client_cert else 'no'}, "
            f"v{self.protocol_version})"
        )

    async def close(self) -> None:
        """Disconnect cleanly (suppresses the reconnect callback)."""
        self._closing = True
        await self._safe_disconnect()
        self._client = None
        log.info(f"[{self._name}] MQTT closed")

    async def verify(self, timeout: float = 5.0) -> bool:
        """Reachability check — for MQTT, open() already established CONNACK."""
        return self.connected

    # --- Pub/sub verbs ---------------------------------------------------

    async def publish(
        self,
        topic: str,
        payload: bytes | str | None = None,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        """Publish a message to a topic."""
        if self._client is None or not self.connected:
            raise ConnectionError(f"[{self._name}] MQTT not connected")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._client.publish(topic, payload, qos=qos, retain=retain)

    async def subscribe(self, topic: str, qos: int = 0) -> None:
        """Subscribe to a topic (or topic filter)."""
        if self._client is None or not self.connected:
            raise ConnectionError(f"[{self._name}] MQTT not connected")
        self._client.subscribe(topic, qos=qos)

    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a topic."""
        if self._client is None or not self.connected:
            return
        self._client.unsubscribe(topic)

    # --- Transport-interface compatibility shims -------------------------

    async def send(self, data: bytes) -> None:
        """Compatibility shim: interpret ``"topic payload"`` and publish.

        Lets the generic raw-test path (which sends bytes) exercise an MQTT
        device. The first whitespace-separated token is the topic; the rest is
        the payload.
        """
        text = data.decode("utf-8", errors="replace")
        topic, _, payload = text.partition(" ")
        if topic:
            await self.publish(topic, payload)

    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        """Pub/sub has no synchronous reply; publish and return empty bytes."""
        await self.send(data)
        return b""

    # --- Properties ------------------------------------------------------

    @property
    def connected(self) -> bool:
        """True when the gmqtt client reports an active session."""
        client = self._client
        if client is None:
            return False
        return bool(getattr(client, "is_connected", False))

    @property
    def last_error(self) -> str:
        """Last error string (for the connection-fault classifier)."""
        return self._last_error

    # --- gmqtt callbacks -------------------------------------------------

    def _on_connect(self, client: Any, flags: int, rc: int, properties: Any) -> None:
        self._connack_rc = rc
        if rc not in (0, None):
            self._last_error = _CONNACK_MESSAGES.get(
                rc, f"connection refused: CONNACK rc={rc}"
            )

    async def _on_message(
        self, client: Any, topic: str, payload: bytes, qos: int, properties: Any
    ) -> int:
        self.last_data_received = time.monotonic()
        handler = self.on_message
        if handler is not None:
            try:
                result = handler(topic, payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception(f"[{self._name}] MQTT on_message handler error")
        return 0

    def _on_disconnect(self, client: Any, packet: Any, exc: Any = None) -> None:
        if exc is not None:
            self._last_error = str(exc) or type(exc).__name__
        if self._closing:
            return
        # Unexpected drop — let the device manager reconnect.
        cb = self.on_disconnect
        if cb is not None:
            try:
                cb()
            except Exception:
                log.exception(f"[{self._name}] MQTT on_disconnect callback error")

    # --- Internals -------------------------------------------------------

    async def _safe_disconnect(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            pass

    def _gmqtt_version(self) -> Any:
        from gmqtt.mqtt.constants import MQTTv311, MQTTv50

        return MQTTv50 if str(self.protocol_version).startswith("5") else MQTTv311

    def _build_ssl_context(self) -> ssl.SSLContext | bool:
        """Build the TLS context, or False for a plain connection."""
        if not self.use_tls:
            return False

        ctx = ssl.create_default_context()

        if not self.verify_ssl:
            # Order matters: clear hostname check before relaxing verify_mode.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            # Tolerate old/weak self-signed certs (e.g. Hisense mosquitto 1.4.2,
            # SHA1 / short RSA keys that OpenSSL 3 rejects at SECLEVEL>=1).
            try:
                ctx.set_ciphers(self.ciphers or "DEFAULT@SECLEVEL=0")
            except ssl.SSLError:
                pass
        elif self.ca_cert:
            ctx.load_verify_locations(self.ca_cert)
        elif self.ciphers:
            ctx.set_ciphers(self.ciphers)

        if self.client_cert:
            ctx.load_cert_chain(
                certfile=self.client_cert, keyfile=self.client_key or None
            )

        return ctx
