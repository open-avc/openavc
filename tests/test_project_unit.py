"""Every door that backs the project up or replaces it carries the same files.

One file is planted for each entry the project's list holds; each door is
then asked what it took. A door keeping its own list is what let one of them
leave a tree behind.
"""

import zipfile
from pathlib import Path

import pytest

from openavc.core import backup_manager, project_swap, project_unit
from openavc.updater.backup import create_backup as create_update_backup
from openavc.updater.rollback import restore_pre_update_data, write_pending_marker

#: One file per entry beside the project file, at a path its tree's own rules
#: accept: a theme is a ``<id>.json`` directly in ``themes/``, a custom control
#: is a folder, an asset may sit in a folder of its own.
_PLANTED = {
    project_unit.SCRIPTS_DIR: "start.py",
    project_unit.ASSETS_DIR: "nested/logo.svg",
    project_unit.UI_DIR: "meter/index.html",
    project_unit.THEMES_DIR: "house.json",
}


def _plant(project_dir: Path, text: str) -> set[str]:
    """Write one file for every entry of the list; answer their paths."""
    planted = {f"{entry}/{rel}" for entry, rel in _PLANTED.items()}
    planted.add(project_unit.STATE_FILE)
    for rel in planted:
        path = project_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{text} {rel}", encoding="utf-8")
    return planted


def test_every_entry_of_the_list_is_planted():
    """The fixture covers the whole list, so a new entry fails here first."""
    assert set(_PLANTED) | {project_unit.STATE_FILE} == set(project_unit.BESIDE_PROJECT)
    assert set(project_unit.FILE_TREES) <= set(_PLANTED)


def test_a_project_backup_carries_every_entry(tmp_path):
    project_dir = tmp_path / "projects" / "default"
    project_dir.mkdir(parents=True)
    (project_dir / "project.avc").write_text('{"project": {"name": "Room"}}')
    planted = _plant(project_dir, "original")

    backup = backup_manager.create_backup(project_dir / "project.avc", "test")

    with zipfile.ZipFile(backup) as archive:
        assert planted <= set(archive.namelist())


def test_a_replace_swaps_every_entry_and_one_with_none_leaves_none(tmp_path):
    project_dir = tmp_path / "live"
    project_file = project_dir / "project.avc"
    planted = _plant(project_dir, "previous")
    project_file.write_text("{}")

    project_swap.replace_on_disk(project_file, b"{}", lambda incoming: _plant(incoming, "opened"))

    for rel in planted:
        assert (project_dir / rel).read_text() == f"opened {rel}"

    project_swap.replace_on_disk(project_file, b"{}", None)

    assert [p for p in project_dir.rglob("*") if p.is_file()] == [project_file]


@pytest.mark.parametrize("project_name", ["project.avc", "my-space.avc"])
def test_a_rollback_puts_back_every_entry_of_a_project_at_the_data_root(tmp_path, project_name):
    """An appliance keeps its project beside the system's own data, so the
    update's backup takes only the project's entries from there; every one
    the list holds comes back with a rollback, the themes included."""
    data = tmp_path / "data"
    project = data / project_name
    project.parent.mkdir(parents=True)
    project.write_text('{"openavc_version": "0.7.0"}')
    planted = _plant(data, "original")

    backup = create_update_backup(data, "0.7.0", project_path=project)
    with zipfile.ZipFile(backup) as archive:
        assert {f"external-project/{rel}" for rel in planted} <= set(archive.namelist())

    write_pending_marker(data, from_version="0.7.0", to_version="0.8.0", backup_path=backup)
    for rel in planted:
        (data / rel).write_text("changed by the update")

    assert restore_pre_update_data(data, project_path=project) is True
    for rel in planted:
        assert (data / rel).read_text() == f"original {rel}"
