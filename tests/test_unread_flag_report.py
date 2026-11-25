"""A boolean variable given a value that is neither true nor false is reported.

The words that mean true and false are ``drivers/truth_words``; a reply value
on neither list leaves its variable with no value rather than off
(``tests/test_truth_words.py``). That is said where the driver's other reply
problems are said: a warning naming the device in the log and a
``device.error`` with ``source: response_value``, once per variable for the
life of the driver, beside the contract observer's ``coercion_failure`` every
time. A blank field (a device that leaves a parameter empty) says nothing, so
it is not reported. On an invented Acme device.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import COERCION_FAILURE
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.reply_guard import REPLY_PROBLEM_SOURCES, RESPONSE_VALUE_SOURCE

_LOGGER = "openavc.drivers.configurable"


def _driver() -> tuple[Any, list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "transport": "tcp",
        "default_config": {"host": "127.0.0.1", "port": 0, "poll_interval": 0},
        "state_variables": {
            "power": {"type": "boolean", "label": "Power"},
            "mute": {"type": "boolean", "label": "Mute"},
        },
        "child_entity_types": {
            "input": {
                "label": "Input",
                "id_format": {"type": "integer"},
                "state_variables": {"phantom": {"type": "boolean", "label": "Phantom"}},
            },
        },
        "commands": {},
        "responses": [
            {"match": r"PWR=(.*)", "set": {"power": "$1"}},
            {"match": r"MUTE=(.*)", "set": {"mute": "$1"}},
            {"match": r"PH(\d)=(.*)", "child_set": [
                {"type": "input", "id": "$1", "state": {"phantom": "$2"}},
            ]},
        ],
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 5000}, state, events)
    errors: list[dict[str, Any]] = []

    async def on_error(_event: str, payload: dict[str, Any]) -> None:
        errors.append(payload)

    events.on("device.error.dev1", on_error)
    seen: list[tuple[str, dict[str, Any]]] = []
    driver.contract_observer = lambda kind, detail: seen.append((kind, detail))
    return driver, errors, seen


async def _emitted(driver: Any) -> None:
    await asyncio.gather(*list(driver._rule_failure_emits))


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.WARNING
        and "neither true nor false" in r.getMessage()
    ]


async def test_a_word_on_neither_list_is_reported_once_per_variable(caplog):
    driver, errors, seen = _driver()
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await driver.on_data_received(b"PWR=standby\r")
        await driver.on_data_received(b"PWR=sleep\r")
        await driver.on_data_received(b"MUTE=half\r")
        await _emitted(driver)
    assert driver.get_state("power") is None and driver.get_state("mute") is None
    warnings = _warnings(caplog)
    assert len(warnings) == 2
    assert warnings[0] == (
        '[dev1] The device sent "standby" for power, which is neither true nor '
        'false, so power has no value. Map "standby" in the driver\'s response rule.'
    )
    assert [e["source"] for e in errors] == [RESPONSE_VALUE_SOURCE] * 2
    assert errors[1]["error"].startswith('The device sent "half" for mute')
    # The observer still hears every one.
    assert [d["raw"] for k, d in seen if k == COERCION_FAILURE] == ["standby", "sleep", "half"]


async def test_a_child_variable_is_reported_too(caplog):
    driver, errors, _ = _driver()
    driver.register_child("input", 1)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await driver.on_data_received(b"PH1=maybe\r")
        await _emitted(driver)
    assert len(_warnings(caplog)) == 1 and "for input.phantom," in _warnings(caplog)[0]
    assert [e["source"] for e in errors] == [RESPONSE_VALUE_SOURCE]


async def test_a_blank_field_is_no_value_and_says_nothing(caplog):
    driver, errors, _ = _driver()
    await driver.on_data_received(b"PWR=on\r")
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await driver.on_data_received(b"PWR=\r")
        await _emitted(driver)
    assert driver.get_state("power") is None
    assert _warnings(caplog) == [] and errors == []


async def test_a_word_that_means_true_or_false_says_nothing(caplog):
    driver, errors, _ = _driver()
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for line in (b"PWR=Enabled\r", b"PWR=disabled\r", b"PWR=01\r", b"PWR= off \r"):
            await driver.on_data_received(line)
        await _emitted(driver)
    assert driver.get_state("power") is False
    assert _warnings(caplog) == [] and errors == []


def test_the_audit_counts_it_as_the_drivers_reading_not_the_device_refusing():
    assert RESPONSE_VALUE_SOURCE in REPLY_PROBLEM_SOURCES
