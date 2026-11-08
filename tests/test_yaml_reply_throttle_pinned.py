"""What a held (throttled) reply write does when its window lapses.

A response rule with a throttle holds the newest write per scope and writes
it when the window ends. These pin the flush's edges nothing else reaches: a
flush whose hold was dropped first, a held child that is gone by the flush,
and a held child every property of which something else wrote since. On an
invented Acme device.
"""

from __future__ import annotations

from typing import Any

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class


def _driver() -> Any:
    definition: dict[str, Any] = {
        "id": "acme_meter",
        "name": "Acme Meter",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "default_config": {"host": "", "port": 5000},
        "config_schema": {},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "child_entity_types": {
            "channel": {
                "label": "Channel",
                "id_format": {"type": "integer", "min": 1, "max": 8},
                "state_variables": {"level": {"type": "integer", "label": "Level"}},
                "instances": {"count": 4},
            },
        },
        "commands": {},
        "responses": [],
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 5000}, state, events)
    driver._register_declared_children()
    return driver


def _spy_batches(driver: Any) -> list[tuple[str, Any, dict[str, Any]]]:
    calls: list[tuple[str, Any, dict[str, Any]]] = []
    real = driver.set_child_state_batch

    def spy(ctype: str, local_id: Any, updates: dict[str, Any]) -> None:
        calls.append((ctype, local_id, dict(updates)))
        real(ctype, local_id, updates)

    driver.set_child_state_batch = spy
    return calls


def _hold(driver: Any, tstate: dict[str, Any], scope: str, flat: list, children: list) -> None:
    """Fire once (stamped, applied by the caller), then hold the next write."""
    assert driver._throttle_hold(tstate, scope, [], []) is False
    assert driver._throttle_hold(tstate, scope, flat, children) is True
    tstate["timers"][scope].cancel()


async def test_a_flush_after_its_hold_was_dropped_writes_nothing_and_stamps_nothing():
    driver = _driver()
    tstate: dict[str, Any] = {"window": 10.0, "last": {}}
    _hold(driver, tstate, "", [("power", "on")], [])
    stamped = tstate["last"][""]
    driver._drop_held(tstate, "")

    driver._flush_held(tstate, "")

    assert tstate["last"][""] == stamped
    assert driver.get_state("power") is None
    assert tstate["pending"] == {}
    assert tstate["timers"] == {}


async def test_a_flush_stamps_the_scope_and_clears_its_timer():
    driver = _driver()
    tstate: dict[str, Any] = {"window": 10.0, "last": {}}
    _hold(driver, tstate, "", [("power", "on")], [])
    # Back-dated well past a clock tick: Windows' monotonic clock can read
    # the same value twice in a row, so a fresh stamp is not always larger.
    stamped = tstate["last"][""] - 60.0
    tstate["last"][""] = stamped

    driver._flush_held(tstate, "")

    assert driver.get_state("power") == "on"
    assert tstate["last"][""] > stamped
    assert tstate["pending"] == {}
    assert tstate["timers"] == {}


async def test_a_held_child_gone_by_the_flush_is_skipped_and_the_rest_land():
    driver = _driver()
    batches = _spy_batches(driver)
    tstate: dict[str, Any] = {"window": 10.0, "last": {}}
    _hold(
        driver, tstate, "s", [],
        [("channel", 1, {"level": 5}), ("channel", 2, {"level": 6})],
    )
    driver.deregister_child("channel", 1)

    driver._flush_held(tstate, "s")

    assert batches == [("channel", 2, {"level": 6})]
    assert driver.get_child_state("channel", 2)["level"] == 6
    assert not driver.is_child_registered("channel", 1)


async def test_a_held_child_whose_every_prop_changed_since_is_not_written():
    driver = _driver()
    tstate: dict[str, Any] = {"window": 10.0, "last": {}}
    _hold(driver, tstate, "s", [], [("channel", 3, {"level": 5})])
    driver.set_child_state_batch("channel", 3, {"level": 9})
    batches = _spy_batches(driver)

    driver._flush_held(tstate, "s")

    assert batches == []
    assert driver.get_child_state("channel", 3)["level"] == 9


async def test_a_flat_key_written_since_the_hold_keeps_the_newer_value():
    driver = _driver()
    tstate: dict[str, Any] = {"window": 10.0, "last": {}}
    driver.set_state("power", "standby")
    _hold(driver, tstate, "", [("power", "on")], [])
    driver.set_state("power", "off")

    driver._flush_held(tstate, "")

    assert driver.get_state("power") == "off"


async def test_a_flush_that_fails_part_way_is_logged_and_keeps_the_stamp(caplog):
    driver = _driver()
    tstate: dict[str, Any] = {"window": 10.0, "last": {}}
    _hold(driver, tstate, "", [("power", "on")], [])

    def broken(key: str, value: Any) -> None:
        raise RuntimeError("store gone")

    driver.set_state = broken
    driver._flush_held(tstate, "")

    assert "Writing a throttled response failed" in caplog.text
    assert tstate["pending"] == {}


async def test_a_fire_after_the_window_drops_a_hold_still_waiting_for_its_timer():
    driver = _driver()
    tstate: dict[str, Any] = {"window": 10.0, "last": {}}
    assert driver._throttle_hold(tstate, "", [], []) is False
    assert driver._throttle_hold(tstate, "", [("power", "on")], []) is True
    timer = tstate["timers"][""]
    tstate["last"][""] -= 20.0  # the window has lapsed; the timer has not run yet

    assert driver._throttle_hold(tstate, "", [("power", "off")], []) is False

    assert timer.cancelled()
    assert tstate["pending"] == {}
    assert tstate["timers"] == {}


async def test_closing_the_session_drops_every_hold_in_every_rule_table():
    definition: dict[str, Any] = {
        "id": "acme_meter_json",
        "name": "Acme Meter",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "default_config": {"host": "", "port": 5000},
        "config_schema": {},
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
            "level": {"type": "integer", "label": "Level"},
        },
        "commands": {},
        "responses": [
            {"match": r"PWR=(\w+)", "set": {"power": "$1"}, "throttle": 5},
            {"json": True, "set": {"level": {"key": "level"}}, "throttle": 5},
        ],
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = create_configurable_driver_class(definition)(
        "dev1", {"host": "127.0.0.1", "port": 5000}, state, events,
    )
    text_state = driver._compiled_responses[0][3]
    json_state = driver._json_responses[0][1]
    held = []
    for tstate in (text_state, json_state):
        assert driver._throttle_hold(tstate, "", [], []) is False
        assert driver._throttle_hold(tstate, "", [("power", "on")], []) is True
        held.append(tstate["timers"][""])

    driver._drop_all_held()

    assert all(timer.cancelled() for timer in held)
    for tstate in (text_state, json_state):
        assert tstate["pending"] == {}
        assert tstate["timers"] == {}
