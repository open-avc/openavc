"""Regression: simulator port allocation must not leak or burn slots.

Platform test: invented driver ("acme_probe"), no real device.

Two coupled guarantees for openavc/simulator/engine.py:
  - a bind/startup failure releases the port it reserved (no permanent leak);
  - the allocator reuses a released port instead of a monotonic cursor that
    only ever advances and shrinks the usable pool until restart.
"""

import pytest

from openavc.simulator.engine import (
    PORT_RANGE_END,
    PORT_RANGE_START,
    SimulatorInfo,
    SimulatorManager,
)


class _FakeSim:
    """Minimal stand-in for a BaseSimulator whose start() can fail.

    ``fail`` refuses every port; ``unbindable`` refuses only the listed ones,
    which is what a reserved block looks like from in here.
    """

    def __init__(self, fail: bool = False, unbindable: set[int] | None = None):
        self._fail = fail
        self._unbindable = unbindable or set()
        self.port = 0
        self.attempts: list[int] = []

    def set_child_entities(self, _children):
        pass

    def add_change_listener(self, _listener):
        pass

    async def start(self, port):
        self.attempts.append(port)
        self.port = port
        if self._fail or port in self._unbindable:
            raise OSError("address already in use")

    async def stop(self):
        pass


@pytest.fixture
def all_ports_bindable(monkeypatch):
    """Take the machine's real port availability out of the question.

    The allocator screens candidates by trying to bind them, so on a box that
    is running a simulator on the bottom of the range these tests would be
    asserting facts about the box. They are about the allocator.
    """
    monkeypatch.setattr(
        SimulatorManager, "_port_is_bindable", lambda self, port: True
    )


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


async def test_failed_start_releases_reserved_port(all_ports_bindable):
    mgr = SimulatorManager()
    _register(mgr, _FakeSim(fail=True))

    with pytest.raises(OSError):
        await mgr.start_device("acme_probe", "dev1")

    # The reserved port is returned to the pool and no ghost instance lingers.
    assert mgr._allocated_ports == set()
    assert "dev1" not in mgr._instances
    # ...so the very next allocation hands the same slot back out.
    assert mgr._allocate_port() == PORT_RANGE_START


@pytest.mark.asyncio
async def test_one_device_moves_past_a_port_that_will_not_bind(all_ports_bindable):
    """A port that passes the probe and then fails the real bind is a race, and
    a single attempt cannot survive it."""
    sim = _FakeSim(unbindable={PORT_RANGE_START, PORT_RANGE_START + 1})
    mgr = SimulatorManager()
    _register(mgr, sim)

    await mgr.start_device("acme_probe", "dev1")

    assert sim.attempts == [
        PORT_RANGE_START,
        PORT_RANGE_START + 1,
        PORT_RANGE_START + 2,
    ], "each retry must step to a new port, not repeat the one that just failed"
    assert mgr._instances["dev1"].port == PORT_RANGE_START + 2
    # Only the winner stays reserved.
    assert mgr._allocated_ports == {PORT_RANGE_START + 2}


@pytest.mark.asyncio
async def test_an_unusable_base_port_does_not_take_every_device_down_with_it():
    """The regression this whole change exists for.

    A failed start releases its port (deliberately -- a transient collision
    must not burn a slot). The allocator then handed the next device the lowest
    free port, which was the same dead number, and it failed the same way. On a
    Windows box where Hyper-V had reserved the bottom of the range, that turned
    one bad port into a simulator with no devices in it and eleven identical
    tracebacks.
    """
    reserved = {PORT_RANGE_START, PORT_RANGE_START + 1, PORT_RANGE_START + 2}
    mgr = SimulatorManager()
    # The probe now screens these out before anything is attempted.
    mgr._port_is_bindable = lambda port: port not in reserved

    started = []
    for i in range(4):
        sim = _FakeSim(unbindable=reserved)
        _register(mgr, sim)
        await mgr.start_device("acme_probe", f"dev{i}")
        started.append(mgr._instances[f"dev{i}"].port)

    assert len(started) == 4, "every device must get a port"
    assert not (set(started) & reserved), "no device may be handed a reserved port"
    assert len(set(started)) == 4, "and no two devices may share one"
    assert started == [
        PORT_RANGE_START + 3,
        PORT_RANGE_START + 4,
        PORT_RANGE_START + 5,
        PORT_RANGE_START + 6,
    ]


def test_allocate_port_skips_what_it_cannot_bind():
    """The probe is a bind, not a connect, and this is why it matters.

    A reserved port has nobody listening on it and still refuses to be bound,
    so a connect probe calls it free and hands it straight out.
    """
    mgr = SimulatorManager()
    mgr._port_is_bindable = lambda port: port >= PORT_RANGE_START + 5
    assert mgr._allocate_port() == PORT_RANGE_START + 5


def test_allocate_port_reuses_released_slot(all_ports_bindable):
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


def test_allocate_port_exhausted_raises(all_ports_bindable):
    mgr = SimulatorManager()
    mgr._allocated_ports = set(range(PORT_RANGE_START, PORT_RANGE_END + 1))
    with pytest.raises(RuntimeError):
        mgr._allocate_port()
