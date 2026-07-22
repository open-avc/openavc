"""Regression tests for HTTP transport port resolution in BaseDriver (A66).

The sentinel default `self.config.get("port", 80)` used to make explicit
`port: 80, ssl: true` indistinguishable from "port not set", so the next
branch silently rewrote it to 443. A user behind a reverse proxy that
terminates TLS upstream and serves HTTPS on :80 would never see their
config respected. The fix reads `port` without a default and falls back
to scheme-appropriate ports only when None.
"""

from unittest.mock import patch

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class


def _build_http_driver(host="192.0.2.1", port=None, ssl=False):
    definition = {
        "id": "http_test",
        "name": "HTTP Test",
        "manufacturer": "TestCo",
        "category": "test",
        "version": "1.0.0",
        "transport": "http",
        "default_config": {"host": host},
        "config_schema": {
            "host": {"type": "string", "required": True},
        },
        "state_variables": {},
        "commands": {},
        "responses": [],
    }
    config = {"host": host, "verify_timeout": 0}
    if port is not None:
        config["port"] = port
    if ssl:
        config["ssl"] = True

    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    return cls("dev1", config, state, events)


class _FakeTransport:
    """Captures base_url passed to HTTPClientTransport and no-ops everything else."""

    last_base_url: str | None = None
    last_max_response_bytes: object = None

    def __init__(self, *, base_url, max_response_bytes=None, **_kwargs):
        _FakeTransport.last_base_url = base_url
        _FakeTransport.last_max_response_bytes = max_response_bytes

    async def open(self):
        return None

    async def verify(self, timeout):
        return True

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def _reset_capture():
    _FakeTransport.last_base_url = None
    yield


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_explicit_port_80_with_ssl_is_honored():
    """Explicit port:80, ssl:true must build https://host:80, not :443 (A66)."""
    driver = _build_http_driver(port=80, ssl=True)
    await driver.connect()
    assert _FakeTransport.last_base_url == "https://192.0.2.1:80"


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_explicit_port_443_with_ssl_is_honored():
    """Explicit port:443, ssl:true builds https://host:443."""
    driver = _build_http_driver(port=443, ssl=True)
    await driver.connect()
    assert _FakeTransport.last_base_url == "https://192.0.2.1:443"


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_explicit_port_8080_with_ssl():
    """Non-standard HTTPS port (e.g., :8080 behind a reverse proxy)."""
    driver = _build_http_driver(port=8080, ssl=True)
    await driver.connect()
    assert _FakeTransport.last_base_url == "https://192.0.2.1:8080"


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_unset_port_with_ssl_defaults_to_443():
    """Port omitted + ssl:true falls back to 443."""
    driver = _build_http_driver(ssl=True)
    await driver.connect()
    assert _FakeTransport.last_base_url == "https://192.0.2.1:443"


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_unset_port_without_ssl_defaults_to_80():
    """Port omitted + ssl unset falls back to 80 (plain HTTP)."""
    driver = _build_http_driver()
    await driver.connect()
    assert _FakeTransport.last_base_url == "http://192.0.2.1:80"


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_explicit_port_80_plain_http():
    """Explicit port:80 without ssl is unchanged from the sentinel-default era."""
    driver = _build_http_driver(port=80, ssl=False)
    await driver.connect()
    assert _FakeTransport.last_base_url == "http://192.0.2.1:80"


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_max_response_bytes_defaults_when_config_omits_it():
    """No config key → the transport keeps the module default cap."""
    from server.transport.http_client import DEFAULT_MAX_RESPONSE_BYTES

    driver = _build_http_driver()
    await driver.connect()
    assert _FakeTransport.last_max_response_bytes == DEFAULT_MAX_RESPONSE_BYTES


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_max_response_bytes_from_config_flows_through():
    """A device that legitimately returns a larger body (firmware/log export)
    raises the cap via the config key — previously constructor-only."""
    driver = _build_http_driver()
    driver.config["max_response_bytes"] = 100 * 1024 * 1024
    await driver.connect()
    assert _FakeTransport.last_max_response_bytes == 100 * 1024 * 1024


@pytest.mark.asyncio
@patch("server.transport.http_client.HTTPClientTransport", _FakeTransport)
async def test_max_response_bytes_invalid_config_falls_back_to_default():
    """A junk value (non-numeric or non-positive) falls back to the default
    rather than passing a nonsense cap to the transport."""
    from server.transport.http_client import DEFAULT_MAX_RESPONSE_BYTES

    driver = _build_http_driver()
    driver.config["max_response_bytes"] = "lots"
    await driver.connect()
    assert _FakeTransport.last_max_response_bytes == DEFAULT_MAX_RESPONSE_BYTES

    driver2 = _build_http_driver()
    driver2.config["max_response_bytes"] = 0
    await driver2.connect()
    assert _FakeTransport.last_max_response_bytes == DEFAULT_MAX_RESPONSE_BYTES
