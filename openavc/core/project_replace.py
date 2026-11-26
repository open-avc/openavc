"""THE replace of the running project by another whole project, its files
included: a restore, opening a copy from the library, a blank project, an
import of a project file on its own.

A door prepares what comes in (``PreparedProject``): the project migrated and
checked as a load reads it, before anything is written, so one this system
will not run is refused with nothing changed; the files it brings beside its
project file (a ``fill`` for the swap); and the logins it brings. Then
``Engine.replace_with`` runs the replace under the project lock
(``_lock_for_a_change``), in this order:

0. the revision the door read checked, and what the door decides by the
   project it replaces settled (``settle``): an imported project file that is
   the same project (``core/project_identity``) keeps the files beside it,
   its saved values included, and another sets them aside;
1. the backup of the project it replaces (``backup_manager.capture``), taken
   under the lock, so an edit saved while the replace waited is in it, and
   refused when it cannot be saved;
2. what comes in staged beside the live project (``core/project_swap``);
3. the state persister stopped, so no value is written over the files being
   swapped;
4. the "replace in progress" marker written, naming that backup, which is
   on disk before it (``project_swap.write_marker``); every entry
   the project is made of swapped by renames, the project file, its saved
   values (``state.json``) and the start's notice (``recovery.json``)
   included; the marker removed. A power cut in between has the next start
   put the backup back (``core/project_recovery``);
5. the project file loaded and applied as a load is, with one announcement,
   its variables seeded from its state.json (or their defaults) by the
   load's variables stage, before its triggers run, as at a start. When
   that fails, the files before it are swapped back under the marker and
   the project before it loaded again, the variables back as they were,
   and the failure is raised as ``ProjectNotAppliedError``;
6. whatever happened, the persister started again, and the values the
   store holds written out.

A door that writes or removes a file beside the project outside a change
(an uploaded asset, a theme, a custom control, a script's source) holds the
same lock from its checks to its write (``holding_project_files``), so its
file lands before the backup or once the new project runs, never between. A
door that reads the files off the event loop outside a change (a backup, an
export with sealed logins) starts only between replaces, and a replace waits
for it to finish before the swap (``read_project_files``).

``Engine.replace_project`` is the other way a whole project comes in, the
cloud's push, which brings no files: it keeps the files beside the project,
the saved values and the running project's stamp.

Mixed into the engine through ``ProjectChangesMixin``. Logs under the
engine's name.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from openavc.core import backup_manager, file_news, project_identity, project_recovery, project_swap
from openavc.core.project_loader import ProjectConfig, load_project, project_text
from openavc.core.project_update import MigrationNotes
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")

T = TypeVar("T")


@dataclass
class PreparedProject:
    """A whole project ready to replace the running one: ``project`` as
    this system will load it, what its migration changed (``notes``, the
    update notice once it runs), the name of the backup taken of the
    project it replaces (``backup_reason``, and ``spare``, a backup that
    backup's rotation leaves alone: the one being restored), what it brings
    beside its project file (``fill``, ``core/project_swap.stage``; with
    ``beside`` False the files beside the live one are kept), and
    ``after_swap(project_file)``, run once its files are in place and before
    it loads: the logins it brings, filled in where this system has none.
    ``on_replaced()`` runs once it is the running project. ``settle(self,
    running)`` runs under the lock before the backup: what the door decides
    by the project it replaces (``running``), such as whether the files
    beside it stay (``kept_files``: True when they did, None for a door
    whose project always brings its own)."""

    project: ProjectConfig
    notes: MigrationNotes
    backup_reason: str
    fill: Callable[[Path], None] | None = None
    beside: bool = True
    after_swap: Callable[[Path], None] | None = None
    on_replaced: Callable[[], None] | None = None
    spare: Path | None = None
    settle: Callable[[PreparedProject, ProjectConfig | None], None] | None = None
    kept_files: bool | None = None

    def staged_beside(self, project_file: Path) -> project_swap.Staged:
        """This project staged beside ``project_file`` for a swap, its
        project file written as a save writes it (``project_text``)."""
        text = project_text(self.project).encode("utf-8")
        return project_swap.stage(project_file, text, self.fill, beside=self.beside)


def replace_files(prepared: PreparedProject, project_file: Path, *, keep_earlier_notes: bool = False) -> None:
    """``prepared``'s files put in place of the project at ``project_file``
    where no project is running (a start: ``core/project_recovery``):
    staged and swapped under the shared project-save lock, so a save or a
    backup never reads a part-swapped project, its logins filled in, and
    what its migration changed kept for the notice, with no marker and
    nothing to reload. The notice describes ``prepared`` alone, unless
    ``keep_earlier_notes`` (a backup of the same project put back), when
    notes nobody has read yet stay ahead of its own. A swap that fails puts
    back what it moved and raises."""
    from openavc.core import project_update
    from openavc.core.project_loader import _project_save_lock

    text = project_text(prepared.project).encode("utf-8")
    with _project_save_lock:
        project_swap.replace_on_disk(project_file, text, prepared.fill, beside=prepared.beside)
        if prepared.after_swap is not None:
            prepared.after_swap(project_file)
    keep = project_update.record if keep_earlier_notes else project_update.replace
    keep(project_file.parent, prepared.notes)
    if prepared.on_replaced is not None:
        prepared.on_replaced()


def read_as_loaded(text: bytes | str) -> tuple[ProjectConfig, MigrationNotes]:
    """A project file's text as a load reads it (``load_project``):
    migrated, repaired and validated, the installed equipment composed in,
    and what the migration changed. Raises ``ProjectInvalidError`` for one
    that is not a valid project, ``InstalledEquipmentError`` for one this
    server's equipment refuses, and ``ValueError`` (a ``JSONDecodeError``
    included) for text that is not a project at all."""
    from openavc.core.installed_equipment import InstalledEquipment
    from openavc.core.project_changes import ProjectInvalidError
    from openavc.core.project_migration import migrate_project

    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("The project file does not hold a project.")
    notes = MigrationNotes()
    raw, _ = migrate_project(raw, notes)
    try:
        project = ProjectConfig(**raw)
    except ValidationError as invalid:
        raise ProjectInvalidError(invalid) from invalid
    return InstalledEquipment.from_environment().compose(project, for_load=True), notes


def from_backup(backup_path: Path) -> PreparedProject:
    """A backup prepared to replace the running project: ZIP or legacy
    ``.avc.bak``, read and checked as a load reads it. Raises
    ``ProjectInvalidError`` and ``InstalledEquipmentError`` as
    ``read_as_loaded`` does, and ``backup_manager.BackupUnreadable`` for one
    that holds no project this system can read at all."""
    from openavc.core.installed_equipment import InstalledEquipmentError
    from openavc.core.project_changes import ProjectInvalidError

    text = backup_manager.archived_project(backup_path)
    try:
        project, notes = read_as_loaded(text)
    except (ProjectInvalidError, InstalledEquipmentError):
        raise
    except Exception as unreadable:
        raise backup_manager.BackupUnreadable(
            f"Backup {backup_path.name} holds no project this system can read"
        ) from unreadable
    return PreparedProject(
        project, notes, "Before restore",
        fill=backup_manager.backup_filler(backup_path),
        beside=backup_manager.is_zip_backup(backup_path),
        after_swap=lambda project_file: backup_manager.restore_backup_logins(backup_path, project_file),
        spare=backup_path,
    )


def blank(project: ProjectConfig) -> PreparedProject:
    """A project with nothing beside its file prepared to replace the
    running one, or the one a start found (a new blank project, the starter
    a first start copies in, the Recovery Project): no scripts, no files, no
    saved values."""
    return PreparedProject(project, MigrationNotes(), "Before creating blank project")


def imported(project: ProjectConfig, notes: MigrationNotes) -> PreparedProject:
    """A project file imported on its own, with nothing beside it, prepared
    to replace the running one, its device settings trimmed as a save trims
    them. Whether the files beside the running project stay is settled
    against it under the lock (``_settle_imported``)."""
    from openavc.core.typed_fields import trim_device_settings

    trim_device_settings(project)
    return PreparedProject(
        project, notes, f"Before importing '{project.project.name}'", settle=_settle_imported,
    )


def _settle_imported(prepared: PreparedProject, running: ProjectConfig | None) -> None:
    """The same project (``project_identity.same_project``) keeps the files
    beside it (its scripts, file trees and saved values) and the running
    project's stamp; another project replaces them with none, the previous
    ones set aside in the backup the replace takes, and brings its own
    stamp, or a new one."""
    incoming = prepared.project
    same = project_identity.same_project(running.project if running else None, incoming.project)
    if same:
        project_identity.keep_running(running, incoming)
    else:
        project_identity.stamp_missing(incoming)
    prepared.beside = not same
    prepared.kept_files = same


class ProjectReplaceMixin:
    """``replace_with``: the running project replaced by a prepared one,
    and the hold a door takes so its file never lands inside a replace."""

    def holding_project_files(self) -> asyncio.Lock:
        """THE hold a door takes to write or remove a file beside the
        project outside a project change: an uploaded image or sound, a
        theme, a custom control, a script's source, through the
        Programmer's doors and the AI's alike. It is the change lock that
        :meth:`replace_with` holds from its backup to its load, held from
        the door's checks to its write, so a file saved meanwhile lands
        once the new project runs, never in the folder the swap carries
        away after the backup has read it, and a delete asks the project
        the files on disk belong to whether anything still shows the file.
        A change writes its own files under the lock already
        (``core/change_files``). Nothing that can wait on a change runs
        inside the hold: the door announces what it changed once the hold
        is let go."""
        return self._reload_lock

    def _init_project_replace(self) -> None:
        # The doors reading the project's files off the event loop now
        # (read_project_files), and whether none is.
        self._file_readers = 0
        self._no_file_readers = asyncio.Event()
        self._no_file_readers.set()

    async def read_project_files(self, read: Callable[[], Awaitable[T]]) -> T:
        """THE way a door reads the project's files from disk while the event
        loop runs on, outside a change: a backup (the manual one, the
        periodic one, the AI's first) or an export with sealed logins.
        ``read()`` starts only between replaces, and :meth:`replace_with`
        swaps nothing until every read started has finished, so what one
        reads is one project, never the project file being replaced with
        the incoming project's files. A save does not wait for it. Never
        called under the change lock, which it takes to start. A caller
        cancelled while the read runs on in its thread leaves it counted
        until it finishes."""
        async def counted() -> T:
            return await read()

        async with self._reload_lock:
            self._file_readers += 1
            self._no_file_readers.clear()
        reading = asyncio.ensure_future(counted())
        reading.add_done_callback(self._file_read_finished)
        return await asyncio.shield(reading)

    def _file_read_finished(self, reading: asyncio.Future) -> None:
        if not reading.cancelled():
            reading.exception()  # heard by the caller, or by nobody once it was cancelled
        self._file_readers -= 1
        if not self._file_readers:
            self._no_file_readers.set()

    async def replace_with(self, prepared: PreparedProject, *, expected_revision: str | None = None):
        """Replace the running project, its files included, by ``prepared``
        (the module docstring has the order). Returns the ``ApplyOutcome``.
        Raises ``ProjectRevisionConflictError`` when the project moved past
        ``expected_revision``, before anything is written, ``BackupFailed``
        when the backup could not be saved and ``ProjectNotAppliedError``
        when the swap or the load failed; either way the project before it
        is running, its files as they were. Once it runs, each file tree it
        swapped is announced (``core/file_news``), the lock let go first;
        one that kept the files beside the project announces none."""
        async with self._lock_for_a_change():
            outcome = await self._replace_locked(prepared, expected_revision)
        if prepared.beside:
            await file_news.trees_changed(self)
        return outcome

    async def _replace_locked(self, prepared: PreparedProject, expected_revision: str | None):
        """Steps 0 to 6 under the lock."""
        self._check_revision(expected_revision)
        if prepared.settle is not None:
            prepared.settle(prepared, self.project)
        if self.persister:
            # The values changed since the last flush are in the backup.
            self.persister.flush()
        taken = await backup_manager.capture(
            self.project_path, prepared.backup_reason, kind=backup_manager.KIND_REPLACE, spare=prepared.spare,
        )
        staged = await asyncio.to_thread(self._stage_replace, prepared)
        try:
            return await self._replace_staged(prepared, staged, taken)
        finally:
            await asyncio.to_thread(staged.discard)

    def _stage_replace(self, prepared: PreparedProject) -> project_swap.Staged:
        """What ``prepared`` brings, staged beside the live project; the
        project file is written as a save writes it. A failure to stage (a
        full disk, a backup member that cannot be read) is raised as not
        applied, nothing swapped."""
        from openavc.core.project_changes import ProjectNotAppliedError
        from openavc.core.project_loader import ProjectSaveError

        try:
            return prepared.staged_beside(self.project_path)
        except OSError as error:
            raise ProjectNotAppliedError(ProjectSaveError(error)) from error
        except Exception as error:
            raise ProjectNotAppliedError(error) from error

    async def _replace_staged(self, prepared: PreparedProject, staged: project_swap.Staged, taken: Path | None):
        """Steps 3 to 6: the persister stopped, the swap under the marker,
        the load, and the persister started again on what the store holds."""
        project_dir = self.project_path.parent
        backup = backup_manager.backup_id(project_dir, taken) if taken is not None else None
        # A backup or an export still reading the files finishes first
        # (read_project_files); none starts while the lock is held.
        await self._no_file_readers.wait()
        if self.persister:
            self.persister.stop()
        try:
            self._swap_under_marker(staged, staged.swap_in, backup)
            if prepared.after_swap is not None:
                prepared.after_swap(self.project_path)
            outcome = await self._load_replaced(staged, backup)
            self.keep_update_notes(prepared.notes, replace=True)
            project_recovery.publish(self.state, project_dir)
            if prepared.on_replaced is not None:
                prepared.on_replaced()
            return outcome
        finally:
            self._restart_persister()

    def _swap_under_marker(self, staged: project_swap.Staged, swap: Callable[[], None], backup: str | None) -> None:
        """``swap`` with the marker on disk before it, removed once the live
        entries are one whole project: the swap done, or every rename of a
        failed one undone. Kept when an undo failed too, so the next start
        puts the backup back. A failure is raised as not applied."""
        from openavc.core.project_changes import ProjectNotAppliedError
        from openavc.core.project_loader import ProjectSaveError

        project_dir = self.project_path.parent
        try:
            project_swap.write_marker(project_dir, backup)
            try:
                swap()
            except OSError:
                if not staged.partway:
                    project_swap.clear_marker(project_dir)
                raise
            project_swap.clear_marker(project_dir)
        except OSError as error:
            raise ProjectNotAppliedError(ProjectSaveError(error)) from error

    async def _load_replaced(self, staged: project_swap.Staged, backup: str | None):
        """The swapped-in project file loaded and applied as a load is, its
        variables seeded from the state.json swapped in (the variables
        stage, before the triggers run), announced once. When that fails,
        the files before it are swapped back and the project before it
        loaded again, so what runs is what the files hold, and the first
        failure is raised."""
        from openavc.core.project_changes import ProjectNotAppliedError
        from openavc.core.project_diff import ProjectOrigin

        self._values_coming_in = self.persister.load() if self.persister else {}
        try:
            running = self.project.project if self.project else None
            incoming = load_project(self.project_path, running=running)
            return await self._apply_project_locked(incoming, ProjectOrigin.LOAD, persist=False)
        except Exception as failure:
            log.error("The replacing project could not be loaded; putting the previous one back", exc_info=True)
            self._values_coming_in = None
            self._swap_under_marker(staged, staged.swap_back, backup)
            await self._reload_after_failed_replace()
            if isinstance(failure, ProjectNotAppliedError):
                raise
            raise ProjectNotAppliedError(failure) from failure
        finally:
            self._values_coming_in = None

    async def _reload_after_failed_replace(self) -> None:
        """The project before a replace that failed, loaded from its files
        again: the failed apply put the runtime and the variables back, but
        against the incoming files. Logged, never raised: the caller raises
        the first failure."""
        from openavc.core.project_diff import ProjectOrigin

        try:
            previous = load_project(self.project_path)
            await self._apply_project_locked(previous, ProjectOrigin.LOAD, persist=False)
        except Exception:
            log.exception("The previous project could not be loaded again after a failed replace")

    def _restart_persister(self) -> None:
        """The persister started again on the running project's saved
        variables, whatever happened, and the values the store holds written
        out: the incoming project's, seeded from its state.json and set by
        its triggers since, or the previous project's, put back."""
        persister = self.persister
        if persister is None:
            return
        from openavc.core.project_variables import persistent_var_keys

        keys = persistent_var_keys(self.project.variables) if self.project else set()
        persister.resume(keys)
