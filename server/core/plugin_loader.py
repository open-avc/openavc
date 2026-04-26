"""
Plugin loader — discovery, validation, lifecycle, and error isolation.

Scans plugin_repo/ for valid plugin packages, validates manifests,
manages the start/stop lifecycle, and handles missing/incompatible plugins.
"""

import asyncio
import importlib
import os
import platform
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
    "state_read", "state_write", "event_emit", "event_subscribe",
    "macro_execute", "device_command", "network_listen", "usb_access",
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
        # Config save callback
        self._save_config_fn = None

    def set_save_config_fn(self, fn):
        """Set the callback for saving plugin config to the project file."""
        self._save_config_fn = fn

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

        # Add .deps to sys.path if it exists
        deps_path = str(plugin_repo_dir / ".deps")
        if os.path.isdir(deps_path) and deps_path not in sys.path:
            sys.path.insert(0, deps_path)

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
        """Import a Python file and find a class with PLUGIN_INFO."""
        module_name = f"plugin_{plugin_dir.name}"

        # Add plugin dir to path temporarily for relative imports
        dir_str = str(plugin_dir)
        added_to_path = False
        if dir_str not in sys.path:
            sys.path.insert(0, dir_str)
            added_to_path = True

        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the plugin class
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        hasattr(attr, "PLUGIN_INFO") and
                        isinstance(attr.PLUGIN_INFO, dict) and
                        "id" in attr.PLUGIN_INFO):
                    return attr

        except Exception:  # Catch-all: exec_module runs arbitrary plugin code
            log.exception(f"Error loading plugin file {filepath}")
        finally:
            if added_to_path:
                try:
                    sys.path.remove(dir_str)
                except ValueError:
                    pass

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
            from packaging.version import Version, InvalidVersion
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
                info = plugin_class.PLUGIN_INFO
                self._incompatible_plugins[plugin_id] = {
                    "plugin_id": plugin_id,
                    "current_platform": self._platform_id,
                    "supported_platforms": info.get("platforms", []),
                }
                self._status[plugin_id] = "incompatible"
                self._state.set(
                    f"plugin.{plugin_id}.incompatible", True, source="system"
                )
                log.warning(
                    f"Plugin '{plugin_id}' is not compatible with {self._platform_id}"
                )
                continue

            if enabled:
                await self.start_plugin(plugin_id, config)

    async def start_plugin(self, plugin_id: str, config: dict | None = None) -> bool:
        """Start a single plugin. Returns True on success."""
        if plugin_id in self._instances:
            log.warning(f"Plugin '{plugin_id}' is already running")
            return True

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            log.error(f"Plugin '{plugin_id}' class not found in registry")
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

        # Create registry and API
        registry = PluginRegistry(plugin_id)

        def _on_callback_failure(_pid=plugin_id):
            count = self._callback_failures.get(_pid, 0) + 1
            self._callback_failures[_pid] = count
            if count >= MAX_CALLBACK_FAILURES:
                log.error(
                    f"Plugin '{_pid}' hit {count} consecutive callback failures "
                    f"— auto-disabling"
                )
                asyncio.create_task(self._auto_disable_plugin(_pid))

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
            await instance.start(api)

            self._instances[plugin_id] = instance
            self._registries[plugin_id] = registry
            self._apis[plugin_id] = api
            self._status[plugin_id] = "running"
            self._errors.pop(plugin_id, None)
            self._callback_failures.pop(plugin_id, None)

            # Clear any missing state
            self._missing_plugins.pop(plugin_id, None)
            self._state.set(f"plugin.{plugin_id}.missing", None, source="system")
            self._state.set(f"plugin.{plugin_id}.missing_reason", None, source="system")

            await self._events.emit("plugin.started", {"plugin_id": plugin_id})
            log.info(f"Plugin '{plugin_id}' started (v{info.get('version', '?')})")
            return True

        except Exception as e:  # Catch-all: plugin start() runs arbitrary code
            log.exception(f"Plugin '{plugin_id}' failed to start")
            self._status[plugin_id] = "error"
            self._errors[plugin_id] = str(e)
            # Clean up any partial registrations
            await registry.cleanup(self._state, self._events)
            await self._events.emit(
                "plugin.error", {"plugin_id": plugin_id, "error": str(e)}
            )
            return False

    async def stop_plugin(self, plugin_id: str) -> None:
        """Stop a running plugin and clean up all registrations."""
        instance = self._instances.pop(plugin_id, None)
        registry = self._registries.pop(plugin_id, None)
        self._apis.pop(plugin_id, None)

        if instance is not None:
            try:
                await instance.stop()
            except Exception:  # Catch-all: plugin stop() runs arbitrary code
                log.exception(f"Plugin '{plugin_id}' stop() raised an exception")

        if registry is not None:
            await registry.cleanup(self._state, self._events)

        self._status[plugin_id] = "stopped"
        self._errors.pop(plugin_id, None)
        self._callback_failures.pop(plugin_id, None)
        await self._events.emit("plugin.stopped", {"plugin_id": plugin_id})
        log.info(f"Plugin '{plugin_id}' stopped")

    async def stop_all(self) -> None:
        """Stop all running plugins."""
        plugin_ids = list(self._instances.keys())
        for plugin_id in plugin_ids:
            await self.stop_plugin(plugin_id)

    # ──── Activate After Install ────

    async def activate_plugin(self, plugin_id: str, config: dict | None = None) -> dict:
        """Activate a previously-missing plugin after install."""
        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            return {"activated": False, "reason": "Plugin still not found in registry"}

        # Clear missing state
        self._missing_plugins.pop(plugin_id, None)
        self._state.set(f"plugin.{plugin_id}.missing", None, source="system")
        self._state.set(f"plugin.{plugin_id}.missing_reason", None, source="system")

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
                return await instance.health_check()
            except Exception as e:  # Catch-all: plugin health_check() runs arbitrary code
                return {"status": "error", "message": f"Health check failed: {e}"}

        return {"status": "ok", "message": "Running (no health check implemented)"}

    def is_running(self, plugin_id: str) -> bool:
        """Check if a plugin is currently running."""
        return plugin_id in self._instances

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
        """Remove all internal tracking for a plugin (status, missing, incompatible)."""
        self._status.pop(plugin_id, None)
        self._missing_plugins.pop(plugin_id, None)
        self._incompatible_plugins.pop(plugin_id, None)

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
                plugins.append({
                    "plugin_id": plugin_id,
                    "name": plugin_id,
                    "status": "missing",
                    "installed": False,
                    "compatible": True,
                    "missing_reason": missing_info.get("reason", ""),
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

        return result

    def get_plugin_status(self, plugin_id: str) -> str:
        """Get the current status of a plugin."""
        return self._status.get(plugin_id, "unknown")

    def get_all_extensions(self) -> dict[str, Any]:
        """Get all extensions from running plugins, organized by extension type."""
        result: dict[str, list] = {
            "views": [],
            "device_panels": [],
            "status_cards": [],
            "context_actions": [],
            "panel_elements": [],
        }

        for plugin_id, instance in self._instances.items():
            plugin_class = type(instance)
            extensions = getattr(plugin_class, "EXTENSIONS", None)
            if not extensions:
                continue

            info = plugin_class.PLUGIN_INFO
            plugin_name = info.get("name", plugin_id)

            for ext_type in result:
                for ext in extensions.get(ext_type, []):
                    result[ext_type].append({
                        **ext,
                        "plugin_id": plugin_id,
                        "plugin_name": plugin_name,
                    })

        return result

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

    async def _auto_disable_plugin(self, plugin_id: str) -> None:
        """Stop a plugin that has exceeded MAX_CALLBACK_FAILURES."""
        if plugin_id not in self._instances:
            return
        await self.stop_plugin(plugin_id)
        self._status[plugin_id] = "error"
        self._errors[plugin_id] = (
            f"Auto-disabled after {MAX_CALLBACK_FAILURES} consecutive callback failures"
        )
        self._state.set(f"plugin.{plugin_id}.auto_disabled", True, source="system")
        await self._events.emit("plugin.auto_disabled", {"plugin_id": plugin_id})

    # ──── Internal ────

    def _plugin_log(self, plugin_id: str, message: str, level: str = "info") -> None:
        """Log a message from a plugin."""
        logger_fn = getattr(log, level, log.info)
        logger_fn(f"[Plugin:{plugin_id}] {message}")
        # Also emit as event for the IDE system log
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon(
            lambda: asyncio.create_task(
                self._events.emit("log.plugin", {
                    "plugin_id": plugin_id,
                    "message": message,
                    "level": level,
                })
            )
        )
