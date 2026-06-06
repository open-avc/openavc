"""Tests for SimulationManager payload parity and incremental sync().

Covers the hardening of the openavc-side simulator lifecycle:
  - the launch/sync payload carries the full device config incl. v0.5.0
    child_entities (so an added device isn't a degraded simulation)
  - the removed-path only forgets a port when the stop actually succeeded
  - the added-path rolls back a leaked instance and recovers from a stale
    "already simulated" 400 by adopting the running port
"""

import pytest

from server.core.simulation import SimulationManager


# ── Fakes ──────────────────────────────────────────────────────────────────

class _FakeDriver:
    def __init__(self, host="10.0.0.5", port=4001):
        self.config = {"host": host, "port": port}


class _FakeDeviceManager:
    def __init__(self, device_configs, devices):
        self._device_configs = device_configs
        self._devices = devices
        self.reconnected: list[str] = []

    async def reconnect_device(self, device_id):
        self.reconnected.append(device_id)


class _FakeEngine:
    def __init__(self, dm):
        self.devices = dm


class _FakeProcess:
    returncode = None


class _FakeResp:
    def __init__(self, status, json_data=None, text_data="", json_raises=False):
        self.status = status
        self._json = json_data or {}
        self._text = text_data
        self._json_raises = json_raises

    async def json(self):
        if self._json_raises:
            raise ValueError("malformed response body")
        return self._json

    async def text(self):
        return self._text


class _FakeSession:
    """Async-context-manager ClientSession stand-in driven by a handler."""

    def __init__(self, handler, calls):
        self._handler = handler
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=None):
        self._calls.append(("POST", url))
        return self._handler("POST", url, json)

    async def get(self, url, timeout=None):
        self._calls.append(("GET", url))
        return self._handler("GET", url, None)


def _install_fake_aiohttp(monkeypatch, handler):
    """Replace aiohttp.ClientSession with a fake; return the call log."""
    import aiohttp

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: _FakeSession(handler, calls))
    return calls


def _active_manager(dm) -> SimulationManager:
    mgr = SimulationManager(engine=_FakeEngine(dm))
    mgr._active = True
    mgr._process = _FakeProcess()
    mgr._sim_ui_url = "http://localhost:19500"
    return mgr


# ── H-053 / H-054: full payload incl. child_entities ────────────────────────

def test_device_sim_payload_includes_full_config_and_children():
    mgr = SimulationManager(engine=object())
    cfg = {
        "driver": "acme_ctrl",
        "name": "Acme Controller",
        "config": {"host": "10.0.0.9", "port": 5000, "password": "secret"},
        "child_entities": {"encoder": {"01": {"label": "Enc 1", "config": {}}}},
    }
    p = mgr._device_sim_payload("dev1", cfg)

    assert p["device_id"] == "dev1"
    assert p["driver_id"] == "acme_ctrl"
    assert p["device_name"] == "Acme Controller"
    assert p["real_host"] == "10.0.0.9"
    assert p["real_port"] == 5000
    assert p["config"] == {"password": "secret"}  # host/port stripped
    assert p["child_entities"] == {"encoder": {"01": {"label": "Enc 1", "config": {}}}}


def test_device_sim_payload_defaults_children_to_empty():
    mgr = SimulationManager(engine=object())
    p = mgr._device_sim_payload("dev1", {"driver": "x", "config": {}})
    assert p["child_entities"] == {}


# ── M-099: removed-path only forgets the port when the stop succeeded ────────

@pytest.mark.asyncio
async def test_sync_removed_keeps_port_when_stop_fails(monkeypatch):
    dm = _FakeDeviceManager(device_configs={}, devices={})  # dev1 removed
    mgr = _active_manager(dm)
    mgr._sim_ports = {"dev1": 19000}
    mgr._original_configs = {"dev1": {"host": "10.0.0.5", "port": 4001}}

    _install_fake_aiohttp(monkeypatch, lambda m, u, j: _FakeResp(500, text_data="boom"))
    await mgr.sync()

    # Stop failed → port slot retained so the next sync retries (no leak).
    assert "dev1" in mgr._sim_ports
    assert "dev1" in mgr._original_configs


@pytest.mark.asyncio
async def test_sync_removed_drops_port_when_stop_succeeds(monkeypatch):
    dm = _FakeDeviceManager(device_configs={}, devices={})
    mgr = _active_manager(dm)
    mgr._sim_ports = {"dev1": 19000}
    mgr._original_configs = {"dev1": {"host": "10.0.0.5", "port": 4001}}

    _install_fake_aiohttp(monkeypatch, lambda m, u, j: _FakeResp(200, json_data={"status": "stopped"}))
    await mgr.sync()

    assert "dev1" not in mgr._sim_ports
    assert "dev1" not in mgr._original_configs


# ── M-098: added-path rolls back a leaked instance ──────────────────────────

@pytest.mark.asyncio
async def test_sync_added_rolls_back_on_post_start_failure(monkeypatch):
    driver = _FakeDriver()
    dm = _FakeDeviceManager(
        device_configs={"dev2": {"driver": "acme", "config": {}}},
        devices={"dev2": driver},
    )
    mgr = _active_manager(dm)
    mgr._sim_ports = {}

    def handler(method, url, json):
        if method == "POST" and url.endswith("/dev2/start"):
            # Server committed the instance (200) but our handling then fails.
            return _FakeResp(200, json_raises=True)
        if method == "POST" and url.endswith("/dev2/stop"):
            return _FakeResp(200, json_data={"status": "stopped"})
        return _FakeResp(404)

    calls = _install_fake_aiohttp(monkeypatch, handler)
    await mgr.sync()

    # dev2 never got committed locally, and a rollback /stop was issued.
    assert "dev2" not in mgr._sim_ports
    assert ("POST", f"{mgr._sim_ui_url}/api/devices/dev2/stop") in calls


# ── L-067: added-path adopts an orphaned instance on a 400 ──────────────────

@pytest.mark.asyncio
async def test_sync_added_adopts_orphan_on_already_simulated(monkeypatch):
    driver = _FakeDriver()
    dm = _FakeDeviceManager(
        device_configs={"dev3": {"driver": "acme", "config": {}}},
        devices={"dev3": driver},
    )
    mgr = _active_manager(dm)
    mgr._sim_ports = {}

    def handler(method, url, json):
        if method == "POST" and url.endswith("/dev3/start"):
            return _FakeResp(400, text_data="Device 'dev3' is already simulated")
        if method == "GET" and url.endswith("/api/devices"):
            return _FakeResp(200, json_data={"devices": [{"device_id": "dev3", "port": 19005}]})
        return _FakeResp(404)

    _install_fake_aiohttp(monkeypatch, handler)
    await mgr.sync()

    # Adopted the running instance's port and redirected the driver to it.
    assert mgr._sim_ports.get("dev3") == 19005
    assert driver.config["host"] == "127.0.0.1"
    assert driver.config["port"] == 19005
    assert "dev3" in dm.reconnected
