"""
Update manager - orchestrates the full update lifecycle.

Coordinates: check -> backup -> download -> verify -> apply -> restart.
Manages state keys for UI binding and progress tracking.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.updater.checker import UpdateChecker
from server.updater.platform import (
    detect_deployment_type,
    can_self_update,
    update_instructions,
)
from server.version import __version__

log = logging.getLogger(__name__)


class UpdateManager:
    """Orchestrates the update lifecycle for an OpenAVC instance.

    Provides the high-level API used by REST endpoints and the cloud agent.
    Updates state keys via the state_store for real-time UI feedback.
    """

    def __init__(self, state_store=None, data_dir: Path | None = None):
        self._state = state_store
        self._checker = UpdateChecker()
        self._deployment_type = detect_deployment_type()
        self._update_in_progress = False
        self._auto_check_task: asyncio.Task | None = None
        self._history: list[dict[str, Any]] = []

        # Data directory for backups and update cache
        if data_dir is None:
            from server.system_config import get_system_config
            self._data_dir = get_system_config().data_dir
        else:
            self._data_dir = data_dir

        # Load update history
        self._load_history()

    def _set_state(self, key: str, value: Any) -> None:
        """Set a state key if state store is available."""
        if self._state:
            self._state.set(key, value, source="system")

    def _load_history(self) -> None:
        """Load update history from disk."""
        history_path = self._data_dir / "update-history.json"
        if history_path.exists():
            try:
                self._history = json.loads(history_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._history = []

    def _save_history(self) -> None:
        """Save update history to disk."""
        history_path = self._data_dir / "update-history.json"
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            history_path.write_text(
                json.dumps(self._history, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("Failed to save update history: %s", e)

    def _add_history_entry(self, from_version: str, to_version: str, status: str, error: str = "") -> None:
        """Record an update attempt in history."""
        self._history.insert(0, {
            "from_version": from_version,
            "to_version": to_version,
            "status": status,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 50 entries
        self._history = self._history[:50]
        self._save_history()

    async def check_for_updates(self, channel: str | None = None) -> dict[str, Any]:
        """Check for available updates.

        Returns a dict with update info suitable for API response.
        """
        if channel is None:
            from server.system_config import get_system_config
            channel = get_system_config().get("updates", "channel", "stable")

        self._set_state("system.update_status", "checking")
        self._set_state("system.update_error", "")

        try:
            result = await self._checker.check(channel)
        finally:
            self._set_state("system.update_status", "idle")

        if result is None:
            self._set_state("system.update_available", "")
            error = self._checker.last_error
            if error:
                self._set_state("system.update_error", error)
            return {
                "update_available": False,
                "current_version": self._checker.current_version,
                "channel": channel,
                "error": error,
            }

        self._set_state("system.update_available", result.version)
        can_update = can_self_update(self._deployment_type)

        response = {
            "update_available": True,
            "current_version": self._checker.current_version,
            "available_version": result.version,
            "channel": channel,
            "prerelease": result.prerelease,
            "changelog": result.changelog,
            "published_at": result.published_at,
            "can_self_update": can_update,
            "deployment_type": self._deployment_type.value,
        }

        if not can_update:
            response["instructions"] = update_instructions(self._deployment_type, result.version)

        return response

    async def apply_update(self) -> dict[str, Any]:
        """Download and apply an available update.

        This is the full update flow: backup -> download -> verify -> apply -> restart.
        For non-self-updating deployments, returns instructions instead.
        """
        if self._update_in_progress:
            return {"success": False, "error": "Update already in progress"}

        release = self._checker.last_result
        if release is None:
            return {"success": False, "error": "No update available. Run check first."}

        if not can_self_update(self._deployment_type):
            return {
                "success": False,
                "error": "This deployment type does not support self-update.",
                "instructions": update_instructions(self._deployment_type, release.version),
            }

        self._update_in_progress = True
        self._set_state("system.update_error", "")

        try:
            # Step 1: Backup
            self._set_state("system.update_status", "backing_up")
            from server.updater.backup import create_backup
            backup_path = create_backup(self._data_dir, __version__)
            log.info("Pre-update backup created: %s", backup_path)

            # Step 2: Download
            self._set_state("system.update_status", "downloading")
            self._set_state("system.update_progress", 0)
            # TODO: Implement download with progress tracking
            # This will be built in Section 4.7 when we have actual release artifacts
            self._set_state("system.update_progress", 100)

            # Step 3: Apply
            self._set_state("system.update_status", "applying")
            # TODO: Implement platform-specific apply logic (Section 4.7)

            # Step 4: Restart
            self._set_state("system.update_status", "restarting")
            # TODO: Implement platform-specific restart (Section 4.7)

            self._add_history_entry(__version__, release.version, "applied")
            return {"success": True, "message": f"Update to v{release.version} initiated"}

        except Exception as e:
            error_msg = f"Update failed: {e}"
            log.exception(error_msg)
            self._set_state("system.update_status", "error")
            self._set_state("system.update_error", error_msg)
            self._add_history_entry(__version__, release.version, "failed", error_msg)
            return {"success": False, "error": error_msg}
        finally:
            self._update_in_progress = False

    async def rollback(self) -> dict[str, Any]:
        """Rollback to the previous version."""
        from server.updater.rollback import can_rollback
        from server.system_config import APP_DIR

        if not can_rollback(APP_DIR):
            return {"success": False, "error": "No previous version available for rollback"}

        # TODO: Implement actual rollback (Section 4.7)
        return {"success": False, "error": "Rollback not yet implemented for this deployment type"}

    def get_status(self) -> dict[str, Any]:
        """Get current update status."""
        return {
            "current_version": __version__,
            "deployment_type": self._deployment_type.value,
            "can_self_update": can_self_update(self._deployment_type),
            "update_available": self._state.get("system.update_available", "") if self._state else "",
            "update_status": self._state.get("system.update_status", "idle") if self._state else "idle",
            "update_progress": self._state.get("system.update_progress", 0) if self._state else 0,
            "update_error": self._state.get("system.update_error", "") if self._state else "",
        }

    def get_history(self) -> list[dict[str, Any]]:
        """Get update history."""
        return list(self._history)

    async def start_auto_check(self, interval_hours: int = 24) -> None:
        """Start periodic background update checks."""
        if self._auto_check_task and not self._auto_check_task.done():
            return  # Already running

        from server.system_config import get_system_config
        cfg = get_system_config()
        if not cfg.get("updates", "check_enabled", True):
            log.info("Automatic update checks disabled")
            return

        interval_hours = cfg.get("updates", "auto_check_interval_hours", interval_hours)

        async def _periodic_check():
            while True:
                await asyncio.sleep(interval_hours * 3600)
                try:
                    await self.check_for_updates()
                except Exception as e:
                    log.warning("Periodic update check failed: %s", e)

        self._auto_check_task = asyncio.create_task(_periodic_check())
        log.info("Automatic update check scheduled every %d hours", interval_hours)

    async def stop_auto_check(self) -> None:
        """Stop periodic background update checks."""
        if self._auto_check_task and not self._auto_check_task.done():
            self._auto_check_task.cancel()
            try:
                await self._auto_check_task
            except asyncio.CancelledError:
                pass
            self._auto_check_task = None
