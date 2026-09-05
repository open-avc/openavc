"""An upgraded box must not keep serving its old version's starter bundles.

Seeding is one-shot behind a `.seeded` marker, so every box that upgrades keeps
whatever `bundle.zip` its original version wrote. That went wrong across the
`server` -> `openavc` rename in 0.25.0: a box upgrading from 0.24.x kept 0.24.x's
bundles, whose drivers still `import server`. Opening any of the four built-in
starters then produced an orphaned device and a discovery-companion traceback —
while the correct drivers sat unused inside the installed package.

Two properties matter and they pull against each other:
  * the shipped bundle must win, or upgraded boxes stay broken; and
  * nothing the user edited may be touched, which is the promise
    `_install_bundled_drivers`' no-overwrite rule already makes.

Byte-identity against the OLD bundle is what reconciles them: it proves we wrote
the file and nobody has changed it.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from openavc.core import project_library


def _bundle(path: Path, driver_src: str, project: str = '{"project": {}}') -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("project.avc", project)
        zf.writestr("drivers/acme_widget.py", driver_src)


OLD = "from server.drivers.base import BaseDriver\n"
NEW = "from openavc.drivers.base import BaseDriver\n"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A seeded library + a shipped template dir + a driver_repo, all isolated."""
    seed = tmp_path / "templates"
    lib = tmp_path / "saved_projects"
    repo = tmp_path / "driver_repo"
    for d in (seed, lib, repo):
        d.mkdir()
    monkeypatch.setattr(project_library, "_SEED_DIR", seed)
    monkeypatch.setattr(project_library, "_lib_dir", lambda: lib)
    monkeypatch.setattr(project_library, "_DRIVER_REPO_DIR", repo)

    _bundle(seed / "simple_projector.zip", NEW)          # what this release ships
    (lib / "simple_projector").mkdir()
    _bundle(lib / "simple_projector" / "bundle.zip", OLD)  # what the old version left
    (lib / "simple_projector" / "project.avc").write_text('{"user": "edited"}')
    (lib / ".seeded").touch()                              # seeding is already done
    return seed, lib, repo


def _lib_driver(lib: Path) -> str:
    with zipfile.ZipFile(lib / "simple_projector" / "bundle.zip") as zf:
        return zf.read("drivers/acme_widget.py").decode()


def test_stale_bundle_is_replaced_by_the_shipped_one(wired) -> None:
    """The whole bug: an upgraded box keeps the old bundle forever."""
    _seed, lib, _repo = wired
    assert _lib_driver(lib) == OLD
    project_library.ensure_starter_projects()
    assert _lib_driver(lib) == NEW, (
        "the shipped bundle did not replace the stale one — an upgraded box "
        "would keep handing out drivers that import the old package"
    )


def test_a_users_edited_project_avc_is_never_touched(wired) -> None:
    """Only the bundle is refreshed. Content belongs to the user."""
    _seed, lib, _repo = wired
    project_library.ensure_starter_projects()
    assert (lib / "simple_projector" / "project.avc").read_text() == '{"user": "edited"}'


def test_an_unedited_unpacked_driver_is_replaced_by_the_fresh_one(wired) -> None:
    """`_install_bundled_drivers` never overwrites, so the stale copy already in
    driver_repo would win forever. It is byte-identical to the old bundle, which
    proves we put it there -- and the live project opened from this starter is
    still using it, so the current copy goes straight back. Removing it and
    leaving the reinstall to the next open orphaned every device on a box
    whose live project came from a starter, until somebody re-opened the
    starter from the library (and lost their edits doing so)."""
    _seed, _lib, repo = wired
    # write_bytes, not write_text: on Windows text mode rewrites "\n" as
    # "\r\n", so the copy would no longer be byte-identical to the bundle it
    # came from and the code would correctly read it as user-edited. The real
    # install path writes drivers with write_bytes for exactly this reason.
    (repo / "acme_widget.py").write_bytes(OLD.encode())
    project_library.ensure_starter_projects()
    assert (repo / "acme_widget.py").read_bytes() == NEW.encode(), (
        "the driver the live project uses is not the shipped copy; the box "
        "would boot with the device orphaned"
    )


def test_a_driver_the_new_bundle_no_longer_ships_is_not_put_back(wired) -> None:
    """Only what the shipped bundle carries comes back: a driver a release
    dropped stays dropped."""
    seed, _lib, repo = wired
    with zipfile.ZipFile(seed / "simple_projector.zip", "w") as zf:
        zf.writestr("project.avc", "{}")
        zf.writestr("drivers/acme_other.py", NEW)
    (repo / "acme_widget.py").write_bytes(OLD.encode())
    project_library.ensure_starter_projects()
    assert not (repo / "acme_widget.py").exists()


def test_a_driver_the_user_edited_is_left_alone(wired) -> None:
    """The counterweight. Differing bytes mean it is not ours to delete."""
    _seed, _lib, repo = wired
    mine = OLD + "# I changed this in the IDE\n"
    # Bytes here too, so the edit is the only thing that distinguishes this
    # file from the bundle's copy. Under write_text on Windows the newline
    # rewrite alone would differ, and this would pass without testing anything.
    (repo / "acme_widget.py").write_bytes(mine.encode())
    project_library.ensure_starter_projects()
    assert (repo / "acme_widget.py").read_bytes() == mine.encode()


def test_projects_this_release_does_not_ship_are_ignored(wired) -> None:
    """A user's own saved project must never be rewritten by seeding logic."""
    _seed, lib, _repo = wired
    mine = lib / "my_room"
    mine.mkdir()
    _bundle(mine / "bundle.zip", OLD)
    project_library.ensure_starter_projects()
    with zipfile.ZipFile(mine / "bundle.zip") as zf:
        assert zf.read("drivers/acme_widget.py").decode() == OLD


def test_refresh_is_idempotent_and_silent_when_already_current(wired) -> None:
    """Second start must be a no-op — it must not churn the file every boot."""
    _seed, lib, _repo = wired
    project_library.ensure_starter_projects()
    stamp = (lib / "simple_projector" / "bundle.zip").stat().st_mtime_ns
    project_library.ensure_starter_projects()
    assert (lib / "simple_projector" / "bundle.zip").stat().st_mtime_ns == stamp


def test_a_starter_the_user_deleted_is_not_resurrected(wired) -> None:
    """Refresh updates what is there; it never re-adds a project."""
    _seed, lib, _repo = wired
    import shutil as _sh
    _sh.rmtree(lib / "simple_projector")
    project_library.ensure_starter_projects()
    assert not (lib / "simple_projector").exists()
