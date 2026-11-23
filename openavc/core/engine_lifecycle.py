"""How the engine starts and stops, the part of Engine a restart runs:
``start``, ``stop`` and the teardown behind it, one method per stage, and
``shutdown``: THE stop for a process exit, which the lifespan and the restart
(openavc/restart.py) both run, so a stop finishes the same stages however it
was asked, within one deadline.

The order is the contract, and it is written once in each direction, as the
list of stages in ``start`` and in ``shutdown`` (the server's own pieces
around ``_stop_inner``'s). A start settles the
system's own decisions and registers the drivers before the project loads
(resolving a device asks the registry), loads macros before triggers and
devices and variables before scripts (scripts subscribe to ``var.*`` /
``device.*`` keys at import), and starts the triggers only after
``system.started``, so startup triggers fire. A stop ends everything that
can fire a macro, then cancels the macros, then disconnects the devices they
command. THE point where a stop saves the persistent variables is between
those last two, once nothing that can set one is left (a "System Stopping"
trigger's macro, a script's or a plugin's stop, a state callback either set
off: the run-up's last stage waits for those); what was set before the stop
is written at its start, before any stage can hang. ``_reconcile``
(core/project_reconcile.py) shares these orderings, so a change to one is
checked against the other.

Each stage of the start runs through one ``StartRun`` (core/start_stages.py)
and is one of two kinds. A stage the start goes on past when it fails is a
service the room's control runs without: the driver library's load (the
devices whose driver did not register wait for it), the plugins, the
scripts' load, the backup taken at the start, the settings devices fill in,
the network services (ISC, the mDNS advertisement, the cloud agent), the
periodic backup and clean-up, the files earlier commands left, the update
checks and panel software updates; ``openavc.main`` adds who can connect
(it feeds a warning and the banner alone), Discovery's driver data and the
address banner. Each is kept, by its stage and in words, in the run-state
record (middleware/startup_splash.py) that ``/api/startup-status`` answers.
Every other stage stops the start when it raises, and the record names it:
what the room's control needs (live updates to open screens, the system
settings and status, the project, its variables, macros and devices, the
script engine, the triggers, the macro updates panels follow,
``system.started``), since a room running without it does other than its
project says; and in ``openavc.main``, the update's own checks before and
after the engine (a stopped start is what rolls a failed update back) and
the restart door. A stopped start is retried and then held
on the splash page (openavc/start_retry.py), where a stage the start goes on
past lets the room run, and confirms an update it came with.
tests/test_start_stage_failures.py raises in each stage.

Each device that cannot be worked out or added is named on its own
(core/device_sync.py) and the others still start. A device, a plugin or a
script that does not run says so on its own surface until it is fixed (the
device manager's record, the plugin loader's status, the script engine's load
error), the same surface a project change's outcome reads. Every part that
failed is also named in one warning. Each step that settles a decision or
converts something on disk before the project loads (``prepare_host``) is
logged and never fatal, but not named. Each network service, the update
checks and panel software updates catch their own start failure, since a
settings change starts some of them again later (core/runtime_services.py),
and answer False: the start keeps that too, each network service under its
own stage (``_NETWORK_PARTS``), so the record says which one is missing. A
backup taken at the start that keeps its logins (``_seal_the_start_backup``)
raises into its stage.

A stage of the stop that raises or overruns is named in one warning too, and
the stages after it still run (core/stop_stages.py runs each on its own,
against the one deadline), so devices are disconnected and the simulator
ended whatever an earlier stage did.

Mixed into Engine (core/engine.py). Logs under the engine's name.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from openavc.core.device_sync import add_devices, work_out_each
from openavc.core.script_engine import ScriptEngine
from openavc.core.start_stages import GoOn, StartRun
from openavc.core.stop_stages import StopRun
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


def _cancel_now(task: asyncio.Task | None):
    """A stage that cancels ``task`` as it is called, before the stop yields,
    so a task that has not run yet never starts; the stage then waits for it
    to end."""
    if task is None or task.done():
        return None
    task.cancel()
    return _cancel_and_wait(task)


async def _stop_push_listeners() -> None:
    from openavc.listen_sockets import push_listeners

    await push_listeners.stop()


async def _close_simulator_proxy() -> None:
    from openavc.api import simulator_proxy

    await simulator_proxy.aclose_client()


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


def _make_backups_owner_only(sys_config, project_dir: Path) -> None:
    # The data folder's backups/ holds logins (a paired system's cloud key in
    # each pre-update zip, the Project Library's zip taken before its logins
    # moved); an earlier version wrote them under the umask, readable by
    # every account on the computer. Every install type, every start.
    from openavc.updater.backup import make_backups_private

    make_backups_private(Path(sys_config.data_dir))


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
    (_make_backups_owner_only, "Could not make the data folder's backups readable by this account only"),
)


def _run_steps(steps, sys_config, project_dir: Path) -> None:
    for step, failure in steps:
        try:
            step(sys_config, project_dir)
        except Exception:  # never block startup on one of these
            log.exception(failure)


def prepare_host(project_dir: Path):
    """Everything a start settles before the drivers and the project load,
    in order; returns the system config. The repo migration is here, so
    drivers left in the old layout are seen by the driver load that follows
    on this same start (``_load_drivers_at_start``)."""
    from openavc.system_config import get_system_config

    sys_config = get_system_config()
    _run_steps(_BEFORE_SYSTEM_JSON, sys_config, project_dir)
    sys_config.ensure_file()
    _run_steps(_AFTER_SYSTEM_JSON, sys_config, project_dir)
    return sys_config


# The stages a start goes on past when they fail, each with the sentence the
# run-state record keeps for it (core/start_stages.py).
_DRIVER_LIBRARY = GoOn(
    "drivers", "loading the driver library",
    "The driver library did not finish loading, so a device whose driver did "
    "not load is not running. Check the server log for the reason.",
)
_PLUGINS = GoOn(
    "plugins", "starting the plugins",
    "The plugins did not finish starting, so some may not be running. "
    "Check the server log for the reason.",
)
_SCRIPTS = GoOn(
    "scripts", "loading the scripts",
    "The scripts did not finish loading, so some may not be running. "
    "Check the server log for the reason.",
)
_START_BACKUP = GoOn(
    "start_backup", "saving the start-up backup",
    "The project backup taken at this start was not finished. "
    "Check the server log for the reason.",
)
_FILLED_IN_SETTINGS = GoOn(
    "filled_in_settings", "starting the settings devices report",
    "Settings that devices fill in, such as a model or a MAC address, are not "
    "being saved from the equipment. Check the server log for the reason.",
)
_NETWORK_SERVICES = GoOn(
    "network_services", "starting the network services",
    "The network services did not finish starting, so the cloud connection, "
    "the announcement panels find this server by, or Inter-System "
    "Communication may not be running. Check the server log for the reason.",
)
# The parts of the network services, kept apart when one catches its own
# failure (core/runtime_services.py), so the record says which one is missing.
_ISC = GoOn(
    "isc", "starting Inter-System Communication",
    "Inter-System Communication did not start, so this system is not sharing "
    "state or commands with other OpenAVC systems. Check the server log for "
    "the reason.",
)
_MDNS_ANNOUNCEMENT = GoOn(
    "mdns_announcement", "starting the network announcement",
    "This server is not announcing itself on the network, so panels and the "
    "panel app cannot find it by searching. Check the server log for the reason.",
)
_CLOUD_AGENT = GoOn(
    "cloud_agent", "connecting to OpenAVC Cloud",
    "The connection to OpenAVC Cloud did not start, so this system cannot be "
    "reached or managed from the cloud. Check the server log for the reason.",
)
_NETWORK_PARTS = {part.stage: part for part in (_ISC, _MDNS_ANNOUNCEMENT, _CLOUD_AGENT)}
_PERIODIC_TASKS = GoOn(
    "periodic_tasks", "starting the periodic backup and clean-up",
    "The half-hourly project backup and the clean-up of unanswered panel "
    "requests did not start. Check the server log for the reason.",
)
_STAGED_FILES = GoOn(
    "staged_files", "clearing files left by earlier commands",
    "Files left by commands from an earlier run were not cleared. "
    "Check the server log for the reason.",
)
_UPDATE_CHECKS = GoOn(
    "update_checks", "starting the update checks",
    "The update checks did not start, so this server does not look for "
    "updates. Check the server log for the reason.",
)
_PANEL_SOFTWARE = GoOn(
    "panel_software", "starting panel software updates",
    "Panel software updates did not start. Check the server log for the reason.",
)


class EngineLifecycleMixin:
    """``start``, ``shutdown``, ``stop`` and ``_stop_inner``, each a list of
    stages.

    From its host (Engine) it uses nearly everything: the subsystems built in
    ``Engine.__init__``, the project load and the login moves
    (core/project_logins.py), the device
    add/bring-up, the variable runtime (core/project_variables.py), the
    runtime services (core/runtime_services.py: ISC, mDNS, the cloud agent,
    the update manager and panel software, each started and stopped there)
    and the background loops.
    """

    _stopping = False  # stop() has begun; read through ``stopping``
    # The server's pieces a shutdown also ends (attach_server).
    server_start: asyncio.Task | None = None
    audit = None
    listen_watcher: asyncio.Task | None = None
    # The server's run-state record (middleware/startup_splash.py), which says
    # where the process is in its life; None for an engine with no server.
    run_state = None

    @property
    def stopping(self) -> bool:
        """True once the server is on its way down: its run-state record says
        stopping (said the moment a stop signal arrives, while uvicorn still
        drains connections, or a restart is accepted) or this engine's own
        stop has begun (a failed start's retry ends the process without
        saying it on the record, and an engine with no server has only its
        own). Something that would reach real equipment, or decide what
        happens to it, on the way down asks this; ``_running`` is no help, as
        it also reads False until a start has finished."""
        record = self.run_state
        return (record is not None and record.is_stopping) or self._stopping

    async def start(self, run: StartRun | None = None) -> None:
        """
        Start the engine, one stage at a time through ``run``
        (core/start_stages.py): the stages are the list below, in order, each
        either one the start goes on past (``go_on``, a ``GoOn`` above) or
        one that stops it (``run``); the module docstring says which and why.
        ``openavc.main`` passes the server's run, so the run-state record
        names the stage that stopped a start and keeps the ones it went on
        past, and names every failed part in one warning once its own stages
        after this have run; a start given no run makes its own and names
        them here.

        This is a deliberately separate inline sequence from
        :meth:`_reconcile` — it also builds one-time infrastructure (state
        persister, script engine install, plugin scan, permanent
        subscriptions, ISC/cloud/mDNS startup) that a reconcile never
        touches. The subsystem ORDERING constraints are shared, though:
        macros load before triggers, scripts load after variables and
        devices. A change to the reconcile ordering must be checked against
        this sequence too, and vice versa.
        """
        owns_run = run is None
        run = run or StartRun()
        log.info("Engine starting...")
        self._stopping = False
        self._start_time = time.monotonic()
        await run.run("starting live updates to open screens", self._push_state_to_clients)
        sys_config = await run.run(
            "preparing the system settings", lambda: prepare_host(self.project_path.parent),
        )
        await run.go_on(_DRIVER_LIBRARY, self._load_drivers_at_start)
        await run.run("setting up the system status", lambda: self._publish_system_keys(sys_config))
        await run.run("loading the project", self._load_project_at_start)
        await run.run("loading the variables", self._start_variables)
        await run.run(
            "loading the macros", lambda: self._load_macro_definitions(macros=True, groups=True),
        )
        await run.run("adding the devices", lambda: self._add_devices_at_start(run))
        await run.go_on(_PLUGINS, self._start_plugins_at_start)
        await run.go_on(_START_BACKUP, self._seal_the_start_backup)
        await run.run("starting the script engine", self._make_script_engine)
        await run.go_on(_SCRIPTS, self._load_scripts_at_start)
        await run.run("loading the triggers", self._load_triggers)
        await run.run("starting live macro and plugin updates", self._bridge_events_to_ws)
        # Save a value a device reports into a config field that asks for it
        await run.go_on(_FILLED_IN_SETTINGS, self.learned_config.start)
        for part in await run.go_on(_NETWORK_SERVICES, self._start_network_services) or ():
            run.kept(_NETWORK_PARTS[part])
        await run.go_on(_PERIODIC_TASKS, self._start_background_tasks)
        # Files staged for a command by an earlier run are gone with it.
        await run.go_on(_STAGED_FILES, self.file_staging.start)
        self._running = True
        # Pre-warm the network-info cache off the event loop so the first
        # status/health poll never blocks on socket / gethostname syscalls.
        _in_background(asyncio.to_thread(self._detect_network_info))
        await run.run("marking the system started", self._announce_started)
        # Start trigger engine after system.started (so startup triggers work)
        await run.run("starting the triggers", self.triggers.start)
        if await run.go_on(_UPDATE_CHECKS, self._start_update_manager) is False:
            run.kept(_UPDATE_CHECKS)
        # Panel software updates on appliance hardware, through the update manager
        if await run.go_on(_PANEL_SOFTWARE, self._start_panel_software) is False:
            run.kept(_PANEL_SOFTWARE)
        if owns_run:
            run.report()
        log.info(
            f'Engine started — project "{self.project.project.name}" '
            f"({len(self.project.devices)} devices, "
            f"{len(self.project.macros)} macros)"
        )

    async def _announce_started(self) -> None:
        self.state.set("system.started", True, source="system")
        await self.events.emit("system.started")

    def _push_state_to_clients(self) -> None:
        # The WebSocket hub listens to the state store and pushes from here
        # on, before the first state write, so a client registered at any
        # point of the start is owed every change after its snapshot
        # (ws_hub.add_client). The server's /ws registers one only once the
        # start has finished (the run-state record turns it away until then:
        # middleware/startup_splash.py), but a write made before the hub
        # listens (the simulation resuming, a device's first connect) would
        # never reach a client already registered, whoever registered it.
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

    def _load_drivers_at_start(self) -> None:
        """Register every driver on disk: the built-in definitions and
        anything installed into driver_repo/. After ``prepare_host`` (its
        repo migration) and before the project loads, since resolving a
        device's config and reporting its driver dependencies both ask the
        registry what exists. The load skips a file it cannot take on its
        own; a load that fails anyway is a stage the start goes on past
        (``_DRIVER_LIBRARY``)."""
        from openavc.drivers.driver_loader import load_builtin_drivers

        load_builtin_drivers()

    async def _load_project_at_start(self) -> None:
        """The project, with corruption recovery, composed with the product
        hardware, its logins moved out, saved if any of that changed it, and
        published (its recovery notice, settings, name and assets)."""
        loaded_project = self._load_project_safe()
        self.project = self.installed_equipment.compose(loaded_project, for_load=True)
        # The device logins it still holds in the clear move out before
        # anything dials (core/project_logins.py).
        logins_moved = await self._move_device_logins_at_load()
        # Exports and backups read the saved project. Materialize a newly
        # inserted host reference before any hardware or programming starts.
        if self.project != loaded_project:
            await self._save_project_at_start(logins_moved=logins_moved)
        # Whatever that had to do to get a project, say so. Published from the
        # record on disk rather than from this boot, so a notice nobody has
        # dismissed survives the restart that would otherwise bury it.
        from openavc.core import project_recovery, project_update
        project_recovery.publish(self.state, self.project_path.parent)
        project_update.publish(self.state, self.project_path.parent)
        self._apply_project_settings()
        self.state.set("system.project_name", self.project.project.name, source="system")
        # Publish project asset catalog so plugins (e.g. audio_player) can
        # subscribe to project.assets and pick up uploaded files.
        from openavc.api.assets import publish_assets_state
        publish_assets_state(self)

    async def _add_devices_at_start(self, run: StartRun) -> None:
        """Add every project device, in parallel with the connection table
        merged into each one's config, connected unless a simulation is coming
        back; then the ports they push to. A device that cannot be worked out
        or added is named in the start's warning, by id with why, and the
        others still start (``core/device_sync.py``); the device manager
        keeps it with why on its card, which is where it is told."""
        self._sweep_device_logins()
        resolved = work_out_each(self.project.devices, self.resolved_device_config)
        # A simulation that was on when the server stopped comes back
        # (core/simulation.py). Its devices register undialed, and the
        # bring-up round below starts the simulator and redirects them before
        # the first connect, so none of them reaches the real equipment.
        resume_simulation = self.simulation.load_resume()

        def failed(device_id: str, error: Exception) -> None:
            run.name(f"Device '{device_id}': {error}")
            log.error(f"Failed to add device '{device_id}': {error}")

        for device_id, error in resolved.failed.items():
            await self.devices.keep_unresolved(error)
            failed(device_id, error)
        await add_devices(
            self.devices, resolved.configs, defer_connect=resume_simulation, on_failure=failed,
        )
        if resume_simulation:
            self._schedule_device_bringup()
            await self.wait_for_device_bringup()
        self._record_push_ports()

    async def _start_plugins_at_start(self) -> None:
        """Scan the plugins, move their logins, start the project's. A stage
        the start goes on past (``_PLUGINS``)."""
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

    def _make_script_engine(self) -> None:
        project_dir = self.project_path.parent
        self.scripts = ScriptEngine(self.state, self.events, self.devices, project_dir, self.macros)
        self.scripts.install()

    def _load_scripts_at_start(self) -> None:
        """The project's scripts. A script that fails says so on its own
        (the script engine's load error); a load that fails as a whole is a
        stage the start goes on past (``_SCRIPTS``)."""
        self.scripts.load_scripts([s.model_dump() for s in self.project.scripts])

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

    def attach_server(
        self, *, start: asyncio.Task | None = None, audit=None, run_state=None,
    ) -> None:
        """The server around the engine: what it runs that a shutdown also
        ends, the task starting the engine in the background (``start``) and
        the device audit (``audit``), and its run-state record
        (``run_state``), whose stopping ``stopping`` reads. Set once by
        ``openavc.main`` as its lifespan begins; the listen watcher is set
        there (``listen_watcher``) once the engine is up."""
        self.server_start = start
        self.audit = audit
        self.run_state = run_state

    async def shutdown(self, deadline: float) -> list[str]:
        """End everything this process runs, for a process exit, within one
        ``deadline`` (``time.monotonic()``; ``core/stop_stages.stop_deadline``).
        The lifespan runs it at a service stop and ``openavc/restart.py`` at a
        restart, so both finish the same stages, in this order:

        the server's start if it is still running (cancelled before anything
        else, so one that has not begun never does, and what it acquired is
        released below), the listen watcher (it may be the start's: no restart
        request from it from here on), the push listeners (no device push
        comes in), the device audit (it holds project devices paused), the
        engine's own stop (``stop``'s stages), and the simulator proxy's
        client.

        Each stage runs on its own (core/stop_stages.py): one that raises or
        overruns is named and the rest still run. Returns what went wrong.

        ``stopping`` reads True from its first line, before the server's own
        pieces end: a restart has no stop signal to say so first."""
        self._stopping = True
        run = StopRun(deadline)
        await run.run("the server's start", lambda: _cancel_now(self.server_start))
        await run.run("the listen watcher", lambda: _cancel_now(self.listen_watcher))
        await run.run("the push listeners", _stop_push_listeners)
        await run.run("the device audit", self._end_audit)
        await self._stop_stages(run)
        await run.run("the simulator proxy", _close_simulator_proxy)
        run.report("OpenAVC stopped")
        return run.problems

    async def stop(self, deadline: float | None = None) -> list[str]:
        """Stop the engine: ``shutdown``'s engine stages alone, each on its
        own; bounded by ``deadline`` when one is given. Returns what went
        wrong."""
        run = StopRun(deadline)
        await self._stop_stages(run)
        run.report("Engine stopped")
        return run.problems

    async def _end_audit(self) -> None:
        if self.audit is not None:
            await self.audit.shutdown()

    async def _stop_stages(self, run: StopRun) -> None:
        log.info("Engine stopping...")
        self._stopping = True
        self._running = False
        await run.run("the device bring-up", self._stop_bringup)
        # Drain any queued bookkeeping persist before teardown so a
        # just-applied pending-settings clear or plugin-config save isn't
        # lost when the loop closes.
        await run.run("the bookkeeping save", lambda: self._drain_bookkeeping("before"))
        # Serialize teardown against an in-flight reload_project (reachable
        # from REST, the cloud command handler, and AI tools). Tearing
        # subsystems down while a hot-reload runs interleaves trigger and
        # subscription start/stop. Wait for any reload to finish first, until
        # the stop's bound: a reload that hangs does not keep the devices up.
        # A plugin's stop() or a system.stopping handler that saves runs
        # inside the teardown, so its save is queued, from the moment the
        # stop waits for the lock (_running_hooks, as _lock_for_a_change).
        with self._running_hooks():
            async with run.holding(self._reload_lock, "the project change still running"):
                await self._stop_inner(run)
        # A bookkeeping write scheduled DURING teardown (e.g. a plugin
        # on_stop calling save_config) spawned a flush task that queued
        # behind the lock held above. Drain it now that the lock is free so
        # the write persists before the process exits.
        await run.run(
            "the bookkeeping save after the stop", lambda: self._drain_bookkeeping("after"),
        )

    async def _stop_inner(self, run: StopRun) -> None:
        """Tear down all subsystems, one stage each. Caller holds
        _reload_lock (or waited its bound for it)."""
        await run.run("the update manager", self._stop_update_manager)
        await run.run("panel software updates", self._stop_panel_software)
        await run.run("learned config", self.learned_config.stop)
        # Unsubscribe all event/state handlers to prevent leaks on reload
        await run.run("the engine's subscriptions", self._drop_subscriptions)
        # What was set before the stop is written now, before any stage can
        # hang; the persister keeps watching until the macros are done.
        await run.run("the variables set before the stop", self._flush_persister)
        await run.run("system.stopping", lambda: self.events.emit("system.stopping"))
        await run.run("the triggers", self.triggers.stop)
        await run.run("the cloud agent", self._stop_cloud_agent)
        await run.run("the mDNS advertiser", self._stop_mdns_advertiser)
        await run.run("ISC", self._stop_isc_if_running)
        await run.run("the scripts", self._unload_scripts)
        await run.run("the plugins", self.plugin_loader.stop_all)
        # Stop batch task (its unwind flushes what is still batched)
        await run.run("the state flush", self.ws.stop_state_flush)
        await run.run("the periodic backup", lambda: _cancel_and_wait(self._periodic_backup_task))
        await run.run("the panel sweep", self._end_panel_sweep)
        await run.run("the programs installer", self._end_os_programs)
        # A script's or a plugin's state callback still running may yet set a
        # persistent variable: wait for what the state store has set off
        # before the persister stops. Last in the run-up, so a callback that
        # never ends costs only the run-up.
        await run.run("the state callbacks", self.state.flush_pending_events)
        # Everything that can fire a macro (triggers, scripts, plugins) has
        # stopped. From here the stages let go of the equipment, and each may
        # run to the deadline itself.
        run.hand_over()
        # Cancel in-flight macros (with the engine's grace drain) before the
        # devices they command are torn down — a macro racing disconnect_all
        # would error its remaining steps into a half-stopped engine. Late on
        # purpose: nothing that could restart one is still running.
        await run.run("the macros", self._end_macros)
        # Nothing that can set a variable is left (a "System Stopping"
        # trigger's macro, a script's or a plugin's stop): save the
        # persistent ones and stop watching, before the devices let go.
        await run.run("the state persister", self._stop_persister)
        # Stop file commands and setup actions still running, then delete
        # every staged file: either can be reading one.
        await run.run("the file commands", self.command_runs.stop)
        await run.run("the setup actions", self.setup_actions.stop)
        await run.run("the staged files", self.file_staging.stop)
        await run.run("the devices", self.devices.disconnect_all)
        # Then end the simulator: SimulationManager.end(EndReason.SHUTDOWN),
        # which hands nothing back to the real equipment and keeps the record,
        # so simulation comes back when the server starts again.
        await run.run("the simulator", self.simulation.shutdown)
        await run.run("system.started", self._mark_stopped)
        # Last: wait for what the stop's own writes set off (the devices'
        # going offline, system.started), so none of it is dropped.
        await run.run("the pending events", self.state.flush_pending_events)
        log.info("Engine stopped")

    async def _end_macros(self) -> None:
        """Close the macro engine, so a trigger fire still held in its
        listeners past the triggers' stop starts nothing, then cancel the
        macros that are running, with the engine's grace."""
        self.macros.close()
        await self.macros.cancel_all()

    def _mark_stopped(self) -> None:
        self.state.set("system.started", False, source="system")

    def start_failed(self) -> None:
        """A start that stopped short reads as not started: ``system.started``
        is said True before the start's last stages (``start``) and the
        server's own (``openavc.main``), so a stage after it that stops the
        start would otherwise leave it True."""
        self._mark_stopped()

    def _flush_persister(self) -> None:
        if self.persister:
            self.persister.flush()

    def _stop_persister(self) -> None:
        if self.persister:
            self.persister.stop()

    async def _stop_isc_if_running(self) -> None:
        if self.isc:
            await self._stop_isc()

    def _unload_scripts(self) -> None:
        if self.scripts:
            self.scripts.unload_all()

    def _drop_subscriptions(self) -> None:
        for sub_id in self._state_sub_ids:
            self.state.unsubscribe(sub_id)
        self._state_sub_ids.clear()
        for sub_id in self._event_sub_ids:
            self.events.off(sub_id)
        self._event_sub_ids.clear()

    async def _end_panel_sweep(self) -> None:
        """The panel sweep's slot is emptied; the periodic backup's keeps its
        ended task."""
        if await _cancel_and_wait(self._panel_sweep_task):
            self._panel_sweep_task = None

    async def _end_os_programs(self) -> None:
        task, self._os_programs_task = self._os_programs_task, None
        await _cancel_and_wait(task)
