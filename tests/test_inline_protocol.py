"""Tests for the inline-protocol feature — a device that carries its own
commands / responses / state_variables in its project-file config, merged over
the (usually empty) file definition and run by the existing ConfigurableDriver
engine.

These are platform tests: they use an invented generic device (``acme_widget``)
and synthetic payloads, never a real driver or captured fixtures.
"""

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import (
    normalize_config_commands,
    normalize_config_responses,
    create_configurable_driver_class,
)

# A bare "generic" definition: a transport and nothing else. The protocol is
# expected to come entirely from the device config.
GENERIC_DEF = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "transport": "tcp",
    "default_config": {"host": "", "port": 4000},
    "config_schema": {},
    "state_variables": {},
    "commands": {},
    "responses": [],
}


class _SpySend:
    """Connected transport that records send() calls."""

    def __init__(self):
        self.connected = True
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _make(config):
    """Instantiate a fresh acme_widget with the given device config."""
    cls = create_configurable_driver_class(GENERIC_DEF)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(f"acme_{id(config)}", config, state, events), cls


# ── Merge: no inline config is a no-op ─────────────────────────────────────


def test_no_inline_config_is_noop():
    """A device with no config protocol keeps sharing the class definition."""
    drv, cls = _make({"host": "10.0.0.5"})
    # The instance still points at the shared class definition (no copy made).
    assert drv._definition is cls._definition
    assert drv.DRIVER_INFO["commands"] == {}


# ── Commands ───────────────────────────────────────────────────────────────


async def test_config_command_dict_shape_sends():
    """A dict-shaped config command formats params and sends bytes."""
    drv, _ = _make(
        {
            "commands": {
                "set_volume": {
                    "label": "Set Volume",
                    "send": "VOL{level}",
                    "params": {"level": {"type": "integer", "required": True}},
                },
            },
        }
    )
    # Visible to the engine and to the IDE (per-instance DRIVER_INFO).
    assert "set_volume" in drv._definition["commands"]
    assert drv.DRIVER_INFO["commands"]["set_volume"]["label"] == "Set Volume"
    assert "level" in drv.DRIVER_INFO["commands"]["set_volume"]["params"]

    spy = _SpySend()
    drv.transport = spy
    await drv.send_command("set_volume", {"level": 42})
    assert spy.sent == [b"VOL42"]


async def test_config_command_flat_string_shape_is_coerced():
    """The legacy flat ``{name: "raw string"}`` shape is promoted to {send:.}."""
    drv, _ = _make({"commands": {"ping": "PWR1"}})
    assert drv._definition["commands"]["ping"] == {"send": "PWR1"}

    spy = _SpySend()
    drv.transport = spy
    await drv.send_command("ping")
    assert spy.sent == [b"PWR1"]


async def test_config_command_escape_sequences_encoded():
    """Escape sequences in a config command are encoded on send."""
    drv, _ = _make({"commands": {"poll": "STATUS?\\r\\n"}})
    spy = _SpySend()
    drv.transport = spy
    await drv.send_command("poll")
    assert spy.sent == [b"STATUS?\r\n"]


async def test_config_commands_as_json_string_tolerated():
    """Commands authored as a JSON string (hand/AI-edited) still parse."""
    drv, _ = _make({"commands": '{"ping": "PWR1"}'})
    assert drv._definition["commands"]["ping"] == {"send": "PWR1"}


def test_command_placeholders_become_params():
    """A {placeholder} in a send string auto-declares a param so the Send
    Command card prompts for it."""
    drv, _ = _make(
        {"commands": {"set_vol": {"label": "Set Volume", "send": "VOL {level}"}}}
    )
    assert "level" in drv.DRIVER_INFO["commands"]["set_vol"]["params"]


def test_config_field_placeholder_is_not_a_param():
    """A placeholder that names a config field resolves from config, not a
    prompt — so it is not declared as a param."""
    drv, _ = _make({"host": "1.2.3.4", "commands": {"q": {"send": "STAT {host}"}}})
    assert "host" not in drv.DRIVER_INFO["commands"]["q"]["params"]


def test_http_command_placeholders_in_path_and_body_become_params():
    """HTTP commands carry placeholders in path/body, not send — those become
    params too."""
    drv, _ = _make(
        {
            "commands": {
                "set_input": {
                    "label": "Set Input",
                    "method": "PUT",
                    "path": "/api/input/{input_id}",
                    "body": '{"level": {level}}',
                },
            },
        }
    )
    params = drv.DRIVER_INFO["commands"]["set_input"]["params"]
    assert "input_id" in params
    assert "level" in params


# ── Line ending: appended once to each command ─────────────────────────────


async def test_line_ending_appended_to_commands():
    """The device's delimiter is appended to each command so the user doesn't
    type it per row."""
    drv, _ = _make({"delimiter": "\r", "commands": {"pwr": "PWR ON"}})
    assert drv._definition["commands"]["pwr"]["send"] == "PWR ON\r"
    spy = _SpySend()
    drv.transport = spy
    await drv.send_command("pwr")
    assert spy.sent == [b"PWR ON\r"]


async def test_line_ending_not_double_appended():
    """A command that already ends with the line ending is not doubled."""
    drv, _ = _make({"delimiter": "\r", "commands": {"pwr": "PWR ON\r"}})
    assert drv._definition["commands"]["pwr"]["send"] == "PWR ON\r"


def test_no_line_ending_leaves_commands_untouched():
    """A blank delimiter sends commands exactly as typed."""
    drv, _ = _make({"delimiter": "", "commands": {"pwr": "PWR ON"}})
    assert drv._definition["commands"]["pwr"]["send"] == "PWR ON"


# ── Send raw (one-off / diagnostics) ───────────────────────────────────────


async def test_send_raw_encodes_and_appends_delimiter():
    """send_raw encodes escapes and appends the device delimiter."""
    drv, _ = _make({"delimiter": "\r"})
    spy = _SpySend()
    drv.transport = spy
    await drv.send_raw("PWR ON")
    assert spy.sent == [b"PWR ON\r"]


async def test_send_raw_respects_existing_terminator():
    """send_raw doesn't double a terminator the user typed explicitly."""
    drv, _ = _make({"delimiter": "\r"})
    spy = _SpySend()
    drv.transport = spy
    await drv.send_raw("PWR ON\\r")  # literal \r in the typed string
    assert spy.sent == [b"PWR ON\r"]


async def test_send_raw_not_connected_raises():
    drv, _ = _make({"delimiter": "\r"})
    drv.transport = None
    with pytest.raises(ConnectionError):
        await drv.send_raw("PWR ON")


# ── Polling: commands flagged "poll" become poll queries ───────────────────


def test_poll_flagged_command_builds_poll_queries():
    """A command flagged poll contributes its send string (line ending
    included) to polling.queries; unflagged commands don't."""
    drv, _ = _make(
        {
            "delimiter": "\r",
            "commands": {
                "get_power": {"label": "Get Power", "send": "CR0", "poll": True},
                "power_on": {"label": "Power On", "send": "C00"},
            },
        }
    )
    assert drv._definition["polling"]["queries"] == ["CR0\r"]


def test_no_poll_flag_no_poll_queries():
    drv, _ = _make({"commands": {"power_on": {"send": "C00"}}})
    assert not drv._definition.get("polling", {}).get("queries")


def test_http_poll_flag_uses_command_name():
    """An HTTP-style command (no send string) is polled by name, which
    ConfigurableDriver.poll() looks up."""
    drv, _ = _make(
        {"commands": {"get_status": {"method": "GET", "path": "/status", "poll": True}}}
    )
    assert drv._definition["polling"]["queries"] == ["get_status"]


# ── Responses: regex-free simple modes ─────────────────────────────────────


async def test_response_mode_contains_sets_fixed_value():
    """`contains` matches a substring and sets the state to a fixed value."""
    drv, _ = _make(
        {
            "responses": [
                {"mode": "contains", "text": "PWR ON", "state": "power", "value": "on"},
                {"mode": "contains", "text": "PWR OFF", "state": "power", "value": "off"},
            ],
        }
    )
    await drv.on_data_received(b"PWR ON")
    assert drv.get_state("power") == "on"
    await drv.on_data_received(b"PWR OFF")
    assert drv.get_state("power") == "off"


async def test_response_mode_prefix_number():
    """`prefix_number` captures the number after a prefix, coerced to type."""
    drv, _ = _make(
        {
            "responses": [
                {"mode": "prefix_number", "prefix": "VOL=", "state": "volume",
                 "type": "integer"},
            ],
        }
    )
    await drv.on_data_received(b"VOL=42")
    assert drv.get_state("volume") == 42
    # Negative / decimal shapes are captured too.
    await drv.on_data_received(b"VOL=-3")
    assert drv.get_state("volume") == -3


async def test_response_mode_prefix_text():
    """`prefix_text` captures the rest of the line after a prefix."""
    drv, _ = _make(
        {
            "responses": [
                {"mode": "prefix_text", "prefix": "NAME=", "state": "name"},
            ],
        }
    )
    await drv.on_data_received(b"NAME=Main Stage")
    assert drv.get_state("name") == "Main Stage"


async def test_response_mode_regex_capture_group():
    """`regex` is the escape hatch: raw pattern + capture group."""
    drv, _ = _make(
        {
            "responses": [
                {"mode": "regex", "pattern": r"IN(\d+)", "group": 1,
                 "state": "input", "type": "integer"},
            ],
        }
    )
    await drv.on_data_received(b"IN7")
    assert drv.get_state("input") == 7


async def test_contains_special_chars_are_literal():
    """Substrings with regex metacharacters match literally (regex-free)."""
    drv, _ = _make(
        {
            "responses": [
                {"mode": "contains", "text": "ERR[1]", "state": "fault",
                 "value": "yes"},
            ],
        }
    )
    # Would be a character class if not escaped; must match the literal text.
    await drv.on_data_received(b"ERR[1]")
    assert drv.get_state("fault") == "yes"


# ── State variables: auto-derived + explicit ───────────────────────────────


def test_state_vars_auto_derived_from_responses_seed():
    """Vars written by responses are auto-declared and seeded before any reply."""
    drv, _ = _make(
        {
            "responses": [
                {"mode": "prefix_number", "prefix": "VOL=", "state": "volume",
                 "type": "integer"},
                {"mode": "prefix_text", "prefix": "NAME=", "state": "name"},
            ],
        }
    )
    # Seeded defaults: integer → 0, string → "".
    assert drv.get_state("volume") == 0
    assert drv.get_state("name") == ""
    assert "volume" in drv.DRIVER_INFO["state_variables"]
    assert "name" in drv.DRIVER_INFO["state_variables"]


def test_explicit_state_var_overrides_derived():
    """An explicit state_variables declaration wins over the derived default."""
    drv, _ = _make(
        {
            "state_variables": {"volume": {"type": "integer", "min": 10,
                                           "label": "Volume"}},
            "responses": [
                {"mode": "prefix_number", "prefix": "VOL=", "state": "volume",
                 "type": "integer"},
            ],
        }
    )
    # Seeded from the declared min, not the derived default of 0.
    assert drv.get_state("volume") == 10
    assert drv.DRIVER_INFO["state_variables"]["volume"]["label"] == "Volume"


# ── Isolation: the shared class definition is never mutated ─────────────────


def test_inline_protocol_does_not_leak_across_instances():
    """One device's inline protocol must not bleed into another of the same
    driver type, nor into the shared class definition/metadata."""
    cls = create_configurable_driver_class(GENERIC_DEF)
    s1, e1 = StateStore(), EventBus()
    s1.set_event_bus(e1)
    s2, e2 = StateStore(), EventBus()
    s2.set_event_bus(e2)

    drv_a = cls("a", {"commands": {"foo": "FOO"}}, s1, e1)
    drv_b = cls("b", {}, s2, e2)

    # Instance A sees its command; instance B and the class do not.
    assert "foo" in drv_a._definition["commands"]
    assert "foo" in drv_a.DRIVER_INFO["commands"]
    assert drv_b._definition.get("commands", {}) == {}
    assert cls._definition.get("commands", {}) == {}
    assert cls.DRIVER_INFO.get("commands", {}) == {}


# ── Normalizer units ───────────────────────────────────────────────────────


def test_normalize_commands_skips_bad_shapes():
    out = normalize_config_commands({"a": "RAW", "b": {"send": "X"}, "c": 5})
    assert out == {"a": {"send": "RAW"}, "b": {"send": "X"}}


def test_normalize_responses_passes_through_canonical():
    """An already-canonical entry (with mappings) is preserved untouched."""
    canonical = {"match": r"X(\d)", "mappings": [{"group": 1, "state": "x"}]}
    assert normalize_config_responses([canonical]) == [canonical]


def test_normalize_responses_drops_incomplete_rows():
    # Missing the state target → skipped, not a crash.
    assert normalize_config_responses([{"mode": "contains", "text": "X"}]) == []
    assert normalize_config_responses(["not a dict", 42]) == []
