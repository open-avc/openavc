"""The reconcile's stages, forward and back.

A change that fails part-way through the reconcile leaves the running
system where it was before the change: every stage it entered is undone,
last first, the one that raised included. The parametrized test fails the
apply right after each stage in turn, for an edit and for a whole new
project, and compares what the system runs (variables and their saved
values, devices, plugins, macros, groups, triggers, scripts, the mesh
settings, the advertised name, the project file) with what it ran before. A
stage added without an undo that puts back what it changed fails here. The
file is written only once every stage has run, so a change whose write fails
(a full disk) is undone the same way.

The saved-value tests are the case that went wrong: a deleted variable's
saved value was gone from state.json after a failed apply, and the next edit
brought the variable back on its default.
"""

from __future__ import annotations

import copy
import errno
import json
import textwrap

from unittest.mock import MagicMock

import pytest

from openavc.core import project_reconcile
from openavc.core.device_records import RUNNING, DeviceRecord
from openavc.core.engine import Engine
from openavc.core.project_diff import ProjectDiff, ProjectOrigin
from openavc.core.project_loader import ProjectConfig, ProjectSaveError, load_project
from openavc.core.project_reconcile import RECONCILE_STAGES, ReconcileRun
from openavc.core.script_engine import ScriptEngine
from openavc.core.project_changes import ProjectNotAppliedError


class _Devices:
    """A device manager that keeps the configs it was handed, nothing more."""

    def __init__(self) -> None:
        self.running: dict[str, dict] = {}

    def device_records(self):
        return [DeviceRecord(did, RUNNING, cfg) for did, cfg in self.running.items()]

    def build_failed(self, device_id):
        return False

    def get_device_config(self, device_id):
        return self.running.get(device_id)

    def why_not_running(self, device_id):
        return None

    async def keep_unresolved(self, error):
        return True

    async def add_device(self, config, *, defer_connect=False):
        self.running[config["id"]] = config

    async def update_device(self, device_id, config, *, defer_connect=False):
        self.running[device_id] = config

    async def remove_device(self, device_id):
        self.running.pop(device_id, None)

    async def retry_all_orphans(self, *, defer_connect=False):
        return None

    async def bring_up(self):
        return None


class _Plugins:
    """A plugin loader that keeps which plugins run, and with what config."""

    def __init__(self) -> None:
        self.running: dict[str, dict] = {}

    def get_known_plugin_ids(self):
        return set(self.running)

    def is_running(self, plugin_id):
        return plugin_id in self.running

    def config_differs(self, plugin_id, config):
        return self.running.get(plugin_id) != config

    async def start_plugin(self, plugin_id, config=None):
        self.running[plugin_id] = dict(config or {})
        return True

    async def start_plugins(self, entries):
        return {
            plugin_id: await self.start_plugin(plugin_id, entry.config)
            for plugin_id, entry in entries.items() if entry.enabled
        }

    def why_not_running(self, plugin_id):
        return None

    async def stop_plugin(self, plugin_id):
        self.running.pop(plugin_id, None)

    def remove_plugin_tracking(self, plugin_id):
        self.running.pop(plugin_id, None)

    async def restart_or_apply(self, plugin_id, config):
        self.running[plugin_id] = dict(config)
        return "hot_applied"


class _Mesh:
    """A running ISC mesh that keeps the settings it was last handed."""

    def __init__(self) -> None:
        self.settings: dict = {}

    async def reload(self, **settings):
        self.settings = settings


def _scripts_dir(tmp_path):
    folder = tmp_path / "scripts"
    folder.mkdir(exist_ok=True)
    return folder


def _write_script(tmp_path, name: str, watches: str) -> None:
    (_scripts_dir(tmp_path) / name).write_text(textwrap.dedent(f"""\
        from openavc import on_state_change

        @on_state_change("{watches}")
        async def {name[:-3]}_heard(key, old_value, new_value):
            pass
    """), encoding="utf-8")


def _before() -> dict:
    """The project running before the change."""
    return {
        "openavc_version": "0.17.0",
        "project": {"id": "p", "name": "Old"},
        "variables": [
            {"id": "poke", "type": "string", "default": ""},
            {"id": "volume", "type": "number", "default": 10, "persist": True},
            {"id": "mirror", "type": "string", "default": "", "source_key": "var.poke"},
        ],
        "macros": [{
            "id": "m_base", "name": "Base",
            "steps": [{"action": "state.set", "key": "var.fired", "value": True}],
            "triggers": [{"id": "t_base", "type": "state_change",
                          "state_key": "var.poke", "state_operator": "any"},
                         {"id": "t_gone", "type": "event", "event_pattern": "custom.never",
                          "cooldown_seconds": 600}],
        }],
        "devices": [{"id": "d1", "driver": "generic_tcp", "name": "D1", "config": {}}],
        "device_groups": [{"id": "g1", "name": "G1", "device_ids": ["d1"]}],
        "connections": {"d1": {"host": "192.0.2.10", "port": 23}},
        "scripts": [{"id": "s_kept", "file": "kept.py"},
                    {"id": "s_old", "file": "old.py"}],
        "plugins": {"acme_plugin": {"enabled": True, "config": {"level": 1}}},
        "ui": {"settings": {}, "pages": []},
        "isc": {"enabled": True, "shared_state": ["var.a*"], "peers": [], "auth_key": ""},
    }


def _after() -> dict:
    """A change to every section: a variable deleted and one added, a
    trigger deleted and one added, macros, groups, devices, connections,
    plugins, scripts, the mesh and the name."""
    project = _before()
    project["project"]["name"] = "New"
    project["variables"] = [
        {"id": "poke", "type": "string", "default": ""},
        {"id": "fresh", "type": "string", "default": "new", "persist": True},
        {"id": "mirror", "type": "string", "default": "", "source_key": "var.fresh"},
    ]
    project["macros"][0]["name"] = "Base, renamed"
    project["macros"][0]["triggers"] = project["macros"][0]["triggers"][:1]
    project["macros"].append({
        "id": "m_new", "name": "New", "steps": [],
        "triggers": [{"id": "t_new", "type": "state_change",
                      "state_key": "var.fresh", "state_operator": "any"}],
    })
    project["devices"].append({"id": "d2", "driver": "generic_tcp", "name": "D2", "config": {}})
    project["device_groups"][0]["device_ids"] = ["d1", "d2"]
    project["connections"] = {"d1": {"host": "192.0.2.11", "port": 23},
                              "d2": {"host": "192.0.2.12", "port": 23}}
    project["scripts"] = [{"id": "s_kept", "file": "kept2.py"},
                          {"id": "s_new", "file": "new.py"}]
    project["plugins"] = {"acme_plugin": {"enabled": True, "config": {"level": 2}},
                          "other_plugin": {"enabled": True, "config": {}}}
    project["isc"]["shared_state"] = ["var.b*"]
    return project


async def _running_engine(tmp_path) -> Engine:
    """An engine running ``_before()`` the way a start leaves it: the saved
    value read back, devices and plugins registered, macros, groups and
    triggers loaded, scripts imported and the mesh handed its settings."""
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_before()), encoding="utf-8")
    (tmp_path / "state.json").write_text(json.dumps({"var.volume": 42}), encoding="utf-8")
    for name, watches in (("kept.py", "var.poke"), ("old.py", "var.volume"),
                          ("kept2.py", "var.fresh"), ("new.py", "var.fresh")):
        _write_script(tmp_path, name, watches)

    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    eng.devices = _Devices()
    eng.plugin_loader = _Plugins()
    eng.isc = _Mesh()
    eng.mdns_advertiser = MagicMock()
    eng._load_project_drivers = lambda: None

    eng._start_variables()
    await eng._sync_devices(defer_connect=True)
    await eng._sync_plugins()
    eng._load_macro_definitions(macros=True, groups=True)
    await eng._restart_triggers(fire_startup=False)
    # The trigger the change deletes has fired: its cooldown is running.
    await eng.triggers._fire_macro(eng.triggers._triggers["t_gone"], {})
    eng.scripts = ScriptEngine(eng.state, eng.events, eng.devices, tmp_path, eng.macros)
    eng.scripts.install()
    eng.scripts.load_scripts([s.model_dump() for s in eng.project.scripts])
    await eng._hand_isc_settings()
    return eng


def _what_runs(eng) -> dict:
    """Everything a stage can change, as plain values to compare."""
    triggers = eng.triggers
    return {
        "variables": eng.state.get_namespace("var."),
        "persisted keys": eng.persister.persistent_keys,
        "state.json": json.loads((eng.project_path.parent / "state.json").read_text()),
        "devices": copy.deepcopy(eng.devices.running),
        "plugins": copy.deepcopy(eng.plugin_loader.running),
        "macros": {mid: m.get("name") for mid, m in eng.macros._macros.items()},
        "group g1": eng.macros._groups.members("g1"),
        "triggers": sorted(triggers._triggers),
        "trigger listeners": len(triggers._state_sub_ids),
        # What each trigger keeps across a rebuild: its cooldown stamp, its
        # cooldown baseline, its last run and its last scheduled fire.
        "trigger stamps": eng.state.get_matching("system.trigger.*"),
        "trigger cooldowns": dict(triggers._last_fired_monotonic),
        "trigger last runs": dict(triggers._last_run),
        "trigger schedules": dict(triggers._cron_last_fires),
        "scripts": {
            sid: sorted(loaded.handler_names) for sid, loaded in eng.scripts._scripts.items()
        },
        "mesh": eng.isc.settings.get("shared_state_patterns"),
        "project name": eng.state.get("system.project_name"),
        "project file": eng.project_path.read_bytes(),
    }


def _fail_right_after(eng, stage_name: str) -> None:
    """Make the apply raise once the named stage has done its work."""
    stage = next(s for s in RECONCILE_STAGES if s.name == stage_name)
    forward = getattr(eng, stage.forward)

    async def then_fail(run):
        await forward(run)
        raise RuntimeError(f"failed after {stage_name}")

    setattr(eng, stage.forward, then_fail)


def _stage_cases():
    for stage in RECONCILE_STAGES:
        for origin in ProjectOrigin:
            run = ReconcileRun(ProjectDiff.all_dirty(), origin)
            if stage.needed(run):
                yield pytest.param(stage.name, origin, id=f"{stage.name}-{origin.value}")


@pytest.mark.parametrize("stage_name,origin", list(_stage_cases()))
@pytest.mark.asyncio
async def test_a_change_that_fails_after_any_stage_leaves_the_system_as_it_was(
    tmp_path, stage_name, origin,
):
    eng = await _running_engine(tmp_path)
    before = _what_runs(eng)
    previous = eng.project
    _fail_right_after(eng, stage_name)

    with pytest.raises(ProjectNotAppliedError, match=f"failed after {stage_name}"):
        await eng.apply_project(ProjectConfig(**_after()), origin=origin)

    assert eng.project is previous
    assert _what_runs(eng) == before
    # The name is announced only once a change runs, so a failed one never
    # moved it.
    eng.mdns_advertiser.update_name.assert_not_called()
    # The binding follows its source again, the previous project's.
    eng.state.set("var.poke", "pressed", source="test")
    assert eng.state.get("var.mirror") == "pressed"
    await eng.triggers.stop()
    eng.scripts.unload_all()


@pytest.mark.parametrize("origin", list(ProjectOrigin))
@pytest.mark.asyncio
async def test_a_change_whose_file_cannot_be_written_leaves_the_system_as_it_was(
    tmp_path, monkeypatch, origin,
):
    """Every stage ran, then the write failed (a full disk): each stage is
    undone, and the save's own error is what the caller hears."""
    eng = await _running_engine(tmp_path)
    before = _what_runs(eng)
    previous = eng.project

    async def no_room(path, project):
        raise ProjectSaveError(OSError(errno.ENOSPC, "No space left on device"))

    monkeypatch.setattr("openavc.core.project_changes.save_project_async", no_room)

    with pytest.raises(ProjectNotAppliedError, match="disk is full"):
        await eng.apply_project(ProjectConfig(**_after()), origin=origin)

    assert eng.project is previous
    assert _what_runs(eng) == before
    eng.mdns_advertiser.update_name.assert_not_called()
    await eng.triggers.stop()
    eng.scripts.unload_all()


def test_every_stage_names_a_forward_and_an_undo_the_engine_has():
    for stage in RECONCILE_STAGES:
        assert callable(getattr(Engine, stage.forward, None)), stage.name
        assert callable(getattr(Engine, stage.undo, None)), stage.name
    assert len({stage.name for stage in RECONCILE_STAGES}) == len(RECONCILE_STAGES)


@pytest.mark.asyncio
async def test_the_undo_runs_each_entered_stage_last_first(tmp_path, monkeypatch):
    """The stages entered before the failure, the one that raised included,
    and none after it, undone in the reverse of the order they ran."""
    eng = await _running_engine(tmp_path)
    calls: list[str] = []
    stages = []
    for stage in RECONCILE_STAGES:
        stages.append(stage._replace(
            forward=f"_forward {stage.name}", undo=f"_undo {stage.name}",
        ))

        async def forward(run, name=stage.name):
            calls.append(f"forward {name}")
            if name == "scripts":
                raise RuntimeError("the scripts stage failed")

        async def undo(run, name=stage.name):
            calls.append(f"undo {name}")

        setattr(eng, f"_forward {stage.name}", forward)
        setattr(eng, f"_undo {stage.name}", undo)
    monkeypatch.setattr(project_reconcile, "RECONCILE_STAGES", tuple(stages))

    with pytest.raises(ProjectNotAppliedError, match="the scripts stage failed"):
        await eng.apply_project(ProjectConfig(**_after()))

    # An edit: every stage but the driver library's, which a load alone runs.
    names = [s.name for s in RECONCILE_STAGES if s.name != "driver library"]
    entered = names[:names.index("scripts") + 1]
    assert calls == ([f"forward {name}" for name in entered]
                     + [f"undo {name}" for name in reversed(entered)])
    await eng.triggers.stop()
    eng.scripts.unload_all()


@pytest.mark.asyncio
async def test_an_undo_that_fails_does_not_stop_the_ones_before_it(tmp_path, caplog):
    eng = await _running_engine(tmp_path)
    before = _what_runs(eng)

    async def mesh_down():
        raise RuntimeError("the mesh is down")

    eng._reload_isc = mesh_down
    with pytest.raises(ProjectNotAppliedError, match="the mesh is down"):
        await eng.apply_project(ProjectConfig(**_after()))

    assert any("Rollback could not undo the ISC stage" in r.getMessage()
               for r in caplog.records)
    after = _what_runs(eng)
    assert {k: v for k, v in after.items() if k != "mesh"} == {
        k: v for k, v in before.items() if k != "mesh"}
    await eng.triggers.stop()
    eng.scripts.unload_all()


# --- A saved variable value -------------------------------------------------


def _saved(eng) -> dict:
    return json.loads((eng.project_path.parent / "state.json").read_text())


def _fail_once(eng, name: str) -> None:
    """Make the engine method ``name`` raise on its first call only."""
    real = getattr(eng, name)
    calls = []

    def once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(f"{name} failed")
        return real(*args, **kwargs)

    setattr(eng, name, once)


def _without_volume(eng) -> ProjectConfig:
    edited = eng.project.model_copy(deep=True)
    edited.variables = [v for v in edited.variables if v.id != "volume"]
    return edited


@pytest.mark.asyncio
async def test_a_deleted_variables_saved_value_survives_a_failed_apply(tmp_path):
    eng = await _running_engine(tmp_path)
    assert eng.state.get("var.volume") == 42
    # The bindings stage runs after the variables stage has deleted the key.
    _fail_once(eng, "_register_variable_validation")

    with pytest.raises(ProjectNotAppliedError, match="_register_variable_validation failed"):
        await eng.apply_project(_without_volume(eng))

    assert _saved(eng)["var.volume"] == 42
    assert eng.state.get("var.volume") == 42
    assert "var.volume" in eng.persister.persistent_keys

    # The next edit keeps the value rather than seeding the default.
    def poke_default(project):
        project.variables[0].default = "x"

    await eng.apply_project_edit(poke_default)
    assert eng.state.get("var.volume") == 42
    assert _saved(eng)["var.volume"] == 42
    await eng.triggers.stop()
    eng.scripts.unload_all()


@pytest.mark.asyncio
async def test_a_value_a_failed_apply_stopped_persisting_stays_saved(tmp_path):
    eng = await _running_engine(tmp_path)
    _fail_once(eng, "_register_variable_validation")
    edited = eng.project.model_copy(deep=True)
    edited.variables[1].persist = False

    with pytest.raises(ProjectNotAppliedError, match="_register_variable_validation failed"):
        await eng.apply_project(edited)

    assert _saved(eng)["var.volume"] == 42
    assert "var.volume" in eng.persister.persistent_keys
    await eng.triggers.stop()
    eng.scripts.unload_all()


@pytest.mark.asyncio
async def test_a_value_a_failed_apply_bound_to_a_source_is_put_back(tmp_path):
    eng = await _running_engine(tmp_path)
    eng.state.set("var.poke", "pressed", source="test")
    edited = eng.project.model_copy(deep=True)
    edited.variables[1].source_key = "var.poke"
    # The bindings stage fails once the new binding has written the value.
    _fail_once(eng, "_register_variable_validation")

    with pytest.raises(ProjectNotAppliedError, match="_register_variable_validation failed"):
        await eng.apply_project(edited)

    assert eng.state.get("var.volume") == 42
    assert _saved(eng)["var.volume"] == 42
    # No longer bound: poke moves on its own.
    eng.state.set("var.poke", "again", source="test")
    assert eng.state.get("var.volume") == 42
    await eng.triggers.stop()
    eng.scripts.unload_all()


@pytest.mark.asyncio
async def test_a_variable_a_failed_apply_added_is_gone_again(tmp_path):
    eng = await _running_engine(tmp_path)
    _fail_once(eng, "_register_variable_validation")
    edited = ProjectConfig(**{**_before(), "variables": _after()["variables"]})

    with pytest.raises(ProjectNotAppliedError):
        await eng.apply_project(edited)

    assert eng.state.get("var.fresh") is None
    assert "fresh" not in eng.state.get_namespace("var.")
    assert "var.fresh" not in eng.persister.persistent_keys
    assert "var.fresh" not in _saved(eng)
    await eng.triggers.stop()
    eng.scripts.unload_all()
