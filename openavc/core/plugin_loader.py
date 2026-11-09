"""
Plugin loader — discovery, validation, lifecycle, and error isolation.

Scans plugin_repo/ for valid plugin packages, validates manifests,
manages the start/stop lifecycle, and handles missing/incompatible plugins.
"""

import asyncio
import functools
import platform
import threading
from pathlib import Path
from typing import Any

from openavc.core import plugin_manifest
from openavc.core.plugin_api import PluginAPI, PluginRefusal
from openavc.core.plugin_extensions import (
    collect_extensions,
    sandbox_permissions,
    validate_extensions as validate_extensions,
)
from openavc.core.plugin_import import find_plugin_class, make_deps_loadable, plugin_folders
from openavc.core.plugin_manifest import (
    platform_supported,
    validate_macro_actions as validate_macro_actions,
    validate_script_api as validate_script_api,
)
from openavc.core.plugin_registry import PluginRegistry
from openavc.utils.logger import get_logger

log = get_logger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback for fire-and-forget tasks: surface failures that would
    otherwise vanish as a 'Task exception was never retrieved' GC warning."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(
            f"Background task {task.get_name()!r} failed: {exc}", exc_info=exc
        )


# Global plugin class registry: plugin_id -> plugin_class
_PLUGIN_CLASS_REGISTRY: dict[str, type] = {}
_REGISTRY_LOCK = threading.Lock()

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


# What a bounded stop() logs, by when it runs: (after the timeout, when it raised).
_STOP_WORDING = {
    "stop": (" — continuing teardown", "raised an exception"),
    "failed_start": (" while cleaning up a failed start", "raised while cleaning up a failed start"),
}


async def _bounded_stop(plugin_id: str, instance: Any, *, during: str) -> None:
    """Call a plugin's stop() under PLUGIN_STOP_TIMEOUT, every exception logged
    and swallowed: a stop() that hangs or raises never stops the platform's own
    teardown. ``during`` is a ``_STOP_WORDING`` key."""
    timed_out, raised = _STOP_WORDING[during]
    try:
        await asyncio.wait_for(instance.stop(), timeout=PLUGIN_STOP_TIMEOUT)
    except asyncio.TimeoutError:
        log.error(f"Plugin '{plugin_id}' stop() timed out after {PLUGIN_STOP_TIMEOUT}s{timed_out}")
    except Exception:  # Catch-all: plugin stop() runs arbitrary code
        log.exception(f"Plugin '{plugin_id}' stop() {raised}")


def _start_failure_text(plugin_id: str, error: Exception) -> str:
    """The error a failed start() stores, logged as it is stored."""
    if isinstance(error, asyncio.TimeoutError):
        msg = f"start() timed out after {PLUGIN_START_TIMEOUT}s"
        log.error(f"Plugin '{plugin_id}' {msg}")
        return msg
    msg = str(error)
    # The type and text in the message itself, not only the traceback: the Log
    # view shows a record's message, and the cloud's answer for an unmarked
    # failure sends the reader there.
    log.exception(f"Plugin '{plugin_id}' failed to start: {type(error).__name__}: {error}")
    return msg


def _reap_named_tasks(plugin_id: str) -> None:
    """Cancel every task still running under the plugin's name.

    Every task a PluginAPI creates is named ``plugin.<id>.<name>``, so this
    catches anything the registry didn't hold. A periodic task that escapes
    tracking keeps executing actions after the plugin is gone (seen in the
    field: a leaked hold-repeat drove a volume macro four times a second
    through a stop, an uninstall and a reinstall). Nothing named for the plugin
    survives a stop.
    """
    prefix = f"plugin.{plugin_id}."
    for task in asyncio.all_tasks():
        name = task.get_name() or ""
        if name.startswith(prefix) and not task.done():
            log.warning(
                f"Reaping orphaned task '{name}' that outlived plugin "
                f"'{plugin_id}' teardown"
            )
            task.cancel()


def _call_hook(hook: Any, failure: str, *args: Any) -> None:
    """Call one of the app's router hooks. A failure is logged, never raised:
    a mount must not abort a started plugin, nor an unmount block a stop."""
    try:
        hook(*args)
    except Exception:  # The hook is the app's; isolate it like plugin code
        log.exception(failure)


def _as_written(declared: Any) -> Any:
    return declared


def _without_handlers(declared: dict) -> dict:
    """Each entry without its handler, a method name nothing outside the plugin needs."""
    return {
        name: {k: v for k, v in spec.items() if k != "handler"}
        for name, spec in declared.items()
    }


# The class-level declarations get_plugin_info hands on, in order, when the
# class has them: (result key, attribute, how it is shown).
_DESCRIBED = (
    ("config_schema", "CONFIG_SCHEMA", _as_written),
    ("extensions", "EXTENSIONS", _as_written),
    ("surface_layout", "SURFACE_LAYOUT", _as_written),
    ("macro_actions", "MACRO_ACTIONS", _without_handlers),
    ("script_api", "SCRIPT_API", _without_handlers),
)


def _described_declarations(plugin_class: type) -> dict[str, Any]:
    described = {}
    for key, attr, shown in _DESCRIBED:
        declared = getattr(plugin_class, attr, None)
        if declared:
            described[key] = shown(declared)
    return described


def _listed_missing(plugin_id: str, missing: dict) -> dict[str, Any]:
    """A plugin the project names that is not installed."""
    return {
        "plugin_id": plugin_id,
        "name": plugin_id,
        "status": "missing",
        "installed": False,
        "compatible": True,
        "missing_reason": missing.get("reason", ""),
    }


def _listed_incompatible(plugin_id: str, incompatible: dict) -> dict[str, Any]:
    """A plugin marked incompatible whose class is not registered (one that
    is registered is listed with the installed plugins)."""
    return {
        "plugin_id": plugin_id,
        "name": plugin_id,
        "status": "incompatible",
        "installed": True,
        "compatible": False,
        "supported_platforms": incompatible.get("supported_platforms", []),
    }


def _builder_action(plugin_id: str, plugin_name: str, action_type: str, spec: dict) -> dict[str, Any]:
    entry = {
        "action_type": action_type,
        "plugin_id": plugin_id,
        "plugin_name": plugin_name,
        "label": spec.get("label") or action_type,
        "params": spec.get("params", []),
    }
    for optional in ("description", "icon"):
        if spec.get(optional):
            entry[optional] = spec[optional]
    return entry


def _script_method(plugin_id: str, plugin_name: str, method_name: str, spec: dict) -> dict[str, Any]:
    entry = {
        "plugin_id": plugin_id,
        "plugin_name": plugin_name,
        "method": method_name,
        "sync": bool(spec.get("sync", False)),
    }
    if spec.get("doc"):
        entry["doc"] = spec["doc"]
    return entry


class PluginLoader:
    """
    Manages plugin discovery, validation, lifecycle, and error isolation.

    One PluginLoader instance per Engine. Plugins are loaded from plugin_repo/
    and started based on the project file's plugins configuration.
    """

    def __init__(self, state_store, event_bus, macro_engine, device_manager,
                 cloud_agent_provider=None):
        self._state = state_store
        self._events = event_bus
        self._macros = macro_engine
        self._devices = device_manager
        # A callable rather than the agent itself — the engine builds the agent
        # after the loader exists, and replaces it when cloud config changes.
        self._cloud_agent_provider = cloud_agent_provider
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
        # Set once the whole process is stopping, so the declared-port file is
        # not rewritten to reflect a running set that is only empty because we
        # are exiting. Never cleared: a loader does not come back.
        self._shutting_down = False
        # What sync_declared_ports last acted on, so it can be called freely.
        # None rather than () so the first call always runs, including the one
        # that finds nothing and writes the empty list that closes stale rules.
        self._synced_ports: tuple | None = None
        # Plugin status: plugin_id -> status string
        self._status: dict[str, str] = {}
        # Error messages: plugin_id -> error string
        self._errors: dict[str, str] = {}
        # The plugins whose stored error is the text of an exception start()
        # raised that was not a PluginRefusal. The card shows it; nothing that
        # goes to the cloud does (see error_for_cloud). Kept by _set_error and
        # _clear_error alongside _errors.
        self._unmarked_errors: set[str] = set()
        # Callback failure counts: plugin_id -> count
        self._callback_failures: dict[str, int] = {}
        # Plugins with an auto-disable in flight (so repeated failures past the
        # threshold don't each spawn a duplicate _auto_disable_plugin task).
        self._auto_disabling: set[str] = set()
        # Strong refs for in-flight auto-disable tasks — asyncio only weakly
        # references tasks, and the _auto_disabling id-set above is intent
        # tracking, not a reference. Self-pruning via done-callback.
        self._auto_disable_tasks: set[asyncio.Task] = set()
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
        # What a plugin is handed: its config with the logins saved on this
        # system filled in (core/device_logins.filled_plugin_config). None
        # hands the config as it is (tests, the plugin test harness).
        self._config_filler = None
        # Router mount/unmount hooks (set by main.py, which owns the app).
        # Called when a plugin that registered an APIRouter starts/stops.
        self._mount_router_fn = None
        self._unmount_router_fn = None
        self._mount_guest_router_fn = None
        self._unmount_guest_router_fn = None

    def sync_declared_ports(self) -> None:
        """Write out what the running plugins need open, and act on it.

        Called after any change to the running set. Cheap, idempotent, and
        never fatal: a firewall that could not be updated is a degraded system,
        not a broken one, and a plugin whose port stays shut still runs.

        Does nothing while the process is shutting down. On Linux the server
        cannot open a port itself -- it runs unprivileged, and
        `openavc.system.firewall.sync` returns without acting off Windows -- so
        the only thing that opens one is the root helper `ExecStartPre` runs
        BEFORE the server starts, reading the file this writes. That makes the
        file a message to the next boot, and stopping every plugin because the
        process is exiting says nothing about what the next boot needs. Left
        unguarded, every clean shutdown wrote an empty list and the port was
        never open on Linux on any boot.
        """
        if self._shutting_down:
            return

        from openavc.core.plugin_ports import collect, write
        from openavc.system_config import get_data_dir

        try:
            entries = collect(dict(self._instances))
            # Cheap when nothing moved, which is what lets this be called from
            # every lifecycle path without anyone having to reason about how
            # often it fires. Shelling out to a firewall per plugin at boot
            # would otherwise be the cost of that convenience.
            fingerprint = tuple((e["port"], e["protocol"]) for e in entries)
            if fingerprint == self._synced_ports:
                return
            self._synced_ports = fingerprint
            write(get_data_dir(), entries)
            from openavc.system import firewall

            firewall.sync(entries)
        except Exception:
            log.exception("Could not sync plugin-declared firewall ports")

    def set_save_config_fn(self, fn):
        """Set the callback for saving plugin config to the project file."""
        self._save_config_fn = fn

    def set_config_filler(self, fn):
        """Set ``fn(plugin_id, config) -> config``, which turns a config as the
        project holds it (a login saved on this system as the marker) into
        the one the plugin is handed. Every config enters a plugin through
        ``_start_plugin_locked`` or ``apply_config``, which both ask it, so no
        caller can hand a plugin the marker."""
        self._config_filler = fn

    def filled_config(self, plugin_id: str, config: dict | None) -> dict:
        """``config`` as ``plugin_id`` would be handed it."""
        config = config or {}
        if self._config_filler is None:
            return config
        try:
            return self._config_filler(plugin_id, config)
        except Exception:  # a plugin starts without its logins rather than not at all
            log.exception(f"Could not fill in the saved logins for plugin '{plugin_id}'")
            return config

    def config_differs(self, plugin_id: str, config: dict | None) -> bool:
        """Whether ``config`` (as the project holds it) would hand the running
        plugin anything other than what it runs with: the comparison a
        reconcile makes, filled against filled, so a marker standing for the
        login the plugin already has is no change."""
        return self.filled_config(plugin_id, config) != self.get_running_config(plugin_id)

    def set_router_hooks(self, mount_fn, unmount_fn, mount_guest_fn=None, unmount_guest_fn=None):
        """Set callbacks that mount/unmount a plugin's registered HTTP routers.

        mount_fn(plugin_id, router, panel_paths, media_paths) is called after a
        plugin that called api.register_router() starts (panel_paths is the
        plugin's panel-reachable pattern list or None, media_paths the routes
        it declared as media streams); unmount_fn(plugin_id) is called on stop.
        mount_guest_fn(plugin_id, router, alias)/unmount_guest_fn(plugin_id)
        are the same pair for the open guest router (api.register_guest_router);
        alias is the plugin's validated PLUGIN_INFO guest_alias or None. Wired
        by main.py because the FastAPI app lives there, not in the loader.
        """
        self._mount_router_fn = mount_fn
        self._unmount_router_fn = unmount_fn
        self._mount_guest_router_fn = mount_guest_fn
        self._unmount_guest_router_fn = unmount_guest_fn

    # ──── Discovery ────

    def scan_plugins(self, plugin_repo_dir: Path | None = None) -> dict[str, type]:
        """
        Scan plugin_repo/ for plugin packages and register their classes
        (core/plugin_import.py finds and imports them).

        Returns dict of plugin_id -> plugin_class for all discovered plugins.
        """
        if plugin_repo_dir is None:
            from openavc.system_config import PLUGIN_REPO_DIR
            plugin_repo_dir = PLUGIN_REPO_DIR

        if not plugin_repo_dir.is_dir():
            log.debug(f"Plugin repo directory not found: {plugin_repo_dir}")
            return {}

        make_deps_loadable(plugin_repo_dir / ".deps")
        discovered = {}
        for entry in plugin_folders(plugin_repo_dir):
            try:
                plugin_class = find_plugin_class(entry)
                if plugin_class:
                    plugin_id = plugin_class.PLUGIN_INFO["id"]
                    register_plugin_class(plugin_class)
                    discovered[plugin_id] = plugin_class
            except Exception:  # Catch-all: loading arbitrary plugin code can raise anything
                log.exception(f"Failed to load plugin from {entry.name}")

        log.info(f"Discovered {len(discovered)} plugins from {plugin_repo_dir}")
        return discovered

    # ──── Validation ────

    def validate_manifest(self, plugin_class: type) -> tuple[bool, str]:
        """Check a plugin's manifest and declarations on this platform
        (core/plugin_manifest.py). Returns (valid, error_message)."""
        return plugin_manifest.validate_manifest(plugin_class, self._platform_id)

    def is_platform_compatible(self, plugin_class: type) -> bool:
        """Check if a plugin is compatible with the current platform."""
        return platform_supported(getattr(plugin_class, "PLUGIN_INFO", {}), self._platform_id)

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

        # The running set is final for this sweep. Guarded, so the per-plugin
        # calls below have already collapsed into at most one firewall change.
        self.sync_declared_ports()

    def _get_lock(self, plugin_id: str) -> asyncio.Lock:
        """Return the per-plugin lifecycle lock, creating it on first use."""
        lock = self._locks.get(plugin_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[plugin_id] = lock
        return lock

    def _set_error(self, plugin_id: str, text: str, *, unmarked: bool = False) -> None:
        """Store the error the card shows. ``unmarked`` when it is the plugin's
        own exception text, which the cloud is never told."""
        self._errors[plugin_id] = text
        if unmarked:
            self._unmarked_errors.add(plugin_id)
        else:
            self._unmarked_errors.discard(plugin_id)

    def _clear_error(self, plugin_id: str) -> None:
        self._errors.pop(plugin_id, None)
        self._unmarked_errors.discard(plugin_id)

    def _mark_incompatible(self, plugin_id: str, plugin_class: type) -> None:
        """Record a plugin as platform-incompatible (distinct from 'error')."""
        info = getattr(plugin_class, "PLUGIN_INFO", {})
        self._incompatible_plugins[plugin_id] = {
            "plugin_id": plugin_id,
            "current_platform": self._platform_id,
            "supported_platforms": info.get("platforms", []),
        }
        self._status[plugin_id] = "incompatible"
        self._clear_error(plugin_id)
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
        if config is not None:
            config = self.filled_config(plugin_id, config)
        if plugin_id in self._instances:
            # Already running. Don't silently drop a differing config — restart
            # to apply it (config-update relies on the new config taking
            # effect); an identical or empty config is a genuine no-op.
            if config is None or config == self.get_running_config(plugin_id):
                return True
            log.info(
                f"Plugin '{plugin_id}' already running with a different config "
                f"— restarting to apply it"
            )
            await self._stop_plugin_locked(plugin_id)

        plugin_class = self._startable_class(plugin_id)
        if plugin_class is None:
            return False
        info = plugin_class.PLUGIN_INFO
        if config is None:
            config = {}
        registry = PluginRegistry(plugin_id)
        api = self._new_api(plugin_id, info, config, registry)

        # `instance` is bound before the try so the failure path can tell "the
        # constructor never ran" from "start() did and left things open": only
        # the second has anything to tear down.
        instance = None
        try:
            instance = plugin_class()
            await asyncio.wait_for(instance.start(api), timeout=PLUGIN_START_TIMEOUT)
            self._register_macro_actions(plugin_id, instance)
            self._register_script_api(plugin_id, instance)
            self._instances[plugin_id] = instance
            self._registries[plugin_id] = registry
            self._apis[plugin_id] = api
            self._mark_running(plugin_id)
            self._mount_routers(plugin_id, registry, info.get("guest_alias"))
            await self._events.emit("plugin.started", {"plugin_id": plugin_id})
            log.info(f"Plugin '{plugin_id}' started (v{info.get('version', '?')})")
            # A plugin that bundles a sidecar may need a port open in the host
            # firewall. Guarded, so this is free unless the set actually moved.
            self.sync_declared_ports()
            return True
        except Exception as e:  # Catch-all: plugin start() runs arbitrary code
            await self._record_start_failure(plugin_id, e, instance, registry)
            return False

    def _startable_class(self, plugin_id: str) -> type | None:
        """The registered class, once it has passed the platform and manifest
        checks; None, with the reason recorded, when it may not start."""
        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            log.error(f"Plugin '{plugin_id}' class not found in registry")
            return None
        # Platform first, so an incompatible plugin shows as 'incompatible'
        # (its own banner) and not as a generic 'error'.
        if not self.is_platform_compatible(plugin_class):
            self._mark_incompatible(plugin_id, plugin_class)
            return None
        valid, error = self.validate_manifest(plugin_class)
        if not valid:
            log.error(f"Plugin '{plugin_id}' manifest invalid: {error}")
            self._status[plugin_id] = "error"
            self._set_error(plugin_id, f"Invalid manifest: {error}")
            return None
        return plugin_class

    def _new_api(
        self, plugin_id: str, info: dict, config: dict, registry: PluginRegistry
    ) -> PluginAPI:
        """The API a new instance is handed. Its callback failures count against
        a new instance epoch, so a queued auto-disable from an earlier instance
        can tell it no longer applies."""
        self._epoch_counter += 1
        epoch = self._epoch_counter
        self._instance_epoch[plugin_id] = epoch
        self._auto_disabling.discard(plugin_id)
        return PluginAPI(
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
            failure_reporter=functools.partial(self._count_callback_failure, plugin_id, epoch),
            success_reporter=functools.partial(self._reset_callback_failures, plugin_id),
            cloud_agent_provider=self._cloud_agent_provider,
        )

    def _count_callback_failure(self, plugin_id: str, epoch: int) -> None:
        """One more consecutive callback failure; at MAX_CALLBACK_FAILURES the
        instance of ``epoch`` is auto-disabled, once per failure streak."""
        if plugin_id in self._auto_disabling:
            return
        count = self._callback_failures.get(plugin_id, 0) + 1
        self._callback_failures[plugin_id] = count
        if count < MAX_CALLBACK_FAILURES:
            return
        log.error(
            f"Plugin '{plugin_id}' hit {count} consecutive callback failures "
            f"— auto-disabling"
        )
        self._auto_disabling.add(plugin_id)
        task = asyncio.create_task(self._auto_disable_plugin(plugin_id, epoch))
        self._auto_disable_tasks.add(task)
        task.add_done_callback(self._auto_disable_tasks.discard)
        task.add_done_callback(_log_task_exception)

    def _reset_callback_failures(self, plugin_id: str) -> None:
        """A callback that worked ends the streak, so transient errors don't add up."""
        self._callback_failures.pop(plugin_id, None)

    def _mark_running(self, plugin_id: str) -> None:
        """A started plugin: running, with no error, failure streak or flag
        (missing, incompatible, auto-disabled) left from an earlier run."""
        self._status[plugin_id] = "running"
        self._clear_error(plugin_id)
        self._callback_failures.pop(plugin_id, None)
        self._clear_missing_state(plugin_id)
        self._clear_plugin_flags(plugin_id)

    async def _record_start_failure(
        self, plugin_id: str, error: Exception, instance: Any, registry: PluginRegistry
    ) -> None:
        """A start() that raised or timed out: the error the card shows, then
        the platform's half of the teardown and the plugin's own."""
        msg = _start_failure_text(plugin_id, error)
        self._status[plugin_id] = "error"
        self._set_error(
            plugin_id, msg,
            unmarked=not isinstance(error, (asyncio.TimeoutError, PluginRefusal)),
        )
        # The same order as a stop: unregister first, so nothing dispatches
        # into a half-built plugin, then the plugin's own teardown. A child
        # process, a socket, a serial port or a hardware handle start() opened
        # is the plugin's to close, and its teardown is what was interrupted;
        # without it the handle stays open with nothing referencing it until
        # the server restarts. A stop() that trips over an attribute start()
        # never assigned is an ordinary outcome here.
        self._unregister_dispatch(plugin_id)
        if instance is not None:
            await _bounded_stop(plugin_id, instance, during="failed_start")
        await registry.cleanup(self._state, self._events)
        await self._events.emit(
            "plugin.error", {"plugin_id": plugin_id, "error": msg}
        )

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
        self._unregister_dispatch(plugin_id)
        if instance is not None:
            await _bounded_stop(plugin_id, instance, during="stop")
        if registry is not None:
            await registry.cleanup(self._state, self._events)
        if api is not None:
            api._cancel_all_tasks()
        _reap_named_tasks(plugin_id)
        self._unmount_routers(plugin_id)
        self._mark_stopped(plugin_id)
        await self._events.emit("plugin.stopped", {"plugin_id": plugin_id})
        log.info(f"Plugin '{plugin_id}' stopped")
        # Close a rule nobody asks for any more. This is the half that makes
        # the declaration honest: a port that opened on install and stayed open
        # after uninstall would be worse than never opening it.
        self.sync_declared_ports()

    def _mark_stopped(self, plugin_id: str) -> None:
        self._status[plugin_id] = "stopped"
        self._clear_error(plugin_id)
        self._callback_failures.pop(plugin_id, None)
        self._auto_disabling.discard(plugin_id)
        # A normal stop clears the auto-disabled flag; auto-disable re-sets it
        # right after its own stop call, so the True it wants still survives.
        self._state.delete(f"plugin.{plugin_id}.auto_disabled")

    def _unregister_dispatch(self, plugin_id: str) -> None:
        """Take a plugin's macro actions and script API methods back."""
        self._macros.unregister_plugin_actions(plugin_id)
        self._unregister_script_api(plugin_id)

    def _mount_routers(
        self, plugin_id: str, registry: PluginRegistry, guest_alias: str | None
    ) -> None:
        """Mount the HTTP routers a plugin registered during start(). A mount
        that fails is logged and the plugin stays started."""
        if registry.http_router is not None and self._mount_router_fn:
            _call_hook(
                self._mount_router_fn,
                f"Failed to mount HTTP router for plugin '{plugin_id}'",
                plugin_id,
                registry.http_router,
                registry.panel_ext_paths or None,
                registry.media_ext_paths or None,
            )
        if registry.guest_router is not None and self._mount_guest_router_fn:
            _call_hook(
                self._mount_guest_router_fn,
                f"Failed to mount guest HTTP router for plugin '{plugin_id}'",
                plugin_id, registry.guest_router, guest_alias,
            )

    def _unmount_routers(self, plugin_id: str) -> None:
        """Unmount a plugin's routes, on every stop whether or not it mounted
        any. A failure is logged and the stop goes on."""
        hooks = (
            (self._unmount_router_fn, "HTTP router"),
            (self._unmount_guest_router_fn, "guest HTTP router"),
        )
        for unmount, what in hooks:
            if unmount:
                _call_hook(unmount, f"Failed to unmount {what} for plugin '{plugin_id}'", plugin_id)

    async def stop_all(self) -> None:
        """Stop all running plugins, because the process is going away.

        Deliberately leaves the declared-port file alone -- see
        `sync_declared_ports`. Disabling a plugin and exiting the server both
        empty the running set, and only the first of them means the port
        should close.
        """
        self._shutting_down = True
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
        new_config = self.filled_config(plugin_id, new_config)
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

    async def restart_or_apply(self, plugin_id: str, new_config: dict) -> str:
        """Apply new config to a running plugin: hot when the plugin opts in
        via ``on_config_changed``, otherwise stop/start. Every config-write
        path (REST, cloud AI) routes through here so behavior is identical.

        Holds the per-plugin lock across the whole check → hot-apply →
        stop → start sequence. The cloud AI layer fans tool calls out as
        tasks, so two overlapping updates (or an update racing a disable)
        could otherwise interleave in the not-running window mid-restart —
        one returning "nothing to do" while the other restarts with a config
        the project file no longer holds.

        Returns the outcome: "hot_applied", "restarted", "not_running"
        (nothing to do), or "start_failed" — the restart's start step
        failed and the plugin is left stopped; callers must surface that
        instead of reporting a clean update."""
        async with self._get_lock(plugin_id):
            if not self.is_running(plugin_id):
                return "not_running"
            if await self.apply_config(plugin_id, new_config):
                return "hot_applied"
            await self._stop_plugin_locked(plugin_id)
            if await self._start_plugin_locked(plugin_id, new_config):
                return "restarted"
            return "start_failed"

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

    def error_for_cloud(self, plugin_id: str) -> str | None:
        """The plugin's stored error when it may go to the cloud, else None.

        What the AI assistant answers is kept in the cloud chat history, so it
        carries a plugin's error only when someone wrote it for a reader: the
        platform's own sentences (a manifest problem, a start that timed out,
        an auto-disable) and a :class:`PluginRefusal` the plugin raised. None
        when there is no error, or when it is the text of anything else
        start() raised; the card still shows that, and the Log has it.
        """
        if plugin_id in self._unmarked_errors:
            return None
        return self._errors.get(plugin_id) or None

    def couldnt_start_sentence(self, plugin_id: str) -> str:
        """What the cloud is told about a start whose error it may not hear."""
        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        info = getattr(plugin_class, "PLUGIN_INFO", None) or {}
        name = info.get("name") or plugin_id
        return f"{name} couldn't start. The Programmer's Log has the details."

    def list_plugins(self, *, for_cloud: bool = False) -> list[dict[str, Any]]:
        """List all known plugins with status information.

        ``for_cloud`` words each error as :meth:`error_for_cloud` allows, for
        the AI assistant's answers; the Programmer's list shows them as stored.
        Installed plugins first, then the project's missing ones, then any
        marked incompatible that never registered.
        """
        plugins = [
            self._listed(plugin_id, plugin_class, for_cloud)
            for plugin_id, plugin_class in _PLUGIN_CLASS_REGISTRY.items()
        ]
        seen = set(_PLUGIN_CLASS_REGISTRY)
        unregistered = (
            (self._missing_plugins, _listed_missing),
            (self._incompatible_plugins, _listed_incompatible),
        )
        for tracker, entry_for in unregistered:
            for plugin_id, tracked in tracker.items():
                if plugin_id not in seen:
                    seen.add(plugin_id)
                    plugins.append(entry_for(plugin_id, tracked))
        return plugins

    def _listed(self, plugin_id: str, plugin_class: type, for_cloud: bool) -> dict[str, Any]:
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
            "sandbox_permissions": sandbox_permissions(plugin_id, plugin_class),
            "guest_alias": info.get("guest_alias", ""),
            "installed": True,
            "compatible": self.is_platform_compatible(plugin_class),
        }
        if status == "error":
            entry["error"] = self._listed_error(plugin_id, for_cloud)
        return entry

    def _listed_error(self, plugin_id: str, for_cloud: bool) -> str:
        if for_cloud:
            return self.error_for_cloud(plugin_id) or self.couldnt_start_sentence(plugin_id)
        return self._errors.get(plugin_id, "")

    def get_plugin_info(self, plugin_id: str) -> dict[str, Any] | None:
        """Get detailed info for a specific plugin."""
        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            return self._missing_plugin_info(plugin_id)

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
            "sandbox_permissions": sandbox_permissions(plugin_id, plugin_class),
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
        result.update(_described_declarations(plugin_class))
        return result

    def _missing_plugin_info(self, plugin_id: str) -> dict[str, Any] | None:
        missing = self._missing_plugins.get(plugin_id)
        if not missing:
            return None
        return {"plugin_id": plugin_id, "status": "missing", "installed": False, **missing}

    def get_plugin_status(self, plugin_id: str) -> str:
        """Get the current status of a plugin."""
        return self._status.get(plugin_id, "unknown")

    def get_all_macro_actions(self) -> list[dict[str, Any]]:
        """Get all macro actions from running plugins.

        Returns a flat list, each entry shaped for the macro builder:
        ``{action_type, plugin_id, plugin_name, label, description?, icon?, params}``
        """
        return [
            _builder_action(plugin_id, plugin_name, action_type, spec)
            for plugin_id, plugin_name, actions in self._running_declarations("MACRO_ACTIONS")
            for action_type, spec in actions.items()
        ]

    def get_all_script_api(self) -> list[dict[str, Any]]:
        """Get all script API methods from running plugins.

        Returns a flat list, each entry shaped for the script editor
        autocomplete and hover docs:
        ``{plugin_id, plugin_name, method, doc?, sync}``
        """
        return [
            _script_method(plugin_id, plugin_name, method_name, spec)
            for plugin_id, plugin_name, methods in self._running_declarations("SCRIPT_API")
            for method_name, spec in methods.items()
        ]

    def _running_declarations(self, attr: str):
        """(plugin_id, plugin_name, declaration) for each running plugin whose
        class declares ``attr`` non-empty, in running order."""
        for plugin_id, instance in self._instances.items():
            plugin_class = type(instance)
            declared = getattr(plugin_class, attr, None)
            if declared:
                yield plugin_id, plugin_class.PLUGIN_INFO.get("name", plugin_id), declared

    def get_all_extensions(self) -> dict[str, Any]:
        """Every running plugin's extensions by type, as the panel and the IDE
        receive them (core/plugin_extensions.py)."""
        return collect_extensions(self._instances)

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
            self._set_error(
                plugin_id,
                f"Auto-disabled after {MAX_CALLBACK_FAILURES} consecutive "
                f"callback failures",
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
        from openavc.core.script_api import plugins as plugins_proxy
        for method_name, spec in script_api.items():
            handler = getattr(instance, spec["handler"])
            plugins_proxy._register_method(plugin_id, method_name, handler)

    def _unregister_script_api(self, plugin_id: str) -> None:
        """Remove all script methods registered by a plugin."""
        from openavc.core.script_api import plugins as plugins_proxy
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
