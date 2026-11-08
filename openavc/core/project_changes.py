"""How a change to the project reaches disk and the running system while the
engine runs, and the revision every such change is saved under.

Two kinds of change. An **applied** one (``apply_project`` and the doors
built on it) is taken in, saved, compared with the running project, swapped
in and reconciled (``core/project_reconcile.py``), undone on the runtime if the
reconcile fails, and then announced. A **bookkeeping** one is a change the
runtime already has (a device's pending settings cleared, a plugin's own
``save_config``): it is only saved and announced, never reconciled, and the
code that makes one may be running inside a reconcile, which is why it can
be queued (``schedule_bookkeeping_change``).

Both run under ``_reload_lock``, and both mint the next revision here. A
revision is an opaque token, compared for equality and nothing else; see
``_init_project_changes``. Every reader and writer of the bookkeeping queue
and its flush task is in this module, the wait a stop makes for it included.

Mixed into Engine (core/engine.py). Logs under the engine's name.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from openavc.core.client_origin import reload_origin
from openavc.core.project_diff import ProjectDiff, ProjectOrigin
from openavc.core.project_loader import (
    ProjectConfig,
    load_project,
    refresh_crash_copy,
    save_project_async,
)
from openavc.core.typed_fields import trim_device_settings
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


class ProjectRevisionConflictError(Exception):
    """A revision-checked save found a newer project revision on the server."""


async def _call(callback) -> None:
    """Run a callback that may be a plain function or a coroutine function."""
    result = callback()
    if asyncio.iscoroutine(result):
        await result


async def _call_logged(callback, failure: str) -> None:
    """Run an optional callback; a failure is logged, never raised, so the
    callbacks after it still run."""
    if callback is None:
        return
    try:
        await _call(callback)
    except Exception:
        log.exception(failure)


class ProjectChangesMixin:
    """The project's change doors, its revision and its bookkeeping saves."""

    def _init_project_changes(self) -> None:
        # The project's revision, as an OPAQUE token — compared for equality
        # and nothing else. It was a counter from 0, which meant every run
        # handed out the same values: a Builder tab left open across a restart
        # held an `If-Match` the new process agreed with, and its first autosave
        # saved over everything anyone had done since, with no 409 and nothing
        # in the log, because as far as the server could tell it WAS a valid
        # save. The random half makes a token from an earlier run unmatchable;
        # the counter half keeps a log line readable ("the 12th save this run").
        # Never parse it, order it, or persist it — a token restored from a
        # backup would be one this run has already handed out.
        self._boot_id: str = secrets.token_hex(4)
        self._revision_counter: int = 0  # saves committed this run
        self._project_revision: str = f"{self._boot_id}-0"

        # Reload serialization
        self._reload_lock = asyncio.Lock()

        # Deferred bookkeeping persists (see persist_bookkeeping_change):
        # queued mutation, rollback and saved callbacks and the task that flushes them
        # outside the reconcile lock.
        self._bookkeeping_queue: list[tuple[Any, Any, Any]] = []
        self._bookkeeping_task: asyncio.Task | None = None

    async def reload_project(self) -> None:
        """Hot-reload project.avc from disk (LOAD origin — full reconcile).

        The disk read happens under the reload lock so it always sees the
        latest persisted bytes, never a snapshot taken before a concurrent
        save finished.
        """
        async with self._reload_lock:
            new_project = load_project(self.project_path)
            await self._apply_project_locked(new_project, ProjectOrigin.LOAD, persist=False)

    async def apply_project(
        self,
        new_project: ProjectConfig,
        *,
        expected_revision: str | None = None,
        origin: ProjectOrigin = ProjectOrigin.EDIT,
        persist: bool = True,
    ) -> str:
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
            self._check_revision(expected_revision)
            return await self._apply_project_locked(new_project, origin, persist)

    def _check_revision(self, expected_revision: str | None) -> None:
        """Refuse a save made against a revision this run has moved past;
        ``None`` skips the check. Caller holds ``_reload_lock``."""
        if expected_revision is None or expected_revision == self._project_revision:
            return
        if self._revision_counter == 0:
            # Nothing has been saved since this process started, so no
            # other writer can have invalidated the caller's token — it
            # is from an earlier run. "Another session" would send them
            # looking for a colleague who was never there.
            raise ProjectRevisionConflictError(
                "The system restarted since this page loaded the project. "
                "Reload to see the latest changes."
            )
        raise ProjectRevisionConflictError(
            "Project was modified by another session. "
            "Reload to see the latest changes."
        )

    def _new_revision(self) -> str:
        """Mint the next revision token: this run's id plus a save count."""
        self._revision_counter += 1
        return f"{self._boot_id}-{self._revision_counter}"

    async def apply_project_edit(self, mutate) -> str:
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
            return await self._apply_project_locked(new_project, ProjectOrigin.EDIT, persist=True)

    async def reassign_installed_equipment(self, device_id, *, name=None, enabled=None) -> str:
        """Explicitly apply a product's product hardware to existing programming."""
        async with self._reload_lock:
            proposed = self.project.model_copy(deep=True)
            target = next((device for device in proposed.devices if self.installed_equipment.matches(device)), None)
            if target is None:
                raise ValueError("This server has no product hardware to assign.")
            information = self.devices.get_device_info(target.id)
            previous_id = self.installed_equipment.reassign(proposed, device_id, information, self.project_path.parent / "scripts")
            selected = next(device for device in proposed.devices if device.id == device_id)
            values = selected.model_dump()
            if name is not None:
                values["name"] = name
            if enabled is not None:
                values["enabled"] = enabled
            proposed.devices[proposed.devices.index(selected)] = type(selected).model_validate(values)
            return await self._apply_project_locked(
                proposed, ProjectOrigin.EDIT, persist=True,
                reassigned_equipment=previous_id,
            )

    async def _apply_project_locked(
        self,
        new_project: ProjectConfig,
        origin: ProjectOrigin,
        persist: bool,
        reassigned_equipment: str | None = None,
    ) -> str:
        """Body of :meth:`apply_project` once any revision is checked; caller
        holds ``_reload_lock``. The project is taken in (and saved), compared
        with the running one, swapped in and reconciled (all of it undone on
        a failure), and then announced. Returns the new revision."""
        as_given, new_project = await self._take_in(
            new_project, origin, persist, reassigned_equipment
        )
        if origin is ProjectOrigin.LOAD:
            diff = ProjectDiff.all_dirty()
        else:
            # Against the project as it was given: a login typed over the
            # marker leaves the marker in place, and the device still has to
            # reconnect with the new login.
            diff = ProjectDiff.compute(self.project, as_given)
        await self._swap_in(new_project, diff, origin)
        await self._announce_applied(diff, origin)
        return self._project_revision

    async def _take_in(
        self,
        new_project: ProjectConfig,
        origin: ProjectOrigin,
        persist: bool,
        reassigned_equipment: str | None,
    ) -> tuple[ProjectConfig, ProjectConfig]:
        """The project as this system will run it, saved: ``(as_given,
        project)``, where ``as_given`` has the installed equipment composed in
        and ``project`` also has its logins moved out (the same object when
        none moved)."""
        if persist:
            # Every door that saves a device's settings ends here, so this is
            # where a stray space typed around an address or a login comes off
            # (core/typed_fields.py). Before the diff, so a re-save of a padded
            # value reconnects the device on the clean one.
            trim_device_settings(new_project)
        if origin is ProjectOrigin.EDIT:
            self.installed_equipment.validate_edit(self.project, new_project, reassigned=reassigned_equipment)
        loaded_project = new_project
        as_given = self.installed_equipment.compose(new_project, for_load=origin is ProjectOrigin.LOAD)
        # Every project that comes in ends here, so this is where a login it
        # holds in the clear moves into this system's login file and the
        # marker takes its place (core/device_logins). A login somebody typed
        # replaces the saved one; one in a copy (a backup, the library, an
        # import, a push) only fills a gap. No backup is taken here, unlike at
        # start: the copy that came in is still where it came from (the
        # backup restored, the library, the cloud), and its logins are in the
        # file, read back, before the marker is written.
        project = self._move_logins(as_given, typed=origin is ProjectOrigin.EDIT)
        # A file reload can insert host equipment too. Preserve that reference
        # on disk even when the caller did not request another project save.
        if persist or project != loaded_project:
            # save_project stamps the derived dependency lists onto the
            # object in place, so after this await the project matches the
            # bytes on disk exactly — no re-read or re-validation needed.
            await save_project_async(self.project_path, project)
            # The crash copy is the file this save replaced, which held the
            # logins just moved when they came in on disk (a backup restored,
            # a bare push).
            if project is not as_given:
                await asyncio.to_thread(refresh_crash_copy, self.project_path)
        return as_given, project

    async def _swap_in(
        self, new_project: ProjectConfig, diff: ProjectDiff, origin: ProjectOrigin
    ) -> None:
        """Make ``new_project`` the running project under a new revision and
        reconcile to it; a reconcile that fails puts the runtime back on the
        project it ran before, and raises."""
        previous = (
            self.project, self._project_revision, self._revision_counter,
            self._dirty_since_backup,
        )
        # Swapping self.project IS the UI reload: UI dispatch looks elements
        # up at event time, so a UI-only change needs nothing beyond this
        # swap and the ui.definition broadcast below.
        self.project = new_project
        self._apply_project_settings()
        self._project_revision = self._new_revision()
        self._dirty_since_backup = True

        try:
            await self._reconcile(diff, origin)
        except Exception:
            log.error("Project reconcile failed, rolling back to previous "
                      "project state", exc_info=True)
            await self._roll_back(previous, diff)
            raise

    async def _roll_back(self, previous: tuple, diff: ProjectDiff) -> None:
        """The runtime back on the project it ran before a failed reconcile."""
        self.project, revision, counter, dirty = previous
        self._apply_project_settings()
        # The counter goes back too, so it stays a count of COMMITTED
        # saves. The token minted above never left the process (nothing
        # is broadcast or returned until the reconcile succeeds), so the
        # next save may safely mint it again.
        self._project_revision = revision
        self._revision_counter = counter
        self._dirty_since_backup = dirty

        # The new bytes stay on disk deliberately: the file is the source
        # of truth and the user's edit must survive a runtime hiccup.
        # Rollback restores the RUNTIME to the previous project.
        try:
            await self._rollback_reconcile(diff)
        except Exception:
            log.error("Rollback re-sync also failed", exc_info=True)

    async def _announce_applied(self, diff: ProjectDiff, origin: ProjectOrigin) -> None:
        """What a clean apply tells the rest of the system."""
        # Zero the startup-error count so a stale count from the initial
        # start (now fixed) doesn't linger in the store and cloud relay.
        self.state.set("system.startup_errors", 0, source="system")
        if diff.devices_to_sync:
            self._record_push_ports()
        if diff.project_meta:
            self.state.set("system.project_name", self.project.project.name,
                           source="system")
            if self.mdns_advertiser:
                self.mdns_advertiser.update_name(self.project.project.name)
        await self._announce_panel_definition(diff)

        # Notify the Programmer IDE to refetch project data. Always sent:
        # external editors need to learn the new revision even for changes
        # they can't see in the UI definition.
        await self.broadcast_ws({
            "type": "project.reloaded",
            "revision": self._project_revision,
            **reload_origin(),
        })

        await self.events.emit("system.project.reloaded")
        log.info(f"Project applied (origin={origin.value}, "
                 f"revision={self._project_revision})")

    async def _announce_panel_definition(self, diff: ProjectDiff) -> None:
        """Push the new UI definition to connected panels, only when what it
        carries changed (panels re-render on every ui.definition)."""
        if diff.ui:
            # The bindings are new, so an action the runtime could not run
            # before is worth saying again -- somebody editing a project is
            # trying to fix something, and silence reads as having fixed it.
            self.ui_events.forget_undispatched_actions()
        if diff.panel_definition:
            await self.broadcast_ws({
                "type": "ui.definition",
                "ui": self.panel_ui(),
            })

    async def persist_bookkeeping_change(self, mutate, *, on_error=None) -> str:
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
                    await _call(on_error)
                raise
            self._project_revision = self._new_revision()
            revision = self._project_revision
        await self.broadcast_ws({
            "type": "project.reloaded",
            "revision": revision,
            **reload_origin(),
        })
        return revision

    def schedule_bookkeeping_change(self, mutate, *, on_error=None, on_saved=None) -> None:
        """Queue a bookkeeping persist to run outside the reconcile lock.

        Safe to call from anywhere — including event handlers awaited inline
        while ``apply_project`` holds the reconcile lock (device connect
        inside ``_sync_devices``, plugin hooks inside ``_sync_plugins``),
        where awaiting the lock would deadlock the engine.

        Writes queued in the same tick coalesce into one save and one
        revision bump. Each ``mutate(project)`` is applied to whatever
        project object is current at flush time, so a project swap between
        schedule and flush can't resurrect stale state or drop the write.

        ``on_saved`` runs outside the lock only after a successful save.
        A callback failure cannot undo a file already written to disk.
        """
        self._bookkeeping_queue.append((mutate, on_error, on_saved))
        if self._bookkeeping_task is None or self._bookkeeping_task.done():
            self._bookkeeping_task = asyncio.create_task(
                self._flush_bookkeeping()
            )

    async def _flush_bookkeeping(self) -> None:
        """Save what is queued, one batch per pass, until nothing is."""
        while self._bookkeeping_queue:
            batch, revision = await self._save_batch()
            if revision is not None:
                await self._after_batch_saved(batch, revision)

    async def _save_batch(self) -> tuple[list[tuple[Any, Any, Any]], str | None]:
        """Take everything queued and save it as one change; the batch and
        its new revision, or ``None`` when nothing was saved. A failed save
        is undone under the lock, before another change can land."""
        async with self._reload_lock:
            batch = list(self._bookkeeping_queue)
            self._bookkeeping_queue.clear()
            if not self.project:
                return batch, None
            try:
                for mutate, _on_error, _on_saved in batch:
                    mutate(self.project)
                await save_project_async(self.project_path, self.project)
                self._project_revision = self._new_revision()
                return batch, self._project_revision
            except Exception as e:
                log.error(f"Deferred project persist failed: {e}")
                # Undo dependent changes in the reverse order they applied.
                for _mutate, on_error, _on_saved in reversed(batch):
                    await _call_logged(on_error, "Bookkeeping on_error callback failed")
                return batch, None

    async def _after_batch_saved(self, batch: list[tuple[Any, Any, Any]], revision: str) -> None:
        for _mutate, _on_error, on_saved in batch:
            await _call_logged(on_saved, "Bookkeeping on_saved callback failed")
        await self.broadcast_ws({
            "type": "project.reloaded",
            "revision": revision,
        })

    async def _drain_bookkeeping(self, when: str) -> None:
        task = self._bookkeeping_task
        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5)
            except Exception:
                log.warning(f"Bookkeeping persist did not finish {when} shutdown")
