"""Cloud config persistence — stores pairing info to survive restarts."""

import json
import logging
import os
import stat
import tempfile
from pathlib import Path

import server.config as cfg

log = logging.getLogger(__name__)

CLOUD_CONFIG_FILE = "cloud.json"


def _config_path() -> Path:
    """Get the cloud config file path (next to the project file)."""
    project_path = Path(cfg.PROJECT_PATH)
    return project_path.parent / CLOUD_CONFIG_FILE


def load_cloud_config() -> dict:
    """Load cloud config from disk. Returns empty dict if not found."""
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to load cloud config from %s: %s", path, e)
        return {}


def save_cloud_config(config: dict) -> None:
    """Save cloud config to disk (atomic write to prevent corruption).

    The file holds the system master key — the root credential for the cloud
    trust boundary — so it is written owner read/write only (0600) on POSIX,
    matching how TLS private keys are persisted (``server/tls.py``). ``mkstemp``
    already creates the temp file 0600 and ``os.replace`` preserves that, but
    the mode is set explicitly so the guarantee doesn't silently rest on that
    implementation detail (a future switch to a plain write would default to
    0644 under a typical umask). On Windows the data dir's ACL is user-only by
    inheritance, as for the TLS keys, so no chmod is applied.
    """
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix="cloud_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp_path, str(path))
        except BaseException:
            os.unlink(tmp_path)
            raise
        if os.name == "posix":
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError as exc:
                log.warning("Could not set 0600 mode on %s: %s", path, exc)
        log.info("Saved cloud config to %s", path)
    except OSError as e:
        log.error("Failed to save cloud config to %s: %s", path, e)
        raise


def apply_saved_cloud_config() -> None:
    """Apply saved cloud config to runtime config (called at startup)."""
    saved = load_cloud_config()
    if saved.get("enabled"):
        cfg.CLOUD_ENABLED = True
        cfg.CLOUD_ENDPOINT = saved.get("endpoint", cfg.CLOUD_ENDPOINT)
        cfg.CLOUD_SYSTEM_KEY = saved.get("system_key", "")
        cfg.CLOUD_SYSTEM_ID = saved.get("system_id", "")
        log.info("Applied saved cloud config (system_id=%s)", cfg.CLOUD_SYSTEM_ID)
