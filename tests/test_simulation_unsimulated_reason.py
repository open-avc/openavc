"""A device a running simulation could not simulate says why on its card.

Platform test: invented driver ("acme_widget"), no real device.

That the device is not simulated is worked out from the run (selected, never
given a port), so no refusal is missed. Why is recorded where the run hears it:
the simulator's list of devices it could not run at a start, its answer to the
start of a device a save added, or the server's own failure to reach it. The
card, a refused command and a problem report all read that record, and only a
driver that really has no simulator is told to add one.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import openavc.core.simulation as simulation
from openavc.core.connection_fault import (
    NO_SIMULATOR,
    SimulatorCause,
    SimulatorGap,
    no_simulator_fault,
)
from tests.test_simulation_hardware_isolation import Hardware, _Pipe, rig  # noqa: F401
from tests.test_simulation_sync import _FakeResp, _install_fake_aiohttp

_BUILD_ERROR = "TypeError: 'NoneType' object is not iterable"


def _refused(cause: str, detail: str = "", device_id: str = "missing") -> dict:
    return {"device_id": device_id, "driver_id": "acme_widget", "cause": cause, "detail": detail}


@pytest.fixture
def start_with(rig, monkeypatch):  # noqa: F811
    """Start a run whose simulator answers its device list with ``answer``."""
    manager, _ = rig
    monkeypatch.setattr(simulation, "_choose_ui_port", lambda preferred: preferred)
    monkeypatch.setattr(simulation, "_PORT_QUERY_RETRY_S", 0)
    stdin = SimpleNamespace(write=lambda data: None, drain=AsyncMock(), close=lambda: None)
    process = SimpleNamespace(stdin=stdin, stdout=_Pipe(), stderr=_Pipe(), returncode=None)
    monkeypatch.setattr(simulation.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    async def start(device_ids, answer):
        engine = SimpleNamespace(devices=manager, state=manager.state, project=object())
        simulator = simulation.SimulationManager(engine)
        simulator._await_simulator_ready = AsyncMock()
        _install_fake_aiohttp(monkeypatch, lambda m, u, j: _FakeResp(200, json_data=answer))
        await simulator._do_start(device_ids)
        return simulator

    yield start
    simulation.set_running_ui_port(None)


def _card(manager) -> str:
    return manager.state.get("device.missing.offline_detail")


@pytest.mark.asyncio
async def test_a_simulator_that_could_not_be_built_is_not_told_to_add_one(rig, start_with):  # noqa: F811
    manager, _ = rig
    await start_with(["missing"], {"devices": [], "refused": [_refused(SimulatorCause.BUILD_FAILED, _BUILD_ERROR)]})
    assert manager.state.get("device.missing.offline_reason") == NO_SIMULATOR
    card = _card(manager)
    assert "could not be built" in card and _BUILD_ERROR in card
    assert "_sim.py" not in card and "has no simulator" not in card
    assert manager._devices["missing"].connects == 0


@pytest.mark.asyncio
async def test_a_driver_with_no_simulator_is_still_told_to_add_one(rig, start_with):  # noqa: F811
    manager, _ = rig
    await start_with(["missing"], {"devices": [], "refused": [_refused(SimulatorCause.MISSING)]})
    card = _card(manager)
    assert "'acme_widget' has no simulator" in card and "_sim.py" in card


@pytest.mark.asyncio
async def test_a_simulator_that_could_not_start_says_so(rig, start_with):  # noqa: F811
    manager, _ = rig
    await start_with(["missing"], {"devices": [], "refused": [
        _refused(SimulatorCause.START_FAILED, "RuntimeError: No available ports in range 19000-19499"),
    ]})
    card = _card(manager)
    assert "could not start" in card and "No available ports" in card
    assert "_sim.py" not in card


@pytest.mark.asyncio
async def test_a_device_the_answer_does_not_mention_claims_no_cause(rig, start_with):  # noqa: F811
    manager, _ = rig
    await start_with(["missing"], {"devices": []})
    card = _card(manager)
    assert "has no simulator" not in card and "_sim.py" not in card
    assert "stop simulation" in card.lower()


@pytest.mark.asyncio
async def test_a_refused_command_reads_the_same_sentence(rig, start_with):  # noqa: F811
    manager, _ = rig
    await start_with(["missing"], {"devices": [], "refused": [_refused(SimulatorCause.BUILD_FAILED, _BUILD_ERROR)]})
    with pytest.raises(ConnectionError) as refused:
        await manager.send_command("missing", "wake")
    assert str(refused.value) == _card(manager)


# A device a save added: the start route's own answer is the record.


async def _add(rig, response=None, post=None):  # noqa: F811
    manager, simulator = rig
    manager._devices["new"] = Hardware(manager.state, "new")
    manager._device_configs["new"] = {"id": "new", "driver": "acme_widget", "config": {}}
    simulator._adopt_existing_sim = AsyncMock(return_value=False)
    post = post or AsyncMock(return_value=response)
    await simulator._sync_add_device(SimpleNamespace(post=post), "http://127.0.0.1:19500", "new")
    return simulator.simulation_gap("new")


@pytest.mark.asyncio
async def test_an_added_device_keeps_the_simulators_refusal(rig):  # noqa: F811
    response = _FakeResp(422, json_data={"detail": {"cause": SimulatorCause.BUILD_FAILED, "detail": _BUILD_ERROR}})
    gap = await _add(rig, response)
    assert gap == SimulatorGap("acme_widget", SimulatorCause.BUILD_FAILED, _BUILD_ERROR)
    manager, _ = rig
    assert "could not be built" in manager.state.get("device.new.offline_detail")


@pytest.mark.parametrize("response, post, heard", [
    (_FakeResp(500, text_data="Could not start the simulator for 'new'."), None, "Could not start the simulator"),
    (_FakeResp(200, json_data={"port": 0}), None, "no port"),
    (None, AsyncMock(side_effect=TimeoutError()), "did not answer"),
    (_FakeResp(400, text_data="Device 'new' is already simulated"), None, "already simulated"),
])
@pytest.mark.asyncio
async def test_an_added_device_the_simulator_did_not_run_says_what_was_heard(
    rig, response, post, heard,  # noqa: F811
):
    gap = await _add(rig, response, post)
    assert gap.cause == SimulatorCause.REFUSED
    assert heard in gap.detail


@pytest.mark.asyncio
async def test_a_device_that_gets_its_simulator_later_forgets_the_refusal(rig):  # noqa: F811
    response = _FakeResp(422, json_data={"detail": {"cause": SimulatorCause.BUILD_FAILED, "detail": _BUILD_ERROR}})
    await _add(rig, response)
    assert await _add(rig, _FakeResp(200, json_data={"port": 19007})) is None
    _, simulator = rig
    assert "new" not in simulator._refusals


# The sentence itself.


def test_product_hardware_hears_why_and_only_to_stop_simulation():
    built = no_simulator_fault(SimulatorGap("acme_widget", SimulatorCause.BUILD_FAILED, _BUILD_ERROR), product=True)
    assert _BUILD_ERROR in built.message and "Stop simulation" in built.message
    assert "Fix the driver" not in built.message and "acme_widget" not in built.message
    assert no_simulator_fault(SimulatorGap("acme_widget", SimulatorCause.MISSING), product=True).message == (
        "This device has no simulator. Stop simulation to control it."
    )


def test_a_long_error_is_cut_to_one_short_line():
    detail = "ValueError: " + "x" * 600 + "\nTraceback (most recent call last):\n  File ..."
    message = no_simulator_fault(SimulatorGap("acme_widget", SimulatorCause.BUILD_FAILED, detail)).message
    assert "Traceback" not in message and "\n" not in message
    assert len(message) < 500


# A problem report says why too.


@pytest.mark.asyncio
async def test_a_problem_report_records_why_the_device_was_not_simulated(rig, start_with):  # noqa: F811
    from openavc.support.problem_report import simulation_facts

    manager, _ = rig
    simulator = await start_with(["missing"], {"devices": [], "refused": [_refused(SimulatorCause.BUILD_FAILED, _BUILD_ERROR)]})
    engine = SimpleNamespace(devices=manager, state=manager.state, simulation=simulator)
    assert simulation_facts(engine, "missing") == (False, {
        "driver_id": "acme_widget", "cause": SimulatorCause.BUILD_FAILED, "detail": _BUILD_ERROR,
    })
