"""Stop Simulation hands a device back with nothing the simulator reported.

The same driver goes on to the equipment, and anything the equipment does not
report on its first connection -- a unit in standby holding its queries, a
value only a command reply sets -- used to keep the simulator's number, shown
as the equipment's own until something replaced it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.simulation import SimulationManager
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


class _AcmeMatrix(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_matrix",
        "name": "Acme Matrix",
        "transport": "tcp",
        "commands": {},
        "state_variables": {
            "outputs_reported": {"type": "integer", "label": "Outputs Reported"},
            "standby": {"type": "boolean", "label": "Standby"},
        },
        "child_entity_types": {
            "output": {
                "label": "Output",
                "id_format": {"type": "integer", "min": 1, "max": 4},
                "state_variables": {
                    "input": {"type": "integer", "label": "Routed Input"},
                },
            },
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _Devices:
    """The part of the DeviceManager the restore step drives. A reconnect
    records what the device's readings were as it began."""

    def __init__(self, *drivers: _AcmeMatrix):
        self._devices = {d.device_id: d for d in drivers}
        self.unsimulated_driver = None
        self.at_reconnect: dict[str, dict[str, Any]] = {}

    async def reconnect_device(self, device_id: str) -> None:
        driver = self._devices[device_id]
        self.at_reconnect[device_id] = {
            "outputs_reported": driver.get_state("outputs_reported"),
            "standby": driver.get_state("standby"),
            "output": driver.get_child_state("output", 1),
        }


def _simulated(state: StateStore, device_id: str) -> _AcmeMatrix:
    """A matrix as the simulator left it: connected, every reading taken."""
    driver = _AcmeMatrix(device_id, {"host": "127.0.0.1", "port": 19000}, state, EventBus())
    driver.register_child("output", 1, initial_state={"label": "Projector"})
    driver.set_state("connected", True)
    driver.set_states({"outputs_reported": 0, "standby": False})
    driver.set_child_state("output", 1, "input", 0)
    return driver


@pytest.mark.asyncio
async def test_the_simulators_readings_are_gone_before_the_equipment_connects():
    state = StateStore()
    driver = _simulated(state, "mx1")
    devices = _Devices(driver)
    sim = SimulationManager(SimpleNamespace(devices=devices, state=state))
    sim._original_configs["mx1"] = {"host": "10.0.0.9", "port": 5000}

    await sim._restore_connections()

    seen = devices.at_reconnect["mx1"]
    assert seen["outputs_reported"] is None
    assert seen["standby"] is None
    assert seen["output"]["input"] is None
    # The output stays, under the name the project gave it, so what is bound
    # to it is still there when the equipment reports.
    assert seen["output"]["label"] == "Projector"
    assert driver.is_child_registered("output", 1)
    assert driver.get_state("connected") is True


@pytest.mark.asyncio
async def test_a_device_the_simulator_never_reached_keeps_its_readings():
    """A driver with no simulator is held offline for the run, so what it
    shows was read from the equipment before the run started."""
    state = StateStore()
    driver = _simulated(state, "mx2")
    devices = _Devices(driver)
    sim = SimulationManager(SimpleNamespace(devices=devices, state=state))
    sim._sim_payloads["mx2"] = {}

    await sim._restore_connections()

    assert devices.at_reconnect["mx2"]["outputs_reported"] == 0
    assert devices.at_reconnect["mx2"]["output"]["input"] == 0
