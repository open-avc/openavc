"""A roster follows the device's own count only beside ``count_from``.

``count_from_state`` names the state variable a roster sized from a config
field (``count_from``) follows once the device reports a count. ``ids``,
``ids_from`` and ``count`` fix the roster, so beside them it is refused where a
driver is written and named as a notice where one is loaded, and every reader
of the block (the runtime roster, the authoring prediction, the simulator, the
poll-length warning) reads such a block by its source alone. On an invented
Acme device.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import check_driver_definition
from openavc.drivers.child_ids import declared_child_ids
from openavc.drivers.roster_source import config_child_ids, roster_source
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_issues
from openavc.drivers.driver_warnings import _poll_lines
from openavc.drivers.python_info import check_python_driver_info
from openavc.simulator.child_roster import declared_ids

FIX = "ids, ids_from and count fix the roster; use count_from with count_from_state"

FIXED_SOURCES = [("count", 3), ("ids", [1, 2]), ("ids_from", "zones")]


def _zone(instances: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": "Zone",
        "id_format": {"type": "integer", "min": 1, "max": 8},
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
        "instances": instances,
    }


def _definition(instances: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "default_config": {"host": "", "port": 5000, "zones": "1,2", "zone_count": 2},
        "config_schema": {
            "zones": {"type": "string", "label": "Zones"},
            "zone_count": {"type": "integer", "label": "Zone count"},
        },
        "state_variables": {"num_zones": {"type": "integer", "label": "Zones"}},
        "child_entity_types": {"zone": _zone(instances)},
        "commands": {"noop": {"send": "NOOP\r"}},
        "responses": [],
    }


def _said(messages: list[str]) -> list[str]:
    return [m for m in messages if "count_from_state beside" in m]


# --- the author's doors and the load -----------------------------------------


@pytest.mark.parametrize("source, value", FIXED_SOURCES)
def test_the_authors_doors_refuse_it_beside_a_source_that_fixes_the_roster(source, value):
    verdict = check_driver_definition(
        _definition({source: value, "count_from_state": "num_zones"}), strict=True
    )
    assert _said(verdict.errors) == [
        f"child_entity_types.zone.instances: count_from_state beside {source}: {FIX}"
    ]


@pytest.mark.parametrize("source, value", FIXED_SOURCES)
def test_a_driver_that_loads_today_still_loads_and_is_told(source, value):
    verdict = check_driver_definition(
        _definition({source: value, "count_from_state": "num_zones"}), strict=False
    )
    assert verdict.errors == []
    assert _said(verdict.notices) == [
        f"child_entity_types.zone.instances: count_from_state beside {source}: {FIX}"
    ]


def test_the_refusal_lands_on_the_child_type():
    issues = validate_driver_issues(
        _definition({"count": 3, "count_from_state": "num_zones"}), strict=True
    )
    assert [i["path"] for i in issues if "count_from_state beside" in i["message"]] == [
        "child_entity_types.zone"
    ]


def test_beside_count_from_it_is_the_roster_that_follows_the_device():
    verdict = check_driver_definition(
        _definition({"count_from": "zone_count", "count_from_state": "num_zones"}), strict=True
    )
    assert verdict.errors == []


def test_alone_it_is_told_only_to_declare_a_source():
    """No source at all is the one-of rule's to name, once."""
    verdict = check_driver_definition(_definition({"count_from_state": "num_zones"}), strict=True)
    assert _said(verdict.errors) == []
    assert any("declare exactly one of" in m for m in verdict.errors)


@pytest.mark.parametrize("source, value", FIXED_SOURCES)
def test_a_python_driver_is_held_to_the_same_rule(source, value):
    info = _definition({source: value, "count_from_state": "num_zones"})
    message = f"child_entity_types.zone.instances: count_from_state beside {source}: {FIX}"
    assert _said(check_python_driver_info(info, strict=True).errors) == [message]
    lenient = check_python_driver_info(info, strict=False)
    assert _said(lenient.errors) == []
    assert _said(lenient.notices) == [message]


# --- the one reading ---------------------------------------------------------


@pytest.mark.parametrize("instances, kind, follows", [
    ({"count_from": "zone_count", "count_from_state": "num_zones"}, "count_from", "num_zones"),
    ({"count_from": "zone_count"}, "count_from", None),
    ({"count": 3, "count_from_state": "num_zones"}, "count", None),
    ({"ids": [1, 2], "count_from_state": "num_zones"}, "ids", None),
    ({"ids_from": "zones", "count_from_state": "num_zones"}, "ids_from", None),
    ({"count_from_state": "num_zones"}, None, None),
    ({"count_from": "zone_count", "count_from_state": ""}, "count_from", None),
    ({}, None, None),
    (None, None, None),
    (["count"], None, None),
])
def test_the_source_and_what_it_follows(instances, kind, follows):
    source = roster_source(instances)
    assert (source.kind, source.follows) == (kind, follows)


# --- every reader agrees -----------------------------------------------------


def _runtime(instances: dict[str, Any], config: dict[str, Any]) -> Any:
    definition = _definition(instances)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    return cls("dev1", {"host": "127.0.0.1", "port": 5000, **config}, state, events)


@pytest.mark.parametrize("source, value, config, ids", [
    ("count", 3, {}, [1, 2, 3]),
    ("ids", [1, 2], {}, [1, 2]),
    ("ids_from", "zones", {"zones": "1,4"}, [1, 4]),
])
def test_the_runtime_reads_the_source_and_never_follows_the_device(source, value, config, ids):
    driver = _runtime({source: value, "count_from_state": "num_zones"}, config)
    driver._register_declared_children()
    assert sorted(driver.list_children("zone")) == ids
    driver.set_state("num_zones", 6)
    assert sorted(driver.list_children("zone")) == ids


def test_the_runtime_follows_the_device_beside_count_from():
    driver = _runtime({"count_from": "zone_count", "count_from_state": "num_zones"}, {"zone_count": 2})
    driver._register_declared_children()
    assert sorted(driver.list_children("zone")) == [1, 2]
    driver.set_state("num_zones", 4)
    assert sorted(driver.list_children("zone")) == [1, 2, 3, 4]


@pytest.mark.parametrize("source, value, config, ids", [
    ("count", 3, {}, [1, 2, 3]),
    ("ids", [1, 2], {}, [1, 2]),
    ("ids_from", "zones", {"zones": "1,4"}, ["1", "4"]),
])
def test_the_prediction_reads_the_source_beside_a_fixed_one(source, value, config, ids):
    type_def = _zone({source: value, "count_from_state": "num_zones"})
    assert declared_child_ids(type_def, config) == ids


def test_the_prediction_has_no_answer_for_a_roster_that_follows_the_device():
    type_def = _zone({"count_from": "zone_count", "count_from_state": "num_zones"})
    assert declared_child_ids(type_def, {"zone_count": 2}) is None
    assert config_child_ids(type_def, {"zone_count": 2}) == [1, 2]


@pytest.mark.parametrize("source, value, config, ids", [
    ("count", 3, {}, [1, 2, 3]),
    ("ids", [1, 2], {}, [1, 2]),
    ("ids_from", "zones", {"zones": "1,4"}, [1, 4]),
])
def test_the_simulator_reads_the_source_beside_a_fixed_one(source, value, config, ids):
    assert declared_ids(_zone({source: value, "count_from_state": "num_zones"}), config) == ids


def test_the_simulator_reads_declared_ids_as_the_type_reads_them():
    """An integer type's "02" is child 2, as the runtime registers it."""
    assert declared_ids(_zone({"ids": ["01", 2]}), {}) == [1, 2]
    assert declared_ids(_zone({"ids_from": "zones"}), {"zones": "01, 3"}) == [1, 3]
    text = {**_zone({"ids_from": "zones"}), "id_format": {"type": "string"}}
    assert declared_ids(text, {"zones": "01, b"}) == ["01", "b"]


def test_the_poll_counts_a_roster_beside_a_fixed_source_by_that_source():
    definition = _definition({"ids_from": "zones", "count_from_state": "num_zones"})
    queries = [{"send": "LEVEL? {child_id}\r", "each_child": "zone"}]
    assert _poll_lines(queries, definition["child_entity_types"], {"zones": "1,2,4"}) == 3
