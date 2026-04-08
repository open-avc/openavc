"""
System configuration management for OpenAVC.

Implements the layered config system per Implementation Design Section 10.4:
  defaults -> system.json -> environment variables

system.json is stored in the data directory and persists across application updates.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Application root (the openavc/ repo directory)
APP_DIR = Path(__file__).resolve().parent.parent

# Default system.json schema
DEFAULTS: dict[str, Any] = {
    "network": {
        "http_port": 8080,
        "bind_address": "127.0.0.1",
    },
    "auth": {
        "programmer_password": "",
        "api_key": "",
        "panel_lock_code": "",
    },
    "isc": {
        "enabled": True,
        "discovery_enabled": True,
        "auth_key": "",
    },
    "logging": {
        "level": "info",
        "file_enabled": True,
        "max_size_mb": 50,
        "max_files": 5,
    },
    "updates": {
        "check_enabled": True,
        "channel": "stable",
        "auto_check_interval_hours": 1,
        "auto_backup_before_update": True,
        "notify_only": False,
    },
    "cloud": {
        "enabled": False,
        "endpoint": "wss://cloud.openavc.com/agent/v1",
        "system_key": "",
        "system_id": "",
    },
    "kiosk": {
        "enabled": False,
        "target_url": "http://localhost:8080/panel",
        "cursor_visible": False,
    },
}

# Mapping: (section, key) -> (env_var, type)
ENV_OVERRIDES: dict[tuple[str, str], tuple[str, type]] = {
    ("network", "http_port"): ("OPENAVC_PORT", int),
    ("network", "bind_address"): ("OPENAVC_BIND", str),
    ("auth", "programmer_password"): ("OPENAVC_PROGRAMMER_PASSWORD", str),
    ("auth", "api_key"): ("OPENAVC_API_KEY", str),
    ("auth", "panel_lock_code"): ("OPENAVC_PANEL_LOCK_CODE", str),
    ("logging", "level"): ("OPENAVC_LOG_LEVEL", str),
    ("updates", "check_enabled"): ("OPENAVC_UPDATE_CHECK", bool),
    ("updates", "channel"): ("OPENAVC_UPDATE_CHANNEL", str),
    ("cloud", "enabled"): ("OPENAVC_CLOUD_ENABLED", bool),
    ("cloud", "endpoint"): ("OPENAVC_CLOUD_ENDPOINT", str),
    ("cloud", "system_key"): ("OPENAVC_CLOUD_SYSTEM_KEY", str),
    ("cloud", "system_id"): ("OPENAVC_CLOUD_SYSTEM_ID", str),
}


def _is_dev_environment() -> bool:
    """Detect if running from a source/development checkout."""
    return (APP_DIR / "pyproject.toml").exists() and (APP_DIR / "server").is_dir()


def _is_docker() -> bool:
    """Detect if running inside a Docker container."""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists():
            text = cgroup.read_text(encoding="utf-8", errors="ignore")
            if "docker" in text or "containerd" in text:
                return True
    except OSError:
        pass
    return False


def get_data_dir() -> Path:
    """Determine the data directory.

    Priority:
    1. OPENAVC_DATA_DIR environment variable (explicit override)
    2. Docker: /data
    3. Development: ./data relative to repo root
    4. Platform default: Windows -> C:\\ProgramData\\OpenAVC, Linux -> /var/lib/openavc
    """
    env_dir = os.environ.get("OPENAVC_DATA_DIR")
    if env_dir:
        return Path(env_dir)

    if _is_docker():
        return Path("/data")

    if _is_dev_environment():
        return APP_DIR / "data"

    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "OpenAVC"

    return Path("/var/lib/openavc")


def get_log_dir() -> Path:
    """Determine the log directory.

    Priority:
    1. OPENAVC_LOG_DIR environment variable
    2. Docker: /data/logs
    3. Development: ./data/logs
    4. Platform default: Windows -> data_dir/logs, Linux -> /var/log/openavc
    """
    env_dir = os.environ.get("OPENAVC_LOG_DIR")
    if env_dir:
        return Path(env_dir)

    if _is_docker():
        return Path("/data/logs")

    if _is_dev_environment():
        return get_data_dir() / "logs"

    if sys.platform == "win32":
        return get_data_dir() / "logs"

    return Path("/var/log/openavc")


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursively for nested dicts.

    Only merges keys that exist in base (ignores unknown keys from file).
    """
    result = {}
    for key, default_value in base.items():
        if key in override:
            if isinstance(default_value, dict) and isinstance(override[key], dict):
                result[key] = _deep_merge(default_value, override[key])
            else:
                result[key] = override[key]
        else:
            result[key] = default_value
    return result


def _parse_env_value(raw: str, target_type: type) -> Any:
    """Parse an environment variable string to the target type."""
    if target_type is bool:
        return raw.lower() in ("true", "1", "yes")
    if target_type is int:
        try:
            return int(raw)
        except ValueError:
            log.warning("Invalid integer env var value: %r", raw)
            return None
    return raw


class SystemConfig:
    """Manages system.json configuration with layered overrides.

    Access values with: config.get("network", "http_port")
    Or get entire sections: config.section("network")
    """

    def __init__(self):
        self._data: dict[str, Any] = {}
        self._data_dir: Path = get_data_dir()
        self._log_dir: Path = get_log_dir()
        self._file_path: Path = self._data_dir / "system.json"
        self._loaded = False

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    @property
    def file_path(self) -> Path:
        return self._file_path

    @property
    def plugin_repo_path(self) -> Path:
        """Path to the plugin repository directory."""
        return APP_DIR / "plugin_repo"

    @property
    def driver_repo_path(self) -> Path:
        """Path to the driver repository directory."""
        return APP_DIR / "driver_repo"

    @property
    def themes_dir(self) -> Path:
        """Path to the themes directory."""
        return APP_DIR / "themes"

    def load(self) -> None:
        """Load configuration: defaults -> system.json -> env vars."""
        # Start with defaults
        import copy
        self._data = copy.deepcopy(DEFAULTS)

        # Layer: system.json (if it exists)
        if self._file_path.exists():
            try:
                file_data = json.loads(self._file_path.read_text(encoding="utf-8"))
                if isinstance(file_data, dict):
                    self._data = _deep_merge(self._data, file_data)
                    log.info("Loaded system config from %s", self._file_path)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to load system.json from %s: %s (using defaults)", self._file_path, e)
        else:
            log.info("No system.json found at %s (using defaults, will create on first save)", self._file_path)

        # Layer: environment variable overrides
        for (section, key), (env_var, target_type) in ENV_OVERRIDES.items():
            raw = os.environ.get(env_var)
            if raw is not None:
                parsed = _parse_env_value(raw, target_type)
                if parsed is not None:
                    self._data[section][key] = parsed

        self._loaded = True

    def save(self) -> None:
        """Write the current config (minus env overrides) to system.json."""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._file_path.write_text(
                json.dumps(self._data, indent=4) + "\n",
                encoding="utf-8",
            )
            log.info("Saved system config to %s", self._file_path)
        except OSError as e:
            log.error("Failed to save system.json to %s: %s", self._file_path, e)

    def ensure_file(self) -> None:
        """Create system.json with defaults if it doesn't exist."""
        if not self._file_path.exists():
            self.save()

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """Get a config value by section and key."""
        return self._data.get(section, {}).get(key, default)

    def section(self, name: str) -> dict[str, Any]:
        """Get an entire config section as a dict."""
        return dict(self._data.get(name, {}))

    def set(self, section: str, key: str, value: Any) -> None:
        """Set a config value (in memory only, call save() to persist)."""
        if section not in self._data:
            self._data[section] = {}
        self._data[section][key] = value

    def to_dict(self) -> dict[str, Any]:
        """Return the full config as a dict."""
        import copy
        return copy.deepcopy(self._data)


# Singleton instance
_instance: SystemConfig | None = None


def get_system_config() -> SystemConfig:
    """Get the singleton SystemConfig instance, loading on first access."""
    global _instance
    if _instance is None:
        _instance = SystemConfig()
        _instance.load()
    return _instance


def reset_system_config() -> None:
    """Reset the singleton (for testing)."""
    global _instance
    _instance = None
