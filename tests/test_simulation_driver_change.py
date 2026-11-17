"""A driver change reaches a running simulation.

Platform test: invented drivers (``acme_widget``, ``acme_gadget``), no real
device.

The simulator reads its driver folders once, when it starts. A driver saved,
imported or updated while a simulation runs used to reach the real devices
and never the simulated ones: a simulated device kept answering with the old
driver until Stop and Start, and one whose driver file had been renamed could
not be started again at all. Now the server tells the simulation: the
simulator reads its folders again, and each simulated device on the changed
driver is started again from the file there now, in the next sync.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.simulator import api as sim_api
from openavc.simulator.engine import SimulatorManager
from tests.test_simulation_sync import (
    _active_manager,
    _FakeDeviceManager,
    _FakeDriver,
    _FakeResp,
    _install_fake_aiohttp,
)

_YAML = (
    "id: acme_widget\n"
    "name: {name}\n"
    "manufacturer: Acme\n"
    "category: utility\n"
    "transport: tcp\n"
    "default_config:\n"
    "  port: 4001\n"
    "commands:\n"
    "  power_on:\n"
    "    label: Power On\n"
    "    send: \"{send}\\r\"\n"
)

_SIM = (
    "from openavc.simulator.tcp_simulator import TCPSimulator\n\n\n"
    "class AcmeGadgetSimulator(TCPSimulator):\n"
    "    SIMULATOR_INFO = {{'driver_id': 'acme_gadget', 'name': {name!r}, 'transport': 'tcp'}}\n"
)


@pytest.fixture
def simulator(tmp_path):
    manager = SimulatorManager()
    app = FastAPI()
    app.include_router(sim_api.router)
    previous = sim_api._manager
    sim_api.set_manager(manager)
    yield manager, TestClient(app), tmp_path
    sim_api._manager = previous


def _names(client: TestClient) -> dict[str, str]:
    return {s["driver_id"]: s["name"] for s in client.get("/api/available").json()["simulators"]}


# --- the simulator reads its folders again -----------------------------------


def test_a_renamed_driver_file_is_the_one_the_next_start_reads(simulator):
    manager, client, folder = simulator
    (folder / "acme widget (2).avcdriver").write_text(
        _YAML.format(name="Acme Widget", send="PWR ON"), encoding="utf-8"
    )
    manager.discover([str(folder)])

    (folder / "acme widget (2).avcdriver").unlink()
    (folder / "acme_widget.avcdriver").write_text(
        _YAML.format(name="Acme Widget Two", send="POWER 1"), encoding="utf-8"
    )
    assert client.post("/api/drivers/rescan").status_code == 200

    info = manager._simulator_for("acme_widget", "d1")
    assert info.avcdriver_path == folder / "acme_widget.avcdriver"
    assert _names(client)["acme_widget"] == "Acme Widget Two Simulator"


def test_a_changed_python_simulator_is_imported_again(simulator):
    manager, client, folder = simulator
    (folder / "acme_gadget_sim.py").write_text(_SIM.format(name="Gadget One"), encoding="utf-8")
    manager.discover([str(folder)])
    assert _names(client)["acme_gadget"] == "Gadget One"

    # A new length: a file the same size, written in the same second, is
    # served from its bytecode, which the driver change's swap removes.
    (folder / "acme_gadget_sim.py").write_text(
        _SIM.format(name="Gadget Number Two"), encoding="utf-8"
    )
    client.post("/api/drivers/rescan")

    assert _names(client)["acme_gadget"] == "Gadget Number Two"


def test_a_rescan_before_any_scan_forgets_nothing(simulator):
    manager, client, _folder = simulator
    assert client.post("/api/drivers/rescan").json() == {"simulators": 0}
    assert manager.rescan() == {}


# --- the server starts the simulated devices on the driver again -------------


def _two_devices() -> tuple[_FakeDeviceManager, dict[str, _FakeDriver]]:
    drivers = {
        "widget": _FakeDriver("127.0.0.1", 19001),
        "other": _FakeDriver("127.0.0.1", 19002),
    }
    dm = _FakeDeviceManager(
        device_configs={
            "widget": {"driver": "acme_widget", "config": {}},
            "other": {"driver": "acme_other", "config": {}},
        },
        devices=drivers,
    )
    return dm, drivers


@pytest.mark.asyncio
async def test_a_changed_driver_restarts_only_its_simulated_devices(monkeypatch):
    dm, drivers = _two_devices()
    mgr = _active_manager(dm)
    mgr._sim_ports = {"widget": 19001, "other": 19002}
    mgr._sim_payloads = {
        did: mgr._device_sim_payload(did, dm.get_device_config(did)) for did in ("widget", "other")
    }

    def handler(method, url, body):
        if url.endswith("/drivers/rescan"):
            return _FakeResp(200, {"simulators": 2})
        if url.endswith("/widget/stop"):
            return _FakeResp(200)
        if url.endswith("/widget/start"):
            return _FakeResp(200, {"port": 19007})
        raise AssertionError(url)

    calls = _install_fake_aiohttp(monkeypatch, handler)
    assert await mgr.drivers_changed(["acme_widget"]) is True
    assert [u for _, u in calls] == ["http://localhost:19500/api/drivers/rescan"]

    await mgr.sync()

    assert [u.rsplit("/api/", 1)[1] for _, u in calls[1:]] == [
        "devices/widget/stop", "devices/widget/start",
    ]
    assert mgr._sim_ports == {"widget": 19007, "other": 19002}
    assert drivers["widget"].config["port"] == 19007
    assert dm.reconnected == ["widget"]

    # Started again once: the next sync leaves it alone.
    await mgr.sync()
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_a_restart_whose_stop_fails_is_tried_again(monkeypatch):
    """The old instance still runs, so the start is refused as already
    simulated and that instance is kept; the next sync starts it again."""
    dm, _drivers = _two_devices()
    mgr = _active_manager(dm)
    mgr._sim_ports = {"widget": 19001}
    mgr._selection = ["widget"]
    stops = iter([500, 200])
    starts = iter([_FakeResp(400), _FakeResp(200, {"port": 19007})])

    def handler(method, url, body):
        if url.endswith("/drivers/rescan"):
            return _FakeResp(200)
        if url.endswith("/stop"):
            return _FakeResp(next(stops))
        if url.endswith("/start"):
            return next(starts)
        return _FakeResp(200, {"devices": [{"device_id": "widget", "port": 19001}]})

    calls = _install_fake_aiohttp(monkeypatch, handler)
    await mgr.drivers_changed(["acme_widget"])
    await mgr.sync()
    assert mgr._sim_ports == {"widget": 19001}
    assert mgr._driver_restarts == {"widget"}
    await mgr.sync()

    assert [u.rsplit("/", 1)[1] for _, u in calls] == [
        "rescan", "stop", "start", "devices", "stop", "start",
    ]
    assert mgr._sim_ports == {"widget": 19007}
    assert mgr._driver_restarts == set()


@pytest.mark.asyncio
async def test_a_simulator_that_does_not_answer_still_restarts_the_device(monkeypatch):
    dm, _drivers = _two_devices()
    mgr = _active_manager(dm)
    mgr._sim_ports = {"widget": 19001}
    mgr._selection = ["widget"]

    def handler(method, url, body):
        if url.endswith("/drivers/rescan"):
            raise ConnectionError("refused")
        if url.endswith("/stop"):
            return _FakeResp(200)
        return _FakeResp(200, {"port": 19007})

    calls = _install_fake_aiohttp(monkeypatch, handler)
    assert await mgr.drivers_changed(["acme_widget"]) is True
    await mgr.sync()

    assert [u.rsplit("/", 1)[1] for _, u in calls] == ["rescan", "stop", "start"]


@pytest.mark.asyncio
async def test_a_device_refused_a_simulator_asks_for_a_sync_on_its_driver(monkeypatch):
    dm, _drivers = _two_devices()
    mgr = _active_manager(dm)
    mgr._sim_ports = {"other": 19002}
    calls = _install_fake_aiohttp(monkeypatch, lambda *a: _FakeResp(200))

    assert await mgr.drivers_changed(["acme_widget"]) is True
    assert await mgr.drivers_changed(["acme_nothing"]) is False
    assert mgr._driver_restarts == set()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_nothing_is_asked_while_no_simulation_runs(monkeypatch):
    dm, _drivers = _two_devices()
    mgr = _active_manager(dm)
    mgr._run.redirected = False
    calls = _install_fake_aiohttp(monkeypatch, lambda *a: _FakeResp(200))

    assert await mgr.drivers_changed(["acme_widget"]) is False
    assert calls == []
