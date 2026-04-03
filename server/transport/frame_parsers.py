"""
OpenAVC Frame Parsers — extract complete messages from a TCP/serial byte stream.

A FrameParser accumulates raw bytes via feed() and returns zero or more
complete messages when enough data has arrived. This decouples framing
logic from the transport layer.

Built-in parsers:
    - DelimiterFrameParser: splits on a byte sequence (e.g., \\r, \\r\\n)
    - LengthPrefixFrameParser: reads a length header then N bytes of payload
    - FixedLengthFrameParser: returns messages of exactly N bytes
    - CallableFrameParser: wraps a user function for custom protocols
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from server.utils.logger import get_logger

log = get_logger(__name__)

# Default max buffer size: 64 KB. Protects against unbounded growth from
# misbehaving devices or missing delimiters.
DEFAULT_MAX_BUFFER = 65536


class FrameParser(ABC):
    """Abstract base class for message frame parsers."""

    @abstractmethod
    def feed(self, data: bytes) -> list[bytes]:
        """
        Feed raw bytes into the parser.

        Returns a list of zero or more complete messages extracted from the
        internal buffer. Any incomplete trailing data is kept for the next
        feed() call.
        """

    @abstractmethod
    def reset(self) -> None:
        """Clear the internal buffer and any parser state."""


class DelimiterFrameParser(FrameParser):
    """
    Splits incoming bytes on a delimiter sequence.

    Messages are returned with the delimiter stripped.
    Empty messages (consecutive delimiters) are skipped.
    """

    def __init__(self, delimiter: bytes = b"\r", max_buffer: int = DEFAULT_MAX_BUFFER) -> None:
        if not delimiter:
            raise ValueError("Delimiter must not be empty")
        self._delimiter = delimiter
        self._buffer = b""
        self._max_buffer = max_buffer

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer += data
        messages: list[bytes] = []
        while self._delimiter in self._buffer:
            msg, self._buffer = self._buffer.split(self._delimiter, 1)
            if msg:  # Skip empty messages
                messages.append(msg)
        # Protect against unbounded growth (no delimiter arriving)
        if len(self._buffer) > self._max_buffer:
            log.warning(f"Delimiter parser buffer overflow ({len(self._buffer)} bytes), clearing")
            self._buffer = b""
        return messages

    def reset(self) -> None:
        self._buffer = b""


class LengthPrefixFrameParser(FrameParser):
    """
    Reads a fixed-size length header, then that many bytes of payload.

    The length header is big-endian unsigned int of ``header_size`` bytes
    (1, 2, or 4). An optional ``header_offset`` is added to the decoded
    length value (e.g., if the length field includes the header itself,
    set header_offset=-header_size).

    ``include_header`` controls whether the returned message includes the
    length header bytes or just the payload.
    """

    def __init__(
        self,
        header_size: int = 2,
        header_offset: int = 0,
        include_header: bool = False,
        max_buffer: int = DEFAULT_MAX_BUFFER,
    ) -> None:
        if header_size not in (1, 2, 4):
            raise ValueError("header_size must be 1, 2, or 4")
        self._header_size = header_size
        self._header_offset = header_offset
        self._include_header = include_header
        self._buffer = b""
        self._max_buffer = max_buffer

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer += data
        messages: list[bytes] = []
        while True:
            if len(self._buffer) < self._header_size:
                break
            # Decode length from header
            header = self._buffer[: self._header_size]
            payload_len = int.from_bytes(header, "big") + self._header_offset
            if payload_len < 0:
                payload_len = 0
            total = self._header_size + payload_len
            # Reject obviously bogus lengths
            if total > self._max_buffer:
                log.warning(f"Length-prefix parser: claimed size {total} exceeds max {self._max_buffer}, clearing buffer")
                self._buffer = b""
                break
            if len(self._buffer) < total:
                break
            if self._include_header:
                messages.append(self._buffer[:total])
            else:
                messages.append(self._buffer[self._header_size : total])
            self._buffer = self._buffer[total:]
        return messages

    def reset(self) -> None:
        self._buffer = b""


class FixedLengthFrameParser(FrameParser):
    """
    Returns messages of exactly ``length`` bytes.

    If the buffer contains fewer than ``length`` bytes, nothing is returned
    until enough data arrives.
    """

    def __init__(self, length: int, max_buffer: int = DEFAULT_MAX_BUFFER) -> None:
        if length <= 0:
            raise ValueError("length must be positive")
        self._length = length
        self._buffer = b""
        self._max_buffer = max_buffer

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer += data
        if len(self._buffer) > self._max_buffer:
            log.warning(
                "FixedLength parser buffer overflow (%d bytes), dropping oldest data",
                len(self._buffer),
            )
            self._buffer = self._buffer[-self._max_buffer:]
        messages: list[bytes] = []
        while len(self._buffer) >= self._length:
            messages.append(self._buffer[: self._length])
            self._buffer = self._buffer[self._length :]
        return messages

    def reset(self) -> None:
        self._buffer = b""


class CallableFrameParser(FrameParser):
    """
    Wraps a user-supplied callable for custom framing logic.

    The callable signature is::

        def parse(buffer: bytes) -> tuple[bytes | None, bytes]

    It receives the current buffer and must return:
        - ``(message, remaining)`` if a complete message was found
        - ``(None, buffer)`` if more data is needed

    The parser calls the function repeatedly until it returns None,
    collecting all extracted messages.
    """

    def __init__(
        self,
        parse_fn: Callable[[bytes], tuple[bytes | None, bytes]],
        max_buffer: int = DEFAULT_MAX_BUFFER,
    ) -> None:
        self._parse_fn = parse_fn
        self._buffer = b""
        self._max_buffer = max_buffer

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer += data
        messages: list[bytes] = []
        try:
            while True:
                msg, remaining = self._parse_fn(self._buffer)
                if msg is None:
                    break
                messages.append(msg)
                self._buffer = remaining
        except Exception:  # Catch-all: user-supplied parse_fn can raise anything
            log.exception("Error in custom frame parser function, clearing buffer")
            self._buffer = b""
        # Protect against unbounded growth
        if len(self._buffer) > self._max_buffer:
            log.warning(f"Callable parser buffer overflow ({len(self._buffer)} bytes), clearing")
            self._buffer = b""
        return messages

    def reset(self) -> None:
        self._buffer = b""
