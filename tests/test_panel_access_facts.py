"""The two facts the Programmer's Panel Access card is built from.

Every address that card publishes is a LAN address. It therefore has to know
two things it cannot work out for itself, and both are guarded here:

- whether this request arrived over the cloud tunnel, because a reader on the
  far side of one is not on that LAN and none of the addresses can reach it;
- whether the host's detected address is real, because a server started before
  its adapter came up detects loopback, and that answer used to be cached for
  the life of the process and published as the address to type at a panel.
"""

import asyncio

import pytest
from starlette.requests import Request

from openavc.api import _engine as engine_mod
from openavc.api.routes.system import get_status
from openavc.core.engine import Engine


class _FakeDevices:
    def list_devices(self):
        return []


class _FakeEngine:
    def __init__(self):
        self.devices = _FakeDevices()

    def get_status(self, include_sensitive=True):
        status = {"version": "0.33.0", "uptime_seconds": 1.0}
        if include_sensitive:
            status["local_ip"] = "192.168.1.50"
            status["bind_address"] = "0.0.0.0"
        return status


@pytest.fixture
def inject_engine():
    saved = engine_mod._engine
    engine_mod.set_engine(_FakeEngine())
    yield
    engine_mod.set_engine(saved)


def _request(client_host: str, headers: dict[str, str] | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/status",
        "client": (client_host, 51000),
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
    })


def _status(client_host: str, headers: dict[str, str] | None = None) -> dict:
    return asyncio.run(get_status(_request(client_host, headers), None))


def test_status_reports_a_lan_request_as_not_tunneled(inject_engine):
    assert _status("192.168.1.10")["tunneled"] is False


def test_status_reports_a_tunneled_request(inject_engine):
    # The agent stamps this on everything it proxies in from the cloud.
    status = _status("127.0.0.1", {"X-OpenAVC-Tunneled": "1"})
    assert status["tunneled"] is True


def test_the_tunnel_marker_is_ignored_from_a_non_loopback_peer(inject_engine):
    # Only the tunnel can present it, because only the tunnel reaches us over
    # loopback. A LAN client asserting it gains nothing.
    status = _status("192.168.1.10", {"X-OpenAVC-Tunneled": "1"})
    assert status["tunneled"] is False


def test_status_always_carries_the_flag(inject_engine):
    # The card branches on it, so it may never be absent.
    assert "tunneled" in _status("127.0.0.1")


class _CacheProbe:
    """Just enough of an Engine for the detection method under test."""

    _network_info = None


def test_a_detected_address_is_cached(monkeypatch):
    calls = []

    def _ranked():
        calls.append(1)
        return ["192.168.1.50"]

    monkeypatch.setattr(
        "openavc.core.engine.network_scanner.get_ranked_interface_ips", _ranked
    )
    probe = _CacheProbe()
    first = Engine._detect_network_info(probe)
    second = Engine._detect_network_info(probe)
    assert first[0] == "192.168.1.50"
    assert second == first
    assert len(calls) == 1


def test_a_failed_detection_is_not_cached(monkeypatch):
    """A host with no address yet must be asked again, not pinned to loopback."""
    answers = [[], [], ["192.168.1.50"]]

    monkeypatch.setattr(
        "openavc.core.engine.network_scanner.get_ranked_interface_ips",
        lambda: answers.pop(0),
    )
    probe = _CacheProbe()
    assert Engine._detect_network_info(probe)[0] == "127.0.0.1"
    assert Engine._detect_network_info(probe)[0] == "127.0.0.1"
    # The adapter comes up, and the very next status poll says so.
    assert Engine._detect_network_info(probe)[0] == "192.168.1.50"
    assert probe._network_info is not None
