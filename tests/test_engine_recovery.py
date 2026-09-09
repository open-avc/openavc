"""Tests for Engine project-load recovery paths."""

import json

from openavc.core import project_recovery
from openavc.core.backup_manager import create_backup
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


# --- The notice a recovery leaves behind (Q-178 / F-028) ---
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
    backup = create_backup(project_path.parent, "Auto backup")
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
    assert create_backup(project_path.parent, "Auto backup") is not None
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
    assert create_backup(project_path.parent, "Auto backup") is not None
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
    assert create_backup(project_path.parent, "Auto backup") is not None
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
    assert create_backup(project_path.parent, "Auto backup") is not None
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
