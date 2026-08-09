"""The rollback prompt has to name the version it is offering.

Rollback availability and the rollback TARGET are answered separately: a blank
target never means "cannot roll back". But a confirmation dialog reading
"Rollback to v?" is a poor thing to ask someone to agree to, and macOS returned
exactly that — the lookup was deferred while the .app payload layout was still
moving, and the deferral outlived the reason for it.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from openavc.updater.rollback import _version_from_pyproject, rollback_target_version

PYPROJECT = '[project]\nname = "openavc"\nversion = "0.24.1"\n'


def _write(path: Path, text: str = PYPROJECT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestVersionFromPyproject:
    def test_reads_the_recorded_version(self, tmp_path):
        assert _version_from_pyproject(_write(tmp_path / "pyproject.toml")) == "0.24.1"

    def test_missing_file_is_blank_not_an_error(self, tmp_path):
        # The caller reports availability separately, so "" must stay a label
        # problem and never escalate into a failed rollback.
        assert _version_from_pyproject(tmp_path / "nope.toml") == ""

    def test_malformed_file_is_blank_not_an_error(self, tmp_path):
        assert _version_from_pyproject(_write(tmp_path / "pyproject.toml", "not [ toml")) == ""

    def test_absent_version_key_is_blank(self, tmp_path):
        text = '[project]\nname = "openavc"\n'
        assert _version_from_pyproject(_write(tmp_path / "pyproject.toml", text)) == ""


class TestMacOSRollbackTarget:
    """The regression: darwin returned "" unconditionally."""

    def _bundle(self, tmp_path: Path) -> Path:
        # /Applications/OpenAVC.app + the .previous snapshot beside it, with the
        # frozen payload where the real bundle carries it.
        app = tmp_path / "OpenAVC.app"
        (app / "Contents" / "Resources" / "server").mkdir(parents=True)
        prev = tmp_path / "OpenAVC.app.previous"
        _write(prev / "Contents" / "Resources" / "server" / "_internal" / "pyproject.toml")
        # app_dir is the frozen payload dir inside the bundle, which is what
        # _macos_previous_bundle walks up from.
        return app / "Contents" / "Resources" / "server" / "_internal"

    def test_names_the_version_in_the_previous_bundle(self, tmp_path):
        app_dir = self._bundle(tmp_path)
        app_dir.mkdir(parents=True, exist_ok=True)
        with patch("openavc.updater.rollback.sys.platform", "darwin"):
            assert rollback_target_version(app_dir) == "0.24.1"

    def test_blank_when_there_is_no_previous_bundle(self, tmp_path):
        app = tmp_path / "OpenAVC.app"
        app_dir = app / "Contents" / "Resources" / "server" / "_internal"
        app_dir.mkdir(parents=True)
        with patch("openavc.updater.rollback.sys.platform", "darwin"):
            assert rollback_target_version(app_dir) == ""


class TestLinuxRollbackTarget:
    """Unchanged behaviour, pinned because both platforms now share one reader."""

    def test_reads_the_previous_install_tree(self, tmp_path):
        app_dir = tmp_path / "openavc"
        app_dir.mkdir()
        _write(tmp_path / "openavc.previous" / "pyproject.toml")
        with patch("openavc.updater.rollback.sys.platform", "linux"):
            assert rollback_target_version(app_dir) == "0.24.1"

    def test_blank_without_a_previous_tree(self, tmp_path):
        app_dir = tmp_path / "openavc"
        app_dir.mkdir()
        with patch("openavc.updater.rollback.sys.platform", "linux"):
            assert rollback_target_version(app_dir) == ""
