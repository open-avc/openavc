"""
OpenAVC Engine — the main runtime orchestrator.

Wires together StateStore, EventBus, DeviceManager, MacroEngine, and
the WebSocket push system. Manages the full system lifecycle:
start, stop, and hot-reload.

Runtimes it owns but does not implement, each in its own module:
``ws_hub`` (client fan-out + batched state push), ``ui_events`` (what a
panel interaction does), and ``device_config`` (what a device dials).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import time
from pathlib import Path
from typing import Any

from openavc import config, runtime_flags
from openavc.core.device_config import bridge_first, resolve_device_config
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.help_requests import HelpRequests
from openavc.core.macro_engine import MacroEngine
from openavc.core.plugin_loader import PluginLoader
from openavc.core.project_diff import ProjectDiff, ProjectOrigin
from openavc.core.project_loader import (
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
    save_project_async,
)
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_persister import StatePersister
from openavc.core.state_store import StateStore, coerce_flat_primitive
from openavc.core.trigger_engine import TriggerEngine
from openavc.core.ui_events import UIEventRuntime
from openavc.core.ws_hub import WSHub
from openavc.discovery import network_scanner
from openavc.ui.matrix_model import resolve_ui
from openavc.utils.logger import get_logger
from openavc.version import __version__

log = get_logger(__name__)


class ProjectRevisionConflictError(Exception):
    """A revision-checked save found a newer project revision on the server."""


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback to log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(f"Background task {task.get_name()!r} failed: {exc}", exc_info=exc)


# Glob metacharacters that turn a single-key subscription into a multi-key
# fan-in (see StateStore pattern grammar). A variable source_key must be one
# concrete key, never a pattern.
_GLOB_METACHARS = "*?["


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
        # WebSocket fan-out + batched state push (see ws_hub); broadcast_ws
        # below is the engine-wide door to it.
        self.ws = WSHub(self.state)
        # Panel interaction runtime (peer of the macro engine, see ui_events)
        self.ui_events = UIEventRuntime(self)
        self.devices = DeviceManager(self.state, self.events)
        # "Ask for help" — raised by a macro step, never a stock control.
        self.help = HelpRequests(self)
        self.macros = MacroEngine(
            self.state, self.events, self.devices,
            broadcast_ws=self.broadcast_ws, help_requests=self.help,
        )
        self.triggers = TriggerEngine(self.state, self.events, self.macros)
        self.scripts: ScriptEngine | None = None
        self.plugin_loader = PluginLoader(
            self.state, self.events, self.macros, self.devices,
            cloud_agent_provider=lambda: self.cloud_agent,
        )
        self.persister: StatePersister | None = None
        self.isc = None  # ISCManager, initialized in start() if enabled
        self.cloud_agent = None  # CloudAgent, initialized in start() if enabled
        self.update_manager = None  # UpdateManager, initialized in start()
        self.mdns_advertiser = None  # MDNSAdvertiser, initialized in start() if enabled

        # Setup-action runner (driver-declared provisioning wizards)
        from openavc.core.setup_actions import SetupActionRunner
        self.setup_actions = SetupActionRunner(self)

        # Simulation
        from openavc.core.simulation import SimulationManager
        self.simulation = SimulationManager(self)

        # Wire StateStore -> EventBus
        self.state.set_event_bus(self.events)

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

        # Deferred bookkeeping persists (see persist_bookkeeping_change):
        # queued (mutate, on_error) pairs and the task that flushes them
        # outside the reconcile lock.
        self._bookkeeping_queue: list[tuple[Any, Any]] = []
        self._bookkeeping_task: asyncio.Task | None = None

        # Tracking
        self._start_time: float = 0
        self._running = False
        self._marker_confirm_task: asyncio.Task | None = None

        # Periodic backup
        self._periodic_backup_task: asyncio.Task | None = None
        self._dirty_since_backup: bool = False
        self._last_backup_time: float = 0

        # Cached (local_ip, hostname, all_ips) for get_status. Detection does
        # blocking adapter-enumeration / gethostname syscalls; they rarely
        # change, so compute once.
        self._network_info: tuple[str, str, list[str]] | None = None

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

        This is a deliberately separate inline sequence from
        :meth:`_reconcile` — it also builds one-time infrastructure (state
        persister, script engine install, plugin scan, permanent
        subscriptions, ISC/cloud/mDNS startup) that a reconcile never
        touches. The subsystem ORDERING constraints are shared, though:
        macros load before triggers, scripts load after variables and
        devices. A change to the reconcile ordering must be checked against
        this sequence too, and vice versa.
        """
        log.info("Engine starting...")
        self._start_time = time.time()

        # Ensure system.json exists in data directory
        from openavc.system_config import (
            get_system_config,
            migrate_legacy_project_dir,
            migrate_legacy_repos,
        )
        sys_config = get_system_config()
        sys_config.ensure_file()

        # Convert an admin password or API key stored as typed (an install
        # predating the hashing, or a hand-provisioned system.json) into a
        # digest. Before the plugin and script engines below on purpose: those
        # run in-process as the service user, so until this has happened,
        # installing a community plugin means handing it the admin password —
        # and on an appliance that is the OS login too — or the API key, which
        # is admin access on its own. No-op once converted, and neither
        # credential changes: the same password and the same key still
        # authenticate afterwards. Separately, so a failure to convert one
        # cannot leave the other as typed.
        try:
            sys_config.migrate_admin_password()
        except Exception:  # never block startup on the conversion
            log.exception("Could not convert the stored admin password to a hash")
        try:
            sys_config.migrate_api_key()
        except Exception:  # never block startup on the conversion
            log.exception("Could not convert the stored API key to a hash")

        # An API key with no password beside it leaves this box unopenable in
        # any browser. PATCH /api/system/config refuses to create that state,
        # but OPENAVC_API_KEY and a hand-provisioned system.json both reach it
        # without a save, so those get a line in the log instead.
        try:
            from openavc.api.auth import warn_if_api_key_is_sole_credential
            warn_if_api_key_is_sole_credential()
        except Exception:  # never block startup on a warning
            log.exception("Could not check the admin credential posture")

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

        # Register every driver on disk — built-in definitions and anything
        # installed into driver_repo/. Has to happen here: after the repo
        # migration above (so drivers left in the old layout are seen on this
        # same startup) and before the project loads, since resolving a
        # device's config and reporting its driver dependencies both ask the
        # registry what exists.
        from openavc.drivers.driver_loader import load_builtin_drivers
        load_builtin_drivers()

        # Set system state keys
        from openavc.updater.platform import detect_deployment_type
        self.state.set("system.version", __version__, source="system")
        self.state.set("system.update_available", "", source="system")
        self.state.set("system.update_channel", sys_config.get("updates", "channel", "stable"), source="system")
        self.state.set("system.update_status", "idle", source="system")
        self.state.set("system.update_progress", 0, source="system")
        self.state.set("system.update_error", "", source="system")
        self.state.set("system.deployment_type", detect_deployment_type().value, source="system")
        # Publish the idle help state so a panel bound to it draws something on
        # a quiet day rather than a blank that reads as a broken binding.
        self.help.declare_keys()

        # Load project — with corruption recovery
        self.project = self._load_project_safe()
        self.state.set("system.project_name", self.project.project.name, source="system")

        # Publish project asset catalog so plugins (e.g. audio_player) can
        # subscribe to project.assets and pick up uploaded files.
        from openavc.api.assets import publish_assets_state
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
        # Bridges first, then their dependents (see bridge_first).
        for batch in bridge_first(list(resolved), resolved):
            batch_results = await asyncio.gather(
                *(self.devices.add_device(resolved[did]) for did in batch),
                return_exceptions=True,
            )
            for did, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    startup_errors.append(f"Device '{did}': {result}")
                    log.error(f"Failed to add device '{did}': {result}")

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
            self.state.subscribe("*", self.ws.on_state_change)
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
        from openavc.cloud.config import apply_saved_cloud_config
        apply_saved_cloud_config()
        await self._start_cloud_agent()

        # Start the batch flush task
        self.ws.start_state_flush()

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
            from openavc.updater.manager import UpdateManager
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
        """Call the update good after 60 seconds of stable running.

        The engine owns only the timer; what the marker means and how it is
        cleared belongs to the updater (``updater.rollback.confirm_startup``).
        """
        try:
            await asyncio.sleep(60)
            from openavc.system_config import get_system_config
            from openavc.updater.rollback import confirm_startup
            confirm_startup(get_system_config().data_dir)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop the engine gracefully."""
        log.info("Engine stopping...")
        self._running = False
        # Drain any queued bookkeeping persist before teardown so a
        # just-applied pending-settings clear or plugin-config save isn't
        # lost when the loop closes.
        task = self._bookkeeping_task
        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5)
            except Exception:
                log.warning("Bookkeeping persist did not finish before shutdown")
        # Serialize teardown against an in-flight reload_project (reachable
        # from REST, the cloud command handler, and AI tools). Tearing
        # subsystems down while a hot-reload runs interleaves trigger and
        # subscription start/stop. Wait for any reload to finish first.
        async with self._reload_lock:
            await self._stop_inner()
        # A bookkeeping write scheduled DURING teardown (e.g. a plugin
        # on_stop calling save_config) spawned a flush task that queued
        # behind the lock held above. Drain it now that the lock is free so
        # the write persists before the process exits.
        task = self._bookkeeping_task
        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5)
            except Exception:
                log.warning("Bookkeeping persist did not finish after shutdown")

    async def _stop_inner(self) -> None:
        """Tear down all subsystems. Caller holds _reload_lock."""
        # Cancel startup confirmation timer
        if self._marker_confirm_task and not self._marker_confirm_task.done():
            self._marker_confirm_task.cancel()
            try:
                await self._marker_confirm_task
            except asyncio.CancelledError:
                pass

        # This is a deliberate shutdown, not a crash: zero the pending-update
        # attempt counter so a restart inside the 60s confirmation window
        # doesn't read as a failed startup and roll back a good update. A
        # crashed process never gets here, so crash detection still works.
        try:
            from openavc.system_config import get_system_config
            from openavc.updater.rollback import reset_marker_attempts
            reset_marker_attempts(get_system_config().data_dir)
        except Exception:
            log.exception("Could not reset pending-update attempts on shutdown")

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

        # Stop batch task (its unwind flushes what is still batched)
        await self.ws.stop_state_flush()

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

        # Cancel in-flight macros (with the engine's grace drain) before the
        # devices they command are torn down — a macro racing disconnect_all
        # would error its remaining steps into a half-stopped engine. Late on
        # purpose: every subsystem that can fire a macro (triggers, scripts,
        # plugins) is already stopped, so nothing restarts one after this.
        await self.macros.cancel_all()

        # Disconnect all devices
        await self.devices.disconnect_all()

        # Drain any in-flight state.changed EventBus emissions so they aren't
        # silently dropped mid-shutdown.
        await self.state.flush_pending_events()

        self.state.set("system.started", False, source="system")
        log.info("Engine stopped")

    async def reload_project(self) -> None:
        """Hot-reload project.avc from disk (LOAD origin — full reconcile).

        The disk read happens under the reload lock so it always sees the
        latest persisted bytes, never a snapshot taken before a concurrent
        save finished.
        """
        async with self._reload_lock:
            new_project = load_project(self.project_path)
            await self._apply_project_locked(
                new_project, None, ProjectOrigin.LOAD, persist=False
            )

    async def apply_project(
        self,
        new_project: ProjectConfig,
        *,
        expected_revision: int | None = None,
        origin: ProjectOrigin = ProjectOrigin.EDIT,
        persist: bool = True,
    ) -> int:
        """The one way a project change enters the engine.

        Checks optimistic concurrency (``expected_revision=None`` skips the
        check), persists the bytes, swaps the in-memory project, bumps the
        revision, and reconciles only the subsystems the change actually
        touches. Returns the new revision.

        The revision compare must happen under the same lock that increments
        ``_project_revision``. Checked outside it, two concurrent saves can
        both pass the compare and the loser's edit is silently overwritten —
        the exact race the 409 contract exists to prevent. Raises
        :class:`ProjectRevisionConflictError` on mismatch.
        """
        async with self._reload_lock:
            return await self._apply_project_locked(
                new_project, expected_revision, origin, persist
            )

    async def apply_project_edit(self, mutate) -> int:
        """Apply an edit built from the current project, atomically.

        Callers that copy ``engine.project``, mutate the copy, and then call
        :meth:`apply_project` have a stale-copy window: a commit that lands
        between the copy and the lock acquisition is silently reverted when
        the stale copy wins the serialization. Here the copy is taken under
        the same lock that applies it, so there is nothing to go stale.

        ``mutate(project)`` receives a deep copy of the current project;
        project reads (find-by-id, 404 checks) belong inside it, since the
        project it sees may differ from what the caller inspected before the
        lock was acquired. An exception from ``mutate`` aborts the edit with
        nothing applied. Returns the new revision.
        """
        async with self._reload_lock:
            new_project = self.project.model_copy(deep=True)
            mutate(new_project)
            return await self._apply_project_locked(
                new_project, None, ProjectOrigin.EDIT, persist=True
            )

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

    async def _apply_project_locked(
        self,
        new_project: ProjectConfig,
        expected_revision: int | None,
        origin: ProjectOrigin,
        persist: bool,
    ) -> int:
        """Body of :meth:`apply_project`. Caller holds ``_reload_lock``."""
        if (
            expected_revision is not None
            and expected_revision != self._project_revision
        ):
            raise ProjectRevisionConflictError(
                "Project was modified by another session"
            )

        if persist:
            # save_project stamps the derived dependency lists onto the
            # object in place, so after this await new_project matches the
            # bytes on disk exactly — no re-read or re-validation needed.
            await save_project_async(self.project_path, new_project)

        if origin is ProjectOrigin.LOAD:
            diff = ProjectDiff.all_dirty()
        else:
            diff = ProjectDiff.compute(self.project, new_project)

        # Snapshot for rollback on reconcile failure
        prev_project = self.project
        prev_revision = self._project_revision
        prev_dirty = self._dirty_since_backup

        # Swapping self.project IS the UI reload: UI dispatch looks elements
        # up at event time, so a UI-only change needs nothing beyond this
        # swap and the ui.definition broadcast below.
        self.project = new_project
        self._project_revision += 1
        self._dirty_since_backup = True

        try:
            await self._reconcile(diff, origin)
        except Exception:
            log.error("Project reconcile failed, rolling back to previous "
                      "project state", exc_info=True)
            self.project = prev_project
            self._project_revision = prev_revision
            self._dirty_since_backup = prev_dirty

            # The new bytes stay on disk deliberately: the file is the source
            # of truth and the user's edit must survive a runtime hiccup.
            # Rollback restores the RUNTIME to the previous project.
            try:
                await self._rollback_reconcile(diff)
            except Exception:
                log.error("Rollback re-sync also failed", exc_info=True)

            raise

        # Clean apply succeeded — zero the startup-error count so a stale
        # count from the initial start (now fixed) doesn't linger in the store
        # and cloud relay.
        self.state.set("system.startup_errors", 0, source="system")
        if diff.project_meta:
            self.state.set("system.project_name", self.project.project.name,
                           source="system")
            if self.mdns_advertiser:
                self.mdns_advertiser.update_name(self.project.project.name)

        # Push the new UI definition to connected panels — only when the UI
        # actually changed (panels re-render on every ui.definition).
        if diff.ui:
            # The bindings are new, so an action the runtime could not run
            # before is worth saying again -- somebody editing a project is
            # trying to fix something, and silence reads as having fixed it.
            self.ui_events.forget_undispatched_actions()
            await self.broadcast_ws({
                "type": "ui.definition",
                "ui": self.panel_ui(),
            })

        # Notify the Programmer IDE to refetch project data. Always sent:
        # external editors need to learn the new revision even for changes
        # they can't see in the UI definition.
        await self.broadcast_ws({
            "type": "project.reloaded",
            "revision": self._project_revision,
        })

        await self.events.emit("system.project.reloaded")
        log.info(f"Project applied (origin={origin.value}, "
                 f"revision={self._project_revision})")
        return self._project_revision

    async def _reconcile(self, diff: ProjectDiff, origin: ProjectOrigin) -> None:
        """Reconcile the running subsystems to ``self.project``, touching only
        the sections ``diff`` marks dirty.

        The ordering is load-bearing: triggers stop before any state-key
        cleanup, macros load before triggers rebuild, scripts load after
        variables and devices (they subscribe to ``var.*`` / ``device.*``
        keys at import).
        """
        # LOAD only: pick up out-of-band driver files copied straight into
        # driver_repo/ on the filesystem. API-driven installs register their
        # driver themselves, so an edit-save never pays for a library rescan.
        if origin is ProjectOrigin.LOAD:
            self._load_project_drivers()

        # Stop triggers before any reconcile step that deletes state keys
        # (var.*, device.*, plugin.*) so a state_change trigger can't fire
        # on a key that is mid-cleanup. Macro changes land here too: the
        # trigger definitions live in the macros.
        triggers_stopped = False
        if diff.requires_trigger_rebuild:
            await self.triggers.stop()
            triggers_stopped = True

        # Swapping macro/group definitions under a running macro would
        # half-execute it against the new definition (nested macro and
        # group.command steps re-resolve mid-flight) — cancel only when
        # those definitions actually changed.
        if diff.macros or diff.device_groups:
            await self.macros.cancel_all()

        if diff.variables:
            # Seed new defaults, then clean up orphaned var.* keys
            project_var_ids = {v.id for v in self.project.variables}
            for var in self.project.variables:
                key = f"var.{var.id}"
                if self.state.get(key) is None:
                    self.state.set(key, var.default, source="system")
            all_var_keys = self.state.get_namespace("var.")
            orphaned_vars = [
                vid for vid in all_var_keys if vid not in project_var_ids
            ]
            for vid in orphaned_vars:
                self.state.delete(f"var.{vid}")
            if orphaned_vars:
                log.info(f"Cleaned up {len(orphaned_vars)} orphaned variable state key(s)")

            if self.persister:
                persistent_keys = {
                    f"var.{v.id}" for v in self.project.variables if v.persist
                }
                self.persister.update_keys(persistent_keys)

        if diff.devices or diff.connections:
            # Always the whole sync, never a per-device diff: a bridge edit
            # changes the resolved host/port of every device bound through
            # it, so cross-row effects only surface when every device's
            # resolved config is re-compared. _sync_devices diffs in memory
            # and is cheap.
            await self._sync_devices()

            # Promote any orphans whose driver is now in the registry.
            # Must follow both the driver load and the device sync.
            await self.devices.retry_all_orphans()

            # _sync_devices replaces driver instances, so simulator
            # redirects must be re-applied.
            if self.simulation.active:
                await self.simulation.sync()

        if diff.plugins:
            await self._sync_plugins()

        if diff.macros:
            self.macros.load_macros([m.model_dump() for m in self.project.macros])
        if diff.device_groups:
            self.macros.load_groups(
                [g.model_dump() for g in self.project.device_groups]
            )

        if triggers_stopped:
            # The rebuild is loss-free: cooldown and cron-dedup baselines are
            # restored from the state store for surviving trigger ids.
            # Startup triggers re-fire only when a whole new project arrives;
            # an edit-save must not re-run power-on automation.
            self.triggers.load_triggers(
                [m.model_dump() for m in self.project.macros]
            )
            await self.triggers.start(
                fire_startup=(origin is ProjectOrigin.LOAD)
            )

        if diff.variables:
            self._bind_variable_sources()
            self._register_variable_validation()

        if diff.scripts and self.scripts:
            if origin is ProjectOrigin.LOAD:
                # A new project can replace script FILES while the configs
                # stay identical (library open, backup restore) — only a
                # full reload is safe here.
                self.scripts.reload_scripts(
                    [s.model_dump() for s in self.project.scripts]
                )
            else:
                # Per-script: unchanged scripts keep their handlers and
                # timer phase; a failed re-import keeps the old version.
                for script_id in diff.scripts_to_unload:
                    self.scripts.unload_script(script_id)
                for cfg in diff.scripts_to_reload:
                    result = self.scripts.reload_script(cfg)
                    if result.get("status") == "error":
                        log.error(
                            f"Script '{cfg.get('id')}' failed to reload: "
                            f"{result.get('error')}"
                        )

        if diff.isc:
            await self._reload_isc()

    async def _rollback_reconcile(self, diff: ProjectDiff) -> None:
        """Best-effort re-sync after a failed reconcile, scoped to the same
        sections the failed pass may have partially applied, against the
        restored ``self.project``.

        Deliberately narrower than the forward pass (unchanged from the
        pre-reconciler rollback): deleted ``var.*`` keys stay deleted,
        persister keys, scripts, ISC, and mDNS are not restored, and the new
        bytes stay on disk.
        """
        if diff.devices or diff.connections:
            await self._sync_devices()
        if diff.plugins:
            await self._sync_plugins()

        if diff.macros:
            self.macros.load_macros([m.model_dump() for m in self.project.macros])
        if diff.device_groups:
            self.macros.load_groups(
                [g.model_dump() for g in self.project.device_groups]
            )

        if diff.requires_trigger_rebuild:
            # The forward pass may have failed before or after restarting
            # triggers. stop() first: calling start() on already-started
            # triggers would stack a second set of state/event subscriptions
            # and every trigger would fire twice per change. Never re-fire
            # startup triggers while recovering — the restored project was
            # already running.
            await self.triggers.stop()
            self.triggers.load_triggers(
                [m.model_dump() for m in self.project.macros]
            )
            await self.triggers.start(fire_startup=False)

        if diff.variables:
            self._bind_variable_sources()
            self._register_variable_validation()

    def monitors(self) -> list[dict]:
        """The readings this project says matter, as plain dicts.

        One list, read by everything that needs it: the alert monitor compiles
        the ones carrying limits into rules, and the cloud manifest carries the
        whole set so the health card can draw a tile. Handed out as dicts rather
        than models so a consumer never holds a live reference into the project
        that is about to be replaced.
        """
        if not self.project:
            return []
        return [m.model_dump(mode="json") for m in self.project.monitors]

    def resolved_device_config(self, device) -> dict:
        """The config this device actually dials — driver defaults, project
        config, and the connection table layered, with bridge/USB bindings
        resolved. See ``core.device_config``."""
        return resolve_device_config(device, self.project)

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
            for batch in bridge_first(new_device_ids, project_devices):
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
                # Same path as every other config write (REST, cloud AI):
                # try the plugin's on_config_changed hot-apply hook first,
                # fall back to a restart.
                outcome = await self.plugin_loader.restart_or_apply(
                    plugin_id, new_config
                )
                if outcome == "start_failed":
                    log.error(
                        f"Plugin '{plugin_id}' failed to restart after a "
                        f"config change and is stopped"
                    )

    async def persist_bookkeeping_change(self, mutate, *, on_error=None) -> int:
        """Persist an in-place project mutation whose runtime effect is
        already applied — no reconcile, just save + revision bump +
        ``project.reloaded`` broadcast (so an open IDE's stale ETag gets a
        409 instead of silently reverting the change).

        ``mutate(project)`` is applied to the current project under the
        reconcile lock. On a save failure ``on_error`` runs (to revert the
        mutation) and the exception propagates.

        MUST NOT be called from code that can run while the reconcile lock
        is held — event handlers fired during a reconcile (device connect,
        plugin hooks, state changes) — that would deadlock. Those callers
        use :meth:`schedule_bookkeeping_change` instead.
        """
        async with self._reload_lock:
            try:
                mutate(self.project)
                await save_project_async(self.project_path, self.project)
            except Exception:
                if on_error is not None:
                    result = on_error()
                    if asyncio.iscoroutine(result):
                        await result
                raise
            self._project_revision += 1
            revision = self._project_revision
        await self.broadcast_ws({
            "type": "project.reloaded",
            "revision": revision,
        })
        return revision

    def schedule_bookkeeping_change(self, mutate, *, on_error=None) -> None:
        """Queue a bookkeeping persist to run outside the reconcile lock.

        Safe to call from anywhere — including event handlers awaited inline
        while ``apply_project`` holds the reconcile lock (device connect
        inside ``_sync_devices``, plugin hooks inside ``_sync_plugins``),
        where awaiting the lock would deadlock the engine.

        Writes queued in the same tick coalesce into one save and one
        revision bump. Each ``mutate(project)`` is applied to whatever
        project object is current at flush time, so a project swap between
        schedule and flush can't resurrect stale state or drop the write.
        """
        self._bookkeeping_queue.append((mutate, on_error))
        if self._bookkeeping_task is None or self._bookkeeping_task.done():
            self._bookkeeping_task = asyncio.create_task(
                self._flush_bookkeeping()
            )

    async def _flush_bookkeeping(self) -> None:
        while self._bookkeeping_queue:
            revision = None
            async with self._reload_lock:
                batch = list(self._bookkeeping_queue)
                self._bookkeeping_queue.clear()
                if not self.project:
                    return
                try:
                    for mutate, _on_error in batch:
                        mutate(self.project)
                    await save_project_async(self.project_path, self.project)
                    self._project_revision += 1
                    revision = self._project_revision
                except Exception as e:
                    log.error(f"Deferred project persist failed: {e}")
                    for _mutate, on_error in batch:
                        if on_error is None:
                            continue
                        try:
                            result = on_error()
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            log.exception("Bookkeeping on_error callback failed")
            if revision is not None:
                await self.broadcast_ws({
                    "type": "project.reloaded",
                    "revision": revision,
                })

    async def _save_plugin_config(self, plugin_id: str, config: dict) -> None:
        """Save updated plugin config to the project file (callback for PluginAPI).

        The plugin already runs with ``config`` (PluginAPI swapped it before
        calling here), so this is a bookkeeping persist — no reconcile. A
        plugin can call ``save_config`` from a hook the reconciler awaits
        inline (``on_start``, ``on_config_changed``, an event handler), i.e.
        while the reconcile lock is held — taking the lock then would
        deadlock. When the lock is free this persists before returning and
        raises on failure (the PluginAPI contract); when it is held, the
        write is deferred to run right after the reconcile finishes.
        """
        if not self.project or plugin_id not in self.project.plugins:
            return
        previous = self.project.plugins[plugin_id].config

        def mutate(project) -> None:
            if plugin_id in project.plugins:
                project.plugins[plugin_id].config = config

        async def on_error() -> None:
            # Revert the in-memory project so it matches disk. Otherwise a
            # bad/unwritable config lingers in the shared project model and
            # the next save of ANYTHING re-serializes it and fails too.
            if plugin_id in self.project.plugins:
                self.project.plugins[plugin_id].config = previous
            await self.broadcast_ws({
                "type": "error",
                "message": f"Failed to save plugin config for '{plugin_id}'",
            })

        if self._reload_lock.locked():
            # Possibly our own call chain holds the lock (a plugin hook run
            # by the reconciler) — awaiting it would deadlock. The plugin's
            # live config is already current; only the persist is deferred.
            mutate(self.project)
            self.schedule_bookkeeping_change(mutate, on_error=on_error)
        else:
            try:
                await self.persist_bookkeeping_change(mutate, on_error=on_error)
            except Exception as e:
                log.error(f"Failed to save plugin config for '{plugin_id}': {e}")
                raise

    # --- UI Event Handling ---

    async def handle_ui_event(
        self, event_type: str, element_id: str, data: dict[str, Any] | None = None,
        *, dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Handle a UI event from a connected panel — the door every panel
        interaction comes through (WS handler, cloud UI tools). The binding
        runtime itself lives in ``core.ui_events``.

        Returns what the interaction dispatched, which the panel path ignores
        and ``simulate_ui_action`` reports. ``dry_run`` resolves the same
        actions and reports them without any of the effects — for a caller
        checking a control rather than operating one."""
        return await self.ui_events.handle(event_type, element_id, data, dry_run=dry_run)

    def panel_ui(self) -> dict[str, Any]:
        """The UI definition as a RENDERER must receive it, not as it is stored.

        The one difference is the matrix: a matrix's sources and destinations are
        authored as a terse generator and drawn as two lists, and that expansion
        happens here rather than in the renderer (matrix plan D6). The panel and
        the Builder canvas are the same renderer in two places, so a copy each
        would be two copies of the same translation, drifting.

        ``/api/project`` deliberately does NOT go through this: it is what the
        Builder edits and saves back, and resolving there would materialise
        somebody's `count: 128` into 128 rows on their next save.
        """
        if not self.project:
            return {}
        return resolve_ui(self.project.ui.model_dump(mode="json"))

    def _load_project_safe(self) -> ProjectConfig:
        """Load project.avc with corruption recovery.

        If the project file is missing, corrupted, or fails validation:
        1. Try restoring from the most recent backup
        2. If no backup works, create a minimal empty project so the server starts
        """
        from openavc.core.backup_manager import list_backups, restore_from_backup
        from openavc.system_config import get_seed_project_path

        project_dir = self.project_path.parent

        # Happy path — load normally
        try:
            return load_project(self.project_path)
        except FileNotFoundError:
            log.warning(f"Project file not found: {self.project_path}")
            # The configured project doesn't exist yet. This is the normal
            # first-boot state when install-time seeding didn't reach this path
            # — notably a Docker image run with a *bind-mounted* /data, which
            # shadows the seed cp'd into the image layer (only named volumes
            # inherit image content). Seed from the canonical bundled project so
            # every deployment boots the starter project instead of an empty
            # Recovery Project, independent of how /data was provided. A missing
            # file has no backups to restore, so this is the right first move.
            seed = get_seed_project_path()
            if seed is not None:
                try:
                    project_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(seed, self.project_path)
                    log.info(f"Seeded default project from {seed}")
                    return load_project(self.project_path)
                except Exception as e:
                    log.warning(f"Failed to seed project from {seed}: {e}")
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
        from openavc.drivers.driver_loader import load_all_drivers
        from openavc.system_config import DRIVER_REPO_DIR

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
            value, was_coerced = coerce_flat_primitive(mapped)
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

    # --- WebSocket Management ---

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        """Send a message to every connected WS client (panels, IDE).

        The engine-wide broadcast door — macros, triggers, discovery, and the
        REST routes all reach panels through here. Delivery, per-client
        queues, and namespace filtering live in ``core.ws_hub``.
        """
        await self.ws.broadcast(message)

    async def _on_pending_settings_applied(
        self, event: str, payload: dict[str, Any]
    ) -> None:
        """Persist the project after pending device settings are applied.

        Fires on device connect — which can be inside ``_sync_devices``
        while ``apply_project`` holds the reconcile lock (this handler is
        awaited inline by the EventBus). Always defers: the DeviceManager
        already cleared the applied settings from the live config, so only
        the persist (+ revision bump, so a stale IDE PUT can't silently
        restore the queue) is outstanding.
        """
        device_id = payload.get("device_id", "")
        if not self.project or not device_id:
            return

        remaining = payload.get("remaining", {})

        def mutate(project) -> None:
            for dev in project.devices:
                if dev.id == device_id:
                    dev.pending_settings = remaining
                    break

        self.schedule_bookkeeping_change(mutate)
        log.info(f"[{device_id}] Project persist queued after applying "
                 f"pending settings")

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
                    from openavc.core.backup_manager import create_backup
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
        from openavc.core.backup_manager import create_backup
        result = create_backup(self.project_path.parent, reason)
        if result:
            self._last_backup_time = time.time()
            self._dirty_since_backup = False

    # --- ISC helpers ---

    async def _start_isc(self) -> None:
        """Initialize ISC if enabled in both system config and project."""
        # Read the live system-config value rather than an import-time
        # constant, so a PATCH /system/config toggle is honored on
        # reload/reconcile without a restart.
        from openavc.system_config import get_system_config
        isc_enabled = bool(get_system_config().get("isc", "enabled", True))
        if not self.project or not isc_enabled or not self.project.isc.enabled:
            return
        try:
            from openavc.core.isc import ISCManager, get_or_create_instance_id
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
            from openavc.api.isc_ws import set_isc_manager
            set_isc_manager(self.isc)
            # Wire ISC into the script API
            from openavc.core.script_api import isc as isc_proxy
            isc_proxy._bind(self.isc)
        except Exception:  # Catch-all: isolates ISC subsystem errors from core startup
            log.exception("ISC: Failed to start — continuing without ISC")
            self.isc = None

    async def _reload_isc(self) -> None:
        """Reload ISC configuration after project change."""
        if not self.project:
            return
        from openavc.system_config import get_system_config
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
            from openavc.api.isc_ws import set_isc_manager
            set_isc_manager(None)
            # Unbind the script API proxy. Otherwise scripts calling
            # isc.send_to() / isc.broadcast() reach the stopped manager
            # and surface a misleading ConnectionError instead of the
            # intended "ISC not enabled" RuntimeError.
            from openavc.core.script_api import isc as isc_proxy
            isc_proxy._bind(None)
        elif not self.isc and isc_should_run:
            # ISC was off but project enabled it
            await self._start_isc()

    # --- mDNS Advertiser helpers ---

    async def _start_mdns_advertiser(self) -> None:
        """Start mDNS service advertisement if enabled in system config."""
        # Live system-config read (not an import-time constant) so a PATCH
        # /system/config toggle is honored on reconcile.
        from openavc.system_config import get_system_config
        if not get_system_config().get("discovery", "advertise", True):
            return
        try:
            from openavc.core.isc import get_or_create_instance_id
            from openavc.discovery.mdns_advertiser import MDNSAdvertiser

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
        from openavc.system_config import get_system_config
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
            from openavc.cloud.agent import CloudAgent
            from openavc.cloud.heartbeat import HeartbeatCollector
            from openavc.cloud.state_relay import StateRelay
            from openavc.cloud.command_handler import CommandHandler
            from openavc.cloud.alert_monitor import AlertMonitor
            from openavc.cloud.tunnel import TunnelHandler

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
                ws_client_count_fn=lambda: self.ws.client_count,
            )
            self.cloud_agent.set_heartbeat_collector(heartbeat)

            relay = StateRelay(self.cloud_agent, self.state)
            self.cloud_agent.set_state_relay(relay)

            handler = CommandHandler(
                self.cloud_agent, self.devices, self.events,
                reload_fn=self.reload_project,
                apply_fn=self.apply_project,
                project_path=self.project_path,
            )
            self.cloud_agent.set_command_handler(handler)

            from openavc.cloud.ai_tool_handler import AIToolHandler
            ai_tool_handler = AIToolHandler(
                self.cloud_agent, self.devices, self.events,
                project_path=self.project_path,
            )
            self.cloud_agent.set_ai_tool_handler(ai_tool_handler)

            alert_monitor = AlertMonitor(
                self.cloud_agent, self.state, self.events,
                monitors_provider=self.monitors,
            )
            self.cloud_agent.set_alert_monitor(alert_monitor)

            tunnel_handler = TunnelHandler(self.cloud_agent)
            self.cloud_agent.set_tunnel_handler(tunnel_handler)

            from openavc.cloud.cert_manager import CertificateManager
            from openavc.system_config import get_system_config
            cert_manager = CertificateManager(self.cloud_agent, get_system_config())
            self.cloud_agent.set_cert_manager(cert_manager)

            # So an acknowledgement lands on the panel that asked.
            self.cloud_agent.set_help_requests(self.help)

            # Connect (runs in background)
            await self.cloud_agent.connect()
            log.info("Cloud agent: initialized and connecting")
        except Exception:  # Catch-all: isolates cloud subsystem errors from core startup
            log.exception("Cloud agent: failed to start — continuing without cloud")
            self.cloud_agent = None

    # --- Status ---

    def _detect_network_info(self) -> tuple[str, str, list[str]]:
        """Return ``(local_ip, hostname, all_ips)``, cached after the first call.

        ``all_ips`` is every reachable IPv4 leg of this host, best guess first,
        and ``local_ip`` is that top pick — a multi-homed controller has no
        single right answer, so the setup screen shows the whole list. Both
        syscall paths block the event loop and the values rarely change over a
        process's lifetime, hence the cache.
        """
        if self._network_info is not None:
            return self._network_info
        all_ips = network_scanner.get_ranked_interface_ips()
        local_ip = all_ips[0] if all_ips else "127.0.0.1"
        try:
            hostname = socket.gethostname()
        except OSError:
            hostname = ""
        self._network_info = (local_ip, hostname, all_ips)
        return self._network_info

    def refresh_network_info(self) -> tuple[str, str, list[str]]:
        """Re-detect (and re-cache) the local addresses and hostname.

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
            "ws_clients": self.ws.client_count,
            "isc_enabled": self.isc is not None,
            "cloud_enabled": self.cloud_agent is not None,
            "http_port": config.HTTP_PORT,
            "port80_active": runtime_flags.port80_active,
        }
        if include_sensitive:
            local_ip, hostname, all_ips = self._detect_network_info()
            status["hostname"] = hostname
            status["local_ip"] = local_ip
            status["local_ips"] = all_ips
            status["bind_address"] = config.BIND_ADDRESS
        if self.isc:
            status["isc_peers"] = sum(
                1 for p in self.isc._peers.values() if p.connected
            )
        if self.cloud_agent:
            status["cloud_connected"] = self.cloud_agent._connected
        return status
