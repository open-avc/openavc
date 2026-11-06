"""Device logins in copies of a project: an export, a project backup, an
update backup.

What is pinned here:

- The sealed bundle (``core.login_bundle``): opens with its passphrase and
  nothing else, says when it was sealed without the passphrase, refuses a
  passphrase under the minimum, a file from a newer version and a key method
  that would need more memory than any export asks for.
- An export: without logins (GET) it holds none; with a passphrase (POST) it
  holds them only inside the bundle, nowhere in the clear. A project with no
  saved login refuses the passphrase export. A library copy's export holds
  the login it would get if opened here. The library list says which copies
  have one.
- An import: a .zip holding a bundle answers ``sealed_logins``; the right
  passphrase files them as the copy's own and opening the copy gives each
  device its login, with this system's own login winning; a wrong one is
  refused and can be tried again; a skipped one leaves the devices on Needs
  login.
- A project backup holds its devices' logins sealed with this system's key,
  never the key and never a login in the clear. Restored on this system it
  fills in a login this system no longer has, and never replaces one it has;
  restored on another system it fills in nothing.
- An update backup holds the login file, never its key.

An invented device throughout.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from openavc.api import rest
from openavc.core import login_bundle
from openavc.core import project_library as library
from openavc.core.backup_manager import ARCHIVE_LOGINS, backup_id, create_backup, restore_from_backup
from openavc.core.device_logins import KEY_FILENAME, SAVED_LOGIN, DeviceLogins, FoundLogin
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.drivers import registry
from openavc.drivers.base import BaseDriver
from openavc.main import app

DRIVER = "acme_login_copies"
PASSPHRASE = "Blue harbour 42"
PASSWORD = "Pw-Hall-7731"
API_KEY = "Key-Hall-5519"


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


@pytest.fixture(autouse=True)
def _nothing_held():
    yield
    login_bundle._held.clear()


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
    rest.set_engine(eng)
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            yield eng
    finally:
        rest.set_engine(None)
        await eng.devices.disconnect_all()


@pytest.fixture
async def client(engine):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        yield http


def _device(device_id: str, name: str, **config: Any) -> DeviceConfig:
    return DeviceConfig(id=device_id, driver=DRIVER, name=name, config=dict(config))


def _project(*devices: DeviceConfig, project_id: str = "hall", **passwords: str) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id=project_id, name="Hall"),
        devices=list(devices),
        connections={
            d.id: {"host": "192.0.2.10", "port": 5500, "username": "admin",
                   "password": passwords.get(d.id, SAVED_LOGIN)}
            for d in devices
        },
    )


def _write(path, project: ProjectConfig) -> None:
    path.write_text(json.dumps(project.model_dump(mode="json"), indent=4), encoding="utf-8")


async def _running(engine, project: ProjectConfig) -> None:
    _write(engine.project_path, project)
    await engine.reload_project()


def _members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _readable_anywhere(members: dict[str, bytes], *secrets: str) -> list[str]:
    """Each member that holds one of ``secrets`` as text or as base64 text."""
    hits = []
    for name, body in members.items():
        for secret in secrets:
            forms = (secret.encode(), base64.b64encode(secret.encode()).rstrip(b"="))
            if any(form in body for form in forms):
                hits.append(f"{name}: {secret}")
    return hits


# --- the sealed bundle ----------------------------------------------------------------


def test_a_bundle_opens_with_its_passphrase_and_nothing_else():
    found = [FoundLogin("proj1", DRIVER, {"password": PASSWORD, "api_key": API_KEY})]
    raw = login_bundle.seal(found, PASSPHRASE)

    assert PASSWORD.encode() not in raw and API_KEY.encode() not in raw
    assert login_bundle.open_bundle(raw, PASSPHRASE) == found
    for wrong in ("Blue harbour 43", "blue harbour 42", PASSPHRASE + " ", ""):
        with pytest.raises(login_bundle.WrongPassphrase):
            login_bundle.open_bundle(raw, wrong)


def test_a_bundle_says_when_it_was_sealed_without_the_passphrase():
    from datetime import datetime, timezone

    when = datetime(2026, 11, 5, 14, 3, 7, 123456, tzinfo=timezone.utc)
    raw = login_bundle.seal([FoundLogin("proj1", DRIVER, {"password": PASSWORD})], PASSPHRASE, now=when)

    assert login_bundle.read_info(raw).sealed_at == "2026-11-05T14:03:07+00:00"


def test_a_passphrase_typed_with_a_different_accent_encoding_still_opens_it():
    composed, decomposed = "Café lights 9", "Café lights 9"
    raw = login_bundle.seal([FoundLogin("proj1", DRIVER, {"password": PASSWORD})], composed)

    assert login_bundle.open_bundle(raw, decomposed)[0].fields == {"password": PASSWORD}


def test_a_short_passphrase_is_refused():
    with pytest.raises(login_bundle.PassphraseTooShort, match="at least 8 characters"):
        login_bundle.seal([FoundLogin("proj1", DRIVER, {"password": PASSWORD})], "seven77")


def test_a_damaged_bundle_reads_as_a_passphrase_that_does_not_open_it():
    raw = json.loads(login_bundle.seal([FoundLogin("proj1", DRIVER, {"password": PASSWORD})], PASSPHRASE))
    token = raw["sealed"]
    raw["sealed"] = token[:40] + ("A" if token[40] != "A" else "B") + token[41:]

    with pytest.raises(login_bundle.WrongPassphrase):
        login_bundle.open_bundle(json.dumps(raw).encode(), PASSPHRASE)


@pytest.mark.parametrize("change,why", [
    ({"version": 2}, "newer version"),
    ({"format": "something-else"}, "not a set of sealed device logins"),
    ({"kdf": {"name": "scrypt", "n": 1 << 30, "r": 8, "p": 1, "salt": "c2FsdHNhbHRzYWx0"}}, "will not derive"),
    ({"kdf": {"name": "scrypt", "n": 1000, "r": 8, "p": 1, "salt": "c2FsdHNhbHRzYWx0"}}, "will not derive"),
    ({"kdf": {"name": "argon2"}}, "does not know"),
])
def test_a_bundle_this_version_will_not_open_is_refused_before_deriving(change, why):
    raw = json.loads(login_bundle.seal([FoundLogin("proj1", DRIVER, {"password": PASSWORD})], PASSPHRASE))
    raw.update(change)

    with patch.object(login_bundle, "derive_key", side_effect=AssertionError("derived")):
        with pytest.raises(login_bundle.BundleUnreadable, match=why):
            login_bundle.open_bundle(json.dumps(raw).encode(), PASSPHRASE)


# --- an export -------------------------------------------------------------------------


async def test_an_export_without_logins_holds_none(engine, client):
    await _running(engine, _project(_device("proj1", "Projector", api_key=API_KEY), proj1=PASSWORD))

    answer = await client.get("/api/project/export")

    assert answer.status_code == 200, answer.text
    members = _members(answer.content)
    assert login_bundle.BUNDLE_MEMBER not in members
    assert _readable_anywhere(members, PASSWORD, API_KEY) == []
    project = json.loads(members["project.avc"])
    assert project["connections"]["proj1"]["password"] == SAVED_LOGIN


async def test_an_export_with_logins_holds_them_sealed_and_nowhere_in_the_clear(engine, client):
    await _running(engine, _project(_device("proj1", "Projector", api_key=API_KEY), proj1=PASSWORD))
    plain = _members((await client.get("/api/project/export")).content)

    answer = await client.post("/api/project/export", json={"passphrase": PASSPHRASE})

    assert answer.status_code == 200, answer.text
    assert answer.headers["content-disposition"] == 'attachment; filename="hall.zip"'
    members = _members(answer.content)
    assert _readable_anywhere(members, PASSWORD, API_KEY) == []
    assert members["project.avc"] == plain["project.avc"]
    assert KEY_FILENAME not in " ".join(members)
    opened = login_bundle.open_bundle(members[login_bundle.BUNDLE_MEMBER], PASSPHRASE)
    assert opened == [FoundLogin("proj1", DRIVER, {"api_key": API_KEY, "password": PASSWORD})]


async def test_an_export_with_logins_refuses_a_short_passphrase(engine, client):
    await _running(engine, _project(_device("proj1", "Projector"), proj1=PASSWORD))

    answer = await client.post("/api/project/export", json={"passphrase": "short"})

    assert answer.status_code == 422
    assert answer.json()["detail"] == "The passphrase must be at least 8 characters."
    assert "short" not in answer.text.replace("The passphrase must be at least", "")


async def test_an_export_with_logins_of_a_project_with_none_saved_is_refused(engine, client):
    await _running(engine, _project(_device("proj1", "Projector")))

    answer = await client.post("/api/project/export", json={"passphrase": PASSPHRASE})

    assert answer.status_code == 422
    assert "No logins are saved on this system for this project" in answer.json()["detail"]


async def test_a_library_copy_exports_the_login_it_would_get_if_opened_here(engine, client, data):
    await _running(engine, _project(_device("proj1", "Projector"), proj1=PASSWORD))
    saved = await client.post("/api/library", json={"id": "hall_copy", "name": "Hall copy", "description": ""})
    assert saved.status_code == 200, saved.text
    DeviceLogins(data).save("proj1", DRIVER, {"api_key": API_KEY}, library="hall_copy")
    engine.device_logins._loaded = False
    copy_path = data / "saved_projects" / "hall_copy" / "project.avc"
    copy = json.loads(copy_path.read_text(encoding="utf-8"))
    copy["devices"][0]["config"]["api_key"] = SAVED_LOGIN
    copy_path.write_text(json.dumps(copy), encoding="utf-8")

    answer = await client.post("/api/library/hall_copy/export", json={"passphrase": PASSPHRASE})

    assert answer.status_code == 200, answer.text
    members = _members(answer.content)
    assert _readable_anywhere(members, PASSWORD, API_KEY) == []
    opened = login_bundle.open_bundle(members[login_bundle.BUNDLE_MEMBER], PASSPHRASE)
    assert opened == [FoundLogin("proj1", DRIVER, {"api_key": API_KEY, "password": PASSWORD})]


async def test_the_library_list_says_which_copies_have_a_login_to_include(engine, client, data):
    await _running(engine, _project(_device("proj1", "Projector"), proj1=PASSWORD))
    saved = await client.post("/api/library", json={"id": "with_login", "name": "With", "description": ""})
    assert saved.status_code == 200, saved.text
    content = json.dumps(_project(_device("other1", "Display"), project_id="none").model_dump(mode="json"))
    library.import_project(content.encode(), "none.avc", "without_login")

    answer = await client.get("/api/library")

    flags = {p["id"]: p["has_saved_logins"] for p in answer.json()["projects"]}
    assert flags["with_login"] is True
    assert flags["without_login"] is False


# --- an import -------------------------------------------------------------------------


async def _export_with_logins(engine, client, passphrase: str = PASSPHRASE) -> bytes:
    await _running(
        engine, _project(_device("proj1", "Projector", api_key=API_KEY), _device("disp1", "Display"),
                         proj1=PASSWORD, disp1="Pw-Disp-2208"),
    )
    answer = await client.post("/api/project/export", json={"passphrase": passphrase})
    assert answer.status_code == 200, answer.text
    return answer.content


async def _on_a_fresh_system(engine, tmp_path, monkeypatch, name: str = "other") -> None:
    """The same server process, now running on another system's data folder
    (its own login file and key) with an empty project."""
    folder = tmp_path / name
    folder.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(folder))
    monkeypatch.setattr(library.config, "SAVED_PROJECTS_DIR", folder / "saved_projects")
    from openavc.core.device_logins import device_logins

    engine.device_logins = device_logins(folder)
    await _running(engine, _project(project_id="empty"))
    return folder


async def _import(client, content: bytes) -> httpx.Response:
    return await client.post("/api/library/import", files={"file": ("hall.zip", content, "application/zip")})


async def _open(client, library_id: str = "hall") -> httpx.Response:
    return await client.post(
        "/api/project/open-from-library",
        json={"library_id": library_id, "project_name": "Hall", "project_id": library_id},
    )


async def test_the_right_passphrase_brings_every_login_in(engine, client, tmp_path, monkeypatch):
    content = await _export_with_logins(engine, client)
    folder = await _on_a_fresh_system(engine, tmp_path, monkeypatch)

    imported = await _import(client, content)
    assert imported.status_code == 200, imported.text
    assert set(imported.json()["sealed_logins"]) == {"sealed_at"}

    added = await client.post("/api/library/hall/logins", json={"passphrase": PASSPHRASE})
    assert added.status_code == 200, added.text
    assert added.json() == {"status": "added", "devices": 2}

    opened = await _open(client)
    assert opened.status_code == 200, opened.text
    assert opened.json()["needs_login"] == []
    saved = DeviceLogins(folder)
    assert saved.login("proj1", DRIVER, "password") == PASSWORD
    assert saved.login("proj1", DRIVER, "api_key") == API_KEY
    assert saved.login("disp1", DRIVER, "password") == "Pw-Disp-2208"
    assert PASSWORD not in engine.project_path.read_text(encoding="utf-8")
    assert engine.resolved_device_config(engine.project.devices[0])["config"]["password"] == PASSWORD


async def test_a_wrong_passphrase_is_refused_and_can_be_tried_again(engine, client, tmp_path, monkeypatch):
    content = await _export_with_logins(engine, client)
    folder = await _on_a_fresh_system(engine, tmp_path, monkeypatch)
    await _import(client, content)

    wrong = await client.post("/api/library/hall/logins", json={"passphrase": "Green harbour 42"})

    assert wrong.status_code == 422
    assert wrong.json()["detail"] == "That passphrase does not open these logins."
    assert not (folder / "device_logins.json").exists()
    right = await client.post("/api/library/hall/logins", json={"passphrase": PASSPHRASE})
    assert right.status_code == 200, right.text


async def test_a_skipped_passphrase_leaves_the_devices_on_needs_login(engine, client, tmp_path, monkeypatch):
    content = await _export_with_logins(engine, client)
    await _on_a_fresh_system(engine, tmp_path, monkeypatch)
    await _import(client, content)

    skipped = await client.delete("/api/library/hall/logins")
    assert skipped.json() == {"status": "skipped"}
    late = await client.post("/api/library/hall/logins", json={"passphrase": PASSPHRASE})
    assert late.status_code == 404
    assert "Import the file again" in late.json()["detail"]

    opened = await _open(client)
    assert opened.json()["needs_login"] == [
        {"id": "proj1", "name": "Projector"}, {"id": "disp1", "name": "Display"},
    ]


async def test_this_systems_own_login_wins_over_the_imported_one(engine, client, tmp_path, monkeypatch):
    content = await _export_with_logins(engine, client)
    folder = await _on_a_fresh_system(engine, tmp_path, monkeypatch)
    await _running(engine, _project(_device("proj1", "Projector"), project_id="empty", proj1="Pw-Local-0001"))
    await _import(client, content)

    await client.post("/api/library/hall/logins", json={"passphrase": PASSPHRASE})
    opened = await _open(client)

    assert opened.json()["needs_login"] == []
    saved = DeviceLogins(folder)
    assert saved.login("proj1", DRIVER, "password") == "Pw-Local-0001"
    assert saved.login("proj1", DRIVER, "api_key") == API_KEY


async def test_an_import_to_the_library_keeps_them_for_when_the_copy_is_opened(
    engine, client, tmp_path, monkeypatch,
):
    content = await _export_with_logins(engine, client)
    folder = await _on_a_fresh_system(engine, tmp_path, monkeypatch)
    await _import(client, content)
    await client.post("/api/library/hall/logins", json={"passphrase": PASSPHRASE})

    saved = DeviceLogins(folder)
    assert saved.login("proj1", DRIVER, "password", library="hall") == PASSWORD
    assert saved.login("proj1", DRIVER, "password") is None


async def test_an_export_without_logins_asks_for_nothing_on_import(engine, client, tmp_path, monkeypatch):
    await _running(engine, _project(_device("proj1", "Projector"), proj1=PASSWORD))
    content = (await client.get("/api/project/export")).content
    await _on_a_fresh_system(engine, tmp_path, monkeypatch)

    imported = await _import(client, content)

    assert "sealed_logins" not in imported.json()
    assert (await _open(client)).json()["needs_login"] == [{"id": "proj1", "name": "Projector"}]


async def test_sealed_logins_this_version_cannot_read_are_left_out_with_a_warning(
    engine, client, tmp_path, monkeypatch,
):
    content = await _export_with_logins(engine, client)
    members = _members(content)
    bundle = json.loads(members[login_bundle.BUNDLE_MEMBER])
    bundle["version"] = 9
    members[login_bundle.BUNDLE_MEMBER] = json.dumps(bundle).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    await _on_a_fresh_system(engine, tmp_path, monkeypatch)

    imported = await _import(client, buf.getvalue())

    assert imported.status_code == 200, imported.text
    assert "sealed_logins" not in imported.json()
    assert any("newer version of OpenAVC" in w for w in imported.json()["warnings"])


async def test_deleting_the_copy_forgets_the_waiting_logins(engine, client, tmp_path, monkeypatch):
    content = await _export_with_logins(engine, client)
    await _on_a_fresh_system(engine, tmp_path, monkeypatch)
    await _import(client, content)

    await client.delete("/api/library/hall")

    assert login_bundle.held("hall") is None


# --- a project backup ------------------------------------------------------------------


async def test_a_backup_holds_its_devices_logins_sealed_with_this_systems_key(engine, data):
    await _running(engine, _project(_device("proj1", "Projector", api_key=API_KEY), proj1=PASSWORD))
    engine.device_logins.save("elsewhere1", "acme_other", {"password": "Pw-Not-In-Project"})

    path = create_backup(engine.project_path, "Manual backup")

    members = _members(path.read_bytes())
    assert ARCHIVE_LOGINS in members
    assert KEY_FILENAME not in members
    assert _readable_anywhere(members, PASSWORD, API_KEY, "Pw-Not-In-Project") == []
    key = (data / KEY_FILENAME).read_bytes().strip()
    from cryptography.fernet import Fernet

    sealed = json.loads(members[ARCHIVE_LOGINS])["sealed"]
    held = json.loads(Fernet(key).decrypt(sealed.encode()))["devices"]
    assert [(d["device"], d["fields"]) for d in held] == [
        ("proj1", {"api_key": API_KEY, "password": PASSWORD}),
    ]


async def test_a_backup_of_a_project_with_no_saved_login_holds_none(engine, data):
    await _running(engine, _project(_device("proj1", "Projector")))

    path = create_backup(engine.project_path, "Manual backup")

    assert ARCHIVE_LOGINS not in _members(path.read_bytes())
    assert not (data / KEY_FILENAME).exists()


async def test_restoring_a_backup_here_fills_in_a_login_this_system_no_longer_has(engine, client, data):
    await _running(engine, _project(_device("proj1", "Projector"), proj1=PASSWORD))
    filename = backup_id(engine.project_path.parent, create_backup(engine.project_path, "Earlier"))
    await _running(engine, _project())
    engine.device_logins._commit({})  # long gone: the sweep deleted it

    answer = await client.post(f"/api/backups/{filename}/restore")

    assert answer.status_code == 200, answer.text
    assert answer.json()["needs_login"] == []
    assert DeviceLogins(data).login("proj1", DRIVER, "password") == PASSWORD


async def test_restoring_a_backup_never_replaces_a_login_this_system_has(engine, client, data):
    await _running(engine, _project(_device("proj1", "Projector"), proj1=PASSWORD))
    filename = backup_id(engine.project_path.parent, create_backup(engine.project_path, "Earlier"))
    engine.device_logins.save("proj1", DRIVER, {"password": "Pw-Changed-Since"})

    await client.post(f"/api/backups/{filename}/restore")

    assert DeviceLogins(data).login("proj1", DRIVER, "password") == "Pw-Changed-Since"


async def test_a_backup_restored_on_another_system_fills_in_nothing(engine, client, tmp_path, monkeypatch):
    await _running(engine, _project(_device("proj1", "Projector"), proj1=PASSWORD))
    path = create_backup(engine.project_path, "Earlier")
    folder = await _on_a_fresh_system(engine, tmp_path, monkeypatch)
    engine.device_logins.save("unrelated1", DRIVER, {"password": "Pw-Other-System"})

    restore_from_backup(path, engine.project_path)
    await engine.reload_project()

    assert DeviceLogins(folder).login("proj1", DRIVER, "password") is None
    assert engine.logins_needed() == [{"id": "proj1", "name": "Projector"}]


# --- an update backup ------------------------------------------------------------------


async def test_an_update_backup_holds_the_login_file_and_never_its_key(engine, data):
    from openavc.updater.backup import create_backup as create_update_backup

    await _running(engine, _project(_device("proj1", "Projector"), proj1=PASSWORD))

    path = create_update_backup(data, "0.0.0", engine.project_path)

    members = _members(path.read_bytes())
    assert members["device_logins.json"] == (data / "device_logins.json").read_bytes()
    assert not [name for name in members if name.endswith(KEY_FILENAME)]
    assert _readable_anywhere(members, PASSWORD) == []


# --- the crash copy ----------------------------------------------------------------


async def test_the_crash_copy_does_not_keep_logins_the_first_start_moved(engine, data):
    from openavc.core.project_loader import crash_copy_path

    _write(engine.project_path, _project(_device("proj1", "Projector"), proj1=PASSWORD))
    engine.project_path.with_suffix(".avc.bak").write_text("{}", encoding="utf-8")

    await engine.start()
    try:
        assert PASSWORD not in engine.project_path.read_text(encoding="utf-8")
        assert PASSWORD not in crash_copy_path(engine.project_path).read_text(encoding="utf-8")
    finally:
        await engine.stop()


async def test_the_crash_copy_does_not_keep_logins_a_restored_backup_brought(engine, client, data):
    from openavc.core.project_loader import crash_copy_path

    await _running(engine, _project(_device("proj1", "Projector")))
    engine.device_logins.save("proj1", DRIVER, {"password": PASSWORD})
    before_move = engine.project_path.parent / "backups"
    before_move.mkdir(exist_ok=True)
    old = _project(_device("proj1", "Projector"), proj1="Pw-Old-From-Backup")
    with zipfile.ZipFile(before_move / "backup_20260101_000000_old.zip", "w") as zf:
        zf.writestr("project.avc", json.dumps(old.model_dump(mode="json")))
        zf.writestr("backup_meta.json", json.dumps({"reason": "Old"}))

    answer = await client.post("/api/backups/backups/backup_20260101_000000_old.zip/restore")

    assert answer.status_code == 200, answer.text
    assert "Pw-Old-From-Backup" not in engine.project_path.read_text(encoding="utf-8")
    assert "Pw-Old-From-Backup" not in crash_copy_path(engine.project_path).read_text(encoding="utf-8")
    assert DeviceLogins(data).login("proj1", DRIVER, "password") == PASSWORD
