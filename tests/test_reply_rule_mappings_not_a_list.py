"""A reply rule whose ``mappings:`` is not a list reads as a rule with none.

YAML reads a ``mappings:`` key with nothing under it as null. The loader names
the field ("must be a list") and loads the driver anyway, so the compile reads
such a rule as one with no ``mappings:`` and its ``set:`` is read, in a text
rule and in a ``json: true`` rule alike (an OSC rule, which reads only
``mappings:``, compiles to none), instead of raising out of the driver's
constructor. Invented Acme drivers throughout.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.compiled_protocol import (
    build_json_mappings,
    build_regex_mappings,
    compile_driver,
)
from openavc.drivers.configurable import create_configurable_driver_class

_STATE_VARS: dict[str, Any] = {"power": {"type": "boolean", "label": "Power"}}

#: What YAML can hand the compile for a ``mappings:`` that is not a list.
_NOT_A_LIST = [None, {"power": "p"}, "power", 3]


def _driver(state, events, transport: str, rule: dict[str, Any]):
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": transport,
        "delimiter": "\\r\\n",
        "state_variables": _STATE_VARS,
        "commands": {},
        "responses": [rule],
    }
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    return cls("acme1", {"host": "127.0.0.1", "port": 23}, state, events)


@pytest.mark.parametrize("written", _NOT_A_LIST)
def test_a_text_rule_reads_its_set_beside_a_mappings_that_is_not_a_list(written):
    rule = {"match": "^PWR (\\d)$", "mappings": written, "set": {"power": "$1"}}
    assert build_regex_mappings(rule, _STATE_VARS) == [
        {"group": 1, "state": "power", "type": "boolean"}
    ]


@pytest.mark.parametrize("written", _NOT_A_LIST)
def test_a_json_rule_reads_its_set_beside_a_mappings_that_is_not_a_list(written):
    rule = {"json": True, "mappings": written, "set": {"power": "p"}}
    assert build_json_mappings(rule, _STATE_VARS) == [
        {"state": "power", "key": "p", "type": "boolean"}
    ]


@pytest.mark.parametrize("written", _NOT_A_LIST)
def test_an_osc_rule_with_a_mappings_that_is_not_a_list_compiles_to_no_mappings(written):
    definition = {
        "state_variables": _STATE_VARS,
        "responses": [{"address": "/acme/power", "mappings": written}],
    }
    rule = compile_driver(definition, {}, device_id="acme1").osc_responses[0]
    assert rule[0] == "/acme/power" and rule[1] == []


async def test_a_device_on_a_text_rule_with_an_empty_mappings_key_starts_and_reads(
    state, events
):
    rule = {"match": "^PWR (\\d)$", "mappings": None, "set": {"power": "$1"}}
    driver = _driver(state, events, "tcp", rule)
    await driver.on_data_received(b"PWR 1")
    assert driver.get_state("power") is True


async def test_a_device_on_a_json_rule_with_an_empty_mappings_key_starts_and_reads(
    state, events
):
    rule = {"json": True, "mappings": None, "set": {"power": "p"}}
    driver = _driver(state, events, "http", rule)
    await driver.on_data_received(b'{"p": true}')
    assert driver.get_state("power") is True
