"""A driver installed after the simulator started can still be simulated.

Platform test: invented driver ("acme_latecomer"), no real device.

The driver directories are walked once, in the server's lifespan. Installing
a missing driver mid-session writes it into driver_repo — which is one of the
paths that was walked, but the walk is long over — so the device it unblocks
asked for a simulator the manager did not know about, was refused, and was
left dialing its real address. That is the install-a-missing-driver first-run
flow with the demo posture switched on, so the manager re-scans before
refusing an unknown driver.
"""

import pytest

from openavc.simulator.engine import SimulatorManager


class _FakeSim:
    """Enough of a BaseSimulator to be started on a port."""

    def __init__(self):
        self.port = 0

    def set_child_entities(self, _children):
        pass

    def add_change_listener(self, _listener):
        pass

    async def start(self, port):
        self.port = port

    async def stop(self):
        pass


@pytest.fixture
def all_ports_bindable(monkeypatch):
    monkeypatch.setattr(
        SimulatorManager, "_port_is_bindable", lambda self, port: True
    )


def _write_driver(directory, driver_id: str) -> None:
    (directory / f"{driver_id}.avcdriver").write_text(
        f"id: {driver_id}\n"
        f"name: Acme Latecomer\n"
        "manufacturer: Acme\n"
        "category: utility\n"
        "transport: tcp\n"
        "default_config:\n"
        "  port: 4001\n",
        encoding="utf-8",
    )


async def test_a_driver_installed_after_the_scan_is_found(
    tmp_path, all_ports_bindable,
):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    mgr = SimulatorManager()
    mgr.discover([str(repo)])
    assert "acme_latecomer" not in mgr._available

    # What installing a community driver does: write it into a path that was
    # already scanned, long after the scan.
    _write_driver(repo, "acme_latecomer")

    sim = _FakeSim()
    mgr._create_instance = lambda info, device_id, config: sim
    started = await mgr.start_device("acme_latecomer", "dev1")

    assert started is sim
    assert "dev1" in mgr._instances
    assert "acme_latecomer" in mgr._available


async def test_a_driver_that_is_nowhere_is_still_refused(
    tmp_path, all_ports_bindable,
):
    """The rescan is a second look, not an excuse to succeed."""
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    mgr = SimulatorManager()
    mgr.discover([str(repo)])

    with pytest.raises(ValueError, match="No simulator available"):
        await mgr.start_device("acme_absent", "dev1")


async def test_a_manager_that_never_scanned_anything_is_unchanged(
    all_ports_bindable,
):
    """No paths recorded means no rescan to make — the refusal is the same
    one it always was, not an AttributeError on the way to it."""
    mgr = SimulatorManager()

    with pytest.raises(ValueError, match="No simulator available"):
        await mgr.start_device("acme_absent", "dev1")


async def test_rescan_does_not_disturb_a_running_instance(
    tmp_path, all_ports_bindable,
):
    """discover() rebuilds the available map; the instances already running
    hold their own SimulatorInfo and must survive it."""
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    _write_driver(repo, "acme_latecomer")
    mgr = SimulatorManager()
    mgr.discover([str(repo)])

    first = _FakeSim()
    mgr._create_instance = lambda info, device_id, config: first
    await mgr.start_device("acme_latecomer", "dev1")

    _write_driver(repo, "acme_second")
    second = _FakeSim()
    mgr._create_instance = lambda info, device_id, config: second
    await mgr.start_device("acme_second", "dev2")

    assert mgr._instances["dev1"] is first
    assert mgr._instances["dev2"] is second
    assert first.port != second.port
