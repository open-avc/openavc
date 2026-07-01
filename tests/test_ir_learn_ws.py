"""Tests for the vendor-neutral IR learn WebSocket channel and the bridge
raw IR-emit diagnostic endpoint.

Uses a minimal FastAPI app wired to a fake engine and an invented learning
bridge — the channel drives the generic bridge_learn_* / bridge_emit capability,
never a real device or wire format.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import server.api._engine as engine_mod
from server.api import ir_learn_ws
from server.api.routes import devices as devices_routes


class _FakeLearningBridge:
    is_bridge = True
    can_learn = True

    def __init__(self, captures, *, connected=True):
        self._captures = list(captures)
        self._connected = connected
        self.started = False
        self.stopped = False
        self.emitted: list[tuple] = []

    async def bridge_learn_start(self):
        self.started = True

    async def bridge_learn_poll(self, timeout):
        if self._captures:
            return self._captures.pop(0)
        # Simulate the blocking-with-timeout wait so the capture loop doesn't
        # spin; short enough that a client stop resolves promptly.
        await asyncio.sleep(0.02)
        return None

    async def bridge_learn_stop(self):
        self.stopped = True

    async def bridge_emit(self, port, kind, payload):
        self.emitted.append((port, kind, payload))
        return {"port": port, "kind": kind}


class _NotABridge:
    is_bridge = False
    can_learn = False
    _connected = True


class _FakeDevices:
    def __init__(self, drivers):
        self._d = drivers

    def get_driver(self, device_id):
        return self._d.get(device_id)


class _FakeEngine:
    def __init__(self, drivers):
        self.devices = _FakeDevices(drivers)


@pytest.fixture
def app_and_bridges():
    ir_learn_ws._active_learn.clear()
    bridges: dict = {}
    engine = _FakeEngine(bridges)
    engine_mod.set_engine(engine)

    app = FastAPI()
    app.include_router(ir_learn_ws.router)
    # devices.router carries no prefix of its own; the /api prefix is applied by
    # the rest.py aggregator in production. Mirror that here.
    app.include_router(devices_routes.router, prefix="/api")
    try:
        yield app, bridges
    finally:
        engine_mod.set_engine(None)
        ir_learn_ws._active_learn.clear()


def _drain_until(ws, wanted_type, limit=10):
    """Read messages until one of ``wanted_type`` arrives (skipping heartbeats)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == wanted_type:
            return msg
    raise AssertionError(f"did not see {wanted_type!r} within {limit} messages")


# --- learn WS ---------------------------------------------------------------


def test_one_off_capture_returns_first_code_and_stops(app_and_bridges):
    app, bridges = app_and_bridges
    bridge = _FakeLearningBridge(["0000 006D 0000 0001 0060 0018"])
    bridges["b1"] = bridge
    client = TestClient(app)
    with client.websocket_connect("/api/devices/b1/ir-learn?mode=one_off") as ws:
        assert _drain_until(ws, "learn.started")["mode"] == "one_off"
        cap = _drain_until(ws, "learn.captured")
        assert cap["pronto"].startswith("0000 006D")
        stopped = _drain_until(ws, "learn.stopped")
        assert stopped["reason"] == "complete"
    assert bridge.started and bridge.stopped


def test_auto_capture_streams_until_client_stops(app_and_bridges):
    app, bridges = app_and_bridges
    bridge = _FakeLearningBridge(["0000 1111", "0000 2222"])
    bridges["b1"] = bridge
    client = TestClient(app)
    with client.websocket_connect("/api/devices/b1/ir-learn?mode=auto") as ws:
        _drain_until(ws, "learn.started")
        first = _drain_until(ws, "learn.captured")
        assert first["pronto"] == "0000 1111"
        second = _drain_until(ws, "learn.captured")
        assert second["pronto"] == "0000 2222"
        ws.send_json({"action": "stop"})
        stopped = _drain_until(ws, "learn.stopped")
        assert stopped["reason"] == "stopped"
    assert bridge.stopped


def test_second_session_on_same_bridge_is_refused(app_and_bridges):
    app, bridges = app_and_bridges
    bridges["b1"] = _FakeLearningBridge([])
    ir_learn_ws._active_learn.add("b1")  # simulate a live session
    client = TestClient(app)
    with client.websocket_connect("/api/devices/b1/ir-learn") as ws:
        err = _drain_until(ws, "learn.error")
        assert err["code"] == "already_learning"


def test_learn_on_non_bridge_errors(app_and_bridges):
    app, bridges = app_and_bridges
    bridges["x"] = _NotABridge()
    client = TestClient(app)
    with client.websocket_connect("/api/devices/x/ir-learn") as ws:
        err = _drain_until(ws, "learn.error")
        assert err["code"] == "not_a_bridge"


def test_learn_on_offline_bridge_errors(app_and_bridges):
    app, bridges = app_and_bridges
    bridges["b1"] = _FakeLearningBridge([], connected=False)
    client = TestClient(app)
    with client.websocket_connect("/api/devices/b1/ir-learn") as ws:
        err = _drain_until(ws, "learn.error")
        assert err["code"] == "bridge_offline"


# --- raw IR-emit REST -------------------------------------------------------


def test_raw_ir_emit_routes_to_bridge(app_and_bridges):
    app, bridges = app_and_bridges
    bridge = _FakeLearningBridge([])
    bridges["b1"] = bridge
    client = TestClient(app)
    resp = client.post(
        "/api/devices/b1/ir-emit",
        json={"port": "ir:2", "pronto": "0000 006D 0000 0001 0060 0018", "repeat": 3},
    )
    assert resp.status_code == 200
    assert bridge.emitted == [
        ("ir:2", "ir", {"pronto": "0000 006D 0000 0001 0060 0018", "repeat": 3})
    ]


def test_raw_ir_emit_offline_bridge_is_503(app_and_bridges):
    app, bridges = app_and_bridges
    bridges["b1"] = _FakeLearningBridge([], connected=False)
    client = TestClient(app)
    resp = client.post(
        "/api/devices/b1/ir-emit", json={"port": "ir:1", "pronto": "0000 006D"}
    )
    assert resp.status_code == 503


def test_raw_ir_emit_non_bridge_is_404(app_and_bridges):
    app, bridges = app_and_bridges
    bridges["x"] = _NotABridge()
    client = TestClient(app)
    resp = client.post("/api/devices/x/ir-emit", json={"port": "ir:1", "pronto": "X"})
    assert resp.status_code == 404
