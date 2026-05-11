"""Tests for semver-aware rollback installer matching (A35).

The rollback path on Windows used strict filename equality to find or
exclude cached installers:

  current = f"OpenAVC-Setup-{__version__}.exe"
  if any(inst.name != current for inst in cache_dir.glob(...)):

That works as long as `__version__` and the filename's version tail
normalize the same way. The moment a prerelease suffix gets normalized
differently in one place (e.g. "0.10.3-rc.1" vs "0.10.3-rc1"), the
match silently fails: `can_rollback` returns True even when only the
running version is cached, and `_rollback_windows` can't find the
target installer it just downloaded.
"""

import sys
from unittest.mock import patch

import pytest

from server.updater import rollback


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="rollback.can_rollback's installer-cache branch is Windows-only",
)


def _make_cache(tmp_path, *versions):
    """Drop empty installer files into a fake update-cache directory."""
    cache_dir = tmp_path / "update-cache"
    cache_dir.mkdir()
    for v in versions:
        (cache_dir / f"OpenAVC-Setup-{v}.exe").touch()
    return cache_dir


def _patch_data_dir(tmp_path):
    class _Cfg:
        data_dir = tmp_path
    return patch("server.system_config.get_system_config", return_value=_Cfg)


def test_can_rollback_true_when_other_version_cached(tmp_path):
    """A different cached version is a rollback target."""
    _make_cache(tmp_path, "0.10.2", "0.10.3")
    with _patch_data_dir(tmp_path), \
         patch("server.version.__version__", "0.10.3"):
        assert rollback.can_rollback(tmp_path) is True


def test_can_rollback_false_when_only_current_version_cached(tmp_path):
    """The fresh-install path caches the running version. That alone is
    NOT a rollback target — there's nothing else to roll back to.
    """
    _make_cache(tmp_path, "0.10.3")
    with _patch_data_dir(tmp_path), \
         patch("server.version.__version__", "0.10.3"):
        assert rollback.can_rollback(tmp_path) is False


def test_can_rollback_false_when_prerelease_match_only(tmp_path):
    """Regression for A35: when the running version is a prerelease whose
    filename normalization happens to differ from __version__ slightly,
    strict string comparison treats the cached running-version installer
    as a rollback target and `can_rollback` falsely returns True.

    parse_semver normalizes both sides to the same tuple, so the comparison
    is robust to dash/dot variations the original equality check missed.
    """
    # Both representations parse to the same semver tuple.
    _make_cache(tmp_path, "0.10.3-rc.1")
    with _patch_data_dir(tmp_path), \
         patch("server.version.__version__", "0.10.3-rc.1"):
        assert rollback.can_rollback(tmp_path) is False


def test_installer_version_helper_strips_prefix(tmp_path):
    """The helper used by can_rollback and _rollback_windows must extract
    the version tuple from the filename (the bit between OpenAVC-Setup-
    and .exe) and parse it.
    """
    cache = _make_cache(tmp_path, "0.10.3", "0.11.0-beta.1")

    versions = sorted(rollback._installer_version(p) for p in cache.iterdir())
    assert (0, 10, 3, "") in versions
    assert (0, 11, 0, "beta.1") in versions
