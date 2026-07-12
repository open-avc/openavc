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
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution — single source of truth for all deployment targets
# ---------------------------------------------------------------------------
# Three distinct concepts:
#   APP_DIR     — bundle/resource root (web assets, themes, built-in drivers).
#                 Dev: openavc/ repo root.  Frozen: _internal/ (sys._MEIPASS).
#                 NOT persistent on Docker (lives in the container image) and
#                 wiped by every Windows installer upgrade (_internal/).
#   INSTALL_DIR — installer artifact root (unins000.exe, nssm.exe)
#                 Dev: same as APP_DIR.  Frozen: exe's parent directory.
#   data_dir    — persistent user data root (projects, backups, plugin_repo,
#                 driver_repo, state). Always on writable persistent storage:
#                 a mounted volume on Docker, /var/lib/openavc on Linux,
#                 C:\ProgramData\OpenAVC on Windows. See get_data_dir().
# ---------------------------------------------------------------------------

def _resolve_app_dir() -> Path:
    """Resolve the application resource root."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundles resources into _internal/ (sys._MEIPASS)
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def _resolve_install_dir() -> Path:
    """Resolve the installation directory (where the installer placed files)."""
    if getattr(sys, "frozen", False):
        # The .exe lives in the install dir (parent of _internal/)
        return Path(sys.executable).resolve().parent
    return _resolve_app_dir()


APP_DIR = _resolve_app_dir()
INSTALL_DIR = _resolve_install_dir()


def _is_dev_environment() -> bool:
    """Detect if running from a source/development checkout."""
    if getattr(sys, "frozen", False):
        return False
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
    4. Platform default: Windows -> C:\\ProgramData\\OpenAVC,
       macOS -> /Library/Application Support/OpenAVC, Linux -> /var/lib/openavc
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

    # macOS: the server runs as a system LaunchDaemon (boot, pre-login, as
    # root), so data lives in the system-wide Application Support, not a
    # per-user ~/Library that wouldn't exist before anyone logs in.
    if sys.platform == "darwin":
        return Path("/Library/Application Support/OpenAVC")

    return Path("/var/lib/openavc")


def get_log_dir() -> Path:
    """Determine the log directory.

    Priority:
    1. OPENAVC_LOG_DIR environment variable
    2. Docker: /data/logs
    3. Development: ./data/logs
    4. Platform default: Windows -> data_dir/logs, macOS -> /Library/Logs/OpenAVC,
       Linux -> /var/log/openavc
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

    # macOS standard system-wide log location.
    if sys.platform == "darwin":
        return Path("/Library/Logs/OpenAVC")

    return Path("/var/log/openavc")


# User-installed driver and plugin repos. These hold content the user installs
# via the Programmer IDE (community drivers/plugins) and must survive upgrades.
# They live on persistent data storage, NOT in APP_DIR — APP_DIR is not
# persistent on Docker (container layer) and is rewritten by Windows installer
# upgrades. Releases may seed APP_DIR / "{driver,plugin}_repo" with built-in
# content; the first-start migration in `migrate_legacy_repos()` moves that
# seed (and any user content from an older install layout) into data_dir.
DRIVER_REPO_DIR = get_data_dir() / "driver_repo"
PLUGIN_REPO_DIR = get_data_dir() / "plugin_repo"

# Per-plugin persistent data directory. Sibling of PLUGIN_REPO_DIR (which
# holds plugin *code*). PLUGIN_DATA_DIR / <plugin_id> is the plugin's
# private space for sidecar binaries, downloaded models, cached state,
# certs, and anything else that should survive plugin updates and
# (optionally) plugin uninstall. Created on first access by PluginAPI.
PLUGIN_DATA_DIR = get_data_dir() / "plugin_data"

# Legacy locations from before the data_dir move — referenced by the
# migration shim only. Not used at runtime once migration has run.
_LEGACY_DRIVER_REPO_DIR = APP_DIR / "driver_repo"
_LEGACY_PLUGIN_REPO_DIR = APP_DIR / "plugin_repo"

# Bundle-relative resources (read-only, stays in APP_DIR)
THEMES_DIR = APP_DIR / "themes"
DRIVER_DEFINITIONS_DIR = APP_DIR / "server" / "drivers" / "definitions"
WEB_PANEL_DIR = APP_DIR / "web" / "panel"
WEB_PROGRAMMER_DIR = APP_DIR / "web" / "programmer" / "dist"
WEB_SIMULATOR_DIR = APP_DIR / "web" / "simulator" / "dist"
SEED_TEMPLATES_DIR = APP_DIR / "server" / "templates"
USER_TEMPLATES_DIR = APP_DIR / "user_templates"
PYPROJECT_PATH = APP_DIR / "pyproject.toml"

# Default system.json schema
DEFAULTS: dict[str, Any] = {
    "network": {
        "http_port": 8080,
        "bind_address": "127.0.0.1",
        "control_interface": "",
        # Trust the X-Forwarded-For header for the client IP only when behind a
        # known reverse proxy. Off by default so a client can't spoof its
        # source IP to dodge rate limits / the localhost exemption.
        "trust_forwarded_for": False,
        # Convenience redirect listener on port 80 so typed URLs can drop the
        # port (http://<ip>/panel just works). Pure redirect to the real
        # HTTP/HTTPS port — never serves content. Off by default: binding
        # port 80 needs CAP_NET_BIND_SERVICE on Linux services, and the port
        # is often owned by other software.
        "port80_redirect": False,
        # Optional deployment-provided host-network backend: an importable
        # module exposing create_backend() -> NetworkBackend | None. Empty =
        # use built-in detection (nmcli). Appliance images set this to point
        # at their own backend. See server/system/network.py.
        "backend_module": "",
    },
    "auth": {
        "programmer_username": "",
        "programmer_password": "",
        "api_key": "",
        "panel_lock_code": "",
        # No-credential posture: "auto" = open only on a dev checkout, require
        # setup on shipped deployments; "true"/"false" force it. See
        # server/api/auth.py anonymous_access_allowed().
        "allow_anonymous": "auto",
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
        "auto_check_interval_hours": 24,
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
    "discovery": {
        "advertise": True,
    },
    "tls": {
        "enabled": False,
        "port": 8443,
        "auto_generate": True,
        "cert_file": "",
        "key_file": "",
        "redirect_http": True,
        # True once the cloud enrollment flow has installed a trusted
        # certificate (served via SNI next to the self-signed pair).
        "cloud_cert": False,
    },
}

# Mapping: (section, key) -> (env_var, type)
ENV_OVERRIDES: dict[tuple[str, str], tuple[str, type]] = {
    ("network", "http_port"): ("OPENAVC_PORT", int),
    ("network", "bind_address"): ("OPENAVC_BIND", str),
    ("network", "control_interface"): ("OPENAVC_CONTROL_INTERFACE", str),
    ("network", "trust_forwarded_for"): ("OPENAVC_TRUST_FORWARDED_FOR", bool),
    ("network", "port80_redirect"): ("OPENAVC_PORT80_REDIRECT", bool),
    ("auth", "programmer_username"): ("OPENAVC_PROGRAMMER_USERNAME", str),
    ("auth", "programmer_password"): ("OPENAVC_PROGRAMMER_PASSWORD", str),
    ("auth", "api_key"): ("OPENAVC_API_KEY", str),
    ("auth", "panel_lock_code"): ("OPENAVC_PANEL_LOCK_CODE", str),
    ("auth", "allow_anonymous"): ("OPENAVC_ALLOW_ANONYMOUS", str),
    ("logging", "level"): ("OPENAVC_LOG_LEVEL", str),
    ("updates", "check_enabled"): ("OPENAVC_UPDATE_CHECK", bool),
    ("updates", "channel"): ("OPENAVC_UPDATE_CHANNEL", str),
    ("cloud", "enabled"): ("OPENAVC_CLOUD_ENABLED", bool),
    ("cloud", "endpoint"): ("OPENAVC_CLOUD_ENDPOINT", str),
    ("cloud", "system_key"): ("OPENAVC_CLOUD_SYSTEM_KEY", str),
    ("cloud", "system_id"): ("OPENAVC_CLOUD_SYSTEM_ID", str),
    ("discovery", "advertise"): ("OPENAVC_MDNS_ADVERTISE", bool),
    ("tls", "enabled"): ("OPENAVC_TLS_ENABLED", bool),
    ("tls", "port"): ("OPENAVC_TLS_PORT", int),
    ("tls", "auto_generate"): ("OPENAVC_TLS_AUTO_GENERATE", bool),
    ("tls", "cert_file"): ("OPENAVC_TLS_CERT_FILE", str),
    ("tls", "key_file"): ("OPENAVC_TLS_KEY_FILE", str),
    ("tls", "redirect_http"): ("OPENAVC_TLS_REDIRECT_HTTP", bool),
    ("tls", "cloud_cert"): ("OPENAVC_TLS_CLOUD_CERT", bool),
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursively for nested dicts.

    Keys in ``base`` (the DEFAULTS) win their default when absent from the
    file. Keys present only in ``override`` are preserved verbatim — so a
    setting written by a newer platform survives a rollback to an older one
    that doesn't know the key (mirrors the project loader's forward-compat
    intent). Without this, the next save() would silently strip it.
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
    # Carry through unknown keys (sections or, within a section, fields) that
    # aren't in DEFAULTS so a forward-compatible setting isn't dropped.
    for key, value in override.items():
        if key not in base:
            result[key] = value
    return result


def migrate_legacy_repos() -> None:
    """Move legacy APP_DIR/{driver,plugin}_repo content into data_dir.

    PLUGIN_REPO_DIR and DRIVER_REPO_DIR were anchored to APP_DIR for older
    releases. APP_DIR is not persistent on Docker (lives inside the container
    image) and is rebuilt by Windows-installer upgrades, so user-installed
    plugins and drivers were silently wiped. The constants now live under
    `get_data_dir()`; this function moves any content from the old location
    on first start.

    Idempotent: skips if destination already has content. Conservative: never
    deletes the source unless every move succeeded. Also acts as a "seed"
    step for fresh installs whose release bundle ships built-in content
    under APP_DIR / "{driver,plugin}_repo".
    """
    import shutil

    for legacy, target, label in (
        (_LEGACY_PLUGIN_REPO_DIR, PLUGIN_REPO_DIR, "plugin_repo"),
        (_LEGACY_DRIVER_REPO_DIR, DRIVER_REPO_DIR, "driver_repo"),
    ):
        try:
            # Same path resolves on both sides (someone set OPENAVC_DATA_DIR
            # to APP_DIR, or APP_DIR == data_dir for a non-standard layout).
            # Nothing to migrate.
            if legacy.resolve() == target.resolve():
                continue
        except OSError:
            # resolve() can fail if a parent doesn't exist yet; safe to
            # continue with the un-resolved comparison below.
            if legacy == target:
                continue

        if not legacy.exists() or not legacy.is_dir():
            continue

        try:
            legacy_entries = [p for p in legacy.iterdir() if not p.name.startswith(".")]
            legacy_dot_entries = [p for p in legacy.iterdir() if p.name.startswith(".")]
        except OSError as e:
            log.warning("Cannot read legacy %s at %s: %s", label, legacy, e)
            continue

        if not legacy_entries and not legacy_dot_entries:
            # Empty directory — nothing to migrate.
            continue

        # If the destination already has real content, do not overwrite.
        if target.exists():
            try:
                target_entries = [p for p in target.iterdir() if not p.name.startswith(".")]
            except OSError as e:
                log.warning("Cannot read %s at %s: %s", label, target, e)
                continue
            if target_entries:
                log.debug(
                    "Skipping %s migration: %s already populated",
                    label, target,
                )
                continue

        log.info("Migrating %s from %s to %s", label, legacy, target)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("Cannot create %s: %s", target, e)
            continue

        moved_all = True
        moved: list[Path] = []
        for entry in [*legacy_entries, *legacy_dot_entries]:
            dest = target / entry.name
            if dest.exists():
                # Conservative: leave both in place. The user-visible target
                # entry wins; the legacy entry remains for manual review.
                log.debug("Skipping %s (destination exists at %s)", entry, dest)
                moved_all = False
                continue
            try:
                shutil.move(str(entry), str(dest))
                moved.append(entry)
            except (OSError, shutil.Error) as e:
                log.warning("Failed to move %s -> %s: %s", entry, dest, e)
                moved_all = False

        if moved_all:
            # Only remove the legacy directory itself if we fully drained it.
            try:
                legacy.rmdir()
            except OSError:
                # Non-empty (race) or permission denied — leave it; future
                # runs will see the remaining entries and retry.
                pass


def migrate_legacy_project_dir(
    legacy_dir: Path | None = None,
    target_dir: Path | None = None,
) -> None:
    """Move the legacy default project directory (APP_DIR/projects) into data_dir.

    The default project path used to resolve to APP_DIR/projects/default/
    project.avc; it now lives under get_data_dir()/projects like every other
    piece of persistent user data (and like every packaged deployment already
    configures via OPENAVC_PROJECT). The project directory carries much more
    than project.avc — state.json, cloud.json, .instance_id, scripts/,
    assets/, themes/, and backups/ all live beside it — so the whole tree is
    moved with a single os.rename: either everything moves atomically or
    nothing changes.

    Skipped entirely when OPENAVC_PROJECT or OPENAVC_DATA_DIR is set: an
    explicit path is the operator's choice, and files must never be moved out
    from under it.

    On any failure the directory is left untouched and a warning explains how
    to finish the move manually.
    """
    if os.environ.get("OPENAVC_PROJECT") or os.environ.get("OPENAVC_DATA_DIR"):
        return

    legacy = legacy_dir if legacy_dir is not None else APP_DIR / "projects"
    target = target_dir if target_dir is not None else get_data_dir() / "projects"

    try:
        if legacy.resolve() == target.resolve():
            return
    except OSError:
        if legacy == target:
            return

    if not legacy.is_dir():
        return

    try:
        if not any(legacy.iterdir()):
            return
    except OSError as e:
        log.warning("Cannot read legacy projects directory at %s: %s", legacy, e)
        return

    if target.exists():
        try:
            if any(target.iterdir()):
                log.warning(
                    "Legacy projects directory found at %s, but %s already has "
                    "content; leaving both in place. Merge them manually (with "
                    "the server stopped) or set OPENAVC_PROJECT to the "
                    "project.avc you want to load.",
                    legacy, target,
                )
                return
            # Empty directory at the target would make the rename fail.
            target.rmdir()
        except OSError as e:
            log.warning("Cannot inspect projects directory at %s: %s", target, e)
            return

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        legacy.rename(target)
        log.info("Migrated projects directory from %s to %s", legacy, target)
    except OSError as e:
        log.warning(
            "Could not move projects directory from %s to %s: %s. Nothing was "
            "changed — your data is still at %s. Move it to %s manually (with "
            "the server stopped), or set OPENAVC_PROJECT to its project.avc.",
            legacy, target, e, legacy, target,
        )


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
        # Persisted layer: defaults + system.json, WITHOUT env overrides. This
        # is what save() writes back. Kept separate from self._data (the
        # effective runtime view, which has env overrides layered on top) so an
        # env-provided value — including secrets like OPENAVC_API_KEY or
        # OPENAVC_PROGRAMMER_PASSWORD — never gets baked into system.json.
        self._file_data: dict[str, Any] = {}
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
        return PLUGIN_REPO_DIR

    @property
    def driver_repo_path(self) -> Path:
        """Path to the driver repository directory."""
        return DRIVER_REPO_DIR

    @property
    def themes_dir(self) -> Path:
        """Path to the themes directory."""
        return THEMES_DIR

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

        # Snapshot the persisted layer (defaults + file) BEFORE env overrides
        # are applied. save() serializes this, so env-injected values — secrets
        # included — stay out of system.json. set() keeps it in sync with
        # deliberate UI changes.
        self._file_data = copy.deepcopy(self._data)

        # Layer: environment variable overrides (runtime view only)
        for (section, key), (env_var, target_type) in ENV_OVERRIDES.items():
            raw = os.environ.get(env_var)
            if raw is not None:
                parsed = _parse_env_value(raw, target_type)
                if parsed is not None:
                    self._data[section][key] = parsed

        self._loaded = True

    def save(self) -> None:
        """Write the persisted config layer (defaults + file + UI changes,
        minus env overrides) to system.json.

        Env overrides are deliberately excluded. They're supplied fresh each
        boot and are the source of truth at runtime, but baking them into
        system.json would (1) leak env-provided secrets — OPENAVC_API_KEY,
        OPENAVC_PROGRAMMER_PASSWORD, OPENAVC_PANEL_LOCK_CODE — into a plaintext
        file the operator never chose to hold them, and (2) make a one-off env
        value silently persist after the env var is unset, overriding the
        "env is source of truth" model. ``self._file_data`` holds exactly the
        pre-env layer; set() keeps it in sync with deliberate changes.

        Atomic write (temp file + os.replace), mirroring state_persister. A
        crash or power loss mid-write can't leave a truncated system.json,
        which the layered-config loader would silently fall back to defaults
        for — losing the operator's settings.
        """
        fd = None
        tmp_path = None
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            content = json.dumps(self._file_data, indent=4) + "\n"
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._data_dir),
                suffix=".tmp",
                prefix=".system_",
            )
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            fd = None
            os.replace(tmp_path, str(self._file_path))
            tmp_path = None
            log.info("Saved system config to %s", self._file_path)
        except OSError as e:
            log.error("Failed to save system.json to %s: %s", self._file_path, e)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

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
        """Set a config value (in memory only, call save() to persist).

        Updates both the effective runtime view (self._data) and the persisted
        layer (self._file_data) so a deliberate change survives save() even
        though env overrides are otherwise stripped from what's written. See
        save() for why the two layers are kept apart.
        """
        if section not in self._data:
            self._data[section] = {}
        self._data[section][key] = value
        if section not in self._file_data:
            self._file_data[section] = {}
        self._file_data[section][key] = value

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
