"""A device audit command's sentence, word for word, for every part it can say.

The sentence the timeline, the wizard, the summary and the driver test report
all show for a command: each part in its place, and the wording of the
branches the end-to-end command tests never reach (a restart that never came
back, a query nobody answered, more moved values than the sentence names).
"""

from __future__ import annotations

import pytest

from openavc.audit.command_text import params_text, trial_outcome, trial_sentence


def _moved(key, label, first, last, **flags):
    return {"key": key, "label": label, "first": first, "last": last, **flags}


EVERY_PART = {
    "refusals": {
        "device_errors": 1, "last_error": "ERR 3", "unmatched": 2,
        "later": [
            {"error": "BUSY", "after": 3.2, "count": 2, "request": "POLL?"},
            {"error": "", "after": 4.0, "count": 1, "request": ""},
        ],
    },
    "sent_nothing": True,
    "effects": [{
        "state": "power", "state_key": "power", "label": "Power", "expected": True,
        "value": False, "outcome": "unchanged",
    }],
    "query": {
        "state": "input", "state_key": "input", "label": "Input", "value": "hdmi1",
        "outcome": "reported",
    },
    "moved": [
        # Named by the effect and the query already: not said again.
        _moved("power", "Power", False, True),
        _moved("input", "Input", "hdmi2", "hdmi1"),
        _moved("a", "A", 1, 2),
        _moved("b", "B", None, 2.0),
        _moved("c", "C", True, False),
        _moved("d", "D", 1, 5),
        _moved("meter", "Meter", 1, 2, already_moving=True),
        _moved("pulse", "Pulse", 0, 0, went_back=True),
    ],
    "drops": [{"after": 1.0, "back_after": 2.0}],
    "restart": {"back_after": None, "went_away_after": 4.0, "declared_seconds": 30},
    "traffic": {"received": 3},
}

CASES = [
    ("every part, in order", EVERY_PART,
     "the device refused it: ERR 3; the driver said it succeeded, but nothing was sent; "
     "Power is still No, not Yes; it reported Input as hdmi1; A went from 1 to 2, "
     "B went from not reported to 2, C went from Yes to No, 1 more value changed; "
     "the connection dropped 1.0 s after it was sent and the driver reconnected 2.0 s later; "
     "the device went away and had not come back when the audit stopped watching; "
     "the device refused a request the driver sent on its own 2 times, the first 3.2 s later "
     "(POLL?): BUSY; the device refused a request the driver sent on its own 4.0 s later; "
     "2 replies while it was watched matched none of the driver's rules."),
    ("not accepted says only that",
     {"error": "Volume must be 0 to 100", "effects": EVERY_PART["effects"]},
     "not accepted: Volume must be 0 to 100"),
    ("refused, the device saying nothing", {"refusals": {"device_errors": 2}},
     "the device refused it."),
    ("refused in a last_error alone", {"refusals": {"last_error": "BAD"}},
     "the device refused it: BAD."),
    ("a query nobody answered",
     {"query": {"state": "power", "state_key": "power", "outcome": "no_reply", "value": None}},
     "nothing came back."),
    ("a query answered without the value",
     {"query": {"state": "power", "state_key": "power", "outcome": "not_reported", "value": None}},
     "a reply came, but power was not reported."),
    ("a query value with no label",
     {"query": {"state": "power", "state_key": "power", "outcome": "reported", "value": True}},
     "it reported power as Yes."),
    ("two more moved than it names",
     {"moved": [_moved(k, k.upper(), 0, 1) for k in "abcde"]},
     "A went from 0 to 1, B went from 0 to 1, C went from 0 to 1, 2 more values changed."),
    ("exactly as many moved as it names",
     {"moved": [_moved(k, k.upper(), 0, 1) for k in "abc"]},
     "A went from 0 to 1, B went from 0 to 1, C went from 0 to 1."),
    ("a restart that came back",
     {"restart": {"back_after": 12.5, "went_away_after": 2.0, "declared_seconds": 30}},
     "the device came back 12.5 s after the command (the driver says up to 30 s)."),
    ("a restart that never went",
     {"restart": {"back_after": None, "went_away_after": None, "declared_seconds": 30}},
     "the connection stayed up."),
    ("one unmatched reply", {"refusals": {"unmatched": 1}},
     "1 reply while it was watched matched none of the driver's rules."),
    ("a later refusal, once",
     {"refusals": {"later": [{"error": "BUSY", "after": 1.5, "count": 1, "request": "POLL?"}]}},
     "the device refused a request the driver sent on its own 1.5 s later (POLL?): BUSY."),
    ("nothing moved, one reply", {"traffic": {"received": 1}},
     "no status value changed; the device sent 1 reply."),
    ("nothing moved, no reply", {"traffic": {"received": 0}},
     "no status value changed, and nothing came back."),
    ("only a value already moving",
     {"moved": [_moved("meter", "Meter", 1, 2, already_moving=True)], "traffic": {"received": 2}},
     "only values that were already changing moved; the device sent 2 replies."),
    ("only a value that went back",
     {"moved": [_moved("pulse", "Pulse", 0, 0, went_back=True)]},
     "values changed and went back to where they were, and nothing came back."),
    ("an empty record", {}, "no status value changed, and nothing came back."),
    ("an effect with no label",
     {"effects": [{"state": "volume", "state_key": "volume", "expected": 40, "value": None,
                   "outcome": "not_reported"}]},
     "the device has not reported volume."),
    ("each effect outcome",
     {"effects": [
         {"state": "p", "state_key": "p", "label": "P", "expected": True, "value": True,
          "outcome": outcome}
         for outcome in ("confirmed", "already", "different", "no_value")
     ]},
     "P is now Yes, as the driver says it should be; P was already Yes, so the audit cannot "
     "tell whether this command set it; P changed to Yes, not Yes; P was not checked: it "
     "depends on a value this command was not given."),
]


@pytest.mark.parametrize(("record", "sentence"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_a_commands_sentence(record, sentence):
    assert trial_sentence(record) == sentence


def test_an_outcome_stands_on_its_own():
    assert trial_outcome({"summary": "it worked."}) == "It worked."
    assert trial_outcome({"traffic": {"received": 0}}) == (
        "No status value changed, and nothing came back."
    )


def test_a_commands_parameters_as_a_line_reads_them():
    assert params_text({}) == ""
    assert params_text(
        {"level": 40, "input_id": "hdmi1", "on": True}, {"level": {"label": "Level"}},
    ) == " (Level 40, Input ID hdmi1, On Yes)"
    assert params_text({"x": None}, "not a dict") == " (X None)"
