"""Tests for project lifecycle features: migration, connections, orphans, driver deps."""

import copy
import json
import tempfile
from pathlib import Path

import pytest

from server.core.device_manager import DeviceManager, _DRIVER_REGISTRY
from server.core.event_bus import EventBus
from server.core.project_loader import (
    ProjectConfig,
    build_driver_dependencies,
    load_project,
    save_project,
)
from server.core.project_migration import CURRENT_VERSION, migrate_project
from server.core.state_store import StateStore


# ---------------------------------------------------------------------------
# Fixtures — NEVER use the live project.avc
# ---------------------------------------------------------------------------

TEST_PROJECT_OLD = {
    "project": {"id": "test_lifecycle", "name": "Lifecycle Test"},
    "devices": [
        {
            "id": "projector1",
            "driver": "pjlink_class1",
            "name": "Test Projector",
            "config": {"host": "192.168.1.100", "port": 4352, "poll_interval": 15},
        },
        {
            "id": "display1",
            "driver": "samsung_mdc",
            "name": "Test Display",
            "config": {"host": "10.0.0.50", "port": 1515, "com_port": "COM3"},
        },
    ],
}

TEST_PROJECT_NEW = {
    "openavc_version": "0.4.0",
    "project": {"id": "test_lifecycle", "name": "Lifecycle Test"},
    "devices": [
        {
            "id": "projector1",
            "driver": "pjlink_class1",
            "name": "Test Projector",
            "config": {"poll_interval": 15},
        },
    ],
    "connections": {
        "projector1": {"host": "192.168.1.100", "port": 4352},
    },
    "driver_dependencies": [],
    "plugins": {},
    "plugin_dependencies": [],
}


@pytest.fixture
def old_project_path():
    """Write the OLD-format project to a temp file."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(TEST_PROJECT_OLD, f)
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def new_project_path():
    """Write the NEW-format project to a temp file."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(TEST_PROJECT_NEW, f)
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def dm():
    """DeviceManager wired to fresh StateStore + EventBus."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return DeviceManager(state, events), state, events


# ===========================================================================
# 1. Migration tests
# ===========================================================================


class TestMigration:

    def test_old_project_migrates_to_current_version(self):
        """0.1.0 project with host/port in config -> 0.2.0 with connections table."""
        data = copy.deepcopy(TEST_PROJECT_OLD)
        migrated, was_migrated = migrate_project(data)

        assert was_migrated is True
        assert migrated["openavc_version"] == CURRENT_VERSION

        # Connection fields moved out of device config
        proj_config = migrated["devices"][0]["config"]
        assert "host" not in proj_config
        assert "port" not in proj_config
        assert proj_config["poll_interval"] == 15

        # Connection fields now in connections table
        assert migrated["connections"]["projector1"]["host"] == "192.168.1.100"
        assert migrated["connections"]["projector1"]["port"] == 4352

        # display1 had host, port, AND com_port
        disp_config = migrated["devices"][1]["config"]
        assert "host" not in disp_config
        assert "port" not in disp_config
        assert "com_port" not in disp_config

        disp_conn = migrated["connections"]["display1"]
        assert disp_conn["host"] == "10.0.0.50"
        assert disp_conn["port"] == 1515
        assert disp_conn["com_port"] == "COM3"

    def test_migration_is_idempotent(self):
        """Running migration on an already-migrated project changes nothing."""
        data = copy.deepcopy(TEST_PROJECT_NEW)
        migrated, was_migrated = migrate_project(data)

        assert was_migrated is False
        assert migrated["openavc_version"] == "0.4.0"
        assert migrated["connections"] == TEST_PROJECT_NEW["connections"]
        assert migrated["devices"][0]["config"] == TEST_PROJECT_NEW["devices"][0]["config"]

    def test_empty_project_migrates_cleanly(self):
        """A minimal project with no devices should migrate without errors."""
        data = {"project": {"id": "empty", "name": "Empty Room"}}
        migrated, was_migrated = migrate_project(data)

        assert was_migrated is True
        assert migrated["openavc_version"] == CURRENT_VERSION
        assert migrated["connections"] == {}
        assert migrated.get("driver_dependencies") == []

    def test_load_old_project_produces_valid_model(self, old_project_path):
        """load_project() should auto-migrate and return a valid ProjectConfig."""
        project = load_project(old_project_path)

        assert isinstance(project, ProjectConfig)
        assert project.openavc_version == CURRENT_VERSION
        assert "projector1" in project.connections
        assert project.connections["projector1"]["host"] == "192.168.1.100"
        # host/port should NOT be in device config after migration
        assert "host" not in project.devices[0].config
        assert "port" not in project.devices[0].config

    def test_migration_persists_to_disk(self, old_project_path):
        """After loading an old project, the migrated format should be saved back."""
        load_project(old_project_path)

        # Re-read the raw file — it should now be in 0.2.0 format
        raw = json.loads(Path(old_project_path).read_text(encoding="utf-8"))
        assert raw["openavc_version"] == CURRENT_VERSION
        assert "connections" in raw

    def test_device_with_no_connection_fields(self):
        """A device with only non-connection config should have no connections entry."""
        data = {
            "project": {"id": "test", "name": "Test"},
            "devices": [
                {
                    "id": "dev1",
                    "driver": "some_driver",
                    "name": "Dev",
                    "config": {"poll_interval": 10, "custom_setting": "abc"},
                },
            ],
        }
        migrated, was_migrated = migrate_project(data)

        assert was_migrated is True
        # Device has no connection fields, so no entry in connections
        assert "dev1" not in migrated["connections"]
        # Non-connection config stays intact
        assert migrated["devices"][0]["config"]["poll_interval"] == 10
        assert migrated["devices"][0]["config"]["custom_setting"] == "abc"


# ===========================================================================
# 2. Connection table tests
# ===========================================================================


class TestConnectionTable:

    def test_connection_merge_produces_correct_config(self):
        """device.config + connections override should produce correct merged config."""
        project = ProjectConfig(**TEST_PROJECT_NEW)

        device = project.devices[0]
        conn = project.connections.get(device.id, {})
        merged = {**device.config, **conn}

        assert merged["poll_interval"] == 15
        assert merged["host"] == "192.168.1.100"
        assert merged["port"] == 4352

    def test_connection_defaults_preserved_when_no_override(self):
        """Fields in device.config not overridden by connections should keep defaults."""
        data = copy.deepcopy(TEST_PROJECT_NEW)
        # Add extra config fields
        data["devices"][0]["config"]["timeout"] = 30
        data["devices"][0]["config"]["encoding"] = "utf-8"
        project = ProjectConfig(**data)

        device = project.devices[0]
        conn = project.connections.get(device.id, {})
        merged = {**device.config, **conn}

        # Connection fields present
        assert merged["host"] == "192.168.1.100"
        assert merged["port"] == 4352
        # Config defaults preserved
        assert merged["timeout"] == 30
        assert merged["encoding"] == "utf-8"
        assert merged["poll_interval"] == 15

    def test_device_without_connection_entry(self):
        """A device with no connections entry should use its config as-is."""
        data = copy.deepcopy(TEST_PROJECT_NEW)
        data["devices"].append({
            "id": "standalone",
            "driver": "generic_tcp",
            "name": "Standalone",
            "config": {"poll_interval": 5},
        })
        project = ProjectConfig(**data)

        device = next(d for d in project.devices if d.id == "standalone")
        conn = project.connections.get(device.id, {})
        merged = {**device.config, **conn}

        assert merged == {"poll_interval": 5}

    def test_connection_override_wins_over_device_config(self):
        """If both device.config and connections have the same key, connections wins."""
        data = copy.deepcopy(TEST_PROJECT_NEW)
        # Put a conflicting value in device config
        data["devices"][0]["config"]["host"] = "should-be-overridden"
        project = ProjectConfig(**data)

        device = project.devices[0]
        conn = project.connections.get(device.id, {})
        merged = {**device.config, **conn}

        # Connection table value wins
        assert merged["host"] == "192.168.1.100"


# ===========================================================================
# 3. Orphaned device tests
# ===========================================================================


class TestOrphanedDevices:

    async def test_nonexistent_driver_creates_orphan(self, dm):
        """Adding a device with a missing driver should create an orphaned device."""
        manager, state, _ = dm
        await manager.add_device({
            "id": "orphan1",
            "driver": "nonexistent_driver_xyz",
            "name": "Orphaned Device",
            "config": {},
        })

        assert "orphan1" in manager._orphaned_devices
        assert state.get("device.orphan1.orphaned") is True
        assert "nonexistent_driver_xyz" in state.get("device.orphan1.orphan_reason")

    async def test_orphaned_devices_in_list(self, dm):
        """Orphaned devices should appear in list_devices() with orphaned=True."""
        manager, _, _ = dm
        await manager.add_device({
            "id": "orphan1",
            "driver": "missing_driver",
            "name": "Orphan",
            "config": {},
        })

        devices = manager.list_devices()
        assert len(devices) == 1
        assert devices[0]["id"] == "orphan1"
        assert devices[0]["orphaned"] is True
        assert "missing_driver" in devices[0]["orphan_reason"]

    async def test_get_device_info_for_orphan(self, dm):
        """get_device_info() should work for orphaned devices."""
        manager, _, _ = dm
        await manager.add_device({
            "id": "orphan1",
            "driver": "no_such_driver",
            "name": "My Orphan",
            "config": {"some_key": "some_val"},
        })

        info = manager.get_device_info("orphan1")
        assert info["id"] == "orphan1"
        assert info["name"] == "My Orphan"
        assert info["driver"] == "no_such_driver"
        assert info["orphaned"] is True
        assert info["connected"] is False
        assert info["commands"] == {}
        assert info["config"] == {"some_key": "some_val"}

    async def test_remove_orphaned_device(self, dm):
        """Removing an orphaned device should clean up all tracking."""
        manager, state, _ = dm
        await manager.add_device({
            "id": "orphan1",
            "driver": "ghost_driver",
            "name": "Ghost",
            "config": {},
        })
        assert "orphan1" in manager._orphaned_devices

        await manager.remove_device("orphan1")

        assert "orphan1" not in manager._orphaned_devices
        assert "orphan1" not in manager._device_configs
        assert len(manager.list_devices()) == 0

    async def test_update_orphan_to_active(self, dm):
        """update_device() on an orphan should handle the orphan-to-active transition."""
        manager, state, _ = dm

        # First, add as orphaned (driver doesn't exist)
        await manager.add_device({
            "id": "dev1",
            "driver": "fake_driver_999",
            "name": "Was Orphan",
            "config": {},
        })
        assert "dev1" in manager._orphaned_devices

        # Update to use a real driver (generic_tcp is always registered)
        assert "generic_tcp" in _DRIVER_REGISTRY  # sanity check
        await manager.update_device("dev1", {
            "id": "dev1",
            "driver": "generic_tcp",
            "name": "Now Active",
            "config": {"host": "127.0.0.1", "port": 9999},
        })

        # Should no longer be orphaned
        assert "dev1" not in manager._orphaned_devices
        assert "dev1" in manager._devices
        assert state.get("device.dev1.name") == "Now Active"
        await manager.disconnect_all()

    async def test_orphan_emits_event(self, dm):
        """Adding an orphaned device should emit a device.orphaned event."""
        manager, _, events = dm
        received = []
        events.on("device.orphaned", lambda event, data: received.append(data))

        await manager.add_device({
            "id": "orphan1",
            "driver": "vanished_driver",
            "name": "Orphan",
            "config": {},
        })

        assert len(received) == 1
        assert received[0]["device_id"] == "orphan1"
        assert received[0]["driver"] == "vanished_driver"


# ===========================================================================
# 4. Driver dependency tests
# ===========================================================================


class TestDriverDependencies:

    def test_save_populates_driver_dependencies(self):
        """save_project() should auto-populate driver_dependencies."""
        data = copy.deepcopy(TEST_PROJECT_NEW)
        # generic_tcp is always in the registry
        data["devices"] = [{
            "id": "dev1",
            "driver": "generic_tcp",
            "name": "Test",
            "config": {},
        }]
        data["connections"] = {}
        project = ProjectConfig(**data)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            path = f.name

        try:
            save_project(path, project)

            # Reload and check dependencies
            saved = json.loads(Path(path).read_text(encoding="utf-8"))
            deps = saved.get("driver_dependencies", [])
            dep_ids = [d["driver_id"] for d in deps]
            assert "generic_tcp" in dep_ids
        finally:
            Path(path).unlink(missing_ok=True)

    def test_unknown_driver_marked_as_unknown_source(self):
        """Projects with unrecognized drivers should list them with source='unknown'."""
        data = copy.deepcopy(TEST_PROJECT_NEW)
        data["devices"] = [{
            "id": "dev1",
            "driver": "totally_fake_unknown_driver",
            "name": "Unknown",
            "config": {},
        }]
        data["connections"] = {}
        project = ProjectConfig(**data)

        deps = build_driver_dependencies(project)
        assert len(deps) == 1
        assert deps[0].driver_id == "totally_fake_unknown_driver"
        assert deps[0].source == "unknown"

    def test_dependencies_deduplicated(self):
        """Multiple devices using the same driver should produce one dependency."""
        data = copy.deepcopy(TEST_PROJECT_NEW)
        data["devices"] = [
            {"id": "dev1", "driver": "generic_tcp", "name": "A", "config": {}},
            {"id": "dev2", "driver": "generic_tcp", "name": "B", "config": {}},
        ]
        data["connections"] = {}
        project = ProjectConfig(**data)

        deps = build_driver_dependencies(project)
        tcp_deps = [d for d in deps if d.driver_id == "generic_tcp"]
        assert len(tcp_deps) == 1

    def test_empty_project_has_no_dependencies(self):
        """A project with no devices should have empty driver_dependencies."""
        data = {
            "openavc_version": "0.2.0",
            "project": {"id": "empty", "name": "Empty"},
            "devices": [],
            "connections": {},
            "driver_dependencies": [],
        }
        project = ProjectConfig(**data)
        deps = build_driver_dependencies(project)
        assert deps == []
