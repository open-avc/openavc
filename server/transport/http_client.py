"""
OpenAVC HTTP Client Transport — async HTTP/REST client for device APIs.

Unlike TCP/Serial/UDP, HTTP is stateless request/response. There is no
persistent connection or streaming data callback. Instead, drivers call
request methods (get, post, put, delete) and get responses back directly.

Designed for modern AV devices that expose REST APIs: Panasonic PTZ cameras
(CGI), Sony Bravia displays, Crestron DM NVX, Barco ClickShare, Zoom Rooms,
QSC Q-SYS, and many more.

Auth methods supported:
    - none: Open API (no authentication)
    - basic: HTTP Basic Auth (username/password)
    - digest: HTTP Digest Auth (username/password)
    - bearer: Bearer token in Authorization header
    - api_key: Custom header (e.g., X-API-Key)

TLS:
    - HTTPS with trusted certs (verify_ssl=True)
    - HTTPS with self-signed certs (verify_ssl=False) — critical because
      almost every AV device with HTTPS uses self-signed certs
"""

from __future__ import annotations

import asyncio
import json as json_module
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from server.utils.logger import get_logger

log = get_logger(__name__)


class SSEEventStream:
    """One persistent Server-Sent-Events subscription (``push: {type: sse}``).

    Holds a GET request open with ``Accept: text/event-stream`` and delivers
    each event's assembled data block to the callback. The stream owns its
    own reconnect loop: a dropped or rejected connection is retried with
    exponential backoff (1 s doubling to 30 s, reset once data flows), so a
    device reboot or transient network cut re-establishes the subscription
    without tearing the driver down — polling covers the gap.

    ``idle_timeout`` (seconds) bounds the silence between received lines;
    when the device promises periodic keepalives, setting it slightly above
    the keepalive interval lets a half-open TCP connection (device power
    cut, NAT expiry) be detected and reconnected. 0 waits forever.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        path: str,
        callback: Callable[[bytes], Any],
        idle_timeout: float = 0.0,
        connect_timeout: float = 10.0,
        name: str | None = None,
    ) -> None:
        self.path = path
        self._client = client
        self._callback = callback
        self._idle_timeout = idle_timeout
        self._connect_timeout = connect_timeout
        self._name = name or path
        self._closed = False
        # First failure logs at warning; repeats drop to debug until a
        # connection succeeds again, so an unreachable device doesn't flood
        # the log at the retry cadence.
        self._warned = False
        self._task: asyncio.Task | None = asyncio.get_running_loop().create_task(
            self._run()
        )

    async def close(self) -> None:
        """Stop the stream and its reconnect loop (idempotent)."""
        self._closed = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        backoff = 1.0
        headers = {"Accept": "text/event-stream"}
        timeout = httpx.Timeout(
            connect=self._connect_timeout,
            read=self._idle_timeout if self._idle_timeout > 0 else None,
            write=self._connect_timeout,
            pool=self._connect_timeout,
        )
        while not self._closed:
            try:
                async with self._client.stream(
                    "GET", self.path, headers=headers, timeout=timeout
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise ConnectionError(
                            f"event stream rejected with HTTP "
                            f"{response.status_code}"
                        )
                    log.info(
                        f"[{self._name}] Event stream open: {self.path}"
                    )
                    self._warned = False
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        backoff = 1.0
                        if line == "":
                            # Blank line ends an event; dispatch its data.
                            if data_lines:
                                await self._dispatch("\n".join(data_lines))
                                data_lines = []
                            continue
                        if line.startswith(":"):
                            continue  # comment / keepalive
                        field, _, value = line.partition(":")
                        if value.startswith(" "):
                            value = value[1:]
                        if field == "data":
                            data_lines.append(value)
                        # event/id/retry fields carry no data — ignored.
                    # Server closed the stream cleanly; fall through to retry.
                    if data_lines:
                        await self._dispatch("\n".join(data_lines))
                log.debug(
                    f"[{self._name}] Event stream {self.path} ended; "
                    f"reconnecting"
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self._closed:
                    return
                msg = (
                    f"[{self._name}] Event stream {self.path} failed "
                    f"({str(e) or type(e).__name__}); retrying in "
                    f"{backoff:.0f}s"
                )
                if self._warned:
                    log.debug(msg)
                else:
                    log.warning(msg)
                    self._warned = True
            if self._closed:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _dispatch(self, data: str) -> None:
        """Hand one event's data to the callback; a callback error must not
        kill the stream (the next event may parse fine)."""
        try:
            result = self._callback(data.encode("utf-8"))
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception(
                f"[{self._name}] Error in event-stream callback"
            )


@dataclass
class HTTPResponse:
    """Lightweight wrapper around an HTTP response."""

    status_code: int
    headers: dict[str, str]
    text: str
    json_data: Any = None
    ok: bool = True

    def __repr__(self) -> str:
        return (
            f"HTTPResponse(status={self.status_code}, ok={self.ok}, "
            f"length={len(self.text)})"
        )


class HTTPClientTransport:
    """
    Async HTTP/REST client transport for device APIs.

    Provides both high-level methods (get, post, put, delete) and
    compatibility methods (send, send_and_wait) so it works with the
    BaseDriver/ConfigurableDriver transport interface.
    """

    def __init__(
        self,
        base_url: str,
        auth_type: str = "none",
        credentials: dict[str, str] | None = None,
        verify_ssl: bool = True,
        default_headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        name: str | None = None,
        local_address: str | None = None,
        max_response_bytes: int = 32 * 1024 * 1024,
    ):
        """
        Args:
            base_url: Base URL including scheme and port
                      (e.g., "http://192.168.1.100:80" or "https://10.0.0.5").
            auth_type: Authentication method — "none", "basic", "digest",
                       "bearer", or "api_key".
            credentials: Auth credentials dict. Keys depend on auth_type:
                - basic/digest: {"username": "...", "password": "..."}
                - bearer: {"token": "..."}
                - api_key: {"header": "X-API-Key", "key": "..."}
            verify_ssl: Whether to verify SSL certificates. Set to False
                        for self-signed certs (common on AV devices).
            default_headers: Headers sent with every request.
            timeout: Default request timeout in seconds.
            local_address: Optional IP to bind outgoing connections to a
                           specific network adapter.
            max_response_bytes: Ceiling on response body size. Device
                responses are untrusted network data; without a bound, one
                huge or runaway response materializes fully in memory and
                can take down the whole control server. 32 MB clears any
                realistic device API payload (JSON/XML status, EDID dumps,
                camera snapshots) while keeping memory bounded.
        """
        self.base_url = base_url.rstrip("/")
        self.auth_type = auth_type
        self.credentials = credentials or {}
        self.verify_ssl = verify_ssl
        self.default_headers = default_headers or {}
        self.timeout = timeout
        self._name = name or base_url
        self._local_address = local_address
        self.max_response_bytes = max_response_bytes

        self._client: httpx.AsyncClient | None = None
        self._last_response: HTTPResponse | None = None
        self.last_data_received: float = 0.0
        # Last request error string, for the connection-fault classifier.
        self._last_error = ""
        # Open event-stream subscriptions (push: {type: sse}); closed with
        # the client so a stream's reconnect loop never runs against a
        # closed session.
        self._event_streams: list[SSEEventStream] = []

    async def open(self) -> None:
        """Create the httpx.AsyncClient session with configured auth and TLS."""
        if self._client is not None:
            return  # Already open

        # Build auth
        auth = self._build_auth()

        # Build headers
        headers = dict(self.default_headers)

        # API key goes in headers
        if self.auth_type == "api_key":
            header_name = self.credentials.get("header", "X-API-Key")
            header_value = self.credentials.get("key", "")
            headers[header_name] = header_value

        # Bearer token goes in Authorization header
        if self.auth_type == "bearer":
            token = self.credentials.get("token", "")
            headers["Authorization"] = f"Bearer {token}"

        transport = None
        if self._local_address:
            transport = httpx.AsyncHTTPTransport(local_address=self._local_address)

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=auth,
            headers=headers,
            verify=self.verify_ssl,
            timeout=httpx.Timeout(self.timeout),
            transport=transport,
        )

        log.info(
            f"HTTP client opened: {self.base_url} "
            f"(auth={self.auth_type}, ssl_verify={self.verify_ssl})"
        )

    async def close(self) -> None:
        """Close the HTTP session (and any open event streams)."""
        for stream in list(self._event_streams):
            await stream.close()
        self._event_streams.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.info(f"HTTP client closed: {self.base_url}")

    def open_event_stream(
        self,
        path: str,
        callback: Callable[[bytes], Any],
        idle_timeout: float = 0.0,
        name: str | None = None,
    ) -> SSEEventStream:
        """Subscribe to a Server-Sent-Events endpoint (``push: {type: sse}``).

        Holds ``GET path`` open with ``Accept: text/event-stream`` on this
        client session (so auth headers/TLS settings apply) and delivers each
        event's data block to ``callback(bytes)`` (sync or async). The stream
        reconnects on its own with exponential backoff; ``await
        handle.close()`` (or closing this transport) stops it.

        Raises:
            ConnectionError: If the client is not open.
        """
        if self._client is None:
            raise ConnectionError("HTTP client not open — call open() first")
        # Prune subscriptions closed by their owner so repeated
        # subscribe/unsubscribe cycles on one session don't accumulate.
        self._event_streams = [s for s in self._event_streams if not s._closed]
        stream = SSEEventStream(
            client=self._client,
            path=path,
            callback=callback,
            idle_timeout=idle_timeout,
            connect_timeout=self.timeout,
            name=name or self._name,
        )
        self._event_streams.append(stream)
        return stream

    async def verify(self, timeout: float = 5.0) -> bool:
        """Verify the remote HTTP device is reachable with a HEAD request."""
        if self._client is None:
            return False
        try:
            await self._client.head("/", timeout=timeout)
            return True
        except Exception as e:
            # Keep the underlying cause so the connection-fault classifier can
            # tell "refused" / "unreachable" apart from a generic verify miss.
            self._last_error = str(e) or type(e).__name__
            return False

    @property
    def connected(self) -> bool:
        """HTTP is 'connected' if the client session exists."""
        return self._client is not None

    @property
    def last_error(self) -> str:
        """Last request error string (for the connection-fault classifier)."""
        return self._last_error

    @property
    def last_response(self) -> HTTPResponse | None:
        """The most recent response from send() or send_and_wait()."""
        return self._last_response

    # --- High-level request methods ---

    async def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> HTTPResponse:
        """HTTP GET request."""
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        body: Any = None,
        form_data: dict[str, str] | None = None,
    ) -> HTTPResponse:
        """HTTP POST with JSON body or form-encoded data."""
        return await self.request(
            "POST", path, json_body=body, form_data=form_data
        )

    async def put(self, path: str, body: Any = None) -> HTTPResponse:
        """HTTP PUT with JSON body."""
        return await self.request("PUT", path, json_body=body)

    async def delete(self, path: str) -> HTTPResponse:
        """HTTP DELETE request."""
        return await self.request("DELETE", path)

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        form_data: dict[str, str] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> HTTPResponse:
        """
        Generic HTTP request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH, etc.).
            path: URL path relative to base_url (e.g., "/api/status").
            params: Query parameters.
            json_body: JSON-serializable body (sets Content-Type: application/json).
            form_data: Form-encoded body (sets Content-Type: application/x-www-form-urlencoded).
            content: Raw bytes body.
            headers: Additional headers for this request only.
            timeout: Per-request timeout override; None uses the client default.

        Returns:
            HTTPResponse with status, headers, text, and parsed JSON if applicable.

        Raises:
            ConnectionError: If the client is not open.
            httpx.TimeoutException: If the request times out.
            httpx.ConnectError: If connection to the device fails.
            ValueError: If the response body exceeds max_response_bytes.
        """
        if self._client is None:
            raise ConnectionError("HTTP client not open — call open() first")

        method = method.upper()
        # Normalize path
        if not path.startswith("/"):
            path = "/" + path

        try:
            # Stream the response so the body can be bounded: device
            # responses are untrusted, and a non-streaming read would
            # materialize an arbitrarily large body before we could check.
            req = self._client.build_request(
                method,
                path,
                params=params,
                json=json_body,
                data=form_data,
                content=content,
                headers=headers,
                **({"timeout": timeout} if timeout is not None else {}),
            )
            response = await self._client.send(req, stream=True)
            try:
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_response_bytes:
                    msg = (
                        f"Response from {path} too large: {declared} bytes "
                        f"(limit {self.max_response_bytes})"
                    )
                    self._last_error = msg
                    raise ValueError(msg)
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        msg = (
                            f"Response from {path} too large: exceeded "
                            f"{self.max_response_bytes} bytes"
                        )
                        self._last_error = msg
                        raise ValueError(msg)
                    chunks.append(chunk)
                body = b"".join(chunks)
            finally:
                await response.aclose()

            try:
                text = body.decode(response.encoding or "utf-8", errors="replace")
            except LookupError:
                # Device sent a bogus charset in Content-Type
                text = body.decode("utf-8", errors="replace")

            # Parse JSON if content type indicates it
            json_data = None
            content_type = response.headers.get("content-type", "")
            if "json" in content_type or "javascript" in content_type:
                try:
                    json_data = json_module.loads(text)
                except (json_module.JSONDecodeError, ValueError):
                    pass

            result = HTTPResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                text=text,
                json_data=json_data,
                ok=response.is_success,
            )

            import time
            self.last_data_received = time.monotonic()
            log.info(
                f"[{self._name}] {method} {path} -> {result.status_code}"
            )
            return result

        except httpx.TimeoutException as e:
            # ConnectTimeout/ReadTimeout often stringify empty; fall back to the
            # class name so the classifier still sees a "timeout" signal.
            self._last_error = str(e) or type(e).__name__
            log.warning(f"HTTP {method} {path} timeout: {e}")
            raise
        except httpx.ConnectError as e:
            self._last_error = str(e) or type(e).__name__
            log.error(f"HTTP {method} {path} connection error: {e}")
            raise ConnectionError(
                f"Failed to connect to {self.base_url}{path}: {e}"
            ) from e
        except httpx.HTTPError as e:
            self._last_error = str(e) or type(e).__name__
            log.error(f"HTTP {method} {path} error: {e}")
            raise

    # --- Compatibility with BaseDriver/ConfigurableDriver transport interface ---

    async def send(
        self, data: bytes, *, timeout: httpx.Timeout | float | None = None
    ) -> HTTPResponse:
        """
        Compatibility method for the transport interface.

        Interprets data as a request string in one of these formats:
            - "GET /path"
            - "POST /path {json_body}"
            - "/path" (defaults to GET)

        Returns the response (also kept in the last_response property).
        """
        text = data.decode("utf-8", errors="replace").strip()
        method, path, body = self._parse_send_string(text)

        json_body = None
        if body:
            try:
                json_body = json_module.loads(body)
            except (json_module.JSONDecodeError, ValueError):
                # Not JSON — send as raw content
                result = await self.request(
                    method, path, content=body.encode("utf-8"),
                    timeout=timeout,
                )
                self._last_response = result
                return result

        result = await self.request(method, path, json_body=json_body, timeout=timeout)
        self._last_response = result
        return result

    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        """
        Send a request and return the response body as bytes.

        For HTTP, every request is inherently a send-and-wait, so this
        is straightforward — send the request and return the response text.

        The timeout rides the request as a per-call argument rather than
        shared transport state, so overlapping calls on one device (a poll
        racing a command) can't leak one call's timeout into the other's
        request.
        """
        req_timeout = httpx.Timeout(timeout) if timeout != self.timeout else None
        result = await self.send(data, timeout=req_timeout)
        return result.text.encode("utf-8")

    # --- Internal helpers ---

    def _build_auth(self) -> httpx.Auth | None:
        """Build httpx auth object from auth_type and credentials."""
        if self.auth_type == "basic":
            username = self.credentials.get("username", "")
            password = self.credentials.get("password", "")
            return httpx.BasicAuth(username, password)
        elif self.auth_type == "digest":
            username = self.credentials.get("username", "")
            password = self.credentials.get("password", "")
            return httpx.DigestAuth(username, password)
        # bearer and api_key are handled via headers in open()
        return None

    @staticmethod
    def _parse_send_string(text: str) -> tuple[str, str, str]:
        """
        Parse a send string into (method, path, body).

        Formats:
            "GET /api/status"               -> ("GET", "/api/status", "")
            "POST /api/power {\"on\":true}"  -> ("POST", "/api/power", '{"on":true}')
            "/api/status"                    -> ("GET", "/api/status", "")
        """
        if not text:
            return ("GET", "/", "")

        # Check if it starts with an HTTP method
        http_methods = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
        parts = text.split(None, 2)

        if parts[0].upper() in http_methods:
            method = parts[0].upper()
            path = parts[1] if len(parts) > 1 else "/"
            body = parts[2] if len(parts) > 2 else ""
            return (method, path, body)

        # No method prefix — default to GET, entire string is path
        # But check if there's a body after a space following the path
        if text.startswith("/"):
            # Find where path ends and body begins (body starts with { or [)
            space_idx = text.find(" ")
            if space_idx > 0:
                path = text[:space_idx]
                body = text[space_idx + 1:]
                # If there's a body, this is probably a POST
                if body.strip().startswith(("{", "[")):
                    return ("POST", path, body.strip())
                return ("GET", path, "")
            return ("GET", text, "")

        # Fallback: treat entire string as path
        return ("GET", "/" + text, "")
