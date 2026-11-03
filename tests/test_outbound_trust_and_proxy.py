"""OpenAVC's own internet requests trust the OS certificate store and take the
environment's proxy; equipment at a local address is never reached through a
proxy (``openavc/utils/outbound.py``).

Measured before the change, behind a proxy that inspects encrypted traffic
with its certificate authority in the machine's certificate store: the driver
catalog, update checks and installs failed with a certificate error while the
cloud WebSocket connected; and with only ``HTTPS_PROXY`` set, an HTTPS or
``wss://`` device on a private address and a plain ``ws://127.0.0.1``
connection all went to the proxy.
"""

from __future__ import annotations

import asyncio
import datetime
import ssl

import httpx
import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport.http_client import HTTPClientTransport, ReachDevicesTransport
from openavc.utils import outbound
from openavc.utils.outbound import (
    env_proxy_for,
    environment_has_proxy,
    internet_client,
    internet_failure,
    internet_ssl_context,
    internet_tls_kwargs,
    is_internet_failure_sentence,
    is_local_destination,
)

_PROXY_VARS = (
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
    "https_proxy", "http_proxy", "all_proxy", "no_proxy",
)


@pytest.fixture
def no_proxy_env(monkeypatch):
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proxy_env(monkeypatch, no_proxy_env):
    """A proxy in the environment, the way an administrator sets one on the service."""

    def set_to(url: str, no_proxy: str = "") -> None:
        monkeypatch.setenv("HTTPS_PROXY", url)
        monkeypatch.setenv("HTTP_PROXY", url)
        if no_proxy:
            monkeypatch.setenv("NO_PROXY", no_proxy)

    return set_to


@pytest.fixture
def fresh_context():
    internet_ssl_context.cache_clear()
    yield
    internet_ssl_context.cache_clear()


# --- trust -------------------------------------------------------------------


def test_the_internet_context_verifies_and_carries_certifi(fresh_context):
    ctx = internet_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    # certifi's roots are the fallback, so nothing that verified before stops.
    # truststore keeps them on the context it wraps, and verifies against them
    # when the OS store refuses a chain.
    inner = getattr(ctx, "_ctx", ctx)
    assert len(inner.get_ca_certs()) > 50


def test_the_internet_context_asks_the_os_store():
    truststore = pytest.importorskip("truststore")
    internet_ssl_context.cache_clear()
    try:
        assert isinstance(internet_ssl_context(), truststore.SSLContext)
    finally:
        internet_ssl_context.cache_clear()


def _ca_and_server_cert(tmp_path):
    """A test CA and a localhost certificate it signed, as PEM files."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Acme Inspection CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_cert_sign=True, crl_sign=True,
            content_commitment=False, key_encipherment=False, data_encipherment=False,
            key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = tmp_path / "inspection-ca.pem"
    ca_pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_pem = tmp_path / "server.pem"
    cert_pem.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_pem = tmp_path / "server.key"
    key_pem.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return ca_pem, cert_pem, key_pem


async def _https_get(cert_pem, key_pem):
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_pem, key_pem)

    async def handle(reader, writer):
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]
    try:
        async with internet_client(timeout=10) as client:
            return await client.get(f"https://localhost:{port}/")
    finally:
        server.close()
        await server.wait_closed()


async def test_a_bundle_named_in_ssl_cert_file_is_trusted_too(
    tmp_path, monkeypatch, no_proxy_env, fresh_context
):
    ca_pem, cert_pem, key_pem = _ca_and_server_cert(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_pem))
    resp = await _https_get(cert_pem, key_pem)
    assert resp.text == "ok"


async def test_a_certificate_nobody_trusts_is_refused_with_the_sentence(
    tmp_path, monkeypatch, no_proxy_env, fresh_context
):
    _ca_pem, cert_pem, key_pem = _ca_and_server_cert(tmp_path)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    with pytest.raises(httpx.ConnectError) as refused:
        await _https_get(cert_pem, key_pem)
    sentence = internet_failure(refused.value, "localhost")
    assert sentence is not None and "does not trust" in sentence


def test_an_unreadable_ssl_cert_file_does_not_stop_downloads(tmp_path, monkeypatch, fresh_context):
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    assert internet_ssl_context().verify_mode == ssl.CERT_REQUIRED


def test_internet_client_verifies_with_the_context_and_reads_the_environment(no_proxy_env):
    client = internet_client(timeout=5)
    try:
        assert client._transport._pool._ssl_context is internet_ssl_context()
        assert client._trust_env is True
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize("kw", [
    {"transport": httpx.MockTransport(lambda r: httpx.Response(200))},
    {"mounts": {"all://": None}},
])
def test_internet_client_refuses_a_transport_that_would_drop_both_rules(kw):
    with pytest.raises(TypeError):
        internet_client(**kw)


def test_an_internet_websocket_gets_the_context_only_for_wss():
    assert internet_tls_kwargs("wss://cloud.example/agent/v1") == {"ssl": internet_ssl_context()}
    assert internet_tls_kwargs("ws://127.0.0.1:8000/agent/v1") == {}


# --- what local means ---------------------------------------------------------


@pytest.mark.parametrize("host", [
    "127.0.0.1", "::1", "[::1]", "localhost", "10.20.0.80", "172.16.4.2",
    "192.168.1.50", "169.254.10.3", "fe80::1", "fd00::5", "projector.local",
    "PROJECTOR.LOCAL.", "display1",
])
def test_local_destinations(host):
    assert is_local_destination(host)


@pytest.mark.parametrize("host", [
    "raw.githubusercontent.com", "8.8.8.8", "2606:4700::1111", "dante.cloud",
    "display1.av.example.edu", "", None,
])
def test_destinations_that_follow_the_environment(host):
    assert not is_local_destination(host)


# --- which proxy -------------------------------------------------------------


def test_no_proxy_in_the_environment_means_direct(no_proxy_env, monkeypatch):
    monkeypatch.setattr(outbound.urllib.request, "getproxies", lambda: {})
    assert not environment_has_proxy()
    assert env_proxy_for("https://raw.githubusercontent.com/x") is None


def test_the_proxy_applies_to_the_internet_and_never_to_local_equipment(proxy_env):
    proxy_env("http://proxy.example:3128")
    assert environment_has_proxy()
    assert env_proxy_for("https://raw.githubusercontent.com/x") == "http://proxy.example:3128"
    assert env_proxy_for("http://api.example.com/v1") == "http://proxy.example:3128"
    for url in ("https://10.20.0.80/api", "http://192.168.1.50/", "http://127.0.0.1:8080/",
                "https://projector.local/", "http://display1/"):
        assert env_proxy_for(url) is None, url


def test_no_proxy_still_excludes_a_named_host(proxy_env):
    proxy_env("http://proxy.example:3128", no_proxy=".av.example.edu")
    assert env_proxy_for("http://display1.av.example.edu/") is None
    assert env_proxy_for("https://raw.githubusercontent.com/x") == "http://proxy.example:3128"


# --- the sentence -------------------------------------------------------------


def _wrapped(inner: BaseException) -> httpx.ConnectError:
    try:
        try:
            raise inner
        except BaseException as e:
            raise httpx.ConnectError(str(e)) from e
    except httpx.ConnectError as outer:
        return outer


@pytest.mark.parametrize("inner", [
    ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
    ssl.SSLCertVerificationError(
        "A certificate chain processed, but terminated in a root certificate "
        "which is not trusted by the trust provider."
    ),
])
def test_a_refused_certificate_says_what_to_install(inner):
    sentence = internet_failure(_wrapped(inner), "raw.githubusercontent.com")
    assert sentence is not None
    assert sentence.startswith("The connection to raw.githubusercontent.com")
    assert "certificate store" in sentence
    assert is_internet_failure_sentence(sentence)


def test_a_proxy_refusal_names_the_proxy():
    sentence = internet_failure(httpx.ProxyError("407 Proxy Authentication Required"), "api.github.com")
    assert sentence is not None and "HTTPS_PROXY" in sentence
    assert is_internet_failure_sentence(sentence)


def test_any_other_failure_keeps_its_own_sentence():
    assert internet_failure(httpx.ConnectTimeout("timed out"), "api.github.com") is None
    assert not is_internet_failure_sentence("Network error: timed out")


# --- equipment: a driver's own client, the platform's transport, a WebSocket --


class _AcmeHttp(BaseDriver):
    DRIVER_INFO = {"id": "acme_http_links", "name": "Acme HTTP Links", "transport": "tcp"}

    async def send_command(self, command, params=None):
        return None

    def build_client(self, **kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(**kwargs)

    def build_websocket(self, uri: str, **kwargs):
        from websockets.asyncio.client import connect

        return connect(uri, **kwargs)


def _driver() -> _AcmeHttp:
    state = StateStore()
    state.set_event_bus(EventBus())
    return _AcmeHttp("acme1", {"host": "10.20.0.80", "port": 80}, state, EventBus())


def _proxy_of(pool: httpx.AsyncHTTPTransport) -> str | None:
    import httpcore

    inner = pool._pool
    if isinstance(inner, httpcore.AsyncHTTPProxy):
        return str(inner._proxy_url.host.decode())
    return None


def test_with_no_proxy_a_drivers_client_is_the_one_it_asked_for(no_proxy_env, monkeypatch):
    monkeypatch.setattr(outbound.urllib.request, "getproxies", lambda: {})
    client = _driver().build_client(base_url="http://10.20.0.80")
    assert type(client._transport) is httpx.AsyncHTTPTransport


def test_with_a_proxy_a_drivers_client_reaches_local_equipment_directly(proxy_env):
    proxy_env("http://proxy.example:3128")
    client = _driver().build_client(base_url="http://10.20.0.80")
    transport = client._transport
    assert isinstance(transport, ReachDevicesTransport)
    assert _proxy_of(transport.pool_for("10.20.0.80", "http://10.20.0.80/api")) is None
    # A cloud service the driver talks to still goes through the proxy.
    cloud = transport.pool_for("dante.cloud", "https://dante.cloud/graphql")
    assert _proxy_of(cloud) == "proxy.example"


def test_a_driver_that_turns_the_environment_off_keeps_its_client(proxy_env):
    proxy_env("http://proxy.example:3128")
    client = _driver().build_client(base_url="http://10.20.0.80", trust_env=False)
    assert type(client._transport) is httpx.AsyncHTTPTransport


async def test_the_platforms_transport_reaches_local_equipment_directly(proxy_env):
    proxy_env("http://proxy.example:3128")
    transport = HTTPClientTransport(base_url="https://10.20.0.80", verify_ssl=False)
    await transport.open()
    try:
        client = transport._client
        assert _proxy_of(client._transport) is None
        assert client._transport._pool._ssl_context.verify_mode == ssl.CERT_NONE
    finally:
        await transport.close()


def test_a_drivers_websocket_to_local_equipment_skips_the_proxy(proxy_env):
    proxy_env("http://proxy.example:3128")
    drv = _driver()
    assert drv.build_websocket("wss://10.20.0.80:3001/").proxy is None
    assert drv.build_websocket("ws://127.0.0.1:4455/").proxy is None
    # Anything else is left as websockets builds it, and so is a choice the driver made.
    assert drv.build_websocket("wss://api.example.com/").proxy is True
    assert drv.build_websocket("wss://10.20.0.80:3001/", proxy="http://other:1").proxy == "http://other:1"


def test_a_websocket_opened_outside_any_driver_is_left_alone(proxy_env):
    from websockets.asyncio.client import connect

    proxy_env("http://proxy.example:3128")
    assert connect("ws://10.20.0.80:1/").proxy is True


# --- end to end: a proxy that records who asked it for what -------------------


class _RecordingProxy:
    """An HTTP proxy on loopback that notes each request line and answers 502."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.port = 0
        self._server = None

    async def __aenter__(self):
        async def handle(reader, writer):
            try:
                line = await asyncio.wait_for(reader.readline(), 2)
                self.requests.append(line.decode("latin-1").strip())
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        self._server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()


class _Device:
    """An HTTP device on loopback that answers ok."""

    def __init__(self) -> None:
        self.hits = 0
        self.port = 0
        self._server = None

    async def __aenter__(self):
        async def handle(reader, writer):
            self.hits += 1
            try:
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                await writer.drain()
            finally:
                writer.close()

        self._server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()


async def test_equipment_goes_direct_and_the_internet_goes_through_the_proxy(proxy_env):
    async with _RecordingProxy() as proxy, _Device() as device:
        proxy_env(f"http://127.0.0.1:{proxy.port}")

        # The platform's device transport, and a driver's own client.
        transport = HTTPClientTransport(base_url=f"http://127.0.0.1:{device.port}")
        await transport.open()
        try:
            assert (await transport._client.get("/")).text == "ok"
        finally:
            await transport.close()
        async with _driver().build_client(base_url=f"http://127.0.0.1:{device.port}") as own:
            assert (await own.get("/")).text == "ok"
        assert device.hits == 2

        # OpenAVC's own internet request asks the proxy.
        async with internet_client(timeout=5) as client:
            resp = await client.get("http://updates.example/releases")
        assert resp.status_code == 502
        assert proxy.requests == ["GET http://updates.example/releases HTTP/1.1"]
