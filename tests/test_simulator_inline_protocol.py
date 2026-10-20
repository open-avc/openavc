"""Tests for inline-protocol support in the YAML auto-simulator.

A Generic device authors its commands/responses in the device *config* (the
no-code Commands & Responses editor), not the driver file. The simulator merges
that config into the definition it builds from, so it can simulate a no-code
device. Platform tests: invented device ("acme_generic"), synthetic payloads.
"""

import json

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import (
    YAMLAutoSimulator,
    _merge_inline_protocol,
    _mappings_to_set,
)

# A bare "Generic" definition — the protocol comes entirely from the config.
GENERIC_DEF = {
    "id": "acme_generic",
    "name": "Acme Generic",
    "transport": "tcp",
    "state_variables": {},
    "commands": {},
    "responses": [],
}

# A normal file-authored driver (no inline protocol) — the regression baseline.
FILE_DEF = {
    "id": "acme_switcher",
    "name": "Acme Switcher",
    "transport": "tcp",
    "delimiter": "\\r",
    "state_variables": {"input": {"type": "integer"}},
    "commands": {"set_input": {"send": "IN{input}", "params": {"input": {"type": "integer"}}}},
    "responses": [{"match": r"IN(\d+)", "set": {"input": "$1"}}],
}

INLINE_CONFIG = {
    "delimiter": "\r",
    "commands": {"pwr_on": {"label": "Power On", "send": "PWR ON"}},
    "responses": [
        {"mode": "contains", "text": "PWR ON", "state": "power", "value": "on",
         "type": "string"},
        {"mode": "prefix_number", "prefix": "VOL=", "state": "volume",
         "type": "integer"},
    ],
}


def _sim(driver_def, config=None):
    return YAMLAutoSimulator(
        device_id="g1", config=config or {}, driver_def=driver_def
    )


# ── Merge ───────────────────────────────────────────────────────────────────


def test_inline_config_merges_and_seeds_state():
    sim = _sim(GENERIC_DEF, INLINE_CONFIG)
    assert sim._inline_protocol is True
    # State vars derived from the responses are seeded with type defaults.
    assert sim.get_state("volume") == 0
    assert sim.get_state("power") == ""
    # Inline devices push state changes to the panel by default.
    assert sim._push_state is True


def test_no_inline_config_is_inert():
    """A file-authored driver with empty config is unaffected (regression)."""
    sim = _sim(FILE_DEF, {})
    assert sim._inline_protocol is False
    assert sim._inline_response_handlers == []
    # The file driver's own responses still simulate as before.
    assert sim.handle_command(b"IN3") is not None or sim.get_state("input") == 3


# ── Incoming command → state (echo-device behavior) ─────────────────────────


def test_contains_command_sets_state_and_echoes():
    sim = _sim(GENERIC_DEF, INLINE_CONFIG)
    resp = sim.handle_command(b"PWR ON")
    assert sim.get_state("power") == "on"
    assert resp == b"PWR ON\r"


def test_prefix_number_command_sets_state():
    sim = _sim(GENERIC_DEF, INLINE_CONFIG)
    resp = sim.handle_command(b"VOL=42")
    assert sim.get_state("volume") == 42
    assert resp == b"VOL=42\r"


def test_unrecognized_command_is_ignored():
    sim = _sim(GENERIC_DEF, INLINE_CONFIG)
    assert sim.handle_command(b"GIBBERISH") is None


# ── Driving state from the simulator UI emits the matching string ───────────


def test_state_to_response_formatting():
    """The simulator can render a state value back to the device's string, so
    driving a variable from the sim UI emits it to the panel."""
    sim = _sim(GENERIC_DEF, INLINE_CONFIG)
    assert sim._state_responses["volume"].format(42) == "VOL=42"
    assert sim._state_responses["power"].format("on") == "PWR ON"


# ── delimiter ───────────────────────────────────────────────────────────────


def test_config_delimiter_used():
    sim = _sim(GENERIC_DEF, INLINE_CONFIG)
    assert sim._get_delimiter() == "\r"


# ── Helper units ────────────────────────────────────────────────────────────


def test_mappings_to_set_literal_and_capture():
    assert _mappings_to_set([{"state": "power", "value": "on"}]) == {"power": "on"}
    assert _mappings_to_set([{"group": 1, "state": "volume"}]) == {"volume": "$1"}


def test_merge_returns_false_when_no_inline_keys():
    merged, had = _merge_inline_protocol(GENERIC_DEF, {"host": "1.2.3.4"})
    assert had is False
    assert merged is GENERIC_DEF


# ── HTTP: JSON-field responses and the status request marked Poll ───────────

HTTP_DEF = {
    "id": "acme_generic_http",
    "name": "Acme Generic HTTP",
    "transport": "http",
    "state_variables": {},
    "commands": {},
    "responses": [],
}

# The shapes the Commands & Responses editor saves for an HTTP device.
HTTP_CONFIG = {
    "commands": {
        "get_status": {
            "label": "Get Status", "method": "GET", "path": "/api/status",
            "poll": True,
        },
        "reboot": {"label": "Reboot", "method": "POST", "path": "/api/reboot"},
    },
    "responses": [
        {"mode": "json", "field": "occupied", "state": "occupied",
         "type": "boolean"},
        {"mode": "json", "field": "sensor.lux", "state": "lux",
         "type": "integer"},
    ],
}


def test_json_field_response_builds_and_seeds_state():
    sim = _sim(HTTP_DEF, HTTP_CONFIG)
    assert sim.get_state("occupied") is False
    assert sim.get_state("lux") == 0


def test_http_device_is_shown_as_poll_only():
    """An HTTP device answers requests and never pushes, so the Simulator
    card must not say it pushes state changes."""
    sim = _sim(HTTP_DEF, {"commands": HTTP_CONFIG["commands"]})
    assert sim._inline_protocol is True
    assert sim.to_info_dict()["push_state"] is False


def test_polled_command_answers_with_the_json_fields():
    sim = _sim(HTTP_DEF, HTTP_CONFIG)
    sim.set_state("occupied", True)
    sim.set_state("lux", 300)
    reply = sim.handle_command(b"GET /api/status")
    assert json.loads(reply) == {"occupied": True, "sensor": {"lux": 300}}


def test_command_not_marked_poll_gets_no_status_reply():
    sim = _sim(HTTP_DEF, HTTP_CONFIG)
    assert sim.handle_command(b"POST /api/reboot") is None


def test_polled_command_without_json_fields_is_not_answered():
    config = {"commands": HTTP_CONFIG["commands"]}
    sim = _sim(HTTP_DEF, config)
    assert sim.handle_command(b"GET /api/status") is None


async def test_the_driver_reads_the_polled_reply():
    """What the simulator answers is what the device's own response rows read."""
    sim = _sim(HTTP_DEF, HTTP_CONFIG)
    sim.set_state("occupied", True)
    sim.set_state("lux", 300)
    reply = sim.handle_command(b"GET /api/status")

    cls = create_configurable_driver_class({**HTTP_DEF, "default_config": {}})
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = cls("g1", dict(HTTP_CONFIG), state, events)
    await driver.on_data_received(reply)
    assert state.get("device.g1.occupied") is True
    assert state.get("device.g1.lux") == 300
