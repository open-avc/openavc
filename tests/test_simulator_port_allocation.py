"""Regression: simulator port allocation must not leak or burn slots.

Platform test: invented driver ("acme_probe"), no real device.

Two coupled guarantees for openavc/simulator/engine.py:
  - a bind/startup failure releases the port it reserved (no permanent leak);
  - the allocator reuses a released port instead of a monotonic cursor that
    only ever advances and shrinks the usable pool until restart.
"""

import pytest

from simulator.engine import (
    PORT_RANGE_END,
    PORT_RANGE_START,
    SimulatorInfo,
    SimulatorManager,
)


class _FakeSim:
    """Minimal stand-in for a BaseSimulator whose start() can fail."""

    def __init__(self, fail: bool):
        self._fail = fail
        self.port = 0

    def set_child_entities(self, _children):
        pass

    def add_change_listener(self, _listener):
        pass

    async def start(self, port):
        self.port = port
        if self._fail:
            raise OSError("address already in use")

    async def stop(self):
        pass


def _register(mgr: SimulatorManager, sim: _FakeSim) -> None:
    mgr._available["acme_probe"] = SimulatorInfo(
        driver_id="acme_probe",
        name="Acme Probe",
        category="test",
        transport="tcp",
        default_port=0,
        source="python",
    )
    mgr._create_instance = lambda info, device_id, config: sim


async def test_failed_start_releases_reserved_port():
    mgr = SimulatorManager()
    _register(mgr, _FakeSim(fail=True))

    with pytest.raises(OSError):
        await mgr.start_device("acme_probe", "dev1")

    # The reserved port is returned to the pool and no ghost instance lingers.
    assert mgr._allocated_ports == set()
    assert "dev1" not in mgr._instances
    # ...so the very next allocation hands the same slot back out.
    assert mgr._allocate_port() == PORT_RANGE_START


def test_allocate_port_reuses_released_slot():
    mgr = SimulatorManager()
    p0 = mgr._allocate_port()
    mgr._allocated_ports.add(p0)
    p1 = mgr._allocate_port()
    mgr._allocated_ports.add(p1)
    assert (p0, p1) == (PORT_RANGE_START, PORT_RANGE_START + 1)

    # Releasing the first slot must make it reusable; a monotonic cursor would
    # skip past it and return PORT_RANGE_START + 2.
    mgr._allocated_ports.discard(p0)
    assert mgr._allocate_port() == p0


def test_allocate_port_exhausted_raises():
    mgr = SimulatorManager()
    mgr._allocated_ports = set(range(PORT_RANGE_START, PORT_RANGE_END + 1))
    with pytest.raises(RuntimeError):
        mgr._allocate_port()
