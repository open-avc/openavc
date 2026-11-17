"""What a project change says about the parts of it the system could not run.

A change that is taken in and running can still leave a part of it not
running: a device whose driver raised setting it up, a plugin that is not
installed or whose start raised, a script that failed to load. The change's
outcome names each one, with the reason its own surface publishes (a device's
record, the plugin loader's status, the script engine's load error), and each
part keeps saying so on that surface until it is fixed: a later change that
does not touch it leaves it as it is, and there is no count for one to zero.

An invented ``acme_parts_widget`` driver stands in for every device; its
constructor raises while ``_Widget.refuse`` holds an exception.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from openavc.core.connection_fault import DRIVER_FAILED
from openavc.core.engine import Engine
from openavc.core.plugin_loader import register_plugin_class
from openavc.core.project_loader import ProjectConfig, load_project
from openavc.core.script_engine import ScriptEngine
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY

DRIVER = "acme_parts_widget"
ABSENT_PLUGIN = "acme_absent_plugin"
REFUSING_PLUGIN = "acme_refusing_plugin"
ENGINE_LOG = "openavc.core.engine"


class _Widget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Parts Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {},
        "commands": {},
        "state_variables": {},
    }
    refuse: BaseException | None = None

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        if type(self).refuse is not None:
            raise type(self).refuse

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _RefusingPlugin:
    """A plugin whose start raises with text the cloud is never told."""

    PLUGIN_INFO = {
        "id": REFUSING_PLUGIN, "name": "Acme Refusing", "version": "1.0.0",
        "author": "Acme", "description": "Refuses to start.", "category": "utility",
        "license": "MIT",
    }

    async def start(self, api):
        raise RuntimeError("acme internal detail 7731")

    async def stop(self):
        return None


@pytest.fixture(autouse=True)
def _driver():
    _DRIVER_REGISTRY[DRIVER] = _Widget
    _Widget.refuse = None
    yield
    _DRIVER_REGISTRY.pop(DRIVER, None)
    _Widget.refuse = None


def _project(**sections) -> dict:
    project = {
        "openavc_version": "0.17.0",
        "project": {"id": "p", "name": "Parts"},
        "devices": [{"id": "good", "driver": DRIVER, "name": "Good", "config": {}}],
        "connections": {"good": {"host": "192.0.2.10", "port": 23}},
        "ui": {"settings": {}, "pages": []},
    }
    project.update(sections)
    return project


async def _engine(tmp_path, project: dict) -> Engine:
    """An engine running ``project`` as a start leaves it, without the
    network services."""
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(project), encoding="utf-8")
    (tmp_path / "scripts").mkdir(exist_ok=True)
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    eng.mdns_advertiser = MagicMock()
    eng._load_project_drivers = lambda: None
    eng.scripts = ScriptEngine(eng.state, eng.events, eng.devices, tmp_path, eng.macros)
    eng.scripts.install()
    await eng._sync_devices(defer_connect=True)
    await eng._sync_plugins()
    return eng


async def _done(eng: Engine) -> None:
    await eng._stop_bringup()
    await eng.triggers.stop()
    eng.scripts.unload_all()
    for record in eng.devices.device_records():
        await eng.devices.remove_device(record.device_id)


@pytest.mark.asyncio
async def test_a_change_names_each_part_it_could_not_run_and_why(tmp_path, caplog):
    eng = await _engine(tmp_path, _project())
    (tmp_path / "scripts" / "broken.py").write_text(
        "import acme_module_that_is_not_there\n", encoding="utf-8",
    )
    register_plugin_class(_RefusingPlugin)
    _Widget.refuse = RuntimeError("the widget refused")
    changed = _project(
        devices=[{"id": "good", "driver": DRIVER, "name": "Good", "config": {}},
                 {"id": "bad", "driver": DRIVER, "name": "Bad", "config": {}}],
        connections={"good": {"host": "192.0.2.10", "port": 23},
                     "bad": {"host": "192.0.2.11", "port": 23}},
        plugins={ABSENT_PLUGIN: {"enabled": True, "config": {}},
                 REFUSING_PLUGIN: {"enabled": True, "config": {}}},
        scripts=[{"id": "s_broken", "file": "broken.py"}],
    )
    try:
        with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
            outcome = await eng.apply_project(ProjectConfig(**changed))

        assert outcome.revision == eng._project_revision
        parts = {(p.kind, p.part_id): p for p in outcome.failed}
        assert {key: p.reason for key, p in parts.items()} == {
            ("device", "bad"): DRIVER_FAILED,
            ("plugin", ABSENT_PLUGIN): "missing",
            ("plugin", REFUSING_PLUGIN): "error",
            ("script", "s_broken"): "load_failed",
        }
        # Each says what the part's own surface says.
        assert parts[("device", "bad")].detail == eng.state.get("device.bad.offline_detail")
        assert "not installed" in parts[("plugin", ABSENT_PLUGIN)].detail
        assert parts[("script", "s_broken")].detail == eng.scripts.get_load_errors()["s_broken"]
        # A plugin's own exception text is never in the outcome, which the
        # cloud's doors answer from too.
        refusing = parts[("plugin", REFUSING_PLUGIN)].detail
        assert "7731" not in refusing
        assert refusing == eng.plugin_loader.couldnt_start_sentence(REFUSING_PLUGIN)
        # The running device the change did not touch is not a failed part.
        assert ("device", "good") not in parts
        # One warning names them.
        warnings = [r.getMessage() for r in caplog.records
                    if r.name == ENGINE_LOG and r.levelno == logging.WARNING]
        assert any("4 parts are not running" in line and "'bad'" in line
                   and ABSENT_PLUGIN in line and "s_broken" in line for line in warnings)
    finally:
        await _done(eng)


@pytest.mark.asyncio
async def test_a_change_with_every_part_running_names_none(tmp_path):
    eng = await _engine(tmp_path, _project())
    try:
        renamed = _project(project={"id": "p", "name": "Renamed"})
        outcome = await eng.apply_project(ProjectConfig(**renamed))
        assert outcome.failed == ()
        assert outcome.revision == eng._project_revision
    finally:
        await _done(eng)


@pytest.mark.asyncio
async def test_a_part_that_failed_keeps_saying_so_through_a_change_that_does_not_touch_it(
    tmp_path, monkeypatch,
):
    """The start's count went to 0 on any clean save while the device it
    counted was still not running; now nothing counts, and the device's own
    record keeps saying why."""
    from openavc.updater.manager import UpdateManager

    async def _nothing(*_a, **_k):
        return None

    monkeypatch.setattr(UpdateManager, "start_auto_check", _nothing)
    monkeypatch.setattr(UpdateManager, "stop_auto_check", _nothing)
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project(
        devices=[{"id": "bad", "driver": DRIVER, "name": "Bad", "config": {}}],
        connections={"bad": {"host": "192.0.2.11", "port": 23}},
    )), encoding="utf-8")
    eng = Engine(str(path))
    monkeypatch.setattr(eng, "_start_mdns_advertiser", _nothing)
    monkeypatch.setattr(eng, "_start_cloud_agent", _nothing)
    _Widget.refuse = RuntimeError("the widget refused")
    try:
        await eng.start()
        assert eng.devices.status_of("bad") == "driver_failed"
        assert eng.state.get("system.startup_errors") is None

        def rename(project):
            project.project.name = "Renamed"

        outcome = await eng.apply_project_edit(rename)

        assert outcome.failed == ()
        assert eng.state.get("system.startup_errors") is None
        assert eng.devices.status_of("bad") == "driver_failed"
        assert eng.state.get("device.bad.offline_reason") == DRIVER_FAILED
    finally:
        await eng.stop()


@pytest.mark.asyncio
async def test_a_plugin_a_change_adds_that_is_not_installed_is_marked_missing(tmp_path):
    """As a start marks it, whether or not the project turns it on; one the
    project turns off is not a part that failed, since nothing asked it to
    run."""
    eng = await _engine(tmp_path, _project())
    heard: list[dict] = []
    eng.events.on("plugin.missing", lambda _event, payload: heard.append(payload))
    try:
        outcome = await eng.apply_project(ProjectConfig(**_project(
            plugins={ABSENT_PLUGIN: {"enabled": False, "config": {}}},
        )))

        assert outcome.failed == ()
        assert eng.state.get(f"plugin.{ABSENT_PLUGIN}.missing") is True
        listed = {p["plugin_id"]: p for p in eng.plugin_loader.list_plugins()}
        assert listed[ABSENT_PLUGIN]["status"] == "missing"
        assert heard == [{"plugin_id": ABSENT_PLUGIN}]

        await eng.apply_project(ProjectConfig(**_project()))
        assert eng.state.get(f"plugin.{ABSENT_PLUGIN}.missing") is None
        assert ABSENT_PLUGIN not in {p["plugin_id"] for p in eng.plugin_loader.list_plugins()}
    finally:
        await _done(eng)


@pytest.mark.asyncio
async def test_a_change_that_fails_leaves_no_part_it_marked(tmp_path):
    """The missing mark a failed change made is undone with it."""
    eng = await _engine(tmp_path, _project())

    async def fail(run):
        raise RuntimeError("failed after the plugins")

    eng._follow_definitions = fail
    changed = _project(
        plugins={ABSENT_PLUGIN: {"enabled": True, "config": {}}},
        macros=[{"id": "m1", "name": "M1", "steps": []}],
    )
    try:
        with pytest.raises(RuntimeError, match="failed after the plugins"):
            await eng.apply_project(ProjectConfig(**changed))
        assert eng.state.get(f"plugin.{ABSENT_PLUGIN}.missing") is None
        assert ABSENT_PLUGIN not in {p["plugin_id"] for p in eng.plugin_loader.list_plugins()}
    finally:
        await _done(eng)


@pytest.mark.asyncio
async def test_an_installed_plugin_the_entry_turns_off_is_not_started(tmp_path):
    """The rule a start and a change share (``PluginLoader.start_plugins``):
    an installed plugin turned off is left stopped and is no part that
    failed; turned on, it starts."""
    eng = await _engine(tmp_path, _project())
    started: list[str] = []

    class _Quiet:
        PLUGIN_INFO = {**_RefusingPlugin.PLUGIN_INFO, "id": "acme_quiet_plugin", "name": "Acme Quiet"}

        async def start(self, api):
            started.append("acme_quiet_plugin")

        async def stop(self):
            return None

    register_plugin_class(_Quiet)
    loader = eng.plugin_loader
    try:
        await loader.start_plugins({"acme_quiet_plugin": {"enabled": False, "config": {}}})
        assert started == [] and not loader.is_running("acme_quiet_plugin")

        await loader.start_plugins({"acme_quiet_plugin": {"enabled": True, "config": {}}})
        assert started == ["acme_quiet_plugin"]
        assert loader.why_not_running("acme_quiet_plugin") is None
        await loader.stop_plugin("acme_quiet_plugin")
    finally:
        await _done(eng)
