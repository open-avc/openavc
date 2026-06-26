"""Tests for the child-entity REST endpoints under ``/api/devices/{id}/children``.

Covers the P5 surface from openavc-device-children-plan.md: list (all types,
single type, single child), PATCH (label + config persistence + live state
mirror), and refresh hook. Uses the same MagicMock-engine pattern as
``test_api_endpoints.py`` but swaps in a real driver instance + real
DeviceConfig so the route's interaction with the driver and project file
is exercised end-to-end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.api import rest, ws
from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.project_loader import ChildEntityConfig, DeviceConfig
from server.core.state_store import StateStore
from server.drivers.base import BaseDriver
from server.main import app


# ---------------------------------------------------------------------------
# Fixture driver — declares two child types, mirrors the realistic shape
# used in test_base_driver_children.py so behavior is consistent.
# ---------------------------------------------------------------------------


class _FakeControllerDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "fake_controller",
        "name": "Fake Controller",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "child_entity_types": {
            "encoder": {
                "label": "Encoder",
                "label_plural": "Encoders",
                "id_format": {
                    "type": "integer", "min": 1, "max": 762, "pad_width": 3,
                },
                "state_variables": {
                    "name": {"type": "string"},
                    "ip": {"type": "string"},
                    "signal_present": {"type": "boolean"},
                },
                "summary_fields": ["name", "ip", "signal_present"],
                "label_field": "name",
            },
            "decoder": {
                "label": "Decoder",
                "id_format": {"type": "integer", "min": 1, "max": 32},
                "state_variables": {
                    "name": {"type": "string"},
                    "source": {"type": "integer", "min": 0, "max": 762},
                },
            },
        },
    }

    refresh_calls: int = 0

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _RefreshSupportingDriver(_FakeControllerDriver):
    """Variant that implements ``refresh_children`` so we can test the
    happy path of the refresh endpoint without monkeypatching.
    """

    DRIVER_INFO = {**_FakeControllerDriver.DRIVER_INFO, "id": "refreshable"}

    async def refresh_children(self) -> Any:
        self.refresh_calls += 1
        # Pretend a re-discovery added encoder 9.
        self.register_child("encoder", 9, initial_state={"name": "AutoDisc"})
        return {"added": ["encoder.009"]}


# ---------------------------------------------------------------------------
# Engine fixture — wires real state, event bus, DeviceManager-shaped mock,
# and a real ProjectConfig device list so PATCH persistence is observable.
# ---------------------------------------------------------------------------


def _make_engine(tmp_path: Path, *, driver_cls=_FakeControllerDriver):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)

    driver = driver_cls(
        device_id="ctrl1", config={}, state=state, events=events,
    )
    # Mark connected so refresh endpoint passes its connection guard.
    driver.set_state("connected", True)

    engine = MagicMock()
    engine.state = state
    engine.events = events
    engine.macros = MacroEngine(state, events, MagicMock())
    engine.triggers = MagicMock()
    engine.triggers.list_triggers.return_value = []
    engine.scripts = MagicMock()
    engine.plugin_loader = MagicMock()
    engine.isc = None
    engine._running = True
    engine._ws_clients = []
    engine.get_status.return_value = {"version": "0.0.0-test"}

    # Real DeviceManager-shaped interface — get_driver returns the live
    # driver; the rest of the surface is unused by these routes but stubbed
    # for safety in case the open router probes it.
    engine.devices = MagicMock()
    engine.devices.get_driver = MagicMock(return_value=driver)
    engine.devices.list_devices.return_value = [
        {"id": "ctrl1", "name": "Controller 1", "driver": "fake_controller",
         "connected": True},
    ]

    # Real project — uses ChildEntityConfig so save/load round-tripping
    # works the same as a live system.
    device_cfg = DeviceConfig(
        id="ctrl1",
        driver="fake_controller",
        name="Controller 1",
        config={},
        child_entities={
            "encoder": {
                "005": ChildEntityConfig(
                    label="Lobby TX", config={"room": "Lobby"},
                ),
            },
        },
    )
    engine.project = MagicMock()
    engine.project.devices = [device_cfg]
    engine.project.connections = {}
    engine.project.variables = []
    engine.project.macros = []
    engine.project.scripts = []
    engine.project.ui = MagicMock()
    engine.project.ui.model_dump.return_value = {"pages": []}
    engine.project.plugins = {}
    engine.project_path = str(tmp_path / "project.avc")
    engine.project_dir = tmp_path

    return engine, driver, device_cfg


@pytest.fixture
def child_client(tmp_path):
    engine, driver, device_cfg = _make_engine(tmp_path)
    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app), engine, driver, device_cfg
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


@pytest.fixture
def refreshable_client(tmp_path):
    engine, driver, device_cfg = _make_engine(
        tmp_path, driver_cls=_RefreshSupportingDriver,
    )
    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app), engine, driver, device_cfg
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


# ---------------------------------------------------------------------------
# GET /api/devices/{id}/children
# ---------------------------------------------------------------------------


def test_list_children_unknown_device_returns_404(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.get("/api/devices/no_such_device/children")
    assert resp.status_code == 404


def test_list_children_empty_when_no_children_registered(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.get("/api/devices/ctrl1/children")
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == "ctrl1"
    # Schema is exposed even when no children are registered yet, so the
    # IDE can render the per-type tab structure pre-poll.
    assert "encoder" in body["child_entity_types"]
    assert "decoder" in body["child_entity_types"]
    enc_schema = body["child_entity_types"]["encoder"]["state_variables"]
    # Effective schema includes the platform-injected online + label keys.
    assert enc_schema["online"]["type"] == "boolean"
    assert enc_schema["label"]["type"] == "string"
    assert body["children"] == {"encoder": [], "decoder": []}


def test_list_children_returns_registered_entries_with_project_label(child_client):
    c, _engine, driver, _cfg = child_client
    # Project file already has a label for encoder 005 (set in fixture).
    # Driver register_child must seed it into the live `label` state key.
    driver.set_project_child_entities({
        "encoder": {"005": {"label": "Lobby TX", "config": {"room": "Lobby"}}},
    })
    driver.register_child("encoder", 5, initial_state={"name": "Enc5"})
    driver.register_child("decoder", 2, initial_state={"name": "Dec2"})

    resp = c.get("/api/devices/ctrl1/children")
    assert resp.status_code == 200
    body = resp.json()

    enc_entries = body["children"]["encoder"]
    assert len(enc_entries) == 1
    e = enc_entries[0]
    assert e["local_id"] == 5
    assert e["local_id_padded"] == "005"
    assert e["registered"] is True
    assert e["label"] == "Lobby TX"  # from project
    assert e["config"] == {"room": "Lobby"}  # from project
    assert e["state"]["name"] == "Enc5"
    assert e["state"]["label"] == "Lobby TX"  # seeded by register_child
    assert e["state"]["online"] is True

    dec_entries = body["children"]["decoder"]
    assert len(dec_entries) == 1
    d = dec_entries[0]
    assert d["local_id"] == 2
    # decoder has no pad_width → bare integer string.
    assert d["local_id_padded"] == "2"
    assert d["label"] == ""  # no project entry for decoder 2
    assert d["config"] == {}


def test_list_children_empty_for_orphan_device(tmp_path):
    """Orphan/disabled devices (no live driver) return 200 with empty body —
    the IDE renders an inert tab instead of an error."""
    engine, _driver, _cfg = _make_engine(tmp_path)
    engine.devices.get_driver = MagicMock(return_value=None)
    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        c = TestClient(app)
        resp = c.get("/api/devices/ctrl1/children")
        assert resp.status_code == 200
        assert resp.json() == {
            "device_id": "ctrl1",
            "child_entity_types": {},
            "children": {},
        }
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


# ---------------------------------------------------------------------------
# GET /api/devices/{id}/children/{type}
# ---------------------------------------------------------------------------


def test_list_by_type_unknown_type_returns_404(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.get("/api/devices/ctrl1/children/video_wall")
    assert resp.status_code == 404


def test_list_by_type_returns_just_that_type(child_client):
    c, _engine, driver, _cfg = child_client
    driver.register_child("encoder", 1)
    driver.register_child("encoder", 7)
    driver.register_child("decoder", 3)

    resp = c.get("/api/devices/ctrl1/children/encoder")
    assert resp.status_code == 200
    body = resp.json()
    assert body["child_type"] == "encoder"
    assert body["schema"]["label_plural"] == "Encoders"
    ids = [e["local_id"] for e in body["children"]]
    assert ids == [1, 7]  # insertion-ordered, decoder not included


def test_list_by_type_orphan_503(tmp_path):
    """Single-type list on a device with no live driver returns 503 —
    we don't know which types it supports without the driver, so an
    empty list would be a lie."""
    engine, _driver, _cfg = _make_engine(tmp_path)
    engine.devices.get_driver = MagicMock(return_value=None)
    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        c = TestClient(app)
        resp = c.get("/api/devices/ctrl1/children/encoder")
        assert resp.status_code == 503
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


# ---------------------------------------------------------------------------
# GET /api/devices/{id}/children/{type}/{local_id}
# ---------------------------------------------------------------------------


def test_get_single_child_unregistered_404(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.get("/api/devices/ctrl1/children/encoder/5")
    assert resp.status_code == 404


def test_get_single_child_out_of_range_404(child_client):
    """Range validation surfaces as 404 (resource doesn't exist), not 500."""
    c, _engine, _driver, _cfg = child_client
    resp = c.get("/api/devices/ctrl1/children/encoder/9999")
    assert resp.status_code == 404


def test_get_single_child_happy_path(child_client):
    c, _engine, driver, _cfg = child_client
    driver.register_child("encoder", 5, initial_state={
        "name": "Lobby", "ip": "10.0.0.5", "signal_present": True,
    })
    resp = c.get("/api/devices/ctrl1/children/encoder/5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["local_id"] == 5
    assert body["local_id_padded"] == "005"
    assert body["state"]["ip"] == "10.0.0.5"
    assert body["state"]["signal_present"] is True


# ---------------------------------------------------------------------------
# PATCH /api/devices/{id}/children/{type}/{local_id}
# ---------------------------------------------------------------------------


def test_patch_label_persists_to_project_and_state(child_client):
    c, _engine, driver, device_cfg = child_client
    driver.register_child("encoder", 7, initial_state={"name": "Original"})

    with patch("server.core.project_loader.save_project") as save:
        resp = c.patch(
            "/api/devices/ctrl1/children/encoder/7",
            json={"label": "Stage Right TX"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "Stage Right TX"
    assert body["state"]["label"] == "Stage Right TX"

    # Project file mutated and saved.
    entry = device_cfg.child_entities["encoder"]["007"]
    assert entry.label == "Stage Right TX"
    save.assert_called_once()

    # Live state key updated so existing subscribers see the new label.
    assert driver.state.get("device.ctrl1.encoder.007.label") == "Stage Right TX"


def test_patch_config_only_preserves_existing_label(child_client):
    c, _engine, _driver, device_cfg = child_client
    # Project pre-seeds encoder 005 with label "Lobby TX" + room=Lobby.
    with patch("server.core.project_loader.save_project"):
        resp = c.patch(
            "/api/devices/ctrl1/children/encoder/5",
            json={"config": {"room": "Stage", "tag": "primary"}},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "Lobby TX"  # untouched
    assert body["config"] == {"room": "Stage", "tag": "primary"}
    entry = device_cfg.child_entities["encoder"]["005"]
    assert entry.label == "Lobby TX"
    assert entry.config == {"room": "Stage", "tag": "primary"}


def test_patch_label_works_on_unregistered_child(child_client):
    """Label persistence isn't gated on the child being live — the IDE
    can label a child that hasn't connected yet; the label seeds into
    state on the next register_child call."""
    c, _engine, driver, device_cfg = child_client
    assert not driver.is_child_registered("encoder", 42)

    with patch("server.core.project_loader.save_project"):
        resp = c.patch(
            "/api/devices/ctrl1/children/encoder/42",
            json={"label": "Future TX"},
        )

    assert resp.status_code == 200
    entry = device_cfg.child_entities["encoder"]["042"]
    assert entry.label == "Future TX"
    # No state key created — child isn't registered.
    assert driver.state.get("device.ctrl1.encoder.042.label") is None

    # Verify driver picked up the new project metadata: simulating a
    # later register_child should seed the label.
    driver.register_child("encoder", 42)
    assert driver.state.get("device.ctrl1.encoder.042.label") == "Future TX"


def test_patch_empty_body_returns_422(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.patch("/api/devices/ctrl1/children/encoder/5", json={})
    assert resp.status_code == 422


def test_patch_unknown_type_returns_404(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.patch(
        "/api/devices/ctrl1/children/video_wall/1", json={"label": "x"},
    )
    assert resp.status_code == 404


def test_patch_id_out_of_range_returns_422(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.patch(
        "/api/devices/ctrl1/children/encoder/9999", json={"label": "x"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/devices/{id}/children/refresh
# ---------------------------------------------------------------------------


def test_refresh_not_implemented_returns_501(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.post("/api/devices/ctrl1/children/refresh")
    assert resp.status_code == 501


def test_refresh_disconnected_returns_503(child_client):
    c, _engine, driver, _cfg = child_client
    driver.set_state("connected", False)
    resp = c.post("/api/devices/ctrl1/children/refresh")
    assert resp.status_code == 503


def test_refresh_happy_path(refreshable_client):
    c, _engine, driver, _cfg = refreshable_client
    resp = c.post("/api/devices/ctrl1/children/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "refreshed"
    assert body["result"] == {"added": ["encoder.009"]}
    assert driver.refresh_calls == 1
    # Driver's refresh_children registered a new encoder; verify visible.
    list_resp = c.get("/api/devices/ctrl1/children/encoder")
    assert any(e["local_id"] == 9 for e in list_resp.json()["children"])


def test_refresh_unknown_device_returns_404(child_client):
    c, _engine, _driver, _cfg = child_client
    resp = c.post("/api/devices/no_such/children/refresh")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dynamic + string-id children over REST
#
# Mirrors how a runtime-discovered device (invented DSP, no real product)
# exposes string-keyed components whose control set is published per-instance.
# ---------------------------------------------------------------------------


class _DynamicDspDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "dyn_dsp",
        "name": "Dynamic DSP",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "child_entity_types": {
            "component": {
                "label": "Component",
                "dynamic": True,
                "id_format": {"type": "string"},
                "summary_fields": ["label", "kind"],
            },
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture
def dsp_client(tmp_path):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = _DynamicDspDriver(
        device_id="dsp1", config={}, state=state, events=events,
    )
    driver.set_state("connected", True)
    # Discover one component with a per-instance schema.
    driver.register_child(
        "component", "PgmGain",
        schema={"gain": {"type": "number"}, "mute": {"type": "boolean"}},
        initial_state={"gain": -6.0},
    )

    engine = MagicMock()
    engine.state = state
    engine.events = events
    engine.macros = MacroEngine(state, events, MagicMock())
    engine.triggers = MagicMock()
    engine.triggers.list_triggers.return_value = []
    engine.scripts = MagicMock()
    engine.plugin_loader = MagicMock()
    engine.isc = None
    engine._running = True
    engine._ws_clients = []
    engine.get_status.return_value = {"version": "0.0.0-test"}
    engine.devices = MagicMock()
    engine.devices.get_driver = MagicMock(return_value=driver)
    device_cfg = DeviceConfig(
        id="dsp1", driver="dyn_dsp", name="DSP 1", config={},
    )
    engine.project = MagicMock()
    engine.project.devices = [device_cfg]
    engine.project_path = str(tmp_path / "project.avc")
    engine.project_dir = tmp_path

    rest.set_engine(engine)
    ws.set_engine(engine)
    try:
        yield TestClient(app), engine, driver, device_cfg
    finally:
        rest.set_engine(None)
        ws.set_engine(None)


def test_list_dynamic_children_carries_per_child_schema(dsp_client):
    c, _engine, _driver, _cfg = dsp_client
    resp = c.get("/api/devices/dsp1/children")
    assert resp.status_code == 200
    body = resp.json()
    # Type is reported dynamic; type-level schema only has platform keys.
    assert body["child_entity_types"]["component"]["dynamic"] is True
    entry = body["children"]["component"][0]
    assert entry["local_id"] == "PgmGain"
    assert entry["local_id_padded"] == "PgmGain"
    assert entry["state"]["gain"] == -6.0
    # The per-child schema rides alongside the state for the IDE to type rows.
    assert entry["schema"]["gain"]["type"] == "number"
    assert entry["schema"]["mute"]["type"] == "boolean"


def test_get_dynamic_child_by_string_id(dsp_client):
    c, _engine, _driver, _cfg = dsp_client
    resp = c.get("/api/devices/dsp1/children/component/PgmGain")
    assert resp.status_code == 200
    body = resp.json()
    assert body["local_id"] == "PgmGain"
    assert body["state"]["gain"] == -6.0
    assert "schema" in body
    # An unregistered string id reads as 404.
    assert c.get("/api/devices/dsp1/children/component/Nope").status_code == 404


def test_patch_dynamic_child_string_id_persists_label(dsp_client):
    c, _engine, driver, cfg = dsp_client
    with patch("server.core.project_loader.save_project"):
        resp = c.patch(
            "/api/devices/dsp1/children/component/PgmGain",
            json={"label": "Program Gain"},
        )
    assert resp.status_code == 200
    # Persisted to project (keyed by the verbatim string id) and mirrored live.
    assert cfg.child_entities["component"]["PgmGain"].label == "Program Gain"
    assert driver.get_child_state("component", "PgmGain")["label"] == "Program Gain"
