"""THE read of a Python file's source text, as the import reads it, and the
write that the import reads back as written.

The import decodes a ``.py`` file's bytes itself: a UTF-8 byte-order mark is
dropped, a coding line (``# -*- coding: latin-1 -*-``) names the encoding the
rest is read in, and anything else is UTF-8. A reader that decoded the bytes
as UTF-8 text instead would read a byte-order mark as a syntax error and a
latin-1 file as undecodable, while the runtime loads both. So every reader of
driver or simulator source asks ``python_source``: the checker, the catalog,
the upload's floor gate, the Code view, the driver list and the simulator
checks. A file the import cannot decode is ``SourceDecodeError`` at each of
them, never an empty reading.

``encode_python_source`` is the inverse, for a door that writes text an
author typed (the Code view's save and create): the text in the encoding its
own coding line names, so the import reads back what the editor showed.

Purity contract: standard library only, like ``python_info``, which imports
it; vendored into openavc-drivers with it.
"""

from __future__ import annotations

import importlib.util
import io
import tokenize
from pathlib import Path


class SourceDecodeError(ValueError):
    """A Python file whose bytes the import cannot decode. The message names
    the file; ``without_name`` is the same sentence for a reader that already
    names it."""

    def __init__(self, filename: str, reason: str) -> None:
        self.without_name = (
            f"cannot be read as Python source ({reason}). Save it as UTF-8 text."
        )
        super().__init__(f"{filename} {self.without_name}")


def python_source(filepath: Path) -> str:
    """A Python file's source text: its bytes decoded the way the import
    decodes them (``importlib.util.decode_source``), a UTF-8 byte-order mark
    dropped, a coding line obeyed, and line endings made ``\\n``.

    Raises ``OSError`` when the file cannot be read, and ``SourceDecodeError``
    when its bytes cannot be decoded: an encoding Python does not know, or a
    byte the encoding cannot hold. The import fails on the same file.
    """
    return decode_python_source(filepath.read_bytes(), filepath.name)


def decode_python_source(data: bytes, filename: str) -> str:
    """``python_source`` on bytes in hand, named ``filename`` in messages."""
    try:
        return importlib.util.decode_source(data)
    except (SyntaxError, UnicodeDecodeError) as e:
        raise SourceDecodeError(filename, str(e)) from e


class SourceEncodeError(ValueError):
    """Source text the import would not read back as written: its coding
    line names an encoding Python does not know, or one that cannot hold a
    character in it. The message names the line and the fix."""


def encode_python_source(source: str) -> bytes:
    """The bytes the import reads back as ``source``: the text in the
    encoding its own coding line names, else UTF-8."""
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source.encode("utf-8")).readline)
    except SyntaxError as e:
        raise SourceEncodeError(
            f"The file's coding line names an encoding Python cannot read ({e}). "
            "Change it, or delete it to save the file as UTF-8."
        ) from e
    try:
        return source.encode(encoding)
    except UnicodeEncodeError as e:
        line = source.count("\n", 0, e.start) + 1
        raise SourceEncodeError(
            f"Line {line} has a character ({source[e.start]}) that the encoding named on "
            "the file's coding line cannot hold. Change the character, or delete the "
            "coding line to save the file as UTF-8."
        ) from e
