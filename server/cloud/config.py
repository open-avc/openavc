"""Cloud config persistence — stores pairing info to survive restarts."""

import json
import logging
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
    """Save cloud config to disk."""
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
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
