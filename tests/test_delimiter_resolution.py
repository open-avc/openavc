"""An empty line ending means "no framing", at every layer that reads one.

The device page's Line ending control offers None, and stores it as ``""``.
That empty string used to survive all the way to DelimiterFrameParser, whose
constructor rejects an empty delimiter -- so the transport raised while being
BUILT and the device could never connect, on every attempt, forever. The
reported symptom was a device that stayed offline with CR, LF and CRLF all
working normally.

Three layers have to agree for that to stay fixed, and each is pinned here:
the driver resolves empty to None, both byte-stream transports treat an empty
delimiter as raw mode rather than building a parser on it, and the parser
itself still refuses an empty delimiter (the guard is right; nothing should be
reaching it).
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport.frame_parsers import DelimiterFrameParser
from openavc.transport.serial_transport import SerialTransport
from openavc.transport.tcp import TCPTransport


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
        "state_variables": {}, "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _mk(config=None, driver_info_delimiter=...):
    cls = _AcmeWidget
    if driver_info_delimiter is not ...:
        cls = type("_AcmeWidgetD", (_AcmeWidget,), {
            "DRIVER_INFO": {**_AcmeWidget.DRIVER_INFO,
                            "delimiter": driver_info_delimiter},
        })
    return cls(device_id="widget_1", config=config or {},
               state=StateStore(), events=EventBus())


# --- The driver ------------------------------------------------------------


@pytest.mark.parametrize("configured, expected", [
    ("", None),        # the Line ending "None" option
    (b"", None),       # same statement, already encoded
    ("\r\n", b"\r\n"),
    ("\r", b"\r"),
    ("\n", b"\n"),
])
def test_device_config_delimiter_resolves(configured, expected):
    assert _mk({"delimiter": configured})._resolve_delimiter() == expected


def test_absent_delimiter_falls_back_to_cr():
    assert _mk({})._resolve_delimiter() == b"\r"


@pytest.mark.parametrize("declared, expected", [
    ("", None),
    (b"", None),
    ("\r\n", b"\r\n"),
])
def test_driver_declared_delimiter_resolves(declared, expected):
    """A YAML/Python driver declaring an empty delimiter means raw too."""
    assert _mk({}, driver_info_delimiter=declared)._resolve_delimiter() == expected


def test_driver_declaration_still_wins_over_device_config():
    """Normalising empty must not disturb the precedence: what the driver
    declares beats what the device config carries, empty included."""
    d = _mk({"delimiter": "\r\n"}, driver_info_delimiter="")
    assert d._resolve_delimiter() is None


# --- The transports --------------------------------------------------------


def _tcp(delimiter):
    return TCPTransport(
        host="192.0.2.10", port=23, on_data=lambda _d: None,
        on_disconnect=lambda: None, delimiter=delimiter,
        timeout=5.0, inter_command_delay=0.0,
    )


def _serial(delimiter):
    return SerialTransport(
        port="SIM:acme", baudrate=9600, on_data=lambda _d: None,
        on_disconnect=lambda: None, delimiter=delimiter,
    )


@pytest.mark.parametrize("build", [_tcp, _serial], ids=["tcp", "serial"])
def test_empty_delimiter_selects_raw_mode(build):
    """Constructing with an empty delimiter is raw mode, not a raised error.

    This is the construction that used to raise. It must not raise, and it
    must not leave a parser behind that would split on nothing.
    """
    assert build(b"")._frame_parser is None


@pytest.mark.parametrize("build", [_tcp, _serial], ids=["tcp", "serial"])
def test_none_delimiter_selects_raw_mode(build):
    assert build(None)._frame_parser is None


@pytest.mark.parametrize("build", [_tcp, _serial], ids=["tcp", "serial"])
def test_real_delimiter_still_builds_a_parser(build):
    parser = build(b"\r\n")._frame_parser
    assert isinstance(parser, DelimiterFrameParser)


# --- The parser's own guard ------------------------------------------------


def test_frame_parser_still_refuses_an_empty_delimiter():
    """Nothing should reach it with one now, but splitting on a zero-length
    delimiter is still meaningless, so the guard stays."""
    with pytest.raises(ValueError, match="must not be empty"):
        DelimiterFrameParser(b"")
