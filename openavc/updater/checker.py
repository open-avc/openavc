"""
Update version checker.

Queries the GitHub Releases API to determine if a newer version is available.
Supports stable and beta channels. Which release is newer is the version
rule's (``openavc/utils/version_rule.py``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from openavc.version import __version__
from openavc.utils.outbound import internet_client, internet_failure
from openavc.utils.version_rule import compare_versions, read_version

log = logging.getLogger(__name__)

# GitHub API endpoint for releases (public, no auth needed)
GITHUB_RELEASES_URL = "https://api.github.com/repos/open-avc/openavc/releases"


@dataclass
class ReleaseInfo:
    """Information about an available release."""
    version: str
    tag: str
    prerelease: bool
    changelog: str
    published_at: str
    assets: list[dict[str, str]] = field(default_factory=list)


# The three-part reading below is what rollback.py's installer and cache
# sorting compares; which release is newer is the version rule's.
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$")


def is_valid_semver(version_str: str) -> bool:
    """Return True if the string parses as MAJOR.MINOR.PATCH (optional -prerelease).

    Distinguishes a genuine parse (including a real "0.0.0") from a regex miss,
    which ``parse_semver`` can't express through its (0, 0, 0, "") fallback.
    """
    return _SEMVER_RE.match(version_str.lstrip("v").strip()) is not None


def parse_semver(version_str: str) -> tuple[int, int, int, str]:
    """Parse a semver string into (major, minor, patch, prerelease).

    Handles formats: "1.2.3", "v1.2.3", "1.2.3-beta.1", "v1.2.3-rc.2"
    Returns (major, minor, patch, prerelease_suffix).
    """
    clean = version_str.lstrip("v").strip()
    match = _SEMVER_RE.match(clean)
    if not match:
        return (0, 0, 0, "")
    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    prerelease = match.group(4) or ""
    return (major, minor, patch, prerelease)


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a newer release than ``current``, by the
    version rule (a release candidate is older than its release, and
    ``0.40.0-rc2`` and ``0.40.0rc2`` are one version).

    A candidate that is not a version is never newer; any version is newer
    than a running version that cannot be read, so such a system is still
    offered the newest release.
    """
    offered = read_version(candidate)
    if offered is None:
        return False
    running = read_version(current)
    return running is None or offered > running


#: The code a cloud update that is no update for this system is refused with.
NOT_NEWER = "not_newer"


def not_newer_refusal(target: str, running: str, rollback: bool = False) -> dict[str, Any] | None:
    """What an update the cloud sent answers when ``target`` is no update
    for a system running ``running``; None when it may go ahead.

    A fleet update installs only a newer release (``is_newer``), unless the
    cloud marks it a rollback. A target that is not a version is no update.
    """
    if rollback or is_newer(target, running):
        return None
    if read_version(target) is None:
        said = "The update names no release version"
    else:
        said = f"Already on v{running}"
    return {
        "success": False,
        "error": NOT_NEWER,
        "message": f"{said}; a fleet update installs only a newer version",
    }


def still_an_update(target: str, running: str, rollback: bool = False) -> bool:
    """Whether an update staged for ``target`` still moves a system running
    ``running``: a newer release, or, for a rollback, any other release."""
    if rollback:
        return compare_versions(target, running) != 0
    return is_newer(target, running)


class UpdateChecker:
    """Checks for available OpenAVC updates via GitHub Releases API."""

    def __init__(self, current_version: str | None = None):
        self.current_version = current_version or __version__
        self._last_check_result: ReleaseInfo | None = None
        self._last_check_error: str = ""

    async def check(self, channel: str = "stable") -> ReleaseInfo | None:
        """Check GitHub for available updates.

        Args:
            channel: "stable" (non-prerelease only) or "beta" (includes prereleases)

        Returns:
            ReleaseInfo if a newer version is available, None otherwise.
        """
        self._last_check_error = ""

        try:
            async with internet_client(timeout=15.0) as client:
                response = await client.get(
                    GITHUB_RELEASES_URL,
                    headers={
                        "Accept": "application/vnd.github.v3+json",
                        "User-Agent": f"OpenAVC/{self.current_version}",
                    },
                )
                response.raise_for_status()
                releases = response.json()
        except httpx.HTTPStatusError as e:
            self._last_check_error = f"GitHub API returned {e.response.status_code}"
            log.warning("Update check failed: %s", self._last_check_error)
            return None
        except httpx.RequestError as e:
            self._last_check_error = internet_failure(e, "api.github.com") or f"Network error: {e}"
            log.warning("Update check failed: %s", self._last_check_error)
            return None
        except Exception as e:
            self._last_check_error = f"Unexpected error: {e}"
            log.warning("Update check failed: %s", self._last_check_error)
            return None

        if not isinstance(releases, list):
            self._last_check_error = "Invalid response from GitHub API"
            return None

        # An empty list is a failure, not an answer. This repo has releases, so
        # zero of them means the API did not tell us the truth -- and on
        # 2026-08-17 it did exactly that, serving `[]` with a 200 for about
        # twenty minutes during a GitHub outage while /releases/latest still
        # returned the release that had just been published. Falling through
        # here reports "no updates available", which is the one wrong answer
        # that looks like good news: every instance in the fleet told its owner
        # it was current on the day a new version shipped. Say the check failed
        # instead, so the Updates view offers a retry rather than reassurance.
        if not releases:
            self._last_check_error = (
                "GitHub returned no releases, so the check could not be completed"
            )
            log.warning("Update check failed: %s", self._last_check_error)
            return None

        # Find the best candidate release
        best: dict[str, Any] | None = None
        for release in releases:
            if release.get("draft"):
                continue
            is_prerelease = release.get("prerelease", False)
            if channel == "stable" and is_prerelease:
                continue
            tag = release.get("tag_name", "")
            if not tag:
                continue
            if read_version(tag) is None:
                # Surface a tag that is not a version, so a mistyped or
                # non-standard tag is visible instead of a bare "No updates available".
                log.warning(
                    "Ignoring release with unparseable version tag %r (expected a "
                    "version such as v1.2.3 or v1.2.3-rc1); it will not be offered",
                    tag,
                )
                continue
            if not is_newer(tag, self.current_version):
                continue
            # This release is newer; pick the newest one
            if best is None or is_newer(tag, best["tag_name"]):
                best = release

        if best is None:
            log.info("No updates available (current: %s, channel: %s)", self.current_version, channel)
            self._last_check_result = None
            return None

        # Parse the release into ReleaseInfo
        assets = []
        for asset in best.get("assets", []):
            assets.append({
                "name": asset.get("name", ""),
                "url": asset.get("browser_download_url", ""),
                "size": asset.get("size", 0),
            })

        info = ReleaseInfo(
            version=best["tag_name"].lstrip("v"),
            tag=best["tag_name"],
            prerelease=best.get("prerelease", False),
            changelog=best.get("body", ""),
            published_at=best.get("published_at", ""),
            assets=assets,
        )

        log.info("Update available: %s -> %s (channel: %s)", self.current_version, info.version, channel)
        self._last_check_result = info
        return info

    @property
    def last_result(self) -> ReleaseInfo | None:
        return self._last_check_result

    @property
    def last_error(self) -> str:
        return self._last_check_error
