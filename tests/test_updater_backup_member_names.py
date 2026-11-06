"""The names a rollback restores from a pre-update backup, read the same on every platform.

The updater writes these archives itself, so a strange member name means
something else wrote the data folder. A rollback still places a member only
where its name says on every platform: on Windows ``D:x.py`` joined to a
folder is a path on drive D:, ``a\\..\\..\\x`` climbs out, and ``x.py:s`` is a
hidden stream, so a name holding a colon, a backslash or a NUL is skipped
wherever the rollback runs. OpenAVC never names a project file that way.
"""

import os
import zipfile
from pathlib import Path

import pytest

from openavc.updater.backup import _member_relpath, create_backup, restore_user_data
from tests.helpers import folder_link_kinds, make_folder_link

KINDS = folder_link_kinds()

# Each would land outside its folder, or somewhere hidden, on some platform.
UNSAFE = [
    "projects/D:x.py",
    "projects/default/notes.txt:hidden",
    "projects/default/scripts\\..\\..\\..\\x.py",
    "projects/default/n\x00ul.py",
    "projects/../x.py",
    "projects//etc/x.py",
]


def _room(folder: Path) -> Path:
    (folder / "scripts").mkdir(parents=True)
    (folder / "project.avc").write_text('{"openavc_version": "0.7.0"}', encoding="utf-8")
    (folder / "scripts" / "lights.py").write_text("# lights", encoding="utf-8")
    return folder


def _everything_below(root: Path) -> set[str]:
    """Every name under ``root`` as the file system lists it, without following a link."""
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        found.update(dirnames)
        found.update(filenames)
    return found


@pytest.mark.parametrize("name", UNSAFE)
def test_a_name_unsafe_on_any_platform_is_refused(name):
    assert _member_relpath(name, "projects/") is None


@pytest.mark.parametrize("name", [
    "projects/default/scripts/lights.py",
    "projects/My Room/assets/logo v2.png",
    "projects/default/.hidden-but-plain",
])
def test_an_ordinary_name_is_kept(name):
    assert _member_relpath(name, "projects/") == Path(name[len("projects/"):])


def test_a_rollback_restores_the_tree_and_skips_the_unsafe_names(tmp_path):
    data = tmp_path / "data"
    _room(data / "projects" / "default")
    backup = create_backup(data, "0.7.0")
    with zipfile.ZipFile(backup, "a") as zf:
        zf.writestr("projects/D:x.py", "# drive")
        zf.writestr("projects/default/notes.txt:hidden", "stream")
        zf.writestr("projects/default/scripts\\..\\..\\..\\x.py", "# climbs")

    assert restore_user_data(data, backup) is True

    live = data / "projects"
    assert (live / "default" / "project.avc").read_text(encoding="utf-8") == (
        '{"openavc_version": "0.7.0"}'
    )
    assert (live / "default" / "scripts" / "lights.py").exists()
    names = _everything_below(tmp_path)
    for written in ("D:x.py", "D", "notes.txt:hidden", "x.py", "scripts\\..\\..\\..\\x.py"):
        assert written not in names, f"{written!r} was restored"


@pytest.mark.parametrize("kind", KINDS)
def test_a_file_restored_through_a_link_stays_below_it(tmp_path, kind):
    data = tmp_path / "data"
    (data / "projects").mkdir(parents=True)
    room = _room(tmp_path / "elsewhere" / "room")
    make_folder_link(kind, data / "projects" / "default", room)
    backup = create_backup(data, "0.7.0")
    with zipfile.ZipFile(backup, "a") as zf:
        zf.writestr("projects/default/../../escape.txt", "climbed out of the link")
        zf.writestr("projects/default/D:x.py", "# drive")

    assert restore_user_data(data, backup) is True

    assert (room / "project.avc").read_text(encoding="utf-8") == '{"openavc_version": "0.7.0"}'
    names = _everything_below(tmp_path)
    assert "escape.txt" not in names
    assert "D:x.py" not in names
