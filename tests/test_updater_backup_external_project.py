"""Tests for pre-update backup including externally-located projects (A37).

When OPENAVC_PROJECT points outside data_dir — common in production
deployments that keep project files on a different volume — the backup
module's rglob of `data_dir/projects` doesn't reach them. Without
A37's fix, project.avc, state.json, scripts/, and assets/ would all
be missing from the pre-update backup.
"""

import zipfile

from server.updater.backup import create_backup


def _write(path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_backup_includes_external_project_dir(tmp_path):
    """Regression for A37: when project_path is outside data_dir, the
    backup must include project.avc and its siblings (state.json,
    scripts/, assets/) under external-project/.
    """
    data_dir = tmp_path / "data"
    project_dir = tmp_path / "remote-projects" / "studio_a"
    project_path = project_dir / "project.avc"

    data_dir.mkdir()
    _write(project_path, '{"project": {"id": "studio_a"}}')
    _write(project_dir / "state.json", '{"var.x": 1}')
    _write(project_dir / "scripts" / "lights.py", "# script")
    _write(project_dir / "assets" / "logo.png", "fake-png")

    backup_path = create_backup(data_dir, "1.2.3", project_path=project_path)

    with zipfile.ZipFile(backup_path) as zf:
        names = set(zf.namelist())

    assert "external-project/project.avc" in names
    assert "external-project/state.json" in names
    assert "external-project/scripts/lights.py" in names
    assert "external-project/assets/logo.png" in names


def test_backup_skips_external_branch_for_in_data_dir_project(tmp_path):
    """If the project lives at the normal data_dir/projects/default/ path,
    the regular rglob already archives it — the external branch must not
    double-write the same files under external-project/ too.
    """
    data_dir = tmp_path / "data"
    project_path = data_dir / "projects" / "default" / "project.avc"
    _write(project_path, '{"project": {"id": "default"}}')
    _write(data_dir / "projects" / "default" / "state.json", '{}')

    backup_path = create_backup(data_dir, "1.2.3", project_path=project_path)

    with zipfile.ZipFile(backup_path) as zf:
        names = set(zf.namelist())

    assert "projects/default/project.avc" in names
    assert "projects/default/state.json" in names
    # No external-project/ duplicates
    assert not any(name.startswith("external-project/") for name in names)


def test_backup_omits_external_branch_when_project_path_none(tmp_path):
    """The old call signature `create_backup(data_dir, version)` keeps its
    behavior: no external archive, no error.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write(data_dir / "system.json", "{}")

    backup_path = create_backup(data_dir, "1.2.3")  # project_path defaults to None

    with zipfile.ZipFile(backup_path) as zf:
        names = set(zf.namelist())
    assert "system.json" in names
    assert not any(name.startswith("external-project/") for name in names)


def test_backup_excludes_external_user_backups(tmp_path):
    """User backups ({project_dir}/backups/) inside an external project dir
    must not be embedded in the pre-update archive — same exclusion as the
    regular projects/ walk.
    """
    data_dir = tmp_path / "data"
    project_dir = tmp_path / "remote-projects" / "studio_a"
    project_path = project_dir / "project.avc"

    data_dir.mkdir()
    _write(project_path, '{"project": {"id": "studio_a"}}')
    _write(project_dir / "backups" / "backup-20260101T000000Z.zip", "fake-zip")

    backup_path = create_backup(data_dir, "1.2.3", project_path=project_path)

    with zipfile.ZipFile(backup_path) as zf:
        names = set(zf.namelist())

    assert "external-project/project.avc" in names
    assert not any("backups/" in name for name in names)


def test_backup_tolerates_missing_external_project(tmp_path):
    """If project_path is set but the file no longer exists (e.g. the user
    moved it before triggering the update), the backup must not crash —
    it just skips the external archive.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    missing = tmp_path / "elsewhere" / "project.avc"  # never created

    # Must not raise.
    backup_path = create_backup(data_dir, "1.2.3", project_path=missing)
    assert backup_path.exists()
