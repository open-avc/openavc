"""A reply rule's flat targets and its ``only_when`` gate keys are read
against the state variables the driver declares.

Where a driver is written (Validate, the Builder's save, ``drivers.check``,
the AI's driver tool, catalog CI) a rule that writes a name the driver does
not declare, or gates on a bare name it does not declare, is refused, naming
the key and saying to declare it under ``state_variables``. Where a driver is
loaded the same sentence is a notice: the driver still loads. A key that names
another namespace (``var.``, ``device.$id.``), a child's dotted key and a key
the platform writes for every device are not judged. A device's own protocol
is not a driver file and is never refused here.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import (
    check_driver_definition,
    validate_driver_definition,
)
from openavc.drivers.configurable import create_configurable_driver_class


def _driver(*responses: Any, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"status": {"send": "ST?\r"}},
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
            "input": {"type": "string", "label": "Input"},
        },
        "responses": list(responses),
    }
    body.update(extra)
    return body


def _strict(definition: dict[str, Any]) -> list[str]:
    return list(validate_driver_definition(definition, strict=True))


# --- what the author's doors refuse ----------------------------------------------


def test_a_text_rule_s_set_target_it_does_not_declare_is_refused() -> None:
    errors = _strict(_driver({"match": r"PWR=(\w+)", "set": {"powr": "$1"}}))
    assert errors == [
        "Response 0: set 'powr' writes a state variable this driver does not "
        "declare (did you mean 'power'?); declare it under state_variables"
    ]


def test_a_mappings_target_it_does_not_declare_is_refused() -> None:
    rule = {"match": r"IN=(\w+)", "mappings": [{"group": 1, "state": "source"}]}
    assert _strict(_driver(rule)) == [
        "Response 0: mappings[0] writes 'source', a state variable this driver "
        "does not declare; declare it under state_variables"
    ]


def test_a_json_rule_s_set_target_it_does_not_declare_is_refused() -> None:
    rule = {"json": True, "set": {"power": "pwr", "volume": "vol"}}
    assert _strict(_driver(rule, transport="http", commands={"status": {"method": "GET", "path": "/"}})) == [
        "Response 0: set 'volume' writes a state variable this driver does not "
        "declare; declare it under state_variables"
    ]


def test_an_osc_rule_s_mappings_target_it_does_not_declare_is_refused() -> None:
    rule = {"address": "/main/fader", "mappings": [{"arg": 0, "state": "fader"}]}
    definition = _driver(rule, transport="osc", commands={"status": {"address": "/main/fader"}})
    assert _strict(definition) == [
        "Response 0: mappings[0] writes 'fader', a state variable this driver "
        "does not declare; declare it under state_variables"
    ]


def test_a_bare_gate_key_it_does_not_declare_is_refused() -> None:
    rule = {"match": r"IN=(\w+)", "set": {"input": "$1"}, "only_when": {"key": "powr", "value": "on"}}
    assert _strict(_driver(rule)) == [
        "Response 0: only_when key 'powr' is not a state variable this driver "
        "declares (did you mean 'power'?); declare it under state_variables, or "
        "write a full state key such as var.<name> or device.$id.<name>"
    ]


def test_each_bare_key_of_a_gate_group_is_read() -> None:
    gate = {
        "all": [{"key": "power", "value": "on"}, {"key": "mode"}],
        "any": [{"key": "zone_count", "operator": "falsy"}],
    }
    rule = {"match": r"IN=(\w+)", "set": {"input": "$1"}, "only_when": gate}
    errors = _strict(_driver(rule))
    assert [e.split(" is not")[0] for e in errors] == [
        "Response 0: only_when.any[0] key 'zone_count'",
        "Response 0: only_when.all[1] key 'mode'",
    ]


def test_the_refusal_is_tagged_with_its_rule() -> None:
    errors = validate_driver_definition(
        _driver({"match": "A"}, {"match": r"PWR=(\w+)", "set": {"powr": "$1"}}), strict=True
    )
    assert list(zip(errors, errors.tags))[0][1] == "responses[1]"


# --- what is not judged ------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "power",  # declared
        "var.room_mode",  # a project variable
        "device.$id.power",  # this device's own key, written in full
        "device.other.power",  # another device
        "zone.1.level",  # a child of this device, by its dotted key
        "connected",  # written by the platform for every device
        "offline_reason",
    ],
)
def test_a_gate_key_that_names_something_real_is_not_refused(key: str) -> None:
    rule = {"match": r"IN=(\w+)", "set": {"input": "$1"}, "only_when": {"key": key}}
    assert _strict(_driver(rule)) == []


def test_a_rule_writing_connected_is_not_refused() -> None:
    """The runtime takes a driver's write of ``connected`` without a word."""
    assert _strict(_driver({"match": r"LINK=(\w+)", "set": {"connected": "$1"}})) == []


def test_nothing_is_read_against_state_variables_of_the_wrong_shape() -> None:
    errors = _strict(_driver({"match": r"PWR=(\w+)", "set": {"powr": "$1"}}, state_variables=["power"]))
    assert not [e for e in errors if "declare it under state_variables" in e]


def test_a_target_that_is_not_text_is_left_to_the_other_checks() -> None:
    rule = {"match": r"IN=(\w+)", "mappings": [{"group": 1, "state": ["input"]}]}
    errors = _strict(_driver(rule))
    assert not [e for e in errors if "declare it under state_variables" in e]
    assert not [e for e in errors if "could not be checked" in e]


# --- where a driver is loaded ------------------------------------------------------


def test_at_load_the_same_sentence_is_a_notice_and_the_driver_loads() -> None:
    definition = _driver(
        {"match": r"PWR=(\w+)", "set": {"powr": "$1"}, "only_when": {"key": "mode"}}
    )
    verdict = check_driver_definition(definition)
    assert verdict.errors == []
    assert [n.split(" ")[2] for n in verdict.notices] == ["set", "only_when"]


# --- a device's own protocol --------------------------------------------------------


async def test_a_device_s_own_reply_writing_a_name_its_driver_lacks_still_works() -> None:
    """A device's own protocol is not a driver file: a reply it adds that
    writes a name the driver file does not declare is not refused, and the
    value is written."""
    generic = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "responses": [],
    }
    cls = create_configurable_driver_class(generic)
    state = StateStore()
    state.set_event_bus(EventBus())
    config = {"responses": [{"mode": "contains", "text": "OCC", "state": "occupied", "value": "yes"}]}
    driver = cls("acme_1", config, state, EventBus())
    await driver.on_data_received(b"OCC")
    assert driver.get_state("occupied") == "yes"
