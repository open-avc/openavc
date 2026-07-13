"""Tests for save_project's on-disk safety guarantees.

save_project must never leave the project file in a broken state:

- a rolling ``.avc.bak`` crash-protection copy is taken before every overwrite
  (and only on overwrite — a first save has nothing to protect)
- if the backup copy fails, the save aborts and the original file is untouched
- the write itself is write-temp-then-rename, so a failure mid-write leaves the
  original file intact and no ``.avc.tmp`` file behind
"""

import json

import pytest

from server.core.project_loader import (
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
)


def _project(name: str) -> ProjectConfig:
    return ProjectConfig(project=ProjectMeta(id="t", name=name))


def _name_on_disk(path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["project"]["name"]


def _tmp_leftovers(directory) -> list[str]:
    return [p.name for p in directory.iterdir() if ".avc.tmp" in p.name]


def test_first_save_writes_file_without_bak(tmp_path):
    path = tmp_path / "p.avc"

    save_project(path, _project("One"))

    assert _name_on_disk(path) == "One"
    assert not path.with_suffix(".avc.bak").exists()
    assert _tmp_leftovers(tmp_path) == []


def test_overwrite_keeps_previous_content_in_bak(tmp_path):
    path = tmp_path / "p.avc"
    save_project(path, _project("One"))

    save_project(path, _project("Two"))

    assert _name_on_disk(path) == "Two"
    assert _name_on_disk(path.with_suffix(".avc.bak")) == "One"
    assert _tmp_leftovers(tmp_path) == []
    # The saved bytes are a loadable project, not just valid JSON.
    assert load_project(path).project.name == "Two"


def test_save_aborts_when_bak_copy_fails(tmp_path, monkeypatch):
    """If the crash-protection copy cannot be taken, the save must not
    proceed: better to reject the write than to overwrite the only good
    copy with no fallback."""
    path = tmp_path / "p.avc"
    save_project(path, _project("One"))
    original = path.read_text(encoding="utf-8")

    def failing_copy2(src, dst):
        raise OSError("simulated backup failure")

    monkeypatch.setattr(
        "server.core.project_loader.shutil.copy2", failing_copy2
    )

    with pytest.raises(OSError):
        save_project(path, _project("Two"))

    assert path.read_text(encoding="utf-8") == original
    assert _tmp_leftovers(tmp_path) == []


def test_failure_at_replace_leaves_original_and_cleans_tmp(tmp_path, monkeypatch):
    """A failure at the final rename must leave the original file exactly as
    it was and remove the temp file."""
    path = tmp_path / "p.avc"
    save_project(path, _project("One"))
    original = path.read_text(encoding="utf-8")

    def failing_replace(src, dst):
        raise OSError("simulated failure at swap")

    monkeypatch.setattr(
        "server.core.project_loader.os.replace", failing_replace
    )

    with pytest.raises(OSError):
        save_project(path, _project("Two"))

    assert path.read_text(encoding="utf-8") == original
    assert _tmp_leftovers(tmp_path) == []


def test_failure_during_write_leaves_original_and_cleans_tmp(tmp_path, monkeypatch):
    """A failure while writing the temp file (e.g. disk full) must leave the
    original file intact, close the temp fd, and remove the temp file."""
    path = tmp_path / "p.avc"
    save_project(path, _project("One"))
    original = path.read_text(encoding="utf-8")

    def failing_write(fd, data):
        raise OSError("simulated disk full")

    monkeypatch.setattr(
        "server.core.project_loader.os.write", failing_write
    )

    with pytest.raises(OSError):
        save_project(path, _project("Two"))

    assert path.read_text(encoding="utf-8") == original
    assert _tmp_leftovers(tmp_path) == []
