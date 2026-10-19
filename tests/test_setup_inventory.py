"""Read-only source capture follows actual load paths without executing drivers."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from openavc.drivers import setup_inventory as inventory
from openavc.utils.spawn import CREATE_NO_WINDOW


def capture(root, *, library=None, bundles=None):
    return inventory.capture_source_inputs(
        primary_roots={"persistent": root},
        library_roots={"saved": library} if library else {}, bundles=bundles or {},
    )


def bundle(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            # ZipInfo normalizes backslashes on Windows. These fixtures include
            # literal malformed archive names, so preserve their written bytes.
            info = zipfile.ZipInfo(name)
            info.filename = info.orig_filename = name
            archive.writestr(info, data)


def test_flat_primary_selection_keeps_helpers_and_nested_entries_visible(tmp_path):
    root = tmp_path / "drivers"
    root.mkdir()
    primary = b'raise AssertionError("must never import")\n'
    for name in ("widget.py", "_helper.py", "widget_discovery.py", "widget_sim.py", "layout.avcdriver"):
        (root / name).write_bytes(primary)
    nested = root / "nested"
    nested.mkdir()
    (nested / "not_flat.py").write_bytes(b"not a flat primary")
    result = capture(root)
    assert result.report["source_count"] == 1
    assert result.source_bytes == {hashlib.sha256(primary).hexdigest(): primary}
    assert {Path(row["path"]).name for row in result.report["members"]} == {
        "widget.py", "_helper.py", "widget_discovery.py", "widget_sim.py", "layout.avcdriver", "nested"}
    assert result.report["installation_complete"] is False
    assert result.report["activation_authorized"] is False


def test_library_and_pending_bundle_keep_shadowed_sources_and_member_identity(tmp_path):
    root = tmp_path / "drivers"
    root.mkdir()
    (root / "widget.py").write_bytes(b"installed winner")
    library = tmp_path / "library"
    project = library / "inactive"
    project.mkdir(parents=True)
    bundle(project / "bundle.zip", [("drivers/nested/widget.py", b"later source"),
                                    ("drivers/widget_sim.py", b"helper"),
                                    ("scripts/not_driver.py", b"script")])
    pending = tmp_path / "pending.zip"
    bundle(pending, [("drivers/future.py", b"pending source")])
    result = capture(root, library=library, bundles={"pending": pending})
    assert result.report["source_count"] == 3
    assert set(result.source_bytes.values()) == {b"installed winner", b"later source", b"pending source"}
    row = next(row for row in result.report["members"] if row.get("name") == "drivers/nested/widget.py")
    assert row["destination_name"] == "widget.py" and row["index"] == 0
    assert row["archive_sha256"] == hashlib.sha256((project / "bundle.zip").read_bytes()).hexdigest()
    assert row["source_sha256"] == hashlib.sha256(b"later source").hexdigest()


def test_zero_is_only_supplied_root_capture_not_complete_installation(tmp_path):
    result = capture(tmp_path)
    assert result.report["source_count"] == 0 and result.source_bytes == {}
    assert result.report["scope"] == "supplied_locations"
    assert result.report["installation_complete"] is False
    with pytest.raises(ValueError, match="nonempty"):
        inventory.capture_source_inputs(primary_roots={}, library_roots={}, bundles={})
    with pytest.raises(FileNotFoundError):
        capture(tmp_path / "missing")


def test_linked_root_and_source_keep_aliases_without_modifying_originals(tmp_path):
    root = tmp_path / "actual"
    root.mkdir()
    outside = tmp_path / "external.py"
    outside.write_bytes(b"external source")
    (root / "linked.py").symlink_to(outside)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    result = capture(alias)
    row = result.report["members"][0]
    assert row["path"] == str(alias / "linked.py")
    assert row["location"]["resolved"] == str(outside.resolve())
    targets = {c["link_target"] for c in row["location"]["chain"] if "link_target" in c}
    # Windows readlink retains the extended-length prefix of its substitute
    # name. Capture must preserve that OS spelling, not rewrite the alias.
    assert targets == {os.readlink(root / "linked.py"), os.readlink(alias)}
    assert {Path(target).resolve() for target in targets} == {outside.resolve(), root.resolve()}
    assert (root / "linked.py").is_symlink() and outside.read_bytes() == b"external source"


@pytest.mark.parametrize("names", [
    ("drivers/a.py", "drivers/a.py"),
    ("drivers/one/a.py", "drivers/two/a.py"),
    ("drivers/A.py", "drivers/a.py"),
    ("drivers/a.py", "drivers/../a.py"),
    ("drivers/a.py", "drivers\\b.py"),
    ("drivers/a.py", "drivers/folder.py/"),
])
def test_ambiguous_archive_never_yields_a_partial_capture(tmp_path, names):
    root = tmp_path / "drivers"
    root.mkdir()
    archive = tmp_path / "ambiguous.zip"
    bundle(archive, [(name, b"source") for name in names])
    before = archive.read_bytes()
    with zipfile.ZipFile(archive) as written:
        assert [info.orig_filename for info in written.infolist()] == list(names)
    with pytest.raises(ValueError):
        capture(root, bundles={"pending": archive})
    assert archive.read_bytes() == before


@pytest.mark.parametrize("change", ["insert", "delete", "replace", "retarget"])
def test_recheck_detects_membership_and_link_changes(tmp_path, monkeypatch, change):
    root = tmp_path / "drivers"
    root.mkdir()
    (root / "a.py").write_bytes(b"first")
    (root / "b.py").write_bytes(b"second")
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    (alternate / "a.py").write_bytes(b"first")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    original = inventory._Capture.recheck
    def mutate(self):
        if change == "insert":
            (root / "new.py").write_bytes(b"new")
        elif change == "delete":
            (root / "b.py").unlink()
        elif change == "replace":
            (root / "a.py").write_bytes(b"changed")
        else:
            alias.unlink()
            alias.symlink_to(alternate, target_is_directory=True)
        original(self)
    monkeypatch.setattr(inventory._Capture, "recheck", mutate)
    with pytest.raises((ValueError, FileNotFoundError)):
        capture(alias)


def test_error_or_limit_does_not_become_empty(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_bytes(b"source")
    real_scandir = inventory.os.scandir
    def unreadable(path):
        if Path(path) == tmp_path:
            raise PermissionError("fixture root unavailable")
        return real_scandir(path)
    monkeypatch.setattr(inventory.os, "scandir", unreadable)
    with pytest.raises(PermissionError):
        capture(tmp_path)
    monkeypatch.setattr(inventory.os, "scandir", real_scandir)
    monkeypatch.setattr(inventory, "_MAX_ENTRIES", 0)
    with pytest.raises(ValueError, match="entry limit"):
        capture(tmp_path)


@pytest.mark.parametrize("kind", ["primary", "bundle"])
def test_bytes_cannot_borrow_an_earlier_location_identity(tmp_path, monkeypatch, kind):
    root = tmp_path / "drivers"
    root.mkdir()
    path = tmp_path / "selected.zip" if kind == "bundle" else root / "selected.py"
    replacement = tmp_path / "replacement"
    if kind == "bundle":
        bundle(path, [("drivers/a.py", b"original")])
        bundle(replacement, [("drivers/a.py", b"replacement")])
    else:
        path.write_bytes(b"original")
        replacement.write_bytes(b"replacement")
    saved = tmp_path / "held"
    before = path.read_bytes()
    read = inventory._read_regular
    def substitute(file, **kwargs):
        if file != path:
            return read(file, **kwargs)
        path.rename(saved)
        replacement.rename(path)
        try:
            return read(file, **kwargs)
        finally:
            path.rename(replacement)
            saved.rename(path)
    monkeypatch.setattr(inventory, "_read_regular", substitute)
    with pytest.raises(ValueError, match="captured location"):
        capture(root, bundles={"pending": path} if kind == "bundle" else None)
    assert path.read_bytes() == before


def test_capture_import_has_no_loader_configuration_or_service_side_effect(tmp_path):
    code = """
import sys
from pathlib import Path
from openavc.drivers.setup_inventory import capture_source_inputs
result = capture_source_inputs(primary_roots={'persistent': Path(sys.argv[1])}, library_roots={}, bundles={})
assert result.report['source_count'] == 0
assert not {'openavc.system_config', 'openavc.drivers.driver_loader', 'openavc.core.engine'} & sys.modules.keys()
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                            timeout=10, creationflags=CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
