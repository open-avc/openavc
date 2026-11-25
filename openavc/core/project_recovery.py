"""Getting a project at boot, and saying out loud when the project on disk was
not the one that loaded.

``load_or_recover`` is the load (``Engine._load_project_safe`` asks it). First
a replace of the project that a power cut stopped part way is put back: the
backup it took before its first rename, which its "replace in progress"
marker names (``core/project_swap``). Then a project file that is missing or
will not load is replaced from the newest of its backups that loads, the
ones taken since another project last replaced it
(``backup_manager.backups_since_replaced``: the backups before that hold the
project that was closed). With none, a missing file is seeded from the
bundled starter project, and otherwise the instance starts an empty Recovery
Project (``_Recovery``). Each replaces the whole project the way a running
replace does, after keeping what it replaces under Backups, the file that
would not load included. Which of those leaves a record, and what it says,
is decided beside the record itself. The recovery always worked. What it did not do was tell
anybody. The two log lines it wrote
scrolled past under device-poll noise, and the person whose last edit was in
the window between the backup and the corrupt save had no way to know it was
gone: the room came up, the panel worked, and the only available theory was
"did I even save that?".

So a recovery writes a small record beside the project and publishes it as
``system.project_recovery*`` state, which puts it on the Dashboard, in reach of
a trigger, and (because ``system.*`` relays) on the cloud with everything else.

**The record outlives the boot that wrote it, and only a person clears it.**
A notice that vanished on the next restart would be the same problem again on
a box that reboots nightly. Dismissing is the one thing that removes it, which
means the acknowledgement is the integrator's, not the scheduler's.

**A first boot with no project file is not a recovery.** A missing file with no
backups at all is a fresh install, and there is nothing to report; the loud
version is reserved for a project that existed: one that could not be read,
or a missing one that has backups. The installers leave this first start's
seeding to the runtime, so a reinstall over a system whose project file went
missing never puts the starter in place before its backups are read.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openavc.core import project_identity
from openavc.core.project_loader import ProjectConfig, ProjectMeta, load_project, save_project
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore

log = get_logger(__name__)
# The load's lines are filed under the engine's name, where a start's lines are.
_load_log = get_logger("openavc.core.engine")

# The record, beside project.avc and state.json.
MARKER_NAME = "recovery.json"

# The state keys. One current record per instance: what happened at the last
# boot that had to recover, until somebody says they have read it.
KEY_STATE = "system.project_recovery"
KEY_REASON = "system.project_recovery_reason"
KEY_BACKUP = "system.project_recovery_backup"
KEY_CUTOFF = "system.project_recovery_cutoff"
KEY_AT = "system.project_recovery_at"

ALL_KEYS = (KEY_STATE, KEY_REASON, KEY_BACKUP, KEY_CUTOFF, KEY_AT)

# system.project_recovery values.
NONE = ""
RESTORED = "restored"   # a backup loaded in place of the project file
RESET = "reset"         # nothing loaded; the instance started an empty project
STARTER = "starter"     # a missing file with backups, none of them its project's that loads; the starter runs

# system.project_recovery_reason values — what was wrong with the project file.
UNREADABLE = "unreadable"   # present, and it would not parse or validate
MISSING = "missing"         # not there at all
INTERRUPTED = "interrupted"  # a replace of the project stopped part way

_OUTCOMES = {RESTORED, RESET, STARTER}
_REASONS = {UNREADABLE, MISSING, INTERRUPTED}


def _marker_path(project_dir: Path) -> Path:
    return project_dir / MARKER_NAME


def record(
    project_dir: Path,
    *,
    outcome: str,
    reason: str,
    backup: str = "",
    cutoff: str = "",
) -> None:
    """Write the record for a recovery that just happened.

    Overwrites an older undismissed record: the newest one is the one that
    describes the project now running. Never raises — a boot that recovered
    must still come up, and losing the notice is not worth losing the room.
    """
    if outcome not in _OUTCOMES or reason not in _REASONS:
        log.warning(f"Ignoring recovery record with unknown outcome/reason: {outcome}/{reason}")
        return

    data = {
        "outcome": outcome,
        "reason": reason,
        "backup": backup,
        "cutoff": cutoff,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(_marker_path(project_dir), json.dumps(data, indent=2))
    except OSError as e:
        log.warning(f"Could not write the project recovery record: {e}")


def read(project_dir: Path) -> dict[str, Any] | None:
    """The current record, or None when there is nothing to report.

    An unreadable or malformed marker reads as nothing rather than as an
    error: it is a notice, and a broken notice must not become a second fault.
    """
    path = _marker_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning(f"Ignoring an unreadable project recovery record: {e}")
        return None
    if not isinstance(data, dict) or data.get("outcome") not in _OUTCOMES:
        return None
    return data


def publish(state: StateStore, project_dir: Path) -> None:
    """Publish the record (or its absence) to state.

    Called at startup after the project has loaded, so every key exists from
    the first frame and a panel bound to one draws a blank rather than nothing.
    """
    data = read(project_dir) or {}
    state.set(KEY_STATE, str(data.get("outcome", NONE)), source="system")
    state.set(KEY_REASON, str(data.get("reason", "")), source="system")
    state.set(KEY_BACKUP, str(data.get("backup", "")), source="system")
    state.set(KEY_CUTOFF, str(data.get("cutoff", "")), source="system")
    state.set(KEY_AT, str(data.get("at", "")), source="system")


def dismiss(state: StateStore, project_dir: Path) -> bool:
    """Clear the record. Returns True if there was one to clear."""
    path = _marker_path(project_dir)
    existed = path.exists()
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.warning(f"Could not remove the project recovery record: {e}")
    publish(state, project_dir)
    return existed


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".recovery_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --- The load -----------------------------------------------------------------


def load_or_recover(project_path: Path) -> ProjectConfig:
    """The project at ``project_path``, or what stands in for it.

    A replace that did not finish is put back first
    (``_finish_interrupted_replace``). A file that is missing, or cannot be
    read or validated, is replaced by the newest of its project's backups
    that loads; with none, a missing file by the bundled starter project,
    and otherwise (or with no starter) an empty Recovery Project
    (``_Recovery``). A restored backup leaves a record (``record``), and so
    does the starter or an empty project started in place of a project that
    existed (a file that could not be read, or a missing one with backups),
    because a room that came up on two-minute-old settings, or on none of
    its own, has to say so to the person whose edit is not in it. A missing
    file with no backups at all is a first boot and leaves none.
    """
    _finish_interrupted_replace(project_path)
    try:
        return load_project(project_path, keep_notes=True)
    except FileNotFoundError:
        _load_log.warning(f"Project file not found: {project_path}")
        failure = MISSING
    except json.JSONDecodeError as e:
        failure = UNREADABLE
        _load_log.error(f"Project file is corrupted (invalid JSON): {e}")
    except Exception as e:
        failure = UNREADABLE
        _load_log.error(f"Project file failed to load: {e}")
    return _Recovery(project_path, failure).run()


def _finish_interrupted_replace(project_path: Path) -> None:
    """A replace that a power cut stopped part way, put back: the backup its
    marker names restored, and recorded as ``RESTORED`` for ``INTERRUPTED``,
    so the room comes up on the project before the replace, never a mix of
    two. What a replace left in its working folder is removed either way. A
    backup that cannot be put back is logged, and the load goes on with
    what is on disk."""
    from openavc.core import project_swap

    project_dir = project_path.parent
    marker = project_swap.read_marker(project_dir)
    project_swap.clear_leftovers(project_dir)
    if marker is None:
        return
    backup = marker["backup"]
    if backup:
        _put_back_replaced(project_path, backup)
    try:
        project_swap.clear_marker(project_dir)
    except OSError as e:
        _load_log.warning(f"Could not remove the replace-in-progress marker: {e}")


def _put_back_replaced(project_path: Path, backup: str) -> None:
    """The backup a replace took of the project it replaced, restored and
    recorded. Never raises."""
    from openavc.core.backup_manager import list_backups, restore_from_backup

    project_dir = project_path.parent
    backup_path = project_dir / backup
    _load_log.warning(f"A project replace did not finish; putting back the backup it took first: {backup}")
    try:
        backup_path.resolve().relative_to(project_dir.resolve())
        restore_from_backup(backup_path, project_path)
    except Exception as e:
        _load_log.error(f"The backup taken before a project replace could not be put back ({backup}): {e}")
        return
    taken = next((b.timestamp for b in list_backups(project_path) if b.filename == backup), "")
    record(project_dir, outcome=RESTORED, reason=INTERRUPTED, backup=backup, cutoff=taken)


#: The reason the backup a recovery takes of what it replaces is listed
#: under in Backups, by what was wrong with the project file.
SET_ASIDE_REASONS = {
    UNREADABLE: "Before recovery (the project file would not load)",
    MISSING: "Before recovery (the project file was missing)",
}


class _Recovery:
    """What a start puts in place of a project file it could not load
    (``failure``): the newest of the project's backups that loads
    (``backup_manager.backups_since_replaced``), else the bundled starter
    for a missing file, else an empty Recovery Project. Either of the last
    two says so when backups were there: the project they hold is not
    running.

    Each replaces the project the way a running replace does
    (``core/project_replace``: prepared and checked as a load reads it
    before anything is written, then swapped whole by
    ``project_replace.replace_files``), so nothing the previous project left
    beside its file (its scripts, file trees, saved values, notices) carries
    into the one that starts. Before the first swap, what is there is kept
    under Backups (``backup_manager.set_aside``), the file that would not
    load included, never written over."""

    def __init__(self, project_path: Path, failure: str) -> None:
        self.project_path = project_path
        self.failure = failure
        self._set_aside_taken = False

    def run(self) -> ProjectConfig:
        from openavc.core.backup_manager import backups_since_replaced, list_backups

        # A fresh install, or OPENAVC_PROJECT pointed at a folder not yet
        # created: the swap and the backup work beside the project file.
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        # Read before this start keeps anything under Backups, so what it
        # keeps is neither tried nor counted.
        had_backups = bool(list_backups(self.project_path))
        restored = self._restore_newest_backup(backups_since_replaced(self.project_path))
        if restored is not None:
            return restored
        if self.failure == MISSING:
            seeded = self._seed(say_so=had_backups)
            if seeded is not None:
                return seeded
        return self._start_empty(say_so=had_backups or self.failure == UNREADABLE)

    def _replace(self, prepared, *, spare: Path | None = None) -> None:
        """``prepared`` put in place of what is at the project file, which
        is kept under Backups first, once. ``spare`` is a backup being
        restored: that backup's rotation leaves it alone, and an update
        notice nobody has read yet stays, since it is the same project."""
        from openavc.core.project_replace import replace_files

        if not self._set_aside_taken:
            self._set_aside(spare)
        replace_files(prepared, self.project_path, keep_earlier_notes=spare is not None)

    def _set_aside(self, spare: Path | None) -> None:
        """What is at the project file and beside it, kept under Backups.
        One that cannot be saved is logged and the start goes on: the room
        must come up."""
        from openavc.core import backup_manager

        self._set_aside_taken = True
        try:
            kept = backup_manager.set_aside(self.project_path, SET_ASIDE_REASONS[self.failure], spare=spare)
        except OSError as e:
            _load_log.error(f"The project this start replaces could not be kept under Backups: {e}")
            return
        if kept is not None:
            _load_log.warning(f"Kept the project this start replaces under Backups: {kept.name}")

    def _seed(self, *, say_so: bool) -> ProjectConfig | None:
        """The bundled starter project put in place and loaded, or None
        when there is no seed or it cannot be put in place or loaded.

        A missing project file is the first start of every install type:
        no installer copies the starter in, so a reinstall over a project
        file that went missing leaves its backups to be read first, and a
        Docker /data however it was provided (a bind mount shadows what the
        image layer holds) starts the same way. The starter brings no
        notice of what its load changed: a fresh install never says it was
        updated. With ``say_so`` (backups were there, and none of them
        loaded as the project's), the record says the starter is running
        in place of a missing file (``STARTER``).
        """
        from openavc.core.project_replace import blank, read_as_loaded
        from openavc.system_config import get_seed_project_path

        seed = get_seed_project_path()
        if seed is None:
            return None
        try:
            starter, _ = read_as_loaded(seed.read_bytes())
            self._replace(blank(starter))
            _load_log.info(f"Seeded default project from {seed}")
            project = load_project(self.project_path)
        except Exception as e:
            _load_log.warning(f"Failed to seed project from {seed}: {e}")
            return None
        if say_so:
            _load_log.warning("The project file was missing and no backup of it would open; started the starter project")
            record(self.project_path.parent, outcome=STARTER, reason=MISSING)
        return project

    def _restore_newest_backup(self, backups) -> ProjectConfig | None:
        """The first of ``backups`` (newest first) that loads, recorded as
        ``RESTORED`` for the failure; None when none does. A backup that is
        not a project this system loads is passed over before anything is
        written."""
        from openavc.core.project_replace import from_backup

        project_dir = self.project_path.parent
        for backup in backups:
            backup_path = project_dir / backup.filename
            _load_log.info(f"Attempting restore from backup: {backup.filename}")
            try:
                self._replace(from_backup(backup_path), spare=backup_path)
                project = load_project(self.project_path, keep_notes=True)
            except Exception as e:
                _load_log.warning(f"Backup restore failed ({backup.filename}): {e}")
                continue
            _load_log.info(f"Successfully restored project from backup: {backup.filename}")
            record(project_dir, outcome=RESTORED, reason=self.failure, backup=backup.filename,
                   cutoff=backup.timestamp)
            return project
        return None

    def _start_empty(self, *, say_so: bool) -> ProjectConfig:
        """An empty Recovery Project put in place.

        A project file that was simply absent, with no backups, is a first
        boot rather than a loss, so it gets no record. Only a project that
        existed (``say_so``: one that could not be read, or a missing one
        with backups) is worth interrupting somebody for. The record is
        written after the swap, which moves any earlier one away with the
        project it was about. A swap that cannot be made
        (a folder held open) leaves the files beside the project as they
        were and writes the Recovery Project's file alone, so the last
        resort still starts the room.
        """
        from openavc.core.project_replace import blank

        _load_log.warning("No backups available. Creating empty recovery project.")
        now = datetime.now(timezone.utc).isoformat()
        empty = ProjectConfig(
            project=ProjectMeta(
                id="recovery",
                name="Recovery Project",
                description="Auto-created after project corruption. Use File > Open to load a project.",
                created=now,
                modified=now,
                uuid=project_identity.new_stamp(),
            )
        )
        try:
            self._replace(blank(empty))
        except OSError as e:
            _load_log.error(f"Could not replace the project's files ({e}); writing the Recovery Project's file alone")
            save_project(self.project_path, empty)
        if say_so:
            record(self.project_path.parent, outcome=RESET, reason=self.failure)
        return empty
