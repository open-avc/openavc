"""THE reading of an HTTP text line: a request written as one line of text.

A driver writes an HTTP request as text wherever it writes a line rather than
a command's ``method`` / ``path`` / ``body`` fields: a command's ``send:``, a
poll, start-up or follow-up line, a ``read_back:`` query, the liveness probe,
and the Driver Builder's raw Send. Each is read here, by ``read_http_line``,
which the HTTP transport's own ``send``, the YAML runtime's lines, the raw
Send and the simulator validator all ask:

* ``METHOD /path body``: a method of ``METHODS`` (any case) first, then the
  path, then everything after it as the body.
* ``/path``: a GET of the path. After a space, a body that starts with ``{``
  or ``[`` makes it a POST of that JSON; anything else there is dropped.
* anything else: a GET of the whole text as a path.
* nothing: a GET of the root.

The path is given its leading slash in every form. A space ends the path, so a
value filled into a line that holds one splits the line there: a path that
needs a space writes it encoded (``%20``). A command's own ``read_back: true``
is not a line; it reads the command's path back with GET as it is.

How a body goes out is ``body_parts``: as JSON when it parses as JSON, else as
its text.

Pure (stdlib only), so the simulator process reads a line without importing
the HTTP client.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

#: The methods a line may name, in the order the reading tries them.
METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")


class HttpLine(NamedTuple):
    """A line read as a request: its method (upper case), its path (with its
    leading slash, any query string kept) and its body as written ("" for
    none)."""

    method: str
    path: str
    body: str


def read_http_line(text: str) -> HttpLine:
    """``text`` read as a request (the module docstring has the forms)."""
    text = text.strip()
    parts = text.split(None, 2)
    if parts and parts[0].upper() in METHODS:
        return _named(parts)
    return _unnamed(text)


def body_parts(text: str) -> tuple[Any, bytes | None]:
    """A filled-in body as (JSON, raw content): the JSON when it parses, else
    the text as raw content."""
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError):
        return None, text.encode("utf-8")


def _named(parts: list[str]) -> HttpLine:
    """A line that names its method: the method, the path (the root when
    none), then the body."""
    method, path, body = parts + [""] * (3 - len(parts))
    return HttpLine(method.upper(), _rooted(path), body)


def _unnamed(text: str) -> HttpLine:
    """A line that names no method: a GET of a path, or a POST when JSON
    follows a path that starts with a slash."""
    if not text.startswith("/"):
        return HttpLine("GET", _rooted(text), "")
    path, _space, rest = text.partition(" ")
    rest = rest.strip()
    if rest.startswith(("{", "[")):
        return HttpLine("POST", path, rest)
    return HttpLine("GET", path, "")


def _rooted(path: str) -> str:
    """A path with its leading slash: the root for none."""
    return path if path.startswith("/") else "/" + path
