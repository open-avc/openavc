"""The local Test action reads current equipment without a driver or command replay."""

import copy

import httpx
import pytest

from openavc.core.installed_diagnostics import test_connection as check_connection
from openavc.drivers.equipment_protocol import MAX_MESSAGE_BYTES


@pytest.mark.parametrize("failure", [None, "identity", "model", "bad_model", "snapshot_identity", "auth", "unavailable", "malformed", "large", "timeout"])
async def test_local_diagnostic_reads_only_and_reports_actual_response(monkeypatch, failure):
    manifest = {"identity": "unit-a", "integration": "acme_amplifier", "connection": {
        "url": "https://local.invalid", "token": "fixture-private-token",
    }}
    original = copy.deepcopy(manifest)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET" and not request.content
        assert request.headers["Authorization"] == "Bearer fixture-private-token"
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture-private-token", request=request)
        if failure in {"auth", "unavailable"}:
            return httpx.Response(401 if failure == "auth" else 503, text="fixture-private-token")
        if failure == "malformed":
            return httpx.Response(200, text="fixture-private-token")
        if failure == "large":
            return httpx.Response(200, content=b"x" * (MAX_MESSAGE_BYTES + 1))
        if request.url.path == "/v1/description":
            return httpx.Response(200, json={
                "protocol": "openavc-device-1", "identity": "wrong" if failure == "identity" else "unit-a",
                "capabilities": [] if failure == "bad_model" else {"id": "wrong" if failure == "model" else "acme_amplifier"},
            })
        assert request.url.path == "/v1/snapshot"
        return httpx.Response(200, json={"identity": "wrong" if failure == "snapshot_identity" else "unit-a"})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    result = await check_connection(manifest)
    assert result["success"] is (failure is None)
    assert len(requests) == (2 if failure in {None, "snapshot_identity"} else 1)
    assert manifest == original
    assert "fixture-private-token" not in str(result)
    assert result["latency_ms"] is not None if failure is None else result["latency_ms"] is None


@pytest.mark.parametrize("config", [None, {}, [], {"url": 42, "token": "fixture"}, {"url": "https://local.invalid", "token": 42}])
async def test_invalid_local_connection_is_actionable_without_any_request(config):
    result = await check_connection({"identity": "unit-a", "connection": config})
    assert result["success"] is False
    assert "firmware settings" in result["error"]
