"""An appliance keeps its project beside system data, outside projects/."""

import zipfile

import pytest

from openavc.updater.backup import create_backup, restore_user_data
from openavc.updater.rollback import restore_pre_update_data, write_pending_marker
from tests.helpers import folder_link_kinds, make_folder_link


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("project_name", ["project.avc", "my-space.avc"])
def test_data_root_project_survives_automatic_rollback(tmp_path, project_name):
    data = tmp_path / "data"
    project = data / project_name
    project_files = {
        project_name: '{"openavc_version": "0.7.0"}',
        "state.json": '{"volume": 10}',
        "scripts/start.py": "# original script",
        "assets/nested/logo.svg": "<svg/>",
        "ui/meter/index.html": "original custom control",
    }
    unrelated = {
        "supervisor/state.json": "supervisor state",
        "updates/payload.tar.gz": "download",
        "backups/previous.zip": "old backup",
        "other.avc": "another file at the root",
        "device_logins.key": "test key",
    }
    for name, contents in {**project_files, **unrelated, "system.json": "{}"}.items():
        _write(data / name, contents)

    backup = create_backup(data, "0.7.0", project_path=project)
    with zipfile.ZipFile(backup) as archive:
        assert set(archive.namelist()) == {
            "system.json", *(f"external-project/{name}" for name in project_files),
        }
        for name, contents in project_files.items():
            assert archive.read(f"external-project/{name}").decode() == contents

    write_pending_marker(data, from_version="0.7.0", to_version="0.8.0", backup_path=backup)
    for name in project_files:
        _write(data / name, "changed by the update")
    project.unlink()  # Restore must also work when the project file is gone.
    _write(data / "system.json", "new system settings")

    assert restore_pre_update_data(data, project_path=project) is True
    for name, contents in project_files.items():
        assert (data / name).read_text(encoding="utf-8") == contents
    for name, contents in unrelated.items():
        assert (data / name).read_text(encoding="utf-8") == contents
    assert (data / "system.json").read_text() == "new system settings"


def test_data_root_restore_ignores_unrelated_external_members(tmp_path):
    data = tmp_path / "data"
    project = data / "project.avc"
    _write(project, "original project")
    _write(data / "supervisor/state.json", "live supervisor state")
    backup = create_backup(data, "0.7.0", project_path=project)
    with zipfile.ZipFile(backup, "a") as archive:
        archive.writestr("external-project/supervisor/state.json", "wrong state")
        archive.writestr("external-project/system.json", "wrong settings")
        archive.writestr("external-project/scripts/../../outside.py", "unsafe")
    _write(project, "changed project")

    assert restore_user_data(data, backup, project_path=project) is True
    assert project.read_text() == "original project"
    assert (data / "supervisor/state.json").read_text() == "live supervisor state"
    assert not (data / "system.json").exists()
    assert not (tmp_path / "outside.py").exists()


@pytest.mark.parametrize("kind", folder_link_kinds())
def test_data_root_selection_preserves_linked_assets(tmp_path, kind):
    data = tmp_path / "data"
    project = data / "project.avc"
    _write(project, "original project")
    assets = tmp_path / "shared-assets"
    _write(assets / "nested/logo.svg", "original image")
    make_folder_link(kind, data / "assets", assets)
    alias = tmp_path / "data-alias"
    make_folder_link(kind, alias, data)

    backup = create_backup(alias, "0.7.0", project_path=project)
    _write(assets / "nested/logo.svg", "changed image")
    assert restore_user_data(alias, backup, project_path=project) is True
    assert (assets / "nested/logo.svg").read_text() == "original image"
    assert (data / "assets").resolve() == assets.resolve()
