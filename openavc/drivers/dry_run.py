"""What a driver would send, built by the real runtime against transports that record.

Every command route in a declarative driver ends at a transport call, so a
transport that records instead of transmitting yields the finished wire form:
prefix and suffix framing, substituted placeholders, decoded escapes, the
``send_frame`` header, an HTTP request exactly as httpx would build it. None of
it is re-derived here, so a preview cannot drift from the real send the way a
second implementation of the protocol build would. No socket, no device.

Two callers:

- **One command** (:func:`capture_command`): the Driver Builder's dry run,
  asked on every keystroke while an author edits a command.
- **What connecting sends** (:func:`preview_connect`): a device audit shows it
  before it connects, so the person knows what adding the device will put on
  the wire: the sign-in (the ``auth:`` handshake, its prompt waits answered at
  once), the start-up steps (``on_connect``), one round of status polling, and
  the keep-alive probe. For a Python driver these are code, and the preview
  says so rather than guessing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from openavc.utils.logger import get_logger

log = get_logger(__name__)


class CaptureTransport:
    """Byte-stream stand-in (TCP, UDP, serial) that records instead of sending."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    @property
    def connected(self) -> bool:
        return True

    async def send(self, data: bytes) -> None:
        self.frames.append(bytes(data))

    async def close(self) -> None:
        return None


class CaptureUdp:
    """Stands in for the ad-hoc UDP socket BaseDriver.send_udp opens."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, str, int]] = []

    async def send_to(self, data: bytes, host: str, port: int) -> None:
        self.sent.append((bytes(data), host, port))

    async def close(self) -> None:
        return None


def capture_osc_transport() -> Any:
    """OSC capture double.

    Subclasses OSCTransport rather than duck-typing it: the OSC sender refuses
    a transport that isn't one (and so does the OSC device-setting write), so
    a stand-in has to pass the isinstance check.
    """
    from openavc.transport.osc import OSCTransport

    class _CaptureOSCTransport(OSCTransport):
        def __init__(self) -> None:
            super().__init__()
            self.frames: list[bytes] = []

        @property
        def connected(self) -> bool:
            return True

        async def send(self, data: bytes) -> None:
            self.frames.append(bytes(data))

        async def close(self) -> None:
            return None

    return _CaptureOSCTransport()


def capture_http_transport() -> Any:
    """HTTP capture double, a subclass for the same reason as the OSC one.

    Records the request the driver built and hands back an empty response, so
    the send path completes normally without anything leaving the process.
    """
    from openavc.transport.http_client import HTTPClientTransport, HTTPResponse

    class _CaptureHTTPTransport(HTTPClientTransport):
        def __init__(self) -> None:
            super().__init__(base_url="http://device")
            self.requests: list[dict[str, Any]] = []

        @property
        def connected(self) -> bool:
            return True

        async def request(
            self,
            method: str,
            path: str,
            params: dict[str, Any] | None = None,
            json_body: Any = None,
            form_data: dict[str, str] | None = None,
            content: bytes | None = None,
            headers: dict[str, str] | None = None,
            timeout: Any = None,
        ) -> Any:
            import httpx

            if not path.startswith("/"):
                path = "/" + path
            # Let httpx assemble the request exactly as it would for a real
            # send, so the query string and the encoded body are the genuine
            # article rather than a second rendering of them.
            req = httpx.Request(
                method.upper(),
                httpx.URL(self.base_url).join(path),
                params=params,
                json=json_body,
                data=form_data,
                content=content,
                headers=headers,
            )
            self.requests.append({
                "method": req.method,
                # raw_path is the request-line target: "/api/x?verbose=1".
                "target": req.url.raw_path.decode("ascii", "replace"),
                "headers": dict(headers or {}),
                "body": req.content.decode("utf-8", "replace"),
            })
            return HTTPResponse(status_code=0, headers={}, text="", ok=True)

        async def close(self) -> None:
            return None

    return _CaptureHTTPTransport()


@dataclass
class CommandCapture:
    """The transport a command was sent into, and which route it took."""

    route: str
    transport: Any
    udp: CaptureUdp | None = None


def capture_command_transport(driver: Any, cmd_def: dict[str, Any]) -> CommandCapture:
    """Attach the capture that matches the route ``cmd_def`` will take.

    The runtime picks a command's route from its declared fields, not from the
    driver's transport, so the capture follows the command too.
    """
    udp: CaptureUdp | None = None
    if "address" in cmd_def:
        route, capture = "osc", capture_osc_transport()
    elif "path" in cmd_def or "method" in cmd_def:
        route, capture = "http", capture_http_transport()
    elif isinstance(cmd_def.get("udp"), dict):
        # The side channel never touches driver.transport: the driver opens a
        # socket for the send. Hand it one that records instead.
        route, capture = "udp", CaptureTransport()
        udp = CaptureUdp()

        async def _open_capture() -> CaptureUdp:
            return udp

        driver._open_udp = _open_capture
    else:
        route, capture = "raw", CaptureTransport()
    driver.transport = capture
    return CommandCapture(route=route, transport=capture, udp=udp)


# ---------------------------------------------------------------------------
# What connecting sends
# ---------------------------------------------------------------------------


@dataclass
class ConnectPreview:
    """What connecting a declarative driver puts on the wire, in order.

    Each step is ``{"stage", "kind", ...}``: ``stage`` is ``sign_in``,
    ``start_up``, ``poll`` or ``keep_alive``; ``kind`` is ``wait`` (a prompt
    the sign-in waits for, ``{"pattern"}``), ``send`` (bytes, ``{"data"}``),
    or ``request`` (HTTP, ``{"method", "target", "headers", "body"}``).
    """

    available: bool
    reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    poll_interval: float = 0.0
    keep_alive_interval: float = 0.0


def _drain(capture: Any, stage: str, steps: list[dict[str, Any]], seen: dict[int, int]) -> None:
    """Move what ``capture`` recorded since the last drain into ``steps``."""
    key = id(capture)
    start = seen.get(key, 0)
    if hasattr(capture, "requests"):
        items = capture.requests[start:]
        seen[key] = len(capture.requests)
        for req in items:
            steps.append({"stage": stage, "kind": "request", **req})
        return
    frames = getattr(capture, "frames", [])
    items = frames[start:]
    seen[key] = len(frames)
    for data in items:
        steps.append({"stage": stage, "kind": "send", "data": data})


async def preview_connect(driver: Any) -> ConnectPreview:
    """Run a declarative driver's connection steps against a capture.

    ``driver`` is a fresh instance nobody else holds (it is consumed). Only
    YAML drivers (``ConfigurableDriver``) have declared steps; for any other
    the preview is unavailable, with the reason.
    """
    from openavc.drivers.configurable import ConfigurableDriver

    if not isinstance(driver, ConfigurableDriver):
        return ConnectPreview(
            available=False,
            reason="This driver's connection steps are written in code.",
        )
    definition = driver._definition
    transport_type = str(driver.config.get("transport") or definition.get("transport") or "tcp")
    if transport_type == "osc":
        capture: Any = capture_osc_transport()
    elif transport_type == "http":
        capture = capture_http_transport()
    else:
        capture = CaptureTransport()
    driver.transport = capture
    steps: list[dict[str, Any]] = []
    seen: dict[int, int] = {}

    # Sign-in: the real handshake, each prompt wait recorded and answered.
    auth_def = definition.get("auth")
    if isinstance(auth_def, dict) and driver._auth_should_run(auth_def):
        async def answered(target: Any, failure: Any, timeout: float, stage: str = "") -> None:
            _drain(capture, "sign_in", steps, seen)
            steps.append({"stage": "sign_in", "kind": "wait", "pattern": target.pattern})

        driver._auth_wait_for = answered
        driver._auth_buffer = bytearray()
        driver._auth_event = asyncio.Event()
        driver._auth_event.set()  # no post-password quiet period to sit out
        driver._auth_mode = True
        try:
            await driver._perform_auth_handshake()
        except Exception as exc:
            log.debug("Sign-in preview failed: %s", exc)
        _drain(capture, "sign_in", steps, seen)

    # Start-up: the declared roster, then on_connect exactly as connect runs it.
    try:
        driver._register_declared_children()
    except Exception:
        log.debug("Roster preview failed", exc_info=True)
    try:
        await driver._run_on_connect()
    except Exception as exc:
        log.debug("Start-up preview failed: %s", exc)
    _drain(capture, "start_up", steps, seen)

    # One round of status polling.
    poll_interval = float(driver.config.get("poll_interval", 0) or 0)
    if definition.get("polling"):
        try:
            await driver.poll()
        except Exception as exc:
            log.debug("Poll preview failed: %s", exc)
        _drain(capture, "poll", steps, seen)

    # The keep-alive probe, when the driver declares one.
    keep_alive = 0.0
    if driver._health_enabled():
        keep_alive = float(getattr(driver, "HEALTH_INTERVAL_S", 0) or 0)
        try:
            await driver._send_liveness_probe(driver._liveness_def)
        except Exception as exc:
            log.debug("Keep-alive preview failed: %s", exc)
        _drain(capture, "keep_alive", steps, seen)

    return ConnectPreview(
        available=True, steps=steps, poll_interval=poll_interval,
        keep_alive_interval=keep_alive,
    )
