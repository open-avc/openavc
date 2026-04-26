"""
Rollback system for failed updates.

Supports both automatic rollback (server crash after update) and
manual rollback (user-initiated via API).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

PENDING_UPDATE_MARKER = "pending-update"


def write_pending_marker(data_dir: Path, from_version: str, to_version: str) -> None:
    """Write a marker file before applying an update.

    If the server crashes after update, the marker's presence on next
    startup triggers automatic rollback.
    """
    marker_path = data_dir / PENDING_UPDATE_MARKER
    marker_data = {
        "from_version": from_version,
        "to_version": to_version,
        "attempts": 0,
    }
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
    after applying the update).
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


def can_rollback(app_dir: Path) -> bool:
    """Check if a previous version is available for rollback."""
    if sys.platform == "win32":
        # Windows: check for cached previous installer in the data directory
        from server.system_config import get_system_config
        cache_dir = get_system_config().data_dir / "update-cache"
        return any(cache_dir.glob("OpenAVC-Setup-*.exe")) if cache_dir.exists() else False
    else:
        # Linux: check for /opt/openavc.previous/
        previous = app_dir.parent / f"{app_dir.name}.previous"
        return previous.is_dir()


def perform_rollback(data_dir: Path) -> bool:
    """Restore the previous version of OpenAVC.

    Called automatically when the server crashes after an update (attempts >= 2),
    or manually via the REST API.

    Returns True if rollback was initiated, False if no previous version available.
    """
    marker = read_pending_marker(data_dir)
    from_version = marker.get("from_version", "unknown") if marker else "unknown"
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

    # Find the cached installer matching the version we're rolling back to
    installers = sorted(cache_dir.glob("OpenAVC-Setup-*.exe"))
    if not installers:
        log.error("Rollback failed: no cached installer found")
        return False

    # Prefer the exact from_version installer; fall back to any that isn't to_version
    target_name = f"OpenAVC-Setup-{from_version}.exe"
    installer = None
    for inst in installers:
        if inst.name == target_name:
            installer = inst
            break
    if installer is None:
        candidates = [i for i in installers if to_version not in i.name]
        if not candidates:
            log.error("Rollback failed: no suitable installer (only v%s cached)", to_version)
            return False
        installer = candidates[-1]
    log.warning(
        "Automatic rollback: running cached installer %s (v%s failed after update from v%s)",
        installer.name, to_version, from_version,
    )

    # Clear the marker before rollback to prevent rollback loops
    clear_pending_marker(data_dir)

    try:
        subprocess.Popen(
            [
                str(installer),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
            ],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return True
    except OSError as e:
        log.error("Rollback failed: could not launch installer: %s", e)
        return False


def _rollback_linux(data_dir: Path, from_version: str, to_version: str) -> bool:
    """Write a rollback instruction for the ExecStartPre helper script.

    The actual rollback (swapping /opt/openavc.previous back into place) is
    performed by update-helper.sh which runs as root before the service starts,
    bypassing ProtectSystem=strict. The caller must exit the process after this
    returns True so systemd restarts the service and triggers the helper script.
    """
    rollback_marker = data_dir / "apply-rollback"
    try:
        rollback_marker.write_text("", encoding="utf-8")
    except OSError as e:
        log.error("Rollback failed: could not write rollback marker: %s", e)
        return False

    log.warning(
        "Rollback marker written (v%s failed after update from v%s). "
        "Rollback will apply on next service start.",
        to_version, from_version,
    )
    clear_pending_marker(data_dir)
    return True
