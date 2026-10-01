"""Links in the data a pre-update backup carries, and what a rollback does with them.

A project tree is backed up as the project sees it, links followed, and each
link is recorded so a rollback puts it back as a link and restores the files
behind it in place. The other trees leave a link out. Every case runs with a
symlink and, on Windows, a junction.
"""

import json
import os
import zipfile
from pathlib import Path

import pytest

from openavc.updater.backup import LINKS_MEMBER, create_backup, restore_user_data
from openavc.utils.paths import is_link
from tests import gates
from tests.helpers import folder_link_kinds, make_folder_link

KINDS = folder_link_kinds()


def _members(backup: Path) -> list[str]:
    with zipfile.ZipFile(backup) as zf:
        return zf.namelist()


def _recorded(backup: Path) -> dict:
    with zipfile.ZipFile(backup) as zf:
        return json.loads(zf.read(LINKS_MEMBER))


def _room(folder: Path) -> Path:
    """A project folder: the file a format upgrade rewrites, and one it leaves."""
    (folder / "scripts").mkdir(parents=True)
    (folder / "project.avc").write_text('{"openavc_version": "0.7.0"}', encoding="utf-8")
    (folder / "scripts" / "lights.py").write_text("# lights", encoding="utf-8")
    return folder


def _linked_project(tmp_path: Path, kind: str) -> tuple[Path, Path]:
    """data/projects/default is a link to a folder kept elsewhere."""
    data = tmp_path / "data"
    (data / "projects").mkdir(parents=True)
    room = _room(tmp_path / "elsewhere" / "room")
    make_folder_link(kind, data / "projects" / "default", room)
    return data, room


def test_a_backup_with_no_links_records_none(tmp_path):
    _room(tmp_path / "projects" / "default")
    backup = create_backup(tmp_path, "0.7.0")
    assert LINKS_MEMBER not in _members(backup)
    assert "projects/default/project.avc" in _members(backup)


@pytest.mark.parametrize("kind", KINDS)
def test_a_linked_project_folder_is_backed_up_and_recorded(tmp_path, kind):
    data, room = _linked_project(tmp_path, kind)
    backup = create_backup(data, "0.7.0")
    assert "projects/default/project.avc" in _members(backup)
    assert os.path.normcase(_recorded(backup)["projects"]["default"]) == os.path.normcase(
        os.path.realpath(room)
    )


@pytest.mark.parametrize("kind", KINDS)
def test_a_rollback_puts_the_link_back_and_restores_through_it(tmp_path, kind):
    data, room = _linked_project(tmp_path, kind)
    backup = create_backup(data, "0.7.0")
    # The failed update upgraded the project's format through the link.
    (room / "project.avc").write_text('{"openavc_version": "0.8.0"}', encoding="utf-8")
    untouched = (room / "scripts" / "lights.py").stat().st_mtime_ns

    assert restore_user_data(data, backup) is True

    default = data / "projects" / "default"
    assert is_link(default), "the link came back as a plain copy"
    assert os.path.realpath(default) == os.path.realpath(room)
    assert (room / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.7.0"}'
    assert (room / "scripts" / "lights.py").stat().st_mtime_ns == untouched, (
        "a file already holding the backed-up bytes is not rewritten"
    )
    displaced = data / "projects.pre-rollback" / "default" / "project.avc"
    assert displaced.read_text(encoding="utf-8") == '{"openavc_version": "0.8.0"}'


@pytest.mark.parametrize("kind", KINDS)
def test_a_link_made_after_the_backup_is_not_written_through(tmp_path, kind):
    data = tmp_path / "data"
    default = _room(data / "projects" / "default")
    backup = create_backup(data, "0.7.0")
    # After the backup, the project folder is swapped for a link to another one.
    other = _room(tmp_path / "other")
    (other / "project.avc").write_text('{"openavc_version": "0.8.0"}', encoding="utf-8")
    os.replace(default, tmp_path / "moved")
    make_folder_link(kind, default, other)

    assert restore_user_data(data, backup) is True

    assert not is_link(default)
    assert (default / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.7.0"}'
    assert (other / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.8.0"}'


@pytest.mark.parametrize("kind", KINDS)
def test_a_link_whose_target_is_gone_keeps_the_restored_copy(tmp_path, kind):
    data, room = _linked_project(tmp_path, kind)
    backup = create_backup(data, "0.7.0")
    os.replace(room, tmp_path / "unreachable")

    assert restore_user_data(data, backup) is True

    default = data / "projects" / "default"
    assert not is_link(default)
    assert (default / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.7.0"}'


@pytest.mark.parametrize("kind", KINDS)
def test_a_backup_without_a_link_record_is_restored_as_before(tmp_path, kind):
    """A backup made before links were recorded: the restored copy stays."""
    data, room = _linked_project(tmp_path, kind)
    recorded = create_backup(data, "0.7.0")
    backup = recorded.with_name("older-" + recorded.name)
    with zipfile.ZipFile(recorded) as src, zipfile.ZipFile(backup, "w") as dst:
        for info in src.infolist():
            if info.filename != LINKS_MEMBER:
                dst.writestr(info, src.read(info))
    (room / "project.avc").write_text('{"openavc_version": "0.8.0"}', encoding="utf-8")

    assert restore_user_data(data, backup) is True

    default = data / "projects" / "default"
    assert not is_link(default)
    assert (default / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.7.0"}'
    assert (room / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.8.0"}'


@pytest.mark.parametrize("kind", KINDS)
def test_a_link_that_loops_back_does_not_stop_the_backup(tmp_path, kind):
    data = tmp_path / "data"
    default = _room(data / "projects" / "default")
    make_folder_link(kind, default / "scripts" / "loop", default)

    backup = create_backup(data, "0.7.0")

    assert not any("/loop/" in name for name in _members(backup))
    assert "default/scripts/loop" in _recorded(backup)["projects"]
    assert restore_user_data(data, backup) is True
    assert is_link(default / "scripts" / "loop")


@pytest.mark.parametrize("kind", KINDS)
def test_a_link_into_the_backups_is_not_backed_up(tmp_path, kind):
    data = tmp_path / "data"
    default = _room(data / "projects" / "default")
    (data / "backups").mkdir()
    (data / "backups" / "pre-update-v0.6.0-x.zip").write_bytes(b"an older backup")
    make_folder_link(kind, default / "archive", data / "backups")

    backup = create_backup(data, "0.7.0")

    assert not any("/archive/" in name for name in _members(backup))


@pytest.mark.parametrize("kind", KINDS)
def test_a_linked_plugin_folder_is_left_out(tmp_path, kind):
    """A plugin linked in while it is developed is somebody's working copy,
    and a rollback never restores the plugin folder."""
    data = tmp_path / "data"
    (data / "plugin_repo" / "installed").mkdir(parents=True)
    (data / "plugin_repo" / "installed" / "plugin.json").write_text("{}", encoding="utf-8")
    working_copy = tmp_path / "dev" / "my_plugin"
    working_copy.mkdir(parents=True)
    (working_copy / "plugin.json").write_text("{}", encoding="utf-8")
    make_folder_link(kind, data / "plugin_repo" / "my_plugin", working_copy)

    names = _members(create_backup(data, "0.7.0"))

    assert "plugin_repo/installed/plugin.json" in names
    assert not any(name.startswith("plugin_repo/my_plugin") for name in names)


@pytest.mark.parametrize("kind", KINDS)
def test_a_linked_projects_folder_is_restored_through_its_link(tmp_path, kind):
    data = tmp_path / "data"
    data.mkdir()
    projects = tmp_path / "elsewhere" / "projects"
    room = _room(projects / "default")
    make_folder_link(kind, data / "projects", projects)
    backup = create_backup(data, "0.7.0")
    (room / "project.avc").write_text('{"openavc_version": "0.8.0"}', encoding="utf-8")

    assert restore_user_data(data, backup) is True

    assert is_link(data / "projects")
    assert (room / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.7.0"}'
    displaced = data / "projects.pre-rollback" / "default" / "project.avc"
    assert displaced.read_text(encoding="utf-8") == '{"openavc_version": "0.8.0"}'


@pytest.mark.parametrize("kind", KINDS)
def test_a_rollback_moves_nothing_out_from_behind_a_link(tmp_path, kind):
    """Project backups are carried over from the displaced tree, but a
    ``backups`` folder behind a link left in it is somewhere else, not ours."""
    data = tmp_path / "data"
    default = _room(data / "projects" / "default")
    backup = create_backup(data, "0.7.0")
    shared = tmp_path / "shared"
    (shared / "backups").mkdir(parents=True)
    (shared / "backups" / "theirs.zip").write_bytes(b"not ours")
    make_folder_link(kind, default / "shared", shared)

    assert restore_user_data(data, backup) is True

    assert (shared / "backups" / "theirs.zip").read_bytes() == b"not ours"


@pytest.mark.parametrize("kind", KINDS)
def test_an_external_project_is_restored_through_its_linked_folder(tmp_path, kind):
    data = tmp_path / "data"
    data.mkdir()
    external = _room(tmp_path / "remote" / "studio")
    library = tmp_path / "library"
    library.mkdir()
    (library / "logo.svg").write_text("<svg>old</svg>", encoding="utf-8")
    make_folder_link(kind, external / "assets", library)
    project_path = external / "project.avc"
    backup = create_backup(data, "0.7.0", project_path=project_path)
    (library / "logo.svg").write_text("<svg>new</svg>", encoding="utf-8")

    assert restore_user_data(data, backup, project_path=project_path) is True

    assert is_link(external / "assets")
    assert (library / "logo.svg").read_text(encoding="utf-8") == "<svg>old</svg>"


@gates.skipif_missing(gates.SYMLINKS, gates.symlink_reason())
def test_an_external_projects_linked_file_stays_a_link(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    external = tmp_path / "remote" / "studio"
    external.mkdir(parents=True)
    kept = tmp_path / "synced" / "project.avc"
    kept.parent.mkdir()
    kept.write_text('{"openavc_version": "0.7.0"}', encoding="utf-8")
    project_path = external / "project.avc"
    project_path.symlink_to(kept)
    backup = create_backup(data, "0.7.0", project_path=project_path)
    kept.write_text('{"openavc_version": "0.8.0"}', encoding="utf-8")

    assert restore_user_data(data, backup, project_path=project_path) is True

    assert project_path.is_symlink()
    assert kept.read_text(encoding="utf-8") == '{"openavc_version": "0.7.0"}'
