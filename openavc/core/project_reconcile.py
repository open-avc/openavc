"""How the running system follows the project: the reconcile by section after
an applied change, the narrower re-sync a failed one is rolled back with, the
device and plugin syncs both use, and the bring-up worker that puts the
devices a sync registered on the wire.

The entry points are ``_reconcile`` and ``_rollback_reconcile``, called by
``core/project_changes.py`` once the new project is the running one; nothing
here saves, mints a revision or announces. The order inside ``_reconcile`` is
the contract (its docstring), and ``core/engine_lifecycle.py``'s start runs the
same steps in the same order, so a change to one is checked against the other.

A reconcile registers devices and returns: nothing here dials one inside the
request that saved the project. ``_schedule_device_bringup`` asks the one
worker for a round, and ``wait_for_device_bringup`` is for a caller that needs
the fleet up.

Mixed into Engine (core/engine.py). Logs under the engine's name.
"""

from __future__ import annotations

import asyncio
import contextlib

from openavc.core.device_sync import (
    add_devices,
    delete_device_keys,
    log_failed_sync_add,
    update_changed_devices,
)
from openavc.core.project_diff import ProjectDiff, ProjectOrigin
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


class ProjectReconcileMixin:
    """The reconcile, the device and plugin syncs, and the device bring-up."""

    def _init_reconcile(self) -> None:
        # Device bring-up after a project change (see
        # _schedule_device_bringup): one worker at a time, with a flag for
        # "another round is wanted" so a save landing mid-bring-up is never
        # lost and never starts a second concurrent pass over the fleet.
        self._bringup_task: asyncio.Task | None = None
        self._bringup_requested = False

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

        triggers_stopped = await self._pause_for_reconcile(diff)
        await self._reconcile_sections(diff)

        if triggers_stopped:
            # The rebuild is loss-free: cooldown and cron-dedup baselines are
            # restored from the state store for surviving trigger ids.
            # Startup triggers re-fire only when a whole new project arrives;
            # an edit-save must not re-run power-on automation.
            await self._restart_triggers(fire_startup=(origin is ProjectOrigin.LOAD))

        if diff.variables:
            self._rebind_variables()

        if diff.scripts and self.scripts:
            self._reconcile_scripts(diff, origin)

        if diff.isc:
            await self._reload_isc()

    async def _pause_for_reconcile(self, diff: ProjectDiff) -> bool:
        """Stop what must not run while the change lands; True when the
        triggers were stopped (and so must be restarted)."""
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
        return triggers_stopped

    async def _reconcile_sections(self, diff: ProjectDiff) -> None:
        """Variables, devices, plugins, then the macro and group definitions."""
        if diff.variables:
            self._reconcile_variables()
        if diff.devices_to_sync:
            await self._reconcile_devices()
        if diff.plugins:
            await self._sync_plugins()
        self._load_macro_definitions(macros=diff.macros, groups=diff.device_groups)

    async def _reconcile_devices(self) -> None:
        # Always the whole sync, never a per-device diff: a bridge edit
        # changes the resolved host/port of every device bound through
        # it, so cross-row effects only surface when every device's
        # resolved config is re-compared. _sync_devices diffs in memory
        # and is cheap.
        #
        # Nothing here dials a device. Registration is in-memory and
        # instant; connecting is a network wait per device, and a save
        # that holds it is a save that can be reported as FAILED by a
        # client timeout after the bytes are already on disk. The
        # bring-up runs after this reconcile answers — see
        # _schedule_device_bringup.
        await self._sync_devices(defer_connect=True)

        # Promote any orphans whose driver is now in the registry.
        # Must follow both the driver load and the device sync.
        await self.devices.retry_all_orphans(defer_connect=True)

        # _sync_devices replaces driver instances, so simulator
        # redirects must be re-applied — and they are re-applied BEFORE
        # the deferred connects, which is why the bring-up owns both in
        # that order rather than the redirect chasing a connect that has
        # already failed against the device's real address.
        self._schedule_device_bringup()

    def _reconcile_scripts(self, diff: ProjectDiff, origin: ProjectOrigin) -> None:
        if origin is ProjectOrigin.LOAD:
            # A new project can replace script FILES while the configs
            # stay identical (library open, backup restore) — only a
            # full reload is safe here.
            self.scripts.reload_scripts(
                [s.model_dump() for s in self.project.scripts]
            )
            return
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

    def _load_macro_definitions(self, *, macros: bool, groups: bool) -> None:
        """Hand the macro engine the project's macros, its device groups, or
        both: at a start, and for each one a change or a rollback touched."""
        if macros:
            self.macros.load_macros([m.model_dump() for m in self.project.macros])
        if groups:
            self.macros.load_groups(
                [g.model_dump() for g in self.project.device_groups]
            )

    def _load_triggers(self) -> None:
        """The trigger definitions, which live in the project's macros."""
        self.triggers.load_triggers([m.model_dump() for m in self.project.macros])

    async def _restart_triggers(self, *, fire_startup: bool) -> None:
        self._load_triggers()
        await self.triggers.start(fire_startup=fire_startup)

    async def _rollback_reconcile(self, diff: ProjectDiff) -> None:
        """Best-effort re-sync after a failed reconcile, scoped to the same
        sections the failed pass may have partially applied, against the
        restored ``self.project``.

        Deliberately narrower than the forward pass (unchanged from the
        pre-reconciler rollback): deleted ``var.*`` keys stay deleted,
        persister keys, scripts, ISC, and mDNS are not restored, and the new
        bytes stay on disk.
        """
        if diff.devices_to_sync:
            await self._sync_devices(defer_connect=True)
            self._schedule_device_bringup()
        if diff.plugins:
            await self._sync_plugins()

        self._load_macro_definitions(macros=diff.macros, groups=diff.device_groups)

        if diff.requires_trigger_rebuild:
            # The forward pass may have failed before or after restarting
            # triggers. stop() first: calling start() on already-started
            # triggers would stack a second set of state/event subscriptions
            # and every trigger would fire twice per change. Never re-fire
            # startup triggers while recovering — the restored project was
            # already running.
            await self.triggers.stop()
            await self._restart_triggers(fire_startup=False)

        if diff.variables:
            self._rebind_variables()

    async def _sync_devices(self, *, defer_connect: bool = False) -> None:
        """Sync running devices with project config (add new, remove deleted, update changed).

        ``defer_connect`` registers the added/changed devices without dialing
        them, leaving that to ``DeviceManager.bring_up`` — see
        :meth:`_schedule_device_bringup`.
        """
        if not self.project:
            return

        self._sweep_device_logins()
        # Build merged configs (device.config + connection table overrides)
        project_devices = {d.id: self.resolved_device_config(d) for d in self.project.devices}
        running_ids = set(self.devices.get_device_configs().keys())
        project_ids = set(project_devices.keys())

        for device_id in running_ids - project_ids:
            await self.devices.remove_device(device_id)
            delete_device_keys(self.state, device_id)

        new_device_ids = list(project_ids - running_ids)
        if new_device_ids:
            await add_devices(
                self.devices, {did: project_devices[did] for did in new_device_ids},
                defer_connect=defer_connect, on_failure=log_failed_sync_add,
            )

        await update_changed_devices(
            self.devices, running_ids & project_ids, project_devices, defer_connect=defer_connect,
        )

    async def _sync_plugins(self) -> None:
        """Sync running plugins with project config on hot-reload."""
        if not self.project:
            return

        old_plugins = self.plugin_loader.get_known_plugin_ids()
        new_plugins = set(self.project.plugins.keys())

        for plugin_id in old_plugins - new_plugins:
            await self._drop_plugin(plugin_id)
        for plugin_id in new_plugins - old_plugins:
            await self._add_plugin(plugin_id, self.project.plugins[plugin_id])
        for plugin_id in old_plugins & new_plugins:
            await self._follow_plugin_entry(plugin_id, self.project.plugins[plugin_id])

    async def _drop_plugin(self, plugin_id: str) -> None:
        """A plugin the project no longer has: stopped, and forgotten."""
        if self.plugin_loader.is_running(plugin_id):
            await self.plugin_loader.stop_plugin(plugin_id)
        self.plugin_loader.remove_plugin_tracking(plugin_id)

    async def _add_plugin(self, plugin_id: str, entry) -> None:
        """A plugin the project gained: started, if its entry enables it."""
        if entry.enabled:
            await self.plugin_loader.start_plugin(plugin_id, entry.config)

    async def _follow_plugin_entry(self, plugin_id: str, entry) -> None:
        """A plugin the project still has: stopped, started, or handed its
        new config, as its entry now says."""
        was_running = self.plugin_loader.is_running(plugin_id)
        if was_running and not entry.enabled:
            await self.plugin_loader.stop_plugin(plugin_id)
        elif not was_running and entry.enabled:
            await self.plugin_loader.start_plugin(plugin_id, entry.config)
        elif was_running and self.plugin_loader.config_differs(plugin_id, entry.config):
            await self._hand_plugin_config(plugin_id, entry.config)

    async def _hand_plugin_config(self, plugin_id: str, config: dict) -> None:
        # Same path as every other config write (REST, cloud AI):
        # try the plugin's on_config_changed hot-apply hook first,
        # fall back to a restart.
        outcome = await self.plugin_loader.restart_or_apply(plugin_id, config)
        if outcome == "start_failed":
            log.error(
                f"Plugin '{plugin_id}' failed to restart after a "
                f"config change and is stopped"
            )

    def _schedule_device_bringup(self) -> None:
        """Ask for a device bring-up round, starting the worker if it is idle.

        The reconcile registers devices and returns; this is what actually
        puts them on the wire, outside the request that saved the project.
        Deliberately fire-and-forget from the caller's point of view — a
        device that will not connect is normal, and is reported through its
        ``offline_reason`` and the reconnect loop like any other.
        """
        self._bringup_requested = True
        if self._bringup_task is None or self._bringup_task.done():
            self._bringup_task = asyncio.ensure_future(self._device_bringup_worker())

    async def _device_bringup_worker(self) -> None:
        """Run bring-up rounds until nothing more is asked for.

        One worker, never two: a second save landing mid-round sets the flag
        instead of starting a concurrent pass, so the fleet is never dialed
        twice at once. The flag is consumed and the loop re-checked with no
        await in between, so a request cannot slip past the exit.
        """
        try:
            while self._bringup_requested:
                self._bringup_requested = False
                try:
                    # The simulator redirect first, so a device registered by
                    # this save dials 127.0.0.1 on its FIRST attempt rather
                    # than failing against its real address and being fixed up
                    # afterwards. At startup the first round brings back the
                    # simulation that was on when the server stopped instead.
                    if self.simulation.resume_pending:
                        await self.simulation.resume()
                    elif self.simulation.active:
                        await self.simulation.sync()
                    await self.devices.bring_up()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Device bring-up after a project change failed")
        finally:
            # Cleared synchronously as the coroutine returns, so a
            # _schedule_device_bringup that runs after the loop condition was
            # last read starts a fresh worker instead of setting a flag
            # nothing will ever look at again.
            self._bringup_task = None

    async def _stop_bringup(self) -> None:
        # Stop bringing devices up. Nothing is lost: a device that never got
        # dialed is registered and offline, which is what shutdown makes of
        # every device anyway, and _stop_inner disconnects the rest.
        self._bringup_requested = False
        bringup = self._bringup_task
        if bringup is not None:
            bringup.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await bringup
            # A task cancelled before it ever ran never reaches its own
            # cleanup, so clear the slot here rather than relying on it.
            self._bringup_task = None

    async def wait_for_device_bringup(self) -> None:
        """Block until no bring-up round is pending or running.

        For callers that genuinely need the fleet up before they continue —
        starting simulation, and tests that assert on connection state after
        a save.
        """
        # ``done()`` as well as ``None``: a task cancelled before its first
        # step never runs its own cleanup, so the attribute can outlive it.
        while (task := self._bringup_task) is not None and not task.done():
            await asyncio.wait({task})
