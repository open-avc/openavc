"""Tests for project format migration system."""

import copy
import json

import pytest


from openavc.core.project_migration import (
    CURRENT_VERSION,
    migrate_0_1_to_0_2,
    migrate_0_2_to_0_3,
    migrate_0_3_to_0_4,
    migrate_0_4_to_0_5,
    migrate_0_5_to_0_6,
    migrate_0_6_to_0_7,
    migrate_0_7_to_0_8,
    migrate_0_8_to_0_9,
    migrate_0_9_to_0_10,
    migrate_0_10_to_0_11,
    migrate_0_11_to_0_12,
    migrate_0_12_to_0_13,
    migrate_0_13_to_0_14,
    migrate_0_14_to_0_15,
    migrate_0_15_to_0_16,
    migrate_0_16_to_0_17,
    migrate_0_17_to_0_18,
    migrate_project,
)
from openavc.core.project_loader import ProjectConfig
from openavc.ui.matrix_model import resolve_axis
from openavc.ui.page_review import review_master_element, review_page


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
        from openavc.core.project_loader import DeviceGroup

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
        data = migrate_0_6_to_0_7(data)
        data = migrate_0_7_to_0_8(data)
        data = migrate_0_8_to_0_9(data)
        data = migrate_0_9_to_0_10(data)
        data = migrate_0_10_to_0_11(data)
        data = migrate_0_11_to_0_12(data)
        data = migrate_0_12_to_0_13(data)
        data = migrate_0_13_to_0_14(data)
        data = migrate_0_14_to_0_15(data)
        data = migrate_0_15_to_0_16(data)
        data = migrate_0_16_to_0_17(data)
        data = migrate_0_17_to_0_18(data)  # Now at CURRENT_VERSION
        result, migrated = migrate_project(copy.deepcopy(data))

        assert migrated is False
        assert result["openavc_version"] == CURRENT_VERSION

    def test_0_9_to_0_10_turns_counts_and_patterns_into_a_generator(self):
        """The shorthand survives as a generator, and draws the same matrix.

        ``input_count: 8`` plus a ``route_key_pattern`` said "eight
        destinations, numbered from one, each watching its number substituted
        into that key". The generator says exactly that, in the shape that can
        also say the things the pattern form could not.
        """
        data = {
            "openavc_version": "0.9.0",
            "project": {"id": "p", "name": "P"},
            "ui": {"pages": [{"id": "main", "name": "Main", "elements": [{
                "id": "mx", "type": "matrix", "matrix_config": {
                    "input_count": 3, "output_count": 8,
                    "input_labels": ["Apple TV", "Room PC", "Laptop"],
                    "input_key_pattern": "device.mx.input.*.name",
                    "route_key_pattern": "device.mx.output.*.input",
                    "audio_route_key_pattern": "device.mx.output.*.audio",
                    "show_lock": False,
                    "presets": [{"name": "All 1", "macro": "m"}],
                },
            }]}]},
        }

        result = migrate_0_9_to_0_10(copy.deepcopy(data))
        config = result["ui"]["pages"][0]["elements"][0]["matrix_config"]

        assert config["sources"] == {"from": {
            "count": 3,
            "labels": ["Apple TV", "Room PC", "Laptop"],
            "label_key": "device.mx.input.*.name",
        }}
        assert config["destinations"] == {"from": {
            "count": 8,
            "route_key": "device.mx.output.*.input",
            "audio_route_key": "device.mx.output.*.audio",
        }}
        # Read straight by the renderer, so they pass through untouched...
        assert config["show_lock"] is False
        assert config["presets"] == [{"name": "All 1", "macro": "m"}]
        # ...and the eight keys that became the generator are gone, or every
        # migrated project would open warning about its own history.
        assert not {k for k in config if k.endswith(("_count", "_pattern", "_labels"))}
        assert result["openavc_version"] == "0.10.0"

    def test_0_9_to_0_10_resolves_back_to_what_the_panel_drew(self):
        """The point of the migration, stated as the thing that must not move."""
        data = {
            "openavc_version": "0.9.0",
            "project": {"id": "p", "name": "P"},
            "ui": {"pages": [{"id": "main", "name": "Main", "elements": [{
                "id": "mx", "type": "matrix", "matrix_config": {
                    "input_count": 2, "output_count": 2,
                    "output_labels": ["Main LCD", "Confidence"],
                    "route_key_pattern": "device.mx.output.*.input",
                },
            }]}]},
        }

        result = migrate_0_9_to_0_10(copy.deepcopy(data))
        config = result["ui"]["pages"][0]["elements"][0]["matrix_config"]

        assert resolve_axis(config, "sources") == [
            {"value": 1, "label": "In 1"},
            {"value": 2, "label": "In 2"},
        ]
        assert resolve_axis(config, "destinations") == [
            {"value": 1, "label": "Main LCD",
             "route_key": "device.mx.output.1.input"},
            {"value": 2, "label": "Confidence",
             "route_key": "device.mx.output.2.input"},
        ]

    def test_0_9_to_0_10_states_the_count_a_silent_matrix_was_drawing(self):
        """An axis that never said a count was drawing four, and still is.

        A migration preserves what the panel drew. Recording 4 is the honest
        reading of that; "did you mean 4?" is the review's question, not this
        step's.
        """
        data = {
            "openavc_version": "0.9.0",
            "project": {"id": "p", "name": "P"},
            "ui": {"pages": [{"id": "main", "name": "Main", "elements": [
                {"id": "mx", "type": "matrix", "matrix_config": {
                    "route_key_pattern": "device.mx.output.*.input"}},
                # ...but an element with no config at all keeps none, so it
                # still reads as unconfigured rather than as a 4x4 nobody wrote.
                {"id": "bare", "type": "matrix"},
            ]}]},
        }

        result = migrate_0_9_to_0_10(copy.deepcopy(data))
        elements = result["ui"]["pages"][0]["elements"]

        assert elements[0]["matrix_config"]["sources"]["from"]["count"] == 4
        assert elements[0]["matrix_config"]["destinations"]["from"]["count"] == 4
        assert "matrix_config" not in elements[1]

    def test_0_9_to_0_10_reaches_a_master_element(self):
        """A master is not in any page's element list, and can be a matrix."""
        data = {
            "openavc_version": "0.9.0",
            "project": {"id": "p", "name": "P"},
            "ui": {"pages": [], "master_elements": [{
                "id": "bar", "type": "matrix",
                "matrix_config": {"output_count": 2,
                                  "route_key_pattern": "device.mx.out.*"},
            }]},
        }

        result = migrate_0_9_to_0_10(copy.deepcopy(data))

        assert result["ui"]["master_elements"][0]["matrix_config"]["destinations"] == {
            "from": {"count": 2, "route_key": "device.mx.out.*"}
        }

    def test_0_8_to_0_9_stamps_the_version_and_changes_nothing_else(self):
        """0.9.0 adds the `custom` element type. Nothing that existed moves."""
        data = make_v05_project()
        data = migrate_0_5_to_0_6(data)
        data = migrate_0_6_to_0_7(data)
        data = migrate_0_7_to_0_8(data)
        before = copy.deepcopy(data)

        result = migrate_0_8_to_0_9(copy.deepcopy(data))

        assert result["openavc_version"] == "0.9.0"
        before.pop("openavc_version")
        after = copy.deepcopy(result)
        after.pop("openavc_version")
        assert after == before

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
# 0.10.0 -> 0.11.0 — the project says which readings matter
# ---------------------------------------------------------------------------

class TestMigrate010To011:
    def test_a_project_with_nothing_tagged_only_gains_a_version(self):
        """The whole story for almost every project: nothing moves."""
        data = {
            "openavc_version": "0.10.0",
            "project": {"id": "p", "name": "P"},
            "variables": [{"id": "mode", "type": "string", "label": "Room Mode"}],
        }
        result = migrate_0_10_to_0_11(copy.deepcopy(data))
        assert result["openavc_version"] == "0.11.0"
        assert result["variables"] == data["variables"]
        assert "monitors" not in result

    def test_dashboard_true_becomes_a_monitor_with_no_limits(self):
        data = {
            "openavc_version": "0.10.0",
            "project": {"id": "p", "name": "P"},
            "variables": [
                {"id": "occupied", "type": "boolean", "label": "Occupancy",
                 "dashboard": True},
                {"id": "quiet", "type": "string", "label": "Quiet", "dashboard": False},
            ],
        }
        result = migrate_0_10_to_0_11(copy.deepcopy(data))

        assert result["monitors"] == [
            {"key": "var.occupied", "label": "Occupancy", "type": "boolean"},
        ]
        # The flag goes with it. Two fields meaning one thing is how the
        # Dashboard and the cloud card end up disagreeing about what is on them.
        assert all("dashboard" not in v for v in result["variables"])

    def test_validation_does_not_become_a_limit(self):
        """What may be STORED is a different claim from what is HEALTHY. A
        migration that turned every validation rule into an alert would light up
        rooms whose owners never asked to be told anything."""
        data = {
            "openavc_version": "0.10.0",
            "project": {"id": "p", "name": "P"},
            "variables": [{
                "id": "level", "type": "number", "label": "Level", "dashboard": True,
                "validation": {"min": 0, "max": 100},
            }],
        }
        result = migrate_0_10_to_0_11(copy.deepcopy(data))
        monitor = result["monitors"][0]
        assert "normal_min" not in monitor
        assert "normal_max" not in monitor
        # ...and the variable keeps validating exactly as it did.
        assert result["variables"][0]["validation"] == {"min": 0, "max": 100}

    def test_a_hand_written_monitor_list_is_not_replaced(self):
        data = {
            "openavc_version": "0.10.0",
            "project": {"id": "p", "name": "P"},
            "monitors": [{"key": "var.occupied", "label": "Kept", "normal_min": 1}],
            "variables": [{"id": "occupied", "type": "boolean", "dashboard": True}],
        }
        result = migrate_0_10_to_0_11(copy.deepcopy(data))
        assert result["monitors"] == [
            {"key": "var.occupied", "label": "Kept", "normal_min": 1},
        ]

    def test_a_malformed_variable_entry_does_not_stop_the_migration(self):
        data = {
            "openavc_version": "0.10.0",
            "project": {"id": "p", "name": "P"},
            "variables": ["not a dict", {"dashboard": True}, {"id": "ok", "dashboard": True}],
        }
        result = migrate_0_10_to_0_11(copy.deepcopy(data))
        assert [m["key"] for m in result["monitors"]] == ["var.ok"]


# ---------------------------------------------------------------------------
# 0.14.0 -> 0.15.0 — a label on a control that draws none is dropped
# ---------------------------------------------------------------------------

def make_v014_project_with_stray_labels() -> dict:
    """What the UI Builder used to leave behind: a custom control and a plugin
    control carrying the default label it gave them, a clock carrying one typed
    into the field it once offered, and a text label carrying one from a
    hand-edited file. None of the four draws a label."""
    def plugin(el_id: str) -> dict:
        return {"id": el_id, "type": "plugin", "label": "status_tile",
                "plugin_id": "acme_tiles", "plugin_type": "status_tile", "plugin_config": {}}

    return {
        "openavc_version": "0.14.0",
        "project": {"id": "p", "name": "P"},
        "ui": {
            "pages": [{"id": "main", "name": "Main", "elements": [
                {"id": "room_map", "type": "custom", "label": "Custom Control",
                 "custom_file": "room_map/index.html", "custom_config": {}},
                plugin("tiles"),
                {"id": "wall_clock", "type": "clock", "label": "Lobby clock"},
                {"id": "title", "type": "label", "label": "Title", "text": "Welcome"},
                {"id": "power", "type": "button", "label": "Power"},
            ]}],
            "master_elements": [plugin("bar_tiles")],
        },
    }


def _not_rendered(data: dict) -> set[str]:
    """The controls the page review says carry a property they do not draw."""
    ui = ProjectConfig.model_validate(data).ui
    findings, _ = review_page(ui.pages[0], masters=ui.master_elements)
    for master in ui.master_elements:
        findings += review_master_element(master)
    return {f.element_id for f in findings if f.kind == "property_not_rendered"}


class TestMigrate014To015:
    def test_a_label_on_a_control_that_draws_none_is_dropped(self):
        result, migrated = migrate_project(make_v014_project_with_stray_labels())

        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION
        page = {el["id"]: el for el in result["ui"]["pages"][0]["elements"]}
        for el_id in ("room_map", "tiles", "wall_clock", "title"):
            assert "label" not in page[el_id], el_id
        assert "label" not in result["ui"]["master_elements"][0]
        # Nothing else moves, the label of a control that draws one included.
        assert page["power"]["label"] == "Power"
        assert page["title"]["text"] == "Welcome"
        assert page["room_map"]["custom_file"] == "room_map/index.html"
        assert page["tiles"]["plugin_type"] == "status_tile"

    def test_the_page_review_has_nothing_left_to_say_about_them(self):
        # Before: one warning per control, and no field in the Builder that
        # could clear it.
        assert _not_rendered(make_v014_project_with_stray_labels()) == {
            "room_map", "tiles", "wall_clock", "title", "bar_tiles",
        }
        result, _ = migrate_project(make_v014_project_with_stray_labels())
        assert _not_rendered(result) == set()

    def test_a_malformed_ui_does_not_stop_the_migration(self):
        data = {
            "openavc_version": "0.14.0",
            "project": {"id": "p", "name": "P"},
            "ui": {"pages": ["not a page", {"id": "x", "elements": ["not an element"]}],
                   "master_elements": None},
        }
        result = migrate_0_14_to_0_15(copy.deepcopy(data))
        assert result["openavc_version"] == "0.15.0"
        assert result["ui"]["pages"][1]["elements"] == ["not an element"]


# ---------------------------------------------------------------------------
# 0.15.0 -> 0.16.0 — a fader with no unit keeps the % it has always drawn
# ---------------------------------------------------------------------------

def make_v015_project_with_faders() -> dict:
    """A fader the AI wrote with no unit, one a save wrote back with a null
    unit, one whose Unit field somebody cleared, one in dB, a slider with no
    unit, and a master fader with no unit."""
    return {
        "openavc_version": "0.15.0",
        "project": {"id": "p", "name": "P"},
        "ui": {
            "pages": [{"id": "main", "name": "Main", "elements": [
                {"id": "volume", "type": "fader", "min": 0, "max": 100},
                {"id": "saved", "type": "fader", "unit": None},
                {"id": "cleared", "type": "fader", "unit": ""},
                {"id": "gain", "type": "fader", "unit": "dB", "min": -100, "max": 20},
                {"id": "level", "type": "slider"},
            ]}],
            "master_elements": [{"id": "bar_volume", "type": "fader"}],
        },
    }


class TestMigrate015To016:
    def test_a_fader_with_no_unit_is_given_the_percent_it_drew(self):
        result, migrated = migrate_project(make_v015_project_with_faders())

        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION
        page = {el["id"]: el for el in result["ui"]["pages"][0]["elements"]}
        assert page["volume"]["unit"] == "%"
        assert page["saved"]["unit"] == "%"
        assert result["ui"]["master_elements"][0]["unit"] == "%"
        # Cleared on purpose, or a real unit: left as written.
        assert page["cleared"]["unit"] == ""
        assert page["gain"]["unit"] == "dB"
        # A slider never drew a unit it was not given, so it is not touched.
        assert "unit" not in page["level"]

    def test_a_malformed_ui_does_not_stop_the_migration(self):
        data = {
            "openavc_version": "0.15.0",
            "project": {"id": "p", "name": "P"},
            "ui": {"pages": ["not a page", {"id": "x", "elements": ["not an element"]}],
                   "master_elements": None},
        }
        result = migrate_0_15_to_0_16(copy.deepcopy(data))
        assert result["openavc_version"] == "0.16.0"
        assert result["ui"]["pages"][1]["elements"] == ["not an element"]


# ---------------------------------------------------------------------------
# 0.16.0 -> 0.17.0 — a saved value the schema now bounds is brought inside it
# ---------------------------------------------------------------------------

def make_v016_project_out_of_bounds(**settings) -> dict:
    """Decimals past both ends on every kind of control and on a monitor, one
    in range, one that is not a whole number, and the panel settings given."""
    data = _v016_project_out_of_bounds(settings)
    data["monitors"].append("not a monitor")
    data["ui"]["pages"][0]["elements"].append(
        {"id": "word", "type": "label", "display_decimals": "two"},
    )
    return data


def make_loadable_v016_project_out_of_bounds(**settings) -> dict:
    """The same without the two entries the schema has always refused."""
    return _v016_project_out_of_bounds(settings)


def _v016_project_out_of_bounds(settings: dict) -> dict:
    return {
        "openavc_version": "0.16.0",
        "project": {"id": "p", "name": "P"},
        "monitors": [
            {"key": "var.a", "display_decimals": 40},
            {"key": "var.b", "display_decimals": -2},
            {"key": "var.c"},
        ],
        "ui": {
            "settings": settings,
            "pages": [{"id": "main", "name": "Main", "elements": [
                {"id": "slider", "type": "slider", "display_decimals": 150},
                {"id": "fader", "type": "fader", "unit": "dB", "display_decimals": 50},
                {"id": "label", "type": "label", "display_decimals": 21},
                {"id": "gauge", "type": "gauge", "display_decimals": -1},
                {"id": "button", "type": "button", "display_decimals": 30},
                {"id": "kept", "type": "label", "display_decimals": 3},
                {"id": "whole", "type": "label", "display_decimals": 4.0},
                {"id": "unset", "type": "label"},
                {"id": "nulled", "type": "label", "display_decimals": None},
            ]}],
            "master_elements": [{"id": "bar", "type": "label", "display_decimals": 99}],
        },
    }


class TestMigrate016To017:
    def test_decimals_are_brought_to_zero_through_twenty_wherever_they_are(self):
        result, migrated = migrate_project(make_v016_project_out_of_bounds())

        assert migrated is True
        assert result["openavc_version"] == CURRENT_VERSION
        page = {el["id"]: el for el in result["ui"]["pages"][0]["elements"]}
        assert page["slider"]["display_decimals"] == 20
        assert page["fader"]["display_decimals"] == 20
        assert page["label"]["display_decimals"] == 20
        assert page["gauge"]["display_decimals"] == 0
        # The bound is on the field, so a control that draws no readout is
        # brought inside it too, or the project would not load.
        assert page["button"]["display_decimals"] == 20
        assert result["ui"]["master_elements"][0]["display_decimals"] == 20
        assert [m.get("display_decimals") for m in result["monitors"][:3]] == [20, 0, None]

    def test_a_value_in_range_or_unset_is_left_as_it_was(self):
        result = migrate_0_16_to_0_17(make_v016_project_out_of_bounds())
        page = {el["id"]: el for el in result["ui"]["pages"][0]["elements"]}
        assert page["kept"]["display_decimals"] == 3
        assert page["whole"]["display_decimals"] == 4
        assert "display_decimals" not in page["unset"]
        assert page["nulled"]["display_decimals"] is None
        # Not a whole number: the schema refused it before and still does.
        assert page["word"]["display_decimals"] == "two"
        assert result["monitors"][3] == "not a monitor"

    def test_the_panel_settings_are_brought_inside_the_fields_ranges(self):
        result = migrate_0_16_to_0_17(make_v016_project_out_of_bounds(
            page_transition="fade", page_transition_duration=99999,
            element_entry="stagger", element_stagger_ms=-5,
            idle_timeout_seconds=-5,
        ))
        settings = result["ui"]["settings"]
        assert settings["page_transition_duration"] == 1000
        assert settings["element_stagger_ms"] == 10
        assert settings["idle_timeout_seconds"] == 0

    def test_an_idle_timeout_the_panel_timer_cannot_hold_waits_a_day(self):
        result = migrate_0_16_to_0_17(make_v016_project_out_of_bounds(
            idle_timeout_seconds=3_000_000,
        ))
        assert result["ui"]["settings"]["idle_timeout_seconds"] == 86400

    def test_each_setting_low_end_and_in_range(self):
        result = migrate_0_16_to_0_17(make_v016_project_out_of_bounds(
            page_transition_duration=20, element_stagger_ms=500,
            idle_timeout_seconds=600,
        ))
        settings = result["ui"]["settings"]
        assert settings["page_transition_duration"] == 50
        assert settings["element_stagger_ms"] == 200
        assert settings["idle_timeout_seconds"] == 600

    def test_a_zero_the_panel_drew_as_the_default_becomes_the_default(self):
        result = migrate_0_16_to_0_17(make_loadable_v016_project_out_of_bounds(
            page_transition_duration=0, element_stagger_ms=0,
        ))
        settings = result["ui"]["settings"]
        assert "page_transition_duration" not in settings
        assert "element_stagger_ms" not in settings
        loaded = ProjectConfig(**result).ui.settings
        assert (loaded.page_transition_duration, loaded.element_stagger_ms) == (200, 30)

    def test_the_migrated_project_loads(self):
        result, _ = migrate_project(make_loadable_v016_project_out_of_bounds(
            page_transition_duration=99999, element_stagger_ms=-5,
            idle_timeout_seconds=-5,
        ))
        ProjectConfig(**result)

    def test_a_malformed_project_does_not_stop_the_migration(self):
        for data in (
            {"openavc_version": "0.16.0", "project": {"id": "p", "name": "P"}},
            {"openavc_version": "0.16.0", "ui": "not a ui", "monitors": None},
            {"openavc_version": "0.16.0", "ui": {"settings": None, "pages": None}},
            {"openavc_version": "0.16.0", "ui": {"pages": [
                "not a page",
                {"id": "a", "layouts": None},
                {"id": "b", "layouts": ["not a layout", {"id": "l", "placements": None}]},
                {"id": "c", "layouts": [{"id": "l", "placements": {"e": "not a box"}}]},
                {"id": "d", "layouts": [{"id": "l", "placements": ["not", "a", "map"]}]},
            ], "master_elements": [
                "not a master", {"id": "m", "placements": None},
                {"id": "n", "placements": {"landscape": None}},
            ]}},
        ):
            result = migrate_0_16_to_0_17(copy.deepcopy(data))
            assert result["openavc_version"] == "0.17.0"


#: Every way a 0.16.0 project could hold a side the schema read as no finite
#: number: the tokens the file writer put down for one (Python's json reads
#: `NaN`, `Infinity` and `1e999` as these), and text the schema read as one.
NON_FINITE_SIDES = [
    float("nan"), float("inf"), float("-inf"),
    "NaN", "nan", " nan ", "inf", "-inf", "+inf", "Infinity", "-Infinity", "infinity", "1e999",
]


def make_v016_project_with_boxes(side) -> dict:
    """A page with two arrangements and a master, every box holding ``side``
    on one coordinate, beside a box with a negative position and size."""
    return {
        "openavc_version": "0.16.0",
        "project": {"id": "p", "name": "P"},
        "ui": {
            "pages": [{
                "id": "main", "name": "Main",
                "elements": [
                    {"id": "a", "type": "button"}, {"id": "b", "type": "button"},
                    {"id": "c", "type": "button"},
                ],
                "layouts": [
                    {"id": "landscape", "orientation": "landscape", "primary": True,
                     "placements": {
                         "a": {"x": side, "y": 10, "w": 20, "h": 30},
                         "b": {"x": 5, "y": 6, "w": side, "h": side},
                         "c": {"x": -10, "y": -5, "w": -20, "h": -1},
                     }},
                    {"id": "portrait", "orientation": "portrait", "inherits": "landscape",
                     "placements": {"a": {"x": 1, "y": side, "w": 2, "h": 3}}},
                ],
            }],
            "master_elements": [{
                "id": "bar", "type": "label", "pages": "*",
                "placements": {"landscape": {"x": 0, "y": 0, "w": 100, "h": side}},
            }],
        },
    }


class TestMigrate016To017Boxes:
    @staticmethod
    def _boxes(result: dict) -> tuple[dict, dict, dict]:
        page = result["ui"]["pages"][0]
        primary, variant = page["layouts"]
        return primary["placements"], variant["placements"], result["ui"]["master_elements"][0]["placements"]

    def test_a_side_that_is_no_finite_number_loads_as_the_default_the_panel_draws(self):
        """The panel draws a coordinate that is not a finite number as the
        whole box's (x and y 0, w and h 100); that is what loads."""
        for side in NON_FINITE_SIDES:
            result, migrated = migrate_project(make_v016_project_with_boxes(side))
            assert migrated is True
            loaded = ProjectConfig(**result)
            page = loaded.ui.pages[0]
            primary, variant = (lay.placements for lay in page.layouts)
            assert primary["a"].model_dump() == {"x": 0, "y": 10, "w": 20, "h": 30}, side
            assert primary["b"].model_dump() == {"x": 5, "y": 6, "w": 100, "h": 100}, side
            assert variant["a"].model_dump() == {"x": 1, "y": 0, "w": 2, "h": 3}, side
            master = loaded.ui.master_elements[0].placements["landscape"]
            assert master.model_dump() == {"x": 0, "y": 0, "w": 100, "h": 100}, side

    def test_the_repaired_project_is_one_a_browser_can_read(self):
        result, _ = migrate_project(make_v016_project_with_boxes(float("nan")))
        dumped = ProjectConfig(**result).model_dump(mode="json")
        json.dumps(dumped, allow_nan=False)

    def test_the_side_is_removed_rather_than_rewritten(self):
        result = migrate_0_16_to_0_17(make_v016_project_with_boxes(float("nan")))
        primary, variant, master = self._boxes(result)
        assert primary["a"] == {"y": 10, "w": 20, "h": 30}
        assert primary["b"] == {"x": 5, "y": 6}
        assert variant["a"] == {"x": 1, "w": 2, "h": 3}
        assert master["landscape"] == {"x": 0, "y": 0, "w": 100}

    def test_a_negative_position_or_size_is_left_as_written(self):
        """A container's child may sit past its edge on purpose, and a
        negative size is Validate's to report, not the load's to change."""
        result = migrate_0_16_to_0_17(make_v016_project_with_boxes(float("nan")))
        primary, _, _ = self._boxes(result)
        assert primary["c"] == {"x": -10, "y": -5, "w": -20, "h": -1}

    def test_a_finite_side_in_any_form_the_schema_took_is_left_as_written(self):
        for side in (12.5, 0, -3, "40.5", True, "1_000"):
            result = migrate_0_16_to_0_17(make_v016_project_with_boxes(side))
            primary, _, _ = self._boxes(result)
            assert primary["a"]["x"] == side

    def test_an_overlay_box_is_brought_inside_five_to_a_hundred(self):
        boxes = {
            "zero": {"width": 0, "height": 0},
            "negative": {"width": -10, "height": -1},
            "huge": {"width": 200, "height": 100.5},
            "kept": {"width": 31.25, "height": 37.5},
            "text": {"width": "200", "height": "3"},
            "unset": {"position": "center"},
            "nulled": {"width": None, "height": None},
            "words": {"width": "wide"},
            "non_finite": {"width": float("nan"), "height": float("inf")},
        }
        data = make_v016_project_with_boxes(10)
        data["ui"]["pages"] += [
            {"id": name, "name": name, "page_type": "overlay", "overlay": overlay}
            for name, overlay in boxes.items()
        ] + [
            {"id": "side", "name": "Side", "page_type": "sidebar", "overlay": {"width": 3, "height": 0}},
            {"id": "plain", "name": "Plain", "overlay": None},
            {"id": "odd", "name": "Odd", "overlay": "not an overlay"},
        ]
        result = migrate_0_16_to_0_17(data)
        got = {page["id"]: page.get("overlay") for page in result["ui"]["pages"]}
        assert got["zero"] == {"width": 5, "height": 5}
        assert got["negative"] == {"width": 5, "height": 5}
        assert got["huge"] == {"width": 100, "height": 100}
        assert got["kept"] == {"width": 31.25, "height": 37.5}
        assert got["text"] == {"width": 100, "height": 5}
        assert got["unset"] == {"position": "center"}
        assert got["nulled"] == {"width": None, "height": None}
        # The panel drew a size that is no finite number as its default.
        assert got["non_finite"] == {}
        assert got["side"] == {"width": 5, "height": 5}
        assert got["plain"] is None and got["odd"] == "not an overlay"
        # Words: the schema refused them before and still does.
        assert got["words"] == {"width": "wide"}

    def test_a_project_with_an_overlay_box_out_of_range_loads(self):
        data = make_v016_project_with_boxes(10)
        data["ui"]["pages"].append({
            "id": "dialog", "name": "Dialog", "page_type": "overlay",
            "overlay": {"width": 0, "height": float("nan")},
        })
        result, _ = migrate_project(data)
        overlay = ProjectConfig(**result).ui.pages[1].overlay
        assert (overlay.width, overlay.height) == (5, None)

    def test_a_side_the_schema_refused_before_is_left_for_the_load_to_refuse(self):
        for side in ("wide", None, [1]):
            result = migrate_0_16_to_0_17(make_v016_project_with_boxes(side))
            primary, _, _ = self._boxes(result)
            assert primary["a"]["x"] == side


TRIGGER_SECONDS = ("delay_seconds", "debounce_seconds", "cooldown_seconds")


def make_v016_project_with_trigger_times(value) -> dict:
    """A macro with one trigger per time in seconds holding ``value``, beside
    a trigger holding times inside the bound."""
    return {
        "openavc_version": "0.16.0",
        "project": {"id": "p", "name": "P"},
        "macros": [{
            "id": "m", "name": "M",
            "triggers": [
                {"id": f"t_{field}", "type": "startup", field: value}
                for field in TRIGGER_SECONDS
            ] + [
                {"id": "t_kept", "type": "startup", "delay_seconds": 2.5,
                 "debounce_seconds": 0, "cooldown_seconds": 60},
            ],
        }],
    }


class TestMigrate016To017TriggerSeconds:
    @staticmethod
    def _triggers(result: dict) -> dict:
        return {t["id"]: t for t in result["macros"][0]["triggers"]}

    def test_a_negative_time_is_brought_to_zero(self):
        """The trigger engine waited no time for one, as for 0."""
        for value in (-5, -0.5, "-5", " -3 ", False - 1):
            result, migrated = migrate_project(make_v016_project_with_trigger_times(value))
            assert migrated is True
            triggers = self._triggers(result)
            for field in TRIGGER_SECONDS:
                assert triggers[f"t_{field}"][field] == 0, (field, value)
                assert triggers[f"t_{field}"].get("enabled", True) is True
            loaded = ProjectConfig(**result).macros[0].triggers
            assert [getattr(t, f) for t, f in zip(loaded, TRIGGER_SECONDS)] == [0, 0, 0]

    def test_a_time_inside_the_bound_is_left_as_written(self):
        for value in (0, 3, 2.5, "4", True):
            result = migrate_0_16_to_0_17(make_v016_project_with_trigger_times(value))
            triggers = self._triggers(result)
            for field in TRIGGER_SECONDS:
                assert triggers[f"t_{field}"][field] == value
        assert triggers["t_kept"] == {
            "id": "t_kept", "type": "startup", "delay_seconds": 2.5,
            "debounce_seconds": 0, "cooldown_seconds": 60,
        }

    def test_a_nan_or_negative_infinity_is_removed_and_loads_as_zero(self):
        """The engine read either as 0: neither is more than 0."""
        for value in (float("nan"), float("-inf"), "NaN", "-Infinity", "-1e999"):
            result, _ = migrate_project(make_v016_project_with_trigger_times(value))
            triggers = self._triggers(result)
            for field in TRIGGER_SECONDS:
                assert field not in triggers[f"t_{field}"], (field, value)
                assert triggers[f"t_{field}"].get("enabled", True) is True
            loaded = ProjectConfig(**result).macros[0].triggers
            assert [getattr(t, f) for t, f in zip(loaded, TRIGGER_SECONDS)] == [0, 0, 0]

    def test_an_infinite_time_turns_its_trigger_off(self):
        """An infinite delay or debounce never ended and an infinite cooldown
        never lapsed after the first run: the trigger was held back for good,
        and turned off it still is."""
        for value in (float("inf"), "Infinity", "1e999", " inf "):
            result, _ = migrate_project(make_v016_project_with_trigger_times(value))
            triggers = self._triggers(result)
            for field in TRIGGER_SECONDS:
                assert field not in triggers[f"t_{field}"], (field, value)
                assert triggers[f"t_{field}"]["enabled"] is False, (field, value)
            assert "enabled" not in triggers["t_kept"]
            loaded = ProjectConfig(**result).macros[0].triggers
            assert [t.enabled for t in loaded] == [False, False, False, True]

    def test_the_repaired_project_is_one_a_browser_can_read(self):
        result, _ = migrate_project(make_v016_project_with_trigger_times(float("inf")))
        json.dumps(ProjectConfig(**result).model_dump(mode="json"), allow_nan=False)

    def test_a_time_the_schema_refused_before_is_left_for_the_load_to_refuse(self):
        for value in ("soon", None, [1]):
            result = migrate_0_16_to_0_17(make_v016_project_with_trigger_times(value))
            triggers = self._triggers(result)
            for field in TRIGGER_SECONDS:
                assert triggers[f"t_{field}"][field] == value

    def test_a_malformed_macro_list_does_not_stop_the_migration(self):
        for macros in (
            None, "not a list", 5, {"id": "m"},
            ["not a macro", {"id": "m", "triggers": None}],
            [{"id": "m", "triggers": "not a list"}],
            [{"id": "m", "triggers": 7}],
            [{"id": "m", "triggers": ["not a trigger", None, {"delay_seconds": -1}]}],
        ):
            data = {"openavc_version": "0.16.0", "macros": copy.deepcopy(macros)}
            result = migrate_0_16_to_0_17(data)
            assert result["openavc_version"] == "0.17.0"
        assert result["macros"][0]["triggers"][2] == {"delay_seconds": 0}


def make_v016_project_with_pin(pin) -> dict:
    return {
        "openavc_version": "0.16.0",
        "project": {"id": "p", "name": "P"},
        "ui": {"settings": {"lock_code": pin}, "pages": []},
    }


class TestMigrate016To017PanelPin:
    """A PIN the lock screen could not take becomes what that screen holds
    when somebody types it: its digits 0 to 9, in order, the first six."""

    @pytest.mark.parametrize("pin, kept", [
        ("1234567", "123456"),
        ("12345678901", "123456"),
        ("12 34", "1234"),
        (" 1234 ", "1234"),
        ("1234\n", "1234"),
        ("12a4", "124"),
        ("é12", "12"),
        ("1-2-3-4-5-6-7", "123456"),
    ])
    def test_a_pin_is_brought_to_what_the_lock_screen_holds(self, pin, kept):
        result = migrate_0_16_to_0_17(make_v016_project_with_pin(pin))
        assert result["ui"]["settings"]["lock_code"] == kept
        assert ProjectConfig(**result).ui.settings.lock_code == kept

    @pytest.mark.parametrize("pin", ["pässwörd", "abcd", " ", "١٢٣٤"])
    def test_a_pin_with_no_digit_the_screen_types_is_no_lock(self, pin):
        result = migrate_0_16_to_0_17(make_v016_project_with_pin(pin))
        assert result["ui"]["settings"]["lock_code"] == ""

    @pytest.mark.parametrize("pin", ["", "0", "0123", "123456"])
    def test_a_pin_the_screen_takes_is_left_as_it_was(self, pin):
        result = migrate_0_16_to_0_17(make_v016_project_with_pin(pin))
        assert result["ui"]["settings"]["lock_code"] == pin

    @pytest.mark.parametrize("pin", [1234, None, ["1234"]])
    def test_a_pin_the_schema_refused_before_is_left_for_the_load_to_refuse(self, pin):
        result = migrate_0_16_to_0_17(make_v016_project_with_pin(pin))
        assert result["ui"]["settings"]["lock_code"] == pin

    def test_a_project_with_no_pin_gets_none(self):
        result = migrate_0_16_to_0_17(make_v016_project_with_pin(""))
        del result["ui"]["settings"]["lock_code"]
        again = migrate_0_16_to_0_17({**result, "openavc_version": "0.16.0"})
        assert "lock_code" not in again["ui"]["settings"]


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
