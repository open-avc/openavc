"""A YAML driver's device_settings can be exercised in simulation.

A setting's ``write`` carries the same templates a command's ``send`` does, so
the auto-generated simulator builds a handler from it. Before that it built
none: a settings write fell through the whole dispatch chain, logged
*unrecognized command* and was dropped, and because the setting write path now
waits for the device to report the value back, the setting stayed pending
forever and was re-sent on every connect.

Nothing in the catalog showed it, which is why it went unnoticed: every driver
that declares settings also hand-writes a ``simulator:`` section whose handlers
happen to match its own write strings. So every driver here has NO such
section -- that is the case under test.

Invented device ("acme_*") and synthetic payloads throughout, per the
platform's test policy.
"""

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator


def _sim(driver_def: dict) -> YAMLAutoSimulator:
    return YAMLAutoSimulator(device_id="dev1", config={}, driver_def=driver_def)


def _tcp_def(**overrides) -> dict:
    """An invented TCP driver with settings and deliberately no simulator:."""
    base = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "transport": "tcp",
        "delimiter": "\\r",
        "state_variables": {
            "gain": {"type": "integer", "default": 0},
            "label": {"type": "string", "default": ""},
            "auto_mode": {"type": "boolean", "default": False},
        },
        "commands": {},
        "responses": [
            {"match": r"^GAIN=(\d+)$", "set": {"gain": "$1"}},
            {"match": r"^LABEL=(.+)$", "set": {"label": "$1"}},
            {"match": r"^AUTO=(\d)$", "set": {"auto_mode": "$1"}},
        ],
        "device_settings": {
            "gain": {
                "type": "integer",
                "label": "Gain",
                "write": {"send": "GAIN={value}"},
            },
        },
    }
    base.update(overrides)
    return base


# ──── The gap itself ────


def test_a_settings_write_is_recognised_and_lands_in_state():
    sim = _sim(_tcp_def())
    reply = sim.handle_command(b"GAIN=7")
    assert reply is not None, "the settings write was dropped as unrecognized"
    assert sim._state.get("gain") == 7


def test_the_write_is_reported_back_so_the_setting_does_not_stay_pending():
    """The half that matters most.

    A setting write waits for the device to say the value took. A handler that
    changed state silently would leave it pending forever and re-sent on every
    connect, which is a worse failure than the silence it replaced.
    """
    sim = _sim(_tcp_def())
    reply = sim.handle_command(b"GAIN=7")
    assert reply is not None
    assert b"GAIN=7" in reply, f"nothing reported the value back: {reply!r}"


def test_a_value_nothing_declares_is_still_unrecognized():
    """Both halves of the rule: a simulator that answered everything would
    pass the tests above without having learned anything."""
    sim = _sim(_tcp_def())
    assert sim.handle_command(b"NOTACOMMAND") is None


# ──── The value types ────


@pytest.mark.parametrize(
    "setting,wire,state_key,expected",
    [
        ({"type": "integer", "write": {"send": "GAIN={value}"}},
         b"GAIN=12", "gain", 12),
        ({"type": "string", "write": {"send": "LABEL={value}"}},
         b"LABEL=Front Left", "label", "Front Left"),
        # A boolean with a numeric format spec goes on the wire as 1/0, which
        # is how a fixed-width protocol writes a flag.
        ({"type": "boolean", "write": {"send": "AUTO={value:d}"}},
         b"AUTO=1", "auto_mode", True),
        # An enum's members are wire tokens of any shape, so it captures like
        # a string rather than getting a digits-only capture.
        ({"type": "enum", "values": ["lo", "hi"], "write": {"send": "LABEL={value}"}},
         b"LABEL=hi", "label", "hi"),
    ],
)
def test_each_setting_type_captures_the_form_it_puts_on_the_wire(
    setting, wire, state_key, expected
):
    setting = {**setting, "state_key": state_key}
    sim = _sim(_tcp_def(device_settings={"the_setting": setting}))
    assert sim.handle_command(wire) is not None, f"{wire!r} was not recognised"
    assert sim._state.get(state_key) == expected


def test_a_hex_formatted_write_decodes_back_to_the_number():
    """{value:02X} put hex on the wire, and (\\d+) could never match it back."""
    sim = _sim(_tcp_def(device_settings={
        "gain": {"type": "integer", "state_key": "gain",
                 "write": {"send": "GAIN={value:02X}"}},
    }))
    # The response rule reports decimal; the write carries hex. 0x1F == 31.
    assert sim.handle_command(b"GAIN=1F") is not None
    assert sim._state.get("gain") == 31


# ──── Where the value lands ────


def test_state_key_decides_where_the_value_lands_not_the_setting_name():
    sim = _sim(_tcp_def(device_settings={
        "front_gain": {"type": "integer", "state_key": "gain",
                       "write": {"send": "GAIN={value}"}},
    }))
    assert sim.handle_command(b"GAIN=4") is not None
    assert sim._state.get("gain") == 4
    assert sim._state.get("front_gain") is None


def test_a_setting_with_no_state_key_lands_under_its_own_name():
    sim = _sim(_tcp_def(
        state_variables={"gain": {"type": "integer", "default": 0}},
        device_settings={"gain": {"type": "integer",
                                  "write": {"send": "GAIN={value}"}}},
    ))
    assert sim.handle_command(b"GAIN=5") is not None
    assert sim._state.get("gain") == 5


# ──── Framing and the other transports ────


def test_a_command_prefix_is_NOT_added_to_a_settings_write():
    """A command is framed with the driver's prefix and suffix on the way out.
    ``set_device_setting`` does no such thing -- it substitutes, escape-decodes,
    applies send_frame and sends -- so a handler built with the prefix in front
    would look for a line the wire never carries.

    The catalog agrees: the one prefixed driver that declares settings spells
    its prefix into every write string by hand.
    """
    sim = _sim(_tcp_def(command_prefix="#"))
    assert sim.handle_command(b"GAIN=3") is not None, "the write went unmatched"
    assert sim._state.get("gain") == 3


def test_a_prefix_written_into_the_template_by_hand_is_matched_as_written():
    sim = _sim(_tcp_def(
        command_prefix="#",
        responses=[{"match": r"^#GAIN=(\d+)$", "set": {"gain": "$1"}}],
        device_settings={"gain": {"type": "integer",
                                  "write": {"send": "#GAIN={value}"}}},
    ))
    assert sim.handle_command(b"#GAIN=3") is not None
    assert sim._state.get("gain") == 3


def test_a_config_placeholder_is_substituted_before_the_pattern_is_built():
    """A write can address a unit by a config field. The driver substitutes it
    before sending, so a pattern keeping the literal braces never matches."""
    sim = YAMLAutoSimulator(
        device_id="dev1", config={"unit_id": 4},
        driver_def=_tcp_def(
            default_config={"unit_id": 1},
            responses=[{"match": r"^04GAIN=(\d+)$", "set": {"gain": "$1"}}],
            device_settings={"gain": {"type": "integer",
                                      "write": {"send": "{unit_id:02d}GAIN={value}"}}},
        ),
    )
    assert sim.handle_command(b"04GAIN=2") is not None
    assert sim._state.get("gain") == 2
    assert sim.handle_command(b"01GAIN=2") is None, "the default_config form matched"


def test_the_default_config_supplies_a_placeholder_the_device_config_omits():
    sim = _sim(_tcp_def(
        default_config={"unit_id": 1},
        responses=[{"match": r"^01GAIN=(\d+)$", "set": {"gain": "$1"}}],
        device_settings={"gain": {"type": "integer",
                                  "write": {"send": "{unit_id:02d}GAIN={value}"}}},
    ))
    assert sim.handle_command(b"01GAIN=2") is not None
    assert sim._state.get("gain") == 2


def test_a_write_that_sets_then_reads_back_matches_on_the_set():
    """A real shape in the catalog: the write is a set and a read-back query
    joined by the terminator, sent as one blob and arriving as two lines. The
    query is answered by whatever answers queries; only the segment carrying
    the value is the write."""
    sim = _sim(_tcp_def(device_settings={
        "gain": {"type": "integer",
                 "write": {"send": "GAIN={value}\rGAIN?\r"}},
    }))
    assert sim.handle_command(b"GAIN=6") is not None
    assert sim._state.get("gain") == 6


def test_a_write_whose_value_never_reaches_the_wire_builds_nothing():
    """No {value} means no capture, and a handler with no capture would set the
    state key to nothing every time it matched."""
    sim = _sim(_tcp_def(device_settings={
        "gain": {"type": "integer", "write": {"send": "RESETGAIN"}},
    }))
    assert not [h for h in sim._command_handlers if h.name.startswith("setting:")]


def test_an_http_settings_write_matches_the_line_respond_http_synthesizes():
    """respond_http turns a request into "METHOD /path|body" and runs it
    through this same chain, so one handler serves both transports."""
    sim = _sim(_tcp_def(
        transport="http",
        device_settings={
            "gain": {"type": "integer",
                     "write": {"method": "put", "path": "/api/gain?v={value}"}},
        },
    ))
    assert sim.handle_command(b"PUT /api/gain?v=9") is not None
    assert sim._state.get("gain") == 9


def test_an_http_write_with_a_body_matches_the_body_section_too():
    sim = _sim(_tcp_def(
        transport="http",
        device_settings={
            "gain": {"type": "integer",
                     "write": {"path": "/api/set", "body": "gain={value}"}},
        },
    ))
    # No method declared: the write path defaults to POST.
    assert sim.handle_command(b"POST /api/set|gain=6") is not None
    assert sim._state.get("gain") == 6


def test_an_osc_settings_write_builds_no_handler():
    """A deliberate boundary, not an oversight: OSC never reaches this chain
    (handle_message answers from the response address mappings instead), and a
    handler built here could only ever be dead code claiming coverage."""
    sim = _sim(_tcp_def(device_settings={
        "gain": {"type": "integer",
                 "write": {"address": "/acme/gain", "args": [{"type": "i"}]}},
    }))
    assert not [h for h in sim._command_handlers if h.name.startswith("setting:")]


# ──── Not stepping on what a driver already wrote ────


def test_a_hand_written_simulator_handler_still_wins():
    """Every driver in the catalog that declares settings also hand-writes a
    simulator: section. Those must keep answering exactly as they did, or this
    fix breaks the 18 drivers it was meant to leave alone."""
    sim = _sim(_tcp_def(simulator={
        "command_handlers": [
            {"match": r"^GAIN=(\d+)$", "set_state": {"gain": "99"},
             "respond": "OVERRIDDEN"},
        ],
    }))
    reply = sim.handle_command(b"GAIN=7")
    assert reply is not None and b"OVERRIDDEN" in reply
    assert sim._state.get("gain") == 99


def test_a_command_wins_a_collision_with_a_setting():
    """Same wire form declared both ways resolves to the command -- the thing
    the driver named -- because settings handlers are appended after it."""
    sim = _sim(_tcp_def(
        commands={"set_gain": {"send": "GAIN={value}",
                               "params": {"value": {"type": "integer"}},
                               "sets": {"label": "{value}"}}},
    ))
    assert sim.handle_command(b"GAIN=8") is not None
    assert sim._state.get("label") == "8", "the setting handler took the line"


def test_a_setting_with_no_write_builds_nothing():
    sim = _sim(_tcp_def(device_settings={"gain": {"type": "integer"}}))
    assert not [h for h in sim._command_handlers if h.name.startswith("setting:")]


def test_a_driver_with_no_settings_at_all_is_untouched():
    sim = _sim(_tcp_def(device_settings={}))
    assert not [h for h in sim._command_handlers if h.name.startswith("setting:")]
