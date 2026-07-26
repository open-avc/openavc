"""Regression tests for the discovery config API (server/api/discovery.py).

The SNMP community string is a credential: GET /api/discovery/config must
never return its value (it used to return a masked "****", which the
settings form loaded into state and echoed back verbatim on save or scan,
silently replacing the stored community and breaking SNMP enrichment on any
install with a non-default community). The endpoint now reports a
``snmp_community_set`` boolean instead, and PUT /config and POST /scan treat
an omitted ``snmp_community`` as "keep the stored value".
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.api import discovery as discovery_api


def _make_stub_engine(community: str = "s3cret"):
    engine = MagicMock()
    engine.config = {
        "snmp_enabled": True,
        "snmp_community": community,
        "gentle_mode": False,
        "scan_depth": "standard",
        "max_subnet_size": 20,
    }
    engine.start_scan = AsyncMock(return_value="scan-1")
    engine.get_status.return_value = {
        "status": "running",
        "subnets": ["192.168.1.0/24"],
        "started_at": 0.0,
        "budget_seconds": 300.0,
    }
    return engine


@pytest.fixture
def client():
    engine = _make_stub_engine()
    discovery_api.set_discovery_engine(engine)
    yield TestClient(app), engine
    discovery_api.set_discovery_engine(None)


def test_get_config_never_returns_community_value(client):
    c, _engine = client
    body = c.get("/api/discovery/config").json()
    assert "snmp_community" not in body
    assert body["snmp_community_set"] is True


def test_get_config_reports_unset_community(client):
    c, engine = client
    engine.config["snmp_community"] = ""
    body = c.get("/api/discovery/config").json()
    assert "snmp_community" not in body
    assert body["snmp_community_set"] is False


def test_put_config_without_community_keeps_stored_value(client):
    c, engine = client
    resp = c.put(
        "/api/discovery/config",
        json={"snmp_enabled": False, "gentle_mode": True, "scan_depth": "quick", "max_subnet_size": 22},
    )
    assert resp.status_code == 200
    assert engine.config["snmp_community"] == "s3cret"
    assert engine.config["snmp_enabled"] is False
    assert engine.config["gentle_mode"] is True


def test_put_config_with_community_updates_it(client):
    c, engine = client
    resp = c.put(
        "/api/discovery/config",
        json={"snmp_enabled": True, "snmp_community": "campus-ro"},
    )
    assert resp.status_code == 200
    assert engine.config["snmp_community"] == "campus-ro"


def test_settings_round_trip_preserves_community(client):
    """The corruption path: load settings, save them back unchanged."""
    c, engine = client
    body = c.get("/api/discovery/config").json()
    resp = c.put("/api/discovery/config", json=body)
    assert resp.status_code == 200
    assert engine.config["snmp_community"] == "s3cret"


def test_scan_without_community_keeps_stored_value(client):
    c, engine = client
    resp = c.post("/api/discovery/scan", json={})
    assert resp.status_code == 200
    assert engine.config["snmp_community"] == "s3cret"
    engine.start_scan.assert_awaited()


def test_scan_with_community_updates_it(client):
    c, engine = client
    resp = c.post("/api/discovery/scan", json={"snmp_community": "campus-ro"})
    assert resp.status_code == 200
    assert engine.config["snmp_community"] == "campus-ro"
