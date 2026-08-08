"""Guards for the /api/health payload.

The health endpoint is the cheap, unauthenticated poll surface used by
monitoring tools, container orchestration, and the Windows system tray. It
must surface the server's cached available-update version (maintained by the
periodic auto-check) so those callers can passively see an update without
each of them triggering their own network check.
"""

import asyncio

import pytest

from openavc.api import _engine as engine_mod
from openavc.api.routes.system import health_check


class _FakeState:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _FakeDevices:
    def list_devices(self):
        return []


class _FakeEngine:
    def __init__(self, update_available=""):
        self.state = _FakeState({"system.update_available": update_available})
        self.devices = _FakeDevices()

    def get_status(self, include_sensitive=True):
        return {"version": "0.23.0", "uptime_seconds": 1.0, "cloud_connected": False}


@pytest.fixture
def inject_engine():
    saved = engine_mod._engine
    yield engine_mod.set_engine
    engine_mod.set_engine(saved)


def test_health_reports_cached_update_available(inject_engine):
    inject_engine(_FakeEngine(update_available="0.24.0"))
    result = asyncio.run(health_check())
    assert result["update_available"] == "0.24.0"


def test_health_update_available_empty_when_up_to_date(inject_engine):
    inject_engine(_FakeEngine(update_available=""))
    result = asyncio.run(health_check())
    assert result["status"] == "healthy"
    # Field is always present (empty string) so callers can rely on it.
    assert result["update_available"] == ""
