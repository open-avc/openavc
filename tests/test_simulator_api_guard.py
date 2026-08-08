"""Regression: the simulator dev API must reject browser-driven cross-site and
DNS-rebinding access.

The control API is unauthenticated and can shut the process down or mutate
device state, so a page the AV designer merely visits while the simulator runs
must not be able to drive it. The guard lives in openavc/simulator/server.py.

No real device involved — this exercises the platform's request guard.

Signal in the httpx tests: the app has no manager wired, so a request the guard
ALLOWS through reaches the route and returns 503 ("Simulator engine not
initialized"); a request the guard BLOCKS returns 403 and never reaches the
route (so /api/shutdown never actually kills anything).
"""

import httpx
import pytest
from httpx import ASGITransport

from openavc.simulator.server import LoopbackGuardMiddleware, app


def _client() -> httpx.AsyncClient:
    # base_url sets Host to a loopback address, so these cases vary only Origin.
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1:19500",
    )


# ── Origin-based cross-site protection (Host is loopback) ──


@pytest.mark.parametrize("origin", ["http://evil.com", "https://evil.com:8443", "null"])
async def test_cross_site_origin_blocked(origin):
    async with _client() as client:
        resp = await client.post("/api/shutdown", headers={"origin": origin})
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:19500",   # same-origin
        "http://localhost:19500",   # loopback by name
        "http://localhost:5173",    # loopback dev server (e.g. Vite)
    ],
)
async def test_loopback_origin_allowed(origin):
    async with _client() as client:
        resp = await client.post("/api/shutdown", headers={"origin": origin})
    # Reached the route (no manager) rather than being blocked by the guard.
    assert resp.status_code == 503


async def test_no_origin_allowed():
    # Non-browser clients (curl, other tooling) send no Origin and pass.
    async with _client() as client:
        resp = await client.post("/api/shutdown")
    assert resp.status_code == 503


async def test_safe_get_without_origin_allowed():
    async with _client() as client:
        resp = await client.get("/api/status")
    assert resp.status_code == 503


# ── Host-based (DNS-rebinding) protection and same-origin LAN binds ──
#
# These drive the ASGI middleware directly so the Host header can be set freely.


async def _drive(headers: dict, scope_type: str = "http", method: str = "POST"):
    """Run one request through the guard; return (reached_downstream, sent_msgs)."""
    reached = {"v": False}

    async def downstream(scope, receive, send):
        reached["v"] = True
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        else:
            await send({"type": "websocket.accept"})

    sent: list = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request" if scope_type == "http" else "websocket.connect"}

    scope = {
        "type": scope_type,
        "method": method,
        "path": "/api/shutdown",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }
    await LoopbackGuardMiddleware(downstream)(scope, receive, send)
    return reached["v"], sent


def _status(sent: list) -> int:
    return next(m for m in sent if m["type"] == "http.response.start")["status"]


async def test_dns_rebinding_host_blocked():
    # A rebinding attack must put its own domain in the Host header.
    reached, sent = await _drive({"host": "attacker.example.com:19500"})
    assert reached is False
    assert _status(sent) == 403


async def test_lan_bound_same_origin_allowed():
    # `--host 192.168.1.50`: same-origin UI access must keep working.
    reached, _ = await _drive(
        {"host": "192.168.1.50:19500", "origin": "http://192.168.1.50:19500"}
    )
    assert reached is True


async def test_lan_bound_cross_site_blocked():
    reached, sent = await _drive(
        {"host": "192.168.1.50:19500", "origin": "http://evil.com"}
    )
    assert reached is False
    assert _status(sent) == 403


async def test_websocket_cross_site_origin_closed():
    reached, sent = await _drive(
        {"host": "127.0.0.1:19500", "origin": "http://evil.com"},
        scope_type="websocket",
        method="GET",
    )
    assert reached is False
    assert sent == [{"type": "websocket.close", "code": 1008}]


async def test_websocket_loopback_origin_allowed():
    reached, _ = await _drive(
        {"host": "127.0.0.1:19500", "origin": "http://localhost:5173"},
        scope_type="websocket",
        method="GET",
    )
    assert reached is True
