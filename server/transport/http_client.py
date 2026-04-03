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

import json as json_module
from dataclasses import dataclass
from typing import Any

import httpx

from server.utils.logger import get_logger

log = get_logger(__name__)


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
        """
        self.base_url = base_url.rstrip("/")
        self.auth_type = auth_type
        self.credentials = credentials or {}
        self.verify_ssl = verify_ssl
        self.default_headers = default_headers or {}
        self.timeout = timeout
        self._name = name or base_url

        self._client: httpx.AsyncClient | None = None
        self._last_response: HTTPResponse | None = None

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

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=auth,
            headers=headers,
            verify=self.verify_ssl,
            timeout=httpx.Timeout(self.timeout),
        )

        log.info(
            f"HTTP client opened: {self.base_url} "
            f"(auth={self.auth_type}, ssl_verify={self.verify_ssl})"
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.info(f"HTTP client closed: {self.base_url}")

    @property
    def connected(self) -> bool:
        """HTTP is 'connected' if the client session exists."""
        return self._client is not None

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

        Returns:
            HTTPResponse with status, headers, text, and parsed JSON if applicable.

        Raises:
            ConnectionError: If the client is not open.
            httpx.TimeoutException: If the request times out.
            httpx.ConnectError: If connection to the device fails.
        """
        if self._client is None:
            raise ConnectionError("HTTP client not open — call open() first")

        method = method.upper()
        # Normalize path
        if not path.startswith("/"):
            path = "/" + path

        try:
            req_timeout = getattr(self, "_request_timeout", None)
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                data=form_data,
                content=content,
                headers=headers,
                **({"timeout": req_timeout} if req_timeout else {}),
            )

            # Parse JSON if content type indicates it
            json_data = None
            content_type = response.headers.get("content-type", "")
            if "json" in content_type or "javascript" in content_type:
                try:
                    json_data = response.json()
                except (json_module.JSONDecodeError, ValueError):
                    pass

            result = HTTPResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                text=response.text,
                json_data=json_data,
                ok=response.is_success,
            )

            log.info(
                f"[{self._name}] {method} {path} -> {result.status_code}"
            )
            return result

        except httpx.TimeoutException as e:
            log.warning(f"HTTP {method} {path} timeout: {e}")
            raise
        except httpx.ConnectError as e:
            log.error(f"HTTP {method} {path} connection error: {e}")
            raise ConnectionError(
                f"Failed to connect to {self.base_url}{path}: {e}"
            ) from e
        except httpx.HTTPError as e:
            log.error(f"HTTP {method} {path} error: {e}")
            raise

    # --- Compatibility with BaseDriver/ConfigurableDriver transport interface ---

    async def send(self, data: bytes) -> None:
        """
        Compatibility method for the transport interface.

        Interprets data as a request string in one of these formats:
            - "GET /path"
            - "POST /path {json_body}"
            - "/path" (defaults to GET)

        Stores the response for retrieval via last_response property.
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
                    method, path, content=body.encode("utf-8")
                )
                self._last_response = result
                return

        result = await self.request(method, path, json_body=json_body)
        self._last_response = result

    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        """
        Send a request and return the response body as bytes.

        For HTTP, every request is inherently a send-and-wait, so this
        is straightforward — send the request and return the response text.
        """
        # Store timeout override for the next send() call
        self._request_timeout = httpx.Timeout(timeout) if timeout != self.timeout else None
        try:
            await self.send(data)
            if self._last_response is not None:
                return self._last_response.text.encode("utf-8")
            return b""
        finally:
            self._request_timeout = None

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
