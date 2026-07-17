"""Tests for the send-side computed-length packet framer (``send_frame``).

Covers a byte-stream protocol whose commands ride inside a binary header with a
COMPUTED data-length field — the eISCP shape — which a static ``command_prefix``
can't express because the length varies per message (a feedback query is longer
than a set command). Verifies the runtime wraps every send origin (command,
poll-by-name, liveness probe), that the computed length tracks the payload, that
the block is opt-in, that it validates, and that the auto-simulator strips the
header on read and re-wraps it on reply so a simulated device round-trips.

Uses an invented receiver so no real product is named; the eISCP header layout
is a public binary framing, not a device.
"""

import copy

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition
from server.transport.frame_parsers import LengthPrefixFrameParser

# Invented receiver over an eISCP-style transport: "!1"/"\r" inner ISCP framing,
# wrapped in a 16-byte binary header (magic + header-size + 4-byte computed data
# length + version/reserved) declared once via send_frame.
ACME_EISCP = {
    "id": "acme_eiscp",
    "name": "Acme eISCP Receiver",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "tcp",
    "command_prefix": "!1",
    "command_suffix": "\\r",
    "send_frame": {
        "type": "length_prefix",
        "header": "ISCP\\x00\\x00\\x00\\x10",
        "length_size": 4,
        "length_endian": "big",
        "after_length": "\\x01\\x00\\x00\\x00",
    },
    "frame_parser": {
        "type": "length_prefix",
        "length_offset": 8,
        "header_size": 4,
        "header_extra": 4,
    },
    "default_config": {"host": "", "port": 60128},
    "config_schema": {
        "host": {"type": "string", "required": True, "label": "IP Address"},
        "port": {"type": "integer", "default": 60128, "label": "Port"},
    },
    "state_variables": {
        "power": {"type": "string", "label": "Power"},
    },
    "commands": {
        "power_on": {"label": "Power On", "send": "PWR01"},
        "power_query": {"label": "Power Query", "send": "PWRQSTN"},
        "set_dsp": {
            "label": "DSP Mode",
            "send": "LMD{mode}",
            "params": {"mode": {"type": "string", "required": True}},
        },
    },
    "polling": {"queries": ["power_query"]},
    "liveness": {"send": "!1PWRQSTN\\r", "expect": "!1PWR", "interval": 30},
    "responses": [
        {"match": "^!1PWR(0[01])", "set": {"power": "$1"}},
    ],
    "simulator": {
        "initial_state": {"power": "00"},
        "command_handlers": [
            {
                "match": "!1PWR(00|01)",
                "handler": (
                    'state["power"] = match.group(1)\n'
                    'respond(f\'!1PWR{state["power"]}\\r\')'
                ),
            },
            {
                "match": "!1PWRQSTN",
                "handler": 'respond(f\'!1PWR{state["power"]}\\r\')',
            },
        ],
    },
}


def _eiscp_frame(data: bytes) -> bytes:
    """The expected wire frame for a given ISCP data payload."""
    return (
        b"ISCP\x00\x00\x00\x10"
        + len(data).to_bytes(4, "big")
        + b"\x01\x00\x00\x00"
        + data
    )


class FakeTransport:
    connected = True

    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _make_driver(definition=ACME_EISCP, config=None):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    merged = config if config is not None else {"host": "127.0.0.1", "port": 60128}
    driver = cls("dev1", merged, state, events)
    driver.transport = FakeTransport()
    return driver


async def test_send_frame_wraps_file_command():
    driver = _make_driver()
    await driver.send_command("power_on")
    # Inner ISCP framing (!1...\r) wrapped in the 16-byte eISCP header.
    assert driver.transport.sent == [_eiscp_frame(b"!1PWR01\r")]


async def test_computed_length_tracks_payload():
    # The whole reason send_frame exists: the length field differs per message,
    # so a static command_prefix could never express it. A set (8-byte data) and
    # a feedback query (10-byte data) get different length bytes.
    driver = _make_driver()
    await driver.send_command("power_on")     # !1PWR01\r  -> 8
    await driver.send_command("power_query")  # !1PWRQSTN\r -> 10
    sent = driver.transport.sent
    assert sent[0][8:12] == (8).to_bytes(4, "big")
    assert sent[1][8:12] == (10).to_bytes(4, "big")
    assert sent[0] == _eiscp_frame(b"!1PWR01\r")
    assert sent[1] == _eiscp_frame(b"!1PWRQSTN\r")


async def test_parameterized_command_is_framed():
    driver = _make_driver()
    await driver.send_command("set_dsp", {"mode": "0F"})
    assert driver.transport.sent == [_eiscp_frame(b"!1LMD0F\r")]


async def test_poll_query_by_name_is_framed():
    driver = _make_driver()
    await driver.poll()
    assert driver.transport.sent == [_eiscp_frame(b"!1PWRQSTN\r")]


async def test_liveness_probe_is_framed():
    # The liveness probe is a raw string (author writes the !1.../\r), but the
    # send_frame packet header must still wrap it, or the probe never elicits a
    # reply on a length-framed transport and liveness would flap.
    driver = _make_driver()
    await driver._send_liveness_probe({"send": "!1PWRQSTN\\r"})
    assert driver.transport.sent == [_eiscp_frame(b"!1PWRQSTN\r")]


async def test_no_send_frame_leaves_command_unwrapped():
    # Opt-in: a driver with command framing but no send_frame sends only the
    # inner !1...\r, no packet header.
    definition = copy.deepcopy(ACME_EISCP)
    del definition["send_frame"]
    del definition["frame_parser"]
    driver = _make_driver(definition)
    await driver.send_command("power_on")
    assert driver.transport.sent == [b"!1PWR01\r"]


def test_send_frame_validates_clean():
    assert validate_driver_definition(copy.deepcopy(ACME_EISCP)) == []


def test_bad_send_frame_length_size_rejected():
    definition = copy.deepcopy(ACME_EISCP)
    definition["send_frame"]["length_size"] = 0
    errors = validate_driver_definition(definition)
    assert any("length_size" in e for e in errors)


def test_bad_send_frame_endian_rejected():
    definition = copy.deepcopy(ACME_EISCP)
    definition["send_frame"]["length_endian"] = "middle"
    errors = validate_driver_definition(definition)
    assert any("length_endian" in e for e in errors)


def test_bad_frame_parser_offset_rejected():
    definition = copy.deepcopy(ACME_EISCP)
    definition["frame_parser"]["length_offset"] = -1
    errors = validate_driver_definition(definition)
    assert any("length_offset" in e for e in errors)


async def test_simulator_strips_and_wraps_send_frame():
    # End-to-end simulator parity: the auto-sim must strip the packet header on
    # read (or the binary length byte would mis-split a line reader) and re-wrap
    # its reply so the driver's frame parser can read it back.
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="dev1", config={}, driver_def=ACME_EISCP)

    class FakeReader:
        def __init__(self, data):
            self._data = data
            self._sent = False

        async def read(self, _n):
            if self._sent:
                return b""
            self._sent = True
            return self._data

    # Read a full eISCP frame -> the sim strips the header down to the ISCP body.
    frame = _eiscp_frame(b"!1PWR01\r")
    buffer = bytearray()
    messages = await sim._read_messages(FakeReader(frame), buffer=buffer)
    assert messages == [b"!1PWR01\r"]

    # Dispatch the body -> state updates and the reply is re-wrapped in a frame
    # the driver's receive parser (length at offset 8) reads cleanly.
    response = sim.handle_command(messages[0])
    assert sim._state.get("power") == "01"
    parser = LengthPrefixFrameParser(header_size=4, length_offset=8, header_extra=4)
    assert parser.feed(response) == [b"!1PWR01\r"]
