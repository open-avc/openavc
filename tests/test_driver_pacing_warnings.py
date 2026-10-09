"""The validator's two runtime-shaped warnings for a YAML driver.

- A poll cycle longer than the poll interval, at the driver's default config
  and at the largest child roster it accepts.
- A device that could stop answering with nothing to notice it: no liveness
  block, and either no poll or a transport the poll loop's no-reply check does
  not watch.

Both read the same constants the runtime acts on (spec.py), and the line count
is pinned against the runtime's own expansion so the two cannot drift.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import configurable
from openavc.drivers.avcdriver_semantic import (
    _poll_lines,
    validate_driver_warnings,
)
from openavc.drivers.base import _SILENCE_CHECK_TRANSPORTS
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.spec import (
    DEFAULT_LINE_GAP_S,
    SILENCE_CHECK_TRANSPORTS,
)

RULE = [{"match": r"PWR=(\w+)", "set": {"power": "$1"}}]


def _d(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"noop": {"send": "NOOP\r"}},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
    }
    base.update(over)
    return base


def _matrix(**over: Any) -> dict[str, Any]:
    """An Acme matrix whose outputs are sized by a config field."""
    definition = _d(
        default_config={"poll_interval": 10, "output_count": 4},
        config_schema={
            "output_count": {"type": "integer", "label": "Output Count", "max": 64},
        },
        child_entity_types={
            "output": {
                "label": "Output",
                "id_format": {"type": "integer", "min": 1, "max": 128},
                "state_variables": {"input": {"type": "integer"}},
                "instances": {"count_from": "output_count"},
            },
        },
        polling={
            "queries": [
                "STATUS\r",
                {"each_child": "output", "send": "ROUTE? {child_id}\r"},
                {"each_child": "output", "send": "MUTE? {child_id}\r"},
            ],
        },
        responses=RULE,
        liveness={"send": "PING\r", "expect": "PONG"},
    )
    definition.update(over)
    return definition


def _poll_warnings(definition: dict[str, Any]) -> list[str]:
    return [w for w in validate_driver_warnings(definition) if "poll sends" in w]


def _silence_warnings(definition: dict[str, Any]) -> list[str]:
    return [w for w in validate_driver_warnings(definition) if w.startswith("Nothing notices")]


# --- one source for the numbers -------------------------------------------


def test_the_runtime_and_the_validator_read_the_same_numbers():
    assert configurable.DEFAULT_LINE_GAP_S is DEFAULT_LINE_GAP_S
    assert _SILENCE_CHECK_TRANSPORTS == frozenset(SILENCE_CHECK_TRANSPORTS)


@pytest.mark.parametrize(
    "config",
    [
        {"output_count": 4},
        {"output_count": 9, "meters": True},
        {"output_count": 0},
        {},
    ],
)
def test_lines_are_counted_the_way_the_runtime_expands_them(config: dict[str, Any]):
    definition = _matrix()
    definition["polling"]["queries"].append(
        {"each_child": "output", "send": "METER? {child_id}\r", "when": "meters"}
    )
    definition["polling"]["queries"].append({"send": "TEMP?\r", "when": "meters"})
    cls = create_configurable_driver_class(definition)
    driver = cls("acme1", dict(config), StateStore(), EventBus())
    driver._register_declared_children()
    expanded = sum(
        len(driver._expand_query(q)) for q in definition["polling"]["queries"]
    )
    counted = _poll_lines(
        definition["polling"]["queries"], definition["child_entity_types"], config
    )
    assert counted == expanded


# --- a poll longer than its interval ---------------------------------------


def test_an_unpaced_tcp_poll_longer_than_its_interval_warns():
    # 30 lines at the 50 ms default spacing: 1.5 s against a 1 s interval.
    definition = _d(
        default_config={"poll_interval": 1},
        polling={"queries": [f"Q{n}\r" for n in range(30)]},
    )
    [warning] = _poll_warnings(definition)
    assert warning.startswith("Each poll sends 30 lines, 1.5 s at 50 ms apart")
    assert "the spacing used when no inter_command_delay is set" in warning
    assert "longer than the 1 s poll interval" in warning
    assert "refreshes every 2.5 s" in warning


def test_a_poll_inside_its_interval_says_nothing():
    definition = _d(
        default_config={"poll_interval": 2},
        polling={"queries": [f"Q{n}\r" for n in range(30)]},
    )
    assert _poll_warnings(definition) == []


def test_the_declared_delay_is_the_spacing_when_set():
    # 30 lines at 100 ms: 3 s against a 2 s interval, and no default-spacing note.
    definition = _d(
        default_config={"poll_interval": 2, "inter_command_delay": 0.1},
        polling={"queries": [f"Q{n}\r" for n in range(30)]},
    )
    [warning] = _poll_warnings(definition)
    assert "30 lines, 3 s at 100 ms apart, longer than the 2 s" in warning
    assert "no inter_command_delay" not in warning


def test_the_largest_roster_is_checked_when_the_default_fits():
    # 4 outputs: 9 lines, 0.9 s at 100 ms. At the field's max of 64: 129 lines, 12.9 s.
    definition = _matrix(
        default_config={"poll_interval": 10, "output_count": 4, "inter_command_delay": 0.1},
    )
    [warning] = _poll_warnings(definition)
    assert warning.startswith(
        "At the largest roster this driver accepts (Output Count 64), "
        "each poll sends 129 lines, 12.9 s at 100 ms apart"
    )


def test_a_count_field_with_no_max_takes_the_child_ids_max():
    definition = _matrix(
        default_config={"poll_interval": 10, "output_count": 4, "inter_command_delay": 0.1},
        config_schema={"output_count": {"type": "integer", "label": "Output Count"}},
    )
    [warning] = _poll_warnings(definition)
    assert "(Output Count 128)" in warning
    assert "257 lines" in warning


def test_a_roster_with_no_bound_is_not_guessed():
    definition = _matrix(
        default_config={"poll_interval": 10, "output_count": 4, "inter_command_delay": 0.1},
        config_schema={"output_count": {"type": "integer"}},
    )
    definition["child_entity_types"]["output"]["id_format"] = {"type": "integer"}
    assert _poll_warnings(definition) == []


def test_a_gated_query_counts_only_when_its_field_is_on():
    queries = [{"send": f"Q{n}\r", "when": "verbose"} for n in range(30)]
    off = _d(default_config={"poll_interval": 1}, polling={"queries": queries})
    on = _d(default_config={"poll_interval": 1, "verbose": True}, polling={"queries": queries})
    assert _poll_warnings(off) == []
    assert len(_poll_warnings(on)) == 1


@pytest.mark.parametrize(
    "transport,config",
    [
        ("udp", {"poll_interval": 1}),
        ("osc", {"poll_interval": 1, "inter_command_delay": 0.1}),
        ("tcp", {"poll_interval": 0}),
        ("tcp", {}),
    ],
    ids=["udp-with-no-delay", "osc", "polling-off", "no-interval"],
)
def test_no_spacing_or_no_polling_says_nothing(transport: str, config: dict[str, Any]):
    definition = _d(
        transport=transport,
        default_config=config,
        polling={"queries": [f"Q{n}\r" for n in range(100)]},
    )
    assert _poll_warnings(definition) == []


def test_http_counts_its_delay():
    definition = _d(
        transport="http",
        default_config={"poll_interval": 1, "inter_command_delay": 0.2},
        polling={"queries": [f"q{n}" for n in range(6)]},
    )
    [warning] = _poll_warnings(definition)
    assert "6 lines, 1.2 s at 200 ms apart" in warning


# --- silence nothing notices -------------------------------------------------


def test_a_polled_tcp_driver_is_left_to_the_no_reply_check():
    definition = _d(
        default_config={"poll_interval": 10},
        polling={"queries": ["PWR?\r"]},
        responses=RULE,
    )
    assert _silence_warnings(definition) == []


def test_a_polled_serial_driver_with_no_liveness_warns():
    definition = _d(
        transport="serial",
        default_config={"poll_interval": 10},
        polling={"queries": ["PWR?\r"]},
        responses=RULE,
    )
    [warning] = _silence_warnings(definition)
    assert warning.startswith(
        "Nothing notices if the device stops answering over serial: "
        "polls over serial are not checked for a reply, and there is no liveness block"
    )
    assert warning.endswith(
        "Add a liveness block that asks something the device answers in every state."
    )


def test_a_tcp_driver_that_never_polls_warns():
    definition = _d(responses=RULE)
    [warning] = _silence_warnings(definition)
    assert "over tcp: it sends no poll at its default config" in warning


def test_only_the_serial_alternative_is_named_on_a_dual_transport_driver():
    definition = _d(
        transports=["tcp", "serial"],
        default_config={"poll_interval": 10},
        polling={"queries": ["PWR?\r"]},
        responses=RULE,
    )
    [warning] = _silence_warnings(definition)
    assert "over serial:" in warning
    assert "over tcp" not in warning


@pytest.mark.parametrize(
    "over",
    [
        {"liveness": {"send": "PING\r", "expect": "PONG"}},
        {"responses": []},
        {"transport": "http"},
    ],
    ids=["has-liveness", "reads-nothing", "http"],
)
def test_nothing_to_say(over: dict[str, Any]):
    definition = _d(transport="serial", responses=RULE)
    definition.update(over)
    assert _silence_warnings(definition) == []
