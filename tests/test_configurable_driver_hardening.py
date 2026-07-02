"""Hardening regression tests for the ConfigurableDriver YAML runtime and the
driver-definition validator.

Each test pins a specific audit finding in server/drivers/configurable.py or
server/drivers/driver_loader.py. Per the platform's test policy these exercise
the runtime/loader with an INVENTED device ("acme_*") and synthetic payloads —
no real product, driver file, or captured fixture is involved.
"""

import asyncio
import re

import httpx
import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import (
    _AUTH_MAX_BUFFER,
    ConfigurableDriver,
    create_configurable_driver_class,
)
from server.drivers.driver_loader import validate_driver_definition


def _make_driver(definition: dict, config: dict | None = None, device_id: str = "dev1"):
    """Build a ConfigurableDriver instance from a definition dict."""
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = cls(device_id, config or {}, state, events)
    return drv


# ── Fake transports ───────────────────────────────────────────────────────


class _RaisingHTTP:
    """Stand-in HTTP transport whose .get() raises a configured error."""

    def __init__(self, error: BaseException):
        self.connected = True
        self._error = error

    async def get(self, path: str):  # noqa: ARG002 - signature parity
        raise self._error


class _SpySend:
    """Non-OSC, non-HTTP transport that records send() calls."""

    def __init__(self):
        self.connected = True
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


# ===========================================================================
# H-031 — poll() must propagate transport errors to the missed-poll watchdog
# ===========================================================================

_HTTP_DEF = {
    "id": "acme_http_box",
    "name": "Acme HTTP Box",
    "transport": "http",
    "commands": {},
    "responses": [],
    "state_variables": {},
    "polling": {"queries": ["/status"]},
}


@pytest.mark.asyncio
async def test_poll_propagates_builtin_connection_error():
    """A builtin ConnectionError from the transport propagates out of poll()."""
    drv = _make_driver(_HTTP_DEF)
    drv.transport = _RaisingHTTP(ConnectionError("unreachable"))
    with pytest.raises(ConnectionError):
        await drv.poll()


@pytest.mark.asyncio
async def test_poll_propagates_httpx_timeout():
    """An httpx timeout (not a builtin error) still propagates out of poll()."""
    drv = _make_driver(_HTTP_DEF)
    drv.transport = _RaisingHTTP(httpx.ConnectTimeout("slow"))
    with pytest.raises(httpx.HTTPError):
        await drv.poll()


@pytest.mark.asyncio
async def test_poll_swallows_protocol_error_and_emits_device_error():
    """A non-transport error is surfaced as device.error, not re-raised, so the
    watchdog isn't penalized for a reachable-but-misbehaving device."""
    drv = _make_driver(_HTTP_DEF)
    drv.transport = _RaisingHTTP(ValueError("garbage response"))

    seen: list[tuple] = []
    drv.events.on("device.error.*", lambda name, payload: seen.append((name, payload)))

    # Must NOT raise.
    await drv.poll()
    await asyncio.sleep(0)  # let the emit task run

    assert seen, "expected a device.error event for a protocol-level failure"
    assert seen[0][1]["device_id"] == "dev1"


@pytest.mark.asyncio
async def test_watchdog_marks_unreachable_http_device_disconnected():
    """End-to-end: an unreachable HTTP device trips the watchdog and flips
    connected to False instead of looking online forever."""
    drv = _make_driver({**_HTTP_DEF, "id": "acme_http_box2"}, config={"max_missed_polls": 3})
    drv.transport = _RaisingHTTP(ConnectionError("refused"))
    drv._connected = True
    drv.set_state("connected", True)

    await drv.start_polling(0.01)
    await asyncio.sleep(0.25)

    assert drv.get_state("connected") is False
    if drv._poll_task is not None:
        assert drv._poll_task.done()


# ===========================================================================
# M-060 — value-map result is coerced (type parity + flat-primitive guard)
# ===========================================================================


@pytest.mark.asyncio
async def test_value_map_result_is_coerced_to_declared_type():
    """A mapped value declared `integer` is stored as int, not the raw string."""
    definition = {
        "id": "acme_mapper",
        "name": "Acme Mapper",
        "transport": "tcp",
        "commands": {},
        "responses": [
            {
                "match": r"LVL=(\d+)",
                "mappings": [
                    {"group": 1, "state": "level", "type": "integer",
                     "map": {"99": "5"}},
                ],
            },
        ],
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
    }
    drv = _make_driver(definition)
    await drv.on_data_received(b"LVL=99")
    assert drv.get_state("level") == 5
    assert isinstance(drv.get_state("level"), int)


@pytest.mark.asyncio
async def test_value_map_nested_target_flattened_to_primitive():
    """A hostile map whose target is a list/dict can't violate the flat-
    primitive state invariant — it's stringified by coercion."""
    definition = {
        "id": "acme_mapper2",
        "name": "Acme Mapper 2",
        "transport": "tcp",
        "commands": {},
        "responses": [
            {
                "match": r"X=(\d+)",
                "mappings": [
                    {"group": 1, "state": "thing", "type": "string",
                     "map": {"1": [1, 2, 3]}},
                ],
            },
        ],
        "state_variables": {"thing": {"type": "string", "label": "Thing"}},
    }
    drv = _make_driver(definition)
    await drv.on_data_received(b"X=1")
    value = drv.get_state("thing")
    assert isinstance(value, str)
    assert not isinstance(value, (list, dict))


# ===========================================================================
# L-041 — non-numeric `set` shorthand reference is skipped, not group 0
# ===========================================================================


def test_set_shorthand_skips_non_numeric_reference():
    """`$foo` (not a numeric group) is dropped with a warning rather than
    silently capturing the whole match (group 0)."""
    definition = {
        "id": "acme_set",
        "name": "Acme Set",
        "transport": "tcp",
        "commands": {},
        "responses": [
            {"match": r"V(\d+)", "set": {"good": "$1", "bad": "$foo"}},
        ],
        "state_variables": {
            "good": {"type": "integer", "label": "Good"},
            "bad": {"type": "string", "label": "Bad"},
        },
    }
    drv = _make_driver(definition)
    _pattern, mappings = drv._compiled_responses[0]
    states = {m["state"] for m in mappings}
    assert "good" in states
    assert "bad" not in states  # the typo'd reference produced no mapping


# ===========================================================================
# L-042 — per-instance mapping list, no mutation of the shared class definition
# ===========================================================================


def test_set_shorthand_does_not_mutate_shared_definition():
    """Expanding a `set` shorthand on one instance must not leak into the
    class-level _definition shared by all instances of the driver type."""
    definition = {
        "id": "acme_shared",
        "name": "Acme Shared",
        "transport": "tcp",
        "commands": {},
        # `mappings: []` present-but-empty is the case that used to alias and
        # mutate the shared list.
        "responses": [
            {"match": r"P(\d+)", "mappings": [], "set": {"level": "$1"}},
        ],
        "state_variables": {"level": {"type": "integer", "label": "Level"}},
    }
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()

    inst1 = cls("a", {}, state, events)
    inst2 = cls("b", {}, state, events)

    # Shared class definition stays as authored — not mutated by expansion.
    assert cls._definition["responses"][0]["mappings"] == []
    # Each instance got its own one-mapping list.
    assert len(inst1._compiled_responses[0][1]) == 1
    assert len(inst2._compiled_responses[0][1]) == 1
    assert inst1._compiled_responses[0][1] is not inst2._compiled_responses[0][1]


# ===========================================================================
# L-043 — OSC arg with a missing/non-numeric value fails with a clear error
# ===========================================================================


def test_osc_arg_missing_numeric_value_raises_clear_error():
    """A numeric OSC arg with no value raises a contextual ValueError instead
    of a bare float('') crash."""
    with pytest.raises(ValueError, match="numeric value"):
        ConfigurableDriver._build_osc_args([{"type": "f"}], {})


def test_osc_arg_unresolved_placeholder_raises_clear_error():
    """An unresolved {placeholder} in a numeric OSC arg fails cleanly."""
    with pytest.raises(ValueError, match="numeric value"):
        ConfigurableDriver._build_osc_args([{"type": "i", "value": "{missing}"}], {})


def test_osc_arg_valid_numeric_value_builds():
    """Sanity: a resolvable numeric value still builds correctly."""
    args = ConfigurableDriver._build_osc_args(
        [{"type": "f", "value": "{vol}"}], {"vol": "0.5"}
    )
    assert args == [("f", 0.5)]


# ===========================================================================
# M-063 — OSC send paths refuse to emit on a non-OSC transport
# ===========================================================================

_OSC_CMD_DEF = {
    "id": "acme_osc",
    "name": "Acme OSC",
    "transport": "osc",
    "commands": {
        "fade": {"address": "/fader/1", "args": [{"type": "f", "value": "0.5"}]},
    },
    "device_settings": {
        "gain": {
            "label": "Gain",
            "write": {"address": "/gain", "args": [{"type": "f", "value": "{value}"}]},
        },
    },
    "responses": [],
    "state_variables": {},
}


@pytest.mark.asyncio
async def test_osc_command_refused_on_non_osc_transport():
    """An OSC-shaped command does not emit OSC bytes when the live transport
    isn't an OSCTransport."""
    drv = _make_driver(_OSC_CMD_DEF)
    spy = _SpySend()
    drv.transport = spy
    result = await drv.send_command("fade")
    assert result is None
    assert spy.sent == []  # nothing was put on the wire


@pytest.mark.asyncio
async def test_osc_setting_write_refused_on_non_osc_transport():
    """An OSC-shaped device-setting write raises rather than emit on a non-OSC
    socket."""
    drv = _make_driver(_OSC_CMD_DEF)
    drv.transport = _SpySend()
    with pytest.raises(ConnectionError, match="not OSC"):
        await drv.set_device_setting("gain", 0.7)


# ===========================================================================
# M-062 (runtime) — handshake gate refuses non-tcp/serial transports
# ===========================================================================


def test_auth_should_run_false_on_udp_transport():
    """The handshake (raw byte buffering + frame-parser swap) never runs on a
    udp transport even if an auth block is present."""
    definition = {
        "id": "acme_udp_auth",
        "name": "Acme UDP Auth",
        "transport": "udp",
        "commands": {},
        "responses": [],
        "state_variables": {},
        "auth": {
            "type": "telnet_login",
            "username_prompt": "login: ",
            "password_prompt": "password: ",
        },
    }
    drv = _make_driver(definition, config={"username": "admin", "password": "x"})
    assert drv._auth_should_run(definition["auth"]) is False


def test_auth_should_run_true_on_tcp_transport():
    """Sanity: the same auth block runs on a tcp transport with creds set."""
    definition = {
        "id": "acme_tcp_auth",
        "name": "Acme TCP Auth",
        "transport": "tcp",
        "commands": {},
        "responses": [],
        "state_variables": {},
        "auth": {
            "type": "telnet_login",
            "username_prompt": "login: ",
            "password_prompt": "password: ",
        },
    }
    drv = _make_driver(definition, config={"username": "admin", "password": "x"})
    assert drv._auth_should_run(definition["auth"]) is True


# ===========================================================================
# L-040 — pre-auth buffer is bounded; overflow aborts the handshake
# ===========================================================================


@pytest.mark.asyncio
async def test_auth_buffer_overflow_aborts_handshake():
    """Streaming more than the cap before a prompt match sets the overflow flag
    and makes _auth_wait_for raise instead of growing/scanning unbounded."""
    drv = _make_driver({
        "id": "acme_flood",
        "name": "Acme Flood",
        "transport": "tcp",
        "commands": {},
        "responses": [],
        "state_variables": {},
    })
    drv._auth_mode = True
    drv._auth_buffer = bytearray()
    drv._auth_overflow = False

    await drv.on_data_received(b"x" * (_AUTH_MAX_BUFFER + 1))
    assert drv._auth_overflow is True

    with pytest.raises(ConnectionError, match="auth aborted"):
        await drv._auth_wait_for(re.compile("never-matches"), None, timeout=5.0)


# ===========================================================================
# M-058 / M-059 / M-061 / M-062 — load-time validation gate
# ===========================================================================


def _base_def(**extra) -> dict:
    """Minimal definition that passes the validator (generic_ id skips the
    discovery-hint check so these stay focused on auth/regex rules)."""
    base = {
        "id": "generic_acme",
        "name": "Acme",
        "transport": "tcp",
        "commands": {},
        "responses": [],
        "state_variables": {},
    }
    base.update(extra)
    return base


def test_validator_rejects_redos_response_pattern():
    """M-059: alternation-overlap patterns the old heuristic missed are now
    rejected at load time."""
    for bad in (r"(a|a)+", r"(foo|foobar)*", r"(.+)+"):
        errs = validate_driver_definition(
            _base_def(responses=[{"match": bad, "mappings": []}])
        )
        assert any("backtracking" in e for e in errs), f"{bad!r} not flagged: {errs}"


def test_validator_accepts_safe_response_pattern():
    """A normal capture group is not flagged."""
    errs = validate_driver_definition(
        _base_def(responses=[{"match": r"In(\d+) All", "mappings": []}])
    )
    assert errs == []


def test_validator_redos_checks_auth_patterns():
    """M-058: auth prompt regexes are ReDoS-checked, not bypassed."""
    errs = validate_driver_definition(_base_def(auth={
        "type": "telnet_login",
        "username_prompt": r"(a|a)+",
        "password_prompt": "password: ",
    }))
    assert any("auth.username_prompt" in e and "backtracking" in e for e in errs), errs


def test_validator_requires_both_auth_prompts():
    """M-061: a declared handshake missing a prompt is a load-time error, not a
    silent unauthenticated connect."""
    errs = validate_driver_definition(_base_def(auth={
        "type": "telnet_login",
        "username_prompt": "login: ",
        # password_prompt omitted
    }))
    assert any("password_prompt" in e for e in errs), errs


def test_validator_rejects_auth_on_udp_transport():
    """M-062: a login handshake on udp/http/osc is rejected at load time."""
    errs = validate_driver_definition(_base_def(transport="udp", auth={
        "type": "telnet_login",
        "username_prompt": "login: ",
        "password_prompt": "password: ",
    }))
    assert any("tcp/serial" in e for e in errs), errs


def test_validator_accepts_valid_auth_block():
    """A well-formed tcp handshake passes."""
    errs = validate_driver_definition(_base_def(auth={
        "type": "telnet_login",
        "username_prompt": "login: ",
        "password_prompt": "password: ",
        "success_pattern": "GNET> ",
    }))
    assert errs == []


# ===========================================================================
# H-073 — {name:spec} placeholders honor the Python format-spec mini-language
# ===========================================================================


def test_substitute_plain_placeholder_unchanged():
    """A spec-less {name} behaves exactly as before (str of the value)."""
    assert ConfigurableDriver._safe_substitute("v={vol}", {"vol": 5}) == "v=5"


def test_substitute_zero_pads_integer_value():
    """{preset:02d} on an int zero-pads — the documented Panasonic example."""
    assert ConfigurableDriver._safe_substitute("R{preset:02d}", {"preset": 5}) == "R05"


def test_substitute_zero_pads_numeric_string_value():
    """A param that arrives as a string still pads via numeric coercion."""
    assert ConfigurableDriver._safe_substitute("R{preset:02d}", {"preset": "5"}) == "R05"


def test_substitute_hex_format_spec():
    """Hex specs work too (common for address bytes)."""
    assert ConfigurableDriver._safe_substitute("{addr:04X}", {"addr": 255}) == "00FF"


def test_substitute_preserves_json_braces():
    """Literal JSON braces are untouched; only the {level} token resolves."""
    out = ConfigurableDriver._safe_substitute('{"level": {level}}', {"level": 75})
    assert out == '{"level": 75}'


def test_substitute_unknown_key_with_spec_left_literal():
    """A spec on a key that isn't a param leaves the placeholder verbatim."""
    assert ConfigurableDriver._safe_substitute("{nope:02d}", {}) == "{nope:02d}"


def test_substitute_invalid_spec_left_literal_not_raised():
    """A numeric spec on a non-numeric string can't crash the send — the
    placeholder is left verbatim instead."""
    assert ConfigurableDriver._safe_substitute("{name:02d}", {"name": "abc"}) == "{name:02d}"


@pytest.mark.asyncio
async def test_command_send_applies_format_spec_end_to_end():
    """The full send path substitutes a format spec, so the doc's
    `%23R{preset:02d}` example puts the zero-padded value on the wire."""
    definition = {
        "id": "acme_fmt",
        "name": "Acme Fmt",
        "transport": "tcp",
        "commands": {"recall": {"send": "PR{preset:02d}"}},
        "responses": [],
        "state_variables": {},
    }
    drv = _make_driver(definition)
    spy = _SpySend()
    drv.transport = spy
    await drv.send_command("recall", {"preset": 5})
    assert spy.sent == [b"PR05"]


# ===========================================================================
# H-074 — OSC arg type tags are validated at load (blob/typos fail loudly)
# ===========================================================================


def test_validator_rejects_unknown_osc_command_arg_type():
    """A command OSC arg with an unsupported tag (e.g. 'b'/blob) is rejected at
    load instead of being silently dropped when the message is built."""
    errs = validate_driver_definition(_base_def(transport="osc", commands={
        "fade": {"address": "/fader/1", "args": [{"type": "b", "value": "x"}]},
    }))
    assert any("unknown OSC type 'b'" in e for e in errs), errs


def test_validator_accepts_known_osc_command_arg_types():
    """The eight supported tags pass."""
    args = [{"type": t, "value": "0"} for t in ("f", "i", "s", "h", "d", "T", "F", "N")]
    errs = validate_driver_definition(_base_def(transport="osc", commands={
        "go": {"address": "/go", "args": args},
    }))
    assert errs == []


def test_validator_rejects_unknown_osc_device_setting_arg_type():
    """OSC device-setting writes get the same arg-type check."""
    errs = validate_driver_definition(_base_def(transport="osc", device_settings={
        "gain": {
            "label": "Gain",
            "write": {"address": "/gain", "args": [{"type": "b", "value": "{value}"}]},
        },
    }))
    assert any("unknown OSC type 'b'" in e for e in errs), errs


def test_build_osc_args_drops_unsupported_tag():
    """Runtime defense: an unsupported tag yields no arg (no crash) rather than
    a wrongly-typed one — the loader is the primary guard, this is backstop."""
    assert ConfigurableDriver._build_osc_args([{"type": "b", "value": "x"}], {}) == []


# ── Device-settings + child-schema load-time validation (guardrails) ─────────


def test_validator_rejects_setting_with_unknown_state_key():
    """A typo'd state_key used to load fine and show '(not set)' forever."""
    errs = validate_driver_definition(_base_def(
        state_variables={"brightness": {"type": "integer", "label": "Brightness"}},
        device_settings={
            "brightness": {
                "type": "integer", "label": "Brightness",
                "state_key": "brightnes",  # typo
                "write": {"send": "BRT {value}\r"},
            },
        },
    ))
    assert any("state_key 'brightnes'" in e for e in errs), errs


def test_validator_rejects_setting_without_write_block():
    errs = validate_driver_definition(_base_def(
        state_variables={"volume": {"type": "integer", "label": "Volume"}},
        device_settings={
            "volume": {"type": "integer", "label": "Volume", "state_key": "volume"},
        },
    ))
    assert any("missing 'write'" in e for e in errs), errs


def test_validator_rejects_setting_min_greater_than_max():
    errs = validate_driver_definition(_base_def(
        state_variables={"volume": {"type": "integer", "label": "Volume"}},
        device_settings={
            "volume": {
                "type": "integer", "label": "Volume", "state_key": "volume",
                "min": 100, "max": 0, "write": {"send": "VOL {value}\r"},
            },
        },
    ))
    assert any("min (100) is greater than max (0)" in e for e in errs), errs


def test_validator_accepts_well_formed_setting():
    errs = validate_driver_definition(_base_def(
        state_variables={"volume": {"type": "integer", "label": "Volume"}},
        device_settings={
            "volume": {
                "type": "integer", "label": "Volume", "state_key": "volume",
                "min": 0, "max": 100, "write": {"send": "VOL {value}\r"},
            },
        },
    ))
    assert errs == [], errs


def test_validator_rejects_malformed_child_schema():
    """id_format / cloud_priority mistakes used to fail at connect() (a
    confusing device-offline) or silently fall to the default tier."""
    errs = validate_driver_definition(_base_def(
        child_entity_types={
            "zone": {
                "label": "Zone",
                "id_format": {"type": "uuid", "min": 5, "max": 1, "pad_width": 0},
                "state_variables": {
                    "level": {"type": "loudness"},
                    "mute": {"type": "boolean", "cloud_priority": "medium"},
                },
            },
        },
    ))
    text = "\n".join(errs)
    assert "unknown type 'uuid'" in text, errs
    assert "min (5) is greater than max (1)" in text, errs
    assert "pad_width must be a positive integer" in text, errs
    assert "unknown type 'loudness'" in text, errs
    assert "cloud_priority" in text, errs


def test_validator_accepts_well_formed_child_schema():
    errs = validate_driver_definition(_base_def(
        child_entity_types={
            "zone": {
                "label": "Zone",
                "id_format": {"type": "integer", "min": 1, "max": 8, "pad_width": 2},
                "state_variables": {
                    "level": {"type": "number", "cloud_priority": "low"},
                    "mute": {"type": "boolean", "cloud_priority": "high"},
                },
            },
        },
    ))
    assert errs == [], errs
