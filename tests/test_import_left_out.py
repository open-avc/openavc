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


EVERY_FILE = {
    "project.avc", "scripts/lights.py", "assets/logos/house.png", "ui/meter/index.html",
    "themes/house.json",
}


def test_an_update_backup_brings_its_project_with_the_files_beside_it(library, tmp_path):
    data_dir = tmp_path / "data"
    _project_folder(data_dir / "projects" / "default")
    (data_dir / "system.json").write_text("{}")
    (data_dir / "device_logins.json").write_text("{}")
    backup = update_backup(data_dir, "0.41.0")

    result, copy = _imported(backup.read_bytes(), backup.name)

    assert _copy_holds(copy) == EVERY_FILE
    assert result["warnings"] == [
        "The saved variable values and the device logins in this file were left out.",
    ]


def test_an_update_backup_kept_outside_the_projects_folder_brings_its_files_the_same(library, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    project_file = _project_folder(tmp_path / "elsewhere" / "room")
    backup = update_backup(data_dir, "0.41.0", project_path=project_file)

    result, copy = _imported(backup.read_bytes(), backup.name)

    assert _copy_holds(copy) == EVERY_FILE
    assert result["warnings"] == ["The saved variable values in this file were left out."]


def test_an_update_backup_kept_at_the_data_root_brings_its_files_and_not_the_systems(library, tmp_path):
    data_dir = tmp_path / "data"
    project_file = _project_folder(data_dir)
    (data_dir / "system.json").write_text("{}")
    (data_dir / "cloud.json").write_text("{}")
    backup = update_backup(data_dir, "0.41.0", project_path=project_file)

    result, copy = _imported(backup.read_bytes(), backup.name)

    assert _copy_holds(copy) == EVERY_FILE
    assert result["warnings"] == ["The saved variable values in this file were left out."]


def test_the_project_that_ran_is_taken_over_one_left_in_the_projects_folder(library, tmp_path):
    data_dir = tmp_path / "data"
    _project_folder(data_dir / "projects" / "default", "starter_room")
    project_file = _project_folder(tmp_path / "elsewhere" / "room")
    (project_file.parent / "scripts" / "lights.py").write_text("ran = True\n")
    backup = update_backup(data_dir, "0.41.0", project_path=project_file)

    result, copy = _imported(backup.read_bytes(), backup.name)

    assert result["id"] == "acme_room"
    assert (copy / "scripts" / "lights.py").read_text() == "ran = True\n"
    assert result["warnings"][-1] == (
        "This file holds more than one project. Only external-project/project.avc was imported; "
        "projects/default/project.avc was left out."
    )


def test_another_projects_files_are_not_brought_with_it(library, tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zf:
        zf.writestr("projects/a/project.avc", json.dumps(PROJECT))
        zf.writestr("projects/a/scripts/own.py", "x = 1\n")
        zf.writestr("projects/b/project.avc", json.dumps({**PROJECT, "project": {"id": "b_room", "name": "B"}}))
        zf.writestr("projects/b/scripts/other.py", "x = 2\n")
        zf.writestr("projects/b/assets/other.png", b"\x89PNG\r\n\x1a\n")
        zf.writestr("scripts/top.py", "x = 3\n")

    result, copy = _imported(stream.getvalue(), "two.zip")

    assert result["id"] == "acme_room"
    assert _copy_holds(copy) == {"project.avc", "scripts/own.py"}


def test_a_path_out_of_the_folder_is_never_written(library, tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zf:
        zf.writestr("room/project.avc", json.dumps(PROJECT))
        zf.writestr("room/assets/../../../escaped.png", b"\x89PNG\r\n\x1a\n")
        zf.writestr("room/ui/../../../escaped.html", "<div></div>")
        zf.writestr("room/themes/../../escaped.json", "{}")
        zf.writestr("room/scripts/C:evil.py", "x = 1\n")

    result, copy = _imported(stream.getvalue(), "room.zip")

    assert _copy_holds(copy) == {"project.avc"}
    assert not [p for p in tmp_path.rglob("escaped*")]


def test_an_update_backup_comes_in_through_the_librarys_import(library, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openavc.api.routes import project as project_routes

    data_dir = tmp_path / "data"
    _project_folder(data_dir / "projects" / "default")
    backup = update_backup(data_dir, "0.41.0")
    app = FastAPI()
    app.include_router(project_routes.router, prefix="/api")

    answer = TestClient(app).post("/api/library/import", files={"file": (backup.name, backup.read_bytes())})

    assert answer.status_code == 200, answer.text
    assert answer.json()["warnings"] == ["The saved variable values in this file were left out."]
    assert _copy_holds(project_library._lib_dir() / "acme_room") == EVERY_FILE


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
