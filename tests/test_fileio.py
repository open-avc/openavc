"""Tests for the shared atomic-write helper (server/utils/fileio.py)."""

import pytest

from server.utils.fileio import atomic_write_text


def test_writes_new_file(tmp_path):
    target = tmp_path / "theme.json"
    atomic_write_text(target, '{"id": "night"}')
    assert target.read_text(encoding="utf-8") == '{"id": "night"}'


def test_replaces_existing_file(tmp_path):
    target = tmp_path / "script.py"
    target.write_text("# old", encoding="utf-8")
    atomic_write_text(target, "# new")
    assert target.read_text(encoding="utf-8") == "# new"


def test_failed_write_leaves_original_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "script.py"
    target.write_text("# old", encoding="utf-8")

    import os as _os
    real_replace = _os.replace

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("server.utils.fileio.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "# new")
    monkeypatch.setattr("server.utils.fileio.os.replace", real_replace)

    # Original intact, no stray temp files.
    assert target.read_text(encoding="utf-8") == "# old"
    assert list(tmp_path.glob("*.tmp")) == []
