"""What a start and a stop do, and in what order, pinned on a real engine.

The order is the contract: macros load before triggers, devices and
variables before scripts, the listeners that bridge events to the
WebSocket after everything that can raise them, ``system.started`` before
the triggers start (so startup triggers fire), and on the way down
everything that can fire a macro stops before macros are cancelled, which
happens before the devices they command disconnect.

Also pinned: how a failing part of the start is counted and reported, how
the update manager is handed to a cloud agent, and that a stop waits for a
bookkeeping save that is still running.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from openavc.core.engine import Engine
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver

ENGINE_LOG = "openavc.core.engine"


class _AcmeWidget(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget_lifecycle",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "config_schema": {"host": {"type": "string", "label": "Host"}},
        "state_variables": {},
        "commands": {},
    }

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command, params=None):
        return None


@pytest.fixture
def acme_driver():
    register_driver(_AcmeWidget)
    yield
    unregister_driver("acme_widget_lifecycle")


def _project(tmp_path, *, devices=(), plugins=None, scripts=()) -> str:
    project = {
        "version": "0.16.0",
        "project": {"id": "p", "name": "Lifecycle"},
        "variables": [{"id": "v", "type": "string", "default": ""}],
        "macros": [{"id": "m", "name": "M", "steps": []}],
        "devices": [
            {"id": d, "driver": "acme_widget_lifecycle", "name": d, "config": {"host": "10.0.0.1"}}
            for d in devices
        ],
        "device_groups": [],
        "connections": {},
        "scripts": list(scripts),
        "plugins": plugins or {},
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []},
        ]},
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(project), encoding="utf-8")
    return str(path)


def _spy(order, obj, name, label, *, replace=None):
    """Record ``label`` when ``obj.name`` is called, then run it (or ``replace``)."""
    real = replace if replace is not None else getattr(obj, name)
    if asyncio.iscoroutinefunction(real):
        async def wrapper(*a, **k):
            order.append(label)
            return await real(*a, **k)
    else:
        def wrapper(*a, **k):
            order.append(label)
            return real(*a, **k)
    setattr(obj, name, wrapper)


async def _nothing(*_a, **_k):
    return None


def _quiet_services(eng, order):
    """Network services recorded but never started: no advertiser, no cloud."""
    _spy(order, eng, "_start_isc", "isc")
    _spy(order, eng, "_start_mdns_advertiser", "mdns", replace=_nothing)
    _spy(order, eng, "_start_cloud_agent", "cloud", replace=_nothing)


@pytest.fixture
def no_update_checks(monkeypatch):
    from openavc.updater.manager import UpdateManager
    monkeypatch.setattr(UpdateManager, "start_auto_check", _nothing)
    monkeypatch.setattr(UpdateManager, "stop_auto_check", _nothing)


@pytest.mark.asyncio
async def test_start_runs_its_stages_in_order(tmp_path, monkeypatch, acme_driver, no_update_checks):
    from openavc.core.script_engine import ScriptEngine
    from openavc.core.state_persister import StatePersister

    eng = Engine(_project(tmp_path, devices=["d1"]))
    order: list[str] = []
    _spy(order, eng.help, "declare_keys", "system keys")
    _spy(order, eng, "_load_project_safe", "load project")
    _spy(order, eng, "_bind_variable_sources", "bind variables")
    _spy(order, eng, "_register_variable_validation", "validate variables")
    _spy(order, eng.macros, "load_macros", "macros")
    _spy(order, eng.macros, "load_groups", "groups")
    _spy(order, eng.devices, "add_device", "add device")
    _spy(order, eng, "_record_push_ports", "push ports")
    _spy(order, eng.plugin_loader, "scan_plugins", "scan plugins")
    _spy(order, eng, "_move_logins_at_start", "plugin logins")
    _spy(order, eng.triggers, "load_triggers", "load triggers")
    _spy(order, eng.learned_config, "start", "learned config")
    _quiet_services(eng, order)
    _spy(order, eng.file_staging, "start", "file staging")
    _spy(order, eng.triggers, "start", "triggers start")
    _spy(order, eng, "_start_panel_software", "panel software")
    real_emit = eng.events.emit

    async def emit(event, *a, **k):
        if event == "system.started":
            order.append("system.started")
        return await real_emit(event, *a, **k)

    eng.events.emit = emit
    real_load_scripts = ScriptEngine.load_scripts
    real_persister_start = StatePersister.start

    def load_scripts(self, *a, **k):
        order.append("scripts")
        return real_load_scripts(self, *a, **k)

    def persister_start(self, *a, **k):
        order.append("persister")
        return real_persister_start(self, *a, **k)

    monkeypatch.setattr(ScriptEngine, "load_scripts", load_scripts)
    monkeypatch.setattr(StatePersister, "start", persister_start)
    try:
        await eng.start()
        assert order == [
            "system keys", "load project", "persister", "bind variables",
            "validate variables", "macros", "groups", "add device", "push ports",
            "scan plugins", "plugin logins", "scripts", "load triggers",
            "learned config", "isc", "mdns", "cloud", "file staging",
            "system.started", "triggers start", "panel software",
        ]
        assert eng.state.get("system.started") is True
        assert eng.state.get("system.startup_errors") == 0
        assert eng.state.get("system.project_name") == "Lifecycle"
        from openavc.core import project_recovery, time_zone
        assert eng.state.get(project_recovery.KEY_STATE) == project_recovery.NONE
        assert eng.state.get("system.time_zone") == time_zone.current_name()
        assert {k: eng.state.get(f"system.{k}") for k in (
            "update_available", "update_channel", "update_status", "update_progress", "update_error",
        )} == {"update_available": "", "update_channel": "stable", "update_status": "idle",
               "update_progress": 0, "update_error": ""}
        assert eng.state.get("system.version") and eng.state.get("system.deployment_type")
        assert eng._running is True
        # The listeners that carry engine events to the WebSocket.
        assert len(eng._event_sub_ids) >= 18
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_start_publishes_a_recovery_left_on_record(tmp_path, no_update_checks):
    from openavc.core import project_recovery

    eng = Engine(_project(tmp_path))
    project_recovery.record(
        tmp_path, outcome=project_recovery.RESET, reason=project_recovery.UNREADABLE,
    )
    _quiet_services(eng, [])
    try:
        await eng.start()
        assert eng.state.get(project_recovery.KEY_STATE) == project_recovery.RESET
        assert eng.state.get(project_recovery.KEY_REASON) == project_recovery.UNREADABLE
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_start_counts_and_reports_each_part_that_failed(
        tmp_path, monkeypatch, caplog, acme_driver, no_update_checks):
    from openavc.core.script_engine import ScriptEngine

    eng = Engine(_project(tmp_path, devices=["good", "bad"]))
    order: list[str] = []
    _quiet_services(eng, order)
    real_add = eng.devices.add_device

    async def add_device(config, **kwargs):
        if config["id"] == "bad":
            raise RuntimeError("no such port")
        return await real_add(config, **kwargs)

    def scan_plugins():
        raise RuntimeError("plugin folder unreadable")

    def load_scripts(self, *_a, **_k):
        raise RuntimeError("script folder unreadable")

    eng.devices.add_device = add_device
    eng.plugin_loader.scan_plugins = scan_plugins
    monkeypatch.setattr(ScriptEngine, "load_scripts", load_scripts)
    try:
        with caplog.at_level(logging.INFO, logger=ENGINE_LOG):
            await eng.start()
        assert eng.state.get("system.startup_errors") == 3
        engine_lines = [
            (r.levelno, r.getMessage()) for r in caplog.records if r.name == ENGINE_LOG
        ]
        assert (logging.ERROR, "Failed to add device 'bad': no such port") in engine_lines
        assert (logging.ERROR, "Plugin system failed to start") in engine_lines
        assert (logging.ERROR, "Script engine failed to load scripts") in engine_lines
        assert (
            logging.WARNING,
            "Engine started with 3 error(s): Device 'bad': no such port; "
            "Plugins: plugin folder unreadable; Scripts: script folder unreadable",
        ) in engine_lines
        # A failed part does not stop the rest of the start.
        assert eng.state.get("system.started") is True
        assert "good" in {d["id"] for d in eng.devices.list_devices()}
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_start_starts_the_project_plugins_with_their_entries(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path, plugins={"acme_thing": {"enabled": True, "config": {"a": 1}}}))
    _quiet_services(eng, [])
    seen = []

    async def start_plugins(plugins):
        seen.append(plugins)

    eng.plugin_loader.start_plugins = start_plugins
    try:
        await eng.start()
        assert seen == [{"acme_thing": {"enabled": True, "config": {"a": 1}}}]
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_start_without_project_plugins_starts_none(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    seen = []

    async def start_plugins(plugins):
        seen.append(plugins)

    eng.plugin_loader.start_plugins = start_plugins
    try:
        await eng.start()
        assert seen == []
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_start_hands_the_update_manager_to_the_cloud_agent(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    handler = SimpleNamespace(_update_manager=None)

    async def start_cloud():
        eng.cloud_agent = SimpleNamespace(_command_handler=handler, stop=_nothing, _connected=False)

    eng._start_cloud_agent = start_cloud
    try:
        await eng.start()
        assert eng.update_manager is not None
        assert handler._update_manager is eng.update_manager
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_an_update_manager_that_cannot_start_is_left_out(tmp_path, monkeypatch, caplog):
    from openavc.updater.manager import UpdateManager

    async def boom(self):
        raise RuntimeError("no network")

    monkeypatch.setattr(UpdateManager, "start_auto_check", boom)
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    try:
        with caplog.at_level(logging.ERROR, logger=ENGINE_LOG):
            await eng.start()
        assert eng.update_manager is None
        assert eng.panel_software is None
        assert "Update manager failed to start — continuing without updates" in [
            r.getMessage() for r in caplog.records if r.name == ENGINE_LOG
        ]
        assert eng.state.get("system.started") is True
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_stop_tears_down_in_order(tmp_path, acme_driver, no_update_checks):
    eng = Engine(_project(tmp_path, devices=["d1"]))
    _quiet_services(eng, [])
    await eng.start()
    order: list[str] = []

    def fake(label):
        async def stop():
            order.append(label)
        return SimpleNamespace(stop=stop, stop_auto_check=stop, _connected=False, _peers={})

    eng.update_manager = fake("update manager")
    eng.cloud_agent = fake("cloud")
    eng.mdns_advertiser = fake("mdns")
    eng.isc = fake("isc")
    _spy(order, eng, "_stop_panel_software", "panel software")
    _spy(order, eng.learned_config, "stop", "learned config")
    _spy(order, eng.persister, "stop", "persister")
    _spy(order, eng.triggers, "stop", "triggers")
    _spy(order, eng.scripts, "unload_all", "scripts")
    _spy(order, eng.plugin_loader, "stop_all", "plugins")
    _spy(order, eng.ws, "stop_state_flush", "state flush")
    _spy(order, eng.macros, "cancel_all", "macros")
    _spy(order, eng.command_runs, "stop", "command runs")
    _spy(order, eng.file_staging, "stop", "file staging")
    _spy(order, eng.devices, "disconnect_all", "devices")
    _spy(order, eng.simulation, "shutdown", "simulation")
    _spy(order, eng.state, "flush_pending_events", "pending events")
    real_emit = eng.events.emit

    async def emit(event, *a, **k):
        if event == "system.stopping":
            order.append("system.stopping")
        return await real_emit(event, *a, **k)

    eng.events.emit = emit
    subscribed = len(eng._event_sub_ids) + len(eng._state_sub_ids)
    await eng.stop()
    assert subscribed > 0
    assert eng._event_sub_ids == [] and eng._state_sub_ids == []
    assert order == [
        "update manager", "panel software", "learned config", "persister",
        "system.stopping", "triggers", "cloud", "mdns", "isc", "scripts",
        "plugins", "state flush", "macros", "command runs", "file staging",
        "devices", "simulation", "pending events",
    ]
    assert eng.cloud_agent is None and eng.mdns_advertiser is None and eng.isc is None
    assert eng.state.get("system.started") is False
    assert eng._running is False


@pytest.mark.asyncio
async def test_stop_ends_the_background_tasks(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    await eng.start()

    async def forever():
        await asyncio.Event().wait()

    eng._os_programs_task = asyncio.create_task(forever())
    backup, sweep, programs = (
        eng._periodic_backup_task, eng._panel_sweep_task, eng._os_programs_task,
    )
    await asyncio.sleep(0)
    await eng.stop()
    # The backup loop ends itself on the cancel; the other two end cancelled.
    assert backup.done() and not backup.cancelled()
    assert sweep.cancelled() and programs.cancelled()
    # The sweep and the programs slots are emptied; the backup slot keeps its task.
    assert eng._panel_sweep_task is None
    assert eng._os_programs_task is None
    assert eng._periodic_backup_task is backup


@pytest.mark.asyncio
async def test_stop_waits_for_a_bookkeeping_save_before_tearing_down(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    await eng.start()
    order: list[str] = []

    async def save():
        await asyncio.sleep(0.01)
        order.append("bookkeeping saved")

    eng._bookkeeping_task = asyncio.create_task(save())
    _spy(order, eng.triggers, "stop", "triggers")
    await eng.stop()
    assert order == ["bookkeeping saved", "triggers"]


@pytest.mark.asyncio
async def test_a_bookkeeping_save_that_does_not_finish_is_reported(
        tmp_path, monkeypatch, caplog, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    await eng.start()

    async def never():
        await asyncio.Event().wait()

    task = asyncio.create_task(never())
    eng._bookkeeping_task = task
    real_wait_for = asyncio.wait_for

    async def wait_for(aw, timeout):
        if aw is task:
            raise asyncio.TimeoutError()
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        await eng.stop()
    lines = [r.getMessage() for r in caplog.records if r.name == ENGINE_LOG]
    assert "Bookkeeping persist did not finish before shutdown" in lines
    assert "Bookkeeping persist did not finish after shutdown" in lines
    assert eng.state.get("system.started") is False
    task.cancel()


# ── before the project loads ──

_HOST_STEPS = (
    ("openavc.core.panel_devices", "settle_access_default", "panel access"),
    ("openavc.listen", "current", "who can connect"),
    ("openavc.listen", "save_settled", "save who can connect"),
    ("openavc.system_config.SystemConfig", "ensure_file", "system.json"),
    ("openavc.api.auth", "apply_reset_request", "login reset"),
    ("openavc.system_config.SystemConfig", "migrate_admin_password", "hash password"),
    ("openavc.system_config.SystemConfig", "migrate_api_key", "hash api key"),
    ("openavc.api.auth", "warn_if_api_key_is_sole_credential", "api key warning"),
    ("openavc.system_config", "migrate_legacy_repos", "legacy repos"),
    ("openavc.system_config", "migrate_legacy_project_dir", "legacy project dir"),
    ("openavc.drivers.driver_loader", "load_builtin_drivers", "drivers"),
)


def _record_host_steps(monkeypatch, order, *, failing=None, calls=None):
    import importlib

    for where, name, label in _HOST_STEPS:
        module_name, _, cls = where.partition(".SystemConfig")
        target = importlib.import_module(module_name)
        if cls or where.endswith("SystemConfig"):
            target = target.SystemConfig
        real = getattr(target, name)

        def make(real, label, is_method):
            def step(*args, **kwargs):
                order.append(label)
                if calls is not None:
                    calls[label] = args[1:] if is_method else args
                if label == failing:
                    raise RuntimeError(f"{label} broke")
                return real(*args, **kwargs)
            return step

        monkeypatch.setattr(target, name, make(real, label, target.__name__ == "SystemConfig"))


@pytest.mark.asyncio
async def test_the_host_is_settled_in_order_before_the_project_loads(tmp_path, monkeypatch, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    order, calls = [], {}
    _record_host_steps(monkeypatch, order, calls=calls)
    _spy(order, eng, "_load_project_safe", "load project")
    try:
        await eng.start()
        assert order == [label for _, _, label in _HOST_STEPS] + ["load project"]
        assert calls["panel access"][1] == tmp_path / ".instance_id"
    finally:
        await eng.stop()


@pytest.mark.parametrize("failing,line", [
    ("panel access", "Could not settle the Panel access default"),
    ("who can connect", "Could not settle who can connect"),
    ("save who can connect", "Could not save who can connect"),
    ("login reset", "Could not apply the Programmer login reset"),
    ("hash password", "Could not convert the stored admin password to a hash"),
    ("hash api key", "Could not convert the stored API key to a hash"),
    ("api key warning", "Could not check the admin credential posture"),
    ("legacy repos", "migrate_legacy_repos failed"),
    ("legacy project dir", "migrate_legacy_project_dir failed"),
])
@pytest.mark.asyncio
async def test_a_host_step_that_fails_is_logged_and_the_start_goes_on(
        tmp_path, monkeypatch, caplog, no_update_checks, failing, line):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    order = []
    _record_host_steps(monkeypatch, order, failing=failing)
    try:
        with caplog.at_level(logging.ERROR, logger=ENGINE_LOG):
            await eng.start()
        assert order == [label for _, _, label in _HOST_STEPS]
        errors = [r for r in caplog.records if r.name == ENGINE_LOG and r.levelno == logging.ERROR]
        assert [r.getMessage() for r in errors] == [line]
        assert errors[0].exc_info is not None
        assert eng.state.get("system.started") is True
    finally:
        await eng.stop()


@pytest.mark.parametrize("failing", ["system.json", "drivers"])
@pytest.mark.asyncio
async def test_system_json_and_the_driver_registry_are_not_optional(
        tmp_path, monkeypatch, no_update_checks, failing):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    _record_host_steps(monkeypatch, [], failing=failing)
    with pytest.raises(RuntimeError, match=f"{failing} broke"):
        await eng.start()
    await eng.stop()


# ── the rest of the start ──


@pytest.mark.asyncio
async def test_start_prewarms_the_network_info_off_the_loop(tmp_path, no_update_checks):
    import threading

    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    threads = []

    def detect():
        threads.append(threading.current_thread() is threading.main_thread())
        return ("127.0.0.1", "h", [])

    eng._detect_network_info = detect
    try:
        await eng.start()
        for _ in range(100):
            if threads:
                break
            await asyncio.sleep(0.01)
        assert threads == [False]
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_start_carries_engine_events_to_the_websocket(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    persisted = []

    async def pending_applied(event, payload):
        persisted.append(payload)

    eng._on_pending_settings_applied = pending_applied
    try:
        await eng.start()
        sent = []

        async def broadcast(message, *, client_type=None):
            sent.append((message["type"], client_type))

        eng.broadcast_ws = broadcast
        events = [
            "macro.started.m", "macro.progress.m", "macro.completed.m", "macro.cancelled.m",
            "macro.error.m", "macro.step_error.m", "macro.skipped.m",
            "trigger.fired", "trigger.skipped", "trigger.pending", "trigger.queued",
            "trigger.completed", "script.error",
            "plugin.started", "plugin.stopped", "plugin.error", "plugin.missing",
        ]
        for event in events:
            await eng.events.emit(event, {})
        await eng.events.emit("device.pending_settings_applied", {"device_id": "d1"})
        assert sent == [
            ("macro.started", None), ("macro.progress", None), ("macro.completed", None),
            ("macro.cancelled", None), ("macro.error", None), ("macro.step_error", None),
            ("macro.skipped", None),
            ("trigger.fired", None), ("trigger.skipped", None), ("trigger.pending", None),
            ("trigger.queued", None), ("trigger.completed", None),
            ("script.error", "programmer"),
            ("plugin.started", None), ("plugin.stopped", None), ("plugin.error", None),
            ("plugin.missing", None),
        ]
        assert persisted == [{"device_id": "d1"}]
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_start_that_moved_device_logins_remembers_it_backed_up(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    backups = []

    async def back_up():
        backups.append("backup")
        return True

    eng._back_up_before_moving_logins = back_up
    real_move = eng._move_logins
    calls = {"n": 0}

    def move_logins(project, *, typed):
        calls["n"] += 1
        if calls["n"] == 1:
            return project.model_copy(deep=True)
        return real_move(project, typed=typed)

    eng._move_logins = move_logins
    try:
        await eng.start()
        assert backups == ["backup"]
        assert eng._logins_backed_up_at_start is True
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_start_publishes_the_asset_catalog(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    try:
        await eng.start()
        assert json.loads(eng.state.get("project.assets")) == []
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_stop_forgets_a_bring_up_nobody_has_started(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    await eng.start()
    eng._bringup_requested = True
    await eng.stop()
    assert eng._bringup_requested is False


@pytest.mark.asyncio
async def test_after_a_stop_state_changes_no_longer_reach_the_websocket_hub(tmp_path, no_update_checks):
    eng = Engine(_project(tmp_path))
    _quiet_services(eng, [])
    pushed = []
    real = eng.ws.on_state_change

    def on_state_change(*args, **kwargs):
        pushed.append(args[0])
        return real(*args, **kwargs)

    eng.ws.on_state_change = on_state_change
    await eng.start()
    eng.state.set("var.v", "while running", source="ui")
    assert "var.v" in pushed
    await eng.stop()
    pushed.clear()
    eng.state.set("var.v", "after", source="ui")
    assert pushed == []
