"""The Windows installer cache after a confirmed update.

Every in-app update leaves its installer in ``update-cache``, and every
installer copies itself there too. Once the new version is confirmed running,
the cache keeps the two a rollback can use (the running version's and the one
the update came from) and deletes the rest, so it stops growing by one
installer per update, and Rollback offers the version the update came from.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openavc.updater import rollback
from openavc.updater.rollback import (
    confirm_startup,
    perform_rollback,
    read_pending_marker,
    rollback_target_version,
    write_pending_marker,
)


def _cache(data_dir: Path, *versions: str) -> Path:
    cache = data_dir / "update-cache"
    cache.mkdir(parents=True, exist_ok=True)
    for version in versions:
        (cache / f"OpenAVC-Setup-{version}.exe").write_bytes(version.encode())
    return cache


def _cached(data_dir: Path) -> list[str]:
    return sorted(p.name for p in (data_dir / "update-cache").iterdir())


def _confirm(data_dir: Path, running: str, platform: str = "win32") -> None:
    with patch("openavc.version.__version__", running), \
         patch("openavc.updater.rollback.sys") as mock_sys:
        mock_sys.platform = platform
        confirm_startup(data_dir)


def _rollback_target(data_dir: Path, running: str) -> str:
    with patch("openavc.version.__version__", running), \
         patch("openavc.updater.rollback.sys") as mock_sys, \
         patch("openavc.system_config.get_system_config",
               return_value=SimpleNamespace(data_dir=data_dir)):
        mock_sys.platform = "win32"
        return rollback_target_version(data_dir)


def test_a_confirmed_update_keeps_the_running_and_the_previous_installer(tmp_path):
    _cache(tmp_path, "0.19.3", "0.24.1", "0.38.0", "0.39.0", "0.40.0")
    write_pending_marker(tmp_path, "0.39.0", "0.40.0")

    _confirm(tmp_path, "0.40.0")

    assert read_pending_marker(tmp_path) is None
    assert _cached(tmp_path) == ["OpenAVC-Setup-0.39.0.exe", "OpenAVC-Setup-0.40.0.exe"]
    assert _rollback_target(tmp_path, "0.40.0") == "0.39.0"


def test_rollback_still_runs_the_previous_installer_afterwards(tmp_path):
    _cache(tmp_path, "0.38.0", "0.39.0", "0.40.0")
    write_pending_marker(tmp_path, "0.39.0", "0.40.0")
    _confirm(tmp_path, "0.40.0")

    with patch("openavc.updater.rollback.sys") as mock_sys, \
         patch("openavc.updater.rollback._launch_installer_via_scheduler",
               return_value=True) as launcher:
        mock_sys.platform = "win32"
        assert perform_rollback(tmp_path, from_version="0.39.0", to_version="0.40.0")
    assert launcher.call_args[0][0].name == "OpenAVC-Setup-0.39.0.exe"


def test_a_release_tag_that_differs_from_the_running_version_does_not_hide_the_target(tmp_path):
    """Tag 2.0.0, bundled version 2.0.1: the tag-named download is the running
    build under another name. Kept, it would be the newest installer that is
    not the running version, and Rollback would offer it instead of 1.0.0."""
    _cache(tmp_path, "0.9.0", "1.0.0", "2.0.0", "2.0.1")
    write_pending_marker(tmp_path, "1.0.0", "2.0.0")

    _confirm(tmp_path, "2.0.1")

    assert _cached(tmp_path) == ["OpenAVC-Setup-1.0.0.exe", "OpenAVC-Setup-2.0.1.exe"]
    assert _rollback_target(tmp_path, "2.0.1") == "1.0.0"


def test_without_the_previous_installer_the_newest_other_one_stays(tmp_path):
    """No installer for the version the update came from: keep the one
    Rollback already offered rather than leave it nothing to offer."""
    _cache(tmp_path, "0.9.0", "1.1.0", "2.0.0")
    write_pending_marker(tmp_path, "1.0.0", "2.0.0")

    _confirm(tmp_path, "2.0.0")

    assert _cached(tmp_path) == ["OpenAVC-Setup-1.1.0.exe", "OpenAVC-Setup-2.0.0.exe"]


def test_an_installer_newer_than_the_running_version_goes_too(tmp_path):
    """Rolled back from 0.41.0 earlier, then updated 0.40.0 -> 0.40.1: the
    0.41.0 installer is no rollback target, and keeping it would make Rollback
    offer a version newer than the running one."""
    _cache(tmp_path, "0.39.0", "0.40.0", "0.40.1", "0.41.0")
    write_pending_marker(tmp_path, "0.40.0", "0.40.1")

    _confirm(tmp_path, "0.40.1")

    assert _cached(tmp_path) == ["OpenAVC-Setup-0.40.0.exe", "OpenAVC-Setup-0.40.1.exe"]
    assert _rollback_target(tmp_path, "0.40.1") == "0.40.0"


def test_an_update_that_did_not_take_effect_deletes_nothing(tmp_path):
    _cache(tmp_path, "0.38.0", "0.39.0", "0.40.0")
    write_pending_marker(tmp_path, "0.39.0", "0.40.0")

    _confirm(tmp_path, "0.39.0")

    assert len(_cached(tmp_path)) == 3


def test_other_platforms_leave_the_folder_alone(tmp_path):
    _cache(tmp_path, "0.38.0", "0.39.0", "0.40.0")
    write_pending_marker(tmp_path, "0.39.0", "0.40.0")

    _confirm(tmp_path, "0.40.0", platform="linux")

    assert len(_cached(tmp_path)) == 3


def test_a_file_whose_name_carries_no_version_is_left_alone(tmp_path):
    cache = _cache(tmp_path, "0.38.0", "0.39.0", "0.40.0")
    (cache / "OpenAVC-Setup-copy.exe").write_bytes(b"someone's copy")
    write_pending_marker(tmp_path, "0.39.0", "0.40.0")

    _confirm(tmp_path, "0.40.0")

    assert "OpenAVC-Setup-copy.exe" in _cached(tmp_path)
    assert "OpenAVC-Setup-0.38.0.exe" not in _cached(tmp_path)


def test_a_file_that_cannot_be_deleted_stops_nothing(tmp_path):
    """An installer still running holds its file open on Windows. The rest
    still go, and the update is still confirmed."""
    _cache(tmp_path, "0.37.0", "0.38.0", "0.39.0", "0.40.0")
    write_pending_marker(tmp_path, "0.39.0", "0.40.0")
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == "OpenAVC-Setup-0.37.0.exe":
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)

    with patch.object(Path, "unlink", unlink):
        _confirm(tmp_path, "0.40.0")

    assert read_pending_marker(tmp_path) is None
    assert _cached(tmp_path) == [
        "OpenAVC-Setup-0.37.0.exe", "OpenAVC-Setup-0.39.0.exe", "OpenAVC-Setup-0.40.0.exe",
    ]


def test_a_failure_inside_the_trim_never_reaches_startup(tmp_path):
    _cache(tmp_path, "0.39.0", "0.40.0")
    write_pending_marker(tmp_path, "0.39.0", "0.40.0")

    with patch.object(rollback, "prune_installer_cache", side_effect=RuntimeError("boom")):
        _confirm(tmp_path, "0.40.0")

    assert read_pending_marker(tmp_path) is None


def test_no_cache_folder_is_fine(tmp_path):
    write_pending_marker(tmp_path, "0.39.0", "0.40.0")

    _confirm(tmp_path, "0.40.0")

    assert not (tmp_path / "update-cache").exists()
