"""What ends a device's lines: THE rule for its effective delimiter.

A device's ``delimiter`` is read by the running driver (``BaseDriver.
_resolve_delimiter``, which a TCP or serial transport splits replies on, and
which ends a raw send and splits a pushed datagram), by the simulator (which
ends its replies with it), by the send side (which ends a command the device
authored with it, ``send_framing.inline_suffix``) and by the simulator
validator (which judges the simulator's replies by it). Each asks here:

* **The precedence, once:** the driver's own ``delimiter`` (a YAML driver's
  file, which its ``DRIVER_INFO`` carries, or a Python driver's
  ``DRIVER_INFO``), else the device's (its config, the driver's
  ``default_config`` under it), else CR. A null is passed over, as if left
  out, so the next one is read.
* **An empty one means none,** not a delimiter of no bytes: the device page's
  Line ending control stores its None option as ``""``, and a driver may
  declare ``delimiter: ""`` for a raw byte stream.
* **One that is not text** (a number, a list, or text no bytes can carry: a
  lone surrogate such as ``"\\ud800"``) raises ``LineEndingNotText``,
  saying whether the driver or the device set it: the running driver refuses
  the connection with it, and the simulator and the send side end nothing
  with it.

Two answers are narrower than the line ending itself, for a YAML definition:

* ``sent_line_ending``: what a line the simulated device answers with, or a
  command the device authored, ends with. None on HTTP, whose replies are
  bodies and whose commands are requests.
* ``reply_line_ending``: what the driver splits the device's replies on, which
  the validator judges them by. Only a byte stream (TCP, serial) is split, and
  a driver whose ``frame_parser`` frames its replies reads none of them by a
  line ending (the transport builds the declared parser in its place).

``decode_delimiter`` is a line ending as the bytes on the wire, through the one
escape decoder (``binary_helpers.encode_escape_sequences``): ``effective_delimiter``
(the running driver), the simulator's replies and the validator's judging all
read it through here, so an escaped ``\\xFE`` is byte FE to each of them.

Not asked here, on purpose: a Python simulator's ``SIMULATOR_INFO.delimiter``
(``simulator/tcp_simulator.py``) is what the simulated device splits the
driver's lines on, and may differ from what the driver splits replies on; the
``auth:`` sign-in's credential lines carry their own ending (``auth_block``).

Pure: stdlib, ``transport_choice`` and ``binary_helpers``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openavc.drivers.spec import transport_has
from openavc.drivers.transport_choice import declared_transport
from openavc.transport.binary_helpers import encode_escape_sequences

#: What ends a device's lines when neither its driver nor the device names
#: anything (``spec.py``'s ``delimiter`` field says so to authors).
DEFAULT_LINE_ENDING = "\r"


class LineEndingNotText(ValueError):
    """A delimiter that is neither text that can be sent nor bytes, and who
    set it."""

    def __init__(self, value: Any, from_driver: bool) -> None:
        self.value = value
        self.from_driver = from_driver
        who = "the driver" if from_driver else "the device"
        super().__init__(f"{who}'s delimiter {value!r} is not text")


def line_ending(info: Mapping[str, Any], config: Mapping[str, Any]) -> str | bytes:
    """The line ending as written: ``info``'s ``delimiter`` (the driver's),
    else ``config``'s (the device's), else CR. ``""`` (or ``b""``) is none.
    Raises ``LineEndingNotText`` for a value that is neither."""
    for source, from_driver in ((info, True), (config, False)):
        value = source.get("delimiter")
        if value is None:
            continue
        if not _sendable(value):
            raise LineEndingNotText(value, from_driver)
        return value
    return DEFAULT_LINE_ENDING


def _sendable(value: Any) -> bool:
    """Bytes, or text the escape decoder can make bytes of (UTF-8 carries
    every character but a lone surrogate)."""
    if isinstance(value, bytes):
        return True
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def decode_delimiter(value: str | bytes) -> bytes:
    """A line ending as written, as the bytes on the wire: text through the
    one escape decoder (``\\r``, ``\\xFE`` as the bytes they name, every
    character as UTF-8), bytes as they are; ``b""`` for none."""
    return value if isinstance(value, bytes) else encode_escape_sequences(value)


def effective_delimiter(info: Mapping[str, Any], config: Mapping[str, Any]) -> bytes | None:
    """The line ending as bytes, or None for none. Raises
    ``LineEndingNotText``."""
    return decode_delimiter(line_ending(info, config)) or None


def _text_line_ending(definition: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    """A YAML definition's line ending as written, or ``""`` when it is none
    or not text (the running driver refuses the connection for the latter)."""
    try:
        value = line_ending(definition, config)
    except LineEndingNotText:
        return ""
    return value if isinstance(value, str) else ""


def sent_line_ending(definition: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    """What a line the simulated device answers with, or a command the device
    authored, ends with, as written: ``""`` on HTTP, or for none."""
    if declared_transport(definition) == "http":
        return ""
    return _text_line_ending(definition, config)


def reply_line_ending(definition: Mapping[str, Any], config: Mapping[str, Any]) -> str | None:
    """What the driver splits the device's replies on, as written, or None:
    not a byte stream, a ``frame_parser`` that frames them, or none."""
    # Split only a byte stream (the transport table's byte_stream column); a
    # datagram transport frames one message per packet.
    if not transport_has(declared_transport(definition), "byte_stream"):
        return None
    if definition.get("frame_parser"):
        return None
    return _text_line_ending(definition, config) or None
