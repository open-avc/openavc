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
* THE rule for a setting a transport cannot be built from: it is refused
  as ``invalid_config`` (a :class:`ConnectionFaultError`) before anything
  is dialed, naming the setting, so the device card says what to change and
  the device manager stops retrying (retrying can never change the answer).
  Each builder runs in two halves, its settings and its dial: the framing
  hooks and the settings half (the builder's own reads and the driver's
  ``_transport_kwargs`` hook) run under :func:`_refusing_unusable`, which
  types any ``ValueError`` or ``TypeError`` they raise; the checks below that
  know the setting raise the typed refusal themselves, in words. The dial is
  outside it: a failure once a transport opens is the transport's to word.
* THE line settings a serial port opens with are
  :func:`serial_line_settings`' (read by the contract's table in
  ``serial_line.py``, refused as above). The tests that open a port without
  the driver, the Driver Builder's raw Send and Test Connection, ask it too,
  so a test opens and refuses what the connect does.
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
import importlib
import weakref
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from openavc.core.connection_fault import (
    INVALID_CONFIG,
    ConnectionFaultError,
    no_port_message,
    no_serial_port_message,
)
from openavc.drivers.serial_line import SERIAL_LINE_SETTINGS, read_line_setting
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


# --- Settings a transport cannot be built from --------------------------------


def _refused(message: str) -> ConnectionFaultError:
    """The typed refusal of a setting no connection can be built from."""
    return ConnectionFaultError(message, code=INVALID_CONFIG)


def _in_words(exc: BaseException) -> str:
    return (str(exc).strip() or type(exc).__name__).rstrip(".")


@contextmanager
def _refusing_unusable(sentence: str) -> Iterator[None]:
    """Run a step that reads settings, before anything is dialed, typing a
    ``ValueError`` or ``TypeError`` it raises as ``invalid_config``.

    ``sentence`` frames the error's own words (``{reason}``). A refusal that
    is already typed (an adapter with no address, a setting a check below
    worded) passes through as it is.
    """
    try:
        yield
    except (ValueError, TypeError) as exc:
        raise _refused(sentence.format(reason=_in_words(exc))) from exc


_FRAMING_UNUSABLE = (
    "This device's message framing can't be used: {reason}. Check the line "
    "ending and packet framing in its settings or its driver."
)
_SETTINGS_UNUSABLE = (
    "This device's connection settings can't be used: {reason}. Check them "
    "in Edit Device."
)


def _port_number(port: object) -> int:
    """``port`` as the number a socket takes, or the typed refusal naming it."""
    try:
        number = int(port)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = -1
    if not 0 <= number <= 65535:
        raise _not_a_port(port)
    return number


def _not_a_port(port: object) -> ConnectionFaultError:
    return _refused(
        f"This device's Port setting is {port!r}, which is not a port "
        f"number. Enter a number from 1 to 65535 in the device's "
        f"Connection Settings."
    )


def _delimiter_not_text(delimiter: object, from_driver: bool) -> str:
    if from_driver:
        return (
            f"This device's driver sets its line ending to {delimiter!r}, which "
            f"is not text, so the device can't be connected. The driver's "
            f"delimiter must be text, such as \\r or \\r\\n."
        )
    return (
        f"This device's line ending is set to {delimiter!r}, which is not "
        f"text. Choose a line ending on the device page."
    )


# --- Serial line settings -----------------------------------------------------
# What each setting may be, and how each way of writing one reads, is the
# driver contract's one table (serial_line.SERIAL_LINE_SETTINGS, read_line_setting):
# the refusal names the setting as the device form does and says what it must
# be in the table's words.


def _serial_refused(key: str, value: object) -> ConnectionFaultError:
    setting = SERIAL_LINE_SETTINGS[key]
    label = setting["label"]
    return _refused(
        f"This device's {label} setting is {value!r}, which a serial port "
        f"can't use. {label} must be {setting['allowed']}. Set it in Edit Device."
    )


def _serial_setting(config: dict[str, Any], key: str) -> Any:
    """The reading of one line setting, absent read as the table's default,
    or the typed refusal naming it."""
    raw = config.get(key, SERIAL_LINE_SETTINGS[key]["default"])
    value = read_line_setting(key, raw)
    if value is None:
        raise _serial_refused(key, raw)
    return value


def _serial_port(config: dict[str, Any]) -> Any:
    """The serial port to open; blank is refused before anything is opened.
    Anything else goes to the transport as it is: it refuses a value that is
    not a device path in words of its own."""
    port = config.get("port", "")
    if not str(port or "").strip():
        raise _refused(no_serial_port_message())
    return port


def _serial_flow(config: dict[str, Any]) -> tuple[bool, bool]:
    """``(rtscts, xonxoff)``. A value with no reading opens with no flow
    control rather than refusing, so a device that connected before still
    does (``_note_unread_flow`` says so); the author's doors refuse it in a
    driver."""
    flow = read_line_setting("flow_control", _flow_written(config))
    return flow == "hardware", flow == "software"


def _flow_written(config: dict[str, Any]) -> Any:
    return config.get("flow_control", SERIAL_LINE_SETTINGS["flow_control"]["default"])


# Each driver whose flow control has no reading, with the value the log last
# named, so a device retrying its connection says so once, not every retry.
_UNREAD_FLOW: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def _note_unread_flow(driver: TransportLadder) -> None:
    """Log, once per driver and value, a flow control the port opens without."""
    written = _flow_written(driver.config)
    if read_line_setting("flow_control", written) is not None:
        _UNREAD_FLOW.pop(driver, None)
        return
    if driver in _UNREAD_FLOW and _UNREAD_FLOW[driver] == written:
        return
    _UNREAD_FLOW[driver] = written
    log.warning(
        "[%s] %s It opens with no flow control.",
        driver.device_id, _serial_refused("flow_control", written),
    )


def serial_line_settings(config: dict[str, Any]) -> dict[str, Any]:
    """THE line settings a serial port is opened with for ``config``: the
    ``baudrate``, ``bytesize``, ``parity``, ``stopbits``, ``rtscts`` and
    ``xonxoff`` keyword arguments the transport and pyserial take.

    Each setting is read by the contract's one table
    (``serial_line.read_line_setting``), an absent one as the table's
    default, and one with no reading is refused as ``invalid_config``, naming
    the setting as the device form does, checked in the order above; flow
    control with no reading opens with none (``_serial_flow``). The connect
    asks this, and so does every test that opens a port without the driver:
    the Driver Builder's raw Send (from the draft's ``default_config``) and
    Test Connection (from the device's resolved config).
    """
    baudrate, bytesize, parity, stopbits = (
        _serial_setting(config, key)
        for key in ("baudrate", "bytesize", "parity", "stopbits")
    )
    rtscts, xonxoff = _serial_flow(config)
    return dict(
        baudrate=baudrate, bytesize=bytesize, parity=parity,
        stopbits=stopbits, rtscts=rtscts, xonxoff=xonxoff,
    )


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


# Each settings half reads the device's config into the keyword arguments its
# transport is built with, and dials nothing (_create_transport hands them to
# the driver's _transport_kwargs hook, then to the dial).

async def _tcp(driver: TransportLadder, asked: _Asked) -> dict[str, Any]:
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
    return kwargs


async def _serial(driver: TransportLadder, asked: _Asked) -> dict[str, Any]:
    config = driver.config
    baudrate, bytesize, parity, stopbits, rtscts, xonxoff = (
        driver._coerce_serial_params(config)
    )
    _note_unread_flow(driver)
    kwargs = dict(
        port=_serial_port(config),
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
    return kwargs


async def _udp(driver: TransportLadder, asked: _Asked) -> dict[str, Any]:
    config = driver.config
    kwargs = dict(
        host=config.get("host", ""),
        port=driver._required_port(),
        on_data=driver.on_data_received,
        on_disconnect=driver._handle_transport_disconnect,
        inter_command_delay=config.get("inter_command_delay", 0.0),
        name=driver.device_id,
    )
    return kwargs


async def _osc(driver: TransportLadder, asked: _Asked) -> dict[str, Any]:
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
    return kwargs


async def _http(driver: TransportLadder, asked: _Asked) -> dict[str, Any]:
    from openavc.transport.endpoint import UnusablePort, http_endpoint

    config = driver.config
    try:
        endpoint = http_endpoint(config.get("host", ""), config)
    except UnusablePort as unusable:
        raise _not_a_port(unusable.port) from unusable
    auth_type = config.get("auth_type", "none")
    kwargs = dict(
        base_url=endpoint.base_url,
        auth_type=auth_type,
        credentials=_http_credentials(config, auth_type),
        verify_ssl=endpoint.verify,
        default_headers=config.get("default_headers", {}),
        timeout=config.get("timeout", 10.0),
        name=driver.device_id,
        local_address=asked.control_ip,
        max_response_bytes=driver._http_max_response_bytes(),
        inter_command_delay=config.get("inter_command_delay", 0.0),
    )
    return kwargs


def _known_hosts_path(config: dict[str, Any]) -> str:
    """The device's own known_hosts file, else OpenAVC's in its data folder."""
    known_hosts = config.get("known_hosts_path")
    if known_hosts:
        return known_hosts
    from openavc.system_config import get_data_dir

    return str(get_data_dir() / "ssh" / "known_hosts")


async def _ssh(driver: TransportLadder, asked: _Asked) -> dict[str, Any]:
    from openavc.transport.endpoint import implied_port

    config = driver.config
    host = config.get("host", "")
    # Parsed before the key is asked for: a port that is not a number fails
    # the connect without making OpenAVC's key.
    port = _port_number(config.get("port") or implied_port("ssh", config))
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
    return kwargs


async def _mqtt(driver: TransportLadder, asked: _Asked) -> dict[str, Any]:
    from openavc.transport.endpoint import implied_port

    config = driver.config
    # Pub/sub, not a byte stream: inbound messages arrive topic-tagged via
    # on_mqtt_message (subscribe in _post_connect). TLS vocabulary matches the
    # tcp/http branches (`ssl`, `verify_ssl`); MQTT also accepts a client
    # certificate for devices that require one.
    kwargs = dict(
        host=config.get("host", ""),
        port=_port_number(config.get("port") or implied_port("mqtt", config)),
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
    return kwargs


async def _snmp(driver: TransportLadder, asked: _Asked) -> dict[str, Any]:
    from openavc.transport.endpoint import implied_port

    config = driver.config
    # Request/response only: SNMP has no inbound byte stream, so there is no
    # on_data. A device that also sends traps needs a push listener, which is
    # a separate channel from this one. `write_community` defaults to the read
    # community inside the transport, because most agents ship them separate
    # and a driver that only reads should not have to declare both.
    kwargs = dict(
        host=config.get("host", ""),
        port=_port_number(config.get("port") or implied_port("snmp", config)),
        community=config.get("community", "public"),
        write_community=_set_or_none(config, "write_community"),
        timeout=float(config.get("timeout", 2.0) or 2.0),
        retries=int(config.get("retries", 1) or 0),
        on_disconnect=driver._handle_transport_disconnect,
        inter_command_delay=config.get("inter_command_delay", 0.0),
        name=driver.device_id,
    )
    return kwargs


# --- The dials ----------------------------------------------------------------
# Where the first socket (or port, or process) opens. The transport class is
# looked up here, at connect, so a test that replaces it reaches the connect.

_Settings = Callable[["TransportLadder", _Asked], Awaitable[dict[str, Any]]]
_Dial = Callable[["TransportLadder", dict[str, Any], _Asked], Awaitable[None]]


def _transport_class(path: str) -> Any:
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


def _created(path: str) -> _Dial:
    """A transport whose ``create()`` builds it and opens it."""

    async def dial(driver: TransportLadder, kwargs: dict[str, Any], asked: _Asked) -> None:
        driver.transport = await _transport_class(path).create(**kwargs)

    return dial


def _opened(path: str, *, local_addr: bool) -> _Dial:
    """A transport built by its constructor, then opened (from the chosen
    adapter's address, for a datagram socket)."""

    async def dial(driver: TransportLadder, kwargs: dict[str, Any], asked: _Asked) -> None:
        driver.transport = _transport_class(path)(**kwargs)
        if local_addr:
            await driver.transport.open(local_addr=asked.control_ip)
        else:
            await driver.transport.open()

    return dial


_BUILDERS: dict[str, tuple[_Settings, _Dial]] = {
    "tcp": (_tcp, _created("openavc.transport.tcp.TCPTransport")),
    "serial": (_serial, _created("openavc.transport.serial_transport.SerialTransport")),
    "udp": (_udp, _opened("openavc.transport.udp.UDPTransport", local_addr=True)),
    "osc": (_osc, _opened("openavc.transport.osc.OSCTransport", local_addr=True)),
    "http": (_http, _opened("openavc.transport.http_client.HTTPClientTransport", local_addr=False)),
    "ssh": (_ssh, _created("openavc.transport.ssh.SSHTransport")),
    "mqtt": (_mqtt, _created("openavc.transport.mqtt.MQTTTransport")),
    "snmp": (_snmp, _opened("openavc.transport.snmp.SNMPTransport", local_addr=True)),
}


async def http_settings(driver: TransportLadder) -> dict[str, Any]:
    """The keyword arguments ``driver``'s HTTP transport is built with now,
    after the driver's ``_transport_kwargs`` hook and refused as a connect
    refuses them, with nothing dialled and no adapter asked: what a stand-in
    that records instead of sending is built from (``drivers/dry_run.py``),
    so it carries the headers and sign-in the device will get."""
    asked = _Asked(frame_parser=None, delimiter=None, control_ip=None)
    with _refusing_unusable(_SETTINGS_UNUSABLE):
        return driver._transport_kwargs("http", await _http(driver, asked))


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

        A setting no connection can be built from is refused as
        ``invalid_config`` before the dial (the module docstring has the
        rule); a failure in the dial itself is left as the transport raised it.
        """
        with _refusing_unusable(_FRAMING_UNUSABLE):
            frame_parser = self._create_frame_parser()
            delimiter = self._resolve_delimiter()
        asked = _Asked(
            frame_parser=frame_parser,
            delimiter=delimiter,
            # Reach devices through (if an adapter is chosen); never for a
            # loopback host. The rule, and each transport's fallback port, are
            # in transport/endpoint.py so a connection test dials the same way.
            control_ip=self.local_address() or None,
        )
        build = _BUILDERS.get(transport_type) if isinstance(transport_type, str) else None
        if build is None:
            raise _refused(
                f"This device's Connection is set to {transport_type!r}, which "
                f"OpenAVC can't connect over. Choose another in Edit Device."
            )
        settings, dial = build
        with _refusing_unusable(_SETTINGS_UNUSABLE):
            kwargs = self._transport_kwargs(transport_type, await settings(self, asked))
        await dial(self, kwargs, asked)

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

        The device's line ending, as ``drivers/line_ending.py`` reads it for
        every reader (the simulator and the validator too): DRIVER_INFO's
        delimiter, then self.config's, then b"\\r". Override for custom logic.

        An **empty** delimiter means "no delimiter", not "a delimiter of zero
        bytes", and resolves to None like any other raw byte-stream protocol.
        The device page's Line ending control stores its None option as "",
        and a YAML driver may declare ``delimiter: ""`` for the same reason;
        both used to reach the transport as b"", which is not None, so the
        transport built a DelimiterFrameParser on it and that constructor
        rejects an empty delimiter. The device then failed at transport
        construction on every attempt, before any socket work.

        A delimiter that is not text (a number, a list) is refused as
        ``invalid_config``, saying whether the driver or the device set it:
        the driver's wins, so the device page cannot change it.
        """
        from openavc.drivers.line_ending import LineEndingNotText, effective_delimiter

        try:
            return effective_delimiter(self.DRIVER_INFO, self.config)
        except LineEndingNotText as e:
            raise _refused(_delimiter_not_text(e.value, e.from_driver)) from None

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
        """Read the serial line settings from untyped project config.

        Project config is untyped JSON, so an integrator, the AI or a
        hand-edit can store ``bytesize: "8"``, ``parity: "Even"`` or a value
        no port takes, and pyserial does exact membership tests. The reading
        and its refusals are :func:`serial_line_settings`'.

        Returns ``(baudrate, bytesize, parity, stopbits, rtscts, xonxoff)``.
        """
        return tuple(serial_line_settings(config).values())  # type: ignore[return-value]

    def _required_port(self) -> int:
        """Return ``config['port']`` for TCP/UDP/OSC, or refuse it.

        Driver ``default_config.port`` is layered in by
        ``Engine.resolved_device_config`` before instantiation, so a
        properly-declared driver always has a port here. A missing port
        means the driver definition skipped declaring one; it is refused as
        ``invalid_config`` in the connection test's words instead of
        silently dialing port 23, and so is a port that is not a number.
        """
        port = self.config.get("port")
        if port is None or port == "":
            raise _refused(no_port_message())
        return _port_number(port)

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
