"""Tests for send-side command framing in YAML drivers.

Covers the opt-in ``command_prefix`` / ``command_suffix`` that wrap every
byte-stream command, the per-command ``raw: true`` opt-out, the guarantee that
a driver declaring neither field (but a ``delimiter`` for its frame parser) is
unchanged, that inline/device-config commands are framed exactly once, and that
a TCP/serial poll query naming a command sends the framed command. Uses an
invented receiver so no real product is named.
"""

import copy

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition
from openavc.drivers.send_framing import framed_commands, framed_send
from openavc.simulator import send_coverage
from openavc.simulator.driver_sends import command_sample, effective_config
from openavc.simulator.inline_device import authored_names, merge_inline_protocol
from openavc.transport.binary_helpers import encode_escape_sequences

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
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

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


# ── The one reading of a command's framing, and the readers that ask it ──

_PLAIN = {k: v for k, v in ACME_RECEIVER.items() if k not in ("command_prefix", "command_suffix")}
_NO_LINE_ENDING = {k: v for k, v in _PLAIN.items() if k != "delimiter"}

#: A file command: the driver file, the device's settings, the command, and
#: the text the driver frames it as.
FILE_FRAMINGS = {
    "the_driver_prefix_and_suffix": (ACME_RECEIVER, {}, {"send": "PWR01"}, "!1PWR01\\r"),
    "the_device_prefix_over_the_driver_prefix": (
        ACME_RECEIVER, {"command_prefix": "#"}, {"send": "PWR01"}, "#PWR01\\r"),
    "a_device_prefix_left_empty_keeps_the_driver_prefix": (
        ACME_RECEIVER, {"command_prefix": ""}, {"send": "PWR01"}, "!1PWR01\\r"),
    "a_prefix_set_on_the_device_only": (_PLAIN, {"command_prefix": "#"}, {"send": "PWR01"}, "#PWR01"),
    "a_suffix_that_is_not_whitespace": (
        {**ACME_RECEIVER, "command_suffix": ";\\r"}, {}, {"send": "PWR01"}, "!1PWR01;\\r"),
    "a_send_that_starts_with_the_prefix_takes_it_again": (
        ACME_RECEIVER, {}, {"send": "!1PWR01"}, "!1!1PWR01\\r"),
    "a_send_that_ends_with_the_suffix_takes_it_again": (
        ACME_RECEIVER, {}, {"send": "PWR01\\r"}, "!1PWR01\\r\\r"),
    "raw_is_as_written": (ACME_RECEIVER, {}, {"send": "PING\\r", "raw": True}, "PING\\r"),
    "no_prefix_or_suffix_is_as_written": (_PLAIN, {}, {"send": "PWR01\\r"}, "PWR01\\r"),
    "a_send_that_is_not_text_is_left_with_no_prefix_or_suffix": (_PLAIN, {}, {"send": 123}, 123),
}


@pytest.mark.parametrize(
    "definition, config, command, framed", FILE_FRAMINGS.values(), ids=list(FILE_FRAMINGS))
def test_a_file_command_is_framed_whatever_it_starts_or_ends_with(definition, config, command, framed):
    assert framed_send(definition, config, command) == framed


@pytest.mark.parametrize(
    "definition, config, command, framed",
    [case for case in FILE_FRAMINGS.values() if isinstance(case[3], str)],
    ids=[name for name, case in FILE_FRAMINGS.items() if isinstance(case[3], str)])
def test_the_driver_sends_the_framed_text(definition, config, command, framed):
    driver = _make_driver({**definition, "commands": {"go": command}},
                          {"host": "127.0.0.1", "port": 60128, **config})
    assert driver.build_wire("go") == encode_escape_sequences(framed)


@pytest.mark.parametrize(
    "definition, config, command, framed",
    [case for case in FILE_FRAMINGS.values() if isinstance(case[3], str)],
    ids=[name for name, case in FILE_FRAMINGS.items() if isinstance(case[3], str)])
def test_the_validator_samples_the_line_the_driver_sends(definition, config, command, framed):
    # The validator knows no device: the device's settings reach it as the
    # driver's defaults.
    defaults = {**definition, "default_config": {**definition["default_config"], **config}}
    driver = _make_driver({**definition, "commands": {"go": command}},
                          {"host": "127.0.0.1", "port": 60128, **config})
    line = driver.build_wire("go").decode().strip()
    template = send_coverage._framed_send(command, defaults, effective_config(defaults))
    assert command_sample(template, {}, defaults) == line


#: A command the device authored: the driver file, the device's settings
#: (its commands among them), and the text the driver frames it as.
AUTHORED_FRAMINGS = {
    "the_prefix_and_the_suffix": (ACME_RECEIVER, {}, "MVLUP", "!1MVLUP\\r"),
    "a_send_that_starts_with_the_prefix_keeps_one": (ACME_RECEIVER, {}, "!1MVLUP", "!1MVLUP\\r"),
    "a_send_that_ends_with_the_suffix_keeps_one": (ACME_RECEIVER, {}, "MVLUP\\r", "!1MVLUP\\r"),
    "the_driver_line_ending_with_no_suffix": (_PLAIN, {}, "MVLUP", "MVLUP\\r"),
    "the_driver_line_ending_over_the_device_line_ending": (_PLAIN, {"delimiter": "\n"}, "MVLUP", "MVLUP\\r"),
    "the_device_line_ending_when_the_driver_names_none": (
        _NO_LINE_ENDING, {"delimiter": "\n"}, "MVLUP", "MVLUP\n"),
    "cr_when_neither_names_one": (_NO_LINE_ENDING, {}, "MVLUP", "MVLUP\r"),
    "the_device_prefix": (_PLAIN, {"command_prefix": "#"}, "MVLUP", "#MVLUP\\r"),
    "raw_is_as_written": (ACME_RECEIVER, {}, {"send": "MVLUP", "raw": True}, "MVLUP"),
}


@pytest.mark.parametrize(
    "definition, config, command, framed", AUTHORED_FRAMINGS.values(), ids=list(AUTHORED_FRAMINGS))
def test_a_device_command_is_framed_once_the_same_way_by_the_driver_and_the_simulator(
    definition, config, command, framed,
):
    block = command if isinstance(command, dict) else {"send": command}
    assert framed_send(definition, config, block, authored=True) == framed
    device = {**config, "commands": {"go": command}}
    driver = _make_driver(definition, {"host": "127.0.0.1", "port": 60128, **device})
    assert driver._definition["commands"]["go"]["send"] == framed
    merged, _ = merge_inline_protocol(definition, device)
    assert merged["commands"]["go"]["send"] == framed
    assert authored_names(device) == {"go"}


def test_a_device_command_with_no_send_is_kept_as_it_is():
    http = {"method": "GET", "path": "/status"}
    assert framed_commands(ACME_RECEIVER, {}, {"status": http}) == {"status": http}
