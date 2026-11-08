"""What the engine's reconcile, its device bring-up and its runtime services
(the ISC mesh, the mDNS advertiser, panel software updates, the cloud agent)
do where no other test looked: the order a trigger rebuild runs in, which
simulation step a bring-up round takes, a round that fails, and how each
service starts, follows a change, and fails without taking the engine down.

Each test checks a result (what was built and with what, what was called,
what the engine holds afterwards, a log line), never only that it ran.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc import config as config_mod
from openavc.api import isc_ws
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.script_api import isc as isc_proxy


def _project_dict(*, name="P", isc=None, variables=None):
    return {
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": name},
        "variables": variables or [],
        "macros": [],
        "devices": [],
        "device_groups": [],
        "connections": {},
        "scripts": [],
        "plugins": {},
        "ui": {
            "settings": {},
            "pages": [
                {"id": "main", "name": "Main",
                 "grid": {"columns": 12, "rows": 8}, "elements": []},
            ],
        },
        "isc": isc or {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


def _engine(tmp_path, **kwargs) -> Engine:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project_dict(**kwargs)), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    return eng


_MESH = {
    "enabled": True,
    "shared_state": ["var.room_*"],
    "peers": ["10.0.0.9"],
    "auth_key": "",
    "allowed_remote_commands": ["acme_widget.*"],
}


# --- The reconcile and the bring-up -------------------------------------------


@pytest.mark.asyncio
async def test_a_trigger_rebuild_stops_the_triggers_before_any_section_changes(tmp_path):
    eng = _engine(tmp_path, variables=[{"id": "v1", "type": "number", "default": 1}])
    order: list[str] = []
    stop, reconcile_variables = eng.triggers.stop, eng._reconcile_variables

    async def recorded_stop():
        order.append("triggers stopped")
        await stop()

    def recorded_variables():
        order.append("variables reconciled")
        reconcile_variables()

    eng.triggers.stop = recorded_stop
    eng._reconcile_variables = recorded_variables
    edited = eng.project.model_copy(deep=True)
    edited.variables[0].default = 2

    await eng.apply_project(edited)

    assert order == ["triggers stopped", "variables reconciled"]


def _fake_simulation(*, resume_pending: bool, active: bool):
    return SimpleNamespace(
        resume_pending=resume_pending, active=active,
        resume=AsyncMock(), sync=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_a_simulation_waiting_to_resume_is_resumed_not_synced(tmp_path):
    eng = _engine(tmp_path)
    eng.simulation = _fake_simulation(resume_pending=True, active=True)
    eng.devices.bring_up = AsyncMock()

    eng._schedule_device_bringup()
    await eng.wait_for_device_bringup()

    eng.simulation.resume.assert_awaited_once()
    eng.simulation.sync.assert_not_awaited()
    eng.devices.bring_up.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_running_simulation_is_synced_before_the_devices_come_up(tmp_path):
    eng = _engine(tmp_path)
    eng.simulation = _fake_simulation(resume_pending=False, active=True)
    eng.devices.bring_up = AsyncMock()

    eng._schedule_device_bringup()
    await eng.wait_for_device_bringup()

    eng.simulation.sync.assert_awaited_once()
    eng.simulation.resume.assert_not_awaited()
    eng.devices.bring_up.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_bring_up_round_is_logged_and_the_next_round_still_runs(tmp_path, caplog):
    eng = _engine(tmp_path)
    eng.simulation = _fake_simulation(resume_pending=False, active=False)
    rounds: list[int] = []

    async def bring_up():
        rounds.append(len(rounds) + 1)
        if len(rounds) == 1:
            eng._schedule_device_bringup()  # a save landing mid-round
            raise RuntimeError("round one failed")

    eng.devices.bring_up = bring_up
    with caplog.at_level(logging.ERROR):
        eng._schedule_device_bringup()
        await eng.wait_for_device_bringup()

    assert rounds == [1, 2]
    assert eng._bringup_task is None
    assert any(
        "Device bring-up after a project change failed" in r.getMessage() for r in caplog.records
    )


# --- The ISC mesh -------------------------------------------------------------


@pytest.fixture
def fake_isc(monkeypatch):
    """Stands in for ISCManager; puts the module-level wiring back after."""
    from openavc.core import isc as isc_mod

    built: list[dict] = []

    class FakeManager:
        fail_start = False

        def __init__(self, **kwargs):
            built.append(kwargs)
            self.started = False
            self.reload = AsyncMock()
            self.stop = AsyncMock()

        async def start(self):
            if FakeManager.fail_start:
                raise OSError("port in use")
            self.started = True

    monkeypatch.setattr(isc_mod, "ISCManager", FakeManager)
    yield SimpleNamespace(cls=FakeManager, built=built)
    isc_ws.set_isc_manager(None)
    isc_proxy._bind(None)


@pytest.mark.asyncio
async def test_the_mesh_starts_with_the_project_settings_and_is_wired_in(tmp_path, fake_isc):
    eng = _engine(tmp_path, name="Hall A", isc=_MESH)

    await eng._start_isc()

    assert len(fake_isc.built) == 1
    kwargs = fake_isc.built[0]
    assert kwargs["state"] is eng.state
    assert kwargs["events"] is eng.events
    assert kwargs["devices"] is eng.devices
    assert kwargs["shared_state_patterns"] == ["var.room_*"]
    assert kwargs["manual_peers"] == ["10.0.0.9"]
    assert kwargs["auth_key"] == ""
    assert kwargs["allowed_remote_commands"] == ["acme_widget.*"]
    assert kwargs["instance_id"] == eng.instance_id
    assert kwargs["instance_name"] == "Hall A"
    assert kwargs["http_port"] == config_mod.HTTP_PORT
    assert eng.isc.started is True
    assert isc_ws._isc_manager is eng.isc
    assert isc_proxy._manager is eng.isc


@pytest.mark.asyncio
async def test_a_mesh_that_cannot_start_leaves_the_engine_without_one(tmp_path, fake_isc, caplog):
    eng = _engine(tmp_path, isc=_MESH)
    fake_isc.cls.fail_start = True

    with caplog.at_level(logging.ERROR):
        await eng._start_isc()

    assert eng.isc is None
    assert any("ISC: Failed to start" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_project_with_the_mesh_off_starts_none(tmp_path, fake_isc):
    eng = _engine(tmp_path)

    await eng._start_isc()

    assert fake_isc.built == []
    assert eng.isc is None


@pytest.mark.asyncio
async def test_turning_the_mesh_on_in_an_edit_starts_it(tmp_path, fake_isc):
    eng = _engine(tmp_path, isc=_MESH)
    assert eng.isc is None

    await eng._reload_isc()

    assert len(fake_isc.built) == 1
    assert eng.isc.started is True


@pytest.mark.asyncio
async def test_a_running_mesh_is_handed_the_edited_settings(tmp_path, fake_isc):
    eng = _engine(tmp_path, isc=_MESH)
    running = MagicMock()
    running.reload = AsyncMock()
    eng.isc = running

    await eng._reload_isc()

    running.reload.assert_awaited_once_with(
        shared_state_patterns=["var.room_*"],
        auth_key="",
        manual_peers=["10.0.0.9"],
        allowed_remote_commands=["acme_widget.*"],
    )
    assert fake_isc.built == []


@pytest.mark.asyncio
async def test_the_mesh_reload_with_no_project_does_nothing(tmp_path, fake_isc):
    eng = _engine(tmp_path)
    running = MagicMock()
    running.stop = AsyncMock()
    eng.isc = running
    eng.project = None

    await eng._reload_isc()

    running.stop.assert_not_awaited()
    assert eng.isc is running


# --- The mDNS advertiser ------------------------------------------------------


def _any_network(monkeypatch):
    plan = SimpleNamespace(local_only=False, mode="any", network_addresses=())
    monkeypatch.setattr(config_mod, "listen_plan", lambda: plan)


def _this_computer_only(monkeypatch):
    plan = SimpleNamespace(local_only=True, mode="this_computer", network_addresses=())
    monkeypatch.setattr(config_mod, "listen_plan", lambda: plan)


def _fake_advertiser(monkeypatch, *, fail=False):
    from openavc.discovery import mdns_advertiser

    built: list[dict] = []

    class FakeAdvertiser:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.stop = AsyncMock()

        async def start(self):
            if fail:
                raise OSError("no multicast")

    monkeypatch.setattr(mdns_advertiser, "MDNSAdvertiser", FakeAdvertiser)
    return built


@pytest.mark.asyncio
async def test_an_advertiser_that_cannot_start_leaves_the_engine_without_one(
    tmp_path, monkeypatch, caplog,
):
    eng = _engine(tmp_path)
    _any_network(monkeypatch)
    built = _fake_advertiser(monkeypatch, fail=True)

    with caplog.at_level(logging.ERROR):
        await eng._start_mdns_advertiser()

    assert len(built) == 1
    assert eng.mdns_advertiser is None
    assert any("mDNS advertiser: failed to start" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_settings_change_that_wants_the_advertiser_starts_it(tmp_path, monkeypatch):
    eng = _engine(tmp_path, name="Hall A")
    _any_network(monkeypatch)
    built = _fake_advertiser(monkeypatch)

    await eng.reconcile_runtime_services()

    assert len(built) == 1
    assert built[0]["instance_name"] == "Hall A"
    assert eng.mdns_advertiser is not None


@pytest.mark.asyncio
async def test_a_settings_change_that_leaves_nothing_to_announce_stops_it(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    _this_computer_only(monkeypatch)
    running = MagicMock()
    running.stop = AsyncMock()
    eng.mdns_advertiser = running

    await eng.reconcile_runtime_services()

    running.stop.assert_awaited_once()
    assert eng.mdns_advertiser is None


# --- Panel software updates and the cloud agent -------------------------------


@pytest.mark.asyncio
async def test_panel_software_that_cannot_start_leaves_the_engine_without_it(
    tmp_path, monkeypatch, caplog,
):
    from openavc.updater import panel_software

    class FailingUpdates:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            raise RuntimeError("supervisor unreadable")

    monkeypatch.setattr(panel_software, "PanelSoftwareUpdates", FailingUpdates)
    eng = _engine(tmp_path)
    eng.update_manager = MagicMock()

    with caplog.at_level(logging.ERROR):
        await eng._start_panel_software()

    assert eng.panel_software is None
    assert any("Panel software updates failed to start" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_cloud_pairing_with_no_key_starts_no_agent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config_mod, "CLOUD_ENABLED", True)
    monkeypatch.setattr(config_mod, "CLOUD_SYSTEM_KEY", "")
    monkeypatch.setattr(config_mod, "CLOUD_SYSTEM_ID", "sys-1")
    eng = _engine(tmp_path)

    with caplog.at_level(logging.WARNING):
        await eng._start_cloud_agent()

    assert eng.cloud_agent is None
    assert any(
        "Cloud agent: enabled but missing system_key or system_id, skipping" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_a_cloud_agent_that_cannot_start_leaves_the_engine_without_one(
    tmp_path, monkeypatch, caplog,
):
    from openavc.cloud import agent as agent_mod

    def failing_agent(*args, **kwargs):
        raise RuntimeError("bad endpoint")

    monkeypatch.setattr(config_mod, "CLOUD_ENABLED", True)
    monkeypatch.setattr(config_mod, "CLOUD_SYSTEM_KEY", "key")
    monkeypatch.setattr(config_mod, "CLOUD_SYSTEM_ID", "sys-1")
    monkeypatch.setattr(agent_mod, "CloudAgent", failing_agent)
    eng = _engine(tmp_path)

    with caplog.at_level(logging.ERROR):
        await eng._start_cloud_agent()

    assert eng.cloud_agent is None
    assert any("Cloud agent: failed to start" in r.getMessage() for r in caplog.records)



def _system_switch(monkeypatch, section: str, key: str, value) -> None:
    """The live system config with one switch overridden."""
    from openavc import system_config

    real = system_config.get_system_config()

    class Overridden:
        def get(self, s, k, default=None):
            return value if (s, k) == (section, key) else real.get(s, k, default)

    monkeypatch.setattr(system_config, "get_system_config", lambda: Overridden())


@pytest.mark.asyncio
async def test_the_mesh_switched_off_for_the_system_starts_none(tmp_path, fake_isc, monkeypatch):
    _system_switch(monkeypatch, "isc", "enabled", False)
    eng = _engine(tmp_path, isc=_MESH)

    await eng._start_isc()

    assert fake_isc.built == []
    assert eng.isc is None


@pytest.mark.asyncio
async def test_turning_the_mesh_off_in_an_edit_unwires_its_endpoint(tmp_path, fake_isc):
    eng = _engine(tmp_path)
    running = MagicMock()
    running.stop = AsyncMock()
    eng.isc = running
    isc_ws.set_isc_manager(running)

    await eng._reload_isc()

    running.stop.assert_awaited_once()
    assert eng.isc is None
    assert isc_ws._isc_manager is None


@pytest.mark.asyncio
async def test_advertising_switched_off_announces_nothing(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    _any_network(monkeypatch)
    _system_switch(monkeypatch, "discovery", "advertise", False)
    built = _fake_advertiser(monkeypatch)

    await eng._start_mdns_advertiser()

    assert built == []
    assert eng.mdns_advertiser is None


def _fake_panel_software(monkeypatch):
    from openavc.updater import panel_software

    class FakeUpdates:
        def __init__(self, *args, **kwargs):
            self.stop = AsyncMock()

        async def start(self):
            pass

    monkeypatch.setattr(panel_software, "PanelSoftwareUpdates", FakeUpdates)


@pytest.mark.asyncio
async def test_panel_software_is_handed_to_a_running_cloud_agent(tmp_path, monkeypatch):
    _fake_panel_software(monkeypatch)
    eng = _engine(tmp_path)
    eng.update_manager = MagicMock()
    eng.cloud_agent = SimpleNamespace(_command_handler=SimpleNamespace(_panel_software=None))

    await eng._start_panel_software()

    assert eng.panel_software is not None
    assert eng.cloud_agent._command_handler._panel_software is eng.panel_software


@pytest.mark.asyncio
async def test_a_stop_stops_panel_software(tmp_path, monkeypatch):
    _fake_panel_software(monkeypatch)
    eng = _engine(tmp_path)
    eng.update_manager = MagicMock()
    await eng._start_panel_software()

    await eng._stop_panel_software()

    eng.panel_software.stop.assert_awaited_once()


def _cloud_on(monkeypatch):
    monkeypatch.setattr(config_mod, "CLOUD_ENABLED", True)
    monkeypatch.setattr(config_mod, "CLOUD_SYSTEM_KEY", "key")
    monkeypatch.setattr(config_mod, "CLOUD_SYSTEM_ID", "sys-1")


@pytest.mark.asyncio
async def test_cloud_switched_off_starts_no_agent(tmp_path, monkeypatch):
    from openavc.cloud import agent as agent_mod

    built = []
    _cloud_on(monkeypatch)
    monkeypatch.setattr(config_mod, "CLOUD_ENABLED", False)
    monkeypatch.setattr(agent_mod, "CloudAgent", lambda *a, **k: built.append(a))
    eng = _engine(tmp_path)

    await eng._start_cloud_agent()

    assert built == []
    assert eng.cloud_agent is None


@pytest.mark.asyncio
async def test_the_cloud_agent_is_wired_to_every_part_and_connects(tmp_path, monkeypatch):
    from openavc.cloud import agent as agent_mod

    agent = MagicMock()
    agent.connect = AsyncMock()
    _cloud_on(monkeypatch)
    monkeypatch.setattr(agent_mod, "CloudAgent", lambda *a, **k: agent)
    eng = _engine(tmp_path)

    await eng._start_cloud_agent()

    assert eng.cloud_agent is agent
    for setter in (
        "set_heartbeat_collector", "set_state_relay", "set_command_handler",
        "set_ai_tool_handler", "set_alert_monitor", "set_tunnel_handler", "set_cert_manager",
    ):
        getattr(agent, setter).assert_called_once()
    agent.set_help_requests.assert_called_once_with(eng.help)
    agent.connect.assert_awaited_once()
