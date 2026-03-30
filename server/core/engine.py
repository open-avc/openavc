"""
OpenAVC Engine — the main runtime orchestrator.

Wires together StateStore, EventBus, DeviceManager, MacroEngine, and
the WebSocket push system. Manages the full system lifecycle:
start, stop, and hot-reload.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from server import config
from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.plugin_loader import PluginLoader
from server.core.project_loader import ProjectConfig, load_project, save_project
from server.core.scheduler import Scheduler
from server.core.script_engine import ScriptEngine
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
        self.scheduler = Scheduler(self.events)
        self.triggers = TriggerEngine(self.state, self.events, self.macros)
        self.scripts: ScriptEngine | None = None
        self.plugin_loader = PluginLoader(self.state, self.events, self.macros, self.devices)
        self.isc = None  # ISCManager, initialized in start() if enabled
        self.cloud_agent = None  # CloudAgent, initialized in start() if enabled
        self.update_manager = None  # UpdateManager, initialized in start()

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

        # Event/state subscription IDs (for cleanup on stop/reload)
        self._state_sub_ids: list[str] = []
        self._event_sub_ids: list[str] = []

        # Tracking
        self._start_time: float = 0
        self._running = False
        self._marker_confirm_task: asyncio.Task | None = None

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

        # Initialize user variables with defaults
        for var in self.project.variables:
            key = f"var.{var.id}"
            self.state.set(key, var.default, source="system")

        # Bind variable sources (auto-sync from device state)
        self._bind_variable_sources()

        # Load macros
        macros_data = [m.model_dump() for m in self.project.macros]
        self.macros.load_macros(macros_data)

        # Add and connect devices (merge connection table into device config)
        startup_errors: list[str] = []
        for device in self.project.devices:
            try:
                await self.devices.add_device(self._resolved_device_config(device))
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

        # Scheduler (cron jobs from project.avc)
        schedules_data = [s.model_dump() for s in self.project.schedules]
        self.scheduler.load_schedules(schedules_data)
        await self.scheduler.start()

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
        self._event_sub_ids.append(self.events.on("macro.error.*", self._on_macro_event))

        # Bridge trigger events to WebSocket
        self._event_sub_ids.append(self.events.on("trigger.fired", self._on_trigger_event))
        self._event_sub_ids.append(self.events.on("trigger.skipped", self._on_trigger_event))

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

        await self.events.emit("system.stopping")

        # Stop trigger engine
        await self.triggers.stop()

        # Stop scheduler
        await self.scheduler.stop()

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

        # Disconnect all devices
        await self.devices.disconnect_all()

        self.state.set("system.started", False, source="system")
        log.info("Engine stopped")

    async def reload_project(self) -> None:
        """Hot-reload project.avc without full restart."""
        log.info("Reloading project...")
        self.project = load_project(self.project_path)

        # Sync devices: remove deleted, add new, update changed
        await self._sync_devices()

        # Sync plugins: add new, remove deleted, restart changed
        await self._sync_plugins()

        # Reload macros
        macros_data = [m.model_dump() for m in self.project.macros]
        self.macros.load_macros(macros_data)

        # Reload triggers
        await self.triggers.stop()
        macros_data_triggers = [m.model_dump() for m in self.project.macros]
        self.triggers.load_triggers(macros_data_triggers)
        await self.triggers.start()

        # Re-register UI bindings
        self._register_ui_bindings()

        # Re-bind variable sources
        self._bind_variable_sources()

        # Reload scripts
        if self.scripts:
            scripts_data = [s.model_dump() for s in self.project.scripts]
            self.scripts.reload_scripts(scripts_data)

        # Reload schedules
        await self.scheduler.stop()
        schedules_data = [s.model_dump() for s in self.project.schedules]
        self.scheduler.load_schedules(schedules_data)
        await self.scheduler.start()

        # Reload ISC config
        await self._reload_isc()

        # Push new UI definition to all connected panels
        await self._broadcast_ws({
            "type": "ui.definition",
            "ui": self.project.ui.model_dump(mode="json"),
        })

        # Notify Programmer IDE to refetch project data
        await self._broadcast_ws({"type": "project.reloaded"})

        await self.events.emit("system.project.reloaded")
        log.info("Project reloaded")

    def _resolved_device_config(self, device) -> dict:
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
            project_devices[d.id] = self._resolved_device_config(d)

        running_ids = set(self.devices._device_configs.keys())
        project_ids = set(project_devices.keys())

        # Remove devices no longer in project
        for device_id in running_ids - project_ids:
            await self.devices.remove_device(device_id)

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
                await self._broadcast_ws({
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

        # Look up the binding for this event type
        binding = bindings.get(event_type)

        # Toggle off: look for off_action nested inside press binding
        if not binding and event_type == "toggle_off":
            press_binding = bindings.get("press")
            if press_binding and isinstance(press_binding, dict):
                binding = press_binding.get("off_action")

        # Hold: look for hold_action nested inside press binding (tap_hold mode)
        if not binding and event_type == "hold":
            press_binding = bindings.get("press")
            if press_binding and isinstance(press_binding, dict):
                binding = press_binding.get("hold_action")

        if not binding:
            return

        # Dispatch based on action type
        await self._execute_binding(binding, data)

    async def _execute_binding(
        self, binding: dict[str, Any], data: dict[str, Any]
    ) -> None:
        """Execute a UI binding action."""
        action = binding.get("action", "")

        if action == "value_map":
            # Per-option action map (used by select elements).
            # Look up the element's current value and execute that action.
            element_value = str(data.get("value", ""))
            action_map = binding.get("map", {})
            mapped_action = action_map.get(element_value)
            if mapped_action:
                await self._execute_binding(mapped_action, data)

        elif action == "macro":
            macro_id = binding.get("macro", "")
            if macro_id:
                # Run macro in background so UI doesn't block
                task = asyncio.create_task(self.macros.execute(macro_id))
                task.add_done_callback(_log_task_exception)

        elif action == "device.command":
            device_id = binding.get("device", "")
            command = binding.get("command", "")
            params = dict(binding.get("params", {}))
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
            key = binding.get("key", "")
            # Support "value_from": "element" to use the element's current value
            if binding.get("value_from") == "element":
                value = data.get("value")
            else:
                value = binding.get("value")
            self.state.set(key, value, source="ui")

        elif action in ("page", "navigate"):
            # Page navigation — broadcast to all panels so they can switch
            page_id = binding.get("page", "")
            if page_id:
                await self.events.emit(f"ui.page.{page_id}")
                await self._broadcast_ws({
                    "type": "ui.navigate",
                    "page_id": page_id,
                })

        elif action == "script.call":
            func_name = binding.get("function", "")
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
        from server.config import BASE_DIR
        from server.drivers.driver_loader import load_all_drivers

        driver_repo = BASE_DIR / "driver_repo"
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

    async def _broadcast_ws(self, message: dict[str, Any]) -> None:
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
        await self._broadcast_ws({"type": "script.error", **payload})

    async def _on_trigger_event(self, event: str, payload: dict[str, Any]) -> None:
        """Forward trigger events to WebSocket clients."""
        await self._broadcast_ws({"type": event, **payload})

    async def _on_macro_event(self, event: str, payload: dict[str, Any]) -> None:
        """Forward macro lifecycle events to WebSocket clients."""
        # event is like "macro.progress.system_on" -> extract "progress"
        parts = event.split(".")
        event_type = parts[1] if len(parts) >= 2 else "unknown"
        await self._broadcast_ws({
            "type": f"macro.{event_type}",
            **payload,
        })

    async def _on_plugin_event(self, event: str, payload: dict[str, Any]) -> None:
        """Forward plugin lifecycle events to WebSocket clients."""
        await self._broadcast_ws({"type": event, **(payload or {})})

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
                await self._broadcast_ws({
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
                    await self._broadcast_ws({
                        "type": "state.update",
                        "changes": batch,
                    })
                except Exception:  # Best-effort flush during shutdown; errors are non-critical
                    pass

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
            )
            self.cloud_agent.set_command_handler(handler)

            from server.cloud.ai_tool_handler import AIToolHandler
            ai_tool_handler = AIToolHandler(
                self.cloud_agent, self.devices, self.events,
                reload_fn=self.reload_project,
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
        }
        if self.isc:
            status["isc_peers"] = sum(
                1 for p in self.isc._peers.values() if p.connected
            )
        if self.cloud_agent:
            status["cloud_connected"] = self.cloud_agent._connected
        return status
