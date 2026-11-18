"""A setup action's settings reach the project and the running device.

A setup action (a driver's provisioning wizard) saves the settings it changed
with ``request_config_update``. The change is made to the project current
when the save runs, so an edit saved meanwhile is kept, and the running
device takes the new settings only once the file holds them, under the same
lock, so a save that fails leaves the device dialing what the file says and
no edit saved right after reads the change as one of its own.

Each test runs the real engine, its reconcile included, on a temporary
project, with an invented provisionable driver.
"""

from __future__ import annotations

import asyncio
import errno
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, ProjectSaveError, save_project
from openavc.core.project_migration import CURRENT_VERSION
from openavc.core.setup_actions import SetupActionContext, SetupActionRunner
from openavc.drivers.actions import resolve_device_actions
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver


class _AcmeProvision(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_provision_saves",
        "name": "Acme Provisionable Widget",
        "manufacturer": "Acme",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "actions": [
            {
                "id": "move_port",
                "kind": "setup",
                "label": "Move Control Port",
                "availability": "offline",
            }
        ],
    }

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def run_setup_action(self, action_id, params, progress) -> dict:
        await self.request_config_update({"port": 9999, "provisioned": "yes"})
        return {"moved": True}


def _project() -> ProjectConfig:
    return ProjectConfig(**{
        "openavc_version": CURRENT_VERSION,
        "project": {"id": "hall", "name": "Hall"},
        "devices": [
            {"id": "dev1", "name": "Widget", "driver": "acme_provision_saves", "config": {}},
            {"id": "dev2", "name": "Other", "driver": "acme_provision_saves", "config": {}},
        ],
        "connections": {
            "dev1": {"host": "10.0.0.1", "port": 23},
            "dev2": {"host": "10.0.0.2", "port": 23},
        },
    })


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(data))
    register_driver(_AcmeProvision)
    eng = Engine(str(tmp_path / "project.avc"))
    eng.project = _project()
    save_project(eng.project_path, eng.project)
    sent: list[dict] = []

    async def broadcast_ws(message, *, client_type=None):
        # A real send gives the loop a turn, which is where a change waiting
        # on the lock gets in.
        sent.append(message)
        await asyncio.sleep(0)

    eng.broadcast_ws = broadcast_ws
    eng.sent = sent
    eng._running = True
    monkeypatch.setattr(eng, "_record_push_ports", MagicMock())
    monkeypatch.setattr(eng, "_schedule_device_bringup", MagicMock())
    with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
        for device in eng.project.devices:
            await eng.devices.add_device(eng.resolved_device_config(device), defer_connect=True)
        try:
            yield eng
        finally:
            for device_id in ("dev1", "dev2"):
                await eng.devices._cancel_reconnect(device_id)
            await eng.devices.disconnect_all()
            unregister_driver("acme_provision_saves")


def _disk(engine: Engine) -> dict:
    return json.loads(engine.project_path.read_text(encoding="utf-8"))


async def _let_others_run() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


def _rename(project) -> None:
    project.project.name = "Renamed"


def _rename_other_device(project) -> None:
    next(d for d in project.devices if d.id == "dev2").name = "Other, renamed"


async def test_an_edit_saved_while_a_setup_action_saves_its_settings_is_kept(engine):
    update = None
    async with engine._reload_lock:
        update = asyncio.create_task(
            SetupActionContext(engine, "dev1").apply_config_update({"port": 9999})
        )
        await _let_others_run()
        await engine._change_locked(_rename, reconcile=True)
    await update

    assert engine.project.project.name == "Renamed"
    assert engine.project.connections["dev1"]["port"] == 9999
    assert _disk(engine)["project"]["name"] == "Renamed"
    assert _disk(engine)["connections"]["dev1"]["port"] == 9999


async def test_a_setup_action_whose_save_fails_leaves_the_device_on_what_the_file_holds(
    engine, monkeypatch,
):
    async def no_room(path, project):
        raise ProjectSaveError(OSError(errno.ENOSPC, "No space left on device", str(path)))

    monkeypatch.setattr("openavc.core.project_changes.save_project_async", no_room)
    driver = engine.devices.get_driver("dev1")
    action = next(a for a in resolve_device_actions(driver.DRIVER_INFO) if a["kind"] == "setup")

    runner = SetupActionRunner(engine)
    await runner.start("dev1", action, {})
    await asyncio.gather(*runner._tasks, return_exceptions=True)

    assert engine.devices.get_driver("dev1") is driver
    assert driver.config["port"] == 23
    assert "provisioned" not in driver.config
    assert engine.devices.get_device_config("dev1")["config"]["port"] == 23
    assert engine.project.connections["dev1"]["port"] == 23
    assert _disk(engine)["connections"]["dev1"]["port"] == 23
    assert "provisioned" not in _disk(engine)["devices"][0]["config"]
    said = (
        "Not applied: the project file could not be written. The disk is full. "
        "Free up disk space and try again. The previous project is still running."
    )
    last = [m for m in engine.sent if m.get("type") == "action.progress"][-1]
    assert (last["status"], last["error"]) == ("error", said)
    await engine.devices._cancel_reconnect("dev1")


async def test_an_edit_saved_right_after_a_setup_action_leaves_its_device_running(engine):
    driver = engine.devices.get_driver("dev1")
    async with engine._reload_lock:
        update = asyncio.create_task(
            SetupActionContext(engine, "dev1").apply_config_update({"port": 9999})
        )
        await _let_others_run()
        edit = asyncio.create_task(engine.apply_project_edit(_rename_other_device))
        await _let_others_run()
    await update
    await edit

    assert engine.devices.get_driver("dev1") is driver
    assert driver.config["port"] == 9999
    assert engine.devices.get_device_config("dev1")["config"]["port"] == 9999


async def test_a_setup_action_s_settings_are_saved_without_restarting_its_device(engine):
    driver = engine.devices.get_driver("dev1")
    revision = engine._project_revision

    await SetupActionContext(engine, "dev1").apply_config_update({"port": 9999, "provisioned": "yes"})

    assert engine.devices.get_driver("dev1") is driver
    assert driver.config["port"] == 9999
    assert driver.config["provisioned"] == "yes"
    assert engine._project_revision != revision
    assert _disk(engine)["connections"]["dev1"]["port"] == 9999
    assert _disk(engine)["devices"][0]["config"]["provisioned"] == "yes"
    reloaded = [m for m in engine.sent if m.get("type") == "project.reloaded"]
    assert [m["revision"] for m in reloaded] == [engine._project_revision]


async def test_a_setup_action_s_save_is_announced_to_scripts_once(engine):
    heard: list[str] = []

    async def on_reloaded(event, payload):
        heard.append(event)

    engine.events.on("system.project.reloaded", on_reloaded)
    await SetupActionContext(engine, "dev1").apply_config_update({"port": 9999})

    assert heard == ["system.project.reloaded"]


async def test_a_setup_action_s_settings_record_the_push_ports_and_ask_for_a_bring_up(engine):
    """A setting a wizard moves can be the port a device pushes to (the host
    firewall's record), and on a simulated device it is the address the
    simulator's redirect replaced (the bring-up round points it back)."""
    await SetupActionContext(engine, "dev1").apply_config_update({"port": 9999})

    engine._record_push_ports.assert_called_once_with()
    engine._schedule_device_bringup.assert_called_once_with()


async def test_a_setup_action_for_a_device_the_project_no_longer_has_saves_nothing(engine):
    await engine.apply_project_edit(lambda p: p.devices.pop(0))
    revision = engine._project_revision

    with pytest.raises(RuntimeError, match="not found in project"):
        await SetupActionContext(engine, "dev1").apply_config_update({"port": 9999})

    assert engine._project_revision == revision
    assert "dev1" not in engine.project.connections or engine.project.connections["dev1"]["port"] == 23
