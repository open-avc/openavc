"""Tests for the MQTT transport.

These exercise the transport's own logic — TLS context construction, CONNACK
classification, callback dispatch, protocol-version selection, and the pub/sub
verbs — against a fake gmqtt client (no broker, no network). A full pub/sub
round trip against a real broker is covered by the simulator e2e tests.
"""

from __future__ import annotations

import ssl

import pytest

from server.transport.mqtt import MQTTTransport


# --- Fake gmqtt client -----------------------------------------------------

class FakeClient:
    """Stand-in for gmqtt.Client that drives the handshake in-process."""

    instances: list["FakeClient"] = []
    next_rc = 0  # CONNACK return code the next connect() will report

    def __init__(self, client_id, clean_session=True):
        self.client_id = client_id
        self.clean_session = clean_session
        self._connected = False
        self.config: dict = {}
        self.auth = None
        self.on_connect = None
        self.on_message = None
        self.on_disconnect = None
        self.published: list[tuple] = []
        self.subscribed: list[tuple] = []
        self.unsubscribed: list[str] = []
        self.connect_args: dict | None = None
        FakeClient.instances.append(self)

    def set_config(self, cfg):
        self.config.update(cfg)

    def set_auth_credentials(self, username, password):
        self.auth = (username, password)

    async def connect(self, host, port, ssl=None, keepalive=60, version=None):
        self.connect_args = dict(
            host=host, port=port, ssl=ssl, keepalive=keepalive, version=version
        )
        rc = FakeClient.next_rc
        if self.on_connect:
            self.on_connect(self, 0, rc, None)
        self._connected = rc == 0

    @property
    def is_connected(self):
        return self._connected

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))

    def unsubscribe(self, topic):
        self.unsubscribed.append(topic)

    async def disconnect(self):
        self._connected = False


@pytest.fixture(autouse=True)
def fake_gmqtt(monkeypatch):
    """Patch gmqtt.Client with the in-process fake."""
    FakeClient.instances = []
    FakeClient.next_rc = 0
    monkeypatch.setattr("gmqtt.Client", FakeClient)
    yield FakeClient


# --- Connect + CONNACK -----------------------------------------------------

async def test_connect_success_defaults_to_mqtt_311():
    from gmqtt.mqtt.constants import MQTTv311

    t = await MQTTTransport.create("10.0.0.5", 1883, username="u", password="p")
    assert t.connected is True
    client = FakeClient.instances[-1]
    assert client.auth == ("u", "p")
    # Platform owns reconnect — gmqtt's own retry is disabled.
    assert client.config.get("reconnect_retries") == 0
    # Default protocol version is 3.1.1, not 5.0.
    assert client.connect_args["version"] is MQTTv311
    # Plain (no TLS) connection.
    assert client.connect_args["ssl"] is False
    await t.close()


async def test_connect_version_5_selected():
    from gmqtt.mqtt.constants import MQTTv50

    t = await MQTTTransport.create("10.0.0.5", 1883, protocol_version="5.0")
    assert FakeClient.instances[-1].connect_args["version"] is MQTTv50
    await t.close()


async def test_connack_not_authorized_is_auth_error():
    FakeClient.next_rc = 5  # not authorized
    with pytest.raises(ConnectionError):
        await MQTTTransport.create("10.0.0.5", 1883, username="u", password="bad")


async def test_connack_bad_credentials_message():
    FakeClient.next_rc = 4  # bad username or password
    t = MQTTTransport("10.0.0.5", 1883)
    with pytest.raises(ConnectionError):
        await t.open()
    # last_error wording must be one the connection-fault classifier reads as auth.
    assert "bad username or password" in t.last_error.lower()


# --- Pub/sub ---------------------------------------------------------------

async def test_publish_and_subscribe_delegate():
    t = await MQTTTransport.create("10.0.0.5", 1883)
    await t.publish("a/b", "hello")
    await t.publish("a/c", b"\x01\x02", qos=1, retain=True)
    await t.subscribe("a/#", qos=1)
    await t.unsubscribe("a/#")
    client = FakeClient.instances[-1]
    assert client.published[0] == ("a/b", b"hello", 0, False)
    assert client.published[1] == ("a/c", b"\x01\x02", 1, True)
    assert client.subscribed == [("a/#", 1)]
    assert client.unsubscribed == ["a/#"]
    await t.close()


async def test_publish_when_disconnected_raises():
    t = MQTTTransport("10.0.0.5", 1883)
    with pytest.raises(ConnectionError):
        await t.publish("a/b", "x")


async def test_send_shim_parses_topic_and_payload():
    t = await MQTTTransport.create("10.0.0.5", 1883)
    await t.send(b"my/topic the payload here")
    assert FakeClient.instances[-1].published[-1] == (
        "my/topic", b"the payload here", 0, False,
    )
    await t.close()


# --- Inbound message dispatch ----------------------------------------------

async def test_on_message_dispatch_async_handler():
    received: list[tuple] = []

    async def handler(topic, payload):
        received.append((topic, payload))

    t = await MQTTTransport.create("10.0.0.5", 1883, on_message=handler)
    client = FakeClient.instances[-1]
    assert t.last_data_received == 0.0
    await client.on_message(client, "tv/state", b'{"v":1}', 0, None)
    assert received == [("tv/state", b'{"v":1}')]
    assert t.last_data_received > 0.0
    await t.close()


async def test_on_message_dispatch_sync_handler():
    received: list[tuple] = []
    t = await MQTTTransport.create("10.0.0.5", 1883)
    t.on_message = lambda topic, payload: received.append((topic, payload))
    client = FakeClient.instances[-1]
    await client.on_message(client, "x", b"y", 0, None)
    assert received == [("x", b"y")]
    await t.close()


async def test_on_message_handler_error_is_swallowed():
    def boom(topic, payload):
        raise RuntimeError("handler bug")

    t = await MQTTTransport.create("10.0.0.5", 1883, on_message=boom)
    client = FakeClient.instances[-1]
    # Must not propagate — a buggy driver handler can't kill the transport.
    assert await client.on_message(client, "x", b"y", 0, None) == 0
    await t.close()


# --- Disconnect handling ---------------------------------------------------

async def test_unexpected_disconnect_calls_platform_callback():
    calls: list[int] = []
    t = await MQTTTransport.create(
        "10.0.0.5", 1883, on_disconnect=lambda: calls.append(1)
    )
    client = FakeClient.instances[-1]
    client.on_disconnect(client, None, exc=OSError("dropped"))
    assert calls == [1]
    assert "dropped" in t.last_error


async def test_graceful_close_suppresses_reconnect_callback():
    calls: list[int] = []
    t = await MQTTTransport.create(
        "10.0.0.5", 1883, on_disconnect=lambda: calls.append(1)
    )
    client = FakeClient.instances[-1]
    await t.close()
    # gmqtt fires on_disconnect during our own close — must not trigger reconnect.
    client.on_disconnect(client, None, exc=None)
    assert calls == []


# --- TLS context construction ----------------------------------------------

def test_ssl_context_disabled_when_no_tls():
    t = MQTTTransport("h", 1, use_tls=False)
    assert t._build_ssl_context() is False


def test_ssl_context_no_verify_sets_cert_none():
    t = MQTTTransport("h", 1, use_tls=True, verify_ssl=False)
    ctx = t._build_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


def test_ssl_context_verify_keeps_verification():
    t = MQTTTransport("h", 1, use_tls=True, verify_ssl=True)
    ctx = t._build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_ssl_context_loads_client_cert(tmp_path):
    """A client cert/key pair is loaded into the context without error."""
    cert_path, key_path = _make_self_signed(tmp_path)
    t = MQTTTransport(
        "h", 1, use_tls=True, verify_ssl=False,
        client_cert=str(cert_path), client_key=str(key_path),
    )
    # load_cert_chain raises if the pair is bad; reaching here means it loaded.
    ctx = t._build_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)


def _make_self_signed(tmp_path):
    """Generate a throwaway self-signed cert + key for the cert-load test."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "client.crt"
    key_path = tmp_path / "client.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path
