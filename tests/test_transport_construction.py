"""What a device's config builds, transport by transport.

``BaseDriver._create_transport`` turns a device's config into the platform
transport it connects over. A Python driver adjusts the result through
``_transport_kwargs(transport_type, kwargs)``, so the keyword arguments each
transport is built with, their defaults and their coercions are what every
such driver sees. These tests pin them: every key, for each transport, with
the config a device usually carries and with the settings that change them.

Nothing dials. The transports are replaced by stand-ins that record how they
were made (``create(**kwargs)``, or the constructor and then ``open``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openavc.core.connection_fault import ConnectionFaultError
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport.http_client import DEFAULT_MAX_RESPONSE_BYTES

ADAPTER_IP = "10.9.8.7"


class _AcmeWidget(BaseDriver):
    DRIVER_INFO = {"id": "acme_widget", "transport": "tcp"}

    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        return None

    def _transport_kwargs(self, transport_type: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.asked = (transport_type, dict(kwargs))
        return kwargs


def _driver(config: dict[str, Any]) -> _AcmeWidget:
    return _AcmeWidget("acme1", config, StateStore(), EventBus())


@pytest.fixture(params=[None, ADAPTER_IP], ids=["no-adapter", "adapter"])
def control_ip(request, monkeypatch):
    """Reach devices through: no adapter chosen, or one at ADAPTER_IP."""
    monkeypatch.setattr(
        "openavc.transport.endpoint.control_bind_address", lambda host: request.param,
    )
    return request.param


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("openavc.system_config.get_data_dir", lambda: tmp_path)
    return tmp_path


class _Built:
    """A transport made by its constructor, then opened."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.opened: list[dict[str, Any]] = []

    async def open(self, **kwargs: Any) -> None:
        self.opened.append(kwargs)


def _constructed(monkeypatch, target: str) -> list[_Built]:
    made: list[_Built] = []

    class _Recorded(_Built):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            made.append(self)

    monkeypatch.setattr(target, _Recorded)
    return made


def _created(monkeypatch, target: str) -> AsyncMock:
    create = AsyncMock(return_value=SimpleNamespace(made_by="create"))
    monkeypatch.setattr(target, create)
    return create


async def _build_created(monkeypatch, target: str, transport: str, config: dict[str, Any]):
    create = _created(monkeypatch, target)
    driver = _driver(config)
    await driver._create_transport(transport)
    assert create.await_count == 1
    assert create.await_args.args == ()
    assert driver.transport is create.return_value
    assert driver.asked == (transport, create.await_args.kwargs)
    return driver, create.await_args.kwargs


async def _build_constructed(monkeypatch, target: str, transport: str, config: dict[str, Any]):
    made = _constructed(monkeypatch, target)
    driver = _driver(config)
    await driver._create_transport(transport)
    assert len(made) == 1
    assert driver.transport is made[0]
    assert driver.asked == (transport, made[0].kwargs)
    return driver, made[0]


# --- tcp ---------------------------------------------------------------------


async def test_tcp_with_a_host_and_port(monkeypatch, control_ip):
    driver, kwargs = await _build_created(
        monkeypatch, "openavc.transport.tcp.TCPTransport.create", "tcp",
        {"host": "192.0.2.10", "port": "4999"},
    )
    assert kwargs == {
        "host": "192.0.2.10",
        "port": 4999,
        "on_data": driver.on_data_received,
        "on_disconnect": driver._handle_transport_disconnect,
        "delimiter": b"\r",
        "frame_parser": None,
        "inter_command_delay": 0.0,
        "name": "acme1",
        "local_addr": (ADAPTER_IP, 0) if control_ip else None,
        "timeout": 5.0,
        "ssl": False,
        "ssl_verify": True,
        "keepalive": False,
    }


async def test_tcp_with_tls_keepalive_pacing_and_its_own_line_ending(monkeypatch, control_ip):
    driver, kwargs = await _build_created(
        monkeypatch, "openavc.transport.tcp.TCPTransport.create", "tcp",
        {
            "host": "acme.example", "port": 23, "ssl": True, "verify_ssl": False,
            "tcp_keepalive": 1, "timeout": 2.5, "inter_command_delay": 0.2,
            "delimiter": "\\n",
        },
    )
    assert kwargs["ssl"] is True
    assert kwargs["ssl_verify"] is False
    assert kwargs["keepalive"] is True
    assert kwargs["timeout"] == 2.5
    assert kwargs["inter_command_delay"] == 0.2
    assert kwargs["delimiter"] == b"\n"


async def test_tcp_without_a_port_names_the_driver(monkeypatch, control_ip):
    create = _created(monkeypatch, "openavc.transport.tcp.TCPTransport.create")
    with pytest.raises(ConnectionError, match="missing 'port'.*acme_widget"):
        await _driver({"host": "192.0.2.10"})._create_transport("tcp")
    create.assert_not_awaited()


# --- serial ------------------------------------------------------------------


async def test_serial_defaults(monkeypatch, control_ip):
    driver, kwargs = await _build_created(
        monkeypatch, "openavc.transport.serial_transport.SerialTransport.create", "serial",
        {"port": "/dev/ttyUSB0"},
    )
    assert kwargs == {
        "port": "/dev/ttyUSB0",
        "baudrate": 9600,
        "on_data": driver.on_data_received,
        "on_disconnect": driver._handle_transport_disconnect,
        "delimiter": b"\r",
        "frame_parser": None,
        "inter_command_delay": 0.0,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "rtscts": False,
        "xonxoff": False,
        "name": "acme1",
    }


async def test_serial_line_settings_typed_as_text(monkeypatch, control_ip):
    _, kwargs = await _build_created(
        monkeypatch, "openavc.transport.serial_transport.SerialTransport.create", "serial",
        {
            "port": "COM3", "baudrate": "19200", "bytesize": "7", "parity": "e",
            "stopbits": "1.5", "flow_control": " Hardware ", "inter_command_delay": 0.05,
        },
    )
    assert (kwargs["baudrate"], kwargs["bytesize"], kwargs["parity"]) == (19200, 7, "E")
    assert kwargs["stopbits"] == 1.5 and isinstance(kwargs["stopbits"], float)
    assert (kwargs["rtscts"], kwargs["xonxoff"]) == (True, False)
    assert kwargs["inter_command_delay"] == 0.05


async def test_serial_two_stop_bits_and_software_flow(monkeypatch, control_ip):
    _, kwargs = await _build_created(
        monkeypatch, "openavc.transport.serial_transport.SerialTransport.create", "serial",
        {"port": "COM3", "stopbits": "2", "flow_control": "xon/xoff"},
    )
    assert kwargs["stopbits"] == 2 and isinstance(kwargs["stopbits"], int)
    assert (kwargs["rtscts"], kwargs["xonxoff"]) == (False, True)


async def test_serial_settings_the_port_cannot_take(monkeypatch, control_ip):
    create = _created(monkeypatch, "openavc.transport.serial_transport.SerialTransport.create")
    with pytest.raises(ValueError, match=r"Invalid parity 'Q'"):
        await _driver({"port": "COM3", "parity": "Q"})._create_transport("serial")
    create.assert_not_awaited()


# --- udp ---------------------------------------------------------------------


async def test_udp(monkeypatch, control_ip):
    driver, built = await _build_constructed(
        monkeypatch, "openavc.transport.udp.UDPTransport", "udp",
        {"host": "192.0.2.20", "port": "6454", "inter_command_delay": 0.01},
    )
    assert built.kwargs == {
        "host": "192.0.2.20",
        "port": 6454,
        "on_data": driver.on_data_received,
        "on_disconnect": driver._handle_transport_disconnect,
        "inter_command_delay": 0.01,
        "name": "acme1",
    }
    assert built.opened == [{"local_addr": control_ip}]


# --- osc ---------------------------------------------------------------------


async def test_osc_over_udp(monkeypatch, control_ip):
    driver, built = await _build_constructed(
        monkeypatch, "openavc.transport.osc.OSCTransport", "osc",
        {"host": "192.0.2.30", "port": 53000},
    )
    assert built.kwargs == {
        "host": "192.0.2.30",
        "port": 53000,
        "listen_port": 0,
        "on_data": driver.on_data_received,
        "on_disconnect": driver._handle_transport_disconnect,
        "inter_command_delay": 0.0,
        "name": "acme1",
        "tcp": False,
    }
    assert built.opened == [{"local_addr": control_ip}]


async def test_osc_over_tcp_with_a_reply_port(monkeypatch, control_ip):
    _, built = await _build_constructed(
        monkeypatch, "openavc.transport.osc.OSCTransport", "osc",
        {"host": "192.0.2.30", "port": 53000, "listen_port": 53001, "transport_mode": "TCP"},
    )
    assert built.kwargs["tcp"] is True
    assert built.kwargs["listen_port"] == 53001


# --- http --------------------------------------------------------------------


async def test_http_defaults(monkeypatch, control_ip):
    _, built = await _build_constructed(
        monkeypatch, "openavc.transport.http_client.HTTPClientTransport", "http",
        {"host": "192.0.2.40"},
    )
    assert built.kwargs == {
        "base_url": "http://192.0.2.40:80",
        "auth_type": "none",
        "credentials": {},
        "verify_ssl": True,
        "default_headers": {},
        "timeout": 10.0,
        "name": "acme1",
        "local_address": control_ip,
        "max_response_bytes": DEFAULT_MAX_RESPONSE_BYTES,
        "inter_command_delay": 0.0,
    }
    assert built.opened == [{}]


@pytest.mark.parametrize(
    ("config", "url"),
    [
        ({"ssl": True}, "https://192.0.2.40:443"),
        ({"ssl": True, "port": 80}, "https://192.0.2.40:80"),
        ({"port": 8080}, "http://192.0.2.40:8080"),
    ],
)
async def test_http_scheme_and_port(monkeypatch, control_ip, config, url):
    _, built = await _build_constructed(
        monkeypatch, "openavc.transport.http_client.HTTPClientTransport", "http",
        {"host": "192.0.2.40", **config},
    )
    assert built.kwargs["base_url"] == url


@pytest.mark.parametrize(
    ("config", "credentials"),
    [
        ({"auth_type": "basic", "username": "admin", "password": "pw"},
         {"username": "admin", "password": "pw"}),
        ({"auth_type": "digest"}, {"username": "", "password": ""}),
        ({"auth_type": "bearer", "token": "t0k"}, {"token": "t0k"}),
        ({"auth_type": "api_key", "api_key": "k3y"}, {"header": "X-API-Key", "key": "k3y"}),
        ({"auth_type": "api_key", "api_key_header": "X-Acme-Key"},
         {"header": "X-Acme-Key", "key": ""}),
        ({"auth_type": "oauth", "token": "t0k"}, {}),
    ],
)
async def test_http_credentials_follow_the_auth_type(monkeypatch, control_ip, config, credentials):
    _, built = await _build_constructed(
        monkeypatch, "openavc.transport.http_client.HTTPClientTransport", "http",
        {"host": "192.0.2.40", **config},
    )
    assert built.kwargs["auth_type"] == config["auth_type"]
    assert built.kwargs["credentials"] == credentials


async def test_http_settings_that_pass_straight_through(monkeypatch, control_ip):
    _, built = await _build_constructed(
        monkeypatch, "openavc.transport.http_client.HTTPClientTransport", "http",
        {
            "host": "192.0.2.40", "verify_ssl": False, "default_headers": {"X-A": "1"},
            "timeout": 3, "inter_command_delay": 0.3, "max_response_bytes": "1024",
        },
    )
    assert built.kwargs["verify_ssl"] is False
    assert built.kwargs["default_headers"] == {"X-A": "1"}
    assert built.kwargs["timeout"] == 3
    assert built.kwargs["inter_command_delay"] == 0.3
    assert built.kwargs["max_response_bytes"] == 1024


# --- ssh ---------------------------------------------------------------------


async def test_ssh_password_sign_in(monkeypatch, control_ip, data_dir):
    driver, kwargs = await _build_created(
        monkeypatch, "openavc.transport.ssh.SSHTransport.create", "ssh",
        {"host": "192.0.2.50", "username": "admin", "password": "pw",
         "ssh_auth_method": "password"},
    )
    assert kwargs == {
        "host": "192.0.2.50",
        "port": 22,
        "username": "admin",
        "on_data": driver.on_data_received,
        "on_disconnect": driver._handle_transport_disconnect,
        "auth_method": "password",
        "password": "pw",
        "key_path": None,
        "known_hosts_path": str(data_dir / "ssh" / "known_hosts"),
        "host_key_policy": "accept-new",
        "connect_timeout": 15.0,
        "inter_command_delay": 0.0,
        "name": "acme1",
        "extra_ssh_options": [],
        "legacy_algorithms": True,
        "local_address": control_ip,
    }


async def test_ssh_key_sign_in_with_the_platform_key(monkeypatch, control_ip, data_dir):
    monkeypatch.setattr(
        "openavc.transport.ssh_identity.ensure_platform_key",
        lambda: SimpleNamespace(path=data_dir / "id_platform"),
    )
    _, kwargs = await _build_created(
        monkeypatch, "openavc.transport.ssh.SSHTransport.create", "ssh",
        {"host": "192.0.2.50", "password": ""},
    )
    assert kwargs["auth_method"] == "key"
    assert kwargs["key_path"] == str(data_dir / "id_platform")
    assert kwargs["password"] is None
    assert kwargs["username"] == ""


async def test_ssh_settings_a_device_can_carry(monkeypatch, control_ip, data_dir):
    _, kwargs = await _build_created(
        monkeypatch, "openavc.transport.ssh.SSHTransport.create", "ssh",
        {
            "host": "192.0.2.50", "port": "2222", "key_path": " /keys/acme ",
            "known_hosts_path": "/etc/acme/known_hosts", "host_key_policy": "strict",
            "connect_timeout": "4", "inter_command_delay": 0.1,
            "extra_ssh_options": "Ciphers=+aes128-cbc,3des-cbc\n\nKexAlgorithms=+dh",
            "ssh_legacy_algorithms": False,
        },
    )
    assert kwargs["port"] == 2222
    assert kwargs["key_path"] == "/keys/acme"
    assert kwargs["known_hosts_path"] == "/etc/acme/known_hosts"
    assert kwargs["host_key_policy"] == "strict"
    assert kwargs["connect_timeout"] == 4.0
    assert kwargs["inter_command_delay"] == 0.1
    assert kwargs["extra_ssh_options"] == ["Ciphers=+aes128-cbc,3des-cbc", "KexAlgorithms=+dh"]
    assert kwargs["legacy_algorithms"] is False


async def test_ssh_port_that_is_not_a_number_makes_no_key(monkeypatch, control_ip, data_dir):
    made: list[bool] = []
    monkeypatch.setattr(
        "openavc.transport.ssh_identity.ensure_platform_key",
        lambda: made.append(True) or SimpleNamespace(path=data_dir / "id"),
    )
    create = _created(monkeypatch, "openavc.transport.ssh.SSHTransport.create")
    with pytest.raises(ValueError):
        await _driver({"host": "192.0.2.50", "port": "twenty-two"})._create_transport("ssh")
    assert made == []
    create.assert_not_awaited()


# --- mqtt --------------------------------------------------------------------


async def test_mqtt_defaults(monkeypatch, control_ip):
    driver, kwargs = await _build_created(
        monkeypatch, "openavc.transport.mqtt.MQTTTransport.create", "mqtt",
        {"host": "192.0.2.60", "client_id": "", "username": "", "password": ""},
    )
    assert kwargs == {
        "host": "192.0.2.60",
        "port": 1883,
        "client_id": None,
        "username": None,
        "password": None,
        "use_tls": False,
        "verify_ssl": True,
        "client_cert": None,
        "client_key": None,
        "ca_cert": None,
        "ciphers": None,
        "keepalive": 60,
        "protocol_version": "3.1.1",
        "on_message": driver.on_mqtt_message,
        "on_disconnect": driver._handle_transport_disconnect,
        "name": "acme1",
        "local_address": control_ip,
    }


async def test_mqtt_with_tls_and_a_client_certificate(monkeypatch, control_ip):
    _, kwargs = await _build_created(
        monkeypatch, "openavc.transport.mqtt.MQTTTransport.create", "mqtt",
        {
            "host": "192.0.2.60", "port": "8883", "client_id": "acme-room",
            "username": "u", "password": "p", "use_tls": 1, "verify_ssl": 0,
            "client_cert": "/c.pem", "client_key": "/k.pem", "ca_cert": "/ca.pem",
            "ciphers": "HIGH", "keepalive": "30", "mqtt_version": 5,
        },
    )
    assert kwargs["port"] == 8883
    assert (kwargs["client_id"], kwargs["username"], kwargs["password"]) == ("acme-room", "u", "p")
    assert kwargs["use_tls"] is True
    assert kwargs["verify_ssl"] is False
    assert (kwargs["client_cert"], kwargs["client_key"], kwargs["ca_cert"]) == (
        "/c.pem", "/k.pem", "/ca.pem",
    )
    assert kwargs["ciphers"] == "HIGH"
    assert kwargs["keepalive"] == 30
    assert kwargs["protocol_version"] == "5"


@pytest.mark.parametrize(
    ("config", "use_tls"),
    [({"ssl": True}, True), ({"ssl": False, "use_tls": True}, False), ({"use_tls": True}, True)],
)
async def test_mqtt_ssl_is_asked_before_use_tls(monkeypatch, control_ip, config, use_tls):
    _, kwargs = await _build_created(
        monkeypatch, "openavc.transport.mqtt.MQTTTransport.create", "mqtt",
        {"host": "192.0.2.60", **config},
    )
    assert kwargs["use_tls"] is use_tls


async def test_mqtt_keepalive_of_zero_is_the_default(monkeypatch, control_ip):
    _, kwargs = await _build_created(
        monkeypatch, "openavc.transport.mqtt.MQTTTransport.create", "mqtt",
        {"host": "192.0.2.60", "keepalive": 0},
    )
    assert kwargs["keepalive"] == 60


# --- snmp --------------------------------------------------------------------


async def test_snmp_defaults(monkeypatch, control_ip):
    driver, built = await _build_constructed(
        monkeypatch, "openavc.transport.snmp.SNMPTransport", "snmp",
        {"host": "192.0.2.70", "write_community": ""},
    )
    assert built.kwargs == {
        "host": "192.0.2.70",
        "port": 161,
        "community": "public",
        "write_community": None,
        "timeout": 2.0,
        "retries": 1,
        "on_disconnect": driver._handle_transport_disconnect,
        "inter_command_delay": 0.0,
        "name": "acme1",
    }
    assert built.opened == [{"local_addr": control_ip}]


async def test_snmp_settings_a_device_can_carry(monkeypatch, control_ip):
    _, built = await _build_constructed(
        monkeypatch, "openavc.transport.snmp.SNMPTransport", "snmp",
        {
            "host": "192.0.2.70", "port": "1161", "community": "acme-ro",
            "write_community": "acme-rw", "timeout": "5", "retries": 0,
            "inter_command_delay": 0.4,
        },
    )
    assert built.kwargs["port"] == 1161
    assert (built.kwargs["community"], built.kwargs["write_community"]) == ("acme-ro", "acme-rw")
    assert built.kwargs["timeout"] == 5.0
    assert built.kwargs["retries"] == 0
    assert built.kwargs["inter_command_delay"] == 0.4


async def test_snmp_timeout_of_zero_is_the_default(monkeypatch, control_ip):
    _, built = await _build_constructed(
        monkeypatch, "openavc.transport.snmp.SNMPTransport", "snmp",
        {"host": "192.0.2.70", "timeout": 0},
    )
    assert built.kwargs["timeout"] == 2.0


# --- what every transport shares -------------------------------------------


async def test_an_unknown_transport_is_refused():
    with pytest.raises(ValueError, match=r"^Unsupported transport type: carrier_pigeon$"):
        await _driver({"host": "192.0.2.80"})._create_transport("carrier_pigeon")


async def test_an_adapter_with_no_address_fails_before_the_transport_is_looked_up(monkeypatch):
    def no_address(host):
        raise ConnectionFaultError("The chosen adapter has no address", code="unreachable")

    monkeypatch.setattr("openavc.transport.endpoint.control_bind_address", no_address)
    with pytest.raises(ConnectionFaultError, match="no address"):
        await _driver({"host": "192.0.2.80"})._create_transport("carrier_pigeon")


async def test_the_framing_hooks_are_asked_first_for_every_transport(monkeypatch):
    asked: list[str] = []

    class _Hooked(_AcmeWidget):
        def _create_frame_parser(self):
            asked.append("frame_parser")
            return None

        def _resolve_delimiter(self):
            asked.append("delimiter")
            return b"\r"

        def local_address(self, host=None):
            asked.append("local_address")
            return None

    _constructed(monkeypatch, "openavc.transport.http_client.HTTPClientTransport")
    driver = _Hooked("acme1", {"host": "192.0.2.90"}, StateStore(), EventBus())
    await driver._create_transport("http")
    assert asked == ["frame_parser", "delimiter", "local_address"]


async def test_a_driver_frame_parser_and_kwargs_hook_reach_the_transport(monkeypatch):
    parser = object()

    class _Framed(_AcmeWidget):
        def _create_frame_parser(self):
            return parser

        def _transport_kwargs(self, transport_type, kwargs):
            kwargs["delimiter"] = None
            kwargs["timeout"] = 9.0
            return kwargs

    create = _created(monkeypatch, "openavc.transport.tcp.TCPTransport.create")
    driver = _Framed("acme1", {"host": "192.0.2.90", "port": 10}, StateStore(), EventBus())
    await driver._create_transport("tcp")
    kwargs = create.await_args.kwargs
    assert kwargs["frame_parser"] is parser
    assert kwargs["delimiter"] is None
    assert kwargs["timeout"] == 9.0
