"""A rollback puts the projects back, never the system's own files beside them.

The cloud pairing (``cloud.json``) and this system's instance id
(``.instance_id``) are kept in the project's folder, but they are the
system's, not the project's. An update's backup carries them with the folder;
an automatic rollback leaves the ones the system has now where they are,
however the project is kept: in the projects folder, behind a link, or in a
folder outside the data folder.
"""

import json
import zipfile
from pathlib import Path

import pytest

from openavc.updater.backup import create_backup, restore_user_data
from tests.helpers import folder_link_kinds, make_folder_link

OLD_PROJECT = '{"openavc_version": "0.7.0"}'
NEW_PROJECT = '{"openavc_version": "0.8.0"}'
BEFORE = json.dumps({"enabled": True, "system_id": "sys-before", "system_key": "key-before"})
SINCE = json.dumps({"enabled": True, "system_id": "sys-since", "system_key": "key-since"})


def _room(folder: Path, *, paired: str | None = BEFORE) -> Path:
    (folder / "scripts").mkdir(parents=True)
    (folder / "project.avc").write_text(OLD_PROJECT, encoding="utf-8")
    (folder / "scripts" / "lights.py").write_text("# lights", encoding="utf-8")
    (folder / ".instance_id").write_text("instance-before", encoding="utf-8")
    if paired is not None:
        (folder / "cloud.json").write_text(paired, encoding="utf-8")
    return folder


def _since_the_update(folder: Path) -> None:
    """The update rewrote the project, and the system was paired again."""
    (folder / "project.avc").write_text(NEW_PROJECT, encoding="utf-8")
    (folder / "cloud.json").write_text(SINCE, encoding="utf-8")
    (folder / ".instance_id").write_text("instance-since", encoding="utf-8")


def _assert_kept(folder: Path) -> None:
    assert (folder / "project.avc").read_text(encoding="utf-8") == OLD_PROJECT
    assert (folder / "cloud.json").read_text(encoding="utf-8") == SINCE
    assert (folder / ".instance_id").read_text(encoding="utf-8") == "instance-since"


def test_a_rollback_keeps_the_pairing_in_the_projects_folder(tmp_path):
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    backup = create_backup(data, "0.7.0", project_path=room / "project.avc")
    with zipfile.ZipFile(backup) as zf:
        assert zf.read("projects/default/cloud.json").decode() == BEFORE
    _since_the_update(room)

    assert restore_user_data(data, backup, project_path=room / "project.avc") is True

    _assert_kept(room)


def test_a_rollback_keeps_a_pairing_made_after_the_update(tmp_path):
    data = tmp_path / "data"
    room = _room(data / "projects" / "default", paired=None)
    backup = create_backup(data, "0.7.0", project_path=room / "project.avc")
    _since_the_update(room)

    assert restore_user_data(data, backup, project_path=room / "project.avc") is True

    _assert_kept(room)


def test_a_rollback_keeps_the_pairing_of_a_project_kept_outside_the_data_folder(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    room = _room(tmp_path / "room")
    backup = create_backup(data, "0.7.0", project_path=room / "project.avc")
    with zipfile.ZipFile(backup) as zf:
        assert zf.read("external-project/cloud.json").decode() == BEFORE
    _since_the_update(room)

    assert restore_user_data(data, backup, project_path=room / "project.avc") is True

    _assert_kept(room)


def test_a_rollback_keeps_the_pairing_of_a_project_kept_at_the_data_root(tmp_path):
    data = _room(tmp_path / "data")
    backup = create_backup(data, "0.7.0", project_path=data / "project.avc")
    _since_the_update(data)

    assert restore_user_data(data, backup, project_path=data / "project.avc") is True

    _assert_kept(data)


@pytest.mark.parametrize("kind", folder_link_kinds())
def test_a_rollback_keeps_the_pairing_behind_a_linked_projects_folder(tmp_path, kind):
    data = tmp_path / "data"
    data.mkdir()
    projects = tmp_path / "elsewhere" / "projects"
    room = _room(projects / "default")
    make_folder_link(kind, data / "projects", projects)
    backup = create_backup(data, "0.7.0", project_path=data / "projects" / "default" / "project.avc")
    _since_the_update(room)

    assert restore_user_data(data, backup, project_path=data / "projects" / "default" / "project.avc") is True

    _assert_kept(room)


@pytest.mark.parametrize("kind", folder_link_kinds())
def test_a_rollback_keeps_the_pairing_in_a_linked_project_folder(tmp_path, kind):
    data = tmp_path / "data"
    (data / "projects").mkdir(parents=True)
    room = _room(tmp_path / "elsewhere" / "room")
    make_folder_link(kind, data / "projects" / "default", room)
    backup = create_backup(data, "0.7.0", project_path=data / "projects" / "default" / "project.avc")
    _since_the_update(room)

    assert restore_user_data(data, backup, project_path=data / "projects" / "default" / "project.avc") is True

    _assert_kept(room)


def test_a_file_of_the_same_name_inside_a_project_tree_is_put_back(tmp_path):
    """Only the pairing beside a project is the system's: an image or a
    custom control's file that happens to share its name is the project's."""
    data = tmp_path / "data"
    room = _room(data / "projects" / "default")
    (room / "ui" / "meter").mkdir(parents=True)
    (room / "ui" / "meter" / "cloud.json").write_text("control before", encoding="utf-8")
    backup = create_backup(data, "0.7.0", project_path=room / "project.avc")
    (room / "ui" / "meter" / "cloud.json").write_text("control since", encoding="utf-8")

    assert restore_user_data(data, backup, project_path=room / "project.avc") is True

    assert (room / "ui" / "meter" / "cloud.json").read_text(encoding="utf-8") == "control before"
