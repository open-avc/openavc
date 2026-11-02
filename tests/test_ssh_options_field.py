"""Every device that connects over SSH is offered an SSH options field,
for connection settings only.

The setting belongs to the transport: BaseDriver reads ``extra_ssh_options``
from any SSH device's config and passes each line to ssh as ``-o``. The Add
and Edit Device forms draw only the fields in the driver list, so the field
is added there for every driver that connects over SSH, unless the driver
declares the key itself. The drivers are invented.
"""

from __future__ import annotations

import asyncio
import copy

import pytest

from openavc.api.routes.drivers import list_drivers
from openavc.core.connection_fault import INVALID_CONFIG, typed_fault_from_exc
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver
from openavc.transport.ssh import SSHTransport

SCHEMA = {
    "host": {"type": "string", "required": True, "label": "IP Address"},
    "username": {"type": "string", "default": "admin", "label": "Username"},
}


def _driver(driver_id: str, **info) -> type[BaseDriver]:
    class Acme(BaseDriver):
        DRIVER_INFO = {
            "id": driver_id, "name": "Acme Switch", "manufacturer": "Acme",
            "category": "utility", "commands": {}, "state_variables": {},
            "config_schema": copy.deepcopy(SCHEMA), **info,
        }

        async def send_command(self, command, params=None):
            return None

    return Acme


@pytest.fixture
def drivers(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", {})
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set())


def _listed(driver_id: str) -> dict:
    listed = asyncio.run(list_drivers())["drivers"]
    return next(d for d in listed if d["id"] == driver_id)["config_schema"]


def test_an_ssh_driver_is_offered_the_field(drivers):
    cls = _driver("acme_ssh_switch", transport="ssh")
    registry.register_driver(cls)
    field = _listed("acme_ssh_switch")["extra_ssh_options"]
    assert field["label"] == "SSH options"
    # A multi-line box, folded under Advanced.
    assert field["type"] == "text"
    assert field["advanced"] is True
    assert "one per line" in field["description"]
    assert "Used only over SSH" not in field["description"]
    # The driver's own fields come first and are untouched.
    assert list(_listed("acme_ssh_switch"))[:2] == ["host", "username"]
    assert "extra_ssh_options" not in cls.DRIVER_INFO["config_schema"]


def test_a_driver_that_also_speaks_telnet_says_where_the_field_applies(drivers):
    registry.register_driver(_driver("acme_dual_switch", transport="tcp", transports=["tcp", "ssh"]))
    field = _listed("acme_dual_switch")["extra_ssh_options"]
    assert field["description"].endswith("Used only over SSH.")


def test_a_connection_setting_that_offers_telnet_counts_too(drivers):
    cls = _driver("acme_choice_switch", transport="ssh")
    cls.DRIVER_INFO["config_schema"]["transport"] = {
        "type": "enum", "values": ["ssh", "tcp"], "label": "Connection",
    }
    registry.register_driver(cls)
    field = _listed("acme_choice_switch")["extra_ssh_options"]
    assert field["description"].endswith("Used only over SSH.")


def test_a_driver_with_no_ssh_is_not_offered_it(drivers):
    registry.register_driver(_driver("acme_tcp_switch", transport="tcp", transports=["tcp", "serial"]))
    assert "extra_ssh_options" not in _listed("acme_tcp_switch")


def test_a_driver_that_declares_the_field_keeps_its_own(drivers):
    own = {"type": "string", "label": "Extra ssh -o options"}
    cls = _driver("acme_own_switch", transport="ssh")
    cls.DRIVER_INFO["config_schema"]["extra_ssh_options"] = own
    registry.register_driver(cls)
    assert _listed("acme_own_switch")["extra_ssh_options"] == own


def test_a_driver_with_no_schema_is_left_to_the_keys_its_device_holds(drivers):
    # The Edit dialog shows a schema-less device's own keys instead; a
    # schema of one field would hide its address and login.
    registry.register_driver(_driver("acme_bare_switch", transport="ssh", config_schema={}))
    assert _listed("acme_bare_switch") == {}


# --- what the field may hold -------------------------------------------------
# Running a program on this computer needs the Programmer password even on an
# instance open to everyone; editing a device does not. So the field carries
# connection settings only, and anything else stops the connection before ssh
# is started, with the reason on the device card.

@pytest.mark.parametrize("option", [
    "Ciphers=+aes128-cbc,3des-cbc",
    "MACs=+hmac-md5",
    "kexalgorithms=+diffie-hellman-group-exchange-sha1",
    "HostKeyAlgorithms +ssh-dss",
    "ConnectTimeout = 5",
    "ServerAliveInterval=10",
    "BindAddress=10.20.0.6",
    "ObscureKeystrokeTiming=no",
])
def test_connection_settings_are_passed_to_ssh(option):
    argv = SSHTransport(
        "acme-switch.invalid", 22, "avc", lambda d: None, lambda: None,
        ssh_binary="/usr/bin/ssh", extra_ssh_options=[option],
    ).build_argv()
    assert argv[argv.index(option) - 1] == "-o"


@pytest.mark.parametrize("option, says", [
    ("ProxyCommand=nc %h %p", "SSH options can't include ProxyCommand."),
    ("proxycommand nc %h %p", "SSH options can't include proxycommand."),
    ("LocalCommand=touch /tmp/acme", "SSH options can't include LocalCommand."),
    ("PermitLocalCommand=yes", "SSH options can't include PermitLocalCommand."),
    ("KnownHostsCommand=/bin/true", "SSH options can't include KnownHostsCommand."),
    ("PKCS11Provider=/tmp/acme.so", "SSH options can't include PKCS11Provider."),
    ("Include=/tmp/acme.conf", "SSH options can't include Include."),
    ("Match exec true", "SSH options can't include Match."),
    ("RemoteForward=8080 localhost:8080", "SSH options can't include RemoteForward."),
    ("ForwardAgent=yes", "SSH options can't include ForwardAgent."),
    ("LogLevel=QUIET", "SSH options can't include LogLevel."),
    ("-oProxyCommand=nc %h %p", "is not one."),
    ("Ciphers", "is not one."),
    ("Ciphers=+aes128-cbc\nProxyCommand=nc %h %p", "on a line of its own."),
])
def test_anything_else_stops_the_connection_before_ssh_starts(monkeypatch, option, says):
    async def _never(*args, **kwargs):
        raise AssertionError("ssh was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _never)
    driver = _driver("acme_ssh_switch", transport="ssh")("acme_1", {
        "host": "acme-switch.invalid", "port": 22, "username": "avc",
        "extra_ssh_options": [option],
    }, StateStore(), EventBus())
    with pytest.raises(Exception) as caught:
        asyncio.run(driver.connect())
    fault = typed_fault_from_exc(caught.value)
    assert fault is not None and fault.code == INVALID_CONFIG
    assert says in fault.message


def test_what_the_field_holds_reaches_ssh_one_option_per_line(monkeypatch):
    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr("openavc.transport.ssh.SSHTransport.create", _fake_create)
    driver = _driver("acme_ssh_switch", transport="ssh")("acme_1", {
        "host": "acme-switch.invalid", "port": 22, "username": "avc",
        # As the Edit Device form saves it.
        "extra_ssh_options": "Ciphers=+aes128-cbc,3des-cbc\nMACs=+hmac-md5\n",
    }, StateStore(), EventBus())
    asyncio.run(driver.connect())
    assert seen["extra_ssh_options"] == ["Ciphers=+aes128-cbc,3des-cbc", "MACs=+hmac-md5"]
