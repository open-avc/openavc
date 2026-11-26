"""Tests for Engine project-load recovery paths."""

import json

from openavc.core import project_recovery, project_update
from openavc.core.backup_manager import create_backup, list_backups
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, ProjectMeta, save_project


def test_recovery_creates_parent_directory(tmp_path, monkeypatch):
    """Regression for A6: when OPENAVC_PROJECT points at a path whose
    parent directory does not yet exist (fresh dev checkout, custom
    project path with no projects/ dir), `_load_project_safe` must
    mkdir the parent before save_project tries to write a tempfile and
    .avc.bak sibling there. Otherwise startup crashes with
    FileNotFoundError and the splash sticks on 'Startup failed'.

    Seeding is disabled here so we exercise the empty-recovery path
    specifically (a real checkout ships a canonical seed that would
    otherwise take over the missing-file branch — see the seeding tests).
    """
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    missing_parent = tmp_path / "missing" / "nested" / "tree"
    project_path = missing_parent / "project.avc"
    assert not missing_parent.exists(), "precondition: parent must not exist"

    eng = Engine(str(project_path))
    project = eng._load_project_safe()

    assert project is not None
    assert project.project.id == "recovery"
    assert missing_parent.is_dir(), "recovery should have created the parent dir"
    assert project_path.exists(), "recovery should have written the empty project"

    # The written project should round-trip through the loader.
    saved = json.loads(project_path.read_text(encoding="utf-8"))
    assert saved["project"]["id"] == "recovery"


def test_recovery_existing_parent_dir_still_works(tmp_path, monkeypatch):
    """Sanity check: the mkdir call must be idempotent for the common
    case where the parent dir already exists.
    """
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "project.avc"  # tmp_path already exists
    eng = Engine(str(project_path))
    project = eng._load_project_safe()

    assert project is not None
    assert project.project.id == "recovery"
    assert project_path.exists()


def _write_seed(path):
    """Write a minimal, loader-valid seed project to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    save_project(
        path,
        ProjectConfig(project=ProjectMeta(id="seeded_starter", name="Seeded Starter")),
    )


def test_missing_project_seeds_from_canonical_seed(tmp_path, monkeypatch):
    """When the configured project is missing, `_load_project_safe` seeds it
    from the canonical bundled seed instead of creating an empty Recovery
    Project. This is what makes default-project seeding independent of how the
    data dir was provided — notably a bind-mounted Docker /data, which shadows
    the seed cp'd into the image layer.
    """
    seed_path = tmp_path / "bundle" / "seed" / "default" / "project.avc"
    _write_seed(seed_path)
    monkeypatch.setattr(
        "openavc.system_config.get_seed_project_path", lambda: seed_path
    )

    # Missing project AND missing parent dir, mimicking a fresh bind mount.
    project_path = tmp_path / "data" / "projects" / "default" / "project.avc"
    assert not project_path.parent.exists(), "precondition: parent must not exist"

    eng = Engine(str(project_path))
    project = eng._load_project_safe()

    assert project.project.id == "seeded_starter", "should load the seed, not recovery"
    assert project_path.exists(), "seed should have been copied into place"
    seeded = json.loads(project_path.read_text(encoding="utf-8"))
    assert seeded["project"]["id"] == "seeded_starter"


def test_missing_project_no_seed_falls_back_to_recovery(tmp_path, monkeypatch):
    """With no canonical seed available, a missing project still yields the
    empty Recovery Project — the seed is a net, not a hard dependency.
    """
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "projects" / "default" / "project.avc"

    eng = Engine(str(project_path))
    project = eng._load_project_safe()

    assert project.project.id == "recovery"
    assert project_path.exists()


def test_corrupt_project_does_not_seed(tmp_path, monkeypatch):
    """A corrupt (present but unparseable) project must NOT be silently
    overwritten by the seed — it routes through backup restore and, with no
    backups, the empty Recovery Project (whose description signals corruption).
    The seed only rescues a genuinely *missing* file.
    """
    seed_path = tmp_path / "bundle" / "seed" / "default" / "project.avc"
    _write_seed(seed_path)
    monkeypatch.setattr(
        "openavc.system_config.get_seed_project_path", lambda: seed_path
    )

    project_path = tmp_path / "projects" / "default" / "project.avc"
    project_path.parent.mkdir(parents=True)
    project_path.write_text("{ this is not valid json", encoding="utf-8")

    eng = Engine(str(project_path))
    project = eng._load_project_safe()

    assert project.project.id == "recovery", "corruption must not seed the starter"


# --- The notice a recovery leaves behind ---
#
# The recovery itself was already right. What none of it did was tell the
# person whose last edit fell between the backup and the corrupt save, and
# these pin the telling: the record, what it says, that it outlives the boot,
# and that only a person clears it.


def _real_project(path, name="Live Project"):
    """Write a loader-valid project at ``path`` and back it up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    save_project(path, ProjectConfig(project=ProjectMeta(id="live", name=name)))


def _corrupt(path):
    path.write_text('{"project": {"id": "live"', encoding="utf-8")


def test_restore_from_backup_records_the_backup_and_the_cutoff(tmp_path):
    """A project that would not parse, replaced from a backup, leaves a record
    naming which backup and what it dates from. The cut-off is the whole point:
    it is the line after which the integrator's own changes may be gone."""
    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    backup = create_backup(project_path, "Auto backup")
    assert backup is not None, "precondition: a backup to restore from"
    _corrupt(project_path)

    eng = Engine(str(project_path))
    project = eng._load_project_safe()
    assert project.project.id == "live", "precondition: the backup restored"

    record = project_recovery.read(project_path.parent)
    assert record is not None, "a recovery must leave a record"
    assert record["outcome"] == project_recovery.RESTORED
    assert record["reason"] == project_recovery.UNREADABLE
    assert record["backup"] == f"backups/{backup.name}"
    assert record["cutoff"], "the record must say what the restored project dates from"
    assert record["at"], "and when the recovery happened"

    project_recovery.publish(eng.state, project_path.parent)
    assert eng.state.get(project_recovery.KEY_STATE) == project_recovery.RESTORED
    assert eng.state.get(project_recovery.KEY_BACKUP) == f"backups/{backup.name}"
    assert eng.state.get(project_recovery.KEY_CUTOFF) == record["cutoff"]
    assert eng.state.get(project_recovery.KEY_REASON) == project_recovery.UNREADABLE


def test_no_backup_loads_records_the_reset(tmp_path, monkeypatch):
    """Nothing to restore is the loud case: the room is running an empty
    project, and the saved one is still unreadable on disk."""
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "projects" / "default" / "project.avc"
    project_path.parent.mkdir(parents=True)
    _corrupt(project_path)

    eng = Engine(str(project_path))
    assert eng._load_project_safe().project.id == "recovery"

    record = project_recovery.read(project_path.parent)
    assert record is not None
    assert record["outcome"] == project_recovery.RESET
    assert record["reason"] == project_recovery.UNREADABLE
    assert record["backup"] == "", "there was no backup to name"


def test_missing_project_with_a_backup_says_it_was_missing(tmp_path, monkeypatch):
    """A project file that is gone rather than damaged still costs whatever
    was saved after the backup, so it is reported — with the reason it was."""
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    assert create_backup(project_path, "Auto backup") is not None
    project_path.unlink()

    eng = Engine(str(project_path))
    assert eng._load_project_safe().project.id == "live"

    record = project_recovery.read(project_path.parent)
    assert record is not None
    assert record["outcome"] == project_recovery.RESTORED
    assert record["reason"] == project_recovery.MISSING


def test_first_boot_with_nothing_to_recover_says_nothing(tmp_path, monkeypatch):
    """A missing project with no backup and no seed is a fresh install. There
    is no lost edit to warn about, and a data-loss notice on a brand new system
    would be false."""
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "projects" / "default" / "project.avc"

    eng = Engine(str(project_path))
    assert eng._load_project_safe().project.id == "recovery"

    assert project_recovery.read(project_path.parent) is None
    project_recovery.publish(eng.state, project_path.parent)
    assert eng.state.get(project_recovery.KEY_STATE) == project_recovery.NONE


def test_a_clean_load_leaves_no_record_but_declares_the_keys(tmp_path):
    """Guards the guard: an ordinary boot must not raise a notice, and must
    still publish every key so a panel bound to one draws a blank rather than
    reading a key that does not exist."""
    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)

    eng = Engine(str(project_path))
    assert eng._load_project_safe().project.id == "live"
    assert project_recovery.read(project_path.parent) is None

    project_recovery.publish(eng.state, project_path.parent)
    for key in project_recovery.ALL_KEYS:
        assert eng.state.get(key) == "", key
        assert eng.state.has(key), key


def test_the_record_outlives_the_boot_that_wrote_it(tmp_path):
    """The failure this fixes is a notice nobody saw. A box that reboots
    nightly must still be carrying the notice in the morning."""
    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    assert create_backup(project_path, "Auto backup") is not None
    _corrupt(project_path)
    Engine(str(project_path))._load_project_safe()

    # A second, entirely clean boot.
    second = Engine(str(project_path))
    assert second._load_project_safe().project.id == "live"
    project_recovery.publish(second.state, project_path.parent)
    assert second.state.get(project_recovery.KEY_STATE) == project_recovery.RESTORED


def test_dismissing_is_what_clears_it(tmp_path):
    """And only dismissing does. The second call reports there was nothing
    left to clear, so a double-click cannot report a notice that was there."""
    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    assert create_backup(project_path, "Auto backup") is not None
    _corrupt(project_path)
    eng = Engine(str(project_path))
    eng._load_project_safe()
    project_recovery.publish(eng.state, project_path.parent)

    assert project_recovery.dismiss(eng.state, project_path.parent) is True
    assert project_recovery.read(project_path.parent) is None
    assert eng.state.get(project_recovery.KEY_STATE) == project_recovery.NONE
    assert eng.state.get(project_recovery.KEY_BACKUP) == ""

    assert project_recovery.dismiss(eng.state, project_path.parent) is False

    # And it stays cleared across the next boot.
    third = Engine(str(project_path))
    third._load_project_safe()
    project_recovery.publish(third.state, project_path.parent)
    assert third.state.get(project_recovery.KEY_STATE) == project_recovery.NONE


def test_a_damaged_record_is_no_record(tmp_path):
    """A notice that cannot be read must not become a second fault at boot."""
    project_dir = tmp_path / "projects" / "default"
    project_dir.mkdir(parents=True)
    (project_dir / project_recovery.MARKER_NAME).write_text("{ not json", encoding="utf-8")

    eng = Engine(str(project_dir / "project.avc"))
    assert project_recovery.read(project_dir) is None
    project_recovery.publish(eng.state, project_dir)
    assert eng.state.get(project_recovery.KEY_STATE) == project_recovery.NONE

    # An outcome nobody defined reads the same way.
    (project_dir / project_recovery.MARKER_NAME).write_text(
        json.dumps({"outcome": "something_else"}), encoding="utf-8"
    )
    assert project_recovery.read(project_dir) is None


def test_the_dismiss_endpoint_clears_the_notice(tmp_path):
    """The Dashboard's dismiss button, through the door it actually calls."""
    from fastapi.testclient import TestClient

    from openavc.api import rest
    from openavc.main import app

    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    assert create_backup(project_path, "Auto backup") is not None
    _corrupt(project_path)
    eng = Engine(str(project_path))
    eng.project = eng._load_project_safe()
    project_recovery.publish(eng.state, project_path.parent)

    rest.set_engine(eng)
    try:
        client = TestClient(app)
        before = client.get("/api/state").json()["state"]
        assert before[project_recovery.KEY_STATE] == project_recovery.RESTORED

        resp = client.post("/api/project/recovery/dismiss")
        assert resp.status_code == 200
        assert resp.json() == {"status": "dismissed", "cleared": True}

        after = client.get("/api/state").json()["state"]
        assert after[project_recovery.KEY_STATE] == project_recovery.NONE
        assert client.post("/api/project/recovery/dismiss").json()["cleared"] is False
    finally:
        rest.set_engine(None)


# --- A project file with its own name ---


def test_a_project_with_its_own_name_boots_from_its_backup(tmp_path):
    """The whole recovery used to assume ``project.avc``: nothing was ever
    backed up for any other name, so a corrupt ``main-hall.avc`` came up as an
    empty Recovery Project however many saves had gone before."""
    project_path = tmp_path / "projects" / "hall" / "main-hall.avc"
    _real_project(project_path)
    backup = create_backup(project_path, "Auto backup")
    assert backup is not None, "precondition: a backup to restore from"
    _corrupt(project_path)

    project = Engine(str(project_path))._load_project_safe()

    assert project.project.id == "live"
    record = project_recovery.read(project_path.parent)
    assert record["outcome"] == project_recovery.RESTORED
    assert record["backup"] == f"backups/{backup.name}"
    assert not (project_path.parent / "project.avc").exists()


def test_a_project_with_its_own_name_is_never_recovered_into_another_file(tmp_path, monkeypatch):
    """With no backup, recovery used to take the file's crash copy for a legacy
    backup, write it to ``project.avc`` beside it and reload the still-corrupt
    real file. Nothing may write a file the server does not load."""
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "main-hall.avc"
    _real_project(project_path, name="First")
    _real_project(project_path, name="Second")  # leaves the crash copy behind
    _corrupt(project_path)

    project = Engine(str(project_path))._load_project_safe()

    assert project.project.id == "recovery"
    assert not (tmp_path / "project.avc").exists()


# --- What a start's recovery replaces, and what it keeps ---
#
# The starter, a backup and the Recovery Project each replace the project the
# way a running replace does: everything the project is made of, so nothing
# the previous project left beside its file carries into the one that starts.
# What they replace is kept under Backups first, the file that would not load
# included, so nothing is lost that no backup holds.


def _leave_files_beside(folder, values):
    """A previous project's script, image, saved values and notice beside
    ``folder``'s project file."""
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "scripts" / "old.py").write_bytes(b"# the old project's script\n")
    (folder / "assets").mkdir(exist_ok=True)
    (folder / "assets" / "logo.png").write_bytes(b"PNG-old")
    (folder / "state.json").write_text(json.dumps(values), encoding="utf-8")
    project_recovery.record(folder, outcome=project_recovery.RESTORED, reason=project_recovery.UNREADABLE,
                            backup="backups/backup_old.zip")
    notes = project_update.MigrationNotes()
    notes.changed("The old project's repair.", "Panel Settings")
    project_update.record(folder, notes)


def _beside(folder):
    """What sits beside the project file that a previous project could have
    left there."""
    return sorted(
        str(p.relative_to(folder)) for p in folder.rglob("*")
        if p.is_file() and p.parts[len(folder.parts)] in {"scripts", "assets", "ui", "themes", "state.json", "recovery.json"}
    )


def _backups_holding(folder, member):
    """Each backup under ``folder``'s Backups that holds ``member``: its
    filename and that member's bytes."""
    import zipfile

    held = {}
    for path in sorted((folder / "backups").glob("backup_*.zip")):
        with zipfile.ZipFile(path) as archive:
            if member in archive.namelist():
                held[path.name] = archive.read(member)
    return held


def _bad_backup(folder, values):
    """A backup under Backups whose project file is not a project, beside a
    script and saved values of its own."""
    import zipfile

    (folder / "backups").mkdir(parents=True, exist_ok=True)
    path = folder / "backups" / "backup_20990101_000000_auto_backup.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project.avc", '{"project": {"id": "bad"')
        archive.writestr("backup_meta.json", json.dumps({"reason": "Auto backup", "timestamp": "2099-01-01T00:00:00+00:00"}))
        archive.writestr("scripts/bad.py", "# the bad backup's script\n")
        archive.writestr("state.json", json.dumps(values))
    return path


def test_a_seeded_start_brings_none_of_the_files_left_beside_a_missing_project(tmp_path, monkeypatch):
    seed_path = tmp_path / "bundle" / "project.avc"
    _write_seed(seed_path)
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: seed_path)
    folder = tmp_path / "projects" / "default"
    _leave_files_beside(folder, {"var.old": 5})

    project = project_recovery.load_or_recover(folder / "project.avc")

    assert project.project.id == "seeded_starter"
    assert _beside(folder) == []
    assert project_recovery.read(folder) is None
    assert project_update.read(folder) is None
    # Nothing it moved away is lost: a backup holds the old files.
    assert list(_backups_holding(folder, "scripts/old.py").values()) == [b"# the old project's script\n"]
    assert list(_backups_holding(folder, "state.json").values()) == [json.dumps({"var.old": 5}).encode()]


def test_a_first_start_with_nothing_beside_the_project_takes_no_backup(tmp_path, monkeypatch):
    seed_path = tmp_path / "bundle" / "project.avc"
    _write_seed(seed_path)
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: seed_path)
    folder = tmp_path / "projects" / "default"

    assert project_recovery.load_or_recover(folder / "project.avc").project.id == "seeded_starter"

    assert list((folder / "backups").glob("*.zip")) == []


def test_a_start_that_restores_a_backup_keeps_the_file_that_would_not_load(tmp_path):
    project_path = tmp_path / "projects" / "default" / "project.avc"
    folder = project_path.parent
    _real_project(project_path)
    restored = create_backup(project_path, "Auto backup")
    _corrupt(project_path)
    unreadable = project_path.read_bytes()
    (folder / "state.json").write_text(json.dumps({"var.newer": 9}), encoding="utf-8")

    project = project_recovery.load_or_recover(project_path)

    assert project.project.id == "live"
    assert project_recovery.read(folder)["backup"] == f"backups/{restored.name}"
    kept = _backups_holding(folder, "project.avc")
    kept.pop(restored.name)
    assert list(kept.values()) == [unreadable]
    assert list(_backups_holding(folder, "state.json").values()) == [json.dumps({"var.newer": 9}).encode()]


def test_a_start_on_the_recovery_project_keeps_the_file_that_would_not_load_and_none_beside_it(tmp_path, monkeypatch):
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "projects" / "default" / "project.avc"
    folder = project_path.parent
    _leave_files_beside(folder, {"var.old": 5})
    _corrupt(project_path)
    unreadable = project_path.read_bytes()

    project = project_recovery.load_or_recover(project_path)

    assert project.project.id == "recovery"
    assert _beside(folder) == ["recovery.json"]
    assert project_recovery.read(folder)["outcome"] == project_recovery.RESET
    assert project_update.read(folder) is None
    assert list(_backups_holding(folder, "project.avc").values()) == [unreadable]
    assert list(_backups_holding(folder, "scripts/old.py").values()) == [b"# the old project's script\n"]


def test_a_backup_that_is_not_a_project_is_never_written_over_the_live_files(tmp_path, monkeypatch):
    """The Recovery Project starts on none of a tried backup's files, and the
    saved values that were live are kept under Backups with the file that
    would not load."""
    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "projects" / "default" / "project.avc"
    folder = project_path.parent
    folder.mkdir(parents=True)
    _corrupt(project_path)
    live = json.dumps({"var.level": 3}).encode()
    (folder / "state.json").write_bytes(live)
    bad = _bad_backup(folder, {"var.level": 1})

    project = project_recovery.load_or_recover(project_path)

    assert project.project.id == "recovery"
    assert not (folder / "state.json").exists()
    assert not (folder / "scripts" / "bad.py").exists()
    kept = _backups_holding(folder, "state.json")
    kept.pop(bad.name)
    assert list(kept.values()) == [live]


def test_the_backup_a_start_restores_outlives_the_backup_it_takes_first(tmp_path):
    """A full list whose only backup that loads is the oldest: the backup of
    the file that would not load must not rotate it out before it is read."""
    import os

    from openavc.core.backup_manager import MAX_BACKUPS

    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    oldest = create_backup(project_path, "Auto backup")
    _corrupt(project_path)
    newer = [create_backup(project_path, f"Auto backup {n}") for n in range(MAX_BACKUPS - 1)]
    for age, path in enumerate([oldest, *newer]):
        os.utime(path, (1_000_000 + age, 1_000_000 + age))

    project = project_recovery.load_or_recover(project_path)

    assert project.project.id == "live"
    assert project_recovery.read(project_path.parent)["backup"] == f"backups/{oldest.name}"


def test_a_start_whose_backup_of_the_unreadable_file_cannot_be_saved_still_comes_up(tmp_path, monkeypatch):
    from openavc.core import backup_manager

    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    restored = create_backup(project_path, "Auto backup")
    _corrupt(project_path)

    def full_disk(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup_manager, "set_aside", full_disk)

    assert project_recovery.load_or_recover(project_path).project.id == "live"
    assert project_recovery.read(project_path.parent)["backup"] == f"backups/{restored.name}"


def test_a_folder_that_cannot_be_moved_still_lets_the_recovery_project_start(tmp_path, monkeypatch):
    """A script folder held open (Windows refuses the rename) must not stop
    the last resort: the Recovery Project's file is written on its own, and
    the backup taken first still holds what was there."""
    from openavc.core import project_swap

    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "projects" / "default" / "project.avc"
    folder = project_path.parent
    _leave_files_beside(folder, {"var.old": 5})
    _corrupt(project_path)
    unreadable = project_path.read_bytes()
    rename = project_swap._rename_on_disk

    def scripts_held_open(src, dst):
        if src == folder / "scripts":
            raise PermissionError(13, "The file is in use")
        rename(src, dst)

    monkeypatch.setattr(project_swap, "_rename_on_disk", scripts_held_open)

    project = project_recovery.load_or_recover(project_path)

    assert project.project.id == "recovery"
    assert json.loads(project_path.read_text(encoding="utf-8"))["project"]["id"] == "recovery"
    assert project_recovery.read(folder)["outcome"] == project_recovery.RESET
    assert list(_backups_holding(folder, "project.avc").values()) == [unreadable]


def _unread_update_notice(folder):
    notes = project_update.MigrationNotes()
    notes.changed("Kept until somebody reads it.", "Panel Settings")
    project_update.record(folder, notes)


def _notice_messages(folder):
    return [n["message"] for n in (project_update.read(folder) or {}).get("notes", [])]


def test_a_start_that_puts_back_a_cut_off_replace_keeps_the_unread_update_notice(tmp_path):
    from openavc.core import project_swap
    from openavc.core.backup_manager import backup_id

    project_path = tmp_path / "project.avc"
    _real_project(project_path)
    taken = create_backup(project_path, "Before opening 'Lobby'")
    _unread_update_notice(tmp_path)
    project_swap.write_marker(tmp_path, backup_id(tmp_path, taken))

    assert project_recovery.load_or_recover(project_path).project.id == "live"
    assert _notice_messages(tmp_path) == ["Kept until somebody reads it."]


def test_a_start_that_restores_a_backup_keeps_the_unread_update_notice(tmp_path):
    project_path = tmp_path / "project.avc"
    _real_project(project_path)
    create_backup(project_path, "Auto backup")
    _unread_update_notice(tmp_path)
    _corrupt(project_path)

    assert project_recovery.load_or_recover(project_path).project.id == "live"
    assert _notice_messages(tmp_path) == ["Kept until somebody reads it."]


def _aged(paths):
    """``paths`` given modification times oldest first, a second apart, so
    the rotation orders them as they were written."""
    import os

    for age, path in enumerate(paths):
        os.utime(path, (1_000_000 + age, 1_000_000 + age))


def test_a_start_never_rotates_away_a_backup_it_has_still_to_try(tmp_path, monkeypatch):
    """A full list whose newest backup reads as a project but will not load:
    the backup kept of the file that would not load must not remove the
    oldest, the one backup that opens, before the walk reaches it."""
    from openavc.core.backup_manager import MAX_BACKUPS

    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    oldest = create_backup(project_path, "Auto backup")
    _corrupt(project_path)
    broken = [create_backup(project_path, f"Auto backup {n}") for n in range(MAX_BACKUPS - 2)]
    _real_project(project_path)
    newest = create_backup(project_path, "Auto backup newest")
    _corrupt(project_path)
    _aged([oldest, *broken, newest])

    load = project_recovery.load_project
    calls = []

    def the_first_restore_will_not_load(path, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            raise OSError(5, "Input/output error")
        return load(path, **kwargs)

    monkeypatch.setattr(project_recovery, "load_project", the_first_restore_will_not_load)

    project = project_recovery.load_or_recover(project_path)

    assert project.project.id == "live"
    assert project_recovery.read(project_path.parent)["backup"] == f"backups/{oldest.name}"


def test_a_start_whose_backups_all_fail_keeps_every_one_of_them(tmp_path, monkeypatch):
    from openavc.core.backup_manager import MAX_BACKUPS

    monkeypatch.setattr("openavc.system_config.get_seed_project_path", lambda: None)
    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    _corrupt(project_path)
    tried = [create_backup(project_path, f"Auto backup {n}") for n in range(MAX_BACKUPS)]
    _aged(tried)

    assert project_recovery.load_or_recover(project_path).project.id == "recovery"

    assert all(path.exists() for path in tried)


# --- A cut-off replace whose own backup cannot be put back ----------------------


def _cut_off_with_no_backup(project_path, moved=("scripts", "state.json")):
    """A replace a power cut stopped part way, its marker naming a backup
    that is gone: what it had moved aside (``moved``) is in its working
    folder, and the incoming project's script is in place."""
    from openavc.core import project_swap

    folder = project_path.parent
    gone = folder / "backups" / "backup_20260101_000000_before_opening_lobby.zip"
    gone.parent.mkdir(exist_ok=True)
    gone.write_bytes(b"")
    project_swap.write_marker(folder, "backups/" + gone.name)
    gone.unlink()
    aside = project_swap.moved_aside(folder)
    aside.mkdir(parents=True)
    for name in moved:
        if (folder / name).exists():
            (folder / name).rename(aside / name)
    (folder / "scripts").mkdir(exist_ok=True)
    (folder / "scripts" / "main.py").write_text("# the incoming script\n", encoding="utf-8")


def test_a_cut_off_replace_with_its_project_file_moved_aside_comes_back_from_what_it_kept(tmp_path):
    """The swap had moved every entry aside, the project file last: what
    the start keeps of them is the whole project before the replace, and
    the missing file is replaced from it."""
    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    (project_path.parent / "scripts").mkdir()
    (project_path.parent / "scripts" / "main.py").write_text("# the live script\n", encoding="utf-8")
    _cut_off_with_no_backup(project_path, moved=("scripts", "project.avc"))

    project = project_recovery.load_or_recover(project_path)

    assert project.project.id == "live"
    assert (project_path.parent / "scripts" / "main.py").read_text(encoding="utf-8") == "# the live script\n"
    notice = project_recovery.read(project_path.parent)
    assert (notice["outcome"], notice["reason"]) == (project_recovery.RESTORED, project_recovery.MISSING)
    held = next(b for b in list_backups(project_path) if b.filename == notice["backup"])
    assert held.reason == project_recovery.SET_ASIDE_REASONS[project_recovery.INTERRUPTED]


def test_a_cut_off_replace_of_a_missing_project_file_starts_the_starter_and_says_so(tmp_path):
    """No project file before the replace and none swapped in: the starter
    runs, and the notice says the file was missing, since files of a
    project were there."""
    project_path = tmp_path / "projects" / "default" / "project.avc"
    project_path.parent.mkdir(parents=True)
    (project_path.parent / "state.json").write_text('{"var.level": 3}', encoding="utf-8")
    _cut_off_with_no_backup(project_path)

    project_recovery.load_or_recover(project_path)

    notice = project_recovery.read(project_path.parent)
    assert (notice["outcome"], notice["reason"]) == (project_recovery.STARTER, project_recovery.MISSING)
    assert any(b.reason == project_recovery.SET_ASIDE_REASONS[project_recovery.INTERRUPTED]
               for b in list_backups(project_path))


def test_what_a_cut_off_replace_keeps_never_rotates_away_a_backup_the_start_has_still_to_try(tmp_path):
    """A full list whose only backup that opens is the oldest: keeping what
    the cut-off swap moved aside must not remove it before the walk
    reaches it."""
    from openavc.core.backup_manager import MAX_BACKUPS

    project_path = tmp_path / "projects" / "default" / "project.avc"
    _real_project(project_path)
    oldest = create_backup(project_path, "Auto backup")
    _corrupt(project_path)
    broken = [create_backup(project_path, f"Auto backup {n}") for n in range(MAX_BACKUPS - 1)]
    _aged([oldest, *broken])
    (project_path.parent / "state.json").write_text('{"var.level": 3}', encoding="utf-8")
    _cut_off_with_no_backup(project_path)

    project = project_recovery.load_or_recover(project_path)

    assert project.project.id == "live"
    assert project_recovery.read(project_path.parent)["backup"] == f"backups/{oldest.name}"
