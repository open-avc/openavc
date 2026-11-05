"""Moving device logins out of a project and into the login file.

What is pinned here:

- The login file: format 1 is still read, as the running project's logins; a
  Project Library copy's logins are filed apart from them, and a sweep of the
  running project never touches them.
- The move: the connection password and each setting the driver marks secret
  move into the file, the project keeps the marker, and nothing else changes.
  Each login is in the file and read back before the marker replaces it; any
  failure returns the project exactly as it was given.
- Which value wins: a setting somebody typed replaces the saved login; a copy
  (a backup, the library, an import, a push) never does and only fills a gap.
- Every way in: the first start (with a backup first), a backup restored, a
  setting edited (the device reconnects with the new login), a setup action
  that saves a code (the device is not torn down), a project pushed from the
  cloud, and each Project Library door (the first-start move of every copy,
  Save As, open, duplicate, delete, import).
- A rollback to an earlier version: every marker is put back first, and the
  exit happens holding the project save lock.

An invented device throughout.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from openavc.core import project_library as library
from openavc.core.device_logins import (
    KEY_FILENAME,
    LOGINS_FILENAME,
    REMOVED_KEEP,
    SAVED_LOGIN,
    DeviceLogins,
    LoginFileUnreadable,
    find_logins,
    login_fields,
    move_logins,
    put_back_for_rollback,
)
from openavc.core.project_diff import ProjectOrigin
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver

DRIVER = "acme_login_move"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


class _AcmeWidget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": DRIVER,
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 5500},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "username": {"type": "string", "label": "Username"},
            "password": {"type": "string", "secret": True, "label": "Password"},
            "api_key": {"type": "string", "secret": True, "label": "API Key"},
            "pairing_code": {"type": "password", "label": "Pairing Code"},
            "mode": {"type": "string", "label": "Mode"},
        },
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.fixture(autouse=True)
def _driver(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    registry.register_driver(_AcmeWidget)


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(folder))
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", folder / "saved_projects")
    return folder


def _project(password: Any = "Hunter2-Acme", **config: Any) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id="p1", name="Hall"),
        devices=[DeviceConfig(id="proj1", driver=DRIVER, name="Projector", config={"mode": "eco", **config})],
        connections={"proj1": {"host": "192.0.2.10", "port": 5500, "username": "admin", "password": password}},
    )


def _document(password: Any = "Hunter2-Acme", **config: Any) -> dict[str, Any]:
    return _project(password, **config).model_dump(mode="json")


# --- the login file ---------------------------------------------------------------


def test_a_format_1_file_is_read_as_the_running_projects_logins(tmp_path):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    (tmp_path / KEY_FILENAME).write_bytes(key + b"\n")
    payload = {"devices": [{"device": "proj1", "driver": DRIVER, "fields": {"password": "Old-Format"},
                            "removed_at": None}]}
    envelope = {"format": "openavc-device-logins", "version": 1,
                "sealed": Fernet(key).encrypt(json.dumps(payload).encode()).decode()}
    (tmp_path / LOGINS_FILENAME).write_text(json.dumps(envelope), encoding="utf-8")

    store = DeviceLogins(tmp_path)
    assert store.unreadable is None
    assert store.login("proj1", DRIVER, "password") == "Old-Format"

    store.save("proj2", DRIVER, {"password": "New-Pass"})
    assert json.loads((tmp_path / LOGINS_FILENAME).read_text())["version"] == 2
    assert DeviceLogins(tmp_path).login("proj1", DRIVER, "password") == "Old-Format"


def test_a_library_copys_logins_are_filed_apart_and_outlive_a_sweep(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", DRIVER, {"password": "Room-B"}, library="room_b")
    assert store.login("proj1", DRIVER, "password") is None
    assert store.login("proj1", DRIVER, "password", library="room_b") == "Room-B"

    store.sweep([], now=NOW)
    store.sweep([], now=NOW + REMOVED_KEEP * 2)
    assert DeviceLogins(tmp_path).login("proj1", DRIVER, "password", library="room_b") == "Room-B"
    assert store.libraries() == {"room_b"}


# --- which settings move ------------------------------------------------------------


def test_the_password_and_every_setting_the_driver_marks_secret_are_logins():
    assert login_fields(DRIVER) == {"password", "api_key", "pairing_code"}
    assert login_fields("acme_not_installed") == {"password"}


def test_a_driver_not_installed_moves_only_the_password(tmp_path):
    project = _project(api_key="k-123456")
    project.devices[0].driver = "acme_not_installed"
    moved = move_logins(project, DeviceLogins(tmp_path), typed=False)
    assert moved.connections["proj1"]["password"] == SAVED_LOGIN
    assert moved.devices[0].config["api_key"] == "k-123456"


# --- the move -------------------------------------------------------------------------


def test_each_login_moves_and_nothing_else_changes(tmp_path):
    project = _project(api_key="k-123456", pairing_code="0042")
    store = DeviceLogins(tmp_path)
    moved = move_logins(project, store, typed=False)

    assert moved is not project
    assert project.connections["proj1"]["password"] == "Hunter2-Acme"  # the caller's copy is untouched
    assert moved.connections["proj1"] == {
        "host": "192.0.2.10", "port": 5500, "username": "admin", "password": SAVED_LOGIN,
    }
    assert moved.devices[0].config == {"mode": "eco", "api_key": SAVED_LOGIN, "pairing_code": SAVED_LOGIN}
    fresh = DeviceLogins(tmp_path)
    assert fresh.login("proj1", DRIVER, "password") == "Hunter2-Acme"
    assert fresh.login("proj1", DRIVER, "api_key") == "k-123456"
    assert fresh.login("proj1", DRIVER, "pairing_code") == "0042"
    assert "Hunter2-Acme" not in json.dumps(moved.model_dump(mode="json"))


def test_a_project_with_nothing_to_move_is_returned_as_it_is_and_writes_nothing(tmp_path):
    for project in (_project(SAVED_LOGIN), _project(""), _project(None)):
        assert move_logins(project, DeviceLogins(tmp_path), typed=False) is project
    assert list(tmp_path.iterdir()) == []


def test_a_login_that_is_not_text_stays_where_it_is(tmp_path):
    project = _project(1234)
    assert move_logins(project, DeviceLogins(tmp_path), typed=False) is project


def test_a_copy_never_replaces_the_saved_login_and_a_typed_one_does(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", DRIVER, {"password": "Current-Pass"})

    move_logins(_project("Old-Pass", api_key="k-gap"), store, typed=False)
    assert DeviceLogins(tmp_path).login("proj1", DRIVER, "password") == "Current-Pass"
    assert DeviceLogins(tmp_path).login("proj1", DRIVER, "api_key") == "k-gap"

    move_logins(_project("Typed-Pass"), store, typed=True)
    assert DeviceLogins(tmp_path).login("proj1", DRIVER, "password") == "Typed-Pass"


def test_a_file_that_cannot_be_opened_leaves_the_project_as_it_was(tmp_path):
    from cryptography.fernet import Fernet

    DeviceLogins(tmp_path).save("other", DRIVER, {"password": "x-1"})
    (tmp_path / KEY_FILENAME).write_bytes(Fernet.generate_key())
    before = (tmp_path / LOGINS_FILENAME).read_bytes()

    project = _project()
    assert move_logins(project, DeviceLogins(tmp_path), typed=True) is project
    assert (tmp_path / LOGINS_FILENAME).read_bytes() == before


def test_a_read_back_that_does_not_match_leaves_the_project_as_it_was(tmp_path, monkeypatch):
    def differs(self):
        raise LoginFileUnreadable("read back differently")

    monkeypatch.setattr(DeviceLogins, "verify", differs)
    project = _project()
    assert move_logins(project, DeviceLogins(tmp_path), typed=False) is project


def test_the_read_back_is_a_fresh_read_of_the_disk(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", DRIVER, {"password": "Hunter2-Acme"})
    store.verify()
    store._entries = {}  # what was written is no longer what this store holds
    with pytest.raises(LoginFileUnreadable):
        store.verify()


def test_an_older_format_copy_is_marked_where_it_holds_the_login(tmp_path):
    """A 0.1 copy kept the password in the device's config. It moves from
    there, and the copy keeps its format version."""
    old = {
        "openavc_version": "0.1.0",
        "project": {"id": "old", "name": "Old Room"},
        "devices": [{"id": "proj1", "driver": DRIVER, "name": "Projector",
                     "config": {"host": "192.0.2.10", "password": "Old-Room-Pass"}}],
    }
    moved = move_logins(old, DeviceLogins(tmp_path), typed=False, library="old")
    assert moved["openavc_version"] == "0.1.0"
    assert moved["devices"][0]["config"] == {"host": "192.0.2.10", "password": SAVED_LOGIN}
    assert old["devices"][0]["config"]["password"] == "Old-Room-Pass"
    assert DeviceLogins(tmp_path).login("proj1", DRIVER, "password", library="old") == "Old-Room-Pass"


def test_the_value_moved_is_the_one_the_device_dials():
    """The connection row wins over the config, as resolve_device_config
    layers them."""
    project = _project("From-Connection")
    project.devices[0].config["password"] = "From-Config"
    [found] = find_logins(project)
    assert found.fields["password"] == "From-Connection"


# --- the engine: every way a project comes in ------------------------------------------


@pytest.fixture
def engine(data, tmp_path):
    from openavc.core.engine import Engine

    eng = Engine(str(tmp_path / "project" / "project.avc"))
    eng.project_path.parent.mkdir(parents=True)
    return eng


def _write(path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=4), encoding="utf-8")


def _on_disk(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _backups(engine) -> list[str]:
    from openavc.core.backup_manager import list_backups

    return [b.reason for b in list_backups(engine.project_path)]


class _Loaded(Exception):
    """Stops a startup test once the project and the library have loaded."""


async def _start_until_loaded(engine, monkeypatch) -> None:
    def loaded():
        raise _Loaded()

    monkeypatch.setattr(engine, "_apply_project_settings", loaded)
    with pytest.raises(_Loaded):
        await engine.start()


async def test_the_first_start_moves_every_login_after_a_backup(engine, data, monkeypatch):
    original = _document(api_key="k-123456")
    _write(engine.project_path, original)

    await _start_until_loaded(engine, monkeypatch)

    saved = _on_disk(engine.project_path)
    assert saved["connections"]["proj1"]["password"] == SAVED_LOGIN
    assert saved["devices"][0]["config"]["api_key"] == SAVED_LOGIN
    assert engine.project.connections["proj1"]["password"] == SAVED_LOGIN
    logins = DeviceLogins(data)
    assert logins.login("proj1", DRIVER, "password") == "Hunter2-Acme"
    assert logins.login("proj1", DRIVER, "api_key") == "k-123456"
    assert engine.resolved_device_config(engine.project.devices[0])["config"]["password"] == "Hunter2-Acme"

    # The file as it was is in a backup taken before it was rewritten.
    assert _backups(engine) == ["Before moving device logins out of the project"]
    from openavc.core.backup_manager import list_backups

    with zipfile.ZipFile(engine.project_path.parent / list_backups(engine.project_path)[0].filename) as zf:
        assert json.loads(zf.read("project.avc"))["connections"]["proj1"]["password"] == "Hunter2-Acme"


async def test_a_start_with_nothing_to_move_rewrites_nothing(engine, data, monkeypatch):
    _write(engine.project_path, _document(SAVED_LOGIN))
    before = engine.project_path.read_bytes()
    await _start_until_loaded(engine, monkeypatch)
    assert engine.project_path.read_bytes() == before
    assert _backups(engine) == []
    assert not (data / LOGINS_FILENAME).exists()


async def test_a_start_whose_backup_fails_leaves_the_project_as_it_was(engine, data, monkeypatch):
    from openavc.core import backup_manager

    _write(engine.project_path, _document())
    before = engine.project_path.read_bytes()

    def no_room(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup_manager, "create_backup", no_room)
    await _start_until_loaded(engine, monkeypatch)
    assert engine.project_path.read_bytes() == before
    assert engine.project.connections["proj1"]["password"] == "Hunter2-Acme"


async def test_a_start_whose_login_file_will_not_open_leaves_the_project_as_it_was(engine, data, monkeypatch):
    DeviceLogins(data).save("other", DRIVER, {"password": "x-1"})
    (data / KEY_FILENAME).unlink()
    _write(engine.project_path, _document())
    before = engine.project_path.read_bytes()

    await _start_until_loaded(engine, monkeypatch)
    assert engine.project_path.read_bytes() == before
    assert engine.project.connections["proj1"]["password"] == "Hunter2-Acme"


async def _restore(engine, document: dict[str, Any]) -> None:
    """A backup restored: its project file written to disk, then reloaded."""
    _write(engine.project_path, document)
    with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
        await engine.reload_project()


async def _stop(engine) -> None:
    await engine.devices.disconnect_all()


async def test_restoring_an_old_backup_keeps_the_current_login(engine, data):
    """Same box, the device still in the project, its password changed since
    the backup: the current one is kept."""
    engine.device_logins.save("proj1", DRIVER, {"password": "Changed-Since"})
    try:
        await _restore(engine, _document("Before-The-Change"))
        assert _on_disk(engine.project_path)["connections"]["proj1"]["password"] == SAVED_LOGIN
        assert DeviceLogins(data).login("proj1", DRIVER, "password") == "Changed-Since"
        assert engine.devices.get_device_config("proj1")["config"]["password"] == "Changed-Since"
        # The backup restored is still there; no second copy of it is taken.
        assert _backups(engine) == []
    finally:
        await _stop(engine)


async def test_restoring_a_backup_from_before_a_device_was_removed_finds_its_login(engine, data):
    """Same box, the device removed since the backup, within 90 days."""
    engine.device_logins.save("proj1", DRIVER, {"password": "Hunter2-Acme"})
    engine.device_logins.sweep([], now=datetime.now(timezone.utc) - timedelta(days=30))
    try:
        await _restore(engine, _document(SAVED_LOGIN))
        assert engine.devices.get_device_config("proj1")["config"]["password"] == "Hunter2-Acme"
    finally:
        await _stop(engine)


async def test_a_device_removed_more_than_90_days_ago_needs_its_login(engine, data):
    engine.device_logins.save("proj1", DRIVER, {"password": "Hunter2-Acme"})
    engine.device_logins.sweep([], now=datetime.now(timezone.utc) - REMOVED_KEEP - timedelta(days=1))
    engine.device_logins.sweep([])
    try:
        await _restore(engine, _document(SAVED_LOGIN))
        assert engine.devices.get_device_config("proj1")["config"]["password"] == SAVED_LOGIN
    finally:
        await _stop(engine)


async def test_a_backup_holding_a_login_fills_the_gap(engine, data):
    """A backup from before the logins moved, of a device nothing is saved for."""
    try:
        await _restore(engine, _document("From-The-Backup"))
        assert DeviceLogins(data).login("proj1", DRIVER, "password") == "From-The-Backup"
        assert engine.devices.get_device_config("proj1")["config"]["password"] == "From-The-Backup"
    finally:
        await _stop(engine)


async def test_the_same_id_on_another_driver_is_not_given_the_login(engine, data):
    engine.device_logins.save("proj1", "acme_other_driver", {"password": "Other-Driver"})
    try:
        await _restore(engine, _document(SAVED_LOGIN))
        assert engine.devices.get_device_config("proj1")["config"]["password"] == SAVED_LOGIN
    finally:
        await _stop(engine)


async def test_a_typed_login_replaces_the_saved_one_and_the_device_reconnects_with_it(engine, data):
    engine.project = _project(SAVED_LOGIN)
    engine.device_logins.save("proj1", DRIVER, {"password": "Old-Pass"})
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            await engine._sync_devices(defer_connect=True)
            assert engine.devices.get_device_config("proj1")["config"]["password"] == "Old-Pass"

            await engine.apply_project(_project("New-Pass"))

        assert engine.project.connections["proj1"]["password"] == SAVED_LOGIN
        assert _on_disk(engine.project_path)["connections"]["proj1"]["password"] == SAVED_LOGIN
        assert DeviceLogins(data).login("proj1", DRIVER, "password") == "New-Pass"
        assert engine.devices.get_device_config("proj1")["config"]["password"] == "New-Pass"
        assert _backups(engine) == []  # a save somebody asked for
    finally:
        await _stop(engine)


async def test_a_save_that_sends_the_marker_back_keeps_the_saved_login(engine, data):
    engine.project = _project(SAVED_LOGIN)
    engine.device_logins.save("proj1", DRIVER, {"password": "Kept-Pass"})
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            await engine.apply_project(_project(SAVED_LOGIN, mode="normal"))
        assert DeviceLogins(data).login("proj1", DRIVER, "password") == "Kept-Pass"
        assert engine.devices.get_device_config("proj1")["config"]["password"] == "Kept-Pass"
    finally:
        await _stop(engine)


async def test_a_pushed_copy_holding_an_old_login_keeps_the_current_one(engine, data):
    """A cloud config push or an import: a whole project, loaded and saved."""
    engine.project = _project(SAVED_LOGIN)
    engine.device_logins.save("proj1", DRIVER, {"password": "Current-Pass"})
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            await engine.apply_project(_project("Pushed-Old"), origin=ProjectOrigin.LOAD, persist=True)
        assert _on_disk(engine.project_path)["connections"]["proj1"]["password"] == SAVED_LOGIN
        assert DeviceLogins(data).login("proj1", DRIVER, "password") == "Current-Pass"
    finally:
        await _stop(engine)


async def test_a_setup_action_that_saves_a_code_files_it_and_keeps_the_device_up(engine, data):
    from openavc.core.setup_actions import SetupActionContext

    engine.project = _project(SAVED_LOGIN)
    engine.device_logins.save("proj1", DRIVER, {"password": "Hunter2-Acme"})
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            await engine.devices.add_device(
                engine.resolved_device_config(engine.project.devices[0]), defer_connect=True,
            )
            driver = engine.devices.get_driver("proj1")
            await SetupActionContext(engine, "proj1").apply_config_update({"pairing_code": "7731"})

        assert engine.devices.get_driver("proj1") is driver  # not torn down mid-action
        assert driver.config["pairing_code"] == "7731"
        assert engine.project.devices[0].config["pairing_code"] == SAVED_LOGIN
        assert _on_disk(engine.project_path)["devices"][0]["config"]["pairing_code"] == SAVED_LOGIN
        assert DeviceLogins(data).login("proj1", DRIVER, "pairing_code") == "7731"
    finally:
        await engine.devices._cancel_reconnect("proj1")
        await _stop(engine)


# --- the Project Library ---------------------------------------------------------------


def _library_copy(data, sid: str, document: dict[str, Any]) -> None:
    folder = data / "saved_projects" / sid
    folder.mkdir(parents=True)
    _write(folder / "project.avc", document)


def test_the_first_start_moves_every_library_copys_logins_under_its_own_id(data):
    _library_copy(data, "room_b", _document("Room-B-Pass", api_key="k-b"))
    old = {"openavc_version": "0.12.0", "project": {"id": "room_c", "name": "Room C"},
           "devices": [{"id": "proj1", "driver": DRIVER, "name": "P", "config": {"api_key": "k-c"}}],
           "connections": {"proj1": {"host": "192.0.2.30", "password": "Room-C-Pass"}}}
    _library_copy(data, "room_c", old)
    _library_copy(data, "quiet", _document(SAVED_LOGIN))
    quiet_before = (data / "saved_projects" / "quiet" / "project.avc").read_bytes()

    library.move_library_logins(DeviceLogins(data))

    logins = DeviceLogins(data)
    assert logins.login("proj1", DRIVER, "password", library="room_b") == "Room-B-Pass"
    assert logins.login("proj1", DRIVER, "password", library="room_c") == "Room-C-Pass"
    assert logins.login("proj1", DRIVER, "api_key", library="room_c") == "k-c"
    assert logins.login("proj1", DRIVER, "password") is None  # not the running project's
    room_c = _on_disk(data / "saved_projects" / "room_c" / "project.avc")
    assert room_c["openavc_version"] == "0.12.0"
    assert room_c["connections"]["proj1"] == {"host": "192.0.2.30", "password": SAVED_LOGIN}
    assert (data / "saved_projects" / "quiet" / "project.avc").read_bytes() == quiet_before

    [backup] = (data / "backups").glob("library-before-moving-device-logins-*.zip")
    with zipfile.ZipFile(backup) as zf:
        assert sorted(zf.namelist()) == ["room_b/project.avc", "room_c/project.avc"]
        assert json.loads(zf.read("room_c/project.avc")) == old


def test_a_library_whose_backup_fails_is_left_as_it_was(data, monkeypatch):
    _library_copy(data, "room_b", _document("Room-B-Pass"))
    before = (data / "saved_projects" / "room_b" / "project.avc").read_bytes()
    (data / "backups").write_text("not a folder")  # the backup cannot be written
    library.move_library_logins(DeviceLogins(data))
    assert (data / "saved_projects" / "room_b" / "project.avc").read_bytes() == before


def test_the_logins_of_a_copy_no_longer_in_the_library_are_deleted(data):
    logins = DeviceLogins(data)
    logins.save("proj1", DRIVER, {"password": "Gone"}, library="gone")
    _library_copy(data, "kept", _document(SAVED_LOGIN))
    logins.save("proj1", DRIVER, {"password": "Kept"}, library="kept")
    library.move_library_logins(logins)
    assert DeviceLogins(data).libraries() == {"kept"}


def _save_running(data, sid: str, project: ProjectConfig) -> None:
    library.save_to_library(sid, project, data / "no_scripts", "Room", "")


def test_save_as_files_the_running_projects_logins_under_the_copy(data):
    from openavc.core.device_logins import device_logins

    device_logins(data).save("proj1", DRIVER, {"password": "Running-Pass"})
    _save_running(data, "room_a", _project(SAVED_LOGIN))

    copy = _on_disk(data / "saved_projects" / "room_a" / "project.avc")
    assert copy["connections"]["proj1"]["password"] == SAVED_LOGIN
    assert DeviceLogins(data).login("proj1", DRIVER, "password", library="room_a") == "Running-Pass"


def test_save_as_of_a_project_still_holding_a_login_moves_it(data):
    _save_running(data, "room_a", _project("Plain-Pass"))
    copy = _on_disk(data / "saved_projects" / "room_a" / "project.avc")
    assert copy["connections"]["proj1"]["password"] == SAVED_LOGIN
    assert DeviceLogins(data).login("proj1", DRIVER, "password", library="room_a") == "Plain-Pass"


def test_opening_a_copy_keeps_the_running_login_and_brings_its_own_for_the_rest(data, tmp_path):
    from openavc.core.device_logins import device_logins

    logins = device_logins(data)
    logins.save("proj1", DRIVER, {"password": "Running-Pass"}, library="room_b")
    logins.save("proj1", DRIVER, {"api_key": "k-room-b"}, library="room_b")
    logins.save("proj1", DRIVER, {"password": "This-Room"})
    _library_copy(data, "room_b", _document(SAVED_LOGIN, api_key=SAVED_LOGIN))

    active = tmp_path / "active" / "project.avc"
    active.parent.mkdir()
    library.open_from_library("room_b", active, active.parent / "scripts", "room_b", "Room B")

    fresh = DeviceLogins(data)
    assert fresh.login("proj1", DRIVER, "password") == "This-Room"
    assert fresh.login("proj1", DRIVER, "api_key") == "k-room-b"
    assert _on_disk(active)["connections"]["proj1"]["password"] == SAVED_LOGIN


def test_a_duplicated_copy_has_the_logins_and_a_deleted_one_loses_them(data):
    from openavc.core.device_logins import device_logins

    device_logins(data).save("proj1", DRIVER, {"password": "Room-B"}, library="room_b")
    _library_copy(data, "room_b", _document(SAVED_LOGIN))

    library.duplicate_project("room_b", "room_b2", "Room B copy")
    assert DeviceLogins(data).login("proj1", DRIVER, "password", library="room_b2") == "Room-B"

    library.delete_project("room_b")
    assert DeviceLogins(data).libraries() == {"room_b2"}


@pytest.mark.parametrize("bundle", ["avc", "zip"])
def test_an_imported_project_holding_logins_has_them_moved_under_its_id(data, bundle):
    document = _document("Imported-Pass")
    content = json.dumps(document).encode()
    if bundle == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("project.avc", content)
        content = buf.getvalue()

    result = library.import_project(content, f"room.{bundle}", "imported_room")

    copy = _on_disk(data / "saved_projects" / result["id"] / "project.avc")
    assert copy["connections"]["proj1"]["password"] == SAVED_LOGIN
    assert DeviceLogins(data).login("proj1", DRIVER, "password", library=result["id"]) == "Imported-Pass"


# --- a rollback to an earlier version ---------------------------------------------------


def test_a_rollback_puts_every_login_back_first(data, tmp_path):
    logins = DeviceLogins(data)
    logins.save("proj1", DRIVER, {"password": "Running-Pass", "api_key": "k-run"})
    logins.save("proj2", DRIVER, {"password": "Room-B-Only"}, library="room_b")
    active = tmp_path / "active.avc"
    _write(active, _document(SAVED_LOGIN, api_key=SAVED_LOGIN))
    room_b = _document(SAVED_LOGIN)
    room_b["devices"].append({"id": "proj2", "driver": DRIVER, "name": "Second", "config": {}})
    room_b["connections"]["proj2"] = {"host": "192.0.2.20", "password": SAVED_LOGIN}
    _library_copy(data, "room_b", room_b)

    from openavc.core.project_loader import _project_save_lock

    held = []
    put_back_for_rollback(data, active, then=lambda: held.append(_project_save_lock.locked()))

    assert held == [True]
    restored = _on_disk(active)
    assert restored["connections"]["proj1"]["password"] == "Running-Pass"
    assert restored["devices"][0]["config"]["api_key"] == "k-run"
    copy = _on_disk(data / "saved_projects" / "room_b" / "project.avc")
    assert copy["connections"]["proj1"]["password"] == "Running-Pass"
    assert copy["connections"]["proj2"]["password"] == "Room-B-Only"


def test_a_rollback_with_a_login_file_that_will_not_open_changes_nothing_and_still_exits(data, tmp_path):
    DeviceLogins(data).save("proj1", DRIVER, {"password": "Running-Pass"})
    (data / KEY_FILENAME).unlink()
    active = tmp_path / "active.avc"
    _write(active, _document(SAVED_LOGIN))
    before = active.read_bytes()

    exited = []
    put_back_for_rollback(data, active, then=lambda: exited.append(True))
    assert exited == [True]
    assert active.read_bytes() == before


def test_the_apps_rollback_puts_the_logins_back_then_exits(data, tmp_path, monkeypatch):
    from openavc.updater.manager import UpdateManager

    DeviceLogins(data).save("proj1", DRIVER, {"password": "Running-Pass"})
    active = tmp_path / "active.avc"
    _write(active, _document(SAVED_LOGIN))

    manager = UpdateManager.__new__(UpdateManager)
    manager._data_dir = data
    manager._project_path = active
    seen = []
    monkeypatch.setattr(
        manager, "_restart_process",
        lambda: seen.append(_on_disk(active)["connections"]["proj1"]["password"]),
    )
    manager._exit_for_rollback()
    assert seen == ["Running-Pass"]
