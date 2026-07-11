"""Tests for project format migration system."""

import copy


from server.core.project_migration import (
    CURRENT_VERSION,
    migrate_0_1_to_0_2,
    migrate_0_2_to_0_3,
    migrate_0_3_to_0_4,
    migrate_0_4_to_0_5,
    migrate_0_5_to_0_6,
    migrate_0_6_to_0_7,
    migrate_project,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal project data at each version
# ---------------------------------------------------------------------------

def make_v01_project(**overrides) -> dict:
    """Minimal v0.1.0 project (no openavc_version field = 0.1.0)."""
    data = {
        "project": {"id": "test", "name": "Test Project"},
        "devices": [
            {
                "id": "proj1",
                "driver": "pjlink_class1",
                "name": "Projector",
                "config": {
                    "host": "192.168.1.10",
                    "port": 4352,
                    "password": "secret",
                    "brightness_mode": "eco",
                },
            },
            {
                "id": "amp1",
                "driver": "generic_tcp",
                "name": "Amplifier",
                "config": {
                    "host": "192.168.1.20",
                    "port": 9090,
                },
            },
            {
                "id": "relay1",
                "driver": "gpio_relay",
                "name": "Relay",
                "config": {"pin": 17},
            },
        ],
        "variables": [],
        "macros": [],
        "ui": {"pages": []},
    }
    data.update(overrides)
    return data


def make_v02_project(**overrides) -> dict:
    """Minimal v0.2.0 project."""
    data = make_v01_project()
    data = migrate_0_1_to_0_2(data)
    data.update(overrides)
    return data


def make_v03_project(**overrides) -> dict:
    """Minimal v0.3.0 project."""
    data = make_v02_project()
    data = migrate_0_2_to_0_3(data)
    data.update(overrides)
    return data


def make_v04_project(**overrides) -> dict:
    """Minimal v0.4.0 project."""
    data = make_v03_project()
    data = migrate_0_3_to_0_4(data)
    data.update(overrides)
    return data


def make_v05_project(**overrides) -> dict:
    """Minimal v0.5.0 project."""
    data = make_v04_project()
    data = migrate_0_4_to_0_5(data)
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 0.1.0 → 0.2.0
# ---------------------------------------------------------------------------

class TestMigrate01To02:
    def test_connection_fields_moved(self):
        data = make_v01_project()
        result = migrate_0_1_to_0_2(copy.deepcopy(data))

        # Connection fields moved to connections table
        assert "connections" in result
        assert result["connections"]["proj1"] == {
            "host": "192.168.1.10",
            "port": 4352,
            "password": "secret",
        }
        assert result["connections"]["amp1"] == {
            "host": "192.168.1.20",
            "port": 9090,
        }

    def test_non_connection_fields_stay_in_config(self):
        data = make_v01_project()
        result = migrate_0_1_to_0_2(copy.deepcopy(data))

        # Non-connection config fields should stay
        proj1_config = next(d for d in result["devices"] if d["id"] == "proj1")["config"]
        assert proj1_config == {"brightness_mode": "eco"}

    def test_device_without_connection_fields(self):
        data = make_v01_project()
        result = migrate_0_1_to_0_2(copy.deepcopy(data))

        # relay1 has no connection fields — no entry in connections
        assert "relay1" not in result["connections"]
        relay_config = next(d for d in result["devices"] if d["id"] == "relay1")["config"]
        assert relay_config == {"pin": 17}

    def test_driver_dependencies_added(self):
        data = make_v01_project()
        result = migrate_0_1_to_0_2(copy.deepcopy(data))
        assert result["driver_dependencies"] == []

    def test_version_bumped(self):
        data = make_v01_project()
        result = migrate_0_1_to_0_2(copy.deepcopy(data))
        assert result["openavc_version"] == "0.2.0"

    def test_no_devices(self):
        data = {"project": {"id": "empty", "name": "Empty"}, "devices": []}
        result = migrate_0_1_to_0_2(copy.deepcopy(data))
        assert result["connections"] == {}
        assert result["openavc_version"] == "0.2.0"

    def test_empty_config(self):
        data = {
            "project": {"id": "t", "name": "T"},
            "devices": [{"id": "d1", "driver": "x", "name": "D", "config": {}}],
        }
        result = migrate_0_1_to_0_2(copy.deepcopy(data))
        assert "d1" not in result["connections"]

    def test_existing_driver_dependencies_preserved(self):
        data = make_v01_project(driver_dependencies=["pjlink_class1"])
        result = migrate_0_1_to_0_2(copy.deepcopy(data))
        assert result["driver_dependencies"] == ["pjlink_class1"]


# ---------------------------------------------------------------------------
# 0.2.0 → 0.3.0
# ---------------------------------------------------------------------------

class TestMigrate02To03:
    def test_plugins_added(self):
        data = make_v02_project()
        result = migrate_0_2_to_0_3(copy.deepcopy(data))
        assert result["plugins"] == {}

    def test_plugin_dependencies_added(self):
        data = make_v02_project()
        result = migrate_0_2_to_0_3(copy.deepcopy(data))
        assert result["plugin_dependencies"] == []

    def test_version_bumped(self):
        data = make_v02_project()
        result = migrate_0_2_to_0_3(copy.deepcopy(data))
        assert result["openavc_version"] == "0.3.0"

    def test_existing_plugins_preserved(self):
        data = make_v02_project(plugins={"my_plugin": {"enabled": True}})
        result = migrate_0_2_to_0_3(copy.deepcopy(data))
        assert result["plugins"] == {"my_plugin": {"enabled": True}}

    def test_connections_survive(self):
        data = make_v02_project()
        conns_before = copy.deepcopy(data["connections"])
        result = migrate_0_2_to_0_3(copy.deepcopy(data))
        assert result["connections"] == conns_before


# ---------------------------------------------------------------------------
# 0.3.0 → 0.4.0
# ---------------------------------------------------------------------------

class TestMigrate03To04:
    def test_device_groups_added(self):
        data = make_v03_project()
        result = migrate_0_3_to_0_4(copy.deepcopy(data))
        assert result["device_groups"] == []

    def test_group_field_removed_from_devices(self):
        data = make_v03_project()
        data["devices"][0]["group"] = "Projectors"
        data["devices"][1]["group"] = "Audio"
        result = migrate_0_3_to_0_4(copy.deepcopy(data))

        for dev in result["devices"]:
            assert "group" not in dev

    def test_version_bumped(self):
        data = make_v03_project()
        result = migrate_0_3_to_0_4(copy.deepcopy(data))
        assert result["openavc_version"] == "0.4.0"

    def test_no_group_field_is_fine(self):
        data = make_v03_project()
        # Devices don't have group field — should not error
        result = migrate_0_3_to_0_4(copy.deepcopy(data))
        assert result["openavc_version"] == "0.4.0"

    def test_existing_device_groups_preserved(self):
        data = make_v03_project(device_groups=[{"id": "g1", "name": "G", "device_ids": []}])
        result = migrate_0_3_to_0_4(copy.deepcopy(data))
        assert len(result["device_groups"]) == 1
        assert result["device_groups"][0]["id"] == "g1"

    def test_dotted_group_name_migrates_to_loadable_id(self):
        # A group named "Row.1" must not produce a dotted id — DeviceGroup's
        # validator rejects dots, which previously made the whole project
        # unloadable after auto-migration.
        from server.core.project_loader import DeviceGroup

        data = make_v03_project()
        data["devices"][0]["group"] = "Row.1"
        result = migrate_0_3_to_0_4(copy.deepcopy(data))
        group = result["device_groups"][0]
        assert "." not in group["id"]
        DeviceGroup(**group)  # the real validator accepts the migrated id

    def test_per_device_groups_merged_into_existing_device_groups(self):
        # A file carrying BOTH a device_groups list and lingering per-device
        # group fields must not lose the per-device memberships.
        data = make_v03_project(
            device_groups=[{"id": "existing", "name": "Existing", "device_ids": []}]
        )
        dev_id = data["devices"][0]["id"]
        data["devices"][0]["group"] = "Projectors"
        result = migrate_0_3_to_0_4(copy.deepcopy(data))

        ids = {g["id"] for g in result["device_groups"]}
        assert "existing" in ids           # existing group preserved
        assert "projectors" in ids         # per-device group not dropped
        projectors = next(g for g in result["device_groups"] if g["id"] == "projectors")
        assert dev_id in projectors["device_ids"]

    def test_per_device_group_merges_into_matching_existing_group(self):
        # When an existing group already has the id a device's group name
        # sanitizes to, the device merges into it (no duplicate group).
        data = make_v03_project(
            device_groups=[{"id": "projectors", "name": "Projectors", "device_ids": []}]
        )
        dev_id = data["devices"][0]["id"]
        data["devices"][0]["group"] = "Projectors"
        result = migrate_0_3_to_0_4(copy.deepcopy(data))

        projectors = [g for g in result["device_groups"] if g["id"] == "projectors"]
        assert len(projectors) == 1        # merged, not duplicated
        assert dev_id in projectors[0]["device_ids"]


# ---------------------------------------------------------------------------
# 0.4.0 → 0.5.0
# ---------------------------------------------------------------------------

class TestMigrate04To05:
    def test_version_bumped(self):
        data = make_v04_project()
        result = migrate_0_4_to_0_5(copy.deepcopy(data))
        assert result["openavc_version"] == "0.5.0"

    def test_child_entities_added_to_every_device(self):
        """Migration injects an empty child_entities dict on each device
        so the on-disk schema has the new key, even before any controller
        driver populates it."""
        data = make_v04_project()
        result = migrate_0_4_to_0_5(copy.deepcopy(data))
        for device in result["devices"]:
            assert device["child_entities"] == {}

    def test_existing_child_entities_preserved(self):
        """A v0.4.0 file that already happens to carry child_entities
        (hand-edited or imported from a future version) keeps its
        contents through migration."""
        data = make_v04_project()
        data["devices"][0]["child_entities"] = {
            "encoder": {"005": {"label": "Lobby TX"}},
        }
        result = migrate_0_4_to_0_5(copy.deepcopy(data))
        assert result["devices"][0]["child_entities"] == {
            "encoder": {"005": {"label": "Lobby TX"}},
        }

    def test_no_devices(self):
        """A project with no devices migrates cleanly (just version bump)."""
        data = {"project": {"id": "x", "name": "X"}, "devices": []}
        result = migrate_0_4_to_0_5(copy.deepcopy(data))
        assert result["openavc_version"] == "0.5.0"
        assert result["devices"] == []


class TestMigrate05To06:
    def test_version_bumped(self):
        data = make_v05_project()
        result = migrate_0_5_to_0_6(copy.deepcopy(data))
        assert result["openavc_version"] == "0.6.0"

    def test_connections_table_preserved(self):
        """The bridge model adds free-form keys to the existing connections
        table, so the version-stamp migration must leave existing connection
        entries untouched."""
        data = make_v05_project()
        data["connections"] = {"proj1": {"host": "192.168.1.10", "port": 4352}}
        result = migrate_0_5_to_0_6(copy.deepcopy(data))
        assert result["connections"]["proj1"] == {
            "host": "192.168.1.10", "port": 4352,
        }

    def test_no_structural_change(self):
        """Version-stamp migration: device shape is unchanged."""
        data = make_v05_project()
        before_devices = copy.deepcopy(data["devices"])
        result = migrate_0_5_to_0_6(copy.deepcopy(data))
        assert result["devices"] == before_devices


# ---------------------------------------------------------------------------
# Full chain: 0.1.0 → CURRENT_VERSION
# ---------------------------------------------------------------------------

class TestFullMigrationChain:
    def test_0_1_to_current(self):
        data = make_v01_project()
        result, migrated = migrate_project(copy.deepcopy(data))

        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION

        # All migration artifacts present
        assert "connections" in result
        assert "driver_dependencies" in result
        assert "plugins" in result
        assert "plugin_dependencies" in result
        assert "device_groups" in result

        # Connection fields migrated correctly
        assert result["connections"]["proj1"]["host"] == "192.168.1.10"

    def test_0_2_to_current(self):
        data = make_v02_project()
        result, migrated = migrate_project(copy.deepcopy(data))

        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION
        assert "plugins" in result
        assert "device_groups" in result

    def test_0_3_to_current(self):
        data = make_v03_project()
        result, migrated = migrate_project(copy.deepcopy(data))

        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION
        assert "device_groups" in result

    def test_current_version_not_migrated(self):
        data = make_v05_project()
        data = migrate_0_5_to_0_6(data)
        data = migrate_0_6_to_0_7(data)  # Now at CURRENT_VERSION (0.7.0)
        result, migrated = migrate_project(copy.deepcopy(data))

        assert migrated is False
        assert result["openavc_version"] == CURRENT_VERSION

    def test_0_4_to_current(self):
        data = make_v04_project()
        result, migrated = migrate_project(copy.deepcopy(data))

        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION
        # Every device gained an empty child_entities entry.
        for device in result["devices"]:
            assert device["child_entities"] == {}

    def test_missing_version_treated_as_0_1(self):
        data = {"project": {"id": "old", "name": "Old"}, "devices": []}
        # No openavc_version field
        assert "openavc_version" not in data
        result, migrated = migrate_project(copy.deepcopy(data))

        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION


# ---------------------------------------------------------------------------
# Edge cases and malformed input
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_project(self):
        data = {}
        result, migrated = migrate_project(copy.deepcopy(data))
        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION
        assert result.get("connections") == {}
        assert result.get("device_groups") == []

    def test_missing_devices_key(self):
        data = {"openavc_version": "0.1.0"}
        result, migrated = migrate_project(copy.deepcopy(data))
        assert migrated is True
        assert result["connections"] == {}

    def test_extra_fields_preserved(self):
        data = make_v01_project()
        data["custom_field"] = "custom_value"
        data["another"] = [1, 2, 3]
        result, migrated = migrate_project(copy.deepcopy(data))

        assert result["custom_field"] == "custom_value"
        assert result["another"] == [1, 2, 3]

    def test_device_with_missing_config(self):
        data = {
            "openavc_version": "0.1.0",
            "devices": [{"id": "d1", "driver": "x", "name": "D"}],
        }
        result, migrated = migrate_project(copy.deepcopy(data))
        assert migrated is True
        # Should not crash on missing config key

    def test_device_with_missing_id(self):
        data = {
            "openavc_version": "0.1.0",
            "devices": [{"driver": "x", "name": "D", "config": {"host": "1.2.3.4"}}],
        }
        result, migrated = migrate_project(copy.deepcopy(data))
        assert migrated is True
        # Empty string ID gets a connection entry
        assert "" in result["connections"]

    def test_unknown_version_no_migration(self):
        data = {"openavc_version": "99.0.0", "devices": []}
        result, migrated = migrate_project(copy.deepcopy(data))
        assert migrated is False
        assert result["openavc_version"] == "99.0.0"

    def test_idempotent_double_migration(self):
        data = make_v01_project()
        result1, _ = migrate_project(copy.deepcopy(data))
        result2, migrated = migrate_project(copy.deepcopy(result1))

        assert migrated is False
        assert result2["openavc_version"] == CURRENT_VERSION
