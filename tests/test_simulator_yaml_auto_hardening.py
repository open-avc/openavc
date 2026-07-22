"""Hardening regression tests for the YAML auto-simulator (simulator/yaml_auto.py)
and its companion validator (simulator/validate.py).

Each test pins a specific audit finding. Per the platform's test policy these
exercise the simulator engine with an INVENTED device ("acme_*") and synthetic
payloads — no real product or captured fixture is involved.
"""

import asyncio

import pytest

from server.drivers.compiled_protocol import send_regex
from simulator.base import StateMachine
from simulator.validate import (
    ValidationResult,
    _build_auto_pattern,
    _check_state_machines,
)
from simulator.yaml_auto import YAMLAutoSimulator


def _make_sim(driver_def: dict, device_id: str = "dev1") -> YAMLAutoSimulator:
    return YAMLAutoSimulator(device_id=device_id, config={}, driver_def=driver_def)


# ===========================================================================
# H-034 / M-064 — state machines fire on command, time out, and reject
# ===========================================================================

_PROJECTOR_DEF = {
    "id": "acme_projector",
    "name": "Acme Projector",
    "transport": "tcp",
    "delimiter": "\\r",
    "state_variables": {},
    "commands": {
        "power_on": {"send": "PWRON"},
        "power_off": {"send": "PWROFF"},
        "set_input": {"send": "INP{input}", "params": {"input": {"type": "integer"}}},
    },
    "responses": [],
    "simulator": {
        "state_machines": {
            "power": {
                "states": ["off", "warming", "on", "cooling"],
                "initial": "off",
                "transitions": [
                    {"from": "off", "trigger": "power_on", "to": "warming"},
                    {"from": "warming", "after_seconds": 0.05, "to": "on"},
                    {"from": "on", "trigger": "power_off", "to": "cooling"},
                    {"from": "cooling", "after_seconds": 5.0, "to": "off"},
                    {"from": "cooling", "trigger": "*", "reject": True},
                ],
            },
        },
    },
}


@pytest.mark.asyncio
async def test_state_machine_triggers_on_command_and_times_out():
    """A command name fires the matching transition (H-034) and the entered
    state's after_seconds auto-transition arms and fires."""
    sim = _make_sim(_PROJECTOR_DEF)
    assert sim.get_state("power") == "off"

    sim.handle_command(b"PWRON")
    assert sim.get_state("power") == "warming"

    await asyncio.sleep(0.2)  # warming --after 0.05s--> on
    assert sim.get_state("power") == "on"


@pytest.mark.asyncio
async def test_state_machine_rejects_command_during_cooldown():
    """A reject transition suppresses the response for commands the device
    ignores in its current state (driven through the real warmup/cooldown)."""
    sim = _make_sim(_PROJECTOR_DEF)
    sim.handle_command(b"PWRON")        # off -> warming
    await asyncio.sleep(0.2)            # warming --after 0.05s--> on
    assert sim.get_state("power") == "on"
    sim.handle_command(b"PWROFF")       # on -> cooling (cooling->off is 5s away)
    assert sim.get_state("power") == "cooling"
    # During cooldown the projector ignores commands (reject "*") -> no response.
    assert sim.handle_command(b"PWRON") is None
    assert sim.get_state("power") == "cooling"  # still cooling, no transition

    # stop() cancels the pending cooldown timer (no leaked task).
    await sim.stop()


def test_state_machine_unrelated_command_does_not_transition():
    """A command with no matching transition leaves the machine untouched."""
    sim = _make_sim(_PROJECTOR_DEF)
    sim.handle_command(b"INP3")  # set_input — not a power trigger
    assert sim.get_state("power") == "off"


# ===========================================================================
# M-222 / M-223 / L-139 — transition resolution must be order-independent:
# an exact trigger beats a "*" wildcard wherever each appears in the list,
# reject resolves with the same rules, and every after_seconds arms.
# ===========================================================================

def _machine(transitions: list[dict], initial: str = "locked") -> StateMachine:
    return StateMachine(
        name="m",
        states=["locked", "idle", "ready", "error"],
        initial=initial,
        transitions=transitions,
        on_change=lambda key, val: None,
    )


@pytest.mark.parametrize("order", ["wildcard_first", "specific_first"])
def test_specific_transition_beats_wildcard_reject_in_any_order(order):
    """M-222/M-223: 'reject everything except unlock' works no matter which
    line the author writes first — the wildcard must not shadow the specific
    transition, and reordering must not change behavior."""
    specific = {"from": "locked", "trigger": "unlock", "to": "idle"}
    catch_all = {"from": "locked", "trigger": "*", "reject": True}
    transitions = (
        [catch_all, specific] if order == "wildcard_first" else [specific, catch_all]
    )
    sm = _machine(transitions)

    assert sm.is_rejected("unlock") is False
    assert sm.trigger("unlock") is True
    assert sm.current == "idle"

    sm.current = "locked"
    assert sm.is_rejected("other_cmd") is True
    assert sm.trigger("other_cmd") is False
    assert sm.current == "locked"


def test_specific_reject_beats_wildcard_accept():
    """The same specificity rule in the other direction: a named reject wins
    over a catch-all transition listed above it."""
    sm = _machine([
        {"from": "locked", "trigger": "*", "to": "idle"},
        {"from": "locked", "trigger": "forbidden", "reject": True},
    ])
    assert sm.is_rejected("forbidden") is True
    assert sm.trigger("forbidden") is False
    assert sm.current == "locked"
    assert sm.trigger("anything_else") is True
    assert sm.current == "idle"


@pytest.mark.asyncio
async def test_yaml_dispatch_allows_specific_command_past_wildcard_reject():
    """M-223 end-to-end: the YAML dispatch path consults is_rejected() before
    trigger(), so a '*' reject used to veto a specific allowed transition for
    the same state even when the specific line was listed first."""
    driver_def = {
        "id": "acme_lock",
        "name": "Acme Lock",
        "transport": "tcp",
        "delimiter": "\\r",
        "state_variables": {},
        "commands": {
            "unlock": {"send": "UNLOCK"},
            "lock": {"send": "LOCK"},
            "beep": {"send": "BEEP"},
        },
        "responses": [],
        "simulator": {
            "state_machines": {
                "door": {
                    "states": ["locked", "idle"],
                    "initial": "locked",
                    "transitions": [
                        {"from": "locked", "trigger": "*", "reject": True},
                        {"from": "locked", "trigger": "unlock", "to": "idle"},
                    ],
                },
            },
        },
    }
    sim = _make_sim(driver_def)
    sim.handle_command(b"BEEP")  # wildcard-rejected while locked
    assert sim.get_state("door") == "locked"
    sim.handle_command(b"UNLOCK")  # specific transition must not be vetoed
    assert sim.get_state("door") == "idle"
    await sim.stop()


@pytest.mark.asyncio
async def test_all_after_seconds_transitions_arm():
    """L-139: a state with a fallback timeout listed before a fast path must
    arm BOTH timers — only the first-listed used to arm. The first to fire
    wins and cancels the rest."""
    sm = _machine(
        [
            {"from": "locked", "after_seconds": 5.0, "to": "error"},
            {"from": "locked", "after_seconds": 0.05, "to": "ready"},
        ],
        initial="idle",
    )
    sm._enter_state("locked")  # arms the timers for the entered state
    assert len(sm._timer_tasks) == 2
    await asyncio.sleep(0.2)
    assert sm.current == "ready"  # fast path fired, fallback was cancelled
    assert all(t.done() for t in sm._timer_tasks)
    sm.cancel_timers()


def test_validator_warns_on_duplicate_trigger_transition():
    """A second entry for the same (from, trigger) pair is unreachable —
    the validator should say so instead of leaving it silent."""
    sim_def = {
        "state_machines": {
            "power": {
                "states": ["off", "on"],
                "initial": "off",
                "transitions": [
                    {"from": "off", "trigger": "power_on", "to": "on"},
                    {"from": "off", "trigger": "power_on", "reject": True},
                ],
            },
        },
    }
    result = ValidationResult("x", "x", "yaml")
    _check_state_machines(result, sim_def, {"power_on": {}})
    assert any("unreachable" in w.message for w in result.warnings)

    # Distinct triggers for the same from-state stay silent.
    sim_def["state_machines"]["power"]["transitions"][1] = {
        "from": "off", "trigger": "power_toggle", "to": "on",
    }
    result2 = ValidationResult("x", "x", "yaml")
    _check_state_machines(result2, sim_def, {"power_on": {}, "power_toggle": {}})
    assert not any("unreachable" in w.message for w in result2.warnings)


def test_validator_flags_malformed_state_machine():
    """M-064: a state_machine missing 'initial'/off-list state is an error, and
    a trigger naming no command is a warning — instead of a launch-time crash."""
    sim = {
        "state_machines": {
            "power": {
                "states": ["off", "on"],
                # initial omitted
                "transitions": [
                    {"from": "off", "trigger": "bogus_cmd", "to": "on"},
                    {"from": "on", "trigger": "power_off", "to": "nowhere"},
                ],
            },
        },
    }
    result = ValidationResult("x", "x", "yaml")
    _check_state_machines(result, sim, {"power_off": {}})

    msgs = " ".join(i.message for i in result.errors)
    assert "needs an 'initial'" in msgs
    assert "nowhere" in msgs  # 'to' state not in states
    warn_msgs = " ".join(i.message for i in result.warnings)
    assert "bogus_cmd" in warn_msgs  # trigger names no command


def test_validator_accepts_valid_state_machine():
    """A well-formed state machine passes cleanly."""
    result = ValidationResult("x", "x", "yaml")
    _check_state_machines(
        result,
        _PROJECTOR_DEF["simulator"],
        _PROJECTOR_DEF["commands"],
    )
    assert not result.errors


def test_malformed_state_machine_does_not_crash_construction():
    """M-064 runtime backstop: a malformed machine is skipped, not a KeyError."""
    bad = {
        **_PROJECTOR_DEF,
        "id": "acme_bad_sm",
        "simulator": {"state_machines": {"power": {"states": ["off"]}}},  # no initial/transitions
    }
    sim = _make_sim(bad)  # must not raise
    assert "power" not in sim._state_machines


# ===========================================================================
# H-033 — handler try/except over builtin exceptions works (not NameError)
# ===========================================================================


def test_script_handler_can_catch_builtin_exception():
    """A handler's `except ValueError` runs its branch instead of dying with a
    NameError from the emptied __builtins__ sandbox."""
    definition = {
        "id": "acme_script",
        "name": "Acme Script",
        "transport": "tcp",
        "delimiter": "\\r",
        "state_variables": {},
        "commands": {},
        "responses": [],
        "simulator": {
            "command_handlers": [
                {
                    "match": r"SET (.+)",
                    "handler": (
                        "try:\n"
                        "    n = int(match.group(1))\n"
                        "except ValueError:\n"
                        "    n = -1\n"
                        "state['value'] = n\n"
                        "respond(f'OK {n}')\n"
                    ),
                },
            ],
        },
    }
    sim = _make_sim(definition)
    resp = sim.handle_command(b"SET abc")
    assert sim.get_state("value") == -1  # except-branch ran
    assert resp == b"OK -1"


# ===========================================================================
# M-065 — OSC script handlers get the same builtins as TCP
# ===========================================================================


def test_osc_handler_namespace_has_full_builtins():
    """range/list/len (absent from the old OSC namespace) are available."""
    definition = {
        "id": "acme_osc",
        "name": "Acme OSC",
        "transport": "osc",
        "state_variables": {},
        "commands": {},
        "responses": [],
        "simulator": {
            "command_handlers": [
                {
                    "address": "/test",
                    "handler": "respond('/ok', [('i', len(list(range(3))))])",
                },
            ],
        },
    }
    sim = _make_sim(definition)
    handler = sim._osc_script_handlers[0]
    result = sim._execute_osc_script_handler(handler, "/test", [("f", 1.0)])
    assert result == [("/ok", [("i", 3)])]


# ===========================================================================
# M-066 — integer coercion returns the raw value (parity with the driver)
# ===========================================================================


def test_integer_coercion_returns_raw_on_failure():
    """A non-integer value is returned raw, not silently turned into 0."""
    definition = {
        "id": "acme_levels",
        "name": "Acme Levels",
        "transport": "tcp",
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
        "commands": {},
        "responses": [],
    }
    sim = _make_sim(definition)
    assert sim._coerce_value("level", "abc") == "abc"
    assert sim._coerce_value("level", "12.5") == "12.5"
    assert sim._coerce_value("level", "5") == 5


# ===========================================================================
# Auto-sim replies reconstructed from response patterns (shared emit
# inversion): anchors/escapes handled, $N picks the group, bool keys hit
# ===========================================================================


def test_query_reply_reconstructed_from_escaped_pattern():
    definition = {
        "id": "acme_projector",
        "name": "Acme Projector",
        "transport": "tcp",
        "state_variables": {"volume": {"type": "integer", "min": 0, "max": 100}},
        "commands": {"query_volume": {"send": "VOL?"}},
        "responses": [
            {"match": "\\(VOL!(\\d+)\\)", "set": {"volume": "$1"}},
        ],
    }
    sim = _make_sim(definition)
    # The reply is the reconstructed form the driver's own regex accepts —
    # escaped parens emit literal parens, the capture emits the state value.
    assert sim._dispatch_command(b"VOL?") == b"(VOL!0)\r\n"


def test_query_reply_places_value_at_referenced_group():
    definition = {
        "id": "acme_preset",
        "name": "Acme Preset",
        "transport": "tcp",
        "state_variables": {"preset_level": {"type": "integer", "min": 0, "max": 99}},
        "commands": {"query_preset_level": {"send": "PST?"}},
        "responses": [
            # preset_level reads group 2; group 1 is the preset name.
            {"match": "NAME (\\S+) LVL (-?\\d+)",
             "set": {"preset_level": "$2"}},
        ],
    }
    sim = _make_sim(definition)
    # {value} lands at group 2; group 1 emits representative content, so
    # the reply still matches the driver's pattern.
    assert sim._dispatch_command(b"PST?") == b"NAME 0 LVL 0\r\n"


def test_value_map_hits_boolean_state_values():
    definition = {
        "id": "acme_mute",
        "name": "Acme Mute",
        "transport": "tcp",
        "state_variables": {"mute": {"type": "boolean"}},
        "commands": {"query_mute": {"send": "MUTE?"}},
        "responses": [
            # YAML bare true/false arrive as Python bools — the value_map
            # key must normalize the way format() normalizes its lookup.
            {"match": "^Amt1$", "set": {"mute": True}},
            {"match": "^Amt0$", "set": {"mute": False}},
        ],
    }
    sim = _make_sim(definition)
    assert sim._dispatch_command(b"MUTE?") == b"Amt0\r\n"
    sim.set_state("mute", True)
    assert sim._dispatch_command(b"MUTE?") == b"Amt1\r\n"


# ===========================================================================
# Canonical mappings: response rules (the form the Driver Builder persists)
# feed the same query-reply machinery as the set: shorthand
# ===========================================================================


def test_mappings_rule_builds_same_template_as_set_rule():
    """A mappings-form rule with a group produces the identical reply
    template the equivalent set-form rule does."""
    base = {
        "id": "acme_switcher",
        "name": "Acme Switcher",
        "transport": "tcp",
        "state_variables": {"input": {"type": "integer", "min": 1, "max": 8}},
        "commands": {"query_input": {"send": "INP?"}},
    }
    sim_set = _make_sim({
        **base,
        "responses": [{"match": r"In(\d+) All", "set": {"input": "$1"}}],
    })
    sim_map = _make_sim({
        **base,
        "responses": [
            {"match": r"In(\d+) All",
             "mappings": [{"state": "input", "group": 1, "type": "integer"}]},
        ],
    }, device_id="dev2")
    assert sim_map._state_responses["input"].template == "In{value} All"
    assert (
        sim_map._state_responses["input"].template
        == sim_set._state_responses["input"].template
    )


def test_mappings_map_builds_reverse_value_map():
    """A mapping's map: (raw wire token -> friendly state value) is reversed
    for emission: the friendly value keys the wire text carrying the raw
    token, so the friendly text never lands on the wire."""
    definition = {
        "id": "acme_display",
        "name": "Acme Display",
        "transport": "tcp",
        "state_variables": {"power": {"type": "boolean"}},
        "commands": {"query_power": {"send": "PWR?", "query_for": "power"}},
        "responses": [
            {"match": r"PWR=(\d)",
             "mappings": [{"state": "power", "group": 1, "type": "boolean",
                           "map": {"1": True, "0": False}}]},
        ],
    }
    sim = _make_sim(definition)
    sr = sim._state_responses["power"]
    # Bool friendly values normalize lowercase, matching format()'s lookup.
    assert sr.value_map["true"] == "PWR=1"
    assert sr.value_map["false"] == "PWR=0"


def test_query_round_trip_from_mappings_rule():
    """query_for + a mappings rule answers the query with the current
    state's wire form (the send half and reply half both work for a
    Builder-authored driver)."""
    definition = {
        "id": "acme_projector",
        "name": "Acme Projector",
        "transport": "tcp",
        "state_variables": {"power": {"type": "string"}},
        "commands": {"query_power": {"send": "PWR?", "query_for": "power"}},
        "responses": [
            {"match": "PWR=(ON|OFF)",
             "mappings": [{"state": "power", "group": 1,
                           "map": {"ON": "on", "OFF": "off"}}]},
        ],
    }
    sim = _make_sim(definition)
    sim.set_state("power", "off")
    assert sim._dispatch_command(b"PWR?") == b"PWR=OFF\r\n"
    sim.set_state("power", "on")
    assert sim._dispatch_command(b"PWR?") == b"PWR=ON\r\n"


def test_osc_address_and_json_mappings_rules_build_no_state_responses():
    """OSC address rules and json rules keep producing zero state-response
    entries (address handlers / explicit simulators cover those)."""
    definition = {
        "id": "acme_console",
        "name": "Acme Console",
        "transport": "osc",
        "state_variables": {"mute": {"type": "boolean"},
                            "level": {"type": "float"}},
        "commands": {},
        "responses": [
            {"address": "/acme/mute",
             "mappings": [{"state": "mute", "arg": 0, "type": "boolean",
                           "map": {"1": True, "0": False}}]},
            {"json": True, "match": r"(\{.*\})",
             "mappings": [{"state": "level", "group": 1,
                           "json_path": "level", "type": "float"}]},
        ],
    }
    sim = _make_sim(definition)
    assert sim._state_responses == {}


# ===========================================================================
# M-067 — child_id command params capture digits, not greedy (.+)
# ===========================================================================


def test_child_id_param_captures_digits():
    rx = send_regex(
        "{out}*{inp}!",
        {"out": {"type": "child_id"}, "inp": {"type": "integer"}},
    )
    assert rx == r"(\d+)\*(\d+)!"


# ===========================================================================
# Format-spec send templates ({name:spec}) match and decode in the auto-sim
# ===========================================================================


def test_format_spec_command_matches_and_decodes_hex():
    definition = {
        "id": "acme_hexvol",
        "name": "Acme Hex Volume",
        "transport": "tcp",
        "state_variables": {"volume": {"type": "integer", "min": 0, "max": 100}},
        "commands": {
            "set_volume": {
                "send": "VOL{volume:02X}",
                "params": {"volume": {"type": "integer", "min": 0, "max": 100}},
            },
        },
        "responses": [],
    }
    sim = _make_sim(definition)
    sim._dispatch_command(b"VOL1A")
    # The hex wire value decodes back to the number the driver sent, so an
    # integer state var holds 26 — not the raw string "1A".
    assert sim._state["volume"] == 26


def test_send_template_with_trailing_terminator_matches():
    definition = {
        "id": "acme_term",
        "name": "Acme Terminator",
        "transport": "tcp",
        "state_variables": {"level": {"type": "integer", "min": 0, "max": 99}},
        "commands": {
            "set_level": {
                # Single-quoted-YAML style: literal backslash-r terminator
                # plus a trailing space, as several corpus drivers author it.
                "send": "SET {level:d} \\r",
                "params": {"level": {"type": "integer", "min": 0, "max": 99}},
            },
        },
        "responses": [],
    }
    sim = _make_sim(definition)
    # The simulator dispatch strips each delimiter-split line, so the
    # pattern must not require the terminator.
    sim._dispatch_command(b"SET 7")
    assert sim._state["level"] == 7


def test_auto_pattern_keeps_trailing_letter():
    # The validator's old private copy rstripped the character set
    # {'\\', 'r', 'n'} off the end of the finished pattern, so a template
    # ending in a real 'n' lost it ("power_on" became ^power_o$) and the
    # coverage check false-failed.
    pat = _build_auto_pattern("power_on", {})
    assert pat is not None
    assert pat.match("power_on")


# ===========================================================================
# L-044 — a handler syntax error skips that handler, not the whole device
# ===========================================================================


def test_handler_syntax_error_does_not_abort_device():
    definition = {
        "id": "acme_syntax",
        "name": "Acme Syntax",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "responses": [],
        "simulator": {
            "command_handlers": [
                {"match": "BAD", "handler": "this is not valid python ::"},
                {"match": "GOOD", "handler": "respond('ok')"},
            ],
        },
    }
    sim = _make_sim(definition)  # must not raise
    assert sim.handle_command(b"GOOD") == b"ok"
    assert sim.handle_command(b"BAD") is None  # bad handler was skipped


# ===========================================================================
# L-045 / L-046 / L-047 — integer initial state, clamp rounding, bad bounds
# ===========================================================================


def test_integer_initial_state_from_fractional_min_is_int():
    definition = {
        "id": "acme_vol",
        "name": "Acme Vol",
        "transport": "tcp",
        "state_variables": {"vol": {"type": "integer", "label": "Vol", "min": 0.5, "max": 10}},
        "commands": {},
        "responses": [],
    }
    sim = _make_sim(definition)
    assert sim.get_state("vol") == 1  # ceil(0.5)
    assert isinstance(sim.get_state("vol"), int)


def test_integer_clamp_rounds_inward_for_fractional_bounds():
    definition = {
        "id": "acme_vol2",
        "name": "Acme Vol2",
        "transport": "tcp",
        "state_variables": {"vol": {"type": "integer", "label": "Vol", "min": 2.5, "max": 7.5}},
        "commands": {},
        "responses": [],
    }
    sim = _make_sim(definition)
    sim.set_state("vol", 0)  # below 2.5 -> ceil = 3 (still >= min, not truncated to 2)
    assert sim.get_state("vol") == 3
    sim.set_state("vol", 100)  # above 7.5 -> floor = 7
    assert sim.get_state("vol") == 7


def test_non_numeric_bounds_do_not_crash():
    """L-047: a non-numeric min/max is ignored rather than crashing set_state
    or _coerce_value."""
    definition = {
        "id": "acme_badbounds",
        "name": "Acme BadBounds",
        "transport": "tcp",
        "state_variables": {"vol": {"type": "integer", "label": "Vol", "min": "abc"}},
        "commands": {},
        "responses": [],
    }
    sim = _make_sim(definition)
    sim.set_state("vol", 5)  # must not raise
    assert sim.get_state("vol") == 5
    assert sim._coerce_value("vol", "5") == 5  # must not raise


# ===========================================================================
# H-032 — notification templates substitute the documented {value}/{key}
# ===========================================================================


def test_notification_substitutes_value_and_key():
    """Pins the documented notification contract ({value}, {key}). The stray
    {channel} placeholder was never implemented and is no longer advertised."""
    definition = {
        "id": "acme_notify",
        "name": "Acme Notify",
        "transport": "tcp",
        "delimiter": "\\r",
        "state_variables": {"power": {"type": "boolean", "label": "Power"}},
        "commands": {},
        "responses": [],
        "simulator": {
            "notifications": {"power": {"true": "PWR {key}={value}"}},
        },
    }
    sim = _make_sim(definition)
    captured: list[str] = []
    sim._notification_map  # ensure parsed
    # Resolve the template the same way set_state does.
    template = sim._notification_map["power"]["true"]
    msg = template.replace("{value}", "True").replace("{key}", "power")
    captured.append(msg)
    assert captured == ["PWR power=True"]
    assert "{channel}" not in template


# ===========================================================================
# HTTP response delay — an authored 0 must mean "instant", not fall through
# ===========================================================================

def _make_http_sim(delays: dict) -> YAMLAutoSimulator:
    return _make_sim({
        "id": "acme_http",
        "name": "Acme HTTP",
        "transport": "http",
        "state_variables": {},
        "commands": {},
        "responses": [],
        "simulator": {"delays": delays},
    })


def test_http_delay_zero_beats_request_response_alias():
    """`command_response: 0` is an explicit instant-reply choice; the old
    `get(...) or get("request_response", 0)` treated 0 as unset and fell
    through to the alias."""
    sim = _make_http_sim({"command_response": 0, "request_response": 5})
    assert sim._http_response_delay() == 0


def test_http_delay_seed_shadows_request_response_alias():
    """YAML auto-sims always seed command_response (0.05 default), so the
    undocumented request_response alias never applies to them."""
    sim = _make_http_sim({"request_response": 2})
    assert sim._http_response_delay() == 0.05


def test_http_delay_defaults_to_auto_seed():
    sim = _make_http_sim({})
    assert sim._http_response_delay() == 0.05


def test_http_delay_uses_command_response():
    sim = _make_http_sim({"command_response": 0.5})
    assert sim._http_response_delay() == 0.5


# ===========================================================================
# Declared command semantics (sets: / query_for:) drive the auto-sim;
# name inference is only the fallback, and the old built-in single-letter
# query map (I/V/Z/P) is gone in favor of declarations.
# ===========================================================================


def test_declared_sets_applies_literals_and_param_refs():
    definition = {
        "id": "acme_declared",
        "name": "Acme Declared",
        "transport": "tcp",
        "state_variables": {
            "power": {"type": "boolean"},
            "input": {"type": "integer", "min": 1, "max": 8},
        },
        "commands": {
            "warmup": {
                # Name matches nothing; declaration carries the semantics.
                "send": "GO",
                "sets": {"power": True},
            },
            "pick": {
                "send": "IN{inp}!",
                "params": {"inp": {"type": "integer", "min": 1, "max": 8}},
                "sets": {"input": "{inp}"},
            },
        },
        "responses": [],
    }
    sim = _make_sim(definition)
    sim._dispatch_command(b"GO")
    assert sim._state["power"] is True
    sim._dispatch_command(b"IN5!")
    assert sim._state["input"] == 5


def test_declared_sets_integer_literal_is_not_a_group_index():
    definition = {
        "id": "acme_intlit",
        "name": "Acme Int Literal",
        "transport": "tcp",
        "state_variables": {"input": {"type": "integer", "min": 1, "max": 8}},
        "commands": {
            # No params at all — a bare int literal must land verbatim, not
            # be read as "capture group 5" (which doesn't exist).
            "preset_five": {"send": "P5!", "sets": {"input": 5}},
        },
        "responses": [],
    }
    sim = _make_sim(definition)
    sim._dispatch_command(b"P5!")
    assert sim._state["input"] == 5


def test_declared_sets_param_refs_use_template_order_groups():
    definition = {
        "id": "acme_order",
        "name": "Acme Order",
        "transport": "tcp",
        "state_variables": {
            "input": {"type": "integer", "min": 1, "max": 99},
            "output": {"type": "integer", "min": 1, "max": 99},
        },
        "commands": {
            "route": {
                "send": "{out}*{inp}!",
                # Params declared in the OPPOSITE order of the template —
                # the capture groups must follow the template, not the dict.
                "params": {
                    "inp": {"type": "integer", "min": 1, "max": 99},
                    "out": {"type": "integer", "min": 1, "max": 99},
                },
                "sets": {"input": "{inp}", "output": "{out}"},
            },
        },
        "responses": [],
    }
    sim = _make_sim(definition)
    sim._dispatch_command(b"3*7!")
    assert sim._state["output"] == 3
    assert sim._state["input"] == 7


def test_declared_sets_decodes_hex_spec_param():
    definition = {
        "id": "acme_hexset",
        "name": "Acme Hex Sets",
        "transport": "tcp",
        "state_variables": {
            "master_volume": {"type": "integer", "min": 0, "max": 100},
        },
        "commands": {
            # The param name does NOT match the state var — exactly the case
            # name inference can never connect; the declaration does.
            "set_volume": {
                "send": "MVL{level:02X}",
                "params": {"level": {"type": "integer", "min": 0, "max": 100}},
                "sets": {"master_volume": "{level}"},
            },
        },
        "responses": [],
    }
    sim = _make_sim(definition)
    sim._dispatch_command(b"MVL1A")
    assert sim._state["master_volume"] == 26


def test_declared_sets_beats_name_inference():
    definition = {
        "id": "acme_contra",
        "name": "Acme Contradiction",
        "transport": "tcp",
        "state_variables": {
            "power": {"type": "boolean"},
            "mute": {"type": "boolean"},
        },
        "commands": {
            # The name says power_on; the declaration says it mutes. The
            # declaration wins outright — no heuristic mixing.
            "power_on": {"send": "X1", "sets": {"mute": True}},
        },
        "responses": [],
    }
    sim = _make_sim(definition)
    sim._dispatch_command(b"X1")
    assert sim._state["mute"] is True
    assert sim._state.get("power") is not True


def test_declared_query_for_on_command_answers_with_state():
    definition = {
        "id": "acme_qcmd",
        "name": "Acme Query Command",
        "transport": "tcp",
        "state_variables": {"level": {"type": "integer", "min": 0, "max": 99}},
        "commands": {
            # "check" infers to nothing by name; the declaration pairs it.
            "check": {"send": "STA?", "query_for": "level"},
        },
        "responses": [
            {"match": r"LVL(\d+)", "set": {"level": "$1"}},
        ],
        "simulator": {"initial_state": {"level": 42}},
    }
    sim = _make_sim(definition)
    reply = sim._dispatch_command(b"STA?")
    assert reply is not None
    assert reply.decode().strip() == "LVL42"


def test_declared_query_for_on_polling_dict_entry():
    definition = {
        "id": "acme_qdict",
        "name": "Acme Query Dict",
        "transport": "tcp",
        "state_variables": {"input": {"type": "integer", "min": 1, "max": 8}},
        "commands": {},
        "responses": [
            {"match": r"In(\d+) All", "set": {"input": "$1"}},
        ],
        "polling": {"queries": [{"send": "I", "query_for": "input"}]},
        "simulator": {"initial_state": {"input": 3}},
    }
    sim = _make_sim(definition)
    reply = sim._dispatch_command(b"I")
    assert reply is not None
    assert reply.decode().strip() == "In3 All"


def test_single_letter_query_map_is_retired():
    # "V" used to be answered via a built-in vendor-flavored map
    # (I/V/Z/P → input/volume/mute/power). Without a declaration the
    # simulator no longer invents the pairing.
    definition = {
        "id": "acme_nomap",
        "name": "Acme No Map",
        "transport": "tcp",
        "state_variables": {"volume": {"type": "integer", "min": 0, "max": 100}},
        "commands": {},
        "responses": [
            {"match": r"Vol(\d+)", "set": {"volume": "$1"}},
        ],
        "polling": {"queries": ["V"]},
        "simulator": {"initial_state": {"volume": 50}},
    }
    sim = _make_sim(definition)
    assert sim._dispatch_command(b"V") is None


def test_on_connect_query_for_registers_query_handler():
    definition = {
        "id": "acme_onconn",
        "name": "Acme On Connect",
        "transport": "tcp",
        "state_variables": {"temp": {"type": "integer", "min": 0, "max": 200}},
        "commands": {},
        "responses": [
            {"match": r"TEMP(\d+)", "set": {"temp": "$1"}},
        ],
        "on_connect": [{"send": "STATUS?", "query_for": "temp"}],
        "simulator": {"initial_state": {"temp": 71}},
    }
    sim = _make_sim(definition)
    reply = sim._dispatch_command(b"STATUS?")
    assert reply is not None
    assert reply.decode().strip() == "TEMP71"


def test_declared_sets_keeps_wire_form_for_string_var():
    # Hex-code protocols (eISCP-style) declare the 2-digit code itself as
    # the state value: the var is a string, so the wire capture is kept
    # verbatim rather than decoded to a number.
    definition = {
        "id": "acme_hexcode",
        "name": "Acme Hex Code",
        "transport": "tcp",
        "state_variables": {"volume_code": {"type": "string"}},
        "commands": {
            "set_volume": {
                "send": "MVL{level:02X}",
                "params": {"level": {"type": "integer", "min": 0, "max": 100}},
                "sets": {"volume_code": "{level}"},
            },
        },
        "responses": [],
    }
    sim = _make_sim(definition)
    sim._dispatch_command(b"MVL1A")
    assert sim._state["volume_code"] == "1A"
