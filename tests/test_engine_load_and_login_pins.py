"""What the engine does on the less travelled paths of loading its project and
moving the logins it holds: a seed that cannot be copied, a project file that
parses but does not validate, a backup that will not restore, a login move or
backup that fails, and the project's secrets handed to the log redaction.

Each test checks a result (the project that came back, the record left on
disk, the file as saved, the state of the login file, the secrets registered),
not only that nothing raised.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from openavc.core import project_recovery
from openavc.core.backup_manager import create_backup, list_backups
from openavc.core.device_logins import SAVED_LOGIN, device_owner
from openavc.core.engine import Engine
from openavc.core.project_loader import (
    DeviceConfig,
    PluginConfig,
    ProjectConfig,
    ProjectMeta,
    crash_copy_path,
    save_project,
)
from openavc.utils.log_redaction import get_secret_registry

ENGINE_LOG = "openavc.core.engine"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(folder))
    yield folder
    get_secret_registry().clear()


def _project_path(tmp_path):
    return tmp_path / "projects" / "default" / "project.avc"


def _real_project(path, name="Live Project"):
    path.parent.mkdir(parents=True, exist_ok=True)
    save_project(path, ProjectConfig(project=ProjectMeta(id="live", name=name)))


# --- loading the project ---------------------------------------------------------


def test_a_seed_that_cannot_be_copied_falls_through_to_a_quiet_recovery_project(
    tmp_path, monkeypatch, caplog,
):
    """A missing project whose seed cannot be copied is still a first boot:
    with no backup, an empty Recovery Project, saved, and no notice."""
    not_a_file = tmp_path / "seed_is_a_folder"
    not_a_file.mkdir()
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: not_a_file)
    project_path = _project_path(tmp_path)

    eng = Engine(str(project_path))
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        project = eng._load_project_safe()

    assert project.project.id == "recovery"
    assert project.project.name == "Recovery Project"
    assert json.loads(project_path.read_text(encoding="utf-8"))["project"]["id"] == "recovery"
    assert project_recovery.read(project_path.parent) is None
    messages = [r.getMessage() for r in caplog.records if r.name == ENGINE_LOG]
    assert any(m.startswith(f"Failed to seed project from {not_a_file}") for m in messages)
    assert "No backups available. Creating empty recovery project." in messages


def test_a_seed_that_cannot_be_copied_still_restores_a_backup_as_missing(tmp_path, monkeypatch):
    """The seed failing leaves the missing file to the backups, and the record
    says the file was missing, not unreadable."""
    not_a_file = tmp_path / "seed_is_a_folder"
    not_a_file.mkdir()
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: not_a_file)
    project_path = _project_path(tmp_path)
    _real_project(project_path)
    backup = create_backup(project_path, "Auto backup")
    assert backup is not None
    project_path.unlink()

    project = Engine(str(project_path))._load_project_safe()

    assert project.project.id == "live"
    record = project_recovery.read(project_path.parent)
    assert record["outcome"] == project_recovery.RESTORED
    assert record["reason"] == project_recovery.MISSING
    assert record["backup"] == f"backups/{backup.name}"


def test_a_project_that_parses_but_does_not_validate_is_restored_as_unreadable(
    tmp_path, monkeypatch, caplog,
):
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = _project_path(tmp_path)
    _real_project(project_path)
    assert create_backup(project_path, "Auto backup") is not None
    project_path.write_text('{"project": 5}', encoding="utf-8")

    eng = Engine(str(project_path))
    with caplog.at_level(logging.ERROR, logger=ENGINE_LOG):
        project = eng._load_project_safe()

    assert project.project.id == "live"
    assert project_recovery.read(project_path.parent)["reason"] == project_recovery.UNREADABLE
    assert any(
        r.getMessage().startswith("Project file failed to load: ")
        for r in caplog.records if r.name == ENGINE_LOG
    )


def test_a_project_that_does_not_validate_with_no_backup_is_a_reset(tmp_path, monkeypatch):
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = _project_path(tmp_path)
    project_path.parent.mkdir(parents=True)
    project_path.write_text('{"project": 5}', encoding="utf-8")

    project = Engine(str(project_path))._load_project_safe()

    assert project.project.id == "recovery"
    record = project_recovery.read(project_path.parent)
    assert record["outcome"] == project_recovery.RESET
    assert record["reason"] == project_recovery.UNREADABLE


def test_a_backup_that_will_not_restore_is_passed_over_for_the_next_one(
    tmp_path, monkeypatch, caplog,
):
    """Backups are tried newest first; one that fails is logged and the next
    one is tried, and the record names the one that loaded."""
    from openavc.core import backup_manager

    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = _project_path(tmp_path)
    _real_project(project_path)
    assert create_backup(project_path, "First") is not None
    assert create_backup(project_path, "Second") is not None
    backups = list_backups(project_path)
    assert len(backups) == 2
    newest, older = backups[0].filename, backups[1].filename
    project_path.write_text("{not json", encoding="utf-8")

    real_restore = backup_manager.restore_from_backup
    tried: list[str] = []

    def restore(backup_path, target):
        tried.append(backup_path.name)
        if len(tried) == 1:
            raise OSError("disk said no")
        return real_restore(backup_path, target)

    monkeypatch.setattr(backup_manager, "restore_from_backup", restore)
    eng = Engine(str(project_path))
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        project = eng._load_project_safe()

    assert project.project.id == "live"
    assert tried == [Path(newest).name, Path(older).name]
    assert project_recovery.read(project_path.parent)["backup"] == older
    assert f"Backup restore failed ({newest}): disk said no" in [
        r.getMessage() for r in caplog.records if r.name == ENGINE_LOG
    ]


# --- the project's settings and secrets -------------------------------------------


def test_a_settings_block_that_cannot_be_read_falls_back_to_the_system_interval(tmp_path):
    class Unreadable:
        @property
        def devices(self):
            raise RuntimeError("malformed")

    eng = Engine(str(_project_path(tmp_path)))
    eng.devices.project_reconnect_interval = 42
    eng.project = SimpleNamespace(settings=Unreadable())

    eng._apply_project_settings()

    assert eng.devices.project_reconnect_interval is None


def test_with_no_project_the_redaction_gets_no_project_secrets(tmp_path):
    get_secret_registry().set_project_secrets({"Left-Over-Secret"})
    eng = Engine(str(_project_path(tmp_path)))

    eng._register_project_secrets()

    assert get_secret_registry()._project == set()


def test_secrets_that_cannot_be_read_never_stop_the_project(tmp_path, caplog):
    get_secret_registry().set_project_secrets({"Left-Over-Secret"})
    eng = Engine(str(_project_path(tmp_path)))
    eng.project = ProjectConfig(project=ProjectMeta(id="p", name="P"))

    class Unlistable:
        def __iter__(self):
            raise RuntimeError("devices unreadable")

    # A project whose devices cannot be listed: reading its logins raises.
    eng.project.devices = Unlistable()
    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOG):
        eng._register_project_secrets()

    assert get_secret_registry()._project == {"Left-Over-Secret"}
    assert "Could not register the project's secrets for log redaction" in [
        r.getMessage() for r in caplog.records if r.name == ENGINE_LOG
    ]


# --- which logins wait for somebody ------------------------------------------------


def test_with_no_project_nothing_waits_for_a_login(tmp_path):
    eng = Engine(str(_project_path(tmp_path)))
    assert eng.logins_needed() == []
    assert eng.plugin_logins_needed() == []


def test_a_disabled_or_uninstalled_plugin_never_waits_for_a_login(tmp_path):
    eng = Engine(str(_project_path(tmp_path)))
    eng.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        plugins={
            "acme_not_installed": PluginConfig(enabled=True, config={"password": SAVED_LOGIN}),
        },
    )
    assert eng.plugin_logins_needed() == []


# --- moving the logins out of the project -------------------------------------------


def _project_with_a_login():
    return ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        devices=[DeviceConfig(
            id="d1", driver="acme_widget", name="Widget",
            config={"host": "192.0.2.5", "password": "Widget-Pass-1"},
        )],
    )


def test_a_login_move_that_fails_leaves_the_project_as_it_was(tmp_path, monkeypatch, caplog):
    eng = Engine(str(_project_path(tmp_path)))
    project = _project_with_a_login()

    def boom(_device):
        raise RuntimeError("cannot tell")

    monkeypatch.setattr(eng.installed_equipment, "matches", boom)
    with caplog.at_level(logging.ERROR, logger=ENGINE_LOG):
        assert eng._move_logins(project, typed=False) is project

    assert project.devices[0].config["password"] == "Widget-Pass-1"
    assert "Could not move the device logins out of the project" in [
        r.getMessage() for r in caplog.records if r.name == ENGINE_LOG
    ]


@pytest.mark.asyncio
async def test_a_plugin_login_move_whose_backup_fails_leaves_the_file_alone(
    tmp_path, monkeypatch,
):
    """No backup, no move: the project in memory and on disk keep the login,
    and the start has still not backed up."""
    project_path = _project_path(tmp_path)
    eng = Engine(str(project_path))
    project_path.parent.mkdir(parents=True)
    eng.project = _project_with_a_login()
    save_project(project_path, eng.project)
    before = project_path.read_text(encoding="utf-8")
    held = eng.project

    def no_backup(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("openavc.core.backup_manager.create_backup", no_backup)
    monkeypatch.setattr(
        "openavc.core.project_library.move_library_logins", lambda _logins: None,
    )
    await eng._move_logins_at_start()

    assert eng.project is held
    assert eng._logins_backed_up_at_start is False
    assert project_path.read_text(encoding="utf-8") == before
    assert eng.project.devices[0].config["password"] == "Widget-Pass-1"


@pytest.mark.asyncio
async def test_a_plugin_login_move_that_moves_saves_and_refreshes_the_crash_copy(
    tmp_path, monkeypatch,
):
    project_path = _project_path(tmp_path)
    eng = Engine(str(project_path))
    project_path.parent.mkdir(parents=True)
    eng.project = _project_with_a_login()
    save_project(project_path, eng.project)
    save_project(project_path, eng.project)  # a crash copy, holding the login
    assert "Widget-Pass-1" in crash_copy_path(project_path).read_text(encoding="utf-8")
    monkeypatch.setattr(
        "openavc.core.project_library.move_library_logins", lambda _logins: None,
    )

    await eng._move_logins_at_start()

    assert eng._logins_backed_up_at_start is True
    on_disk = json.loads(project_path.read_text(encoding="utf-8"))
    assert on_disk["devices"][0]["config"]["password"] == SAVED_LOGIN
    assert eng.device_logins.secret(device_owner("d1", "acme_widget"), "password") == "Widget-Pass-1"
    # The crash copy is the file as now saved, the login no longer in it.
    assert crash_copy_path(project_path).read_text(encoding="utf-8") == project_path.read_text(
        encoding="utf-8"
    )
    assert [b.reason for b in list_backups(project_path)] == [
        "Before moving device logins out of the project",
    ]


@pytest.mark.asyncio
async def test_a_library_that_cannot_be_moved_never_stops_the_start(tmp_path, monkeypatch, caplog):
    eng = Engine(str(_project_path(tmp_path)))
    eng.project = ProjectConfig(project=ProjectMeta(id="p", name="P"))

    def boom(_logins):
        raise RuntimeError("library unreadable")

    monkeypatch.setattr("openavc.core.project_library.move_library_logins", boom)
    with caplog.at_level(logging.ERROR, logger=ENGINE_LOG):
        await eng._move_logins_at_start()

    assert "Could not move the logins out of the Project Library" in [
        r.getMessage() for r in caplog.records if r.name == ENGINE_LOG
    ]


def test_a_sweep_with_no_project_touches_nothing(tmp_path, monkeypatch):
    eng = Engine(str(_project_path(tmp_path)))
    swept: list = []
    monkeypatch.setattr(eng.device_logins, "sweep", lambda present: swept.append(present))
    eng._sweep_device_logins()
    assert swept == []


def test_a_sweep_that_fails_is_logged_and_the_project_goes_on(tmp_path, monkeypatch, caplog):
    eng = Engine(str(_project_path(tmp_path)))
    eng.project = _project_with_a_login()

    def boom(_present):
        raise OSError("read-only")

    monkeypatch.setattr(eng.device_logins, "sweep", boom)
    with caplog.at_level(logging.ERROR, logger=ENGINE_LOG):
        eng._sweep_device_logins()

    assert "Could not update the saved device logins" in [
        r.getMessage() for r in caplog.records if r.name == ENGINE_LOG
    ]


class _AcmeGate:
    """An invented plugin with one login."""

    PLUGIN_INFO = {
        "id": "acme_gate", "name": "Acme Gate", "version": "1.0.0", "author": "Acme",
        "description": "An invented plugin with a login.", "category": "integration",
        "license": "MIT", "platforms": ["all"], "capabilities": [],
    }
    CONFIG_SCHEMA = {"password": {"type": "string", "label": "Password"}}


def test_only_an_enabled_installed_plugin_waits_for_its_login(tmp_path, monkeypatch):
    from openavc.core import plugin_loader

    monkeypatch.setitem(plugin_loader._PLUGIN_CLASS_REGISTRY, "acme_gate", _AcmeGate)
    eng = Engine(str(_project_path(tmp_path)))
    eng.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        plugins={"acme_gate": PluginConfig(enabled=True, config={"password": SAVED_LOGIN})},
    )
    assert eng.plugin_logins_needed() == [{"id": "acme_gate", "name": "Acme Gate"}]

    eng.project.plugins["acme_gate"].enabled = False
    assert eng.plugin_logins_needed() == []


@pytest.mark.asyncio
async def test_a_load_that_changed_the_project_but_moved_no_login_keeps_the_crash_copy(
    tmp_path, monkeypatch,
):
    """Saved because the product hardware changed it, the crash copy stays the
    file as it was: only a save that moved logins out refreshes it."""
    project_path = _project_path(tmp_path)
    _real_project(project_path, name="Before")
    eng = Engine(str(project_path))

    def compose(project, *, for_load):
        changed = project.model_copy(deep=True)
        changed.project.name = "After"
        return changed

    monkeypatch.setattr(eng.installed_equipment, "compose", compose)
    await eng._load_project_at_start()

    assert json.loads(project_path.read_text(encoding="utf-8"))["project"]["name"] == "After"
    assert json.loads(crash_copy_path(project_path).read_text(encoding="utf-8"))["project"][
        "name"
    ] == "Before"


def test_a_device_login_saved_here_is_not_one_of_the_projects_secrets(tmp_path):
    """The redaction's project secrets are the mesh key and plugin logins; a
    device's login is masked under that device, never as the project's."""
    eng = Engine(str(_project_path(tmp_path)))
    eng.project = eng._move_logins(_project_with_a_login(), typed=False)
    assert eng.project.devices[0].config["password"] == SAVED_LOGIN

    eng._register_project_secrets()

    assert "Widget-Pass-1" not in get_secret_registry()._project


# --- the services at shutdown -----------------------------------------------------


@pytest.mark.asyncio
async def test_the_shutdown_leaves_the_isc_endpoint_and_proxy_on_the_stopped_mesh(tmp_path):
    """Today's shutdown: the mesh is stopped and its slot emptied, but the ISC
    WebSocket endpoint and the scripts' isc proxy keep the stopped manager
    (a reload that turns ISC off hands them None). A change here is a change
    in behaviour, not a refactor."""
    from openavc.api import isc_ws
    from openavc.core.script_api import isc as isc_proxy

    stopped: list[str] = []

    async def stop():
        stopped.append("isc")

    mesh = SimpleNamespace(stop=stop)
    eng = Engine(str(_project_path(tmp_path)))
    eng.isc = mesh
    isc_ws.set_isc_manager(mesh)
    isc_proxy._bind(mesh)
    try:
        await eng._stop_network_services()
        assert stopped == ["isc"]
        assert eng.isc is None
        assert isc_ws._isc_manager is mesh
        assert isc_proxy._manager is mesh
    finally:
        isc_ws.set_isc_manager(None)
        isc_proxy._bind(None)
