"""
OpenAVC Engine — the main runtime orchestrator.

Wires together StateStore, EventBus, DeviceManager, MacroEngine, and
the WebSocket push system. Manages the full system lifecycle:
start, stop, and hot-reload.

Runtimes it owns but does not implement, each in its own module:
``ws_hub`` (client fan-out + batched state push), ``ui_events`` (what a
panel interaction does), and ``device_config`` (what a device dials). Parts
of Engine itself live in mixins it inherits: ``engine_lifecycle`` (start and
stop, one method per stage), ``project_changes`` (how a project change is
saved, applied and announced, and its revision), ``project_reconcile`` (how
the running system follows it: the reconcile by section, the device and
plugin syncs, the device bring-up), ``project_variables`` (the ``var.*``
runtime) and ``runtime_services`` (the ISC mesh, the mDNS advertiser, panel
software updates and the cloud agent). How the project's devices reach the
device manager is ``device_sync``.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import hmac
import socket
import time
from pathlib import Path
from typing import Any

from openavc import config, runtime_flags
from openavc.core.device_config import resolve_device_config
from openavc.core.device_logins import (
    DEVICE,
    SAVED_LOGIN,
    StoreView,
    fill_saved_logins,
    filled_plugin_config,
    in_project,
    marked_fields,
    move_logins,
    move_plugin_logins,
    plugin_logins_waiting,
    saved_isc_key,
    saved_logins_of,
)
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.help_requests import HelpRequests
from openavc.core.learned_config import LearnedConfig
from openavc.core.macro_engine import MacroEngine
from openavc.core.panel_devices import (
    ACCESS_APPROVED,
    CLOSE_CODE_NOT_APPROVED,
    CLOSE_REASON_NOT_APPROVED,
    PanelDeviceStore,
    access_mode,
)
from openavc.core.plugin_loader import PluginLoader
from openavc.core.engine_lifecycle import EngineLifecycleMixin
from openavc.core.project_changes import (  # noqa: F401 -- ProjectRevisionConflictError is imported from here
    ProjectChangesMixin,
    ProjectRevisionConflictError,
)
from openavc.core.project_reconcile import ProjectReconcileMixin
from openavc.core.project_variables import ProjectVariablesMixin
from openavc.core.project_loader import (
    ProjectConfig,
    refresh_crash_copy,
    save_project_async,
)
from openavc.core.runtime_services import RuntimeServicesMixin
from openavc.core.script_engine import ScriptEngine
from openavc.core.state_persister import StatePersister
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine
from openavc.core.ui_events import UIEventRuntime
from openavc.core.ws_hub import WSHub
from openavc.discovery import network_scanner
from openavc.drivers.registry import is_driver_registered
from openavc.ui.action_devices import annotate_action_devices
from openavc.ui.matrix_model import resolve_ui
from openavc.utils.log_redaction import get_secret_registry, project_secret_values
from openavc.utils.logger import get_logger
from openavc.version import __version__

log = get_logger(__name__)


def _replace_plugin_config(project: ProjectConfig, plugin_id: str, config: dict) -> None:
    """``config`` as the plugin's config in ``project``, if it still has it."""
    if plugin_id in project.plugins:
        project.plugins[plugin_id].config = config


class Engine(
    EngineLifecycleMixin, ProjectChangesMixin, ProjectReconcileMixin, ProjectVariablesMixin,
    RuntimeServicesMixin,
):
    """
    Main runtime engine. Singleton per OpenAVC instance.
    Coordinates all subsystems and manages the lifecycle.
    """

    def __init__(self, project_path: str):
        from openavc.core.host_policy import HostPolicy
        self.host_policy = HostPolicy()
        self.project_path = Path(project_path)
        self.project: ProjectConfig | None = None
        # Resolved lazily and then held: get_or_create_instance_id() falls back
        # to a fresh ephemeral UUID when it cannot write .instance_id (a
        # read-only data dir), so calling it per request would hand out a
        # DIFFERENT id every time. Panels pin their cert trust under this id.
        self._instance_id: str | None = None

        # Core subsystems
        self.state = StateStore()
        self.events = EventBus()
        # WebSocket fan-out + batched state push (see ws_hub); broadcast_ws
        # below is the engine-wide door to it.
        self.ws = WSHub(self.state)
        # The panels approved to run this system (core/panel_devices). Loaded
        # here, not in start(): the socket gate asks it from the first
        # handshake, and a test-built engine that never starts still has it.
        from openavc.system_config import get_data_dir
        self.panel_devices = PanelDeviceStore(get_data_dir() / "panel_devices.json")
        self.panel_devices.load()
        # The device logins saved on this system (core/device_logins). Read
        # from disk on first use; neither of its files exists until a login
        # is saved.
        from openavc.core.device_logins import device_logins
        self.device_logins = device_logins(get_data_dir())
        self._panel_sweep_task: asyncio.Task | None = None
        self._os_programs_task: asyncio.Task | None = None
        # Panel interaction runtime (peer of the macro engine, see ui_events)
        self.ui_events = UIEventRuntime(self)
        from openavc.core.installed_equipment import InstalledEquipment
        self.installed_equipment = InstalledEquipment.from_environment()
        self.installed_equipment.project_provider = lambda: self.project
        self.installed_equipment.register()
        self.devices = DeviceManager(self.state, self.events)
        self.devices.installed_equipment = self.installed_equipment
        # A driver reload re-adds its devices undialed and asks for a bring-up
        # round, so the simulation redirect lands before the first connect.
        self.devices.request_bringup = self._schedule_device_bringup
        self.devices.on_driver_reloaded = self._record_push_ports
        # "Ask for help" — raised by a macro step, never a stock control.
        self.help = HelpRequests(self)
        self.macros = MacroEngine(
            self.state, self.events, self.devices,
            broadcast_ws=self.broadcast_ws, help_requests=self.help,
            project_provider=lambda: self.project,
            # None before a project is loaded, so the group fan-out's
            # does-this-device-exist check stays quiet rather than calling
            # every member missing.
            project_device_ids=lambda: (
                {d.id for d in self.project.devices} if self.project else None
            ),
        )
        self.triggers = TriggerEngine(self.state, self.events, self.macros)
        self.scripts: ScriptEngine | None = None
        self.plugin_loader = PluginLoader(
            self.state, self.events, self.macros, self.devices,
            cloud_agent_provider=lambda: self.cloud_agent,
        )
        # A plugin is handed its config with the logins saved on this system
        # filled in, and "" for one this system does not have, never the marker.
        self.plugin_loader.set_config_filler(
            lambda plugin_id, plugin_config: filled_plugin_config(
                plugin_id, plugin_config, self.device_logins,
            )
        )
        self.persister: StatePersister | None = None
        self.isc = None  # ISCManager, initialized in start() if enabled
        # Whether this start backed up the project before moving its logins.
        self._logins_backed_up_at_start = False
        self.cloud_agent = None  # CloudAgent, initialized in start() if enabled
        # UpdateManager and PanelSoftwareUpdates, both initialized in start()
        self.update_manager = self.panel_software = None
        self.mdns_advertiser = None  # MDNSAdvertiser, initialized in start() if enabled

        # Setup-action runner (driver-declared provisioning wizards)
        from openavc.core.setup_actions import SetupActionRunner
        self.setup_actions = SetupActionRunner(self)
        # Commands that take or hand back a file: the files waiting on disk,
        # and Send Command's background runs of them.
        from openavc.core.command_runs import CommandRuns
        from openavc.core.file_staging import STAGING_DIRNAME, FileStaging
        self.file_staging = FileStaging(get_data_dir() / STAGING_DIRNAME)
        self.command_runs = CommandRuns(self, self.file_staging)
        # Settings the device fills in (config fields with learned_from).
        self.learned_config = LearnedConfig(self)

        # Simulation
        from openavc.core.simulation import SimulationManager
        self.simulation = SimulationManager(self)
        self.devices.redirected_to_simulator = self.simulation.is_redirected
        self.devices.real_connection = self.simulation.real_connection_of

        # Wire StateStore -> EventBus
        self.state.set_event_bus(self.events)

        self._init_variable_runtime()
        # The revision, the reload lock and the bookkeeping queue.
        self._init_project_changes()

        # Event/state subscription IDs (for cleanup on stop/reload)
        self._state_sub_ids: list[str] = []
        self._event_sub_ids: list[str] = []

        # The device bring-up worker's slot and its "another round" flag.
        self._init_reconcile()

        # Tracking
        self._start_time: float = 0
        self._running = False

        # Periodic backup
        self._periodic_backup_task: asyncio.Task | None = None
        self._dirty_since_backup: bool = False
        self._last_backup_time: float = 0

        # Cached (local_ip, hostname, all_ips) for get_status. Detection does
        # blocking adapter-enumeration / gethostname syscalls; they rarely
        # change, so compute once. Stays None until a detection finds an
        # address -- see _detect_network_info.
        self._network_info: tuple[str, str, list[str]] | None = None

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

    def _apply_project_settings(self) -> None:
        """Push the project's `settings` block onto the runtime that reads it,
        and its own secrets (the mesh key) onto the log redaction.

        Called wherever `self.project` is replaced -- load, apply, and the
        rollback -- so a save or a cloud push takes effect without a restart,
        and a rollback puts the previous value back with everything else.
        """
        self._register_project_secrets()
        try:
            settings = getattr(self.project, "settings", None)
            devices = getattr(settings, "devices", None)
            self.devices.project_reconnect_interval = getattr(
                devices, "reconnect_interval_seconds", None
            )
        except Exception:
            # A project this malformed has bigger problems than its retry
            # interval; falling back to system.json is the pre-existing
            # behaviour and is always safe.
            self.devices.project_reconnect_interval = None

    def _register_project_secrets(self) -> None:
        """Hand the log redaction the project's own secrets: the mesh key and
        every plugin login, from the login file where the project holds the
        marker, as the project holds them where it does not (a move that could
        not finish). Registered whether the mesh or the plugin runs or not."""
        try:
            values = {
                v for v in project_secret_values(self.project) if v != SAVED_LOGIN
            }
            if self.project is not None:
                key = saved_isc_key(self.project, self.device_logins)
                if key:
                    values.add(key)
                for found in saved_logins_of(self.project, StoreView(self.device_logins)):
                    if found.kind != DEVICE:
                        values |= set(found.fields.values())
            get_secret_registry().set_project_secrets(values)
        except Exception:  # never block a project on the log redaction
            log.debug("Could not register the project's secrets for log redaction", exc_info=True)

    def _record_push_ports(self) -> None:
        """Write the ports this project's devices push to, for the Linux host
        firewall helper (``core/push_ports.py``). Never fatal: a port left
        shut costs a device its pushes, not its connection."""
        from openavc.core import push_ports
        from openavc.system_config import get_data_dir

        if self.project is None:
            return
        try:
            push_ports.record(
                get_data_dir(),
                push_ports.collect(self.project.devices, self.resolved_device_config),
            )
        except Exception:
            log.exception("Could not record the ports devices push to")

    def resolved_device_config(self, device) -> dict:
        """The config this device actually dials — driver defaults, project
        config, and the connection table layered, with bridge/USB bindings
        resolved (``core.device_config``), and each setting the project marks
        as saved on this system filled from the login file
        (``core.device_logins``). What is handed to the device manager, and
        so to the driver; a reader that shows or sends a device's settings
        uses ``resolve_device_config``, which keeps the marker."""
        installed = self.installed_equipment.resolve(device)
        if installed is not None:
            return installed
        return fill_saved_logins(resolve_device_config(device, self.project), self.device_logins)

    def logins_needed(self) -> list[dict[str, str]]:
        """The devices that wait for somebody to enter a login, in project
        order, as id and name: each enabled device on an installed driver
        whose project marks a login as saved on this system that this system
        does not have (``core.device_logins``), the setting its driver refuses
        to dial with ``login_missing``. Read from the project and the login
        file, so it is right as soon as a project is applied, before any
        device has tried to connect."""
        if self.project is None:
            return []
        needed = []
        for device in self.project.devices:
            if not device.enabled or not is_driver_registered(device.driver):
                continue
            if marked_fields(self.resolved_device_config(device).get("config")):
                needed.append({"id": device.id, "name": device.name or device.id})
        return needed

    def plugin_logins_needed(self) -> list[dict[str, str]]:
        """The plugins that wait for somebody to enter a login, in project
        order, as id and name: each enabled, installed plugin whose config
        marks a login it declares as saved on this system that this system
        does not have (``core.device_logins.plugin_logins_waiting``). A login
        the plugin makes itself (Present's display keys) never waits: it is
        made again."""
        if self.project is None:
            return []
        from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

        needed = []
        for plugin_id, entry in self.project.plugins.items():
            plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
            if not entry.enabled or plugin_class is None:
                continue
            if plugin_logins_waiting(plugin_id, entry.config, self.device_logins):
                info = getattr(plugin_class, "PLUGIN_INFO", None) or {}
                needed.append({"id": plugin_id, "name": str(info.get("name") or plugin_id)})
        return needed

    def _move_logins(self, project: ProjectConfig, *, typed: bool) -> ProjectConfig:
        """``project`` with each login it holds in the clear moved into this
        system's login file (``core.device_logins.move_logins``): the same
        object when nothing moved or the move could not finish. Product
        hardware is left out; its login never lives in the project."""
        try:
            built_in = {d.id for d in project.devices if self.installed_equipment.matches(d)}
            return move_logins(project, self.device_logins, typed=typed, skip=built_in.__contains__)
        except Exception:  # a move that fails leaves every login where it was
            log.exception("Could not move the device logins out of the project")
            return project

    async def _move_logins_at_start(self) -> None:
        """The second half of the first-start move, run once the plugins are
        loaded: each plugin login the project still holds in the clear moves
        into the login file, then every Project Library copy's logins (inline:
        one small file per saved project). The project is backed up first
        unless this start already did, before the device logins moved: that
        backup is the file as it was, these logins and all."""
        moved = self._move_logins(self.project, typed=False)
        if moved is not self.project:
            if self._logins_backed_up_at_start or await self._back_up_before_moving_logins():
                self._logins_backed_up_at_start = True
                self.project = moved
                await save_project_async(self.project_path, self.project)
                await asyncio.to_thread(refresh_crash_copy, self.project_path)
                self._register_project_secrets()
        try:
            from openavc.core.project_library import move_library_logins

            move_library_logins(self.device_logins)
        except Exception:  # never block startup on the library
            log.exception("Could not move the logins out of the Project Library")

    async def _back_up_before_moving_logins(self) -> bool:
        """Back up the project file before the move rewrites it. False when
        the backup failed, and the file must then stay as it is."""
        from openavc.core.backup_manager import create_backup

        try:
            await asyncio.to_thread(
                create_backup, self.project_path, "Before moving device logins out of the project",
            )
        except Exception:
            log.exception("Could not back up the project, so its device logins stay in it for now")
            return False
        return True

    def _sweep_device_logins(self) -> None:
        """Date the saved logins of devices, plugin items and plugins that left
        the project, and delete those gone 90 days (``DeviceLogins.sweep``). Never fatal: a sweep that
        cannot write leaves the dates as they were, and the logins in place."""
        if self.project is None:
            return
        try:
            self.device_logins.sweep(in_project(self.project))
        except Exception:
            log.exception("Could not update the saved device logins")

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
        # The plugin saves the config it runs with, its logins filled in. Each
        # moves into the login file here, since this persist never passes
        # _apply_project_locked: one the plugin changed (a regenerated key, a
        # password typed into its own page) replaces the saved one, and the
        # project keeps the marker.
        config = move_plugin_logins(plugin_id, config, self.device_logins, current=previous)

        on_error = functools.partial(self._plugin_config_not_saved, plugin_id, previous)
        if self._reload_lock.locked():
            # Possibly our own call chain holds the lock (a plugin hook run
            # by the reconciler) — awaiting it would deadlock. The plugin's
            # live config is already current; only the persist is deferred.
            _replace_plugin_config(self.project, plugin_id, config)
            self.schedule_bookkeeping_change(
                lambda project: self._write_running_plugin_config(project, plugin_id),
                on_error=on_error,
            )
        else:
            try:
                await self.persist_bookkeeping_change(
                    lambda project: _replace_plugin_config(project, plugin_id, config),
                    on_error=on_error,
                )
            except Exception as e:
                log.error(f"Failed to save plugin config for '{plugin_id}': {e}")
                raise
        # A login the plugin made or changed is masked in the log from now on.
        self._register_project_secrets()

    async def _plugin_config_not_saved(self, plugin_id: str, previous: dict) -> None:
        # Revert the in-memory project so it matches disk. Otherwise a
        # bad/unwritable config lingers in the shared project model and
        # the next save of ANYTHING re-serializes it and fails too.
        _replace_plugin_config(self.project, plugin_id, previous)
        await self.broadcast_ws({
            "type": "error",
            "message": f"Failed to save plugin config for '{plugin_id}'",
        })

    def _write_running_plugin_config(self, project: ProjectConfig, plugin_id: str) -> None:
        """The deferred half of a plugin's save, run after the reconcile."""
        # By then another save of this plugin may have landed (its own
        # save_config, the settings form, the assistant). Writing the config
        # as it was captured would put the older one back over it, so the
        # config the plugin runs with when this runs is the one written.
        if plugin_id not in project.plugins or not self.plugin_loader.is_running(plugin_id):
            return
        running = copy.deepcopy(self.plugin_loader.get_running_config(plugin_id))
        project.plugins[plugin_id].config = move_plugin_logins(
            plugin_id, running, self.device_logins,
            current=project.plugins[plugin_id].config,
        )

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

        Two resolutions now, on the same reasoning. The second stamps each
        element with the devices its ``do`` actions can reach
        (``ui/action_devices.py``), which is how the panel marks a control that
        will be refused on press. It needs the project's macros and groups —
        a press runs a macro, which commands a group — so the renderer could
        not work it out from the page it is given even if we wanted it to.

        **The panel lock PIN is not in what comes back.** A panel client holds
        no programmer credential (the panel gate admits it, and in open mode
        that is anyone on the LAN), so everything this returns reaches every
        panel the gate admits — and this is the payload the lock screen used to
        be handed the code it was checking in. It leaves as ``lock_enabled``,
        and the attempt goes to ``POST /api/panel/unlock``.
        """
        if not self.project:
            return {}
        ui = resolve_ui(self.project.ui.model_dump(mode="json"))
        settings = ui.get("settings")
        if isinstance(settings, dict):
            settings["lock_enabled"] = bool(settings.pop("lock_code", ""))
        return annotate_action_devices(
            ui,
            [m.model_dump(mode="json") for m in self.project.macros],
            [g.model_dump(mode="json") for g in self.project.device_groups],
            # Which commands go out with the device offline (the press marks);
            # without a device manager every command needs a connection.
            works_offline=(
                self.devices.command_works_offline
                if getattr(self, "devices", None) is not None else None
            ),
        )

    def panel_lock_accepts(self, code: str) -> bool:
        """Whether ``code`` is this project's panel lock PIN.

        The server checks it because the server is the only side that can:
        a panel holds no programmer credential, so handing it the PIN to
        compare against published the PIN to every admitted panel, and the
        "lock" was a
        courtesy the same server had already given away. Constant-time, which
        costs nothing on a six-digit string and means a wrong PIN takes the
        same time whatever it got right.

        A project with no PIN set accepts nothing rather than everything — the
        panel never puts the screen up in that case, so an attempt arriving
        anyway is not a person who left the field blank.

        Both sides are encoded before the comparison, for the reason
        ``api/auth.py``'s username check is: ``compare_digest`` raises on two
        ``str`` arguments that are not both ASCII. The Builder's field takes
        digits only, but a project file can be hand-written or AI-authored, and
        a lock code with an accent in it would answer every attempt with a 500.
        """
        if not self.project:
            return False
        expected = self.project.ui.settings.lock_code or ""
        if not expected:
            return False
        return hmac.compare_digest(
            str(code or "").encode("utf-8"), expected.encode("utf-8")
        )

    def _load_project_safe(self) -> ProjectConfig:
        """project.avc, or what stands in for it when it is missing or cannot
        be read: the seed, a backup, or an empty Recovery Project, with the
        record a recovery leaves (``project_recovery.load_or_recover``)."""
        from openavc.core import project_recovery

        return project_recovery.load_or_recover(self.project_path)

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

    # --- WebSocket Management ---

    async def broadcast_ws(
        self, message: dict[str, Any], *, client_type: str | None = None
    ) -> None:
        """Send a message to every connected WS client (panels, IDE).

        The engine-wide broadcast door — macros, triggers, discovery, and the
        REST routes all reach panels through here. Delivery, per-client
        queues, and namespace filtering live in ``core.ws_hub``.
        ``client_type`` narrows delivery to one kind of client, as there
        (``"programmer"`` for a message that is the Programmer's business).
        """
        await self.ws.broadcast(message, client_type=client_type)

    # --- Panel access ---

    async def panel_access_changed(self) -> None:
        """Apply a saved ``panels.access`` to the live sockets.

        Switching to approved closes every panel socket that was admitted
        only because the mode was open (with 4010, so the page shows the
        waiting screen and checks in); a socket admitted by a credential, the
        box's own screen, a tunnel or an approved cookie stays up. Switching
        to open changes nothing for connected clients. Programmer clients are
        told either way, so the Panels card can redraw.
        """
        from openavc.system_config import get_system_config

        config = get_system_config()
        mode = access_mode()
        if mode == ACCESS_APPROVED:
            closed = await self.ws.close_clients(
                admitted_by="open",
                code=CLOSE_CODE_NOT_APPROVED,
                reason=CLOSE_REASON_NOT_APPROVED,
            )
            if closed:
                log.info(f"Panel access is now approved panels only; {closed} panel socket(s) sent to the waiting screen")
            # The one-time notice that a system updated with its panels
            # connected is still open has nothing left to say.
            if config.get("panels", "upgrade_notice", False):
                config.set("panels", "upgrade_notice", False)
                config.save()
        await self.ws.broadcast(
            {
                "type": "panel.devices.changed",
                "reason": "access_changed",
                "access": mode,
                "upgrade_notice": bool(config.get("panels", "upgrade_notice", False)),
            },
            client_type="programmer",
        )

    async def _retry_client_missing_devices(self) -> None:
        """Reconnect every device that stopped retrying because a program it
        needs was missing, now that the program is installed."""
        for item in self.devices.list_devices():
            device_id = item["id"]
            if self.state.get(f"device.{device_id}.offline_reason") != "client_missing":
                continue
            try:
                await self.devices.reconnect_device(device_id)
            except Exception:
                log.exception("[%s] Reconnect after installing the SSH client failed", device_id)

    async def _panel_devices_sweep_loop(self) -> None:
        """Drop pending and denied panel records past their time, and tell
        the Programmer, so the list stays honest without a check-in."""
        try:
            while True:
                await asyncio.sleep(60)
                for device in self.panel_devices.expire():
                    await self.ws.broadcast(
                        {"type": "panel.devices.changed", "reason": "expired",
                         "device": device.public()},
                        client_type="programmer",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Panel device sweep stopped")

    def _bridge_events_to_ws(self) -> None:
        """Subscribe the forwarders below to the engine events they carry to
        the WebSocket clients, and the persist after pending settings apply.
        Once, at a start, after everything that raises them exists."""
        # Bridge macro events to WebSocket for live progress tracking
        self._event_sub_ids.append(self.events.on("macro.started.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.progress.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.completed.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.cancelled.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.error.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.step_error.*", self._on_macro_event))
        self._event_sub_ids.append(self.events.on("macro.skipped.*", self._on_macro_event))

        # Bridge trigger events to WebSocket
        self._event_sub_ids.append(self.events.on("trigger.fired", self._on_trigger_event))
        self._event_sub_ids.append(self.events.on("trigger.skipped", self._on_trigger_event))
        self._event_sub_ids.append(self.events.on("trigger.pending", self._on_trigger_event))
        self._event_sub_ids.append(self.events.on("trigger.queued", self._on_trigger_event))
        self._event_sub_ids.append(self.events.on("trigger.completed", self._on_trigger_event))

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
        """Forward script error events to the Programmer's WebSocket clients.

        Never a panel's: the payload carries the exception's text and its
        traceback, and a panel draws nothing from it. The room hears about a
        failed control through the press's own answer.
        """
        await self.broadcast_ws({"type": "script.error", **payload}, client_type="programmer")

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
                        create_backup, self.project_path, "Auto-backup"
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
        result = create_backup(self.project_path, reason)
        if result:
            self._last_backup_time = time.time()
            self._dirty_since_backup = False

    # --- Status ---

    def _detect_network_info(self) -> tuple[str, str, list[str]]:
        """Return ``(local_ip, hostname, all_ips)``, cached after the first call.

        ``all_ips`` is every reachable IPv4 leg of this host, best guess first,
        and ``local_ip`` is that top pick — a multi-homed controller has no
        single right answer, so the setup screen shows the whole list. Both
        syscall paths block the event loop and the values rarely change over a
        process's lifetime, hence the cache.

        A detection that found nothing is NOT cached. Finding nothing means
        the host had no address at the moment we looked, which is what a
        server started before its adapter comes up sees — at boot, or ahead
        of DHCP. Caching that answer pinned ``local_ip`` to 127.0.0.1 for the
        life of the process, and the Dashboard then published loopback as the
        address to type at a panel. Re-detecting costs one adapter
        enumeration and a routeless UDP connect, and only on a host that
        still has no address.
        """
        if self._network_info is not None:
            return self._network_info
        all_ips = network_scanner.get_ranked_interface_ips()
        local_ip = all_ips[0] if all_ips else "127.0.0.1"
        try:
            hostname = socket.gethostname()
        except OSError:
            hostname = ""
        info = (local_ip, hostname, all_ips)
        if all_ips:
            self._network_info = info
        return info

    def refresh_network_info(self) -> tuple[str, str, list[str]]:
        """Re-detect (and re-cache) the local addresses and hostname.

        Blocking — call off-loop. The setup screen polls through this so a
        device that boots before its network is up shows its address as soon
        as the cable goes in, instead of serving the stale startup cache.
        """
        self._network_info = None
        return self._detect_network_info()

    @property
    def instance_id(self) -> str:
        """This instance's persistent UUID, resolved once per process.

        The same id the mDNS advertiser puts in its ``id`` TXT field and ISC
        identifies this peer by, so a panel that found us by QR or manual IP
        gets the identifier a panel that found us by mDNS already had.
        """
        if self._instance_id is None:
            from openavc.core.isc import get_or_create_instance_id

            self._instance_id = get_or_create_instance_id(self.project_path)
        return self._instance_id

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
            # Not gated with the host/network identifiers below: this is not a
            # LAN-reconnaissance detail, it is already broadcast to the whole
            # LAN in the mDNS TXT record, and a panel paired by QR or manual IP
            # has no other way to learn it. The panel app keys its pinned
            # server cert on it so the pin survives a DHCP address change.
            "instance_id": self.instance_id,
        }
        if include_sensitive:
            local_ip, hostname, all_ips = self._detect_network_info()
            # Who can connect (openavc/listen.py). With one adapter chosen,
            # only its addresses are offered as somewhere to open the panel.
            plan = config.listen_plan()
            if plan.mode == "adapter":
                all_ips = [ip for ip in all_ips if ip in plan.network_addresses] or list(
                    plan.network_addresses
                )
                local_ip = all_ips[0] if all_ips else "127.0.0.1"
            status["hostname"] = hostname
            status["local_ip"] = local_ip
            status["local_ips"] = all_ips
            # The one address an earlier client reads: 0.0.0.0, 127.0.0.1
            # (also a chosen adapter that is missing), or the adapter's.
            status["bind_address"] = plan.legacy_bind
            status["listen"] = plan.to_dict()
        if self.isc:
            status["isc_peers"] = sum(
                1 for p in self.isc._peers.values() if p.connected
            )
        if self.cloud_agent:
            status["cloud_connected"] = self.cloud_agent._connected
        return status
