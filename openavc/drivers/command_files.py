"""Files that travel with a driver command: the one a person picks, and the
one a command hands back.

A Python driver declares a ``file`` param (``drivers/spec.py``) and receives
a :class:`CommandFile` for it: the file the person picked on the device page
or in a Device Audit, staged on the server's disk until the command finishes.
A command declaring ``returns_file: true`` hands a file back by returning a
:class:`DeviceFile`; the device page offers it as a download.

Nothing here is kept. The staged copy is deleted when the command ends, and a
returned file once it has been downloaded (``core/file_staging.py`` owns
both). ``repr`` and ``str`` of either are the name and the size, so a log
line, an event or an audit record that prints a param prints no content.

Such a command runs only where a file can be given or taken. Every other door
(a macro, a panel button, a script, the cloud, the AI) is refused with
:class:`FileCommandError` by ``DeviceManager.send_command``, the one gate they
all pass.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

# What CommandFile.chunks() hands out when asked for no particular size.
DEFAULT_CHUNK_BYTES = 64 * 1024


def describe_size(size: int) -> str:
    """A byte count the way a person reads it: 940 bytes, 3.4 MB, 1.2 GB."""
    if size < 1000:
        return f"{size} byte{'' if size == 1 else 's'}"
    for unit, step in (("KB", 1000), ("MB", 1000**2), ("GB", 1000**3)):
        if size < step * 1000 or unit == "GB":
            value = size / step
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{size} bytes"  # pragma: no cover - the loop always returns


class FileCommandError(RuntimeError):
    """A command that takes or hands back a file was sent from a door that
    cannot give or take one, or was given something other than a file.

    A RuntimeError, so every door that words an exception for a person
    (``api.error_messages.friendly_error``) passes the sentence through as
    it is. The message is user-facing: it names the command and where it
    runs from.
    """


def takes_file_sentence(label: str) -> str:
    return f"'{label}' takes a file, so it runs from Send Command on the device's page."


def returns_file_sentence(label: str) -> str:
    return f"'{label}' sends back a file, so it runs from Send Command on the device's page."


class CommandFile:
    """A file a person picked for a command's ``file`` param.

    Read it whole with ``await file.read()``, or in pieces with
    ``async for chunk in file.chunks(65536)`` for a large one (firmware): the
    bytes come off the server's disk a piece at a time, so a large file never
    sits in memory. Reads run off the event loop. How much has been read is
    the progress the device page shows while the command runs.

    Valid only while the command runs; the file is deleted after it.
    """

    __slots__ = ("name", "size", "_path", "_read", "_handles")

    def __init__(self, name: str, size: int, path: Path) -> None:
        self.name = name
        self.size = size
        self._path = path
        self._read = 0
        self._handles: set[Any] = set()

    @property
    def bytes_read(self) -> int:
        """How many bytes the driver has read so far (the furthest point)."""
        return self._read

    async def read(self) -> bytes:
        """The whole file."""
        data = await asyncio.to_thread(self._path.read_bytes)
        self._read = max(self._read, len(data))
        return data

    async def chunks(self, size: int = DEFAULT_CHUNK_BYTES) -> AsyncIterator[bytes]:
        """The file in pieces of ``size`` bytes (the last one shorter)."""
        if size < 1:
            raise ValueError("chunk size must be at least 1 byte")
        handle = await asyncio.to_thread(self._path.open, "rb")
        self._handles.add(handle)
        try:
            offset = 0
            while True:
                piece = await asyncio.to_thread(handle.read, size)
                if not piece:
                    return
                offset += len(piece)
                self._read = max(self._read, offset)
                yield piece
        finally:
            self._handles.discard(handle)
            handle.close()

    def close(self) -> None:
        """Close what a reader left open. A driver that stops reading part
        way through (its command cancelled, an error) leaves its chunk
        reader to be finalized later; the platform closes it here, so the
        staged file can be deleted at once. Called by the platform."""
        for handle in list(self._handles):
            handle.close()
        self._handles.clear()

    def __repr__(self) -> str:
        return f"{self.name} ({describe_size(self.size)})"

    __str__ = __repr__


class DeviceFile:
    """A file a command hands back, such as a configuration backup.

    Return one from ``send_command`` for a command declaring
    ``returns_file: true``. ``name`` is what the download is called (a bare
    file name; any folder part is dropped), ``data`` its bytes.
    """

    __slots__ = ("name", "data", "content_type")

    def __init__(
        self,
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("DeviceFile data must be bytes")
        self.name = name
        self.data = bytes(data)
        self.content_type = content_type

    @property
    def size(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return f"{self.name} ({describe_size(self.size)})"

    __str__ = __repr__
