"""Driver command-test harness — the Driver Builder's live-test panel.

Two routes over a lab, not a CRUD surface: `POST
/driver-definitions/{id}/test-command` sends a command and reports what came
back, and `GET /driver-test-conflicts` warns when a production device already
holds the connection the test wants.

Three ways to run a command, in descending fidelity:

* **Live via the real runtime** (`definition` + `command_name`) — instantiates
  the actual `ConfigurableDriver`, runs auth and on_connect, sends, and
  reports the response, state changes, driver-contract violations, the
  replies no response rule matched and the channels a reply named beyond the
  test's roster. What works here works when the driver is wired to a real
  device.
* **Dry run** (`dry_run`) — the same construction, but the transport records
  instead of transmitting. The reported wire is what the runtime handed the
  transport, so the preview cannot drift from the send the way a second
  implementation of the protocol build would. No socket, no device.
* **Raw probe** (`command_string`) — open a transport, send bytes, wait. No
  commands, no auth, no on_connect: "what does this device say if I send X".
  A serial port still opens with the line settings a connect would use, read
  from the draft's `default_config` and the panel's overrides when the request
  carries a `definition` (`transport_ladder.serial_line_settings`). The text
  is read (`_escaped`) before anything opens. The AI's test_driver_command is
  this probe over TCP (`raw_tcp_probe`).

Split out of `routes/drivers.py`: this is a third of that file behind two
routes, and it shares nothing with driver installation or definition CRUD.
"""

from typing import Any, NamedTuple

from openavc.api.host_policy import ProgrammerRoute
from fastapi import APIRouter, HTTPException

from openavc.api._engine import _get_engine, _rate_limit_test
from openavc.api.models import TestCommandRequest
from openavc.audit.observe import contract_sentence
from openavc.drivers.contract_events import (
    CHILD_UNPLACED,
    CHILD_UNREGISTERED,
    COERCION_FAILURE,
    RULE_FAILED,
    TYPE_MISMATCH,
    UNDECLARED_STATE,
    UNKNOWN_COMMAND,
    UNMATCHED_RESPONSE,
)
from openavc.drivers.dry_run import display_text
from openavc.drivers.reply_guard import rule_failure_sentence
from openavc.core.device_config import (
    device_connections,
    devices_at_host,
    same_serial_port,
)
from openavc.drivers.transport_choice import effective_transport
from openavc.transport.endpoint import HttpEndpoint, UnusablePort, http_endpoint
from openavc.utils.logger import get_logger
from openavc.utils.outbound import device_transport

log = get_logger(__name__)

router = APIRouter(route_class=ProgrammerRoute)


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
    port: str,
    host: str = "",
    transport: str = "tcp",
) -> dict:
    """Find production devices using the connection a planned test wants (A81).

    Many AV devices allow only one TCP control session at a time, and a
    serial port opens for one process only. The test panel calls this before
    opening a competing connection so the UI can warn the user and offer to
    pause the production driver.

    Devices are compared where they really connect (``device_connections``):
    the driver's default port, a transport override, a bridge's pass-through
    port and a USB-serial binding all count, and a running device is matched
    where its driver connects now, so a device a running simulation points at
    the simulator holds the simulator's port, not its own. ``tcp`` matches
    host and port, ``serial`` matches the port name; other transports hold no
    session and return nothing.

    Returns ``{"conflicts": [...]}`` with one entry per matching device. An
    empty list means it's safe to test.
    """
    transport = transport.lower()
    if transport not in ("tcp", "serial"):
        return {"conflicts": []}
    engine = _get_engine()
    if not engine.project:
        return {"conflicts": []}
    running = getattr(engine, "devices", None)

    if transport == "tcp":
        try:
            target_port: int | str = int(port)
        except (TypeError, ValueError):
            return {"conflicts": []}
        if not host.strip():
            return {"conflicts": []}
        matches = [
            conn for conn in devices_at_host(engine.project, [host], running)
            if conn.transport == "tcp" and conn.port == target_port
        ]
    else:
        if not port.strip():
            return {"conflicts": []}
        matches = [
            conn for conn in device_connections(engine.project, running)
            if conn.transport == "serial" and isinstance(conn.port, str)
            and same_serial_port(conn.port, port)
        ]

    return {"conflicts": [
        {
            "device_id": conn.device_id,
            "device_name": conn.name,
            "driver": conn.driver,
            "connected": bool(engine.state.get(f"device.{conn.device_id}.connected")),
            "paused": bool(engine.state.get(f"device.{conn.device_id}.paused")),
        }
        for conn in matches
    ]}


class _TestDriverBuildError(Exception):
    """The supplied definition or connection settings can't produce a driver."""


class _TestDriver(NamedTuple):
    """A one-shot ConfigurableDriver instance and the config it was built with."""

    driver: Any
    config: dict[str, Any]
    state: Any
    definition: dict[str, Any]
    transport_type: str


def _test_port(
    port: int | str, transport_type: str, fallback: int | str | None,
) -> int | str:
    """The port a test dials. Serial drivers use `port` as a string path
    (e.g. "COM3"); IP transports need an int, so a numeric string is
    coerced and anything else takes ``fallback`` ("" leaves it unset), or is
    refused without one."""
    if transport_type == "serial":
        return port
    try:
        return int(port)
    except (TypeError, ValueError):
        if fallback is None:
            raise _TestDriverBuildError(
                f"Invalid port for {transport_type}: {port!r}"
            ) from None
        return fallback


def _drafted_config(body: TestCommandRequest) -> dict[str, Any]:
    """The config a test reads before the panel's host and port: the draft's
    ``default_config``, then the panel's overrides. Every way of testing a
    draft reads it, so a raw probe and a defined command open the same line."""
    default_config = (body.definition or {}).get("default_config") or {}
    return {**default_config, **(body.config_overrides or {})}


def _build_test_driver(
    body: TestCommandRequest, port_fallback: int | str | None = None
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
    from openavc.core.state_store import StateStore
    from openavc.core.event_bus import EventBus
    from openavc.drivers.configurable import create_configurable_driver_class

    definition = dict(body.definition or {})
    default_config = dict(definition.get("default_config") or {})
    chosen = _drafted_config(body)
    # The transport the test driver connects over: the config's choice, else
    # the draft's, else tcp (transport_choice).
    transport_type = effective_transport(chosen, definition)
    config = {
        **chosen,
        "host": body.host,
        "port": _test_port(body.port, transport_type, port_fallback),
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


def _note_once(found: list[str], message: str) -> None:
    """Add a message once; the same rule fires on every poll."""
    if message not in found:
        found.append(message)


def _unmatched_text(detail: dict[str, Any]) -> str:
    """A reply no rule matched, as the panel lists it: its text, or an OSC
    message's address and arguments."""
    text = detail.get("text")
    if text is not None:
        return str(text)
    args = detail.get("args") or []
    address = detail.get("address", "")
    return f"{address} {args}" if args else str(address)


# What the live test does with each kind a contract observer is told
# (``drivers/contract_events.py``); every kind is in exactly one of these.
#
# A fault in the driver fails the test, though the device answered: the
# runtime carries on past each one, so the reply looks read and nothing else
# says so. A rule that failed reading the reply is skipped; a value that did
# not convert is stored as it came (or False); a value not of its declared
# type is stored all the same; a reply whose channel or zone id, or page
# start, could not be read writes nothing (``drivers/child_placement.py``);
# a command the driver does not have sends nothing.
FAILS_THE_TEST = (
    RULE_FAILED, COERCION_FAILURE, TYPE_MISMATCH, CHILD_UNPLACED, UNKNOWN_COMMAND,
)
# Listed beside the result without failing it. A reply no rule matched: the
# runtime drops it with a debug line, so without this the author sees the
# bytes arrive and nothing happen, and cannot tell a missing rule from a rule
# that did not fit. A reply naming a channel or zone beyond this test's
# roster: no fault of the driver (a device answers for every channel it has),
# but its status goes nowhere, which the author should see.
LISTED = (UNMATCHED_RESPONSE, CHILD_UNREGISTERED)
# Already reported by the raise the test driver's strict state checking makes
# on the same write (``BaseDriver.strict_state``), in that error's own words.
REPORTED_BY_ITS_RAISE = (UNDECLARED_STATE,)


class _Heard:
    """What the test driver said about its own contract while the test ran,
    as the result reports it: ``contract_errors`` fail the test, the
    ``unmatched`` replies and the ``unregistered`` channels are listed.
    Each event is worded as a device audit words it
    (``audit/observe.contract_sentence``), and said once however often the
    same rule fires."""

    def __init__(self) -> None:
        self.contract_errors: list[str] = []
        self.unmatched: list[str] = []
        self.unregistered: list[str] = []

    def note_contract_error(self, message: str) -> None:
        _note_once(self.contract_errors, message)

    def observe(self, kind: str, detail: dict[str, Any]) -> None:
        """The test driver's contract observer."""
        if kind == RULE_FAILED:
            self.note_contract_error(
                rule_failure_sentence(str(detail.get("rule")), str(detail.get("error")))
            )
        elif kind in FAILS_THE_TEST:
            self.note_contract_error(contract_sentence(kind, detail))
        elif kind == UNMATCHED_RESPONSE:
            self.unmatched.append(_unmatched_text(detail))
        elif kind == CHILD_UNREGISTERED:
            _note_once(self.unregistered, contract_sentence(kind, detail))

    def result_fields(self) -> dict[str, list[str]]:
        return {
            "contract_errors": self.contract_errors,
            "unmatched": self.unmatched,
            "unregistered": self.unregistered,
        }


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

    from openavc.drivers.base import UndeclaredStateError

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
    heard = _Heard()
    driver.contract_observer = heard.observe

    def note_contract_error(e: UndeclaredStateError) -> None:
        """Record a violation once; the same rule fires on every poll."""
        heard.note_contract_error(str(e))

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
                "received": [display_text(b) for b in received_chunks],
                "state_changes": {},
                "error": f"Connect failed: {e}",
                **heard.result_fields(),
            }

        cmd_def = (definition.get("commands") or {}).get(body.command_name)
        if cmd_def is None:
            return {
                "success": False,
                "sent": None,
                "received": [display_text(b) for b in received_chunks],
                "state_changes": {},
                "error": f"Unknown command '{body.command_name}'",
                **heard.result_fields(),
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

        # The command's read_back goes out on its own after the command; let
        # it, so what it read is in the reply and the state changes instead
        # of being cut off by the disconnect below.
        read_back_tasks = list(getattr(driver, "_read_back_tasks", ()))
        if read_back_tasks and error_text is None:
            await asyncio.wait(read_back_tasks, timeout=body.timeout)

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
        "success": error_text is None and not heard.contract_errors,
        "sent": sent_repr,
        "received": [display_text(b) for b in received_chunks],
        "state_changes": state_changes,
        "error": error_text,
        **heard.result_fields(),
    }


async def _dry_run_command(body: TestCommandRequest) -> dict:
    """Build a command through the real driver and report what it would send.

    Uses the same driver construction as the live test, then attaches a
    transport that records instead of transmitting. Nothing is rendered here:
    the reported wire is what the runtime handed the transport, so a preview
    built from it cannot drift from the send the way a second implementation
    of the protocol build would.

    No connect, so no socket, no auth handshake and no on_connect — this is
    the command's wire form, not a connection test. An HTTP request is built
    by the device's own HTTP client from the test's settings, so it carries
    the headers and sign-in header the device would get (masked). A port box
    still empty or holding a word is left unset, so each transport reads its
    own default, and an address an HTTP request cannot be sent to is left out
    of it (``preview_command``).
    """
    try:
        built = _build_test_driver(body, port_fallback="")
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

    # The capture follows the route the runtime will take, which it picks
    # from the command's declared fields — not from the driver's transport.
    # The panel reports a shape/transport mismatch on its own; here we follow
    # the command so the author still sees what the command would build.
    from openavc.drivers.dry_run import preview_command

    return await preview_command(driver, command, body.params or {}, cmd_def)


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
    from openavc.drivers.configurable import ConfigurableDriver
    from openavc.drivers.send_shape import osc_arg_tag, osc_arg_text

    transport = effective_transport(config, definition)
    all_params = {**config, **params}

    if "address" in cmd_def:  # OSC
        addr = ConfigurableDriver._safe_substitute(
            cmd_def.get("address", ""), all_params
        )
        args = cmd_def.get("args") or []
        # Each arg's type and value read as the encoder reads them
        # (send_shape), so the summary shows what went on the wire, not the
        # raw template.
        arg_summary = ", ".join(
            "{}={}".format(
                osc_arg_tag(a),
                ConfigurableDriver._safe_substitute(osc_arg_text(a), all_params),
            )
            for a in args
            if isinstance(a, dict)
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
    if not body.command_string:
        raise HTTPException(
            status_code=422,
            detail="Provide either a definition + command_name or a command_string",
        )
    if body.transport == "tcp":
        return await raw_tcp_probe(body)
    if body.transport == "serial":
        return await _test_serial_raw(body)
    if body.transport == "udp":
        return await _test_udp_raw(body)
    if body.transport == "http":
        return await _test_http_raw(body)
    if body.transport == "osc":
        return await _test_osc_raw(body)
    raise HTTPException(
        status_code=422,
        detail="Only TCP, UDP, serial, HTTP, and OSC test connections are supported",
    )


def _not_opened(body: TestCommandRequest, error: BaseException) -> dict:
    """A raw probe whose transport could not be opened."""
    return {
        "success": False,
        "sent": body.command_string,
        "received": [],
        "state_changes": {},
        "error": str(error),
    }


async def _exchange(transport: Any, body: TestCommandRequest, data: bytes) -> dict:
    """Send the probe's bytes on an opened transport, wait for the reply,
    close the transport and report what came back."""
    import asyncio

    response_text = None
    error_text = None
    try:
        response = await transport.send_and_wait(data, timeout=body.timeout)
        response_text = display_text(response)
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


def _raw_port(body: TestCommandRequest, name: str) -> int:
    """The probe's port as a number, or a 422 naming the transport."""
    try:
        return int(body.port)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {name} port: {body.port!r}",
        ) from None


def _escaped(value: str, what: str) -> bytes:
    """THE reading of a raw probe's text (``\\r``, ``\\x0D`` as the bytes they
    stand for). An incomplete escape is a 422 naming ``what``, raised before
    anything is opened: a one-connection device is never taken for nothing."""
    try:
        return value.encode().decode("unicode_escape").encode()
    except UnicodeError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"The {what} '{value}' has an incomplete backslash escape. Write a byte "
                "as \\x and two hex digits, such as \\x0D, and a backslash itself as \\\\."
            ),
        ) from None


async def raw_tcp_probe(body: TestCommandRequest) -> dict:
    """THE raw TCP probe, the Builder's and the AI's test_driver_command:
    TLS-wrapped when the driver's connection is (its ssl and verify_ssl, as
    the transport ladder builds a TCP connection)."""
    from openavc.transport.tcp import TCPTransport

    data = _escaped(body.command_string, "command")
    delimiter = _escaped(body.delimiter, "message delimiter")
    port = _raw_port(body, "TCP")
    try:
        transport = await TCPTransport.create(
            host=body.host,
            port=port,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            delimiter=delimiter,
            timeout=body.timeout,
            ssl=body.ssl,
            ssl_verify=body.verify_ssl,
        )
    except ConnectionError as e:
        return _not_opened(body, e)
    return await _exchange(transport, body, data)


async def _test_serial_raw(body: TestCommandRequest) -> dict:
    """Raw serial probe — open SerialTransport with the port path in body.port,
    at the line settings a connect of the draft opens it with (the draft's
    ``default_config`` and the panel's overrides, read by the transport
    ladder). A setting the connect refuses is refused here in its words,
    before the port is opened."""
    from openavc.drivers.transport_ladder import serial_line_settings
    from openavc.transport.serial_transport import SerialTransport

    port_path = str(body.port)
    if not port_path:
        raise HTTPException(
            status_code=422,
            detail="Serial port path is required (e.g. COM3 or /dev/ttyUSB0).",
        )

    data = _escaped(body.command_string, "command")
    delimiter = _escaped(body.delimiter, "message delimiter")
    try:
        transport = await SerialTransport.create(
            port=port_path,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            delimiter=delimiter,
            timeout=body.timeout,
            **serial_line_settings(_drafted_config(body)),
        )
    except (OSError, ConnectionError) as e:
        return _not_opened(body, e)
    return await _exchange(transport, body, data)


async def _test_udp_raw(body: TestCommandRequest) -> dict:
    """Raw UDP probe — open UDPTransport, send command_string bytes, await reply.

    The configurable driver runtime already supports UDP in definition mode; this
    surfaces the same shape to the raw probe path so authors can fire one-off
    bytes without first declaring a command.
    """
    from openavc.transport.udp import UDPTransport

    data = _escaped(body.command_string, "command")
    udp = UDPTransport(host=body.host, port=_raw_port(body, "UDP"), name="udp_test")
    try:
        await udp.open()
    except OSError as e:
        return _not_opened(body, e)
    return await _exchange(udp, body, data)


def _raw_http_endpoint(body: TestCommandRequest) -> HttpEndpoint:
    """Where a raw HTTP probe goes: where the driver's own connection does,
    from the request's host, port, ssl and verify_ssl (a host written as a
    URL names its own scheme, port and path)."""
    try:
        return http_endpoint(
            body.host,
            {"port": body.port, "ssl": body.ssl, "verify_ssl": body.verify_ssl},
        )
    except UnusablePort as unusable:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid HTTP port: {unusable.port!r}",
        ) from None


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

    endpoint = _raw_http_endpoint(body)
    url = endpoint.base_url + (path if path.startswith("/") else "/" + path)
    sent = f"{method} {url}"

    try:
        async with httpx.AsyncClient(
            timeout=body.timeout,
            # Directly to equipment at a local address, whatever the proxy
            # settings say (utils/outbound.py); the transport carries verify.
            transport=device_transport(url, verify=endpoint.verify),
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
    from openavc.transport.osc_codec import osc_encode_message, osc_decode_message
    from openavc.transport.udp import UDPTransport

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
