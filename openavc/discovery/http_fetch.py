"""One raw HTTP GET: what a device's web server says to a plain request.

Discovery reads two kinds of page. A UPnP device description, which the SSDP
scanner fetches from the address a device announced, and the page a device
serves at ``/``, which a device audit records with its status line, headers
and title. Both are one ``GET`` on a fresh connection bound to the control
interface, following no redirect and interpreting nothing, so both come
through here. HTTPS connects with discovery's permissive TLS context (a device
is not trusted yet) and keeps the certificate the device presented.

HTTP/1.0 with ``Connection: close`` on purpose: the server ends the response
by closing, so no chunked body or keep-alive has to be understood to know
where the page ends.
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
from dataclasses import dataclass, field
from typing import Any

from openavc.discovery.certificates import discovery_tls_context, read_peer_certificate

log = logging.getLogger("discovery.http_fetch")

_URL_RE = re.compile(r"(https?)://([^/:]+)(?::(\d+))?(/.*)?$", re.IGNORECASE)
_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_CHARSET_RE = re.compile(r"charset=([\w.:-]+)", re.IGNORECASE)


@dataclass
class HttpExchange:
    """One GET as it happened.

    ``head`` is the status line and headers as sent (decoded latin-1, so every
    byte survives); ``body`` the bytes after them, up to the read cap.
    ``error`` says why there is no response (``refused``, ``timeout``,
    ``tls: ...``) or why one broke off; whatever arrived before a break is
    kept. ``certificate`` is the one an HTTPS server presented.
    """

    url: str
    head: str = ""
    body: bytes = b""
    error: str = ""
    certificate: dict[str, Any] | None = None
    truncated: bool = False
    headers: list[tuple[str, str]] = field(default_factory=list)

    @property
    def status_line(self) -> str:
        return self.head.split("\r\n", 1)[0] if self.head else ""

    @property
    def status(self) -> int | None:
        parts = self.status_line.split(" ", 2)
        if len(parts) >= 2 and parts[0].upper().startswith("HTTP/") and parts[1].isdigit():
            return int(parts[1])
        return None

    def header(self, name: str) -> str | None:
        """The first header of that name (case-insensitive), or None."""
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return None

    def _decode(self, data: bytes) -> str:
        """``data`` decoded by the declared charset, else UTF-8, never raising."""
        charset = "utf-8"
        match = _CHARSET_RE.search(self.header("content-type") or "")
        if match:
            charset = match.group(1)
        try:
            return data.decode(charset, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")

    def body_text(self) -> str:
        return self._decode(self.body)

    def title(self) -> str | None:
        """The page's ``<title>``, whitespace collapsed, or None."""
        match = _TITLE_RE.search(self.body)
        if not match:
            return None
        text = " ".join(self._decode(match.group(1)).split())
        return text or None


def parse_head(head: str) -> list[tuple[str, str]]:
    """The headers of a response head, in order, repeats kept."""
    headers: list[tuple[str, str]] = []
    for line in head.split("\r\n")[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers.append((key.strip(), value.strip()))
    return headers


def _connect_error(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, ssl.SSLError):
        return f"tls: {exc}"
    return str(exc) or type(exc).__name__


async def http_get(
    url: str,
    *,
    timeout: float = 3.0,
    source_ip: str = "",
    max_bytes: int = 16384,
    read_to_end: bool = False,
) -> HttpExchange | None:
    """GET ``url`` once. None only when the URL is not ``http(s)://host...``.

    A scan takes the first read of up to ``max_bytes``. ``read_to_end`` keeps
    reading until the server closes, the cap, or the timeout, for a caller
    that wants the whole document. ``source_ip`` binds the connection to that
    local address (the control interface); empty lets the OS pick.
    """
    match = _URL_RE.match(url)
    if not match:
        return None
    scheme = match.group(1).lower()
    host = match.group(2)
    port = int(match.group(3)) if match.group(3) else (443 if scheme == "https" else 80)
    path = match.group(4) or "/"
    tls = scheme == "https"
    exchange = HttpExchange(url=url)

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, port,
                local_addr=(source_ip, 0) if source_ip else None,
                ssl=discovery_tls_context() if tls else None,
                server_hostname=host if tls else None,
            ),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, TimeoutError, OSError) as exc:
        exchange.error = _connect_error(exc)
        return exchange

    response = b""
    try:
        if tls:
            exchange.certificate = read_peer_certificate(writer)
        request = (
            f"GET {path} HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(request.encode("utf-8"))
        await writer.drain()

        response = await asyncio.wait_for(reader.read(max_bytes), timeout=timeout)
        if read_to_end:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            chunks = [response]
            size = len(response)
            chunk = response
            while chunk and size < max_bytes and loop.time() < deadline:
                try:
                    chunk = await asyncio.wait_for(
                        reader.read(max_bytes - size),
                        timeout=max(0.05, deadline - loop.time()),
                    )
                except asyncio.TimeoutError:
                    break
                chunks.append(chunk)
                size += len(chunk)
            response = b"".join(chunks)
            exchange.truncated = size >= max_bytes
    except (asyncio.TimeoutError, TimeoutError, OSError) as exc:
        exchange.error = _connect_error(exc)
    finally:
        try:
            writer.close()
        except OSError:
            pass

    split = response.find(b"\r\n\r\n")
    if split >= 0:
        exchange.head = response[:split].decode("latin-1")
        exchange.body = response[split + 4:]
    else:
        exchange.body = response
    exchange.headers = parse_head(exchange.head)
    return exchange
