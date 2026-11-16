"""The fault code a driver writes on a child, at the checker and at runtime.

A child's ``offline_reason`` holds one of four codes. Three are a driver's to
assert (``not_fitted``, ``not_responding``, ``service_fault``); the fourth,
``parent_offline``, is the platform's: it marks every child in service when the
device becomes unreachable and puts exactly those back when it returns.

Where a driver is written (Validate, the Builder's save, ``drivers.check``,
the AI's driver tool, catalog CI) a YAML child that writes ``parent_offline``,
or a code outside the vocabulary, is refused, naming the three codes. Where a
driver is loaded the same sentence is a notice and the driver loads. At
runtime a driver's write of ``parent_offline`` is dropped at every door it
writes a child through, and any other code is written as given: a misspelt
code still shows the child is in trouble.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from openavc.core.connection_fault import (
    CHILD_NOT_RESPONDING,
    CHILD_PARENT_OFFLINE,
    CHILD_SERVICE_FAULT,
)
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import (
    check_driver_definition,
    validate_driver_definition,
)
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class

CODES = 'write not_fitted, not_responding or service_fault, or "" for none'
PARENT = "the code the platform writes while the device is unreachable"


def _driver(*responses: Any, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"status": {"send": "ST?\r"}},
        "child_entity_types": {
            "output": {
                "label": "Output",
                "instances": {"count": 4},
                "state_variables": {},
            },
        },
        "responses": list(responses),
    }
    body.update(extra)
    return body


def _down(reason: Any) -> dict[str, Any]:
    """A text rule marking one output down with ``reason``."""
    return {
        "match": r"OUT(\d+) (\w+)",
        "child_set": [
            {"type": "output", "id": "$1", "state": {"online": False, "offline_reason": reason}}
        ],
    }


def _strict(definition: dict[str, Any]) -> list[str]:
    return list(validate_driver_definition(definition, strict=True))


# --- what the author's doors refuse ----------------------------------------------


def test_a_child_writing_parent_offline_is_refused_naming_the_three_codes() -> None:
    assert _strict(_driver(_down("parent_offline"))) == [
        f"Response 0: child_set[0]: state 'offline_reason' writes 'parent_offline', "
        f"{PARENT}; {CODES}"
    ]


def test_a_misspelt_code_is_refused_with_the_nearest_code() -> None:
    assert _strict(_driver(_down("not_respondng"))) == [
        "Response 0: child_set[0]: state 'offline_reason' writes 'not_respondng', "
        "which is not a child fault code (did you mean 'not_responding'?); "
        f"{CODES}"
    ]


def test_a_value_that_is_not_text_is_stored_as_text_and_refused() -> None:
    """The runtime stores 3 as '3', a code nothing matches."""
    errors = _strict(_driver(_down(3)))
    assert errors == [
        "Response 0: child_set[0]: state 'offline_reason' writes 3, which is not "
        f"a child fault code; {CODES}"
    ]


def test_each_target_of_a_value_map_is_read() -> None:
    reason = {"group": 2, "map": {"UP": "", "DOWN": "not_responding", "LOST": "parent_offline"}}
    assert _strict(_driver(_down(reason))) == [
        "Response 0: child_set[0]: state 'offline_reason' maps 'LOST' to "
        f"'parent_offline', {PARENT}; {CODES}"
    ]


def test_a_long_form_value_is_read() -> None:
    assert _strict(_driver(_down({"value": "fault"}))) == [
        "Response 0: child_set[0]: state 'offline_reason' writes 'fault', which is "
        f"not a child fault code; {CODES}"
    ]


def test_a_long_form_value_its_map_covers_is_read_as_its_target() -> None:
    reason = {"value": "1", "map": {"1": "service_fault"}}
    assert _strict(_driver(_down(reason))) == []


def test_a_child_list_s_state_is_read() -> None:
    rule = {
        "match": r"DOWN=(.*)",
        "child_list": {
            "type": "output",
            "element": r"(\d+)",
            "id": "$1",
            "state": {"online": False, "offline_reason": "parent_offline"},
        },
    }
    assert _strict(_driver(rule)) == [
        f"Response 0: child_list: state 'offline_reason' writes 'parent_offline', "
        f"{PARENT}; {CODES}"
    ]


def test_an_osc_child_s_argument_map_is_read() -> None:
    rule = {
        "address": "/out/*/fault",
        "child_set": [
            {
                "type": "output",
                "id": {"segment": 1},
                "state": {"offline_reason": {"arg": 0, "map": {"1": "not_responding", "2": "lost"}}},
            }
        ],
    }
    definition = _driver(rule, transport="osc", commands={"status": {"address": "/status"}})
    assert _strict(definition) == [
        "Response 0: child_set[0]: state 'offline_reason' maps '2' to 'lost', which "
        f"is not a child fault code; {CODES}"
    ]


def test_a_json_child_s_spec_map_is_read_and_its_path_is_not() -> None:
    rule = {
        "json": True,
        "child_set": [
            {
                "type": "output",
                "id": 1,
                "state": {"offline_reason": {"key": "fault", "map": {"0": "", "1": "parent_offline"}}},
            },
            {"type": "output", "id": 2, "state": {"offline_reason": "outputs.2.fault"}},
        ],
    }
    definition = _driver(rule, transport="http", commands={"status": {"method": "GET", "path": "/"}})
    assert _strict(definition) == [
        "Response 0: child_set[0]: state 'offline_reason' maps '1' to "
        f"'parent_offline', {PARENT}; {CODES}"
    ]


@pytest.mark.parametrize(
    "reason",
    [
        "",  # nothing claimed
        None,
        "not_fitted",
        "not_responding",
        "service_fault",
        "$2",  # read off the wire
        {"group": 2},
        {"group": 2, "map": {"UP": "", "DOWN": "service_fault"}},
    ],
)
def test_nothing_claimed_a_driver_code_and_a_capture_are_not_refused(reason: Any) -> None:
    assert _strict(_driver(_down(reason))) == []


def test_the_refusal_is_tagged_with_its_rule() -> None:
    errors = validate_driver_definition(
        _driver({"match": "A"}, _down("parent_offline")), strict=True
    )
    assert errors.tags[: len(errors)] == ["responses[1]"]


# --- where a driver is loaded ------------------------------------------------------


def test_at_load_the_same_sentence_is_a_notice_and_the_driver_loads() -> None:
    verdict = check_driver_definition(_driver(_down("parent_offline")))
    assert verdict.errors == []
    assert verdict.notices == [
        f"Response 0: child_set[0]: state 'offline_reason' writes 'parent_offline', "
        f"{PARENT}; {CODES}"
    ]


# --- at runtime: a YAML child ------------------------------------------------------


def _running(reason: Any):
    cls = create_configurable_driver_class(_driver(_down(reason)))
    state = StateStore()
    state.set_event_bus(EventBus())
    drv = cls("wall", {}, state, EventBus())
    drv.set_project_child_entities({})
    drv.set_state("connected", True)
    return drv


def _presence(drv: BaseDriver, ctype: str, lid: int) -> tuple[Any, Any]:
    prefix = f"device.{drv.device_id}.{ctype}.{lid}"
    return drv.state.get(f"{prefix}.online"), drv.state.get(f"{prefix}.offline_reason")


async def test_a_yaml_child_the_device_reports_down_stays_down_across_a_reconnect() -> None:
    """The finding's run: the driver marks output 3 down claiming
    parent_offline. The claim is dropped, so the platform's return does not
    put the output back in service against what the device said."""
    drv = _running("parent_offline")
    await drv.on_data_received(b"OUT3 DOWN")
    assert _presence(drv, "output", 3) == (False, None)

    drv.set_state("connected", False)
    drv.set_state("connected", True)

    assert _presence(drv, "output", 3) == (False, None)


async def test_a_yaml_child_s_misspelt_code_is_written_as_given() -> None:
    drv = _running("not_respondng")
    await drv.on_data_received(b"OUT3 DOWN")
    assert _presence(drv, "output", 3) == (False, "not_respondng")


# --- at runtime: every door a driver writes a child through ---------------------


class _Matrix(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_matrix",
        "name": "Acme Matrix",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "child_entity_types": {
            "output": {
                "label": "Output",
                "id_format": {"type": "integer", "min": 1, "max": 8},
                "state_variables": {"source": {"type": "integer"}},
            },
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _matrix() -> _Matrix:
    drv = _Matrix(device_id="mtx", config={}, state=StateStore(), events=EventBus())
    drv.set_state("connected", True)
    return drv


_CLAIM = {"online": False, "offline_reason": CHILD_PARENT_OFFLINE}


@pytest.mark.parametrize(
    "door",
    [
        lambda drv: drv.set_child_state("output", 1, "offline_reason", CHILD_PARENT_OFFLINE),
        lambda drv: drv.set_child_state_batch("output", 1, dict(_CLAIM)),
        lambda drv: drv.set_children_state_batch([("output", 1, dict(_CLAIM))]),
    ],
    ids=["set_child_state", "set_child_state_batch", "set_children_state_batch"],
)
def test_a_driver_s_parent_offline_is_dropped_at_each_write_door(door) -> None:
    drv = _matrix()
    drv.register_child("output", 1)
    drv.register_child("output", 2)

    door(drv)

    assert _presence(drv, "output", 1)[1] is None
    # Its neighbour pins that the drop is the claim's alone: the platform's
    # own mark still lands when the device becomes unreachable.
    drv.set_state("connected", False)
    assert _presence(drv, "output", 2) == (False, CHILD_PARENT_OFFLINE)


def test_a_write_that_is_only_the_claim_writes_nothing() -> None:
    drv = _matrix()
    drv.register_child("output", 1)
    changes: list[Any] = []
    drv.state.subscribe_bulk("device.mtx.output.*", changes.append)
    drv.set_child_state_batch("output", 1, {"offline_reason": CHILD_PARENT_OFFLINE})
    drv.set_children_state_batch([("output", 1, {"offline_reason": CHILD_PARENT_OFFLINE})])
    assert changes == []
    assert _presence(drv, "output", 1) == (True, None)


def test_the_rest_of_a_batch_carrying_parent_offline_is_written() -> None:
    drv = _matrix()
    drv.register_child("output", 1)
    drv.set_child_state_batch("output", 1, {**_CLAIM, "source": 4})
    assert _presence(drv, "output", 1) == (False, None)
    assert drv.state.get("device.mtx.output.1.source") == 4


def test_a_driver_s_parent_offline_is_dropped_at_registration() -> None:
    drv = _matrix()
    drv.register_child("output", 1, initial_state=dict(_CLAIM))
    assert _presence(drv, "output", 1) == (False, None)


def test_a_code_the_child_holds_is_kept_when_the_driver_claims_parent_offline() -> None:
    drv = _matrix()
    drv.register_child("output", 1)
    drv.set_child_state_batch("output", 1, drv.child_fault(CHILD_NOT_RESPONDING))
    drv.set_child_state("output", 1, "offline_reason", CHILD_PARENT_OFFLINE)
    assert _presence(drv, "output", 1) == (False, CHILD_NOT_RESPONDING)


def test_any_other_code_is_written_as_given() -> None:
    drv = _matrix()
    drv.register_child("output", 1)
    drv.set_child_state_batch("output", 1, {"online": False, "offline_reason": "wedged"})
    assert _presence(drv, "output", 1) == (False, "wedged")
    drv.set_child_state("output", 1, "offline_reason", CHILD_SERVICE_FAULT)
    assert _presence(drv, "output", 1) == (False, CHILD_SERVICE_FAULT)


def test_the_drop_is_said_once_per_device(caplog) -> None:
    drv = _matrix()
    drv.register_child("output", 1)
    drv.register_child("output", 2)
    with caplog.at_level(logging.WARNING):
        drv.set_child_state("output", 1, "offline_reason", CHILD_PARENT_OFFLINE)
        drv.set_child_state_batch("output", 2, dict(_CLAIM))
    said = [r.getMessage() for r in caplog.records if "parent_offline" in r.getMessage()]
    assert len(said) == 1
    assert said[0].startswith("[mtx] ")
