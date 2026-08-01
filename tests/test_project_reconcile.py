"""Reconciler behavior: an incremental save touches only what changed.

Pins the scoped-reconcile contract of Engine.apply_project:

- a UI-only edit cancels no running macros, stops no triggers, never
  re-fires startup triggers, and never rescans the driver library or
  touches devices — but still broadcasts the new UI definition
- macro edits cancel running macros and rebuild triggers WITHOUT re-firing
  startup triggers; only a whole new project (LOAD) fires them
- variable edits seed/sweep var.* keys and rebuild bindings without a
  device sync
- script edits reload only the scripts that actually changed
- a reconcile failure rolls the runtime back scoped to the sections the
  failed pass touched, while the saved bytes stay on disk (persist-first)
- the optimistic-concurrency compare-and-set lives inside apply_project
- ProjectDiff classifies section changes and ignores derived fields
"""

import asyncio
import json
from pathlib import Path

import pytest

from server.core.engine import Engine, ProjectRevisionConflictError
from server.core.project_diff import ProjectDiff
from server.core.project_loader import ProjectConfig, load_project
from tests.helpers import wait_for_condition

STARTUP_MACRO = {
    "id": "m_startup",
    "name": "Power On",
    "steps": [{"action": "state.set", "key": "var.fired", "value": True}],
    "triggers": [{
        "id": "trg_startup",
        "type": "startup",
        "enabled": True,
        "delay_seconds": 0,
    }],
}


def _project_dict(*, name="P", variables=None, macros=None, scripts=None,
                  ui_elements=None, devices=None, connections=None):
    return {
        "openavc_version": "0.8.0",
        "project": {"id": "p", "name": name},
        "variables": variables or [],
        "macros": macros or [],
        "devices": devices or [],
        "device_groups": [],
        "connections": connections or {},
        "scripts": scripts or [],
        "plugins": {},
        "ui": {
            "settings": {},
            "pages": [
                # Built at the current version on purpose: this fixture is
                # both written to disk (and so migrated on load) and handed
                # straight to ProjectConfig. An older shape would take the two
                # paths to different UI sections and every edit would look like
                # a UI edit.
                {"id": "main", "name": "Main",
                 "snap": {"enabled": True, "x": 8.3333, "y": 12.5},
                 "elements": ui_elements or [],
                 "layouts": [{"id": "landscape", "orientation": "landscape",
                              "primary": True, "inherits": None,
                              "placements": {}, "hidden": []}]},
            ],
        },
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


def _make_engine(tmp_path, **kwargs) -> Engine:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project_dict(**kwargs)), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    return eng


async def _prime_triggers(eng) -> dict:
    """Load macros + triggers the way Engine.start() would and start them,
    counting macro executions. Returns the counter dict."""
    fire_count = {"n": 0}

    async def counting_execute(macro_id, *a, **k):
        fire_count["n"] += 1

    eng.macros.execute = counting_execute
    macros_data = [m.model_dump() for m in eng.project.macros]
    eng.macros.load_macros(macros_data)
    eng.triggers.load_triggers(macros_data)
    await eng.triggers.start()
    return fire_count


def _spy(counter: dict, key: str, wrapped=None):
    """An async callable that counts, optionally delegating to the original."""
    async def spy(*a, **k):
        counter[key] = counter.get(key, 0) + 1
        if wrapped is not None:
            return await wrapped(*a, **k)
    return spy


# ── ProjectDiff classification ──


def test_diff_identical_projects_marks_nothing_dirty():
    old = ProjectConfig(**_project_dict())
    new = ProjectConfig(**_project_dict())
    diff = ProjectDiff.compute(old, new)
    assert not diff.any_dirty
    assert not diff.requires_trigger_rebuild


def test_diff_ignores_derived_dependency_lists():
    from server.core.project_loader import DriverDependency

    old = ProjectConfig(**_project_dict())
    new = ProjectConfig(**_project_dict())
    new.driver_dependencies = [DriverDependency(
        driver_id="acme_widget", driver_name="Acme Widget",
        version="1.0.0", source="community",
    )]
    diff = ProjectDiff.compute(old, new)
    assert not diff.any_dirty


def test_diff_ui_only_change():
    old = ProjectConfig(**_project_dict())
    new = ProjectConfig(**_project_dict(ui_elements=[
        {"id": "btn1", "type": "button", "grid_area": {"col": 1, "row": 1}},
    ]))
    diff = ProjectDiff.compute(old, new)
    assert diff.ui
    assert not diff.requires_trigger_rebuild
    assert not (diff.devices or diff.connections or diff.variables
                or diff.macros or diff.plugins or diff.scripts or diff.isc
                or diff.project_meta or diff.device_groups)


def test_diff_none_old_project_marks_everything_dirty():
    new = ProjectConfig(**_project_dict())
    diff = ProjectDiff.compute(None, new)
    assert diff.any_dirty and diff.ui and diff.devices and diff.scripts


def test_diff_script_granularity():
    old = ProjectConfig(**_project_dict(scripts=[
        {"id": "keep", "file": "keep.py"},
        {"id": "change", "file": "change.py", "enabled": True},
        {"id": "remove", "file": "remove.py"},
    ]))
    new = ProjectConfig(**_project_dict(scripts=[
        {"id": "keep", "file": "keep.py"},
        {"id": "change", "file": "change.py", "enabled": False},
        {"id": "add", "file": "add.py"},
    ]))
    diff = ProjectDiff.compute(old, new)
    assert diff.scripts
    assert diff.scripts_to_unload == ["remove"]
    reload_ids = [c["id"] for c in diff.scripts_to_reload]
    assert sorted(reload_ids) == ["add", "change"]


# ── EDIT: UI-only save leaves the runtime alone ──


@pytest.mark.asyncio
async def test_ui_edit_does_not_disturb_runtime(tmp_path):
    """The most common save in the product — dragging a button — must not
    cancel macros, stop triggers, re-fire startup automation, rescan the
    driver library, or touch devices."""
    eng = _make_engine(tmp_path, macros=[STARTUP_MACRO])
    fire_count = await _prime_triggers(eng)
    await wait_for_condition(lambda: fire_count["n"] == 1,
                             message="startup trigger did not fire on start")

    counters: dict = {}
    real_triggers_stop = eng.triggers.stop
    eng.macros.cancel_all = _spy(counters, "cancel_all")
    eng.triggers.stop = _spy(counters, "triggers_stop")
    eng._sync_devices = _spy(counters, "sync_devices")
    eng._load_project_drivers = lambda: counters.update(
        drivers=counters.get("drivers", 0) + 1
    )

    broadcasts = []

    async def record_broadcast(msg):
        broadcasts.append(msg)

    eng.broadcast_ws = record_broadcast

    new_project = ProjectConfig(**_project_dict(
        macros=[STARTUP_MACRO],
        ui_elements=[{"id": "btn1", "type": "button",
                      "grid_area": {"col": 1, "row": 1}}],
    ))
    revision = await eng.apply_project(new_project)

    assert revision == 1
    assert counters == {}, f"UI-only edit touched the runtime: {counters}"
    await asyncio.sleep(0.1)
    assert fire_count["n"] == 1, "startup trigger re-fired on a UI edit"

    types = [m["type"] for m in broadcasts]
    assert types == ["ui.definition", "project.reloaded"]
    assert broadcasts[1]["revision"] == 1

    # The bytes landed on disk (persist-first).
    on_disk = json.loads(Path(eng.project_path).read_text(encoding="utf-8"))
    assert on_disk["ui"]["pages"][0]["elements"][0]["id"] == "btn1"

    await real_triggers_stop()


@pytest.mark.asyncio
async def test_non_ui_edit_does_not_broadcast_ui_definition(tmp_path):
    eng = _make_engine(tmp_path)
    broadcasts = []

    async def record_broadcast(msg):
        broadcasts.append(msg)

    eng.broadcast_ws = record_broadcast

    new_project = ProjectConfig(**_project_dict(name="Renamed"))
    await eng.apply_project(new_project)

    types = [m["type"] for m in broadcasts]
    assert types == ["project.reloaded"]
    assert eng.state.get("system.project_name") == "Renamed"


# ── EDIT vs LOAD: macros, triggers, startup firing ──


@pytest.mark.asyncio
async def test_macro_edit_cancels_macros_and_rebuilds_triggers_without_startup(tmp_path):
    eng = _make_engine(tmp_path, macros=[STARTUP_MACRO])
    fire_count = await _prime_triggers(eng)
    await wait_for_condition(lambda: fire_count["n"] == 1,
                             message="startup trigger did not fire on start")

    counters: dict = {}
    eng.macros.cancel_all = _spy(counters, "cancel_all")

    second_macro = {
        "id": "m_other", "name": "Other",
        "steps": [{"action": "delay", "duration": 0.01}],
        "triggers": [],
    }
    await eng.apply_project(ProjectConfig(**_project_dict(
        macros=[STARTUP_MACRO, second_macro],
    )))

    assert counters.get("cancel_all") == 1
    # The new macro's definition is live.
    assert "m_other" in eng.macros._macros
    # The trigger rebuild kept the startup trigger loaded but did NOT re-fire.
    assert "trg_startup" in eng.triggers._triggers
    await asyncio.sleep(0.1)
    assert fire_count["n"] == 1, "startup trigger re-fired on a macro edit"

    await eng.triggers.stop()


@pytest.mark.asyncio
async def test_load_origin_fires_startup_triggers(tmp_path):
    """A whole new project arriving (reload from disk) IS a startup."""
    eng = _make_engine(tmp_path, macros=[STARTUP_MACRO])
    fire_count = await _prime_triggers(eng)
    await wait_for_condition(lambda: fire_count["n"] == 1,
                             message="startup trigger did not fire on start")

    drivers = {"n": 0}
    real_load = eng._load_project_drivers

    def counting_load():
        drivers["n"] += 1
        real_load()

    eng._load_project_drivers = counting_load

    await eng.reload_project()

    assert drivers["n"] == 1, "LOAD must rescan the driver library"
    await wait_for_condition(lambda: fire_count["n"] == 2,
                             message="startup trigger did not fire on LOAD")

    await eng.triggers.stop()


# ── EDIT: variables ──


@pytest.mark.asyncio
async def test_variable_edit_seeds_and_sweeps_without_device_sync(tmp_path):
    eng = _make_engine(tmp_path, variables=[
        {"id": "old_var", "type": "number", "default": 1},
    ])
    eng.state.set("var.old_var", 1, source="system")

    counters: dict = {}
    eng._sync_devices = _spy(counters, "sync_devices")

    await eng.apply_project(ProjectConfig(**_project_dict(variables=[
        {"id": "new_var", "type": "number", "default": 42},
    ])))

    assert eng.state.get("var.new_var") == 42, "new variable not seeded"
    assert eng.state.get("var.old_var") is None, "orphaned var key not swept"
    assert "sync_devices" not in counters, "variable edit synced devices"

    await eng.triggers.stop()


# ── EDIT: per-script reload ──


@pytest.mark.asyncio
async def test_script_edit_reloads_only_changed_scripts(tmp_path):
    from unittest.mock import MagicMock

    eng = _make_engine(tmp_path, scripts=[
        {"id": "keep", "file": "keep.py"},
        {"id": "change", "file": "change.py", "enabled": True},
        {"id": "remove", "file": "remove.py"},
    ])
    eng.scripts = MagicMock()
    eng.scripts.reload_script.return_value = {"status": "reloaded", "handlers": 1}

    await eng.apply_project(ProjectConfig(**_project_dict(scripts=[
        {"id": "keep", "file": "keep.py"},
        {"id": "change", "file": "change.py", "enabled": False},
        {"id": "add", "file": "add.py"},
    ])))

    eng.scripts.reload_scripts.assert_not_called()
    eng.scripts.unload_script.assert_called_once_with("remove")
    reloaded = [c.args[0]["id"] for c in eng.scripts.reload_script.call_args_list]
    assert sorted(reloaded) == ["add", "change"], (
        "only the changed and added scripts may reload"
    )


@pytest.mark.asyncio
async def test_load_origin_uses_full_script_reload(tmp_path):
    """LOAD can replace script files while configs stay identical (library
    open, backup restore) — only a full script reload is safe."""
    from unittest.mock import MagicMock

    eng = _make_engine(tmp_path, scripts=[{"id": "keep", "file": "keep.py"}])
    eng.scripts = MagicMock()

    await eng.reload_project()

    eng.scripts.reload_scripts.assert_called_once()
    eng.scripts.reload_script.assert_not_called()

    await eng.triggers.stop()


# ── Optimistic concurrency ──


@pytest.mark.asyncio
async def test_apply_project_occ_conflict(tmp_path):
    eng = _make_engine(tmp_path)
    eng._project_revision = 5

    with pytest.raises(ProjectRevisionConflictError):
        await eng.apply_project(
            ProjectConfig(**_project_dict(name="Stale")),
            expected_revision=3,
        )

    assert eng._project_revision == 5
    assert eng.project.project.name == "P"

    revision = await eng.apply_project(
        ProjectConfig(**_project_dict(name="Fresh")),
        expected_revision=5,
    )
    assert revision == 6
    assert eng.project.project.name == "Fresh"


# ── Scoped rollback ──


@pytest.mark.asyncio
async def test_failed_variable_edit_rolls_back_scoped(tmp_path):
    """A reconcile failure on a variables-only edit restores the runtime
    (project, revision, trigger listeners) without re-syncing devices —
    and the new bytes stay on disk (the file is the source of truth)."""
    eng = _make_engine(
        tmp_path,
        variables=[{"id": "v1", "type": "number", "default": 1}],
        macros=[{
            "id": "m1", "name": "M1",
            "steps": [{"action": "state.set", "key": "var.x", "value": 1}],
            "triggers": [{
                "id": "trg_state", "type": "state_change", "enabled": True,
                "state_key": "var.v1", "state_operator": "any",
            }],
        }],
    )
    fire_count = await _prime_triggers(eng)
    baseline_subs = len(eng.triggers._state_sub_ids)
    assert baseline_subs == 1

    counters: dict = {}
    eng._sync_devices = _spy(counters, "sync_devices")

    def boom():
        raise RuntimeError("validation rebuild failed")

    eng._register_variable_validation = boom

    with pytest.raises(RuntimeError, match="validation rebuild failed"):
        await eng.apply_project(ProjectConfig(**_project_dict(
            variables=[{"id": "v1", "type": "number", "default": 2}],
            macros=eng.project.model_dump(mode="json")["macros"],
        )))

    # Runtime rolled back: revision and project restored, no device sync,
    # trigger listeners intact (not stacked, not lost).
    assert eng._project_revision == 0
    assert eng.project.variables[0].default == 1
    assert "sync_devices" not in counters, "scoped rollback synced devices"
    assert len(eng.triggers._state_sub_ids) == baseline_subs

    # Persist-first: the user's edit survived on disk despite the rollback.
    on_disk = json.loads(Path(eng.project_path).read_text(encoding="utf-8"))
    assert on_disk["variables"][0]["default"] == 2
    assert fire_count["n"] == 0, "no macro may fire during a rollback"

    await eng.triggers.stop()
