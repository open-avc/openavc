"""The rules for a project's ``assets/`` tree, tested where they live.

The tree is flat in practice, so nothing here is exotic — which is the point.
Four of the six paths that move a project agreed the tree was flat and
flattened or dropped anything nested, while save, duplicate and export carried
it, so a subfolder survived a duplicate and then silently did not exist in the
next backup (backlog §139). One module owns the answer now; these are its
rules, and the door-level round trips live beside the doors.
"""

import io
import zipfile
from pathlib import Path

import pytest

from openavc.core import asset_tree


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


def test_collection_skips_a_symlink(tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not ours")
    (assets / "link.png").symlink_to(outside)

    assert asset_tree.zip_entries(assets) == []


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


def test_extract_skips_a_dotfile_and_a_dot_folder(tmp_path: Path):
    zf = _zip({
        "assets/.DS_Store": b"junk",
        "assets/.hidden/thing.png": b"junk",
        "assets/logo.png": b"fine",
    })
    written = asset_tree.extract_from_zip(zf, tmp_path / "assets")
    assert written == ["logo.png"]


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="platform has no symlinks"
)
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
