"""Why the simulator could not run a device, said by the simulator itself.

Platform test: invented driver ("acme_widget"), no real device.

The simulator alone knows why a device it was asked to run is not running: its
driver has no simulator, the simulator could not be built from the driver, or
it was built and could not start. Each refusal is kept with its cause and the
error, answered on the start route and listed beside the running devices, so
the server can tell the integrator which one it was.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.core.connection_fault import SimulatorCause
from openavc.simulator import api as sim_api
from openavc.simulator.engine import SimulatorManager, SimulatorRefusal

_DRIVER = (
    "id: acme_widget\n"
    "name: Acme Widget\n"
    "manufacturer: Acme\n"
    "category: utility\n"
    "transport: tcp\n"
    "default_config:\n"
    "  port: 4001\n"
    "state_variables:\n"
    "  power:\n"
    "    type: boolean\n"
    "    label: Power\n"
    "commands:\n"
    "  power_on:\n"
    "    label: Power On\n"
    "    send: \"PWR1\\r\"\n"
)


@pytest.fixture
def all_ports_bindable(monkeypatch):
    monkeypatch.setattr(SimulatorManager, "_port_is_bindable", lambda self, port: True)


def _manager(tmp_path, driver_text: str = _DRIVER) -> SimulatorManager:
    (tmp_path / "acme_widget.avcdriver").write_text(driver_text, encoding="utf-8")
    manager = SimulatorManager()
    manager.discover([str(tmp_path)])
    return manager


def _breaks_when_built(manager: SimulatorManager) -> SimulatorManager:
    """The simulator's builder raises, as a driver it cannot read makes it do."""

    def build(info, device_id, config):
        raise TypeError("'NoneType' object is not iterable")

    manager._create_instance = build
    return manager


async def _refusal(manager: SimulatorManager, driver_id: str = "acme_widget") -> SimulatorRefusal:
    with pytest.raises(SimulatorRefusal) as refused:
        await manager.start_device(driver_id, "dev1")
    return refused.value


async def test_a_simulator_that_cannot_be_built_says_so_with_its_error(tmp_path, all_ports_bindable):
    manager = _breaks_when_built(_manager(tmp_path))
    refusal = await _refusal(manager)
    assert refusal.cause == SimulatorCause.BUILD_FAILED
    assert refusal.detail.startswith("TypeError: ")
    assert "NoneType" in refusal.detail
    assert manager.list_refusals() == [refusal]


async def test_a_driver_with_no_simulator_says_it_has_none(tmp_path, all_ports_bindable):
    manager = _manager(tmp_path)
    refusal = await _refusal(manager, "acme_absent")
    assert refusal.cause == SimulatorCause.MISSING
    assert refusal.driver_id == "acme_absent"


async def test_a_sim_file_that_fails_to_import_is_a_build_failure_not_a_missing_simulator(
    tmp_path, all_ports_bindable,
):
    (tmp_path / "acme_gadget_sim.py").write_text(
        "raise RuntimeError('the gadget table is missing')\n", encoding="utf-8",
    )
    manager = _manager(tmp_path)
    refusal = await _refusal(manager, "acme_gadget")
    assert refusal.cause == SimulatorCause.BUILD_FAILED
    assert refusal.detail == "RuntimeError: the gadget table is missing"


async def test_a_simulator_that_cannot_start_says_so_with_its_error(tmp_path, monkeypatch):
    monkeypatch.setattr(SimulatorManager, "_port_is_bindable", lambda self, port: False)
    manager = _manager(tmp_path)
    refusal = await _refusal(manager)
    assert refusal.cause == SimulatorCause.START_FAILED
    assert "No available ports" in refusal.detail


async def test_an_error_with_no_text_is_named_by_its_type(tmp_path, all_ports_bindable):
    manager = _manager(tmp_path)

    def build(info, device_id, config):
        raise KeyError()

    manager._create_instance = build
    refusal = await _refusal(manager)
    assert refusal.cause == SimulatorCause.BUILD_FAILED
    assert refusal.detail == "KeyError"


async def test_a_later_start_clears_the_refusal(tmp_path, all_ports_bindable):
    manager = _breaks_when_built(_manager(tmp_path))
    await _refusal(manager)
    del manager._create_instance  # the driver is fixed: the real builder again
    started = await manager.start_device("acme_widget", "dev1")
    try:
        assert manager.list_refusals() == []
    finally:
        await started.stop()


# ── The simulator API ───────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, all_ports_bindable, monkeypatch):
    manager = _breaks_when_built(_manager(tmp_path))
    monkeypatch.setattr(sim_api, "_manager", manager)
    app = FastAPI()
    app.include_router(sim_api.router)
    return TestClient(app)


def test_the_start_route_answers_the_refusal_with_its_cause(api):
    res = api.post("/api/devices/dev1/start", json={"driver_id": "acme_widget"})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["cause"] == SimulatorCause.BUILD_FAILED
    assert detail["detail"].startswith("TypeError: ")


def test_the_device_list_names_each_device_it_could_not_run(api):
    api.post("/api/devices/dev1/start", json={"driver_id": "acme_widget"})
    api.post("/api/devices/dev2/start", json={"driver_id": "acme_absent"})
    listed = api.get("/api/devices").json()
    assert listed["devices"] == []
    assert {(r["device_id"], r["driver_id"], r["cause"]) for r in listed["refused"]} == {
        ("dev1", "acme_widget", SimulatorCause.BUILD_FAILED),
        ("dev2", "acme_absent", SimulatorCause.MISSING),
    }


async def test_a_launch_starts_the_rest_and_keeps_why_each_refused_one_did_not(tmp_path, all_ports_bindable):
    manager = _manager(tmp_path)
    await manager.start_requested([
        {"device_id": "dev1", "driver_id": "acme_widget"},
        {"device_id": "dev2", "driver_id": "acme_absent"},
    ])
    try:
        assert [sim.device_id for sim in manager.list_instances()] == ["dev1"]
        assert [(r.device_id, r.cause) for r in manager.list_refusals()] == [("dev2", SimulatorCause.MISSING)]
    finally:
        await manager.stop_all()
