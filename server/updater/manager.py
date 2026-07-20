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

    def __init__(
        self,
        state_store=None,
        data_dir: Path | None = None,
        project_path: Path | None = None,
    ):
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

        # Project file path — used so the pre-update backup can also archive
        # project.avc + state.json + scripts/ + assets/ when OPENAVC_PROJECT
        # points outside data_dir.
        if project_path is None:
            from server import config as server_config
            project_path = Path(server_config.PROJECT_PATH)
        self._project_path = project_path

        # Load update history
        self._load_history()

        # Re-surface a cloud-staged update across restarts. The staged record
        # persists on disk, but state keys don't — without this the IDE would
        # lose sight of a staged update the moment the server restarts.
        staged = self.get_staged_update()
        if staged:
            self._set_state(
                "system.update_staged_version", staged.get("target_version", "")
            )

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
            except (json.JSONDecodeError, OSError) as e:
                # Log rather than silently swallow: a corrupt history skips the
                # pending->success/failed reconciliation below, so a real update
                # would otherwise vanish from history with no trace.
                log.warning("Could not read update history (%s); starting fresh", e)
                self._history = []

        if self._history and self._history[0].get("status") == "pending":
            expected = self._history[0].get("to_version", "")
            from_version = self._history[0].get("from_version", "")
            # The update applied if the running version is no longer the version
            # we updated FROM. Don't require an exact match against `expected`:
            # `expected` is the release tag while __version__ comes from the
            # bundled pyproject, and a documented tag/pyproject skew would
            # otherwise mark a genuinely-applied update as failed.
            applied = (
                (bool(expected) and __version__ == expected)
                or (bool(from_version) and __version__ != from_version)
            )
            if applied:
                self._history[0]["status"] = "success"
                if expected and __version__ != expected:
                    self._history[0]["note"] = (
                        f"Applied; running v{__version__} (release tag v{expected})"
                    )
                log.info("Confirmed update succeeded (running v%s)", __version__)
            else:
                self._history[0]["status"] = "failed"
                self._history[0]["error"] = (
                    f"Update did not apply: expected v{expected}, running v{__version__}"
                )
                log.warning("Update to v%s failed — still running v%s", expected, __version__)
            self._save_history()

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

    def _add_history_entry(
        self, from_version: str, to_version: str, status: str, error: str = "",
        rollback: bool = False,
    ) -> None:
        """Record an update attempt in history."""
        self._history.insert(0, {
            "from_version": from_version,
            "to_version": to_version,
            "status": status,
            "error": error,
            "rollback": rollback,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep last 50 entries
        self._history = self._history[:50]
        self._save_history()

    def _get_artifact_name(self, release: ReleaseInfo) -> str:
        """Determine the platform-specific artifact name for this release."""
        if self._deployment_type == DeploymentType.WINDOWS_INSTALLER:
            return f"OpenAVC-Setup-{release.version}.exe"
        import platform
        machine = platform.machine().lower()
        # macOS self-updates swap a tarball of the .app (the .pkg is
        # first-install only). Arch labels match the CI runners: arm64 / x86_64.
        if self._deployment_type == DeploymentType.MACOS_APP:
            arch = "arm64" if machine in ("aarch64", "arm64") else "x86_64"
            return f"openavc-{release.version}-macos-{arch}.tar.gz"
        # Linux: detect architecture
        if machine in ("x86_64", "amd64"):
            arch = "amd64"
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

    def _safe_artifact_filename(self, url_path: str, target_version: str) -> str:
        """Derive a safe artifact filename from a cloud-provided URL path.

        Returns the URL's basename only when it's a plain, separator-free token;
        otherwise synthesizes ``update-<sanitized-version>.<ext>``. This keeps
        URL-controlled path components (traversal, odd characters, Windows-invalid
        names) out of the download cache path and the scheduled-task XML.
        """
        import os
        import re
        base = os.path.basename(url_path or "")
        if base and not base.startswith(".") and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", base):
            return base
        safe_ver = re.sub(r"[^A-Za-z0-9._-]", "_", target_version or "")[:32] or "unknown"
        ext = ".exe" if self._deployment_type == DeploymentType.WINDOWS_INSTALLER else ".tar.gz"
        return f"update-{safe_ver}{ext}"

    async def _download_update(self, release: ReleaseInfo) -> Path:
        """Download the platform-specific update artifact from GitHub release assets.

        Returns the path to the downloaded file.
        """
        artifact_name = self._get_artifact_name(release)
        artifact_url = self._find_asset_url(release, artifact_name)
        if not artifact_url:
            raise RuntimeError(f"Artifact '{artifact_name}' not found in release assets")

        artifact_path = await self._download_artifact(artifact_url, artifact_name)

        # Verify checksum via SHA256SUMS.txt from release assets.
        # Fail-closed: a release with no checksums file is refused rather than
        # applied unverified — absence of a checksum must never downgrade to
        # "apply anyway" (supply-chain protection).
        checksum_url = self._find_asset_url(release, "SHA256SUMS.txt")
        if not checksum_url:
            artifact_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"No SHA256SUMS.txt in release assets for v{release.version} — "
                "refusing to apply an unverified update artifact."
            )
        await self._verify_checksum(checksum_url=checksum_url,
                                    artifact_path=artifact_path, artifact_name=artifact_name)

        # For tarball-swap deployments, fetch the artifact's detached signature
        # next to it so the root helper can verify it before extracting (H-075).
        if self._consumes_signed_tarball():
            sig_url = self._find_asset_url(release, artifact_name + ".sig")
            if sig_url:
                await self._download_sidecar_sig(sig_url, artifact_path)
            else:
                log.warning(
                    "No %s.sig in release assets — the root helper will refuse "
                    "this update if release signing is armed", artifact_name,
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
        try:
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
        except BaseException:
            # A mid-stream error/cancellation must not leave a partial artifact
            # in the cache (it would never be applied — the size/checksum gates
            # catch it — but it wastes disk until the next download).
            artifact_path.unlink(missing_ok=True)
            raise

        actual_size = artifact_path.stat().st_size
        log.info("Downloaded: %s (%d bytes, expected %d)", artifact_path.name, actual_size, total)

        if total > 0 and actual_size != total:
            artifact_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Download incomplete: got {actual_size} bytes, expected {total}"
            )

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
            file_size = artifact_path.stat().st_size
            log.error(
                "Checksum mismatch for %s (%d bytes): expected %s, got %s",
                artifact_name, file_size, expected_hash, actual_hash,
            )
            artifact_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Checksum mismatch for {artifact_name} ({file_size} bytes): "
                f"expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
            )

        log.info("Checksum verified for %s", artifact_name)

    async def _download_sidecar_sig(self, sig_url: str, artifact_path: Path) -> None:
        """Download the artifact's detached ``.sig`` next to it, best-effort.

        The root update-helper (Linux) is the *authoritative* integrity gate: it
        verifies ``${artifact}.sig`` against a root-owned trusted key that the
        service user can neither forge nor replace. manager.py runs AS that
        service user, so a manager-side verify would be bypassable and adds
        nothing to the trust model — its only job is to place the signature
        where the root helper looks (``str(artifact_path) + ".sig"``). If the
        signature isn't published (signing not yet armed, or the cloud hasn't
        started serving it), log and continue: the helper then treats the
        artifact as unsigned and refuses it only once signing is armed.
        """
        sig_path = Path(str(artifact_path) + ".sig")
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
                resp = await c.get(sig_url)
                resp.raise_for_status()
                sig_path.write_bytes(resp.content)
            log.info("Downloaded update signature: %s", sig_path.name)
        except Exception as e:  # noqa: BLE001 — best-effort sidecar, never fatal here
            sig_path.unlink(missing_ok=True)
            log.warning(
                "No detached signature fetched for %s (%s) — the root helper "
                "will refuse this update if release signing is armed",
                artifact_path.name, e,
            )

    def _consumes_signed_tarball(self) -> bool:
        """True for deployments whose update is a root-helper-swapped tarball.

        These are the paths where the ``.sig`` sidecar matters (Linux/Pi via
        update-helper.sh, macOS via openavc-macos-run.sh, the Android
        appliance). Windows self-updates via an Azure-signed installer .exe, so
        it has its own authenticity layer and needs no ``.sig``.
        """
        return self._deployment_type in (
            DeploymentType.LINUX_PACKAGE,
            DeploymentType.MACOS_APP,
            DeploymentType.ANDROID_APPLIANCE,
        )

    async def check_for_updates(self, channel: str | None = None) -> dict[str, Any]:
        """Check for available updates.

        Returns a dict with update info suitable for API response.
        """
        if channel is None:
            from server.system_config import get_system_config
            channel = get_system_config().get("updates", "channel", "stable")

        # Don't clobber an in-flight apply's status/progress UI. A concurrent
        # check (operator or cloud) would otherwise flip system.update_status to
        # checking/idle and stomp the apply's error mid-update.
        touch_status = not self._update_in_progress
        if touch_status:
            self._set_state("system.update_status", "checking")
            self._set_state("system.update_error", "")

        try:
            result = await self._checker.check(channel)
        finally:
            if touch_status:
                self._set_state("system.update_status", "idle")

        if result is None:
            self._set_state("system.update_available", "")
            error = self._checker.last_error
            if error and touch_status:
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
        If the cloud has staged an update (auto_restart=False) we route through
        apply_cloud_update with that staged URL instead of querying GitHub.
        """
        if self._update_in_progress:
            return {"success": False, "error": "Update already in progress"}

        # Prefer a cloud-staged update if one is pending — covers the
        # auto_restart=False case where the cloud pushed a target_version
        # without applying it immediately (A63).
        staged = self.get_staged_update()
        if staged:
            result = await self.apply_cloud_update(
                staged["target_version"],
                staged["update_url"],
                staged.get("checksum_sha256"),
            )
            # Clear the staged record only after a successful apply. A transient
            # failure (network blip during download) must keep it so the
            # operator's queued update can retry instead of silently vanishing.
            if result.get("success"):
                self.clear_staged_update()
            return result

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
            backup_path = create_backup(self._data_dir, __version__, project_path=self._project_path)
            cleanup_old_backups(self._data_dir)
            log.info("Pre-update backup created: %s", backup_path)

            # Step 2: Download + verify checksum
            self._set_state("system.update_status", "downloading")
            self._set_state("system.update_progress", 0)
            artifact_path = await self._download_update(release)
            self._set_state("system.update_progress", 100)

            # Step 3: Write pending-update marker (records the backup so an
            # automatic rollback can restore user data from it)
            from server.updater.rollback import write_pending_marker
            write_pending_marker(self._data_dir, __version__, release.version,
                                 backup_path=backup_path)

            # Step 4: Apply (platform-specific)
            self._set_state("system.update_status", "applying")
            if self._deployment_type == DeploymentType.WINDOWS_INSTALLER:
                self._apply_windows(artifact_path, release.version)
            elif self._deployment_type in (DeploymentType.LINUX_PACKAGE,
                                           DeploymentType.MACOS_APP,
                                           DeploymentType.ANDROID_APPLIANCE):
                self._apply_linux(artifact_path, release.version)

            # Step 5: Record success and restart
            # Record as "pending" — confirmed on next startup when the new
            # version actually runs (avoids false "success" if restart fails)
            self._add_history_entry(__version__, release.version, "pending")

            self._set_state("system.update_status", "restarting")
            log.info("Update to v%s applied, restarting...", release.version)

            asyncio.get_running_loop().call_later(1.0, self._restart_process)

            return {"success": True, "message": f"Update to v{release.version} started"}

        except asyncio.CancelledError:
            # A cancellation at a download await point still needs the markers
            # cleared and a history row recorded, then must propagate.
            self._cleanup_failed_apply(release.version, "Update cancelled")
            raise
        except Exception as e:
            error_msg = f"Update failed: {e}"
            log.exception(error_msg)
            self._cleanup_failed_apply(release.version, error_msg)
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

        # Validate the cloud-provided URL before fetching anything: require
        # https so a plain-HTTP or downgraded URL can't steer the download to an
        # attacker. The checksum is already fail-closed, so integrity is assured
        # regardless of host; this is defense in depth on the transport.
        from urllib.parse import urlparse
        parsed = urlparse(update_url)
        if parsed.scheme != "https":
            return {
                "success": False,
                "error": f"Refusing non-HTTPS update URL (scheme: {parsed.scheme or 'none'})",
            }

        self._update_in_progress = True
        self._set_state("system.update_error", "")

        try:
            # Step 1: Backup
            self._set_state("system.update_status", "backing_up")
            from server.updater.backup import create_backup, cleanup_old_backups
            backup_path = create_backup(self._data_dir, __version__, project_path=self._project_path)
            cleanup_old_backups(self._data_dir)
            log.info("Pre-update backup created: %s", backup_path)

            # Step 2: Download from cloud-provided URL.
            # Sanitize the URL-derived filename to a safe basename — it reaches
            # the cache path and (on Windows) the scheduled-task XML, so path
            # separators / odd characters must never pass through. Fall back to a
            # synthesized name built from a sanitized version string.
            filename = self._safe_artifact_filename(parsed.path, target_version)

            self._set_state("system.update_status", "downloading")
            self._set_state("system.update_progress", 0)
            artifact_path = await self._download_artifact(update_url, filename)
            self._set_state("system.update_progress", 100)

            # Step 3: Verify checksum — fail-closed.
            # The cloud only omits the checksum for a release whose
            # SHA256SUMS.txt was never registered; refuse rather than apply an
            # artifact we cannot verify (supply-chain protection).
            if not checksum_sha256:
                artifact_path.unlink(missing_ok=True)
                raise RuntimeError(
                    "No checksum provided for the update artifact — refusing to "
                    "apply an unverified update."
                )
            self._set_state("system.update_status", "verifying")
            self._verify_hash(artifact_path, checksum_sha256)

            # Fetch the detached signature (convention: artifact URL + ".sig")
            # next to the tarball for the root helper's integrity gate (H-075).
            # The cloud must serve it at that URL (release_service exposes the
            # .sig asset); until it does, the sidecar is simply absent and the
            # helper refuses only once signing is armed.
            if self._consumes_signed_tarball():
                await self._download_sidecar_sig(update_url + ".sig", artifact_path)

            # Step 4: Write pending-update marker (records the backup so an
            # automatic rollback can restore user data from it)
            from server.updater.rollback import write_pending_marker
            write_pending_marker(self._data_dir, __version__, target_version,
                                 backup_path=backup_path)

            # Step 5: Apply (platform-specific)
            self._set_state("system.update_status", "applying")
            if self._deployment_type == DeploymentType.WINDOWS_INSTALLER:
                self._apply_windows(artifact_path, target_version)
            elif self._deployment_type in (DeploymentType.LINUX_PACKAGE,
                                           DeploymentType.MACOS_APP,
                                           DeploymentType.ANDROID_APPLIANCE):
                self._apply_linux(artifact_path, target_version)

            self._add_history_entry(__version__, target_version, "pending")

            self._set_state("system.update_status", "restarting")
            log.info("Update to v%s applied (cloud URL), restarting...", target_version)

            asyncio.get_running_loop().call_later(1.0, self._restart_process)

            return {"success": True, "message": f"Update to v{target_version} started"}

        except asyncio.CancelledError:
            self._cleanup_failed_apply(target_version, "Update cancelled")
            raise
        except Exception as e:
            error_msg = f"Update failed: {e}"
            log.exception(error_msg)
            self._cleanup_failed_apply(target_version, error_msg)
            return {"success": False, "error": error_msg}
        finally:
            self._update_in_progress = False

    # --- Cloud-staged update (auto_restart=False) -----------------------

    def _staged_path(self) -> Path:
        return self._data_dir / "staged-update.json"

    def stage_update(
        self, target_version: str, update_url: str,
        checksum_sha256: str | None = None,
    ) -> None:
        """Persist a cloud-staged update so it can be applied manually later (A63).

        Called when the cloud sends ``software_update`` with
        ``auto_restart=False``. The next call to ``apply_update`` uses this
        target instead of polling GitHub.
        """
        import json
        payload = {
            "target_version": target_version,
            "update_url": update_url,
            "checksum_sha256": checksum_sha256,
        }
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._staged_path().write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            self._set_state("system.update_staged_version", target_version)
            log.info("Cloud staged update for v%s", target_version)
        except OSError:
            log.exception("Failed to persist cloud-staged update")

    def get_staged_update(self) -> dict[str, Any] | None:
        """Return the staged-update record, or None if no update is staged."""
        import json
        path = self._staged_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("Failed to read staged-update marker; ignoring")
            return None
        if not isinstance(data, dict):
            return None
        target = data.get("target_version")
        url = data.get("update_url")
        if not target or not url:
            return None
        return data

    def clear_staged_update(self) -> None:
        """Remove the staged-update marker (called after apply)."""
        try:
            self._staged_path().unlink(missing_ok=True)
        except OSError:
            log.exception("Failed to clear staged-update marker")
        self._set_state("system.update_staged_version", "")

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
        """Apply update on Windows via silent installer scheduled through Task Scheduler.

        The Inno Setup installer caches itself during installation
        (CacheInstallerForRollback in setup.iss), so rollback is always possible.

        The installer runs via Windows Task Scheduler rather than as a child
        process. NSSM 2.24 walks the service's process tree on exit and kills
        every descendant by parent-PID enumeration, so any installer launched
        as a child of the dying server gets killed before it can replace files.
        Task Scheduler runs the task in its own process tree under
        taskhostw.exe, completely outside NSSM's awareness.
        """
        from server.updater.rollback import _launch_installer_via_scheduler

        log.info("Scheduling silent installer: %s", artifact_path.name)
        if not _launch_installer_via_scheduler(artifact_path, new_version):
            raise RuntimeError("Failed to schedule installer task")

    def _apply_linux(self, artifact_path: Path, to_version: str) -> None:
        """Write the update instruction for the privileged pre-start helper.

        Shared by the Linux and macOS self-update paths — both drop a tarball
        and a root-run helper swaps it in before the server relaunches:
        - Linux: update-helper.sh (systemd ExecStartPre=-+, bypasses
          ProtectSystem=strict) backs up /opt/openavc, extracts, rebuilds venv.
        - macOS: openavc-macos-apply.sh (the LaunchDaemon's wrapper, runs as
          root) snapshots OpenAVC.app -> OpenAVC.app.previous and swaps in the
          new app tree.
        Both read this same apply-update.json. After this method returns, the
        caller exits the process so the service manager relaunches and the
        helper applies the staged update.
        """
        instruction = {
            "artifact": str(artifact_path),
            "from_version": __version__,
            "to_version": to_version,
        }
        instruction_path = self._data_dir / "apply-update.json"
        instruction_path.write_text(json.dumps(instruction), encoding="utf-8")
        log.info("Wrote update instruction: %s -> v%s (%s)", __version__, to_version, instruction_path)

    def _clear_linux_apply_instruction(self) -> None:
        """Remove a stale Linux apply-update.json.

        update-helper.sh (ExecStartPre, runs as root) unconditionally consumes
        this file on the next service start, so a stale instruction left by a
        failed/aborted apply would otherwise drive a root-level apply on an
        unrelated future restart.
        """
        try:
            (self._data_dir / "apply-update.json").unlink(missing_ok=True)
        except OSError:
            log.exception("Failed to clear stale apply-update instruction")

    def _cleanup_failed_apply(self, to_version: str, error_msg: str) -> None:
        """Shared cleanup for a failed or cancelled apply.

        Clears the pending-update marker (so a later restart doesn't trigger a
        false rollback against a stale from_version) and the Linux apply
        instruction (so the root helper can't re-consume it), records the
        failure in history, and surfaces the error in state.
        """
        from server.updater.rollback import clear_pending_marker
        try:
            clear_pending_marker(self._data_dir)
        except OSError:
            log.exception("Failed to clear stale pending-update marker")
        self._clear_linux_apply_instruction()
        self._set_state("system.update_status", "error")
        self._set_state("system.update_error", error_msg)
        self._add_history_entry(__version__, to_version, "failed", error_msg)

    def _restart_process(self) -> None:
        """Exit the process so the external update mechanism can apply changes.

        Windows: exit code 42 tells NSSM not to restart (installer handles it).
        Linux: exit code 0 triggers systemd restart; ExecStartPre applies the update.
        macOS: exit code 0 lets launchd (KeepAlive) relaunch; the daemon's root
            wrapper applies the staged update before re-exec'ing the server.
        """
        import os
        if self._deployment_type == DeploymentType.WINDOWS_INSTALLER:
            log.info("Exiting for update — installer handles restart (exit 42)")
            os._exit(42)
        elif self._deployment_type == DeploymentType.LINUX_PACKAGE:
            log.info("Exiting for update — systemd will apply and restart (exit 0)")
            os._exit(0)
        elif self._deployment_type == DeploymentType.MACOS_APP:
            log.info("Exiting for update — launchd wrapper will apply and restart (exit 0)")
            os._exit(0)
        elif self._deployment_type == DeploymentType.ANDROID_APPLIANCE:
            log.info("Exiting for update — appliance supervisor will apply and restart (exit 0)")
            os._exit(0)

    async def rollback(self) -> dict[str, Any]:
        """Rollback to the previous version."""
        from server.updater.rollback import (
            can_rollback, perform_rollback, rollback_target_version,
        )
        from server.system_config import APP_DIR

        if self._update_in_progress:
            return {"success": False, "error": "Cannot rollback while an update is in progress"}

        if not can_rollback(APP_DIR):
            return {"success": False, "error": "No previous version available for rollback"}

        # Resolve the actual target version up front so history can record a
        # real version instead of a placeholder.
        target_version = rollback_target_version(APP_DIR)

        # Create a backup of current state before rolling back
        self._set_state("system.update_status", "backing_up")
        try:
            from server.updater.backup import create_backup, cleanup_old_backups
            create_backup(self._data_dir, __version__, project_path=self._project_path)
            cleanup_old_backups(self._data_dir)  # rotate like the apply paths (don't leave rollback backups unbounded)
        except Exception as e:
            log.warning("Pre-rollback backup failed: %s", e)

        # Drop any cloud-staged update before rolling back: it targets the very
        # version we're abandoning, and would otherwise be re-applied by the
        # next apply_update() (which prefers a staged record).
        self.clear_staged_update()

        self._set_state("system.update_status", "applying")
        # Pass the versions explicitly: a manual rollback runs after the
        # pending-update marker is cleared, so perform_rollback can't recover
        # them from it. We're abandoning the running version (to_version) and
        # restoring the previous one (from_version, the rollback target).
        success = perform_rollback(
            self._data_dir,
            from_version=target_version or "unknown",
            to_version=__version__,
        )

        if success:
            self._add_history_entry(
                __version__, target_version, "success", rollback=True,
            )
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
            # Derive the rollback target from what perform_rollback would
            # actually restore (cached installer / .previous tree), not from
            # history — history can name the version just rejected (the rollback
            # entry's own from_version) or one with no cached installer.
            from server.updater.rollback import rollback_target_version
            rollback_version = rollback_target_version(APP_DIR)

        staged = self.get_staged_update()

        return {
            "current_version": __version__,
            "deployment_type": self._deployment_type.value,
            "can_self_update": can_self_update(self._deployment_type),
            "update_available": self._state.get("system.update_available", "") if self._state else "",
            "update_channel": self._state.get("system.update_channel", "stable") if self._state else "stable",
            "update_status": self._state.get("system.update_status", "idle") if self._state else "idle",
            "update_progress": self._state.get("system.update_progress", 0) if self._state else 0,
            "update_error": self._state.get("system.update_error", "") if self._state else "",
            "staged_version": staged.get("target_version", "") if staged else "",
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

        # Coerce + bound the configured interval: a string (e.g. "12" from
        # hand-edited system.json) would crash asyncio.sleep and stop checks
        # forever; 0/negative would turn the loop into a busy spin.
        raw_interval = cfg.get("updates", "auto_check_interval_hours", interval_hours)
        try:
            interval_hours = int(raw_interval)
        except (TypeError, ValueError):
            log.warning("Invalid auto_check_interval_hours %r; using 24", raw_interval)
            interval_hours = 24
        if interval_hours <= 0:
            log.warning("auto_check_interval_hours must be positive (got %r); using 24", raw_interval)
            interval_hours = 24

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
        await self._cancel_maintenance_task()

    async def _cancel_maintenance_task(self) -> None:
        """Cancel the maintenance-window loop and wait for it to actually stop.

        Awaiting matters when reconfiguring: a cancel-without-await leaves the
        old loop running to its next await point, so two loops could briefly
        coexist and issue redundant checks/applies.
        """
        task = self._maintenance_task
        self._maintenance_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @staticmethod
    def _parse_window_time(value: Any):
        """Parse a 'HH:MM' string into a datetime.time, or None if invalid.

        Rejects out-of-range values (e.g. 25:99) and non-strings so a hostile or
        malformed cloud field can never raise out of dt_time() and kill the
        maintenance task.
        """
        from datetime import time as dt_time
        if not isinstance(value, str):
            return None
        parts = value.split(":")
        if len(parts) != 2:
            return None
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return dt_time(hour, minute)

    @staticmethod
    def _resolve_tz(tz_name: Any):
        """Resolve a timezone name to a tzinfo, or None (UTC) on any failure.

        Cloud-supplied tz is unvalidated: ZoneInfo raises ValueError/OSError on
        empty/garbage/oversized values, which must not escape and kill the loop.
        """
        if not tz_name or not isinstance(tz_name, str):
            return None
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz_name)
        except (ImportError, KeyError, ValueError, OSError) as e:
            log.warning("Invalid maintenance_window_tz %r (%s); using UTC", tz_name, e)
            return None

    async def apply_update_policy(self, policy_config: dict[str, Any]) -> None:
        """Apply an update policy received from the cloud.

        ``policy_config`` crosses the cloud trust boundary, so every field is
        validated before use: an unknown ``policy`` falls back to manual, a
        malformed/missing/out-of-range window disables the auto loop (rather
        than crashing the maintenance task), and a bad timezone falls back to
        UTC. Keys: policy, maintenance_window_start, maintenance_window_end,
        maintenance_window_tz.
        """
        policy = policy_config.get("policy")
        if policy not in ("auto", "manual"):
            if policy is not None:
                log.warning("Unknown cloud update policy %r; treating as manual", policy)
            policy = "manual"
        log.info("Cloud update policy: %s", policy)

        # Tear down any existing maintenance loop and WAIT for it to stop before
        # starting a new one, so two loops can't transiently overlap (M-013).
        await self._cancel_maintenance_task()

        if policy != "auto" or not can_self_update(self._deployment_type):
            return

        start_time = self._parse_window_time(policy_config.get("maintenance_window_start"))
        end_time = self._parse_window_time(policy_config.get("maintenance_window_end"))
        if start_time is None or end_time is None:
            log.warning(
                "Auto update policy with invalid/missing maintenance window "
                "(%r-%r); not scheduling auto-updates.",
                policy_config.get("maintenance_window_start"),
                policy_config.get("maintenance_window_end"),
            )
            return
        tz = self._resolve_tz(policy_config.get("maintenance_window_tz"))

        self._maintenance_task = asyncio.create_task(
            self._maintenance_window_loop(start_time, end_time, tz)
        )
        log.info(
            "Auto-update scheduled during %02d:%02d-%02d:%02d %s",
            start_time.hour, start_time.minute, end_time.hour, end_time.minute,
            getattr(tz, "key", None) or "UTC",
        )

    async def _maintenance_window_loop(self, start_time, end_time, tz) -> None:
        """Apply updates only while inside the [start, end) maintenance window."""
        max_retries = 7  # days to retry
        try:
            for _ in range(max_retries):
                # Wait until we're actually inside the window (enforces the end,
                # so an update can't fire any time after the start).
                await self._sleep_until_in_window(start_time, end_time, tz)

                result = await self.check_for_updates()
                if not result.get("update_available"):
                    await asyncio.sleep(24 * 3600)
                    continue

                if self._is_system_active():
                    log.info("System is active during maintenance window, deferring update")
                    await asyncio.sleep(24 * 3600)
                    continue

                log.info("Maintenance window: applying update")
                await self.apply_update()
                return  # Update applied (or failed), stop the loop

        except asyncio.CancelledError:
            pass

    async def _sleep_until_in_window(self, start_time, end_time, tz) -> None:
        """Sleep (re-checking every 5 minutes) until inside [start, end)."""
        while not self._in_window(start_time, end_time, tz):
            await asyncio.sleep(300)

    def _in_window(self, start_time, end_time, tz) -> bool:
        """True when the current local time is within the [start, end) window."""
        now = datetime.now(timezone.utc)
        if tz is not None:
            now = now.astimezone(tz)
        now_t = now.time()
        if start_time <= end_time:
            return start_time <= now_t < end_time
        # Overnight window (e.g. 23:00-02:00) wraps past midnight.
        return now_t >= start_time or now_t < end_time

    def _is_system_active(self) -> bool:
        """Check if the system is actively being used (WS clients connected)."""
        if not self._state:
            return False
        # Check for connected WebSocket clients (Programmer or Panel)
        ws_count = self._state.get("system.ws_clients", 0)
        return isinstance(ws_count, int) and ws_count > 0
