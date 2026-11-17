"""What a driver would send, built by the real runtime against transports that record.

Every command route in a declarative driver ends at a transport call, so a
transport that records instead of transmitting yields the finished wire form:
prefix and suffix framing, substituted placeholders, decoded escapes, the
``send_frame`` header. An HTTP request is built by the device's own HTTP
transport, made from its settings by the transport ladder with only the network
taken away (:func:`capture_http_transport`), so it carries the headers, body
encoding and sign-in header the device would get, masked as the device's
traffic view masks them. None of it is re-derived here, so a preview cannot
drift from the real send the way a second implementation of the protocol build
would. No socket, no device.

Two callers:

- **One command** (:func:`preview_command`): the Driver Builder's
  dry run, asked on every keystroke while an author edits a command.
- **What connecting sends** (:func:`preview_connect`): a device audit shows it
  before it connects, so the person knows what adding the device will put on
  the wire: the HTTP connect check (``HEAD /``, by the connect's own rule), the
  sign-in (the ``auth:`` handshake, its prompt waits answered at once), the
  start-up steps (``on_connect``), one round of status polling, and the
  keep-alive probe. For a Python driver these are code, and the preview says so
  rather than guessing.
"""

from __future__ import annotations

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


async def capture_http_transport(driver: Any) -> Any:
    """HTTP capture double: the device's own HTTP transport, built from
    ``driver``'s settings by the transport ladder and opened, with only the
    network taken away.

    The session, the headers every request carries, the sign-in and each
    request are the real code's, so a recorded request is what the device
    would get, Content-Type, default headers and sign-in header included.
    Each is recorded as the request leaves (after the sign-in is added),
    with its headers masked as the device's traffic view masks them (the
    sign-in header down to its scheme, the device's credentials as
    ``***``), and answered with an empty 200 so the send path completes.
    Nothing reaches the device's traffic view, and no request waits on the
    device's spacing. Settings a connection cannot be built from raise the
    connect's own refusal. Close it when done.
    """
    import httpx

    from openavc.core.device_traffic import TrafficRedactor, header_pairs
    from openavc.drivers.transport_ladder import http_settings
    from openavc.transport.http_client import HTTPClientTransport, request_target
    from openavc.utils.log_redaction import get_secret_registry

    masked = TrafficRedactor(get_secret_registry().secrets_for(driver.device_id))

    class _CaptureHTTPTransport(HTTPClientTransport):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.requests: list[dict[str, Any]] = []

        def _wire(self) -> httpx.AsyncBaseTransport:
            return httpx.MockTransport(self._capture)

        def _capture(self, request: httpx.Request) -> httpx.Response:
            self.requests.append({
                "method": request.method,
                "target": request_target(request),
                "headers": dict(masked.value(header_pairs(request.headers))),
                "body": request.content.decode("utf-8", "replace"),
            })
            return httpx.Response(200)

        async def close(self) -> None:
            await self._end_session()

    settings = await http_settings(driver)
    capture = _CaptureHTTPTransport(
        **{**settings, "name": None, "local_address": None, "inter_command_delay": 0},
    )
    capture._client = capture._new_client()
    return capture


@dataclass
class CommandCapture:
    """The transport a command was sent into, and which route it took."""

    route: str
    transport: Any
    udp: CaptureUdp | None = None


async def capture_command_transport(driver: Any, cmd_def: dict[str, Any]) -> CommandCapture:
    """Attach the capture that matches the route ``cmd_def`` will take.

    The runtime picks a command's route from its declared fields, not from the
    driver's transport, so the capture follows the command too. Close the
    capture when done (an HTTP one holds a session).
    """
    udp: CaptureUdp | None = None
    if "address" in cmd_def:
        route, capture = "osc", capture_osc_transport()
    elif "path" in cmd_def or "method" in cmd_def:
        route, capture = "http", await capture_http_transport(driver)
    elif isinstance(cmd_def.get("udp"), dict):
        # The side channel never touches driver.transport: the driver opens a
        # socket for the send. Hand it one that records instead.
        route, capture = "udp", CaptureTransport()
        udp = CaptureUdp()

        async def _open_capture(local_addr: str | None = None) -> CaptureUdp:
            return udp

        driver._open_udp = _open_capture
    else:
        route, capture = "raw", CaptureTransport()
    driver.transport = capture
    return CommandCapture(route=route, transport=capture, udp=udp)


def _command_answer(route: str | None, error: str | None = None) -> dict[str, Any]:
    """The dry run's answer before anything is filled in."""
    answer: dict[str, Any] = {"dry_run": True, "success": error is None, "error": error, "route": route}
    if error is None:
        answer.update(wire="", wire_hex=None, headers=None, body=None, read_back=[])
    return answer


async def preview_command(
    driver: Any, command: str, params: dict[str, Any], cmd_def: dict[str, Any],
) -> dict[str, Any]:
    """What sending ``command`` with ``params`` would put on the wire: the
    Driver Builder's dry run (``POST /driver-definitions/{id}/test-command``
    with ``dry_run``), in the answer that route sends.

    ``driver`` is a fresh instance nobody else holds (it is consumed). The
    command's own message comes first (``wire``, ``wire_hex``, and on HTTP
    ``headers`` and ``body``), then what its ``read_back`` sends after it.
    The preview is of the command, so it is shown whatever the address boxes
    hold: an HTTP request that cannot be addressed to the test's host and
    port (a host still being typed, a serial port name, a port out of range)
    is built with the address left out, as for an empty host box, and only
    settings no request can be built from at all are refused, with the
    connect's own sentence.
    """
    unaddressable = _unaddressable()
    try:
        captured = await _capture_for_preview(driver, cmd_def, unaddressable)
    except unaddressable as e:
        return _command_answer("http", str(e))
    try:
        return await _run_command(driver, command, params, captured)
    finally:
        await captured.transport.close()


def _unaddressable() -> tuple[type[Exception], ...]:
    """What building an HTTP request's session raises for settings it cannot
    use: the connect's refusal, and httpx's for an address that is no URL."""
    import httpx

    from openavc.core.connection_fault import ConnectionFaultError

    return (ConnectionFaultError, httpx.InvalidURL)


async def _capture_for_preview(
    driver: Any, cmd_def: dict[str, Any], unaddressable: tuple[type[Exception], ...],
) -> CommandCapture:
    """The command's capture; built again with the address left out when the
    test's host and port cannot address it."""
    try:
        return await capture_command_transport(driver, cmd_def)
    except unaddressable:
        driver.config["host"] = ""
        driver.config.pop("port", None)
    return await capture_command_transport(driver, cmd_def)


async def _send_into(driver: Any, command: str, params: dict[str, Any]) -> str | None:
    """Send the command into the capture; why it was refused, or None.

    A command with nothing to send yet (being written) previews blank:
    nothing is what it would send. The capture follows the command's form, so
    a form the connection cannot carry does not arise here.
    """
    from openavc.drivers.command_outcome import outcome_of

    try:
        await driver.send_command(command, params)
    except Exception as e:
        if outcome_of(e) != "not_sent":
            return str(e)
    return None


async def _run_command(
    driver: Any, command: str, params: dict[str, Any], captured: CommandCapture,
) -> dict[str, Any]:
    """Send ``command`` into ``captured`` and answer with what it recorded."""
    import asyncio

    # A preview records what is sent, not when: the read-back's spacing
    # from the command is not sat out.
    driver._pace_lines = False
    refused = await _send_into(driver, command, params)
    if refused is not None:
        return _command_answer(captured.route, refused)
    # What the command's read_back sends after it, recorded on the same
    # capture once the command's own message is in.
    capture = captured.transport
    own = len(getattr(capture, "requests", None) or getattr(capture, "frames", []))
    read_back_tasks = list(getattr(driver, "_read_back_tasks", ()))
    if read_back_tasks:
        await asyncio.gather(*read_back_tasks, return_exceptions=True)
    answer = _command_answer(captured.route)
    _FILLS.get(captured.route, _fill_frames)(answer, captured, own)
    return answer


def _fill_http(answer: dict[str, Any], captured: CommandCapture, own: int) -> None:
    """One command builds one request; a command that built none (no method
    and no path yet) leaves the preview blank."""
    requests = captured.transport.requests
    if requests:
        sent = requests[0]
        answer["wire"] = f"{sent['method']} {sent['target']}"
        answer["headers"] = sent["headers"]
        answer["body"] = sent["body"]
    answer["read_back"] = [
        {"wire": f"{sent['method']} {sent['target']}", "wire_hex": None, "body": sent["body"] or None}
        for sent in requests[own:]
    ]


def _fill_udp(answer: dict[str, Any], captured: CommandCapture, own: int) -> None:
    """One command, one datagram (a magic packet is sent twice, to the
    broadcast address and to the host; the bytes are the same)."""
    sent = captured.udp.sent if captured.udp is not None else []
    if not sent:
        return
    data = sent[0][0]
    answer["wire"] = data.decode("utf-8", errors="replace")
    answer["wire_hex"] = data.hex()
    # Where it goes, each once: a broadcast leaves through every connected
    # adapter, one datagram each, all to the same address.
    answer["udp_targets"] = list(dict.fromkeys(f"{h}:{p}" for _, h, p in sent))


def _fill_frames(answer: dict[str, Any], captured: CommandCapture, own: int) -> None:
    """A byte stream or OSC: the command's frames, then its read-back's."""
    frames = captured.transport.frames
    if not frames:
        return
    data = b"".join(frames[:own])
    answer["wire_hex"] = data.hex()
    answer["wire"] = _describe_frame(captured.route, data)
    answer["read_back"] = [
        {"wire": _describe_frame(captured.route, frame), "wire_hex": frame.hex()}
        for frame in frames[own:]
    ]


_FILLS = {"http": _fill_http, "udp": _fill_udp}


def _describe_frame(route: str, data: bytes) -> str:
    """One captured message as the preview shows it. An OSC packet is read
    back with the codec's own decoder, so the address and typed args shown
    are the ones actually encoded rather than a second pass over the
    templates."""
    if route == "osc":
        from openavc.transport.osc_codec import osc_decode_message

        try:
            address, args = osc_decode_message(data)
        except Exception:
            return display_text(data)
        rendered = ", ".join(f"{tag}={value}" for tag, value in args)
        return f"{address} [{rendered}]" if args else address
    return display_text(data)


def display_text(data: bytes) -> str:
    """Bytes as the Builder's test panel shows them: the text when it reads
    as text (line endings included), else hex, for a binary protocol."""
    try:
        text = data.decode("utf-8")
        # If it round-trips and renders, show as text.
        if text.isprintable() or any(c in text for c in "\r\n\t"):
            return text
    except UnicodeDecodeError:
        pass
    return data.hex()


# ---------------------------------------------------------------------------
# What connecting sends
# ---------------------------------------------------------------------------


@dataclass
class ConnectPreview:
    """What connecting a declarative driver puts on the wire, in order.

    Each step is ``{"stage", "kind", ...}``: ``stage`` is ``connect_check``
    (the HTTP ``HEAD /`` a connect sends to see the device answers),
    ``sign_in``, ``start_up``, ``poll`` or ``keep_alive``; ``kind`` is
    ``wait`` (a prompt the sign-in waits for, ``{"pattern"}``), ``send``
    (bytes, ``{"data"}``), or ``request`` (HTTP, ``{"method", "target",
    "headers", "body"}``).
    """

    available: bool
    reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    poll_interval: float = 0.0
    keep_alive_interval: float = 0.0


@dataclass
class _Recording:
    """The capture a preview runs against, and the steps drained from it."""

    capture: Any
    steps: list[dict[str, Any]] = field(default_factory=list)
    taken: int = 0

    def drain(self, stage: str) -> None:
        """Move what the capture recorded since the last drain into ``steps``."""
        if hasattr(self.capture, "requests"):
            items = self.capture.requests[self.taken:]
            self.taken = len(self.capture.requests)
            self.steps.extend({"stage": stage, "kind": "request", **req} for req in items)
            return
        frames = getattr(self.capture, "frames", [])
        items = frames[self.taken:]
        self.taken = len(frames)
        self.steps.extend({"stage": stage, "kind": "send", "data": data} for data in items)


async def _stand_in(driver: Any, transport_type: str) -> Any:
    """The capture a connect over ``transport_type`` is previewed against."""
    if transport_type == "osc":
        return capture_osc_transport()
    if transport_type == "http":
        return await capture_http_transport(driver)
    return CaptureTransport()


async def _preview_connect_check(driver: Any, rec: _Recording, transport_type: str) -> None:
    """The check a connect makes that the device answers, by the connect's
    own rule (skipped for ``verify_timeout: 0``). Only the HTTP capture is
    the device's own transport, so only its check is run."""
    if transport_type != "http":
        return
    try:
        await driver._connect_verify(driver._connect_attempt, transport_type)
    except Exception as exc:
        log.debug("Connect check preview failed: %s", exc)
    rec.drain("connect_check")


async def _preview_sign_in(driver: Any, rec: _Recording) -> None:
    """The real handshake, each prompt wait recorded and answered."""
    auth_def = driver._definition.get("auth")
    if not (isinstance(auth_def, dict) and driver._auth_should_run(auth_def)):
        return

    async def answered(target: Any, failure: Any, timeout: float, stage: str = "") -> None:
        rec.drain("sign_in")
        rec.steps.append({"stage": "sign_in", "kind": "wait", "pattern": target.pattern})

    driver._auth_wait_for = answered
    driver._arm_sign_in()
    driver._auth_event.set()  # no post-password quiet period to sit out
    try:
        await driver._perform_auth_handshake()
    except Exception as exc:
        log.debug("Sign-in preview failed: %s", exc)
    rec.drain("sign_in")


async def _preview_start_up(driver: Any, rec: _Recording) -> None:
    """The declared roster, then on_connect exactly as connect runs it."""
    try:
        driver._register_declared_children()
    except Exception:
        log.debug("Roster preview failed", exc_info=True)
    try:
        await driver._run_on_connect()
    except Exception as exc:
        log.debug("Start-up preview failed: %s", exc)
    rec.drain("start_up")


async def _preview_poll(driver: Any, rec: _Recording) -> None:
    """One round of status polling."""
    if not driver._definition.get("polling"):
        return
    try:
        await driver.poll()
    except Exception as exc:
        log.debug("Poll preview failed: %s", exc)
    rec.drain("poll")


async def _preview_keep_alive(driver: Any, rec: _Recording) -> float:
    """The keep-alive probe, when the driver declares one; its interval."""
    if not driver._health_enabled():
        return 0.0
    try:
        await driver._send_liveness_probe(driver._liveness_def)
    except Exception as exc:
        log.debug("Keep-alive preview failed: %s", exc)
    rec.drain("keep_alive")
    return float(getattr(driver, "HEALTH_INTERVAL_S", 0) or 0)


async def preview_connect(driver: Any) -> ConnectPreview:
    """Run a declarative driver's connection steps against a capture.

    ``driver`` is a fresh instance nobody else holds (it is consumed). Only
    YAML drivers (``ConfigurableDriver``) have declared steps; for any other
    the preview is unavailable, with the reason. Settings a connection
    cannot be built from raise the connect's own refusal.
    """
    from openavc.drivers.configurable import ConfigurableDriver

    if not isinstance(driver, ConfigurableDriver):
        return ConnectPreview(
            available=False,
            reason="OpenAVC cannot show what this driver sends before it connects.",
        )
    transport_type = driver._effective_transport()
    rec = _Recording(await _stand_in(driver, transport_type))
    driver.transport = rec.capture
    # A preview records what is sent, not when: no pauses between lines.
    driver._pace_lines = False
    try:
        await _preview_connect_check(driver, rec, transport_type)
        await _preview_sign_in(driver, rec)
        await _preview_start_up(driver, rec)
        await _preview_poll(driver, rec)
        keep_alive = await _preview_keep_alive(driver, rec)
    finally:
        await rec.capture.close()
    return ConnectPreview(
        available=True, steps=rec.steps,
        poll_interval=float(driver.config.get("poll_interval", 0) or 0),
        keep_alive_interval=keep_alive,
    )
