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

from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from openavc.api.models import TestCommandRequest
from openavc.api.routes import devices as devices_routes
from openavc.api.routes.driver_test import _test_http_raw


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
        host="10.0.0.5", port=443, transport="http", command_string="GET /status"
    )
    result = await _test_http_raw(body)
    assert result["success"] is True
    assert captured_httpx["verify"] is True


async def test_raw_http_test_honors_verify_ssl_opt_out(captured_httpx):
    body = TestCommandRequest(
        host="10.0.0.5", port=443, transport="http",
        command_string="GET /status", verify_ssl=False,
    )
    await _test_http_raw(body)
    assert captured_httpx["verify"] is False


def _engine_with_http_device(device_id: str, config: dict):
    device = SimpleNamespace(id=device_id, config=config)
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

    definition = {"transport": "osc"}
    cmd_def = {
        "address": "/ch/{channel}/level",
        "args": [{"type": "f", "value": "{level}"}],
    }
    summary = _describe_outgoing(definition, cmd_def, {}, {"channel": 3, "level": 0.5})
    assert summary == "OSC /ch/3/level [f=0.5]"
