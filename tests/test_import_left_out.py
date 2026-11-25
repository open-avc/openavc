"""An import into the Project Library says what it left out of the file.

Each zip here is made the way the platform makes it (a project backup, the
backup an update takes, an export), from one project folder holding every
entry that sits beside a project file, so the warnings track what each
backup really holds. A plain export, and one zipped with a design tool's
leftovers, import without a word.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from openavc.core import backup_manager, import_left_out, project_library, project_unit
from openavc.updater.backup import create_backup as update_backup

PROJECT = {
    "openavc_version": "0.18.0",
    "project": {"id": "acme_room", "name": "Acme Room", "created": "2026-03-03T10:00:00"},
}

EVERY_PART = (
    "scripts, images, custom controls, custom themes and saved variable values"
)


@pytest.fixture
def library(tmp_path, monkeypatch):
    lib = tmp_path / "library"
    monkeypatch.setattr(project_library.config, "SAVED_PROJECTS_DIR", lib)
    return lib


def _project_folder(folder: Path, project_id: str = "acme_room") -> Path:
    """A project file with every entry ``core/project_unit`` lists beside it."""
    folder.mkdir(parents=True)
    data = {**PROJECT, "project": {**PROJECT["project"], "id": project_id}}
    (folder / "project.avc").write_text(json.dumps(data))
    (folder / "scripts").mkdir()
    (folder / "scripts" / "lights.py").write_text("x = 1\n")
    (folder / "assets" / "logos").mkdir(parents=True)
    (folder / "assets" / "logos" / "house.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (folder / "ui" / "meter").mkdir(parents=True)
    (folder / "ui" / "meter" / "index.html").write_text("<div></div>")
    (folder / "themes").mkdir()
    (folder / "themes" / "house.json").write_text(json.dumps({"id": "house", "name": "House"}))
    (folder / "state.json").write_text(json.dumps({"var.level": 5}))
    return folder / "project.avc"


def _imported(content: bytes, name: str) -> tuple[dict, Path]:
    result = project_library.import_project(content, name)
    return result, project_library._lib_dir() / result["id"]


def _copy_holds(copy: Path) -> set[str]:
    return {p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.is_file()}


def test_every_entry_beside_a_project_file_has_its_words():
    assert set(import_left_out.WORDS) == set(project_unit.BESIDE_PROJECT)


def test_a_project_backup_names_its_saved_values_and_logins(library, tmp_path, monkeypatch):
    project_file = _project_folder(tmp_path / "room")
    monkeypatch.setattr(backup_manager, "_logins_copy", lambda data: '{"sealed": "for this system"}')
    backup = backup_manager.create_backup(project_file, "Manual backup")

    result, copy = _imported(backup.read_bytes(), backup.name)

    assert "state.json" not in _copy_holds(copy)
    assert result["warnings"] == [
        "The saved variable values and the device logins in this file were left out. Restoring it "
        "from Backups in the Program view, on the system that took it, brings the project back with them."
    ]


def test_a_project_backup_without_logins_names_its_saved_values(library, tmp_path, monkeypatch):
    project_file = _project_folder(tmp_path / "room")
    monkeypatch.setattr(backup_manager, "_logins_copy", lambda data: None)
    backup = backup_manager.create_backup(project_file, "Manual backup")

    result, _copy = _imported(backup.read_bytes(), backup.name)

    assert result["warnings"] == [
        "The saved variable values in this file were left out. Restoring it from Backups in the "
        "Program view, on the system that took it, brings the project back with them."
    ]


def test_an_update_backup_names_what_sits_beside_its_project_file(library, tmp_path):
    data_dir = tmp_path / "data"
    _project_folder(data_dir / "projects" / "default")
    (data_dir / "system.json").write_text("{}")
    (data_dir / "device_logins.json").write_text("{}")
    backup = update_backup(data_dir, "0.41.0")

    result, copy = _imported(backup.read_bytes(), backup.name)

    assert _copy_holds(copy) == {"project.avc"}
    assert result["warnings"] == [
        f"Only the project file was imported, from this file's projects/default folder: the {EVERY_PART} "
        f"beside it were left out. A file made with Export brings a project with its files.",
        "The device logins in this file were left out.",
    ]


def test_an_update_backup_kept_outside_the_projects_folder_is_named_the_same(library, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    project_file = _project_folder(tmp_path / "elsewhere" / "room")
    backup = update_backup(data_dir, "0.41.0", project_path=project_file)

    result, _copy = _imported(backup.read_bytes(), backup.name)

    assert result["warnings"] == [
        f"Only the project file was imported, from this file's external-project folder: the {EVERY_PART} "
        f"beside it were left out. A file made with Export brings a project with its files.",
    ]


def test_a_file_holding_two_projects_names_the_one_left_out(library, tmp_path):
    data_dir = tmp_path / "data"
    _project_folder(data_dir / "projects" / "default")
    _project_folder(data_dir / "projects" / "second", "second_room")
    backup = update_backup(data_dir, "0.41.0")

    result, _copy = _imported(backup.read_bytes(), backup.name)

    # Which one comes first is the order the folders were walked in.
    imported, other = ("default", "second") if result["id"] == "acme_room" else ("second", "default")
    assert result["warnings"][-1] == (
        f"This file holds more than one project. Only projects/{imported}/project.avc was imported; "
        f"projects/{other}/project.avc was left out."
    )


def test_an_export_imports_without_a_word(library, tmp_path):
    project_file = _project_folder(tmp_path / "room")
    content, name, _type = project_library.export_active_project(project_file)

    result, copy = _imported(content, name)

    assert result["warnings"] == []
    assert _copy_holds(copy) == {
        "project.avc", "scripts/lights.py", "assets/logos/house.png", "ui/meter/index.html",
        "themes/house.json",
    }


def test_an_export_zipped_again_on_a_mac_imports_without_a_word(library, tmp_path):
    project_file = _project_folder(tmp_path / "room")
    content, name, _type = project_library.export_active_project(project_file)
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(stream, "w") as out:
        for member in source.namelist():
            out.writestr(member, source.read(member))
        out.writestr(".DS_Store", b"\x00")
        out.writestr("__MACOSX/._project.avc", b"\x00\x05\x16\x07")
        out.writestr("__MACOSX/scripts/._lights.py", b"\x00\x05\x16\x07")

    result, _copy = _imported(stream.getvalue(), name)

    assert result["warnings"] == []


def test_a_project_file_inside_another_tree_is_not_another_project(library, tmp_path):
    project_file = _project_folder(tmp_path / "room")
    content, name, _type = project_library.export_active_project(project_file)
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(stream, "w") as out:
        for member in source.namelist():
            out.writestr(member, source.read(member))
        out.writestr("ui/meter/sample.avc", b"{}")
        out.writestr("assets/sample.avc", b"{}")

    result, _copy = _imported(stream.getvalue(), name)

    assert result["warnings"] == []


@pytest.mark.parametrize("tree", ["plugins/acme_plugin/examples", "drivers/examples", "driver_repo", "plugin_repo/acme"])
def test_a_project_file_a_bundle_or_the_system_holds_is_not_another_project(tree):
    assert not import_left_out.is_project_file(f"{tree}/demo.avc")
    assert import_left_out.left_out(["project.avc", f"{tree}/demo.avc"], "project.avc") == []
