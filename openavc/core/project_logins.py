"""The logins a project holds, from the engine's side: which devices and
plugins wait for somebody to enter one, moving each login the project still
holds in the clear into this system's login file (``core.device_logins``),
the log redaction's copy of the project's own secrets, the sweep of logins
whose owner left the project, and a plugin's own save of its config, which
moves its logins on the way to disk.

A move happens twice in a start: the device logins as the project loads, the
plugin logins once the plugins are loaded and can say which settings hold
one. The project file is backed up before the first move rewrites it, once
per start (``_adopt_moved_logins`` is that rule); a move or a backup that
cannot finish changes nothing.

A plugin calls ``save_config`` from hooks the reconcile awaits while it holds
the reload lock, so ``_save_plugin_config`` saves now when the lock is free and
otherwise defers the write to run right after the reconcile, writing the
config the plugin then runs with.

Mixed into Engine (core/engine.py), which builds the login file in
``_init_logins`` and supplies ``resolved_device_config``, the plugin loader
and the bookkeeping saves (core/project_changes.py). Logs under the engine's
name.
"""

from __future__ import annotations

import asyncio
import copy
import functools

from openavc.core.device_logins import (
    DEVICE,
    SAVED_LOGIN,
    StoreView,
    in_project,
    marked_fields,
    move_logins,
    move_plugin_logins,
    plugin_logins_waiting,
    saved_isc_key,
    saved_logins_of,
)
from openavc.core.project_loader import ProjectConfig, refresh_crash_copy, save_project_async
from openavc.drivers.registry import is_driver_registered
from openavc.utils.log_redaction import get_secret_registry, project_secret_values
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


def _replace_plugin_config(project: ProjectConfig, plugin_id: str, config: dict) -> None:
    """``config`` as the plugin's config in ``project``, if it still has it."""
    if plugin_id in project.plugins:
        project.plugins[plugin_id].config = config


class ProjectLoginsMixin:
    """Where the project's logins live, and who is waiting for one."""

    def _init_logins(self) -> None:
        # The device logins saved on this system (core/device_logins). Read
        # from disk on first use; neither of its files exists until a login
        # is saved.
        from openavc.core.device_logins import device_logins
        from openavc.system_config import get_data_dir

        self.device_logins = device_logins(get_data_dir())
        # Whether this start backed up the project before moving its logins.
        self._logins_backed_up_at_start = False

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
        (a move that could not finish)."""
        values = {v for v in project_secret_values(self.project) if v != SAVED_LOGIN}
        if self.project is None:
            return values
        key = saved_isc_key(self.project, self.device_logins)
        if key:
            values.add(key)
        for found in saved_logins_of(self.project, StoreView(self.device_logins)):
            if found.kind != DEVICE:
                values |= set(found.fields.values())
        return values

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
        self.project = moved
        return True

    async def _move_logins_at_start(self) -> None:
        """The second half of the first-start move, run once the plugins are
        loaded: each plugin login the project still holds in the clear moves
        into the login file, saved with the crash copy refreshed, then every
        Project Library copy's logins (inline: one small file per saved
        project)."""
        if await self._adopt_moved_logins(self._move_logins(self.project, typed=False)):
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
