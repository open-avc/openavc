"""
Rollback system for failed updates.

Supports both automatic rollback (server crash after update) and
manual rollback (user-initiated via API).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from server.utils.spawn import CREATE_NO_WINDOW

log = logging.getLogger(__name__)

PENDING_UPDATE_MARKER = "pending-update"
ROLLBACK_MARKER = "apply-rollback"


def clear_stale_rollback_marker(data_dir: Path) -> bool:
    """Drop a leftover apply-rollback marker at server startup.

    The marker is consumed (and removed) by the root-level wrapper that runs
    before this process on every supported deployment (update-helper.sh via
    ExecStartPre on Linux, the launchd run wrapper on macOS). One still
    present when Python starts was never consumed — the process that wrote
    it didn't exit, or the deployment has no wrapper (dev, Docker). Left in
    place, the next unrelated restart would apply it and silently downgrade
    the install. Returns True if a stale marker was removed.
    """
    marker = data_dir / ROLLBACK_MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        log.exception("Could not remove stale apply-rollback marker at startup")
        return False
    log.warning("Removed stale apply-rollback marker at startup")
    return True


def _launch_installer_via_scheduler(installer: Path, label: str) -> bool:
    """Schedule a one-time Windows task to run the installer ~15s from now.

    Launching the installer as a direct child via subprocess.Popen does not
    work under NSSM: NSSM walks the service's process tree on exit and kills
    every descendant. Task Scheduler runs the task under taskhostw.exe, in
    its own process tree, completely outside NSSM's awareness.

    We register the task via XML rather than schtasks CLI flags because:
      - schtasks /st truncates seconds (e.g., 22:18:44 -> 22:18:00), which
        can leave the trigger in the past and the task never fires
      - StartWhenAvailable defaults to false via CLI, so a slightly-late
        trigger is silently skipped forever
      - RunLevel via /ru SYSTEM defaults to LeastPrivilege, which can prevent
        the installer from running with full admin rights
    """
    run_at = (datetime.now() + timedelta(seconds=15)).strftime("%Y-%m-%dT%H:%M:%S")
    task_name = f"OpenAVCUpdate-{label}"

    xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>OpenAVC</Author>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{run_at}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <Enabled>true</Enabled>
  </Settings>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Actions Context="Author">
    <Exec>
      <Command>{installer}</Command>
      <Arguments>/VERYSILENT /SUPPRESSMSGBOXES /NORESTART</Arguments>
    </Exec>
  </Actions>
</Task>'''

    xml_path = installer.parent / f"_{task_name}.xml"
    try:
        xml_path.write_text(xml, encoding="utf-16")
        subprocess.run(
            ["schtasks", "/create", "/f", "/tn", task_name, "/xml", str(xml_path)],
            check=True, capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        log.info("Scheduled installer task '%s' to run at %s", task_name, run_at)
        return True
    except subprocess.CalledProcessError as e:
        log.error("Failed to schedule installer task: %s", e.stderr.strip() if e.stderr else e)
        return False
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("Failed to schedule installer task: %s", e)
        return False
    finally:
        xml_path.unlink(missing_ok=True)


def write_pending_marker(
    data_dir: Path,
    from_version: str,
    to_version: str,
    backup_path: Path | None = None,
) -> None:
    """Write a marker file before applying an update.

    If the server crashes after update, the marker's presence on next
    startup triggers automatic rollback. ``backup_path`` records the
    pre-update backup zip so that rollback can restore user data from it.
    """
    marker_path = data_dir / PENDING_UPDATE_MARKER
    marker_data = {
        "from_version": from_version,
        "to_version": to_version,
        "attempts": 0,
    }
    if backup_path is not None:
        marker_data["backup"] = str(backup_path)
    marker_path.write_text(json.dumps(marker_data), encoding="utf-8")
    log.info("Wrote pending-update marker: %s -> %s", from_version, to_version)


def read_pending_marker(data_dir: Path) -> dict | None:
    """Read the pending-update marker if it exists."""
    marker_path = data_dir / PENDING_UPDATE_MARKER
    if not marker_path.exists():
        return None
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read pending-update marker: %s", e)
        return None


def increment_marker_attempts(data_dir: Path) -> int:
    """Increment the attempt counter on the pending marker.

    Returns the new attempt count. If count >= 2, automatic rollback
    should be triggered.
    """
    marker_path = data_dir / PENDING_UPDATE_MARKER
    data = read_pending_marker(data_dir)
    if data is None:
        return 0
    data["attempts"] = data.get("attempts", 0) + 1
    marker_path.write_text(json.dumps(data), encoding="utf-8")
    return data["attempts"]


def reset_marker_attempts(data_dir: Path) -> None:
    """Zero the attempt counter on the pending marker (clean shutdown).

    Called from graceful engine shutdown. A deliberate restart inside the
    60-second confirmation window (operator, API, cloud command) is not a
    crash, so the boot that follows it must count as a fresh first attempt —
    otherwise the attempts>=2 check reads any second boot in the window as
    a failed startup and rolls back a good update. A real crash never runs
    this, so crash-loop detection is unaffected.
    """
    marker_path = data_dir / PENDING_UPDATE_MARKER
    data = read_pending_marker(data_dir)
    if data is None or data.get("attempts", 0) == 0:
        return
    data["attempts"] = 0
    try:
        marker_path.write_text(json.dumps(data), encoding="utf-8")
        log.info("Reset pending-update attempt counter (clean shutdown)")
    except OSError as e:
        log.warning("Could not reset pending-update marker attempts: %s", e)


def clear_pending_marker(data_dir: Path) -> None:
    """Remove the pending-update marker (server started successfully)."""
    marker_path = data_dir / PENDING_UPDATE_MARKER
    if marker_path.exists():
        marker_path.unlink()
        log.info("Cleared pending-update marker (startup successful)")


def check_rollback_needed(data_dir: Path) -> bool:
    """Check if automatic rollback should be triggered.

    Called early in server startup. Returns True if the marker exists
    and attempts >= 2 (meaning the server has crashed at least once
    after applying the update — graceful shutdowns reset the counter
    via reset_marker_attempts, so only boots that follow a non-clean
    exit accumulate).
    """
    marker = read_pending_marker(data_dir)
    if marker is None:
        return False

    attempts = increment_marker_attempts(data_dir)
    if attempts >= 2:
        log.error(
            "Server has failed to start after update (%s -> %s). "
            "Automatic rollback will be triggered.",
            marker.get("from_version"),
            marker.get("to_version"),
        )
        return True

    log.info(
        "Pending update marker found (attempt %d). "
        "Server must stay running for 60 seconds to confirm success.",
        attempts,
    )
    return False


def restore_pre_update_data(data_dir: Path, project_path: Path | None = None) -> bool:
    """Restore user data from the pre-update backup during automatic rollback.

    Must run BEFORE the code rollback is initiated, while the version that
    applied the update is still executing: the rolled-back code may predate
    the running version's project-format migrations (and this function), so
    the data restore has to happen on this side of the code swap. Manual
    rollback deliberately does NOT restore data — the operator may roll back
    long after a confirmed update, and edits made since must not be silently
    discarded.

    Best-effort: a failed restore never blocks the code rollback. The system
    is crash-looping at this point; restoring service comes first, and a
    project file left in the newer format is exactly the pre-existing
    behavior (the loader warns on a newer stamp).
    """
    marker = read_pending_marker(data_dir)
    if marker is None:
        return False
    backup = _find_pre_update_backup(data_dir, marker)
    if backup is None:
        log.warning("No pre-update backup found; rolling back code only")
        return False

    from server.updater.backup import restore_user_data
    log.warning("Restoring user data from pre-update backup: %s", backup.name)
    try:
        return restore_user_data(data_dir, backup, project_path=project_path)
    except Exception:
        log.exception("Pre-update data restore failed; rolling back code only")
        return False


def _find_pre_update_backup(data_dir: Path, marker: dict) -> Path | None:
    """Locate the backup zip for the update being rolled back."""
    recorded = marker.get("backup")
    if recorded:
        path = Path(recorded)
        if path.is_file():
            return path
        log.warning("Pre-update backup recorded in marker is missing: %s", recorded)
    # Marker written by an older version (no backup field): fall back to the
    # newest backup taken for the version we're rolling back to.
    from_version = marker.get("from_version")
    if not from_version:
        return None
    candidates = sorted(
        (data_dir / "backups").glob(f"pre-update-v{from_version}-*.zip"),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def _installer_version(installer: Path) -> tuple[int, int, int, str]:
    """Parse the semver tuple out of an `OpenAVC-Setup-<version>.exe` filename.

    Compare using this instead of string-equal on filenames: a prerelease
    suffix or a different but equivalent normalization (e.g. "0.10.3-rc.1"
    vs "0.10.3-rc1") would otherwise leave rollback unable to find or
    exclude the matching installer.
    """
    from server.updater.checker import parse_semver
    return parse_semver(installer.stem.removeprefix("OpenAVC-Setup-"))


def _macos_previous_bundle(app_dir: Path) -> Path | None:
    """The ``OpenAVC.app.previous`` snapshot the macOS rollback restores.

    The launchd update wrapper snapshots the whole ``.app`` bundle to
    ``<bundle>.previous`` before swapping in an update, so rollback restores
    that. ``app_dir`` resolves *inside* the bundle (``sys._MEIPASS``), so walk
    up to the enclosing ``.app`` and name its sibling snapshot. Returns None
    when not running from a ``.app`` (e.g. source/dev).
    """
    for parent in (app_dir, *app_dir.parents):
        if parent.name.endswith(".app"):
            return parent.parent / f"{parent.name}.previous"
    return None


def can_rollback(app_dir: Path) -> bool:
    """Check if a previous version is available for rollback."""
    if sys.platform == "win32":
        # Windows: a cached installer for a version OTHER than the running one
        # must exist. The fresh-install path caches the running version's own
        # installer, which is not a rollback target.
        from server.system_config import get_system_config
        from server.updater.checker import parse_semver
        from server.version import __version__
        cache_dir = get_system_config().data_dir / "update-cache"
        if not cache_dir.exists():
            return False
        current_ver = parse_semver(__version__)
        return any(
            _installer_version(inst) != current_ver
            for inst in cache_dir.glob("OpenAVC-Setup-*.exe")
        )
    if sys.platform == "darwin":
        # macOS: the wrapper snapshots the whole .app to OpenAVC.app.previous.
        previous = _macos_previous_bundle(app_dir)
        return previous is not None and previous.is_dir()
    # Linux: check for /opt/openavc.previous/
    previous = app_dir.parent / f"{app_dir.name}.previous"
    return previous.is_dir()


def perform_rollback(
    data_dir: Path,
    from_version: str | None = None,
    to_version: str | None = None,
) -> bool:
    """Restore the previous version of OpenAVC.

    Called automatically when the server crashes after an update (attempts >= 2),
    or manually via the REST API.

    ``from_version``/``to_version`` are used as given when the caller supplies
    them (the manual API path knows both directly). Otherwise they fall back to
    the pending-update marker, which is the source for the automatic path. The
    marker is gone by the time a manual rollback runs (it's cleared once an
    update is confirmed), so without the override both would read "unknown".

    Returns True if rollback was initiated, False if no previous version available.
    """
    if from_version is None or to_version is None:
        marker = read_pending_marker(data_dir)
        if from_version is None:
            from_version = marker.get("from_version", "unknown") if marker else "unknown"
        if to_version is None:
            to_version = marker.get("to_version", "unknown") if marker else "unknown"

    if sys.platform == "win32":
        return _rollback_windows(data_dir, from_version, to_version)
    else:
        return _rollback_linux(data_dir, from_version, to_version)


def _rollback_windows(data_dir: Path, from_version: str, to_version: str) -> bool:
    """Rollback on Windows by re-running a cached previous installer."""
    cache_dir = data_dir / "update-cache"
    if not cache_dir.exists():
        log.error("Rollback failed: no update-cache directory")
        return False

    # Find the cached installer matching the version we're rolling back to.
    # Match semver-wise so a prerelease tag or renormalized suffix (e.g.
    # "0.10.3-rc.1" vs "0.10.3-rc1") doesn't break filename equality.
    from server.updater.checker import parse_semver
    installers = sorted(cache_dir.glob("OpenAVC-Setup-*.exe"))
    if not installers:
        log.error("Rollback failed: no cached installer found")
        return False

    # Prefer the exact from_version installer; fall back to any that isn't to_version
    target_ver = parse_semver(from_version)
    to_ver = parse_semver(to_version)
    installer = None
    for inst in installers:
        if _installer_version(inst) == target_ver:
            installer = inst
            break
    if installer is None:
        candidates = [i for i in installers if _installer_version(i) != to_ver]
        if not candidates:
            log.error("Rollback failed: no suitable installer (only v%s cached)", to_version)
            return False
        installer = candidates[-1]
    log.warning(
        "Automatic rollback: scheduling cached installer %s (v%s failed after update from v%s)",
        installer.name, to_version, from_version,
    )

    # Clear the marker before rollback to prevent rollback loops
    clear_pending_marker(data_dir)

    return _launch_installer_via_scheduler(installer, f"rollback-{from_version}")


def _rollback_linux(data_dir: Path, from_version: str, to_version: str) -> bool:
    """Write a rollback instruction for the ExecStartPre helper script.

    The actual rollback (swapping /opt/openavc.previous back into place) is
    performed by update-helper.sh which runs as root before the service starts,
    bypassing ProtectSystem=strict. The caller must exit the process after this
    returns True so systemd restarts the service and triggers the helper script.
    """
    rollback_marker = data_dir / ROLLBACK_MARKER
    tmp = data_dir / f"{ROLLBACK_MARKER}.tmp"
    # Stage-and-rename so a failed write can't leave the marker behind: the
    # helper consumes it unconditionally on the next service start, so a
    # marker that exists while this function reports failure (caller keeps
    # running) would silently downgrade on a later unrelated restart.
    try:
        tmp.write_text("", encoding="utf-8")
        os.replace(tmp, rollback_marker)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        log.error("Rollback failed: could not write rollback marker: %s", e)
        return False

    log.warning(
        "Rollback marker written (v%s failed after update from v%s). "
        "Rollback will apply on next service start.",
        to_version, from_version,
    )
    clear_pending_marker(data_dir)
    return True


def rollback_target_version(app_dir: Path) -> str:
    """Best-effort version that ``perform_rollback`` would restore to.

    Display-only. Derived from the same source the rollback actually uses (the
    highest cached installer on Windows, the ``.previous`` tree on Linux) rather
    than update history — history could name the version just rejected, or one
    with no cached installer. Returns "" when the target can't be determined
    (rollback may still be possible; the caller reports availability separately).
    """
    from server.updater.checker import parse_semver
    from server.version import __version__

    if sys.platform == "win32":
        from server.system_config import get_system_config
        cache_dir = get_system_config().data_dir / "update-cache"
        if not cache_dir.exists():
            return ""
        current = parse_semver(__version__)
        candidates = [
            inst for inst in cache_dir.glob("OpenAVC-Setup-*.exe")
            if _installer_version(inst) != current
        ]
        if not candidates:
            return ""
        best = max(candidates, key=_installer_version)
        return best.stem.removeprefix("OpenAVC-Setup-")

    if sys.platform == "darwin":
        # Display-only. The .app.previous snapshot does carry a bundled
        # pyproject.toml, but its path inside the frozen bundle isn't pinned
        # until the .app layout is finalized (Phase 3/8). Rollback availability
        # is reported separately by can_rollback(); leave the label blank rather
        # than read a guessed path.
        return ""

    # Linux: read the version recorded in the .previous install tree if present.
    previous = app_dir.parent / f"{app_dir.name}.previous"
    pyproject = previous / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return str(data.get("project", {}).get("version", "") or "")
        except (OSError, ValueError) as e:
            log.debug("Could not read rollback target version: %s", e)
    return ""
