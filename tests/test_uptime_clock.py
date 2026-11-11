"""Uptime counts how long the system has run, whatever the wall clock does.

A computer with no battery-backed clock starts at whatever time it last saved
and is set right only once network time answers, which can move the wall clock
by days a few minutes after boot. Uptime measured against the wall clock jumps
with it: such a system reported eleven days up eleven minutes after it booted.
Both places that report uptime (``/api/status`` and ``/api/health`` through
Engine.get_status, and the cloud heartbeat) have to read a clock that only
moves forward at the rate time passes.
"""

from __future__ import annotations

import json
import time

import pytest

from openavc.cloud.heartbeat import HeartbeatCollector
from openavc.core.engine import Engine
from openavc.core.state_store import StateStore

ELEVEN_DAYS = 11 * 24 * 3600


def _wall_clock_set_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network time arrives and moves the wall clock eleven days on."""
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + ELEVEN_DAYS)


async def _nothing(*_a, **_k):
    return None


@pytest.fixture
def quiet_engine(tmp_path, monkeypatch):
    from openavc.updater.manager import UpdateManager

    monkeypatch.setattr(UpdateManager, "start_auto_check", _nothing)
    monkeypatch.setattr(UpdateManager, "stop_auto_check", _nothing)
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "version": "0.16.0",
        "project": {"id": "p", "name": "Uptime"},
        "devices": [], "device_groups": [], "connections": {},
        "ui": {"settings": {}, "pages": []},
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }), encoding="utf-8")
    engine = Engine(str(path))
    engine._start_mdns_advertiser = _nothing
    engine._start_cloud_agent = _nothing
    return engine


@pytest.mark.asyncio
async def test_status_uptime_ignores_the_wall_clock_being_set(quiet_engine, monkeypatch):
    await quiet_engine.start()
    try:
        with monkeypatch.context() as m:
            _wall_clock_set_forward(m)
            uptime = quiet_engine.get_status()["uptime_seconds"]
        assert 0 <= uptime < 60
    finally:
        await quiet_engine.stop()


class _NoDevices:
    def list_devices(self):
        return []


@pytest.mark.asyncio
async def test_heartbeat_uptime_ignores_the_wall_clock_being_set(monkeypatch):
    collector = HeartbeatCollector(StateStore(), _NoDevices(), start_time=time.monotonic())
    _wall_clock_set_forward(monkeypatch)
    metrics = await collector.collect()
    assert 0 <= metrics["uptime_seconds"] < 5


@pytest.mark.asyncio
async def test_heartbeat_without_a_start_time_also_ignores_the_wall_clock(monkeypatch):
    collector = HeartbeatCollector(StateStore(), _NoDevices())
    _wall_clock_set_forward(monkeypatch)
    metrics = await collector.collect()
    assert 0 <= metrics["uptime_seconds"] < 5
