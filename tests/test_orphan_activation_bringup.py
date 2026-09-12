"""Activating an orphaned device registers it undialed and asks for one
bring-up round — the same rule a driver reload follows, for the same reason.

Dialing inside the activation path sent a device under simulation to its REAL
address. Installing the missing driver promoted the orphan, the fresh driver
instance connected immediately from the project config, and nothing re-applied
the simulator redirect: the device then reconnect-looped against hardware that
was not there, with no recovery an integrator could guess — starting
simulation refuses ("already active") and a reconnect re-dials the real
address. The engine's bring-up round runs the simulation sync first, so with
the connect deferred the first attempt already goes to the simulator. A bare
manager with no engine behind it still dials the device itself.
"""

from typing import Any

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY

DRIVER_ID = "acme_orphan_widget"


class OrphanTCPDriver(BaseDriver):
    DRIVER_INFO = {
        "id": DRIVER_ID,
        "name": "Acme Orphan Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"host": "192.0.2.20", "port": 9998},
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


DEVICE_CFG = {
    "id": "dev1",
    "driver": DRIVER_ID,
    "name": "Widget",
    "config": {"host": "192.0.2.20", "port": 9998},
}


@pytest.fixture
def dm():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return DeviceManager(state, events)


@pytest.fixture
def unregistered_driver():
    """The driver is absent while the device is added, then installed."""
    _DRIVER_REGISTRY.pop(DRIVER_ID, None)
    yield
    _DRIVER_REGISTRY.pop(DRIVER_ID, None)


async def _orphan(dm) -> None:
    await dm.add_device(dict(DEVICE_CFG))
    assert "dev1" in dm._orphaned_devices


def _install_driver() -> None:
    _DRIVER_REGISTRY[DRIVER_ID] = OrphanTCPDriver


async def test_activation_defers_the_dial_to_the_engines_bringup_round(
    dm, unregistered_driver,
):
    """With the engine's scheduler wired, activating an orphan registers the
    device undialed and asks for a round; the round (here run by hand, as the
    engine's worker would after the simulation sync) opens it once, on
    whatever address the sync left behind."""
    await _orphan(dm)
    _install_driver()

    rounds: list[int] = []
    dm.request_bringup = lambda: rounds.append(1)

    assert await dm.retry_orphaned_device("dev1") is True

    driver = dm._devices["dev1"]
    assert driver.connect_calls == 0
    assert dm.is_connect_deferred("dev1")
    assert rounds == [1]

    # The engine's worker re-applies the simulation redirect here, then dials.
    driver.config["host"] = "127.0.0.1"
    await dm.bring_up()
    assert driver.connect_calls == 1
    assert driver.host_at_connect == ["127.0.0.1"]
    assert not dm.is_connect_deferred("dev1")


async def test_activation_without_an_engine_dials_the_device_itself(
    dm, unregistered_driver,
):
    await _orphan(dm)
    _install_driver()
    assert dm.request_bringup is None

    assert await dm.retry_orphaned_device("dev1") is True

    driver = dm._devices["dev1"]
    assert driver.connect_calls == 1
    assert not dm.is_connect_deferred("dev1")


async def test_a_caller_that_asked_for_the_deferral_owns_its_own_round(
    dm, unregistered_driver,
):
    """The engine's reconcile defers explicitly and schedules the round
    itself, so the activation must not ask for a second one."""
    await _orphan(dm)
    _install_driver()

    rounds: list[int] = []
    dm.request_bringup = lambda: rounds.append(1)

    assert await dm.retry_orphaned_device("dev1", defer_connect=True) is True

    assert dm._devices["dev1"].connect_calls == 0
    assert dm.is_connect_deferred("dev1")
    assert rounds == []


async def test_retry_all_orphans_still_reports_what_it_activated(
    dm, unregistered_driver,
):
    await _orphan(dm)
    _install_driver()
    dm.request_bringup = lambda: None

    assert await dm.retry_all_orphans() == ["dev1"]
    assert dm._orphaned_devices == {}


async def test_an_orphan_whose_driver_is_still_missing_stays_orphaned(
    dm, unregistered_driver,
):
    await _orphan(dm)

    assert await dm.retry_orphaned_device("dev1") is False
    assert "dev1" in dm._orphaned_devices
    assert not dm.is_connect_deferred("dev1")
