"""Unpair in the Programmer removes the system from the cloud portal too.

It used to clear the keys and stop the agent without a word to the cloud, so
the portal kept the system paired: offline, raising its offline alerts and
counting on the bill. Unpair now signs a request to the cloud with the key it
still holds, before forgetting it, and unpairs locally whatever the answer.
"""

import hashlib

import httpx
import pytest

import openavc.config as cfg
from openavc.api.routes import cloud as cloud_routes
from openavc.cloud.crypto import compute_hmac, derive_auth_key

SYSTEM_ID = "11111111-2222-3333-4444-555555555555"
SYSTEM_KEY = "ab" * 32


class _Agent:
    def __init__(self, order):
        self.order = order

    async def stop(self):
        self.order.append("agent stopped")


class _Engine:
    def __init__(self, order):
        self.cloud_agent = _Agent(order)


@pytest.fixture
def paired(monkeypatch):
    order: list[str] = []
    saved: list[dict] = []
    monkeypatch.setattr(cfg, "CLOUD_ENABLED", True)
    monkeypatch.setattr(cfg, "CLOUD_SYSTEM_ID", SYSTEM_ID)
    monkeypatch.setattr(cfg, "CLOUD_SYSTEM_KEY", SYSTEM_KEY)
    monkeypatch.setattr(cfg, "CLOUD_ENDPOINT", "wss://cloud.example.test/agent/v1")
    engine = _Engine(order)
    monkeypatch.setattr(cloud_routes, "_get_engine", lambda: engine)
    monkeypatch.setattr("openavc.cloud.config.save_cloud_config", saved.append)
    return order, saved, engine


def _cloud_answers(monkeypatch, handler):
    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)


def _cleared_locally(saved, engine):
    assert engine.cloud_agent is None
    assert cfg.CLOUD_ENABLED is False and cfg.CLOUD_SYSTEM_KEY == "" and cfg.CLOUD_SYSTEM_ID == ""
    assert saved and saved[-1]["system_key"] == "" and saved[-1]["enabled"] is False


@pytest.mark.asyncio
async def test_the_cloud_is_told_with_the_systems_signature_before_the_key_is_gone(paired, monkeypatch):
    order, saved, engine = paired
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        order.append("cloud told")
        return httpx.Response(204)

    _cloud_answers(monkeypatch, handler)
    result = await cloud_routes.cloud_unpair()

    assert result == {"status": "unpaired", "portal_removed": True}
    assert order == ["cloud told", "agent stopped"]
    (request,) = seen
    assert str(request.url) == "https://cloud.example.test/api/v1/systems/unpair"
    assert request.method == "POST"
    headers = request.headers
    assert headers["X-System-ID"] == SYSTEM_ID
    auth_key = derive_auth_key(bytes.fromhex(SYSTEM_KEY), SYSTEM_ID)
    message = (SYSTEM_ID + headers["X-Timestamp"] + hashlib.sha256(b"").hexdigest()).encode()
    assert headers["X-Signature"] == compute_hmac(auth_key, message)
    _cleared_locally(saved, engine)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 401, 500])
async def test_a_cloud_that_does_not_confirm_still_unpairs_here(paired, monkeypatch, status):
    # 404 is every cloud from before the door: the state of every install
    # until the cloud is deployed.
    order, saved, engine = paired
    _cloud_answers(monkeypatch, lambda request: httpx.Response(status))
    result = await cloud_routes.cloud_unpair()
    assert result == {"status": "unpaired", "portal_removed": False}
    _cleared_locally(saved, engine)


@pytest.mark.asyncio
async def test_no_connection_to_the_cloud_still_unpairs_here(paired, monkeypatch):
    order, saved, engine = paired

    def handler(request):
        raise httpx.ConnectError("no route to host", request=request)

    _cloud_answers(monkeypatch, handler)
    result = await cloud_routes.cloud_unpair()
    assert result["portal_removed"] is False
    _cleared_locally(saved, engine)
