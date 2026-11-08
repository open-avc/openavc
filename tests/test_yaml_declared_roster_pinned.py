"""What a YAML driver's declared child roster does with the declarations
nothing else tests.

An ``instances:`` block on a child type names the children a device has
before it ever answers: ``ids`` (a literal list), ``count`` / ``count_from``
/ ``count_from_state`` (1..N) or ``ids_from`` (a comma-separated config
field). These pin the edges: a declaration that cannot be read, ids of the
wrong kind or shape, a count that is not a number or is negative, a child
the id format refuses, and a reconcile that fails. On an invented Acme
device.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class


def _driver(
    child_types: dict[str, Any], config: dict[str, Any] | None = None,
) -> Any:
    definition: dict[str, Any] = {
        "id": "acme_matrix",
        "name": "Acme Matrix",
        "manufacturer": "Acme",
        "category": "switchers",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "default_config": {"host": "", "port": 5000},
        "config_schema": {},
        "state_variables": {
            "num_outputs": {"type": "integer", "label": "Outputs"},
            "mode": {"type": "string", "label": "Mode"},
        },
        "child_entity_types": child_types,
        "commands": {},
        "responses": [],
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    cfg = {"host": "127.0.0.1", "port": 5000}
    cfg.update(config or {})
    return cls("dev1", cfg, state, events)


def _type(instances: Any, id_format: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "label": "Output",
        "id_format": id_format or {"type": "integer", "min": 1},
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
        "instances": instances,
    }


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_ids_that_are_not_a_list_register_nothing_and_warn(caplog):
    driver = _driver({"output": _type({"ids": "1,2"})})
    caplog.clear()
    assert driver._register_declared_children() == {}
    assert driver.list_children("output") == []
    assert _warnings(caplog) == ["[dev1] instances: output ids must be a list"]


def test_integer_ids_skip_what_is_not_a_scalar_or_not_an_integer(caplog):
    driver = _driver({"output": _type({"ids": [1, True, {"x": 1}, " 3 ", "x", 2.0]})})
    caplog.clear()
    assert driver._register_declared_children() == {"output": 2}
    assert sorted(driver.list_children("output")) == [1, 3]
    assert _warnings(caplog) == [
        "[dev1] instances: output id True is not a scalar; skipping",
        "[dev1] instances: output id {'x': 1} is not a scalar; skipping",
        "[dev1] instances: output id 'x' is not an integer; skipping",
        "[dev1] instances: output id 2.0 is not a scalar; skipping",
    ]


def test_string_ids_are_trimmed_and_blank_ones_dropped(caplog):
    driver = _driver({
        "zone": _type({"ids": ["lobby", "  ", " hall ", 7]}, {"type": "string"}),
    })
    caplog.clear()
    assert driver._register_declared_children() == {"zone": 3}
    assert sorted(driver.list_children("zone"), key=str) == ["7", "hall", "lobby"]
    assert _warnings(caplog) == []


def test_a_count_on_a_string_id_type_registers_nothing_and_warns(caplog):
    driver = _driver({"zone": _type({"count": 3}, {"type": "string"})})
    caplog.clear()
    assert driver._register_declared_children() == {}
    assert _warnings(caplog) == [
        "[dev1] instances: count/count_from require integer ids (zone declares "
        "string ids — use ids_from)"
    ]


def test_a_count_that_is_not_an_integer_registers_nothing_and_warns(caplog):
    driver = _driver({"output": _type({"count": "many"})})
    caplog.clear()
    assert driver._register_declared_children() == {}
    assert _warnings(caplog) == [
        "[dev1] instances: output count 'many' is not an integer"
    ]


def test_a_negative_count_registers_nothing_silently(caplog):
    driver = _driver({"output": _type({"count": -2})})
    caplog.clear()
    assert driver._register_declared_children() == {}
    assert _warnings(caplog) == []


def test_a_count_of_zero_registers_an_empty_roster():
    driver = _driver({"output": _type({"count": 0})})
    assert driver._register_declared_children() == {"output": 0}


def test_a_reported_count_that_is_not_a_number_falls_back_to_the_config_field():
    driver = _driver(
        {"output": _type({"count_from_state": "num_outputs", "count_from": "outputs"})},
        {"outputs": 2},
    )
    driver.set_state("num_outputs", "lots")
    assert driver._register_declared_children() == {"output": 2}
    assert sorted(driver.list_children("output")) == [1, 2]


def test_a_positive_reported_count_wins_over_the_config_field():
    driver = _driver(
        {"output": _type({"count_from_state": "num_outputs", "count_from": "outputs"})},
        {"outputs": 2},
    )
    driver.set_state("num_outputs", 5)
    assert sorted(driver.list_children("output")) == [1, 2, 3, 4, 5]


def test_no_count_known_leaves_the_roster_untouched():
    driver = _driver(
        {"output": _type({"count_from_state": "num_outputs", "count_from": "outputs"})},
        {"outputs": " "},
    )
    driver.register_child("output", 9)
    assert driver._register_declared_children() == {}
    assert driver.list_children("output") == [9]


def test_ids_from_skips_tokens_that_are_not_integers(caplog):
    driver = _driver({"output": _type({"ids_from": "outputs"})}, {"outputs": "1, x,,3"})
    caplog.clear()
    assert driver._register_declared_children() == {"output": 2}
    assert sorted(driver.list_children("output")) == [1, 3]
    assert _warnings(caplog) == [
        "[dev1] instances: output id 'x' from 'outputs' is not an integer; skipping"
    ]


def test_ids_from_string_ids_keep_every_non_blank_token():
    driver = _driver(
        {"zone": _type({"ids_from": "zones"}, {"type": "string"})},
        {"zones": " a , ,b"},
    )
    assert driver._register_declared_children() == {"zone": 2}
    assert sorted(driver.list_children("zone")) == ["a", "b"]


@pytest.mark.parametrize("ids_from", [None, "", 5])
def test_ids_from_that_names_no_field_registers_nothing(ids_from):
    driver = _driver({"output": _type({"ids_from": ids_from})})
    assert driver._register_declared_children() == {}


def test_child_types_that_are_not_maps_or_declare_no_instances_are_skipped():
    driver = _driver({
        "broken": "not a map",
        "free": _type("not a map"),
        "output": _type({"count": 2}),
    })
    assert driver._register_declared_children() == {"output": 2}


def test_ids_the_id_format_refuses_are_warned_and_not_counted(caplog):
    driver = _driver({
        "output": _type(
            {"count": 4, "label": "Out {id}"}, {"type": "integer", "min": 1, "max": 2},
        ),
    })
    caplog.clear()
    assert driver._register_declared_children() == {"output": 2}
    assert sorted(driver.list_children("output")) == [1, 2]
    assert driver.get_child_state("output", 1)["label"] == "Out 1"
    refused = [w for w in _warnings(caplog) if "could not register" in w]
    assert len(refused) == 2
    assert refused[0].startswith("[dev1] instances: could not register output 3: ")


def test_a_label_the_project_set_is_never_replaced_by_the_template():
    driver = _driver({"output": _type({"count": 2, "label": "Out {id}"})})
    driver.set_project_child_entities({"output": {"1": {"label": "Stage Left"}}})
    assert driver.get_child_state("output", 2)["label"] == "Out 2"
    assert driver.get_child_state("output", 1)["label"] == "Stage Left"
    assert driver.get_child_state("output", 1)["offline_reason"] == "parent_offline"


def test_a_child_no_longer_wanted_is_deregistered():
    driver = _driver({"output": _type({"count_from": "outputs"})}, {"outputs": 3})
    driver._register_declared_children()
    driver.config["outputs"] = 1
    assert driver._register_declared_children() == {"output": 1}
    assert driver.list_children("output") == [1]


def test_a_reconcile_that_fails_on_a_reported_count_is_logged_and_unlatched(caplog):
    driver = _driver({"output": _type({"count_from_state": "num_outputs"})})

    def broken() -> dict[str, int]:
        raise RuntimeError("roster broke")

    driver._register_declared_children = broken
    driver.set_state("num_outputs", 3)
    assert "Failed to reconcile children after 'num_outputs' update" in caplog.text
    assert driver._reconciling_roster is False
    assert driver.get_state("num_outputs") == 3


def test_a_roster_that_fails_when_the_project_hands_its_labels_over_is_logged(caplog):
    driver = _driver({"output": _type({"count": 2})})

    def broken() -> dict[str, int]:
        raise RuntimeError("roster broke")

    driver._register_declared_children = broken
    driver.set_project_child_entities({})
    assert "Failed to register declared children" in caplog.text


def test_a_state_write_that_sizes_no_roster_does_not_reconcile():
    driver = _driver({"output": _type({"count_from_state": "num_outputs"})})
    calls: list[int] = []
    driver._register_declared_children = lambda: calls.append(1) or {}
    driver.set_state("mode", "matrix")
    assert calls == []
    driver.set_state("num_outputs", 2)
    assert calls == [1]


async def test_refresh_without_any_instances_block_defers_to_the_base_driver():
    driver = _driver({"output": {"label": "Output", "state_variables": {}}})
    with pytest.raises(NotImplementedError):
        await driver.refresh_children()


async def test_refresh_with_an_instances_block_reconciles_from_config():
    driver = _driver({"output": _type({"count_from": "outputs"})}, {"outputs": 2})
    driver.config["outputs"] = 3
    assert await driver.refresh_children() == {"output": 3}


def test_a_reported_count_of_zero_is_not_yet_reported_and_the_config_field_counts():
    driver = _driver(
        {"output": _type({"count_from_state": "num_outputs", "count_from": "outputs"})},
        {"outputs": 2},
    )
    driver.set_state("num_outputs", 0)
    assert sorted(driver.list_children("output")) == [1, 2]
    assert driver._register_declared_children() == {"output": 2}
