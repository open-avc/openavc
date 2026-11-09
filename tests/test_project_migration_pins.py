"""What each project migration step does today, on the paths the step tests
leave out.

Characterisation pins: they record the current output, not a claim that it is
right. Every pin goes through a step's public function (or a helper an older
test already imports), so a step can be reorganised inside without touching
them. Where a pin records something that looks odd, it says so.

Key order is pinned as well as values: a migrated project is written back to
disk, so the order the step leaves its keys in is what the saved file shows.
"""

import copy
import json
import logging

import pytest

from openavc.core.project_migration import (
    CURRENT_VERSION,
    MIGRATIONS,
    _migrate_bindings_0_6_to_0_7,
    _migrate_matrix_config_0_9_to_0_10,
    migrate_0_1_to_0_2,
    migrate_0_3_to_0_4,
    migrate_0_6_to_0_7,
    migrate_0_7_to_0_8,
    migrate_0_9_to_0_10,
    migrate_0_10_to_0_11,
    migrate_0_14_to_0_15,
    migrate_0_15_to_0_16,
    migrate_project,
)

MIGRATION_LOGGER = "openavc.core.project_migration"


def _ordered(value):
    """A dict's items in order, all the way down, so == also compares order."""
    if isinstance(value, dict):
        return [(k, _ordered(v)) for k, v in value.items()]
    if isinstance(value, list):
        return [_ordered(v) for v in value]
    return value


def _messages(caplog):
    return [
        (r.name, r.levelno, r.getMessage())
        for r in caplog.records
        if r.name == MIGRATION_LOGGER
    ]


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------

class TestTheChain:
    def test_current_version_is_where_the_chain_ends(self):
        assert CURRENT_VERSION == MIGRATIONS[-1][1]

    def test_each_step_starts_where_the_last_one_ended(self):
        assert MIGRATIONS[0][0] == "0.1.0"
        for (_, target, _), (source, _, _) in zip(MIGRATIONS, MIGRATIONS[1:]):
            assert source == target

    def test_each_step_stamps_its_own_target_when_called_directly(self):
        for source, target, step in MIGRATIONS:
            data = {"openavc_version": source, "project": {"id": "p", "name": "P"}}
            assert step(data)["openavc_version"] == target, step.__name__

    def test_the_chain_returns_the_dict_it_was_given(self):
        data = {"openavc_version": "0.15.0", "project": {"id": "p", "name": "P"}}
        out, migrated = migrate_project(data)
        assert out is data
        assert migrated is True

    def test_a_current_project_is_left_alone_and_logs_nothing(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        data = {"openavc_version": CURRENT_VERSION, "project": {"id": "p", "name": "P"}}
        before = copy.deepcopy(data)
        out, migrated = migrate_project(data)
        assert migrated is False
        assert out == before
        assert _messages(caplog) == []

    def test_every_step_run_is_logged_in_order(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        migrate_project({"openavc_version": "0.13.0", "project": {"id": "p", "name": "P"}})
        assert [m for m in _messages(caplog) if m[2].startswith("Migrating")] == [
            (MIGRATION_LOGGER, logging.INFO, f"Migrating project from {s} to {t}")
            for s, t, _ in MIGRATIONS[12:]
        ]

    def test_an_unknown_version_is_not_migrated_and_warns(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        data = {"openavc_version": "9.9.9", "project": {"id": "p", "name": "P"}}
        out, migrated = migrate_project(data)
        assert migrated is False
        assert out["openavc_version"] == "9.9.9"
        assert _messages(caplog) == [(
            MIGRATION_LOGGER, logging.WARNING,
            f"Project version 9.9.9 does not match current platform version "
            f"{CURRENT_VERSION} — some features may not work correctly",
        )]

    def test_a_project_with_no_version_runs_the_whole_chain(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        out, migrated = migrate_project({"project": {"id": "p", "name": "P"}})
        assert migrated is True
        assert out["openavc_version"] == CURRENT_VERSION
        steps = [m for m in _messages(caplog) if m[2].startswith("Migrating")]
        assert len(steps) == len(MIGRATIONS)

    def test_a_version_less_project_gets_its_keys_in_the_order_the_steps_add_them(self):
        out, _ = migrate_project({"project": {"id": "p", "name": "P"}})
        assert list(out) == [
            "project", "connections", "driver_dependencies", "openavc_version",
            "plugins", "plugin_dependencies", "device_groups",
        ]


# ---------------------------------------------------------------------------
# 0.1.0 -> 0.2.0
# ---------------------------------------------------------------------------

class TestSerialRenames:
    def test_when_both_spellings_are_present_the_new_one_wins(self):
        data = {"devices": [{"id": "d", "config": {
            "com_port": "COM1", "port": "COM3",
            "baud_rate": 9600, "baudrate": 115200, "mode": "x",
        }}]}
        out = migrate_0_1_to_0_2(data)
        assert out["connections"] == {"d": {"port": "COM3", "baudrate": 115200}}
        assert out["devices"][0]["config"] == {"mode": "x"}

    def test_a_legacy_name_alone_is_renamed_and_moved(self):
        data = {"devices": [{"id": "d", "config": {"baud_rate": 9600, "com_port": "COM1"}}]}
        out = migrate_0_1_to_0_2(data)
        # The renamed fields go to the end of the config before the move, so the
        # connection entry lists them in rename order, not the file's order.
        assert _ordered(out["connections"]["d"]) == [("port", "COM1"), ("baudrate", 9600)]

    def test_a_device_with_no_connection_fields_gets_no_entry(self):
        out = migrate_0_1_to_0_2({"devices": [{"id": "d", "config": {"mode": "x"}}]})
        assert out["connections"] == {}

    def test_an_existing_connections_table_is_replaced(self):
        out = migrate_0_1_to_0_2({"connections": {"old": {"host": "h"}}, "devices": []})
        assert out["connections"] == {}

    def test_a_device_with_no_config_is_left_alone(self):
        out = migrate_0_1_to_0_2({"devices": [{"id": "d"}]})
        assert out["devices"] == [{"id": "d"}]
        assert out["connections"] == {}

    @pytest.mark.parametrize("config", [["volume"], "abc", ["host"]])
    def test_a_config_that_is_not_a_dict_stops_the_step(self, config):
        with pytest.raises(AttributeError, match="keys"):
            migrate_0_1_to_0_2({"devices": [{"id": "d", "config": config}]})

    def test_driver_dependencies_already_present_are_kept(self):
        out = migrate_0_1_to_0_2({"driver_dependencies": ["x"]})
        assert out["driver_dependencies"] == ["x"]


# ---------------------------------------------------------------------------
# 0.3.0 -> 0.4.0
# ---------------------------------------------------------------------------

class TestDeviceGroups:
    def test_a_device_with_no_id_is_not_listed_but_its_group_is_made(self):
        data = {"devices": [{"group": "Front"}, {"id": "", "group": "Front"}]}
        out = migrate_0_3_to_0_4(data)
        assert out["device_groups"] == [{"id": "front", "name": "Front", "device_ids": []}]
        assert out["devices"] == [{}, {"id": ""}]

    def test_a_device_already_in_the_group_is_not_listed_twice(self):
        data = {
            "devices": [{"id": "a", "group": "Front"}, {"id": "a", "group": "Front"}],
            "device_groups": [{"id": "front", "name": "Front Row", "device_ids": ["a"]}],
        }
        out = migrate_0_3_to_0_4(data)
        assert out["device_groups"] == [
            {"id": "front", "name": "Front Row", "device_ids": ["a"]},
        ]

    def test_an_existing_group_keeps_its_own_members_first(self):
        data = {
            "devices": [{"id": "b", "group": "Front"}],
            "device_groups": [{"id": "front", "name": "F", "device_ids": ["a"]}],
        }
        out = migrate_0_3_to_0_4(data)
        assert out["device_groups"] == [{"id": "front", "name": "F", "device_ids": ["a", "b"]}]

    def test_an_existing_group_with_no_member_list_gets_one(self):
        data = {
            "devices": [{"id": "a", "group": "Front"}],
            "device_groups": [{"id": "front", "name": "F"}],
        }
        out = migrate_0_3_to_0_4(data)
        assert _ordered(out["device_groups"]) == [
            [("id", "front"), ("name", "F"), ("device_ids", ["a"])],
        ]

    def test_a_null_group_list_and_a_falsy_group_name(self):
        data = {"devices": [{"id": "a", "group": ""}, {"id": "b", "group": None}],
                "device_groups": None}
        out = migrate_0_3_to_0_4(data)
        assert out["device_groups"] == []
        assert out["devices"] == [{"id": "a"}, {"id": "b"}]

    def test_groups_are_made_in_the_order_devices_first_name_them(self):
        data = {"devices": [
            {"id": "a", "group": "B side"}, {"id": "b", "group": "A side"},
            {"id": "c", "group": "B side"},
        ]}
        out = migrate_0_3_to_0_4(data)
        assert out["device_groups"] == [
            {"id": "b_side", "name": "B side", "device_ids": ["a", "c"]},
            {"id": "a_side", "name": "A side", "device_ids": ["b"]},
        ]
        assert [list(g) for g in out["device_groups"]] == [["id", "name", "device_ids"]] * 2


# ---------------------------------------------------------------------------
# 0.6.0 -> 0.7.0
# ---------------------------------------------------------------------------

def _v06(elements=None, masters=None, pages=None):
    ui = {"pages": pages if pages is not None else [{"id": "p", "elements": elements or []}]}
    if masters is not None:
        ui["master_elements"] = masters
    return {"openavc_version": "0.6.0", "project": {"id": "p", "name": "P"}, "ui": ui}


class TestBindingsRewrite:
    def test_bindings_that_are_not_a_dict_come_back_as_they_were(self):
        assert _migrate_bindings_0_6_to_0_7("x") == "x"
        assert _migrate_bindings_0_6_to_0_7(None) is None

    def test_an_element_whose_bindings_are_not_a_dict_is_untouched(self):
        el = {"id": "b", "bindings": ["press"]}
        out = migrate_0_6_to_0_7(_v06([el]))
        assert out["ui"]["pages"][0]["elements"][0] == {"id": "b", "bindings": ["press"]}

    def test_an_element_with_no_bindings_gets_none(self):
        out = migrate_0_6_to_0_7(_v06([{"id": "b"}]))
        assert out["ui"]["pages"][0]["elements"][0] == {"id": "b"}

    def test_an_action_slot_that_is_neither_list_nor_object_is_dropped(self):
        assert _migrate_bindings_0_6_to_0_7({"press": "macro", "release": 5, "hold": {}}) == {}

    def test_a_list_keeps_only_its_objects(self):
        out = _migrate_bindings_0_6_to_0_7({"press": [{"action": "a"}, "x", 3, {"action": "b"}]})
        assert out == {"do": {"press": [{"action": "a"}, {"action": "b"}]}}

    def test_the_show_sources_override_in_file_order(self):
        both = {"value": {"key": "v"}, "text": {"key": "t"}}
        assert _migrate_bindings_0_6_to_0_7(both)["show"]["value"] == {"key": "t"}
        selected = {"variable": {"key": "var.a"}, "selected": {"key": "var.s"}}
        assert _migrate_bindings_0_6_to_0_7(selected)["show"]["value"] == {
            "source": "state", "key": "var.s", "write_back": True,
        }
        look = {"feedback": {"a": 1}, "color": {"b": 2}}
        assert _migrate_bindings_0_6_to_0_7(look)["show"]["look"] == {"b": 2}

    def test_a_variable_without_a_string_key(self):
        out = _migrate_bindings_0_6_to_0_7({"variable": {"key": 7}})
        assert out == {"show": {"value": {"source": "state", "key": 7}}}
        out = _migrate_bindings_0_6_to_0_7({"variable": {}})
        assert out == {"show": {"value": {"source": "state", "key": ""}}}
        out = _migrate_bindings_0_6_to_0_7({"selected": {}})
        assert out == {"show": {"value": {"source": "state", "key": "", "write_back": True}}}

    def test_a_source_that_is_not_an_object_is_ignored(self):
        out = _migrate_bindings_0_6_to_0_7({
            "value": "x", "text": 1, "variable": "var.a", "selected": [],
            "items": "i", "feedback": "f", "color": None,
        })
        assert out == {}

    def test_visible_when_is_carried_unless_it_is_none(self):
        assert _migrate_bindings_0_6_to_0_7({"visible_when": False}) == {
            "show": {"visible_when": False},
        }
        assert _migrate_bindings_0_6_to_0_7({"visible_when": None}) == {}

    def test_show_keys_and_do_slots_are_written_in_one_order(self):
        out = _migrate_bindings_0_6_to_0_7({
            "visible_when": {"key": "k"},
            "color": {"c": 1},
            "items": {"key": "i"},
            "selected": {"key": "var.s"},
            "audio_mute_route": {"action": "m"},
            "press": [{"action": "p"}],
            "change": {"action": "c"},
        })
        assert [k for k, _ in _ordered(out)] == ["show", "do"]
        assert list(out["show"]) == ["value", "items", "look", "visible_when"]
        assert list(out["do"]) == ["press", "change", "audio_mute_route"]

    def test_presets_land_in_a_matrix_config_that_was_not_a_dict(self):
        el = {"id": "m", "type": "matrix", "matrix_config": "junk",
              "bindings": {"presets": [{"name": "A", "macro": "a"}]}}
        out = migrate_0_6_to_0_7(_v06([el]))
        got = out["ui"]["pages"][0]["elements"][0]
        assert got["matrix_config"] == {"presets": [{"name": "A", "macro": "a"}]}
        assert got["bindings"] == {}

    def test_presets_that_are_not_a_list_are_dropped(self):
        el = {"id": "m", "bindings": {"presets": {"name": "A"}}}
        out = migrate_0_6_to_0_7(_v06([el]))
        assert out["ui"]["pages"][0]["elements"][0] == {"id": "m", "bindings": {}}

    def test_a_page_element_or_master_that_is_not_a_dict_is_skipped(self):
        data = _v06(pages=["junk", {"id": "p", "elements": ["x", {"id": "b",
                    "bindings": {"press": {"action": "a"}}}]}],
                    masters=[3, {"id": "m", "bindings": {"press": {"action": "b"}}}])
        out = migrate_0_6_to_0_7(data)
        assert out["ui"]["pages"][0] == "junk"
        assert out["ui"]["pages"][1]["elements"][0] == "x"
        assert out["ui"]["pages"][1]["elements"][1]["bindings"] == {
            "do": {"press": [{"action": "a"}]},
        }
        assert out["ui"]["master_elements"] == [3, {"id": "m", "bindings": {
            "do": {"press": [{"action": "b"}]},
        }}]

    def test_a_ui_that_is_not_a_dict_only_gets_the_stamp(self):
        out = migrate_0_6_to_0_7({"openavc_version": "0.6.0", "ui": "junk"})
        assert out == {"openavc_version": "0.7.0", "ui": "junk"}

    @pytest.mark.parametrize("ui", [
        {"pages": None},
        {"pages": [{"id": "p", "elements": None}]},
        {"pages": [], "master_elements": None},
    ])
    def test_a_null_list_stops_the_step(self, ui):
        # This step reads the lists as written. The later steps that walk every
        # element (0.10.0, 0.15.0, 0.16.0) read a null list as empty instead.
        with pytest.raises(TypeError):
            migrate_0_6_to_0_7({"openavc_version": "0.6.0", "ui": ui})

    def test_a_page_is_rewritten_before_a_later_one_fails(self):
        first = {"id": "a", "bindings": {"press": {"action": "x"}}}
        data = {"openavc_version": "0.6.0", "ui": {"pages": [
            {"id": "p1", "elements": [first]}, {"id": "p2", "elements": None},
        ]}}
        with pytest.raises(TypeError):
            migrate_0_6_to_0_7(data)
        assert data["ui"]["pages"][0]["elements"][0]["bindings"] == {
            "do": {"press": [{"action": "x"}]},
        }


# ---------------------------------------------------------------------------
# 0.7.0 -> 0.8.0
# ---------------------------------------------------------------------------

def _v07(pages, masters=None, settings=None):
    ui = {"pages": pages}
    if settings is not None:
        ui["settings"] = settings
    if masters is not None:
        ui["master_elements"] = masters
    return {"openavc_version": "0.7.0", "project": {"id": "p", "name": "P"}, "ui": ui}


def _cell(el_id, col=1, row=1, col_span=1, row_span=1, el_type="button", **extra):
    return {"id": el_id, "type": el_type,
            "grid_area": {"col": col, "row": row, "col_span": col_span, "row_span": row_span},
            **extra}


def _layout(out, index=0):
    return out["ui"]["pages"][index]["layouts"][0]


class TestLayoutConversion:
    def test_a_whole_page_in_order(self):
        page = {
            "id": "main", "name": "Main", "page_type": "page",
            "grid": {"columns": 4, "rows": 2}, "grid_gap": 10,
            "elements": [_cell("a", 2, 1, style={"font_size": 28, "bg_color": "#000"})],
            "after": True,
        }
        out = migrate_0_7_to_0_8(_v07([page], settings={"orientation": "portrait",
                                                        "theme_overrides": {"grid_gap": 6}}))
        assert _ordered(out["ui"]["pages"][0]) == _ordered({
            "id": "main", "name": "Main", "page_type": "page",
            "elements": [{"id": "a", "type": "button",
                          "style": {"font_size": 2.0, "bg_color": "#000"}, "parent": None}],
            "after": True,
            "snap": {"enabled": True, "x": 8.3333, "y": 12.5},
            "layouts": [{
                "id": "portrait", "orientation": "portrait", "primary": True,
                "inherits": None,
                "placements": {"a": {"x": 25.4297, "y": 0.75, "w": 24.1797, "h": 48.625}},
                "hidden": [],
            }],
        })
        assert out["ui"]["settings"] == {"theme_overrides": {"grid_gap": 6}}

    def test_a_page_with_no_elements_key_gets_one_before_snap(self):
        out = migrate_0_7_to_0_8(_v07([{"id": "p"}]))
        assert list(out["ui"]["pages"][0]) == ["id", "elements", "snap", "layouts"]

    @pytest.mark.parametrize("given,expected", [
        (None, "landscape"), ("", "landscape"), ("sideways", "landscape"),
        ("portrait", "portrait"), ("landscape", "landscape"),
    ])
    def test_the_orientation(self, given, expected):
        settings = {} if given is None else {"orientation": given}
        out = migrate_0_7_to_0_8(_v07([{"id": "p", "elements": []}], settings=settings))
        assert _layout(out)["orientation"] == expected
        assert "orientation" not in out["ui"]["settings"]

    def test_settings_that_are_not_a_dict_are_left_alone(self):
        out = migrate_0_7_to_0_8(_v07([{"id": "p", "elements": [_cell("a")]}], settings="junk"))
        assert out["ui"]["settings"] == "junk"
        assert _layout(out)["orientation"] == "landscape"

    @pytest.mark.parametrize("overrides,x", [
        ({"grid_gap": 20}, 1.5625),   # pad 20 / 1280
        ({"grid_gap": 0}, 0.625),     # 0 reads as the default 8
        ({"grid_gap": "20"}, 0.625),  # a string is not a number
        ("junk", 0.625),
    ])
    def test_the_page_padding_comes_from_the_theme_gap(self, overrides, x):
        out = migrate_0_7_to_0_8(_v07([{"id": "p", "elements": [_cell("a")]}],
                                      settings={"theme_overrides": overrides}))
        assert _layout(out)["placements"]["a"]["x"] == x

    def test_a_zero_page_gap_reads_as_the_padding(self):
        page = {"id": "p", "grid_gap": 0, "elements": [_cell("a", 2)]}
        out = migrate_0_7_to_0_8(_v07([page], settings={"theme_overrides": {"grid_gap": 4}}))
        assert _layout(out)["placements"]["a"] == {
            "x": 8.6198, "y": 0.5, "w": 7.9948, "h": 11.9375,
        }

    def test_a_bad_grid_reads_as_twelve_by_eight(self):
        bad = {"id": "p", "grid": {"columns": 0, "rows": "8"}, "elements": [_cell("a")]}
        good = {"id": "q", "elements": [_cell("a")]}
        out = migrate_0_7_to_0_8(_v07([bad, good]))
        assert _layout(out, 0)["placements"] == _layout(out, 1)["placements"]

    def test_a_bad_cell_reads_as_one_by_one_at_the_corner(self):
        page = {"id": "p", "elements": [
            {"id": "a", "type": "button", "grid_area": "junk"},
            {"id": "b", "type": "button", "grid_area": {"col": 0, "row": -2,
                                                         "col_span": 0.5, "row_span": True}},
            _cell("c"),
        ]}
        placements = _layout(migrate_0_7_to_0_8(_v07([page])))["placements"]
        assert placements["a"] == placements["b"] == placements["c"]

    def test_fractional_cells_truncate(self):
        page = {"id": "p", "elements": [_cell("a", 2.9, 1, 1.5, 1), _cell("b", 2, 1, 1, 1)]}
        placements = _layout(migrate_0_7_to_0_8(_v07([page])))["placements"]
        assert placements["a"] == placements["b"]

    def test_an_element_with_no_id_keeps_its_cell_and_style(self):
        page = {"id": "p", "elements": [
            {"type": "button", "grid_area": {"col": 1, "row": 1}, "style": {"font_size": 28}},
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert out["ui"]["pages"][0]["elements"] == [
            {"type": "button", "grid_area": {"col": 1, "row": 1}, "style": {"font_size": 28}},
        ]
        assert _layout(out)["placements"] == {}

    def test_a_non_dict_element_stays_in_the_list(self):
        page = {"id": "p", "elements": ["junk", _cell("a")]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert out["ui"]["pages"][0]["elements"][0] == "junk"
        assert list(_layout(out)["placements"]) == ["a"]

    def test_non_dict_pages_are_left_in_place(self):
        out = migrate_0_7_to_0_8(_v07(["junk", {"id": "p", "elements": []}]))
        assert out["ui"]["pages"][0] == "junk"
        assert "layouts" in out["ui"]["pages"][1]

    def test_a_ui_that_is_not_a_dict_only_gets_the_stamp(self):
        out = migrate_0_7_to_0_8({"openavc_version": "0.7.0", "ui": None})
        assert out == {"openavc_version": "0.8.0", "ui": None}

    def test_style_values_that_are_not_numbers_pass_through(self):
        el = _cell("a", style={"font_size": "18", "padding": True, "margin": 7, "other": 28},
                   icon_size=None, item_height=21, thumb_size="x")
        out = migrate_0_7_to_0_8(_v07([{"id": "p", "elements": [el]}]))
        got = out["ui"]["pages"][0]["elements"][0]
        assert got["style"] == {"font_size": "18", "padding": True, "margin": 0.5, "other": 28}
        assert got["icon_size"] is None
        assert got["item_height"] == 1.5
        assert got["thumb_size"] == "x"

    def test_a_style_that_is_not_a_dict_is_left_alone(self):
        el = _cell("a", style="big", icon_size=28)
        got = migrate_0_7_to_0_8(_v07([{"id": "p", "elements": [el]}]))["ui"]["pages"][0]["elements"][0]
        assert got["style"] == "big"
        assert got["icon_size"] == 2.0

    def test_a_sidebar_is_its_own_width_and_the_screen_height(self):
        page = {"id": "s", "page_type": "sidebar", "overlay": {"width": 160},
                "elements": [_cell("a", 1, 1)]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert _layout(out)["placements"]["a"] == {"x": 5.0, "y": 1.0, "w": 2.9167, "h": 11.375}
        assert out["ui"]["pages"][0]["overlay"] == {"width": 12.5}

    def test_a_sidebar_height_is_ignored_for_its_box_but_converted(self):
        page = {"id": "s", "page_type": "sidebar", "overlay": {"width": 160, "height": 400},
                "elements": [_cell("a", 1, 1)]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert _layout(out)["placements"]["a"] == {"x": 5.0, "y": 1.0, "w": 2.9167, "h": 11.375}
        assert out["ui"]["pages"][0]["overlay"] == {"width": 12.5, "height": 50.0}

    def test_a_sidebar_with_no_width_uses_the_old_default(self):
        page = {"id": "s", "page_type": "sidebar", "elements": [_cell("a", 1, 1)]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert _layout(out)["placements"]["a"]["x"] == 2.5
        assert "overlay" not in out["ui"]["pages"][0]

    def test_an_overlay_with_a_bad_size_uses_the_defaults_and_keeps_the_value(self):
        page = {"id": "o", "page_type": "overlay",
                "overlay": {"width": "400", "height": 0, "position": "center"},
                "elements": [_cell("a", 1, 1)]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert _layout(out)["placements"]["a"] == {"x": 2.0, "y": 2.6667, "w": 6.1667, "h": 9.5}
        assert json.dumps(out["ui"]["pages"][0]["overlay"]) == (
            '{"width": "400", "height": 0.0, "position": "center"}'
        )

    def test_an_overlay_that_is_not_a_dict(self):
        page = {"id": "o", "page_type": "overlay", "overlay": "junk", "elements": [_cell("a")]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert out["ui"]["pages"][0]["overlay"] == "junk"
        assert _layout(out)["placements"]["a"]["x"] == 2.0

    def test_a_full_page_overlay_box_still_becomes_percent(self):
        page = {"id": "p", "overlay": {"width": 640, "height": 400}, "elements": [_cell("a")]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert out["ui"]["pages"][0]["overlay"] == {"width": 50.0, "height": 50.0}
        assert _layout(out)["placements"]["a"]["x"] == 0.625

    def test_duplicate_ids_share_the_last_rect(self):
        page = {"id": "p", "elements": [_cell("a", 1), _cell("a", 3)]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert _layout(out)["placements"] == {"a": {"x": 17.1875, "y": 1.0, "w": 7.6563, "h": 11.375}}
        assert [e.get("grid_area") for e in out["ui"]["pages"][0]["elements"]] == [None, None]


class TestContainerAdoption:
    def test_no_group_adopts_nothing_and_logs_nothing(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        page = {"id": "p", "elements": [_cell("a", 1, 1, 4, 4), _cell("b", 2, 2)]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert [e["parent"] for e in out["ui"]["pages"][0]["elements"]] == [None, None]
        assert _messages(caplog) == []

    def test_a_group_that_contains_nothing_logs_nothing(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        page = {"id": "p", "elements": [_cell("g", 1, 1, 1, 1, "group"), _cell("b", 5, 5)]}
        migrate_0_7_to_0_8(_v07([page]))
        assert _messages(caplog) == []

    def test_the_adoption_is_logged_with_the_page_and_the_containers(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        page = {"id": "main", "elements": [
            _cell("outer", 1, 1, 6, 6, "group"), _cell("inner", 2, 2, 2, 2, "group"),
            _cell("a", 2, 2), _cell("b", 5, 5),
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        parents = {e["id"]: e["parent"] for e in out["ui"]["pages"][0]["elements"]}
        assert parents == {"outer": None, "inner": "outer", "a": "inner", "b": "outer"}
        assert _messages(caplog) == [(
            MIGRATION_LOGGER, logging.INFO,
            "Project migration 0.8.0: page 'main' adopted 3 element(s) into "
            "container(s) ['inner', 'outer'] -- drag them out in the Outline panel "
            "if that is not the grouping you meant",
        )]

    def test_a_page_with_no_id_logs_a_question_mark(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        page = {"elements": [_cell("g", 1, 1, 2, 2, "group"), _cell("a", 1, 1)]}
        migrate_0_7_to_0_8(_v07([page]))
        assert "page '?' adopted 1 element(s)" in _messages(caplog)[0][2]

    def test_a_group_with_no_id_adopts_nothing_and_the_next_one_does(self):
        page = {"id": "p", "elements": [
            {"type": "group", "grid_area": {"col": 1, "row": 1, "col_span": 2, "row_span": 2}},
            _cell("big", 1, 1, 6, 6, "group"),
            _cell("a", 1, 1),
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        els = out["ui"]["pages"][0]["elements"]
        assert "parent" not in els[0]
        assert els[2]["parent"] == "big"

    def test_an_element_with_no_id_inside_a_group_is_not_adopted(self):
        page = {"id": "p", "elements": [
            _cell("g", 1, 1, 4, 4, "group"),
            {"type": "button", "grid_area": {"col": 1, "row": 1}},
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert "parent" not in out["ui"]["pages"][0]["elements"][1]

    def test_a_bad_cell_is_judged_as_one_by_one_at_the_corner(self):
        page = {"id": "p", "elements": [
            _cell("g", 1, 1, 2, 2, "group"),
            {"id": "a", "type": "button", "grid_area": "junk"},
            {"id": "b", "type": "button", "grid_area": {"col": "3"}},
            {"id": "c", "type": "button", "grid_area": {"col": -2, "row": 0, "col_span": -1}},
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        parents = {e["id"]: e["parent"] for e in out["ui"]["pages"][0]["elements"]}
        assert parents == {"g": None, "a": "g", "b": "g", "c": "g"}

    def test_the_innermost_group_is_the_one_with_fewer_cells(self):
        # Both groups hold the element: the tall thin one (six cells) wins by
        # area, where a ranking by width plus height would pick the square (nine).
        page = {"id": "p", "elements": [
            _cell("square", 1, 1, 3, 3, "group"), _cell("thin", 1, 1, 1, 6, "group"),
            _cell("a", 1, 1),
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert out["ui"]["pages"][0]["elements"][2]["parent"] == "thin"

    def test_equal_size_groups_go_by_their_order_on_the_page(self):
        page = {"id": "p", "elements": [
            _cell("first", 1, 1, 3, 3, "group"), _cell("second", 2, 2, 3, 3, "group"),
            _cell("a", 2, 2),
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert out["ui"]["pages"][0]["elements"][2]["parent"] == "first"

    def test_containment_is_inclusive_at_the_edges(self):
        page = {"id": "p", "elements": [
            _cell("g", 2, 2, 3, 3, "group"),
            _cell("corner", 4, 4), _cell("past", 5, 4), _cell("before", 1, 2),
            _cell("exact", 2, 2, 3, 3),
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        parents = {e["id"]: e["parent"] for e in out["ui"]["pages"][0]["elements"]}
        assert parents == {"g": None, "corner": "g", "past": None, "before": None, "exact": "g"}

    def test_a_three_way_tie_keeps_the_first_two_links_of_the_chain(self):
        page = {"id": "p", "elements": [
            _cell("g1", 1, 1, 2, 2, "group"), _cell("g2", 1, 1, 2, 2, "group"),
            _cell("g3", 1, 1, 2, 2, "group"),
        ]}
        out = migrate_0_7_to_0_8(_v07([page]))
        parents = {e["id"]: e["parent"] for e in out["ui"]["pages"][0]["elements"]}
        assert parents == {"g1": None, "g2": "g1", "g3": "g1"}

    def test_a_child_rect_is_relative_to_a_zero_width_container_as_if_full(self):
        # A container's rect is zero wide only when the gaps eat the whole box;
        # the guard divides by 100 instead. The negative height divides as it is.
        page = {"id": "p", "grid": {"columns": 1, "rows": 1}, "elements": [
            _cell("g", 1, 1, 1, 1, "group"), _cell("a", 1, 1),
        ]}
        out = migrate_0_7_to_0_8(_v07([page], settings={"theme_overrides": {"grid_gap": 640}}))
        # Saved as 0.0, never -0.0, though the y offset divides by a negative height.
        assert json.dumps(_layout(out)["placements"]["a"]) == (
            '{"x": 0.0, "y": 0.0, "w": 0.0, "h": 100.0}'
        )

    def test_an_offset_child_of_a_zero_width_container(self):
        page = {"id": "p", "grid_gap": 8, "grid": {"columns": 2, "rows": 1}, "elements": [
            _cell("g", 1, 1, 2, 1, "group"), _cell("a", 2, 1),
        ]}
        out = migrate_0_7_to_0_8(_v07([page], settings={"theme_overrides": {"grid_gap": 640}}))
        placements = _layout(out)["placements"]
        assert placements["g"] == {"x": 50.0, "y": 80.0, "w": 0.0, "h": -60.0}
        assert placements["a"] == {"x": 0.3125, "y": 0.0, "w": -0.3125, "h": 100.0}


class TestSpacers:
    @pytest.mark.parametrize("visual", [
        "bg_color", "background_image", "background_gradient", "border_width",
    ])
    def test_each_visual_key_keeps_a_spacer(self, visual):
        page = {"id": "p", "elements": [_cell("s", el_type="spacer", style={visual: 2},
                                              label="Gap", text="x")]}
        out = migrate_0_7_to_0_8(_v07([page]))
        got = out["ui"]["pages"][0]["elements"][0]
        assert (got["type"], got["text"], got["label"]) == ("label", "", None)
        assert list(got) == ["id", "type", "style", "label", "text", "parent"]

    def test_a_falsy_visual_value_does_not_keep_it(self):
        page = {"id": "p", "elements": [_cell("s", el_type="spacer",
                                              style={"bg_color": "", "border_width": 0})]}
        out = migrate_0_7_to_0_8(_v07([page]))
        assert out["ui"]["pages"][0]["elements"] == []

    def test_a_spacer_with_a_style_that_is_not_a_dict_is_dropped(self):
        page = {"id": "p", "elements": [_cell("s", el_type="spacer", style="bg_color")]}
        assert migrate_0_7_to_0_8(_v07([page]))["ui"]["pages"][0]["elements"] == []


class TestNavigateRename:
    def test_shapes_it_does_not_rewrite(self):
        el = {"id": "b", "bindings": {"do": {
            "press": ["junk", {"action": "page", "map": "junk"}],
            "release": {"action": "navigate", "map": {"a": "junk", "b": [3, {"action": "page"}]}},
        }}}
        out = migrate_0_7_to_0_8(_v07([{"id": "p", "elements": [el]}]))
        do = out["ui"]["pages"][0]["elements"][0]["bindings"]["do"]
        assert do == {
            "press": ["junk", {"action": "ui.navigate", "map": "junk"}],
            "release": {"action": "ui.navigate", "map": {"a": "junk", "b": [3, {"action": "ui.navigate"}]}},
        }

    def test_bindings_without_a_do_map(self):
        els = [{"id": "a", "bindings": "junk"}, {"id": "b", "bindings": {"do": ["x"]}},
               {"id": "c"}]
        out = migrate_0_7_to_0_8(_v07([{"id": "p", "elements": els}]))
        got = out["ui"]["pages"][0]["elements"]
        assert [e.get("bindings") for e in got] == ["junk", {"do": ["x"]}, None]

    def test_an_element_with_no_id_is_still_renamed(self):
        el = {"type": "button", "bindings": {"do": {"press": {"action": "page"}}}}
        out = migrate_0_7_to_0_8(_v07([{"id": "p", "elements": [el]}]))
        assert out["ui"]["pages"][0]["elements"][0]["bindings"]["do"]["press"] == {
            "action": "ui.navigate",
        }

    def test_a_non_dict_master_is_skipped(self):
        out = migrate_0_7_to_0_8(_v07([], masters=["junk"]))
        assert out["ui"]["master_elements"] == ["junk"]

    @pytest.mark.parametrize("ui", [
        {"pages": None},
        {"pages": [{"id": "p", "elements": None}]},
        {"pages": [], "master_elements": None},
    ])
    def test_a_null_list_stops_the_step(self, ui):
        with pytest.raises(TypeError):
            migrate_0_7_to_0_8({"openavc_version": "0.7.0", "ui": ui})

    def test_the_rename_stops_at_a_page_whose_elements_are_not_a_list(self):
        # The pages before it are renamed already; the masters are not reached.
        el = {"id": "b", "bindings": {"do": {"press": {"action": "page"}}}}
        master = {"id": "m", "bindings": {"do": {"press": {"action": "navigate"}}}}
        data = _v07([{"id": "p", "elements": [el]}, {"id": "q", "elements": 5}],
                    masters=[master])
        with pytest.raises(TypeError):
            migrate_0_7_to_0_8(data)
        assert el["bindings"]["do"]["press"]["action"] == "ui.navigate"
        assert master["bindings"]["do"]["press"]["action"] == "navigate"


class TestMasters:
    def _project(self, targets, **master):
        pages = [
            {"id": "first", "grid": {"columns": 2, "rows": 2}, "elements": []},
            {"id": "second", "grid": {"columns": 4, "rows": 4}, "elements": []},
        ]
        m = {"id": "m", "type": "button", "grid_area": {"col": 1, "row": 1}, **master}
        if targets is not ...:
            m["pages"] = targets
        return _v07(pages, masters=[m])

    @pytest.mark.parametrize("targets,reference", [
        (..., "first"), ("*", "first"), ([], "first"), ("", "first"), (None, "first"),
        ("second", "second"), ("missing", None),
        (["missing", "second", "first"], "second"), (["missing"], None),
        ({"second": True}, None),
    ])
    def test_the_page_a_master_is_converted_against(self, targets, reference, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        out = migrate_0_7_to_0_8(self._project(targets))
        size = {"first": (2, 2), "second": (4, 4), None: (12, 8)}[reference]
        assert _messages(caplog) == [(
            MIGRATION_LOGGER, logging.INFO,
            "Project migration 0.8.0: master element 'm' converted against page "
            f"'{reference or '<none, used 12x8>'}' ({size[0]}x{size[1]} grid)",
        )]
        rect = out["ui"]["master_elements"][0]["placements"]["landscape"]
        cols, rows = size
        cell_w = (1280 - 16 - (cols - 1) * 8) / cols
        assert rect["w"] == round(cell_w / 1280 * 100, 4)

    def test_a_master_uses_the_theme_gap_for_padding_and_cells(self):
        out = migrate_0_7_to_0_8(_v07(
            [{"id": "p", "grid_gap": 30, "elements": []}],
            masters=[{"id": "m", "grid_area": {"col": 2, "row": 1}}],
            settings={"theme_overrides": {"grid_gap": 4}},
        ))
        assert out["ui"]["master_elements"][0]["placements"]["landscape"] == {
            "x": 8.6198, "y": 0.5, "w": 7.9948, "h": 11.9375,
        }

    def test_a_master_is_written_in_order(self):
        out = migrate_0_7_to_0_8(_v07(
            [], masters=[{"id": "m", "grid_area": {"col": 1, "row": 1},
                          "style": {"font_size": 14}, "pages": "*", "z": 1}],
            settings={"orientation": "portrait"},
        ))
        assert _ordered(out["ui"]["master_elements"][0]) == _ordered({
            "id": "m", "style": {"font_size": 1.0}, "pages": "*", "z": 1,
            "parent": None,
            "placements": {"portrait": {"x": 0.625, "y": 1.0, "w": 7.6563, "h": 11.375}},
            "hidden": False,
        })

    def test_no_pages_means_the_default_grid(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        migrate_0_7_to_0_8(_v07([], masters=[{"grid_area": "junk"}]))
        assert _messages(caplog)[0][2] == (
            "Project migration 0.8.0: master element '?' converted against page "
            "'<none, used 12x8>' (12x8 grid)"
        )

    def test_a_first_page_with_no_id_means_the_default_grid(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        pages = [{"grid": {"columns": 2, "rows": 2}, "elements": []},
                 {"id": "b", "grid": {"columns": 3, "rows": 3}, "elements": []}]
        migrate_0_7_to_0_8(_v07(pages, masters=[{"id": "m"}]))
        assert "(12x8 grid)" in _messages(caplog)[0][2]

    def test_the_first_page_counts_even_when_it_is_not_a_dict_in_the_list(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        pages = ["junk", {"id": "b", "grid": {"columns": 3, "rows": 3}, "elements": []}]
        migrate_0_7_to_0_8(_v07(pages, masters=[{"id": "m"}]))
        assert "page 'b' (3x3 grid)" in _messages(caplog)[0][2]

    def test_masters_are_converted_after_every_page(self, caplog):
        caplog.set_level(logging.DEBUG, logger=MIGRATION_LOGGER)
        pages = [{"id": "p", "elements": [_cell("g", 1, 1, 2, 2, "group"), _cell("a")]}]
        migrate_0_7_to_0_8(_v07(pages, masters=[{"id": "m"}]))
        assert [m[2].split(":")[1].split()[0] for m in _messages(caplog)] == ["page", "master"]


# ---------------------------------------------------------------------------
# 0.9.0 -> 0.10.0
# ---------------------------------------------------------------------------

class TestMatrixConfig:
    def test_an_axis_already_in_the_new_form_is_kept_and_the_old_keys_go(self):
        config = {"sources": [{"value": 1}], "input_count": 3, "output_count": 2,
                  "route_key_pattern": "r.*", "show_lock": False}
        assert _ordered(_migrate_matrix_config_0_9_to_0_10(config)) == _ordered({
            "sources": [{"value": 1}], "show_lock": False,
            "destinations": {"from": {"count": 2, "route_key": "r.*"}},
        })

    @pytest.mark.parametrize("count,expected", [
        ("abc", 4), (None, 4), (0, 4), ("", 4), (-3, 0), ("6", 6), (2.9, 2), ([1], 4),
    ])
    def test_the_count(self, count, expected):
        out = _migrate_matrix_config_0_9_to_0_10({"input_count": count})
        assert out["sources"] == {"from": {"count": expected}}

    def test_an_audio_key_without_a_video_key(self):
        out = _migrate_matrix_config_0_9_to_0_10({"audio_route_key_pattern": "a.*"})
        assert out["destinations"] == {"from": {"count": 4, "audio_route_key": "a.*"}}

    def test_a_source_axis_never_takes_the_route_keys(self):
        out = _migrate_matrix_config_0_9_to_0_10({
            "route_key_pattern": "r.*", "audio_route_key_pattern": "a.*",
            "input_key_pattern": "in.*", "input_labels": ["A"],
        })
        assert out == {
            "sources": {"from": {"count": 4, "labels": ["A"], "label_key": "in.*"}},
            "destinations": {"from": {"count": 4, "route_key": "r.*", "audio_route_key": "a.*"}},
        }

    def test_empty_labels_and_patterns_are_not_carried(self):
        out = _migrate_matrix_config_0_9_to_0_10({
            "output_labels": [], "output_key_pattern": "", "input_labels": "AB",
        })
        assert out == {"sources": {"from": {"count": 4}}, "destinations": {"from": {"count": 4}}}

    def test_the_generator_keys_come_in_one_order(self):
        out = _migrate_matrix_config_0_9_to_0_10({
            "audio_route_key_pattern": "a", "route_key_pattern": "r",
            "output_key_pattern": "k", "output_labels": ["x"], "output_count": 1,
        })
        assert list(out["destinations"]["from"]) == [
            "count", "labels", "label_key", "route_key", "audio_route_key",
        ]
        assert list(out) == ["sources", "destinations"]

    def test_only_matrices_with_a_config_are_rewritten(self):
        data = {"openavc_version": "0.9.0", "ui": {
            "pages": [None, {"elements": [
                {"type": "matrix"}, {"type": "matrix", "matrix_config": {}},
                {"type": "matrix", "matrix_config": "junk"},
                {"type": "button", "matrix_config": {"input_count": 2}},
                "junk",
            ]}, {"elements": None}],
            "master_elements": [{"type": "matrix", "matrix_config": {"input_count": 2}}],
        }}
        out = migrate_0_9_to_0_10(data)
        els = out["ui"]["pages"][1]["elements"]
        assert els[0] == {"type": "matrix"}
        assert els[1]["matrix_config"] == {}
        assert els[2]["matrix_config"] == "junk"
        assert els[3]["matrix_config"] == {"input_count": 2}
        assert out["ui"]["master_elements"][0]["matrix_config"]["sources"] == {
            "from": {"count": 2},
        }


# ---------------------------------------------------------------------------
# 0.10.0 -> 0.11.0
# ---------------------------------------------------------------------------

class TestDashboardMonitors:
    def test_the_flag_is_dropped_from_every_variable(self):
        data = {"variables": [
            {"id": "a", "dashboard": False}, {"id": 5, "dashboard": True},
            {"id": "", "dashboard": True}, {"dashboard": True}, "junk",
        ]}
        out = migrate_0_10_to_0_11(data)
        assert out["variables"] == [{"id": "a"}, {"id": 5}, {"id": ""}, {}, "junk"]
        assert "monitors" not in out

    def test_one_monitor_per_key_after_the_existing_ones(self):
        data = {
            "monitors": [{"key": "var.b", "label": "kept"}, "junk", {"key": 3}],
            "variables": [
                {"id": "a", "dashboard": True, "label": "A", "type": "number"},
                {"id": "a", "dashboard": "yes"},
                {"id": "b", "dashboard": True},
                {"id": "c", "dashboard": 1, "label": None, "type": ""},
            ],
        }
        out = migrate_0_10_to_0_11(data)
        assert _ordered(out["monitors"]) == _ordered([
            {"key": "var.b", "label": "kept"}, "junk", {"key": 3},
            {"key": "var.a", "label": "A", "type": "number"},
            {"key": "var.c", "label": "", "type": "string"},
        ])

    def test_an_existing_monitor_whose_key_is_not_a_string_is_kept(self):
        data = {"monitors": [{"key": ["x"]}, {"key": {"a": 1}}],
                "variables": [{"id": "a", "dashboard": True}]}
        out = migrate_0_10_to_0_11(data)
        assert out["monitors"] == [
            {"key": ["x"]}, {"key": {"a": 1}},
            {"key": "var.a", "label": "", "type": "string"},
        ]

    def test_a_monitor_list_that_is_not_a_list_is_replaced(self):
        out = migrate_0_10_to_0_11({"monitors": "junk", "variables": [{"id": "a", "dashboard": True}]})
        assert out["monitors"] == [{"key": "var.a", "label": "", "type": "string"}]

    def test_an_existing_list_with_no_new_monitors_is_left_as_given(self):
        existing = []
        data = {"monitors": existing, "variables": None}
        out = migrate_0_10_to_0_11(data)
        assert out["monitors"] is existing

    def test_a_new_list_object_replaces_the_given_one(self):
        existing = [{"key": "var.x"}]
        out = migrate_0_10_to_0_11({"monitors": existing,
                                    "variables": [{"id": "y", "dashboard": True}]})
        assert out["monitors"] is not existing
        assert existing == [{"key": "var.x"}]


# ---------------------------------------------------------------------------
# The steps that walk every element (0.15.0, 0.16.0)
# ---------------------------------------------------------------------------

class TestElementWalks:
    @pytest.mark.parametrize("step,version", [
        (migrate_0_9_to_0_10, "0.9.0"),
        (migrate_0_14_to_0_15, "0.14.0"),
        (migrate_0_15_to_0_16, "0.15.0"),
    ])
    @pytest.mark.parametrize("ui", [
        {"pages": None, "master_elements": None},
        {"pages": [None, "x", {"elements": None}]},
        {},
        "junk",
    ])
    def test_a_null_or_missing_list_reads_as_empty(self, step, version, ui):
        data = {"openavc_version": version, "ui": copy.deepcopy(ui)}
        out = step(data)
        assert out["ui"] == ui

    @pytest.mark.parametrize("step,version", [
        (migrate_0_9_to_0_10, "0.9.0"),
        (migrate_0_14_to_0_15, "0.14.0"),
        (migrate_0_15_to_0_16, "0.15.0"),
    ])
    def test_an_unreadable_list_stops_the_step_before_anything_changes(self, step, version):
        first = {"type": "fader", "label": "x", "matrix_config": {"input_count": 2}}
        first["type"] = {"0.9.0": "matrix", "0.14.0": "clock", "0.15.0": "fader"}[version]
        data = {"openavc_version": version, "ui": {"pages": [
            {"elements": [first]}, {"elements": 5},
        ]}}
        before = copy.deepcopy(data)
        with pytest.raises(TypeError):
            step(data)
        assert data == before

    def test_pages_are_walked_before_masters(self):
        seen = []

        class Recording(dict):
            def get(self, key, default=None):
                if key == "type":
                    seen.append(self["id"])
                return super().get(key, default)

        data = {"openavc_version": "0.15.0", "ui": {
            "master_elements": [Recording(id="m1")],
            "pages": [{"elements": [Recording(id="a")]}, {"elements": [Recording(id="b")]}],
        }}
        migrate_0_15_to_0_16(data)
        assert seen == ["a", "b", "m1"]

    def test_a_label_is_dropped_from_the_four_types_only(self):
        data = {"openavc_version": "0.14.0", "ui": {
            "pages": [{"elements": [
                {"type": t, "label": "L"} for t in ("clock", "custom", "label", "plugin", "button")
            ]}],
            "master_elements": [{"type": "custom", "label": "L"}, {"label": "L"}, 7],
        }}
        out = migrate_0_14_to_0_15(data)
        assert [("label" in e) for e in out["ui"]["pages"][0]["elements"]] == [
            False, False, False, False, True,
        ]
        assert out["ui"]["master_elements"] == [{"type": "custom"}, {"label": "L"}, 7]

    def test_a_fader_unit_of_false_or_zero_is_left_alone(self):
        data = {"openavc_version": "0.15.0", "ui": {"pages": [{"elements": [
            {"type": "fader", "unit": False}, {"type": "fader", "unit": 0},
            {"type": "fader", "unit": None},
        ]}]}}
        out = migrate_0_15_to_0_16(data)
        assert [e["unit"] for e in out["ui"]["pages"][0]["elements"]] == [False, 0, "%"]


# ---------------------------------------------------------------------------
# A rich project through every step that rewrites, compared as saved JSON
# ---------------------------------------------------------------------------

def test_a_rich_0_6_project_saves_the_same_bytes_through_the_layout_step():
    """One project exercising most of 0.7.0 and 0.8.0 together, checked as the
    JSON text a save would write (key order included)."""
    data = {
        "openavc_version": "0.6.0",
        "project": {"id": "p", "name": "P"},
        "ui": {
            "settings": {"orientation": "portrait", "theme_overrides": {"grid_gap": 6}},
            "pages": [
                {"id": "main", "grid": {"columns": 6, "rows": 4}, "elements": [
                    {"id": "frame", "type": "group",
                     "grid_area": {"col": 1, "row": 1, "col_span": 4, "row_span": 3},
                     "style": {"border_width": 2, "border_radius": 7}},
                    {"id": "go", "type": "button",
                     "grid_area": {"col": 2, "row": 2, "col_span": 2},
                     "bindings": {"press": {"action": "navigate", "page": "two"},
                                  "feedback": {"key": "var.on"}},
                     "style": {"font_size": 21, "padding": 4}, "icon_size": 35},
                    {"id": "gap", "type": "spacer",
                     "grid_area": {"col": 5, "row": 1}, "style": {"bg_color": "#111"}},
                    {"id": "air", "type": "spacer", "grid_area": {"col": 6, "row": 1}},
                    {"id": "mx", "type": "matrix",
                     "grid_area": {"col": 1, "row": 4, "col_span": 6},
                     "bindings": {"route": [{"action": "route"}],
                                  "presets": [{"name": "All", "macro": "all"}]},
                     "matrix_config": {"input_count": 2}},
                ]},
                {"id": "pop", "page_type": "overlay", "grid_gap": 12,
                 "overlay": {"width": 600, "height": 360},
                 "elements": [{"id": "x", "type": "label",
                               "grid_area": {"col": 12, "row": 8},
                               "bindings": {"text": {"key": "var.t"}}}]},
            ],
            "master_elements": [
                {"id": "bar", "type": "button", "pages": ["pop", "main"],
                 "grid_area": {"col": 1, "row": 8, "col_span": 12},
                 "bindings": {"press": [{"action": "page", "page": "main"}]}},
            ],
        },
    }
    out = migrate_0_7_to_0_8(migrate_0_6_to_0_7(data))
    assert json.dumps(out) == json.dumps(_RICH_EXPECTED)


_RICH_EXPECTED = {
    "openavc_version": "0.8.0",
    "project": {"id": "p", "name": "P"},
    "ui": {
        "settings": {"theme_overrides": {"grid_gap": 6}},
        "pages": [
            {"id": "main", "elements": [
                {"id": "frame", "type": "group",
                 "style": {"border_width": 0.1429, "border_radius": 0.5}, "parent": None},
                {"id": "go", "type": "button",
                 "bindings": {"show": {"look": {"key": "var.on"}},
                              "do": {"press": [{"action": "ui.navigate", "page": "two"}]}},
                 "style": {"font_size": 1.5, "padding": 0.2857}, "icon_size": 2.5,
                 "parent": "frame"},
                {"id": "gap", "type": "label", "style": {"bg_color": "#111"},
                 "text": "", "label": None, "parent": None},
                {"id": "mx", "type": "matrix",
                 "bindings": {"do": {"route": [{"action": "route"}]}},
                 "matrix_config": {"input_count": 2,
                                   "presets": [{"name": "All", "macro": "all"}]},
                 "parent": None},
            ], "snap": {"enabled": True, "x": 8.3333, "y": 12.5}, "layouts": [{
                "id": "portrait", "orientation": "portrait", "primary": True,
                "inherits": None, "placements": {
                    "frame": {"x": 0.4688, "y": 0.75, "w": 65.8854, "h": 73.6875},
                    "go": {"x": 25.1778, "y": 33.6726, "w": 49.6442, "h": 32.6548},
                    "gap": {"x": 66.8229, "y": 0.75, "w": 16.1198, "h": 24.0625},
                    "mx": {"x": 0.4688, "y": 75.1875, "w": 99.0625, "h": 24.0625},
                }, "hidden": [],
            }]},
            {"id": "pop", "page_type": "overlay", "overlay": {"width": 46.875, "height": 45.0},
             "elements": [{"id": "x", "type": "label",
                           "bindings": {"show": {"value": {"key": "var.t"}}},
                           "parent": None}],
             "snap": {"enabled": True, "x": 8.3333, "y": 12.5}, "layouts": [{
                 "id": "portrait", "orientation": "portrait", "primary": True,
                 "inherits": None, "placements": {
                     "x": {"x": 92.6667, "y": 89.1667, "w": 6.3333, "h": 9.1667},
                 }, "hidden": [],
             }]},
        ],
        "master_elements": [
            {"id": "bar", "type": "button", "pages": ["pop", "main"],
             "bindings": {"do": {"press": [{"action": "ui.navigate", "page": "main"}]}},
             "parent": None,
             "placements": {"portrait": {"x": 0.4688, "y": 87.5938, "w": 99.0625, "h": 11.6562}},
             "hidden": False},
        ],
    },
}
