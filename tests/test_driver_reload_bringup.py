"""A driver reload re-adds its devices the way a project save does: registered
but not dialed, then one bring-up round.

Dialing inside reload_driver sent a device under simulation to its REAL
address on every driver update: the new driver instance was built from the
project config, connected at once, and only afterwards could the simulation
re-apply its redirect (and only if something asked it to). The engine's
bring-up round runs the simulation sync first, so with the connect deferred
the first attempt already goes to the simulator. A bare manager with no engine
still dials the devices itself, so nothing behind it changes.
"""

import asyncio
from typing import Any

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY


class ReloadTCPDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_reload_widget",
        "name": "Acme Reload Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"host": "192.0.2.10", "port": 9999},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self.connect_calls = 0
        self.host_at_connect: list[str] = []

    async def connect(self):
        self.connect_calls += 1
        self.host_at_connect.append(str(self.config.get("host")))
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self):
        self._connected = False
        self.state.set(f"device.{self.device_id}.connected", False, source="driver")

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def stop_polling(self):
        return None


_DRIVER_REGISTRY["acme_reload_widget"] = ReloadTCPDriver

DEVICE_CFG = {
    "id": "dev1",
    "driver": "acme_reload_widget",
    "name": "Widget",
    "config": {"host": "192.0.2.10", "port": 9999},
}


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def dm(core):
    state, events = core
    return DeviceManager(state, events)


async def test_reload_defers_the_dial_to_the_engines_bringup_round(dm, core):
    """With the engine's scheduler wired, reload_driver registers the new
    instance undialed and asks for a round; the round (here run by hand, as
    the engine's worker would after the simulation sync) opens it once."""
    await dm.add_device(DEVICE_CFG)
    old = dm._devices["dev1"]
    assert old.connect_calls == 1

    rounds: list[int] = []
    dm.request_bringup = lambda: rounds.append(1)

    reconnected = await dm.reload_driver("acme_reload_widget")

    assert reconnected == ["dev1"]
    new = dm._devices["dev1"]
    assert new is not old
    assert new.connect_calls == 0
    assert dm.is_connect_deferred("dev1")
    assert rounds == [1]

    # The engine's worker re-applies a simulation redirect here, then dials.
    new.config["host"] = "127.0.0.1"
    await dm.bring_up()
    assert new.connect_calls == 1
    assert new.host_at_connect == ["127.0.0.1"]
    assert not dm.is_connect_deferred("dev1")


async def test_reload_without_an_engine_dials_the_devices_itself(dm, core):
    await dm.add_device(DEVICE_CFG)
    assert dm.request_bringup is None

    await dm.reload_driver("acme_reload_widget")

    new = dm._devices["dev1"]
    assert new.connect_calls == 1
    assert not dm.is_connect_deferred("dev1")
    assert core[0].get("device.dev1.connected") is True


async def test_reload_keeps_a_paused_device_paused_and_undialed(dm, core):
    """A pause survives the instance swap as before: the paused device is
    neither dialed by the reload nor by the bring-up round that follows."""
    await dm.add_device(DEVICE_CFG)
    await dm.pause_device("dev1", ttl=30)
    dm.request_bringup = lambda: None

    await dm.reload_driver("acme_reload_widget")
    await dm.bring_up()

    new = dm._devices["dev1"]
    assert new.connect_calls == 0
    assert core[0].get("device.dev1.paused") is True
    assert not dm.is_connect_deferred("dev1")

    dm._cancel_pause_expiry("dev1")
    await asyncio.sleep(0)
