"""A start whose project file is missing, or will not load, puts back the
newest backup of the project that was running, and never one of a project
that was closed.

The backups belong to the project file's folder, not to one project: after
another project is opened there, the newest backups before it hold the
project that was closed. So the start reads only the backups taken since the
project was last replaced (``backup_manager.backups_since_replaced``), each
backup saying what kind it is and which project it holds. With none of them
loading, a missing file starts the starter project and says so, since its
backups are still listed; a first start with no backups at all says nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from openavc.core import backup_manager, project_recovery
from openavc.core.backup_manager import (
    KIND_AUTO,
    KIND_CHANGE,
    KIND_MANUAL,
    KIND_REPLACE,
    backups_since_replaced,
    create_backup,
    list_backups,
)
from openavc.core.project_loader import ProjectConfig, ProjectMeta, save_project


@pytest.fixture
def starter(tmp_path, monkeypatch):
    """The bundled starter project the start seeds a missing file from."""
    seed = tmp_path / "bundle" / "project.avc"
    seed.parent.mkdir(parents=True)
    save_project(seed, ProjectConfig(project=ProjectMeta(id="starter", name="Starter")))
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: seed)
    return seed


@pytest.fixture
def no_starter(monkeypatch):
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "projects" / "default"
    path.mkdir(parents=True)
    return path


_FIRST = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
_stamps = iter(range(1, 10_000))


def _room(folder, name: str, uuid: str = "") -> None:
    """``name``'s project written as the project file."""
    room_id = name.lower().replace(" ", "_")
    save_project(folder / "project.avc", ProjectConfig(project=ProjectMeta(id=room_id, name=name, uuid=uuid)))


def _backup(folder, reason: str, kind: str = "") -> str:
    """A backup of what is at the project file now, a second newer than the
    last, so the list's order is the order they were taken."""
    path = create_backup(folder / "project.avc", reason, kind=kind)
    assert path is not None
    meta = backup_manager._read_zip_meta(path)
    meta["timestamp"] = (_FIRST + timedelta(seconds=next(_stamps))).isoformat()
    _rewrite_meta(path, meta)
    return backup_manager.backup_id(folder, path)


def _rewrite_meta(path, meta) -> None:
    import zipfile

    with zipfile.ZipFile(path) as source:
        members = {name: source.read(name) for name in source.namelist() if name != "backup_meta.json"}
    with zipfile.ZipFile(path, "w") as out:
        for name, data in members.items():
            out.writestr(name, data)
        out.writestr("backup_meta.json", json.dumps(meta))


def _opened_another(folder, name: str, uuid: str = "") -> str:
    """Another project opened from the library: the backup its door takes
    of the project it replaces, then the new project's file."""
    taken = _backup(folder, f"Before opening '{name}'", KIND_REPLACE)
    _room(folder, name, uuid)
    return taken


def _start(folder):
    return project_recovery.load_or_recover(folder / "project.avc")


# --- A missing project file -----------------------------------------------------


def test_a_missing_project_file_starts_on_its_newest_backup_not_the_starter(folder, starter):
    """The defect: the start seeded the starter before it looked at the
    backups, so a room whose file went missing came up on the starter with
    its own backup beside it, and said nothing."""
    _room(folder, "Hall")
    _backup(folder, "Auto-backup", KIND_AUTO)
    newest = _backup(folder, "Auto-backup", KIND_AUTO)
    (folder / "project.avc").unlink()

    assert _start(folder).project.name == "Hall"

    record = project_recovery.read(folder)
    assert record["outcome"] == project_recovery.RESTORED
    assert record["reason"] == project_recovery.MISSING
    assert record["backup"] == newest
    taken = next(b.timestamp for b in list_backups(folder / "project.avc") if b.filename == newest)
    assert record["cutoff"] == taken


def test_a_missing_file_never_starts_the_project_that_was_closed(folder, no_starter):
    """Room A, then Room B opened from the library, then B's file gone before
    B had a backup of its own: the newest backup is the one taken before the
    open, which holds Room A, the project the integrator closed."""
    _room(folder, "Room A")
    _backup(folder, "Auto-backup", KIND_AUTO)
    _opened_another(folder, "Room B")
    (folder / "project.avc").unlink()

    project = _start(folder)

    assert project.project.name != "Room A"
    assert project_recovery.read(folder)["reason"] == project_recovery.MISSING


def test_with_no_backup_of_its_own_a_missing_file_starts_the_starter_and_says_so(folder, starter):
    _room(folder, "Room A")
    _backup(folder, "Auto-backup", KIND_AUTO)
    _opened_another(folder, "Room B")
    (folder / "project.avc").unlink()

    assert _start(folder).project.name == "Starter"

    record = project_recovery.read(folder)
    assert record["outcome"] == project_recovery.STARTER
    assert record["reason"] == project_recovery.MISSING
    assert record["backup"] == ""


def test_a_missing_file_starts_on_the_backups_taken_since_another_project_was_opened(folder, starter):
    _room(folder, "Room A")
    _backup(folder, "Auto-backup", KIND_AUTO)
    _opened_another(folder, "Room B")
    since = _backup(folder, "Auto-backup", KIND_AUTO)
    (folder / "project.avc").unlink()

    assert _start(folder).project.name == "Room B"
    assert project_recovery.read(folder)["backup"] == since


@pytest.mark.parametrize("reason", [
    "Before opening 'Room B'",
    "Before importing 'Room B'",
    "Before creating blank project",
    "Before restore",
])
def test_a_backup_from_before_kinds_is_judged_by_the_reason_its_door_wrote(folder, starter, reason):
    _room(folder, "Room A")
    _backup(folder, "Auto-backup")
    _backup(folder, reason)
    _room(folder, "Room B")
    (folder / "project.avc").unlink()

    assert _start(folder).project.name == "Starter"
    assert project_recovery.read(folder)["outcome"] == project_recovery.STARTER


def test_a_backup_named_like_a_replace_by_a_person_is_still_the_project(folder, starter):
    """Create Backup takes the name a person types; its kind, not its
    name, says it was not taken before a replace."""
    _room(folder, "Hall")
    _backup(folder, "Before restore", KIND_MANUAL)
    (folder / "project.avc").unlink()

    assert _start(folder).project.name == "Hall"


def test_a_change_to_the_running_project_is_not_a_replace(folder, starter):
    _room(folder, "Hall")
    _backup(folder, "Before AI changes", KIND_CHANGE)
    _backup(folder, "Before cloud config push", KIND_CHANGE)
    (folder / "project.avc").unlink()

    assert _start(folder).project.name == "Hall"


def test_the_set_aside_of_a_start_cut_off_before_its_swap_hides_no_backup(folder, starter):
    """A start keeps what sits beside a missing file under Backups, then
    loses power before it puts anything in place. At the next start that
    backup, which holds no project file, is the newest; the project's own
    backup behind it is still the one started."""
    _room(folder, "Hall")
    _backup(folder, "Auto-backup", KIND_AUTO)
    (folder / "project.avc").unlink()
    (folder / "state.json").write_text(json.dumps({"var.level": 3}), encoding="utf-8")
    kept = backup_manager.set_aside(folder / "project.avc", project_recovery.SET_ASIDE_REASONS[project_recovery.MISSING])
    assert kept is not None
    assert list_backups(folder / "project.avc")[0].filename == backup_manager.backup_id(folder, kept)

    assert _start(folder).project.name == "Hall"


def test_a_first_start_with_no_backups_starts_the_starter_and_says_nothing(folder, starter):
    assert _start(folder).project.name == "Starter"

    assert project_recovery.read(folder) is None


def test_with_no_starter_and_no_backup_of_its_own_the_empty_project_says_so(folder, no_starter):
    _room(folder, "Room A")
    _backup(folder, "Auto-backup", KIND_AUTO)
    _opened_another(folder, "Room B")
    (folder / "project.avc").unlink()

    assert _start(folder).project.id == "recovery"

    record = project_recovery.read(folder)
    assert record["outcome"] == project_recovery.RESET
    assert record["reason"] == project_recovery.MISSING


# --- A project file that will not load -------------------------------------------


def test_an_unreadable_file_never_brings_back_the_project_that_was_closed(folder, starter):
    _room(folder, "Room A")
    _backup(folder, "Auto-backup", KIND_AUTO)
    _opened_another(folder, "Room B")
    (folder / "project.avc").write_text('{"project": {"id": "room_b"', encoding="utf-8")

    project = _start(folder)

    assert project.project.id == "recovery"
    record = project_recovery.read(folder)
    assert record["outcome"] == project_recovery.RESET
    assert record["reason"] == project_recovery.UNREADABLE


def test_an_unreadable_file_starts_on_its_own_newest_backup(folder, starter):
    _room(folder, "Room A")
    _opened_another(folder, "Room B")
    since = _backup(folder, "Auto-backup", KIND_AUTO)
    (folder / "project.avc").write_text('{"project": {"id": "room_b"', encoding="utf-8")

    assert _start(folder).project.name == "Room B"
    assert project_recovery.read(folder)["backup"] == since


# --- What a start that found none says it tried -----------------------------------


def _broken(folder) -> None:
    """The project file written so that it will not load, as a power cut
    part way through a save leaves it."""
    (folder / "project.avc").write_text('{"project": {"id": "hall"', encoding="utf-8")


def _published(folder) -> dict:
    from openavc.core.state_store import StateStore

    state = StateStore()
    project_recovery.publish(state, folder)
    return {key: state.get(key) for key in project_recovery.ALL_KEYS}


def test_an_unreadable_file_whose_backups_all_fail_names_each_one_it_tried(folder, no_starter):
    """The record named no backup, so the notice could not say which of the
    listed backups were the project's own copies that would not open."""
    _room(folder, "Hall")
    _broken(folder)
    tried = [_backup(folder, "Auto-backup", KIND_AUTO) for _ in range(3)]

    assert _start(folder).project.id == "recovery"

    record = project_recovery.read(folder)
    assert record["outcome"] == project_recovery.RESET
    assert record["backup"] == ""
    assert record["tried"] == tried[::-1]
    assert _published(folder)[project_recovery.KEY_TRIED] == "\n".join(tried[::-1])


def test_a_missing_file_whose_backups_all_fail_names_them_beside_the_starter(folder, starter):
    _room(folder, "Room A")
    _backup(folder, "Auto-backup", KIND_AUTO)
    _opened_another(folder, "Hall")
    _broken(folder)
    tried = [_backup(folder, "Auto-backup", KIND_AUTO) for _ in range(2)]
    (folder / "project.avc").unlink()

    assert _start(folder).project.name == "Starter"

    record = project_recovery.read(folder)
    assert record["outcome"] == project_recovery.STARTER
    assert record["tried"] == tried[::-1]


def test_a_start_with_only_the_closed_projects_backups_tried_none(folder, starter):
    _room(folder, "Room A")
    _backup(folder, "Auto-backup", KIND_AUTO)
    _opened_another(folder, "Room B")
    (folder / "project.avc").unlink()

    assert _start(folder).project.name == "Starter"

    assert project_recovery.read(folder)["tried"] == []
    assert _published(folder)[project_recovery.KEY_TRIED] == ""


def test_a_start_that_restores_a_backup_names_none_tried(folder, starter):
    """Only a start that found no backup to open names what it tried: the
    restored one is named, and its time is the line after which work is
    missing, whatever newer backup would not open."""
    _room(folder, "Hall")
    restored = _backup(folder, "Auto-backup", KIND_AUTO)
    _broken(folder)
    _backup(folder, "Auto-backup", KIND_AUTO)

    assert _start(folder).project.name == "Hall"

    record = project_recovery.read(folder)
    assert record["backup"] == restored
    assert record["tried"] == []
    assert _published(folder)[project_recovery.KEY_TRIED] == ""


@pytest.mark.parametrize("tried", [None, "backups/backup_a.zip", ["backups/backup_a.zip", 7], {"a": 1}])
def test_a_record_with_no_list_of_names_publishes_none_tried(folder, tried):
    """A record an earlier release wrote has no ``tried``; a damaged one is
    read as naming none rather than becoming a second fault."""
    data = {"outcome": project_recovery.RESET, "reason": project_recovery.UNREADABLE, "backup": "",
            "cutoff": "", "at": "2026-11-25T10:00:00+00:00"}
    if tried is not None:
        data["tried"] = tried
    (folder / project_recovery.MARKER_NAME).write_text(json.dumps(data), encoding="utf-8")

    published = _published(folder)

    assert published[project_recovery.KEY_STATE] == project_recovery.RESET
    assert published[project_recovery.KEY_TRIED] == ""


# --- Which backups are the running project's ---------------------------------------


def test_each_backup_says_which_project_it_holds(folder):
    _room(folder, "Hall", uuid="hall-stamp")
    _backup(folder, "Auto-backup", KIND_AUTO)

    [info] = list_backups(folder / "project.avc")
    assert (info.kind, info.project_uuid) == (KIND_AUTO, "hall-stamp")


def test_a_backup_of_another_project_ends_the_running_projects_backups(folder):
    """A project file put in place by hand, with no door to take a backup
    before it: the stamps the backups hold show where one project ends."""
    _room(folder, "Room A", uuid="a-stamp")
    _backup(folder, "Auto-backup", KIND_AUTO)
    _room(folder, "Room B", uuid="b-stamp")
    newest = _backup(folder, "Auto-backup", KIND_AUTO)

    assert [b.filename for b in backups_since_replaced(folder / "project.avc")] == [newest]


def test_a_project_stamped_part_way_through_its_backups_is_one_project(folder):
    """A project from before stamps gets its stamp at its first load since,
    so its older backups hold none: they are the same project."""
    _room(folder, "Hall")
    older = _backup(folder, "Auto-backup", KIND_AUTO)
    _room(folder, "Hall", uuid="hall-stamp")
    newer = _backup(folder, "Auto-backup", KIND_AUTO)

    assert [b.filename for b in backups_since_replaced(folder / "project.avc")] == [newer, older]


def test_a_legacy_pre_restore_copy_is_a_replace(folder):
    _room(folder, "Room A")
    (folder / "project.20240101.pre_restore_1.avc.bak").write_bytes((folder / "project.avc").read_bytes())

    assert backups_since_replaced(folder / "project.avc") == []
