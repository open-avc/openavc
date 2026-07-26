"""Integrity policy for community artifacts (drivers, plugins, their companions).

Community drivers and plugins are downloaded from GitHub and then executed:
a ``.py`` driver is imported and registered, a plugin runs in-process. Nothing
about that trust is accidental — installing a community artifact means granting
it full server-side trust, and that is a decided posture. What this module adds
is the part that was never decided, only missing: some assurance that the bytes
which arrive are the bytes the catalog was built from.

Two controls live here, and it is worth being precise about what each one buys,
because a control that looks like verification but isn't is worse than none.

**Source pinning** (:func:`validate_catalog_url`). A URL must be https and must
sit under the one official catalog repository. A hostname-only "is it GitHub?"
check gives false assurance: any attacker-controlled GitHub repo passes it, yet
the code runs in-process. This is the control that stops an install being aimed
somewhere else entirely — including by a caller that isn't a person, such as an
AI tool acting on a URL it was handed.

**Artifact hashes** (:class:`ArtifactHashes`). The catalogs publish a SHA-256
for every file an install fetches. We hash what we received and compare *before
writing anything to disk* — verifying after extraction would mean the malicious
file already landed. This catches an artifact that changed without its catalog
being rebuilt, a truncated or partially-served download, and a stale CDN or
cache serving one file from a different revision than the catalog.

**It does not defend against a compromised catalog repository, and must not be
described as if it does.** The hashes travel the same channel as the artifact,
so anything that can rewrite one can rewrite the other. Closing that gap needs a
signature made with a key the repository does not hold — the release-signing
trust root in ``installer/trusted-keys/`` is the natural home for it, and the
absent/mismatched policy below is deliberately shaped so a signature can be
added in front of it later without changing any call site.

Absent hashes are the one case that stays permissive: a catalog entry with no
hashes at all warns and proceeds, because catalogs that predate this field would
otherwise become uninstallable. That is the honest state of a control that is
not yet backed by a signature — it is written down here rather than dressed up.
Once hashes are present, everything is fail-closed: a mismatch, or a file that
was fetched but is not in the manifest, refuses the install.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from urllib.parse import unquote, urlparse

from server.utils.logger import get_logger

log = get_logger(__name__)

# The catalog repositories. Community code may come from these and nowhere else.
DRIVERS_OWNER_REPO = "open-avc/openavc-drivers"
PLUGINS_OWNER_REPO = "open-avc/openavc-plugins"

# For a URL to count as "from an official catalog" its host must be one of these
# and its path must sit under the required prefix for that host.
_HOST_PREFIXES = {
    "raw.githubusercontent.com": "/{owner_repo}/",
    "github.com": "/{owner_repo}/",
    "api.github.com": "/repos/{owner_repo}/",
}


class CommunityArtifactError(Exception):
    """An artifact was refused: wrong source, wrong bytes, or an extra file."""


def validate_catalog_url(url: str, *, owner_repo: str) -> None:
    """Raise unless ``url`` is an https URL under the official catalog repo.

    Rejecting anything else is what keeps "community driver" and "community
    plugin" meaning the curated, reviewed catalog rather than any repository
    that happens to live on the same host.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise CommunityArtifactError(
            f"Community artifact URL must use https, got: {parsed.scheme or url!r}"
        )
    template = _HOST_PREFIXES.get(parsed.hostname or "")
    if template is None:
        raise CommunityArtifactError(
            f"Community artifact URL must come from the official catalog "
            f"({owner_repo}); host {parsed.hostname or url!r} is not allowed"
        )
    # Decode %2e%2e / %2f before the prefix and traversal checks so an encoded
    # path can't masquerade as the catalog and then resolve elsewhere.
    path = unquote(parsed.path)
    prefix = template.format(owner_repo=owner_repo)
    if not path.startswith(prefix) or ".." in path.split("/"):
        raise CommunityArtifactError(
            f"Community artifact URL must be a path under {owner_repo}; "
            f"path {parsed.path!r} is not"
        )


def catalog_relpath(url: str, *, owner_repo: str) -> str | None:
    """Repo-relative path of a raw catalog URL, or ``None`` if it isn't one.

    ``https://raw.githubusercontent.com/open-avc/openavc-drivers/main/audio/x.py``
    becomes ``audio/x.py`` — the key the catalogs publish hashes under. Only raw
    URLs carry a usable path (owner/repo/ref/…); an api.github.com listing URL
    has a different shape and returns ``None``.
    """
    parsed = urlparse(url)
    if parsed.hostname != "raw.githubusercontent.com":
        return None
    parts = unquote(parsed.path).lstrip("/").split("/")
    owner, repo = owner_repo.split("/", 1)
    # owner / repo / ref / rest...
    if len(parts) < 4 or parts[0] != owner or parts[1] != repo:
        return None
    return "/".join(parts[3:]) or None


class ArtifactHashes:
    """The catalog's declared SHA-256 for each file of one community artifact.

    Construct with ``files=None`` (or empty) when the catalog published none —
    every check then degrades to a single warning instead of a refusal. See the
    module docstring for why that case stays permissive.
    """

    def __init__(
        self,
        artifact: str,
        files: Mapping[str, str] | None,
        *,
        source: str,
    ) -> None:
        self.artifact = artifact
        self.source = source
        self.files: dict[str, str] = {
            str(k): str(v).lower()
            for k, v in (files or {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        self.verified: set[str] = set()
        self._warned = False

    @property
    def declared(self) -> bool:
        """True when the catalog published hashes for this artifact."""
        return bool(self.files)

    def _warn_once(self) -> None:
        if self._warned:
            return
        self._warned = True
        log.warning(
            "%s: %s publishes no file hashes — installing without an integrity "
            "check. The download is protected by HTTPS to GitHub only.",
            self.artifact,
            self.source,
        )

    def check(self, repo_path: str | None, data: bytes) -> None:
        """Verify one downloaded file. Call BEFORE writing it anywhere.

        Raises :class:`CommunityArtifactError` when the bytes don't match, or
        when this file isn't in a manifest that exists — an unlisted file is how
        an extra one would be smuggled in beside the reviewed set.
        """
        if not self.declared:
            self._warn_once()
            return
        if not repo_path:
            raise CommunityArtifactError(
                f"{self.artifact}: cannot place a downloaded file within "
                f"{self.source} to check it against"
            )
        expected = self.files.get(repo_path)
        if expected is None:
            raise CommunityArtifactError(
                f"{self.artifact}: {repo_path!r} is not listed in {self.source} — "
                "refusing a file the catalog does not publish"
            )
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise CommunityArtifactError(
                f"{self.artifact}: {repo_path!r} does not match {self.source} "
                f"(expected {expected}, got {actual}) — refusing to install it"
            )
        self.verified.add(repo_path)

    def require_complete(self) -> None:
        """Raise if the manifest listed files that were never fetched.

        Only meaningful where the file list itself arrives over the network (a
        plugin's directory listing): without it, dropping a file from the
        listing would go unnoticed. Not used for drivers, whose optional
        companions are legitimately absent when a driver ships without one.
        """
        if not self.declared:
            return
        missing = sorted(set(self.files) - self.verified)
        if missing:
            raise CommunityArtifactError(
                f"{self.artifact}: {len(missing)} file(s) in {self.source} were "
                f"never delivered ({', '.join(missing[:5])}"
                f"{', …' if len(missing) > 5 else ''}) — refusing an incomplete install"
            )
