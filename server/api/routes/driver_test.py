"""Driver command-test harness — the Driver Builder's live-test panel.

Two routes over a lab, not a CRUD surface: `POST
/driver-definitions/{id}/test-command` sends a command and reports what came
back, and `GET /driver-test-conflicts` warns when a production device already
holds the connection the test wants.

Three ways to run a command, in descending fidelity:

* **Live via the real runtime** (`definition` + `command_name`) — instantiates
  the actual `ConfigurableDriver`, runs auth and on_connect, sends, and
  reports the response, state changes, and driver-contract violations. What
  works here works when the driver is wired to a real device.
* **Dry run** (`dry_run`) — the same construction, but the transport records
  instead of transmitting. The reported wire is what the runtime handed the
  transport, so the preview cannot drift from the send the way a second
  implementation of the protocol build would. No socket, no device.
* **Raw probe** (`command_string`) — open a transport, send bytes, wait. No
  definition, no auth, no on_connect: "what does this device say if I send X".

Split out of `routes/drivers.py`: this is a third of that file behind two
routes, and it shares nothing with driver installation or definition CRUD.
"""

from typing import Any, NamedTuple

from fastapi import APIRouter, HTTPException

from server.api._engine import _get_engine, _rate_limit_test
from server.api.models import TestCommandRequest
from server.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter()


@router.post("/driver-definitions/{driver_id}/test-command")
async def test_driver_command(driver_id: str, body: TestCommandRequest) -> dict:
    """Test a driver command against live hardware.

    If the request includes a `definition` and `command_name`, the endpoint
    instantiates the actual `ConfigurableDriver` runtime — running auth and
    on_connect just like production — and sends the named command. This is
    the production code path: anything that works in the test panel will
    work when the driver is wired into a real device.

    Falls back to a raw send-and-wait when only `command_string` is given,
    for one-off "what does this device say if I send X" probes.

    With `dry_run`, the command is built by the same runtime but handed to a
    transport that records instead of transmitting — the caller learns what
    would go on the wire without a device, a socket, or a send.
    """
    if body.dry_run:
        if not (body.definition and body.command_name):
            raise HTTPException(
                status_code=422,
                detail="A dry run needs a definition and a command_name",
            )
        # Deliberately outside the rate limit below. That limit protects
        # devices from competing test sessions (many accept one control
        # connection at a time); a dry run touches no device, and callers
        # ask for one on every keystroke.
        return await _dry_run_command(body)

    _rate_limit_test(f"test_command:{driver_id}")

    if body.definition and body.command_name:
        return await _test_via_configurable_driver(body)

    return await _test_raw(body)


@router.get("/driver-test-conflicts")
async def check_connection_conflict(
    host: str,
    port: str,
    transport: str = "tcp",
) -> dict:
    """Find production devices using the same host:port as a planned test (A81).

    Many AV devices allow only one TCP control session at a time. The test
    panel calls this before opening a competing connection so the UI can
    warn the user and offer to pause the production driver.

    Returns ``{"conflicts": [...]}`` with one entry per matching device. An
    empty list means it's safe to test. Matching is currently TCP-only —
    the single-session problem is a TCP-specific failure mode.
    """
    if transport != "tcp":
        return {"conflicts": []}
    engine = _get_engine()
    if not engine.project:
        return {"conflicts": []}

    try:
        target_port = int(port)
    except (TypeError, ValueError):
        return {"conflicts": []}

    from server.core.device_manager import get_driver_registry

    registry = {d["id"]: d for d in get_driver_registry()}

    conflicts: list[dict[str, Any]] = []
    for device in engine.project.devices:
        if not device.enabled:
            continue
        driver_info = registry.get(device.driver)
        if not driver_info or driver_info.get("transport", "tcp") != "tcp":
            continue
        conn = engine.project.connections.get(device.id, {})
        cfg = {**device.config, **conn}
        device_host = str(cfg.get("host", ""))
        try:
            device_port = int(cfg.get("port", 0))
        except (TypeError, ValueError):
            continue
        if device_host != host or device_port != target_port:
            continue
        connected = bool(engine.state.get(f"device.{device.id}.connected"))
        paused = bool(engine.state.get(f"device.{device.id}.paused"))
        conflicts.append({
            "device_id": device.id,
            "device_name": device.name,
            "driver": device.driver,
            "connected": connected,
            "paused": paused,
        })
    return {"conflicts": conflicts}


class _TestDriverBuildError(Exception):
    """The supplied definition or connection settings can't produce a driver."""


class _TestDriver(NamedTuple):
    """A one-shot ConfigurableDriver instance and the config it was built with."""

    driver: Any
    config: dict[str, Any]
    state: Any
    definition: dict[str, Any]
    transport_type: str


def _build_test_driver(
    body: TestCommandRequest, port_fallback: int | None = None
) -> _TestDriver:
    """Instantiate a one-shot driver from a live-test request.

    Shared by the live test and the dry run so both exercise the same
    definition handling: definition defaults, then the caller's overrides
    (host, port, credentials), then `poll_interval` forced to 0 so a one-shot
    test never starts a background poller.

    ``port_fallback`` stands in for an unusable port instead of failing —
    the dry run wants a preview while the author is still filling the
    connection panel; the live test has nowhere to connect without one.

    Raises _TestDriverBuildError with a user-facing message.
    """
    from server.core.state_store import StateStore
    from server.core.event_bus import EventBus
    from server.drivers.configurable import create_configurable_driver_class

    definition = dict(body.definition or {})
    default_config = dict(definition.get("default_config") or {})
    # Serial drivers use `port` as a string path (e.g. "COM3"); IP transports
    # need an int. Coerce numeric strings for IP, leave serial strings alone.
    transport_type = definition.get("transport", body.transport)
    port_value: int | str = body.port
    if transport_type != "serial":
        try:
            port_value = int(body.port)
        except (TypeError, ValueError):
            if port_fallback is None:
                raise _TestDriverBuildError(
                    f"Invalid port for {transport_type}: {body.port!r}"
                ) from None
            port_value = port_fallback
    config = {
        **default_config,
        **(body.config_overrides or {}),
        "host": body.host,
        "port": port_value,
        "poll_interval": 0,
    }
    # Patch the definition so the runtime sees poll-off.
    definition["default_config"] = {**default_config, "poll_interval": 0}

    state = StateStore()
    events = EventBus()

    try:
        driver_cls = create_configurable_driver_class(definition)
        driver = driver_cls(
            device_id=f"test_{definition.get('id', 'driver')}",
            config=config,
            state=state,
            events=events,
        )
    except Exception as e:
        raise _TestDriverBuildError(f"Driver definition is invalid: {e}") from e

    return _TestDriver(driver, config, state, definition, transport_type)


async def _test_via_configurable_driver(body: TestCommandRequest) -> dict:
    """Run a command through the live ConfigurableDriver code path.

    Builds an isolated StateStore + EventBus, instantiates a one-shot driver
    from the supplied definition with `poll_interval` forced to 0, hooks
    on_data_received to capture incoming bytes, and reports the response,
    state changes, and any errors back to the caller.
    """
    import asyncio

    try:
        built = _build_test_driver(body)
    except _TestDriverBuildError as e:
        return {
            "success": False,
            "sent": None,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }

    from server.drivers.base import UndeclaredStateError

    driver = built.driver
    config = built.config
    state = built.state
    definition = built.definition
    transport_type = built.transport_type

    # Strict for this driver only — never the process-wide env var, which
    # would make every device this server is polling raise mid-poll while the
    # panel is open. The test driver is attached to nothing, so a raise here
    # costs a room nothing. See BaseDriver.strict_state.
    driver.strict_state = True
    contract_errors: list[str] = []

    def note_contract_error(e: UndeclaredStateError) -> None:
        """Record a violation once; the same rule fires on every poll."""
        if str(e) not in contract_errors:
            contract_errors.append(str(e))

    # Capture state changes so the panel can show what the command moved.
    initial_state: dict[str, Any] = (
        dict(state.snapshot()) if hasattr(state, "snapshot") else {}
    )

    # Hook on_data_received to capture inbound bytes for display while still
    # running the production response-matching logic so state changes happen.
    received_chunks: list[bytes] = []
    response_event = asyncio.Event()
    original_on_data = driver.on_data_received

    async def capture_on_data(data: bytes) -> None:
        received_chunks.append(data)
        response_event.set()
        # Caught here rather than left to propagate: for a byte-stream
        # transport this runs on the read-loop task, where an exception is
        # logged and dropped and would never reach the caller. Swallowing it
        # after recording also keeps the rest of the exchange observable —
        # the author sees what came back *and* what the driver did wrong.
        try:
            await original_on_data(data)
        except UndeclaredStateError as e:
            note_contract_error(e)

    driver.on_data_received = capture_on_data  # type: ignore[method-assign]

    sent_repr: str | None = None
    error_text: str | None = None

    try:
        try:
            await driver.connect()
        except UndeclaredStateError as e:
            # An on_connect query can write state too. Report it as the
            # contract violation it is, not as "Connect failed".
            note_contract_error(e)
        except Exception as e:
            return {
                "success": False,
                "sent": None,
                "received": [_decode_for_display(b) for b in received_chunks],
                "state_changes": {},
                "error": f"Connect failed: {e}",
                "contract_errors": contract_errors,
            }

        cmd_def = (definition.get("commands") or {}).get(body.command_name)
        if cmd_def is None:
            return {
                "success": False,
                "sent": None,
                "received": [_decode_for_display(b) for b in received_chunks],
                "state_changes": {},
                "error": f"Unknown command '{body.command_name}'",
                "contract_errors": contract_errors,
            }

        sent_repr = _describe_outgoing(definition, cmd_def, config, body.params or {})

        # send_command is fire-and-forget for TCP/OSC. For HTTP it returns
        # the response synchronously and on_data_received gets called with
        # the body, so capture_on_data fires the event in either case.
        try:
            send_result = await driver.send_command(
                body.command_name, body.params or {}
            )
        except UndeclaredStateError as e:
            # A command's `sets:` writes state on send. Same reasoning as the
            # connect branch: this is a contract fault, not a send failure.
            note_contract_error(e)
            send_result = None
        except Exception as e:
            error_text = f"Send failed: {e}"
            send_result = None

        # If nothing has come back yet, give the device a moment to reply.
        if not received_chunks and error_text is None:
            try:
                await asyncio.wait_for(response_event.wait(), timeout=body.timeout)
            except asyncio.TimeoutError:
                # No response is OK for fire-and-forget commands — surface
                # it as info, not an error, so users can tell the difference.
                if send_result is False or send_result is None:
                    error_text = "No response within timeout"

        # Settle period — many devices send a response in multiple frames
        # (NEC NaviSet per-parameter lines, Christie LX ACK then DATA). Without
        # this short wait the disconnect tears down the transport before
        # trailing frames arrive. HTTP already returns the full response
        # synchronously so we skip the settle there.
        if (
            received_chunks
            and error_text is None
            and transport_type in ("tcp", "udp", "osc", "serial")
        ):
            await asyncio.sleep(min(0.3, body.timeout / 4))

    finally:
        try:
            await driver.disconnect()
        except Exception:
            pass

    final_state = dict(state.snapshot()) if hasattr(state, "snapshot") else {}
    state_changes = {
        k: v
        for k, v in final_state.items()
        if initial_state.get(k) != v and not k.endswith(".connected")
    }

    return {
        # A contract violation fails the test even when the device answered
        # perfectly: the exchange worked, the driver's declarations did not.
        "success": error_text is None and not contract_errors,
        "sent": sent_repr,
        "received": [_decode_for_display(b) for b in received_chunks],
        "state_changes": state_changes,
        "error": error_text,
        "contract_errors": contract_errors,
    }


class _CaptureTransport:
    """Byte-stream stand-in (TCP, UDP, serial) that records instead of sending.

    Every ConfigurableDriver command route finishes at a transport call, so
    recording at that boundary yields the finished wire form — prefix/suffix
    framing, substituted placeholders, decoded escapes, computed send_frame
    header and all — without re-deriving any of it.
    """

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    @property
    def connected(self) -> bool:
        return True

    async def send(self, data: bytes) -> None:
        self.frames.append(bytes(data))

    async def close(self) -> None:
        return None


def _capture_osc_transport() -> Any:
    """OSC capture double.

    Subclasses OSCTransport rather than duck-typing it: the OSC sender
    refuses a transport that isn't one (and so does the OSC device-setting
    write), so a stand-in has to pass the isinstance check.
    """
    from server.transport.osc import OSCTransport

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


def _capture_http_transport() -> Any:
    """HTTP capture double — subclasses for the same reason as the OSC one.

    Records the request the driver built and hands back an empty response, so
    the send path completes normally without anything leaving the process.
    """
    from server.transport.http_client import HTTPClientTransport, HTTPResponse

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


async def _dry_run_command(body: TestCommandRequest) -> dict:
    """Build a command through the real driver and report what it would send.

    Uses the same driver construction as the live test, then attaches a
    transport that records instead of transmitting. Nothing is rendered here:
    the reported wire is what the runtime handed the transport, so a preview
    built from it cannot drift from the send the way a second implementation
    of the protocol build would.

    No connect, so no socket, no auth handshake and no on_connect — this is
    the command's wire form, not a connection test.
    """
    try:
        built = _build_test_driver(body, port_fallback=0)
    except _TestDriverBuildError as e:
        return {"dry_run": True, "success": False, "error": str(e), "route": None}

    driver = built.driver
    command = body.command_name or ""
    cmd_def = (built.definition.get("commands") or {}).get(command)
    if not isinstance(cmd_def, dict):
        return {
            "dry_run": True,
            "success": False,
            "error": f"Unknown command '{command}'",
            "route": None,
        }

    # Match the capture to the route the runtime will take, which it picks
    # from the command's declared fields — not from the driver's transport.
    # The panel reports a shape/transport mismatch on its own; here we follow
    # the command so the author still sees what the command would build.
    if "address" in cmd_def:
        route = "osc"
        capture = _capture_osc_transport()
    elif "path" in cmd_def or "method" in cmd_def:
        route = "http"
        capture = _capture_http_transport()
    else:
        route = "raw"
        capture = _CaptureTransport()
    driver.transport = capture

    try:
        await driver.send_command(command, body.params or {})
    except Exception as e:
        return {"dry_run": True, "success": False, "error": str(e), "route": route}

    result: dict[str, Any] = {
        "dry_run": True,
        "success": True,
        "error": None,
        "route": route,
        "wire": "",
        "wire_hex": None,
        "headers": None,
        "body": None,
    }

    if route == "http":
        # One command builds one request; a command that built none (no
        # method and no path yet) leaves the preview blank.
        if capture.requests:
            sent = capture.requests[0]
            result["wire"] = f"{sent['method']} {sent['target']}"
            result["headers"] = sent["headers"]
            result["body"] = sent["body"]
        return result

    if not capture.frames:
        return result

    data = b"".join(capture.frames)
    result["wire_hex"] = data.hex()
    if route == "osc":
        # Read the captured packet back with the codec's own decoder, so the
        # address and typed args shown are the ones actually encoded rather
        # than a second pass over the templates.
        from server.transport.osc_codec import osc_decode_message

        try:
            address, args = osc_decode_message(data)
        except Exception:
            result["wire"] = _decode_for_display(data)
            return result
        rendered = ", ".join(f"{tag}={value}" for tag, value in args)
        result["wire"] = f"{address} [{rendered}]" if args else address
        return result

    result["wire"] = _decode_for_display(data)
    return result


def _decode_for_display(data: bytes) -> str:
    """Best-effort decoding for the test panel UI.

    Tries UTF-8 first; falls back to a hex preview for binary protocols.
    """
    try:
        text = data.decode("utf-8")
        # If it round-trips and renders, show as text.
        if text.isprintable() or any(c in text for c in "\r\n\t"):
            return text
    except UnicodeDecodeError:
        pass
    return data.hex()


def _describe_outgoing(
    definition: dict[str, Any],
    cmd_def: dict[str, Any],
    config: dict[str, Any],
    params: dict[str, Any],
) -> str:
    """Build a human-readable summary of what was sent on the wire.

    Used by the test panel so authors can see which placeholders resolved to
    which values — the same string the runtime substitutes, not the raw
    template.
    """
    from server.drivers.configurable import ConfigurableDriver

    transport = definition.get("transport", "tcp")
    all_params = {**config, **params}

    if "address" in cmd_def:  # OSC
        addr = ConfigurableDriver._safe_substitute(
            cmd_def.get("address", ""), all_params
        )
        args = cmd_def.get("args") or []
        # Substitute arg values the same way _build_osc_args does, so the
        # summary shows what went on the wire, not the raw template.
        arg_summary = ", ".join(
            "{}={}".format(
                a.get("type", "s"),
                ConfigurableDriver._safe_substitute(str(a.get("value", "")), all_params),
            )
            for a in args
        )
        return f"OSC {addr}" + (f" [{arg_summary}]" if arg_summary else "")

    if transport == "http" or "method" in cmd_def or "path" in cmd_def:
        method = cmd_def.get("method", "GET").upper()
        path = ConfigurableDriver._safe_substitute(
            cmd_def.get("path", "/"), all_params
        )
        return f"{method} {path}"

    raw = cmd_def.get("send", "")
    return ConfigurableDriver._safe_substitute(raw, all_params) if raw else ""


async def _test_raw(body: TestCommandRequest) -> dict:
    """Legacy raw-bytes test path — open transport, send command_string, wait.

    No auth, no on_connect. Used when a user types a one-off probe in the
    raw command field without selecting a defined command.
    """
    import asyncio

    if not body.command_string:
        raise HTTPException(
            status_code=422,
            detail="Provide either a definition + command_name or a command_string",
        )

    if body.transport == "http":
        return await _test_http_raw(body)

    if body.transport == "osc":
        return await _test_osc_raw(body)

    if body.transport == "serial":
        return await _test_serial_raw(body)

    if body.transport == "udp":
        return await _test_udp_raw(body)

    if body.transport not in ("tcp",):
        raise HTTPException(
            status_code=422,
            detail="Only TCP, UDP, serial, HTTP, and OSC test connections are supported",
        )

    from server.transport.tcp import TCPTransport

    delimiter = body.delimiter.encode().decode("unicode_escape").encode()
    response_text = None
    error_text = None

    # Coerce numeric-string port to int for IP transports.
    try:
        port = int(body.port)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid TCP port: {body.port!r}",
        ) from None

    try:
        transport = await TCPTransport.create(
            host=body.host,
            port=port,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            delimiter=delimiter,
            timeout=body.timeout,
        )
    except ConnectionError as e:
        return {
            "success": False,
            "sent": body.command_string,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }

    try:
        cmd_data = body.command_string.encode().decode("unicode_escape").encode()
        response = await transport.send_and_wait(cmd_data, timeout=body.timeout)
        response_text = _decode_for_display(response)
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for response"
    except (OSError, ValueError, UnicodeError) as e:
        error_text = str(e)
    finally:
        await transport.close()

    return {
        "success": error_text is None,
        "sent": body.command_string,
        "received": [response_text] if response_text is not None else [],
        "state_changes": {},
        "error": error_text,
    }


async def _test_serial_raw(body: TestCommandRequest) -> dict:
    """Raw serial probe — open SerialTransport with the port path in body.port."""
    import asyncio
    from server.transport.serial_transport import SerialTransport

    port_path = str(body.port)
    if not port_path:
        raise HTTPException(
            status_code=422,
            detail="Serial port path is required (e.g. COM3 or /dev/ttyUSB0).",
        )

    delimiter = body.delimiter.encode().decode("unicode_escape").encode()
    response_text = None
    error_text = None

    try:
        transport = await SerialTransport.create(
            port=port_path,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            delimiter=delimiter,
            timeout=body.timeout,
        )
    except (OSError, ConnectionError) as e:
        return {
            "success": False,
            "sent": body.command_string,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }

    try:
        cmd_data = body.command_string.encode().decode("unicode_escape").encode()
        response = await transport.send_and_wait(cmd_data, timeout=body.timeout)
        response_text = _decode_for_display(response)
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for response"
    except (OSError, ValueError, UnicodeError) as e:
        error_text = str(e)
    finally:
        await transport.close()

    return {
        "success": error_text is None,
        "sent": body.command_string,
        "received": [response_text] if response_text is not None else [],
        "state_changes": {},
        "error": error_text,
    }


async def _test_udp_raw(body: TestCommandRequest) -> dict:
    """Raw UDP probe — open UDPTransport, send command_string bytes, await reply.

    The configurable driver runtime already supports UDP in definition mode; this
    surfaces the same shape to the raw probe path so authors can fire one-off
    bytes without first declaring a command.
    """
    import asyncio
    from server.transport.udp import UDPTransport

    try:
        port = int(body.port)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid UDP port: {body.port!r}",
        ) from None

    response_text = None
    error_text = None
    udp = UDPTransport(host=body.host, port=port, name="udp_test")

    try:
        await udp.open()
    except OSError as e:
        return {
            "success": False,
            "sent": body.command_string,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }

    try:
        cmd_data = body.command_string.encode().decode("unicode_escape").encode()
        response = await udp.send_and_wait(cmd_data, timeout=body.timeout)
        response_text = _decode_for_display(response)
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for response"
    except (OSError, ValueError, UnicodeError) as e:
        error_text = str(e)
    finally:
        await udp.close()

    return {
        "success": error_text is None,
        "sent": body.command_string,
        "received": [response_text] if response_text is not None else [],
        "state_changes": {},
        "error": error_text,
    }


async def _test_http_raw(body: TestCommandRequest) -> dict:
    """Raw HTTP probe — parses 'METHOD /path' out of command_string."""
    import httpx

    cmd = body.command_string.strip()
    method = "GET"
    path = cmd
    if " " in cmd:
        parts = cmd.split(None, 1)
        if parts[0].upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            method = parts[0].upper()
            path = parts[1]

    scheme = "https" if body.port == 443 else "http"
    url = f"{scheme}://{body.host}:{body.port}{path}"
    sent = f"{method} {url}"

    try:
        async with httpx.AsyncClient(
            timeout=body.timeout, verify=body.verify_ssl
        ) as client:
            resp = await client.request(method, url)
            return {
                "success": True,
                "sent": sent,
                "received": [f"HTTP {resp.status_code}\n{resp.text[:2000]}"],
                "state_changes": {},
                "error": None,
            }
    except httpx.TimeoutException:
        return {
            "success": False,
            "sent": sent,
            "received": [],
            "state_changes": {},
            "error": "HTTP request timed out",
        }
    except Exception as e:
        return {
            "success": False,
            "sent": sent,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }


async def _test_osc_raw(body: TestCommandRequest) -> dict:
    """Raw OSC probe — sends the address with no args."""
    import asyncio
    from server.transport.osc_codec import osc_encode_message, osc_decode_message
    from server.transport.udp import UDPTransport

    address = body.command_string.strip()
    if not address.startswith("/"):
        address = "/" + address

    response_text = None
    error_text = None
    sent = f"OSC {address}"

    udp = UDPTransport(host=body.host, port=body.port, name="osc_test")
    try:
        await udp.open()
        msg = osc_encode_message(address)
        response = await udp.send_and_wait(msg, timeout=body.timeout)
        try:
            resp_addr, resp_args = osc_decode_message(response)
            arg_strs = [str(v) for _, v in resp_args]
            response_text = f"{resp_addr} [{', '.join(arg_strs)}]"
        except (ValueError, Exception):
            response_text = response.hex()
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for OSC response"
    except (OSError, ValueError) as e:
        error_text = str(e)
    finally:
        await udp.close()

    return {
        "success": error_text is None,
        "sent": sent,
        "received": [response_text] if response_text is not None else [],
        "state_changes": {},
        "error": error_text,
    }
