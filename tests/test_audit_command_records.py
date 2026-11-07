"""What a device audit records about a driver's commands, case by case.

The command list a driver's declaration reads as, the verdict a command's
window ends with, and the values the audit left changed, each for the cases
the end-to-end command tests never reach: a declared value nobody gave, a
driver whose traffic is not captured, a restart back exactly on its declared
time, a status table read after the driver stopped, a setting written before
the first command.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace as NS

from openavc.audit.commands import (
    CommandPass,
    CommandTrial,
    changed_values,
    command_catalog,
    declared_effects,
    moved_values,
)
from openavc.core.device_traffic import RX, TX

INFO = {
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "volume": {"type": "integer", "label": "Volume"},
    },
    "child_entity_types": {
        "channel": {
            "label": "Channel", "id_type": "integer",
            "state_variables": {"mute": {"label": "Mute"}, "level": {}},
        },
    },
    "commands": {
        "power_on": {"label": "Power On", "sets": {"power": True}},
        "set_volume": {
            "label": "Set Volume", "sets": {"volume": "{level}"},
            "params": {"level": {"type": "integer"}},
        },
        "query_mute": {
            "label": "Query Mute", "query_for": "mute",
            "params": {"ch": {"type": "child_id", "child_type": "channel"}},
        },
        "query_power": {"label": "Query Power", "query_for": "power"},
        "query_volume_of": {
            "label": "Query Volume Of", "query_for": "volume",
            "params": {"ch": {"type": "child_id", "child_type": "channel"}},
        },
        "guarded": {"label": "Guarded", "confirm": "Sure?"},
        "polled": {"label": "Polled"},
        "raw_poll": {"label": "Raw"},
        "bad_restart": {"label": "Bad Restart", "restarts_device_for": "soon"},
        "own_confirm": {"label": "Own Confirm", "confirm": True},
        "confirm_number": {"label": "Confirm Number", "confirm": 1},
        "action_confirm": {"label": "Action Confirm"},
        "reboot": {"label": "Reboot", "restarts_device_for": 5},
        "two_children": {
            "label": "Two Children", "sets": {"mute": True},
            "params": {
                "a": {"type": "child_id", "child_type": "channel"},
                "b": {"type": "child_id", "child_type": "channel"},
            },
        },
        "set_mute": {
            "label": "Set Mute", "sets": {"mute": "{on}", "volume": 3},
            "params": {
                "ch": {"type": "child_id", "child_type": "channel"}, "on": {"type": "boolean"},
            },
        },
    },
    "actions": [
        {"id": "a", "kind": "command", "command": "action_confirm", "label": "Go", "confirm": True},
        {"id": "r", "kind": "command", "command": "reboot", "confirm": "Really restart?"},
        # On a device page with no confirmation: its own still counts.
        {"id": "g", "kind": "command", "command": "guarded", "confirm": False},
    ],
    "quick_actions": ["power_on", "own_confirm", "confirm_number", "polled", "bad_restart"],
}


def _driver(info=INFO, fmt=lambda ctype, local: f"{int(local):02d}"):
    return NS(
        DRIVER_INFO=info, config={}, format_child_id=fmt,
        _definition={"polling": {"queries": [{"send": "polled"}, "raw_poll", "NOT A COMMAND"]}},
    )


# -- the command list -----------------------------------------------------------


def test_the_command_list_reads_each_declaration():
    catalog = {entry["name"]: entry for entry in command_catalog(_driver())}
    # Polled by name, a dict query or a plain one: a status query.
    assert catalog["polled"]["query"] and catalog["polled"]["polled"]
    assert catalog["raw_poll"]["query"] and catalog["raw_poll"]["polled"]
    assert not catalog["power_on"]["query"]
    # A restart time that is not a number reads as none.
    assert catalog["bad_restart"]["restarts_device_for"] == 0
    assert catalog["bad_restart"]["suggested"]
    # confirm: true is a plain sentence; a number is no confirmation at all.
    assert catalog["own_confirm"]["confirm"] == (
        "The driver asks for a confirmation before Own Confirm runs."
    )
    assert not catalog["own_confirm"]["suggested"]
    assert catalog["confirm_number"]["confirm"] == ""
    assert catalog["confirm_number"]["suggested"]
    # An action's confirm: true names the action.
    assert catalog["action_confirm"]["confirm"] == (
        "The driver asks for a confirmation before Go runs."
    )
    assert catalog["reboot"]["confirm"] == "Really restart?"
    assert catalog["guarded"]["confirm"] == "Sure?"
    assert not catalog["guarded"]["suggested"]
    assert [e["name"] for e in command_catalog(_driver())] == list(INFO["commands"])


# -- what a command declares it sets ----------------------------------------------


def test_a_declared_value_nobody_gave_is_not_checked():
    entry = {**INFO["commands"]["set_mute"], "name": "set_mute"}
    assert declared_effects(entry, {"ch": "1", "on": ""}, _driver()) == [
        {"state": "mute", "state_key": "channel.01.mute", "expected": None, "has_value": False},
        {"state": "volume", "state_key": "volume", "expected": 3, "has_value": True},
    ]
    assert declared_effects(entry, {"ch": "1", "on": False}, _driver())[0] == {
        "state": "mute", "state_key": "channel.01.mute", "expected": False, "has_value": True,
    }


def test_a_child_id_the_driver_cannot_format_is_used_as_given():
    def refuse(ctype, local):
        raise ValueError("no")

    entry = {**INFO["commands"]["set_mute"], "name": "set_mute"}
    assert declared_effects(entry, {"ch": "7", "on": True}, _driver(fmt=refuse))[0]["state_key"] == (
        "channel.7.mute"
    )
    # Coerced to the kind the child type declares before it is padded.
    seen = []
    declared_effects(entry, {"ch": "7", "on": True}, _driver(fmt=lambda t, i: seen.append(i) or i))
    assert seen == [7]


def test_two_child_ids_address_no_child():
    entry = {**INFO["commands"]["two_children"], "name": "two_children"}
    assert declared_effects(entry, {"a": "1", "b": "2"}, _driver())[0]["state_key"] == "mute"


# -- the verdict a window ends with --------------------------------------------------


def _pass(state, *, frames=True, started=True, table=None, traffic=(), events=()):
    observer = NS(
        traffic=list(traffic), events=list(events), redactor=lambda: None,
        frames=lambda: [object()] if frames else [],
    )
    sandbox = NS(
        started=started, device_state=lambda: dict(state), observer=observer, driver=_driver(),
    )
    attempt = NS(sandbox=sandbox, status_table=lambda: table or {})
    run = NS(listens=[attempt], listen=attempt, index=0, settings=None,
             choice=NS(driver_id="acme", identity={}))
    return CommandPass(NS(), run)


def _trial(command, params=None, *, declared=None, before=None, changes=(), **fields):
    entry = INFO["commands"][command]
    if declared is None:
        declared = {k: entry[k] for k in ("sets", "query_for", "restarts_device_for") if k in entry}
    return CommandTrial(
        number=1, command=command, label=entry["label"], params=params or {}, attempt=1,
        batch=False, connect_attempt=0, sent_at=100.0, returned_at=100.5, declared=declared,
        before=before or {}, changes=[{"key": k} for k in changes], **fields,
    )


def _entry(direction, t):
    return NS(direction=direction, chunk=False, channel="", t=t)


def test_each_declared_outcome():
    commands = _pass({"power": True, "volume": 30})
    no_value = _trial("set_volume")
    commands._conclude(no_value)
    assert no_value.effects == [{
        "state": "volume", "state_key": "volume", "expected": None, "has_value": False,
        "label": "Volume", "value": 30, "outcome": "no_value",
    }]
    for before, changes, outcome in (
        ({"power": True}, (), "already"),
        ({"power": True}, ("power",), "confirmed"),
        ({"power": False}, (), "confirmed"),
    ):
        trial = _trial("power_on", before=before, changes=changes)
        commands._conclude(trial)
        assert trial.effects[0]["outcome"] == outcome, (before, changes)
    for state, changes, outcome in (
        ({"volume": 10}, ("volume",), "different"),
        ({"volume": 10}, (), "unchanged"),
        ({}, ("volume",), "not_reported"),
    ):
        trial = _trial("set_volume", {"level": 40})
        trial.changes = [{"key": k} for k in changes]
        _pass(state)._conclude(trial)
        assert trial.effects[0]["outcome"] == outcome, (state, changes)


def test_a_query_reads_its_childs_value():
    reply = [_entry(TX, 100.1), _entry(RX, 100.2)]
    answered = _trial("query_mute", {"ch": "2"}, changes=("channel.02.mute",))
    _pass({"channel.02.mute": True}, traffic=reply)._conclude(answered)
    assert answered.query == {
        "state": "mute", "state_key": "channel.02.mute", "label": "Channel 02 Mute",
        "value": True, "changed": True, "outcome": "reported",
    }
    silent = _trial("query_power")
    _pass({"power": True}, traffic=[_entry(TX, 100.1)])._conclude(silent)
    assert (silent.query["outcome"], silent.query["changed"]) == ("no_reply", False)
    # A child command's query of a value the child type does not declare
    # reads the device's own.
    own = _trial("query_volume_of", {"ch": "2"})
    _pass({"volume": 7, "channel.02.volume": 1}, traffic=reply)._conclude(own)
    assert (own.query["state_key"], own.query["value"]) == ("volume", 7)
    empty = _trial("query_power")
    _pass({}, traffic=reply)._conclude(empty)
    assert empty.query["outcome"] == "not_reported"


def test_whether_anything_was_sent_needs_the_traffic():
    uncaptured = _trial("power_on")
    _pass({}, frames=False)._conclude(uncaptured)
    assert uncaptured.sent_nothing is None
    captured = _trial("power_on")
    _pass({}, frames=True)._conclude(captured)
    assert captured.sent_nothing is True
    refused = _trial("power_on", error="Not connected")
    _pass({}, frames=True)._conclude(refused)
    assert (refused.sent_nothing, refused.effects) == (None, [])


def test_unmatched_replies_keep_five_examples_of_200_characters():
    events = [NS(kind="unmatched_response", detail={"text": f"{i}" * 300}) for i in range(7)]
    events.append(NS(kind="undeclared_state", detail={}))
    trial = _trial("power_on")
    _pass({}, events=events)._conclude(trial)
    assert trial.refusals["unmatched"] == 7
    assert trial.refusals["unmatched_examples"] == [f"{i}" * 200 for i in range(5)]


def test_drops_and_a_restart_are_timed_from_the_send():
    dropped = _trial("power_on", went_away_at=110.0, error="gone")
    dropped.gone_and_back = [[110.0, 118.34], [130.0, None]]
    _pass({})._conclude(dropped)
    assert dropped.drops == [{"after": 10.0, "back_after": 8.3}, {"after": 30.0, "back_after": None}]
    assert dropped.restart is None
    on_time = _trial("reboot", went_away_at=101.04, back_at=105.0)
    _pass({})._conclude(on_time)
    assert on_time.drops == []
    assert on_time.restart == {
        "declared_seconds": 5, "went_away_after": 1.0, "back_after": 5.0, "away_for": 4.0,
        "within_declared": True,
    }
    late = _trial("reboot", went_away_at=101.0, back_at=105.01)
    _pass({})._conclude(late)
    assert late.restart["within_declared"] is False
    never = _trial("reboot")
    _pass({})._conclude(never)
    assert never.restart == {
        "declared_seconds": 5, "went_away_after": None, "back_after": None, "away_for": None,
        "within_declared": None,
    }


def test_a_window_closed_after_the_driver_stopped_reads_the_kept_table():
    table = {
        "variables": [
            {"name": "power", "value": True, "reported": True},
            {"name": "volume", "value": 9, "reported": False},
        ],
        "children": {"channel": {"03": {"mute": False}}},
    }
    trial = _trial("set_mute", {"ch": "3", "on": False})
    _pass({}, started=False, table=table)._conclude(trial)
    assert [(e["state_key"], e["value"], e["outcome"]) for e in trial.effects] == [
        ("channel.03.mute", False, "confirmed"), ("volume", None, "not_reported"),
    ]


# -- what the audit left changed -------------------------------------------------


def _run(now, trials, *, settings=None, unwatched=None, started=True, table=None):
    listen = NS(
        sandbox=NS(started=started, driver=NS(DRIVER_INFO=INFO), device_state=lambda: dict(now)),
        status_table=lambda: table or {},
    )
    return NS(
        commands=NS(trials=trials), settings=settings, listen=listen,
        unwatched=unwatched or {}, choice=NS(driver_id="acme"),
    )


def _sent(number, at, before, changes=(), moving=()):
    return NS(
        number=number, label=f"Command {number}", sent_at=at, before=before,
        changes=[{"key": k} for k in changes], already_moving=list(moving),
    )


def test_a_setting_written_first_is_where_what_changed_starts():
    setting = NS(started_at=50.0, before={"power": True, "volume": 9})
    run = _run(
        {"power": True, "volume": 5}, [_sent(1, 100.0, {"power": False, "volume": 1}, ("power",))],
        settings=NS(trials=[setting]),
    )
    assert [(c["key"], c["before"]) for c in changed_values(run)] == [("volume", 9)]


def test_a_value_of_another_type_has_changed():
    run = _run(
        {"level": True, "mode": 1, "gone": None},
        [_sent(1, 100.0, {"level": 1, "mode": 1, "gone": 4})],
    )
    # A value no longer reported is left out: there is nothing to compare.
    assert [c["key"] for c in changed_values(run)] == ["level"]
    moved = moved_values(
        [{"key": "level", "old": 1, "new": True}, {"key": "level", "old": True, "new": 1.0}], [], INFO,
    )
    assert moved[0]["went_back"] is False


def test_a_value_moved_unwatched_after_its_command_moves_on_its_own():
    trial = _sent(1, 100.0, {"clock": 1, "level": 1}, ("clock", "level"))
    run = _run(
        {"clock": 3, "level": 4}, [trial],
        unwatched={"clock": deque([150.0]), "level": deque([90.0])},
    )
    assert {c["key"]: c["on_its_own"] for c in changed_values(run)} == {
        "clock": True, "level": False,
    }


def test_what_changed_after_the_driver_stopped_reads_the_kept_table():
    table = {
        "variables": [{"name": "power", "value": True, "reported": True}],
        "children": {"channel": {"01": {"mute": True}}},
    }
    run = _run({}, [_sent(1, 100.0, {"power": False})], started=False, table=table)
    assert [(c["key"], c["label"], c["now"]) for c in changed_values(run)] == [
        ("channel.01.mute", "Channel 01 Mute", True), ("power", "Power", True),
    ]
