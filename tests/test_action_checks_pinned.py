"""Pins for a driver's Quick Actions, as they load.

``quick_actions`` is checked first, one id at a time (text, a declared
command, not a command that takes or hands back a file), then ``actions``,
each entry's fields in a fixed order (id and duplicates, kind, label, icon,
availability, confirm, params, url, visible_when, the command a
``kind: command`` action promotes), with the file refusal after the entry's
own messages. Those are the rules both kinds of driver share, every message
tagged ``actions``. A YAML driver then gets its own two, entry by entry and
tagged ``actions[i]``: ``kind: setup`` needs a Python driver, and an entry's
``params`` take the form aids a command's do.

Each YAML case is a set of top-level keys over one driver. The expected value
is every error ``validate_driver_issues`` reports for the whole definition, in
order, each with the section tag the Driver Builder anchors it to. A case that
raises is pinned by the exception's type. The direct cases record what
``validate_actions`` answers on its own, and the Python cases what
``python_driver_info_issues`` and ``python_driver_reference_skips`` answer for a
``DRIVER_INFO`` holding the reader's markers.

These record what the checks say today, so a restructuring that keeps the
contract keeps every message, its position and its tag.
"""
from __future__ import annotations

from typing import Any

import pytest

from openavc.drivers.actions import validate_actions as actions_validate_actions
from openavc.drivers.avcdriver_semantic import (
    UNEVALUATED_KEY,
    validate_actions,
    validate_driver_definition,
    validate_driver_issues,
)
from openavc.drivers.python_info import (
    UNEVALUATED,
    python_driver_info_issues,
    python_driver_reference_skips,
)

FILE_PARAM = {"params": {"data": {"type": "file", "label": "Data"}}}

COMMANDS: dict[str, Any] = {
    "noop": {"send": "NOOP\r"},
    "power_on": {"send": "PWR 1\r", "label": "Power On"},
    "upload": {"send": "UP\r", **FILE_PARAM},
    "fetch": {"send": "GET\r", "returns_file": True},
    "set_level": {
        "send": "LVL {level}\r",
        "params": {"level": {"type": "integer", "min": 0, "max": 10}},
    },
}

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": COMMANDS,
}

PARTIAL = {**COMMANDS, UNEVALUATED_KEY: UNEVALUATED_KEY}


def acts(*entries: Any, **over: Any) -> dict[str, Any]:
    """An ``actions`` list of these entries, over these other keys."""
    return {"actions": list(entries), **over}


def act(**entry: Any) -> dict[str, Any]:
    """One ``actions`` entry, over the rest of the driver."""
    return {"actions": [entry]}


VISIBLE = {"key": "device.power", "operator": "eq", "value": True}

CASES: dict[str, dict[str, Any]] = {
    # --- quick_actions --------------------------------------------------
    "quick_a_string": {"quick_actions": "power_on"},
    "quick_a_mapping": {"quick_actions": {"power_on": True}},
    "quick_a_number": {"quick_actions": 5},
    "quick_zero": {"quick_actions": 0},
    "quick_none": {"quick_actions": None},
    "quick_empty": {"quick_actions": []},
    "quick_good": {"quick_actions": ["noop", "power_on"]},
    "quick_every_wrong_item": {
        "quick_actions": ["noop", "", 5, None, True, ["noop"], "missing", "upload", "fetch"],
    },
    "quick_a_duplicate": {"quick_actions": ["noop", "noop"]},
    "quick_over_no_commands": {"quick_actions": ["missing", "upload"], "commands": {}},
    "quick_over_commands_a_list": {"quick_actions": ["missing"], "commands": ["noop"]},
    "quick_over_a_marker_command": {
        "quick_actions": ["missing", "upload", ""],
        "commands": PARTIAL,
    },
    "quick_over_only_the_marker": {
        "quick_actions": ["missing"],
        "commands": {UNEVALUATED_KEY: {"send": "X\r"}},
    },
    "quick_naming_the_marker": {"quick_actions": [UNEVALUATED_KEY]},
    "quick_beside_bad_actions": {"quick_actions": ["missing"], "actions": [5]},
    # --- actions itself -------------------------------------------------
    "actions_a_string": {"actions": "power_on"},
    "actions_a_mapping": {"actions": {"kind": "setup", "id": "x"}},
    "actions_a_mapping_of_entries": {"actions": {"a": {"id": "a", "kind": "setup"}}},
    "actions_none": {"actions": None},
    "actions_empty": {"actions": []},
    "actions_zero": {"actions": 0},
    "actions_empty_string": {"actions": ""},
    "actions_false": {"actions": False},
    # --- an entry's shape and id ---------------------------------------
    "entry_not_a_mapping": acts("noop", 5, None, ["noop"]),
    "entry_empty": acts({}),
    "id_missing": act(label="Go"),
    "id_empty": act(id=""),
    "id_a_number": act(id=5),
    "id_none": act(id=None),
    "id_a_list": act(id=["noop"]),
    "id_duplicate": acts({"id": "noop"}, {"id": "noop"}, {"id": "noop"}),
    "id_duplicate_after_a_bad_entry": acts({"id": "noop"}, 5, {"id": "noop"}),
    "id_duplicate_across_kinds": acts(
        {"id": "noop"}, {"id": "noop", "kind": "link", "url": "http://x"},
    ),
    # --- kind -----------------------------------------------------------
    "kind_unknown": act(id="noop", kind="macro"),
    "kind_none": act(id="noop", kind=None),
    "kind_a_list": act(id="noop", kind=["command"]),
    "kind_a_number": act(id="noop", kind=1),
    "kind_upper_case": act(id="noop", kind="Command"),
    "kind_setup": act(id="provision", kind="setup"),
    "kind_setup_with_params": act(
        id="provision", kind="setup",
        params={"level": {"type": "integer", "min": 9, "max": 1}},
    ),
    "kind_link": act(id="web", kind="link", url="http://{host}"),
    "kind_link_no_url": act(id="web", kind="link"),
    # --- label, icon, availability, confirm ----------------------------
    "label_a_number": act(id="noop", label=5),
    "label_empty": act(id="noop", label=""),
    "label_none": act(id="noop", label=None),
    "label_a_list": act(id="noop", label=["Go"]),
    "icon_a_number": act(id="noop", icon=5),
    "icon_empty": act(id="noop", icon=""),
    "icon_a_boolean": act(id="noop", icon=False),
    "availability_unknown": act(id="noop", availability="never"),
    "availability_none": act(id="noop", availability=None),
    "availability_a_list": act(id="noop", availability=["online"]),
    "availability_each": acts(
        {"id": "noop", "availability": "online"},
        {"id": "power_on", "availability": "offline"},
        {"id": "set_level", "availability": "always"},
    ),
    "availability_empty": act(id="noop", availability=""),
    "confirm_true": act(id="noop", confirm=True),
    "confirm_false": act(id="noop", confirm=False),
    "confirm_text": act(id="noop", confirm="Sure?"),
    "confirm_empty_text": act(id="noop", confirm=""),
    "confirm_a_number": act(id="noop", confirm=1),
    "confirm_zero": act(id="noop", confirm=0),
    "confirm_a_list": act(id="noop", confirm=[]),
    # --- params ---------------------------------------------------------
    "params_a_list": act(id="noop", params=["level"]),
    "params_a_string": act(id="noop", params="level"),
    "params_none": act(id="noop", params=None),
    "params_empty": act(id="noop", params={}),
    "params_with_bad_aids": act(
        id="set_level",
        params={
            "level": {"type": "integer", "min": 9, "max": 1},
            "name": {"type": "string", "pattern": "("},
        },
    ),
    "params_a_bad_param": act(id="set_level", params={"level": "integer"}),
    "params_on_a_link": act(
        id="web", kind="link", url="http://x", params={"level": {"min": 9, "max": 1}},
    ),
    # --- url --------------------------------------------------------------
    "url_on_a_link_empty": act(id="web", kind="link", url=""),
    "url_on_a_link_a_number": act(id="web", kind="link", url=5),
    "url_on_a_link_none": act(id="web", kind="link", url=None),
    "url_on_a_command": act(id="noop", url="http://x"),
    "url_on_a_command_empty": act(id="noop", url=""),
    "url_on_a_setup": act(id="provision", kind="setup", url="http://x"),
    "url_on_an_unknown_kind": act(id="noop", kind="macro", url="http://x"),
    # --- visible_when ---------------------------------------------------
    "visible_when_good": act(id="noop", visible_when=VISIBLE),
    "visible_when_a_list": act(id="noop", visible_when=[VISIBLE]),
    "visible_when_a_string": act(id="noop", visible_when="device.power"),
    "visible_when_empty": act(id="noop", visible_when={}),
    "visible_when_no_key": act(id="noop", visible_when={"operator": "eq", "value": 1}),
    "visible_when_bad_group": act(id="noop", visible_when={"any": "x"}),
    "visible_when_empty_group": act(id="noop", visible_when={"all": []}),
    # --- the command a kind: command action promotes -------------------
    "target_the_id": act(id="power_on"),
    "target_the_id_missing": act(id="missing"),
    "target_named": act(id="go", command="power_on"),
    "target_named_missing": act(id="go", command="missing"),
    "target_a_number": act(id="go", command=5),
    "target_zero": act(id="go", command=0),
    "target_false": act(id="go", command=False),
    "target_a_list": act(id="go", command=["noop"]),
    "target_empty_list": act(id="go", command=[]),
    "target_empty_text": act(id="go", command=""),
    "target_none": act(id="go", command=None),
    "target_on_a_link": act(id="web", kind="link", url="http://x", command="missing"),
    "target_on_a_setup": act(id="provision", kind="setup", command="missing"),
    "target_on_an_unknown_kind": act(id="go", kind="macro", command="missing"),
    "target_kind_none": act(id="go", kind=None, command="missing"),
    "target_no_id": act(command="missing"),
    "target_over_no_commands": act(id="go", command="missing", _over={"commands": {}}),
    "target_over_commands_a_list": act(id="go", _over={"commands": ["go"]}),
    "target_over_a_marker_command": act(id="go", command="missing", _over={"commands": PARTIAL}),
    "target_naming_the_marker": act(id="go", command=UNEVALUATED_KEY),
    # --- a file command promoted ---------------------------------------
    "file_by_id": act(id="upload"),
    "file_returned_by_id": act(id="fetch"),
    "file_named": act(id="go", command="upload"),
    "file_with_an_empty_id": act(id="", command="upload"),
    "file_with_no_id": act(command="upload"),
    "file_with_a_number_id": act(id=5, command="upload"),
    "file_id_behind_a_zero_command": act(id="upload", command=0),
    "file_id_behind_an_empty_list": act(id="fetch", command=[]),
    "file_id_behind_false": act(id="upload", command=False),
    "file_id_behind_an_empty_command": act(id="upload", command=""),
    "file_id_behind_a_number_command": act(id="upload", command=5),
    "file_id_behind_a_missing_command": act(id="upload", command="missing"),
    "file_named_over_a_marker_command": act(id="go", command="upload", _over={"commands": PARTIAL}),
    "file_on_a_link": act(id="upload", kind="link", url="http://x"),
    "file_on_a_setup": act(id="upload", kind="setup"),
    "file_kind_none": act(id="upload", kind=None),
    "file_kind_explicit": act(id="upload", kind="command"),
    "file_duplicate": acts({"id": "upload"}, {"id": "upload"}),
    "file_beside_quick": {"quick_actions": ["upload"], "actions": [{"id": "fetch"}]},
    # --- everything at once, and the order ------------------------------
    "everything_wrong": act(
        id="go", kind="command", label=5, icon=5, availability="never", confirm=5,
        params=["x"], url="http://x", visible_when="x", command="missing",
    ),
    "everything_wrong_on_a_file": act(
        id="upload", label=5, icon=5, availability="never", confirm=5,
        params={"level": {"min": 9, "max": 1}}, url="http://x", visible_when="x",
    ),
    "everything_wrong_on_a_setup": act(
        id="provision", kind="setup", label=5, icon=5, availability="never",
        confirm=5, params={"level": {"min": 9, "max": 1}}, url="http://x",
        visible_when={"any": "x"}, command="upload",
    ),
    "everything_wrong_with_no_id": act(
        kind="macro", label=5, icon=5, availability="never", confirm=5, params=[],
        url="", visible_when=[],
    ),
    "the_order_across_entries": acts(
        {"id": "upload", "label": 5},
        {"id": "provision", "kind": "setup", "params": {"a": {"min": 2, "max": 1}}},
        5,
        {"id": "set_level", "params": {"level": {"min": 2, "max": 1}}, "icon": 5},
        {"id": "upload"},
        quick_actions=["fetch", "missing"],
    ),
    "a_good_block": acts(
        {"id": "power_on", "icon": "power", "confirm": "Turn it on?"},
        {"id": "level", "command": "set_level", "params": {"level": {"min": 0, "max": 5}}},
        {"id": "web", "kind": "link", "url": "http://{host}", "availability": "always"},
        {"id": "noop", "visible_when": VISIBLE, "label": "Ping"},
        quick_actions=["noop"],
    ),
}


def _definition(case: str) -> dict[str, Any]:
    over = dict(CASES[case])
    entries = over.get("actions")
    if isinstance(entries, list):
        extra: dict[str, Any] = {}
        cleaned = []
        for entry in entries:
            if isinstance(entry, dict) and "_over" in entry:
                entry = dict(entry)
                extra.update(entry.pop("_over"))
            cleaned.append(entry)
        over["actions"] = cleaned
        over.update(extra)
    return {**BASE, **over}


# The walk's YAML-only pass enumerates ``actions or []`` without a list check
# of its own, so a truthy value that cannot be iterated raises after the
# shared rule has named it.
RAISES: dict[str, dict[str, Any]] = {
    "actions_a_number": {"actions": 5},
    "actions_true": {"actions": True},
    "actions_a_float": {"actions": 1.5},
}

# ``validate_actions`` on its own: the shared rule both surfaces call.
DIRECT_CASES: dict[str, dict[str, Any]] = {
    "nothing": {},
    "no_commands": {"quick_actions": ["noop"], "actions": [{"id": "noop"}]},
    "commands_none": {"commands": None, "actions": [{"id": "noop"}]},
    "commands_a_string": {"commands": "noop", "quick_actions": ["noop"]},
    "only_the_marker": {"commands": {UNEVALUATED_KEY: 1}, "actions": [{"id": "x"}]},
    "the_marker_beside_a_file": {
        "commands": {UNEVALUATED_KEY: 1, "upload": {**FILE_PARAM}},
        "actions": [{"id": "upload"}, {"id": "x"}, {"id": "go", "command": "upload"}],
        "quick_actions": ["upload", "x"],
    },
    "setup_is_not_its_rule": {
        "commands": COMMANDS,
        "actions": [{"id": "provision", "kind": "setup", "params": {"a": {"min": 2, "max": 1}}}],
    },
    "a_file_command_that_is_not_a_mapping": {
        "commands": {"upload": "UP"}, "actions": [{"id": "upload"}], "quick_actions": ["upload"],
    },
    "a_file_param_not_a_mapping": {
        "commands": {"upload": {"params": {"data": "file"}}}, "actions": [{"id": "upload"}],
    },
    "returns_file_truthy_not_true": {
        "commands": {"fetch": {"returns_file": 1}}, "actions": [{"id": "fetch"}],
    },
}

# A Python driver's DRIVER_INFO, with the reader's markers where the source
# could not be read.
PYTHON_CASES: dict[str, dict[str, Any]] = {
    "plain": {"commands": COMMANDS, "actions": [{"id": "upload"}, {"id": "missing"}]},
    "commands_partial": {
        "commands": {**COMMANDS, UNEVALUATED_KEY: UNEVALUATED},
        "actions": [{"id": "missing"}, {"id": "upload"}, {"id": "provision", "kind": "setup"}],
        "quick_actions": ["missing", "fetch"],
    },
    "commands_computed": {
        "commands": UNEVALUATED,
        "actions": [{"id": "missing"}],
        "quick_actions": ["missing"],
    },
    "actions_computed": {"commands": COMMANDS, "actions": UNEVALUATED},
    "quick_actions_computed": {"commands": COMMANDS, "quick_actions": UNEVALUATED},
    "an_entry_computed": {"commands": COMMANDS, "actions": [UNEVALUATED, {"id": "noop"}]},
    "a_field_computed": {
        "commands": COMMANDS,
        "actions": [{
            "id": "noop", "label": UNEVALUATED, "icon": UNEVALUATED,
            "availability": UNEVALUATED, "confirm": UNEVALUATED,
            "params": UNEVALUATED, "visible_when": UNEVALUATED,
        }],
    },
    "the_kind_computed": {
        "commands": COMMANDS, "actions": [{"id": "upload", "kind": UNEVALUATED}],
    },
    "the_id_computed": {
        "commands": COMMANDS,
        "actions": [{"id": UNEVALUATED}, {"id": UNEVALUATED, "command": "upload"}],
    },
    "the_target_computed": {
        "commands": COMMANDS, "actions": [{"id": "upload", "command": UNEVALUATED}],
    },
    "a_quick_item_computed": {"commands": COMMANDS, "quick_actions": [UNEVALUATED, "noop"]},
    "a_setup_with_bad_params": {
        "commands": COMMANDS,
        "actions": [{"id": "provision", "kind": "setup", "params": {"a": {"min": 2, "max": 1}}}],
    },
}


def python_info(case: str) -> dict[str, Any]:
    return {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp", **PYTHON_CASES[case]}


EXPECTED: dict[str, list[tuple[str, str]]] = {'a_good_block': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'actions_a_mapping': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver"),
                       ('actions', 'actions: must be a list')],
 'actions_a_mapping_of_entries': [('commands.upload',
                                   "Command 'upload' param 'data': type 'file' requires a Python "
                                   'driver'),
                                  ('commands.fetch',
                                   "Command 'fetch': 'returns_file' requires a Python driver"),
                                  ('actions', 'actions: must be a list')],
 'actions_a_string': [('commands.upload',
                       "Command 'upload' param 'data': type 'file' requires a Python driver"),
                      ('commands.fetch',
                       "Command 'fetch': 'returns_file' requires a Python driver"),
                      ('actions', 'actions: must be a list')],
 'actions_empty': [('commands.upload',
                    "Command 'upload' param 'data': type 'file' requires a Python driver"),
                   ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'actions_empty_string': [('commands.upload',
                           "Command 'upload' param 'data': type 'file' requires a Python driver"),
                          ('commands.fetch',
                           "Command 'fetch': 'returns_file' requires a Python driver"),
                          ('actions', 'actions: must be a list')],
 'actions_false': [('commands.upload',
                    "Command 'upload' param 'data': type 'file' requires a Python driver"),
                   ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                   ('actions', 'actions: must be a list')],
 'actions_none': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'actions_zero': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                  ('actions', 'actions: must be a list')],
 'availability_a_list': [('commands.upload',
                          "Command 'upload' param 'data': type 'file' requires a Python driver"),
                         ('commands.fetch',
                          "Command 'fetch': 'returns_file' requires a Python driver"),
                         ('actions',
                          "actions[0]: 'availability' must be one of ['online', 'offline', "
                          "'always']")],
 'availability_each': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver")],
 'availability_empty': [('commands.upload',
                         "Command 'upload' param 'data': type 'file' requires a Python driver"),
                        ('commands.fetch',
                         "Command 'fetch': 'returns_file' requires a Python driver"),
                        ('actions',
                         "actions[0]: 'availability' must be one of ['online', 'offline', "
                         "'always']")],
 'availability_none': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver")],
 'availability_unknown': [('commands.upload',
                           "Command 'upload' param 'data': type 'file' requires a Python driver"),
                          ('commands.fetch',
                           "Command 'fetch': 'returns_file' requires a Python driver"),
                          ('actions',
                           "actions[0]: 'availability' must be one of ['online', 'offline', "
                           "'always']")],
 'confirm_a_list': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                    ('actions', "actions[0]: 'confirm' must be a boolean or a message string")],
 'confirm_a_number': [('commands.upload',
                       "Command 'upload' param 'data': type 'file' requires a Python driver"),
                      ('commands.fetch',
                       "Command 'fetch': 'returns_file' requires a Python driver"),
                      ('actions', "actions[0]: 'confirm' must be a boolean or a message string")],
 'confirm_empty_text': [('commands.upload',
                         "Command 'upload' param 'data': type 'file' requires a Python driver"),
                        ('commands.fetch',
                         "Command 'fetch': 'returns_file' requires a Python driver")],
 'confirm_false': [('commands.upload',
                    "Command 'upload' param 'data': type 'file' requires a Python driver"),
                   ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'confirm_text': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'confirm_true': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'confirm_zero': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                  ('actions', "actions[0]: 'confirm' must be a boolean or a message string")],
 'entry_empty': [('commands.upload',
                  "Command 'upload' param 'data': type 'file' requires a Python driver"),
                 ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                 ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'entry_not_a_mapping': [('commands.upload',
                          "Command 'upload' param 'data': type 'file' requires a Python driver"),
                         ('commands.fetch',
                          "Command 'fetch': 'returns_file' requires a Python driver"),
                         ('actions', 'actions[0]: must be a mapping'),
                         ('actions', 'actions[1]: must be a mapping'),
                         ('actions', 'actions[2]: must be a mapping'),
                         ('actions', 'actions[3]: must be a mapping')],
 'everything_wrong': [('commands.upload',
                       "Command 'upload' param 'data': type 'file' requires a Python driver"),
                      ('commands.fetch',
                       "Command 'fetch': 'returns_file' requires a Python driver"),
                      ('actions', "actions[0]: 'label' must be a string"),
                      ('actions', "actions[0]: 'icon' must be a string (lucide icon name)"),
                      ('actions',
                       "actions[0]: 'availability' must be one of ['online', 'offline', 'always']"),
                      ('actions', "actions[0]: 'confirm' must be a boolean or a message string"),
                      ('actions', "actions[0]: 'params' must be a mapping"),
                      ('actions', "actions[0]: 'url' is only valid on a kind:link action"),
                      ('actions', "actions[0]: 'visible_when' must be a mapping"),
                      ('actions', "actions[0]: command 'missing' is not a declared command")],
 'everything_wrong_on_a_file': [('commands.upload',
                                 "Command 'upload' param 'data': type 'file' requires a Python "
                                 'driver'),
                                ('commands.fetch',
                                 "Command 'fetch': 'returns_file' requires a Python driver"),
                                ('actions', "actions[0]: 'label' must be a string"),
                                ('actions',
                                 "actions[0]: 'icon' must be a string (lucide icon name)"),
                                ('actions',
                                 "actions[0]: 'availability' must be one of ['online', 'offline', "
                                 "'always']"),
                                ('actions',
                                 "actions[0]: 'confirm' must be a boolean or a message string"),
                                ('actions',
                                 "actions[0]: 'url' is only valid on a kind:link action"),
                                ('actions', "actions[0]: 'visible_when' must be a mapping"),
                                ('actions',
                                 "actions[0]: command 'upload' takes or hands back a file, so it "
                                 'cannot be a Quick Action; it runs from Send Command on the '
                                 'device page'),
                                ('actions[0]',
                                 "actions[0] param 'level': min (9) must be <= max (1)")],
 'everything_wrong_on_a_setup': [('commands.upload',
                                  "Command 'upload' param 'data': type 'file' requires a Python "
                                  'driver'),
                                 ('commands.fetch',
                                  "Command 'fetch': 'returns_file' requires a Python driver"),
                                 ('actions', "actions[0]: 'label' must be a string"),
                                 ('actions',
                                  "actions[0]: 'icon' must be a string (lucide icon name)"),
                                 ('actions',
                                  "actions[0]: 'availability' must be one of ['online', 'offline', "
                                  "'always']"),
                                 ('actions',
                                  "actions[0]: 'confirm' must be a boolean or a message string"),
                                 ('actions',
                                  "actions[0]: 'url' is only valid on a kind:link action"),
                                 ('actions',
                                  'actions[0]: visible_when.any must be a non-empty list'),
                                 ('actions[0]',
                                  "actions[0]: kind 'setup' requires a Python driver (a "
                                  "run_setup_action handler); YAML drivers support kind 'command' "
                                  'only'),
                                 ('actions[0]',
                                  "actions[0] param 'level': min (9) must be <= max (1)")],
 'everything_wrong_with_no_id': [('commands.upload',
                                  "Command 'upload' param 'data': type 'file' requires a Python "
                                  'driver'),
                                 ('commands.fetch',
                                  "Command 'fetch': 'returns_file' requires a Python driver"),
                                 ('actions',
                                  "actions[0]: missing required 'id' (non-empty string)"),
                                 ('actions',
                                  "actions[0]: unknown kind 'macro' (expected one of ['command', "
                                  "'setup', 'link'])"),
                                 ('actions', "actions[0]: 'label' must be a string"),
                                 ('actions',
                                  "actions[0]: 'icon' must be a string (lucide icon name)"),
                                 ('actions',
                                  "actions[0]: 'availability' must be one of ['online', 'offline', "
                                  "'always']"),
                                 ('actions',
                                  "actions[0]: 'confirm' must be a boolean or a message string"),
                                 ('actions', "actions[0]: 'params' must be a mapping"),
                                 ('actions',
                                  "actions[0]: 'url' is only valid on a kind:link action"),
                                 ('actions', "actions[0]: 'visible_when' must be a mapping")],
 'file_beside_quick': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver"),
                       ('actions',
                        "quick_actions[0]: 'upload' takes or hands back a file, so it cannot be a "
                        'Quick Action; it runs from Send Command on the device page'),
                       ('actions',
                        "actions[0]: command 'fetch' takes or hands back a file, so it cannot be a "
                        'Quick Action; it runs from Send Command on the device page')],
 'file_by_id': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                ('actions',
                 "actions[0]: command 'upload' takes or hands back a file, so it cannot be a Quick "
                 'Action; it runs from Send Command on the device page')],
 'file_duplicate': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                    ('actions',
                     "actions[0]: command 'upload' takes or hands back a file, so it cannot be a "
                     'Quick Action; it runs from Send Command on the device page'),
                    ('actions', "actions[1]: duplicate action id 'upload'"),
                    ('actions',
                     "actions[1]: command 'upload' takes or hands back a file, so it cannot be a "
                     'Quick Action; it runs from Send Command on the device page')],
 'file_id_behind_a_missing_command': [('commands.upload',
                                       "Command 'upload' param 'data': type 'file' requires a "
                                       'Python driver'),
                                      ('commands.fetch',
                                       "Command 'fetch': 'returns_file' requires a Python driver"),
                                      ('actions',
                                       "actions[0]: command 'missing' is not a declared command")],
 'file_id_behind_a_number_command': [('commands.upload',
                                      "Command 'upload' param 'data': type 'file' requires a "
                                      'Python driver'),
                                     ('commands.fetch',
                                      "Command 'fetch': 'returns_file' requires a Python driver"),
                                     ('actions', "actions[0]: 'command' must be a string")],
 'file_id_behind_a_zero_command': [('commands.upload',
                                    "Command 'upload' param 'data': type 'file' requires a Python "
                                    'driver'),
                                   ('commands.fetch',
                                    "Command 'fetch': 'returns_file' requires a Python driver"),
                                   ('actions', "actions[0]: 'command' must be a string"),
                                   ('actions',
                                    "actions[0]: command 'upload' takes or hands back a file, so "
                                    'it cannot be a Quick Action; it runs from Send Command on the '
                                    'device page')],
 'file_id_behind_an_empty_command': [('commands.upload',
                                      "Command 'upload' param 'data': type 'file' requires a "
                                      'Python driver'),
                                     ('commands.fetch',
                                      "Command 'fetch': 'returns_file' requires a Python driver"),
                                     ('actions',
                                      "actions[0]: command 'upload' takes or hands back a file, so "
                                      'it cannot be a Quick Action; it runs from Send Command on '
                                      'the device page')],
 'file_id_behind_an_empty_list': [('commands.upload',
                                   "Command 'upload' param 'data': type 'file' requires a Python "
                                   'driver'),
                                  ('commands.fetch',
                                   "Command 'fetch': 'returns_file' requires a Python driver"),
                                  ('actions', "actions[0]: 'command' must be a string"),
                                  ('actions',
                                   "actions[0]: command 'fetch' takes or hands back a file, so it "
                                   'cannot be a Quick Action; it runs from Send Command on the '
                                   'device page')],
 'file_id_behind_false': [('commands.upload',
                           "Command 'upload' param 'data': type 'file' requires a Python driver"),
                          ('commands.fetch',
                           "Command 'fetch': 'returns_file' requires a Python driver"),
                          ('actions', "actions[0]: 'command' must be a string"),
                          ('actions',
                           "actions[0]: command 'upload' takes or hands back a file, so it cannot "
                           'be a Quick Action; it runs from Send Command on the device page')],
 'file_kind_explicit': [('commands.upload',
                         "Command 'upload' param 'data': type 'file' requires a Python driver"),
                        ('commands.fetch',
                         "Command 'fetch': 'returns_file' requires a Python driver"),
                        ('actions',
                         "actions[0]: command 'upload' takes or hands back a file, so it cannot be "
                         'a Quick Action; it runs from Send Command on the device page')],
 'file_kind_none': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                    ('actions',
                     "actions[0]: unknown kind 'None' (expected one of ['command', 'setup', "
                     "'link'])")],
 'file_named': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                ('actions',
                 "actions[0]: command 'upload' takes or hands back a file, so it cannot be a Quick "
                 'Action; it runs from Send Command on the device page')],
 'file_named_over_a_marker_command': [('commands.upload',
                                       "Command 'upload' param 'data': type 'file' requires a "
                                       'Python driver'),
                                      ('commands.fetch',
                                       "Command 'fetch': 'returns_file' requires a Python driver"),
                                      ('commands.<unevaluated>',
                                       "Command '<unevaluated>': must be a dict"),
                                      ('actions',
                                       "actions[0]: command 'upload' takes or hands back a file, "
                                       'so it cannot be a Quick Action; it runs from Send Command '
                                       'on the device page')],
 'file_on_a_link': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'file_on_a_setup': [('commands.upload',
                      "Command 'upload' param 'data': type 'file' requires a Python driver"),
                     ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                     ('actions[0]',
                      "actions[0]: kind 'setup' requires a Python driver (a run_setup_action "
                      "handler); YAML drivers support kind 'command' only")],
 'file_returned_by_id': [('commands.upload',
                          "Command 'upload' param 'data': type 'file' requires a Python driver"),
                         ('commands.fetch',
                          "Command 'fetch': 'returns_file' requires a Python driver"),
                         ('actions',
                          "actions[0]: command 'fetch' takes or hands back a file, so it cannot be "
                          'a Quick Action; it runs from Send Command on the device page')],
 'file_with_a_number_id': [('commands.upload',
                            "Command 'upload' param 'data': type 'file' requires a Python driver"),
                           ('commands.fetch',
                            "Command 'fetch': 'returns_file' requires a Python driver"),
                           ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'file_with_an_empty_id': [('commands.upload',
                            "Command 'upload' param 'data': type 'file' requires a Python driver"),
                           ('commands.fetch',
                            "Command 'fetch': 'returns_file' requires a Python driver"),
                           ('actions', "actions[0]: missing required 'id' (non-empty string)"),
                           ('actions',
                            "actions[0]: command 'upload' takes or hands back a file, so it cannot "
                            'be a Quick Action; it runs from Send Command on the device page')],
 'file_with_no_id': [('commands.upload',
                      "Command 'upload' param 'data': type 'file' requires a Python driver"),
                     ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                     ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'icon_a_boolean': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                    ('actions', "actions[0]: 'icon' must be a string (lucide icon name)")],
 'icon_a_number': [('commands.upload',
                    "Command 'upload' param 'data': type 'file' requires a Python driver"),
                   ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                   ('actions', "actions[0]: 'icon' must be a string (lucide icon name)")],
 'icon_empty': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'id_a_list': [('commands.upload',
                "Command 'upload' param 'data': type 'file' requires a Python driver"),
               ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
               ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'id_a_number': [('commands.upload',
                  "Command 'upload' param 'data': type 'file' requires a Python driver"),
                 ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                 ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'id_duplicate': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                  ('actions', "actions[1]: duplicate action id 'noop'"),
                  ('actions', "actions[2]: duplicate action id 'noop'")],
 'id_duplicate_across_kinds': [('commands.upload',
                                "Command 'upload' param 'data': type 'file' requires a Python "
                                'driver'),
                               ('commands.fetch',
                                "Command 'fetch': 'returns_file' requires a Python driver"),
                               ('actions', "actions[1]: duplicate action id 'noop'")],
 'id_duplicate_after_a_bad_entry': [('commands.upload',
                                     "Command 'upload' param 'data': type 'file' requires a Python "
                                     'driver'),
                                    ('commands.fetch',
                                     "Command 'fetch': 'returns_file' requires a Python driver"),
                                    ('actions', 'actions[1]: must be a mapping'),
                                    ('actions', "actions[2]: duplicate action id 'noop'")],
 'id_empty': [('commands.upload',
               "Command 'upload' param 'data': type 'file' requires a Python driver"),
              ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
              ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'id_missing': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'id_none': [('commands.upload',
              "Command 'upload' param 'data': type 'file' requires a Python driver"),
             ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
             ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'kind_a_list': [('commands.upload',
                  "Command 'upload' param 'data': type 'file' requires a Python driver"),
                 ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                 ('actions',
                  "actions[0]: unknown kind '['command']' (expected one of ['command', 'setup', "
                  "'link'])")],
 'kind_a_number': [('commands.upload',
                    "Command 'upload' param 'data': type 'file' requires a Python driver"),
                   ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                   ('actions',
                    "actions[0]: unknown kind '1' (expected one of ['command', 'setup', 'link'])")],
 'kind_link': [('commands.upload',
                "Command 'upload' param 'data': type 'file' requires a Python driver"),
               ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'kind_link_no_url': [('commands.upload',
                       "Command 'upload' param 'data': type 'file' requires a Python driver"),
                      ('commands.fetch',
                       "Command 'fetch': 'returns_file' requires a Python driver")],
 'kind_none': [('commands.upload',
                "Command 'upload' param 'data': type 'file' requires a Python driver"),
               ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
               ('actions',
                "actions[0]: unknown kind 'None' (expected one of ['command', 'setup', 'link'])")],
 'kind_setup': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                ('actions[0]',
                 "actions[0]: kind 'setup' requires a Python driver (a run_setup_action handler); "
                 "YAML drivers support kind 'command' only")],
 'kind_setup_with_params': [('commands.upload',
                             "Command 'upload' param 'data': type 'file' requires a Python driver"),
                            ('commands.fetch',
                             "Command 'fetch': 'returns_file' requires a Python driver"),
                            ('actions[0]',
                             "actions[0]: kind 'setup' requires a Python driver (a "
                             "run_setup_action handler); YAML drivers support kind 'command' only"),
                            ('actions[0]', "actions[0] param 'level': min (9) must be <= max (1)")],
 'kind_unknown': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                  ('actions',
                   "actions[0]: unknown kind 'macro' (expected one of ['command', 'setup', "
                   "'link'])")],
 'kind_upper_case': [('commands.upload',
                      "Command 'upload' param 'data': type 'file' requires a Python driver"),
                     ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                     ('actions',
                      "actions[0]: unknown kind 'Command' (expected one of ['command', 'setup', "
                      "'link'])")],
 'label_a_list': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                  ('actions', "actions[0]: 'label' must be a string")],
 'label_a_number': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                    ('actions', "actions[0]: 'label' must be a string")],
 'label_empty': [('commands.upload',
                  "Command 'upload' param 'data': type 'file' requires a Python driver"),
                 ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'label_none': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'params_a_bad_param': [('commands.upload',
                         "Command 'upload' param 'data': type 'file' requires a Python driver"),
                        ('commands.fetch',
                         "Command 'fetch': 'returns_file' requires a Python driver")],
 'params_a_list': [('commands.upload',
                    "Command 'upload' param 'data': type 'file' requires a Python driver"),
                   ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                   ('actions', "actions[0]: 'params' must be a mapping")],
 'params_a_string': [('commands.upload',
                      "Command 'upload' param 'data': type 'file' requires a Python driver"),
                     ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                     ('actions', "actions[0]: 'params' must be a mapping")],
 'params_empty': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'params_none': [('commands.upload',
                  "Command 'upload' param 'data': type 'file' requires a Python driver"),
                 ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'params_on_a_link': [('commands.upload',
                       "Command 'upload' param 'data': type 'file' requires a Python driver"),
                      ('commands.fetch',
                       "Command 'fetch': 'returns_file' requires a Python driver"),
                      ('actions[0]', "actions[0] param 'level': min (9) must be <= max (1)")],
 'params_with_bad_aids': [('commands.upload',
                           "Command 'upload' param 'data': type 'file' requires a Python driver"),
                          ('commands.fetch',
                           "Command 'fetch': 'returns_file' requires a Python driver"),
                          ('actions[0]', "actions[0] param 'level': min (9) must be <= max (1)"),
                          ('actions[0]',
                           "actions[0] param 'name': pattern: invalid regex '(': missing ), "
                           'unterminated subpattern at position 0')],
 'quick_a_duplicate': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver")],
 'quick_a_mapping': [('commands.upload',
                      "Command 'upload' param 'data': type 'file' requires a Python driver"),
                     ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                     ('actions', 'quick_actions: must be a list of command ids')],
 'quick_a_number': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                    ('actions', 'quick_actions: must be a list of command ids')],
 'quick_a_string': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                    ('actions', 'quick_actions: must be a list of command ids')],
 'quick_beside_bad_actions': [('commands.upload',
                               "Command 'upload' param 'data': type 'file' requires a Python "
                               'driver'),
                              ('commands.fetch',
                               "Command 'fetch': 'returns_file' requires a Python driver"),
                              ('actions', "quick_actions[0]: 'missing' is not a declared command"),
                              ('actions', 'actions[0]: must be a mapping')],
 'quick_empty': [('commands.upload',
                  "Command 'upload' param 'data': type 'file' requires a Python driver"),
                 ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'quick_every_wrong_item': [('commands.upload',
                             "Command 'upload' param 'data': type 'file' requires a Python driver"),
                            ('commands.fetch',
                             "Command 'fetch': 'returns_file' requires a Python driver"),
                            ('actions', 'quick_actions[1]: must be a non-empty command id string'),
                            ('actions', 'quick_actions[2]: must be a non-empty command id string'),
                            ('actions', 'quick_actions[3]: must be a non-empty command id string'),
                            ('actions', 'quick_actions[4]: must be a non-empty command id string'),
                            ('actions', 'quick_actions[5]: must be a non-empty command id string'),
                            ('actions', "quick_actions[6]: 'missing' is not a declared command"),
                            ('actions',
                             "quick_actions[7]: 'upload' takes or hands back a file, so it cannot "
                             'be a Quick Action; it runs from Send Command on the device page'),
                            ('actions',
                             "quick_actions[8]: 'fetch' takes or hands back a file, so it cannot "
                             'be a Quick Action; it runs from Send Command on the device page')],
 'quick_good': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'quick_naming_the_marker': [('commands.upload',
                              "Command 'upload' param 'data': type 'file' requires a Python "
                              'driver'),
                             ('commands.fetch',
                              "Command 'fetch': 'returns_file' requires a Python driver"),
                             ('actions',
                              "quick_actions[0]: '<unevaluated>' is not a declared command")],
 'quick_none': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'quick_over_a_marker_command': [('commands.upload',
                                  "Command 'upload' param 'data': type 'file' requires a Python "
                                  'driver'),
                                 ('commands.fetch',
                                  "Command 'fetch': 'returns_file' requires a Python driver"),
                                 ('commands.<unevaluated>',
                                  "Command '<unevaluated>': must be a dict"),
                                 ('actions',
                                  "quick_actions[1]: 'upload' takes or hands back a file, so it "
                                  'cannot be a Quick Action; it runs from Send Command on the '
                                  'device page'),
                                 ('actions',
                                  'quick_actions[2]: must be a non-empty command id string')],
 'quick_over_commands_a_list': [('commands', 'commands: must be a mapping')],
 'quick_over_no_commands': [],
 'quick_over_only_the_marker': [],
 'quick_zero': [('commands.upload',
                 "Command 'upload' param 'data': type 'file' requires a Python driver"),
                ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                ('actions', 'quick_actions: must be a list of command ids')],
 'target_a_list': [('commands.upload',
                    "Command 'upload' param 'data': type 'file' requires a Python driver"),
                   ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                   ('actions', "actions[0]: 'command' must be a string")],
 'target_a_number': [('commands.upload',
                      "Command 'upload' param 'data': type 'file' requires a Python driver"),
                     ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                     ('actions', "actions[0]: 'command' must be a string")],
 'target_empty_list': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver"),
                       ('actions', "actions[0]: 'command' must be a string")],
 'target_empty_text': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver"),
                       ('actions', "actions[0]: command 'go' is not a declared command")],
 'target_false': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                  ('actions', "actions[0]: 'command' must be a string")],
 'target_kind_none': [('commands.upload',
                       "Command 'upload' param 'data': type 'file' requires a Python driver"),
                      ('commands.fetch',
                       "Command 'fetch': 'returns_file' requires a Python driver"),
                      ('actions',
                       "actions[0]: unknown kind 'None' (expected one of ['command', 'setup', "
                       "'link'])")],
 'target_named': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'target_named_missing': [('commands.upload',
                           "Command 'upload' param 'data': type 'file' requires a Python driver"),
                          ('commands.fetch',
                           "Command 'fetch': 'returns_file' requires a Python driver"),
                          ('actions', "actions[0]: command 'missing' is not a declared command")],
 'target_naming_the_marker': [('commands.upload',
                               "Command 'upload' param 'data': type 'file' requires a Python "
                               'driver'),
                              ('commands.fetch',
                               "Command 'fetch': 'returns_file' requires a Python driver"),
                              ('actions',
                               "actions[0]: command '<unevaluated>' is not a declared command")],
 'target_no_id': [('commands.upload',
                   "Command 'upload' param 'data': type 'file' requires a Python driver"),
                  ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                  ('actions', "actions[0]: missing required 'id' (non-empty string)")],
 'target_none': [('commands.upload',
                  "Command 'upload' param 'data': type 'file' requires a Python driver"),
                 ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                 ('actions', "actions[0]: command 'go' is not a declared command")],
 'target_on_a_link': [('commands.upload',
                       "Command 'upload' param 'data': type 'file' requires a Python driver"),
                      ('commands.fetch',
                       "Command 'fetch': 'returns_file' requires a Python driver")],
 'target_on_a_setup': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver"),
                       ('actions[0]',
                        "actions[0]: kind 'setup' requires a Python driver (a run_setup_action "
                        "handler); YAML drivers support kind 'command' only")],
 'target_on_an_unknown_kind': [('commands.upload',
                                "Command 'upload' param 'data': type 'file' requires a Python "
                                'driver'),
                               ('commands.fetch',
                                "Command 'fetch': 'returns_file' requires a Python driver"),
                               ('actions',
                                "actions[0]: unknown kind 'macro' (expected one of ['command', "
                                "'setup', 'link'])")],
 'target_over_a_marker_command': [('commands.upload',
                                   "Command 'upload' param 'data': type 'file' requires a Python "
                                   'driver'),
                                  ('commands.fetch',
                                   "Command 'fetch': 'returns_file' requires a Python driver"),
                                  ('commands.<unevaluated>',
                                   "Command '<unevaluated>': must be a dict")],
 'target_over_commands_a_list': [('commands', 'commands: must be a mapping')],
 'target_over_no_commands': [],
 'target_the_id': [('commands.upload',
                    "Command 'upload' param 'data': type 'file' requires a Python driver"),
                   ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver")],
 'target_the_id_missing': [('commands.upload',
                            "Command 'upload' param 'data': type 'file' requires a Python driver"),
                           ('commands.fetch',
                            "Command 'fetch': 'returns_file' requires a Python driver"),
                           ('actions', "actions[0]: command 'missing' is not a declared command")],
 'target_zero': [('commands.upload',
                  "Command 'upload' param 'data': type 'file' requires a Python driver"),
                 ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                 ('actions', "actions[0]: 'command' must be a string")],
 'the_order_across_entries': [('commands.upload',
                               "Command 'upload' param 'data': type 'file' requires a Python "
                               'driver'),
                              ('commands.fetch',
                               "Command 'fetch': 'returns_file' requires a Python driver"),
                              ('actions',
                               "quick_actions[0]: 'fetch' takes or hands back a file, so it cannot "
                               'be a Quick Action; it runs from Send Command on the device page'),
                              ('actions', "quick_actions[1]: 'missing' is not a declared command"),
                              ('actions', "actions[0]: 'label' must be a string"),
                              ('actions',
                               "actions[0]: command 'upload' takes or hands back a file, so it "
                               'cannot be a Quick Action; it runs from Send Command on the device '
                               'page'),
                              ('actions', 'actions[2]: must be a mapping'),
                              ('actions', "actions[3]: 'icon' must be a string (lucide icon name)"),
                              ('actions', "actions[4]: duplicate action id 'upload'"),
                              ('actions',
                               "actions[4]: command 'upload' takes or hands back a file, so it "
                               'cannot be a Quick Action; it runs from Send Command on the device '
                               'page'),
                              ('actions[1]',
                               "actions[1]: kind 'setup' requires a Python driver (a "
                               "run_setup_action handler); YAML drivers support kind 'command' "
                               'only'),
                              ('actions[1]', "actions[1] param 'a': min (2) must be <= max (1)"),
                              ('actions[3]',
                               "actions[3] param 'level': min (2) must be <= max (1)")],
 'url_on_a_command': [('commands.upload',
                       "Command 'upload' param 'data': type 'file' requires a Python driver"),
                      ('commands.fetch',
                       "Command 'fetch': 'returns_file' requires a Python driver"),
                      ('actions', "actions[0]: 'url' is only valid on a kind:link action")],
 'url_on_a_command_empty': [('commands.upload',
                             "Command 'upload' param 'data': type 'file' requires a Python driver"),
                            ('commands.fetch',
                             "Command 'fetch': 'returns_file' requires a Python driver"),
                            ('actions', "actions[0]: 'url' is only valid on a kind:link action")],
 'url_on_a_link_a_number': [('commands.upload',
                             "Command 'upload' param 'data': type 'file' requires a Python driver"),
                            ('commands.fetch',
                             "Command 'fetch': 'returns_file' requires a Python driver"),
                            ('actions', "actions[0]: 'url' must be a non-empty string")],
 'url_on_a_link_empty': [('commands.upload',
                          "Command 'upload' param 'data': type 'file' requires a Python driver"),
                         ('commands.fetch',
                          "Command 'fetch': 'returns_file' requires a Python driver"),
                         ('actions', "actions[0]: 'url' must be a non-empty string")],
 'url_on_a_link_none': [('commands.upload',
                         "Command 'upload' param 'data': type 'file' requires a Python driver"),
                        ('commands.fetch',
                         "Command 'fetch': 'returns_file' requires a Python driver")],
 'url_on_a_setup': [('commands.upload',
                     "Command 'upload' param 'data': type 'file' requires a Python driver"),
                    ('commands.fetch', "Command 'fetch': 'returns_file' requires a Python driver"),
                    ('actions', "actions[0]: 'url' is only valid on a kind:link action"),
                    ('actions[0]',
                     "actions[0]: kind 'setup' requires a Python driver (a run_setup_action "
                     "handler); YAML drivers support kind 'command' only")],
 'url_on_an_unknown_kind': [('commands.upload',
                             "Command 'upload' param 'data': type 'file' requires a Python driver"),
                            ('commands.fetch',
                             "Command 'fetch': 'returns_file' requires a Python driver"),
                            ('actions',
                             "actions[0]: unknown kind 'macro' (expected one of ['command', "
                             "'setup', 'link'])"),
                            ('actions', "actions[0]: 'url' is only valid on a kind:link action")],
 'visible_when_a_list': [('commands.upload',
                          "Command 'upload' param 'data': type 'file' requires a Python driver"),
                         ('commands.fetch',
                          "Command 'fetch': 'returns_file' requires a Python driver"),
                         ('actions', "actions[0]: 'visible_when' must be a mapping")],
 'visible_when_a_string': [('commands.upload',
                            "Command 'upload' param 'data': type 'file' requires a Python driver"),
                           ('commands.fetch',
                            "Command 'fetch': 'returns_file' requires a Python driver"),
                           ('actions', "actions[0]: 'visible_when' must be a mapping")],
 'visible_when_bad_group': [('commands.upload',
                             "Command 'upload' param 'data': type 'file' requires a Python driver"),
                            ('commands.fetch',
                             "Command 'fetch': 'returns_file' requires a Python driver"),
                            ('actions', 'actions[0]: visible_when.any must be a non-empty list')],
 'visible_when_empty': [('commands.upload',
                         "Command 'upload' param 'data': type 'file' requires a Python driver"),
                        ('commands.fetch',
                         "Command 'fetch': 'returns_file' requires a Python driver"),
                        ('actions',
                         "actions[0]: visible_when: condition missing 'key' (state key string)")],
 'visible_when_empty_group': [('commands.upload',
                               "Command 'upload' param 'data': type 'file' requires a Python "
                               'driver'),
                              ('commands.fetch',
                               "Command 'fetch': 'returns_file' requires a Python driver"),
                              ('actions', 'actions[0]: visible_when.all must be a non-empty list')],
 'visible_when_good': [('commands.upload',
                        "Command 'upload' param 'data': type 'file' requires a Python driver"),
                       ('commands.fetch',
                        "Command 'fetch': 'returns_file' requires a Python driver")],
 'visible_when_no_key': [('commands.upload',
                          "Command 'upload' param 'data': type 'file' requires a Python driver"),
                         ('commands.fetch',
                          "Command 'fetch': 'returns_file' requires a Python driver"),
                         ('actions',
                          "actions[0]: visible_when: condition missing 'key' (state key string)")]}


RAISES_EXPECTED: dict[str, str] = {'actions_a_float': 'TypeError', 'actions_a_number': 'TypeError', 'actions_true': 'TypeError'}


DIRECT_EXPECTED: dict[str, list[str]] = {'a_file_command_that_is_not_a_mapping': [],
 'a_file_param_not_a_mapping': [],
 'commands_a_string': [],
 'commands_none': [],
 'no_commands': [],
 'nothing': [],
 'only_the_marker': [],
 'returns_file_truthy_not_true': [],
 'setup_is_not_its_rule': [],
 'the_marker_beside_a_file': ["quick_actions[0]: 'upload' takes or hands back a file, so it cannot "
                              'be a Quick Action; it runs from Send Command on the device page',
                              "actions[0]: command 'upload' takes or hands back a file, so it "
                              'cannot be a Quick Action; it runs from Send Command on the device '
                              'page',
                              "actions[2]: command 'upload' takes or hands back a file, so it "
                              'cannot be a Quick Action; it runs from Send Command on the device '
                              'page']}


PYTHON_EXPECTED: dict[str, tuple[list[str], list[str]]] = {'a_field_computed': (["actions[0]: 'label' must be a string",
                       "actions[0]: 'icon' must be a string (lucide icon name)",
                       "actions[0]: 'availability' must be one of ['online', 'offline', 'always']",
                       "actions[0]: 'confirm' must be a boolean or a message string",
                       "actions[0]: 'params' must be a mapping",
                       "actions[0]: 'visible_when' must be a mapping"],
                      []),
 'a_quick_item_computed': (['quick_actions[0]: must be a non-empty command id string'], []),
 'a_setup_with_bad_params': ([], []),
 'actions_computed': (['actions: must be a list'], []),
 'an_entry_computed': (['actions[0]: must be a mapping'], []),
 'commands_computed': ([],
                       ['commands is computed, so no command params were checked',
                        '2 action/quick_action reference(s) into commands, because commands is '
                        'computed']),
 'commands_partial': (["quick_actions[1]: 'fetch' takes or hands back a file, so it cannot be a "
                       'Quick Action; it runs from Send Command on the device page',
                       "actions[1]: command 'upload' takes or hands back a file, so it cannot be a "
                       'Quick Action; it runs from Send Command on the device page'],
                      ['5 of an unknown number of commands were readable; params of the rest were '
                       'not checked',
                       '4 action/quick_action reference(s) into commands, because commands is only '
                       'partly visible (5 key(s) read, the rest merged or built at runtime)']),
 'plain': (["actions[0]: command 'upload' takes or hands back a file, so it cannot be a Quick "
            'Action; it runs from Send Command on the device page',
            "actions[1]: command 'missing' is not a declared command"],
           []),
 'quick_actions_computed': (['quick_actions: must be a list of command ids'], []),
 'the_id_computed': (["actions[0]: missing required 'id' (non-empty string)",
                      "actions[1]: missing required 'id' (non-empty string)"],
                     []),
 'the_kind_computed': (["actions[0]: unknown kind '<unevaluated>' (expected one of ['command', "
                        "'setup', 'link'])"],
                       []),
 'the_target_computed': (["actions[0]: 'command' must be a string"], [])}


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_errors_their_order_and_their_tags(case: str) -> None:
    definition = _definition(case)
    issues = validate_driver_issues(definition)
    got = [(i["path"], i["message"]) for i in issues if i["severity"] == "error"]
    assert got == EXPECTED[case]
    assert list(validate_driver_definition(definition)) == [m for _, m in got]


def test_every_case_has_an_expectation() -> None:
    assert set(CASES) == set(EXPECTED)


@pytest.mark.parametrize("case", sorted(RAISES_EXPECTED))
def test_what_the_walk_raises_on(case: str) -> None:
    with pytest.raises(Exception) as raised:
        validate_driver_definition({**BASE, **RAISES[case]})
    assert type(raised.value).__name__ == RAISES_EXPECTED[case]


def test_every_raising_case_has_an_expectation() -> None:
    assert set(RAISES) == set(RAISES_EXPECTED)


@pytest.mark.parametrize("case", sorted(DIRECT_EXPECTED))
def test_the_shared_rule_on_its_own(case: str) -> None:
    assert validate_actions(DIRECT_CASES[case]) == DIRECT_EXPECTED[case]
    assert actions_validate_actions(DIRECT_CASES[case]) == DIRECT_EXPECTED[case]


def test_every_direct_case_has_an_expectation() -> None:
    assert set(DIRECT_CASES) == set(DIRECT_EXPECTED)


@pytest.mark.parametrize("case", sorted(PYTHON_EXPECTED))
def test_the_python_surface(case: str) -> None:
    info = python_info(case)
    issues, skips = PYTHON_EXPECTED[case]
    assert python_driver_info_issues(info) == issues
    assert python_driver_reference_skips(info) == skips


def test_every_python_case_has_an_expectation() -> None:
    assert set(PYTHON_CASES) == set(PYTHON_EXPECTED)
