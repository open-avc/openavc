"""Plugin logins and the mesh key, saved on this system instead of in the project.

What is pinned here:
- What a plugin declares: a schema field marked secret (or named password), at
  the top level or inside a group, and the list items of CONFIG_SECRETS, each
  filed under its id. A mapping_list row's field is not one, and a malformed
  CONFIG_SECRETS is refused.
- The login file: plugin logins and the mesh key are filed beside the device
  logins, read back, aged out when they leave the project, carried by a
  backup, an export and the Project Library, and put back before a rollback.
- Every way in: a project loaded, a login typed in the settings, the marker
  sent back, a plugin's own save_config.
- What a plugin is handed: its saved logins, and "" for a login this system
  does not have, never the marker; a key the plugin makes itself is made
  again and never waits for anyone.
- The mesh key: the mesh runs with the saved key, the log masks it, and the
  ISC page's Show and Change.

An invented plugin and an invented device throughout.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import zipfile
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from openavc.api import plugins as plugin_routes
from openavc.api import rest
from openavc.core import plugin_secrets
from openavc.core import project_library as library
from openavc.utils.log_redaction import get_secret_registry
from openavc.core.device_logins import (
    ISC_FIELD,
    ISC_OWNER,
    LOGINS_FILENAME,
    PLUGIN,
    PROJECT,
    REMOVED_KEEP,
    SAVED_LOGIN,
    KEY_FILENAME,
    DeviceLogins,
    FoundLogin,
    FoundView,
    StoreView,
    in_project,
    move_logins,
    plugin_owner,
    put_back,
    put_back_for_rollback,
    saved_logins_of,
)
from openavc.core.plugin_loader import register_plugin_class
from openavc.core.project_loader import ISCConfig, PluginConfig, ProjectConfig, ProjectMeta
from openavc.main import app

PLUGIN_ID = "acme_bridge"
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class _AcmeBridge:
    """Logins in every place a plugin can declare one."""

    PLUGIN_INFO = {
        "id": PLUGIN_ID,
        "name": "Acme Bridge",
        "version": "1.0.0",
        "author": "Acme",
        "description": "An invented plugin with logins.",
        "category": "integration",
        "license": "MIT",
        "platforms": ["all"],
        "capabilities": [],
    }
    CONFIG_SCHEMA = {
        "host": {"type": "string", "label": "Host"},
        "username": {"type": "string", "label": "Username"},
        "password": {"type": "string", "label": "Password"},
        "connection": {
            "type": "group",
            "label": "Connection",
            "fields": {
                "api_key": {"type": "string", "label": "API key", "secret": True},
                "region": {"type": "string", "label": "Region"},
            },
        },
        "routes": {
            "type": "mapping_list",
            "label": "Routes",
            "item_schema": {
                "topic": {"type": "string"},
                "token": {"type": "string", "secret": True},
            },
        },
    }
    CONFIG_SECRETS = {
        "feeds": {"id": "feed_id", "fields": ["password"]},
        "screens": {"id": "id", "fields": ["screen_key"], "generated": True},
    }

    #: Every config a start or a hot apply handed an instance, in order.
    handed: list[dict[str, Any]] = []
    starts = 0

    def __init__(self):
        self.api = None

    async def start(self, api):
        type(self).starts += 1
        type(self).handed.append(api.config)
        self.api = api
        config = api.config
        made = False
        for screen in config.get("screens", []):
            if not screen.get("screen_key"):
                screen["screen_key"] = f"made-{screen['id']}-key"
                made = True
        if made:
            await api.save_config(config)

    async def stop(self):
        pass


@pytest.fixture(autouse=True)
def _plugin():
    _AcmeBridge.handed = []
    _AcmeBridge.starts = 0
    register_plugin_class(_AcmeBridge)


@pytest.fixture(autouse=True)
def _no_secrets_left_behind():
    """The logins a project here saves go into the process-wide redaction
    registry; clear them, so a later test on the same worker does not read
    this file's secrets as its own."""
    yield
    get_secret_registry().clear()


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(folder))
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", folder / "saved_projects")
    return folder


@pytest.fixture
async def engine(data, tmp_path):
    from openavc.core.engine import Engine

    eng = Engine(str(tmp_path / "project" / "project.avc"))
    eng.project_path.parent.mkdir(parents=True)
    eng.plugin_loader.set_save_config_fn(eng._save_plugin_config)
    rest.set_engine(eng)
    plugin_routes.set_engine(eng)
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            yield eng
    finally:
        rest.set_engine(None)
        plugin_routes.set_engine(None)
        await eng.plugin_loader.stop_all()
        await eng.devices.disconnect_all()


@pytest.fixture
async def client(engine):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        yield http


def _config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "host": "192.0.2.20",
        "username": "admin",
        "password": "Bridge-Pass-1",
        "connection": {"api_key": "key-ABCDEF", "region": "east"},
        "routes": [{"topic": "room/1", "token": "row-token-1"}],
        "feeds": [
            {"feed_id": "lobby", "name": "Lobby", "password": "Feed-Pass-L"},
            {"feed_id": "stage", "name": "Stage", "password": ""},
        ],
        "screens": [{"id": "wall", "screen_key": "wall-key-123"}],
    }
    config.update(overrides)
    return config


def _project(config: dict[str, Any] | None = None, *, isc_key: str = "Mesh-Key-123",
             enabled: bool = True) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id="p1", name="Hall"),
        devices=[],
        connections={},
        plugins={PLUGIN_ID: PluginConfig(enabled=enabled, config=config if config is not None else _config())},
        isc=ISCConfig(enabled=False, auth_key=isc_key),
    )


def _write(path, project: ProjectConfig | dict[str, Any]) -> None:
    document = project if isinstance(project, dict) else project.model_dump(mode="json")
    path.write_text(json.dumps(document, indent=4), encoding="utf-8")


def _on_disk(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _plugin_on_disk(engine) -> dict[str, Any]:
    return _on_disk(engine.project_path)["plugins"][PLUGIN_ID]["config"]


LOBBY = plugin_secrets.item_name("feeds", "feed_id", "lobby")
WALL = plugin_secrets.item_name("screens", "id", "wall")


# --- what a plugin declares ----------------------------------------------------------


def test_the_marker_is_the_one_the_login_file_writes():
    assert plugin_secrets._MARKER == SAVED_LOGIN


def test_a_plugin_declares_its_logins_in_its_schema_and_its_lists():
    secrets = plugin_secrets.declared(_AcmeBridge)
    # The password counts by its name, the API key by its mark, inside its group.
    assert secrets.schema_fields == (("password",), ("connection", "api_key"))
    assert [(d.key, d.id_key, d.fields, d.generated) for d in secrets.lists] == [
        ("feeds", "feed_id", ("password",), False),
        ("screens", "id", ("screen_key",), True),
    ]
    found = plugin_secrets.find(_config(), secrets)
    assert found == {
        "": {"password": "Bridge-Pass-1", "connection.api_key": "key-ABCDEF"},
        LOBBY: {"password": "Feed-Pass-L"},
        WALL: {"screen_key": "wall-key-123"},
    }


def test_a_row_secret_stays_in_the_project_and_is_named():
    assert plugin_secrets.unsupported_row_secrets(_AcmeBridge.CONFIG_SCHEMA) == ["routes.token"]
    assert "routes" not in str(plugin_secrets.find(_config(), plugin_secrets.declared(_AcmeBridge)))


def test_two_items_sharing_an_id_keep_their_logins_where_they_are():
    config = _config(feeds=[
        {"feed_id": "lobby", "password": "One-Pass-1"},
        {"feed_id": "lobby", "password": "Two-Pass-2"},
    ])
    found = plugin_secrets.find(config, plugin_secrets.declared(_AcmeBridge))
    assert LOBBY not in found


@pytest.mark.parametrize("declaration, says", [
    (["feeds"], "must be a dict"),
    ({"feeds": {"id": "feed_id"}}, "distinct field names"),
    ({"feeds": {"id": "", "fields": ["password"]}}, "the key holding each item's id"),
    ({"feeds": {"id": "feed_id", "fields": ["password", "password"]}}, "distinct field names"),
    ({"feeds": {"id": "feed_id", "fields": ["feed_id"]}}, "cannot also be a login"),
    ({"feeds": {"id": "feed_id", "fields": ["password"], "generated": "yes"}}, "true or false"),
    ({"feeds": {"id": "feed_id", "fields": ["password"], "extra": 1}}, "unknown keys"),
    ({"host": {"id": "feed_id", "fields": ["password"]}}, "also a CONFIG_SCHEMA field"),
])
def test_a_malformed_list_declaration_is_named(declaration, says):
    problem = plugin_secrets.config_secrets_problem(declaration, _AcmeBridge.CONFIG_SCHEMA)
    assert problem is not None and says in problem


async def test_a_plugin_with_a_malformed_list_declaration_is_not_started(engine):
    class _Broken(_AcmeBridge):
        PLUGIN_INFO = {**_AcmeBridge.PLUGIN_INFO, "id": "acme_broken"}
        CONFIG_SECRETS = {"feeds": {"id": "feed_id", "fields": []}}

    register_plugin_class(_Broken)
    await engine.plugin_loader.start_plugin("acme_broken", {})
    assert not engine.plugin_loader.is_running("acme_broken")
    assert "CONFIG_SECRETS" in engine.plugin_loader._errors["acme_broken"]


# --- the login file ------------------------------------------------------------------


def test_plugin_logins_and_the_mesh_key_are_filed_beside_the_devices_and_read_back(data):
    logins = DeviceLogins(data)
    logins.save("proj1", "acme_widget", {"password": "Device-Pass"})
    logins.store([
        FoundLogin(PLUGIN_ID, "", {"password": "Bridge-Pass-1"}, PLUGIN),
        FoundLogin(PLUGIN_ID, LOBBY, {"password": "Feed-Pass-L"}, PLUGIN),
        FoundLogin("isc", "", {ISC_FIELD: "Mesh-Key-123"}, PROJECT),
    ], typed=True)

    fresh = DeviceLogins(data)
    assert fresh.login("proj1", "acme_widget", "password") == "Device-Pass"
    assert fresh.secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-1"
    assert fresh.secret(plugin_owner(PLUGIN_ID, LOBBY), "password") == "Feed-Pass-L"
    assert fresh.secret(ISC_OWNER, ISC_FIELD) == "Mesh-Key-123"
    assert fresh.plugin_items(PLUGIN_ID) == {
        "": {"password": "Bridge-Pass-1"}, LOBBY: {"password": "Feed-Pass-L"},
    }
    # The same format: one file, one number.
    assert json.loads((data / LOGINS_FILENAME).read_text())["version"] == 2
    for value in ("Device-Pass", "Bridge-Pass-1", "Feed-Pass-L", "Mesh-Key-123"):
        assert value not in (data / LOGINS_FILENAME).read_text()


def test_a_sweep_keeps_a_plugins_logins_while_it_and_its_items_are_in_the_project(data):
    logins = DeviceLogins(data)
    logins.store([
        FoundLogin(PLUGIN_ID, "", {"password": "Bridge-Pass-1"}, PLUGIN),
        FoundLogin(PLUGIN_ID, LOBBY, {"password": "Feed-Pass-L"}, PLUGIN),
        FoundLogin("isc", "", {ISC_FIELD: "Mesh-Key-123"}, PROJECT),
    ], typed=True)
    project = _project(_config(feeds=[]))  # the lobby feed was deleted

    logins.sweep(in_project(project), now=NOW)
    entries = logins._entries
    assert entries[(None, PLUGIN, PLUGIN_ID, "")].removed_at is None
    assert entries[(None, PLUGIN, PLUGIN_ID, LOBBY)].removed_at == NOW
    assert entries[(None, *ISC_OWNER)].removed_at is None

    logins.sweep(in_project(project), now=NOW + REMOVED_KEEP)
    assert (None, PLUGIN, PLUGIN_ID, LOBBY) not in logins._entries
    assert logins.secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-1"

    # A plugin taken out of the project goes the same way.
    without = _project()
    without.plugins = {}
    logins.sweep(in_project(without), now=NOW)
    assert logins._entries[(None, PLUGIN, PLUGIN_ID, "")].removed_at == NOW


# --- every way in -----------------------------------------------------------------------


async def test_a_project_loaded_moves_plugin_logins_and_the_mesh_key_and_hands_them_back(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()

    on_disk = _on_disk(engine.project_path)
    plugin = on_disk["plugins"][PLUGIN_ID]["config"]
    assert plugin["password"] == SAVED_LOGIN
    assert plugin["connection"] == {"api_key": SAVED_LOGIN, "region": "east"}
    assert plugin["feeds"][0]["password"] == SAVED_LOGIN
    assert plugin["feeds"][1]["password"] == ""  # nothing to move
    assert plugin["screens"][0]["screen_key"] == SAVED_LOGIN
    assert plugin["routes"][0]["token"] == "row-token-1"  # not a login a row can have
    assert on_disk["isc"]["auth_key"] == SAVED_LOGIN
    for value in ("Bridge-Pass-1", "key-ABCDEF", "Feed-Pass-L", "wall-key-123", "Mesh-Key-123"):
        assert value not in engine.project_path.read_text()

    # The plugin runs with every one of them.
    handed = _AcmeBridge.handed[-1]
    assert handed["password"] == "Bridge-Pass-1"
    assert handed["connection"]["api_key"] == "key-ABCDEF"
    assert handed["feeds"][0]["password"] == "Feed-Pass-L"
    assert handed["screens"][0]["screen_key"] == "wall-key-123"


async def test_a_login_typed_in_the_settings_replaces_the_saved_one_and_restarts_once(engine, client, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    starts = _AcmeBridge.starts

    config = json.loads(json.dumps(engine.project.plugins[PLUGIN_ID].config))
    config["password"] = "Bridge-Pass-2"
    reply = await client.put(f"/api/plugins/{PLUGIN_ID}/config", json=config)

    assert reply.status_code == 200, reply.text
    assert _AcmeBridge.starts == starts + 1
    assert _AcmeBridge.handed[-1]["password"] == "Bridge-Pass-2"
    assert _AcmeBridge.handed[-1]["connection"]["api_key"] == "key-ABCDEF"
    assert DeviceLogins(data).secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-2"
    assert _plugin_on_disk(engine)["password"] == SAVED_LOGIN

    shown = (await client.get(f"/api/plugins/{PLUGIN_ID}/config")).json()
    assert shown["config"]["password"] == SAVED_LOGIN
    assert shown["needs_login"] == []
    assert "Bridge-Pass-2" not in json.dumps(shown)


async def test_the_marker_sent_back_keeps_the_saved_login(engine, client, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    starts = _AcmeBridge.starts

    # A settings save restarts the plugin once, through the project change
    # that saves it, with the saved logins.
    config = json.loads(json.dumps(engine.project.plugins[PLUGIN_ID].config))
    config["host"] = "192.0.2.21"
    reply = await client.put(f"/api/plugins/{PLUGIN_ID}/config", json=config)

    assert reply.status_code == 200, reply.text
    assert _AcmeBridge.starts == starts + 1
    assert _AcmeBridge.handed[-1]["host"] == "192.0.2.21"
    assert _AcmeBridge.handed[-1]["password"] == "Bridge-Pass-1"
    assert _AcmeBridge.handed[-1]["feeds"][0]["password"] == "Feed-Pass-L"
    assert DeviceLogins(data).secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-1"
    assert _plugin_on_disk(engine)["password"] == SAVED_LOGIN


async def test_a_reconcile_with_the_marker_in_place_does_not_restart_the_plugin(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    starts = _AcmeBridge.starts

    def rename(project):
        project.project.name = "Hall 2"

    await engine.apply_project_edit(rename)
    await engine._sync_plugins()
    assert _AcmeBridge.starts == starts
    assert not engine.plugin_loader.config_differs(PLUGIN_ID, engine.project.plugins[PLUGIN_ID].config)


async def test_a_login_the_plugin_saves_itself_moves_out_of_the_project(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()

    api = engine.plugin_loader._apis[PLUGIN_ID]
    config = api.config
    config["feeds"][1]["password"] = "Feed-Pass-S"
    await api.save_config(config)

    assert DeviceLogins(data).secret(
        plugin_owner(PLUGIN_ID, plugin_secrets.item_name("feeds", "feed_id", "stage")), "password",
    ) == "Feed-Pass-S"
    assert _plugin_on_disk(engine)["feeds"][1]["password"] == SAVED_LOGIN
    assert engine.project.plugins[PLUGIN_ID].config["feeds"][1]["password"] == SAVED_LOGIN
    # The plugin keeps running with what it saved.
    assert api.config["feeds"][1]["password"] == "Feed-Pass-S"
    assert "Feed-Pass-S" not in engine.project_path.read_text()


async def test_a_login_not_saved_here_is_handed_empty_and_named(engine, client, data):
    # A copy from another system: every login is the marker, none saved here.
    marked = _config(
        password=SAVED_LOGIN,
        connection={"api_key": SAVED_LOGIN, "region": "east"},
        feeds=[{"feed_id": "lobby", "name": "Lobby", "password": SAVED_LOGIN}],
    )
    _write(engine.project_path, _project(marked, isc_key=SAVED_LOGIN))
    await engine.reload_project()

    handed = _AcmeBridge.handed[0]
    assert SAVED_LOGIN not in json.dumps(handed)
    assert handed["password"] == ""
    assert handed["connection"]["api_key"] == ""
    assert handed["feeds"][0]["password"] == ""

    shown = (await client.get(f"/api/plugins/{PLUGIN_ID}/config")).json()
    assert shown["needs_login"] == ["password", "connection.api_key", "feeds[lobby].password"]
    assert engine.plugin_logins_needed() == [{"id": PLUGIN_ID, "name": "Acme Bridge"}]

    # The plugin saving something else keeps them waiting, not "not set".
    api = engine.plugin_loader._apis[PLUGIN_ID]
    config = api.config
    config["host"] = "192.0.2.22"
    await api.save_config(config)
    saved = _plugin_on_disk(engine)
    assert saved["password"] == SAVED_LOGIN
    assert saved["feeds"][0]["password"] == SAVED_LOGIN
    assert saved["host"] == "192.0.2.22"

    # Entering it here ends the wait.
    config = json.loads(json.dumps(engine.project.plugins[PLUGIN_ID].config))
    config["password"] = "Bridge-Pass-3"
    await client.put(f"/api/plugins/{PLUGIN_ID}/config", json=config)
    shown = (await client.get(f"/api/plugins/{PLUGIN_ID}/config")).json()
    assert shown["needs_login"] == ["connection.api_key", "feeds[lobby].password"]


async def test_a_key_the_plugin_makes_itself_is_made_again_and_never_waits(engine, data):
    marked = _config(screens=[{"id": "wall", "screen_key": SAVED_LOGIN}])
    _write(engine.project_path, _project(marked))
    await engine.reload_project()
    # Made at start, inside the reload, so its save lands right after it.
    await engine._bookkeeping_task

    # Handed "", the plugin made a new key, which is now saved here.
    assert DeviceLogins(data).secret(plugin_owner(PLUGIN_ID, WALL), "screen_key") == "made-wall-key"
    assert _plugin_on_disk(engine)["screens"][0]["screen_key"] == SAVED_LOGIN
    assert engine.plugin_loader._apis[PLUGIN_ID].config["screens"][0]["screen_key"] == "made-wall-key"
    assert engine.plugin_logins_needed() == []


async def test_the_first_start_moves_plugin_logins_once_the_plugins_are_loaded(engine, data):
    from openavc.core.backup_manager import list_backups

    _write(engine.project_path, _project(isc_key=""))
    engine.project = engine._load_project_safe()

    await engine._move_logins_at_start()

    assert _plugin_on_disk(engine)["password"] == SAVED_LOGIN
    assert DeviceLogins(data).secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-1"
    assert [b.reason for b in list_backups(engine.project_path)] == [
        "Before moving device logins out of the project",
    ]


async def test_a_start_that_already_backed_up_takes_no_second_backup(engine, data):
    from openavc.core.backup_manager import list_backups

    _write(engine.project_path, _project(isc_key=""))
    engine.project = engine._load_project_safe()
    engine._logins_backed_up_at_start = True

    await engine._move_logins_at_start()

    assert _plugin_on_disk(engine)["password"] == SAVED_LOGIN
    assert list_backups(engine.project_path) == []


def _start_backup(engine):
    from openavc.core.backup_manager import list_backups

    [backup] = list_backups(engine.project_path)
    assert backup.reason == "Before moving device logins out of the project"
    return engine.project_path.parent / backup.filename


def _members(path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


MOVED = ("Bridge-Pass-1", "key-ABCDEF", "Feed-Pass-L", "wall-key-123", "Mesh-Key-123")


async def test_once_the_start_has_moved_every_login_its_backup_holds_none_in_the_clear(
    engine, data, tmp_path,
):
    _write(engine.project_path, _project())
    engine.project = engine._load_project_safe()
    await engine._move_logins_at_start()
    backup = _start_backup(engine)
    taken = backup.stat().st_mtime_ns
    assert b"Bridge-Pass-1" in _members(backup)["project.avc"]  # until the move is done

    await engine._seal_the_start_backup()

    members = _members(backup)
    for value in MOVED:
        assert all(value.encode() not in content for content in members.values())
    kept = json.loads(members["project.avc"])
    assert kept["plugins"][PLUGIN_ID]["config"]["password"] == SAVED_LOGIN
    assert kept["plugins"][PLUGIN_ID]["config"]["feeds"][0]["password"] == SAVED_LOGIN
    assert kept["isc"]["auth_key"] == SAVED_LOGIN
    assert "backup_meta.json" in members
    # It lists and rotates where it did, and restored on this system it fills
    # in every login it held.
    assert backup.stat().st_mtime_ns == taken
    _start_backup(engine)
    elsewhere = tmp_path / "same_system_new_login_file"
    elsewhere.mkdir()
    shutil.copy(data / KEY_FILENAME, elsewhere / KEY_FILENAME)
    restored = DeviceLogins(elsewhere)
    restored.restore_copy(members["device_logins.json"].decode(), in_project(kept), where="the test")
    assert restored.secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-1"
    assert restored.secret(plugin_owner(PLUGIN_ID, LOBBY), "password") == "Feed-Pass-L"
    assert restored.secret(ISC_OWNER, ISC_FIELD) == "Mesh-Key-123"


async def test_a_start_whose_save_after_the_move_failed_keeps_its_backup_as_it_was(
    engine, data, monkeypatch,
):
    from openavc.core import project_logins

    _write(engine.project_path, _project())
    engine.project = engine._load_project_safe()

    async def no_room(path, project):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(project_logins, "save_project_async", no_room)
    with pytest.raises(OSError):
        await engine._move_logins_at_start()
    backup = _start_backup(engine)
    before = backup.read_bytes()

    await engine._seal_the_start_backup()

    assert backup.read_bytes() == before
    assert b"Bridge-Pass-1" in _members(backup)["project.avc"]


async def test_a_plugin_that_is_not_installed_keeps_its_logins_where_they_are(engine, data):
    project = _project()
    project.plugins = {"acme_absent": PluginConfig(enabled=True, config={"password": "Absent-Pass"})}
    moved = move_logins(project, engine.device_logins, typed=False)
    assert moved.plugins["acme_absent"].config["password"] == "Absent-Pass"


# --- copies ----------------------------------------------------------------------------


def _saved(data) -> DeviceLogins:
    logins = DeviceLogins(data)
    moved = move_logins(_project(), logins, typed=True)
    assert moved.plugins[PLUGIN_ID].config["password"] == SAVED_LOGIN
    return logins


def test_a_backup_carries_the_plugin_logins_and_the_mesh_key_sealed(data):
    logins = _saved(data)
    marked = move_logins(_project(), logins, typed=False)
    sealed = logins.backup_copy(in_project(marked))
    for value in ("Bridge-Pass-1", "Feed-Pass-L", "Mesh-Key-123"):
        assert value not in sealed

    elsewhere = DeviceLogins(data / "fresh")
    elsewhere.key_path.parent.mkdir()
    elsewhere.key_path.write_bytes(logins.key_path.read_bytes())  # the same system, its file lost
    filled = elsewhere.restore_copy(sealed, in_project(marked), where="the backup")
    assert filled == 5
    assert elsewhere.secret(plugin_owner(PLUGIN_ID, LOBBY), "password") == "Feed-Pass-L"
    assert elsewhere.secret(ISC_OWNER, ISC_FIELD) == "Mesh-Key-123"


def test_an_export_seals_the_plugin_logins_and_the_mesh_key(data):
    from openavc.core import login_bundle

    logins = _saved(data)
    marked = move_logins(_project(), logins, typed=False).model_dump(mode="json")
    found = saved_logins_of(marked, StoreView(logins))
    assert {(f.kind, f.name, f.qualifier) for f in found} == {
        (PLUGIN, PLUGIN_ID, ""), (PLUGIN, PLUGIN_ID, LOBBY), (PLUGIN, PLUGIN_ID, WALL),
        (PROJECT, "isc", ""),
    }

    raw = login_bundle.seal(found, "a long passphrase")
    for value in (b"Bridge-Pass-1", b"Feed-Pass-L", b"Mesh-Key-123"):
        assert value not in raw
    opened = login_bundle.open_bundle(raw, "a long passphrase")
    assert sorted(opened, key=lambda f: f.owner) == sorted(found, key=lambda f: f.owner)

    # On another system: the opened bundle puts each one back in place.
    restored = put_back(marked, FoundView(opened))
    assert restored["plugins"][PLUGIN_ID]["config"]["password"] == "Bridge-Pass-1"
    assert restored["plugins"][PLUGIN_ID]["config"]["connection"]["api_key"] == "key-ABCDEF"
    assert restored["plugins"][PLUGIN_ID]["config"]["feeds"][0]["password"] == "Feed-Pass-L"
    assert restored["isc"]["auth_key"] == "Mesh-Key-123"


def test_a_rollback_puts_the_plugin_logins_and_the_mesh_key_back(data, tmp_path):
    logins = _saved(data)
    project_path = tmp_path / "project.avc"
    _write(project_path, move_logins(_project(), logins, typed=False))
    copy_dir = data / "saved_projects" / "room_b"
    copy_dir.mkdir(parents=True)
    _write(copy_dir / "project.avc", move_logins(_project(), logins, typed=False))

    put_back_for_rollback(data, project_path)

    for path in (project_path, copy_dir / "project.avc"):
        document = _on_disk(path)
        plugin = document["plugins"][PLUGIN_ID]["config"]
        assert plugin["password"] == "Bridge-Pass-1"
        assert plugin["connection"]["api_key"] == "key-ABCDEF"
        assert plugin["feeds"][0]["password"] == "Feed-Pass-L"
        assert plugin["screens"][0]["screen_key"] == "wall-key-123"
        assert document["isc"]["auth_key"] == "Mesh-Key-123"


def test_a_library_copy_takes_the_plugin_logins_with_it_and_brings_them_back(data):
    logins = _saved(data)
    marked = move_logins(_project(), logins, typed=False)
    logins.file_library("room_b", in_project(marked))
    assert logins.secret(plugin_owner(PLUGIN_ID, LOBBY), "password", library="room_b") == "Feed-Pass-L"

    # Opened on a system that lost the running project's: the copy's fill in.
    other = DeviceLogins(data)
    other._entries = {k: v for k, v in other._entries.items() if k[0] is not None}
    other.adopt_library("room_b", in_project(marked))
    assert other.secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-1"
    assert other.secret(ISC_OWNER, ISC_FIELD) == "Mesh-Key-123"


# --- the mesh key ---------------------------------------------------------------------


async def test_the_mesh_runs_with_the_saved_key_and_the_log_masks_it(engine, data):
    from openavc.core.device_logins import saved_isc_key
    from openavc.utils.log_redaction import get_secret_registry

    _write(engine.project_path, _project())
    await engine.reload_project()

    assert engine.project.isc.auth_key == SAVED_LOGIN
    assert saved_isc_key(engine.project, engine.device_logins) == "Mesh-Key-123"
    registry = get_secret_registry()
    assert registry.redact_any("key Mesh-Key-123 and Bridge-Pass-1") == "key *** and ***"
    assert SAVED_LOGIN not in registry.all_secrets()


async def test_the_isc_page_shows_and_changes_the_key(engine, client, data):
    _write(engine.project_path, _project())
    await engine.reload_project()

    status = (await client.get("/api/isc/status")).json()
    assert status["auth_key"] == "saved"
    assert "Mesh-Key-123" not in json.dumps(status)
    assert (await client.get("/api/isc/auth-key")).json() == {"auth_key": "Mesh-Key-123"}

    reply = await client.put("/api/isc/auth-key", json={"auth_key": "Mesh-Key-456"})
    assert reply.status_code == 200, reply.text
    assert (await client.get("/api/isc/auth-key")).json() == {"auth_key": "Mesh-Key-456"}
    assert _on_disk(engine.project_path)["isc"]["auth_key"] == SAVED_LOGIN
    assert "Mesh-Key-456" not in engine.project_path.read_text()

    for refused in ("", "   ", SAVED_LOGIN):
        reply = await client.put("/api/isc/auth-key", json={"auth_key": refused})
        assert reply.status_code == 400
    assert (await client.get("/api/isc/auth-key")).json() == {"auth_key": "Mesh-Key-456"}


async def test_the_isc_page_says_when_the_key_is_not_saved_here(engine, client):
    _write(engine.project_path, _project(isc_key=SAVED_LOGIN))
    await engine.reload_project()
    assert (await client.get("/api/isc/status")).json()["auth_key"] == "needs_login"
    assert (await client.get("/api/isc/auth-key")).json() == {"auth_key": ""}

    _write(engine.project_path, _project(isc_key=""))
    await engine.reload_project()
    assert (await client.get("/api/isc/status")).json()["auth_key"] == "none"


async def test_a_mesh_key_change_reloads_the_running_mesh(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    engine.isc = AsyncMock()
    with patch.object(engine, "isc_gates", return_value=(True, True)):

        def mutate(project):
            project.isc.auth_key = "Mesh-Key-789"

        await engine.apply_project_edit(mutate)
    engine.isc.reload.assert_awaited()
    assert engine.isc.reload.await_args.kwargs["auth_key"] == "Mesh-Key-789"
    engine.isc = None


# --- the assistant's log --------------------------------------------------------------


async def test_the_assistants_log_hides_a_plugin_login_saved_here(engine, data):
    from openavc.utils.log_redaction import get_secret_registry
    from openavc.utils.secret_audience import Exit

    _write(engine.project_path, _project())
    await engine.reload_project()

    secrets = get_secret_registry().to_mask(Exit.CLOUD)
    assert {"Bridge-Pass-1", "Feed-Pass-L", "Mesh-Key-123"} <= secrets
    assert SAVED_LOGIN not in secrets


# --- a plugin's own save reaches the platform once the file holds it ---------------


@asynccontextmanager
async def _inside_a_change(engine):
    """Where a plugin hook runs: inside a change that holds the lock, so a
    save it makes is queued behind that change."""
    async with engine._reload_lock:
        with engine._running_hooks():
            yield


async def test_a_save_queued_behind_a_change_reaches_the_platform_once_saved(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]

    config = api.config
    config["host"] = "192.0.2.77"
    async with _inside_a_change(engine):
        await api.save_config(config)
        # The plugin reads its own save at once; the platform's copy, which
        # the next change compares with and the settings page shows, waits
        # for the file.
        assert api.config["host"] == "192.0.2.77"
        assert engine.plugin_loader.get_running_config(PLUGIN_ID)["host"] == "192.0.2.20"
        assert engine.project.plugins[PLUGIN_ID].config["host"] == "192.0.2.20"
    await engine._bookkeeping_task

    assert _plugin_on_disk(engine)["host"] == "192.0.2.77"
    assert engine.plugin_loader.get_running_config(PLUGIN_ID)["host"] == "192.0.2.77"
    assert engine.project.plugins[PLUGIN_ID].config["host"] == "192.0.2.77"
    assert api.config["host"] == "192.0.2.77"


@pytest.mark.parametrize("queued", [False, True])
async def test_a_save_that_fails_leaves_the_plugin_and_the_login_file_as_saved(engine, data, monkeypatch, queued):
    from openavc.core import project_changes

    _write(engine.project_path, _project())
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]

    async def disk_full(path, project):
        raise OSError("Injected unwritable project")

    config = api.config
    config["host"] = "192.0.2.78"
    config["password"] = "Bridge-Pass-9"
    monkeypatch.setattr(project_changes, "save_project_async", disk_full)
    if queued:
        async with _inside_a_change(engine):
            await api.save_config(config)
        await engine._bookkeeping_task
    else:
        with pytest.raises(OSError):
            await api.save_config(config)

    assert api.config["host"] == "192.0.2.20"
    assert api.config["password"] == "Bridge-Pass-1"
    assert engine.plugin_loader.get_running_config(PLUGIN_ID)["host"] == "192.0.2.20"
    assert engine.project.plugins[PLUGIN_ID].config["host"] == "192.0.2.20"
    assert DeviceLogins(data).secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-1"


@pytest.mark.parametrize("hot_apply", [False, True])
async def test_a_config_handed_to_the_plugin_before_its_queued_save_lands_is_kept(engine, data, hot_apply):
    _write(engine.project_path, _project())
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]
    if hot_apply:
        engine.plugin_loader._instances[PLUGIN_ID].on_config_changed = AsyncMock(return_value=True)

    config = api.config
    config["host"] = "192.0.2.79"
    async with _inside_a_change(engine):
        await api.save_config(config)
        # The settings form hands the plugin its config before its own
        # change takes the lock.
        handed = json.loads(json.dumps(engine.project.plugins[PLUGIN_ID].config))
        handed["host"] = "192.0.2.80"
        await engine.plugin_loader.restart_or_apply(PLUGIN_ID, handed)
    await engine._bookkeeping_task

    running = engine.plugin_loader._apis[PLUGIN_ID]
    assert (running is api) is hot_apply
    assert running.config["host"] == "192.0.2.80"
    assert engine.plugin_loader.get_running_config(PLUGIN_ID)["host"] == "192.0.2.80"
    assert _plugin_on_disk(engine)["host"] != "192.0.2.79"
    assert engine.project.plugins[PLUGIN_ID].config["host"] != "192.0.2.79"


async def test_a_later_save_that_reached_the_file_first_is_not_written_over(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]

    earlier, later = api.config, api.config
    earlier["host"] = "192.0.2.81"
    later["host"] = "192.0.2.82"
    async with _inside_a_change(engine):
        # Scheduled before the earlier save queues its flush, so the later
        # save runs first once the lock is free, and reaches the file first.
        landing = asyncio.ensure_future(api.save_config(later))
        await api.save_config(earlier)
    await landing
    await engine._bookkeeping_task

    assert _plugin_on_disk(engine)["host"] == "192.0.2.82"
    assert engine.project.plugins[PLUGIN_ID].config["host"] == "192.0.2.82"
    assert api.config["host"] == "192.0.2.82"
    assert engine.plugin_loader.get_running_config(PLUGIN_ID)["host"] == "192.0.2.82"


async def test_a_save_a_plugin_makes_as_it_stops_is_written(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]
    instance = engine.plugin_loader._instances[PLUGIN_ID]

    async def stop():
        config = api.config
        config["host"] = "192.0.2.83"
        await api.save_config(config)

    instance.stop = stop
    await engine.plugin_loader.stop_plugin(PLUGIN_ID)

    assert _plugin_on_disk(engine)["host"] == "192.0.2.83"
    assert engine.project.plugins[PLUGIN_ID].config["host"] == "192.0.2.83"


async def test_a_save_a_plugin_makes_as_a_change_disables_it_is_written(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]
    instance = engine.plugin_loader._instances[PLUGIN_ID]

    async def stop():
        config = api.config
        config["host"] = "192.0.2.85"
        # Inside the change that disables it, so the save waits for it.
        await api.save_config(config)

    instance.stop = stop
    disabled = engine.project.model_copy(deep=True)
    disabled.plugins[PLUGIN_ID].enabled = False
    await engine.apply_project(disabled)
    if engine._bookkeeping_task is not None:
        await engine._bookkeeping_task

    assert not engine.plugin_loader.is_running(PLUGIN_ID)
    assert _plugin_on_disk(engine)["host"] == "192.0.2.85"


async def test_a_generated_login_handed_back_empty_keeps_the_marker(engine, data, monkeypatch):
    async def start_without_making_keys(self, api):
        self.api = api

    monkeypatch.setattr(_AcmeBridge, "start", start_without_making_keys)
    marked = _config(screens=[{"id": "wall", "screen_key": SAVED_LOGIN}])
    _write(engine.project_path, _project(marked))
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]

    config = api.config
    assert config["screens"][0]["screen_key"] == ""
    config["host"] = "192.0.2.86"
    await api.save_config(config)

    assert _plugin_on_disk(engine)["host"] == "192.0.2.86"
    assert _plugin_on_disk(engine)["screens"][0]["screen_key"] == SAVED_LOGIN


async def test_a_login_saved_behind_a_change_is_masked_before_its_file_holds_it(engine, data):
    _write(engine.project_path, _project())
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]

    config = api.config
    config["password"] = "Bridge-Pass-7"
    async with _inside_a_change(engine):
        await api.save_config(config)
        assert "Bridge-Pass-7" not in get_secret_registry().redact_any("sent Bridge-Pass-7")
        # Any registration meanwhile still knows it.
        engine._register_project_secrets()
        assert "Bridge-Pass-7" not in get_secret_registry().redact_any("sent Bridge-Pass-7")
    await engine._bookkeeping_task
    assert "Bridge-Pass-7" not in get_secret_registry().redact_any("sent Bridge-Pass-7")
    assert DeviceLogins(data).secret(plugin_owner(PLUGIN_ID), "password") == "Bridge-Pass-7"


async def test_a_save_made_after_a_reconcile_outlives_the_one_made_during_it(engine, data):
    # The plugin makes a key at start, inside the reconcile, so that save is
    # persisted after the reconcile. A save the plugin makes as soon as the
    # reload returns lands first; the earlier one must not be written over it.
    marked = _config(screens=[{"id": "wall", "screen_key": SAVED_LOGIN}])
    _write(engine.project_path, _project(marked))
    await engine.reload_project()

    api = engine.plugin_loader._apis[PLUGIN_ID]
    config = api.config
    config["host"] = "192.0.2.99"
    await api.save_config(config)
    if engine._bookkeeping_task is not None:
        await engine._bookkeeping_task

    assert _plugin_on_disk(engine)["host"] == "192.0.2.99"
    assert engine.project.plugins[PLUGIN_ID].config["host"] == "192.0.2.99"
    # The key made at start is kept, as a saved login.
    assert _plugin_on_disk(engine)["screens"][0]["screen_key"] == SAVED_LOGIN
    assert DeviceLogins(data).secret(plugin_owner(PLUGIN_ID, WALL), "screen_key") == "made-wall-key"


async def test_a_plugin_that_saves_as_a_failed_change_is_undone_never_hangs_it(engine, data, monkeypatch):
    from openavc.core import project_changes
    from openavc.core.project_changes import ProjectNotAppliedError

    async def start_and_save(self, api):
        self.api = api
        await api.save_config(api.config)

    monkeypatch.setattr(_AcmeBridge, "start", start_and_save)
    _write(engine.project_path, _project())
    await engine.reload_project()
    await engine._bookkeeping_task

    async def disk_full(path, project):
        raise OSError("Injected unwritable project")

    changed = engine.project.model_copy(deep=True)
    changed.plugins[PLUGIN_ID].config["host"] = "192.0.2.87"
    monkeypatch.setattr(project_changes, "save_project_async", disk_full)
    # The change restarts the plugin, and its undo restarts it again: each
    # start saves, inside the change.
    with pytest.raises(ProjectNotAppliedError):
        await asyncio.wait_for(engine.apply_project(changed), timeout=10)
    if engine._bookkeeping_task is not None:
        await engine._bookkeeping_task

    assert engine.plugin_loader.is_running(PLUGIN_ID)
    assert engine.project.plugins[PLUGIN_ID].config["host"] == "192.0.2.20"
    assert engine.plugin_loader._apis[PLUGIN_ID].config["host"] == "192.0.2.20"


async def test_a_save_cancelled_while_it_waits_its_turn_leaves_the_plugin_as_saved(engine, data, monkeypatch):
    from openavc.core import project_changes
    from tests.helpers import wait_for_condition

    _write(engine.project_path, _project())
    await engine.reload_project()
    api = engine.plugin_loader._apis[PLUGIN_ID]

    real = project_changes.save_project_async
    released = asyncio.Event()

    async def held(path, project):
        await released.wait()
        await real(path, project)

    monkeypatch.setattr(project_changes, "save_project_async", held)
    other = asyncio.create_task(engine.persist_bookkeeping_change(lambda project: None))
    await wait_for_condition(engine._reload_lock.locked, message="the other save never took the lock")

    config = api.config
    config["host"] = "192.0.2.88"
    saving = asyncio.create_task(api.save_config(config))
    await asyncio.sleep(0.05)
    assert api.config["host"] == "192.0.2.88"
    # A plugin's task cancelled as it stops, while its save waits.
    saving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await saving
    released.set()
    await other

    assert api.config["host"] == "192.0.2.20"
    assert _plugin_on_disk(engine)["host"] == "192.0.2.20"
