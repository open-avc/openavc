"""Regression tests for the driver/device test endpoints' request limits.

Two hardening fixes on the live-test paths:

- ``TestCommandRequest.timeout`` is bounded. It was an unbounded float, so
  a single request could hold an open socket/serial port (and the device's
  only TCP control session) for an arbitrarily long wait.

- HTTPS probes verify certificates the way the runtime transport does. The
  raw HTTP test and the device reachability check hardcoded
  ``verify=False``; the HTTP transport defaults to verification with a
  per-device ``verify_ssl`` config opt-out (base.py), so the tests now
  mirror that instead of silently trusting any certificate.
"""
from __future__ import annotations

import ssl
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openavc.api.models import TestCommandRequest
from openavc.api.routes import devices as devices_routes
from openavc.api.routes.driver_test import _test_http_raw
from openavc.core.project_loader import DeviceConfig


# --- timeout bounds ----------------------------------------------------------

def test_timeout_defaults_to_five_seconds():
    assert TestCommandRequest().timeout == 5.0


def test_timeout_rejects_unbounded_values():
    with pytest.raises(ValidationError):
        TestCommandRequest(timeout=1e9)
    with pytest.raises(ValidationError):
        TestCommandRequest(timeout=0)
    with pytest.raises(ValidationError):
        TestCommandRequest(timeout=-5)


def test_timeout_accepts_reasonable_values():
    assert TestCommandRequest(timeout=30).timeout == 30
    assert TestCommandRequest(timeout=60).timeout == 60


# --- TLS verification --------------------------------------------------------

class _FakeClient:
    def __init__(self, captured: dict, **kwargs):
        captured.update(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url):
        return SimpleNamespace(status_code=200, text="ok")

    async def head(self, url):
        return SimpleNamespace(status_code=200)


@pytest.fixture()
def captured_httpx(monkeypatch) -> dict:
    captured: dict = {}
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: _FakeClient(captured, **kwargs)
    )
    return captured


async def test_raw_http_test_verifies_tls_by_default(captured_httpx):
    body = TestCommandRequest(
        host="10.0.0.5", port=443, transport="http", command_string="GET /status",
        ssl=True,
    )
    result = await _test_http_raw(body)
    assert result["success"] is True
    # The transport carries verify (httpx ignores the client's once handed one).
    tls = captured_httpx["transport"]._pool._ssl_context
    assert tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname


async def test_raw_http_test_honors_verify_ssl_opt_out(captured_httpx):
    body = TestCommandRequest(
        host="10.0.0.5", port=443, transport="http",
        command_string="GET /status", ssl=True, verify_ssl=False,
    )
    await _test_http_raw(body)
    assert captured_httpx["transport"]._pool._ssl_context.verify_mode == ssl.CERT_NONE


@pytest.mark.parametrize(
    ("host", "port", "use_ssl", "sent"),
    [
        # HTTPS on a port of the device's own, as its driver reaches it.
        ("10.0.0.5", 8443, True, "GET https://10.0.0.5:8443/status"),
        # Port 443 alone does not make it HTTPS: the driver's ssl setting does.
        ("10.0.0.5", 443, False, "GET http://10.0.0.5:443/status"),
        ("10.0.0.5", "", True, "GET https://10.0.0.5:443/status"),
        ("2001:db8::5", 8080, False, "GET http://[2001:db8::5]:8080/status"),
    ],
)
async def test_raw_http_probe_goes_where_the_driver_connects(
    captured_httpx, host, port, use_ssl, sent,
):
    body = TestCommandRequest(
        host=host, port=port, transport="http", command_string="GET /status", ssl=use_ssl,
    )
    result = await _test_http_raw(body)
    assert result["sent"] == sent


class _FakeTcp:
    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        return b"OK\r"

    async def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("use_ssl", "verify_ssl"),
    [(False, True), (True, True), (True, False)],
)
async def test_raw_tcp_probe_wraps_tls_as_the_driver_does(monkeypatch, use_ssl, verify_ssl):
    """A driver that turns on ssl talks TLS over TCP; a raw probe of it does too,
    checking the certificate unless the driver turns that off."""
    from openavc.api.routes.driver_test import _test_raw
    from openavc.transport import tcp

    opened: list[dict] = []

    async def create(**kwargs):
        opened.append(kwargs)
        return _FakeTcp()

    monkeypatch.setattr(tcp.TCPTransport, "create", staticmethod(create))
    body = TestCommandRequest(
        host="10.0.0.5", port=4352, transport="tcp", command_string="%1POWR ?\r",
        ssl=use_ssl, verify_ssl=verify_ssl,
    )

    result = await _test_raw(body)

    assert result["success"] is True, result
    assert (opened[0].get("ssl"), opened[0].get("ssl_verify")) == (use_ssl, verify_ssl)


async def test_raw_http_probe_path_without_a_slash(captured_httpx):
    body = TestCommandRequest(
        host="10.0.0.5", port=80, transport="http", command_string="GET status",
    )
    result = await _test_http_raw(body)
    assert result["sent"] == "GET http://10.0.0.5:80/status"


async def test_raw_http_probe_refuses_a_port_that_is_not_a_number():
    body = TestCommandRequest(
        host="10.0.0.5", port="web", transport="http", command_string="GET /status",
    )
    with pytest.raises(HTTPException) as caught:
        await _test_http_raw(body)
    assert caught.value.status_code == 422
    assert "'web'" in caught.value.detail


def _engine_with_http_device(device_id: str, config: dict):
    # A device record the way a project holds one; its driver is not
    # installed, so the transport and URL come from the config alone.
    device = DeviceConfig(id=device_id, driver="acme_unregistered", name=device_id, config=config)
    project = SimpleNamespace(devices=[device], connections={})
    return SimpleNamespace(project=project)


async def test_device_reachability_verifies_tls_by_default(
    captured_httpx, monkeypatch
):
    # Distinct device ids per test: the endpoint rate-limits per device.
    monkeypatch.setattr(
        devices_routes,
        "_get_engine",
        lambda: _engine_with_http_device(
            "cam_default", {"transport": "http", "base_url": "https://10.0.0.9"}
        ),
    )
    result = await devices_routes.test_device_connection("cam_default")
    assert result["success"] is True
    assert captured_httpx["verify"] is True


async def test_device_reachability_honors_verify_ssl_config(
    captured_httpx, monkeypatch
):
    monkeypatch.setattr(
        devices_routes,
        "_get_engine",
        lambda: _engine_with_http_device(
            "cam_optout",
            {"transport": "http", "base_url": "https://10.0.0.9", "verify_ssl": False},
        ),
    )
    await devices_routes.test_device_connection("cam_optout")
    assert captured_httpx["verify"] is False


# --- test-panel "what was sent" summary --------------------------------------

def test_describe_outgoing_substitutes_osc_arg_values():
    """The OSC summary must show resolved arg values like the wire does.

    The address was substituted but arg values showed the raw {placeholder}
    template, contradicting the summary's purpose.
    """
    from openavc.api.routes.driver_test import _describe_outgoing

    cmd_def = {
        "address": "/ch/{channel}/level",
        "args": [{"type": "f", "value": "{level}"}],
    }
    summary = _describe_outgoing(cmd_def, {}, {"channel": 3, "level": 0.5})
    assert summary == "OSC /ch/3/level [f=0.5]"


def test_describe_outgoing_names_an_untyped_osc_arg_as_the_encoder_sends_it():
    """An arg that names no type goes out as a float, so the summary says f."""
    from openavc.api.routes.driver_test import _describe_outgoing

    cmd_def = {"address": "/gain", "args": [{"value": "{level}"}]}
    summary = _describe_outgoing(cmd_def, {}, {"level": 2})
    assert summary == "OSC /gain [f=2]"
