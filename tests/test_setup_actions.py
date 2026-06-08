"""Tests for the setup-action mechanism (provisioning wizards).

Exercises the generic platform machinery — SetupActionRunner, SetupActionContext,
and the BaseDriver run_setup_action / request_config_update / request_reconnect
contract — with an INVENTED provisionable device (Acme), never a real product.
The device starts offline ("remote access" disabled); its setup handler enables
remote, persists a config delta, and reconnects, exactly mirroring the shape a
real provisioning wizard takes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.project_loader import DeviceConfig
from server.core.setup_actions import SetupActionInProgress, SetupActionRunner
from server.core.state_store import StateStore
from server.drivers.actions import resolve_device_actions
from server.drivers.base import BaseDriver

pytestmark = pytest.mark.usefixtures("_patch_save_project")


@pytest.fixture
def _patch_save_project():
    # apply_config_update persists the project; we don't need real file I/O here.
    with patch("server.core.project_loader.save_project") as m:
        yield m


# --- Invented provisionable driver -----------------------------------------


class _ProvisionDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_provision",
        "name": "Acme Provisionable Widget",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "actions": [
            {
                "id": "enable_remote",
                "kind": "setup",
                "label": "Enable Remote Access",
                "availability": "offline",
                "params": {"password": {"type": "password", "required": True}},
            }
        ],
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Offline until the setup action "enables remote access" on the device.
        self._remote_enabled = False

    async def connect(self) -> None:
        if not self._remote_enabled:
            self._stash = "connection refused (remote not enabled)"
            raise ConnectionError(self._stash)
        self._connected = True
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def run_setup_action(self, action_id, params, progress) -> dict:
        await progress("Connecting over the out-of-band channel", 10)
        await progress("Enabling remote access", 50)
        # Flip the device into a connectable state, persist new settings,
        # then reconnect over them.
        self._remote_enabled = True
        await self.request_config_update({"port": 9999, "provisioned": "yes"})
        await progress("Reconnecting", 90)
        await self.request_reconnect()
        return {"enabled": True, "password_len": len(params.get("password", ""))}


class _FailDriver(_ProvisionDriver):
    async def run_setup_action(self, action_id, params, progress) -> dict:
        await progress("Connecting", 10)
        raise RuntimeError("the switch rejected the change")


class _NoHandlerDriver(_ProvisionDriver):
    # Inherits the setup action declaration but not the handler.
    async def run_setup_action(self, action_id, params, progress) -> dict:
        return await BaseDriver.run_setup_action(self, action_id, params, progress)


# --- Fake engine ------------------------------------------------------------


class _FakeEngine:
    def __init__(self, dm: DeviceManager) -> None:
        self.devices = dm
        self.project_path = "unused.avc"
        self.project = SimpleNamespace(
            devices=[
                DeviceConfig(
                    id="dev1", driver="acme_provision", name="Dev 1",
                    config={"host": "h", "port": 23},
                )
            ],
            connections={},
        )
        self.ws_messages: list[dict[str, Any]] = []

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        self.ws_messages.append(message)


def _make_env(driver_cls=_ProvisionDriver):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    driver = driver_cls("dev1", {"host": "h", "port": 23}, state, events)
    driver.set_state("connected", False)
    dm._devices["dev1"] = driver
    dm._device_configs["dev1"] = {
        "id": "dev1", "driver": "acme_provision", "name": "Dev 1",
        "config": {"host": "h", "port": 23}, "enabled": True,
    }
    engine = _FakeEngine(dm)
    runner = SetupActionRunner(engine)
    return runner, engine, dm, driver


def _setup_action(driver) -> dict:
    return next(
        a for a in resolve_device_actions(driver.DRIVER_INFO) if a["kind"] == "setup"
    )


async def _drain(runner: SetupActionRunner) -> None:
    """Await all in-flight setup tasks."""
    tasks = list(runner._tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# --- Happy path -------------------------------------------------------------


async def test_setup_action_streams_progress_updates_config_and_reconnects():
    runner, engine, dm, driver = _make_env()
    action = _setup_action(driver)

    started = await runner.start("dev1", action, {"password": "secret"})
    assert started["status"] == "started"
    run_id = started["run_id"]
    await _drain(runner)

    events = [m for m in engine.ws_messages if m["type"] == "action.progress"]
    assert events, "expected action.progress events"
    assert all(e["run_id"] == run_id for e in events)
    assert all(e["device_id"] == "dev1" and e["action_id"] == "enable_remote" for e in events)

    statuses = [e["status"] for e in events]
    assert "running" in statuses
    assert statuses[-1] == "done"

    done = events[-1]
    assert done["result"] == {"enabled": True, "password_len": 6}

    # The device came back online over the new settings.
    assert driver.get_state("connected") is True

    # Config delta persisted: connection field -> connections table,
    # non-connection field -> device protocol config, live driver updated.
    assert engine.project.connections["dev1"]["port"] == 9999
    dev = engine.project.devices[0]
    assert dev.config["provisioned"] == "yes"
    assert driver.config["port"] == 9999
    assert driver.config["provisioned"] == "yes"

    # Auto-reconnect suppression was lifted at the end.
    assert "dev1" not in dm._intentional_disconnect


# --- Error path -------------------------------------------------------------


async def test_setup_action_error_emits_error_event():
    runner, engine, dm, driver = _make_env(driver_cls=_FailDriver)
    action = _setup_action(driver)

    await runner.start("dev1", action, {"password": "x"})
    await _drain(runner)

    events = [m for m in engine.ws_messages if m["type"] == "action.progress"]
    assert events[-1]["status"] == "error"
    assert "rejected" in events[-1]["error"]
    assert driver.get_state("connected") is False
    assert "dev1" not in dm._intentional_disconnect

    # end_setup resumed auto-reconnect for the still-offline device; cancel it
    # so the loop doesn't dangle past the test.
    await dm._cancel_reconnect("dev1")


async def test_setup_action_unimplemented_handler_reports_error():
    runner, engine, dm, driver = _make_env(driver_cls=_NoHandlerDriver)
    action = _setup_action(driver)

    await runner.start("dev1", action, {"password": "x"})
    await _drain(runner)

    last = [m for m in engine.ws_messages if m["type"] == "action.progress"][-1]
    assert last["status"] == "error"
    assert last["error"] == "not_implemented"
    await dm._cancel_reconnect("dev1")


# --- Concurrency + guards ---------------------------------------------------


async def test_second_setup_action_while_running_is_rejected():
    runner, engine, dm, driver = _make_env()
    gate = asyncio.Event()

    async def slow_handler(action_id, params, progress):
        await progress("waiting", 10)
        await gate.wait()
        return {"ok": True}

    driver.run_setup_action = slow_handler  # type: ignore[assignment]
    action = _setup_action(driver)

    await runner.start("dev1", action, {})
    await asyncio.sleep(0)  # let the task reach the gate
    assert runner.is_running("dev1")

    with pytest.raises(SetupActionInProgress):
        await runner.start("dev1", action, {})

    gate.set()
    await _drain(runner)
    assert not runner.is_running("dev1")


async def test_start_unknown_device_raises_value_error():
    runner, engine, dm, driver = _make_env()
    action = _setup_action(driver)
    with pytest.raises(ValueError):
        await runner.start("ghost", action, {})


# --- BaseDriver contract guards ---------------------------------------------


async def test_request_helpers_raise_outside_a_setup_run():
    _runner, _engine, _dm, driver = _make_env()
    with pytest.raises(RuntimeError):
        await driver.request_config_update({"port": 1})
    with pytest.raises(RuntimeError):
        await driver.request_reconnect()


async def test_default_run_setup_action_raises_not_implemented():
    _runner, _engine, _dm, driver = _make_env(driver_cls=_NoHandlerDriver)

    async def _noop(step, pct=None):
        return None

    with pytest.raises(NotImplementedError):
        await BaseDriver.run_setup_action(driver, "enable_remote", {}, _noop)


# --- DeviceManager begin/end_setup ------------------------------------------


async def test_begin_setup_suppresses_and_end_setup_resumes_reconnect():
    _runner, _engine, dm, driver = _make_env()

    await dm.begin_setup("dev1")
    assert "dev1" in dm._intentional_disconnect

    # Device still offline -> end_setup resumes the auto-reconnect loop.
    await dm.end_setup("dev1")
    assert "dev1" not in dm._intentional_disconnect
    assert "dev1" in dm._reconnect_tasks
    await dm._cancel_reconnect("dev1")
