"""References inside a driver definition that must resolve to something in it.

Every gate the platform had validated what a driver *declares*. These are the
rules that check whether its declarations agree with each other: a command
parameter naming a child type that exists, a Quick Action naming a command
that exists, a parameter's bounds sitting inside the range its child type
declares. All three used to pass every gate and surface only when a user
pressed the button.

The rules are shared by both authoring surfaces on purpose — a YAML driver was
no better covered than a Python one, and a dangling reference now reads the
same sentence whichever kind of driver declared it. Each test below therefore
asserts against both, from the one function, so the two cannot drift.

Synthetic throughout: an invented ``acme_widget`` and invented child types,
never a real product.
"""

from __future__ import annotations

import copy

import pytest

from openavc.drivers.avcdriver_semantic import (
    UNEVALUATED_KEY,
    child_param_reference_errors,
    routing_block_errors,
    validate_substitutions,
    validate_actions,
)
from openavc.drivers.python_info import (
    python_driver_info_issues,
    python_driver_reference_skips,
)


def _definition() -> dict:
    """A valid driver declaring two child types and commands addressing them."""
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {"power": {"type": "boolean"}},
        "child_entity_types": {
            "zone": {
                "id_format": {"type": "integer", "min": 1, "max": 8},
                "state_variables": {"level": {"type": "integer"}},
            },
            "block": {
                "id_format": {"type": "string"},
                "state_variables": {"gain": {"type": "number"}},
            },
        },
        "commands": {
            "set_zone_level": {
                "label": "Set Zone Level",
                "send": "ZL{zone} {level}\r",
                "params": {
                    "zone": {"type": "child_id", "child_type": "zone"},
                    "level": {"type": "integer", "min": 0, "max": 100},
                },
            },
            "set_block_gain": {
                "label": "Set Block Gain",
                "send": "BG{block} {gain}\r",
                "params": {
                    "block": {"type": "child_id", "child_type": "block"},
                    "gain": {"type": "number"},
                },
            },
        },
        "actions": [{"id": "set_zone_level", "kind": "command"}],
    }


def _errors(definition: dict) -> list[str]:
    errors, _ = child_param_reference_errors(definition)
    return errors


def _skips(definition: dict) -> list[str]:
    _, skipped = child_param_reference_errors(definition)
    return skipped


# --- a clean definition stays clean ------------------------------------------

def test_a_definition_whose_references_all_resolve_reports_nothing():
    assert _errors(_definition()) == []
    assert _skips(_definition()) == []
    assert validate_actions(_definition()) == []


# --- a command param naming a child type that does not exist -----------------

def test_a_dangling_child_type_is_reported():
    """The finding: a typo'd child_type passed every gate. At dispatch the
    runtime fell back to plain integer coercion, so the user was told the
    value they typed was wrong when the driver was."""
    definition = _definition()
    definition["commands"]["set_block_gain"]["params"]["block"]["child_type"] = "blok"

    errors = _errors(definition)
    assert len(errors) == 1
    assert "commands.set_block_gain.params.block" in errors[0]
    assert "'blok' is not a declared child_entity_type" in errors[0]
    # The message names what IS declared, so the fix does not need a doc.
    assert "block" in errors[0] and "zone" in errors[0]


def test_a_dangling_child_type_reads_the_same_on_the_python_surface():
    """One rule, one wording. A Python driver reaches it through python_info;
    a YAML one through validate_driver_definition. Neither has a copy."""
    definition = _definition()
    definition["commands"]["set_block_gain"]["params"]["block"]["child_type"] = "blok"

    from openavc.drivers.driver_loader import validate_driver_definition

    yaml_errors = [
        e for e in validate_driver_definition(definition, strict=False)
        if "blok" in e
    ]
    python_errors = [e for e in python_driver_info_issues(definition) if "blok" in e]
    assert yaml_errors == python_errors
    assert len(yaml_errors) == 1


def test_a_child_id_param_declaring_no_child_type_is_reported():
    definition = _definition()
    del definition["commands"]["set_zone_level"]["params"]["zone"]["child_type"]

    errors = _errors(definition)
    assert len(errors) == 1
    assert "must declare 'child_type'" in errors[0]


def test_a_driver_declaring_no_child_types_at_all_still_reports_the_reference():
    definition = _definition()
    del definition["child_entity_types"]

    errors = _errors(definition)
    assert len(errors) == 2
    assert all("the driver declares none" in e for e in errors)


@pytest.mark.parametrize("param_type", ["integer", "string", "enum"])
def test_only_child_id_params_are_checked(param_type):
    """A ``child_type`` on a param of another kind means nothing to the
    runtime, so it must not be validated as a reference."""
    definition = _definition()
    definition["commands"]["set_zone_level"]["params"]["zone"] = {
        "type": param_type, "child_type": "nonsense",
    }
    assert _errors(definition) == []


# --- a param's bounds against the range its child type declares --------------

def test_a_param_max_above_the_types_id_format_max_is_reported():
    definition = _definition()
    definition["commands"]["set_zone_level"]["params"]["zone"]["max"] = 99

    errors = _errors(definition)
    assert len(errors) == 1
    assert "max 99 is above" in errors[0]
    assert "child_entity_types.zone.id_format.max (8)" in errors[0]


def test_a_param_min_below_the_types_id_format_min_is_reported():
    definition = _definition()
    definition["commands"]["set_zone_level"]["params"]["zone"]["min"] = 0

    errors = _errors(definition)
    assert len(errors) == 1
    assert "min 0 is below" in errors[0]


def test_bounds_that_agree_with_the_id_format_report_nothing():
    definition = _definition()
    definition["commands"]["set_zone_level"]["params"]["zone"].update(
        {"min": 2, "max": 8},
    )
    assert _errors(definition) == []


def test_a_string_id_type_has_no_orderable_range_to_check():
    definition = _definition()
    definition["commands"]["set_block_gain"]["params"]["block"]["max"] = 500
    assert _errors(definition) == []


# --- computed target sets: skip that reference, never the driver -------------

def test_computed_commands_skips_the_param_checks_and_says_so():
    """P9.6's objection, preserved. A Python driver may build ``commands`` at
    runtime, and a check whose target set is unknown must report the gap
    rather than a dangling reference it cannot see the target of."""
    definition = _definition()
    definition["commands"] = UNEVALUATED_KEY  # the reader's marker for a computed block

    assert _errors(definition) == []
    skips = _skips(definition)
    assert len(skips) == 1
    assert "commands is computed" in skips[0]


def test_computed_child_entity_types_skips_only_that_reference():
    definition = _definition()
    definition["child_entity_types"] = UNEVALUATED_KEY

    assert _errors(definition) == []
    assert any("child_entity_types is computed" in s for s in _skips(definition))


def test_partly_visible_commands_are_checked_as_far_as_they_are_readable():
    """A driver merging commands in from a module constant: the keys that ARE
    visible still get checked, and the gap is named rather than implied."""
    definition = _definition()
    definition["commands"][UNEVALUATED_KEY] = {"params": {}}
    definition["commands"]["set_block_gain"]["params"]["block"]["child_type"] = "blok"

    errors = _errors(definition)
    assert len(errors) == 1  # the readable half is still checked
    assert any("readable" in s for s in _skips(definition))


def test_computed_child_type_names_cannot_prove_a_reference_dangles():
    definition = _definition()
    definition["child_entity_types"][UNEVALUATED_KEY] = {}
    definition["commands"]["set_block_gain"]["params"]["block"]["child_type"] = "blok"

    assert _errors(definition) == []
    assert any("keys are computed" in s for s in _skips(definition))


# --- an action naming a command that does not exist --------------------------

def test_an_action_naming_no_command_is_reported():
    """The finding: the device page rendered a live green button labelled with
    the raw broken id, and clicking it 404'd."""
    definition = _definition()
    definition["actions"].append({"id": "query_evrything", "kind": "command"})

    errors = [e for e in validate_actions(definition) if "query_evrything" in e]
    assert len(errors) == 1
    assert "is not a declared command" in errors[0]


def test_the_python_surface_reaches_the_same_action_rule():
    definition = _definition()
    definition["actions"].append({"id": "query_evrything", "kind": "command"})

    shared = [e for e in validate_actions(definition) if "query_evrything" in e]
    through_python = [
        e for e in python_driver_info_issues(definition) if "query_evrything" in e
    ]
    assert through_python == shared


def test_an_action_pointing_at_a_command_by_name_resolves():
    definition = _definition()
    definition["actions"] = [
        {"id": "level_up", "kind": "command", "command": "set_zone_level"},
    ]
    assert validate_actions(definition) == []


def test_partly_visible_commands_do_not_make_a_valid_action_look_broken():
    """The regression this guards: the first run of this rule reported a
    shipped driver as broken because it merges its commands in from a module
    constant. The command was declared; the reader could not see it. A subset
    of the target set proves nothing about what is missing from it."""
    definition = _definition()
    definition["commands"][UNEVALUATED_KEY] = {"params": {}}
    definition["actions"].append({"id": "merged_in_at_runtime", "kind": "command"})
    definition["quick_actions"] = ["also_merged_in"]

    assert validate_actions(definition) == []
    skips = python_driver_reference_skips(definition)
    assert any("action/quick_action reference(s) into commands" in s for s in skips)


def test_quick_actions_naming_no_command_are_reported_when_commands_are_visible():
    definition = _definition()
    definition["quick_actions"] = ["set_zone_level", "no_such_command"]

    errors = [e for e in validate_actions(definition) if "no_such_command" in e]
    assert len(errors) == 1


# --- a device setting's state_key --------------------------------------------

def _with_setting(**sdef) -> dict:
    definition = _definition()
    body: dict = {"type": "boolean", "write": {"send": "PWR {value}\r"}}
    body.update(sdef)
    definition["device_settings"] = {"standby": body}
    return definition


def test_a_dangling_state_key_reads_the_same_on_both_surfaces():
    """A typo'd state_key loads fine and shows "(not set)" forever while the
    write still fires — a setting that looks like it works and never reads
    back. The rule was YAML-only; the Python surface reaches it now."""
    from openavc.drivers.driver_loader import validate_driver_definition

    definition = _with_setting(state_key="powr")
    yaml_errors = [
        e for e in validate_driver_definition(definition, strict=False)
        if "powr" in e
    ]
    python_errors = [e for e in python_driver_info_issues(definition) if "powr" in e]
    assert len(yaml_errors) == 1
    assert "is not a declared state variable" in yaml_errors[0]
    assert python_errors == yaml_errors


def test_a_state_key_defaults_to_the_setting_name():
    assert [e for e in python_driver_info_issues(_with_setting()) if "standby" in e]
    ok = _with_setting()
    ok["state_variables"]["standby"] = {"type": "boolean"}
    assert python_driver_info_issues(ok) == []


def test_computed_state_variables_skip_the_state_key_check():
    definition = _with_setting(state_key="powr")
    definition["state_variables"] = UNEVALUATED_KEY

    assert [e for e in python_driver_info_issues(definition) if "powr" in e] == []
    assert any(
        "device_settings state_key reference(s)" in s
        for s in python_driver_reference_skips(definition)
    )


# --- the shared rule is genuinely shared -------------------------------------

def test_the_python_path_carries_no_second_copy_of_the_action_rules():
    """python_info used to hand-roll its own actions checks — narrower than
    the shared ones (it never checked that a command exists) and worded
    differently, so the same broken driver was described one way by the
    catalog and another by the Builder. It delegates now."""
    definition = _definition()
    definition["actions"] = [
        {"id": "ok", "kind": "command", "command": "set_zone_level"},
        {"id": "ok", "kind": "command", "command": "set_zone_level"},  # duplicate id
        {"kind": "command"},                                            # no id
        {"id": "bad_kind", "kind": "nonsense"},
    ]
    shared = validate_actions(definition)
    through_python = python_driver_info_issues(definition)
    for message in shared:
        assert message in through_python, message
    # And the duplicate-id rule, which the hand-rolled copy never had at all.
    assert any("duplicate action id" in m for m in through_python)


def _routed(**routing) -> dict:
    """The shared driver, plus a routing declaration to check."""
    definition = _definition()
    definition["routing"] = {
        "destination_child_type": "zone",
        "command": "set_zone_level",
        "destination_param": "zone",
        "planes": [{"label": "Level", "route_property": "level"}],
        **routing,
    }
    return definition


def test_a_correct_routing_declaration_passes_on_both_surfaces():
    definition = _routed()
    assert routing_block_errors(definition) == []
    assert "routing" not in " ".join(python_driver_info_issues(definition))


@pytest.mark.parametrize("broken, names", [
    ({"destination_child_type": "zoen"}, "zoen"),
    ({"command": "set_zone_levl"}, "set_zone_levl"),
    ({"planes": [{"label": "Level", "route_property": "levl"}]}, "levl"),
])
def test_a_routing_declaration_that_names_nothing_is_refused_on_both(broken, names):
    """A Python driver's routing block used to be checked by nobody.

    The block names a child type, a property on it and a command, and it is
    read when somebody draws a panel rather than when the driver runs -- so a
    wrong name here surfaces as a matrix whose crosspoints never light, in a
    room, long after the driver was written. YAML got that checked from the
    day the block existed; a `.py` driver declaring the same thing got nothing,
    which is the asymmetry this file exists to prevent.
    """
    definition = _routed(**broken)
    shared = routing_block_errors(definition)
    assert shared, "the shared rule should refuse this"
    assert any(names in message for message in shared)
    through_python = python_driver_info_issues(definition)
    for message in shared:
        assert message in through_python, message


def test_a_block_whose_targets_are_computed_is_skipped_and_named():
    """Three shipped Python drivers build their commands and child properties
    at construction time, so a source reader sees an empty command map and a
    marker where the properties go. Every name in a perfectly correct block
    then reads as dangling, which is the same "not in a set we could not read"
    non-finding the sibling rules already decline to make -- and declining
    quietly would be worse, so the gap is named."""
    definition = _routed()
    definition["commands"] = {}          # built at connect, invisible here
    assert python_driver_info_issues(definition) == []
    (skip,) = [s for s in python_driver_reference_skips(definition) if "routing" in s]
    assert "1 routing plane(s)" in skip


def test_a_computed_child_property_set_also_skips():
    definition = _routed()
    definition["child_entity_types"]["zone"]["state_variables"] = UNEVALUATED_KEY
    assert python_driver_info_issues(definition) == []
    assert any("routing" in s for s in python_driver_reference_skips(definition))


def test_a_device_that_routes_itself_is_still_checked():
    """It names no child type, so nothing a computed roster could hide -- and
    skipping it would silence the one shape that has no other cover."""
    definition = _definition()
    definition["routing"] = {
        "command": "set_zone_levl",
        "planes": [{"label": "Video", "route_property": "power"}],
    }
    assert python_driver_reference_skips(definition) == []
    assert any("set_zone_levl" in m for m in python_driver_info_issues(definition))


def test_a_driver_that_declares_no_routing_is_not_asked_about_it():
    assert routing_block_errors(_definition()) == []
    assert python_driver_info_issues(_definition()) == []


def test_a_yaml_definition_never_produces_a_skip():
    """The computed-value marker is emitted only by the Python source reader.
    A YAML definition cannot contain one, so skip-and-print is inert there."""
    definition = _definition()
    assert _skips(definition) == []
    assert python_driver_reference_skips(definition) == []


def test_reference_checking_does_not_mutate_the_definition():
    definition = _definition()
    before = copy.deepcopy(definition)
    child_param_reference_errors(definition)
    validate_actions(definition)
    python_driver_info_issues(definition)
    assert definition == before


# ── {name} substitutions must resolve to something the driver declares ──
#
# The runtime leaves an unknown placeholder verbatim on purpose, so a JSON
# body full of braces cannot crash a send. That makes a typo invisible: the
# token travels to the device as literal text, the command appears to do
# nothing, and no gate said a word. These rules are the authoring half.


def _substitution_definition() -> dict:
    """A driver interpolating a config field and a command parameter."""
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"host": "", "port": 5000, "zone_tag": "MAIN"},
        "config_schema": {"host": {"type": "string", "label": "IP Address"}},
        "state_variables": {"volume": {"type": "integer", "label": "Volume"}},
        "commands": {
            "set_volume": {
                "label": "Set Volume",
                "send": "{zone_tag} VOL{level}\r\n",
                "params": {"level": {"type": "integer", "label": "Level"}},
            },
        },
        "responses": [{"match": "{zone_tag} VOL(\\d+)", "set": {"volume": "$1"}}],
        "polling": {"queries": ["{zone_tag} VOL?\r\n"]},
    }


def test_a_driver_that_declares_what_it_interpolates_is_clean():
    assert validate_substitutions(_substitution_definition()) == []


@pytest.mark.parametrize(
    ("mutate", "where"),
    [
        (lambda d: d["commands"]["set_volume"].update(send="VOL{levl}\r\n"),
         "commands.set_volume.send"),
        (lambda d: d["commands"]["set_volume"].update(
            path="/api/v{ver}/volume"), "commands.set_volume.path"),
        (lambda d: d["commands"]["set_volume"].update(
            body='{"level": {lvl}}'), "commands.set_volume.body"),
        (lambda d: d["commands"]["set_volume"].update(
            headers={"X-Zone": "{zne}"}), "commands.set_volume.headers.X-Zone"),
        (lambda d: d["responses"].insert(0, {"match": "{zne} VOL(\\d+)"}),
         "responses[0].match"),
        (lambda d: d["polling"].update(queries=["{zne} VOL?\r\n"]),
         "polling.queries[0]"),
        (lambda d: d.update(on_connect=["{zne} INIT\r\n"]), "on_connect[0]"),
    ],
)
def test_an_unresolvable_placeholder_is_reported_where_it_sits(mutate, where):
    definition = _substitution_definition()
    mutate(definition)
    errors = validate_substitutions(definition)
    assert errors, f"{where}: expected an error"
    assert errors[0].startswith(where), errors[0]
    assert "resolves to nothing" in errors[0]


def test_the_message_suggests_the_name_the_author_meant():
    definition = _substitution_definition()
    definition["commands"]["set_volume"]["send"] = "VOL{levl}\r\n"
    assert "did you mean 'level'?" in validate_substitutions(definition)[0]


@pytest.mark.parametrize(
    "send",
    [
        '{"volume": {level}}',        # literal JSON braces around a real param
        "VOL{level:03d}\r\n",         # a format spec on a real param
        "Z{child_id} VOL{level}\r\n",  # the platform's per-child token
        "{zone_tag} VOL{level}\r\n",   # a config field
    ],
)
def test_valid_templates_do_not_fire(send):
    """The rule must not punish the shapes device protocols actually use.
    A JSON body is the reason the runtime tolerates unknown braces at all."""
    definition = _substitution_definition()
    definition["commands"]["set_volume"]["send"] = send
    assert validate_substitutions(definition) == []


def test_the_push_tokens_the_platform_injects_resolve():
    """``listener_port`` and ``push_callback_url`` are set on the config by the
    push machinery, not declared by the driver — a registration command that
    references them is correct."""
    definition = _substitution_definition()
    definition["commands"]["register"] = {
        "label": "Register",
        "method": "GET",
        "path": "/event?port={listener_port}&url={push_callback_url}",
    }
    assert validate_substitutions(definition) == []


def test_a_response_pattern_cannot_reach_a_command_parameter():
    """A response is matched long after the command's params are gone, so a
    param name there resolves to nothing even though the command declares it."""
    definition = _substitution_definition()
    definition["responses"] = [{"match": "VOL{level}", "set": {"volume": "$1"}}]
    errors = validate_substitutions(definition)
    assert errors and "only config values substitute here" in errors[0]


def test_a_command_cannot_reach_another_commands_parameter():
    definition = _substitution_definition()
    definition["commands"]["set_input"] = {
        "label": "Set Input",
        "send": "IN{level}\r\n",          # 'level' belongs to set_volume
        "params": {"input": {"type": "integer", "label": "Input"}},
    }
    errors = validate_substitutions(definition)
    assert any(e.startswith("commands.set_input.send") for e in errors), errors


def test_a_computed_params_block_skips_rather_than_guesses():
    """A Python driver may build its params at runtime. The visible set is then
    a SUBSET of the real one, so 'not in it' proves nothing — the same reason
    the actions check skips. Reporting a working driver as broken is worse."""
    definition = _substitution_definition()
    definition["commands"]["set_volume"]["params"] = {UNEVALUATED_KEY: {}}
    definition["commands"]["set_volume"]["send"] = "VOL{whatever}\r\n"
    assert validate_substitutions(definition) == []


def test_substitution_checking_does_not_mutate_the_definition():
    definition = _substitution_definition()
    before = copy.deepcopy(definition)
    validate_substitutions(definition)
    assert definition == before


def test_a_command_with_no_params_block_is_left_alone():
    """The runtime substitutes whatever the caller passed, so a command that
    declares no params can still use a placeholder a macro supplies at call
    time. Loose — no type check, no range gate, no UI control — but working,
    and this rule has no standing to fail it."""
    definition = _substitution_definition()
    definition["commands"]["set_input"] = {
        "label": "Set Input",
        "send": "IN{input}\r\n",   # 'input' arrives from the caller
    }
    assert validate_substitutions(definition) == []
