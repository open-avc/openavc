"""Tests for Engine project-load recovery paths."""

import json

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
