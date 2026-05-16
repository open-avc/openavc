"""Tests for /api/devices/missing-drivers and /api/devices/install-missing.

The running_app fixture spins up an Engine with a project containing two
orphaned devices (drivers not registered) plus one regular device, then
mocks the community-catalog fetch so the tests never touch GitHub.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from server.core.engine import Engine
from server.main import app
from server.api import rest, ws


# Project with two orphans (drivers don't exist) and one real device using
# the always-present generic_http built-in. The orphans give us realistic
# input for the missing-drivers endpoint.
TEST_PROJECT = {
    "project": {"id": "missing_test", "name": "Missing Drivers Test"},
    "devices": [
        {
            "id": "occupancy",
            "driver": "generic_http",
            "name": "Occupancy Sensor",
            "config": {"host": "127.0.0.1", "port": 80},
            "enabled": True,
        },
        {
            "id": "lobby_display",
            "driver": "samsung_mdc_test",
            "name": "Lobby Display",
            "config": {},
            "enabled": True,
        },
        {
            "id": "boardroom_display",
            "driver": "samsung_mdc_test",
            "name": "Boardroom Display",
            "config": {},
            "enabled": True,
        },
        {
            "id": "matrix",
            "driver": "extron_sis_test",
            "name": "Matrix Switcher",
            "config": {},
            "enabled": True,
        },
    ],
    "variables": [],
    "macros": [],
    "ui": {"pages": []},
}


# Mock community catalog: samsung_mdc_test is in the catalog (installable),
# extron_sis_test is not (uncatalogued — must be uploaded manually).
MOCK_CATALOG = [
    {
        "id": "samsung_mdc_test",
        "name": "Samsung MDC Display",
        "manufacturer": "Samsung",
        "category": "display",
        "file": "displays/samsung_mdc_test.py",
        "min_platform_version": None,
    },
    {
        "id": "lutron_homeworks_test",
        "name": "Lutron HomeWorks QS",
        "manufacturer": "Lutron",
        "category": "lighting",
        "file": "lighting/lutron_homeworks_test.avcdriver",
        "min_platform_version": None,
    },
]


@pytest.fixture
async def running_app():
    """Engine with two orphaned devices + one connected, plus catalog mocked."""
    # The TestClient hits the API as IP 'testclient', not 127.0.0.1, so the
    # middleware's localhost exemption doesn't apply. Earlier tests in the
    # suite leave entries in the per-IP rate-limit buckets that can spill
    # over and 429 our requests on CI. Clear before yielding.
    from server.middleware.rate_limit import _ip_buckets
    _ip_buckets.clear()

    project = json.loads(json.dumps(TEST_PROJECT))
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(project, f)
        tmp_path = f.name

    engine = Engine(tmp_path)
    from server.core.project_loader import load_project
    engine.project = load_project(tmp_path)

    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device))
    engine._running = True

    rest.set_engine(engine)
    ws.set_engine(engine)

    # Patch the community-index fetch to skip the real GitHub call. Both
    # endpoints construct their own CommunityIndexCache, so we patch the
    # cache class's get_drivers method.
    with patch(
        "server.discovery.community_index.CommunityIndexCache.get_drivers",
        new=AsyncMock(return_value=MOCK_CATALOG),
    ):
        yield TestClient(app)

    await engine.devices.disconnect_all()
    Path(tmp_path).unlink(missing_ok=True)
    rest.set_engine(None)
    ws.set_engine(None)


async def test_missing_drivers_lists_unique_ids(running_app):
    """Two devices share samsung_mdc_test; result has one entry per unique
    driver, with both device IDs populated under it."""
    resp = running_app.get("/api/devices/missing-drivers")
    assert resp.status_code == 200
    items = resp.json()["missing"]
    assert len(items) == 2

    by_id = {m["driver_id"]: m for m in items}
    assert sorted(by_id["samsung_mdc_test"]["device_ids"]) == [
        "boardroom_display", "lobby_display"
    ]
    assert by_id["extron_sis_test"]["device_ids"] == ["matrix"]


async def test_missing_drivers_annotates_community_match(running_app):
    """samsung_mdc_test is in the mock catalog -> community_match populated.
    extron_sis_test isn't -> community_match is null."""
    resp = running_app.get("/api/devices/missing-drivers")
    items = resp.json()["missing"]
    by_id = {m["driver_id"]: m for m in items}

    samsung_match = by_id["samsung_mdc_test"]["community_match"]
    assert samsung_match is not None
    assert samsung_match["name"] == "Samsung MDC Display"
    assert samsung_match["manufacturer"] == "Samsung"
    assert samsung_match["file_url"].endswith("displays/samsung_mdc_test.py")

    assert by_id["extron_sis_test"]["community_match"] is None


async def test_missing_drivers_route_does_not_collide_with_device_id(running_app):
    """Regression guard: the literal /devices/missing-drivers route must be
    declared before /devices/{device_id} or it would 404 as 'device not
    found'. This was the live-server bug seen during development."""
    resp = running_app.get("/api/devices/missing-drivers")
    assert resp.status_code == 200
    assert "missing" in resp.json()


async def test_install_missing_reports_uncatalogued_failures(running_app):
    """A driver_id not in the catalog goes into `failed` with a clear
    reason; the rest of the batch isn't aborted."""
    # Skip the actual GitHub download by also mocking install_community_driver
    with patch(
        "server.api.routes.drivers.install_community_driver",
        new=AsyncMock(return_value={"status": "installed"}),
    ):
        resp = running_app.post(
            "/api/devices/install-missing",
            json={"driver_ids": ["samsung_mdc_test", "extron_sis_test"]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] == ["samsung_mdc_test"]
    assert len(body["failed"]) == 1
    assert body["failed"][0]["driver_id"] == "extron_sis_test"
    assert "catalog" in body["failed"][0]["error"].lower()


async def test_install_missing_empty_batch_returns_empty_lists(running_app):
    """An empty driver_ids list short-circuits without hitting the catalog."""
    resp = running_app.post(
        "/api/devices/install-missing",
        json={"driver_ids": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"installed": [], "failed": [], "activated_devices": []}


async def test_install_missing_activates_orphans(running_app):
    """End-to-end happy path: install registers the driver, retry sweep
    promotes the matching orphan, response lists it under activated_devices."""
    from server.core.device_manager import _DRIVER_REGISTRY
    from server.drivers.base import BaseDriver

    class _MockSamsung(BaseDriver):
        DRIVER_INFO = {
            "id": "samsung_mdc_test",
            "name": "Samsung MDC",
            "manufacturer": "Samsung",
            "category": "display",
            "transport": "tcp",
            "default_config": {},
            "commands": {},
            "state_variables": {},
            "config_schema": {},
        }

        async def connect(self):
            self._connected = True
            self.state.set(f"device.{self.device_id}.connected", True, source="driver")

        async def disconnect(self):
            self._connected = False

        async def send_command(self, command, params=None):
            pass

        async def stop_polling(self):
            pass

    # Stub install_community_driver so it just registers our mock driver,
    # no GitHub fetch.
    async def fake_install(req):
        _DRIVER_REGISTRY["samsung_mdc_test"] = _MockSamsung
        return {"status": "installed", "driver_id": req.driver_id, "file": "x"}

    with patch(
        "server.api.routes.drivers.install_community_driver",
        new=fake_install,
    ):
        try:
            resp = running_app.post(
                "/api/devices/install-missing",
                json={"driver_ids": ["samsung_mdc_test"]},
            )
        finally:
            _DRIVER_REGISTRY.pop("samsung_mdc_test", None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] == ["samsung_mdc_test"]
    # Both orphaned displays should have come online
    assert sorted(body["activated_devices"]) == [
        "boardroom_display", "lobby_display"
    ]
