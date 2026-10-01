"""A pinned control interface binds a driver's outgoing socket to that
adapter's address, except for a device on this machine: loopback cannot be
reached from an adapter's address (Windows refuses the connect), and a
simulated device is always on 127.0.0.1."""

from typing import Any

import pytest

import openavc.system_config as system_config
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport import tcp


class _Pinned:
    def get(self, section: str, key: str) -> Any:
        assert (section, key) == ("network", "control_interface")
        return "192.0.2.77"


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "category": "utility",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.mark.parametrize(
    ("host", "bound"),
    [
        ("10.0.0.5", ("192.0.2.77", 0)),
        ("127.0.0.1", None),
        ("localhost", None),
        ("::1", None),
    ],
)
async def test_the_pin_binds_every_device_but_one_on_this_machine(monkeypatch, host, bound):
    monkeypatch.setattr(system_config, "get_system_config", lambda: _Pinned())
    seen: dict[str, Any] = {}

    async def fake_create(cls, *args: Any, **kwargs: Any) -> object:
        seen["local_addr"] = kwargs.get("local_addr")
        return object()

    monkeypatch.setattr(tcp.TCPTransport, "create", classmethod(fake_create))
    driver = _AcmeWidget("w1", {"host": host, "port": 5000}, StateStore(), EventBus())
    await driver._create_transport("tcp")
    assert seen["local_addr"] == bound
