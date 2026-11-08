"""Building a device's platform transport from its config.

``connect()`` resolves the transport a device talks over ("tcp", "serial",
"udp", "osc", "http", "ssh", "mqtt" or "snmp") and ``_create_transport``
builds ``self.transport`` for it, reading the settings that transport takes
from the device's config. One builder per transport, below.

The rules worth knowing before changing anything here:

* Every builder hands its keyword arguments to the driver's
  ``_transport_kwargs(transport_type, kwargs)`` hook just before the
  transport is built, so a driver can adjust them (drop the delimiter for a
  raw byte stream, raise a timeout) and keep the rest. The ``open()`` call
  that udp, osc, snmp and http make after construction is not part of the
  hook: a driver that needs it changed overrides ``_create_transport``.
* The framing hooks and ``local_address()`` are asked first, for every
  transport and in that order, so an adapter with no address fails the
  connect before anything is built.
* Each transport class, and ``transport/endpoint.py``, is imported when a
  connection is built, not when this module loads: a test that replaces one
  of those module attributes reaches the connect.
* The settings each transport reads, their defaults, their coercions and
  which wins when two are set are pinned key by key in
  tests/test_transport_construction.py.

``TransportLadder`` is a mixin that ``BaseDriver`` inherits, so every name
stays on the driver object: drivers override ``_create_transport``,
``_transport_kwargs``, ``_create_frame_parser`` and ``_resolve_delimiter``,
and call ``local_address()``. The builders are module functions rather than
methods, so they put no new names on the driver for a driver's own method
to shadow. From its host this needs ``device_id``, ``config``,
``DRIVER_INFO``, ``on_data_received()``, ``on_mqtt_message()`` and
``_handle_transport_disconnect()`` (BaseDriver).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from openavc.transport.frame_parsers import FrameParser
from openavc.utils.logger import get_logger

# These lines have always logged under the driver runtime's name, which the
# IDE's log view shows as their source.
log = get_logger("openavc.drivers.base")


def _ssh_option_list(value: object) -> list[str]:
    """Normalise a device's ``extra_ssh_options`` into ssh ``-o`` arguments.

    A driver can declare a list; the SSH options field every SSH device gets
    (drivers/registry.py) is text, one option per line. Both reach here: a
    string splits on line breaks only, and blanks drop out. Never on commas,
    which separate the entries of an algorithm list inside ONE option
    (``Ciphers=+aes128-cbc,3des-cbc``). Nothing is validated beyond that --
    an option ssh will not take is ssh's to report (connection_fault.py
    reads its words), and an allowlist of our own would be a second thing
    to keep current.
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [line.strip() for line in str(value).splitlines() if line.strip()]


# --- Serial line settings -----------------------------------------------------

_BYTESIZES = (5, 6, 7, 8)
_PARITIES = ("N", "E", "O", "M", "S")
# pyserial wants ints for 1/2 and the float 1.5 for one-and-a-half.
_STOPBITS: dict[float, int | float] = {1.0: 1, 2.0: 2, 1.5: 1.5}
# The UI offers none / hardware (RTS/CTS); the software (XON/XOFF) spelling
# is accepted too.
_HARDWARE_FLOW = ("hardware", "rtscts", "rts/cts")
_SOFTWARE_FLOW = ("software", "xonxoff", "xon/xoff")


def _serial_int(config: dict[str, Any], key: str, default: int, expected: str) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {key} {config.get(key)!r} ({expected})")


def _serial_bytesize(config: dict[str, Any]) -> int:
    bytesize = _serial_int(config, "bytesize", 8, "must be 5, 6, 7, or 8")
    if bytesize not in _BYTESIZES:
        raise ValueError(f"Invalid bytesize {bytesize} (must be 5, 6, 7, or 8)")
    return bytesize


def _serial_parity(config: dict[str, Any]) -> str:
    parity = str(config.get("parity", "N")).upper()
    if parity not in _PARITIES:
        raise ValueError(
            f"Invalid parity {config.get('parity')!r} (must be one of N, E, O, M, S)"
        )
    return parity


def _serial_stopbits(config: dict[str, Any]) -> int | float:
    raw = config.get("stopbits", 1)
    try:
        as_float = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid stopbits {raw!r} (must be 1, 1.5, or 2)")
    if as_float not in _STOPBITS:
        raise ValueError(f"Invalid stopbits {raw!r} (must be 1, 1.5, or 2)")
    return _STOPBITS[as_float]


def _serial_flow(config: dict[str, Any]) -> tuple[bool, bool]:
    """``(rtscts, xonxoff)``. An unrecognised value is no flow control
    rather than a refusal: a stray value should not make the device
    unconnectable."""
    flow = str(config.get("flow_control", "none")).strip().lower()
    return flow in _HARDWARE_FLOW, flow in _SOFTWARE_FLOW


def _set_or_none(config: dict[str, Any], key: str) -> Any:
    """A setting's value, or None when it is unset or blank."""
    return config.get(key) or None


# --- HTTP ---------------------------------------------------------------------


def _http_credentials(config: dict[str, Any], auth_type: str) -> dict[str, Any]:
    """The credentials an HTTP device's ``auth_type`` signs in with; none for
    a type that takes none (or one this does not know)."""
    if auth_type in ("basic", "digest"):
        return {"username": config.get("username", ""), "password": config.get("password", "")}
    if auth_type == "bearer":
        return {"token": config.get("token", "")}
    if auth_type == "api_key":
        return {
            "header": config.get("api_key_header", "X-API-Key"),
            "key": config.get("api_key", ""),
        }
    return {}


# --- The builders -------------------------------------------------------------


@dataclass(frozen=True)
class _Asked:
    """What ``_create_transport`` asks the driver before any builder runs: its
    framing hooks' answers and the local address its connection leaves from
    (None to let the operating system choose)."""

    frame_parser: FrameParser | None
    delimiter: bytes | None
    control_ip: str | None


async def _tcp(driver: TransportLadder, asked: _Asked) -> None:
    from openavc.transport.tcp import TCPTransport

    config = driver.config
    # TLS-wrapped TCP with `ssl: true`, in the same vocabulary as the http
    # branch (`ssl` to enable, `verify_ssl` for cert checking). The defaults
    # match TCPTransport.create(), so a plain-TCP device that sets none of
    # these is unaffected.
    kwargs = dict(
        host=config.get("host", ""),
        port=driver._required_port(),
        on_data=driver.on_data_received,
        on_disconnect=driver._handle_transport_disconnect,
        delimiter=asked.delimiter,
        frame_parser=asked.frame_parser,
        inter_command_delay=config.get("inter_command_delay", 0.0),
        name=driver.device_id,
        local_addr=(asked.control_ip, 0) if asked.control_ip else None,
        timeout=config.get("timeout", 5.0),
        ssl=config.get("ssl", False),
        ssl_verify=config.get("verify_ssl", True),
        keepalive=bool(config.get("tcp_keepalive", False)),
    )
    driver.transport = await TCPTransport.create(**driver._transport_kwargs("tcp", kwargs))


async def _serial(driver: TransportLadder, asked: _Asked) -> None:
    from openavc.transport.serial_transport import SerialTransport

    config = driver.config
    baudrate, bytesize, parity, stopbits, rtscts, xonxoff = (
        driver._coerce_serial_params(config)
    )
    kwargs = dict(
        port=config.get("port", ""),
        baudrate=baudrate,
        on_data=driver.on_data_received,
        on_disconnect=driver._handle_transport_disconnect,
        delimiter=asked.delimiter,
        frame_parser=asked.frame_parser,
        inter_command_delay=config.get("inter_command_delay", 0.0),
        bytesize=bytesize,
        parity=parity,
        stopbits=stopbits,
        rtscts=rtscts,
        xonxoff=xonxoff,
        name=driver.device_id,
    )
    driver.transport = await SerialTransport.create(**driver._transport_kwargs("serial", kwargs))


async def _udp(driver: TransportLadder, asked: _Asked) -> None:
    from openavc.transport.udp import UDPTransport

    config = driver.config
    kwargs = dict(
        host=config.get("host", ""),
        port=driver._required_port(),
        on_data=driver.on_data_received,
        on_disconnect=driver._handle_transport_disconnect,
        inter_command_delay=config.get("inter_command_delay", 0.0),
        name=driver.device_id,
    )
    driver.transport = UDPTransport(**driver._transport_kwargs("udp", kwargs))
    await driver.transport.open(local_addr=asked.control_ip)


async def _osc(driver: TransportLadder, asked: _Asked) -> None:
    from openavc.transport.osc import OSCTransport

    config = driver.config
    host = config.get("host", "")
    port = driver._required_port()
    # OSC over TCP+SLIP when the device opts in (a large reply that must
    # arrive whole); UDP otherwise.
    osc_tcp = str(config.get("transport_mode", "udp")).lower() == "tcp"
    kwargs = dict(
        host=host,
        port=port,
        listen_port=config.get("listen_port", 0),
        on_data=driver.on_data_received,
        on_disconnect=driver._handle_transport_disconnect,
        inter_command_delay=config.get("inter_command_delay", 0.0),
        name=driver.device_id,
        tcp=osc_tcp,
    )
    driver.transport = OSCTransport(**driver._transport_kwargs("osc", kwargs))
    await driver.transport.open(local_addr=asked.control_ip)


async def _http(driver: TransportLadder, asked: _Asked) -> None:
    from openavc.transport.endpoint import implied_port
    from openavc.transport.http_client import HTTPClientTransport

    config = driver.config
    host = config.get("host", "")
    # Read without a default: `.get("port", 80)` makes an explicit
    # `port: 80, ssl: true` indistinguishable from "not set", and the
    # fallback would silently rewrite it to 443.
    port = config.get("port")
    scheme = "https" if config.get("ssl", False) else "http"
    if port is None:
        port = implied_port("http", config)
    auth_type = config.get("auth_type", "none")
    kwargs = dict(
        base_url=f"{scheme}://{host}:{port}",
        auth_type=auth_type,
        credentials=_http_credentials(config, auth_type),
        verify_ssl=config.get("verify_ssl", True),
        default_headers=config.get("default_headers", {}),
        timeout=config.get("timeout", 10.0),
        name=driver.device_id,
        local_address=asked.control_ip,
        max_response_bytes=driver._http_max_response_bytes(),
        inter_command_delay=config.get("inter_command_delay", 0.0),
    )
    driver.transport = HTTPClientTransport(**driver._transport_kwargs("http", kwargs))
    await driver.transport.open()


def _known_hosts_path(config: dict[str, Any]) -> str:
    """The device's own known_hosts file, else OpenAVC's in its data folder."""
    known_hosts = config.get("known_hosts_path")
    if known_hosts:
        return known_hosts
    from openavc.system_config import get_data_dir

    return str(get_data_dir() / "ssh" / "known_hosts")


async def _ssh(driver: TransportLadder, asked: _Asked) -> None:
    from openavc.transport.endpoint import implied_port
    from openavc.transport.ssh import SSHTransport

    config = driver.config
    host = config.get("host", "")
    # Parsed before the key is asked for: a port that is not a number fails
    # the connect without making OpenAVC's key.
    port = int(config.get("port") or implied_port("ssh", config))
    username = config.get("username", "")
    auth_method = config.get("ssh_auth_method", "key")
    known_hosts = _known_hosts_path(config)
    key_path = await driver._ssh_key_path(auth_method)
    kwargs = dict(
        host=host,
        port=port,
        username=username,
        on_data=driver.on_data_received,
        on_disconnect=driver._handle_transport_disconnect,
        auth_method=auth_method,
        password=_set_or_none(config, "password"),
        key_path=key_path,
        known_hosts_path=known_hosts,
        host_key_policy=config.get("host_key_policy", "accept-new"),
        connect_timeout=float(config.get("connect_timeout", 15.0)),
        inter_command_delay=config.get("inter_command_delay", 0.0),
        name=driver.device_id,
        extra_ssh_options=_ssh_option_list(config.get("extra_ssh_options")),
        legacy_algorithms=config.get("ssh_legacy_algorithms", True) is not False,
        local_address=asked.control_ip,
    )
    driver.transport = await SSHTransport.create(**driver._transport_kwargs("ssh", kwargs))


async def _mqtt(driver: TransportLadder, asked: _Asked) -> None:
    from openavc.transport.endpoint import implied_port
    from openavc.transport.mqtt import MQTTTransport

    config = driver.config
    # Pub/sub, not a byte stream: inbound messages arrive topic-tagged via
    # on_mqtt_message (subscribe in _post_connect). TLS vocabulary matches the
    # tcp/http branches (`ssl`, `verify_ssl`); MQTT also accepts a client
    # certificate for devices that require one.
    kwargs = dict(
        host=config.get("host", ""),
        port=int(config.get("port") or implied_port("mqtt", config)),
        client_id=_set_or_none(config, "client_id"),
        username=_set_or_none(config, "username"),
        password=_set_or_none(config, "password"),
        use_tls=bool(config.get("ssl", config.get("use_tls", False))),
        verify_ssl=bool(config.get("verify_ssl", True)),
        client_cert=_set_or_none(config, "client_cert"),
        client_key=_set_or_none(config, "client_key"),
        ca_cert=_set_or_none(config, "ca_cert"),
        ciphers=_set_or_none(config, "ciphers"),
        keepalive=int(config.get("keepalive", 60) or 60),
        protocol_version=str(config.get("mqtt_version", "3.1.1")),
        on_message=driver.on_mqtt_message,
        on_disconnect=driver._handle_transport_disconnect,
        name=driver.device_id,
        local_address=asked.control_ip,
    )
    driver.transport = await MQTTTransport.create(**driver._transport_kwargs("mqtt", kwargs))


async def _snmp(driver: TransportLadder, asked: _Asked) -> None:
    from openavc.transport.endpoint import implied_port
    from openavc.transport.snmp import SNMPTransport

    config = driver.config
    # Request/response only: SNMP has no inbound byte stream, so there is no
    # on_data. A device that also sends traps needs a push listener, which is
    # a separate channel from this one. `write_community` defaults to the read
    # community inside the transport, because most agents ship them separate
    # and a driver that only reads should not have to declare both.
    kwargs = dict(
        host=config.get("host", ""),
        port=int(config.get("port") or implied_port("snmp", config)),
        community=config.get("community", "public"),
        write_community=_set_or_none(config, "write_community"),
        timeout=float(config.get("timeout", 2.0) or 2.0),
        retries=int(config.get("retries", 1) or 0),
        on_disconnect=driver._handle_transport_disconnect,
        inter_command_delay=config.get("inter_command_delay", 0.0),
        name=driver.device_id,
    )
    driver.transport = SNMPTransport(**driver._transport_kwargs("snmp", kwargs))
    await driver.transport.open(local_addr=asked.control_ip)


_BUILDERS: dict[str, Callable[[TransportLadder, _Asked], Awaitable[None]]] = {
    "tcp": _tcp,
    "serial": _serial,
    "udp": _udp,
    "osc": _osc,
    "http": _http,
    "ssh": _ssh,
    "mqtt": _mqtt,
    "snmp": _snmp,
}


class TransportLadder:
    """The transport a device's config builds, and the hooks a driver adjusts
    it with. A mixin ``BaseDriver`` inherits (see the module docstring)."""

    # Supplied by BaseDriver; declared here for the methods and builders.
    DRIVER_INFO: dict[str, Any]
    device_id: str
    config: dict[str, Any]
    transport: Any
    on_data_received: Callable[[bytes], Awaitable[None]]
    on_mqtt_message: Callable[[str, bytes], Awaitable[None]]
    _handle_transport_disconnect: Callable[[], None]

    async def _create_transport(self, transport_type: str) -> None:
        """Build ``self.transport`` for the resolved transport type.

        The platform ladder covers every declarative transport; each
        constructor's keyword arguments pass through _transport_kwargs()
        so a driver can adjust them (drop the delimiter for a raw byte
        stream, raise a timeout) without re-implementing the ladder.
        Override this method wholesale only when the driver owns its
        session outright (a raw httpx client, a websocket library) —
        set up the session here and leave ``self.transport`` as None,
        then override _link_alive() and _close_session() so the
        connected property, the health loop, and every teardown path
        see the driver-owned session instead of a platform transport.
        """
        asked = _Asked(
            frame_parser=self._create_frame_parser(),
            delimiter=self._resolve_delimiter(),
            # Reach devices through (if an adapter is chosen); never for a
            # loopback host. The rule, and each transport's fallback port, are
            # in transport/endpoint.py so a connection test dials the same way.
            control_ip=self.local_address() or None,
        )
        build = _BUILDERS.get(transport_type) if isinstance(transport_type, str) else None
        if build is None:
            raise ValueError(f"Unsupported transport type: {transport_type}")
        await build(self, asked)

    def local_address(self, host: str | None = None) -> str | None:
        """The local IP a connection this driver opens to ``host`` leaves
        from, or None to let the operating system choose.

        Settings > Network > Reach devices through can pin device traffic to
        one network adapter. Every connection the platform builds for a driver
        follows it, and so does an ``httpx.AsyncClient`` the driver builds
        itself. A socket, WebSocket or datagram endpoint the driver opens
        itself asks here and binds to the answer::

            ip = self.local_address()
            local_addr = (ip, 0) if ip else None
            await asyncio.open_connection(host, port, local_addr=local_addr)
            await websockets.connect(url, local_addr=local_addr)
            await loop.create_datagram_endpoint(
                factory, remote_addr=(host, port), local_addr=local_addr)
            sock.bind((ip, 0))  # a raw socket, only when ip is not None

        ``host`` defaults to this device's host; pass another when the
        driver also talks to a second address. None when no adapter is
        chosen, and for a host on this computer (a simulator, a local
        service), which a socket bound to an adapter cannot reach. Raises
        :class:`ConnectionFaultError` (``unreachable``) while the chosen
        adapter has no address at all: let it fail the connect, and the
        device card says which adapter and where to change it.
        """
        from openavc.transport.endpoint import control_bind_address

        return control_bind_address(self.config.get("host", "") if host is None else host)

    def _transport_kwargs(
        self, transport_type: str, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        """Hook: adjust the platform transport's constructor arguments.

        Called by _create_transport() with the resolved transport type and the
        fully-assembled keyword arguments just before the transport is built.
        Override to tweak them — drop the delimiter for a raw byte-stream
        protocol (``kwargs["delimiter"] = None``), raise a timeout, force a
        frame parser — while keeping the rest of the platform's construction
        (disconnect wiring, control-interface binding, TLS vocabulary) intact.
        Mutate and return ``kwargs``. Covers constructor arguments only; the
        udp/osc ``open(local_addr=...)`` call is fixed — override
        _create_transport() wholesale if you need to change that. Default:
        returns ``kwargs`` unchanged.
        """
        return kwargs

    def _create_frame_parser(self) -> FrameParser | None:
        """
        Hook: return a custom FrameParser for this driver.

        Override for binary protocols that need length-prefix or callable
        parsing. Return None to use delimiter-based framing (the default).
        """
        return None

    def _resolve_delimiter(self) -> bytes | None:
        """
        Hook: resolve the message delimiter for this driver.

        Checks DRIVER_INFO["delimiter"] first, then self.config["delimiter"],
        then falls back to b"\\r". Override for custom logic.

        An **empty** delimiter means "no delimiter", not "a delimiter of zero
        bytes", and resolves to None like any other raw byte-stream protocol.
        The device page's Line ending control stores its None option as "",
        and a YAML driver may declare ``delimiter: ""`` for the same reason;
        both used to reach the transport as b"", which is not None, so the
        transport built a DelimiterFrameParser on it and that constructor
        rejects an empty delimiter. The device then failed at transport
        construction on every attempt, before any socket work.
        """
        from openavc.transport.binary_helpers import encode_escape_sequences

        for source in (self.DRIVER_INFO, self.config):
            delim = source.get("delimiter")
            if delim is None:
                continue
            raw = delim if isinstance(delim, bytes) else encode_escape_sequences(delim)
            # "" and b"" both mean no framing, not a zero-length delimiter.
            return raw or None
        return b"\r"

    async def _ssh_key_path(self, auth_method: str) -> str | None:
        """The private key an SSH connection signs in with.

        The device's own Key Path when it has one. Otherwise, for key sign-in,
        OpenAVC's own key (``transport/ssh_identity.py``), made on first use,
        so key sign-in works on an install where nobody can place a key file.
        Password sign-in takes no key. If the key cannot be made (a read-only
        data folder), the connection goes ahead without one and the device's
        refusal names the sign-in problem.
        """
        configured = str(self.config.get("key_path") or "").strip()
        if configured:
            return configured
        if str(auth_method or "key").lower() == "password":
            return None
        from openavc.transport import ssh_identity

        try:
            key = await asyncio.to_thread(ssh_identity.ensure_platform_key)
        except Exception as exc:  # any failure falls back to signing in without it
            log.warning(
                "[%s] Could not prepare OpenAVC's SSH key: %s", self.device_id, exc,
            )
            return None
        return str(key.path)

    @staticmethod
    def _coerce_serial_params(
        config: dict[str, Any],
    ) -> tuple[int, int, str, int | float, bool, bool]:
        """Coerce + validate serial params from untyped project config.

        Project config is untyped JSON, so an integrator / AI tool / hand-edit
        can store ``bytesize: "8"`` or ``stopbits: "1.5"`` as strings, or a
        flat-out invalid value. pyserial does exact membership tests and raises
        a bare ValueError at connect that the device manager then buries under
        ~120 generic reconnect attempts. Coerce string forms to the right type
        and raise a clear, actionable error for genuinely invalid values,
        checked in the order returned.

        Returns ``(baudrate, bytesize, parity, stopbits, rtscts, xonxoff)``.
        """
        baudrate = _serial_int(config, "baudrate", 9600, "must be an integer")
        bytesize = _serial_bytesize(config)
        parity = _serial_parity(config)
        stopbits = _serial_stopbits(config)
        rtscts, xonxoff = _serial_flow(config)
        return baudrate, bytesize, parity, stopbits, rtscts, xonxoff

    def _required_port(self) -> int:
        """Return ``config['port']`` for TCP/UDP/OSC, or raise a clear error.

        Driver ``default_config.port`` is layered in by
        ``Engine.resolved_device_config`` before instantiation, so a
        properly-declared driver always has a port here. A missing port
        means the driver definition skipped declaring one — surface that
        as a config error instead of silently dialing port 23.
        """
        port = self.config.get("port")
        if port is None or port == "":
            driver_id = self.DRIVER_INFO.get("id", "?")
            raise ConnectionError(
                f"Device '{self.device_id}': missing 'port' in config "
                f"(driver '{driver_id}' must declare default_config.port "
                f"or the device must override it)"
            )
        try:
            return int(port)
        except (TypeError, ValueError) as e:
            driver_id = self.DRIVER_INFO.get("id", "?")
            raise ConnectionError(
                f"Device '{self.device_id}': invalid port {port!r} "
                f"(driver '{driver_id}')"
            ) from e

    def _http_max_response_bytes(self) -> int:
        """Resolve the HTTP response-body cap from config.

        The 32 MB default clears any realistic device payload; a driver whose
        device legitimately returns a larger body (a firmware or log export)
        raises it via the ``max_response_bytes`` config key. A missing,
        non-numeric, or non-positive value falls back to the default rather
        than handing the transport a nonsense cap.
        """
        from openavc.transport.http_client import DEFAULT_MAX_RESPONSE_BYTES

        raw = self.config.get("max_response_bytes")
        if raw is None:
            return DEFAULT_MAX_RESPONSE_BYTES
        try:
            value = int(raw)
        except (TypeError, ValueError):
            log.warning(
                f"[{self.device_id}] Ignoring invalid max_response_bytes "
                f"{raw!r}; using default {DEFAULT_MAX_RESPONSE_BYTES}"
            )
            return DEFAULT_MAX_RESPONSE_BYTES
        if value <= 0:
            log.warning(
                f"[{self.device_id}] Ignoring non-positive max_response_bytes "
                f"{raw!r}; using default {DEFAULT_MAX_RESPONSE_BYTES}"
            )
            return DEFAULT_MAX_RESPONSE_BYTES
        return value
