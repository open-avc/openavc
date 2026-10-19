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
from typing import Any
from unittest.mock import patch

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.outbound_messages import RoomText
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.core.setup_actions import (
    SetupActionContext,
    SetupActionInProgress,
    SetupActionRunner,
)
from openavc.core.state_store import StateStore
from openavc.core.setup_results import Outcome, SetupProgress, SetupResult
from openavc.drivers.actions import resolve_device_actions
from openavc.drivers.base import BaseDriver

pytestmark = pytest.mark.usefixtures("_patch_save_project")


@pytest.fixture
def _patch_save_project():
    # apply_config_update persists the project; we don't need real file I/O here.
    with patch("openavc.core.project_loader.save_project") as m:
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
                "result_contract": "setup/v1",
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

    async def run_setup_action(self, action_id, params, progress) -> SetupResult:
        await progress(SetupProgress("remote.connecting", "Connecting over the out-of-band channel", 10))
        await progress(SetupProgress("remote.enabling", "Enabling remote access", 50))
        # Flip the device into a connectable state, persist new settings,
        # then reconnect over them.
        self._remote_enabled = True
        await self.request_config_update({"port": 9999, "provisioned": "yes"})
        await progress(SetupProgress("remote.reconnecting", "Reconnecting", 90))
        await self.request_reconnect()
        return SetupResult(Outcome.SUCCEEDED, RoomText("Remote access enabled."))


class _FailDriver(_ProvisionDriver):
    async def run_setup_action(self, action_id, params, progress) -> dict:
        await progress(SetupProgress("remote.connecting", "Connecting", 10))
        raise RuntimeError("the switch rejected the change: sensitive-diagnostic")


class _NoHandlerDriver(_ProvisionDriver):
    # Inherits the setup action declaration but not the handler.
    async def run_setup_action(self, action_id, params, progress) -> dict:
        return await BaseDriver.run_setup_action(self, action_id, params, progress)


# --- Fake engine ------------------------------------------------------------


class _FakeEngine:
    def __init__(self, dm: DeviceManager) -> None:
        self.devices = dm
        self.project_path = "unused.avc"
        self.project = ProjectConfig(
            project=ProjectMeta(id="t", name="Test"),
            devices=[
                DeviceConfig(
                    id="dev1", driver="acme_provision", name="Dev 1",
                    config={"host": "h", "port": 23},
                )
            ],
            connections={},
        )
        self._project_revision = 0
        self.ws_messages: list[dict[str, Any]] = []

    async def apply_project(self, new_project, **kwargs) -> int:
        # Mirror the seam's copy+swap+bump contract; the real reconcile is
        # pinned by test_apply_config_update_bumps_revision_without_bounce.
        self.project = new_project
        self._project_revision += 1
        return self._project_revision

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


async def _start(runner, driver, params=None):
    """Use the public runner handshake; capture only protected status here.

    Actual socket recipient and final-send checks are in test_setup_handshake.
    """
    action = _setup_action(driver)
    params = {"password": "secret"} if params is None else params
    prepared = runner.prepare("dev1", action["id"], params,
                              original_grant=lambda: True, current=lambda: True)
    snapshots = []
    runner.observe(prepared["run_id"], lambda: snapshots.append(
        runner.results.status(prepared["capability"], current=lambda: True)))
    started = await runner.start("dev1", action, params,
                                 capability=prepared["capability"], current=lambda: True)
    return started, snapshots


async def _drain(runner: SetupActionRunner) -> None:
    """Await all in-flight setup tasks."""
    tasks = list(runner._tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# --- Happy path -------------------------------------------------------------


async def test_setup_action_streams_progress_updates_config_and_reconnects():
    runner, engine, dm, driver = _make_env()
    started, events = await _start(runner, driver)
    assert started["status"] == "claimed"
    run_id = started["run_id"]
    await _drain(runner)

    assert events, "expected protected progress observations"
    assert all(e["run_id"] == run_id for e in events)
    assert not engine.ws_messages, "setup output must not use a general broadcast"

    statuses = [e["status"] for e in events]
    assert "running" in statuses
    assert statuses[-1] == "terminal"

    done = events[-1]
    assert done["result"]["outcome"] == "succeeded"
    assert done["result"]["config_save"] == "reported_complete"
    assert done["result"]["reconnect"] == "reported_complete"

    # The device came back online over the new settings.
    assert driver.get_state("connected") is True

    # Config delta persisted: connection field -> connections table,
    # non-connection field -> device protocol config, live driver updated.
    assert engine.project.connections["dev1"]["port"] == 9999
    dev = engine.project.devices[0]
    assert dev.config["provisioned"] == "yes"
    assert driver.config["port"] == 9999
    assert driver.config["provisioned"] == "yes"

    # The delta went through the apply_project seam — revision advanced, so
    # a stale IDE PUT 409s instead of silently reverting the wizard's write.
    assert engine._project_revision == 1

    # Auto-reconnect suppression was lifted at the end.
    assert "dev1" not in dm._intentional_disconnect


async def test_none_connection_field_in_delta_is_ignored():
    """A None-valued connection field in a delta is dropped before it touches
    anything: honoring it would remove the key from the persisted connections
    table while the live driver kept key=None — a skew the device reconcile
    reads as a config change, bouncing the device mid-action.
    """
    _runner, engine, _dm, driver = _make_env()
    ctx = SetupActionContext(engine, "dev1")

    await ctx.apply_config_update({"port": None, "provisioned": "yes"})

    # The protocol field applied; the None connection field changed nothing.
    assert engine.project.devices[0].config["provisioned"] == "yes"
    assert engine.project.devices[0].config["port"] == 23
    assert "port" not in engine.project.connections.get("dev1", {})
    assert driver.config["port"] == 23
    assert driver.config["provisioned"] == "yes"
    assert engine._project_revision == 1


async def test_delta_of_only_none_connection_fields_is_a_no_op():
    _runner, engine, _dm, driver = _make_env()
    ctx = SetupActionContext(engine, "dev1")

    await ctx.apply_config_update({"host": None, "port": None})

    assert engine._project_revision == 0
    assert driver.config == {"host": "h", "port": 23}


async def test_apply_config_update_bumps_revision_without_bounce(tmp_path):
    """Regression (data loss), real seam: a setup action's config delta must
    advance the project revision — the old path saved without bumping, so a
    stale IDE PUT silently reverted what the wizard just wrote. And the
    reconcile it triggers must NOT tear down the device mid-action: the live
    config is updated first, so the device compare stays convergent and the
    running handler's ``self`` stays valid.
    """
    from openavc.drivers.registry import register_driver, unregister_driver
    from openavc.core.engine import Engine
    from openavc.core.client_origin import current_client_id, current_setup_device

    register_driver(_ProvisionDriver)
    engine = Engine(str(tmp_path / "p.avc"))
    sent = []
    async def capture(message, namespaces=None):
        sent.append(message)
    engine.broadcast_ws = capture
    token = current_client_id.set("original-tab")
    try:
        engine.project = ProjectConfig(
            project=ProjectMeta(id="t", name="Test"),
            devices=[
                DeviceConfig(
                    id="dev1", driver="acme_provision", name="Dev 1", config={},
                )
            ],
            connections={"dev1": {"host": "h", "port": 23}},
        )
        await engine.devices.add_device(
            engine.resolved_device_config(engine.project.devices[0])
        )
        driver_before = engine.devices.get_driver("dev1")
        assert driver_before is not None
        revision_before = engine._project_revision

        ctx = SetupActionContext(engine, "dev1")
        await ctx.apply_config_update({"port": 9999, "provisioned": "yes"})

        assert engine._project_revision > revision_before
        # Same driver instance — the reconcile did not bounce the device.
        assert engine.devices.get_driver("dev1") is driver_before
        assert engine.project.connections["dev1"]["port"] == 9999
        assert engine.project.devices[0].config["provisioned"] == "yes"
        assert driver_before.config["port"] == 9999
        reloads = [message for message in sent if message["type"] == "project.reloaded"]
        assert len(reloads) == 1
        assert reloads[0]["origin_client"] == "original-tab"
        assert reloads[0]["setup_device_id"] == "dev1"
        assert current_setup_device.get() is None
    finally:
        current_client_id.reset(token)
        await engine.devices._cancel_reconnect("dev1")
        await engine.devices.disconnect_all()
        unregister_driver("acme_provision")


# --- Error path -------------------------------------------------------------


@pytest.mark.parametrize("driver_cls,cause", [(_FailDriver, "RuntimeError"), (_NoHandlerDriver, "NotImplementedError")])
async def test_setup_exception_reports_uncertain_outcome_without_raw_diagnostic(driver_cls, cause):
    runner, engine, dm, driver = _make_env(driver_cls=driver_cls)
    _, events = await _start(runner, driver)
    await _drain(runner)

    last = events[-1]
    assert last["status"] == "terminal"
    assert last["result"]["outcome"] == "unverified"
    assert last["result"]["findings"] == [
        {"code": "setup.exception", "outcome": "failed", "detail": cause}]
    assert "sensitive-diagnostic" not in str(events)
    assert not engine.ws_messages
    assert driver.get_state("connected") is False
    assert "dev1" not in dm._intentional_disconnect
    await dm._cancel_reconnect("dev1")


@pytest.mark.parametrize("outcome,summary", [
    (Outcome.FAILED, "Reached the address, but the password was refused."),
    (Outcome.FAILED, "The device reported failure."),
    (Outcome.SUCCEEDED, "Found 5 endpoints."),
])
async def test_declared_outcome_and_operator_guidance_are_preserved(outcome, summary):
    # Completion is not success: the wizard must branch on the typed outcome.
    # Legacy dictionaries need a separately qualified ingress adapter, never a
    # generic success/ok truthiness guess in the runner.
    runner, engine, dm, driver = _make_env()

    async def handler(action_id, params, progress):
        return SetupResult(outcome, RoomText(summary))

    driver.run_setup_action = handler
    _, events = await _start(runner, driver)
    await _drain(runner)
    last = events[-1]
    assert last["status"] == "terminal"
    assert last["result"]["outcome"] == outcome.value
    assert last["result"]["summary"] == summary
    assert not engine.ws_messages
    await dm._cancel_reconnect("dev1")


# --- Concurrency + guards ---------------------------------------------------


async def test_second_setup_action_while_running_is_rejected():
    runner, engine, dm, driver = _make_env()
    gate = asyncio.Event()

    async def slow_handler(action_id, params, progress):
        await progress(SetupProgress("remote.waiting", "Waiting", 10))
        await gate.wait()
        return SetupResult(Outcome.SUCCEEDED, RoomText("Ready."))

    driver.run_setup_action = slow_handler  # type: ignore[assignment]
    await _start(runner, driver)
    await asyncio.sleep(0)  # let the task reach the gate
    assert runner.is_running("dev1")

    with pytest.raises(SetupActionInProgress):
        await _start(runner, driver)

    gate.set()
    await _drain(runner)
    assert not runner.is_running("dev1")


async def test_start_unknown_device_raises_value_error():
    runner, engine, dm, driver = _make_env()
    action = _setup_action(driver)
    with pytest.raises(ValueError):
        runner.prepare("ghost", action["id"], {}, original_grant=lambda: True, current=lambda: True)


# --- BaseDriver contract guards ---------------------------------------------


async def test_request_helpers_raise_outside_a_setup_run():
    _runner, _engine, _dm, driver = _make_env()
    with pytest.raises(RuntimeError):
        await driver.request_config_update({"port": 1})
    with pytest.raises(RuntimeError):
        await driver.request_reconnect()
    with pytest.raises(RuntimeError):
        driver.register_setup_secret({"key": "invented"})


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
