"""A device changed while a file command runs on it (a firmware image going up).

A change that would rebuild the device (an edit, a driver reload) waits for
the run: it is accepted at once, the run keeps the instance it is sending
through, and the device is rebuilt with the change when the run ends, however
it ends. Removing the device or stopping the system still ends the run, with
a sentence saying why. All against an invented device (Acme).
"""

from __future__ import annotations

import asyncio

import pytest

from openavc.core.command_runs import CANCELLED, DONE, ERROR, RUNNING
from openavc.drivers.registry import register_driver, unregister_driver
from tests.test_command_files import AcmeFiles, _describe, _Engine, _staged, _until

REMOVED = "'Load Firmware' stopped because the device was removed. Check the device before you use it."
STOPPING = (
    "'Load Firmware' stopped because the system is shutting down. "
    "Check the device before you use it."
)


@pytest.fixture
def engine(tmp_path):
    register_driver(AcmeFiles)
    engine = _Engine(tmp_path)
    engine.devices.file_runs = engine.command_runs
    yield engine
    unregister_driver("acme_files")


async def _start(engine) -> object:
    """A firmware run that holds until the test lets the driver go on."""
    engine.driver.gate = asyncio.Event()
    run = engine.command_runs.start(
        "dev1", "load_firmware", {}, {"image": await _staged(engine, b"a" * 3000)},
        describe_error=_describe,
    )
    await asyncio.sleep(0.05)
    return run


def _renamed(engine, name: str) -> dict:
    return {**engine.devices.get_device_config("dev1"), "name": name}


async def test_an_edit_waits_for_the_run_then_applies(engine):
    run = await _start(engine)
    await asyncio.wait_for(engine.devices.update_device("dev1", _renamed(engine, "Dev One")), 1)

    assert engine.devices.get_driver("dev1") is engine.driver
    assert run.status == RUNNING
    assert run.to_dict()["change_waiting"] is True
    await _until(lambda: engine.messages[-1][0]["change_waiting"] is True)

    engine.driver.gate.set()
    await run.task
    assert run.status == DONE and run.error is None
    await _until(lambda: engine.devices.get_driver("dev1") is not engine.driver)
    assert engine.devices.get_device_config("dev1")["name"] == "Dev One"


async def test_a_driver_reload_waits_too_and_keeps_the_held_edit(engine):
    run = await _start(engine)
    await engine.devices.update_device("dev1", _renamed(engine, "Dev One"))
    assert await engine.devices.reload_driver("acme_files") == []
    assert engine.devices.get_driver("dev1") is engine.driver

    engine.driver.gate.set()
    await run.task
    await _until(lambda: engine.devices.get_driver("dev1") is not engine.driver)
    assert engine.devices.get_device_config("dev1")["name"] == "Dev One"


async def test_a_stopped_run_lets_the_held_change_apply(engine):
    run = await _start(engine)
    await engine.devices.update_device("dev1", _renamed(engine, "Dev One"))
    engine.command_runs.cancel("dev1", run.id)
    await asyncio.gather(run.task, return_exceptions=True)
    assert run.status == CANCELLED
    await _until(lambda: engine.devices.get_driver("dev1") is not engine.driver)
    assert engine.devices.get_device_config("dev1")["name"] == "Dev One"


async def test_removing_the_device_ends_the_run_with_why(engine):
    run = await _start(engine)
    await engine.devices.update_device("dev1", _renamed(engine, "Dev One"))
    await engine.devices.remove_device("dev1")

    assert run.task.done()
    assert run.status == ERROR and run.error == REMOVED
    assert engine.messages[-1][0]["error"] == REMOVED
    assert engine.devices.device_record("dev1") is None


async def test_stopping_the_system_ends_the_run_with_why_and_applies_nothing(engine):
    run = await _start(engine)
    await engine.devices.update_device("dev1", _renamed(engine, "Dev One"))
    await engine.command_runs.stop()

    assert run.status == ERROR and run.error == STOPPING
    assert engine.devices.get_driver("dev1") is engine.driver
    await engine.devices.disconnect_all()
    assert engine.devices.get_device_config("dev1")["name"] == "Dev 1"
