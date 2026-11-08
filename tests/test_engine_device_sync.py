"""How a project change reaches the running devices (``Engine._sync_devices``)
and what the reconcile stops first, pinned by what each does.

Which resolved-config fields re-add a device and which differences do not
count, the logins swept on every sync, bridges added before the devices that
route through them, the deferred connect carried to both an add and an
update, the log line for each failure, and the macros a device-group edit
cancels.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.engine import Engine
from openavc.core.project_diff import ProjectDiff, ProjectOrigin
from openavc.core.project_loader import load_project

ENGINE_LOG = "openavc.core.engine"


def _engine(tmp_path, devices) -> Engine:
    project = {
        "version": "0.5.0",
        "project": {"id": "p", "name": "P"},
        "variables": [], "macros": [], "devices": devices, "device_groups": [],
        "connections": {}, "scripts": [], "plugins": {},
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []},
        ]},
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(project), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    return eng


def _manager(running: dict[str, dict]) -> MagicMock:
    devices = MagicMock()
    devices.get_device_configs.return_value = running
    devices.get_device_config.side_effect = running.get
    devices.update_device = AsyncMock()
    devices.add_device = AsyncMock()
    devices.remove_device = AsyncMock()
    return devices


def _one_device(tmp_path, **extra):
    device = {"id": "d1", "driver": "generic_tcp", "name": "D1", "config": {"host": "1.2.3.4"}, **extra}
    eng = _engine(tmp_path, [device])
    resolved = eng.resolved_device_config(eng.project.devices[0])
    return eng, resolved


@pytest.mark.asyncio
async def test_no_project_touches_nothing(tmp_path):
    eng, _ = _one_device(tmp_path)
    eng.project = None
    eng.devices = _manager({})
    await eng._sync_devices()
    eng.devices.get_device_configs.assert_not_called()
    eng.devices.add_device.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_sync_sweeps_the_saved_logins(tmp_path):
    eng, resolved = _one_device(tmp_path)
    eng.devices = _manager({"d1": json.loads(json.dumps(resolved))})
    swept = []
    eng.device_logins.sweep = lambda in_project: swept.append(in_project)
    await eng._sync_devices()
    assert len(swept) == 1


@pytest.mark.parametrize("field,old", [
    ("name", "Old name"),
    ("driver", "generic_serial"),
    ("config", {"host": "9.9.9.9"}),
])
@pytest.mark.asyncio
async def test_a_changed_field_add_device_reads_re_adds_the_device(tmp_path, field, old):
    eng, resolved = _one_device(tmp_path)
    running = json.loads(json.dumps(resolved))
    running[field] = old
    eng.devices = _manager({"d1": running})
    await eng._sync_devices(defer_connect=True)
    eng.devices.update_device.assert_awaited_once_with("d1", resolved, defer_connect=True)


@pytest.mark.parametrize("old_has,new_has,re_added", [
    # A device with no "enabled" reads as enabled.
    ({}, {"enabled": True}, False),
    ({}, {"enabled": False}, True),
    ({"enabled": True}, {}, False),
    # No map and an empty map are the same.
    ({"child_entities": None}, {"child_entities": {}}, False),
    ({"pending_settings": None}, {"pending_settings": {}}, False),
    ({}, {"pending_settings": {}}, False),
    ({"pending_settings": {}}, {"pending_settings": {"brightness": 1}}, True),
])
@pytest.mark.asyncio
async def test_which_differences_count(tmp_path, old_has, new_has, re_added):
    eng, resolved = _one_device(tmp_path)
    base = {k: v for k, v in resolved.items() if k not in ("enabled", "child_entities", "pending_settings")}
    running = {**json.loads(json.dumps(base)), **old_has}
    wanted = {**json.loads(json.dumps(base)), **new_has}
    eng.resolved_device_config = lambda device: wanted
    eng.devices = _manager({"d1": running})
    await eng._sync_devices()
    assert eng.devices.update_device.await_count == (1 if re_added else 0)


@pytest.mark.asyncio
async def test_a_device_with_no_running_config_is_compared_as_empty(tmp_path):
    eng, resolved = _one_device(tmp_path)
    eng.devices = _manager({"d1": {}})
    eng.devices.get_device_config.side_effect = lambda device_id: None
    await eng._sync_devices()
    eng.devices.update_device.assert_awaited_once_with("d1", resolved, defer_connect=False)


@pytest.mark.asyncio
async def test_bridges_are_added_before_the_devices_that_route_through_them(tmp_path, monkeypatch):
    import openavc.core.device_config as device_config

    monkeypatch.setattr(device_config, "is_bridge_config", lambda cfg: cfg.get("driver") == "acme_bridge")
    eng = _engine(tmp_path, [
        {"id": "dep", "driver": "generic_tcp", "name": "Dep", "config": {"host": "1.2.3.4"}},
        {"id": "br", "driver": "acme_bridge", "name": "Bridge", "config": {"host": "1.2.3.5"}},
    ])
    eng.devices = _manager({})
    order = []

    async def add_device(config, *, defer_connect):
        order.append(f"start {config['id']}")
        await asyncio.sleep(0)
        order.append(f"end {config['id']} defer={defer_connect}")

    eng.devices.add_device = add_device
    await eng._sync_devices(defer_connect=True)
    assert order == ["start br", "end br defer=True", "start dep", "end dep defer=True"]


@pytest.mark.asyncio
async def test_each_failure_is_logged_and_the_rest_go_on(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "new", "driver": "generic_tcp", "name": "New", "config": {"host": "1.2.3.4"}},
        {"id": "changed", "driver": "generic_tcp", "name": "Changed", "config": {"host": "1.2.3.5"}},
    ])
    eng.devices = _manager({
        "changed": {"id": "changed", "name": "Before", "driver": "generic_tcp", "config": {}},
        "gone": {"id": "gone"},
    })
    eng.devices.add_device.side_effect = RuntimeError("port in use")
    eng.devices.update_device.side_effect = RuntimeError("driver crashed")
    eng.state.set("device.gone.power", "on", source="device")
    eng.state.set("device.gone.connected", True, source="device")
    with caplog.at_level(logging.INFO, logger=ENGINE_LOG):
        await eng._sync_devices()
    lines = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == ENGINE_LOG]
    assert (logging.ERROR, "Failed to add device 'new' during sync: port in use") in lines
    assert (logging.ERROR, "Failed to update device 'changed' during sync: driver crashed") in lines
    assert (logging.INFO, "Cleaned up 2 orphaned state key(s) for removed device 'gone'") in lines
    eng.devices.remove_device.assert_awaited_once_with("gone")
    assert eng.state.get_namespace("device.gone.") == {}


@pytest.mark.asyncio
async def test_a_removed_device_with_no_state_logs_nothing(tmp_path, caplog):
    eng = _engine(tmp_path, [])
    eng.devices = _manager({"gone": {"id": "gone"}})
    with caplog.at_level(logging.INFO, logger=ENGINE_LOG):
        await eng._sync_devices()
    eng.devices.remove_device.assert_awaited_once_with("gone")
    assert not [r for r in caplog.records if r.name == ENGINE_LOG and "orphaned" in r.getMessage()]


@pytest.mark.parametrize("diff,cancelled", [
    (ProjectDiff(device_groups=True), True),
    (ProjectDiff(macros=True), True),
    (ProjectDiff(variables=True), False),
])
@pytest.mark.asyncio
async def test_running_macros_are_cancelled_only_when_their_definitions_change(tmp_path, diff, cancelled):
    eng = _engine(tmp_path, [])
    eng.macros.cancel_all = AsyncMock()
    await eng._reconcile(diff, ProjectOrigin.EDIT)
    assert eng.macros.cancel_all.await_count == (1 if cancelled else 0)


@pytest.mark.asyncio
async def test_a_variable_reconcile_logs_the_keys_it_cleaned_up(tmp_path, caplog):
    eng = _engine(tmp_path, [])
    eng.state.set("var.left_over", 1, source="system")
    eng.state.set("var.also_gone", 2, source="system")
    with caplog.at_level(logging.INFO, logger=ENGINE_LOG):
        await eng._reconcile(ProjectDiff(variables=True), ProjectOrigin.EDIT)
    assert "Cleaned up 2 orphaned variable state key(s)" in [
        r.getMessage() for r in caplog.records if r.name == ENGINE_LOG
    ]
    assert eng.state.get_namespace("var.") == {}


@pytest.mark.asyncio
async def test_a_variable_reconcile_seeds_new_variables_and_keeps_the_rest(tmp_path):
    eng = _engine(tmp_path, [])
    from openavc.core.project_loader import VariableConfig

    eng.project.variables = [
        VariableConfig(id="kept", type="string", default="d1"),
        VariableConfig(id="new", type="string", default="d2"),
    ]
    eng.state.set("var.kept", "running", source="ui")
    await eng._reconcile(ProjectDiff(variables=True), ProjectOrigin.EDIT)
    assert eng.state.get("var.kept") == "running"
    assert eng.state.get("var.new") == "d2"


@pytest.mark.asyncio
async def test_a_script_that_fails_to_reload_is_logged(tmp_path, caplog):
    eng = _engine(tmp_path, [])
    eng.scripts = MagicMock()
    eng.scripts.reload_script.side_effect = lambda cfg: (
        {"status": "error", "error": "SyntaxError: bad"} if cfg["id"] == "broken" else {"status": "ok"}
    )
    diff = ProjectDiff(scripts=True, scripts_to_reload=[{"id": "broken"}, {"id": "fine"}],
                       scripts_to_unload=["old"])
    with caplog.at_level(logging.ERROR, logger=ENGINE_LOG):
        await eng._reconcile(diff, ProjectOrigin.EDIT)
    eng.scripts.unload_script.assert_called_once_with("old")
    assert [c.args[0]["id"] for c in eng.scripts.reload_script.call_args_list] == ["broken", "fine"]
    assert [r.getMessage() for r in caplog.records if r.name == ENGINE_LOG] == [
        "Script 'broken' failed to reload: SyntaxError: bad",
    ]


def _recording(eng, order):
    """Stand-ins for the device-side calls a reconcile makes, in call order."""
    eng.devices = _manager({})

    async def sync(*, defer_connect=False):
        order.append(f"sync defer={defer_connect}")

    async def orphans(*, defer_connect=False):
        order.append(f"orphans defer={defer_connect}")

    eng._sync_devices = sync
    eng.devices.retry_all_orphans = orphans
    eng._schedule_device_bringup = lambda: order.append("bring-up")


@pytest.mark.parametrize("diff", [ProjectDiff(devices=True), ProjectDiff(connections=True)])
@pytest.mark.asyncio
async def test_a_device_change_syncs_then_promotes_orphans_then_asks_for_a_bring_up(tmp_path, diff):
    eng = _engine(tmp_path, [])
    order = []
    _recording(eng, order)
    await eng._reconcile(diff, ProjectOrigin.EDIT)
    assert order == ["sync defer=True", "orphans defer=True", "bring-up"]


@pytest.mark.asyncio
async def test_a_rollback_re_syncs_devices_and_asks_for_a_bring_up_without_orphans(tmp_path):
    eng = _engine(tmp_path, [])
    order = []
    _recording(eng, order)
    await eng._rollback_reconcile(ProjectDiff(devices=True))
    assert order == ["sync defer=True", "bring-up"]


@pytest.mark.parametrize("diff,macros,groups", [
    (ProjectDiff(macros=True), True, False),
    (ProjectDiff(device_groups=True), False, True),
])
@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.asyncio
async def test_only_the_changed_definitions_are_handed_to_the_macro_engine(
        tmp_path, diff, macros, groups, rollback):
    eng = _engine(tmp_path, [])
    eng.project.device_groups = []
    eng.macros.load_macros = MagicMock()
    eng.macros.load_groups = MagicMock()
    if rollback:
        await eng._rollback_reconcile(diff)
    else:
        await eng._reconcile(diff, ProjectOrigin.EDIT)
    assert eng.macros.load_macros.call_count == (1 if macros else 0)
    assert eng.macros.load_groups.call_count == (1 if groups else 0)
    if groups:
        assert eng.macros.load_groups.call_args.args == ([],)


@pytest.mark.asyncio
async def test_a_rollback_restarts_triggers_without_startup_triggers(tmp_path):
    eng = _engine(tmp_path, [])
    order = []
    eng.triggers.stop = AsyncMock(side_effect=lambda: order.append("stop"))
    eng.triggers.load_triggers = MagicMock(side_effect=lambda macros: order.append("load"))
    eng.triggers.start = AsyncMock(side_effect=lambda **k: order.append(f"start {k}"))
    await eng._rollback_reconcile(ProjectDiff(macros=True))
    assert order == ["stop", "load", "start {'fire_startup': False}"]


@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.asyncio
async def test_a_variable_change_rebinds_and_rewatches_the_variables(tmp_path, rollback):
    eng = _engine(tmp_path, [])
    order = []
    eng._bind_variable_sources = lambda: order.append("bind")
    eng._register_variable_validation = lambda: order.append("validate")
    if rollback:
        await eng._rollback_reconcile(ProjectDiff(variables=True))
    else:
        await eng._reconcile(ProjectDiff(variables=True), ProjectOrigin.EDIT)
    assert order == ["bind", "validate"]
