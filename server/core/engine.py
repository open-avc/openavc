"""
OpenAVC Engine — the main runtime orchestrator.

Wires together StateStore, EventBus, DeviceManager, MacroEngine, and
the WebSocket push system. Manages the full system lifecycle:
start, stop, and hot-reload.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from typing import Any

from server import config
from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.plugin_loader import PluginLoader
from server.core.project_loader import (
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
    save_project_async,
)
from server.core.script_engine import ScriptEngine
from server.core.state_persister import StatePersister
from server.core.state_store import StateStore
from server.core.trigger_engine import TriggerEngine
from server.core.value_resolver import resolve_ref
from server.utils.logger import get_logger
from server.version import __version__

log = get_logger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback to log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(f"Background task {task.get_name()!r} failed: {exc}", exc_info=exc)


# Sentinel used to distinguish deleted keys (absent from the store) from
# keys that exist with a None value. Used by _on_state_change.
_STATE_MISSING = object()

# The state store documents flat primitives only (str, int, float, bool,
# None) — WS broadcast, the ISC mesh, the cloud relay, and persistence all
# rely on it. bool is intentionally listed though it's an int subclass.
_FLAT_PRIMITIVE_TYPES = (str, int, float, bool, type(None))

# Glob metacharacters that turn a single-key subscription into a multi-key
# fan-in (see StateStore pattern grammar). A variable source_key must be one
# concrete key, never a pattern.
_GLOB_METACHARS = "*?["


def _coerce_flat_primitive(value: Any) -> tuple[Any, bool]:
    """Coerce a value to the flat-primitive state invariant.

    A few engine write paths take author- or runtime-supplied values
    (variable ``source_map`` results, static ``state.set`` binding values)
    that the project schema types as ``Any``, so a list/dict could otherwise
    reach the store and break downstream consumers that assume primitives.
    Primitives pass through unchanged; anything else is flattened to a JSON
    string so it stays representable.

    Returns ``(coerced_value, was_coerced)`` so callers can log with context.
    """
    if isinstance(value, _FLAT_PRIMITIVE_TYPES):
        return value, False
    try:
        return json.dumps(value, ensure_ascii=False), True
    except (TypeError, ValueError):
        return str(value), True


class Engine:
    """
    Main runtime engine. Singleton per OpenAVC instance.
    Coordinates all subsystems and manages the lifecycle.
    """

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.project: ProjectConfig | None = None

        # Core subsystems
        self.state = StateStore()
        self.events = EventBus()
        self.devices = DeviceManager(self.state, self.events)
        self.macros = MacroEngine(self.state, self.events, self.devices, broadcast_ws=self.broadcast_ws)
        self.triggers = TriggerEngine(self.state, self.events, self.macros)
        self.scripts: ScriptEngine | None = None
        self.plugin_loader = PluginLoader(self.state, self.events, self.macros, self.devices)
        self.persister: StatePersister | None = None
        self.isc = None  # ISCManager, initialized in start() if enabled
        self.cloud_agent = None  # CloudAgent, initialized in start() if enabled
        self.update_manager = None  # UpdateManager, initialized in start()
        self.mdns_advertiser = None  # MDNSAdvertiser, initialized in start() if enabled

        # Setup-action runner (driver-declared provisioning wizards)
        from server.core.setup_actions import SetupActionRunner
        self.setup_actions = SetupActionRunner(self)

        # Simulation
        from server.core.simulation import SimulationManager
        self.simulation = SimulationManager(self)

        # Wire StateStore -> EventBus
        self.state.set_event_bus(self.events)

        # WebSocket clients (set of WebSocket connections)
        self._ws_clients: set = set()
        # Per-client namespace filters: id(ws) -> tuple of prefix strings
        self._ws_ns_filters: dict[int, tuple[str, ...]] = {}

        # State batching for WebSocket push
        self._state_batch: dict[str, Any] = {}
        # Keys that were deleted (rather than set) since the last flush.
        # Tracked separately so the flush loop can emit a state.delete WS
        # message — clients can't tell delete from set-to-None otherwise.
        self._state_deleted_keys: set[str] = set()
        self._batch_task: asyncio.Task | None = None

        # Variable-to-state binding subscriptions
        self._var_binding_subs: list[str] = []
        self._var_validation_subs: list[str] = []
        # Keys currently mid-propagation in a variable-binding cascade. A
        # re-entrancy guard so chained bindings (var bound to another var's
        # key) propagate while genuine cycles (A<->B) terminate.
        self._var_binding_active: set[str] = set()
        self._project_revision: int = 0  # incremented on every save

        # Event/state subscription IDs (for cleanup on stop/reload)
        self._state_sub_ids: list[str] = []
        self._event_sub_ids: list[str] = []

        # Reload serialization
        self._reload_lock = asyncio.Lock()

        # Tracking
        self._start_time: float = 0
        self._running = False
        self._marker_confirm_task: asyncio.Task | None = None

        # Periodic backup
        self._periodic_backup_task: asyncio.Task | None = None
        self._dirty_since_backup: bool = False
        self._last_backup_time: float = 0

        # Cached (local_ip, hostname) for get_status. Detection does blocking
        # socket / gethostname syscalls; they rarely change, so compute once.
        self._network_info: tuple[str, str] | None = None

    async def start(self) -> None:
        """
        Start the engine:
        1. Load project.avc
        2. Initialize user variables
        3. Connect all devices
        4. Load macros
        5. Register UI bindings
        6. Start state batch push
        7. Emit system.started
        """
        log.info("Engine starting...")
        self._start_time = time.time()

        # Ensure system.json exists in data directory
        from server.system_config import (
            get_system_config,
            migrate_legacy_project_dir,
            migrate_legacy_repos,
        )
        sys_config = get_system_config()
        sys_config.ensure_file()

        # One-shot migration of plugin_repo/driver_repo from the pre-data_dir
        # layout (APP_DIR/{plugin,driver}_repo). Runs before driver and plugin
        # loading so the moved content is picked up on the same startup. No-op
        # when the new locations already have content.
        try:
            migrate_legacy_repos()
        except Exception:  # never block startup on the migration
            log.exception("migrate_legacy_repos failed")

        # Same one-shot treatment for the legacy default project location
        # (APP_DIR/projects). Must run before the project load, cloud config
        # read, and ISC instance-id read below so they all see the moved
        # directory. No-op when OPENAVC_PROJECT or OPENAVC_DATA_DIR is set.
        try:
            migrate_legacy_project_dir()
        except Exception:  # never block startup on the migration
            log.exception("migrate_legacy_project_dir failed")

        # Set system state keys
        from server.updater.platform import detect_deployment_type
        self.state.set("system.version", __version__, source="system")
        self.state.set("system.update_available", "", source="system")
        self.state.set("system.update_channel", sys_config.get("updates", "channel", "stable"), source="system")
        self.state.set("system.update_status", "idle", source="system")
        self.state.set("system.update_progress", 0, source="system")
        self.state.set("system.update_error", "", source="system")
        self.state.set("system.deployment_type", detect_deployment_type().value, source="system")

        # Load project — with corruption recovery
        self.project = self._load_project_safe()

        # Load project-level drivers (community drivers installed via IDE)
        self._load_project_drivers()

        # Publish project asset catalog so plugins (e.g. audio_player) can
        # subscribe to project.assets and pick up uploaded files.
        from server.api.assets import publish_assets_state
        publish_assets_state(self)

        # Initialize state persister and load saved values
        state_file = self.project_path.parent / "state.json"
        self.persister = StatePersister(state_file, self.state)
        persisted_values = self.persister.load()

        # Initialize user variables from defaults / persisted values.
        persistent_keys = self._init_variable_values(persisted_values)

        # Start watching persistent variables for changes
        self.persister.start(persistent_keys)

        # Bind variable sources (auto-sync from device state)
        self._bind_variable_sources()

        # Register validation listener for variables with rules
        self._register_variable_validation()

        # Load macros and device groups
        macros_data = [m.model_dump() for m in self.project.macros]
        self.macros.load_macros(macros_data)
        groups_data = [g.model_dump() for g in self.project.device_groups]
        self.macros.load_groups(groups_data)

        # Add and connect devices in parallel (merge connection table into
        # device config). Sequential await of driver.connect() would serialize
        # TCP timeouts (5 s each) — with N offline devices, startup blocks for
        # N x 5 s. Each add_device is independent (writes to its own device_id
        # keys in state/config dicts), so gather is safe.
        startup_errors: list[str] = []
        resolved = {
            d.id: self.resolved_device_config(d) for d in self.project.devices
        }
        # Bridges first, then their dependents (see _bridge_first).
        for batch in self._bridge_first(list(resolved), resolved):
            batch_results = await asyncio.gather(
                *(self.devices.add_device(resolved[did]) for did in batch),
                return_exceptions=True,
            )
            for did, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    startup_errors.append(f"Device '{did}': {result}")
                    log.error(f"Failed to add device '{did}': {result}")

        # Register UI event bindings
        self._register_ui_bindings()

        # Plugin System — scan and start plugins
        try:
            self.plugin_loader.set_save_config_fn(self._save_plugin_config)
            self.plugin_loader.scan_plugins()
            if self.project.plugins:
                plugins_dict = {
                    pid: pc.model_dump() if hasattr(pc, "model_dump") else pc
                    for pid, pc in self.project.plugins.items()
                }
                await self.plugin_loader.start_plugins(plugins_dict)
        except Exception as e:  # Catch-all: isolates plugin system errors from core startup
            startup_errors.append(f"Plugins: {e}")
            log.exception("Plugin system failed to start")

        # Script Engine
        project_dir = self.project_path.parent
        self.scripts = ScriptEngine(self.state, self.events, self.devices, project_dir, self.macros)
        self.scripts.install()
        try:
            scripts_data = [s.model_dump() for s in self.project.scripts]
            self.scripts.load_scripts(scripts_data)
        except Exception as e:  # Catch-all: isolates script loading errors from core startup
            startup_errors.append(f"Scripts: {e}")
            log.exception("Script engine failed to load scripts")

        # Trigger engine (automatic macro triggers)
        macros_data_triggers = [m.model_dump() for m in self.project.macros]
        self.triggers.load_triggers(macros_data_triggers)

        # Subscribe to all state changes for WebSocket batching
        self._state_sub_ids.append(
            self.state.subscribe("*", self._on_state_change)
        )

        # Bridge macro events to WebSocket for live progress tracking
        self._event_sub_ids.append(self.events.on("macro.started.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.progress.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.completed.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.cancelled.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.error.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.step_error.*", self._on_macro_event))

        # Bridge trigger events to WebSocket
        self._event_sub_ids.append(self.events.on("trigger.fired", self._on_trigger_event))
        self._event_sub_ids.append(self.events.on("trigger.skipped", self._on_trigger_event))
        self._event_sub_ids.append(self.events.on("trigger.pending", self._on_trigger_event))
        self._event_sub_ids.append(self.events.on("trigger.queued", self._on_trigger_event))

        # Bridge script error events to WebSocket
        self._event_sub_ids.append(self.events.on("script.error", self._on_script_error))

        # Bridge plugin lifecycle events to WebSocket
        self._event_sub_ids.append(self.events.on("plugin.started", self._on_plugin_event))
        self._event_sub_ids.append(self.events.on("plugin.stopped", self._on_plugin_event))
        self._event_sub_ids.append(self.events.on("plugin.error", self._on_plugin_event))
        self._event_sub_ids.append(self.events.on("plugin.missing", self._on_plugin_event))

        # Persist project file when pending device settings are applied
        self._event_sub_ids.append(self.events.on(
            "device.pending_settings_applied",
            self._on_pending_settings_applied,
        ))

        # Inter-System Communication
        await self._start_isc()

        # mDNS Service Advertisement
        await self._start_mdns_advertiser()

        # Cloud Agent (apply saved pairing config before starting)
        from server.cloud.config import apply_saved_cloud_config
        apply_saved_cloud_config()
        await self._start_cloud_agent()

        # Start the batch flush task
        self._batch_task = asyncio.create_task(self._flush_state_batch_loop())
        self._batch_task.add_done_callback(_log_task_exception)

        # Start periodic backup timer (every 30 min if project has changed)
        self._periodic_backup_task = asyncio.create_task(self._periodic_backup_loop())
        self._periodic_backup_task.add_done_callback(_log_task_exception)

        self._running = True

        # Pre-warm the network-info cache off the event loop so the first
        # status/health poll never blocks on socket / gethostname syscalls.
        prime_task = asyncio.create_task(asyncio.to_thread(self._detect_network_info))
        prime_task.add_done_callback(_log_task_exception)

        # Record startup error count for UI visibility. Always set it (0 when
        # clean) so a previous run's count can't linger in the store / cloud
        # relay after the project is fixed and reloaded.
        self.state.set("system.startup_errors", len(startup_errors), source="system")
        if startup_errors:
            log.warning(f"Engine started with {len(startup_errors)} error(s): {'; '.join(startup_errors)}")

        # System state
        self.state.set("system.started", True, source="system")
        await self.events.emit("system.started")

        # Start trigger engine after system.started (so startup triggers work)
        await self.triggers.start()

        # Update Manager — check for updates and schedule auto-check
        try:
            from server.updater.manager import UpdateManager
            self.update_manager = UpdateManager(state_store=self.state)
            await self.update_manager.start_auto_check()
            # Wire into cloud command handler for cloud-triggered updates
            if self.cloud_agent and hasattr(self.cloud_agent, '_command_handler'):
                handler = self.cloud_agent._command_handler
                if handler:
                    handler._update_manager = self.update_manager
        except Exception:
            log.exception("Update manager failed to start — continuing without updates")
            self.update_manager = None

        # 60-second startup confirmation — clear pending-update marker
        self._marker_confirm_task = asyncio.create_task(
            self._confirm_startup_after_delay()
        )
        self._marker_confirm_task.add_done_callback(_log_task_exception)

        log.info(
            f'Engine started — project "{self.project.project.name}" '
            f"({len(self.project.devices)} devices, "
            f"{len(self.project.macros)} macros)"
        )

    async def _confirm_startup_after_delay(self) -> None:
        """Clear pending-update marker after 60 seconds of stable running."""
        try:
            await asyncio.sleep(60)
            from server.system_config import get_system_config
            from server.updater.rollback import read_pending_marker, clear_pending_marker
            data_dir = get_system_config().data_dir
            marker = read_pending_marker(data_dir)
            if marker:
                clear_pending_marker(data_dir)
                log.info(
                    "Update confirmed successful after 60s (v%s -> v%s)",
                    marker.get("from_version"), marker.get("to_version"),
                )
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop the engine gracefully."""
        log.info("Engine stopping...")
        self._running = False
        # Serialize teardown against an in-flight reload_project (reachable
        # from REST, the cloud command handler, and AI tools). Tearing
        # subsystems down while a hot-reload runs interleaves trigger and
        # subscription start/stop. Wait for any reload to finish first.
        async with self._reload_lock:
            await self._stop_inner()

    async def _stop_inner(self) -> None:
        """Tear down all subsystems. Caller holds _reload_lock."""
        # Cancel startup confirmation timer
        if self._marker_confirm_task and not self._marker_confirm_task.done():
            self._marker_confirm_task.cancel()
            try:
                await self._marker_confirm_task
            except asyncio.CancelledError:
                pass

        # Stop update manager
        if self.update_manager:
            await self.update_manager.stop_auto_check()

        # Unsubscribe all event/state handlers to prevent leaks on reload
        for sub_id in self._state_sub_ids:
            self.state.unsubscribe(sub_id)
        self._state_sub_ids.clear()
        for sub_id in self._event_sub_ids:
            self.events.off(sub_id)
        self._event_sub_ids.clear()

        # Flush and stop state persister
        if self.persister:
            self.persister.stop()

        await self.events.emit("system.stopping")

        # Stop trigger engine
        await self.triggers.stop()

        # Stop Cloud Agent
        if self.cloud_agent:
            await self.cloud_agent.stop()
            self.cloud_agent = None

        # Stop mDNS advertiser
        if self.mdns_advertiser:
            await self.mdns_advertiser.stop()
            self.mdns_advertiser = None

        # Stop ISC
        if self.isc:
            await self.isc.stop()
            self.isc = None

        # Unload scripts
        if self.scripts:
            self.scripts.unload_all()

        # Stop all plugins
        await self.plugin_loader.stop_all()

        # Stop batch task
        if self._batch_task and not self._batch_task.done():
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass

        # Stop periodic backup task
        if self._periodic_backup_task and not self._periodic_backup_task.done():
            self._periodic_backup_task.cancel()
            try:
                await self._periodic_backup_task
            except asyncio.CancelledError:
                pass

        # Stop simulation if active
        if self.simulation.active:
            await self.simulation.stop()

        # Disconnect all devices
        await self.devices.disconnect_all()

        # Drain any in-flight state.changed EventBus emissions so they aren't
        # silently dropped mid-shutdown.
        await self.state.flush_pending_events()

        self.state.set("system.started", False, source="system")
        log.info("Engine stopped")

    async def reload_project(self) -> None:
        """Hot-reload project.avc without full restart."""
        async with self._reload_lock:
            await self._reload_project_inner()

    def reload_persisted_state(self) -> None:
        """Re-apply state.json to the store and restart the persister.

        Called after a backup restore. A plain reload only re-subscribes the
        persister (via ``update_keys``); it never re-reads state.json, so the
        running store would keep the stale pre-restore values and the persister
        would write them straight back over the just-restored file. This reloads
        the restored values into the store — falling back to each persistent
        variable's default when the backup carried no value for it — then
        restarts the persister so it tracks the restored state cleanly.
        """
        if not self.persister or not self.project:
            return
        persisted = self.persister.load()
        for var in self.project.variables:
            if not var.persist:
                continue
            key = f"var.{var.id}"
            self.state.set(key, persisted.get(key, var.default), source="system")
        persistent_keys = {f"var.{v.id}" for v in self.project.variables if v.persist}
        # stop() clears any prior subscriptions + the _stopped flag the caller's
        # pre-restore stop() set; start() re-subscribes with _stopped cleared.
        self.persister.stop()
        self.persister.start(persistent_keys)

    async def _reload_project_inner(self) -> None:
        log.info("Reloading project...")

        # Snapshot current state for rollback on failure
        prev_project = self.project
        prev_revision = self._project_revision
        prev_dirty = self._dirty_since_backup

        try:
            self.project = load_project(self.project_path)
            self._project_revision += 1
            self._dirty_since_backup = True

            # Pick up any community drivers that were dropped into
            # driver_repo/ since startup (file-system installs, manual
            # copies during development, etc.). Without this, _sync_devices
            # below would mark every device using a freshly-installed driver
            # as orphaned even though the file is on disk.
            self._load_project_drivers()

            # Stop triggers and cancel macros first — prevents triggers from
            # firing on state keys that are about to be cleaned up
            await self.triggers.stop()
            await self.macros.cancel_all()

            # Sync variables: initialize new defaults, clean up orphaned keys
            project_var_ids = {v.id for v in self.project.variables}
            for var in self.project.variables:
                key = f"var.{var.id}"
                if self.state.get(key) is None:
                    self.state.set(key, var.default, source="system")
            # Remove orphaned var.* state keys for deleted variables
            all_var_keys = self.state.get_namespace("var.")
            orphaned_vars = [vid for vid in all_var_keys if vid not in project_var_ids]
            for vid in orphaned_vars:
                self.state.delete(f"var.{vid}")
            if orphaned_vars:
                log.info(f"Cleaned up {len(orphaned_vars)} orphaned variable state key(s)")

            # Update persistent variable keys
            if self.persister:
                persistent_keys = {
                    f"var.{v.id}" for v in self.project.variables if v.persist
                }
                self.persister.update_keys(persistent_keys)

            # Sync devices: remove deleted, add new, update changed
            await self._sync_devices()

            # Promote any orphans whose driver is now in the registry
            # (e.g. driver_repo/ files added between reloads). _sync_devices
            # only re-adds devices whose config changed, so an orphan stuck
            # waiting for its driver wouldn't otherwise come online here.
            await self.devices.retry_all_orphans()

            # If simulation is active, sync simulated devices with the new project state
            if self.simulation.active:
                await self.simulation.sync()

            # Sync plugins: add new, remove deleted, restart changed
            await self._sync_plugins()

            # Reload macros and device groups
            macros_data = [m.model_dump() for m in self.project.macros]
            self.macros.load_macros(macros_data)
            groups_data = [g.model_dump() for g in self.project.device_groups]
            self.macros.load_groups(groups_data)

            # Reload and restart triggers (stopped earlier before variable sync)
            macros_data_triggers = [m.model_dump() for m in self.project.macros]
            self.triggers.load_triggers(macros_data_triggers)
            await self.triggers.start()

            # Re-register UI bindings
            self._register_ui_bindings()

            # Re-bind variable sources
            self._bind_variable_sources()

            # Re-register variable validation listeners
            self._register_variable_validation()

            # Reload scripts
            if self.scripts:
                scripts_data = [s.model_dump() for s in self.project.scripts]
                self.scripts.reload_scripts(scripts_data)

            # Reload ISC config
            await self._reload_isc()

            # Update mDNS advertiser with new project name
            if self.mdns_advertiser:
                self.mdns_advertiser.update_name(self.project.project.name)

        except Exception:
            log.error("Reload failed, rolling back to previous project state",
                      exc_info=True)
            self.project = prev_project
            self._project_revision = prev_revision
            self._dirty_since_backup = prev_dirty

            # Best-effort: re-sync subsystems with the restored project so the
            # runtime doesn't diverge from self.project. The failure may have
            # landed after _sync_devices / _sync_plugins already applied part
            # of the new project (devices removed/added, plugins started or
            # stopped); re-running both against the restored project reconciles
            # the live device and plugin set back to it.
            try:
                await self._sync_devices()
                await self._sync_plugins()

                macros_data = [m.model_dump() for m in self.project.macros]
                self.macros.load_macros(macros_data)
                groups_data = [g.model_dump() for g in self.project.device_groups]
                self.macros.load_groups(groups_data)

                # Stop triggers before reloading/starting again. The normal
                # reload path stops triggers up front (line 436) and only
                # restarts at the end; if the exception fired after that
                # restart succeeded, `start()` already populated the listener
                # lists. Calling `start()` again without `stop()` first would
                # stack a second set of state/event subscriptions on top of
                # the existing ones, causing triggers to fire 2x per change.
                await self.triggers.stop()
                self.triggers.load_triggers(macros_data)
                await self.triggers.start()

                self._register_ui_bindings()
                self._bind_variable_sources()
                self._register_variable_validation()
            except Exception:
                log.error("Rollback re-sync also failed", exc_info=True)

            raise

        # Clean reload succeeded — zero the startup-error count so a stale
        # count from the initial start (now fixed) doesn't linger in the store
        # and cloud relay.
        self.state.set("system.startup_errors", 0, source="system")

        # Push new UI definition to all connected panels
        await self.broadcast_ws({
            "type": "ui.definition",
            "ui": self.project.ui.model_dump(mode="json"),
        })

        # Notify Programmer IDE to refetch project data
        await self.broadcast_ws({
            "type": "project.reloaded",
            "revision": self._project_revision,
        })

        await self.events.emit("system.project.reloaded")
        log.info("Project reloaded")

    def resolved_device_config(self, device) -> dict:
        """Get device config dict with driver defaults and connection table merged in.

        Layering (later wins):
          1. ``driver.DRIVER_INFO["default_config"]`` — driver-declared
             defaults (e.g. control-protocol port). Ensures discovery /
             AI-tool add paths inherit the right defaults even when the
             caller only supplied ``host``.
          2. ``device.config`` — protocol fields saved in the project.
          3. ``project.connections[id]`` — connection-table overrides
             (host, port, baudrate, etc.) saved separately.
        """
        from server.core.device_manager import get_driver_default_config

        cfg = device.model_dump() if hasattr(device, "model_dump") else dict(device)
        defaults = get_driver_default_config(cfg.get("driver", ""))
        conn = self.project.connections.get(cfg["id"], {})
        cfg["config"] = {**defaults, **cfg.get("config", {}), **conn}
        cfg["config"] = self._resolve_bridge_binding(cfg["config"])
        return cfg

    def _resolve_bridge_binding(self, config: dict) -> dict:
        """Rewrite a bridge-bound device's effective connection to its bridge's port.

        When a device's connection carries ``bridge`` (a bridge device id) +
        ``bridge_port`` (a port the bridge advertises), the device's bytes
        travel *through* that bridge rather than to a host of its own. For a
        serial pass-through port this is a pure config rewrite: point the
        downstream at the bridge's transparent TCP pass-through endpoint
        (``transport=tcp``, ``host=<bridge host>``, ``port=<passthrough_port>``)
        and reuse the existing TCP transport unchanged. The serial params
        (baudrate/parity/...) stay in the config so the bridge driver can push
        them to the hardware via ``prepare_bridge_port`` before bytes flow.

        Unresolvable bindings (unknown bridge, unknown port, missing host) are
        left untouched and logged — the device then fails to connect with a
        clear error rather than silently dialing the wrong place. IR / relay
        ports are not transport rewrites (commands route through the bridge at
        send time, Phase 2/3) and are left as-is for that path.
        """
        bridge_id = config.get("bridge")
        bridge_port_id = config.get("bridge_port")
        if not bridge_id or not bridge_port_id:
            return config

        bridge_dev = next(
            (d for d in self.project.devices if d.id == bridge_id), None
        )
        if bridge_dev is None:
            log.warning(
                "Bridge '%s' referenced by a device's connection is not in the "
                "project — leaving the binding unresolved", bridge_id,
            )
            return config

        from server.core.device_manager import get_driver_bridge_ports
        port_def = get_driver_bridge_ports(bridge_dev.driver).get(bridge_port_id)
        if port_def is None:
            log.warning(
                "Bridge '%s' (driver '%s') does not advertise port '%s' — "
                "leaving the binding unresolved",
                bridge_id, bridge_dev.driver, bridge_port_id,
            )
            return config

        passthrough_port = port_def.get("passthrough_port")
        if port_def.get("kind") == "serial" and passthrough_port:
            # Resolve the bridge's own host the same layered way every device's
            # connection is (driver defaults < device.config < connections
            # table). Reading the connections table alone misses a host that
            # comes from a driver default or sits in the bridge's device.config
            # (e.g. an imported or template project) — which would leave the
            # binding unresolved and the downstream device wrongly offline.
            from server.core.device_manager import get_driver_default_config

            bridge_cfg = getattr(bridge_dev, "config", None) or {}
            bridge_conn = self.project.connections.get(bridge_id, {})
            bridge_host = {
                **get_driver_default_config(bridge_dev.driver),
                **bridge_cfg,
                **bridge_conn,
            }.get("host")
            if not bridge_host:
                log.warning(
                    "Bridge '%s' has no host configured — leaving the serial "
                    "binding for '%s' unresolved", bridge_id, bridge_port_id,
                )
                return config
            resolved = dict(config)
            resolved["transport"] = "tcp"
            resolved["host"] = bridge_host
            resolved["port"] = passthrough_port
            return resolved

        # IR / relay (and any future non-pass-through kind): no transport
        # rewrite — the command path routes through the bridge object at send
        # time (Phase 2/3).
        return config

    @staticmethod
    def _is_bridge_config(cfg: dict) -> bool:
        """True if a resolved device config belongs to a bridge driver."""
        from server.core.device_manager import get_driver_bridge_ports
        return bool(get_driver_bridge_ports(cfg.get("driver", "")))

    def _bridge_first(
        self, device_ids: list[str], resolved: dict[str, dict]
    ) -> list[list[str]]:
        """Split ``device_ids`` into ``[bridges, others]`` (each batch included
        only if non-empty), preserving order within each, so bridge devices are
        added and connected before the devices that route through them — a
        bridge-bound device's connect path needs its bridge live to prep the
        port (push serial baud/parity) first.
        """
        bridges: list[str] = []
        others: list[str] = []
        for did in device_ids:
            (bridges if self._is_bridge_config(resolved[did]) else others).append(did)
        return [batch for batch in (bridges, others) if batch]

    async def _sync_devices(self) -> None:
        """Sync running devices with project config (add new, remove deleted, update changed)."""
        if not self.project:
            return

        # Build merged configs (device.config + connection table overrides)
        project_devices: dict[str, dict] = {}
        for d in self.project.devices:
            project_devices[d.id] = self.resolved_device_config(d)

        running_ids = set(self.devices.get_device_configs().keys())
        project_ids = set(project_devices.keys())

        # Remove devices no longer in project and clean up orphaned state keys
        for device_id in running_ids - project_ids:
            await self.devices.remove_device(device_id)
            # Clean up orphaned device.{id}.* state keys (14.6)
            prefix = f"device.{device_id}."
            orphaned = self.state.get_namespace(prefix)
            for suffix in orphaned:
                self.state.delete(f"{prefix}{suffix}")
            if orphaned:
                log.info(f"Cleaned up {len(orphaned)} orphaned state key(s) for removed device '{device_id}'")

        # Add new devices in parallel — sequential awaits would serialize
        # connect timeouts. Match the parallelization in start(). Bridges first
        # so a bridge-bound device finds its live bridge to prep the port.
        new_device_ids = list(project_ids - running_ids)
        if new_device_ids:
            for batch in self._bridge_first(new_device_ids, project_devices):
                add_results = await asyncio.gather(
                    *(self.devices.add_device(project_devices[did]) for did in batch),
                    return_exceptions=True,
                )
                for did, result in zip(batch, add_results):
                    if isinstance(result, Exception):
                        log.error(f"Failed to add device '{did}' during sync: {result}")

        # Update changed devices in parallel — compare raw project config AND
        # connection table entries separately to detect IP/port changes
        changed_ids: list[str] = []
        for device_id in running_ids & project_ids:
            old_config = self.devices.get_device_config(device_id) or {}
            new_config = project_devices[device_id]
            old_conn = old_config.get("config", {})
            new_conn = new_config.get("config", {})
            # Re-add (update_device does remove+add) when any field that
            # add_device acts on changes — not just name/driver/connection.
            # enabled gates connect/poll, child_entities seeds child labels,
            # and pending_settings is applied on (re)connect; omitting them
            # left those edits inert on the hot-reload path until a restart.
            if (old_config.get("name") != new_config.get("name") or
                    old_config.get("driver") != new_config.get("driver") or
                    old_config.get("enabled", True) != new_config.get("enabled", True) or
                    (old_config.get("child_entities") or {}) != (new_config.get("child_entities") or {}) or
                    (old_config.get("pending_settings") or {}) != (new_config.get("pending_settings") or {}) or
                    old_conn != new_conn):
                changed_ids.append(device_id)
        if changed_ids:
            update_results = await asyncio.gather(
                *(self.devices.update_device(did, project_devices[did])
                  for did in changed_ids),
                return_exceptions=True,
            )
            for did, result in zip(changed_ids, update_results):
                if isinstance(result, Exception):
                    log.error(f"Failed to update device '{did}' during sync: {result}")

    async def _sync_plugins(self) -> None:
        """Sync running plugins with project config on hot-reload."""
        if not self.project:
            return

        old_plugins = self.plugin_loader.get_known_plugin_ids()
        new_plugins = set(self.project.plugins.keys())

        # Plugins removed from project
        for plugin_id in old_plugins - new_plugins:
            if self.plugin_loader.is_running(plugin_id):
                await self.plugin_loader.stop_plugin(plugin_id)
            self.plugin_loader.remove_plugin_tracking(plugin_id)

        # Plugins added to project
        for plugin_id in new_plugins - old_plugins:
            entry = self.project.plugins[plugin_id]
            config = entry.config if hasattr(entry, "config") else entry.get("config", {})
            enabled = entry.enabled if hasattr(entry, "enabled") else entry.get("enabled", False)
            if enabled:
                await self.plugin_loader.start_plugin(plugin_id, config)

        # Plugins with changed config or enable/disable
        for plugin_id in old_plugins & new_plugins:
            entry = self.project.plugins[plugin_id]
            new_config = entry.config if hasattr(entry, "config") else entry.get("config", {})
            new_enabled = entry.enabled if hasattr(entry, "enabled") else entry.get("enabled", False)

            was_running = self.plugin_loader.is_running(plugin_id)
            old_config = self.plugin_loader.get_running_config(plugin_id) if was_running else {}

            if was_running and not new_enabled:
                await self.plugin_loader.stop_plugin(plugin_id)
            elif not was_running and new_enabled:
                await self.plugin_loader.start_plugin(plugin_id, new_config)
            elif was_running and new_config != old_config:
                await self.plugin_loader.stop_plugin(plugin_id)
                await self.plugin_loader.start_plugin(plugin_id, new_config)

    def bump_project_revision(self) -> None:
        """Advance the project revision after a server-side save that bypasses
        the reload path.

        Plugin enable/disable and plugin-config saves (e.g. the Video Streams
        editor) persist the project directly, without going through
        ``reload_project`` (which is what normally increments the revision).
        Left un-bumped, an open editor's cached ETag still matches the server,
        so its next full-project ``PUT /api/project`` overwrites these changes
        instead of being rejected with a 409. Bumping here keeps the
        optimistic-concurrency guard authoritative for every persisted change.
        """
        self._project_revision += 1

    async def _save_plugin_config(self, plugin_id: str, config: dict) -> None:
        """Save updated plugin config to the project file (callback for PluginAPI)."""
        if not self.project:
            return
        if plugin_id in self.project.plugins:
            previous = self.project.plugins[plugin_id].config
            self.project.plugins[plugin_id].config = config
            try:
                await save_project_async(self.project_path, self.project)
                self.bump_project_revision()
            except Exception as e:
                # Revert the in-memory project so it matches disk. Otherwise a
                # bad/unwritable config lingers in the shared project model and
                # the next save of ANYTHING re-serializes it and fails too.
                self.project.plugins[plugin_id].config = previous
                log.error(f"Failed to save plugin config for '{plugin_id}': {e}")
                await self.broadcast_ws({
                    "type": "error",
                    "message": f"Failed to save plugin config: {e}",
                })
                raise

    # --- UI Event Handling ---

    async def handle_ui_event(
        self, event_type: str, element_id: str, data: dict[str, Any] | None = None
    ) -> None:
        """
        Handle a UI event from a connected panel.

        Looks up the element's bindings and dispatches the appropriate action.
        """
        data = data or {}

        # Emit the raw UI event
        event_name = f"ui.{event_type}.{element_id}"
        await self.events.emit(event_name, {"element_id": element_id, **data})

        # Find the element and its bindings
        element = self._find_element(element_id)
        if not element:
            return

        bindings = element.bindings
        show = bindings.get("show") if isinstance(bindings.get("show"), dict) else {}
        do = bindings.get("do") if isinstance(bindings.get("do"), dict) else {}

        # Two-way LINK: a control whose value is bound with write_back drives the
        # state key it reflects. Only writable keys round-trip this way; a
        # device.* value is read-only and must be driven by a do.<interaction>
        # device.command with $value, never written to the state mirror directly
        # (a state.set to device.* no-ops, overwritten on the next poll). The
        # value source for both a slider/select/text_input ("change") and a list
        # row ("select") is show.value; the device guard here is defensive
        # against a hand-edited / AI-authored write_back on a device key.
        value_binding = show.get("value") if isinstance(show.get("value"), dict) else None
        if value_binding and value_binding.get("write_back"):
            link_key = value_binding.get("key", "")
            if link_key and not link_key.startswith("device."):
                # change → scale the display value to the element's output range;
                # select → write the tapped item's value as-is (a list has no
                # output range). Value is already a flat primitive (validated at
                # the WS boundary). The panel reads this same key to reflect the
                # control, so the write closes the two-way loop and lets
                # bindings/triggers/macros react to it.
                if event_type == "change":
                    self.state.set(
                        link_key,
                        self._scale_value_forward(element, data.get("value")),
                        source="ui",
                    )
                elif event_type == "select":
                    self.state.set(link_key, data.get("value"), source="ui")

        # Look up the action list for this interaction (always a list of actions)
        binding = do.get(event_type)

        # Toggle off: look for off_action inside the first press action that has one
        if not binding and event_type == "toggle_off":
            press_actions = do.get("press")
            if isinstance(press_actions, dict) and "off_action" in press_actions:
                binding = [press_actions["off_action"]]
            elif isinstance(press_actions, list):
                for act in press_actions:
                    if isinstance(act, dict) and "off_action" in act:
                        binding = [act["off_action"]]
                        break

        # Hold: look for hold_action inside the first press action that has one
        if not binding and event_type == "hold":
            press_actions = do.get("press")
            if isinstance(press_actions, dict) and "hold_action" in press_actions:
                binding = [press_actions["hold_action"]]
            elif isinstance(press_actions, list):
                for act in press_actions:
                    if isinstance(act, dict) and "hold_action" in act:
                        binding = [act["hold_action"]]
                        break

        if not binding:
            return

        # Binding is a list of actions — execute sequentially
        if not isinstance(binding, list):
            binding = [binding]
        for action_item in binding:
            if isinstance(action_item, dict):
                await self._execute_action(action_item, data, element)

    async def _execute_action(
        self, action_def: dict[str, Any], data: dict[str, Any],
        element: Any = None,
    ) -> None:
        """Execute a single UI binding action."""
        action = action_def.get("action", "")

        # The UI-event tokens a binding can reference. Built once so the
        # device.command and state.set branches resolve them identically: $value
        # is scaled to the element's output range; $input/$output come from
        # matrix route bindings; $mute comes from mute_route / audio_mute_route
        # bindings. Always all four keys so they resolve from the event, never
        # from the state store. Any other $var/$device/$system ref falls through
        # to the state store (the same shared resolver the macro engine uses).
        event_ctx = {
            "value": self._scale_value_forward(element, data.get("value")),
            "input": data.get("input"),
            "output": data.get("output"),
            "mute": data.get("mute"),
        }

        if action == "value_map":
            # Per-option action map (used by select elements).
            element_value = str(data.get("value", ""))
            action_map = action_def.get("map", {})
            mapped_action = action_map.get(element_value)
            if mapped_action:
                await self._execute_action(mapped_action, data, element)

        elif action == "macro":
            macro_id = action_def.get("macro", "")
            if macro_id:
                # Run macro in background so UI doesn't block
                task = asyncio.create_task(self.macros.execute(macro_id))
                task.add_done_callback(_log_task_exception)

        elif action == "device.command":
            device_id = action_def.get("device", "")
            command = action_def.get("command", "")
            params = dict(action_def.get("params", {}))
            # Resolve $-references in each param: the UI-event tokens above
            # ($value scaled, $input/$output/$mute), then any $var/$device/
            # $system ref from the state store.
            for k, v in params.items():
                params[k] = resolve_ref(v, state=self.state, event_ctx=event_ctx)
            try:
                await self.devices.send_command(device_id, command, params)
            except Exception:  # Catch-all: driver send_command may raise arbitrary errors
                log.exception(f"Binding command failed: {device_id}.{command}")

        elif action == "state.set":
            key = action_def.get("key", "")
            # Support "value_from": "element" to use the element's current value
            if action_def.get("value_from") == "element":
                value = data.get("value")
            else:
                # Resolve a $-reference in the literal value, with the same
                # event context as device.command — so $value works in a
                # state.set value and $var/$device/$system refs resolve like the
                # macro state.set, not pass through as a literal "$..." string.
                value = resolve_ref(
                    action_def.get("value"), state=self.state, event_ctx=event_ctx
                )
            # A hand-edited / AI-authored binding may carry a nested literal;
            # keep the store's flat-primitive invariant.
            value, coerced = _coerce_flat_primitive(value)
            if coerced:
                log.warning(
                    "state.set binding for key '%s' had a non-primitive value; "
                    "coerced to a JSON string", key,
                )
            self.state.set(key, value, source="ui")

        elif action in ("page", "navigate"):
            # Page navigation — broadcast to all panels so they can switch
            page_id = action_def.get("page", "")
            if page_id:
                await self.events.emit(f"ui.page.{page_id}")
                await self.broadcast_ws({
                    "type": "ui.navigate",
                    "page_id": page_id,
                })

        elif action == "script.call":
            func_name = action_def.get("function", "")
            if func_name:
                await self.events.emit(f"script.call.{func_name}", data)

    @staticmethod
    def _scale_value_forward(element: Any, raw_value: Any) -> Any:
        """Scale a display value to a device value using output_min/output_max."""
        if raw_value is None or element is None:
            return raw_value
        output_min = getattr(element, "output_min", None)
        output_max = getattr(element, "output_max", None)
        if output_min is None or output_max is None:
            return raw_value

        val = float(raw_value)
        if getattr(element, "scale_to_full", None) is False:
            return max(output_min, min(output_max, val))

        display_min = getattr(element, "min", None)
        display_max = getattr(element, "max", None)
        if display_min is None or display_max is None:
            return raw_value
        display_range = display_max - display_min
        if display_range == 0:
            return output_min
        frac = (val - display_min) / display_range
        return output_min + frac * (output_max - output_min)

    def _find_element(self, element_id: str) -> Any | None:
        """Find a UI element by ID across all pages."""
        if not self.project:
            return None
        for page in self.project.ui.pages:
            for element in page.elements:
                if element.id == element_id:
                    return element
        return None

    def _load_project_safe(self) -> ProjectConfig:
        """Load project.avc with corruption recovery.

        If the project file is missing, corrupted, or fails validation:
        1. Try restoring from the most recent backup
        2. If no backup works, create a minimal empty project so the server starts
        """
        from server.core.backup_manager import list_backups, restore_from_backup

        project_dir = self.project_path.parent

        # Happy path — load normally
        try:
            return load_project(self.project_path)
        except FileNotFoundError:
            log.warning(f"Project file not found: {self.project_path}")
        except json.JSONDecodeError as e:
            log.error(f"Project file is corrupted (invalid JSON): {e}")
        except Exception as e:
            log.error(f"Project file failed to load: {e}")

        # Try restoring from backups, newest first
        backups = list_backups(project_dir)
        for backup in backups:
            backup_path = project_dir / backup.filename
            log.info(f"Attempting restore from backup: {backup.filename}")
            try:
                restore_from_backup(backup_path, project_dir)
                project = load_project(self.project_path)
                log.info(f"Successfully restored project from backup: {backup.filename}")
                return project
            except Exception as e:
                log.warning(f"Backup restore failed ({backup.filename}): {e}")
                continue

        # No backups worked — create minimal empty project
        log.warning("No backups available. Creating empty recovery project.")
        from datetime import datetime, timezone
        empty = ProjectConfig(
            project=ProjectMeta(
                id="recovery",
                name="Recovery Project",
                description="Auto-created after project corruption. Use File > Open to load a project.",
                created=datetime.now(timezone.utc).isoformat(),
                modified=datetime.now(timezone.utc).isoformat(),
            )
        )
        # save_project writes via tempfile.mkstemp(dir=path.parent) and a
        # .avc.bak sibling, both of which require the parent directory to
        # exist. Without this, fresh installs / OPENAVC_PROJECT pointed at
        # a not-yet-created directory crash startup with FileNotFoundError.
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        save_project(self.project_path, empty)
        return empty

    def _load_project_drivers(self) -> None:
        """Reload drivers from the global driver_repo/ directory.

        Called after project load to pick up any drivers installed after
        the initial startup load.
        """
        from server.drivers.driver_loader import load_all_drivers
        from server.system_config import DRIVER_REPO_DIR

        driver_repo = DRIVER_REPO_DIR
        if driver_repo.exists():
            loaded = load_all_drivers([driver_repo])
            if loaded:
                log.info(f"Loaded {loaded} driver(s) from {driver_repo}")

    def _init_variable_values(self, persisted_values: dict[str, Any]) -> set[str]:
        """Seed ``var.*`` state from defaults / persisted values.

        Returns the set of currently-persistent var keys (for the persister to
        watch). A still-persistent variable's saved value wins over its
        default; a variable whose persist flag was turned OFF reverts to its
        default even if state.json still holds a stale value — the restore is
        gated on the *current* persist flag, not merely on the key's presence
        in the file.
        """
        persistent_keys: set[str] = set()
        if not self.project:
            return persistent_keys
        for var in self.project.variables:
            key = f"var.{var.id}"
            if var.persist:
                persistent_keys.add(key)
            if var.persist and key in persisted_values:
                self.state.set(key, persisted_values[key], source="system")
            else:
                self.state.set(key, var.default, source="system")
        return persistent_keys

    def _bind_variable_sources(self) -> None:
        """
        Set up auto-sync subscriptions for variables with source_key.
        When the source state key changes, the variable's value is updated
        automatically, optionally mapped through source_map.
        """
        # Unsubscribe existing bindings
        for sub_id in self._var_binding_subs:
            self.state.unsubscribe(sub_id)
        self._var_binding_subs.clear()
        self._var_binding_active.clear()

        if not self.project:
            return

        def _map_value(raw: Any, sm: dict | None, vk: str) -> Any:
            """Apply source_map then enforce the flat-primitive invariant.

            source_map values are typed Any in the schema, so an author can
            map to a list/dict; flatten those rather than letting a nested
            value into the store and out to WS / ISC / the cloud relay.
            """
            mapped = sm.get(str(raw), raw) if sm else raw
            value, was_coerced = _coerce_flat_primitive(mapped)
            if was_coerced:
                log.warning(
                    "Variable binding %s: mapped value for source %r is not a "
                    "flat primitive; coerced to a JSON string", vk, mapped,
                )
            return value

        for var in self.project.variables:
            if not var.source_key:
                continue

            var_key = f"var.{var.id}"
            source_key = var.source_key
            source_map = var.source_map

            # A source_key must be one concrete state key. Glob metacharacters
            # would register a multi-key fan-in (no defined value, last-writer-
            # wins thrash), so reject rather than silently binding a pattern.
            if any(c in source_key for c in _GLOB_METACHARS):
                log.warning(
                    "Variable '%s' source_key %r contains glob metacharacters; "
                    "skipping binding (source_key must be a single state key)",
                    var.id, source_key,
                )
                continue

            # Initial sync: read current source value and apply
            current = self.state.get(source_key)
            if current is not None:
                self.state.set(var_key, _map_value(current, source_map, var_key),
                               source="variable_binding")

            # Subscribe to changes. The re-entrancy guard keys on the variable
            # being written, not on the source string: chained bindings (var B
            # bound to var A's key) propagate when A updates, while a genuine
            # cycle (A<->B) terminates after one hop. The previous blanket
            # "ignore variable_binding source" guard froze every chain.
            def make_handler(vk: str, sm: dict | None):
                def handler(key: str, old_value: Any, new_value: Any, source: str):
                    if vk in self._var_binding_active:
                        return  # cycle — this var is already mid-propagation
                    value = _map_value(new_value, sm, vk)
                    self._var_binding_active.add(vk)
                    try:
                        self.state.set(vk, value, source="variable_binding")
                    finally:
                        self._var_binding_active.discard(vk)
                return handler

            sub_id = self.state.subscribe(
                source_key, make_handler(var_key, source_map)
            )
            self._var_binding_subs.append(sub_id)
            log.debug(f"Variable binding: {var_key} ← {source_key}"
                      f"{' (with map)' if source_map else ''}")

    def _register_variable_validation(self) -> None:
        """Register state listeners that warn when validation rules are violated."""
        for sub_id in self._var_validation_subs:
            self.state.unsubscribe(sub_id)
        self._var_validation_subs.clear()

        if not self.project:
            return

        for var in self.project.variables:
            if not var.validation:
                continue
            var_key = f"var.{var.id}"
            val = var.validation

            def make_handler(vk: str, vid: str, vtype: str, v_rules):
                def handler(key: str, old_value, new_value, source: str):
                    if source == "system":
                        return  # Don't warn on init
                    if new_value is None:
                        return
                    warnings = []
                    if vtype == "number" and isinstance(new_value, (int, float)):
                        if v_rules.min is not None and new_value < v_rules.min:
                            warnings.append(f"value {new_value} is below minimum {v_rules.min}")
                        if v_rules.max is not None and new_value > v_rules.max:
                            warnings.append(f"value {new_value} exceeds maximum {v_rules.max}")
                    if vtype == "string" and v_rules.allowed and isinstance(new_value, str):
                        if new_value not in v_rules.allowed:
                            warnings.append(f"value '{new_value}' is not in allowed values: {v_rules.allowed}")
                    for w in warnings:
                        log.warning(f"Variable validation: var.{vid} — {w} (source={source})")
                return handler

            sub_id = self.state.subscribe(
                var_key, make_handler(var_key, var.id, var.type, val)
            )
            self._var_validation_subs.append(sub_id)

    def _register_ui_bindings(self) -> None:
        """Walk all UI elements and log their interaction bindings for debugging."""
        if not self.project:
            return
        count = 0
        for page in self.project.ui.pages:
            for element in page.elements:
                do = element.bindings.get("do") if element.bindings else None
                if isinstance(do, dict):
                    count += sum(1 for actions in do.values() if actions)
        log.info(f"Registered {count} UI binding(s)")

    # --- WebSocket Management ---

    def add_ws_client(self, ws, ns_prefixes: tuple[str, ...] | None = None) -> None:
        """Register a WebSocket client with optional namespace filter."""
        self._ws_clients.add(ws)
        if ns_prefixes:
            self._ws_ns_filters[id(ws)] = ns_prefixes
        log.info(f"WebSocket client connected ({len(self._ws_clients)} total)")

    def remove_ws_client(self, ws) -> None:
        """Unregister a WebSocket client."""
        self._ws_clients.discard(ws)
        self._ws_ns_filters.pop(id(ws), None)
        log.info(f"WebSocket client disconnected ({len(self._ws_clients)} total)")

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected WebSocket clients."""
        if not self._ws_clients:
            return

        msg_type = message.get("type")
        # state.update and state.delete carry per-key payloads; a client with
        # ns_prefixes should only receive keys under those namespaces.
        is_filterable = msg_type in ("state.update", "state.delete")
        has_any_filters = bool(self._ws_ns_filters)

        # Fast path: no namespace filters or not a filterable message type
        if not is_filterable or not has_any_filters:
            text = json.dumps(message)
            clients = list(self._ws_clients)
            results = await asyncio.gather(
                *(ws.send_text(text) for ws in clients),
                return_exceptions=True,
            )
            for ws, result in zip(clients, results):
                if isinstance(result, Exception):
                    self._ws_clients.discard(ws)
                    self._ws_ns_filters.pop(id(ws), None)
            return

        # Slow path: filter per client based on message type
        full_text: str | None = None
        sends: list[tuple[Any, Any]] = []  # (ws, send_coroutine)
        for ws in list(self._ws_clients):
            ns = self._ws_ns_filters.get(id(ws))
            if not ns:
                if full_text is None:
                    full_text = json.dumps(message)
                sends.append((ws, ws.send_text(full_text)))
                continue

            if msg_type == "state.update":
                changes = message.get("changes", {})
                filtered = {k: v for k, v in changes.items()
                            if k.startswith(ns)}
                if not filtered:
                    continue
                sends.append((ws, ws.send_text(json.dumps({
                    "type": "state.update", "changes": filtered,
                }))))
            else:  # state.delete
                keys = message.get("keys", [])
                filtered_keys = [k for k in keys if k.startswith(ns)]
                if not filtered_keys:
                    continue
                sends.append((ws, ws.send_text(json.dumps({
                    "type": "state.delete", "keys": filtered_keys,
                }))))

        if not sends:
            return
        clients_to_send = [ws for ws, _ in sends]
        results = await asyncio.gather(
            *(coro for _, coro in sends),
            return_exceptions=True,
        )
        for ws, result in zip(clients_to_send, results):
            if isinstance(result, Exception):
                self._ws_clients.discard(ws)
                self._ws_ns_filters.pop(id(ws), None)

    async def _on_pending_settings_applied(
        self, event: str, payload: dict[str, Any]
    ) -> None:
        """Persist project file after pending device settings are applied."""
        device_id = payload.get("device_id", "")
        if not self.project or not device_id:
            return

        # Update the project config to clear applied pending settings
        remaining = payload.get("remaining", {})
        for dev in self.project.devices:
            if dev.id == device_id:
                dev.pending_settings = remaining
                break

        await save_project_async(self.project_path, self.project)
        log.info(f"[{device_id}] Project saved after applying pending settings")

    async def _on_script_error(self, event: str, payload: dict[str, Any]) -> None:
        """Forward script error events to WebSocket clients."""
        await self.broadcast_ws({"type": "script.error", **payload})

    async def _on_trigger_event(self, event: str, payload: dict[str, Any]) -> None:
        """Forward trigger events to WebSocket clients."""
        await self.broadcast_ws({"type": event, **payload})

    async def _on_macro_event(self, event: str, payload: dict[str, Any]) -> None:
        """Forward macro lifecycle events to WebSocket clients."""
        # event is like "macro.progress.system_on" -> extract "progress"
        parts = event.split(".")
        event_type = parts[1] if len(parts) >= 2 else "unknown"
        await self.broadcast_ws({
            "type": f"macro.{event_type}",
            **payload,
        })

    async def _on_plugin_event(self, event: str, payload: dict[str, Any]) -> None:
        """Forward plugin lifecycle events to WebSocket clients."""
        await self.broadcast_ws({"type": event, **(payload or {})})

    def _on_state_change(
        self, key: str, old_value: Any, new_value: Any, source: str
    ) -> None:
        """Collect state changes into a batch for WebSocket push.

        Safe in single-threaded asyncio: this sync callback runs atomically
        between awaits of the flush loop. The lock is acquired in the flush
        loop to guard the read-clear operation.

        Distinguishes deletion from set-to-None by probing the store: when
        StateStore.delete() fires the listener, the key has already been
        removed. Deletes go to _state_deleted_keys; sets go to _state_batch.
        Either action clears the key from the other bucket so a delete-then-set
        (or set-then-delete) within one window resolves to the latest action.
        """
        is_deleted = new_value is None and self.state.get(key, _STATE_MISSING) is _STATE_MISSING
        if is_deleted:
            self._state_batch.pop(key, None)
            self._state_deleted_keys.add(key)
        else:
            self._state_deleted_keys.discard(key)
            self._state_batch[key] = new_value

    async def _flush_state_batch_loop(self) -> None:
        """Periodically flush batched state changes to WebSocket clients."""
        try:
            while self._running:
                await asyncio.sleep(0.05)  # 50ms = max 20 updates/sec
                if not self._state_batch and not self._state_deleted_keys:
                    continue
                await self._flush_state_batch()
        except asyncio.CancelledError:
            pass
        finally:
            # Best-effort flush of any remaining batch during shutdown.
            if self._state_batch or self._state_deleted_keys:
                try:
                    await self._flush_state_batch()
                except Exception:  # Errors are non-critical at shutdown
                    pass

    async def _flush_state_batch(self) -> None:
        """Drain _state_batch and _state_deleted_keys into WS messages.

        Emits a state.update for set keys and a state.delete for deleted
        keys. Atomic swap of the buffers ensures _on_state_change calls
        between the two broadcasts land in the next flush window.
        """
        # Swap out the buffers atomically — sync _on_state_change can't
        # interleave with us between awaits, but this pattern is safe if
        # callers ever change.
        batch = self._state_batch
        self._state_batch = {}
        deleted = self._state_deleted_keys
        self._state_deleted_keys = set()

        if batch:
            await self.broadcast_ws({
                "type": "state.update",
                "changes": batch,
            })
        if deleted:
            await self.broadcast_ws({
                "type": "state.delete",
                "keys": sorted(deleted),
            })

    # --- Periodic backup ---

    _PERIODIC_BACKUP_INTERVAL = 1800  # 30 minutes

    async def _periodic_backup_loop(self) -> None:
        """Create an auto-backup every 30 minutes if the project has changed."""
        try:
            while True:
                await asyncio.sleep(60)  # Check every 60 seconds
                if not self._dirty_since_backup:
                    continue
                if self._last_backup_time and (time.time() - self._last_backup_time < self._PERIODIC_BACKUP_INTERVAL):
                    continue
                try:
                    from server.core.backup_manager import create_backup
                    # Offload the (uncapped) project+assets ZIP compression to a
                    # worker thread — running it inline stalls device polling, WS
                    # state pushes, command dispatch, and cloud heartbeats for
                    # the whole compression on the event-loop thread.
                    await asyncio.to_thread(
                        create_backup, self.project_path.parent, "Auto-backup"
                    )
                    self._dirty_since_backup = False
                    self._last_backup_time = time.time()
                except Exception:
                    log.debug("Periodic backup failed", exc_info=True)
        except asyncio.CancelledError:
            pass

    def create_backup(self, reason: str) -> None:
        """Convenience method to create a named backup of the current project."""
        from server.core.backup_manager import create_backup
        result = create_backup(self.project_path.parent, reason)
        if result:
            self._last_backup_time = time.time()
            self._dirty_since_backup = False

    # --- ISC helpers ---

    async def _start_isc(self) -> None:
        """Initialize ISC if enabled in both system config and project."""
        # Read the live system-config value, not the import-time
        # config.ISC_ENABLED constant, so a PATCH /system/config toggle is
        # honored on reload/reconcile without a restart.
        from server.system_config import get_system_config
        isc_enabled = bool(get_system_config().get("isc", "enabled", True))
        if not self.project or not isc_enabled or not self.project.isc.enabled:
            return
        try:
            from server.core.isc import ISCManager, get_or_create_instance_id
            instance_id = get_or_create_instance_id(self.project_path)
            instance_name = self.project.project.name
            self.isc = ISCManager(
                state=self.state,
                events=self.events,
                devices=self.devices,
                shared_state_patterns=self.project.isc.shared_state,
                auth_key=self.project.isc.auth_key,
                instance_id=instance_id,
                instance_name=instance_name,
                http_port=config.HTTP_PORT,
                manual_peers=self.project.isc.peers,
                allowed_remote_commands=self.project.isc.allowed_remote_commands,
            )
            await self.isc.start()
            # Wire ISC manager into the ISC WebSocket endpoint
            from server.api.isc_ws import set_isc_manager
            set_isc_manager(self.isc)
            # Wire ISC into the script API
            from server.core.script_api import isc as isc_proxy
            isc_proxy._bind(self.isc)
        except Exception:  # Catch-all: isolates ISC subsystem errors from core startup
            log.exception("ISC: Failed to start — continuing without ISC")
            self.isc = None

    async def _reload_isc(self) -> None:
        """Reload ISC configuration after project change."""
        if not self.project:
            return
        from server.system_config import get_system_config
        isc_enabled = bool(get_system_config().get("isc", "enabled", True))
        isc_should_run = isc_enabled and self.project.isc.enabled

        if self.isc and isc_should_run:
            # Hot-reload config
            await self.isc.reload(
                shared_state_patterns=self.project.isc.shared_state,
                auth_key=self.project.isc.auth_key,
                manual_peers=self.project.isc.peers,
                allowed_remote_commands=self.project.isc.allowed_remote_commands,
            )
        elif self.isc and not isc_should_run:
            # ISC was running but project disabled it
            await self.isc.stop()
            self.isc = None
            from server.api.isc_ws import set_isc_manager
            set_isc_manager(None)
            # Unbind the script API proxy. Otherwise scripts calling
            # isc.send_to() / isc.broadcast() reach the stopped manager
            # and surface a misleading ConnectionError instead of the
            # intended "ISC not enabled" RuntimeError.
            from server.core.script_api import isc as isc_proxy
            isc_proxy._bind(None)
        elif not self.isc and isc_should_run:
            # ISC was off but project enabled it
            await self._start_isc()

    # --- mDNS Advertiser helpers ---

    async def _start_mdns_advertiser(self) -> None:
        """Start mDNS service advertisement if enabled in system config."""
        # Live system-config read (not the import-time config.MDNS_ADVERTISE
        # constant) so a PATCH /system/config toggle is honored on reconcile.
        from server.system_config import get_system_config
        if not get_system_config().get("discovery", "advertise", True):
            return
        try:
            from server.core.isc import get_or_create_instance_id
            from server.discovery.mdns_advertiser import MDNSAdvertiser

            instance_id = get_or_create_instance_id(self.project_path)
            self.mdns_advertiser = MDNSAdvertiser(
                instance_name=self.project.project.name,
                instance_id=instance_id,
                http_port=config.HTTP_PORT,
                version=__version__,
                tls_enabled=config.TLS_ENABLED,
                tls_port=config.TLS_PORT,
            )
            await self.mdns_advertiser.start()
        except Exception:
            log.exception("mDNS advertiser: failed to start — continuing without advertisement")
            self.mdns_advertiser = None

    async def _reconcile_mdns(self) -> None:
        """Start or stop the mDNS advertiser to match the live system config."""
        from server.system_config import get_system_config
        should_run = bool(get_system_config().get("discovery", "advertise", True))
        if should_run and not self.mdns_advertiser:
            await self._start_mdns_advertiser()
        elif not should_run and self.mdns_advertiser:
            await self.mdns_advertiser.stop()
            self.mdns_advertiser = None

    async def reconcile_runtime_services(self) -> None:
        """Re-evaluate ISC and mDNS advertisement against the live system
        config so a ``PATCH /system/config`` toggle takes effect without a
        restart. Safe to call when nothing changed (each branch is a no-op if
        the subsystem already matches the desired state)."""
        await self._reload_isc()
        await self._reconcile_mdns()

    # --- Cloud Agent helpers ---

    async def _start_cloud_agent(self) -> None:
        """Initialize the cloud agent if enabled in system config."""
        if not config.CLOUD_ENABLED:
            return
        if not config.CLOUD_SYSTEM_KEY or not config.CLOUD_SYSTEM_ID:
            log.warning("Cloud agent: enabled but missing system_key or system_id, skipping")
            return
        try:
            from server.cloud.agent import CloudAgent
            from server.cloud.heartbeat import HeartbeatCollector
            from server.cloud.state_relay import StateRelay
            from server.cloud.command_handler import CommandHandler
            from server.cloud.alert_monitor import AlertMonitor
            from server.cloud.tunnel import TunnelHandler

            cloud_config = {
                "endpoint": config.CLOUD_ENDPOINT,
                "system_key": config.CLOUD_SYSTEM_KEY,
                "system_id": config.CLOUD_SYSTEM_ID,
                "heartbeat_interval": config.CLOUD_HEARTBEAT_INTERVAL,
                "state_batch_interval": config.CLOUD_STATE_BATCH_INTERVAL,
            }

            self.cloud_agent = CloudAgent(self.state, self.events, self.devices, cloud_config)

            # Wire subsystems
            heartbeat = HeartbeatCollector(
                self.state, self.devices,
                ws_client_count_fn=lambda: len(self._ws_clients),
            )
            self.cloud_agent.set_heartbeat_collector(heartbeat)

            relay = StateRelay(self.cloud_agent, self.state)
            self.cloud_agent.set_state_relay(relay)

            handler = CommandHandler(
                self.cloud_agent, self.devices, self.events,
                reload_fn=self.reload_project,
                project_path=self.project_path,
            )
            self.cloud_agent.set_command_handler(handler)

            from server.cloud.ai_tool_handler import AIToolHandler
            ai_tool_handler = AIToolHandler(
                self.cloud_agent, self.devices, self.events,
                reload_fn=self.reload_project,
                project_path=self.project_path,
            )
            self.cloud_agent.set_ai_tool_handler(ai_tool_handler)

            alert_monitor = AlertMonitor(self.cloud_agent, self.state, self.events)
            self.cloud_agent.set_alert_monitor(alert_monitor)

            tunnel_handler = TunnelHandler(self.cloud_agent)
            self.cloud_agent.set_tunnel_handler(tunnel_handler)

            # Connect (runs in background)
            await self.cloud_agent.connect()
            log.info("Cloud agent: initialized and connecting")
        except Exception:  # Catch-all: isolates cloud subsystem errors from core startup
            log.exception("Cloud agent: failed to start — continuing without cloud")
            self.cloud_agent = None

    # --- Status ---

    def _detect_network_info(self) -> tuple[str, str]:
        """Return ``(local_ip, hostname)``, cached after the first call.

        Uses a context-managed socket so the descriptor is always closed even
        when ``connect()`` fails (no FD leak on isolated/no-route control
        networks), and caches the result — both syscalls block the event loop
        and the values rarely change over a process's lifetime.
        """
        if self._network_info is not None:
            return self._network_info
        local_ip = "127.0.0.1"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
        except OSError:
            pass
        try:
            hostname = socket.gethostname()
        except OSError:
            hostname = ""
        self._network_info = (local_ip, hostname)
        return self._network_info

    def refresh_network_info(self) -> tuple[str, str]:
        """Re-detect (and re-cache) the local IP and hostname.

        Blocking — call off-loop. The setup screen polls through this so a
        device that boots before its network is up shows its address as soon
        as the cable goes in, instead of serving the stale startup cache.
        """
        self._network_info = None
        return self._detect_network_info()

    def get_status(self, include_sensitive: bool = True) -> dict[str, Any]:
        """Return system status info.

        ``include_sensitive`` gates host/network identifiers (hostname, local
        IP, bind address). The open, unauthenticated ``/api/status`` route
        passes ``False`` for anonymous callers so a claimed instance doesn't
        disclose LAN reconnaissance details; authenticated callers (the IDE,
        which needs them to build panel access URLs) get the full set.
        """
        uptime = time.time() - self._start_time if self._start_time else 0

        status = {
            "status": "running" if self._running else "stopped",
            "version": __version__,
            "uptime_seconds": round(uptime, 1),
            "project_name": (
                self.project.project.name if self.project else "No project"
            ),
            "device_count": len(self.devices.list_devices()),
            "macro_count": len(self.macros._macros),
            "script_handlers": (
                self.scripts.handler_count() if self.scripts else 0
            ),
            "ws_clients": len(self._ws_clients),
            "isc_enabled": self.isc is not None,
            "cloud_enabled": self.cloud_agent is not None,
            "http_port": config.HTTP_PORT,
        }
        if include_sensitive:
            local_ip, hostname = self._detect_network_info()
            status["hostname"] = hostname
            status["local_ip"] = local_ip
            status["bind_address"] = config.BIND_ADDRESS
        if self.isc:
            status["isc_peers"] = sum(
                1 for p in self.isc._peers.values() if p.connected
            )
        if self.cloud_agent:
            status["cloud_connected"] = self.cloud_agent._connected
        return status
