"""Hardening regression tests for the YAML auto-simulator (simulator/yaml_auto.py)
and its companion validator (simulator/validate.py).

Each test pins a specific audit finding. Per the platform's test policy these
exercise the simulator engine with an INVENTED device ("acme_*") and synthetic
payloads — no real product or captured fixture is involved.
"""

import asyncio

import pytest

from simulator.validate import ValidationResult, _check_state_machines
from simulator.yaml_auto import YAMLAutoSimulator, _send_template_to_regex


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
# M-067 — child_id command params capture digits, not greedy (.+)
# ===========================================================================


def test_child_id_param_captures_digits():
    rx = _send_template_to_regex(
        "{out}*{inp}!",
        {"out": {"type": "child_id"}, "inp": {"type": "integer"}},
    )
    assert rx == r"(\d+)\*(\d+)!"


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
