"""Module-level settings constants, and the sole home of the env-only ones.

Not a compatibility shim. This module has two distinct jobs, and knowing
which one a given constant is doing tells you where to add the next
setting:

**1. It owns the settings that never appear in system.json.** ``PROJECT_PATH``,
``SAVED_PROJECTS_DIR``, every ``RATE_LIMIT_*`` knob and the two cloud
intervals are defined here and nowhere else — environment variables and
derived paths, with no entry in ``system_config.DEFAULTS`` and no Settings
UI. **A new setting of that shape belongs here.**

**2. For everything else it is a flat mirror** of a ``system_config`` value,
read once at import. ``system_config.DEFAULTS`` owns those defaults outright,
which is why the reads below pass no fallback argument: ``load()`` seeds its
data from ``DEFAULTS``, so a fallback here could never fire and would only
state a second, silently divergent default. **A new user-editable setting
belongs in ``DEFAULTS``**, and gets a line here only if callers want the
constant form.

The mirror is a snapshot, and a mutable one — that is the sharp edge. Editing
settings at runtime updates the ``SystemConfig`` singleton, not these
constants, so the two views can disagree until restart. Code that must see a
live value calls ``get_system_config()``; code that writes a settings change
someone will read back through a constant has to update both, which is what
the cloud-pairing routes do to ``CLOUD_*``. That double-write is the reason
several modules import *both* this and ``system_config`` — not an accident to
be tidied away.

``tests/test_config_ownership.py`` enforces the split: env-only names stay
out of ``DEFAULTS``, and mirrored names stay in it with no local fallback.

    from openavc import config
    config.HTTP_PORT
"""

import os

from openavc.system_config import get_system_config, get_data_dir

# Paths
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


# --- Mirrors of system_config values (DEFAULTS owns every default below) ---

def _load_config_values():
    """Load config values from SystemConfig. Called at module import time."""
    cfg = get_system_config()
    return cfg

_cfg = _load_config_values()

# Network
HTTP_PORT: int = _cfg.get("network", "http_port")
BIND_ADDRESS: str = _cfg.get("network", "bind_address")
# Only honor X-Forwarded-For when explicitly fronted by a trusted reverse
# proxy. Default False: use the real TCP peer so the client IP can't be
# spoofed to bypass rate limiting or the localhost exemption.
TRUST_FORWARDED_FOR: bool = _cfg.get("network", "trust_forwarded_for")
# Port-80 convenience redirect (typed URLs drop the port). Best-effort bind.
PORT80_REDIRECT: bool = _cfg.get("network", "port80_redirect")

# HTTPS / TLS
TLS_ENABLED: bool = _cfg.get("tls", "enabled")
TLS_PORT: int = _cfg.get("tls", "port")
TLS_AUTO_GENERATE: bool = _cfg.get("tls", "auto_generate")
TLS_CERT_FILE: str = _cfg.get("tls", "cert_file")
TLS_KEY_FILE: str = _cfg.get("tls", "key_file")
TLS_REDIRECT_HTTP: bool = _cfg.get("tls", "redirect_http")
TLS_CLOUD_CERT: bool = _cfg.get("tls", "cloud_cert")

# Logging
LOG_LEVEL: str = _cfg.get("logging", "level")

# Authentication (all empty = fully open)
PROGRAMMER_PASSWORD: str = _cfg.get("auth", "programmer_password")
API_KEY: str = _cfg.get("auth", "api_key")
PANEL_LOCK_CODE: str = _cfg.get("auth", "panel_lock_code")

# Cloud Agent. These four are the ones the cloud-pairing routes write back to
# after a pair/unpair, so the constant and the singleton stay in step without
# a restart — see the module docstring.
CLOUD_ENABLED: bool = _cfg.get("cloud", "enabled")
CLOUD_ENDPOINT: str = _cfg.get("cloud", "endpoint")
CLOUD_SYSTEM_KEY: str = _cfg.get("cloud", "system_key")
CLOUD_SYSTEM_ID: str = _cfg.get("cloud", "system_id")


# --- Owned here: env-only, no system.json entry, no Settings UI ---
# (PROJECT_PATH and SAVED_PROJECTS_DIR at the top of the file are these too —
# they sit up there because they need no SystemConfig.)

# Rate Limiting
RATE_LIMIT_ENABLED = os.environ.get("OPENAVC_RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_OPEN_PER_MINUTE = _safe_int("OPENAVC_RATE_LIMIT_OPEN", 120)
RATE_LIMIT_STANDARD_PER_MINUTE = _safe_int("OPENAVC_RATE_LIMIT_STANDARD", 60)
RATE_LIMIT_CONTROL_PER_MINUTE = _safe_int("OPENAVC_RATE_LIMIT_CONTROL", 120)
RATE_LIMIT_STRICT_PER_MINUTE = _safe_int("OPENAVC_RATE_LIMIT_STRICT", 10)

# Cloud agent timing
CLOUD_HEARTBEAT_INTERVAL = _safe_int("OPENAVC_CLOUD_HEARTBEAT_INTERVAL", 30)
CLOUD_STATE_BATCH_INTERVAL = _safe_int("OPENAVC_CLOUD_STATE_BATCH_INTERVAL", 2)


def get_config():
    """Get the SystemConfig singleton. Used by modules that need the full config object."""
    return get_system_config()
