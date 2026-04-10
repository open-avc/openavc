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
from server.core.project_loader import ProjectConfig, load_project, save_project
from server.core.script_engine import ScriptEngine
from server.core.state_persister import StatePersister
from server.core.state_store import StateStore
from server.core.trigger_engine import TriggerEngine
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
        self.macros = MacroEngine(self.state, self.events, self.devices)
        self.triggers = TriggerEngine(self.state, self.events, self.macros)
        self.scripts: ScriptEngine | None = None
        self.plugin_loader = PluginLoader(self.state, self.events, self.macros, self.devices)
        self.persister: StatePersister | None = None
        self.isc = None  # ISCManager, initialized in start() if enabled
        self.cloud_agent = None  # CloudAgent, initialized in start() if enabled
        self.update_manager = None  # UpdateManager, initialized in start()

        # Simulation
        from server.core.simulation import SimulationManager
        self.simulation = SimulationManager(self)

        # Wire StateStore -> EventBus
        self.state.set_event_bus(self.events)

        # WebSocket clients (set of WebSocket connections)
        self._ws_clients: set = set()

        # State batching for WebSocket push
        self._state_batch: dict[str, Any] = {}
        self._batch_task: asyncio.Task | None = None
        self._batch_lock = asyncio.Lock()

        # Variable-to-state binding subscriptions
        self._var_binding_subs: list[str] = []
        self._var_validation_subs: list[str] = []
        self._project_revision: int = 0  # incremented on every save

        # Event/state subscription IDs (for cleanup on stop/reload)
        self._state_sub_ids: list[str] = []
        self._event_sub_ids: list[str] = []

        # Tracking
        self._start_time: float = 0
        self._running = False
        self._marker_confirm_task: asyncio.Task | None = None

        # Periodic backup
        self._periodic_backup_task: asyncio.Task | None = None
        self._dirty_since_backup: bool = False
        self._last_backup_time: float = 0

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
        from server.system_config import get_system_config
        sys_config = get_system_config()
        sys_config.ensure_file()

        # Set system state keys
        self.state.set("system.version", __version__, source="system")
        self.state.set("system.update_available", "", source="system")
        self.state.set("system.update_channel", sys_config.get("updates", "channel", "stable"), source="system")
        self.state.set("system.update_status", "idle", source="system")
        self.state.set("system.update_progress", 0, source="system")
        self.state.set("system.update_error", "", source="system")

        # Load project
        self.project = load_project(self.project_path)

        # Load project-level drivers (community drivers installed via IDE)
        self._load_project_drivers()

        # Initialize state persister and load saved values
        state_file = self.project_path.parent / "state.json"
        self.persister = StatePersister(state_file, self.state)
        persisted_values = self.persister.load()

        # Initialize user variables: persisted value takes priority over default
        persistent_keys: set[str] = set()
        for var in self.project.variables:
            key = f"var.{var.id}"
            if var.persist:
                persistent_keys.add(key)
            if key in persisted_values:
                self.state.set(key, persisted_values[key], source="system")
            else:
                self.state.set(key, var.default, source="system")

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

        # Add and connect devices (merge connection table into device config)
        startup_errors: list[str] = []
        for device in self.project.devices:
            try:
                await self.devices.add_device(self.resolved_device_config(device))
            except Exception as e:  # Catch-all: isolates individual device startup failures
                startup_errors.append(f"Device '{device.id}': {e}")
                log.error(f"Failed to add device '{device.id}': {e}")

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

        # Record startup errors (if any) for UI visibility
        if startup_errors:
            self.state.set("system.startup_errors", len(startup_errors), source="system")
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

        self.state.set("system.started", False, source="system")
        log.info("Engine stopped")

    async def reload_project(self) -> None:
        """Hot-reload project.avc without full restart."""
        log.info("Reloading project...")
        self.project = load_project(self.project_path)
        self._project_revision += 1
        self._dirty_since_backup = True

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

        # Reload triggers
        await self.triggers.stop()
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
        """Get device config dict with connection table entries merged in."""
        cfg = device.model_dump() if hasattr(device, "model_dump") else dict(device)
        conn = self.project.connections.get(cfg["id"], {})
        if conn:
            cfg["config"] = {**cfg.get("config", {}), **conn}
        return cfg

    async def _sync_devices(self) -> None:
        """Sync running devices with project config (add new, remove deleted, update changed)."""
        if not self.project:
            return

        # Build merged configs (device.config + connection table overrides)
        project_devices: dict[str, dict] = {}
        for d in self.project.devices:
            project_devices[d.id] = self.resolved_device_config(d)

        running_ids = set(self.devices._device_configs.keys())
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

        # Add new devices
        for device_id in project_ids - running_ids:
            await self.devices.add_device(project_devices[device_id])

        # Update changed devices — compare raw project config AND connection
        # table entries separately to detect IP/port changes
        for device_id in running_ids & project_ids:
            old_config = self.devices._device_configs.get(device_id, {})
            new_config = project_devices[device_id]
            old_conn = old_config.get("config", {})
            new_conn = new_config.get("config", {})
            if (old_config.get("name") != new_config.get("name") or
                    old_config.get("driver") != new_config.get("driver") or
                    old_conn != new_conn):
                await self.devices.update_device(device_id, new_config)

    async def _sync_plugins(self) -> None:
        """Sync running plugins with project config on hot-reload."""
        if not self.project:
            return

        old_plugins = set(self.plugin_loader._instances.keys()) | set(
            pid for pid, s in self.plugin_loader._status.items()
            if s in ("stopped", "missing", "incompatible", "error")
        )
        new_plugins = set(self.project.plugins.keys())

        # Plugins removed from project
        for plugin_id in old_plugins - new_plugins:
            if plugin_id in self.plugin_loader._instances:
                await self.plugin_loader.stop_plugin(plugin_id)
            # Clear status tracking
            self.plugin_loader._status.pop(plugin_id, None)
            self.plugin_loader._missing_plugins.pop(plugin_id, None)
            self.plugin_loader._incompatible_plugins.pop(plugin_id, None)

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

            was_running = plugin_id in self.plugin_loader._instances
            old_config = {}
            if was_running:
                api = self.plugin_loader._apis.get(plugin_id)
                old_config = api._config if api else {}

            if was_running and not new_enabled:
                await self.plugin_loader.stop_plugin(plugin_id)
            elif not was_running and new_enabled:
                await self.plugin_loader.start_plugin(plugin_id, new_config)
            elif was_running and new_config != old_config:
                await self.plugin_loader.stop_plugin(plugin_id)
                await self.plugin_loader.start_plugin(plugin_id, new_config)

    async def _save_plugin_config(self, plugin_id: str, config: dict) -> None:
        """Save updated plugin config to the project file (callback for PluginAPI)."""
        if not self.project:
            return
        if plugin_id in self.project.plugins:
            self.project.plugins[plugin_id].config = config
            try:
                save_project(self.project_path, self.project)
            except (OSError, ValueError, TypeError) as e:
                log.error(f"Failed to save plugin config for '{plugin_id}': {e}")
                await self.broadcast_ws({
                    "type": "error",
                    "message": f"Failed to save plugin config: {e}",
                })

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

        # Two-way variable binding: on "change" events, set the variable
        if event_type == "change":
            variable_binding = bindings.get("variable")
            if variable_binding and isinstance(variable_binding, dict):
                var_key = variable_binding.get("key", "")
                if var_key:
                    self.state.set(var_key, data.get("value"), source="ui")

        # Look up the binding for this event type (always a list of actions)
        binding = bindings.get(event_type)

        # Toggle off: look for off_action inside the first press action that has one
        if not binding and event_type == "toggle_off":
            press_actions = bindings.get("press")
            if isinstance(press_actions, dict) and "off_action" in press_actions:
                binding = [press_actions["off_action"]]
            elif isinstance(press_actions, list):
                for act in press_actions:
                    if isinstance(act, dict) and "off_action" in act:
                        binding = [act["off_action"]]
                        break

        # Hold: look for hold_action inside the first press action that has one
        if not binding and event_type == "hold":
            press_actions = bindings.get("press")
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
                await self._execute_action(action_item, data)

    async def _execute_action(
        self, action_def: dict[str, Any], data: dict[str, Any]
    ) -> None:
        """Execute a single UI binding action."""
        action = action_def.get("action", "")

        if action == "value_map":
            # Per-option action map (used by select elements).
            element_value = str(data.get("value", ""))
            action_map = action_def.get("map", {})
            mapped_action = action_map.get(element_value)
            if mapped_action:
                await self._execute_action(mapped_action, data)

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
            # Replace $value placeholder with actual value from UI event
            for k, v in params.items():
                if v == "$value":
                    params[k] = data.get("value")
                elif v == "$input":
                    params[k] = data.get("input")
                elif v == "$output":
                    params[k] = data.get("output")
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
                value = action_def.get("value")
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

    def _find_element(self, element_id: str) -> Any | None:
        """Find a UI element by ID across all pages."""
        if not self.project:
            return None
        for page in self.project.ui.pages:
            for element in page.elements:
                if element.id == element_id:
                    return element
        return None

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

        if not self.project:
            return

        for var in self.project.variables:
            if not var.source_key:
                continue

            var_key = f"var.{var.id}"
            source_key = var.source_key
            source_map = var.source_map

            # Initial sync: read current source value and apply
            current = self.state.get(source_key)
            if current is not None:
                mapped = source_map.get(str(current), current) if source_map else current
                self.state.set(var_key, mapped, source="variable_binding")

            # Subscribe to changes
            def make_handler(vk: str, sm: dict | None):
                def handler(key: str, old_value: Any, new_value: Any, source: str):
                    if source == "variable_binding":
                        return  # Prevent loops
                    mapped = sm.get(str(new_value), new_value) if sm else new_value
                    self.state.set(vk, mapped, source="variable_binding")
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
        """Walk all UI elements and log their bindings for debugging."""
        if not self.project:
            return
        count = 0
        for page in self.project.ui.pages:
            for element in page.elements:
                if element.bindings:
                    for event_type in ("press", "release", "change"):
                        if event_type in element.bindings:
                            count += 1
        log.info(f"Registered {count} UI binding(s)")

    # --- WebSocket Management ---

    def add_ws_client(self, ws) -> None:
        """Register a WebSocket client."""
        self._ws_clients.add(ws)
        log.info(f"WebSocket client connected ({len(self._ws_clients)} total)")

    def remove_ws_client(self, ws) -> None:
        """Unregister a WebSocket client."""
        self._ws_clients.discard(ws)
        log.info(f"WebSocket client disconnected ({len(self._ws_clients)} total)")

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected WebSocket clients."""
        if not self._ws_clients:
            return
        text = json.dumps(message)
        disconnected = []
        for ws in self._ws_clients:
            try:
                await ws.send_text(text)
            except Exception:  # WebSocket send can raise any connection-related error
                disconnected.append(ws)
        for ws in disconnected:
            self._ws_clients.discard(ws)

    async def _on_pending_settings_applied(
        self, event: str, payload: dict[str, Any]
    ) -> None:
        """Persist project file after pending device settings are applied."""
        device_id = payload.get("device_id", "")
        if not self.project or not device_id:
            return

        # Update the project config to clear applied pending settings
        for dev in self.project.devices:
            if dev.id == device_id:
                # Sync from device_manager's config (which was updated in-place)
                dm_config = self.devices._device_configs.get(device_id, {})
                remaining = dm_config.get("pending_settings", {})
                dev.pending_settings = remaining
                break

        save_project(self.project_path, self.project)
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
        """
        self._state_batch[key] = new_value

    async def _flush_state_batch_loop(self) -> None:
        """Periodically flush batched state changes to WebSocket clients."""
        try:
            while self._running:
                await asyncio.sleep(0.05)  # 50ms = max 20 updates/sec
                if not self._state_batch:
                    continue
                # Swap out the batch atomically — even though _on_state_change
                # is sync and can't interleave with us between awaits, this
                # pattern is safe if callers ever change.
                batch = self._state_batch
                self._state_batch = {}
                await self.broadcast_ws({
                    "type": "state.update",
                    "changes": batch,
                })
        except asyncio.CancelledError:
            pass
        finally:
            # Flush any remaining batched state before exiting
            if self._state_batch:
                batch = self._state_batch
                self._state_batch = {}
                try:
                    await self.broadcast_ws({
                        "type": "state.update",
                        "changes": batch,
                    })
                except Exception:  # Best-effort flush during shutdown; errors are non-critical
                    pass

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
                    create_backup(self.project_path.parent, "Auto-backup")
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
        if not self.project or not config.ISC_ENABLED or not self.project.isc.enabled:
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
        isc_should_run = config.ISC_ENABLED and self.project.isc.enabled

        if self.isc and isc_should_run:
            # Hot-reload config
            await self.isc.reload(
                shared_state_patterns=self.project.isc.shared_state,
                auth_key=self.project.isc.auth_key,
                manual_peers=self.project.isc.peers,
            )
        elif self.isc and not isc_should_run:
            # ISC was running but project disabled it
            await self.isc.stop()
            self.isc = None
            from server.api.isc_ws import set_isc_manager
            set_isc_manager(None)
        elif not self.isc and isc_should_run:
            # ISC was off but project enabled it
            await self._start_isc()

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

    def get_status(self) -> dict[str, Any]:
        """Return system status info."""
        uptime = time.time() - self._start_time if self._start_time else 0
        # Detect network address for panel access URLs
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = ""

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
                len(self.scripts._event_handler_ids) + len(self.scripts._state_sub_ids)
                if self.scripts
                else 0
            ),
            "ws_clients": len(self._ws_clients),
            "isc_enabled": self.isc is not None,
            "cloud_enabled": self.cloud_agent is not None,
            "hostname": hostname,
            "local_ip": local_ip,
            "http_port": config.HTTP_PORT,
            "bind_address": config.BIND_ADDRESS,
        }
        if self.isc:
            status["isc_peers"] = sum(
                1 for p in self.isc._peers.values() if p.connected
            )
        if self.cloud_agent:
            status["cloud_connected"] = self.cloud_agent._connected
        return status
