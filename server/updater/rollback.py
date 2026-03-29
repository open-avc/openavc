"""
Rollback system for failed updates.

Supports both automatic rollback (server crash after update) and
manual rollback (user-initiated via API).
"""

from __future__ import annotations

import json
import logging
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
        # Windows: check for cached previous installer
        cache_dir = app_dir / "update-cache"
        return any(cache_dir.glob("OpenAVC-Setup-*.exe")) if cache_dir.exists() else False
    else:
        # Linux: check for /opt/openavc.previous/
        previous = app_dir.parent / f"{app_dir.name}.previous"
        return previous.is_dir()
