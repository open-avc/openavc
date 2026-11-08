"""What a project change does once it reaches the engine, pinned where no
other test looks: the applied edit (the rollback, the announcements, the
equipment refusal), the bookkeeping saves (their callbacks, their broadcasts,
the one flush task a stop waits on), a plugin's own save, and the plugin sync.

Each test checks a result (state, disk, a broadcast, a log line, an
exception), never only that the code ran.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.core.client_origin import client_origin
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, load_project
from openavc.utils.log_redaction import get_secret_registry


def _project_dict(*, name="P", plugins=None, elements=None, variables=None):
    return {
        "openavc_version": "0.7.0",
        "project": {"id": "p", "name": name},
        "variables": variables or [],
        "macros": [],
        "devices": [],
        "device_groups": [],
        "connections": {},
        "scripts": [],
        "plugins": plugins or {},
        "ui": {
            "settings": {},
            "pages": [
                {"id": "main", "name": "Main",
                 "grid": {"columns": 12, "rows": 8}, "elements": elements or []},
            ],
        },
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


def _engine(tmp_path, **kwargs) -> Engine:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project_dict(**kwargs)), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    return eng


def _capture_broadcasts(eng) -> list[dict]:
    sent: list[dict] = []

    async def record(msg, client_type=None):
        sent.append(msg)

    eng.broadcast_ws = record
    return sent


def _disk(eng) -> dict:
    return json.loads(eng.project_path.read_text(encoding="utf-8"))


def _button(el_id: str) -> dict:
    # An action nothing dispatches: the server says so, once per element.
    return {
        "id": el_id, "type": "button", "label": el_id,
        "bindings": {"do": {"press": [{"action": "acme.teleport"}]}},
    }


# --- An applied edit ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_reconcile_puts_the_backup_flag_back(tmp_path):
    eng = _engine(tmp_path)
    eng._dirty_since_backup = False

    async def boom(diff, origin):
        raise RuntimeError("reconcile failed")

    eng._reconcile = boom
    with pytest.raises(RuntimeError, match="reconcile failed"):
        await eng.apply_project(ProjectConfig(**_project_dict(name="Renamed")))

    assert eng._dirty_since_backup is False


@pytest.mark.asyncio
async def test_a_clean_apply_marks_the_project_for_the_next_backup(tmp_path):
    eng = _engine(tmp_path)
    eng._dirty_since_backup = False

    await eng.apply_project(ProjectConfig(**_project_dict(name="Renamed")))

    assert eng._dirty_since_backup is True


@pytest.mark.asyncio
async def test_a_rollback_that_fails_too_still_raises_the_reconcile_failure(tmp_path, caplog):
    eng = _engine(tmp_path)

    async def boom(diff, origin):
        raise RuntimeError("the reconcile's own failure")

    async def rollback_boom(diff):
        raise ValueError("the rollback's failure")

    eng._reconcile = boom
    eng._rollback_reconcile = rollback_boom
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="the reconcile's own failure"):
            await eng.apply_project(ProjectConfig(**_project_dict(name="Renamed")))

    assert any("Rollback re-sync also failed" in r.getMessage() for r in caplog.records)
    assert eng.project.project.name == "P"


@pytest.mark.asyncio
async def test_a_rename_reaches_the_mdns_advertiser(tmp_path):
    eng = _engine(tmp_path)
    advertiser = MagicMock()
    eng.mdns_advertiser = advertiser

    await eng.apply_project(ProjectConfig(**_project_dict(name="Board Room")))

    advertiser.update_name.assert_called_once_with("Board Room")
    assert eng.state.get("system.project_name") == "Board Room"


@pytest.mark.asyncio
async def test_an_edit_that_keeps_the_name_leaves_the_advertiser_alone(tmp_path):
    eng = _engine(tmp_path)
    advertiser = MagicMock()
    eng.mdns_advertiser = advertiser

    await eng.apply_project(ProjectConfig(**_project_dict(
        variables=[{"id": "v1", "type": "number", "default": 1}],
    )))

    advertiser.update_name.assert_not_called()


async def _warnings_after(eng, caplog, edit) -> int:
    """Press, apply ``edit`` to a copy of the running project, press again;
    how many times the bad binding was reported."""
    edited = eng.project.model_copy(deep=True)
    edit(edited)
    with caplog.at_level(logging.WARNING):
        await eng.handle_ui_event("press", "btn_a")
        await eng.apply_project(edited)
        await eng.handle_ui_event("press", "btn_a")
    return len([r for r in caplog.records if "nothing dispatches" in r.getMessage()])


@pytest.mark.asyncio
async def test_a_ui_edit_reports_a_bad_binding_again(tmp_path, caplog):
    eng = _engine(tmp_path, elements=[_button("btn_a")])

    def edit(project):
        project.ui.pages[0].elements[0].label = "Renamed"

    assert await _warnings_after(eng, caplog, edit) == 2


@pytest.mark.asyncio
async def test_an_edit_outside_the_ui_says_it_once(tmp_path, caplog):
    eng = _engine(tmp_path, elements=[_button("btn_a")])

    def edit(project):
        project.project.name = "Renamed"

    assert await _warnings_after(eng, caplog, edit) == 1


@pytest.mark.asyncio
async def test_reassigning_with_no_product_hardware_is_refused(tmp_path):
    eng = _engine(tmp_path)
    before = eng._project_revision

    with pytest.raises(ValueError, match="This server has no product hardware to assign."):
        await eng.reassign_installed_equipment("anything")

    assert eng._project_revision == before
    assert eng._revision_counter == 0


# --- Bookkeeping saves -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bookkeeping_persist_names_the_tab_that_asked(tmp_path):
    eng = _engine(tmp_path)
    sent = _capture_broadcasts(eng)

    with client_origin("tab-1"):
        revision = await eng.persist_bookkeeping_change(lambda p: None)

    assert sent == [
        {"type": "project.reloaded", "revision": revision, "origin_client": "tab-1"},
    ]


@pytest.mark.asyncio
async def test_a_bookkeeping_persist_that_fails_runs_a_plain_undo_and_raises(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    sent = _capture_broadcasts(eng)
    undone = []

    async def boom(path, project):
        raise OSError("disk full")

    monkeypatch.setattr("openavc.core.engine.save_project_async", boom)
    with pytest.raises(OSError, match="disk full"):
        await eng.persist_bookkeeping_change(lambda p: None, on_error=lambda: undone.append(1))
    with pytest.raises(OSError, match="disk full"):
        await eng.persist_bookkeeping_change(lambda p: None)

    assert undone == [1]
    assert sent == []
    assert eng._revision_counter == 0


@pytest.mark.asyncio
async def test_a_deferred_save_announces_only_its_revision(tmp_path):
    """No origin, even when the request that queued it had one."""
    eng = _engine(tmp_path)
    sent = _capture_broadcasts(eng)

    with client_origin("tab-1"):
        eng.schedule_bookkeeping_change(lambda p: None)
        await eng._bookkeeping_task

    assert sent == [{"type": "project.reloaded", "revision": eng._project_revision}]


@pytest.mark.asyncio
async def test_two_writes_in_one_tick_share_the_flush_a_stop_waits_on(tmp_path):
    eng = _engine(tmp_path, variables=[{"id": "v1", "type": "number", "default": 1}])
    _capture_broadcasts(eng)

    def first(project):
        project.project.name = "First"

    def second(project):
        project.variables[0].default = 2

    eng.schedule_bookkeeping_change(first)
    eng.schedule_bookkeeping_change(second)
    await eng._bookkeeping_task

    disk = _disk(eng)
    assert disk["project"]["name"] == "First"
    assert disk["variables"][0]["default"] == 2
    assert eng._revision_counter == 1


@pytest.mark.asyncio
async def test_a_failed_deferred_save_undoes_and_announces_nothing(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    sent = _capture_broadcasts(eng)
    calls = []

    async def boom(path, project):
        raise OSError("disk full")

    async def undo():
        calls.append("undo")

    monkeypatch.setattr("openavc.core.engine.save_project_async", boom)
    eng.schedule_bookkeeping_change(
        lambda p: None, on_error=undo, on_saved=lambda: calls.append("saved"),
    )
    await eng._bookkeeping_task

    assert calls == ["undo"]
    assert sent == []
    assert eng._revision_counter == 0


@pytest.mark.asyncio
async def test_an_undo_that_raises_does_not_stop_the_others(tmp_path, monkeypatch, caplog):
    eng = _engine(tmp_path)
    _capture_broadcasts(eng)
    undone = []

    async def boom(path, project):
        raise OSError("disk full")

    def bad_undo():
        raise RuntimeError("undo broke")

    monkeypatch.setattr("openavc.core.engine.save_project_async", boom)
    eng.schedule_bookkeeping_change(lambda p: None, on_error=lambda: undone.append("first"))
    # The later write is undone first (reverse order), and its failure is
    # logged rather than stopping the first one's undo.
    eng.schedule_bookkeeping_change(lambda p: None, on_error=bad_undo)
    with caplog.at_level(logging.ERROR):
        await eng._bookkeeping_task

    assert undone == ["first"]
    assert any("Bookkeeping on_error callback failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_an_on_saved_that_raises_does_not_stop_the_others_or_the_announcement(tmp_path, caplog):
    eng = _engine(tmp_path)
    sent = _capture_broadcasts(eng)
    saved = []

    def bad_saved():
        raise RuntimeError("on_saved broke")

    async def good_saved():
        saved.append("second")

    eng.schedule_bookkeeping_change(lambda p: None, on_saved=bad_saved)
    eng.schedule_bookkeeping_change(lambda p: None, on_saved=good_saved)
    with caplog.at_level(logging.ERROR):
        await eng._bookkeeping_task

    assert saved == ["second"]
    assert sent == [{"type": "project.reloaded", "revision": eng._project_revision}]
    assert any("Bookkeeping on_saved callback failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_deferred_save_with_no_project_is_dropped(tmp_path):
    eng = _engine(tmp_path)
    sent = _capture_broadcasts(eng)
    before = eng.project_path.read_bytes()
    eng.project = None

    def mutate(project):
        raise AssertionError("a change ran with no project")

    eng.schedule_bookkeeping_change(mutate)
    await eng._bookkeeping_task

    assert eng._bookkeeping_queue == []
    assert sent == []
    assert eng.project_path.read_bytes() == before


@pytest.mark.asyncio
async def test_pending_settings_with_no_device_queue_nothing(tmp_path):
    eng = _engine(tmp_path)

    await eng._on_pending_settings_applied(
        "device.pending_settings_applied", {"device_id": "", "remaining": {}},
    )

    assert eng._bookkeeping_queue == []
    assert eng._bookkeeping_task is None


# --- A plugin's own save -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_plugin_config_that_cannot_be_saved_says_which_plugin(tmp_path, monkeypatch):
    eng = _engine(tmp_path, plugins={"plug1": {"enabled": False, "config": {"x": 1}}})
    sent = _capture_broadcasts(eng)

    async def boom(path, project):
        raise OSError("disk full")

    monkeypatch.setattr("openavc.core.engine.save_project_async", boom)
    with pytest.raises(OSError):
        await eng._save_plugin_config("plug1", {"x": 2})

    assert {"type": "error", "message": "Failed to save plugin config for 'plug1'"} in sent


@pytest.mark.asyncio
async def test_a_plugin_save_refreshes_the_log_redaction(tmp_path, monkeypatch):
    eng = _engine(tmp_path, plugins={"plug1": {"enabled": False, "config": {"x": 1}}})
    _capture_broadcasts(eng)
    registry = get_secret_registry()
    refreshed = []
    monkeypatch.setattr(
        registry, "set_project_secrets",
        lambda values: refreshed.append(eng._revision_counter),
    )

    await eng._save_plugin_config("plug1", {"x": 2})

    # Once, after the save went through.
    assert refreshed == [1]


# --- The plugin sync ---------------------------------------------------------


def _loader(*, known=(), running=(), outcome="restarted"):
    loader = MagicMock()
    loader.get_known_plugin_ids.return_value = set(known)
    loader.is_running.side_effect = lambda pid: pid in running
    loader.config_differs.return_value = True
    loader.start_plugin = AsyncMock()
    loader.stop_plugin = AsyncMock()
    loader.restart_or_apply = AsyncMock(return_value=outcome)
    return loader


@pytest.mark.asyncio
async def test_a_plugin_that_fails_to_restart_is_logged_as_stopped(tmp_path, caplog):
    eng = _engine(tmp_path, plugins={"p1": {"enabled": True, "config": {"x": 2}}})
    eng.plugin_loader = _loader(known=["p1"], running=["p1"], outcome="start_failed")

    with caplog.at_level(logging.ERROR):
        await eng._sync_plugins()

    eng.plugin_loader.restart_or_apply.assert_awaited_once_with("p1", {"x": 2})
    assert any(
        "Plugin 'p1' failed to restart after a config change and is stopped" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_the_plugin_sync_with_no_project_touches_nothing(tmp_path):
    eng = _engine(tmp_path)
    eng.project = None
    eng.plugin_loader = _loader(known=["p1"], running=["p1"])

    await eng._sync_plugins()

    eng.plugin_loader.get_known_plugin_ids.assert_not_called()
    eng.plugin_loader.stop_plugin.assert_not_awaited()
