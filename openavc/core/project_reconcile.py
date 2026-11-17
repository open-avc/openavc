"""How the running system follows the project: one table of stages, each
with its undo, run forward after an applied change and backwards when that
fails; the device and plugin syncs the stages use; and the bring-up worker
that puts the devices a sync registered on the wire.

``RECONCILE_STAGES`` is the reconcile, in order, and the order is the
contract: triggers stop before any stage deletes state keys, variables and
devices come before the scripts that subscribe to their keys at import, and
macros load before the triggers rebuild. ``core/engine_lifecycle.py``'s start
runs the same steps in the same order, so a change to one is checked against
the other. A stage runs when the change touches what it follows, and is
entered in the change's ``ReconcileRun`` before it runs, so a stage that
raises half-way is undone too. ``_rollback_reconcile`` runs the undo of every
stage the run entered, last first, once ``core/project_changes.py`` has put
the previous project back; an undo that fails is logged and the rest still
run. A stage is added with both directions: tests/test_reconcile_stages.py
fails the apply at each one in turn and checks the system is back where it
was.

What an undo cannot give back: a macro run the change cancelled stays
cancelled, and drivers a load found on disk stay registered. The advertised
name and ``system.project_name`` move only once a change is running (its
announcement, in ``core/project_changes.py``), so a failed one never moved
them. Nothing here saves the project, mints a revision or announces.

A reconcile registers devices and returns: nothing here dials one inside the
request that saved the project. ``_schedule_device_bringup`` asks the one
worker for a round, and ``wait_for_device_bringup`` is for a caller that needs
the fleet up.

Mixed into Engine (core/engine.py). Logs under the engine's name.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from openavc.core.device_sync import (
    add_devices,
    delete_device_keys,
    keep_unresolved_at_sync,
    log_failed_sync_add,
    update_changed_devices,
    work_out_each,
)
from openavc.core.project_diff import ProjectDiff, ProjectOrigin
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


@dataclass
class ReconcileRun:
    """One change's pass through the stages: what changed and how it
    arrived, the stages entered so far in order, and what the variable
    stages kept for their undo. Made fresh for each apply."""

    diff: ProjectDiff
    origin: ProjectOrigin
    entered: list[Stage] = field(default_factory=list)
    # The var.* values and the persisted key set from before the change.
    variables_before: tuple[dict[str, Any], frozenset[str] | None] | None = None
    # The values of the variables the change binds, before a binding wrote them.
    bound_before: dict[str, Any] = field(default_factory=dict)


class Stage(NamedTuple):
    """One stage of the reconcile: when a change needs it, and the engine
    methods (each given the run) that take the running system forward and
    back. Named, not bound, so a method replaced on the engine is the one
    that runs."""

    name: str
    needed: Callable[[ReconcileRun], bool]
    forward: str
    undo: str


RECONCILE_STAGES: tuple[Stage, ...] = (
    Stage("driver library", lambda run: run.origin is ProjectOrigin.LOAD,
          "_rescan_driver_library", "_keep_found_drivers"),
    Stage("trigger pause", lambda run: run.diff.requires_trigger_rebuild,
          "_stop_triggers", "_restart_previous_triggers"),
    Stage("macro cancel", lambda run: run.diff.macros or run.diff.device_groups,
          "_cancel_running_macros", "_leave_macros_cancelled"),
    Stage("variables", lambda run: run.diff.variables,
          "_follow_variables", "_restore_variables"),
    Stage("devices", lambda run: run.diff.devices_to_sync,
          "_reconcile_devices", "_resync_devices"),
    Stage("plugins", lambda run: run.diff.plugins,
          "_follow_plugins", "_follow_plugins"),
    Stage("macro definitions", lambda run: run.diff.macros or run.diff.device_groups,
          "_follow_definitions", "_follow_definitions"),
    Stage("trigger restart", lambda run: run.diff.requires_trigger_rebuild,
          "_start_changed_triggers", "_stop_triggers"),
    Stage("variable bindings", lambda run: run.diff.variables,
          "_follow_bindings", "_restore_bindings"),
    Stage("scripts", lambda run: run.diff.scripts,
          "_follow_scripts", "_restore_scripts"),
    Stage("ISC", lambda run: run.diff.isc, "_follow_isc", "_follow_isc"),
)


class ProjectReconcileMixin:
    """The reconcile and its undo, the device and plugin syncs, and the
    device bring-up."""

    def _init_reconcile(self) -> None:
        # Device bring-up after a project change (see
        # _schedule_device_bringup): one worker at a time, with a flag for
        # "another round is wanted" so a save landing mid-bring-up is never
        # lost and never starts a second concurrent pass over the fleet.
        self._bringup_task: asyncio.Task | None = None
        self._bringup_requested = False

    async def _reconcile(self, run: ReconcileRun) -> None:
        """Take the running subsystems to ``self.project``: each stage the
        change needs, in order, entered in ``run`` before it runs."""
        for stage in RECONCILE_STAGES:
            if stage.needed(run):
                run.entered.append(stage)
                await getattr(self, stage.forward)(run)

    async def _rollback_reconcile(self, run: ReconcileRun) -> None:
        """Undo every stage ``run`` entered, last first, against the project
        put back. An undo that fails is logged and the ones before it in the
        table still run: a broken ISC reload must not leave the triggers
        stopped or a deleted variable gone."""
        for stage in reversed(run.entered):
            try:
                await getattr(self, stage.undo)(run)
            except Exception:
                log.exception(f"Rollback could not undo the {stage.name} stage")

    # --- The stages, each forward beside its undo. An undo that is its own
    # forward follows the project again, which by then is the previous one.

    async def _rescan_driver_library(self, run: ReconcileRun) -> None:
        # A whole new project only: pick up out-of-band driver files copied
        # straight into driver_repo/. API-driven installs register their
        # driver themselves, so an edit-save never pays for a library rescan.
        self._load_project_drivers()

    async def _keep_found_drivers(self, run: ReconcileRun) -> None:
        """Nothing to undo: a driver found on disk is the library's, and the
        previous project can use it as well."""

    async def _stop_triggers(self, run: ReconcileRun) -> None:
        # Before any stage that deletes state keys (var.*, device.*,
        # plugin.*), so a state_change trigger cannot fire on a key that is
        # mid-cleanup. Macro changes land here too: the trigger definitions
        # live in the macros.
        await self.triggers.stop()

    async def _restart_previous_triggers(self, run: ReconcileRun) -> None:
        # stop() first: the stop this undoes may have raised half-way, and
        # start() on triggers already started would stack a second set of
        # subscriptions, so every trigger fired twice per change. Never
        # re-fire startup triggers: the previous project was already running.
        await self.triggers.stop()
        await self._restart_triggers(fire_startup=False)

    async def _cancel_running_macros(self, run: ReconcileRun) -> None:
        # Swapping macro/group definitions under a running macro would
        # half-execute it against the new definition (nested macro and
        # group.command steps re-resolve mid-flight).
        await self.macros.cancel_all()

    async def _leave_macros_cancelled(self, run: ReconcileRun) -> None:
        """Nothing to undo: a cancelled macro run is not resumed."""

    async def _follow_variables(self, run: ReconcileRun) -> None:
        persister = self.persister
        run.variables_before = (
            self.state.get_namespace("var."),
            persister.persistent_keys if persister else None,
        )
        self._reconcile_variables()

    async def _restore_variables(self, run: ReconcileRun) -> None:
        """A key the change deleted comes back with its value and a key it
        seeded goes; the persister watches the keys it watched, and
        state.json is written now, so a value the change pruned from the file
        is back in it before a restart could read it."""
        if run.variables_before is None:
            return
        before, persisted = run.variables_before
        now = self.state.get_namespace("var.")
        for var_id in now.keys() - before.keys():
            self.state.delete(f"var.{var_id}")
        for var_id in before.keys() - now.keys():
            self.state.set(f"var.{var_id}", before[var_id], source="system")
        if self.persister and persisted is not None:
            self.persister.restore_keys(persisted)

    async def _reconcile_devices(self, run: ReconcileRun) -> None:
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

    async def _resync_devices(self, run: ReconcileRun) -> None:
        """The devices back on the previous project's, dialed by the
        bring-up like any other change."""
        await self._sync_devices(defer_connect=True)
        self._schedule_device_bringup()

    async def _follow_plugins(self, run: ReconcileRun) -> None:
        await self._sync_plugins()

    async def _follow_definitions(self, run: ReconcileRun) -> None:
        self._load_macro_definitions(
            macros=run.diff.macros, groups=run.diff.device_groups,
        )

    async def _start_changed_triggers(self, run: ReconcileRun) -> None:
        # The rebuild is loss-free: cooldown and cron-dedup baselines are
        # restored from the state store for surviving trigger ids. Startup
        # triggers re-fire only when a whole new project arrives; an
        # edit-save must not re-run power-on automation.
        await self._restart_triggers(fire_startup=run.origin is ProjectOrigin.LOAD)

    async def _follow_bindings(self, run: ReconcileRun) -> None:
        # A binding writes its source's value into the variable at once.
        values = self.state.get_namespace("var.")
        run.bound_before = {
            v.id: values[v.id] for v in self.project.variables
            if v.source_key and v.id in values
        }
        self._rebind_variables()

    async def _restore_bindings(self, run: ReconcileRun) -> None:
        """Each variable the change bound back on the value it had, then the
        previous project's bindings, which write their own sources' values."""
        for var_id, value in run.bound_before.items():
            self.state.set(f"var.{var_id}", value, source="system")
        self._rebind_variables()

    async def _follow_scripts(self, run: ReconcileRun) -> None:
        if not self.scripts:
            return
        if run.origin is ProjectOrigin.LOAD:
            # A new project can replace script FILES while the configs
            # stay identical (library open, backup restore) — only a
            # full reload is safe here.
            self._reload_every_script()
            return
        # Per-script: unchanged scripts keep their handlers and
        # timer phase; a failed re-import keeps the old version.
        for script_id in run.diff.scripts_to_unload:
            self.scripts.unload_script(script_id)
        for cfg in run.diff.scripts_to_reload:
            self._reload_script(cfg)

    async def _restore_scripts(self, run: ReconcileRun) -> None:
        """After a load, every script reloaded from the previous project;
        after an edit, each script it touched back on its previous config,
        or unloaded when the edit added it."""
        if not self.scripts:
            return
        if run.origin is ProjectOrigin.LOAD:
            self._reload_every_script()
            return
        previous = {s.id: s.model_dump() for s in self.project.scripts}
        touched = [*run.diff.scripts_to_unload,
                   *(cfg["id"] for cfg in run.diff.scripts_to_reload)]
        for script_id in touched:
            if script_id in previous:
                self._reload_script(previous[script_id])
            else:
                self.scripts.unload_script(script_id)

    def _reload_every_script(self) -> None:
        self.scripts.reload_scripts([s.model_dump() for s in self.project.scripts])

    def _reload_script(self, cfg: dict) -> None:
        result = self.scripts.reload_script(cfg)
        if result.get("status") == "error":
            log.error(
                f"Script '{cfg.get('id')}' failed to reload: "
                f"{result.get('error')}"
            )

    async def _follow_isc(self, run: ReconcileRun) -> None:
        await self._reload_isc()

    # --- Shared with the start ------------------------------------------

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

    async def _sync_devices(self, *, defer_connect: bool = False) -> None:
        """Sync running devices with project config (add new, remove deleted, update changed).

        ``defer_connect`` registers the added/changed devices without dialing
        them, leaving that to ``DeviceManager.bring_up`` — see
        :meth:`_schedule_device_bringup`.

        Each device is worked out on its own (``core/device_sync.py``): one
        that cannot be is kept by the device manager with why, or, already
        running, keeps the settings it runs with. It is still in the project,
        so it is never removed as one that left. Which devices the system
        has is the device manager's record of each (``device_records``).
        """
        if not self.project:
            return

        self._sweep_device_logins()
        # Build merged configs (device.config + connection table overrides)
        resolved = work_out_each(self.project.devices, self.resolved_device_config)
        project_devices = resolved.configs
        known_ids = {record.device_id for record in self.devices.device_records()}
        project_ids = {d.id for d in self.project.devices}

        for device_id in known_ids - project_ids:
            await self.devices.remove_device(device_id)
            delete_device_keys(self.state, device_id)

        await keep_unresolved_at_sync(self.devices, resolved.failed)
        new_device_ids = [did for did in project_devices if did not in known_ids]
        if new_device_ids:
            await add_devices(
                self.devices, {did: project_devices[did] for did in new_device_ids},
                defer_connect=defer_connect, on_failure=log_failed_sync_add,
            )

        await update_changed_devices(
            self.devices, known_ids & project_devices.keys(), project_devices,
            defer_connect=defer_connect,
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
                    if self.stopping:
                        # On the way down nothing is dialed: the stop is about
                        # to disconnect every device, and a comeback the same
                        # signal cut short has left the simulated ones on
                        # their real addresses.
                        return
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
