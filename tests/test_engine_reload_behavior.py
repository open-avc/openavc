"""Behavioral pins for the engine's hot-reload reconciliation.

Covers the observable contract of reload_project / save_project_checked:

- reload broadcasts ``ui.definition`` and ``project.reloaded`` (with the new
  revision) to WebSocket clients, and emits ``system.project.reloaded`` on the
  EventBus (a user-facing trigger pattern)
- variable sync: a new variable is seeded with its default, an existing
  variable keeps its live value, a removed variable's ``var.*`` key is deleted
- _sync_devices: adds new devices, removes deleted ones (cleaning their
  orphaned ``device.{id}.*`` state keys), and connects bridges before the
  devices that route through them
- bridge cross-dependency: editing only a bridge's connection re-resolves
  every device bound through it, because a bound device's effective host and
  port come from its bridge, not from its own row
- _sync_plugins: add / remove / enable / disable / config-change dispatch
- PUT /api/project end-to-end against a real engine: the bytes land on disk
  and the runtime picks them up (no stubbed save or reload)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from server.core.device_manager import register_driver, unregister_driver
from server.core.engine import Engine
from server.core.project_loader import load_project
from server.drivers.configurable import create_configurable_driver_class


def _write_project(path, *, name="P", variables=None, devices=None,
                   connections=None, plugins=None) -> None:
    project = {
        # Current format — these tests pin reload behavior, not migration.
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": name},
        "variables": variables or [],
        "macros": [],
        "devices": devices or [],
        "device_groups": [],
        "connections": connections or {},
        "scripts": [],
        "plugins": plugins or {},
        "ui": {
            "settings": {},
            "pages": [
                {"id": "main", "name": "Main",
                 "grid": {"columns": 12, "rows": 8}, "elements": []},
            ],
        },
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }
    Path(path).write_text(json.dumps(project), encoding="utf-8")


def _engine(tmp_path, **kwargs) -> Engine:
    path = tmp_path / "project.avc"
    _write_project(path, **kwargs)
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    return eng


def _mock_devices(running: dict) -> MagicMock:
    devices = MagicMock()
    devices.get_device_configs.return_value = dict(running)
    devices.get_device_config.side_effect = lambda did: running.get(did)
    devices.add_device = AsyncMock()
    devices.update_device = AsyncMock()
    devices.remove_device = AsyncMock()
    return devices


@pytest.fixture
def acme_bridge_driver():
    """Invented serial bridge: one serial port piped transparently on TCP 4999."""
    definition = {
        "id": "acme_bridge_reload_test",
        "name": "Acme Bridge (test)",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "bridge": {
            "ports": [
                {"id": "serial:1", "kind": "serial",
                 "passthrough_port": 4999, "label": "Serial Port 1"},
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
    unregister_driver("acme_bridge_reload_test")


@pytest.fixture
def acme_serial_display_driver():
    """Invented downstream serial device that can ride a bridge."""
    definition = {
        "id": "acme_serial_display_reload_test",
        "name": "Acme Serial Display (test)",
        "manufacturer": "Acme",
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
    unregister_driver("acme_serial_display_reload_test")


# ── Reload notifications ──


@pytest.mark.asyncio
async def test_reload_broadcasts_ui_definition_and_project_reloaded(tmp_path):
    eng = _engine(tmp_path)
    sent = []

    async def record(message):
        sent.append(message)

    eng.broadcast_ws = record

    await eng.reload_project()

    ui_msgs = [m for m in sent if m["type"] == "ui.definition"]
    assert len(ui_msgs) == 1
    assert "pages" in ui_msgs[0]["ui"]

    reloaded_msgs = [m for m in sent if m["type"] == "project.reloaded"]
    assert len(reloaded_msgs) == 1
    assert reloaded_msgs[0]["revision"] == eng._project_revision == 1

    await eng.triggers.stop()


@pytest.mark.asyncio
async def test_reload_emits_system_project_reloaded_event(tmp_path):
    """system.project.reloaded is a user-facing trigger pattern — macros can
    fire on it. A reload must emit it on the EventBus."""
    eng = _engine(tmp_path)
    fired = []
    eng.events.on("system.project.reloaded",
                  lambda event, payload: fired.append(event))

    await eng.reload_project()

    assert fired == ["system.project.reloaded"]
    await eng.triggers.stop()


# ── Variable sync ──


@pytest.mark.asyncio
async def test_reload_seeds_new_variable_default(tmp_path):
    eng = _engine(tmp_path)
    _write_project(eng.project_path,
                   variables=[{"id": "volume", "type": "number", "default": 42}])

    await eng.reload_project()

    assert eng.state.get("var.volume") == 42
    await eng.triggers.stop()


@pytest.mark.asyncio
async def test_reload_preserves_existing_variable_value(tmp_path):
    """A variable that already has a live value must NOT be reset to its
    default by a reload — only missing keys are seeded."""
    eng = _engine(
        tmp_path,
        variables=[{"id": "volume", "type": "number", "default": 42}],
    )
    eng.state.set("var.volume", 7, source="system")

    await eng.reload_project()

    assert eng.state.get("var.volume") == 7
    await eng.triggers.stop()


@pytest.mark.asyncio
async def test_reload_deletes_orphaned_variable_key(tmp_path):
    eng = _engine(tmp_path,
                  variables=[{"id": "old_var", "type": "string", "default": "x"}])
    eng.state.set("var.old_var", "x", source="system")
    _write_project(eng.project_path)  # variable removed from the project

    await eng.reload_project()

    assert eng.state.get("var.old_var") is None
    await eng.triggers.stop()


# ── Device sync ──


@pytest.mark.asyncio
async def test_sync_devices_adds_new_device(tmp_path):
    device = {"id": "d1", "driver": "generic_tcp", "name": "D1",
              "config": {"host": "192.0.2.10"}}
    eng = _engine(tmp_path, devices=[device])
    eng.devices = _mock_devices({})

    await eng._sync_devices()

    eng.devices.add_device.assert_awaited_once()
    assert eng.devices.add_device.await_args.args[0]["id"] == "d1"
    eng.devices.remove_device.assert_not_awaited()
    eng.devices.update_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_devices_removes_deleted_device_and_cleans_state(tmp_path):
    eng = _engine(tmp_path)  # empty project — the running device is gone
    eng.state.set("device.gone.power", True, source="system")
    eng.state.set("device.gone.connected", True, source="system")
    running = {"gone": {"id": "gone", "driver": "generic_tcp", "name": "G",
                        "config": {}}}
    eng.devices = _mock_devices(running)

    await eng._sync_devices()

    eng.devices.remove_device.assert_awaited_once_with("gone")
    assert eng.state.get("device.gone.power") is None
    assert eng.state.get("device.gone.connected") is None


@pytest.mark.asyncio
async def test_sync_devices_adds_bridges_before_their_dependents(
    tmp_path, acme_bridge_driver, acme_serial_display_driver
):
    """A bridge-bound device's connect path needs its bridge live first (to
    prep the serial port), so bridges are added ahead of other devices."""
    devices = [
        # Deliberately listed downstream-first — the sync must reorder.
        {"id": "disp1", "driver": "acme_serial_display_reload_test",
         "name": "Display", "config": {}},
        {"id": "bridge1", "driver": "acme_bridge_reload_test",
         "name": "Bridge", "config": {}},
    ]
    connections = {
        "bridge1": {"host": "192.0.2.40"},
        "disp1": {"bridge": "bridge1", "bridge_port": "serial:1",
                  "baudrate": 9600},
    }
    eng = _engine(tmp_path, devices=devices, connections=connections)
    eng.devices = _mock_devices({})
    order = []

    async def record_add(config):
        order.append(config["id"])

    eng.devices.add_device = AsyncMock(side_effect=record_add)

    await eng._sync_devices()

    assert order == ["bridge1", "disp1"]


@pytest.mark.asyncio
async def test_editing_bridge_connection_reresolves_bound_devices(
    tmp_path, acme_bridge_driver, acme_serial_display_driver
):
    """Editing ONLY the bridge's connection row must re-resolve every device
    bound through it. A bound device's effective host/port come from its
    bridge, so its own unchanged row does not mean its connection is
    unchanged. A per-device diff of raw project rows would miss this."""
    devices = [
        {"id": "bridge1", "driver": "acme_bridge_reload_test",
         "name": "Bridge", "config": {}},
        {"id": "disp1", "driver": "acme_serial_display_reload_test",
         "name": "Display", "config": {}},
    ]
    connections = {
        "bridge1": {"host": "192.0.2.40"},
        "disp1": {"bridge": "bridge1", "bridge_port": "serial:1",
                  "baudrate": 9600},
    }
    eng = _engine(tmp_path, devices=devices, connections=connections)

    # The runtime is in sync with the current project: snapshot the resolved
    # configs as the running set (deep-copied so the edit below can't leak in).
    running = {
        d.id: json.loads(json.dumps(eng.resolved_device_config(d)))
        for d in eng.project.devices
    }
    assert running["disp1"]["config"]["host"] == "192.0.2.40"
    assert running["disp1"]["config"]["port"] == 4999
    eng.devices = _mock_devices(running)

    # Edit ONLY the bridge's row; the display's own row is untouched.
    eng.project.connections["bridge1"]["host"] = "192.0.2.99"

    await eng._sync_devices()

    updated = {
        call.args[0]: call.args[1]
        for call in eng.devices.update_device.await_args_list
    }
    assert "disp1" in updated, (
        "a device bound through an edited bridge must be re-resolved"
    )
    assert updated["disp1"]["config"]["host"] == "192.0.2.99"
    assert updated["disp1"]["config"]["port"] == 4999
    assert "bridge1" in updated


# ── Plugin sync ──


def _mock_plugin_loader(known=(), running=(), configs=None) -> MagicMock:
    loader = MagicMock()
    running_set = set(running)
    configs = configs or {}
    loader.get_known_plugin_ids.return_value = set(known)
    loader.is_running.side_effect = lambda pid: pid in running_set
    loader.get_running_config.side_effect = lambda pid: configs.get(pid, {})
    loader.start_plugin = AsyncMock()
    loader.stop_plugin = AsyncMock()
    loader.remove_plugin_tracking = MagicMock()
    return loader


@pytest.mark.asyncio
async def test_sync_plugins_starts_new_enabled_plugin(tmp_path):
    eng = _engine(tmp_path, plugins={
        "p1": {"enabled": True, "config": {"level": 3}},
    })
    eng.plugin_loader = _mock_plugin_loader()

    await eng._sync_plugins()

    eng.plugin_loader.start_plugin.assert_awaited_once_with("p1", {"level": 3})


@pytest.mark.asyncio
async def test_sync_plugins_does_not_start_new_disabled_plugin(tmp_path):
    eng = _engine(tmp_path, plugins={
        "p1": {"enabled": False, "config": {}},
    })
    eng.plugin_loader = _mock_plugin_loader()

    await eng._sync_plugins()

    eng.plugin_loader.start_plugin.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_plugins_stops_and_untracks_removed_plugin(tmp_path):
    eng = _engine(tmp_path)  # plugin no longer in the project
    eng.plugin_loader = _mock_plugin_loader(known=["p1"], running=["p1"])

    await eng._sync_plugins()

    eng.plugin_loader.stop_plugin.assert_awaited_once_with("p1")
    eng.plugin_loader.remove_plugin_tracking.assert_called_once_with("p1")


@pytest.mark.asyncio
async def test_sync_plugins_untracks_removed_stopped_plugin_without_stop(tmp_path):
    eng = _engine(tmp_path)
    eng.plugin_loader = _mock_plugin_loader(known=["p1"], running=[])

    await eng._sync_plugins()

    eng.plugin_loader.stop_plugin.assert_not_awaited()
    eng.plugin_loader.remove_plugin_tracking.assert_called_once_with("p1")


@pytest.mark.asyncio
async def test_sync_plugins_stops_disabled_plugin(tmp_path):
    eng = _engine(tmp_path, plugins={
        "p1": {"enabled": False, "config": {}},
    })
    eng.plugin_loader = _mock_plugin_loader(known=["p1"], running=["p1"])

    await eng._sync_plugins()

    eng.plugin_loader.stop_plugin.assert_awaited_once_with("p1")
    eng.plugin_loader.start_plugin.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_plugins_starts_enabled_stopped_plugin(tmp_path):
    eng = _engine(tmp_path, plugins={
        "p1": {"enabled": True, "config": {"level": 9}},
    })
    eng.plugin_loader = _mock_plugin_loader(known=["p1"], running=[])

    await eng._sync_plugins()

    eng.plugin_loader.start_plugin.assert_awaited_once_with("p1", {"level": 9})
    eng.plugin_loader.stop_plugin.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_plugins_restarts_plugin_on_config_change(tmp_path):
    """A running plugin whose config changed must end up running with the new
    config — today that is a stop followed by a start."""
    eng = _engine(tmp_path, plugins={
        "p1": {"enabled": True, "config": {"level": 2}},
    })
    eng.plugin_loader = _mock_plugin_loader(
        known=["p1"], running=["p1"], configs={"p1": {"level": 1}},
    )

    await eng._sync_plugins()

    eng.plugin_loader.stop_plugin.assert_awaited_once_with("p1")
    eng.plugin_loader.start_plugin.assert_awaited_once_with("p1", {"level": 2})


@pytest.mark.asyncio
async def test_sync_plugins_leaves_unchanged_running_plugin_alone(tmp_path):
    eng = _engine(tmp_path, plugins={
        "p1": {"enabled": True, "config": {"level": 1}},
    })
    eng.plugin_loader = _mock_plugin_loader(
        known=["p1"], running=["p1"], configs={"p1": {"level": 1}},
    )

    await eng._sync_plugins()

    eng.plugin_loader.stop_plugin.assert_not_awaited()
    eng.plugin_loader.start_plugin.assert_not_awaited()


# ── PUT /api/project end to end ──


@pytest.mark.asyncio
async def test_put_project_persists_bytes_and_hot_reloads(tmp_path):
    """The full PUT path against a real engine — nothing stubbed. The request
    must write the new project to disk (with the crash-protection backup) and
    the running engine must pick up the change."""
    from server.api import rest, ws
    from server.main import app

    eng = _engine(tmp_path)
    rest.set_engine(eng)
    ws.set_engine(eng)

    body = {
        "project": {"id": "p", "name": "Renamed via PUT"},
        "variables": [{"id": "volume", "type": "number", "default": 42}],
    }
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport,
                           base_url="http://testserver") as client:
        resp = await client.put("/api/project", json=body,
                                headers={"If-Match": '"0"'})

    assert resp.status_code == 200
    assert resp.headers["etag"] == '"1"'

    # The bytes landed on disk, with the previous file kept as .avc.bak.
    on_disk = json.loads(Path(eng.project_path).read_text(encoding="utf-8"))
    assert on_disk["project"]["name"] == "Renamed via PUT"
    assert on_disk["variables"][0]["id"] == "volume"
    bak = Path(eng.project_path).with_suffix(".avc.bak")
    assert json.loads(bak.read_text(encoding="utf-8"))["project"]["name"] == "P"

    # The runtime applied the new project, not just the disk.
    assert eng.project.project.name == "Renamed via PUT"
    assert eng._project_revision == 1
    assert eng.state.get("var.volume") == 42

    await eng.triggers.stop()
