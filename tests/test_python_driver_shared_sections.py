"""A Python driver's DRIVER_INFO is held to the section checks both kinds of
driver share: each state variable, each child type, each command's parameter
aids and its ``sets:`` and ``query_for:``, and the push block. The checker and
the community catalog refuse what they find; a driver that loads or a product
that connects keeps working, and what is wrong is said instead.

A value the source reader could not see (a constant, a call, a comprehension)
is never judged, and neither is anything judged against a section it could
not read whole: one skip, named in what the checker prints.

Synthetic throughout: an invented ``acme_widget``, never a real product.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.declared_names import UNEVALUATED_KEY
from openavc.drivers.python_info import (
    UNEVALUATED,
    check_python_driver_info,
    python_driver_info_issues,
    python_driver_reference_skips,
)

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {"power_on": {"label": "On"}},
}


def info(**sections: Any) -> dict[str, Any]:
    return {**BASE, **sections}


def issues(**sections: Any) -> list[str]:
    return python_driver_info_issues(info(**sections))


def skips(**sections: Any) -> list[str]:
    return python_driver_reference_skips(info(**sections))


# --- the push block ------------------------------------------------------------


def test_a_mistyped_push_type_is_named() -> None:
    found = issues(push={"type": "tcp_listner", "port": 0})
    assert len(found) == 1
    assert found[0].startswith("push: missing or unknown 'type' (supported: ")


def test_a_push_key_the_type_does_not_take_is_named() -> None:
    found = issues(push={"type": "multicast", "group": "239.1.1.1", "port": 5000, "grop": 1})
    assert [m for m in found if m.startswith("push: unknown key(s): grop")]


def test_a_push_register_naming_no_command_is_named() -> None:
    found = issues(push={"type": "tcp_listener", "port": 0, "register": "arm"})
    assert "push: register command 'arm' is not declared in commands" in found


# --- a command's parameter aids ------------------------------------------------


def test_a_pattern_that_does_not_compile_is_named() -> None:
    found = issues(commands={"set": {"params": {"code": {"type": "string", "pattern": "(["}}}})
    assert [m for m in found if m.startswith("Command 'set' param 'code': pattern")]


def test_a_picker_following_a_param_that_is_not_there_is_named() -> None:
    params = {"level": {"type": "string", "options_from": {"param": "zone", "source": "child_schema"}}}
    found = issues(commands={"set": {"params": params}})
    assert "Command 'set' param 'level': options_from.param 'zone' is not a param of this command" in found


def test_bounds_the_wrong_way_round_are_named() -> None:
    found = issues(commands={"set": {"params": {"level": {"type": "integer", "min": 9, "max": 1}}}})
    assert found == ["Command 'set' param 'level': min (9) must be <= max (1)"]


# --- child types ----------------------------------------------------------------


def test_a_dotted_child_type_name_is_named() -> None:
    found = issues(child_entity_types={"out.put": {}})
    assert found == [
        "child_entity_types: type name 'out.put' must not contain dots (used as state key separator)"
    ]


def test_an_unknown_id_kind_is_named() -> None:
    found = issues(child_entity_types={"output": {"id_format": {"type": "uuid"}}})
    assert found == [
        "child_entity_types.output.id_format: unknown type 'uuid' (expected 'integer' or 'string')"
    ]


def test_a_child_variable_of_an_unknown_type_is_named_with_the_fix() -> None:
    found = issues(child_entity_types={"output": {"state_variables": {"on": {"type": "bool"}}}})
    assert found == [
        "child_entity_types.output.state_variables.on: unknown type 'bool'; use 'boolean'"
    ]


# --- state variables --------------------------------------------------------------


def test_a_state_variable_of_an_unknown_type_says_what_to_write() -> None:
    found = issues(state_variables={"relay": {"type": "bool", "label": "Relay"}})
    assert found == ["State variable 'relay': unknown type 'bool'; use 'boolean'"]


def test_a_type_with_no_near_name_lists_the_types() -> None:
    found = issues(state_variables={"relay": {"type": "double", "label": "Relay"}})
    assert found == [
        "State variable 'relay': unknown type 'double'; use one of 'string', 'integer', "
        "'number', 'boolean', 'enum', 'float'"
    ]


def test_a_state_variable_needs_a_label() -> None:
    assert issues(state_variables={"power": {"type": "boolean"}}) == [
        "State variable 'power': missing 'label'"
    ]


# --- sets: and query_for: ----------------------------------------------------------


def test_sets_naming_an_undeclared_variable_is_named() -> None:
    found = issues(commands={"power_on": {"sets": {"pwr": True}}})
    assert found == ["Command 'power_on': sets 'pwr' which is not a declared state variable"]


def test_query_for_naming_an_undeclared_variable_is_named() -> None:
    found = issues(commands={"get_power": {"query_for": "pwr"}})
    assert found == ["Command 'get_power': query_for 'pwr' is not a declared state variable"]


def test_a_well_formed_driver_has_nothing_to_say() -> None:
    clean = info(
        commands={
            "power_on": {"label": "On", "sets": {"power": True}},
            "get_power": {"label": "Get", "query_for": "power"},
        },
        child_entity_types={"output": {"id_format": {"type": "integer", "min": 1, "max": 4}}},
        push={"type": "multicast", "group": "239.1.1.1", "port": 5000},
    )
    assert python_driver_info_issues(clean) == []
    assert python_driver_reference_skips(clean) == []


# --- the one skip: a value the reader could not see is never judged ------------------


def test_a_computed_label_is_not_read_as_missing() -> None:
    sections = {"state_variables": {"power": {"type": "boolean", "label": UNEVALUATED}}}
    assert issues(**sections) == []
    assert skips(**sections) == [
        "state_variables.power (holds a value that could not be read from the source)"
    ]


def test_a_computed_push_type_is_not_read_as_missing() -> None:
    sections = {"push": {"type": UNEVALUATED, "port": 0}}
    assert issues(**sections) == []
    assert skips(**sections) == ["push (holds a value that could not be read from the source)"]


def test_a_computed_id_bound_is_not_judged_but_the_type_s_name_still_is() -> None:
    sections = {"child_entity_types": {"out.put": {"id_format": {"max": UNEVALUATED}}}}
    assert issues(**sections) == [
        "child_entity_types: type name 'out.put' must not contain dots (used as state key separator)"
    ]
    assert skips(**sections) == [
        "child_entity_types.out.put (holds a value that could not be read from the source)"
    ]


def test_a_whole_computed_section_is_never_iterated() -> None:
    sections = {"child_entity_types": UNEVALUATED, "state_variables": UNEVALUATED}
    assert issues(**sections) == []
    assert "state_variables, child_entity_types (could not be read from the source)" in skips(**sections)


def test_a_computed_actions_block_is_neither_refused_nor_counted() -> None:
    sections = {"commands": {"power_on": {"label": "On"}, UNEVALUATED_KEY: UNEVALUATED},
                "actions": UNEVALUATED, "quick_actions": UNEVALUATED}
    assert issues(**sections) == []
    assert "actions, quick_actions (could not be read from the source)" in skips(**sections)


def test_a_param_whose_own_bound_is_computed_leaves_its_siblings_judged() -> None:
    params = {
        "zone": {"type": "integer", "max": UNEVALUATED},
        "level": {"type": "integer", "min": 9, "max": 1},
    }
    assert issues(commands={"set": {"params": params}}) == [
        "Command 'set' param 'level': min (9) must be <= max (1)"
    ]
    assert skips(commands={"set": {"params": params}}) == [
        "commands.set.params.zone (holds a value that could not be read from the source)"
    ]


def test_a_picker_following_a_computed_sibling_is_not_judged() -> None:
    params = {
        "block": UNEVALUATED,
        "control": {"type": "string", "options_from": {"param": "block", "source": "child_schema"}},
    }
    assert issues(commands={"set": {"params": params}}) == []
    assert skips(commands={"set": {"params": params}}) == [
        "commands.set.params.block, commands.set.params.control "
        "(holds a value that could not be read from the source)"
    ]


def test_a_picker_following_a_param_that_may_be_merged_in_is_not_judged() -> None:
    params = {
        "control": {"type": "string", "options_from": {"param": "block", "source": "child_schema"}},
        UNEVALUATED_KEY: UNEVALUATED,
    }
    assert issues(commands={"set": {"params": params}}) == []


# --- the one skip: a reference into a section not read whole --------------------------


def test_sets_into_a_computed_child_type_is_not_judged() -> None:
    """A display's properties built by a function: every sets: addressed to
    it would otherwise read as undeclared."""
    sections = {
        "child_entity_types": {"display": {"state_variables": UNEVALUATED}},
        "commands": {
            "set_volume": {
                "params": {"display": {"type": "child_id", "child_type": "display"}},
                "sets": {"volume": "{display}"},
            },
        },
    }
    assert issues(**sections) == []
    assert (
        "commands.set_volume (names into child_entity_types, which could not be read whole "
        "from the source)"
    ) in skips(**sections)


def test_sets_into_computed_state_variables_is_not_judged() -> None:
    sections = {"state_variables": UNEVALUATED, "commands": {"power_on": {"sets": {"pwr": True}}}}
    assert issues(**sections) == []


def test_a_push_registering_into_merged_commands_is_not_judged() -> None:
    sections = {
        "commands": {"power_on": {"label": "On"}, UNEVALUATED_KEY: UNEVALUATED},
        "push": {"type": "tcp_listener", "port": 0, "register": "arm"},
    }
    assert issues(**sections) == []
    assert (
        "push (names into commands, which could not be read whole from the source)"
    ) in skips(**sections)


def test_routing_into_commands_built_at_runtime_is_not_judged() -> None:
    sections = {
        "commands": {},
        "routing": {"command": "route", "planes": [{"label": "Video", "route_property": "power"}]},
    }
    assert issues(**sections) == []
    assert skips(**sections) == [
        "routing (names into commands, which could not be read whole from the source)"
    ]


# --- a check that raises is the driver's message, never an exception ------------------


def test_a_value_a_check_cannot_read_is_a_message() -> None:
    found = issues(push={"type": ["multicast"]})
    assert found == [
        "push: could not be checked (unhashable type: 'list'). One of its values is not the "
        "kind it should be, such as a number or true/false where a name goes; correct it and "
        "check again"
    ]


# --- where it refuses and where it only says so ----------------------------------------


def test_the_shared_checks_refuse_only_at_the_author_s_doors() -> None:
    driver = info(
        push={"type": "tcp_listner", "port": 0},
        actions=[{"id": "go", "command": "nope"}],
    )
    at_load = check_python_driver_info(driver)
    assert at_load.errors == ["actions[0]: command 'nope' is not a declared command"]
    assert len(at_load.notices) == 1 and at_load.notices[0].startswith("push: missing or unknown")
    strict = check_python_driver_info(driver, strict=True)
    assert strict.errors == at_load.errors + at_load.notices
    assert strict.notices == []
    assert python_driver_info_issues(driver) == strict.errors


@pytest.mark.parametrize("value", [UNEVALUATED, ["a"], 5, "text"])
def test_a_section_of_another_kind_never_raises(value: Any) -> None:
    for section in ("state_variables", "child_entity_types", "commands", "push", "routing"):
        python_driver_info_issues(info(**{section: value}))
        python_driver_reference_skips(info(**{section: value}))
