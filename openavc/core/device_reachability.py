"""Can a project device be reached where it really connects, without its driver.

The device page's Test Connection (``POST /api/devices/{id}/test``) and the
AI's ``test_device_connection`` both ask here, so the two cannot disagree
about a device's address. The address is the one the device manager dials:
``core/device_config`` layers the driver's defaults, the device's config and
the project's connections table, then the bridge and USB rewrites, and
``transport/endpoint`` supplies a transport's fallback port and the
control-interface bind. Each probe opens what that transport opens in
``BaseDriver._create_transport``, without speaking the protocol:

- tcp, ssh, mqtt, OSC over TCP: a TCP connection, closed at once.
- http: a HEAD request to the URL the transport builds from host, port and
  ``ssl``, with the certificate checked per ``verify_ssl``. A ``base_url`` in
  the config is used only when there is no host: the transport never reads it.
- serial: the port opened and closed.
- udp, snmp, OSC over UDP: there is nothing to connect to. The socket is
  pointed at the address, which proves the name resolves and a route exists,
  and the result says that is all it proves.
- bridge: an IR device emits through its bridge, so the bridge is tested.

Success means something accepted a connection at that address. It does not
mean the device there speaks the driver's protocol: whether it answers is the
device's ``connected`` and ``offline_reason`` state.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

import httpx

from openavc.core.device_config import connection_of, resolve_device_config
from openavc.transport.endpoint import control_bind_address

_CONNECT_TIMEOUT_S = 5.0
_SERIAL_TIMEOUT_S = 2

_NO_HOST = (
    "No address is set for this device. Enter its IP address or hostname "
    "in the device's Connection Settings."
)
_NO_PORT = (
    "No port is set for this device, and its driver does not supply one. "
    "Enter the port in the device's Connection Settings."
)
_NO_SERIAL_PORT = (
    "No serial port is set for this device. Choose one in the device's "
    "Connection Settings."
)
_DATAGRAM_NOTE = (
    "This device talks over UDP, which has no connection to open, so this "
    "only checks that the address resolves and has a route. Whether the "
    "device answers shows in its status."
)


async def check_reachability(resolved: dict[str, Any], project: Any) -> dict[str, Any]:
    """Test one device's connection.

    ``resolved`` is the device as :func:`resolve_device_config` returns it;
    ``project`` is only read to find the bridge an IR device emits through.
    Returns ``{success, error, latency_ms, connection}``, plus ``note`` when a
    success proves less than a connection would. ``connection`` is the
    address that was tried (``DeviceConnection.address``).
    """
    conn = connection_of(resolved)
    address = conn.address()
    if conn.bridge:
        bridge = next(
            (d for d in getattr(project, "devices", None) or [] if d.id == conn.bridge), None
        )
        if bridge is None:
            # The binding could not be resolved, so there is no address to try.
            return _failure(
                f"This device sends through the bridge '{conn.bridge}', which is "
                "not in the project.",
                address,
            )
        if conn.transport == "bridge":
            return await _through_bridge(bridge, project, address)

    cfg = resolved.get("config") or {}
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
        error = str(exc) or type(exc).__name__
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


def _failure(error: str, address: dict[str, Any]) -> dict[str, Any]:
    return {"success": False, "error": error, "latency_ms": None, "connection": address}


async def _through_bridge(bridge: Any, project: Any, address: dict[str, Any]) -> dict[str, Any]:
    """An IR device has no connection of its own: test the bridge it emits
    through, and report the bridge's address marked with its id."""
    resolved = resolve_device_config(bridge, project)
    if connection_of(resolved).transport == "bridge":
        return _failure(
            f"The bridge '{bridge.id}' is itself set to send through a bridge.", address
        )
    result = await check_reachability(resolved, project)
    result["connection"] = {**result["connection"], "bridge": bridge.id}
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


async def _open_tcp(host: str, port: Any, cfg: dict[str, Any]) -> str | None:
    if not host:
        return _NO_HOST
    problem = _port_problem(port, cfg)
    if problem:
        return problem
    bind = control_bind_address(host)
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, int(port), local_addr=(bind, 0) if bind else None
            ),
            timeout=_CONNECT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return f"Connection timed out ({_CONNECT_TIMEOUT_S:g}s)"
    except OSError as exc:
        return str(exc)
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
    bind = control_bind_address(host)
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
        return str(exc)
    return None


async def _head(host: str, port: Any, cfg: dict[str, Any]) -> str | None:
    scheme = "https" if cfg.get("ssl", False) else "http"
    if host:
        url = f"{scheme}://{host}:{port}"
    else:
        url = str(cfg.get("base_url") or cfg.get("url") or "")
        if not url:
            return _NO_HOST
    verify = bool(cfg.get("verify_ssl", True))
    kwargs: dict[str, Any] = {"timeout": _CONNECT_TIMEOUT_S, "verify": verify}
    bind = control_bind_address(host)
    if bind:
        # httpx ignores the client's `verify` once it is handed a transport.
        kwargs["transport"] = httpx.AsyncHTTPTransport(local_address=bind, verify=verify)
    try:
        async with httpx.AsyncClient(**kwargs) as client:
            await client.head(url)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return str(exc) or type(exc).__name__
    return None


async def _open_serial(cfg: dict[str, Any]) -> str | None:
    # pyserial's open is a blocking syscall: a locked port or a slow
    # USB-serial adapter would stall the event loop for its whole duration.
    serial_port = str(cfg.get("port") or "")
    if not serial_port:
        return _NO_SERIAL_PORT
    baud = cfg.get("baudrate", 9600)

    def _probe() -> None:
        import serial

        serial.Serial(serial_port, baud, timeout=_SERIAL_TIMEOUT_S).close()

    try:
        await asyncio.to_thread(_probe)
    except (OSError, ValueError) as exc:
        return str(exc)
    return None
