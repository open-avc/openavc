"""More pins for the simulator validator's reply checks, through ``validate_yaml_driver``.

The cases the reply-side pins left open: a handler entry that carries two
forms at once, ``respond()`` calls the reader passes over or cannot read, a
handler body that is indented, a ``{state.key}`` used twice, a response pattern
filled from ``config_derived``, a reply longer than its example, the text
trailing several replies, and a ``notifications:`` section on a transport that
cannot push, with no keys to check against, or naming another state value.
Each case is an invented Acme driver; the expected lines are what the
validator says today, messages and order exact.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from openavc.simulator.validate import validate_yaml_driver


_BASE: dict = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "delimiter": "\\r\\n",
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
        "level": {"type": "integer", "min": 3, "label": "Level"},
        "name": {"type": "string", "label": "Name"},
    },
    "commands": {
        "power_on": {"send": "PWR 1\\r\\n"},
    },
    "responses": [
        {"match": "^PWR (\\d)$", "set": {"power": "$1"}},
        {"match": "^LVL (\\d+)$", "set": {"level": "$1"}},
    ],
}


def _with(**changes) -> dict:
    """The base driver with top-level keys replaced (None removes one)."""
    definition = copy.deepcopy(_BASE)
    for key, value in changes.items():
        if value is None:
            definition.pop(key, None)
        else:
            definition[key] = value
    return definition


def _polled(*handlers, **changes) -> dict:
    """The base driver polling ``STATUS?``, with these handlers."""
    return _with(polling={"queries": ["STATUS?"]},
                 simulator={"command_handlers": list(handlers)}, **changes)


def _notifying(notifications, initial_state=None, **changes) -> dict:
    definition = _with(**changes)
    sim: dict = {"notifications": notifications}
    if initial_state is not None:
        sim["initial_state"] = initial_state
    definition["simulator"] = sim
    return definition


def _replying(*replies: str, **changes) -> dict:
    """The base driver with one command per reply, each answered by a
    ``respond:`` template sent verbatim."""
    commands = {f"c{n}": {"send": f"C{n}\\r\\n"} for n in range(len(replies))}
    handlers = [{"receive": f"C{n}", "respond": reply} for n, reply in enumerate(replies)]
    return _with(commands=commands, simulator={"command_handlers": handlers}, **changes)


CASES: dict[str, dict] = {
    # ── response_parsing ──
    "parsing_no_responses_says_nothing": _polled(
        {"receive": "STATUS\\?", "respond": "NOPE\r\n"}, responses=[],
    ),
    "parsing_responses_left_out_says_nothing": _polled(
        {"receive": "STATUS\\?", "respond": "NOPE\r\n"}, responses=None,
    ),
    "parsing_template_braces_are_read": _polled(
        {"receive": "STATUS\\?", "respond": "VAL {x}\r\n"},
    ),
    "parsing_script_literal_with_one_brace_is_read": _polled(
        {"receive": "STATUS\\?", "handler": "respond('A{B\\r\\n')\n"},
    ),
    "parsing_script_literal_with_both_braces_is_unresolved": _polled(
        {"receive": "STATUS\\?", "handler": "respond('A{B}\\r\\n')\n"},
    ),
    "parsing_respond_wins_over_handler": _polled(
        {"receive": "STATUS\\?", "respond": "PWR 1\r\n", "handler": "respond('XX\\r\\n')\n"},
    ),
    "parsing_handler_named_by_receive_over_match": _polled(
        {"receive": "STATUS\\?", "match": "OTHER", "respond": "NOPE\r\n"},
    ),
    "parsing_handler_named_by_match": _polled(
        {"match": "STATUS\\?", "respond": "NOPE\r\n"},
    ),
    "parsing_method_respond_is_not_read": _polled(
        {"receive": "STATUS\\?", "handler": "sim.respond('NOPE\\r\\n')\n"},
    ),
    "parsing_keyword_only_respond_is_passed_over": _polled(
        {"receive": "STATUS\\?", "handler": "respond(text='X')\nrespond('NOPE\\r\\n')\n"},
    ),
    "parsing_non_text_constant_is_unreadable": _polled(
        {"receive": "STATUS\\?", "handler": "respond(1)\nrespond('NOPE\\r\\n')\n"},
    ),
    "parsing_bytes_constant_is_unreadable": _polled(
        {"receive": "STATUS\\?", "handler": "respond(b'X')\nrespond('NOPE\\r\\n')\n"},
    ),
    "parsing_indented_handler_body_is_read": _polled(
        {"receive": "STATUS\\?", "handler": "  respond('NOPE\\r\\n')\n"},
    ),
    "parsing_bare_return_reads_nothing": _polled(
        {"receive": "STATUS\\?", "handler": "return\nrespond('NOPE\\r\\n')\n"},
    ),
    "parsing_two_digit_capture_ref_is_unresolved": _polled(
        {"receive": "STATUS\\?", "respond": "LVL {12}\r\n"},
    ),
    "parsing_state_ref_used_twice": _with(
        simulator={
            "initial_state": {"name": "a"},
            "command_handlers": [{"receive": "NM", "respond": "NM {state.name} {state.name}\r\n"}],
        },
    ),
    "parsing_pattern_filled_from_config_derived": _polled(
        {"receive": "STATUS\\?", "respond": "Z1:PWR 1\r\n"},
        default_config={"zone": "1"},
        config_derived={"prefix": "Z{zone}:"},
        responses=[{"match": "^{prefix}PWR (\\d)$", "set": {"power": "$1"}}],
    ),
    # ── reply_framing ──
    "framing_long_unframed_reply_is_cut_at_forty": _replying(
        "PWR 1 0123456789012345678901234567890123456789XYZ",
    ),
    "framing_trailing_prompts_sorted_once": _replying(
        "\r\nPWR 1\r\n>", "\r\nPWR 1\r\n#", "\r\nPWR 1\r\n>",
        responses=[
            {"match": "^PWR (\\d)$", "set": {"power": "$1"}},
            {"match": "^[>#]$", "set": {}},
        ],
    ),
    "framing_exactly_three_trailing_replies": _replying(
        "PWR 1\r\nA", "PWR 1\r\nB", "PWR 1\r\nC",
    ),
    "framing_four_trailing_replies": _replying(
        "PWR 1\r\nA", "PWR 1\r\nB", "PWR 1\r\nC", "PWR 1\r\nD",
    ),
    "framing_prompt_read_with_config_derived": _replying(
        "\r\nPWR 1\r\nZ1>",
        default_config={"zone": "1"},
        config_derived={"prompt": "Z{zone}>"},
        responses=[
            {"match": "^PWR (\\d)$", "set": {"power": "$1"}},
            {"match": "^{prompt}$", "set": {}},
        ],
    ),
    # ── notifications ──
    "notifications_empty_on_http": _notifying({}, transport="http", delimiter=None),
    "notifications_empty_on_osc": _notifying({}, transport="osc", delimiter=None),
    "notifications_boolean_capitalised_value_key": _notifying(
        {"power": {"True": "PWR {value:d}\r\n"}},
    ),
    "notifications_no_known_keys": _notifying(
        {"anything": "PWR 1\r\n"}, state_variables={},
    ),
    "notifications_pattern_filled_from_config_derived": _notifying(
        {"power": "Z1:PWR 1\r\n"},
        default_config={"zone": "1"},
        config_derived={"prefix": "Z{zone}:"},
        responses=[{"match": "^{prefix}PWR (\\d)$", "set": {"power": "$1"}}],
    ),
    "notifications_other_state_value": _notifying(
        {"power": "NOPE {state.level}\r\n"}, initial_state={"level": 5, "power": False},
    ),
}


#: The checks these cases exercise; the other checks' lines are not pinned here.
_CHECKS = frozenset({"notifications", "response_parsing", "reply_framing"})


def _issues(tmp_path, name: str) -> list[tuple[str, str, str]] | tuple[str, str, str]:
    """The pinned checks' lines, or what the validator raises."""
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(CASES[name], sort_keys=False), encoding="utf-8")
    try:
        result = validate_yaml_driver(path)
    except Exception as exc:  # noqa: BLE001 - what raises is pinned too
        return ("raises", type(exc).__name__, str(exc))
    return [(i.severity, i.check, i.message) for i in result.issues if i.check in _CHECKS]


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_reply_checks_say_what_they_said(tmp_path, name):
    assert _issues(tmp_path, name) == EXPECTED[name]


def test_every_case_has_an_expectation():
    assert sorted(EXPECTED) == sorted(CASES)


EXPECTED: dict = {'framing_exactly_three_trailing_replies': [('info',
                                             'response_parsing',
                                             '3 simulator reply/replies match no driver response '
                                             'rule, none of them the only answer to a polled query '
                                             '— the shape of a write acknowledgement, which '
                                             "carries no state: 'A', 'B', 'C'. A polled query "
                                             'whose answer nothing parses warns instead.'),
                                            ('warning',
                                             'reply_framing',
                                             '3 simulator replies end with text after the last '
                                             "'\\\\r\\\\n', which reaches the driver only when the "
                                             "next reply arrives, joined to its first line: 'C0' "
                                             "-> b'PWR 1\\r\\nA'; 'C1' -> b'PWR 1\\r\\nB'; 'C2' -> "
                                             "b'PWR 1\\r\\nC'. End the reply with the delimiter, "
                                             'or, for a prompt the device prints after each reply, '
                                             'open every reply with the delimiter and give the '
                                             'prompt a response rule of its own.')],
 'framing_four_trailing_replies': [('info',
                                    'response_parsing',
                                    '4 simulator reply/replies match no driver response rule, none '
                                    'of them the only answer to a polled query — the shape of a '
                                    "write acknowledgement, which carries no state: 'A', 'B', 'C', "
                                    "'D'. A polled query whose answer nothing parses warns "
                                    'instead.'),
                                   ('warning',
                                    'reply_framing',
                                    '4 simulator replies end with text after the last '
                                    "'\\\\r\\\\n', which reaches the driver only when the next "
                                    "reply arrives, joined to its first line: 'C0' -> b'PWR "
                                    "1\\r\\nA'; 'C1' -> b'PWR 1\\r\\nB'; 'C2' -> b'PWR 1\\r\\nC'; "
                                    '+1 more. End the reply with the delimiter, or, for a prompt '
                                    'the device prints after each reply, open every reply with the '
                                    'delimiter and give the prompt a response rule of its own.')],
 'framing_long_unframed_reply_is_cut_at_forty': [('info',
                                                  'response_parsing',
                                                  '1 simulator reply/replies match no driver '
                                                  'response rule, none of them the only answer to '
                                                  'a polled query — the shape of a write '
                                                  "acknowledgement, which carries no state: 'PWR 1 "
                                                  "0123456789012345678901234567890123456789XYZ'. A "
                                                  'polled query whose answer nothing parses warns '
                                                  'instead.'),
                                                 ('error',
                                                  'reply_framing',
                                                  '1 of 1 simulator replies come back without the '
                                                  "'\\\\r\\\\n' this driver splits incoming bytes "
                                                  'on, so the driver never completes a frame and '
                                                  "reads nothing they carry: 'C0' -> b'PWR 1 "
                                                  "0123456789012345678901234567890123'. A reply "
                                                  'written by hand — a respond: template or a '
                                                  'respond() call in a handler — is sent verbatim, '
                                                  'so end it with the delimiter; only the handlers '
                                                  'the simulator generates are terminated for '
                                                  'you.')],
 'framing_prompt_read_with_config_derived': [('info',
                                              'reply_framing',
                                              "1 simulator reply ends with 'Z1>' after the last "
                                              "'\\\\r\\\\n', as a device that prints a prompt "
                                              'after each reply does; every reply opens with '
                                              "'\\\\r\\\\n', so the next one completes it as a "
                                              "line a response rule reads: 'C0' -> b'\\r\\nPWR "
                                              "1\\r\\nZ1>'.")],
 'framing_trailing_prompts_sorted_once': [('info',
                                           'reply_framing',
                                           "3 simulator replies end with '#', '>' after the last "
                                           "'\\\\r\\\\n', as a device that prints a prompt after "
                                           "each reply does; every reply opens with '\\\\r\\\\n', "
                                           'so the next one completes it as a line a response rule '
                                           "reads: 'C0' -> b'\\r\\nPWR 1\\r\\n>'; 'C1' -> "
                                           "b'\\r\\nPWR 1\\r\\n#'; 'C2' -> b'\\r\\nPWR "
                                           "1\\r\\n>'.")],
 'notifications_boolean_capitalised_value_key': [('info',
                                                  'reply_framing',
                                                  '1 simulator reply checked, all terminated with '
                                                  "'\\\\r\\\\n'."),
                                                 ('error',
                                                  'notifications',
                                                  "notification 'power' value key 'True' never "
                                                  'matches — boolean lookups use lowercase '
                                                  "'true'/'false' (quote the keys in YAML so they "
                                                  'stay strings)'),
                                                 ('warning',
                                                  'notifications',
                                                  "notification 'power' emits 'PWR True\\r\\n' "
                                                  'which matches no driver response pattern — the '
                                                  'driver will ignore it')],
 'notifications_empty_on_http': [],
 'notifications_empty_on_osc': [],
 'notifications_no_known_keys': [],
 'notifications_other_state_value': [('info',
                                      'reply_framing',
                                      '1 simulator reply checked, all terminated with '
                                      "'\\\\r\\\\n'."),
                                     ('warning',
                                      'notifications',
                                      "notification 'power' emits 'NOPE 5\\r\\n' which matches no "
                                      'driver response pattern — the driver will ignore it')],
 'notifications_pattern_filled_from_config_derived': [('info',
                                                       'reply_framing',
                                                       '1 simulator reply checked, all terminated '
                                                       "with '\\\\r\\\\n'.")],
 'parsing_bare_return_reads_nothing': [('info',
                                        'reply_framing',
                                        '1 simulator reply checked, all terminated with '
                                        "'\\\\r\\\\n'.")],
 'parsing_bytes_constant_is_unreadable': [('info',
                                           'response_parsing',
                                           '1 simulator reply/replies match no driver response '
                                           'rule, none of them the only answer to a polled query — '
                                           'the shape of a write acknowledgement, which carries no '
                                           "state: 'NOPE'. A polled query whose answer nothing "
                                           'parses warns instead.'),
                                          ('info',
                                           'reply_framing',
                                           '1 simulator reply checked, all terminated with '
                                           "'\\\\r\\\\n'.")],
 'parsing_handler_named_by_match': [('warning',
                                     'response_parsing',
                                     'Simulator answers a query this driver polls with a reply no '
                                     "response rule matches: handler='STATUS\\\\?', "
                                     "response='NOPE'. Nothing reads that answer, so the state "
                                     'behind the query never updates.'),
                                    ('info',
                                     'reply_framing',
                                     '2 simulator replies checked, all terminated with '
                                     "'\\\\r\\\\n'.")],
 'parsing_handler_named_by_receive_over_match': [('warning',
                                                  'response_parsing',
                                                  'Simulator answers a query this driver polls '
                                                  'with a reply no response rule matches: '
                                                  "handler='STATUS\\\\?', response='NOPE'. Nothing "
                                                  'reads that answer, so the state behind the '
                                                  'query never updates.'),
                                                 ('info',
                                                  'reply_framing',
                                                  '2 simulator replies checked, all terminated '
                                                  "with '\\\\r\\\\n'.")],
 'parsing_indented_handler_body_is_read': [('warning',
                                            'response_parsing',
                                            'Simulator answers a query this driver polls with a '
                                            'reply no response rule matches: '
                                            "handler='STATUS\\\\?', response='NOPE'. Nothing reads "
                                            'that answer, so the state behind the query never '
                                            'updates.'),
                                           ('info',
                                            'reply_framing',
                                            '1 simulator reply checked, all terminated with '
                                            "'\\\\r\\\\n'.")],
 'parsing_keyword_only_respond_is_passed_over': [('warning',
                                                  'response_parsing',
                                                  'Simulator answers a query this driver polls '
                                                  'with a reply no response rule matches: '
                                                  "handler='STATUS\\\\?', response='NOPE'. Nothing "
                                                  'reads that answer, so the state behind the '
                                                  'query never updates.'),
                                                 ('error',
                                                  'reply_framing',
                                                  '1 of 2 simulator replies come back without the '
                                                  "'\\\\r\\\\n' this driver splits incoming bytes "
                                                  'on, so the driver never completes a frame and '
                                                  "reads nothing they carry: 'STATUS?' -> b'X'. A "
                                                  'reply written by hand — a respond: template or '
                                                  'a respond() call in a handler — is sent '
                                                  'verbatim, so end it with the delimiter; only '
                                                  'the handlers the simulator generates are '
                                                  'terminated for you.')],
 'parsing_method_respond_is_not_read': [('info',
                                         'reply_framing',
                                         '1 simulator reply checked, all terminated with '
                                         "'\\\\r\\\\n'.")],
 'parsing_no_responses_says_nothing': [('info',
                                        'reply_framing',
                                        '1 simulator reply checked, all terminated with '
                                        "'\\\\r\\\\n'.")],
 'parsing_non_text_constant_is_unreadable': [('info',
                                              'response_parsing',
                                              '1 simulator reply/replies match no driver response '
                                              'rule, none of them the only answer to a polled '
                                              'query — the shape of a write acknowledgement, which '
                                              "carries no state: 'NOPE'. A polled query whose "
                                              'answer nothing parses warns instead.'),
                                             ('info',
                                              'reply_framing',
                                              '1 simulator reply checked, all terminated with '
                                              "'\\\\r\\\\n'.")],
 'parsing_pattern_filled_from_config_derived': [('info',
                                                 'reply_framing',
                                                 '2 simulator replies checked, all terminated with '
                                                 "'\\\\r\\\\n'.")],
 'parsing_respond_wins_over_handler': [('info',
                                        'reply_framing',
                                        '2 simulator replies checked, all terminated with '
                                        "'\\\\r\\\\n'.")],
 'parsing_responses_left_out_says_nothing': [('info',
                                              'reply_framing',
                                              '1 simulator reply checked, all terminated with '
                                              "'\\\\r\\\\n'.")],
 'parsing_script_literal_with_both_braces_is_unresolved': [('info',
                                                            'reply_framing',
                                                            '2 simulator replies checked, all '
                                                            "terminated with '\\\\r\\\\n'.")],
 'parsing_script_literal_with_one_brace_is_read': [('warning',
                                                    'response_parsing',
                                                    'Simulator answers a query this driver polls '
                                                    'with a reply no response rule matches: '
                                                    "handler='STATUS\\\\?', response='A{B'. "
                                                    'Nothing reads that answer, so the state '
                                                    'behind the query never updates.'),
                                                   ('info',
                                                    'reply_framing',
                                                    '2 simulator replies checked, all terminated '
                                                    "with '\\\\r\\\\n'.")],
 'parsing_state_ref_used_twice': [('info',
                                   'response_parsing',
                                   '1 simulator reply/replies match no driver response rule, none '
                                   'of them the only answer to a polled query — the shape of a '
                                   "write acknowledgement, which carries no state: 'NM a a'. A "
                                   'polled query whose answer nothing parses warns instead.'),
                                  ('info',
                                   'reply_framing',
                                   "1 simulator reply checked, all terminated with '\\\\r\\\\n'.")],
 'parsing_template_braces_are_read': [('warning',
                                       'response_parsing',
                                       'Simulator answers a query this driver polls with a reply '
                                       "no response rule matches: handler='STATUS\\\\?', "
                                       "response='VAL {x}'. Nothing reads that answer, so the "
                                       'state behind the query never updates.'),
                                      ('info',
                                       'reply_framing',
                                       '2 simulator replies checked, all terminated with '
                                       "'\\\\r\\\\n'.")],
 'parsing_two_digit_capture_ref_is_unresolved': [('info',
                                                  'reply_framing',
                                                  '2 simulator replies checked, all terminated '
                                                  "with '\\\\r\\\\n'.")]}
