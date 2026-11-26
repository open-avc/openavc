"""Getting a project at boot, and saying out loud when the project on disk was
not the one that loaded.

``load_or_recover`` is the load (``Engine._load_project_safe`` asks it), and
THE order a start gets its project in. First a replace of the project that a
power cut stopped part way is put back: the backup it took before its first
rename, which its "replace in progress" marker names (``core/project_swap``),
from then on a backup of the project that runs; when it cannot be put back,
what the swap had moved aside is kept under Backups and the files run as it
left them. Then a project file that is missing or
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
A start that found no backup of the project that opens names the ones it
tried, so the person reading the notice can tell those from the older
backups, of a project that was replaced, still listed under Backups.

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
from collections.abc import Sequence
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
# The backups tried that would not open, newest first, one name a line, when
# none of them did. A line, not a comma: a legacy backup's name is a file's.
KEY_TRIED = "system.project_recovery_tried"

ALL_KEYS = (KEY_STATE, KEY_REASON, KEY_BACKUP, KEY_CUTOFF, KEY_AT, KEY_TRIED)

# system.project_recovery values.
NONE = ""
RESTORED = "restored"   # a backup loaded in place of the project file
RESET = "reset"         # nothing loaded; the instance started an empty project
STARTER = "starter"     # a missing file with backups, none of them its project's that loads; the starter runs
UNFINISHED = "unfinished"  # a replace a power cut stopped, its backup not put back; the files as it left them run

# system.project_recovery_reason values — what was wrong with the project file.
UNREADABLE = "unreadable"   # present, and it would not parse or validate
MISSING = "missing"         # not there at all
INTERRUPTED = "interrupted"  # a replace of the project stopped part way

_OUTCOMES = {RESTORED, RESET, STARTER, UNFINISHED}
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
    tried: Sequence[str] = (),
) -> None:
    """Write the record for a recovery that just happened. ``tried`` names
    the backups a start tried when none of them opened, newest first.

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
        "tried": list(tried),
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
    state.set(KEY_TRIED, "\n".join(_tried(data)), source="system")


def _tried(data: dict[str, Any]) -> list[str]:
    """The record's tried backups: none for a record an earlier release
    wrote, or one whose list is not a list of names."""
    tried = data.get("tried")
    if not isinstance(tried, list) or not all(isinstance(name, str) for name in tried):
        return []
    return tried


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
    """The project at ``project_path``, or what stands in for it: THE
    order a start gets its project in.

    1. A replace a power cut stopped part way is put back from the backup
       its marker names (``_finish_interrupted_replace``), that backup read
       from then on as the running project's (``backup_manager.not_replaced``);
       a backup that cannot be put back leaves what the swap had moved
       aside kept under Backups, and the files as it left them go on.
    2. The project file is loaded.
    3. A file that is missing, or cannot be read or validated, is replaced
       by the newest of the backups taken since another project last
       replaced it that loads (``backup_manager.backups_since_replaced``),
       after what is there is kept under Backups (``_Recovery``).
    4. With none, a missing file is replaced by the bundled starter project,
       and otherwise (or with no starter) by an empty Recovery Project.

    A restored backup leaves a record (``record``), and so does the starter
    or an empty project started in place of a project that existed (a file
    that could not be read, or a missing one with backups), because a room
    that came up on two-minute-old settings, or on none of its own, has to
    say so to the person whose edit is not in it. A missing file with no
    backups at all is a first boot and leaves none.
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
    two. Once the marker is removed, that backup is read as the running
    project's (``backup_manager.not_replaced``). A backup that cannot be put
    back (gone, damaged, or none named) leaves what the swap had moved aside
    kept under Backups first (``_keep_what_was_moved_aside``), and the load
    goes on with what is on disk. What a replace left in its working folder
    is removed either way."""
    from openavc.core import project_swap

    project_dir = project_path.parent
    marker = project_swap.read_marker(project_dir)
    if marker is None:
        project_swap.clear_leftovers(project_dir)
        return
    backup = marker["backup"]
    if not backup:
        _load_log.warning("A project replace did not finish, and it names no backup to put back")
    put_back = bool(backup) and _put_back_replaced(project_path, backup)
    if not put_back:
        _keep_what_was_moved_aside(project_path)
    project_swap.clear_leftovers(project_dir)
    try:
        project_swap.clear_marker(project_dir)
    except OSError as e:
        _load_log.warning(f"Could not remove the replace-in-progress marker: {e}")
        return
    if put_back:
        # Written again only once no marker names it: until then it is what
        # the next start puts back.
        from openavc.core.backup_manager import not_replaced

        not_replaced(project_dir / backup)


def _keep_what_was_moved_aside(project_path: Path) -> None:
    """The entries of the project before a replace that its swap had moved
    aside into its working folder (``project_swap.moved_aside``), kept under
    Backups before that folder is removed, when the replace's own backup
    cannot be put back; recorded as ``UNFINISHED``, naming that backup (none
    when there was nothing to keep or it could not be saved). Its rotation
    leaves alone every backup the start may go on to try. Never raises."""
    from openavc.core import backup_manager, project_swap

    project_dir = project_path.parent
    kept = ""
    to_try = [project_dir / backup.filename for backup in backup_manager.backups_since_replaced(project_path)]
    try:
        taken = backup_manager.set_aside(
            project_path, SET_ASIDE_REASONS[INTERRUPTED], spare=to_try, source=project_swap.moved_aside(project_dir),
        )
    except OSError as e:
        _load_log.error(f"The files an unfinished project replace had moved aside could not be kept under Backups: {e}")
    else:
        if taken is not None:
            kept = backup_manager.backup_id(project_dir, taken)
            _load_log.warning(f"Kept the files an unfinished project replace had moved aside under Backups: {taken.name}")
    record(project_dir, outcome=UNFINISHED, reason=INTERRUPTED, backup=kept)


def _put_back_replaced(project_path: Path, backup: str) -> bool:
    """The backup a replace took of the project it replaced, restored and
    recorded; True when it was. Never raises."""
    from openavc.core.backup_manager import list_backups, restore_from_backup

    project_dir = project_path.parent
    backup_path = project_dir / backup
    _load_log.warning(f"A project replace did not finish; putting back the backup it took first: {backup}")
    try:
        backup_path.resolve().relative_to(project_dir.resolve())
        restore_from_backup(backup_path, project_path)
    except Exception as e:
        _load_log.error(f"The backup taken before a project replace could not be put back ({backup}): {e}")
        return False
    taken = next((b.timestamp for b in list_backups(project_path) if b.filename == backup), "")
    record(project_dir, outcome=RESTORED, reason=INTERRUPTED, backup=backup, cutoff=taken)
    return True


#: The reason the backup a recovery takes of what it replaces is listed
#: under in Backups, by what was wrong with the project file.
SET_ASIDE_REASONS = {
    UNREADABLE: "Before recovery (the project file would not load)",
    MISSING: "Before recovery (the project file was missing)",
    INTERRUPTED: "Before recovery (opening or restoring a project did not finish)",
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
    load included, never written over, and that backup's rotation removes
    none of the backups the start tries."""

    def __init__(self, project_path: Path, failure: str) -> None:
        self.project_path = project_path
        self.failure = failure
        self._set_aside_taken = False
        self._to_try: list[Path] = []
        # The backups that would not open, newest first, as list_backups names them.
        self._tried: list[str] = []

    def run(self) -> ProjectConfig:
        from openavc.core.backup_manager import backups_since_replaced, list_backups

        # A fresh install, or OPENAVC_PROJECT pointed at a folder not yet
        # created: the swap and the backup work beside the project file.
        self.project_path.parent.mkdir(parents=True, exist_ok=True)
        # Read before this start keeps anything under Backups, so what it
        # keeps is neither tried nor counted.
        had_backups = bool(list_backups(self.project_path))
        backups = backups_since_replaced(self.project_path)
        self._to_try = [self.project_path.parent / backup.filename for backup in backups]
        restored = self._restore_newest_backup(backups)
        if restored is not None:
            return restored
        if self.failure == MISSING:
            seeded = self._seed(say_so=had_backups)
            if seeded is not None:
                return seeded
        return self._start_empty(say_so=had_backups or self.failure == UNREADABLE)

    def _replace(self, prepared, *, restoring: bool = False) -> None:
        """``prepared`` put in place of what is at the project file, which
        is kept under Backups first, once. ``restoring``: it is one of the
        project's backups, so an update notice nobody has read yet stays,
        since it is the same project."""
        from openavc.core.project_replace import replace_files

        if not self._set_aside_taken:
            self._set_aside()
        replace_files(prepared, self.project_path, keep_earlier_notes=restoring)

    def _set_aside(self) -> None:
        """What is at the project file and beside it, kept under Backups,
        its rotation leaving alone every backup this start tries. One that
        cannot be saved is logged and the start goes on: the room must come
        up."""
        from openavc.core import backup_manager

        self._set_aside_taken = True
        try:
            kept = backup_manager.set_aside(self.project_path, SET_ASIDE_REASONS[self.failure], spare=self._to_try)
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
            _load_log.warning(f"The project file was missing and no backup of it would open{self._how_many_tried()}; "
                              "started the starter project")
            record(self.project_path.parent, outcome=STARTER, reason=MISSING, tried=self._tried)
        return project

    def _how_many_tried(self) -> str:
        """`` (3 tried)`` for the log, or nothing when none was."""
        return f" ({len(self._tried)} tried)" if self._tried else ""

    def _restore_newest_backup(self, backups) -> ProjectConfig | None:
        """The first of ``backups`` (newest first) that loads, recorded as
        ``RESTORED`` for the failure; None when none does, each one named
        for the record the start then writes. A backup that is not a
        project this system loads is passed over before anything is
        written."""
        from openavc.core.project_replace import from_backup

        project_dir = self.project_path.parent
        for backup in backups:
            backup_path = project_dir / backup.filename
            _load_log.info(f"Attempting restore from backup: {backup.filename}")
            try:
                self._replace(from_backup(backup_path), restoring=True)
                project = load_project(self.project_path, keep_notes=True)
            except Exception as e:
                _load_log.warning(f"Backup restore failed ({backup.filename}): {e}")
                self._tried.append(backup.filename)
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

        if self._tried:
            _load_log.warning(f"No backup of the project would open{self._how_many_tried()}. "
                              "Creating empty recovery project.")
        else:
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
            record(self.project_path.parent, outcome=RESET, reason=self.failure, tried=self._tried)
        return empty
