"""The files a project change writes and removes beside the project, on their
own: what ``put_back`` and ``settle`` leave, the change door aside (the door
and the two script doors are test_script_files_follow_the_change.py)."""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path

import pytest

from openavc.core.change_files import ChangeFiles


def test_a_write_that_is_put_back_removes_a_new_file(tmp_path):
    files = ChangeFiles()
    files.write(tmp_path / "scripts" / "new.py", "x = 1\n")

    files.put_back()

    assert not (tmp_path / "scripts" / "new.py").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_write_that_is_put_back_restores_the_bytes_and_permissions_it_replaced(tmp_path):
    path = tmp_path / "kept.py"
    path.write_bytes(b"kept = '\xe9'\r\n")
    os.chmod(path, 0o644)
    files = ChangeFiles()
    files.write(path, "x = 1\n")
    files.write(path, "x = 2\n")

    files.put_back()

    assert path.read_bytes() == b"kept = '\xe9'\r\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kept.py"]


def test_a_settled_write_stays(tmp_path):
    files = ChangeFiles()
    files.write(tmp_path / "new.py", "x = 1\n")

    files.settle()
    files.put_back()

    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "x = 1\n"


def test_a_removal_waits_for_settle_and_a_put_back_cancels_it(tmp_path):
    (tmp_path / "gone.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    settled, refused = ChangeFiles(), ChangeFiles()
    settled.remove(tmp_path / "gone.py")
    refused.remove(tmp_path / "kept.py")
    assert (tmp_path / "gone.py").exists()

    settled.settle()
    refused.put_back()
    refused.settle()

    assert sorted(p.name for p in tmp_path.iterdir()) == ["kept.py"]


def test_removing_a_file_already_gone_is_quiet(tmp_path, caplog):
    files = ChangeFiles()
    files.remove(tmp_path / "never.py")

    with caplog.at_level(logging.WARNING):
        files.settle()

    assert caplog.records == []


def test_a_removal_that_fails_once_saved_is_logged_and_the_rest_still_go(tmp_path, monkeypatch, caplog):
    (tmp_path / "stuck.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "gone.py").write_text("x = 1\n", encoding="utf-8")
    unlink = Path.unlink

    def refuse_one(self, missing_ok=False):
        if self.name == "stuck.py":
            raise PermissionError("read-only")
        unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_one)
    files = ChangeFiles()
    files.remove(tmp_path / "stuck.py")
    files.remove(tmp_path / "gone.py")

    with caplog.at_level(logging.WARNING):
        files.settle()

    assert sorted(p.name for p in tmp_path.iterdir()) == ["stuck.py"]
    assert "could not be removed" in caplog.text
