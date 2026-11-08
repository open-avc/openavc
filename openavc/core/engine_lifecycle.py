"""How the engine starts and stops, the part of Engine a restart runs:
``start``, ``stop`` and the teardown behind it, one method per stage.

The order is the contract, and it is written once in each direction, as the
list of stages in ``start`` and in ``_stop_inner``. A start settles the
system's own decisions and registers the drivers before the project loads
(resolving a device asks the registry), loads macros before triggers and
devices and variables before scripts (scripts subscribe to ``var.*`` /
``device.*`` keys at import), and starts the triggers only after
``system.started``, so startup triggers fire. A stop ends everything that
can fire a macro, then cancels the macros, then disconnects the devices they
command. ``_reconcile`` (core/engine.py) shares these orderings, so a change
to one is checked against the other.

A part of the start that fails is counted in ``system.startup_errors`` and
named in one warning; it never stops the rest of the start. Each step that
settles a decision or converts something on disk before the project loads
(``prepare_host``) is the same: logged, never fatal.

Mixed into Engine (core/engine.py). Logs under the engine's name.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from openavc.core.device_sync import add_devices
from openavc.core.project_loader import refresh_crash_copy, save_project_async
from openavc.core.script_engine import ScriptEngine
from openavc.utils.logger import get_logger
from openavc.version import __version__

log = get_logger("openavc.core.engine")


def _log_task_exception(task: asyncio.Task) -> None:
    """Done-callback to log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(f"Background task {task.get_name()!r} failed: {exc}", exc_info=exc)


def _in_background(coro) -> asyncio.Task:
    """Run ``coro`` as a task whose failure is logged rather than lost."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_log_task_exception)
    return task


async def _cancel_and_wait(task: asyncio.Task | None) -> bool:
    """Cancel ``task`` if it is still running and wait for it to end; True
    when it was. Only its cancellation is swallowed: anything else it raises
    on the way out reaches the caller."""
    if not task or task.done():
        return False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return True


# --- Before the project loads ------------------------------------------------
#
# Each step takes the system config and the project's folder. A step that
# fails is logged and the start goes on.


def _settle_panel_access(sys_config, project_dir: Path) -> None:
    # Decide Panel access for a system that has never had it written down,
    # BEFORE anything can save system.json on this release: a save writes the
    # shipped default into the file, and the question (was this system running
    # before approval existed?) can no longer be asked.
    # core/panel_devices.settle_access_default has the rule.
    from openavc.core.panel_devices import settle_access_default

    settle_access_default(sys_config, project_dir / ".instance_id")


def _settle_who_can_connect(sys_config, project_dir: Path) -> None:
    # Same for who can connect: the install type's default is written before
    # the first save can write an empty choice. main() has normally settled it
    # already, before binding; this covers a server started some other way.
    # The rule is openavc/listen.py.
    from openavc import listen

    listen.current()


def _save_who_can_connect(sys_config, project_dir: Path) -> None:
    # And write it down, so an earlier release rolled back onto this data
    # reads the current mirror (openavc/listen.py, "Rollback"). After the Panel
    # access decision above, which a save would otherwise pre-empt.
    from openavc import listen

    listen.save_settled(sys_config)


def _apply_login_reset(sys_config, project_dir: Path) -> None:
    # A forgotten Programmer password: a reset file in the data folder clears
    # the login, so this start comes up on the setup screen.
    # openavc/api/auth.py apply_reset_request.
    from openavc.api.auth import apply_reset_request

    apply_reset_request()


# Convert an admin password or API key stored as typed (an install predating
# the hashing, or a hand-provisioned system.json) into a digest. Before the
# plugin and script engines on purpose: those run in-process as the service
# user, so until this has happened, installing a community plugin means
# handing it the admin password — and on an appliance that is the OS login
# too — or the API key, which is admin access on its own. No-op once
# converted, and neither credential changes: the same password and the same
# key still authenticate afterwards. Two steps, so a failure to convert one
# cannot leave the other as typed.


def _hash_admin_password(sys_config, project_dir: Path) -> None:
    sys_config.migrate_admin_password()


def _hash_api_key(sys_config, project_dir: Path) -> None:
    sys_config.migrate_api_key()


def _warn_if_only_an_api_key(sys_config, project_dir: Path) -> None:
    # An API key with no password beside it leaves this box unopenable in any
    # browser. PATCH /api/system/config refuses to create that state, but
    # OPENAVC_API_KEY and a hand-provisioned system.json both reach it without
    # a save, so those get a line in the log instead.
    from openavc.api.auth import warn_if_api_key_is_sole_credential

    warn_if_api_key_is_sole_credential()


def _move_legacy_repos(sys_config, project_dir: Path) -> None:
    # One-shot migration of plugin_repo/driver_repo from the pre-data_dir
    # layout (APP_DIR/{plugin,driver}_repo). Runs before driver and plugin
    # loading so the moved content is picked up on the same startup. No-op
    # when the new locations already have content.
    from openavc.system_config import migrate_legacy_repos

    migrate_legacy_repos()


def _move_legacy_project_dir(sys_config, project_dir: Path) -> None:
    # Same one-shot treatment for the legacy default project location
    # (APP_DIR/projects). Must run before the project load, cloud config read,
    # and ISC instance-id read so they all see the moved directory. No-op when
    # OPENAVC_PROJECT or OPENAVC_DATA_DIR is set.
    from openavc.system_config import migrate_legacy_project_dir

    migrate_legacy_project_dir()


# Each with the line the log gets when it fails, in the order they run.
# system.json is made sure of between the two lists.
_BEFORE_SYSTEM_JSON = (
    (_settle_panel_access, "Could not settle the Panel access default"),
    (_settle_who_can_connect, "Could not settle who can connect"),
    (_save_who_can_connect, "Could not save who can connect"),
)
_AFTER_SYSTEM_JSON = (
    (_apply_login_reset, "Could not apply the Programmer login reset"),
    (_hash_admin_password, "Could not convert the stored admin password to a hash"),
    (_hash_api_key, "Could not convert the stored API key to a hash"),
    (_warn_if_only_an_api_key, "Could not check the admin credential posture"),
    (_move_legacy_repos, "migrate_legacy_repos failed"),
    (_move_legacy_project_dir, "migrate_legacy_project_dir failed"),
)


def _run_steps(steps, sys_config, project_dir: Path) -> None:
    for step, failure in steps:
        try:
            step(sys_config, project_dir)
        except Exception:  # never block startup on one of these
            log.exception(failure)


def prepare_host(project_dir: Path):
    """Everything a start settles before the project loads, in order;
    returns the system config.

    Ends by registering every driver on disk — built-in definitions and
    anything installed into driver_repo/. That has to happen here: after the
    repo migration (so drivers left in the old layout are seen on this same
    startup) and before the project loads, since resolving a device's config
    and reporting its driver dependencies both ask the registry what exists.
    """
    from openavc.system_config import get_system_config

    sys_config = get_system_config()
    _run_steps(_BEFORE_SYSTEM_JSON, sys_config, project_dir)
    sys_config.ensure_file()
    _run_steps(_AFTER_SYSTEM_JSON, sys_config, project_dir)
    from openavc.drivers.driver_loader import load_builtin_drivers

    load_builtin_drivers()
    return sys_config


class EngineLifecycleMixin:
    """``start``, ``stop`` and ``_stop_inner``, each a list of stages.

    From its host (Engine) it uses nearly everything: the subsystems built in
    ``Engine.__init__``, the project load and login moves, the device
    add/bring-up, the variable runtime (core/project_variables.py), the
    runtime services (ISC, mDNS, cloud, panel software) and the background
    loops.
    """

    async def start(self) -> None:
        """
        Start the engine:
        1. Start state batch push (first: clients can already be connected)
        2. Load project.avc
        3. Initialize user variables
        4. Connect all devices
        5. Load macros
        6. Register UI bindings
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
        self._push_state_to_clients()
        sys_config = prepare_host(self.project_path.parent)
        self._publish_system_keys(sys_config)
        await self._load_project_at_start()
        self._start_variables()
        self._load_macro_definitions(macros=True, groups=True)
        startup_errors: list[str] = []
        await self._add_devices_at_start(startup_errors)
        await self._start_plugins_at_start(startup_errors)
        self._start_scripts(startup_errors)
        self._load_triggers()
        self._bridge_events_to_ws()
        # Save a value a device reports into a config field that asks for it
        self.learned_config.start()
        await self._start_network_services()
        self._start_background_tasks()
        # Files staged for a command by an earlier run are gone with it.
        self.file_staging.start()
        self._running = True
        # Pre-warm the network-info cache off the event loop so the first
        # status/health poll never blocks on socket / gethostname syscalls.
        _in_background(asyncio.to_thread(self._detect_network_info))
        self._publish_startup_errors(startup_errors)
        self.state.set("system.started", True, source="system")
        await self.events.emit("system.started")
        # Start trigger engine after system.started (so startup triggers work)
        await self.triggers.start()
        await self._start_update_manager()
        # Panel software updates on appliance hardware, through the update manager
        await self._start_panel_software()
        log.info(
            f'Engine started — project "{self.project.project.name}" '
            f"({len(self.project.devices)} devices, "
            f"{len(self.project.macros)} macros)"
        )

    def _push_state_to_clients(self) -> None:
        # The WebSocket hub listens to the state store and pushes from here
        # on, before the first state write. /ws admits a client as soon as the
        # engine exists, not when start() returns: a Programmer tab or a panel
        # reconnecting into a restart is registered and snapshotted while the
        # project loads and devices come up, and is owed every change after
        # its snapshot (ws_hub.add_client). A write made before the hub
        # listens (the simulation resuming, a device's first connect) never
        # reaches a client that was already connected.
        self._state_sub_ids.append(
            self.state.subscribe("*", self.ws.on_state_change)
        )
        self.ws.start_state_flush()

    def _publish_system_keys(self, sys_config) -> None:
        from openavc.updater.platform import detect_deployment_type
        self.state.set("system.version", __version__, source="system")
        self.state.set("system.update_available", "", source="system")
        self.state.set("system.update_channel", sys_config.get("updates", "channel", "stable"), source="system")
        self.state.set("system.update_status", "idle", source="system")
        self.state.set("system.update_progress", 0, source="system")
        self.state.set("system.update_error", "", source="system")
        self.state.set("system.deployment_type", detect_deployment_type().value, source="system")
        # A zone the panel on this computer's screen reported before, applied
        # before the first schedule is read.
        from openavc.core import time_zone
        time_zone.restore()
        self.state.set("system.time_zone", time_zone.current_name(), source="system")
        # Publish the idle help state so a panel bound to it draws something on
        # a quiet day rather than a blank that reads as a broken binding.
        self.help.declare_keys()

    async def _load_project_at_start(self) -> None:
        """The project, with corruption recovery, composed with the product
        hardware, its logins moved out, saved if any of that changed it, and
        published (its recovery notice, settings, name and assets)."""
        loaded_project = self._load_project_safe()
        self.project = self.installed_equipment.compose(loaded_project, for_load=True)
        # A login the project still holds in the clear (every login, on the
        # first start of a version that keeps them in this system's login
        # file) moves into that file before anything dials, and the project
        # keeps the marker. This file may exist nowhere else (an update that
        # took no backup of its own, an installer run by hand), so it is
        # backed up first; a move that cannot finish changes nothing. A
        # plugin's logins move later in this start, once the plugins are
        # loaded and can say which settings hold one (_move_logins_at_start).
        self._logins_backed_up_at_start = False
        moved = self._move_logins(self.project, typed=False)
        logins_moved = moved is not self.project and await self._back_up_before_moving_logins()
        if logins_moved:
            self._logins_backed_up_at_start = True
            self.project = moved
        # Exports and backups read the saved project. Materialize a newly
        # inserted host reference before any hardware or programming starts.
        if self.project != loaded_project:
            await save_project_async(self.project_path, self.project)
            if logins_moved:
                await asyncio.to_thread(refresh_crash_copy, self.project_path)
        # Whatever that had to do to get a project, say so. Published from the
        # record on disk rather than from this boot, so a notice nobody has
        # dismissed survives the restart that would otherwise bury it.
        from openavc.core import project_recovery
        project_recovery.publish(self.state, self.project_path.parent)
        self._apply_project_settings()
        self.state.set("system.project_name", self.project.project.name, source="system")
        # Publish project asset catalog so plugins (e.g. audio_player) can
        # subscribe to project.assets and pick up uploaded files.
        from openavc.api.assets import publish_assets_state
        publish_assets_state(self)

    async def _add_devices_at_start(self, startup_errors: list[str]) -> None:
        """Add every project device, in parallel with the connection table
        merged into each one's config, connected unless a simulation is coming
        back; then the ports they push to."""
        self._sweep_device_logins()
        resolved = {
            d.id: self.resolved_device_config(d) for d in self.project.devices
        }
        # A simulation that was on when the server stopped comes back
        # (core/simulation.py). Its devices register undialed, and the
        # bring-up round below starts the simulator and redirects them before
        # the first connect, so none of them reaches the real equipment.
        resume_simulation = self.simulation.load_resume()

        def failed(device_id: str, error: Exception) -> None:
            startup_errors.append(f"Device '{device_id}': {error}")
            log.error(f"Failed to add device '{device_id}': {error}")

        await add_devices(
            self.devices, resolved, defer_connect=resume_simulation, on_failure=failed,
        )
        if resume_simulation:
            self._schedule_device_bringup()
            await self.wait_for_device_bringup()
        self._record_push_ports()

    async def _start_plugins_at_start(self, startup_errors: list[str]) -> None:
        """Scan the plugins, move their logins, start the project's."""
        try:
            self.plugin_loader.set_save_config_fn(self._save_plugin_config)
            self.plugin_loader.scan_plugins()
            # The plugins now say which of their settings are logins: those
            # move before any plugin starts, and the Project Library's copies
            # with every login they still hold.
            await self._move_logins_at_start()
            if self.project.plugins:
                plugins_dict = {
                    pid: pc.model_dump() if hasattr(pc, "model_dump") else pc
                    for pid, pc in self.project.plugins.items()
                }
                await self.plugin_loader.start_plugins(plugins_dict)
        except Exception as e:  # Catch-all: isolates plugin system errors from core startup
            startup_errors.append(f"Plugins: {e}")
            log.exception("Plugin system failed to start")

    def _start_scripts(self, startup_errors: list[str]) -> None:
        project_dir = self.project_path.parent
        self.scripts = ScriptEngine(self.state, self.events, self.devices, project_dir, self.macros)
        self.scripts.install()
        try:
            scripts_data = [s.model_dump() for s in self.project.scripts]
            self.scripts.load_scripts(scripts_data)
        except Exception as e:  # Catch-all: isolates script loading errors from core startup
            startup_errors.append(f"Scripts: {e}")
            log.exception("Script engine failed to load scripts")

    async def _start_network_services(self) -> None:
        """Inter-System Communication, the mDNS advertisement, then the cloud
        agent (its saved pairing applied first)."""
        await self._start_isc()
        await self._start_mdns_advertiser()
        from openavc.cloud.config import apply_saved_cloud_config
        apply_saved_cloud_config()
        await self._start_cloud_agent()

    def _start_background_tasks(self) -> None:
        # Periodic backup (every 30 min if project has changed)
        self._periodic_backup_task = _in_background(self._periodic_backup_loop())
        # Forget panel requests nobody answered (every minute), so a tablet
        # that walked away leaves the Programmer's list on its own.
        self._panel_sweep_task = _in_background(self._panel_devices_sweep_loop())
        # Appliance hardware installs the programs a transport runs (the SSH
        # client) when its system lacks them, then retries the devices that
        # were waiting. Returns at once on every other install type.
        from openavc.system.os_programs import ensure_programs_loop
        self._os_programs_task = _in_background(
            ensure_programs_loop(self._retry_client_missing_devices)
        )

    def _publish_startup_errors(self, startup_errors: list[str]) -> None:
        # Record startup error count for UI visibility. Always set it (0 when
        # clean) so a previous run's count can't linger in the store / cloud
        # relay after the project is fixed and reloaded.
        self.state.set("system.startup_errors", len(startup_errors), source="system")
        if startup_errors:
            log.warning(f"Engine started with {len(startup_errors)} error(s): {'; '.join(startup_errors)}")

    async def _start_update_manager(self) -> None:
        """Check for updates and schedule the auto-check; a cloud agent
        already running is handed the manager for cloud-triggered updates."""
        try:
            from openavc.updater.manager import UpdateManager
            self.update_manager = UpdateManager(state_store=self.state)
            await self.update_manager.start_auto_check()
            handler = getattr(self.cloud_agent, "_command_handler", None)
            if handler:
                handler._update_manager = self.update_manager
        except Exception:
            log.exception("Update manager failed to start — continuing without updates")
            self.update_manager = None

    async def stop(self) -> None:
        """Stop the engine gracefully."""
        log.info("Engine stopping...")
        self._running = False
        await self._stop_bringup()
        # Drain any queued bookkeeping persist before teardown so a
        # just-applied pending-settings clear or plugin-config save isn't
        # lost when the loop closes.
        await self._drain_bookkeeping("before")
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
        await self._drain_bookkeeping("after")

    async def _drain_bookkeeping(self, when: str) -> None:
        task = self._bookkeeping_task
        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5)
            except Exception:
                log.warning(f"Bookkeeping persist did not finish {when} shutdown")

    async def _stop_inner(self) -> None:
        """Tear down all subsystems. Caller holds _reload_lock."""
        if self.update_manager:
            await self.update_manager.stop_auto_check()
        await self._stop_panel_software()
        self.learned_config.stop()
        # Unsubscribe all event/state handlers to prevent leaks on reload
        self._drop_subscriptions()
        # Flush and stop state persister
        if self.persister:
            self.persister.stop()
        await self.events.emit("system.stopping")
        await self.triggers.stop()
        await self._stop_network_services()
        if self.scripts:
            self.scripts.unload_all()
        await self.plugin_loader.stop_all()
        # Stop batch task (its unwind flushes what is still batched)
        await self.ws.stop_state_flush()
        await self._stop_background_tasks()
        # Cancel in-flight macros (with the engine's grace drain) before the
        # devices they command are torn down — a macro racing disconnect_all
        # would error its remaining steps into a half-stopped engine. Late on
        # purpose: every subsystem that can fire a macro (triggers, scripts,
        # plugins) is already stopped, so nothing restarts one after this.
        await self.macros.cancel_all()
        # Stop file commands still running, then delete every staged file.
        await self.command_runs.stop()
        await self.file_staging.stop()
        await self.devices.disconnect_all()
        # Then end the simulator, without handing the devices back to the real
        # equipment and without forgetting that simulation is on: it comes
        # back when the server starts again.
        await self.simulation.shutdown()
        # Drain any in-flight state.changed EventBus emissions so they aren't
        # silently dropped mid-shutdown.
        await self.state.flush_pending_events()
        self.state.set("system.started", False, source="system")
        log.info("Engine stopped")

    def _drop_subscriptions(self) -> None:
        for sub_id in self._state_sub_ids:
            self.state.unsubscribe(sub_id)
        self._state_sub_ids.clear()
        for sub_id in self._event_sub_ids:
            self.events.off(sub_id)
        self._event_sub_ids.clear()

    async def _stop_network_services(self) -> None:
        """The cloud agent, the mDNS advertiser, then ISC."""
        if self.cloud_agent:
            await self.cloud_agent.stop()
            self.cloud_agent = None
        if self.mdns_advertiser:
            await self.mdns_advertiser.stop()
            self.mdns_advertiser = None
        if self.isc:
            await self.isc.stop()
            self.isc = None

    async def _stop_background_tasks(self) -> None:
        """The periodic backup, the panel sweep and the programs installer.
        The backup's slot keeps its ended task; the other two are emptied."""
        await _cancel_and_wait(self._periodic_backup_task)
        if await _cancel_and_wait(self._panel_sweep_task):
            self._panel_sweep_task = None
        await _cancel_and_wait(self._os_programs_task)
        self._os_programs_task = None
