"""Tests for cloud config persistence (server/cloud/config.py).

The cloud config file holds the system master key — the root credential for
the cloud trust boundary — so it must be written owner-only (0600) on POSIX.
The current atomic write (mkstemp + os.replace) already yields 0600 as a side
effect of mkstemp, and save_cloud_config now sets it explicitly; this pins the
property so a future refactor away from mkstemp (e.g. a plain write, which
would default to 0644 under a typical umask) can't silently loosen it.
"""

import json
import os
import stat

import pytest

import server.config as cfg
from server.cloud import config as cloud_config


@pytest.fixture
def cloud_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "PROJECT_PATH", str(tmp_path / "project.avc"))
    return tmp_path


@pytest.mark.skipif(os.name != "posix", reason="POSIX file-mode check")
def test_save_cloud_config_is_owner_only(cloud_config_dir):
    cloud_config.save_cloud_config(
        {"enabled": True, "system_key": "SECRET", "system_id": "abc"}
    )
    path = cloud_config._config_path()
    assert path.exists()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file-mode check")
def test_save_cloud_config_tightens_preexisting_loose_file(cloud_config_dir):
    """Overwriting an existing world/group-readable cloud.json ends up 0600."""
    path = cloud_config._config_path()
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o644)
    cloud_config.save_cloud_config({"enabled": True, "system_key": "K"})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_save_cloud_config_round_trips(cloud_config_dir):
    data = {"enabled": True, "system_key": "K", "system_id": "id-1"}
    cloud_config.save_cloud_config(data)
    loaded = cloud_config.load_cloud_config()
    assert loaded == data
    # File is valid JSON on disk.
    assert json.loads(cloud_config._config_path().read_text(encoding="utf-8")) == data
