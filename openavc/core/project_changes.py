"""How a change to the project reaches disk and the running system while the
engine runs, and the revision every such change is saved under.

THE take-in is ``_take_in``: every project that comes in while the engine
runs passes it before it runs or is saved. Its typed values are trimmed, it
is checked as a valid project (``ProjectInvalidError`` when the file it
would write would not load or holds a value past a bound), an edit is held
to the installed equipment and to the words this version knows, the
installed equipment is composed in, and its logins move into the login file.
An edit is also refused for what it adds to the pages that they may not hold
(``ui/page_edits.issues_an_edit_makes``).
Every refusal comes before that move, so a refused change has filed nothing.

THE change door is ``_change_locked``: a change given as ``mutate(project)``
runs under ``_reload_lock`` against a copy of the project current at that
moment, never one taken before the lock, then the take-in and the save.
``apply_project_edit`` and both bookkeeping saves are its callers; the
whole-project doors (``apply_project``, ``apply_named_save``,
``replace_project``, ``reassign_installed_equipment``) end in the same
take-in. What a replace keeps of the project it replaces (the cloud push's
rollback copy, a backup) is taken under the lock before anything changes,
never before the lock, so an edit saved while the replace waited is in it.
A change built elsewhere on a read of the project (the cloud's template
merge) reads it with ``read_saved_project`` (``core/project_revision.py``)
and is sent back with that revision as its expected one, so a save made in
between refuses it.
A file beside the project that a change writes or removes with it (a
script's ``.py``) is the door's too (``core/change_files.py``): written
inside the change and put back when it is not applied, removed only once
the project file holds it.

Two kinds of change. An **applied** one (``apply_project``, the door's edits,
and the doors built on them) is taken in, compared with the running project,
swapped in, reconciled (``core/project_reconcile.py``) and only then saved,
so the file always holds what runs: a failure in the swap, the reconcile or
the write (a full disk) puts the runtime and the logins the change filed
back, with the file not written, and raises ``ProjectNotAppliedError``. Then
it is announced; the announcement cannot turn a change that is already
running into a failure. What an applied change did is its ``ApplyOutcome``,
THE answer every door gives from: the revision it runs under and each part
of it the system could not run (a device, a plugin, a script), with why. A
part that failed is said by its own surface until it is fixed, never as a
count a later change resets. The words every door answers an outcome in, the
change that was not applied, a conflict and a refusal included, are
``api/error_messages.py``'s. A **bookkeeping** one is a change the runtime
takes without a reconcile (a device's pending settings cleared, a plugin's
own ``save_config``, a setup action's settings, a setting the device fills
in): the door without the reconcile, swapped in and written as an edit is
and put back when the write fails, and announced. The running system's own
half of it (a device's settings merged into the live driver, the config the
platform holds for a plugin) is made once the file holds it, under the same lock (the ``runtime_write`` of
``persist_bookkeeping_change`` and of ``schedule_bookkeeping_change``), so
nothing is taken live first and nothing needs undoing when a save fails.
The code that makes one may be running inside a change that holds the
lock (a plugin hook or an event handler it awaits), which is why it can be
queued (``schedule_bookkeeping_change``); THE span that queues a plugin's
own save is ``_running_hooks``, and outside it the save waits its turn.

Both run under ``_reload_lock``, both mint the next revision, and both
make the next timed backup due once the file holds them. THE revision token
and the read that carries it are ``core/project_revision.py``'s: a change
mints the next one there and puts the one before back here. Every reader and writer of the bookkeeping queue
and its flush task is in this module, the wait a stop makes for it included.

THE announcement of a saved change is ``_announce_saved``, the same from
every door: the revision to open tabs (``project.reloaded``, naming the tab
that made the change when one did; a queued change names none), then
``system.project.reloaded`` on the event bus, each logged on a failure,
never raised, since the file already holds the change. The bus is not told
of a save made while a handler of that event runs (``_an_echo``), which
would hear its own save and could save again without end, nor once the
engine is stopping.

Mixed into Engine (core/engine.py). Logs under the engine's name.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from typing import Any

from pydantic import ValidationError

from openavc.core import backup_manager, project_identity, project_update
from openavc.core.change_files import ChangeFiles
from openavc.core.client_origin import reload_origin
from openavc.core.device_logins import LoginChange
from openavc.core.project_diff import ProjectDiff, ProjectOrigin
from openavc.core.project_loader import (
    ProjectConfig,
    load_project,
    refresh_crash_copy,
    save_project_async,
)
from openavc.core.project_reconcile import FailedPart, ReconcileRun
from openavc.core.project_replace import ProjectReplaceMixin
from openavc.core.project_revision import (  # noqa: F401 -- ProjectRevisionConflictError is imported from here
    ProjectRevisionConflictError,
    ProjectRevisionMixin,
)
from openavc.core.project_update import MigrationNotes
from openavc.core.saved_values import refuse_new_words
from openavc.core.typed_fields import trim_device_settings
from openavc.ui.page_edits import refuse_issues_an_edit_makes
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


class ProjectInvalidError(ValueError):
    """A change whose result is not a valid project: the file it would write
    would not load, or holds a value past a bound that a save refuses (a
    load repairs one). Refused before the change is taken in, so nothing
    has changed. ``cause`` is the models' own ``ValidationError``; the
    refusal names each field it found and never a value, which may be a
    login (``api/error_messages.describe_change_failure``). Its own text
    names them the same way, for the log line of a queued change, which
    has no door to answer it."""

    def __init__(self, cause: ValidationError) -> None:
        from openavc.api.errors import validation_error_lines

        problems = "; ".join(validation_error_lines(cause.errors()))
        super().__init__(f"The change leaves the project invalid: {problems}")
        self.cause = cause


class ProjectNotAppliedError(Exception):
    """A change the running system was given and could not take: the swap,
    the reconcile or the file's write failed, and everything was put back,
    so the project before it is still running and the apply wrote no file.
    A reload, which came from the file, leaves the file holding that change,
    which is then not what runs; a whole project with its files
    (``replace_with``) puts the previous files back. ``cause`` is what failed (also the
    ``__cause__``); every door
    answers it in one wording (``api/error_messages.describe_change_failure``),
    never with the cause's own text. A refusal of the change itself (a word
    this version does not know, the installed equipment) is raised as it is,
    before the change is taken in."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"Project change not applied: {type(cause).__name__}: {cause}")
        self.cause = cause


@dataclass(frozen=True)
class ApplyOutcome:
    """What an applied change did: it runs under ``revision``, and
    ``failed`` names each part of it the system could not run, in the
    order the reconcile met them (empty when every part runs).
    ``plugin_moves`` is what its reconcile did to each plugin it moved, as
    ``(plugin id, word)`` (``ReconcileRun.plugin_moves``): the answer of a
    door that saves a plugin's config (``plugin_move``)."""

    revision: str
    failed: tuple[FailedPart, ...] = ()
    plugin_moves: tuple[tuple[str, str], ...] = ()

    def plugin_move(self, plugin_id: str) -> str | None:
        """What this change did to ``plugin_id``: ``hot_applied``,
        ``restarted``, ``started``, ``start_failed`` or ``stopped``; None
        when it left the plugin as it was."""
        return dict(self.plugin_moves).get(plugin_id)


@dataclass(frozen=True)
class KeepReplaced:
    """What a caller of ``replace_project`` keeps of the project it
    replaces, taken under the lock before anything changes, so it is the
    project replaced, an edit saved while the replace waited included:
    ``before`` (a plain or coroutine function: the cloud push's rollback
    copy, which may refuse the push by raising), then a backup named
    ``backup`` (``backup_manager.capture``, of the kind taken before a
    change). A failure in either leaves the
    running project and its file as they were, and is raised as it is: a
    backup that could not be saved as ``BackupFailed``."""

    before: Any = None
    backup: str | None = None

    async def take(self, project_path) -> None:
        if self.before is not None:
            await _call(self.before)
        if self.backup is not None:
            await backup_manager.capture(project_path, self.backup, kind=backup_manager.KIND_CHANGE)


@dataclass(frozen=True)
class _NamedSave:
    """The save that made the current revision, by the operation id its
    sender gave it, and what it did."""

    operation: str
    outcome: ApplyOutcome


@dataclass(frozen=True)
class _TakenIn:
    """A project change as this system will run it, before it runs:
    ``as_given`` has the installed equipment composed in, ``project`` also
    has its logins moved out (the same object when none moved), ``logins``
    is what that move filed in the login file (taken back if the change does
    not run), and ``write`` is whether the file is saved once it runs."""

    as_given: ProjectConfig
    project: ProjectConfig
    logins: LoginChange
    write: bool


@dataclass(frozen=True)
class _Queued:
    """A bookkeeping change waiting for the lock: its ``mutate``, the
    running system's own half of it (``runtime_write``, made once the file
    holds the change, under the lock), what runs when the save fails
    (``on_error``) or once it is done (``on_saved``, outside the lock), and
    whether it was queued by a handler of a saved change's announcement
    (``echo``, ``_an_echo``)."""

    mutate: Any
    on_error: Any = None
    on_saved: Any = None
    runtime_write: Any = None
    echo: bool = False


class _ReloadEmit:
    """One ``system.project.reloaded`` emit while its handlers run. Emptied
    when it ends: a task a handler started, which keeps the context, makes
    a change of its own once the handler is done (``_an_echo``)."""

    __slots__ = ("running",)

    def __init__(self) -> None:
        self.running = True


_announcing: ContextVar[_ReloadEmit | None] = ContextVar("openavc_announcing_a_save", default=None)


def _an_echo() -> bool:
    """Whether a save made now is a handler's answer to a saved change's
    ``system.project.reloaded``, made while that emit runs."""
    emit = _announcing.get()
    return emit is not None and emit.running


def _check_valid(project: ProjectConfig) -> None:
    """Raise ``ProjectInvalidError`` unless the models take ``project`` back
    as a load takes the file it is saved as. The models have no
    ``validate_assignment``, so a change made by setting a field is checked
    here and nowhere else. A check only: the project kept is the one given.
    A project that cannot even be written out as JSON is left to the write,
    which fails it as any failed write fails."""
    try:
        written = project.model_dump(mode="json")
    except Exception:
        return
    try:
        ProjectConfig.model_validate(written)
    except ValidationError as invalid:
        raise ProjectInvalidError(invalid) from invalid


def _each(mutates) -> Any:
    """One mutate that runs each of ``mutates`` in turn: a queued batch."""
    def run(project: ProjectConfig) -> None:
        for mutate in mutates:
            mutate(project)
    return run


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


class ProjectChangesMixin(ProjectRevisionMixin, ProjectReplaceMixin):
    """The project's change doors, its revision and its bookkeeping saves.
    A whole other project with its files (a restore, a library copy, a
    blank project, an imported project file) comes in through
    ``replace_with`` (``core/project_replace``)."""

    def _init_project_changes(self) -> None:
        self._init_project_revision()
        # The last save that came with an operation id: one slot, in memory,
        # so the same save sent again after its answer was lost is recognised
        # while it is still the current revision (_resent_outcome).
        self._last_named_save: _NamedSave | None = None

        # Reload serialization
        self._reload_lock = asyncio.Lock()
        self._init_project_replace()
        # The saved values of a whole project replacing the running one,
        # while it loads (core/project_replace): the variables stage seeds
        # from them.
        self._values_coming_in: dict | None = None
        # How many holders of the lock are running hooks under it now
        # (_running_hooks): a bookkeeping change made meanwhile is queued.
        self._hook_runs = 0

        # Deferred bookkeeping persists (see schedule_bookkeeping_change):
        # the changes waiting for the lock and the task that flushes them.
        self._bookkeeping_queue: list[_Queued] = []
        self._bookkeeping_task: asyncio.Task | None = None

    @contextmanager
    def _running_hooks(self):
        """THE span in which a bookkeeping change is queued rather than
        awaited (:meth:`persist_or_defer_bookkeeping_change`): the holder of
        ``_reload_lock`` is running code that can make one, a plugin hook or
        an event handler it awaits, so a change that waited for the lock
        would wait for itself. An applied change raises it from the moment
        it waits for the lock to the end of its run (the reconcile, its
        undo, the announcement: :meth:`_lock_for_a_change`) and the stop for
        its wait and its teardown. A bookkeeping save runs none, so a change
        made beside one waits its turn and hears its own failure."""
        self._hook_runs += 1
        try:
            yield
        finally:
            self._hook_runs -= 1

    @asynccontextmanager
    async def _lock_for_a_change(self):
        """``_reload_lock`` for an applied change, inside
        :meth:`_running_hooks` from the moment it starts to wait. A plugin's
        save made while the change waits is queued behind it, never parked
        on the lock ahead of a holder that may itself wait on that plugin;
        the lock is first come, first served, so a save parked before the
        change began to wait is written before it."""
        with self._running_hooks():
            async with self._reload_lock:
                yield

    async def reload_project(self) -> None:
        """Hot-reload project.avc from disk (LOAD origin — full reconcile).

        The disk read happens under the reload lock so it always sees the
        latest persisted bytes, never a snapshot taken before a concurrent
        save finished.
        """
        async with self._lock_for_a_change():
            new_project = load_project(
                self.project_path, keep_notes=True, running=self.project.project if self.project else None,
            )
            await self._apply_project_locked(new_project, ProjectOrigin.LOAD, persist=False)
            project_update.publish(self.state, self.project_path.parent)

    def keep_update_notes(self, notes: MigrationNotes, *, replace: bool) -> None:
        """Keep what a migration changed in a project that came in, for the
        Programmer's notice (``core/project_update.py``), and publish it.
        ``replace`` is for a project that replaced the running one: the
        record then describes it alone, or is cleared when it needs none."""
        if not (replace or notes):
            return
        keep = project_update.replace if replace else project_update.record
        keep(self.project_path.parent, notes)
        project_update.publish(self.state, self.project_path.parent)

    async def apply_project(
        self,
        new_project: ProjectConfig,
        *,
        expected_revision: str | None = None,
        origin: ProjectOrigin = ProjectOrigin.EDIT,
        persist: bool = True,
    ) -> ApplyOutcome:
        """The one way a project change enters the engine.

        Checks optimistic concurrency (``expected_revision=None`` skips the
        check), swaps the in-memory project, bumps the revision, reconciles
        only the subsystems the change actually touches, and then saves the
        bytes. Returns its outcome. A change refused before it is taken in
        raises its own refusal; one the running system could not take raises
        :class:`ProjectNotAppliedError`, with the previous project running
        and the file as it was.

        The revision compare must happen under the same lock that increments
        ``_project_revision``. Checked outside it, two concurrent saves can
        both pass the compare and the loser's edit is silently overwritten —
        the exact race the 409 contract exists to prevent. Raises
        :class:`ProjectRevisionConflictError` on mismatch.
        """
        async with self._lock_for_a_change():
            self._check_revision(expected_revision)
            return await self._apply_project_locked(new_project, origin, persist)

    async def apply_named_save(
        self, new_project: ProjectConfig, expected_revision: str | None, operation: str | None,
    ) -> ApplyOutcome:
        """An edit applied as :meth:`apply_project` applies it, named by
        ``operation`` (the Programmer's id for one save). The same save sent
        again under the same id, because its answer was lost, is answered
        with what it did rather than applied twice or refused as another
        session's change (:meth:`_resent_outcome`)."""
        async with self._lock_for_a_change():
            resent = self._resent_outcome(operation)
            if resent is not None:
                return resent
            self._check_revision(expected_revision)
            outcome = await self._apply_project_locked(new_project, ProjectOrigin.EDIT, True)
            self._last_named_save = _NamedSave(operation, outcome) if operation else None
            return outcome

    def _resent_outcome(self, operation: str | None) -> ApplyOutcome | None:
        """What the save named ``operation`` did, when it is the one that
        made the current revision; ``None`` otherwise. Once anything else has
        moved the revision, the resend is checked as any other save is: an
        answer of "saved" with the newer revision would let its sender save
        over that change without seeing it. Caller holds ``_reload_lock``."""
        last = self._last_named_save
        current = (operation, self._project_revision)
        if last is None or (last.operation, last.outcome.revision) != current:
            return None
        log.info("A save sent again after its answer was lost was already applied")
        return last.outcome

    async def replace_project(
        self, new_project: ProjectConfig, update_notes: MigrationNotes, *,
        expected_revision: str | None = None, keep: KeepReplaced | None = None,
    ) -> ApplyOutcome:
        """A project that replaces the running one and keeps the files
        beside it, the saved values and the running project's stamp
        (``core/project_identity``): a whole project pushed from the cloud,
        which brings no files. Applied as a load and saved; once that has
        worked, the update notice is what its migration changed, or none
        (``keep_update_notes``). What the caller keeps of the project it
        replaces (``keep``) is taken under the lock once the revision is
        checked, before anything changes. A project that comes with its
        files, or with none of its own (an imported project file), comes
        through ``replace_with``."""
        async with self._lock_for_a_change():
            self._check_revision(expected_revision)
            if keep is not None:
                await keep.take(self.project_path)
            project_identity.keep_running(self.project, new_project)
            outcome = await self._apply_project_locked(new_project, ProjectOrigin.LOAD, persist=True)
            self.keep_update_notes(update_notes, replace=True)
            return outcome

    async def apply_project_edit(self, mutate, *, files: ChangeFiles | None = None) -> ApplyOutcome:
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
        nothing applied. Returns its outcome. The change door
        (:meth:`_change_locked`), reconciled; ``files`` is what ``mutate``
        writes and removes beside the project with it
        (``core/change_files.py``).
        """
        async with self._lock_for_a_change():
            return await self._change_locked(mutate, reconcile=True, files=files)

    async def _change_locked(
        self, mutate, *, reconcile: bool, files: ChangeFiles | None = None,
    ) -> ApplyOutcome:
        """THE change door: ``mutate`` run against a copy of the project
        current now, under ``_reload_lock`` (the caller holds it), then the
        one take-in (``_take_in``) and the save. With ``reconcile``, an
        edit: the running system is reconciled to it before the file is
        written, and the outcome names each part it could not run
        (:meth:`_apply_project_locked`). Without, a bookkeeping change the
        runtime already has: swapped in and written as an edit is, with no
        reconcile (:meth:`_keep_locked`). Either way it runs under a
        new revision with the next backup due, and a refusal or a failure
        leaves the running project, the file and the login file as they
        were, raising it; the files ``mutate`` wrote beside the project
        (``files``) are put back too, and the ones it removes go only once
        the project file holds the change."""
        with (files or ChangeFiles()).following():
            proposed = self.project.model_copy(deep=True)
            mutate(proposed)
            if reconcile:
                return await self._apply_project_locked(proposed, ProjectOrigin.EDIT, persist=True)
            return await self._keep_locked(proposed)

    async def _keep_locked(self, proposed: ProjectConfig) -> ApplyOutcome:
        """The change door's end for a bookkeeping change: ``proposed`` taken
        in as an edit is, made the running project under a new revision as
        an edit is, and written, with the next backup due once the file
        holds it. Swapped in before the write, as an edit is: a change made
        in place while the file is written lands on the project that is
        kept. A failed write puts the project
        and the revision back, takes back the logins it filed and raises.
        Caller holds ``_reload_lock``."""
        taken = self._take_in(proposed, ProjectOrigin.EDIT, True, None)
        previous = (self.project, self._project_revision, self._revision_counter)
        self.project = taken.project
        self._project_revision = self._new_revision()
        try:
            await save_project_async(self.project_path, taken.project)
        except Exception:
            self.project, self._project_revision, self._revision_counter = previous
            self._take_back_logins(taken.logins)
            raise
        self._dirty_since_backup = True
        # The file holds the change, so what follows is logged, never raised.
        await _call_logged(
            self._apply_project_settings,
            "Project saved, but its settings could not be put on the running system",
        )
        await self._refresh_crash_copy(taken)
        return ApplyOutcome(self._project_revision)

    async def reassign_installed_equipment(self, device_id, *, name=None, enabled=None) -> ApplyOutcome:
        """Explicitly apply a product's product hardware to existing programming."""
        async with self._lock_for_a_change():
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
    ) -> ApplyOutcome:
        """Body of :meth:`apply_project` once any revision is checked; caller
        holds ``_reload_lock``. The project is taken in, compared with the
        running one, swapped in, reconciled and saved (all of it undone on a
        failure, the file never written), and then announced. Returns the
        outcome, which a failure in the announcing no longer changes: the
        change is running. Its plugin hooks and event handlers run inside
        :meth:`_running_hooks`, raised by the door (:meth:`_lock_for_a_change`)."""
        taken = self._take_in(new_project, origin, persist, reassigned_equipment)
        if origin is ProjectOrigin.LOAD:
            diff = ProjectDiff.all_dirty()
        else:
            # Against the project as it was given: a login typed over the
            # marker leaves the marker in place, and the device still has to
            # reconnect with the new login.
            diff = ProjectDiff.compute(self.project, taken.as_given)
        run = await self._swap_in(taken, diff, origin)
        outcome = ApplyOutcome(
            self._project_revision, tuple(run.failed), tuple(run.plugin_moves.items()),
        )
        await self._announce_applied(diff, origin, outcome)
        return outcome

    def _take_in(
        self,
        new_project: ProjectConfig,
        origin: ProjectOrigin,
        persist: bool,
        reassigned_equipment: str | None,
    ) -> _TakenIn:
        """The project as this system will run it, not yet running or saved
        (``_TakenIn``): its typed values trimmed, checked as a valid project,
        the installed equipment composed in and its logins moved into the
        login file, which its devices dial with. Every refusal comes before
        the move, so a refused change has filed nothing."""
        if persist:
            # Every door that saves a device's settings ends here, so this is
            # where a stray space typed around an address or a login comes off
            # (core/typed_fields.py). Before the diff, so a re-save of a padded
            # value reconnects the device on the clean one.
            trim_device_settings(new_project)
        _check_valid(new_project)
        if origin is ProjectOrigin.EDIT:
            self.installed_equipment.validate_edit(self.project, new_project, reassigned=reassigned_equipment)
            # A word a later release saved stays; one this edit adds is refused.
            refuse_new_words(self.project, new_project)
            refuse_issues_an_edit_makes(self.project, new_project)
            # An edit is still the project it edits, whatever stamp it names.
            project_identity.keep_running(self.project, new_project)
        as_given = self.installed_equipment.compose(new_project, for_load=origin is ProjectOrigin.LOAD)
        # Every project that comes in ends here, so this is where a login it
        # holds in the clear moves into this system's login file and the
        # marker takes its place (core/device_logins). A login somebody typed
        # replaces the saved one; one in a copy (a backup, the library, an
        # import, a push) only fills a gap. No backup is taken here, unlike at
        # start: the copy that came in is still where it came from (the
        # backup restored, the library, the cloud), and its logins are in the
        # file, read back, before the marker is written. Before the swap, as
        # the devices dial with them; taken back if the change does not run.
        project, logins = self._move_logins_recorded(as_given, typed=origin is ProjectOrigin.EDIT)
        # A file reload can insert host equipment too. Preserve that reference
        # on disk even when the caller did not request another project save.
        return _TakenIn(as_given, project, logins, write=persist or project != new_project)

    async def _swap_in(
        self, taken: _TakenIn, diff: ProjectDiff, origin: ProjectOrigin
    ) -> ReconcileRun:
        """Make ``taken.project`` the running project under a new revision,
        reconcile to it, and then save it; the reconcile's run, with the
        parts it could not run. The file is written last, so it always holds
        what runs. Everything from the swap on is inside the guard: a failure
        in the swap, the settings, the revision, the reconcile or the write
        (a full disk) puts the runtime and the login file back on the project
        before, with the file not written, and raises
        :class:`ProjectNotAppliedError` from it."""
        previous = (
            self.project, self._project_revision, self._revision_counter,
            self._dirty_since_backup,
        )
        # Fresh for this apply: a swap that fails before the reconcile has
        # entered no stage, so the rollback undoes none.
        run = ReconcileRun(diff, origin)
        try:
            # Swapping self.project IS the UI reload: UI dispatch looks
            # elements up at event time, so a UI-only change needs nothing
            # beyond this swap and the ui.definition broadcast after it.
            self.project = taken.project
            self._apply_project_settings()
            self._project_revision = self._new_revision()
            await self._reconcile(run)
            if taken.write:
                # save_project stamps the derived dependency lists onto the
                # running project in place, so after this await it matches
                # the bytes on disk exactly.
                await save_project_async(self.project_path, taken.project)
            # Once the file holds the change: the timed backup does not take
            # the reload lock, and one taken mid-apply copies the file before.
            self._dirty_since_backup = True
        except Exception as failure:
            log.error("Project apply failed, rolling back to previous "
                      "project state", exc_info=True)
            await self._roll_back(previous, run, taken.logins)
            raise ProjectNotAppliedError(failure) from failure
        if taken.write:
            await self._refresh_crash_copy(taken)
        return run

    async def _refresh_crash_copy(self, taken: _TakenIn) -> None:
        """Once ``taken`` is written: when it moved logins out, the crash
        copy is the file that write replaced, which held them when they came
        in on disk (a backup restored, a bare push), so it is made the file
        as now saved. The save has landed, so a failure here is logged."""
        if taken.project is taken.as_given:
            return
        await _call_logged(
            partial(asyncio.to_thread, refresh_crash_copy, self.project_path),
            "Project saved, but its crash copy still holds the logins it moved",
        )

    async def _roll_back(self, previous: tuple, run: ReconcileRun, logins: LoginChange) -> None:
        """The runtime back on the project it ran before a failed swap,
        reconcile or write. The project, revision, count and backup flag go
        back first, so a failure in what follows cannot leave the new
        revision on the old project; then the settings and the logins the
        change filed, then each reconcile stage the change entered is
        undone, last first (``core/project_reconcile.py``). Each is logged on
        a failure and never raises, so the caller hears the original one."""
        self.project, revision, counter, dirty = previous
        # The counter goes back too, so it stays a count of COMMITTED
        # saves. The token minted above never left the process (nothing
        # is broadcast or returned until the change is saved), so the
        # next save may safely mint it again.
        self._project_revision = revision
        self._revision_counter = counter
        self._dirty_since_backup = dirty
        await _call_logged(
            self._apply_project_settings,
            "Rollback could not put the previous project's settings back",
        )
        # Before the stages: the devices' undo dials them again, with the
        # logins in the file.
        self._take_back_logins(logins)
        # Nothing was written: the file still holds the project put back.
        await _call_logged(
            partial(self._rollback_reconcile, run), "Rollback re-sync also failed",
        )

    async def _announce_applied(
        self, diff: ProjectDiff, origin: ProjectOrigin, outcome: ApplyOutcome,
    ) -> None:
        """What an applied change tells the rest of the system. The change is
        running under its new revision by now, so nothing here may answer it
        as a failure: a part that raises is logged, and the parts after it
        still go out (an open IDE still learns the revision when the panel
        definition cannot be built). The parts it could not run are named in
        one warning; each is published by its own surface already."""
        parts = (
            (partial(self._publish_applied, diff), "publishing its state"),
            (partial(self._announce_panel_definition, diff), "sending the panel definition"),
        )
        for part, what in parts:
            await _call_logged(part, f"Project applied, but {what} failed")
        await self._announce_saved(outcome.revision, reload_origin(), echo=_an_echo())
        applied = f"Project applied (origin={origin.value}, revision={outcome.revision})"
        if not outcome.failed:
            log.info(applied)
            return
        named = "; ".join(
            f"{p.kind} '{p.part_id}' ({p.reason}): {p.detail}" for p in outcome.failed
        )
        count = len(outcome.failed)
        log.warning(f"{applied}, but {count} part{'s are' if count != 1 else ' is'} not running: {named}")

    def _publish_applied(self, diff: ProjectDiff) -> None:
        """The state keys, the push-port record and the advertised name an
        applied change moves."""
        if diff.devices_to_sync:
            self._record_push_ports()
        if diff.project_meta:
            self.state.set("system.project_name", self.project.project.name,
                           source="system")
            if self.mdns_advertiser:
                self.mdns_advertiser.update_name(self.project.project.name)

    async def _announce_saved(self, revision: str, origin: dict[str, str], *, echo: bool) -> None:
        """THE announcement of a saved change, from every door. Open tabs
        learn ``revision`` (``project.reloaded``, with ``origin``, the tab
        that made the change, when one did), always, even for a change
        they cannot see, so a stale copy is refused rather than saved over
        it. Then ``system.project.reloaded`` on the event bus, which a
        script, a trigger or a plugin may wait on: not for an ``echo``, a
        save made while a handler of that event ran (``_an_echo``), and not
        once the engine is stopping, when a trigger or a script would start
        work the stop is about to cancel. The file holds the change, so a
        part that fails is logged and the next still goes out."""
        await _call_logged(
            partial(self.broadcast_ws, {"type": "project.reloaded", "revision": revision, **origin}),
            "Project saved, but announcing its revision failed",
        )
        if echo or self.stopping:
            return
        await _call_logged(
            self._tell_the_bus, "Project saved, but emitting system.project.reloaded failed",
        )

    async def _tell_the_bus(self) -> None:
        """Emit ``system.project.reloaded``, its handlers' own saves marked
        as its echo while it runs."""
        emit = _ReloadEmit()
        token = _announcing.set(emit)
        try:
            await self.events.emit("system.project.reloaded")
        finally:
            emit.running = False
            _announcing.reset(token)

    async def _announce_panel_definition(self, diff: ProjectDiff) -> None:
        """Push the new UI definition to connected panels, only when what it
        carries changed (panels re-render on every ui.definition)."""
        if diff.ui:
            # The bindings are new, so an action the runtime could not run
            # before is worth saying again -- somebody editing a project is
            # trying to fix something, and silence reads as having fixed it.
            self.ui_events.forget_undispatched_actions()
        if diff.panel_definition:
            await self.broadcast_ws(self.panel_definition_frame())

    async def persist_or_defer_bookkeeping_change(self, mutate, *, on_error=None, runtime_write=None) -> None:
        """Persist a bookkeeping change from code that may be running inside
        a change that holds the reload lock (a plugin hook or an event
        handler that change awaits, where waiting for the lock would wait
        for itself).

        Inside one (:meth:`_running_hooks`),
        :meth:`schedule_bookkeeping_change`: ``mutate`` is queued to run
        right after it, against the project current then, and a failure is
        logged. Otherwise :meth:`persist_bookkeeping_change`, behind any
        change holding the lock: saved before this returns, raising on
        failure. Either way nothing reaches the running project or the
        runtime before the file holds it (``runtime_write``).
        """
        if self._hook_runs:
            self.schedule_bookkeeping_change(mutate, on_error=on_error, runtime_write=runtime_write)
        else:
            await self.persist_bookkeeping_change(mutate, on_error=on_error, runtime_write=runtime_write)

    async def persist_bookkeeping_change(self, mutate, *, on_error=None, runtime_write=None) -> str:
        """Persist a project change the running system takes without a
        reconcile — save + revision bump, then the announcement every door
        makes (:meth:`_announce_saved`, once the lock is let go, so an open
        IDE's stale ETag gets a 409 instead of silently reverting the
        change, and a handler of it that saves waits its turn).

        ``mutate(project)`` goes through the change door
        (:meth:`_change_locked`) without the reconcile. When it is refused
        or the save fails, ``on_error`` runs and the exception propagates;
        the running project is then as it was. ``runtime_write()`` is the
        running system's own half of the change (a device's live settings,
        a plugin's config):
        made once the file holds the change and still under the lock, so a
        failed save leaves the runtime as the file holds it, and a change
        waiting on the lock never reconciles against a runtime that has not
        taken this one yet. The file holds the change by then, so its
        failure is logged, never raised.

        MUST NOT be called from code that can run while the reconcile lock
        is held — event handlers fired during a reconcile (device connect,
        plugin hooks, state changes) — that would deadlock. Those callers
        use :meth:`schedule_bookkeeping_change`, or
        :meth:`persist_or_defer_bookkeeping_change` to await it when they
        are not inside one.
        """
        echo = _an_echo()
        async with self._reload_lock:
            try:
                revision = (await self._change_locked(mutate, reconcile=False)).revision
            except Exception:
                if on_error is not None:
                    await _call(on_error)
                raise
            await _call_logged(
                runtime_write, "Project saved, but the running system could not take the change",
            )
        await self._announce_saved(revision, reload_origin(), echo=echo)
        return revision

    def schedule_bookkeeping_change(
        self, mutate, *, on_error=None, on_saved=None, runtime_write=None,
    ) -> None:
        """Queue a bookkeeping persist to run outside the reconcile lock.

        Safe to call from anywhere — including event handlers awaited inline
        while ``apply_project`` holds the reconcile lock (device connect
        inside ``_sync_devices``, plugin hooks inside ``_sync_plugins``),
        where awaiting the lock would deadlock the engine.

        Writes queued in the same tick coalesce into one change through the
        change door (:meth:`_change_locked`, without the reconcile): one
        save and one revision bump, and one refusal or failure for all of
        them. Each ``mutate(project)`` is applied to the project current at
        flush time, so a project swap between schedule and flush can't
        resurrect stale state or drop the write.

        ``runtime_write()`` is as for :meth:`persist_bookkeeping_change`:
        made once the file holds the batch, still under the lock, each in
        the order queued, and never when the save fails; so the running
        system waits for the file as an awaited change does. A failure is
        logged, never raised, and the others still run. ``on_error`` runs
        when the batch is refused or its save fails, ``on_saved`` outside
        the lock once it is saved; a callback failure cannot undo a file
        already written to disk. Then the batch is announced
        (:meth:`_announce_saved`), naming no tab: a tab whose request a
        queued change came out of did not make it. The bus hears it unless
        every change in it was queued as an echo.
        """
        self._bookkeeping_queue.append(
            _Queued(mutate, on_error, on_saved, runtime_write, echo=_an_echo()),
        )
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

    async def _save_batch(self) -> tuple[list[_Queued], str | None]:
        """Take everything queued and save it as one change, the running
        system taking each part once the file holds it; the batch and its
        new revision, or ``None`` when nothing was saved. A refused or
        failed change has changed nothing, and its callers hear so under
        the lock, before another change can land."""
        async with self._reload_lock:
            batch = list(self._bookkeeping_queue)
            self._bookkeeping_queue.clear()
            if not self.project:
                return batch, None
            try:
                mutates = [queued.mutate for queued in batch]
                outcome = await self._change_locked(_each(mutates), reconcile=False)
            except Exception as e:
                log.error(f"Deferred project persist failed: {e}")
                # The later ones first, the reverse of the order queued.
                for queued in reversed(batch):
                    await _call_logged(queued.on_error, "Bookkeeping on_error callback failed")
                return batch, None
            for queued in batch:
                await _call_logged(
                    queued.runtime_write,
                    "Project saved, but the running system could not take the change",
                )
            return batch, outcome.revision

    async def _after_batch_saved(self, batch: list[_Queued], revision: str) -> None:
        for queued in batch:
            await _call_logged(queued.on_saved, "Bookkeeping on_saved callback failed")
        await self._announce_saved(revision, {}, echo=all(queued.echo for queued in batch))

    async def _drain_bookkeeping(self, when: str) -> None:
        task = self._bookkeeping_task
        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5)
            except Exception:
                log.warning(f"Bookkeeping persist did not finish {when} shutdown")
