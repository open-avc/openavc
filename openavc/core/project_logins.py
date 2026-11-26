"""The logins a project holds, from the engine's side: which devices and
plugins wait for somebody to enter one, moving each login the project still
holds in the clear into this system's login file (``core.device_logins``),
the log redaction's copy of the project's own secrets, the sweep of logins
whose owner left the project, and a plugin's own save of its config, whose
logins move with it through the change door.

A move happens twice in a start: the device logins as the project loads
(``_move_device_logins_at_load``), the plugin logins once the plugins are
loaded and can say which settings hold one (``_move_logins_at_start``). The
project file is backed up before the first move rewrites it, once per start
(``_adopt_moved_logins``), and a save that moved logins refreshes the crash
copy (``_save_project_at_start``); a move or a backup that cannot finish
changes nothing. That backup holds the file as it was, logins in the clear,
only until the move is done: once every save that moved logins has
succeeded, it keeps the file with the marker in each one's place and the
logins sealed beside it (``_seal_the_start_backup``). Every older backup of
the project is sealed the same way at each start (``_seal_older_backups``):
one an earlier version took holds the logins in the clear, and would until
newer backups rotate it out. The pre-update zips a rollback restores are not
project backups and are left as they are.

Mixed into Engine (core/engine.py). From the engine it reads the project and
its path, ``resolved_device_config``, the product hardware
(``installed_equipment``), the plugin loader and ``broadcast_ws``; a plugin's
save goes through the bookkeeping doors of core/project_changes.py, which own
when a save waits for a reconcile. Logs under the engine's name.
"""

from __future__ import annotations

import asyncio
import copy
import functools
from pathlib import Path
from typing import Any

from openavc.core import saved_params
from openavc.core.device_logins import (
    DEVICE,
    PARAM,
    SAVED_LOGIN,
    FoundLogin,
    LoginChange,
    StoreView,
    held_as_saved,
    in_project,
    find_logins,
    find_plugin_logins,
    keep_waiting_logins,
    marked_fields,
    move_logins,
    param_owner,
    plugin_configs,
    plugin_logins_waiting,
    saved_isc_key,
    saved_logins_of,
)
from openavc.core.device_sync import resolve_each
from openavc.core.project_loader import ProjectConfig, refresh_crash_copy, save_project_async
from openavc.drivers.registry import get_driver_class, is_driver_registered
from openavc.utils.log_redaction import get_secret_registry, project_secret_values, secret_values
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


def _config_schema(driver_id: Any) -> dict[str, Any] | None:
    cls = get_driver_class(driver_id) if isinstance(driver_id, str) and driver_id else None
    return (getattr(cls, "DRIVER_INFO", None) or {}).get("config_schema")


def secrets_held_in_the_clear(project: Any) -> set[str]:
    """Every secret value ``project`` holds in the clear: its settings by the
    one collector (``_settings_held``) and the logins its drivers and plugins
    declare (``device_logins.find_logins``). A device that runs registers its
    own as its driver is built; these cover the rest, a disabled device, one
    waiting for its driver, a connection no device uses yet, a plugin's. A
    marker is no secret."""
    values = _settings_held(project) | {
        value for found in find_logins(project) for value in found.fields.values()
        if isinstance(value, str)
    }
    return {v for v in values if v != SAVED_LOGIN and not saved_params.is_any_marker(v)}


def _settings_held(project: Any) -> set[str]:
    """The secret values in ``project``'s settings, by
    ``log_redaction.secret_values``: each connection row's, each device's on
    its driver's ``config_schema``, each plugin's config."""
    values: set[str] = set()
    for connection in (getattr(project, "connections", None) or {}).values():
        values |= secret_values(connection if isinstance(connection, dict) else None)
    for device in getattr(project, "devices", None) or []:
        values |= secret_values(getattr(device, "config", None), _config_schema(getattr(device, "driver", None)))
    for _plugin_id, config in plugin_configs(project):
        values |= secret_values(config)
    return values


class ProjectLoginsMixin:
    """Where the project's logins live, and who is waiting for one."""

    def _init_logins(self) -> None:
        # The device logins saved on this system (core/device_logins). Read
        # from disk on first use; neither of its files exists until a login
        # is saved.
        from openavc.core.device_logins import device_logins
        from openavc.system_config import get_data_dir

        self.device_logins = device_logins(get_data_dir())
        # Whether this start backed up the project before moving its logins,
        # the backup it took (the file as it was, logins in the clear), and
        # whether a move it adopted is not yet saved.
        self._logins_backed_up_at_start = False
        self._logins_backup_at_start: Path | None = None
        self._logins_unsaved_at_start = False
        # plugin id -> the logins its latest own save holds, masked in the
        # log from the save on, whether or not it reached the file.
        self._plugin_save_logins: dict[str, set[str]] = {}

    def _register_project_secrets(self) -> None:
        """Hand the log redaction the project's own secrets
        (``_project_secrets``). Registered whether the mesh or the plugin runs
        or not; a project whose secrets cannot be read keeps the ones
        registered before."""
        try:
            get_secret_registry().set_project_secrets(self._project_secrets())
        except Exception:  # never block a project on the log redaction
            log.debug("Could not register the project's secrets for log redaction", exc_info=True)

    def _project_secrets(self) -> set[str]:
        """The mesh key and every plugin login, from the login file where the
        project holds the marker, as the project holds them where it does not
        (a move that could not finish), and as each plugin's latest own save
        holds them, which may not have reached the file yet."""
        values = {v for v in project_secret_values(self.project) if v != SAVED_LOGIN}
        if self.project is None:
            return values
        values |= secrets_held_in_the_clear(self.project)
        key = saved_isc_key(self.project, self.device_logins)
        if key:
            values.add(key)
        for found in saved_logins_of(self.project, StoreView(self.device_logins)):
            if found.kind != DEVICE:
                values |= set(found.fields.values())
        # The plugin holds what it saved before the file does.
        values.update(*self._plugin_save_logins.values())
        return values

    def saved_param_value(self, name: str) -> str | None:
        """The value of the command parameter saved on this system under
        ``name`` (the name its step's marker carries), or None."""
        return self.device_logins.secret(param_owner(name), saved_params.FIELD)

    def save_param_value(self, value: str) -> str:
        """Save ``value`` as a command parameter on this system and return the
        marker a step holds in its place (``core/saved_params.py``). Raises
        ValueError for an empty value or a marker, LoginFileUnreadable when
        the login file cannot be opened; nothing is saved then."""
        name = saved_params.new_name()
        self.device_logins.store(
            [FoundLogin(name, "", {saved_params.FIELD: value}, PARAM)], typed=True,
        )
        self.device_logins.verify()
        return saved_params.marker(name)

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
        waiting = [
            device for device in self.project.devices
            if device.enabled and is_driver_registered(device.driver)
        ]
        # Each worked out on its own: one that cannot be (core/device_sync.py)
        # is not listed, since it waits for its settings, not a login.
        resolved = resolve_each(waiting, self.resolved_device_config).configs
        return [
            {"id": device.id, "name": device.name or device.id}
            for device in waiting
            if device.id in resolved and marked_fields(resolved[device.id].get("config"))
        ]

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

    def _move_logins_recorded(self, project: ProjectConfig, *, typed: bool) -> tuple[ProjectConfig, LoginChange]:
        """``_move_logins``, and what it wrote to the login file: a project
        change files its logins before it runs (its devices dial with them),
        and ``_take_back_logins`` gives them back when it does not run."""
        return self.device_logins.recorded(functools.partial(self._move_logins, project, typed=typed))

    def _take_back_logins(self, change: LoginChange) -> None:
        """Put back what a change that did not run filed in the login file,
        before the previous project's devices dial again. Logged, never
        raised: the caller is already answering the change's own failure."""
        if not change:
            return
        try:
            self.device_logins.take_back(change)
        except Exception:
            log.exception("Could not put the login file back after a project change that did not run")

    async def _adopt_moved_logins(self, moved: ProjectConfig) -> bool:
        """``moved`` (a ``_move_logins`` of the running project) as the
        project, once the file is backed up; True when it was taken. The
        project is backed up once per start: the first backup is the file as
        it was, these logins and all. A backup that fails leaves the project
        as it is."""
        if moved is self.project:
            return False
        if not (self._logins_backed_up_at_start or await self._back_up_before_moving_logins()):
            return False
        self._logins_backed_up_at_start = True
        self._logins_unsaved_at_start = True
        self.project = moved
        return True

    async def _move_device_logins_at_load(self) -> bool:
        """The first half of the first-start move, as the project loads: a
        login the project still holds in the clear (every login, on the first
        start of a version that keeps them in this system's login file) moves
        into that file before anything dials, and the project keeps the
        marker. This file may exist nowhere else (an update that took no
        backup of its own, an installer run by hand), so it is backed up
        first. True when the logins moved; the start saves the project
        (``_save_project_at_start``). A plugin's logins move later, once the
        plugins are loaded and can say which settings hold one."""
        self._logins_backed_up_at_start = False
        self._logins_backup_at_start = None
        self._logins_unsaved_at_start = False
        return await self._adopt_moved_logins(self._move_logins(self.project, typed=False))

    async def _move_logins_at_start(self) -> None:
        """The second half of the first-start move, run once the plugins are
        loaded: each plugin login the project still holds in the clear moves
        into the login file, saved, then every Project Library copy's logins
        (inline: one small file per saved project)."""
        if await self._adopt_moved_logins(self._move_logins(self.project, typed=False)):
            await self._save_project_at_start(logins_moved=True)
            self._register_project_secrets()
        try:
            from openavc.core.project_library import move_library_logins

            move_library_logins(self.device_logins)
        except Exception:  # never block startup on the library
            log.exception("Could not move the logins out of the Project Library")

    async def _save_project_at_start(self, *, logins_moved: bool) -> None:
        """Save the running project during a start. After a save that moved
        logins out, the crash copy is made the file as now saved: the copy
        taken before that save still holds the logins, and would keep them
        until the next save."""
        await save_project_async(self.project_path, self.project)
        if logins_moved:
            self._logins_unsaved_at_start = False
            await asyncio.to_thread(refresh_crash_copy, self.project_path)

    async def _seal_the_start_backup(self) -> None:
        """The backup this start took before moving logins, once every move
        it adopted is saved: written again with the marker in place of each
        login it holds in the clear and the logins sealed beside it, the file
        as it was otherwise (``backup_manager.seal_logins_in_backup``). A save
        that failed leaves it as it is, and so does a login in it other than
        the one saved here, which the move kept the saved one over
        (``held_as_saved``). Then every older backup of the project
        (``_seal_older_backups``). Run once the plugins' half of the move has
        run, or failed, as a stage the start goes on past, which keeps a
        failure on the run-state record."""
        backup, self._logins_backup_at_start = self._logins_backup_at_start, None
        if self._logins_unsaved_at_start:
            return
        from openavc.core.backup_manager import seal_logins_in_backup

        built_in = {d.id for d in self.project.devices if self.installed_equipment.matches(d)}
        skip = built_in.__contains__
        if backup is not None:
            await asyncio.to_thread(
                seal_logins_in_backup, backup, functools.partial(self._moved_if_saved, skip=skip),
            )
        await asyncio.to_thread(self._seal_older_backups, skip)

    def _seal_older_backups(self, skip) -> None:
        """Each project backup still holding logins in the clear that are
        all saved here, sealed as the start backup is. One holding a login
        other than the saved one is left as it is: it is the one place that
        value is left. A backup that cannot be read or written is skipped."""
        from openavc.core.backup_manager import list_backups, seal_logins_in_backup

        move = functools.partial(self._moved_if_saved, skip=skip, quiet=True)
        for info in list_backups(self.project_path):
            if info.format != "zip":
                continue
            try:
                seal_logins_in_backup(self.project_path.parent / info.filename, move)
            except Exception:
                log.exception(f"Could not seal the logins in backup {info.filename}")

    def _moved_if_saved(self, raw: dict, *, skip, quiet: bool = False) -> dict:
        """``raw`` (a backup's project file) with its logins moved, or
        ``raw`` itself while one of them is not the one saved here."""
        if not held_as_saved(raw, self.device_logins, skip=skip):
            if not quiet:
                log.warning("The backup taken before the logins moved keeps them: one is not the login saved here")
            return raw
        return move_logins(raw, self.device_logins, typed=False, skip=skip)

    async def _back_up_before_moving_logins(self) -> bool:
        """Back up the project file before the move rewrites it
        (``backup_manager.capture``). False when the backup failed, and the
        file must then stay as it is. With no project file there is nothing
        on disk to keep."""
        from openavc.core.backup_manager import KIND_CHANGE, capture

        try:
            backup = await capture(
                self.project_path, "Before moving device logins out of the project", kind=KIND_CHANGE,
            )
        except Exception:
            log.exception("Could not back up the project, so its device logins stay in it for now")
            return False
        self._logins_backup_at_start = backup
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

    async def _save_plugin_config(self, plugin_id: str, config: dict, own=None) -> None:
        """Save a plugin's own config to the project file (the callback for
        ``PluginAPI.save_config``; ``own`` is its ``OwnSave``).

        A bookkeeping change through the change door: no reconcile, since
        the plugin already runs with what it saved. Its logins move into the
        login file in the door's take-in, and are taken back if the save
        fails; a login it hands back empty while it waits to be entered here
        keeps the marker (``keep_waiting_logins``). The platform's copy of
        the config moves only once the file holds it (``own.taken``). A
        plugin can call ``save_config`` from a hook a change awaits inline
        (``on_start``, ``on_config_changed``, ``stop``, an event handler), so
        a save made inside one is queued to land right after it; any other
        waits its turn (``persist_or_defer_bookkeeping_change``). The log
        masks the logins it saved from now on.
        """
        if not self.project or plugin_id not in self.project.plugins:
            if own is not None:
                own.dropped()
            return
        self._plugin_save_logins[plugin_id] = {
            value for found in find_plugin_logins(plugin_id, config) for value in found.fields.values()
        }
        self._register_project_secrets()
        try:
            await self.persist_or_defer_bookkeeping_change(
                functools.partial(self._put_plugin_config, plugin_id, config, own),
                on_error=functools.partial(self._plugin_config_not_saved, plugin_id, own),
                runtime_write=own.taken if own is not None else None,
            )
        except Exception as e:
            log.error(f"Failed to save plugin config for '{plugin_id}': {e}")
            raise

    def _put_plugin_config(self, plugin_id: str, config: dict, own, project: ProjectConfig) -> None:
        """A plugin's own save written into ``project`` (the project current
        when the save runs), unless a newer change reached the plugin first
        or it left the project."""
        entry = project.plugins.get(plugin_id)
        if entry is None:
            return
        if own is not None and not own.current():
            log.info(
                f"Plugin '{plugin_id}' saved its settings, but newer ones reached it "
                "before that save could be written, so the newer ones are kept"
            )
            return
        # A copy: the plugin's own view and the platform's hold this one.
        written = copy.deepcopy(config)
        entry.config = keep_waiting_logins(plugin_id, written, entry.config, self.device_logins)

    async def _plugin_config_not_saved(self, plugin_id: str, own) -> None:
        """A plugin's own save that failed: nothing it changed ran, so the
        plugin reads its saved config again, and an open IDE is told. The
        frame names its source: a client reads an ``error`` frame that names
        none as the answer to the oldest request it is waiting on."""
        if own is not None:
            own.dropped()
        await self.broadcast_ws({
            "type": "error",
            "source_type": "plugin.save_config",
            "message": f"Failed to save plugin config for '{plugin_id}'",
        })
