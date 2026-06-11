"""
Plugin loader — discovery, validation, lifecycle, and error isolation.

Scans plugin_repo/ for valid plugin packages, validates manifests,
manages the start/stop lifecycle, and handles missing/incompatible plugins.
"""

import asyncio
import importlib
import importlib.machinery
import importlib.util
import inspect
import os
import platform
import re
import sys
import threading
from pathlib import Path
from typing import Any

from server.core.plugin_api import PluginAPI
from server.core.plugin_registry import PluginRegistry
from server.utils.logger import get_logger

log = get_logger(__name__)

# Global plugin class registry: plugin_id -> plugin_class
_PLUGIN_CLASS_REGISTRY: dict[str, type] = {}
_REGISTRY_LOCK = threading.Lock()

# Required fields in PLUGIN_INFO
REQUIRED_MANIFEST_FIELDS = {"id", "name", "version", "author", "description", "category", "license"}

# Valid capability values
VALID_CAPABILITIES = {
    "state_read", "state_write", "variable_write",
    "event_emit", "event_subscribe",
    "macro_execute", "device_command", "network_listen", "usb_access",
    "http_endpoints",
}

# Valid category values
VALID_CATEGORIES = {"control_surface", "integration", "sensor", "utility"}

# MIT-compatible licenses (case-insensitive check)
MIT_COMPATIBLE_LICENSES = {
    "mit", "bsd-2-clause", "bsd-3-clause", "apache-2.0", "isc",
    "psf", "unlicense", "0bsd", "cc0-1.0",
}

# Max consecutive callback failures before auto-disable
MAX_CALLBACK_FAILURES = 10

# Lifecycle-hook timeouts. Plugins are arbitrary third-party code, so a
# start()/stop()/health_check() that never returns must not hang startup,
# shutdown, or a REST request forever.
PLUGIN_START_TIMEOUT = 30.0
PLUGIN_STOP_TIMEOUT = 10.0
PLUGIN_HEALTH_TIMEOUT = 5.0
# Bound for a plugin's on_config_changed hook. apply_config runs under the
# per-plugin lifecycle lock (via restart_or_apply), so a hung hook would
# otherwise wedge every future stop/start of that plugin.
PLUGIN_APPLY_TIMEOUT = 10.0

# Cap on in-flight plugin-log → event tasks. A plugin logging in a tight loop
# can't spawn unbounded one-shot tasks on the shared event loop; past the cap
# the event mirror is dropped (the logger line is still written).
MAX_PENDING_LOG_EVENTS = 1000

# Extension types the panel/IDE understand. EXTENSIONS keys outside this set
# are rejected at load — an authoring typo shouldn't silently do nothing.
VALID_EXTENSION_TYPES = {
    "views", "device_panels", "status_cards", "context_actions", "panel_elements",
}

# Field that uniquely identifies an extension within its type. panel_elements
# are keyed by their element `type`; every other extension type uses `id`.
_EXTENSION_ID_FIELD = {
    "views": "id",
    "device_panels": "id",
    "status_cards": "id",
    "context_actions": "id",
    "panel_elements": "type",
}

# Valid macro action param field types (mirrors the macro builder's renderer support)
VALID_MACRO_ACTION_PARAM_TYPES = {
    "text", "integer", "float", "boolean", "select",
    "state_key", "device_ref", "macro_ref",
}

# Action name segment after "<plugin_id>." — lowercase letters, digits, underscores
_MACRO_ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Script API method name pattern — valid Python identifier, no leading underscore
_SCRIPT_API_METHOD_RE = re.compile(r"^[a-z][a-zA-Z0-9_]*$")

# Names plugins can't use for SCRIPT_API methods (Python or proxy machinery)
_SCRIPT_API_RESERVED_NAMES = frozenset({
    "register", "unregister", "clear",
    # Anything starting with underscore is rejected by the regex above.
})

# Whitelisted iframe `sandbox` tokens a panel_elements extension can opt into.
# `allow-scripts` is always present and not listed here. Tokens that would
# escape the sandbox (`allow-popups-to-escape-sandbox`,
# `allow-top-navigation`, `allow-pointer-lock`) are deliberately excluded.
_ALLOWED_SANDBOX_PERMISSIONS = frozenset({
    "allow-same-origin",
    "allow-forms",
    "allow-modals",
    "allow-popups",
})

# Whitelisted Permissions-Policy tokens a panel_elements extension can opt
# into via the iframe `allow` attribute. `camera`, `microphone`,
# `geolocation`, and similar sensor-access tokens are deliberately
# excluded — no v1 use case needs them and they have non-obvious
# privacy implications.
_ALLOWED_ALLOW_FEATURES = frozenset({
    "autoplay",
    "encrypted-media",
    "fullscreen",
    "picture-in-picture",
})


def get_platform_id() -> str:
    """Detect the current platform identifier."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        return "win_x64"
    elif system == "linux":
        if machine in ("aarch64", "arm64"):
            return "linux_arm64"
        return "linux_x64"
    return "unknown"


def get_plugin_registry() -> dict[str, type]:
    """Return the global plugin class registry."""
    return _PLUGIN_CLASS_REGISTRY


def register_plugin_class(plugin_class: type) -> None:
    """Register a plugin class in the global registry."""
    info = getattr(plugin_class, "PLUGIN_INFO", None)
    if info and "id" in info:
        plugin_id = info["id"]
        with _REGISTRY_LOCK:
            if plugin_id in _PLUGIN_CLASS_REGISTRY:
                existing = _PLUGIN_CLASS_REGISTRY[plugin_id]
                log.warning(
                    "Plugin ID '%s' already registered (%s), overwriting with %s",
                    plugin_id, existing.__module__, plugin_class.__module__,
                )
            _PLUGIN_CLASS_REGISTRY[plugin_id] = plugin_class
        log.debug(f"Registered plugin class: {plugin_id}")


def unregister_plugin_class(plugin_id: str) -> bool:
    """Unregister a plugin class. Returns True if found."""
    with _REGISTRY_LOCK:
        removed = _PLUGIN_CLASS_REGISTRY.pop(plugin_id, None) is not None
    if removed:
        log.info(f"Unregistered plugin class: {plugin_id}")
    return removed


def validate_macro_actions(
    macro_actions: Any, plugin_id: str, plugin_class: type
) -> tuple[bool, str]:
    """Validate a plugin's MACRO_ACTIONS declaration.

    Returns (valid, error_message).
    """
    if not isinstance(macro_actions, dict):
        return False, "MACRO_ACTIONS must be a dict"

    expected_prefix = f"{plugin_id}."
    for action_type, spec in macro_actions.items():
        if not isinstance(action_type, str):
            return False, f"action key {action_type!r} must be a string"
        if not action_type.startswith(expected_prefix):
            return False, (
                f"action '{action_type}' must be prefixed with the plugin id "
                f"('{expected_prefix}')"
            )
        suffix = action_type[len(expected_prefix):]
        if not _MACRO_ACTION_NAME_RE.match(suffix):
            return False, (
                f"action '{action_type}' suffix must be lowercase letters, digits, "
                f"or underscores (got '{suffix}')"
            )
        if not isinstance(spec, dict):
            return False, f"action '{action_type}' spec must be a dict"

        handler_name = spec.get("handler")
        if not handler_name or not isinstance(handler_name, str):
            return False, f"action '{action_type}' missing 'handler' (method name)"
        handler = getattr(plugin_class, handler_name, None)
        if handler is None:
            return False, (
                f"action '{action_type}' handler '{handler_name}' not found on "
                f"plugin class"
            )
        if not inspect.iscoroutinefunction(handler):
            return False, (
                f"action '{action_type}' handler '{handler_name}' must be an "
                f"async method"
            )

        params = spec.get("params", [])
        if not isinstance(params, list):
            return False, f"action '{action_type}' params must be a list"
        seen_keys: set[str] = set()
        for i, param in enumerate(params):
            if not isinstance(param, dict):
                return False, f"action '{action_type}' param[{i}] must be a dict"
            key = param.get("key")
            if not key or not isinstance(key, str):
                return False, f"action '{action_type}' param[{i}] missing 'key'"
            if key in seen_keys:
                return False, f"action '{action_type}' has duplicate param key '{key}'"
            seen_keys.add(key)
            ptype = param.get("type")
            if ptype not in VALID_MACRO_ACTION_PARAM_TYPES:
                return False, (
                    f"action '{action_type}' param '{key}' has invalid type "
                    f"'{ptype}' (allowed: {sorted(VALID_MACRO_ACTION_PARAM_TYPES)})"
                )
            if ptype == "select" and not isinstance(param.get("options"), list) \
                    and not param.get("options_source"):
                return False, (
                    f"action '{action_type}' select param '{key}' needs either "
                    f"'options' (list) or 'options_source' (state key)"
                )

    return True, ""


def validate_script_api(
    script_api: Any, plugin_id: str, plugin_class: type
) -> tuple[bool, str]:
    """Validate a plugin's SCRIPT_API declaration.

    Returns (valid, error_message).
    """
    if not isinstance(script_api, dict):
        return False, "SCRIPT_API must be a dict"

    # The plugin id itself must be a valid Python identifier — otherwise
    # `openavc.plugins.<id>` won't even parse.
    if not _SCRIPT_API_METHOD_RE.match(plugin_id):
        return False, (
            f"plugin id '{plugin_id}' is not a valid Python identifier — "
            f"SCRIPT_API can't be exposed under openavc.plugins"
        )

    for method_name, spec in script_api.items():
        if not isinstance(method_name, str):
            return False, f"method key {method_name!r} must be a string"
        if not _SCRIPT_API_METHOD_RE.match(method_name):
            return False, (
                f"method '{method_name}' is not a valid identifier "
                f"(lowercase start, letters/digits/underscores only)"
            )
        if method_name in _SCRIPT_API_RESERVED_NAMES:
            return False, f"method name '{method_name}' is reserved"
        if not isinstance(spec, dict):
            return False, f"method '{method_name}' spec must be a dict"

        handler_name = spec.get("handler")
        if not handler_name or not isinstance(handler_name, str):
            return False, f"method '{method_name}' missing 'handler' (method name)"
        handler = getattr(plugin_class, handler_name, None)
        if handler is None:
            return False, (
                f"method '{method_name}' handler '{handler_name}' not found "
                f"on plugin class"
            )
        is_async = inspect.iscoroutinefunction(handler)
        wants_sync = bool(spec.get("sync"))
        if wants_sync and is_async:
            return False, (
                f"method '{method_name}' marked sync=True but handler "
                f"'{handler_name}' is async"
            )
        if not wants_sync and not is_async:
            return False, (
                f"method '{method_name}' handler '{handler_name}' is not async — "
                f"add 'sync': True to SCRIPT_API entry if intentional"
            )

    return True, ""


def validate_extensions(
    extensions: Any, plugin_id: str, plugin_class: type
) -> tuple[bool, str]:
    """Validate a plugin's EXTENSIONS declaration.

    EXTENSIONS feeds the /api/plugins/extensions aggregation that the panel and
    IDE render. Unlike MACRO_ACTIONS/SCRIPT_API this was previously unvalidated,
    so a single malformed entry (a non-dict list element, a list where a dict was
    expected) could blow up the serve-time loop for *every* plugin. Validate the
    shape at load so a bad plugin fails to enable instead of blanking the whole
    plugin UI.

    Returns (valid, error_message).
    """
    if not isinstance(extensions, dict):
        return False, "EXTENSIONS must be a dict"

    for ext_type, ext_list in extensions.items():
        if ext_type not in VALID_EXTENSION_TYPES:
            return False, (
                f"unknown extension type '{ext_type}' "
                f"(allowed: {sorted(VALID_EXTENSION_TYPES)})"
            )
        if not isinstance(ext_list, list):
            return False, f"extension type '{ext_type}' must be a list"

        id_field = _EXTENSION_ID_FIELD[ext_type]
        seen_ids: set[str] = set()
        for i, ext in enumerate(ext_list):
            if not isinstance(ext, dict):
                return False, f"{ext_type}[{i}] must be a dict"
            ext_id = ext.get(id_field)
            if not ext_id or not isinstance(ext_id, str):
                return False, f"{ext_type}[{i}] missing '{id_field}' (string)"
            if ext_id in seen_ids:
                return False, (
                    f"{ext_type} has duplicate {id_field} '{ext_id}'"
                )
            seen_ids.add(ext_id)

    return True, ""


def _purge_plugin_modules(package_name: str) -> None:
    """Remove a plugin's package and all its submodules from sys.modules."""
    for name in [
        m for m in sys.modules
        if m == package_name or m.startswith(package_name + ".")
    ]:
        sys.modules.pop(name, None)


def _exec_plugin_in_package(filepath: Path, plugin_dir: Path) -> Any:
    """Import a plugin's main module inside a private package namespace.

    Each plugin loads as a package ``plugin_<dir>`` whose ``__path__`` is the
    plugin's own directory. A multi-file plugin's siblings therefore import via
    relative imports (``from . import helper`` / ``from .helper import X``) and
    land in ``sys.modules`` under ``plugin_<dir>.helper`` — never under a bare
    ``helper`` key that a second plugin shipping its own ``helper.py`` could
    clobber. No bare directory is added to ``sys.path``, so two plugins with
    same-named helper modules can't shadow each other (A388).

    On any exception every module this call added is rolled back so a failed or
    partial import can't pollute later loads. Returns the executed module, or
    ``None`` if the file has no importable spec.
    """
    package_name = f"plugin_{plugin_dir.name}"
    before = set(sys.modules)
    try:
        if filepath.name == "__init__.py":
            # The plugin's own package init carries the class.
            spec = importlib.util.spec_from_file_location(
                package_name, filepath,
                submodule_search_locations=[str(plugin_dir)],
            )
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = module
            spec.loader.exec_module(module)
            return module

        # Synthesize a parent package so the main module's relative imports
        # resolve against the plugin directory, then load the file as a
        # submodule of it.
        if package_name not in sys.modules:
            pkg_spec = importlib.machinery.ModuleSpec(
                package_name, None, is_package=True
            )
            pkg = importlib.util.module_from_spec(pkg_spec)
            pkg.__path__ = [str(plugin_dir)]
            sys.modules[package_name] = pkg

        # The main file is a regular module, not a package — relative imports
        # resolve via the parent package's __path__ (set above), so don't pass
        # submodule_search_locations here (that would mark it a package and make
        # __spec__.parent disagree with __package__).
        sub_name = f"{package_name}.{filepath.stem}"
        spec = importlib.util.spec_from_file_location(sub_name, filepath)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[sub_name] = module
        spec.loader.exec_module(module)
        return module
    except BaseException:
        for name in set(sys.modules) - before:
            if name == package_name or name.startswith(package_name + "."):
                sys.modules.pop(name, None)
        raise


class PluginLoader:
    """
    Manages plugin discovery, validation, lifecycle, and error isolation.

    One PluginLoader instance per Engine. Plugins are loaded from plugin_repo/
    and started based on the project file's plugins configuration.
    """

    def __init__(self, state_store, event_bus, macro_engine, device_manager):
        self._state = state_store
        self._events = event_bus
        self._macros = macro_engine
        self._devices = device_manager
        self._platform_id = get_platform_id()

        # Running plugin instances: plugin_id -> instance
        self._instances: dict[str, Any] = {}
        # Plugin registries: plugin_id -> PluginRegistry
        self._registries: dict[str, PluginRegistry] = {}
        # Plugin APIs: plugin_id -> PluginAPI
        self._apis: dict[str, PluginAPI] = {}
        # Missing plugins: plugin_id -> info dict
        self._missing_plugins: dict[str, dict] = {}
        # Incompatible plugins: plugin_id -> info dict
        self._incompatible_plugins: dict[str, dict] = {}
        # Plugin status: plugin_id -> status string
        self._status: dict[str, str] = {}
        # Error messages: plugin_id -> error string
        self._errors: dict[str, str] = {}
        # Callback failure counts: plugin_id -> count
        self._callback_failures: dict[str, int] = {}
        # Plugins with an auto-disable in flight (so repeated failures past the
        # threshold don't each spawn a duplicate _auto_disable_plugin task).
        self._auto_disabling: set[str] = set()
        # Per-plugin lifecycle lock so start/stop/auto-disable on the same
        # plugin serialize and can't interleave (a restart can't be killed by a
        # stale queued auto-disable; a config change can't race an enable).
        self._locks: dict[str, asyncio.Lock] = {}
        # Monotonic instance epoch per plugin — incremented on every start so a
        # queued auto-disable can tell whether it targets the instance that
        # actually failed or a freshly-restarted one.
        self._instance_epoch: dict[str, int] = {}
        self._epoch_counter = 0
        # In-flight plugin-log → event tasks (bounded, self-pruning).
        self._log_tasks: set[asyncio.Task] = set()
        # Config save callback
        self._save_config_fn = None
        # Router mount/unmount hooks (set by main.py, which owns the app).
        # Called when a plugin that registered an APIRouter starts/stops.
        self._mount_router_fn = None
        self._unmount_router_fn = None

    def set_save_config_fn(self, fn):
        """Set the callback for saving plugin config to the project file."""
        self._save_config_fn = fn

    def set_router_hooks(self, mount_fn, unmount_fn):
        """Set callbacks that mount/unmount a plugin's registered HTTP router.

        mount_fn(plugin_id, router) is called after a plugin that called
        api.register_router() starts; unmount_fn(plugin_id) is called on stop.
        Wired by main.py because the FastAPI app lives there, not in the loader.
        """
        self._mount_router_fn = mount_fn
        self._unmount_router_fn = unmount_fn

    # ──── Discovery ────

    def scan_plugins(self, plugin_repo_dir: Path | None = None) -> dict[str, type]:
        """
        Scan plugin_repo/ for valid plugin packages and register them.

        Returns dict of plugin_id -> plugin_class for all discovered plugins.
        """
        if plugin_repo_dir is None:
            from server.system_config import PLUGIN_REPO_DIR
            plugin_repo_dir = PLUGIN_REPO_DIR

        if not plugin_repo_dir.is_dir():
            log.debug(f"Plugin repo directory not found: {plugin_repo_dir}")
            return {}

        # Add .deps to sys.path if it exists. Append (don't insert at 0) so a
        # bundled plugin dependency can't shadow a stdlib or first-party module
        # — .deps holds extra packages plugins need, not overrides of ours.
        deps_path = str(plugin_repo_dir / ".deps")
        if os.path.isdir(deps_path) and deps_path not in sys.path:
            sys.path.append(deps_path)

        # On Windows, add .deps to DLL search path so native libs (e.g. hidapi.dll)
        # are findable by ctypes.  Both methods needed: add_dll_directory for
        # LoadLibrary calls, PATH for ctypes.util.find_library.
        if os.path.isdir(deps_path) and platform.system().lower() == "windows":
            try:
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(deps_path)
                if deps_path not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = deps_path + os.pathsep + os.environ.get("PATH", "")
            except OSError as e:
                log.debug(f"Could not add .deps to DLL search path: {e}")

        # On Linux, add .deps to LD_LIBRARY_PATH so dlopen finds native libs
        if os.path.isdir(deps_path) and platform.system().lower() == "linux":
            current_ld = os.environ.get("LD_LIBRARY_PATH", "")
            if deps_path not in current_ld:
                os.environ["LD_LIBRARY_PATH"] = deps_path + (":" + current_ld if current_ld else "")

        discovered = {}

        for entry in sorted(plugin_repo_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue

            try:
                plugin_class = self._load_plugin_from_dir(entry)
                if plugin_class:
                    plugin_id = plugin_class.PLUGIN_INFO["id"]
                    register_plugin_class(plugin_class)
                    discovered[plugin_id] = plugin_class
            except Exception:  # Catch-all: loading arbitrary plugin code can raise anything
                log.exception(f"Failed to load plugin from {entry.name}")

        log.info(f"Discovered {len(discovered)} plugins from {plugin_repo_dir}")
        return discovered

    def _load_plugin_from_dir(self, plugin_dir: Path) -> type | None:
        """Load a plugin class from a directory."""
        # Look for a Python file with a class that has PLUGIN_INFO
        # Priority: __init__.py in package, then <dir_name>_plugin.py, then any .py
        candidates = []

        init_file = plugin_dir / "__init__.py"
        if init_file.exists():
            candidates.append(init_file)

        named_file = plugin_dir / f"{plugin_dir.name}_plugin.py"
        if named_file.exists():
            candidates.append(named_file)

        for py_file in sorted(plugin_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file in candidates:
                continue
            candidates.append(py_file)

        for filepath in candidates:
            plugin_class = self._load_plugin_from_file(filepath, plugin_dir)
            if plugin_class:
                return plugin_class

        return None

    def _load_plugin_from_file(self, filepath: Path, plugin_dir: Path) -> type | None:
        """Import a Python file and find a class with PLUGIN_INFO.

        The file is executed inside a per-plugin package namespace (see
        ``_exec_plugin_in_package``) so a multi-file plugin's helper modules
        can't collide with another plugin's same-named helpers.
        """
        try:
            module = _exec_plugin_in_package(filepath, plugin_dir)
            if module is None:
                return None

            # Find the plugin class
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        hasattr(attr, "PLUGIN_INFO") and
                        isinstance(attr.PLUGIN_INFO, dict) and
                        "id" in attr.PLUGIN_INFO):
                    return attr

            # No plugin class in this file — drop the modules it loaded so a
            # later candidate (or plugin) starts clean.
            _purge_plugin_modules(f"plugin_{plugin_dir.name}")
        except Exception:  # Catch-all: exec_module runs arbitrary plugin code
            log.exception(f"Error loading plugin file {filepath}")

        return None

    # ──── Validation ────

    def validate_manifest(self, plugin_class: type) -> tuple[bool, str]:
        """
        Validate a plugin's PLUGIN_INFO manifest.

        Returns (valid, error_message).
        """
        info = getattr(plugin_class, "PLUGIN_INFO", None)
        if not isinstance(info, dict):
            return False, "Missing or invalid PLUGIN_INFO dict"

        # Required fields
        missing = REQUIRED_MANIFEST_FIELDS - set(info.keys())
        if missing:
            return False, f"Missing required fields: {sorted(missing)}"

        # License check
        license_str = info.get("license", "").lower().strip()
        if license_str not in MIT_COMPATIBLE_LICENSES:
            return False, f"License '{info.get('license')}' is not MIT-compatible"

        # Category check
        category = info.get("category", "")
        if category not in VALID_CATEGORIES:
            return False, f"Invalid category '{category}', must be one of {sorted(VALID_CATEGORIES)}"

        # Capabilities check
        capabilities = info.get("capabilities", [])
        invalid_caps = set(capabilities) - VALID_CAPABILITIES
        if invalid_caps:
            return False, f"Unknown capabilities: {sorted(invalid_caps)}"

        # Platform check
        platforms = info.get("platforms", ["all"])
        if "all" not in platforms and self._platform_id not in platforms:
            return False, (
                f"Plugin not compatible with current platform '{self._platform_id}'. "
                f"Supported: {platforms}"
            )

        # min_openavc_version check
        min_version = info.get("min_openavc_version")
        if min_version:
            from server.version import __version__
            try:
                from packaging.version import Version, InvalidVersion
            except ImportError:
                # packaging should be installed (it's in requirements.txt), but
                # if a deployment's venv is missing it, fail open rather than
                # raise — a missing dep must never turn plugin-enable into a 500.
                Version = None
            if Version is not None:
                try:
                    if Version(__version__) < Version(min_version):
                        return False, (
                            f"Plugin requires OpenAVC v{min_version} or later "
                            f"(current: v{__version__})"
                        )
                except InvalidVersion:
                    pass

        # CONFIG_SCHEMA validation (basic)
        schema = getattr(plugin_class, "CONFIG_SCHEMA", None)
        if schema is not None and not isinstance(schema, dict):
            return False, "CONFIG_SCHEMA must be a dict"

        # MACRO_ACTIONS validation
        macro_actions = getattr(plugin_class, "MACRO_ACTIONS", None)
        if macro_actions is not None:
            valid, error = validate_macro_actions(macro_actions, info["id"], plugin_class)
            if not valid:
                return False, f"MACRO_ACTIONS invalid: {error}"

        # SCRIPT_API validation
        script_api = getattr(plugin_class, "SCRIPT_API", None)
        if script_api is not None:
            valid, error = validate_script_api(script_api, info["id"], plugin_class)
            if not valid:
                return False, f"SCRIPT_API invalid: {error}"

        # EXTENSIONS validation
        extensions = getattr(plugin_class, "EXTENSIONS", None)
        if extensions is not None:
            valid, error = validate_extensions(extensions, info["id"], plugin_class)
            if not valid:
                return False, f"EXTENSIONS invalid: {error}"

        return True, ""

    def is_platform_compatible(self, plugin_class: type) -> bool:
        """Check if a plugin is compatible with the current platform."""
        info = getattr(plugin_class, "PLUGIN_INFO", {})
        platforms = info.get("platforms", ["all"])
        return "all" in platforms or self._platform_id in platforms

    # ──── Lifecycle ────

    async def start_plugins(self, plugins_config: dict[str, Any]) -> None:
        """Start all enabled plugins from the project config."""
        for plugin_id, plugin_entry in plugins_config.items():
            # Handle both PluginConfig objects and raw dicts
            if hasattr(plugin_entry, "enabled"):
                enabled = plugin_entry.enabled
                config = plugin_entry.config if hasattr(plugin_entry, "config") else {}
            else:
                enabled = plugin_entry.get("enabled", False)
                config = plugin_entry.get("config", {})

            plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)

            if plugin_class is None:
                # Missing plugin
                self._missing_plugins[plugin_id] = {
                    "plugin_id": plugin_id,
                    "config": config,
                    "reason": f"Plugin '{plugin_id}' is not installed",
                }
                self._status[plugin_id] = "missing"
                self._state.set(f"plugin.{plugin_id}.missing", True, source="system")
                self._state.set(
                    f"plugin.{plugin_id}.missing_reason",
                    f"Plugin '{plugin_id}' is not installed",
                    source="system",
                )
                await self._events.emit("plugin.missing", {"plugin_id": plugin_id})
                log.warning(f"Plugin '{plugin_id}' is not installed — marked as missing")
                continue

            if not self.is_platform_compatible(plugin_class):
                self._mark_incompatible(plugin_id, plugin_class)
                continue

            if enabled:
                await self.start_plugin(plugin_id, config)

    def _get_lock(self, plugin_id: str) -> asyncio.Lock:
        """Return the per-plugin lifecycle lock, creating it on first use."""
        lock = self._locks.get(plugin_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[plugin_id] = lock
        return lock

    def _mark_incompatible(self, plugin_id: str, plugin_class: type) -> None:
        """Record a plugin as platform-incompatible (distinct from 'error')."""
        info = getattr(plugin_class, "PLUGIN_INFO", {})
        self._incompatible_plugins[plugin_id] = {
            "plugin_id": plugin_id,
            "current_platform": self._platform_id,
            "supported_platforms": info.get("platforms", []),
        }
        self._status[plugin_id] = "incompatible"
        self._errors.pop(plugin_id, None)
        self._state.set(f"plugin.{plugin_id}.incompatible", True, source="system")
        log.warning(
            f"Plugin '{plugin_id}' is not compatible with {self._platform_id}"
        )

    def _clear_missing_state(self, plugin_id: str) -> None:
        """Drop missing-plugin tracking and its broadcast state keys."""
        self._missing_plugins.pop(plugin_id, None)
        self._state.delete(f"plugin.{plugin_id}.missing")
        self._state.delete(f"plugin.{plugin_id}.missing_reason")

    def _clear_plugin_flags(self, plugin_id: str) -> None:
        """Drop incompatible/auto-disabled tracking and their state keys."""
        self._incompatible_plugins.pop(plugin_id, None)
        self._state.delete(f"plugin.{plugin_id}.incompatible")
        self._state.delete(f"plugin.{plugin_id}.auto_disabled")

    async def start_plugin(self, plugin_id: str, config: dict | None = None) -> bool:
        """Start a single plugin. Returns True on success.

        Serialized per-plugin so a concurrent enable / config-update /
        auto-disable on the same plugin can't interleave.
        """
        async with self._get_lock(plugin_id):
            return await self._start_plugin_locked(plugin_id, config)

    async def _start_plugin_locked(
        self, plugin_id: str, config: dict | None = None
    ) -> bool:
        if plugin_id in self._instances:
            # Already running. Don't silently drop a differing config — restart
            # to apply it (config-update relies on the new config taking
            # effect); an identical or empty config is a genuine no-op.
            if config is not None and config != self.get_running_config(plugin_id):
                log.info(
                    f"Plugin '{plugin_id}' already running with a different config "
                    f"— restarting to apply it"
                )
                await self._stop_plugin_locked(plugin_id)
            else:
                return True

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            log.error(f"Plugin '{plugin_id}' class not found in registry")
            return False

        # Platform check first so an incompatible plugin surfaces as
        # 'incompatible' (its own banner) and not a generic 'error'.
        if not self.is_platform_compatible(plugin_class):
            self._mark_incompatible(plugin_id, plugin_class)
            return False

        # Validate manifest
        valid, error = self.validate_manifest(plugin_class)
        if not valid:
            log.error(f"Plugin '{plugin_id}' manifest invalid: {error}")
            self._status[plugin_id] = "error"
            self._errors[plugin_id] = f"Invalid manifest: {error}"
            return False

        info = plugin_class.PLUGIN_INFO
        if config is None:
            config = {}

        # New instance epoch so a queued auto-disable from a previous instance
        # can tell it no longer applies.
        self._epoch_counter += 1
        epoch = self._epoch_counter
        self._instance_epoch[plugin_id] = epoch
        self._auto_disabling.discard(plugin_id)

        # Create registry and API
        registry = PluginRegistry(plugin_id)

        def _on_callback_failure(_pid=plugin_id, _epoch=epoch):
            # One auto-disable per failure streak — past the threshold, don't
            # keep spawning duplicate _auto_disable_plugin tasks.
            if _pid in self._auto_disabling:
                return
            count = self._callback_failures.get(_pid, 0) + 1
            self._callback_failures[_pid] = count
            if count >= MAX_CALLBACK_FAILURES:
                log.error(
                    f"Plugin '{_pid}' hit {count} consecutive callback failures "
                    f"— auto-disabling"
                )
                self._auto_disabling.add(_pid)
                asyncio.create_task(self._auto_disable_plugin(_pid, _epoch))

        def _on_callback_success(_pid=plugin_id):
            # Reset failure counter on success so transient errors don't accumulate
            self._callback_failures.pop(_pid, None)

        api = PluginAPI(
            plugin_id=plugin_id,
            capabilities=info.get("capabilities", []),
            config=config,
            registry=registry,
            state_store=self._state,
            event_bus=self._events,
            macro_engine=self._macros,
            device_manager=self._devices,
            platform_id=self._platform_id,
            save_config_fn=self._save_config_fn,
            log_fn=self._plugin_log,
            failure_reporter=_on_callback_failure,
            success_reporter=_on_callback_success,
        )

        # Instantiate and start
        try:
            instance = plugin_class()
            await asyncio.wait_for(instance.start(api), timeout=PLUGIN_START_TIMEOUT)
            self._register_macro_actions(plugin_id, instance)
            self._register_script_api(plugin_id, instance)

            self._instances[plugin_id] = instance
            self._registries[plugin_id] = registry
            self._apis[plugin_id] = api
            self._status[plugin_id] = "running"
            self._errors.pop(plugin_id, None)
            self._callback_failures.pop(plugin_id, None)

            # Clear missing / incompatible / auto-disabled state from a prior run
            self._clear_missing_state(plugin_id)
            self._clear_plugin_flags(plugin_id)

            # Mount any HTTP router the plugin registered during start()
            if registry.http_router is not None and self._mount_router_fn:
                try:
                    self._mount_router_fn(plugin_id, registry.http_router)
                except Exception:  # Don't let a mount failure abort a started plugin
                    log.exception(
                        f"Failed to mount HTTP router for plugin '{plugin_id}'"
                    )

            await self._events.emit("plugin.started", {"plugin_id": plugin_id})
            log.info(f"Plugin '{plugin_id}' started (v{info.get('version', '?')})")
            return True

        except Exception as e:  # Catch-all: plugin start() runs arbitrary code
            if isinstance(e, asyncio.TimeoutError):
                msg = f"start() timed out after {PLUGIN_START_TIMEOUT}s"
                log.error(f"Plugin '{plugin_id}' {msg}")
            else:
                msg = str(e)
                log.exception(f"Plugin '{plugin_id}' failed to start")
            self._status[plugin_id] = "error"
            self._errors[plugin_id] = msg
            # Clean up any partial registrations
            self._macros.unregister_plugin_actions(plugin_id)
            self._unregister_script_api(plugin_id)
            await registry.cleanup(self._state, self._events)
            await self._events.emit(
                "plugin.error", {"plugin_id": plugin_id, "error": msg}
            )
            return False

    async def stop_plugin(self, plugin_id: str) -> None:
        """Stop a running plugin and clean up all registrations."""
        async with self._get_lock(plugin_id):
            await self._stop_plugin_locked(plugin_id)

    async def _stop_plugin_locked(self, plugin_id: str) -> None:
        instance = self._instances.pop(plugin_id, None)
        registry = self._registries.pop(plugin_id, None)
        api = self._apis.pop(plugin_id, None)
        # Forget the instance epoch so a stale queued auto-disable no-ops.
        self._instance_epoch.pop(plugin_id, None)

        # Unregister macro actions and script API methods before stop() so
        # in-flight macros and scripts can't dispatch to a half-shutdown plugin
        self._macros.unregister_plugin_actions(plugin_id)
        self._unregister_script_api(plugin_id)

        if instance is not None:
            try:
                await asyncio.wait_for(instance.stop(), timeout=PLUGIN_STOP_TIMEOUT)
            except asyncio.TimeoutError:
                log.error(
                    f"Plugin '{plugin_id}' stop() timed out after "
                    f"{PLUGIN_STOP_TIMEOUT}s — continuing teardown"
                )
            except Exception:  # Catch-all: plugin stop() runs arbitrary code
                log.exception(f"Plugin '{plugin_id}' stop() raised an exception")

        if registry is not None:
            await registry.cleanup(self._state, self._events)

        if api is not None:
            api._cancel_all_tasks()

        # Final backstop: every task a PluginAPI creates is named
        # ``plugin.<id>.<name>``, so reap anything carrying this plugin's
        # name that the registry didn't hold. A periodic task that escapes
        # tracking (any generation, any path) keeps executing actions after
        # the plugin is gone — observed in the field as a leaked hold-repeat
        # driving a volume macro 4x/second through stop, uninstall, and
        # reinstall. Nothing named for this plugin survives a stop.
        prefix = f"plugin.{plugin_id}."
        for task in asyncio.all_tasks():
            name = task.get_name() or ""
            if name.startswith(prefix) and not task.done():
                log.warning(
                    f"Reaping orphaned task '{name}' that outlived plugin "
                    f"'{plugin_id}' teardown"
                )
                task.cancel()

        # Remove any HTTP routes the plugin had mounted
        if self._unmount_router_fn:
            try:
                self._unmount_router_fn(plugin_id)
            except Exception:  # Teardown best-effort; never block stop
                log.exception(f"Failed to unmount HTTP router for plugin '{plugin_id}'")

        self._status[plugin_id] = "stopped"
        self._errors.pop(plugin_id, None)
        self._callback_failures.pop(plugin_id, None)
        self._auto_disabling.discard(plugin_id)
        # A normal stop clears the auto-disabled flag; auto-disable re-sets it
        # right after its own stop call, so the True it wants still survives.
        self._state.delete(f"plugin.{plugin_id}.auto_disabled")
        await self._events.emit("plugin.stopped", {"plugin_id": plugin_id})
        log.info(f"Plugin '{plugin_id}' stopped")

    async def stop_all(self) -> None:
        """Stop all running plugins."""
        plugin_ids = list(self._instances.keys())
        for plugin_id in plugin_ids:
            await self.stop_plugin(plugin_id)
        # Cancel any in-flight plugin-log → event tasks.
        for task in list(self._log_tasks):
            task.cancel()
        self._log_tasks.clear()

    # ──── Activate After Install ────

    async def activate_plugin(self, plugin_id: str, config: dict | None = None) -> dict:
        """Activate a previously-missing plugin after install."""
        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            return {"activated": False, "reason": "Plugin still not found in registry"}

        # Clear missing state
        self._clear_missing_state(plugin_id)

        if config is None:
            config = {}

        success = await self.start_plugin(plugin_id, config)
        return {"activated": success}

    # ──── Health Checks ────

    async def get_health(self, plugin_id: str) -> dict:
        """Get a plugin's health check result."""
        instance = self._instances.get(plugin_id)
        if instance is None:
            status = self._status.get(plugin_id, "unknown")
            return {
                "status": status,
                "message": self._errors.get(plugin_id, f"Plugin is {status}"),
            }

        if hasattr(instance, "health_check"):
            try:
                return await asyncio.wait_for(
                    instance.health_check(), timeout=PLUGIN_HEALTH_TIMEOUT
                )
            except asyncio.TimeoutError:
                return {
                    "status": "error",
                    "message": f"Health check timed out after {PLUGIN_HEALTH_TIMEOUT}s",
                }
            except Exception as e:  # Catch-all: plugin health_check() runs arbitrary code
                return {"status": "error", "message": f"Health check failed: {e}"}

        return {"status": "ok", "message": "Running (no health check implemented)"}

    def is_running(self, plugin_id: str) -> bool:
        """Check if a plugin is currently running."""
        return plugin_id in self._instances

    async def apply_config(self, plugin_id: str, new_config: dict) -> bool:
        """Hot-apply new config to a running plugin, if it opts in.

        A plugin opts in by defining ``async def on_config_changed(self,
        new_config) -> bool``. The loader swaps the live ``api.config``
        first, then awaits the hook; a ``True`` return means the plugin
        handled the change and no restart is needed. ``False`` or any
        exception falls back to the normal stop/start restart (the caller's
        job — see ``restart_or_apply``), which is also the safety net for a
        buggy hook.
        """
        instance = self._instances.get(plugin_id)
        api = self._apis.get(plugin_id)
        hook = getattr(instance, "on_config_changed", None) if instance else None
        if instance is None or api is None or not callable(hook):
            return False
        try:
            api._update_config(new_config)
            handled = await asyncio.wait_for(
                hook(dict(new_config)), timeout=PLUGIN_APPLY_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.warning(
                f"Plugin '{plugin_id}' on_config_changed timed out after "
                f"{PLUGIN_APPLY_TIMEOUT}s; falling back to restart"
            )
            return False
        except Exception as e:  # Catch-all: hook runs arbitrary plugin code
            log.warning(
                f"Plugin '{plugin_id}' on_config_changed failed ({e}); "
                f"falling back to restart"
            )
            return False
        if handled is True:
            log.info(f"Plugin '{plugin_id}' hot-applied a config change")
            return True
        return False

    async def restart_or_apply(self, plugin_id: str, new_config: dict) -> bool:
        """Apply new config to a running plugin: hot when the plugin opts in
        via ``on_config_changed``, otherwise stop/start. Every config-write
        path (REST, cloud AI) routes through here so behavior is identical.

        Holds the per-plugin lock across the whole check → hot-apply →
        stop → start sequence. The cloud AI layer fans tool calls out as
        tasks, so two overlapping updates (or an update racing a disable)
        could otherwise interleave in the not-running window mid-restart —
        one returning "nothing to do" while the other restarts with a config
        the project file no longer holds.

        Returns False when the plugin wasn't running (nothing to do)."""
        async with self._get_lock(plugin_id):
            if not self.is_running(plugin_id):
                return False
            if await self.apply_config(plugin_id, new_config):
                return True
            await self._stop_plugin_locked(plugin_id)
            await self._start_plugin_locked(plugin_id, new_config)
            return True

    def clear_missing(self, plugin_id: str) -> None:
        """Remove a plugin from the missing-plugins tracker."""
        self._missing_plugins.pop(plugin_id, None)

    def get_known_plugin_ids(self) -> set[str]:
        """Return IDs of all plugins that have been loaded, are missing, or have a status."""
        ids: set[str] = set()
        ids.update(self._instances.keys())
        ids.update(pid for pid, s in self._status.items()
                   if s in ("stopped", "missing", "incompatible", "error"))
        return ids

    def remove_plugin_tracking(self, plugin_id: str) -> None:
        """Remove all internal tracking for a plugin and its broadcast state.

        Called when a plugin is removed from the project or uninstalled. Drops
        the in-memory trackers and deletes the ``plugin.<id>.*`` status keys so
        a stale incompatible/auto-disabled/missing flag isn't broadcast to every
        connecting panel forever.
        """
        self._status.pop(plugin_id, None)
        self._callback_failures.pop(plugin_id, None)
        self._auto_disabling.discard(plugin_id)
        self._instance_epoch.pop(plugin_id, None)
        self._locks.pop(plugin_id, None)
        self._clear_missing_state(plugin_id)
        self._clear_plugin_flags(plugin_id)

    def get_running_config(self, plugin_id: str) -> dict[str, Any]:
        """Return the config dict of a running plugin, or empty dict if not running."""
        api = self._apis.get(plugin_id)
        return api._config if api else {}

    # ──── Info & Status ────

    def list_plugins(self) -> list[dict[str, Any]]:
        """List all known plugins with status information."""
        plugins = []

        # Registered plugin classes
        seen = set()
        for plugin_id, plugin_class in _PLUGIN_CLASS_REGISTRY.items():
            seen.add(plugin_id)
            info = plugin_class.PLUGIN_INFO
            status = self._status.get(plugin_id, "stopped")
            entry = {
                "plugin_id": plugin_id,
                "name": info.get("name", plugin_id),
                "version": info.get("version", ""),
                "author": info.get("author", ""),
                "description": info.get("description", ""),
                "category": info.get("category", ""),
                "status": status,
                "platforms": info.get("platforms", ["all"]),
                "capabilities": info.get("capabilities", []),
                "installed": True,
                "compatible": self.is_platform_compatible(plugin_class),
            }
            if status == "error":
                entry["error"] = self._errors.get(plugin_id, "")
            plugins.append(entry)

        # Missing plugins (referenced in project but not installed)
        for plugin_id, missing_info in self._missing_plugins.items():
            if plugin_id not in seen:
                seen.add(plugin_id)
                plugins.append({
                    "plugin_id": plugin_id,
                    "name": plugin_id,
                    "status": "missing",
                    "installed": False,
                    "compatible": True,
                    "missing_reason": missing_info.get("reason", ""),
                })

        # Incompatible plugins that aren't in the class registry (e.g. failed to
        # register but were marked incompatible). Registered-but-incompatible
        # plugins already surface above with status 'incompatible'; this is the
        # fallback so the tracker dict can't hold dead, un-surfaced state.
        for plugin_id, incompat_info in self._incompatible_plugins.items():
            if plugin_id not in seen:
                seen.add(plugin_id)
                plugins.append({
                    "plugin_id": plugin_id,
                    "name": plugin_id,
                    "status": "incompatible",
                    "installed": True,
                    "compatible": False,
                    "supported_platforms": incompat_info.get(
                        "supported_platforms", []
                    ),
                })

        return plugins

    def get_plugin_info(self, plugin_id: str) -> dict[str, Any] | None:
        """Get detailed info for a specific plugin."""
        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            missing = self._missing_plugins.get(plugin_id)
            if missing:
                return {
                    "plugin_id": plugin_id,
                    "status": "missing",
                    "installed": False,
                    **missing,
                }
            return None

        info = plugin_class.PLUGIN_INFO
        status = self._status.get(plugin_id, "stopped")
        result = {
            "plugin_id": plugin_id,
            "name": info.get("name", plugin_id),
            "version": info.get("version", ""),
            "author": info.get("author", ""),
            "description": info.get("description", ""),
            "usage": info.get("usage", ""),
            "category": info.get("category", ""),
            "license": info.get("license", ""),
            "status": status,
            "platforms": info.get("platforms", ["all"]),
            "capabilities": info.get("capabilities", []),
            "dependencies": info.get("dependencies", []),
            "installed": True,
            "compatible": self.is_platform_compatible(plugin_class),
            "has_config_schema": hasattr(plugin_class, "CONFIG_SCHEMA"),
            "has_surface_layout": hasattr(plugin_class, "SURFACE_LAYOUT"),
            "has_extensions": hasattr(plugin_class, "EXTENSIONS"),
            "has_macro_actions": bool(getattr(plugin_class, "MACRO_ACTIONS", None)),
            "has_script_api": bool(getattr(plugin_class, "SCRIPT_API", None)),
        }

        if status == "error":
            result["error"] = self._errors.get(plugin_id, "")

        # Include config schema if available
        schema = getattr(plugin_class, "CONFIG_SCHEMA", None)
        if schema:
            result["config_schema"] = schema

        # Include extensions if available
        extensions = getattr(plugin_class, "EXTENSIONS", None)
        if extensions:
            result["extensions"] = extensions

        # Include surface layout if available
        surface = getattr(plugin_class, "SURFACE_LAYOUT", None)
        if surface:
            result["surface_layout"] = surface

        # Include macro actions if available — strip 'handler' (internal-only)
        macro_actions = getattr(plugin_class, "MACRO_ACTIONS", None)
        if macro_actions:
            result["macro_actions"] = {
                action_type: {k: v for k, v in spec.items() if k != "handler"}
                for action_type, spec in macro_actions.items()
            }

        # Include script API methods if available — strip 'handler' (internal)
        script_api = getattr(plugin_class, "SCRIPT_API", None)
        if script_api:
            result["script_api"] = {
                method_name: {k: v for k, v in spec.items() if k != "handler"}
                for method_name, spec in script_api.items()
            }

        return result

    def get_plugin_status(self, plugin_id: str) -> str:
        """Get the current status of a plugin."""
        return self._status.get(plugin_id, "unknown")

    def get_all_macro_actions(self) -> list[dict[str, Any]]:
        """Get all macro actions from running plugins.

        Returns a flat list, each entry shaped for the macro builder:
        ``{action_type, plugin_id, plugin_name, label, description?, icon?, params}``
        """
        result: list[dict[str, Any]] = []
        for plugin_id, instance in self._instances.items():
            plugin_class = type(instance)
            macro_actions = getattr(plugin_class, "MACRO_ACTIONS", None)
            if not macro_actions:
                continue
            info = plugin_class.PLUGIN_INFO
            plugin_name = info.get("name", plugin_id)
            for action_type, spec in macro_actions.items():
                entry = {
                    "action_type": action_type,
                    "plugin_id": plugin_id,
                    "plugin_name": plugin_name,
                    "label": spec.get("label") or action_type,
                    "params": spec.get("params", []),
                }
                if spec.get("description"):
                    entry["description"] = spec["description"]
                if spec.get("icon"):
                    entry["icon"] = spec["icon"]
                result.append(entry)
        return result

    def get_all_script_api(self) -> list[dict[str, Any]]:
        """Get all script API methods from running plugins.

        Returns a flat list, each entry shaped for the script editor
        autocomplete and hover docs:
        ``{plugin_id, plugin_name, method, doc?, sync}``
        """
        result: list[dict[str, Any]] = []
        for plugin_id, instance in self._instances.items():
            plugin_class = type(instance)
            script_api = getattr(plugin_class, "SCRIPT_API", None)
            if not script_api:
                continue
            info = plugin_class.PLUGIN_INFO
            plugin_name = info.get("name", plugin_id)
            for method_name, spec in script_api.items():
                entry = {
                    "plugin_id": plugin_id,
                    "plugin_name": plugin_name,
                    "method": method_name,
                    "sync": bool(spec.get("sync", False)),
                }
                if spec.get("doc"):
                    entry["doc"] = spec["doc"]
                result.append(entry)
        return result

    def get_all_extensions(self) -> dict[str, Any]:
        """Get all extensions from running plugins, organized by extension type.

        Each plugin's contribution is collected under its own try/except and
        every entry is shape-checked, so one plugin shipping a malformed
        EXTENSIONS (a non-dict, a list where a dict was expected, a non-dict
        element) can't break the endpoint for every other plugin (A125). IDs are
        de-duplicated across plugins so a second plugin can't silently shadow a
        first plugin's view/card/element id (A619).

        Sanitizes each extension's ``state_pattern`` to keep plugins inside their
        ``plugin.<id>.*`` namespace. A hostile or careless extension that
        declares ``state_pattern: "*"`` or ``"device.*"`` would otherwise let a
        plugin's sidebar scrape unrelated state (A71).

        For panel_elements, also sanitizes ``sandbox_permissions`` and
        ``allow_features`` against whitelists. The panel runtime trusts
        these values to set the iframe's sandbox tokens and Permissions-
        Policy allow attribute; a plugin that tries to declare an
        unrecognized or dangerous token gets it silently stripped here.
        """
        result: dict[str, list] = {
            "views": [],
            "device_panels": [],
            "status_cards": [],
            "context_actions": [],
            "panel_elements": [],
        }
        # (ext_type, id) pairs already emitted — first plugin to claim an id wins.
        seen_ids: set[tuple[str, str]] = set()

        for plugin_id, instance in self._instances.items():
            try:
                plugin_class = type(instance)
                extensions = getattr(plugin_class, "EXTENSIONS", None)
                if extensions is None:
                    continue
                if not isinstance(extensions, dict):
                    log.warning(
                        "Plugin '%s' EXTENSIONS is not a dict (%s); skipping",
                        plugin_id, type(extensions).__name__,
                    )
                    continue

                info = plugin_class.PLUGIN_INFO
                plugin_name = info.get("name", plugin_id)
                namespace_prefix = f"plugin.{plugin_id}."

                for ext_type in result:
                    ext_list = extensions.get(ext_type, [])
                    if not isinstance(ext_list, list):
                        log.warning(
                            "Plugin '%s' EXTENSIONS['%s'] is not a list; skipping",
                            plugin_id, ext_type,
                        )
                        continue
                    id_field = _EXTENSION_ID_FIELD[ext_type]
                    for ext in ext_list:
                        if not isinstance(ext, dict):
                            log.warning(
                                "Plugin '%s' %s entry is not a dict; skipping",
                                plugin_id, ext_type,
                            )
                            continue
                        ext_id = ext.get(id_field)
                        if isinstance(ext_id, str) and ext_id:
                            key = (ext_type, ext_id)
                            if key in seen_ids:
                                log.warning(
                                    "Plugin '%s' %s '%s' duplicates an already-"
                                    "registered id; skipping",
                                    plugin_id, ext_type, ext_id,
                                )
                                continue
                            seen_ids.add(key)
                        safe_ext = {
                            **ext,
                            "plugin_id": plugin_id,
                            "plugin_name": plugin_name,
                        }
                        pattern = safe_ext.get("state_pattern")
                        if pattern is not None:
                            safe_ext["state_pattern"] = self._sanitize_state_pattern(
                                pattern, plugin_id, namespace_prefix
                            )
                        if ext_type == "panel_elements":
                            safe_ext["sandbox_permissions"] = self._sanitize_token_list(
                                safe_ext.get("sandbox_permissions"),
                                _ALLOWED_SANDBOX_PERMISSIONS,
                                plugin_id, "sandbox_permissions",
                            )
                            safe_ext["allow_features"] = self._sanitize_token_list(
                                safe_ext.get("allow_features"),
                                _ALLOWED_ALLOW_FEATURES,
                                plugin_id, "allow_features",
                            )
                            # Surface the plugin's declared capabilities so the
                            # panel iframe bridge can gate openavc:action requests
                            # (device.command / state.set) against them, mirroring
                            # the server-side PluginAPI capability checks.
                            safe_ext["capabilities"] = list(
                                info.get("capabilities", [])
                            )
                        result[ext_type].append(safe_ext)
            except Exception:  # One bad plugin must not blank the whole endpoint
                log.exception(
                    "Failed to collect extensions for plugin '%s'", plugin_id
                )

        return result

    @staticmethod
    def _sanitize_token_list(
        raw: Any, allowed: frozenset[str], plugin_id: str, field_name: str
    ) -> list[str]:
        """Filter a string list against a whitelist, dropping anything not on it.

        Returns an empty list for missing, non-list, or non-string-element input.
        Unknown tokens are dropped with a log warning so plugin authors can
        diagnose typos and so policy escapes are visible in the system log.
        """
        if raw is None:
            return []
        if not isinstance(raw, list):
            log.warning(
                "Plugin '%s' %s is not a list (%r); ignoring",
                plugin_id, field_name, raw,
            )
            return []
        result: list[str] = []
        for token in raw:
            if not isinstance(token, str):
                log.warning(
                    "Plugin '%s' %s contains non-string token %r; ignoring",
                    plugin_id, field_name, token,
                )
                continue
            if token not in allowed:
                log.warning(
                    "Plugin '%s' %s requests unsupported token %r; ignoring "
                    "(allowed: %s)",
                    plugin_id, field_name, token, sorted(allowed),
                )
                continue
            if token not in result:
                result.append(token)
        return result

    @staticmethod
    def _sanitize_state_pattern(
        pattern: Any, plugin_id: str, namespace_prefix: str
    ) -> str:
        """Force a plugin's state_pattern into its own namespace.

        Returns the original pattern if it's safely scoped; otherwise rewrites
        to the default ``plugin.<id>.*`` and logs a warning. ``{device_id}``
        and similar placeholders inside the namespace are allowed.
        """
        default_pattern = f"{namespace_prefix}*"
        if not isinstance(pattern, str):
            log.warning(
                "Plugin '%s' state_pattern is not a string (%r); using default %r",
                plugin_id, pattern, default_pattern,
            )
            return default_pattern
        trimmed = pattern.strip()
        if not trimmed:
            return default_pattern
        if not trimmed.startswith(namespace_prefix):
            log.warning(
                "Plugin '%s' state_pattern %r escapes its namespace; "
                "rewriting to %r",
                plugin_id, trimmed, default_pattern,
            )
            return default_pattern
        return trimmed

    # ──── Validation Endpoint Support ────

    def validate_plugins(self, plugins_config: dict[str, Any]) -> dict[str, Any]:
        """
        Validate all plugins referenced in the project.

        Returns dict with available, missing, and platform_warnings lists.
        """
        available = []
        missing = []
        platform_warnings = []

        for plugin_id in plugins_config:
            plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)

            if plugin_class is None:
                missing.append({
                    "plugin_id": plugin_id,
                    "affected_config": True,
                })
                continue

            info = plugin_class.PLUGIN_INFO

            if not self.is_platform_compatible(plugin_class):
                platform_warnings.append({
                    "plugin_id": plugin_id,
                    "current_platform": self._platform_id,
                    "supported_platforms": info.get("platforms", []),
                    "message": (
                        f"Plugin '{info.get('name', plugin_id)}' is not "
                        f"compatible with {self._platform_id}"
                    ),
                })
                continue

            available.append({
                "plugin_id": plugin_id,
                "plugin_name": info.get("name", ""),
                "version": info.get("version", ""),
                "status": self._status.get(plugin_id, "stopped"),
            })

        return {
            "available": available,
            "missing": missing,
            "platform_warnings": platform_warnings,
        }

    # ──── Auto-Disable ────

    async def _auto_disable_plugin(self, plugin_id: str, epoch: int) -> None:
        """Stop a plugin that has exceeded MAX_CALLBACK_FAILURES.

        Held under the per-plugin lock and gated on the instance epoch so a
        queued auto-disable can't kill a plugin that was already stopped or
        restarted in the meantime (the failures it counted belonged to a
        previous instance).
        """
        async with self._get_lock(plugin_id):
            self._auto_disabling.discard(plugin_id)
            if (
                plugin_id not in self._instances
                or self._instance_epoch.get(plugin_id) != epoch
            ):
                return
            await self._stop_plugin_locked(plugin_id)
            self._status[plugin_id] = "error"
            self._errors[plugin_id] = (
                f"Auto-disabled after {MAX_CALLBACK_FAILURES} consecutive "
                f"callback failures"
            )
            self._state.set(
                f"plugin.{plugin_id}.auto_disabled", True, source="system"
            )
            await self._events.emit(
                "plugin.auto_disabled", {"plugin_id": plugin_id}
            )

    # ──── Internal ────

    def _register_macro_actions(self, plugin_id: str, instance: Any) -> None:
        """Register every MACRO_ACTIONS entry with the macro engine.

        Validation already ran in validate_manifest, so we only need to bind
        the handler method to the instance and pass it to the engine.
        """
        macro_actions = getattr(type(instance), "MACRO_ACTIONS", None)
        if not macro_actions:
            return
        for action_type, spec in macro_actions.items():
            handler = getattr(instance, spec["handler"])
            label = spec.get("label") or action_type
            self._macros.register_plugin_action(action_type, handler, plugin_id, label)

    def _register_script_api(self, plugin_id: str, instance: Any) -> None:
        """Register every SCRIPT_API entry with the openavc.plugins proxy."""
        script_api = getattr(type(instance), "SCRIPT_API", None)
        if not script_api:
            return
        # Late import to avoid pulling script_api at module load — keeps the
        # loader importable in test environments that mock the script engine.
        from server.core.script_api import plugins as plugins_proxy
        for method_name, spec in script_api.items():
            handler = getattr(instance, spec["handler"])
            plugins_proxy._register_method(plugin_id, method_name, handler)

    def _unregister_script_api(self, plugin_id: str) -> None:
        """Remove all script methods registered by a plugin."""
        from server.core.script_api import plugins as plugins_proxy
        plugins_proxy._unregister_plugin(plugin_id)

    def _plugin_log(self, plugin_id: str, message: str, level: str = "info") -> None:
        """Log a message from a plugin."""
        logger_fn = getattr(log, level, log.info)
        logger_fn(f"[Plugin:{plugin_id}] {message}")
        # Also emit as event for the IDE system log. The mirror task is tracked
        # and bounded — a plugin logging in a tight loop can't spawn unbounded
        # one-shot tasks on the shared event loop; past the cap the mirror is
        # dropped (the logger line above is already written).
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if len(self._log_tasks) >= MAX_PENDING_LOG_EVENTS:
            return
        task = loop.create_task(
            self._events.emit("log.plugin", {
                "plugin_id": plugin_id,
                "message": message,
                "level": level,
            })
        )
        self._log_tasks.add(task)
        task.add_done_callback(self._log_tasks.discard)
