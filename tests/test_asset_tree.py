"""The rules for a project's ``assets/`` tree, tested where they live.

The tree is flat in practice, so nothing here is exotic — which is the point.
Four of the six paths that move a project agreed the tree was flat and
flattened or dropped anything nested, while save, duplicate and export carried
it, so a subfolder survived a duplicate and then silently did not exist in the
next backup. One module owns the answer now; these are its
rules, and the door-level round trips live beside the doors.
"""

import io
import zipfile
from pathlib import Path

import pytest

from openavc.core import asset_tree
from tests import gates
from tests.helpers import folder_link_kinds, make_folder_link


def _zip(files: dict[str, bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return zipfile.ZipFile(buf)


# --- collecting the tree for an archive ------------------------------------


def test_collects_nested_files_with_their_paths(tmp_path: Path):
    assets = tmp_path / "assets"
    (assets / "rooms").mkdir(parents=True)
    (assets / "logo.png").write_bytes(b"top")
    (assets / "rooms" / "plan.png").write_bytes(b"nested")

    entries = asset_tree.zip_entries(assets)

    assert [name for name, _ in entries] == ["assets/logo.png", "assets/rooms/plan.png"]


def test_collects_nothing_from_a_missing_tree(tmp_path: Path):
    assert asset_tree.zip_entries(tmp_path / "assets") == []


@gates.skipif_missing(gates.SYMLINKS, gates.symlink_reason())
def test_collection_skips_a_symlink(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not ours")
    (assets / "link.png").symlink_to(outside)

    assert asset_tree.zip_entries(assets) == []


@pytest.mark.parametrize("kind", folder_link_kinds())
def test_collection_does_not_walk_into_a_linked_folder(tmp_path: Path, kind):
    """An export or a backup carries what is in the tree, not what a linked
    folder in it points to."""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "logo.png").write_bytes(b"ours")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(b"not ours")
    make_folder_link(kind, assets / "shared", outside)

    assert [name for name, _ in asset_tree.zip_entries(assets)] == ["assets/logo.png"]


# --- extracting the tree from an archive ------------------------------------


def test_extract_keeps_folders_and_reports_what_it_wrote(tmp_path: Path):
    zf = _zip({
        "assets/logo.png": b"top",
        "assets/rooms/plan.png": b"rooms",
        "assets/floors/plan.png": b"floors",
        "project.avc": b"{}",
    })
    dest = tmp_path / "assets"

    written = asset_tree.extract_from_zip(zf, dest)

    assert sorted(written) == ["floors/plan.png", "logo.png", "rooms/plan.png"]
    assert (dest / "rooms" / "plan.png").read_bytes() == b"rooms"
    assert (dest / "floors" / "plan.png").read_bytes() == b"floors"
    # Same basename in two folders: flattening destroys one of them.
    assert not (dest / "plan.png").exists()


def test_extract_ignores_everything_outside_the_prefix(tmp_path: Path):
    zf = _zip({"scripts/startup.py": b"print()", "assets/logo.png": b"top"})
    written = asset_tree.extract_from_zip(zf, tmp_path / "assets")
    assert written == ["logo.png"]


def test_extract_skips_a_traversal_member(tmp_path: Path):
    zf = _zip({"assets/../escaped.png": b"nope", "assets/logo.png": b"fine"})
    dest = tmp_path / "project" / "assets"

    written = asset_tree.extract_from_zip(zf, dest)

    assert written == ["logo.png"]
    assert not (tmp_path / "project" / "escaped.png").exists()
    assert not (tmp_path / "escaped.png").exists()


def test_extract_takes_only_the_types_the_upload_takes(tmp_path: Path):
    # An import, a restore and a seed all come through here. A page or a
    # script in assets/ would be served by the open asset route from this
    # server's own address; the upload never let one in.
    zf = _zip({
        "assets/logo.png": b"image",
        "assets/rooms/PLAN.JPG": b"image, upper case",
        "assets/chime.m4a": b"sound",
        "assets/page.html": b"<html></html>",
        "assets/rooms/run.js": b"alert(1)",
        "assets/backup.zip": b"PK",
        "assets/README": b"no extension",
    })

    written = asset_tree.extract_from_zip(zf, tmp_path / "assets")

    assert sorted(written) == ["chime.m4a", "logo.png", "rooms/PLAN.JPG"]


SAFE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="4"/></svg>'


def test_extract_gives_an_svg_the_check_the_upload_gives_it(tmp_path: Path):
    # Opened at its own URL, an SVG runs its script on this server's address.
    zf = _zip({
        "assets/logo.svg": SAFE_SVG,
        "assets/rooms/scripted.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        "assets/handler.SVG": b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        "assets/broken.svg": b"<svg",
    })

    written = asset_tree.extract_from_zip(zf, tmp_path / "assets")

    assert written == ["logo.svg"]
    assert (tmp_path / "assets" / "logo.svg").read_bytes() == SAFE_SVG


def test_svg_problem_answers_in_the_upload_s_words():
    assert asset_tree.svg_problem(SAFE_SVG) is None
    assert asset_tree.svg_problem(b"<svg") == "SVG is not valid XML."
    assert asset_tree.svg_problem(
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>a{}</style></svg>'
    ) == "SVG contains potentially unsafe content (scripts, styles, or event handlers)."


def test_no_type_the_upload_ever_took_is_refused_on_the_way_back_in():
    # Nothing has been taken off the upload's list, so a restore or an import
    # never refuses a file that some release let somebody upload. If a type
    # leaves the list, a project that holds one loses it on its next restore:
    # that is a decision, not a cleanup.
    first_release = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}
    sounds = {".mp3", ".wav", ".ogg", ".m4a"}
    assert asset_tree.ALLOWED_EXTENSIONS >= first_release | sounds


def test_extract_skips_a_dotfile_and_a_dot_folder(tmp_path: Path):
    zf = _zip({
        "assets/.DS_Store": b"junk",
        "assets/.hidden/thing.png": b"junk",
        "assets/logo.png": b"fine",
    })
    written = asset_tree.extract_from_zip(zf, tmp_path / "assets")
    assert written == ["logo.png"]


@gates.skipif_missing(gates.SYMLINKS, gates.symlink_reason())
def test_extract_will_not_write_through_a_symlinked_folder(tmp_path: Path):
    # The rule normalize-then-recheck exists for exactly this: the path is
    # clean, and the directory it lands in is not what it appears to be.
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "project" / "assets"
    dest.mkdir(parents=True)
    (dest / "rooms").symlink_to(outside, target_is_directory=True)

    zf = _zip({"assets/rooms/plan.png": b"escaped"})
    written = asset_tree.extract_from_zip(zf, dest)

    assert written == []
    assert not (outside / "plan.png").exists()


def test_extract_overwrites_rather_than_merging_into_a_stale_name(tmp_path: Path):
    dest = tmp_path / "assets"
    (dest / "rooms").mkdir(parents=True)
    (dest / "rooms" / "plan.png").write_bytes(b"old")

    asset_tree.extract_from_zip(_zip({"assets/rooms/plan.png": b"new"}), dest)

    assert (dest / "rooms" / "plan.png").read_bytes() == b"new"
