"""Dynamic behavior pins for the apply_project seam.

Three properties that section-level unit tests can't see:

- GET-style round-trip cleanliness: serializing the project the way
  ``GET /api/project`` does and PUTting the result back must produce an
  all-clean ``ProjectDiff`` — no macro cancel, no device bounce, no trigger
  rebuild. A single section that fails to round-trip silently resurrects
  the old every-save-tears-everything-down behavior for that section.
- Concurrency: interleaved apply_project writers (with conflict retry, the
  way the IDE saves), bookkeeping flushes, and a dedicated-route write must
  produce strictly monotonic gapless revisions with no lost update — the
  final project must reflect every committed write, in memory and on disk.
- Failure injection: a reconcile that raises mid-apply must roll the
  runtime back to the previous project (triggers intact, not doubled) and
  leave the engine healthy enough that the next apply succeeds cleanly.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from server.core.device_manager import register_driver, unregister_driver
from server.core.engine import Engine, ProjectRevisionConflictError
from server.core.project_diff import ProjectDiff
from server.core.project_loader import ProjectConfig, VariableConfig, load_project
from server.drivers.configurable import create_configurable_driver_class

DIFF_SECTIONS = (
    "devices", "connections", "device_groups", "variables", "macros",
    "plugins", "ui", "scripts", "isc", "project_meta",
)


def _representative_project() -> dict:
    """A project touching every section the reconciler cares about:
    devices (bridge pair, child entities, pending settings), connections
    (direct + bridge-bound), groups, variables (persist / validation /
    source binding), macros exercising every step and trigger type, a full
    UI (overlay page, master elements, page groups, v0.7.0 show/do
    bindings), scripts, plugins, and ISC."""
    return {
        "openavc_version": "0.7.0",
        "project": {
            "id": "p", "name": "Round Trip", "description": "Dynamics pin",
            "created": "2026-01-01T00:00:00", "modified": "2026-01-02T00:00:00",
        },
        "devices": [
            {"id": "bridge1", "driver": "acme_bridge_dynamics_test",
             "name": "Bridge", "config": {}},
            {"id": "disp1", "driver": "acme_serial_display_dynamics_test",
             "name": "Display",
             "config": {"input_labels": ["HDMI 1", "HDMI 2"]},
             "pending_settings": {"brightness": 80},
             "child_entities": {
                 "output": {"001": {"label": "Zone A", "config": {"gain": -6}}},
             }},
            {"id": "tcp1", "driver": "generic_tcp", "name": "Raw TCP",
             "config": {}, "enabled": False},
        ],
        "device_groups": [
            {"id": "all_displays", "name": "All Displays", "device_ids": ["disp1"]},
        ],
        "connections": {
            "bridge1": {"host": "192.0.2.40", "port": 4998},
            "disp1": {"bridge": "bridge1", "bridge_port": "serial:1",
                      "baudrate": 9600},
            "tcp1": {"host": "192.0.2.50", "port": 23},
        },
        "plugins": {
            "acme_plugin": {"enabled": False, "config": {"level": 3, "zone": "a"}},
        },
        "variables": [
            {"id": "volume", "type": "number", "default": 50, "persist": True,
             "validation": {"min": 0, "max": 100}},
            {"id": "mode", "type": "string", "default": "day",
             "validation": {"allowed": ["day", "night"]}},
            {"id": "display_on", "type": "boolean", "default": False,
             "source_key": "device.disp1.power",
             "source_map": {"on": True, "off": False}},
        ],
        "macros": [
            {"id": "m_all_steps", "name": "Every step type",
             "stop_on_error": True, "cancel_group": "presets",
             "steps": [
                 {"action": "device.command", "device": "disp1",
                  "command": "set_input", "params": {"input": "$var.mode"},
                  "skip_if_offline": True,
                  "skip_if": {"key": "var.mode", "operator": "eq",
                              "value": "night"}},
                 {"action": "group.command", "group": "all_displays",
                  "command": "power_on"},
                 {"action": "delay", "seconds": 0.5},
                 {"action": "state.set", "key": "var.volume", "value": 30},
                 {"action": "event.emit", "event": "scene.recalled",
                  "payload": {"scene": "day"}},
                 {"action": "macro", "macro": "m_triggered"},
                 {"action": "conditional",
                  "condition": {"key": "var.display_on", "operator": "truthy"},
                  "then_steps": [{"action": "ui.navigate", "page": "main"}],
                  "else_steps": [{"action": "ui.navigate", "page": "confirm"}]},
                 {"action": "wait_until",
                  "condition": {"key": "device.disp1.power", "operator": "eq",
                                "value": "on"},
                  "timeout": 5, "on_timeout": "continue"},
             ],
             "triggers": []},
            {"id": "m_triggered", "name": "Every trigger type",
             "steps": [{"action": "state.set", "key": "var.mode",
                        "value": "night"}],
             "triggers": [
                 {"id": "t_cron", "type": "schedule", "cron": "0 7 * * 1-5",
                  "cooldown_seconds": 60},
                 {"id": "t_state", "type": "state_change",
                  "state_key": "var.volume", "state_operator": "gt",
                  "state_value": 80, "debounce_seconds": 1,
                  "conditions": [{"key": "var.mode", "operator": "eq",
                                  "value": "day"}]},
                 {"id": "t_event", "type": "event",
                  "event_pattern": "scene.*", "overlap": "queue"},
                 {"id": "t_boot", "type": "startup", "delay_seconds": 2,
                  "enabled": False},
             ]},
        ],
        "ui": {
            "settings": {"theme": "dark", "idle_timeout_seconds": 300,
                         "idle_page": "main", "page_transition": "fade"},
            "pages": [
                {"id": "main", "name": "Main",
                 "grid": {"columns": 12, "rows": 8},
                 "background": {"color": "#101418",
                                "image": "assets://bg.jpg",
                                "image_opacity": 0.5},
                 "elements": [
                     {"id": "btn_power", "type": "button", "label": "Power",
                      "icon": "power", "icon_position": "left",
                      "grid_area": {"col": 1, "row": 1, "col_span": 2,
                                    "row_span": 1},
                      "style": {"bg_color": "#223344"},
                      "bindings": {
                          "show": {"value": {"key": "device.disp1.power"}},
                          "do": {"tap": {"macro": "m_all_steps"}},
                      }},
                     {"id": "sld_volume", "type": "slider", "min": 0,
                      "max": 100, "step": 1, "response": "logarithmic",
                      "response_db_range": 60, "send_on_release": False,
                      "output_min": -80, "output_max": 10,
                      "grid_area": {"col": 3, "row": 1},
                      "bindings": {
                          "show": {"value": {"key": "var.volume",
                                             "write_back": True}},
                      }},
                 ]},
                {"id": "confirm", "name": "Confirm", "page_type": "overlay",
                 "overlay": {"width": 400, "height": 240,
                             "backdrop": "dim", "animation": "fade"},
                 "elements": []},
            ],
            "master_elements": [
                {"id": "mst_clock", "type": "clock", "clock_mode": "time",
                 "format": "h:mm A", "pages": "*",
                 "grid_area": {"col": 11, "row": 1, "col_span": 2}},
            ],
            "page_groups": [{"name": "Rooms", "pages": ["main"]}],
        },
        "scripts": [
            {"id": "startup_scene", "file": "startup_scene.py",
             "enabled": True, "description": "Recall the default scene"},
            {"id": "night_mode", "file": "night_mode.py", "enabled": False},
        ],
        "isc": {"enabled": False, "shared_state": ["var.volume"],
                "peers": ["192.0.2.60:8080"], "auth_key": "k",
                "allowed_remote_commands": ["disp1.*"]},
    }


def _small_project(*, variables=None, macros=None, devices=None,
                   connections=None, plugins=None, scripts=None) -> dict:
    return {
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": "P"},
        "variables": variables or [],
        "macros": macros or [],
        "devices": devices or [],
        "device_groups": [],
        "connections": connections or {},
        "scripts": scripts or [],
        "plugins": plugins or {},
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main",
             "grid": {"columns": 12, "rows": 8}, "elements": []},
        ]},
        "isc": {"enabled": False, "shared_state": [], "peers": [],
                "auth_key": ""},
    }


def _engine_from(tmp_path, project: dict) -> Engine:
    path = tmp_path / "project.avc"
    Path(path).write_text(json.dumps(project), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    return eng


@pytest.fixture
def acme_dynamics_drivers():
    """Invented bridge + downstream serial display, so the representative
    project exercises the bridge connection model."""
    bridge = create_configurable_driver_class({
        "id": "acme_bridge_dynamics_test",
        "name": "Acme Bridge (test)", "manufacturer": "Acme",
        "category": "utility", "version": "1.0.0", "transport": "tcp",
        "bridge": {"ports": [
            {"id": "serial:1", "kind": "serial", "passthrough_port": 4999,
             "label": "Serial Port 1"},
        ]},
        "default_config": {"host": "", "port": 4998},
        "config_schema": {}, "state_variables": {}, "commands": {},
        "responses": [],
    })
    display = create_configurable_driver_class({
        "id": "acme_serial_display_dynamics_test",
        "name": "Acme Serial Display (test)", "manufacturer": "Acme",
        "category": "display", "version": "1.0.0", "transport": "serial",
        "transports": ["tcp", "serial"],
        "default_config": {"baudrate": 9600, "port": ""},
        "config_schema": {}, "state_variables": {}, "commands": {},
        "responses": [],
    })
    register_driver(bridge)
    register_driver(display)
    yield
    unregister_driver("acme_bridge_dynamics_test")
    unregister_driver("acme_serial_display_dynamics_test")


# ── GET-style round-trip cleanliness ──


@pytest.mark.asyncio
async def test_get_put_round_trip_is_all_clean(tmp_path, acme_dynamics_drivers):
    """Serialize the live project exactly as GET /api/project does
    (model_dump(mode='json') → JSON text → parse → ProjectConfig, the PUT
    route's constructor) and apply it back. The diff must be all-clean and
    the apply must touch nothing: no macro cancel, no trigger stop/rebuild,
    no device sync, no ui.definition broadcast. A dirty section here means
    an IDE save that changes nothing still bounces that subsystem."""
    eng = _engine_from(tmp_path, _representative_project())

    sent = []

    async def record(message):
        sent.append(message)

    eng.broadcast_ws = record

    cancel_calls, trigger_stops, device_syncs = [], [], []
    orig_cancel = eng.macros.cancel_all
    orig_stop = eng.triggers.stop
    orig_sync = eng._sync_devices

    async def spy_cancel():
        cancel_calls.append(1)
        await orig_cancel()

    async def spy_stop():
        trigger_stops.append(1)
        await orig_stop()

    async def spy_sync():
        device_syncs.append(1)
        await orig_sync()

    eng.macros.cancel_all = spy_cancel
    eng.triggers.stop = spy_stop
    eng._sync_devices = spy_sync

    raw = json.loads(json.dumps(eng.project.model_dump(mode="json")))
    round_tripped = ProjectConfig(**raw)

    diff = ProjectDiff.compute(eng.project, round_tripped)
    dirty = [name for name in DIFF_SECTIONS if getattr(diff, name)]
    assert not dirty, (
        f"GET-style round-trip dirtied section(s) {dirty} — an IDE save "
        f"that changes nothing would bounce these subsystems on every save"
    )

    await eng.apply_project(round_tripped)

    assert eng._project_revision == 1
    assert eng.project is round_tripped
    assert cancel_calls == [], "clean apply cancelled running macros"
    assert trigger_stops == [], "clean apply rebuilt triggers"
    assert device_syncs == [], "clean apply bounced devices"
    assert [m["type"] for m in sent] == ["project.reloaded"], (
        "clean apply must broadcast project.reloaded only (no ui.definition)"
    )

    # And the persisted bytes survive a second round-trip: what load_project
    # reads back is byte-for-byte the same model.
    assert load_project(eng.project_path) == eng.project


# ── Concurrency stress ──


@pytest.mark.asyncio
async def test_concurrent_writers_no_lost_update_and_gapless_revisions(tmp_path):
    """Interleave two conflict-retrying apply_project writers (two IDEs)
    with a stream of bookkeeping flushes (schedule_bookkeeping_change),
    then a dedicated-route write (DELETE /api/scripts/...). Every
    committed write must be visible in the final project (memory AND
    disk) and the revision sequence must be strictly monotonic with no
    skips or repeats.

    The route write runs after the race on purpose: dedicated routes
    apply a copy of the project with no expected_revision, so a commit
    that lands between the route's copy and its apply is silently
    reverted. Until those routes carry conflict protection, racing the
    route here would (correctly) fail the no-lost-update assertion."""
    from server.api import rest, ws
    from server.main import app

    base = _small_project(
        plugins={"acme_plugin": {"enabled": False, "config": {}}},
        scripts=[{"id": "seed_script", "file": "seed.py", "enabled": True}],
    )
    eng = _engine_from(tmp_path, base)
    rest.set_engine(eng)
    ws.set_engine(eng)

    broadcast_revisions = []

    async def record(message):
        if message["type"] == "project.reloaded":
            broadcast_revisions.append(message["revision"])

    eng.broadcast_ws = record

    async def occ_writer(tag: str, count: int) -> None:
        """An IDE-style writer: snapshot revision + project, mutate, apply
        with If-Match semantics, refetch and retry on conflict."""
        for i in range(count):
            while True:
                expected = eng._project_revision
                copy = eng.project.model_copy(deep=True)
                copy.variables.append(
                    VariableConfig(id=f"occ_{tag}_{i}", type="number", default=i)
                )
                try:
                    await eng.apply_project(copy, expected_revision=expected)
                    break
                except ProjectRevisionConflictError:
                    await asyncio.sleep(0)
            await asyncio.sleep(0)

    async def bookkeeping_writer(count: int) -> None:
        for i in range(count):
            def mutate(project, key=f"bk_{i}"):
                project.plugins["acme_plugin"].config[key] = key
            eng.schedule_bookkeeping_change(mutate)
            await asyncio.sleep(0)

    async def route_writer() -> None:
        transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with AsyncClient(transport=transport,
                               base_url="http://testserver") as client:
            resp = await client.delete("/api/scripts/seed_script")
            assert resp.status_code == 200, resp.text

    try:
        await asyncio.gather(
            occ_writer("a", 4),
            occ_writer("b", 4),
            bookkeeping_writer(6),
        )
        # Drain any still-running bookkeeping flush.
        while eng._bookkeeping_queue or (
            eng._bookkeeping_task and not eng._bookkeeping_task.done()
        ):
            await asyncio.sleep(0.01)
        await route_writer()
    finally:
        rest.set_engine(None)
        ws.set_engine(None)

    # No lost update: every committed write is in the final project.
    final_vars = {v.id for v in eng.project.variables}
    expected_vars = {f"occ_a_{i}" for i in range(4)} | {
        f"occ_b_{i}" for i in range(4)
    }
    assert expected_vars <= final_vars, (
        f"lost OCC-writer update(s): {sorted(expected_vars - final_vars)}"
    )
    bk_keys = set(eng.project.plugins["acme_plugin"].config)
    expected_bk = {f"bk_{i}" for i in range(6)}
    assert expected_bk <= bk_keys, (
        f"lost bookkeeping write(s): {sorted(expected_bk - bk_keys)}"
    )
    assert not any(s.id == "seed_script" for s in eng.project.scripts), (
        "dedicated-route delete was lost or resurrected"
    )

    # Disk matches memory: the last committed save is what a reload sees.
    assert load_project(eng.project_path) == eng.project

    # Revisions: strictly monotonic, no skips, no repeats — every commit
    # broadcast exactly one project.reloaded with its own revision.
    final = eng._project_revision
    assert sorted(broadcast_revisions) == list(range(1, final + 1)), (
        f"revision sequence has skips or repeats: {sorted(broadcast_revisions)}"
    )

    await eng.triggers.stop()


# ── Reconcile failure injection ──


def _mock_devices(running: dict) -> MagicMock:
    devices = MagicMock()
    devices.get_device_configs.return_value = dict(running)
    devices.get_device_config.side_effect = lambda did: running.get(did)
    devices.add_device = AsyncMock()
    devices.update_device = AsyncMock()
    devices.remove_device = AsyncMock()
    devices.retry_all_orphans = AsyncMock()
    return devices


_BASELINE_MACRO = {
    "id": "m_base", "name": "Base",
    "steps": [{"action": "state.set", "key": "var.fired", "value": True}],
    "triggers": [{"id": "t_base", "type": "state_change",
                  "state_key": "var.poke", "state_operator": "any"}],
}


async def _primed_engine(tmp_path, **project_kwargs) -> Engine:
    """Pattern-1 engine primed like start() would: macros + triggers live,
    with one state_change trigger as the post-rollback health probe."""
    kwargs = dict(project_kwargs)
    kwargs["macros"] = [_BASELINE_MACRO] + list(kwargs.get("macros") or [])
    kwargs["variables"] = (
        [{"id": "poke", "type": "string", "default": ""}]
        + list(kwargs.get("variables") or [])
    )
    eng = _engine_from(tmp_path, _small_project(**kwargs))
    macros_data = [m.model_dump() for m in eng.project.macros]
    eng.macros.load_macros(macros_data)
    eng.triggers.load_triggers(macros_data)
    await eng.triggers.start(fire_startup=False)
    return eng


async def _assert_rolled_back_and_healthy(eng: Engine) -> None:
    """After a failed apply: previous revision, one trigger listener (not
    zero, not doubled), and the baseline trigger still fires exactly once."""
    assert eng._project_revision == 0
    assert len(eng.triggers._state_sub_ids) == 1, (
        "rollback left trigger listeners stacked or missing"
    )
    fire_count = {"n": 0}
    original_execute = eng.macros.execute

    async def counting_execute(macro_id, *a, **k):
        fire_count["n"] += 1
        return await original_execute(macro_id, *a, **k)

    eng.macros.execute = counting_execute
    eng.state.set("var.poke", "now", source="test")
    await asyncio.sleep(0.05)
    eng.macros.execute = original_execute
    assert fire_count["n"] == 1, (
        f"baseline trigger fired {fire_count['n']} times after rollback"
    )


def _edited(eng: Engine, **overrides) -> ProjectConfig:
    """The current project plus the given section overrides, round-tripped
    through the model constructor the way the PUT route builds its input."""
    raw = eng.project.model_dump(mode="json")
    raw.update({k: v for k, v in overrides.items()})
    return ProjectConfig(**raw)


@pytest.mark.asyncio
async def test_devices_reconcile_failure_rolls_back_then_next_apply_succeeds(tmp_path):
    # Per-device add/update failures are isolated by design
    # (return_exceptions=True in _sync_devices) — a section-level failure
    # means the device manager itself broke, so inject there. It must fail
    # the forward pass, then succeed for the rollback re-sync and the
    # second apply.
    eng = await _primed_engine(tmp_path)
    eng.devices = _mock_devices({})
    eng.devices.get_device_configs = MagicMock(
        side_effect=[RuntimeError("injected reconcile failure"), {}, {}]
    )
    prev_project = eng.project
    new_project = _edited(
        eng,
        devices=[{"id": "d1", "driver": "generic_tcp", "name": "D1",
                  "config": {}}],
        connections={"d1": {"host": "192.0.2.10", "port": 23}},
    )

    with pytest.raises(RuntimeError, match="injected reconcile failure"):
        await eng.apply_project(new_project)

    assert eng.project is prev_project
    await _assert_rolled_back_and_healthy(eng)

    revision = await eng.apply_project(new_project)

    assert revision == 1
    assert eng.project is new_project
    eng.devices.add_device.assert_awaited_once()
    assert eng.devices.add_device.await_args.args[0]["id"] == "d1"
    await eng.triggers.stop()


@pytest.mark.asyncio
async def test_plugins_reconcile_failure_rolls_back_then_next_apply_succeeds(tmp_path):
    eng = await _primed_engine(tmp_path)
    loader = MagicMock()
    loader.get_known_plugin_ids.return_value = set()
    loader.is_running.return_value = False
    loader.get_running_config.return_value = {}
    loader.start_plugin = AsyncMock(
        side_effect=[RuntimeError("injected reconcile failure"), None]
    )
    loader.stop_plugin = AsyncMock()
    loader.restart_or_apply = AsyncMock(return_value="restarted")
    loader.remove_plugin_tracking = MagicMock()
    eng.plugin_loader = loader
    prev_project = eng.project
    new_project = _edited(
        eng, plugins={"acme_plugin": {"enabled": True, "config": {"level": 1}}}
    )

    with pytest.raises(RuntimeError, match="injected reconcile failure"):
        await eng.apply_project(new_project)

    assert eng.project is prev_project
    await _assert_rolled_back_and_healthy(eng)

    revision = await eng.apply_project(new_project)

    assert revision == 1
    assert eng.project is new_project
    assert loader.start_plugin.await_count == 2
    await eng.triggers.stop()


@pytest.mark.asyncio
async def test_macros_reconcile_failure_rolls_back_then_next_apply_succeeds(tmp_path):
    eng = await _primed_engine(tmp_path)
    original_load = eng.macros.load_macros
    calls = {"n": 0}

    def load_macros_fails_once(macros_data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected reconcile failure")
        return original_load(macros_data)

    eng.macros.load_macros = load_macros_fails_once
    prev_project = eng.project
    new_macro = {"id": "m_new", "name": "New",
                 "steps": [{"action": "state.set", "key": "var.m_new_ran",
                            "value": True}],
                 "triggers": []}
    new_project = _edited(
        eng, macros=[_BASELINE_MACRO, new_macro]
    )

    with pytest.raises(RuntimeError, match="injected reconcile failure"):
        await eng.apply_project(new_project)

    assert eng.project is prev_project
    # Rollback reloads macro definitions from the restored project (call 2).
    assert calls["n"] == 2
    await _assert_rolled_back_and_healthy(eng)

    revision = await eng.apply_project(new_project)

    assert revision == 1
    assert eng.project is new_project
    await eng.macros.execute("m_new")
    assert eng.state.get("var.m_new_ran") is True
    await eng.triggers.stop()


@pytest.mark.asyncio
async def test_variables_reconcile_failure_rolls_back_then_next_apply_succeeds(tmp_path):
    eng = await _primed_engine(tmp_path)
    eng.persister = MagicMock()
    eng.persister.update_keys.side_effect = [
        RuntimeError("injected reconcile failure"), None,
    ]
    prev_project = eng.project
    new_project = _edited(
        eng,
        variables=[
            {"id": "poke", "type": "string", "default": ""},
            {"id": "volume", "type": "number", "default": 42, "persist": True},
        ],
    )

    with pytest.raises(RuntimeError, match="injected reconcile failure"):
        await eng.apply_project(new_project)

    assert eng.project is prev_project
    await _assert_rolled_back_and_healthy(eng)

    revision = await eng.apply_project(new_project)

    assert revision == 1
    assert eng.project is new_project
    assert eng.state.get("var.volume") == 42
    assert eng.persister.update_keys.call_count == 2
    await eng.triggers.stop()
