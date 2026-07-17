"""Tests for send-side command framing in YAML drivers.

Covers the opt-in ``command_prefix`` / ``command_suffix`` that wrap every
byte-stream command, the per-command ``raw: true`` opt-out, the guarantee that
a driver declaring neither field (but a ``delimiter`` for its frame parser) is
unchanged, that inline/device-config commands are framed exactly once, and that
a TCP/serial poll query naming a command sends the framed command. Uses an
invented receiver so no real product is named.
"""

import copy

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition

# Invented text-protocol receiver: a constant "!1" packet header and a "\r"
# terminator wrap every command (the shape of many AV receiver protocols).
ACME_RECEIVER = {
    "id": "acme_receiver",
    "name": "Acme Receiver",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "tcp",
    "delimiter": "\\r",
    "command_prefix": "!1",
    "command_suffix": "\\r",
    "default_config": {"host": "", "port": 60128},
    "config_schema": {
        "host": {"type": "string", "required": True, "label": "IP Address"},
        "port": {"type": "integer", "default": 60128, "label": "Port"},
    },
    "state_variables": {
        "power": {"type": "boolean", "label": "Power"},
    },
    "commands": {
        "power_on": {"label": "Power On", "send": "PWR01"},
        "set_dsp": {
            "label": "DSP Mode",
            "send": "LMD{mode}",
            "params": {"mode": {"type": "string", "required": True}},
        },
        "raw_ping": {"label": "Raw Ping", "send": "PING\\r", "raw": True},
        "power_query": {"label": "Power Query", "send": "PWRQSTN"},
    },
    "polling": {"queries": ["power_query"]},
}


class FakeTransport:
    connected = True

    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _make_driver(definition=ACME_RECEIVER, config=None):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    merged = {"host": "127.0.0.1", "port": 60128}
    if config is not None:
        merged = config
    driver = cls("dev1", merged, state, events)
    driver.transport = FakeTransport()
    return driver


async def test_file_command_is_framed():
    driver = _make_driver()
    await driver.send_command("power_on")
    assert driver.transport.sent == [b"!1PWR01\r"]


async def test_parameterized_command_is_framed():
    driver = _make_driver()
    await driver.send_command("set_dsp", {"mode": "0F"})
    assert driver.transport.sent == [b"!1LMD0F\r"]


async def test_raw_command_skips_framing():
    driver = _make_driver()
    await driver.send_command("raw_ping")
    # No prefix, no extra suffix — only the command's own authored terminator.
    assert driver.transport.sent == [b"PING\r"]


async def test_no_framing_when_fields_unset():
    # A driver that declares a delimiter (for its frame parser) but no
    # command_prefix / command_suffix must NOT get the delimiter appended on
    # send — that would double-terminate every existing file driver.
    definition = copy.deepcopy(ACME_RECEIVER)
    del definition["command_prefix"]
    del definition["command_suffix"]
    driver = _make_driver(definition)
    await driver.send_command("power_on")
    assert driver.transport.sent == [b"PWR01"]


async def test_inline_command_framed_once():
    # A device-config (inline) command is framed at merge time; send_command
    # must not frame it again (no double prefix / suffix).
    config = {
        "host": "127.0.0.1",
        "port": 60128,
        "commands": {"vol_up": "MVLUP"},
    }
    driver = _make_driver(config=config)
    await driver.send_command("vol_up")
    assert driver.transport.sent == [b"!1MVLUP\r"]


async def test_inline_suffix_falls_back_to_delimiter():
    # Inline commands (only) fall back to the delimiter when no command_suffix
    # is declared — preserving the existing no-code "Line Ending" behavior.
    definition = copy.deepcopy(ACME_RECEIVER)
    del definition["command_prefix"]
    del definition["command_suffix"]
    config = {
        "host": "127.0.0.1",
        "port": 60128,
        "commands": {"vol_up": "MVLUP"},
    }
    driver = _make_driver(definition, config)
    await driver.send_command("vol_up")
    assert driver.transport.sent == [b"MVLUP\r"]


async def test_poll_query_naming_command_sends_framed():
    # A TCP/serial poll query that names a command runs that command, so the
    # frame is applied without re-authoring it in the query string.
    driver = _make_driver()
    await driver.poll()
    assert driver.transport.sent == [b"!1PWRQSTN\r"]


def test_framed_definition_validates_clean():
    assert validate_driver_definition(copy.deepcopy(ACME_RECEIVER)) == []


def test_non_string_framing_field_is_rejected():
    definition = copy.deepcopy(ACME_RECEIVER)
    definition["command_prefix"] = 11
    errors = validate_driver_definition(definition)
    assert any("command_prefix" in e for e in errors)


def test_simulator_matches_framed_command():
    # The auto-generated simulator must recognize the framed wire form the real
    # driver sends (command_prefix prepended; the whitespace suffix is stripped
    # from the incoming line), and must NOT match the unframed command.
    from simulator.yaml_auto import YAMLAutoSimulator

    definition = {
        "id": "acme_receiver",
        "name": "Acme Receiver",
        "transport": "tcp",
        "delimiter": "\\r",
        "command_prefix": "!1",
        "command_suffix": "\\r",
        "state_variables": {"power": {"type": "boolean"}},
        "commands": {"power_on": {"send": "PWR01"}},
    }
    sim = YAMLAutoSimulator(device_id="dev1", config={}, driver_def=definition)
    sim.handle_command(b"!1PWR01\r")
    assert sim._state.get("power")

    unframed = YAMLAutoSimulator(device_id="dev2", config={}, driver_def=definition)
    assert unframed.handle_command(b"PWR01\r") is None
    assert not unframed._state.get("power")
