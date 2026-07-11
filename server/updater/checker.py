"""
Update version checker.

Queries the GitHub Releases API to determine if a newer version is available.
Supports stable and beta channels. Uses semver comparison.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from server.version import __version__

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


def _is_numeric_identifier(ident: str) -> bool:
    """A semver prerelease identifier is numeric only if it is all ASCII digits."""
    return ident.isascii() and ident.isdigit()


def _compare_prerelease(a_parts: list[str], b_parts: list[str]) -> int:
    """Compare two dot-separated prerelease identifier lists per semver rule 11.4.

    Returns 1 if `a` has higher precedence, -1 if lower, 0 if equal.
    Numeric identifiers compare numerically; two alphanumerics compare by ASCII;
    a numeric identifier always has LOWER precedence than an alphanumeric one; and
    when all shared identifiers are equal, the longer list has higher precedence.
    """
    for a, b in zip(a_parts, b_parts):
        a_num, b_num = _is_numeric_identifier(a), _is_numeric_identifier(b)
        if a_num and b_num:
            ai, bi = int(a), int(b)
            if ai != bi:
                return 1 if ai > bi else -1
        elif a_num != b_num:
            # Numeric identifiers have lower precedence than alphanumeric ones.
            return -1 if a_num else 1
        elif a != b:
            return 1 if a > b else -1
    if len(a_parts) != len(b_parts):
        return 1 if len(a_parts) > len(b_parts) else -1
    return 0


def is_newer(candidate: str, current: str) -> bool:
    """Return True if candidate version is newer than current.

    Stable releases (no prerelease suffix) are considered newer than
    prereleases of the same version number.
    """
    c_maj, c_min, c_pat, c_pre = parse_semver(candidate)
    r_maj, r_min, r_pat, r_pre = parse_semver(current)

    c_tuple = (c_maj, c_min, c_pat)
    r_tuple = (r_maj, r_min, r_pat)

    if c_tuple > r_tuple:
        return True
    if c_tuple < r_tuple:
        return False

    # Same version number: stable > prerelease
    if c_pre and not r_pre:
        return False
    if not c_pre and r_pre:
        return True
    # Both have prerelease: compare identifier-by-identifier per semver rule 11.4.
    if c_pre and r_pre:
        return _compare_prerelease(c_pre.split("."), r_pre.split(".")) > 0
    # Both stable and same version number.
    return False


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
            async with httpx.AsyncClient(timeout=15.0) as client:
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
            self._last_check_error = f"Network error: {e}"
            log.warning("Update check failed: %s", self._last_check_error)
            return None
        except Exception as e:
            self._last_check_error = f"Unexpected error: {e}"
            log.warning("Update check failed: %s", self._last_check_error)
            return None

        if not isinstance(releases, list):
            self._last_check_error = "Invalid response from GitHub API"
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
            if not is_valid_semver(tag):
                # parse_semver would silently treat this as 0.0.0 and skip it, hiding
                # a release a human might consider valid. Surface it so a mistyped or
                # non-standard tag is visible instead of a bare "No updates available".
                log.warning(
                    "Ignoring release with unparseable version tag %r "
                    "(expected MAJOR.MINOR.PATCH); it will not be offered as an update",
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
