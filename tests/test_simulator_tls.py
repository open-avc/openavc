"""Tests for the simulator servers' optional self-signed TLS.

Some device APIs are HTTPS-only, so their drivers carry an https:// base URL and
no certificate verification. The simulator has to meet them there — these tests
prove a simulator that opts in serves TLS, one that doesn't still serves plain,
and the ephemeral cert files don't leak.

Invented device ("acme_widget") and synthetic payloads: this validates the
platform simulator capability, not any specific driver.
"""

from __future__ import annotations

import os
import ssl

import httpx
import pytest

from openavc.simulator.http_simulator import HTTPSimulator
from openavc.simulator.self_signed_tls import build_optional_tls, remove_cert_files, wants_tls


class _AcmeWidgetSim(HTTPSimulator):
    """A tiny invented HTTP device: one status endpoint over plain http."""

    SIMULATOR_INFO = {
        "driver_id": "acme_widget",
        "name": "Acme Widget Sim",
        "category": "generic",
        "transport": "http",
        "default_port": 80,
        "initial_state": {"power": True},
    }

    def handle_request(self, method, path, headers, body):
        if path == "/api/status":
            return 200, {"power": bool(self.get_state("power"))}
        return 404, {"error": "not found"}


class _AcmeWidgetTlsSim(_AcmeWidgetSim):
    """The same device, HTTPS-only — the posture of an NVX or a Dante Director."""

    SIMULATOR_INFO = {
        **_AcmeWidgetSim.SIMULATOR_INFO,
        "driver_id": "acme_widget_tls",
        "tls": True,
    }


async def _start(sim: HTTPSimulator, port: int) -> None:
    await sim.start(port)


# --- The shared helper itself ---


def test_wants_tls_reads_either_source():
    assert wants_tls({"tls": True}, {}) is True
    assert wants_tls({}, {"tls": True}) is True
    assert wants_tls({}, {}) is False
    assert wants_tls(None, None) is False


def test_build_optional_tls_is_inert_when_not_requested():
    ctx, files = build_optional_tls({"driver_id": "acme_widget"}, {}, "Acme")
    assert ctx is None
    assert files is None


def test_build_optional_tls_mints_a_server_context():
    ctx, files = build_optional_tls({"tls": True}, {}, "Acme")
    try:
        assert isinstance(ctx, ssl.SSLContext)
        # Accepts a client cert but never demands one.
        assert ctx.verify_mode == ssl.CERT_OPTIONAL
        assert files is not None and all(os.path.exists(p) for p in files)
    finally:
        remove_cert_files(files)
    assert not any(os.path.exists(p) for p in files)
    remove_cert_files(files)  # idempotent — safe after the files are gone


# --- HTTPSimulator wiring ---


async def test_http_sim_without_tls_still_serves_plain_http():
    sim = _AcmeWidgetSim("acme1")
    await _start(sim, 19731)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://127.0.0.1:19731/api/status")
        assert r.status_code == 200
        assert r.json() == {"power": True}
        assert sim._tls_files is None
    finally:
        await sim.stop()


async def test_http_sim_with_tls_serves_https():
    sim = _AcmeWidgetTlsSim("acme2")
    await _start(sim, 19732)
    try:
        # Same posture an HTTPS-only device's driver uses: TLS on, verification
        # off (the device ships a self-signed cert).
        async with httpx.AsyncClient(verify=False) as client:
            r = await client.get("https://127.0.0.1:19732/api/status")
        assert r.status_code == 200
        assert r.json() == {"power": True}
    finally:
        await sim.stop()


async def test_tls_sim_rejects_a_plain_http_request():
    """The scheme is not optional once TLS is on — a plain request must fail
    rather than quietly succeed, so a driver's declared scheme is really tested."""
    sim = _AcmeWidgetTlsSim("acme3")
    await _start(sim, 19733)
    try:
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPError):
                await client.get("http://127.0.0.1:19733/api/status", timeout=5.0)
    finally:
        await sim.stop()


async def test_tls_sim_removes_its_cert_files_on_stop():
    sim = _AcmeWidgetTlsSim("acme4")
    await _start(sim, 19734)
    files = sim._tls_files
    assert files is not None and all(os.path.exists(p) for p in files)
    await sim.stop()
    assert sim._tls_files is None
    assert not any(os.path.exists(p) for p in files)
