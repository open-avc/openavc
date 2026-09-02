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

from openavc.utils.spawn import CREATE_NO_WINDOW

log = logging.getLogger(__name__)

PENDING_UPDATE_MARKER = "pending-update"
ROLLBACK_MARKER = "apply-rollback"

# Where the kernel publishes an identity for the running boot. Linux-only,
# which covers the Linux package, the Pi image, Docker and the Android
# appliance — every deployment that gets power-cycled as a matter of course.
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"


def _current_boot_id() -> str | None:
    """Identity of the OS boot this process belongs to, or None.

    Used to tell a startup that crashed from one the machine cut short. There
    is no portable way to ask, and Windows and macOS have no answer here at
    all; None means "can't tell", and every caller must stay correct without
    it rather than assume anything.
    """
    try:
        with open(BOOT_ID_PATH, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


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

    The marker is the question "did the new version get itself running?" left
    on disk. It is cleared the moment the engine finishes starting, so one
    still present at startup says the previous boot never got that far.
    ``backup_path`` records the pre-update backup zip so that rollback can
    restore user data from it, and ``boot`` records which OS boot the attempt
    counter belongs to (see ``record_startup_attempt``).
    """
    marker_path = data_dir / PENDING_UPDATE_MARKER
    marker_data = {
        "from_version": from_version,
        "to_version": to_version,
        "attempts": 0,
    }
    boot = _current_boot_id()
    if boot is not None:
        marker_data["boot"] = boot
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


def record_startup_attempt(data_dir: Path) -> int:
    """Count this boot as one attempt at starting the updated version.

    Returns the number of consecutive startups **within the current OS boot**
    that failed to get the engine up; at 2 the caller rolls back.

    Scoping the count to one boot is what keeps a power cut from being read as
    a crash. A version that genuinely cannot run is relaunched immediately by
    whatever supervises it — systemd, NSSM, the appliance shell — so it fails
    twice inside the same boot within seconds, which is the signal rollback
    exists for. A panel that loses power mid-startup, or is rebooted by the
    person who just installed the update, comes back on a NEW boot, and that
    tells us nothing about the version, so the count starts again.

    Where the boot cannot be identified (Windows, macOS: no /proc) every
    startup counts, which is what this did everywhere before. That is safe now
    only because the marker is cleared the moment the engine starts: a restart
    after a successful start no longer finds a marker to count against.
    """
    marker_path = data_dir / PENDING_UPDATE_MARKER
    data = read_pending_marker(data_dir)
    if data is None:
        return 0

    boot = _current_boot_id()
    recorded = data.get("boot")
    same_boot = boot is None or recorded is None or boot == recorded
    if same_boot:
        data["attempts"] = data.get("attempts", 0) + 1
    else:
        log.info(
            "Machine restarted since the last startup attempt on this update; "
            "counting it as the first attempt rather than a crash",
        )
        data["attempts"] = 1
        data["boot"] = boot

    marker_path.write_text(json.dumps(data), encoding="utf-8")
    return data["attempts"]


def clear_pending_marker(data_dir: Path) -> None:
    """Remove the pending-update marker (server started successfully)."""
    marker_path = data_dir / PENDING_UPDATE_MARKER
    if marker_path.exists():
        marker_path.unlink()
        log.info("Cleared pending-update marker (startup successful)")


def confirm_startup(data_dir: Path) -> None:
    """Clear the pending-update marker once the engine is running.

    Called from ``main._initialize_engine`` the moment ``engine.start()``
    returns: the new version imported, loaded the project, brought up devices
    and plugins, and the HTTP listener was already serving before any of that.
    That is a direct observation that the update can run, and it is the whole
    test — this used to be a 60-second timer instead, which measured whether
    anybody touched the box rather than whether the new version worked, and on
    an appliance (restarted by its supervisor, power-cycled in a room) it
    reverted good updates as a matter of course.

    The marker is always cleared (so the attempt counter can't trip on a later
    restart), but only an update that actually changed the running version is
    logged as a success. A marker that survived a *failed* apply (e.g. the
    helper aborted, version unchanged) must not log "confirmed successful"
    against the target it never reached.
    """
    from openavc.version import __version__

    marker = read_pending_marker(data_dir)
    if not marker:
        return
    clear_pending_marker(data_dir)
    from_version = marker.get("from_version", "")
    to_version = marker.get("to_version", "")
    # Mirror UpdateManager._load_history: the update applied if the running
    # version reached the target, or simply moved off the version we started
    # from (handles release-tag/pyproject skew).
    applied = (
        (bool(to_version) and __version__ == to_version)
        or (bool(from_version) and __version__ != from_version)
    )
    if applied:
        log.info(
            "Update confirmed successful after startup (v%s -> v%s)",
            from_version, __version__,
        )
    else:
        log.warning(
            "Update to v%s did not take effect (still running v%s); "
            "cleared stale pending-update marker",
            to_version, __version__,
        )


def check_rollback_needed(data_dir: Path) -> bool:
    """Check if automatic rollback should be triggered.

    Called early in server startup. A marker still on disk means the previous
    startup never got the engine running (``confirm_startup`` clears it as
    soon as it does), so the count here is a count of failed startups, not of
    restarts. Returns True at 2 within one OS boot — see
    ``record_startup_attempt`` for why the boot matters.
    """
    marker = read_pending_marker(data_dir)
    if marker is None:
        return False

    attempts = record_startup_attempt(data_dir)
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
        "The update is confirmed once the engine finishes starting.",
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

    from openavc.updater.backup import restore_user_data
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
    from openavc.updater.checker import parse_semver
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
        from openavc.system_config import get_system_config
        from openavc.updater.checker import parse_semver
        from openavc.version import __version__
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
    automatic: bool = False,
) -> bool:
    """Restore the previous version of OpenAVC.

    Called automatically when the server crashes after an update (attempts >= 2),
    or manually via the REST API.

    ``from_version``/``to_version`` are used as given when the caller supplies
    them (the manual API path knows both directly). Otherwise they fall back to
    the pending-update marker, which is the source for the automatic path. The
    marker is gone by the time a manual rollback runs (it's cleared once an
    update is confirmed), so without the override both would read "unknown".

    ``automatic`` says which of the two callers this is, and only the log line
    reads it. Both paths land here, and the message used to assert the new
    version "failed after update" either way -- so a deliberate rollback a
    person asked for was recorded in the log as a crash. That reads as a fault
    to whoever opens the log later, which is exactly when it misleads.

    Returns True if rollback was initiated, False if no previous version available.
    """
    if from_version is None or to_version is None:
        marker = read_pending_marker(data_dir)
        if from_version is None:
            from_version = marker.get("from_version", "unknown") if marker else "unknown"
        if to_version is None:
            to_version = marker.get("to_version", "unknown") if marker else "unknown"

    if sys.platform == "win32":
        return _rollback_windows(data_dir, from_version, to_version, automatic)
    else:
        return _rollback_linux(data_dir, from_version, to_version, automatic)


def _rollback_reason(automatic: bool, from_version: str, to_version: str) -> str:
    """One sentence naming why a rollback is happening, for the log."""
    if automatic:
        return f"v{to_version} failed to start after updating from v{from_version}"
    return f"requested: leaving v{to_version} and restoring v{from_version}"


def _rollback_windows(
    data_dir: Path, from_version: str, to_version: str, automatic: bool = False,
) -> bool:
    """Rollback on Windows by re-running a cached previous installer."""
    cache_dir = data_dir / "update-cache"
    if not cache_dir.exists():
        log.error("Rollback failed: no update-cache directory")
        return False

    # Find the cached installer matching the version we're rolling back to.
    # Match semver-wise so a prerelease tag or renormalized suffix (e.g.
    # "0.10.3-rc.1" vs "0.10.3-rc1") doesn't break filename equality.
    from openavc.updater.checker import parse_semver
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
        "Rollback (%s): scheduling cached installer %s -- %s",
        "automatic" if automatic else "manual",
        installer.name,
        _rollback_reason(automatic, from_version, to_version),
    )

    # Clear the marker before rollback to prevent rollback loops
    clear_pending_marker(data_dir)

    return _launch_installer_via_scheduler(installer, f"rollback-{from_version}")


def _rollback_linux(
    data_dir: Path, from_version: str, to_version: str, automatic: bool = False,
) -> bool:
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
        "Rollback marker written (%s) -- %s. "
        "Rollback will apply on next service start.",
        "automatic" if automatic else "manual",
        _rollback_reason(automatic, from_version, to_version),
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
    from openavc.updater.checker import parse_semver
    from openavc.version import __version__

    if sys.platform == "win32":
        from openavc.system_config import get_system_config
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
        # The .app.previous snapshot carries the same bundled pyproject.toml the
        # Linux tree does, one level deeper: the frozen payload sits under
        # Contents/Resources/server/_internal/. This used to return "" because
        # that path was not settled, which put "Rollback to v?" on the one
        # screen where a person most wants to know what they are agreeing to.
        previous = _macos_previous_bundle(app_dir)
        if previous is None:
            return ""
        return _version_from_pyproject(
            previous / "Contents" / "Resources" / "server" / "_internal" / "pyproject.toml"
        )

    # Linux: read the version recorded in the .previous install tree if present.
    previous = app_dir.parent / f"{app_dir.name}.previous"
    return _version_from_pyproject(previous / "pyproject.toml")


def _version_from_pyproject(pyproject: Path) -> str:
    """The ``project.version`` recorded in a bundled pyproject.toml, or "".

    Shared by the macOS and Linux rollback-target lookups: both snapshot the
    previous install as a whole tree, and both carry the pyproject that names
    its version. Returns "" for a missing or unreadable file — the caller
    reports rollback AVAILABILITY separately, so a blank label never means
    "cannot roll back".
    """
    if not pyproject.is_file():
        return ""
    try:
        import tomllib
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        return str(data.get("project", {}).get("version", "") or "")
    except (OSError, ValueError) as e:
        log.debug("Could not read rollback target version: %s", e)
        return ""
