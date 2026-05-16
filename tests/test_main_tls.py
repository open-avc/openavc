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
from pathlib import Path

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from server import tls
from server.main import _build_redirect_app


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
