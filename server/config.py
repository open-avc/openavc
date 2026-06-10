"""
OpenAVC system configuration.

Reads settings from system.json with environment variable overrides.
Backward-compatible module-level constants for existing code that does:
    from server import config
    config.HTTP_PORT
"""

import os

from server.system_config import get_system_config, get_data_dir, APP_DIR

# Paths
BASE_DIR = APP_DIR
# The default project lives in the data directory with the rest of the
# persistent user data (driver_repo, plugin_repo, saved_projects). Packaged
# deployments set OPENAVC_PROJECT explicitly; this default covers development
# checkouts and bare runs. Engine startup migrates the pre-data_dir location
# (APP_DIR/projects) via migrate_legacy_project_dir().
PROJECT_PATH = os.environ.get(
    "OPENAVC_PROJECT", str(get_data_dir() / "projects" / "default" / "project.avc")
)

# Project Library (saved project files — lives in the data directory, not the app directory,
# because the app directory may be read-only in production deployments)
SAVED_PROJECTS_DIR = get_data_dir() / "saved_projects"


def _safe_int(env_key: str, default: int) -> int:
    """Parse an integer env var with fallback on invalid values."""
    raw = os.environ.get(env_key, str(default))
    try:
        return int(raw)
    except ValueError:
        import logging
        logging.getLogger(__name__).warning(f"Invalid value for {env_key}: {raw!r}, using default {default}")
        return default


# --- Values sourced from system.json + env overrides ---

def _load_config_values():
    """Load config values from SystemConfig. Called at module import time."""
    cfg = get_system_config()
    return cfg

_cfg = _load_config_values()

# Network
HTTP_PORT: int = _cfg.get("network", "http_port", 8080)
BIND_ADDRESS: str = _cfg.get("network", "bind_address", "0.0.0.0")
# Only honor X-Forwarded-For when explicitly fronted by a trusted reverse
# proxy. Default False: use the real TCP peer so the client IP can't be
# spoofed to bypass rate limiting or the localhost exemption.
TRUST_FORWARDED_FOR: bool = _cfg.get("network", "trust_forwarded_for", False)

# HTTPS / TLS (DEFAULTS owns the truth — no fallback args)
TLS_ENABLED: bool = _cfg.get("tls", "enabled")
TLS_PORT: int = _cfg.get("tls", "port")
TLS_AUTO_GENERATE: bool = _cfg.get("tls", "auto_generate")
TLS_CERT_FILE: str = _cfg.get("tls", "cert_file")
TLS_KEY_FILE: str = _cfg.get("tls", "key_file")
TLS_REDIRECT_HTTP: bool = _cfg.get("tls", "redirect_http")

# Logging
LOG_LEVEL: str = _cfg.get("logging", "level", "info")

# Authentication (all empty = fully open, backward compatible)
PROGRAMMER_PASSWORD: str = _cfg.get("auth", "programmer_password", "")
API_KEY: str = _cfg.get("auth", "api_key", "")
PANEL_LOCK_CODE: str = _cfg.get("auth", "panel_lock_code", "")

# Inter-System Communication
ISC_ENABLED: bool = _cfg.get("isc", "enabled", True)

# mDNS Service Advertisement
MDNS_ADVERTISE: bool = _cfg.get("discovery", "advertise", True)

# Rate Limiting (not in system.json, env-only for now)
RATE_LIMIT_ENABLED = os.environ.get("OPENAVC_RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_OPEN_PER_MINUTE = _safe_int("OPENAVC_RATE_LIMIT_OPEN", 120)
RATE_LIMIT_STANDARD_PER_MINUTE = _safe_int("OPENAVC_RATE_LIMIT_STANDARD", 60)
RATE_LIMIT_STRICT_PER_MINUTE = _safe_int("OPENAVC_RATE_LIMIT_STRICT", 10)

# Cloud Agent
CLOUD_ENABLED: bool = _cfg.get("cloud", "enabled", False)
CLOUD_ENDPOINT: str = _cfg.get("cloud", "endpoint", "wss://cloud.openavc.com/agent/v1")
CLOUD_SYSTEM_KEY: str = _cfg.get("cloud", "system_key", "")
CLOUD_SYSTEM_ID: str = _cfg.get("cloud", "system_id", "")
CLOUD_HEARTBEAT_INTERVAL = _safe_int("OPENAVC_CLOUD_HEARTBEAT_INTERVAL", 30)
CLOUD_STATE_BATCH_INTERVAL = _safe_int("OPENAVC_CLOUD_STATE_BATCH_INTERVAL", 2)


def get_config():
    """Get the SystemConfig singleton. Used by modules that need the full config object."""
    return get_system_config()
