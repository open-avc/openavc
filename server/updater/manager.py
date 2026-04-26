"""
Update manager - orchestrates the full update lifecycle.

Coordinates: check -> backup -> download -> verify -> apply -> restart.
Manages state keys for UI binding and progress tracking.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from server.updater.checker import ReleaseInfo, UpdateChecker
from server.updater.platform import (
    DeploymentType,
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
        self._maintenance_task: asyncio.Task | None = None
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

    def _get_artifact_name(self, release: ReleaseInfo) -> str:
        """Determine the platform-specific artifact name for this release."""
        if self._deployment_type == DeploymentType.WINDOWS_INSTALLER:
            return f"OpenAVC-Setup-{release.version}.exe"
        # Linux: detect architecture
        import platform
        machine = platform.machine().lower()
        if machine in ("x86_64", "amd64"):
            arch = "x86_64"
        elif machine in ("aarch64", "arm64"):
            arch = "arm64"
        else:
            arch = machine
        return f"openavc-{release.version}-linux-{arch}.tar.gz"

    def _find_asset_url(self, release: ReleaseInfo, artifact_name: str) -> str:
        """Find the download URL for a specific artifact in the release assets."""
        for asset in release.assets:
            if asset.get("name") == artifact_name:
                return asset.get("url", "")
        return ""

    async def _download_update(self, release: ReleaseInfo) -> Path:
        """Download the platform-specific update artifact from GitHub release assets.

        Returns the path to the downloaded file.
        """
        artifact_name = self._get_artifact_name(release)
        artifact_url = self._find_asset_url(release, artifact_name)
        if not artifact_url:
            raise RuntimeError(f"Artifact '{artifact_name}' not found in release assets")

        artifact_path = await self._download_artifact(artifact_url, artifact_name)

        # Verify checksum via SHA256SUMS.txt from release assets
        checksum_url = self._find_asset_url(release, "SHA256SUMS.txt")
        if checksum_url:
            await self._verify_checksum(checksum_url=checksum_url,
                                        artifact_path=artifact_path, artifact_name=artifact_name)
        else:
            log.error(
                "No SHA256SUMS.txt in release assets for v%s — update artifact "
                "was NOT verified. This is expected for development releases.",
                release.version,
            )

        return artifact_path

    async def _download_artifact(self, url: str, filename: str) -> Path:
        """Download a file from a URL with progress tracking and resume support.

        Returns the path to the downloaded file.
        """
        download_dir = self._data_dir / "update-cache"
        download_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = download_dir / filename

        disk = shutil.disk_usage(str(download_dir))
        if disk.free < 500 * 1024 * 1024:
            free_mb = disk.free // (1024 * 1024)
            raise RuntimeError(
                f"Insufficient disk space for update: {free_mb} MB free, need at least 500 MB"
            )

        log.info("Downloading update artifact: %s", url)

        if artifact_path.exists():
            artifact_path.unlink()

        downloaded = 0
        total = 0
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))

                with open(artifact_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(int(downloaded * 100 / total), 99)
                            self._set_state("system.update_progress", pct)

        log.info("Downloaded: %s (%d bytes)", artifact_path.name, artifact_path.stat().st_size)
        return artifact_path

    async def _verify_checksum(self, *, checksum_url: str, artifact_path: Path,
                               artifact_name: str) -> None:
        """Download SHA256SUMS.txt and verify the artifact checksum."""
        self._set_state("system.update_status", "verifying")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
            resp = await c.get(checksum_url)
            resp.raise_for_status()
            checksums_text = resp.text

        # Parse: each line is "hash  filename"
        expected_hash = ""
        for line in checksums_text.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].strip("*") == artifact_name:
                expected_hash = parts[0].lower()
                break

        if not expected_hash:
            raise RuntimeError(f"Checksum for '{artifact_name}' not found in SHA256SUMS.txt")

        # Compute SHA256 of downloaded file
        sha256 = hashlib.sha256()
        with open(artifact_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                sha256.update(chunk)
        actual_hash = sha256.hexdigest().lower()

        if actual_hash != expected_hash:
            artifact_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Checksum mismatch for {artifact_name}: "
                f"expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
            )

        log.info("Checksum verified for %s", artifact_name)

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
            from server.updater.backup import create_backup, cleanup_old_backups
            backup_path = create_backup(self._data_dir, __version__)
            cleanup_old_backups(self._data_dir)
            log.info("Pre-update backup created: %s", backup_path)

            # Step 2: Download + verify checksum
            self._set_state("system.update_status", "downloading")
            self._set_state("system.update_progress", 0)
            artifact_path = await self._download_update(release)
            self._set_state("system.update_progress", 100)

            # Step 3: Write pending-update marker
            from server.updater.rollback import write_pending_marker
            write_pending_marker(self._data_dir, __version__, release.version)

            # Step 4: Apply (platform-specific)
            self._set_state("system.update_status", "applying")
            if self._deployment_type == DeploymentType.WINDOWS_INSTALLER:
                self._apply_windows(artifact_path, release.version)
            elif self._deployment_type == DeploymentType.LINUX_PACKAGE:
                self._apply_linux(artifact_path, release.version)

            # Step 5: Record success and restart
            self._add_history_entry(__version__, release.version, "success")

            self._set_state("system.update_status", "restarting")
            log.info("Update to v%s applied, restarting...", release.version)

            # Schedule restart in background so the response can be sent first
            asyncio.get_running_loop().call_later(1.0, self._restart_process)

            return {"success": True, "message": f"Update to v{release.version} started"}

        except Exception as e:
            error_msg = f"Update failed: {e}"
            log.exception(error_msg)
            self._set_state("system.update_status", "error")
            self._set_state("system.update_error", error_msg)
            self._add_history_entry(__version__, release.version, "failed", error_msg)
            return {"success": False, "error": error_msg}
        finally:
            self._update_in_progress = False

    async def apply_cloud_update(
        self, target_version: str, update_url: str,
        checksum_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Download and apply an update using a cloud-provided URL and checksum.

        Unlike apply_update() which discovers artifacts from GitHub Releases,
        this method uses the exact URL and checksum the cloud platform provides.
        """
        if self._update_in_progress:
            return {"success": False, "error": "Update already in progress"}

        if not can_self_update(self._deployment_type):
            return {
                "success": False,
                "error": "This deployment type does not support self-update.",
                "instructions": update_instructions(self._deployment_type, target_version),
            }

        self._update_in_progress = True
        self._set_state("system.update_error", "")

        try:
            # Step 1: Backup
            self._set_state("system.update_status", "backing_up")
            from server.updater.backup import create_backup, cleanup_old_backups
            backup_path = create_backup(self._data_dir, __version__)
            cleanup_old_backups(self._data_dir)
            log.info("Pre-update backup created: %s", backup_path)

            # Step 2: Download from cloud-provided URL
            # Derive filename from URL path (GitHub URLs end with the artifact name)
            from urllib.parse import urlparse
            url_path = urlparse(update_url).path
            filename = url_path.rsplit("/", 1)[-1] if "/" in url_path else f"update-{target_version}"

            self._set_state("system.update_status", "downloading")
            self._set_state("system.update_progress", 0)
            artifact_path = await self._download_artifact(update_url, filename)
            self._set_state("system.update_progress", 100)

            # Step 3: Verify checksum if provided
            if checksum_sha256:
                self._set_state("system.update_status", "verifying")
                self._verify_hash(artifact_path, checksum_sha256)

            # Step 4: Write pending-update marker
            from server.updater.rollback import write_pending_marker
            write_pending_marker(self._data_dir, __version__, target_version)

            # Step 5: Apply (platform-specific)
            self._set_state("system.update_status", "applying")
            if self._deployment_type == DeploymentType.WINDOWS_INSTALLER:
                self._apply_windows(artifact_path, target_version)
            elif self._deployment_type == DeploymentType.LINUX_PACKAGE:
                self._apply_linux(artifact_path, target_version)

            # Step 6: Record success and restart
            self._add_history_entry(__version__, target_version, "success")

            self._set_state("system.update_status", "restarting")
            log.info("Update to v%s applied (cloud URL), restarting...", target_version)

            asyncio.get_running_loop().call_later(1.0, self._restart_process)

            return {"success": True, "message": f"Update to v{target_version} started"}

        except Exception as e:
            error_msg = f"Update failed: {e}"
            log.exception(error_msg)
            self._set_state("system.update_status", "error")
            self._set_state("system.update_error", error_msg)
            self._add_history_entry(__version__, target_version, "failed", error_msg)
            return {"success": False, "error": error_msg}
        finally:
            self._update_in_progress = False

    def _verify_hash(self, artifact_path: Path, expected_hash: str) -> None:
        """Verify a downloaded artifact against a known SHA-256 hash."""
        sha256 = hashlib.sha256()
        with open(artifact_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                sha256.update(chunk)
        actual_hash = sha256.hexdigest().lower()
        expected_hash = expected_hash.lower()

        if actual_hash != expected_hash:
            artifact_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Checksum mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
            )

        log.info("Checksum verified for %s", artifact_path.name)

    def _apply_windows(self, artifact_path: Path, new_version: str) -> None:
        """Apply update on Windows via silent installer execution.

        The Inno Setup installer caches itself during installation
        (CacheInstallerForRollback in setup.iss), so rollback is always possible.
        """
        log.info("Launching silent installer: %s", artifact_path.name)

        # Launch installer silently — it will stop the NSSM service,
        # replace files, and restart the service automatically.
        # NSSM exit code 42 (set in install-service.bat) prevents NSSM from
        # restarting the process before the installer finishes.
        subprocess.Popen(
            [
                str(artifact_path),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
            ],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32" else 0,
        )

    def _apply_linux(self, artifact_path: Path, to_version: str) -> None:
        """Write update instruction for the ExecStartPre helper script.

        The helper script (update-helper.sh) runs as root before the service
        starts, bypassing ProtectSystem=strict. It reads the instruction file,
        backs up the current install, extracts the tarball, and rebuilds the
        venv. After this method returns, the caller exits the process so
        systemd restarts the service and triggers the helper script.
        """
        instruction = {
            "artifact": str(artifact_path),
            "from_version": __version__,
            "to_version": to_version,
        }
        instruction_path = self._data_dir / "apply-update.json"
        instruction_path.write_text(json.dumps(instruction), encoding="utf-8")
        log.info("Wrote update instruction: %s -> v%s (%s)", __version__, to_version, instruction_path)

    def _restart_process(self) -> None:
        """Exit the process so the external update mechanism can apply changes.

        Windows: exit code 42 tells NSSM not to restart (installer handles it).
        Linux: exit code 0 triggers systemd restart; ExecStartPre applies the update.
        """
        import os
        if self._deployment_type == DeploymentType.WINDOWS_INSTALLER:
            log.info("Exiting for update — installer handles restart (exit 42)")
            os._exit(42)
        elif self._deployment_type == DeploymentType.LINUX_PACKAGE:
            log.info("Exiting for update — systemd will apply and restart (exit 0)")
            os._exit(0)

    async def rollback(self) -> dict[str, Any]:
        """Rollback to the previous version."""
        from server.updater.rollback import can_rollback, perform_rollback
        from server.system_config import APP_DIR

        if self._update_in_progress:
            return {"success": False, "error": "Cannot rollback while an update is in progress"}

        if not can_rollback(APP_DIR):
            return {"success": False, "error": "No previous version available for rollback"}

        # Create a backup of current state before rolling back
        self._set_state("system.update_status", "backing_up")
        try:
            from server.updater.backup import create_backup
            create_backup(self._data_dir, __version__)
        except Exception as e:
            log.warning("Pre-rollback backup failed: %s", e)

        self._set_state("system.update_status", "applying")
        success = perform_rollback(self._data_dir)

        if success:
            self._add_history_entry(__version__, "rollback", "success")
            self._set_state("system.update_status", "restarting")
            # Schedule process exit so the API response can be sent first.
            # On Windows, the installer (launched by perform_rollback) handles restart.
            # On Linux, systemd restarts the service and ExecStartPre applies the rollback.
            asyncio.get_running_loop().call_later(1.0, self._restart_process)
            return {"success": True, "message": "Rollback initiated, server will restart"}
        else:
            self._set_state("system.update_status", "error")
            self._set_state("system.update_error", "Rollback failed")
            return {"success": False, "error": "Rollback failed. Check server logs."}

    def get_status(self) -> dict[str, Any]:
        """Get current update status."""
        from server.updater.rollback import can_rollback
        from server.system_config import APP_DIR

        has_rollback = can_rollback(APP_DIR)
        rollback_version = ""
        if has_rollback:
            # Check history for the version we'd roll back to
            for entry in self._history:
                if entry.get("status") in ("success", "applied"):
                    rollback_version = entry.get("from_version", "")
                    break

        return {
            "current_version": __version__,
            "deployment_type": self._deployment_type.value,
            "can_self_update": can_self_update(self._deployment_type),
            "update_available": self._state.get("system.update_available", "") if self._state else "",
            "update_channel": self._state.get("system.update_channel", "stable") if self._state else "stable",
            "update_status": self._state.get("system.update_status", "idle") if self._state else "idle",
            "update_progress": self._state.get("system.update_progress", 0) if self._state else 0,
            "update_error": self._state.get("system.update_error", "") if self._state else "",
            "rollback_available": has_rollback,
            "rollback_version": rollback_version,
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
            # Check shortly after startup
            await asyncio.sleep(30)
            try:
                await self.check_for_updates()
            except Exception as e:
                log.warning("Update check failed: %s", e)

            while True:
                await asyncio.sleep(interval_hours * 3600)
                try:
                    await self.check_for_updates()
                except Exception as e:
                    log.warning("Update check failed: %s", e)

        self._auto_check_task = asyncio.create_task(_periodic_check())
        log.info("Automatic update check every %d hour(s)", interval_hours)

    async def stop_auto_check(self) -> None:
        """Stop periodic background update checks and maintenance window task."""
        if self._auto_check_task and not self._auto_check_task.done():
            self._auto_check_task.cancel()
            try:
                await self._auto_check_task
            except asyncio.CancelledError:
                pass
            self._auto_check_task = None
        if self._maintenance_task and not self._maintenance_task.done():
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
            self._maintenance_task = None

    def apply_update_policy(self, policy_config: dict[str, Any]) -> None:
        """Apply an update policy received from the cloud.

        Args:
            policy_config: Dict with keys: policy, maintenance_window_start,
                           maintenance_window_end, maintenance_window_tz
        """
        policy = policy_config.get("policy", "manual")
        log.info("Cloud update policy: %s", policy)

        # Cancel any existing maintenance task
        if self._maintenance_task and not self._maintenance_task.done():
            self._maintenance_task.cancel()
            self._maintenance_task = None

        if policy == "auto" and can_self_update(self._deployment_type):
            window_start = policy_config.get("maintenance_window_start")
            window_end = policy_config.get("maintenance_window_end")
            window_tz = policy_config.get("maintenance_window_tz")
            if window_start and window_end:
                self._maintenance_task = asyncio.create_task(
                    self._maintenance_window_loop(window_start, window_end, window_tz)
                )
                log.info(
                    "Auto-update scheduled during %s-%s %s",
                    window_start, window_end, window_tz or "UTC",
                )

    async def _maintenance_window_loop(
        self, window_start: str, window_end: str, tz_name: str | None
    ) -> None:
        """Check periodically if we're in the maintenance window and apply updates."""
        from datetime import time as dt_time
        try:
            start_h, start_m = (int(x) for x in window_start.split(":"))
            end_h, end_m = (int(x) for x in window_end.split(":"))
        except (ValueError, AttributeError):
            log.warning("Invalid maintenance window format: %s-%s", window_start, window_end)
            return

        start_time = dt_time(start_h, start_m)
        max_retries = 7  # Days to retry

        try:
            for _ in range(max_retries):
                # Wait until the maintenance window opens
                await self._sleep_until_window(start_time, tz_name)

                # Check if an update is available
                result = await self.check_for_updates()
                if not result.get("update_available"):
                    # No update, sleep until next day's window
                    await asyncio.sleep(24 * 3600)
                    continue

                # Check if system is actively being used
                if self._is_system_active():
                    log.info("System is active during maintenance window, deferring update")
                    await asyncio.sleep(24 * 3600)
                    continue

                # Apply the update
                log.info("Maintenance window: applying update")
                await self.apply_update()
                return  # Update applied (or failed), stop the loop

        except asyncio.CancelledError:
            pass

    async def _sleep_until_window(self, start_time, tz_name: str | None) -> None:
        """Sleep until the next occurrence of the maintenance window start time."""
        while True:
            now = datetime.now(timezone.utc)
            # Convert to target timezone if specified
            if tz_name:
                try:
                    from zoneinfo import ZoneInfo
                    now_local = now.astimezone(ZoneInfo(tz_name))
                except (ImportError, KeyError):
                    now_local = now
            else:
                now_local = now

            current_time = now_local.time()
            if current_time >= start_time:
                # Window already started or passed, check if we're still in it
                return
            # Sleep until window start (check every 5 minutes to handle drift)
            await asyncio.sleep(300)

    def _is_system_active(self) -> bool:
        """Check if the system is actively being used (WS clients connected)."""
        if not self._state:
            return False
        # Check for connected WebSocket clients (Programmer or Panel)
        ws_count = self._state.get("system.ws_clients", 0)
        return isinstance(ws_count, int) and ws_count > 0
