"""The update archives on Linux and macOS after a confirmed update.

Every in-app update there downloads its archive and the archive's ``.sig``
into ``update-cache``, and the root helper that applies it never deletes them.
A rollback on these platforms restores the ``.previous`` tree, never an
archive, so once the new version is confirmed running the archives go, except
one the helper will still apply: the archive ``apply-update.json`` or
``apply-update.retry`` names.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from openavc.updater import rollback
from openavc.updater.rollback import confirm_startup, read_pending_marker, write_pending_marker

LINUX_040 = "openavc-0.40.0-linux-arm64.tar.gz"
LINUX_041 = "openavc-0.41.0-linux-arm64.tar.gz"


def _cache(data_dir: Path, *names: str) -> Path:
    cache = data_dir / "update-cache"
    cache.mkdir(parents=True, exist_ok=True)
    for name in names:
        (cache / name).write_bytes(name.encode())
    return cache


def _cached(data_dir: Path) -> list[str]:
    return sorted(p.name for p in (data_dir / "update-cache").iterdir())


def _confirm(data_dir: Path, running: str = "0.41.0", platform: str = "linux") -> None:
    with patch("openavc.version.__version__", running), \
         patch("openavc.updater.rollback.sys") as mock_sys:
        mock_sys.platform = platform
        confirm_startup(data_dir)


def _record(data_dir: Path, name: str, archive: str) -> None:
    artifact = data_dir / "update-cache" / archive
    (data_dir / name).write_text(json.dumps({"artifact": str(artifact), "to_version": "0.42.0"}))


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_a_confirmed_update_deletes_every_archive_and_its_signature(tmp_path, platform):
    archives = (LINUX_040, LINUX_041, "openavc-0.41.0-macos-arm64.tar.gz", "update-0.41.0.tar.gz")
    _cache(tmp_path, *archives, *(a + ".sig" for a in archives))
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")

    _confirm(tmp_path, platform=platform)

    assert read_pending_marker(tmp_path) is None
    assert _cached(tmp_path) == []


@pytest.mark.parametrize("record", ["apply-update.retry", "apply-update.json"])
def test_an_archive_the_helper_will_still_apply_stays_with_its_signature(tmp_path, record):
    _cache(tmp_path, LINUX_040, LINUX_040 + ".sig", LINUX_041, LINUX_041 + ".sig",
           "openavc-0.42.0-linux-arm64.tar.gz", "openavc-0.42.0-linux-arm64.tar.gz.sig")
    _record(tmp_path, record, "openavc-0.42.0-linux-arm64.tar.gz")
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")

    _confirm(tmp_path)

    assert _cached(tmp_path) == [
        "openavc-0.42.0-linux-arm64.tar.gz", "openavc-0.42.0-linux-arm64.tar.gz.sig",
    ]


def test_an_archive_downloaded_during_this_start_stays(tmp_path):
    """A cloud-pushed update can start downloading while the engine starts,
    before the confirm. Its archive is newer than the pending marker, which
    every start touches first, and it is left to finish."""
    cache = _cache(tmp_path, LINUX_040)
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")
    marker_time = (tmp_path / "pending-update").stat().st_mtime
    downloading = cache / "openavc-0.42.0-linux-arm64.tar.gz"
    downloading.write_bytes(b"partial")
    os.utime(downloading, (marker_time + 5, marker_time + 5))

    _confirm(tmp_path)

    assert _cached(tmp_path) == ["openavc-0.42.0-linux-arm64.tar.gz"]


def test_a_record_that_cannot_be_read_leaves_every_archive(tmp_path):
    _cache(tmp_path, LINUX_040, LINUX_041)
    (tmp_path / "apply-update.retry").write_text("{not json")
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")

    _confirm(tmp_path)

    assert read_pending_marker(tmp_path) is None
    assert _cached(tmp_path) == [LINUX_040, LINUX_041]


def test_an_update_that_did_not_take_effect_deletes_nothing(tmp_path):
    _cache(tmp_path, LINUX_040, LINUX_041)
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")

    _confirm(tmp_path, running="0.40.0")

    assert _cached(tmp_path) == [LINUX_040, LINUX_041]


def test_what_is_not_an_update_archive_is_left_alone(tmp_path):
    _cache(tmp_path, LINUX_040, "notes.txt", "OpenAVC-Setup-0.40.0.exe", "openavc.tar")
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")

    _confirm(tmp_path)

    assert _cached(tmp_path) == ["OpenAVC-Setup-0.40.0.exe", "notes.txt", "openavc.tar"]


def test_windows_keeps_its_own_rule(tmp_path):
    """The installer trim is Windows' cache rule; it never reads archives."""
    _cache(tmp_path, LINUX_040)
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")

    _confirm(tmp_path, platform="win32")

    assert _cached(tmp_path) == [LINUX_040]


def test_an_archive_that_cannot_be_deleted_stops_nothing(tmp_path):
    _cache(tmp_path, LINUX_040, LINUX_041)
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == LINUX_040:
            raise PermissionError("owned by root")
        return real_unlink(self, *args, **kwargs)

    with patch.object(Path, "unlink", unlink):
        _confirm(tmp_path)

    assert read_pending_marker(tmp_path) is None
    assert _cached(tmp_path) == [LINUX_040]


def test_a_failure_inside_the_trim_never_reaches_startup(tmp_path):
    _cache(tmp_path, LINUX_040)
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")

    with patch.object(rollback, "prune_update_archives", side_effect=RuntimeError("boom"), create=True):
        _confirm(tmp_path)

    assert read_pending_marker(tmp_path) is None


def test_no_cache_folder_is_fine(tmp_path):
    write_pending_marker(tmp_path, "0.40.0", "0.41.0")

    _confirm(tmp_path)

    assert not (tmp_path / "update-cache").exists()
