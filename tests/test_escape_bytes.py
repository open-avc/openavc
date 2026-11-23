"""A typed backslash escape is one set of bytes, whoever reads it.

A driver file writes a byte it cannot type as an escape (``\\r``, ``\\x03``,
``\\xFE``). The running driver sends and splits on the bytes
``binary_helpers.encode_escape_sequences`` makes of it: a byte for each
``\\xHH``, every other character as UTF-8. The simulator reads the same text
when it recognises a line the driver sends, so it reads the line the driver's
bytes arrive as (decoded the way its dispatcher decodes what arrives), not a
character of its own: ``\\xFE`` is byte FE on the wire, which no character's
UTF-8 is.

The line ending itself is ``test_line_ending.py``'s, and the raw probe's text
is ``test_driver_test_raw_probe.py``'s. An invented device throughout.
"""

from __future__ import annotations

import logging

import pytest

from openavc.drivers.line_ending import decode_delimiter
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport.binary_helpers import (
    encode_escape_sequences,
    has_incomplete_byte_escape,
    sent_text,
)


@pytest.mark.parametrize(("written", "on_the_wire"), [
    pytest.param("\\r\\n", b"\r\n", id="CR-LF-escaped"),
    pytest.param("\r\n", b"\r\n", id="CR-LF-as-characters"),
    pytest.param("\\t", b"\t", id="a-tab"),
    pytest.param("\\x03", b"\x03", id="a-control-byte"),
    pytest.param("\\xFE", b"\xfe", id="a-byte-above-7F"),
    pytest.param("\\\\", b"\\", id="an-escaped-backslash"),
    pytest.param("\\q", b"\\q", id="an-unknown-escape-as-typed"),
    pytest.param("#", b"#", id="a-terminator-of-its-own"),
    pytest.param("\u00fe", "\u00fe".encode(), id="a-character-is-its-UTF-8-never-a-byte"),
    pytest.param("", b"", id="none"),
    pytest.param(b"\x03", b"\x03", id="bytes-as-they-are"),
])
def test_a_line_ending_is_the_bytes_the_one_decoder_makes(written, on_the_wire):
    assert decode_delimiter(written) == on_the_wire


@pytest.mark.parametrize(("typed", "incomplete"), [
    pytest.param("PWR\\x", True, id="a-bare-x"),
    pytest.param("PWR\\x0", True, id="one-hex-digit"),
    pytest.param("\\xZZ", True, id="not-hex"),
    pytest.param("\\x0D", False, id="a-whole-byte"),
    pytest.param("A\\\\x", False, id="an-escaped-backslash-then-x"),
    pytest.param("A\\\\\\x", True, id="an-escaped-backslash-then-a-bare-x"),
    pytest.param("PWR?\\r", False, id="no-byte-escape"),
])
def test_an_incomplete_byte_escape_is_one_the_decoder_sends_as_typed(typed, incomplete):
    assert has_incomplete_byte_escape(typed) is incomplete


def test_a_sent_line_reads_as_its_bytes_arrive():
    """What the simulator matches a driver's line by: an escaped byte above
    7F is not valid UTF-8 alone, so it reads as U+FFFD, as it arrives."""
    assert sent_text("\\x02PWR\\x03\\r") == "\x02PWR\x03\r"
    assert sent_text("PWR?\\xFE") == "PWR?\ufffd"
    assert sent_text("Caf\u00e9") == "Caf\u00e9"


def _widget(**over) -> dict:
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "delimiter": "\r",
        "state_variables": {
            "power": {"type": "integer", "label": "Power"},
            "level": {"type": "integer", "label": "Level"},
        },
        "commands": {},
        "responses": [{"match": r"PWR (\d)", "set": {"power": "$1"}}],
    }
    definition.update(over)
    return definition


def _simulator(definition: dict) -> YAMLAutoSimulator:
    logging.getLogger("openavc.simulator.yaml_auto").setLevel(logging.WARNING)
    return YAMLAutoSimulator("widget_1", {}, driver_def=definition)


@pytest.mark.parametrize("query", [
    pytest.param("\\x02PWR?\\x03", id="control-bytes"),
    pytest.param("PWR?\\xFE", id="a-byte-above-7F"),
])
def test_the_simulator_answers_a_poll_line_as_the_driver_sends_it(query):
    sim = _simulator(_widget(
        polling={"interval": 5, "queries": [{"send": query, "query_for": "power"}]},
    ))
    sent = encode_escape_sequences(query + "\\r")  # the driver's bytes

    assert sim.handle_command(sent) is not None


def test_the_simulator_reads_a_setting_write_as_the_driver_sends_it():
    sim = _simulator(_widget(device_settings={
        "level": {
            "type": "integer", "label": "Level", "state_key": "level",
            "write": {"send": "LVL\\xA7{value}\\r"},
        },
    }))

    sim.handle_command(encode_escape_sequences("LVL\\xA77\\r"))

    assert sim.get_state("level") == 7
