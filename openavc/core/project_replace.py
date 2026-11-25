"""THE replace of the running project by another whole project, its files
included: a restore, opening a copy from the library, a blank project.

A door prepares what comes in (``PreparedProject``): the project migrated and
checked as a load reads it, before anything is written, so one this system
will not run is refused with nothing changed; the files it brings beside its
project file (a ``fill`` for the swap); and the logins it brings. Then
``Engine.replace_with`` runs the replace under the project lock
(``_lock_for_a_change``), in this order:

1. the backup of the project it replaces (``backup_manager.capture``), taken
   under the lock, so an edit saved while the replace waited is in it, and
   refused when it cannot be saved;
2. what comes in staged beside the live project (``core/project_swap``);
3. the state persister stopped, so no value is written over the files being
   swapped;
4. the "replace in progress" marker written, naming that backup; every entry
   the project is made of swapped by renames, the project file, its saved
   values (``state.json``) and the start's notice (``recovery.json``)
   included; the marker removed. A power cut in between has the next start
   put the backup back (``core/project_recovery``);
5. the project file loaded and applied as a load is, with one announcement.
   When that fails, the files before it are swapped back under the marker
   and the project before it loaded again, and the failure is raised as
   ``ProjectNotAppliedError``;
6. whatever happened, the persister started again on the state.json now on
   disk, its values put in the store.

``Engine.replace_project`` is the other way a whole project comes in: it
keeps the files beside the project and the saved values, which is right for
a cloud push (it brings no files) and is what an import of a bare ``.avc``
does until it is sent through here.

Mixed into the engine through ``ProjectChangesMixin``. Logs under the
engine's name.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from openavc.core import backup_manager, project_recovery, project_swap
from openavc.core.project_loader import ProjectConfig, load_project, project_text
from openavc.core.project_update import MigrationNotes
from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


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
    ``on_replaced()`` runs once it is the running project."""

    project: ProjectConfig
    notes: MigrationNotes
    backup_reason: str
    fill: Callable[[Path], None] | None = None
    beside: bool = True
    after_swap: Callable[[Path], None] | None = None
    on_replaced: Callable[[], None] | None = None
    spare: Path | None = None

    def staged_beside(self, project_file: Path) -> project_swap.Staged:
        """This project staged beside ``project_file`` for a swap, its
        project file written as a save writes it (``project_text``)."""
        text = project_text(self.project).encode("utf-8")
        return project_swap.stage(project_file, text, self.fill, beside=self.beside)


def replace_files(prepared: PreparedProject, project_file: Path) -> None:
    """``prepared``'s files put in place of the project at ``project_file``
    where no project is running: staged, swapped, its logins filled in, with
    no marker and nothing to reload. A swap that fails puts back what it
    moved and raises."""
    text = project_text(prepared.project).encode("utf-8")
    project_swap.replace_on_disk(project_file, text, prepared.fill, beside=prepared.beside)
    if prepared.after_swap is not None:
        prepared.after_swap(project_file)
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
    """A new blank project prepared to replace the running one: no scripts,
    no files, no saved values."""
    return PreparedProject(project, MigrationNotes(), "Before creating blank project")


class ProjectReplaceMixin:
    """``replace_with``: the running project replaced by a prepared one."""

    async def replace_with(self, prepared: PreparedProject):
        """Replace the running project, its files included, by ``prepared``
        (the module docstring has the order). Returns the ``ApplyOutcome``.
        Raises ``BackupFailed`` when the backup could not be saved and
        ``ProjectNotAppliedError`` when the swap or the load failed; either
        way the project before it is running, its files as they were."""
        async with self._lock_for_a_change():
            if self.persister:
                # The values changed since the last flush are in the backup.
                self.persister.flush()
            taken = await backup_manager.capture(
                self.project_path, prepared.backup_reason, spare=prepared.spare,
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
        the load, and the persister started again on what is on disk."""
        project_dir = self.project_path.parent
        backup = backup_manager.backup_id(project_dir, taken) if taken is not None else None
        swapped = replaced = False
        if self.persister:
            self.persister.stop()
        try:
            self._swap_under_marker(staged.swap_in, backup)
            swapped = True
            if prepared.after_swap is not None:
                prepared.after_swap(self.project_path)
            outcome = await self._load_replaced(staged, backup)
            replaced = True
            self.keep_update_notes(prepared.notes, replace=True)
            project_recovery.publish(self.state, project_dir)
            if prepared.on_replaced is not None:
                prepared.on_replaced()
            return outcome
        finally:
            self._restart_persister(swapped, replaced)

    def _swap_under_marker(self, swap: Callable[[], None], backup: str | None) -> None:
        """``swap`` with the marker on disk before it and removed after. A
        swap that fails has put back what it moved; it is raised as not
        applied."""
        from openavc.core.project_changes import ProjectNotAppliedError
        from openavc.core.project_loader import ProjectSaveError

        project_dir = self.project_path.parent
        try:
            project_swap.write_marker(project_dir, backup)
            swap()
            project_swap.clear_marker(project_dir)
        except OSError as error:
            raise ProjectNotAppliedError(ProjectSaveError(error)) from error

    async def _load_replaced(self, staged: project_swap.Staged, backup: str | None):
        """The swapped-in project file loaded and applied as a load is,
        announced once. When that fails, the files before it are swapped
        back and the project before it loaded again, so what runs is what
        the files hold, and the first failure is raised."""
        from openavc.core.project_changes import ProjectNotAppliedError
        from openavc.core.project_diff import ProjectOrigin

        try:
            incoming = load_project(self.project_path)
            return await self._apply_project_locked(incoming, ProjectOrigin.LOAD, persist=False)
        except Exception as failure:
            log.error("The replacing project could not be loaded; putting the previous one back", exc_info=True)
            self._swap_under_marker(staged.swap_back, backup)
            await self._reload_after_failed_replace()
            if isinstance(failure, ProjectNotAppliedError):
                raise
            raise ProjectNotAppliedError(failure) from failure

    async def _reload_after_failed_replace(self) -> None:
        """The project before a replace that failed, loaded from its files
        again: the failed apply put the runtime back, but against the
        incoming files. Logged, never raised: the caller raises the first
        failure."""
        from openavc.core.project_diff import ProjectOrigin

        try:
            previous = load_project(self.project_path)
            await self._apply_project_locked(previous, ProjectOrigin.LOAD, persist=False)
        except Exception:
            log.exception("The previous project could not be loaded again after a failed replace")

    def _restart_persister(self, swapped: bool, replaced: bool) -> None:
        """The persister started again: after a swap, on the state.json now
        on disk, its values put in the store (``reload_persisted_state``;
        once the incoming project runs, every variable seeded as a start
        seeds it); otherwise on the values the store holds, written out,
        since the file never changed."""
        persister = self.persister
        if persister is None:
            return
        if swapped:
            self.reload_persisted_state(replaced=replaced)
            return
        from openavc.core.project_variables import persistent_var_keys

        keys = persistent_var_keys(self.project.variables) if self.project else set()
        persister.resume(keys)
