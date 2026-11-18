"""One door for every change to the project while the engine runs.

An edit (``apply_project_edit``) and a bookkeeping save (a setting a device
reported, a plugin's own save, a pending setting that landed) are built the
same way: the change runs under the project lock against a copy of the
project current at that moment, and the copy is taken in as every change is
(the trim of typed fields, the installed-equipment check, a check that the
result is a valid project, the move of logins into the login file) before
the file is written. The running project is the one the file holds, and
every saved change makes the next timed backup due.

Each test runs the real engine on a temporary project.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from openavc.api.error_messages import describe_change_failure
from openavc.core.device_logins import SAVED_LOGIN, saved_isc_key
from openavc.core.engine import Engine
from openavc.core.project_changes import ProjectInvalidError
from openavc.core.project_loader import ProjectConfig, load_project, save_project
from openavc.core.project_migration import CURRENT_VERSION
from openavc.utils.log_redaction import get_secret_registry


def _project_dict() -> dict:
    return {
        "openavc_version": CURRENT_VERSION,
        "project": {"id": "hall", "name": "Hall"},
        "variables": [{"id": "v1", "type": "number", "default": 1}],
        "macros": [{"id": "warm", "name": "Warm", "steps": [],
                    "triggers": [{"id": "t1", "type": "startup"}]}],
        "devices": [{"id": "d1", "name": "Widget", "driver": "acme_widget", "config": {}}],
        "connections": {"d1": {"host": "10.0.0.1", "port": 23}},
        "plugins": {"plug1": {"enabled": False, "config": {"x": 1}}},
    }


@pytest.fixture(autouse=True)
def _forget_secrets():
    yield
    get_secret_registry().clear()


@pytest.fixture
def engine(tmp_path, monkeypatch) -> Engine:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(data))
    eng = Engine(str(tmp_path / "project.avc"))
    eng.project = ProjectConfig(**_project_dict())
    save_project(eng.project_path, eng.project)
    monkeypatch.setattr(eng, "_reconcile", AsyncMock())
    eng.broadcast_ws = AsyncMock()
    eng._running = True
    eng._dirty_since_backup = False
    return eng


def _disk(engine: Engine) -> dict:
    return json.loads(engine.project_path.read_text(encoding="utf-8"))


def _write_fails(engine: Engine, monkeypatch) -> None:
    async def no_room(path, project):
        raise OSError("disk full")

    monkeypatch.setattr("openavc.core.project_changes.save_project_async", no_room)


def _rename(project) -> None:
    project.project.name = "Renamed"


def _no_name(project) -> None:
    # A file with no project name does not load.
    project.project.name = None


def _delay_below_zero(project) -> None:
    # A save refuses a value past a bound (a load repairs one).
    project.macros[0].triggers[0].delay_seconds = -5


INVALID = [
    pytest.param(_no_name, "project.name", id="no-name"),
    pytest.param(_delay_below_zero, "macros.0.triggers.0.delay_seconds", id="past-a-bound"),
]


async def _bookkeeping(engine: Engine, kind: str, mutate, **callbacks) -> None:
    """Save ``mutate`` as a bookkeeping change, now or queued."""
    if kind == "now":
        await engine.persist_bookkeeping_change(mutate, **callbacks)
        return
    engine.schedule_bookkeeping_change(mutate, **callbacks)
    await engine._bookkeeping_task


BOOKKEEPING = ["now", "queued"]


# --- A check that the result is a valid project -----------------------------


@pytest.mark.parametrize("mutate,field", INVALID)
async def test_an_edit_whose_result_is_not_a_valid_project_is_refused(engine, mutate, field):
    before = engine.project_path.read_bytes()
    revision = engine._project_revision

    with pytest.raises(ProjectInvalidError) as caught:
        await engine.apply_project_edit(mutate)

    assert engine.project_path.read_bytes() == before
    assert load_project(engine.project_path).project.name == "Hall"
    assert engine.project.project.name == "Hall"
    assert engine.project.macros[0].triggers[0].delay_seconds == 0
    assert engine._project_revision == revision
    assert engine._dirty_since_backup is False
    failure = describe_change_failure(caught.value)
    assert (failure.status, failure.code) == (422, "refused")
    assert failure.sentence.startswith(f"Invalid project configuration:\n{field}: ")


@pytest.mark.parametrize("mutate,field", INVALID)
async def test_a_bookkeeping_change_whose_result_is_not_a_valid_project_is_refused(
    engine, mutate, field,
):
    before = engine.project_path.read_bytes()
    undone = []

    with pytest.raises(ProjectInvalidError):
        await engine.persist_bookkeeping_change(mutate, on_error=lambda: undone.append(1))

    assert undone == [1]
    assert engine.project_path.read_bytes() == before
    assert engine.project.project.name == "Hall"
    assert engine._revision_counter == 0


async def test_a_refused_change_in_a_queued_batch_saves_none_of_it(engine):
    """Changes queued together are saved together: one whose result is not
    a valid project refuses the batch, and each is undone."""
    undone = []
    engine.schedule_bookkeeping_change(_rename, on_error=lambda: undone.append("rename"))
    engine.schedule_bookkeeping_change(_no_name, on_error=lambda: undone.append("no name"))
    await engine._bookkeeping_task

    assert undone == ["no name", "rename"]
    assert _disk(engine)["project"]["name"] == "Hall"
    assert engine.project.project.name == "Hall"


async def test_a_refused_queued_change_names_the_field_in_the_log_and_never_the_value(
    engine, caplog,
):
    """A queued change has no door to answer it, so the log line is all
    there is: it names the field, never what was in it."""
    def bad_name(project):
        project.project.name = ["Acme-Private-7731"]

    with caplog.at_level(logging.ERROR):
        engine.schedule_bookkeeping_change(bad_name)
        await engine._bookkeeping_task

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "project.name: Input should be a valid string" in logged
    assert "Acme-Private-7731" not in logged


def test_the_refusal_is_answered_by_every_route():
    from openavc.api.errors import project_change_failures

    assert ProjectInvalidError in project_change_failures()


# --- The running project is the one the file holds --------------------------


@pytest.mark.parametrize("kind", BOOKKEEPING)
async def test_a_bookkeeping_save_that_fails_leaves_the_running_project_as_the_file_holds_it(
    engine, monkeypatch, kind,
):
    _write_fails(engine, monkeypatch)

    if kind == "now":
        with pytest.raises(OSError):
            await _bookkeeping(engine, kind, _rename)
    else:
        await _bookkeeping(engine, kind, _rename)

    assert engine.project.project.name == "Hall"
    assert _disk(engine)["project"]["name"] == "Hall"


async def test_a_plugin_save_while_a_bookkeeping_save_is_written_is_kept(engine, monkeypatch):
    """A plugin's save that arrives while the file is being written goes
    onto the running project in place and is queued; it lands on the
    project the write keeps, in memory and then on disk."""
    from openavc.core import project_changes

    writing, release = asyncio.Event(), asyncio.Event()
    write = project_changes.save_project_async
    held = []

    async def slow(path, project):
        if not held:
            held.append(1)
            writing.set()
            await release.wait()
        await write(path, project)

    monkeypatch.setattr(project_changes, "save_project_async", slow)
    saving = asyncio.create_task(engine.persist_bookkeeping_change(_rename))
    await writing.wait()
    await engine._save_plugin_config("plug1", {"x": 2})
    release.set()
    await saving
    await engine._bookkeeping_task

    assert engine.project.plugins["plug1"].config == {"x": 2}
    assert engine.project.project.name == "Renamed"
    assert _disk(engine)["plugins"]["plug1"]["config"] == {"x": 2}
    assert _disk(engine)["project"]["name"] == "Renamed"


# --- Every change makes the next backup due ---------------------------------


@pytest.mark.parametrize("kind", BOOKKEEPING)
async def test_a_bookkeeping_save_makes_the_next_backup_due_once_the_file_holds_it(
    engine, monkeypatch, kind,
):
    """The timed backup copies the file without the project lock, so the
    change is marked only once it is written."""
    from openavc.core import project_changes

    seen = []
    write = project_changes.save_project_async

    async def look(path, project):
        seen.append(engine._dirty_since_backup)
        await write(path, project)

    monkeypatch.setattr(project_changes, "save_project_async", look)
    await _bookkeeping(engine, kind, _rename)

    assert seen == [False]
    assert engine._dirty_since_backup is True
    assert _disk(engine)["project"]["name"] == "Renamed"


@pytest.mark.parametrize("kind", BOOKKEEPING)
async def test_a_bookkeeping_save_that_fails_leaves_the_backup_as_it_was(engine, monkeypatch, kind):
    _write_fails(engine, monkeypatch)

    try:
        await _bookkeeping(engine, kind, _rename)
    except OSError:
        pass

    assert engine._dirty_since_backup is False


# --- A bookkeeping change is taken in as an edit is -------------------------


@pytest.mark.parametrize("kind", BOOKKEEPING)
async def test_a_bookkeeping_change_has_its_typed_values_trimmed(engine, kind):
    def padded(project):
        project.connections["d1"]["host"] = " 10.0.0.2 "

    await _bookkeeping(engine, kind, padded)

    assert _disk(engine)["connections"]["d1"]["host"] == "10.0.0.2"
    assert engine.project.connections["d1"]["host"] == "10.0.0.2"


@pytest.mark.parametrize("kind", BOOKKEEPING)
async def test_a_login_a_bookkeeping_change_holds_moves_into_the_login_file(engine, kind):
    def keyed(project):
        project.isc.auth_key = "Mesh-Key-123"

    await _bookkeeping(engine, kind, keyed)

    assert _disk(engine)["isc"]["auth_key"] == SAVED_LOGIN
    assert engine.project.isc.auth_key == SAVED_LOGIN
    assert saved_isc_key(engine.project, engine.device_logins) == "Mesh-Key-123"


async def test_a_login_filed_by_a_bookkeeping_save_that_failed_is_taken_back(engine, monkeypatch):
    def keyed(project):
        project.isc.auth_key = "Mesh-Key-123"

    _write_fails(engine, monkeypatch)
    with pytest.raises(OSError):
        await engine.persist_bookkeeping_change(keyed)

    marked = engine.project.model_copy(deep=True)
    marked.isc.auth_key = SAVED_LOGIN
    assert saved_isc_key(marked, engine.device_logins) == ""
    assert engine.project.isc.auth_key == ""


@pytest.fixture
def product(tmp_path, monkeypatch) -> Engine:
    """An engine on a product whose own hardware is a device in the project."""
    from openavc.drivers import registry

    manifest = tmp_path / "host.json"
    manifest.write_text(json.dumps({
        "device_id": "local", "identity": "this-unit", "integration": "acme_amplifier_v2",
        "name": "Built-in amplifier", "connection": {"token": "host-owned"},
    }))
    monkeypatch.setenv("OPENAVC_EQUIPMENT_MANIFEST", str(manifest))
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(data))
    eng = Engine(str(tmp_path / "project.avc"))
    source = ProjectConfig.model_validate({"project": {"id": "hall", "name": "Hall"}})
    eng.project = eng.installed_equipment.compose(source, for_load=True)
    assert [eng.installed_equipment.matches(d) for d in eng.project.devices] == [True]
    save_project(eng.project_path, eng.project)
    eng.broadcast_ws = AsyncMock()
    eng._running = True
    return eng


@pytest.mark.parametrize("kind", BOOKKEEPING)
async def test_a_bookkeeping_change_cannot_remove_the_products_own_hardware(product, kind):
    from openavc.core.installed_equipment import InstalledEquipmentError

    before = product.project_path.read_bytes()
    devices = [d.id for d in product.project.devices]
    undone = []

    def remove(project):
        project.devices.clear()

    if kind == "now":
        with pytest.raises(InstalledEquipmentError):
            await _bookkeeping(product, kind, remove, on_error=lambda: undone.append(1))
    else:
        await _bookkeeping(product, kind, remove, on_error=lambda: undone.append(1))

    assert undone == [1]
    assert product.project_path.read_bytes() == before
    assert [d.id for d in product.project.devices] == devices
