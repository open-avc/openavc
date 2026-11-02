"""Every device that connects over SSH is offered an SSH options field.

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
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver

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
