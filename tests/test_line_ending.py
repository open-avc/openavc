"""A device's line ending is one answer, whoever asks.

What ends a device's lines (its ``delimiter``) is read by the running driver,
which splits replies on it and ends a raw send with it; by the simulator, which
ends its replies with it; by the send side, which ends a command the device
authored with it; and by the simulator validator, which judges the simulator's
replies by it. They used to read it four ways: with no delimiter in the file
the driver split on CR while the simulator answered CR LF, and with one in both
the file and the device's settings the driver used the file's while the
simulator and the send side used the device's.

The rule is ``drivers/line_ending.py``'s: the driver's delimiter, else the
device's, else CR; an empty one means none. One table of cases runs through
every reader here, so a reader that works it out for itself fails a row.

A driver whose replies a ``frame_parser`` frames reads none of them by a line
ending, so the validator judges no line ending there; and a driver with none
in the file is judged by the CR the driver splits on, not let off
(``test_simulator_validate_reply_framing.py`` has both on the replies
themselves).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.line_ending import (
    LineEndingNotText,
    effective_delimiter,
    line_ending,
    reply_line_ending,
    sent_line_ending,
)
from openavc.drivers.send_framing import framed_send
from openavc.simulator.reply_reading import frame_delimiter
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport.binary_helpers import encode_escape_sequences

_MISSING = object()


def _definition(delimiter: Any = _MISSING, **over: Any) -> dict:
    """An invented one-command widget, its delimiter as given (left out when
    not)."""
    definition: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {"power": {"type": "integer", "label": "Power"}},
        "commands": {"query_power": {"label": "Query Power", "send": "PWR?\r"}},
        "responses": [{"match": r"PWR (\d)", "set": {"power": "$1"}}],
    }
    if delimiter is not _MISSING:
        definition["delimiter"] = delimiter
    definition.update(over)
    return definition


# (the file's delimiter, the device's, the line ending as bytes)
CASES = [
    pytest.param(_MISSING, _MISSING, b"\r", id="neither-declares: CR"),
    pytest.param("\r\n", _MISSING, b"\r\n", id="the-file's"),
    pytest.param(_MISSING, "\n", b"\n", id="the-device's"),
    pytest.param("\r\n", "\r", b"\r\n", id="the-file-wins-over-the-device"),
    pytest.param("\\r\\n", _MISSING, b"\r\n", id="an-escaped-file-delimiter"),
    pytest.param(None, "\n", b"\n", id="a-null-in-the-file-is-passed-over"),
    pytest.param("", "\r", None, id="an-empty-file-delimiter-is-none"),
    pytest.param(_MISSING, "", None, id="the-device's-none-option"),
    pytest.param(">", _MISSING, b">", id="a-terminator-of-its-own"),
    pytest.param("\\xFE", _MISSING, b"\xfe", id="a-byte-escape-above-7F-is-one-byte"),
    pytest.param("\\x03", "\r", b"\x03", id="a-control-byte-escape"),
]


def _config(device: Any) -> dict:
    return {} if device is _MISSING else {"delimiter": device}


def _runtime(definition: dict, config: dict) -> bytes | None:
    driver_class = create_configurable_driver_class(definition)
    driver = driver_class(
        device_id="widget_1", config=config, state=StateStore(), events=EventBus()
    )
    return driver._resolve_delimiter()


def _simulator(definition: dict, config: dict) -> bytes | None:
    sim = YAMLAutoSimulator("widget_1", config, driver_def=definition)
    return sim._get_delimiter() or None


def _authored_ending(definition: dict, config: dict) -> bytes | None:
    """What the device's own command ``PWR?`` ends with on the wire (its
    escapes decoded, as the send path decodes them)."""
    framed = framed_send(definition, config, {"send": "PWR?"}, authored=True)
    return encode_escape_sequences(framed[len("PWR?"):]) or None


@pytest.mark.parametrize("in_file, on_device, expected", CASES)
def test_the_rule(in_file, on_device, expected):
    assert effective_delimiter(_definition(in_file), _config(on_device)) == expected


@pytest.mark.parametrize("in_file, on_device, expected", CASES)
def test_the_running_driver_splits_on_it(in_file, on_device, expected):
    assert _runtime(_definition(in_file), _config(on_device)) == expected


@pytest.mark.parametrize("in_file, on_device, expected", CASES)
def test_the_simulator_ends_its_replies_with_it(in_file, on_device, expected):
    assert _simulator(_definition(in_file), _config(on_device)) == expected


@pytest.mark.parametrize("in_file, on_device, expected", CASES)
def test_a_command_the_device_authored_ends_with_it(in_file, on_device, expected):
    assert _authored_ending(_definition(in_file), _config(on_device)) == expected


@pytest.mark.parametrize("in_file, on_device, expected", CASES)
def test_the_validator_judges_replies_by_it(in_file, on_device, expected):
    """The validator reads a definition with no device: the driver's
    default_config is the device's settings there."""
    definition = _definition(in_file, default_config=_config(on_device))
    assert frame_delimiter(definition, _config(on_device)) == expected


def test_the_simulator_reads_the_driver_defaults_as_the_device_does():
    """A device with no line ending of its own saved takes its driver's
    default_config one, in the running driver and in the simulator alike."""
    definition = _definition(default_config={"delimiter": "\r"})
    device_config = {"delimiter": "\r"}  # what the platform's merge hands the driver
    assert _runtime(definition, device_config) == b"\r"
    assert _simulator(definition, {}) == b"\r"


def test_a_delimiter_that_is_not_text_is_named_with_who_set_it():
    with pytest.raises(LineEndingNotText) as from_driver:
        line_ending({"delimiter": 13}, {})
    assert from_driver.value.from_driver and from_driver.value.value == 13
    with pytest.raises(LineEndingNotText) as from_device:
        line_ending({}, {"delimiter": ["\r"]})
    assert not from_device.value.from_driver


def test_a_delimiter_no_bytes_can_carry_is_not_text():
    """A lone surrogate cannot be sent at all: the running driver refuses it
    as it refuses a number, and the simulator and the validator end nothing
    with it, instead of each raising its own encoding error."""
    with pytest.raises(LineEndingNotText) as from_driver:
        line_ending({"delimiter": "\ud800"}, {})
    assert from_driver.value.from_driver
    definition = _definition("\ud800")
    assert sent_line_ending(definition, {}) == ""
    assert reply_line_ending(definition, {}) is None
    assert frame_delimiter(definition, {}) is None


def test_a_python_driver_may_declare_bytes():
    assert effective_delimiter({"delimiter": b"\x03"}, {}) == b"\x03"
    assert effective_delimiter({"delimiter": b""}, {}) is None


def test_an_http_definition_sends_no_line_ending():
    """An HTTP reply is a body and an HTTP command a request: no line ends
    them, though the running driver's hook still answers CR for a raw send."""
    definition = _definition(transport="http")
    assert sent_line_ending(definition, {}) == ""
    assert _authored_ending(definition, {}) is None
    assert _simulator(definition, {}) is None


def test_a_reply_a_frame_parser_frames_has_no_line_ending():
    definition = _definition(
        "\r", frame_parser={"type": "length_prefix", "header_size": 1}
    )
    assert reply_line_ending(definition, {}) is None
    assert frame_delimiter(definition, {}) is None
    # The line ending is still what a line the device's driver sends ends with.
    assert effective_delimiter(definition, {}) == b"\r"


def test_a_datagram_transport_splits_no_replies():
    assert reply_line_ending(_definition("\r", transport="udp"), {}) is None


def test_the_rule_is_importable_with_nothing_but_its_leaves():
    """The simulator, a separate process, and the validator import it: it
    pulls in no driver runtime, transport stack or logger."""
    code = (
        "import sys\n"
        "import openavc.drivers.line_ending\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('openavc'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1], timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == {
        "openavc", "openavc.drivers", "openavc.drivers.line_ending",
        "openavc.drivers.transport_choice", "openavc.transport",
        "openavc.transport.binary_helpers",
    }
