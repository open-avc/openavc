"""Getting a project at boot, and saying out loud when the project on disk was
not the one that loaded.

``load_or_recover`` is the load (``Engine._load_project_safe`` asks it): a
missing project file is seeded from the bundled starter project, a project
file that will not parse is replaced from the newest backup that does, and
only when no backup loads at all does the instance start an empty Recovery
Project. Which of those leaves a record, and what it says, is decided beside
the record itself. The recovery always worked. What it did not do was tell
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
backup to take its place is a fresh install, and there is nothing to report;
the loud version is reserved for a project that existed and could not be read.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

# system.project_recovery_reason values — what was wrong with the project file.
UNREADABLE = "unreadable"   # present, and it would not parse or validate
MISSING = "missing"         # not there at all

_OUTCOMES = {RESTORED, RESET}
_REASONS = {UNREADABLE, MISSING}


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

    A missing file is seeded from the bundled starter project. If the file is
    missing with no seed, or cannot be read or validated, the newest backup
    that loads replaces it; if none does, an empty Recovery Project is saved
    in its place. Anything but the happy path and the seed leaves a record
    (``record``), because a room that came up on two-minute-old settings has
    to say so to the person whose newer edit is not in it.
    """
    try:
        return load_project(project_path)
    except FileNotFoundError:
        _load_log.warning(f"Project file not found: {project_path}")
        seeded = _seed_missing(project_path)
        if seeded is not None:
            return seeded
        failure = MISSING
    except json.JSONDecodeError as e:
        failure = UNREADABLE
        _load_log.error(f"Project file is corrupted (invalid JSON): {e}")
    except Exception as e:
        failure = UNREADABLE
        _load_log.error(f"Project file failed to load: {e}")
    restored = _restore_newest_backup(project_path, failure)
    if restored is not None:
        return restored
    return _start_empty(project_path, failure)


def _seed_missing(project_path: Path) -> ProjectConfig | None:
    """The bundled starter project copied to ``project_path`` and loaded, or
    None when there is no seed or it cannot be copied or loaded.

    A missing project file is the normal first-boot state when install-time
    seeding did not reach this path: notably a Docker image run with a
    *bind-mounted* /data, which shadows the seed copied into the image layer
    (only named volumes inherit image content). Seeding here boots every
    deployment on the starter project instead of an empty Recovery Project,
    however /data was provided. A missing file has no backups to restore, so
    this is the first move.
    """
    from openavc.system_config import get_seed_project_path

    seed = get_seed_project_path()
    if seed is None:
        return None
    try:
        project_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed, project_path)
        _load_log.info(f"Seeded default project from {seed}")
        return load_project(project_path)
    except Exception as e:
        _load_log.warning(f"Failed to seed project from {seed}: {e}")
        return None


def _restore_newest_backup(project_path: Path, failure: str) -> ProjectConfig | None:
    """The newest backup that restores and loads, recorded as ``RESTORED``
    for ``failure``; None when none does. A backup that fails is logged and
    the next one tried."""
    from openavc.core.backup_manager import list_backups, restore_from_backup

    project_dir = project_path.parent
    for backup in list_backups(project_path):
        backup_path = project_dir / backup.filename
        _load_log.info(f"Attempting restore from backup: {backup.filename}")
        try:
            restore_from_backup(backup_path, project_path)
            project = load_project(project_path)
            _load_log.info(f"Successfully restored project from backup: {backup.filename}")
            record(
                project_dir,
                outcome=RESTORED,
                reason=failure or UNREADABLE,
                backup=backup.filename,
                cutoff=backup.timestamp,
            )
            return project
        except Exception as e:
            _load_log.warning(f"Backup restore failed ({backup.filename}): {e}")
    return None


def _start_empty(project_path: Path, failure: str) -> ProjectConfig:
    """An empty Recovery Project, saved at ``project_path``.

    A project file that was simply absent, with nothing to restore, is a first
    boot rather than a loss, so it gets no record. Only a project that existed
    and could not be read is worth interrupting somebody for.
    """
    _load_log.warning("No backups available. Creating empty recovery project.")
    if failure == UNREADABLE:
        record(project_path.parent, outcome=RESET, reason=UNREADABLE)
    empty = ProjectConfig(
        project=ProjectMeta(
            id="recovery",
            name="Recovery Project",
            description="Auto-created after project corruption. Use File > Open to load a project.",
            created=datetime.now(timezone.utc).isoformat(),
            modified=datetime.now(timezone.utc).isoformat(),
        )
    )
    # save_project writes through a temp file beside the project and a
    # .avc.bak sibling, both of which need the folder to exist: a fresh
    # install, or OPENAVC_PROJECT pointed at a folder not yet created.
    project_path.parent.mkdir(parents=True, exist_ok=True)
    save_project(project_path, empty)
    return empty
