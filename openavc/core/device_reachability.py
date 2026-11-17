"""Can a project device be reached where it really connects, without its driver.

The device page's Test Connection (``POST /api/devices/{id}/test``) and the
AI's ``test_device_connection`` both ask here, so the two cannot disagree
about a device's address. The address is the one the device manager dials:
``core/device_config`` layers the driver's defaults, the device's config and
the project's connections table, then the bridge and USB rewrites, and
``transport/endpoint`` supplies a transport's fallback port and the
control-interface bind. A device a driver runs is tested where that driver
dials now (``DeviceManager.dialled_record``: the record it was handed, with
the simulation redirect on that driver laid over it); one nothing runs yet,
as the project sets it. Each probe opens what that transport opens in
``BaseDriver._create_transport`` (drivers/transport_ladder.py), without
speaking the protocol:

- tcp, ssh, mqtt, OSC over TCP: a TCP connection, closed at once.
- http: a HEAD request to the address the transport is built with
  (``transport/endpoint.http_endpoint``: host, port and ``ssl``, the
  certificate checked per ``verify_ssl``). A ``base_url`` in the config is
  used only when there is no host: the transport never reads it.
- serial: the port opened and closed, at the line settings the connect
  opens it with (``drivers/transport_ladder.serial_line_settings``), so a
  setting the connect refuses is refused here too, before anything is
  opened. A device bound to a USB adapter's serial number opens the port that
  adapter has now, and one whose adapter is not plugged in is refused without
  opening anything (``transport/serial_transport.require_usb_adapter``, the
  device manager's rule at every dial). A port typed as ``SIM:<name>``, or any
  port on a system without the serial package, is refused in the card's words
  (``transport/serial_transport.serial_port_refusal``, which the transport
  asks too).
- udp, snmp, OSC over UDP: there is nothing to connect to. The socket is
  pointed at the address, which proves the name resolves and a route exists,
  and the result says that is all it proves.
- bridge: an IR device emits through its bridge, so the bridge is tested as
  its own Test Connection would test it: where its driver dials now, answered
  from its connection when it is connected, the simulator named when it is
  simulated.

Whether a device goes through a bridge is what the resolver recorded of its
binding (``core/bridge_binding``). One that cannot be completed (no port
chosen, the bridge gone, its port unknown) is not tried: the result is the
sentence the device card shows for it.

Success means something accepted a connection at that address. It does not
mean the device there speaks the driver's protocol: whether it answers is the
device's ``connected`` and ``offline_reason`` state.

A test that would open a connection never opens one to a port a connected
project device's driver holds (``DeviceManager.network_connections``, looked
up by address through ``discovery/port_guard.held_ports``, the list a Discovery
scan leaves alone). Many displays and projectors serve one control connection
at a time and give the port to the newest, so a test of a working device would
take it from its own driver. A device that is connected is answered from that
connection (``live_connections``, which keeps a simulated device too): success,
no latency, ``in_use_by`` naming it and a ``note``. A port another project
device holds (the bridge an IR device sends through, say) is answered the same
way, naming that device; a device connected to the simulator holds nothing at
its own address, so that address is dialed. UDP and serial tests run as before:
pointing a UDP socket sends nothing, and the list holds no serial port.

A failure the operating system reports (host down, refused, no route) is
worded by ``core/connection_fault``, the same sentence the device card shows,
never the raw OS text. The ``error`` is the whole sentence a person reads:
one about the address names it once, and a device that sends through a
bridge also names the bridge, whose address is the one in the sentence.
While a device is simulated it is connected to the simulator, so
:func:`run_connection_test` tests the simulator and says so.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any, Mapping

import httpx

from openavc.core.bridge_binding import BRIDGE_MISSING, BridgeBinding, bridge_binding
from openavc.core.connection_fault import (
    ConnectionFaultError,
    no_address_message,
    no_port_message,
    no_serial_port_message,
    NO_RESPONSE,
    TRANSPORT_DISCONNECTED,
    UNREACHABLE,
    _endpoint,
    classify_connection_fault,
    link_local_fault,
)
from openavc.core.device_config import connection_of, resolve_device_config
from openavc.discovery.network_scanner import resolve_address
from openavc.discovery.port_guard import held_ports
from openavc.drivers.transport_ladder import serial_line_settings
from openavc.transport.endpoint import checks_certificate, control_bind_address, http_endpoint
from openavc.transport.serial_transport import require_usb_adapter, serial_port_refusal
from openavc.utils.logger import get_logger
from openavc.utils.outbound import device_transport

log = get_logger(__name__)

_CONNECT_TIMEOUT_S = 5.0
_SERIAL_TIMEOUT_S = 2

_NO_HOST = no_address_message()
_NO_PORT = no_port_message()
_NO_SERIAL_PORT = no_serial_port_message()
_DATAGRAM_NOTE = (
    "This device talks over UDP, which has no connection to open, so this "
    "only checks that the address resolves and has a route. Whether the "
    "device answers shows in its status."
)


async def run_connection_test(device: Any, engine: Any) -> dict[str, Any]:
    """Test one project device where it really connects: the device page's
    Test Connection and the AI's ``test_device_connection`` both ask here.

    While the device is simulated, the simulator is what it is connected to,
    so that is what is tested, and the result says so (``simulated`` and a
    ``note`` naming the equipment's own address).

    A device whose driver is connected now is answered from that connection
    and nothing is dialed; neither is a port another connected device holds.
    """
    return await _test_device(device, engine, subject="")


async def _test_device(device: Any, engine: Any, subject: str) -> dict[str, Any]:
    """:func:`run_connection_test` of ``device``. ``subject`` is "" for the
    device asked about, or the name of the bridge an IR device sends through,
    which the notes then name."""
    resolved = resolve_device_config(device, engine.project)
    dialled = _dialled(engine, device.id) or resolved
    binding = bridge_binding(dialled.get("config"))
    if binding.unresolved or binding.emits:
        return await _bridge_answer(binding, engine, connection_of(dialled).address())
    own = _own_connection(engine, device.id)
    if own is not None and _opens_connection(dialled):
        return _connected_now(own, resolved, subject)
    held, _unresolved = await held_ports(_connections(engine, "network_connections"))
    result = await check_reachability(dialled, held=held)
    if binding.passthrough:
        _name_the_bridge(result, _device_name(engine.project, binding.bridge))
    if connection_of(dialled).simulated:
        _say_simulated(result, connection_of(resolved), subject)
    return result


async def _bridge_answer(
    binding: BridgeBinding, engine: Any, address: dict[str, Any],
) -> dict[str, Any]:
    """The test of a device whose recorded binding decides it: one that cannot
    be completed is not tried, and an IR device's bridge is tested instead."""
    if binding.unresolved:
        label = _device_name(engine.project, binding.bridge)
        return _failure(binding.problem(label), address)
    return await _through_bridge(binding.bridge, engine, address)


def _name_the_bridge(result: dict[str, Any], bridge_label: str) -> None:
    """A failed test of a device on a bridge's pass-through names the bridge:
    the address in the sentence is the bridge's, never entered on the device."""
    if not result["success"]:
        result["error"] = _sent_through(result["error"], bridge_label)


def _say_simulated(result: dict[str, Any], equipment: Any, subject: str) -> None:
    """Mark a test that reached the simulator, naming the equipment's own
    address (``equipment``, the connection the project sets)."""
    where = _where(equipment)
    who = subject or "This device"
    result["simulated"] = True
    result["note"] = (
        f"{who} is simulated, so the test reached the simulator, not the "
        f"equipment at {where}." if where else
        f"{who} is simulated, so the test reached the simulator, not the equipment."
    )


def _device_name(project: Any, device_id: str) -> str:
    """A project device's name, else its id when the project has no such device."""
    found = next(
        (d for d in getattr(project, "devices", None) or [] if d.id == device_id), None,
    )
    return (found.name or found.id) if found is not None else device_id


def _dialled(engine: Any, device_id: str) -> dict[str, Any] | None:
    """The record a running device's driver dials now
    (``DeviceManager.dialled_record``), or None when nothing runs it, or the
    engine has no device manager to ask."""
    read = getattr(getattr(engine, "devices", None), "dialled_record", None)
    return read(device_id) if read is not None else None


def _own_connection(engine: Any, device_id: str) -> Any:
    """The device's own connection, when its driver is connected now
    (``live_connections``), else None."""
    live = _connections(engine, "live_connections")
    return next((c for c in live if c.device_id == device_id), None)


def _connections(engine: Any, which: str) -> list[Any]:
    """The device manager's ``live_connections`` (every connected project
    device, a simulated one too) or ``network_connections`` (those holding a
    port on the network), or nothing when the engine has no device manager
    to ask."""
    devices = getattr(engine, "devices", None)
    read = getattr(devices, which, None)
    if read is None:
        return []
    try:
        return list(read())
    except Exception:
        log.exception("Could not read the project's connected devices")
        return []


def _opens_connection(resolved: dict[str, Any]) -> bool:
    """Whether testing this device opens a connection to its address: tcp,
    ssh, mqtt, http and OSC over TCP do, UDP, SNMP, OSC over UDP and serial
    do not, and an IR device's bridge is tested as a device of its own."""
    conn = connection_of(resolved)
    if conn.transport in ("serial", "bridge", "udp", "snmp"):
        return False
    if conn.transport == "osc":
        cfg = resolved.get("config") or {}
        return str(cfg.get("transport_mode", "udp")).lower() == "tcp"
    return True


def _where(conn: Any) -> str:
    """``host:port``, or whichever of the two is known."""
    if conn.host and conn.port:
        return f"{conn.host}:{conn.port}"
    return conn.host or str(conn.port or "")


def _connected_now(own: Any, resolved: dict[str, Any], subject: str = "") -> dict[str, Any]:
    """The answer for a device whose driver is connected now (``own``, from
    ``live_connections``): the address it is connected to, and a note saying
    no second connection was opened. A simulated device is connected to the
    simulator, and the note says so, naming the address ``resolved`` sets.
    ``subject`` names the bridge when it is the one connected."""
    if not own.simulated:
        return {
            "success": True,
            "error": None,
            "latency_ms": None,
            "connection": own.address(),
            "in_use_by": own.name,
            "note": (
                f"{subject or 'The device'} is connected now. The test did not open "
                "a second connection to it."
            ),
        }
    where = _where(connection_of(resolved))
    equipment = f"the equipment at {where}" if where else "the equipment"
    return {
        "success": True,
        "error": None,
        "latency_ms": None,
        "connection": own.address(),
        "in_use_by": own.name,
        "simulated": True,
        "note": (
            f"{subject or 'This device'} is simulated and connected to the simulator "
            f"now, not to {equipment}. The test did not open a second connection to it."
        ),
    }


async def check_reachability(
    resolved: dict[str, Any],
    held: Mapping[tuple[str, int], Any] | None = None,
) -> dict[str, Any]:
    """Test one device's connection, at the address its record dials.

    ``resolved`` is the device as :func:`resolve_device_config` returns it, or
    as its driver dials it now. A bridge binding is :func:`run_connection_test`'s
    to read first: a serial pass-through is tried here at the bridge's
    address, as its record says. ``held`` is ``port_guard.held_ports``' map: a
    connection to one of those ports is not opened, and the result names the
    device holding it. Returns ``{success, error, latency_ms, connection}``,
    plus ``note`` when a success proves less than a connection would.
    ``connection`` is the address that was tried (``DeviceConnection.address``).
    """
    conn = connection_of(resolved)
    address = conn.address()
    cfg = resolved.get("config") or {}
    if held and conn.host and isinstance(conn.port, int) and _opens_connection(resolved):
        holder = held.get((await resolve_address(conn.host) or conn.host, conn.port))
        if holder is not None:
            return _held_by(holder, address)
    start = time.monotonic()
    try:
        if conn.transport == "serial":
            error = await _open_serial(cfg)
            note = None
        elif conn.transport in ("udp", "snmp") or (
            conn.transport == "osc"
            and str(cfg.get("transport_mode", "udp")).lower() != "tcp"
        ):
            error = await _point_datagram_socket(conn.host, conn.port, cfg)
            note = _DATAGRAM_NOTE
        elif conn.transport == "http":
            error = await _head(conn.host, conn.port, cfg)
            note = None
        else:
            error = await _open_tcp(conn.host, conn.port, cfg)
            note = None
    except Exception as exc:  # a probe reports, it never raises to the door
        error = _in_words(exc, conn.host, conn.port, conn.transport)
        note = None

    if error is not None:
        return _failure(error, address)
    result: dict[str, Any] = {
        "success": True,
        "error": None,
        "latency_ms": round((time.monotonic() - start) * 1000, 1),
        "connection": address,
    }
    if note:
        result["note"] = note
    return result


def _in_words(exc: BaseException, host: str, port: Any, transport: str) -> str:
    """The classifier's sentence for a failed probe, the one the device card
    shows. Its fallbacks describe a connection that dropped while in use,
    which a test never had, so those keep the error's own text, after the
    address it was dialing."""
    fault = classify_connection_fault(
        last_error=None, exc=exc, host=host, port=port, transport=transport,
    )
    # A test that failed with nothing more specific never connected (httpx's
    # connect timeout lands in the fallback), so all three can be the reason.
    if fault.code in (UNREACHABLE, NO_RESPONSE, TRANSPORT_DISCONNECTED):
        fault = link_local_fault(host, port) or fault
    if fault.code in (NO_RESPONSE, TRANSPORT_DISCONNECTED):
        return f"Could not connect to {_endpoint(host, port)}: {str(exc) or type(exc).__name__}"
    return fault.message


def _failure(error: str, address: dict[str, Any]) -> dict[str, Any]:
    return {"success": False, "error": error, "latency_ms": None, "connection": address}


def _held_by(holder: Any, address: dict[str, Any]) -> dict[str, Any]:
    """The answer for a port another connected project device holds."""
    return {
        "success": True,
        "error": None,
        "latency_ms": None,
        "connection": address,
        "in_use_by": holder.name,
        "note": (
            f"{holder.name} is connected to this address now. The test did not "
            "open a second connection to it."
        ),
    }


def _sent_through(error: str, bridge_label: str) -> str:
    """``error`` followed by the bridge a device sends through: the address in
    the sentence is then the bridge's."""
    if not error.rstrip().endswith((".", "!", "?")):
        error = error.rstrip() + "."
    return f"{error} This device sends through the bridge '{bridge_label}'."


async def _through_bridge(
    bridge_id: str, engine: Any, address: dict[str, Any],
) -> dict[str, Any]:
    """An IR device has no connection of its own: test the bridge it emits
    through as the bridge's own Test Connection would (where its driver dials
    now, from its connection when it is connected), and report the bridge's
    address marked with its id."""
    bridge = next(
        (d for d in getattr(engine.project, "devices", None) or [] if d.id == bridge_id), None,
    )
    if bridge is None:
        # The record the driver runs on names a bridge the project has lost.
        return _failure(BridgeBinding(bridge=bridge_id, route=BRIDGE_MISSING).problem(), address)
    bridge_record = _dialled(engine, bridge.id) or resolve_device_config(bridge, engine.project)
    if connection_of(bridge_record).transport == "bridge":
        return _failure(
            f"The bridge '{bridge.id}' is itself set to send through a bridge.", address
        )
    label = bridge.name or bridge.id
    result = await _test_device(bridge, engine, subject=label)
    result["connection"] = {**result["connection"], "bridge": bridge.id}
    _name_the_bridge(result, label)
    return result


def _port_problem(port: Any, cfg: dict[str, Any]) -> str | None:
    """Why ``port`` (as ``connection_of`` read it) cannot be dialed, or None."""
    if port is None:
        raw = cfg.get("port")
        if raw is not None and raw != "":
            return (
                f"The port '{raw}' is not a number. Enter the device's port "
                "in its Connection Settings."
            )
        return _NO_PORT
    if not 0 < int(port) <= 65535:
        return f"Port {port} is out of range. Ports run from 1 to 65535."
    return None


def _bind_for(host: str) -> tuple[str | None, str | None]:
    """Where a test connection leaves from, or why it cannot leave at all
    (the adapter chosen in Reach devices through has no address)."""
    try:
        return control_bind_address(host), None
    except ConnectionFaultError as exc:
        return None, str(exc)


async def _open_tcp(host: str, port: Any, cfg: dict[str, Any]) -> str | None:
    if not host:
        return _NO_HOST
    problem = _port_problem(port, cfg)
    if problem:
        return problem
    bind, refused = _bind_for(host)
    if refused:
        return refused
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, int(port), local_addr=(bind, 0) if bind else None
            ),
            timeout=_CONNECT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        missing = link_local_fault(host, port)
        if missing is not None:
            return missing.message
        return (
            f"No answer from {_endpoint(host, port)} within {_CONNECT_TIMEOUT_S:g} s. "
            "Check the IP address and network."
        )
    except OSError as exc:
        return _in_words(exc, host, port, "tcp")
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return None


async def _point_datagram_socket(host: str, port: Any, cfg: dict[str, Any]) -> str | None:
    if not host:
        return _NO_HOST
    problem = _port_problem(port, cfg)
    if problem:
        return problem
    bind, refused = _bind_for(host)
    if refused:
        return refused
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, int(port), type=socket.SOCK_DGRAM
        )
        family, kind, proto, _name, sockaddr = infos[0]
        with socket.socket(family, kind, proto) as sock:
            if bind:
                sock.bind((bind, 0))
            sock.connect(sockaddr)
    except OSError as exc:
        return _in_words(exc, host, port, "udp")
    return None


def _head_target(host: str, port: Any, cfg: dict[str, Any]) -> tuple[str, bool] | str:
    """The URL an http test sends its HEAD to and whether the certificate
    is checked, or the sentence saying why there is nothing to try."""
    if not host:
        url = str(cfg.get("base_url") or cfg.get("url") or "")
        return (url, checks_certificate(cfg)) if url else _NO_HOST
    problem = _port_problem(port, cfg)
    if problem:
        return problem
    # The address the HTTP transport is built with, from the same config.
    endpoint = http_endpoint(host, cfg)
    return endpoint.base_url, endpoint.verify


async def _head(host: str, port: Any, cfg: dict[str, Any]) -> str | None:
    target = _head_target(host, port, cfg)
    if isinstance(target, str):
        return target
    url, verify = target
    kwargs: dict[str, Any] = {"timeout": _CONNECT_TIMEOUT_S, "verify": verify}
    bind, refused = _bind_for(host)
    if refused:
        return refused
    if bind:
        # httpx ignores the client's `verify` once it is handed a transport.
        kwargs["transport"] = httpx.AsyncHTTPTransport(local_address=bind, verify=verify)
    else:
        # The way the device's own connection goes: directly to a local
        # address, through the environment's proxy to anything else.
        kwargs["transport"] = device_transport(url, verify=verify)
    try:
        async with httpx.AsyncClient(**kwargs) as client:
            await client.head(url)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return _in_words(exc, host, port, "http")
    return None


async def _open_serial(cfg: dict[str, Any]) -> str | None:
    """Open the port and close it, at the line settings the connect opens it
    with (``transport_ladder.serial_line_settings``): a setting the connect
    refuses is refused here in its words, before anything is opened."""
    try:
        cfg = require_usb_adapter(cfg, "serial")
    except ConnectionFaultError as refused:
        return str(refused)
    serial_port = str(cfg.get("port") or "")
    if not serial_port:
        return _NO_SERIAL_PORT
    refused = serial_port_refusal(serial_port)
    if refused is not None:
        return str(refused)
    try:
        line = serial_line_settings(cfg)
    except ConnectionFaultError as refused:
        return str(refused)

    # pyserial's open is a blocking syscall: a locked port or a slow
    # USB-serial adapter would stall the event loop for its whole duration.
    def _probe() -> None:
        import serial

        serial.Serial(serial_port, timeout=_SERIAL_TIMEOUT_S, **line).close()

    try:
        await asyncio.to_thread(_probe)
    except (OSError, ValueError) as exc:
        return _in_words(exc, "", serial_port, "serial")
    return None
