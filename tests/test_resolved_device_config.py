"""Tests for driver-default + connection-table merge in resolved_device_config.

Verifies the discovery -> add-device gap fix: a driver's
``DRIVER_INFO['default_config']`` is now layered under saved device config
before connection-table overrides, so a discovered device added with only
``host`` still picks up the driver's control port at runtime.

Layering (later wins): driver defaults -> device.config -> connections[id].
"""

from __future__ import annotations

import pytest

from server.core.device_manager import (
    get_driver_default_config,
    register_driver,
    unregister_driver,
)
from server.core.engine import Engine
from server.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from server.drivers.base import BaseDriver
from server.drivers.configurable import create_configurable_driver_class


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tcp_driver():
    """Register a temporary YAML-style TCP driver with a custom port."""
    definition = {
        "id": "fake_kramer_test",
        "name": "Fake Kramer (test)",
        "manufacturer": "TestCo",
        "category": "switcher",
        "version": "1.0.0",
        "transport": "tcp",
        "default_config": {
            "host": "",
            "port": 5000,
            "machine_number": "01",
            "poll_interval": 10,
        },
        "config_schema": {"host": {"type": "string", "required": True}},
        "state_variables": {},
        "commands": {},
        "responses": [],
    }
    cls = create_configurable_driver_class(definition)
    register_driver(cls)
    yield cls
    unregister_driver("fake_kramer_test")


@pytest.fixture
def engine_with_project(tmp_path, fake_tcp_driver):
    """An Engine wired to a minimal in-memory ProjectConfig."""
    engine = Engine(str(tmp_path / "test.avc"))
    engine.project = ProjectConfig(
        project=ProjectMeta(id="t", name="Test"),
        devices=[],
        connections={},
    )
    return engine


# ---------------------------------------------------------------------------
# get_driver_default_config
# ---------------------------------------------------------------------------


def test_get_driver_default_config_returns_copy(fake_tcp_driver):
    a = get_driver_default_config("fake_kramer_test")
    a["mutated"] = True
    b = get_driver_default_config("fake_kramer_test")
    assert "mutated" not in b, (
        "get_driver_default_config must return a copy so callers can't "
        "mutate the driver's class-level DRIVER_INFO['default_config']"
    )


def test_get_driver_default_config_unknown_driver_returns_empty_dict():
    assert get_driver_default_config("does_not_exist_xyz") == {}


# ---------------------------------------------------------------------------
# resolved_device_config layering
# ---------------------------------------------------------------------------


def test_discovery_added_device_inherits_driver_port(engine_with_project):
    """The discovery -> add bug: device saved with only host, but the
    driver declares port 5000. resolved_device_config must surface it."""
    engine = engine_with_project
    device = DeviceConfig(
        id="kramer1",
        driver="fake_kramer_test",
        name="Conference Room Kramer",
        config={},  # discovery add_device leaves protocol config empty
    )
    engine.project.devices.append(device)
    engine.project.connections["kramer1"] = {"host": "192.0.2.50"}

    resolved = engine.resolved_device_config(device)

    assert resolved["config"]["host"] == "192.0.2.50"
    assert resolved["config"]["port"] == 5000, (
        "driver default_config.port must be applied when not overridden"
    )
    assert resolved["config"]["machine_number"] == "01"
    assert resolved["config"]["poll_interval"] == 10


def test_saved_device_config_overrides_driver_default(engine_with_project):
    """A field saved in device.config must win over default_config."""
    engine = engine_with_project
    device = DeviceConfig(
        id="kramer2",
        driver="fake_kramer_test",
        name="Custom",
        config={"machine_number": "07"},
    )
    engine.project.devices.append(device)
    engine.project.connections["kramer2"] = {"host": "192.0.2.51"}

    resolved = engine.resolved_device_config(device)
    assert resolved["config"]["machine_number"] == "07"
    # Other defaults still apply
    assert resolved["config"]["port"] == 5000


def test_connection_table_overrides_both(engine_with_project):
    """Connection-table values must win over saved config and defaults."""
    engine = engine_with_project
    device = DeviceConfig(
        id="kramer3",
        driver="fake_kramer_test",
        name="Custom port",
        config={"machine_number": "07"},
    )
    engine.project.devices.append(device)
    engine.project.connections["kramer3"] = {
        "host": "192.0.2.52",
        "port": 6001,  # custom port saved via PUT /devices/{id}
    }

    resolved = engine.resolved_device_config(device)
    assert resolved["config"]["host"] == "192.0.2.52"
    assert resolved["config"]["port"] == 6001
    assert resolved["config"]["machine_number"] == "07"


def test_orphan_driver_resolves_with_empty_defaults(engine_with_project):
    """An unregistered driver (orphan) must not crash resolution.

    Returns ``{}`` defaults so the device falls through to whatever
    config was saved (which still won't work, but resolution is
    well-defined and the orphan path keeps reporting cleanly).
    """
    engine = engine_with_project
    device = DeviceConfig(
        id="orphan1",
        driver="not_installed",
        name="Orphan",
        config={"host": "192.0.2.99"},
    )
    engine.project.devices.append(device)

    resolved = engine.resolved_device_config(device)
    assert resolved["config"] == {"host": "192.0.2.99"}


# ---------------------------------------------------------------------------
# BaseDriver._required_port hardening
# ---------------------------------------------------------------------------


class _PortOnlyDriver(BaseDriver):
    """Minimal driver used to exercise _required_port in isolation."""

    DRIVER_INFO = {"id": "_port_only_test", "transport": "tcp"}

    async def send_command(self, command, params=None):
        return None


def _make_driver(config):
    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

    return _PortOnlyDriver("test_dev", config, StateStore(), EventBus())


def test_required_port_returns_int():
    d = _make_driver({"port": 5000})
    assert d._required_port() == 5000


def test_required_port_coerces_string_int():
    d = _make_driver({"port": "5000"})
    assert d._required_port() == 5000


def test_required_port_missing_raises_clear_error():
    d = _make_driver({"host": "10.0.0.1"})
    with pytest.raises(ConnectionError, match="missing 'port'"):
        d._required_port()


def test_required_port_empty_string_raises():
    d = _make_driver({"port": ""})
    with pytest.raises(ConnectionError, match="missing 'port'"):
        d._required_port()


def test_required_port_invalid_value_raises():
    d = _make_driver({"port": "not-a-number"})
    with pytest.raises(ConnectionError, match="invalid port"):
        d._required_port()


# ---------------------------------------------------------------------------
# Discovery /add-device REST endpoint — first-add behavior
# ---------------------------------------------------------------------------


@pytest.fixture
def noop_discovery_driver():
    """A registered driver whose connect() never touches the network, with
    the same default_config shape as ``fake_tcp_driver`` — the add-device
    tests drive the real device reconcile, which connects the device."""

    class _DiscoNoopDriver(BaseDriver):
        DRIVER_INFO = {
            "id": "fake_disco_test",
            "name": "Fake Discovery Device (test)",
            "transport": "tcp",
            "default_config": {
                "host": "",
                "port": 5000,
                "machine_number": "01",
                "poll_interval": 10,
            },
            "state_variables": {},
            "commands": {},
        }

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def send_command(self, command, params=None):
            return None

    register_driver(_DiscoNoopDriver)
    yield _DiscoNoopDriver
    unregister_driver("fake_disco_test")


def _wire_add_device_env(tmp_path, monkeypatch):
    """Stub the discovery engine and wire a real Engine (real device manager,
    real apply_project seam) so ``add_device`` runs against the code path
    that ships. Only disk persistence is stubbed out.
    """
    from unittest.mock import MagicMock

    from server.api import discovery as discovery_api

    fake_discovery = MagicMock()
    fake_discovery.results = {}
    discovery_api.set_discovery_engine(fake_discovery)

    engine = Engine(str(tmp_path / "test.avc"))
    engine.project = ProjectConfig(
        project=ProjectMeta(id="t", name="Test"),
        devices=[],
        connections={},
    )
    monkeypatch.setattr(
        "server.core.project_loader.save_project", lambda *a, **k: None
    )

    discovery_api.set_app_engine(engine)
    return engine


async def test_discovery_add_device_pulls_in_driver_defaults_on_first_add(
    tmp_path, noop_discovery_driver, monkeypatch
):
    """Regression: clicking Add/Install in Discovery must save the driver's
    declared port (and other defaults) into the project file AND apply
    them to the runtime device on first add — without requiring a server
    restart.
    """
    from server.api.discovery import AddDeviceRequest, add_device

    engine = _wire_add_device_env(tmp_path, monkeypatch)

    req = AddDeviceRequest(ip="192.0.2.50", driver_id="fake_disco_test")
    result = await add_device(req)

    assert result["status"] == "ok"
    device_id = result["device_id"]

    # 1. The devices reconcile instantiated the runtime device from the
    #    resolved config — driver defaults (port etc.) included.
    runtime_cfg = engine.devices.get_device_config(device_id)
    assert runtime_cfg is not None, "reconcile must add the runtime device"
    assert runtime_cfg["config"]["host"] == "192.0.2.50"
    assert runtime_cfg["config"]["port"] == 5000, (
        "first-add must include driver default_config.port at runtime"
    )
    assert runtime_cfg["config"]["machine_number"] == "01"
    assert runtime_cfg["config"]["poll_interval"] == 10

    # 2. Saved project also has the defaults — user opening the device
    #    sees port populated, not a blank field.
    saved_device = engine.project.devices[-1]
    saved_conn = engine.project.connections[saved_device.id]
    assert saved_conn["host"] == "192.0.2.50"
    assert saved_conn["port"] == 5000
    assert saved_device.config["machine_number"] == "01"
    assert saved_device.config["poll_interval"] == 10


async def test_discovery_add_device_bumps_revision_and_broadcasts(
    tmp_path, noop_discovery_driver, monkeypatch
):
    """Regression (data loss): add-device must advance the project revision
    and broadcast project.reloaded with the NEW revision. The old path saved
    without bumping and broadcast the stale revision, so an open IDE's cached
    ETag stayed valid and its next full-project PUT silently deleted the
    just-discovered device.
    """
    from server.api.discovery import AddDeviceRequest, add_device

    engine = _wire_add_device_env(tmp_path, monkeypatch)

    messages: list[dict] = []

    async def _record(msg):
        messages.append(msg)

    monkeypatch.setattr(engine, "broadcast_ws", _record)

    before = engine._project_revision
    result = await add_device(
        AddDeviceRequest(ip="192.0.2.51", driver_id="fake_disco_test")
    )
    assert result["status"] == "ok"
    assert engine._project_revision > before

    reloaded = [m for m in messages if m["type"] == "project.reloaded"]
    assert reloaded, "add-device must broadcast project.reloaded"
    assert reloaded[-1]["revision"] == engine._project_revision


async def test_discovery_add_device_rejects_duplicate(
    tmp_path, noop_discovery_driver, monkeypatch
):
    """Re-adding a device the project already has must 409 — not append a
    duplicate DeviceConfig or overwrite (and leak) the live runtime driver.
    """
    from fastapi import HTTPException

    from server.api.discovery import AddDeviceRequest, add_device

    engine = _wire_add_device_env(tmp_path, monkeypatch)

    req = AddDeviceRequest(ip="192.0.2.50", driver_id="fake_disco_test")
    first = await add_device(req)
    assert first["status"] == "ok"
    assert len(engine.project.devices) == 1
    device_id = first["device_id"]
    live_driver = engine.devices.get_driver(device_id)
    assert live_driver is not None

    # The first add appended the device to the project, so the guard fires on
    # the project check alone — the realistic re-add path after a rescan.
    with pytest.raises(HTTPException) as exc:
        await add_device(req)
    assert exc.value.status_code == 409
    assert "already in the project" in exc.value.detail

    # No duplicate row persisted and the live driver was not replaced.
    assert len(engine.project.devices) == 1
    assert engine.devices.get_driver(device_id) is live_driver


async def test_discovery_add_device_rejects_nested_config(
    tmp_path, noop_discovery_driver, monkeypatch
):
    """Caller-supplied config must be flat primitives; a nested object or a
    list value is rejected with 422 before anything is persisted.
    """
    from fastapi import HTTPException

    from server.api.discovery import AddDeviceRequest, add_device

    engine = _wire_add_device_env(tmp_path, monkeypatch)

    nested = AddDeviceRequest(
        ip="192.0.2.51",
        driver_id="fake_disco_test",
        config={"creds": {"password": "x"}},
    )
    with pytest.raises(HTTPException) as exc:
        await add_device(nested)
    assert exc.value.status_code == 422
    # Nothing persisted, nothing handed to the runtime.
    assert engine.project.devices == []
    assert engine.devices.get_device_configs() == {}

    listy = AddDeviceRequest(
        ip="192.0.2.52",
        driver_id="fake_disco_test",
        config={"ports": [1, 2, 3]},
    )
    with pytest.raises(HTTPException) as exc2:
        await add_device(listy)
    assert exc2.value.status_code == 422


async def test_discovery_add_device_accepts_flat_primitive_config(
    tmp_path, noop_discovery_driver, monkeypatch
):
    """A flat primitive config (string/number/bool/None) is accepted and
    merged into the saved device.
    """
    from server.api.discovery import AddDeviceRequest, add_device

    engine = _wire_add_device_env(tmp_path, monkeypatch)

    req = AddDeviceRequest(
        ip="192.0.2.53",
        driver_id="fake_disco_test",
        config={"machine_number": "07", "poll_interval": 5, "verify_ssl": False},
    )
    result = await add_device(req)
    assert result["status"] == "ok"
    saved = engine.project.devices[-1]
    assert saved.config["machine_number"] == "07"
    assert saved.config["poll_interval"] == 5
    assert saved.config["verify_ssl"] is False


def test_add_device_request_forbids_unknown_fields():
    """The obsolete per-device ``group`` field (now project-level
    ``device_groups``) must raise instead of being silently dropped.
    """
    from pydantic import ValidationError

    from server.api.discovery import AddDeviceRequest

    with pytest.raises(ValidationError):
        AddDeviceRequest(ip="192.0.2.54", driver_id="fake_kramer_test", group="A/V")


# ---------------------------------------------------------------------------
# Bridge binding resolution (v0.6.0 — device-through-device connection model)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_bridge_driver():
    """Register a temporary YAML-style serial bridge driver.

    Synthetic invented device (core-test rule): advertises one serial port
    that other devices connect through, transparently piped on TCP 4999.
    """
    definition = {
        "id": "fake_bridge_test",
        "name": "Fake Serial Bridge (test)",
        "manufacturer": "TestCo",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "bridge": {
            "ports": [
                {
                    "id": "serial:1",
                    "kind": "serial",
                    "passthrough_port": 4999,
                    "label": "Serial Port 1",
                },
            ],
        },
        "default_config": {"host": "", "port": 4998},
        "config_schema": {},
        "state_variables": {},
        "commands": {},
        "responses": [],
    }
    cls = create_configurable_driver_class(definition)
    register_driver(cls)
    yield cls
    unregister_driver("fake_bridge_test")


@pytest.fixture
def fake_serial_device_driver():
    """Register a temporary dual-transport (tcp|serial) downstream driver."""
    definition = {
        "id": "fake_serial_display_test",
        "name": "Fake Serial Display (test)",
        "manufacturer": "TestCo",
        "category": "display",
        "version": "1.0.0",
        "transport": "serial",
        "transports": ["tcp", "serial"],
        "default_config": {"baudrate": 9600, "port": ""},
        "config_schema": {},
        "state_variables": {},
        "commands": {},
        "responses": [],
    }
    cls = create_configurable_driver_class(definition)
    register_driver(cls)
    yield cls
    unregister_driver("fake_serial_display_test")


def test_get_driver_bridge_ports_reads_declaration(fake_bridge_driver):
    from server.core.device_manager import get_driver_bridge_ports

    ports = get_driver_bridge_ports("fake_bridge_test")
    assert "serial:1" in ports
    assert ports["serial:1"]["kind"] == "serial"
    assert ports["serial:1"]["passthrough_port"] == 4999


def test_get_driver_bridge_ports_non_bridge_returns_empty(fake_tcp_driver):
    from server.core.device_manager import get_driver_bridge_ports

    assert get_driver_bridge_ports("fake_kramer_test") == {}


@pytest.fixture
def fake_ir_bridge_driver():
    """Register a temporary IR bridge driver (advertises an IR emitter port).

    Synthetic invented device: an IR port routes commands through the bridge at
    send time (no pass-through TCP port), unlike a serial port.
    """
    definition = {
        "id": "fake_ir_bridge_test",
        "name": "Fake IR Bridge (test)",
        "manufacturer": "TestCo",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "bridge": {
            "ports": [
                {"id": "ir:1", "kind": "ir", "label": "IR Port 1"},
            ],
        },
        "default_config": {"host": "", "port": 4998},
        "config_schema": {},
        "state_variables": {},
        "commands": {},
        "responses": [],
    }
    cls = create_configurable_driver_class(definition)
    register_driver(cls)
    yield cls
    unregister_driver("fake_ir_bridge_test")


def test_ir_bridge_binding_marks_device_bridge_routed(
    engine_with_project, fake_ir_bridge_driver
):
    """An IR device bound to a bridge's IR port has no transport of its own:
    the resolver marks it transport=bridge (no host rewrite) so connect() opens
    no socket and commands route through the bridge instance at send time."""
    engine = engine_with_project
    bridge = DeviceConfig(
        id="irbridge", driver="fake_ir_bridge_test", name="IR Bridge", config={}
    )
    tv = DeviceConfig(id="tv", driver="generic_ir", name="TV", config={})
    engine.project.devices.extend([bridge, tv])
    engine.project.connections["irbridge"] = {"host": "192.0.2.70"}
    engine.project.connections["tv"] = {"bridge": "irbridge", "bridge_port": "ir:1"}

    cfg = engine.resolved_device_config(tv)["config"]
    assert cfg["transport"] == "bridge"
    # No host/port rewrite — an IR device dials nothing.
    assert "host" not in cfg
    # Binding markers survive for the send-time router.
    assert cfg["bridge"] == "irbridge"
    assert cfg["bridge_port"] == "ir:1"


def test_yaml_bridge_and_transports_survive_into_driver_info(
    fake_bridge_driver, fake_serial_device_driver
):
    """configurable.py must copy `bridge` + `transports` into DRIVER_INFO,
    or the runtime can't see the declaration (the YAML->runtime parity trap)."""
    assert fake_bridge_driver.DRIVER_INFO.get("bridge", {}).get("ports")
    assert fake_serial_device_driver.DRIVER_INFO.get("transports") == ["tcp", "serial"]


def _add_bridge_project(engine, bridge_host="192.0.2.40"):
    """Append a bridge + a downstream serial device bound through it."""
    bridge = DeviceConfig(
        id="bridge1", driver="fake_bridge_test", name="Bridge", config={}
    )
    downstream = DeviceConfig(
        id="disp1", driver="fake_serial_display_test", name="Display", config={}
    )
    engine.project.devices.extend([bridge, downstream])
    engine.project.connections["bridge1"] = {"host": bridge_host}
    engine.project.connections["disp1"] = {
        "bridge": "bridge1",
        "bridge_port": "serial:1",
        "baudrate": 9600,
    }
    return bridge, downstream


def test_serial_bridge_binding_rewrites_to_passthrough(
    engine_with_project, fake_bridge_driver, fake_serial_device_driver
):
    """A bridge-bound serial device resolves to the bridge's transparent TCP
    pass-through endpoint, reusing the existing TCP transport."""
    engine = engine_with_project
    _, downstream = _add_bridge_project(engine)

    cfg = engine.resolved_device_config(downstream)["config"]
    assert cfg["transport"] == "tcp"
    assert cfg["host"] == "192.0.2.40"   # the bridge's host, not the device's
    assert cfg["port"] == 4999           # serial:1 pass-through port
    # serial params survive for the bridge's set_SERIAL push
    assert cfg["baudrate"] == 9600
    # binding markers survive so the connect path can find the bridge to prep it
    assert cfg["bridge"] == "bridge1"
    assert cfg["bridge_port"] == "serial:1"


def test_serial_bridge_host_from_device_config(
    engine_with_project, fake_bridge_driver, fake_serial_device_driver
):
    """The bridge's host may live in its device.config (an imported or template
    project) rather than the connections table. Resolution must merge the same
    layers every device's connection uses — driver defaults < device.config <
    connections table — not read the connections table alone."""
    engine = engine_with_project
    bridge = DeviceConfig(
        id="bridge1", driver="fake_bridge_test", name="Bridge",
        config={"host": "192.0.2.55"},
    )
    downstream = DeviceConfig(
        id="disp1", driver="fake_serial_display_test", name="Display", config={}
    )
    engine.project.devices.extend([bridge, downstream])
    # No connections["bridge1"] host entry — the host is only in device.config.
    engine.project.connections["disp1"] = {
        "bridge": "bridge1",
        "bridge_port": "serial:1",
        "baudrate": 9600,
    }

    cfg = engine.resolved_device_config(downstream)["config"]
    assert cfg["transport"] == "tcp"
    assert cfg["host"] == "192.0.2.55"   # resolved from the bridge's device.config
    assert cfg["port"] == 4999


def test_bridge_unknown_device_leaves_binding_unresolved(
    engine_with_project, fake_serial_device_driver
):
    engine = engine_with_project
    downstream = DeviceConfig(
        id="disp1", driver="fake_serial_display_test", name="Display", config={}
    )
    engine.project.devices.append(downstream)
    engine.project.connections["disp1"] = {
        "bridge": "ghost", "bridge_port": "serial:1",
    }
    cfg = engine.resolved_device_config(downstream)["config"]
    assert cfg.get("host") is None
    assert cfg.get("transport") != "tcp"


def test_bridge_unknown_port_leaves_binding_unresolved(
    engine_with_project, fake_bridge_driver, fake_serial_device_driver
):
    engine = engine_with_project
    _, downstream = _add_bridge_project(engine)
    engine.project.connections["disp1"]["bridge_port"] = "serial:99"
    cfg = engine.resolved_device_config(downstream)["config"]
    assert cfg.get("port") != 4999


def test_bridge_missing_host_leaves_binding_unresolved(
    engine_with_project, fake_bridge_driver, fake_serial_device_driver
):
    engine = engine_with_project
    _, downstream = _add_bridge_project(engine, bridge_host="")
    cfg = engine.resolved_device_config(downstream)["config"]
    assert cfg.get("port") != 4999


def test_direct_serial_device_unaffected_by_bridge_resolver(
    engine_with_project, fake_serial_device_driver
):
    """A normal direct serial connection (no bridge) is left untouched."""
    engine = engine_with_project
    dev = DeviceConfig(
        id="d2", driver="fake_serial_display_test", name="Direct", config={}
    )
    engine.project.devices.append(dev)
    engine.project.connections["d2"] = {"port": "COM3", "baudrate": 19200}
    cfg = engine.resolved_device_config(dev)["config"]
    assert cfg["port"] == "COM3"
    assert cfg.get("transport") != "tcp"
