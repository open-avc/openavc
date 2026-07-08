"""End-to-end integration test for the HTTPS listener + redirect listener.

Unlike ``test_redirect_app.py`` (which uses TestClient) and
``test_tls.py`` (cert generation in isolation), this test spins up real
``uvicorn.Server`` instances bound to free ports and hits them over a
real socket via ``httpx.AsyncClient(verify=False)``. That's the only
way to exercise:

  * uvicorn's TLS layer with a generated cert (TestClient skips SSL),
  * the dual-listener architecture (TLS + redirect) under real I/O,
  * graceful shutdown of two concurrent servers in one event loop.

The plan calls this out explicitly (Phase 8): "TestClient does NOT
exercise uvicorn's TLS layer ... that's why the integration test uses
a real uvicorn.Server."
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from server import tls
from server.main import _build_redirect_app, _harden_tls_context
from tests.helpers import make_cloud_cert_pem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Ask the OS for a free TCP port on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_test_app() -> Starlette:
    """Minimal app exposing /api/health — avoids the engine + project setup
    cost of mounting the real FastAPI app, which isn't what we're testing."""

    async def health(_request):
        return JSONResponse({"status": "ok"})

    async def echo(request):
        body = await request.body()
        return JSONResponse({"received": body.decode("utf-8")})

    return Starlette(routes=[
        Route("/api/health", health, methods=["GET"]),
        Route("/api/echo", echo, methods=["POST"]),
    ])


@contextlib.asynccontextmanager
async def _running_server(server: uvicorn.Server):
    """Start a uvicorn.Server as a background task; ensure it stops on exit.

    Replaces ``capture_signals`` with a no-op so the test process keeps its
    own SIGINT/SIGTERM handlers (pytest needs these for clean cancellation).
    """
    @contextlib.contextmanager
    def _no_signals():
        yield

    server.capture_signals = _no_signals
    task = asyncio.create_task(server.serve())
    # Wait for the server to flip "started" before yielding — otherwise the
    # client may race the bind() and fail with ConnectionRefusedError.
    for _ in range(200):  # ~10 s max
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:
        task.cancel()
        raise RuntimeError("uvicorn did not start within timeout")
    try:
        yield server
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


@pytest.fixture
def cert_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Generate a real self-signed CA + server cert for 127.0.0.1."""
    paths = tls.generate_self_signed(
        tmp_path, hostnames=["localhost"], ips=["127.0.0.1"]
    )
    return paths.cert_path, paths.key_path


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


def test_harden_tls_context_pins_1_2_floor_and_keeps_1_2_ciphers(cert_paths):
    """`_harden_tls_context` (the exact logic `_run_tls` applies) must enforce a
    TLS 1.2 floor AND leave usable TLS 1.2 ciphers.

    The second half is the real regression guard: uvicorn's default cipher
    string resolves to zero TLS 1.2 suites on modern OpenSSL, which would make
    the listener TLS-1.3-only and break TLS-1.2-only clients (older Android
    panels). Build the context the way uvicorn does (Config.load), then harden.
    """
    import ssl

    cert_path, key_path = cert_paths
    cfg = uvicorn.Config(
        _make_test_app(),
        host="127.0.0.1",
        port=_free_port(),
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        log_level="warning",
    )
    cfg.load()
    assert cfg.ssl is not None

    _harden_tls_context(cfg.ssl)

    assert cfg.ssl.minimum_version == ssl.TLSVersion.TLSv1_2
    tls12 = [c for c in cfg.ssl.get_ciphers() if c["protocol"] == "TLSv1.2"]
    assert tls12, "no usable TLS 1.2 ciphers — TLS-1.2-only clients would fail"


@pytest.mark.asyncio
async def test_https_serves_through_real_uvicorn(cert_paths):
    """A real uvicorn.Server with the generated cert serves HTTPS."""
    cert_path, key_path = cert_paths
    tls_port = _free_port()
    tls_server = uvicorn.Server(uvicorn.Config(
        _make_test_app(),
        host="127.0.0.1",
        port=tls_port,
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        log_level="warning",
    ))

    async with _running_server(tls_server):
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(f"https://127.0.0.1:{tls_port}/api/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_http_redirect_to_https_get(cert_paths):
    """GET on the redirect listener returns 301 with the right Location."""
    cert_path, key_path = cert_paths
    tls_port = _free_port()
    redirect_port = _free_port()

    tls_server = uvicorn.Server(uvicorn.Config(
        _make_test_app(),
        host="127.0.0.1",
        port=tls_port,
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        log_level="warning",
    ))
    redirect_server = uvicorn.Server(uvicorn.Config(
        _build_redirect_app(tls_port),
        host="127.0.0.1",
        port=redirect_port,
        log_level="warning",
    ))

    async with _running_server(tls_server), _running_server(redirect_server):
        async with httpx.AsyncClient(verify=False, follow_redirects=False) as client:
            resp = await client.get(f"http://127.0.0.1:{redirect_port}/api/health")
            assert resp.status_code == 302
            assert resp.headers["location"] == f"https://127.0.0.1:{tls_port}/api/health"
            assert resp.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_http_redirect_preserves_query_string(cert_paths):
    """Query string survives the redirect verbatim."""
    cert_path, key_path = cert_paths
    tls_port = _free_port()
    redirect_port = _free_port()

    redirect_server = uvicorn.Server(uvicorn.Config(
        _build_redirect_app(tls_port),
        host="127.0.0.1",
        port=redirect_port,
        log_level="warning",
    ))

    async with _running_server(redirect_server):
        async with httpx.AsyncClient(verify=False, follow_redirects=False) as client:
            resp = await client.get(
                f"http://127.0.0.1:{redirect_port}/api/devices?foo=bar&baz=1"
            )
            assert resp.status_code == 302
            assert resp.headers["location"] == (
                f"https://127.0.0.1:{tls_port}/api/devices?foo=bar&baz=1"
            )


@pytest.mark.asyncio
async def test_http_redirect_post_uses_307(cert_paths):
    """POST gets 307 (not 302) so the method is preserved on redirect."""
    cert_path, key_path = cert_paths
    tls_port = _free_port()
    redirect_port = _free_port()

    redirect_server = uvicorn.Server(uvicorn.Config(
        _build_redirect_app(tls_port),
        host="127.0.0.1",
        port=redirect_port,
        log_level="warning",
    ))

    async with _running_server(redirect_server):
        async with httpx.AsyncClient(verify=False, follow_redirects=False) as client:
            resp = await client.post(
                f"http://127.0.0.1:{redirect_port}/api/echo",
                json={"action": "on"},
            )
            assert resp.status_code == 307
            assert resp.headers["location"] == (
                f"https://127.0.0.1:{tls_port}/api/echo"
            )


@pytest.mark.asyncio
async def test_redirect_follows_through_to_https(cert_paths):
    """End-to-end: client following the 302 lands on the HTTPS server."""
    cert_path, key_path = cert_paths
    tls_port = _free_port()
    redirect_port = _free_port()

    tls_server = uvicorn.Server(uvicorn.Config(
        _make_test_app(),
        host="127.0.0.1",
        port=tls_port,
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        log_level="warning",
    ))
    redirect_server = uvicorn.Server(uvicorn.Config(
        _build_redirect_app(tls_port),
        host="127.0.0.1",
        port=redirect_port,
        log_level="warning",
    ))

    async with _running_server(tls_server), _running_server(redirect_server):
        async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
            resp = await client.get(f"http://127.0.0.1:{redirect_port}/api/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
            # Confirm the request actually transitioned to HTTPS, not just
            # got served by the redirect listener returning 200 somehow.
            assert str(resp.url).startswith(f"https://127.0.0.1:{tls_port}/")


# ---------------------------------------------------------------------------
# Cloud cert: SNI dual-serve + hot swap through a real listener
# ---------------------------------------------------------------------------


def _pem_to_der(cert_pem: bytes) -> bytes:
    return x509.load_pem_x509_certificate(cert_pem).public_bytes(
        serialization.Encoding.DER
    )


async def _served_cert_der(port: int, server_hostname: str | None) -> bytes:
    """Handshake with the listener and return the DER of the cert it served.

    ``server_hostname=None`` connects by bare IP, which sends no SNI — the
    same as every ``https://192.168.x.x`` client.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    kwargs: dict = {"ssl": ctx}
    if server_hostname is not None:
        kwargs["server_hostname"] = server_hostname
    _reader, writer = await asyncio.open_connection("127.0.0.1", port, **kwargs)
    try:
        return writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


@pytest.mark.asyncio
async def test_sni_dual_serve_and_hot_swap(cert_paths, tmp_path):
    """One listener, two certs: SNI on the cloud names gets the cloud cert,
    everything else keeps the self-signed leaf; installing a renewal swaps
    the served cert with no restart. Mirrors the _run_tls() wiring exactly
    (Config.load() + sni_callback on config.ssl)."""
    cert_path, key_path = cert_paths
    label, zone = "ab12cd34ef56ab78", "i.certtest.invalid"
    cloud1_pem, cloud1_key = make_cloud_cert_pem(label, zone)
    tls.install_cloud_cert(tmp_path, cloud1_pem, cloud1_key)

    try:
        tls_port = _free_port()
        config = uvicorn.Config(
            _make_test_app(),
            host="127.0.0.1",
            port=tls_port,
            ssl_certfile=str(cert_path),
            ssl_keyfile=str(key_path),
            log_level="warning",
        )
        config.load()
        config.ssl.sni_callback = tls.make_sni_callback(tls.cloud_cert_holder())
        server = uvicorn.Server(config)

        self_signed_der = _pem_to_der(cert_path.read_bytes())
        cloud1_der = _pem_to_der(cloud1_pem)

        async with _running_server(server):
            # Zone names (wildcard + bare label) get the cloud cert.
            assert await _served_cert_der(tls_port, f"192-168-1-20.{label}.{zone}") == cloud1_der
            assert await _served_cert_der(tls_port, f"{label}.{zone}") == cloud1_der
            # Other SNI and no SNI (bare IP) keep the self-signed leaf.
            assert await _served_cert_der(tls_port, "openavc.local") == self_signed_der
            assert await _served_cert_der(tls_port, None) == self_signed_der

            # Hot swap: install a renewal (fresh key, same names) — the next
            # handshake serves it, no restart.
            cloud2_pem, cloud2_key = make_cloud_cert_pem(label, zone)
            tls.install_cloud_cert(tmp_path, cloud2_pem, cloud2_key)
            cloud2_der = _pem_to_der(cloud2_pem)
            assert cloud2_der != cloud1_der
            assert await _served_cert_der(tls_port, f"a.{label}.{zone}") == cloud2_der
            assert await _served_cert_der(tls_port, None) == self_signed_der

            # HTTP still answers on both paths after the swap.
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(f"https://127.0.0.1:{tls_port}/api/health")
                assert resp.status_code == 200

            # Disable: remove the cert — zone names fall back to self-signed.
            tls.remove_cloud_cert(tmp_path)
            assert await _served_cert_der(tls_port, f"a.{label}.{zone}") == self_signed_der
    finally:
        tls.cloud_cert_holder().clear()
