"""What each backup holds, run, against the table in ``docs/updates.md``.

A project is copied three ways: the backup an update or a rollback takes
first (``updater/backup``), a project backup (``core/backup_manager``) and an
export (``core/project_library``). Each is run here on one data folder
holding every file OpenAVC keeps there, with the project kept in each of the
three places it can be (the projects folder, the data folder's root, a folder
outside the data folder), and what it holds is held to the table row by
row. An automatic rollback's restore is then run on the update's backup and
held to the table's "put back". A file no row names is in no backup, and a
backup holding anything else, or one file twice, fails.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from openavc.core import backup_manager
from openavc.core.project_library import export_active_project
from openavc.updater.backup import create_backup, restore_user_data

DOC = Path(__file__).resolve().parents[1] / "docs" / "updates.md"
HEADING = "### What each backup holds"
COLUMNS = ("Before an update or rollback", "Project backup", "Export")
PRE_UPDATE, PROJECT_BACKUP, EXPORT = COLUMNS

PROJECT = json.dumps({"openavc_version": "0.18.0", "project": {"id": "acme_room", "name": "Acme Room"}})

#: Each row of the docs table, as the files it means: ``("project", path)``
#: below the project's folder, ``("data", path)`` below the data folder.
ROWS: dict[str, list[tuple[str, str]]] = {
    "Project file": [("project", "project.avc")],
    "Scripts": [("project", "scripts/lights.py")],
    "Assets": [("project", "assets/logos/house.png")],
    "Custom controls": [("project", "ui/meter/index.html")],
    "Custom themes": [("project", "themes/house.json")],
    "Saved variable values": [("project", "state.json")],
    "Device logins": [("data", "device_logins.json")],
    "Cloud pairing": [("project", "cloud.json")],
    "System settings (`system.json`)": [("data", "system.json")],
    "Approved panels": [("data", "panel_devices.json")],
    "Installed drivers and plugins": [
        ("data", "driver_repo/acme_widget.avcdriver"), ("data", "plugin_repo/acme_plugin/plugin.py"),
    ],
    "Project Library": [("data", "saved_projects/acme_other/project.avc")],
    "Plugin data": [("data", "plugin_data/acme_plugin/notes.json")],
    "The key the device logins are sealed with": [("data", "device_logins.key")],
    "HTTPS certificate": [("data", "tls/cert.pem"), ("data", "tls/ca.crt")],
    "Time zone": [("data", "time_zone.json")],
    "Update History": [("data", "update-history.json")],
}

#: A cell that is neither Yes nor No, outside the first column, and the test
#: that holds it: a project's logins need this system's login store, an
#: export's drivers and plugins a project that uses some.
HELD_ELSEWHERE = {
    ("Device logins", PROJECT_BACKUP): "tests/test_device_login_copies.py",
    ("Device logins", EXPORT): "tests/test_device_login_copies.py",
    ("Installed drivers and plugins", EXPORT): "tests/test_project_library.py",
}

#: Beside the project, in no row: the start's recovery record, the copy a
#: format upgrade keeps, and the instance id (the system's, like the
#: pairing). An update's backup carries them when it carries the project's
#: whole folder, and a rollback puts back all but the instance id.
BESIDE_NO_ROW = {"recovery.json": True, "project.avc.bak": True, ".instance_id": False}

#: The rest of what OpenAVC keeps in the data folder, in no backup.
DATA_NO_ROW = (
    "logs/server.log", "status/run-state.json", "update-cache/OpenAVC-0.0.1.tar.gz",
    "update-run.json", "staged-update.json", "apply-update.json", "push_ports.json",
    "plugin_ports.json", "device_sleep.json", "simulation.json", "ssh/known_hosts",
    "ssh/id_openavc", "audit_reports/acme.json", "problem_reports/acme.zip",
    "equipment-requests/acme.json", "priv-requests/acme.json", "priv-results/acme.json",
    "backups/pre-update-v0.0.1-20260101T000000Z.zip",
)

#: Project backups kept in the project's folder, in no backup.
PROJECT_NO_ROW = ("backups/backup_20260101_000000_manual.zip",)


@dataclass(frozen=True)
class Cell:
    carried: bool | None  # None: held elsewhere (HELD_ELSEWHERE)
    put_back: bool = False


def _table() -> dict[str, dict[str, Cell]]:
    """The docs table, read: each row's three cells."""
    text = DOC.read_text(encoding="utf-8")
    assert HEADING in text, f"{DOC.name} has no '{HEADING}' section"
    rows: list[str] = []
    for line in text.split(HEADING, 1)[1].splitlines():
        if line.startswith("|"):
            rows.append(line)
        elif rows:
            break
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    assert tuple(header[1:]) == COLUMNS
    table: dict[str, dict[str, Cell]] = {}
    for line in rows[2:]:
        label, *cells = (cell.strip() for cell in line.strip().strip("|").split("|"))
        table[label] = {column: _cell(label, column, cell) for column, cell in zip(COLUMNS, cells)}
    return table


def _cell(label: str, column: str, text: str) -> Cell:
    if text == "No":
        return Cell(False)
    if column == PRE_UPDATE:
        return Cell(True, put_back="put back" in text)
    if text == "Yes":
        return Cell(True)
    assert (label, column) in HELD_ELSEWHERE, f"{label!r} under {column!r} reads {text!r}: say Yes or No, or name its test"
    return Cell(None)


def test_every_row_of_the_table_is_run_here():
    assert list(_table()) == list(ROWS)


# --- the data folder ----------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Where the project is kept, and what each archive calls its folder."""

    name: str
    data: Path
    folder: Path
    prefix: str  # the update's backup's name for the project's folder

    @property
    def project(self) -> Path:
        return self.folder / "project.avc"

    def path(self, where: str, rel: str) -> Path:
        return (self.folder if where == "project" else self.data) / rel


def _layout(tmp_path: Path, name: str) -> Layout:
    data = tmp_path / "data"
    if name == "projects folder":
        return Layout(name, data, data / "projects" / "default", "projects/default/")
    if name == "data root":
        return Layout(name, data, data, "external-project/")
    return Layout(name, data, tmp_path / "room", "external-project/")


def _contents(where: str, rel: str) -> str:
    return PROJECT if rel == "project.avc" else f"{where}:{rel}"


def _fill(layout: Layout) -> None:
    every = [*(entry for entries in ROWS.values() for entry in entries),
             *(("project", rel) for rel in (*BESIDE_NO_ROW, *PROJECT_NO_ROW)),
             *(("data", rel) for rel in DATA_NO_ROW)]
    for where, rel in every:
        path = layout.path(where, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_contents(where, rel), encoding="utf-8")


LAYOUTS = ("projects folder", "data root", "outside the data folder")


@pytest.fixture(params=LAYOUTS)
def layout(request, tmp_path) -> Layout:
    made = _layout(tmp_path, request.param)
    _fill(made)
    return made


def _members(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        return zf.namelist()


def _expected(layout: Layout, column: str, project_name, data_name) -> list[str]:
    """What the table says the backup in ``column`` holds, each file named
    as that backup names it (``project_name`` / ``data_name`` of its path)."""
    table = _table()
    names = []
    for label, entries in ROWS.items():
        if table[label][column].carried:
            for where, rel in entries:
                names.append(project_name(rel) if where == "project" else data_name(rel))
    return names


def _assert_holds(members: list[str], expected: list[str]) -> None:
    assert len(members) == len(set(members)), "a file is in the backup twice"
    assert sorted(members) == sorted(expected)


# --- each backup --------------------------------------------------------------


def test_the_update_s_backup_holds_what_the_table_says(layout):
    backup = create_backup(layout.data, "0.7.0", project_path=layout.project)

    expected = _expected(layout, PRE_UPDATE, lambda rel: layout.prefix + rel, lambda rel: rel)
    if layout.name != "data root":  # the project's whole folder, everything in it
        expected += [layout.prefix + rel for rel in BESIDE_NO_ROW]
    else:  # the project's own entries, and the system's own files beside it
        expected.append(layout.prefix + ".instance_id")
    _assert_holds(_members(backup), expected)


def test_a_project_backup_holds_what_the_table_says(layout):
    backup = backup_manager.create_backup(layout.project, "manual")

    expected = _expected(layout, PROJECT_BACKUP, lambda rel: rel, lambda rel: f"<data>/{rel}")
    _assert_holds([m for m in _members(backup) if m != "backup_meta.json"], expected)


def test_an_export_holds_what_the_table_says(layout):
    content, _name, _type = export_active_project(layout.project)
    archive = layout.data.parent / "export.zip"
    archive.write_bytes(content)

    expected = _expected(layout, EXPORT, lambda rel: rel, lambda rel: f"<data>/{rel}")
    _assert_holds(_members(archive), expected)


# --- an automatic rollback ----------------------------------------------------


def test_a_rollback_puts_back_what_the_table_says(layout):
    backup = create_backup(layout.data, "0.7.0", project_path=layout.project)
    every = [*(entry for entries in ROWS.values() for entry in entries),
             *(("project", rel) for rel in BESIDE_NO_ROW), *(("data", rel) for rel in DATA_NO_ROW)]
    for where, rel in every:
        layout.path(where, rel).write_text("changed since the update", encoding="utf-8")

    assert restore_user_data(layout.data, backup, project_path=layout.project) is True

    table = _table()
    expected = {}
    for label, entries in ROWS.items():
        for where, rel in entries:
            expected[(where, rel)] = table[label][PRE_UPDATE].put_back
    for rel, put_back in BESIDE_NO_ROW.items():
        expected[("project", rel)] = put_back and layout.name != "data root"
    expected.update({("data", rel): False for rel in DATA_NO_ROW})
    for (where, rel), put_back in expected.items():
        found = layout.path(where, rel).read_text(encoding="utf-8")
        wanted = _contents(where, rel) if put_back else "changed since the update"
        assert found == wanted, f"{where}/{rel} after the rollback"


def test_no_cell_says_put_back_outside_the_update_s_backup():
    for label, cells in _table().items():
        for column in (PROJECT_BACKUP, EXPORT):
            assert not cells[column].put_back, label
    assert not re.search(r"\|\s*No, put back", DOC.read_text(encoding="utf-8"))
