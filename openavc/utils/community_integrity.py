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

**One install, one catalog** (:class:`CatalogCheck`). The catalog and the files
it hashes reach us separately, and for a few minutes after an artifact is
updated they can disagree with nothing wrong (see that class). A disagreement
fetches the catalog once more before refusing; it never relaxes the check.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import unquote, urlparse

from openavc.utils.logger import get_logger

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


class ArtifactMismatch(CommunityArtifactError):
    """A downloaded file disagrees with the catalog: other bytes than it hashes,
    or a path it does not list. The two refusals a newer catalog can resolve."""


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

        Raises :class:`ArtifactMismatch` when the bytes don't match, or when
        this file isn't in a manifest that exists — an unlisted file is how an
        extra one would be smuggled in beside the reviewed set.
        """
        self.check_digest(repo_path, hashlib.sha256(data).hexdigest())

    def check_digest(self, repo_path: str | None, actual: str) -> None:
        """:meth:`check` for a file already hashed (its SHA-256, hex)."""
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
            raise ArtifactMismatch(
                f"{self.artifact}: {repo_path!r} is not listed in {self.source} — "
                "refusing a file the catalog does not publish"
            )
        if actual != expected:
            raise ArtifactMismatch(
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


class CatalogCheck:
    """One install's check of its downloads, fetching the catalog once more
    before refusing.

    The catalog and the files it hashes are fetched separately, through caches
    with separate lifetimes: a server may keep its copy of a catalog for minutes,
    and raw.githubusercontent.com keeps each file for five minutes on its own
    clock. For a while after an artifact is updated the catalog and a file can
    disagree in either direction with nothing wrong, and a check against the
    first copy alone would refuse every update for that whole window.

    So the first disagreement calls ``load(True)``, which must bypass any copy
    the caller holds, and checks every file this install already accepted
    against the new copy too: all of an install's files match one catalog or the
    install is refused. Once per install, never once per file. The hash still
    comes only from the catalog, and a refetch that comes back with no hashes
    keeps the copy that refused, so a failed fetch can never turn a refusal into
    an install.

    The CDN's five minutes cannot be bypassed from here (it ignores query
    strings and ``Cache-Control: no-cache``), so a disagreement that survives
    the refetch raises :class:`CommunityArtifactError` with ``refusal``, a
    sentence for the person who pressed Install, and logs the hashes.
    """

    def __init__(
        self,
        hashes: ArtifactHashes,
        load: Callable[[bool], Awaitable[ArtifactHashes]],
        *,
        refusal: str,
    ) -> None:
        self._hashes = hashes
        self._load = load
        self._refusal = refusal
        self._refetched = False
        self._accepted: dict[str, str] = {}

    @classmethod
    async def fetch(
        cls,
        load: Callable[[bool], Awaitable[ArtifactHashes]],
        *,
        refusal: str,
    ) -> CatalogCheck:
        """Start a check from the catalog as ``load(False)`` returns it."""
        return cls(await load(False), load, refusal=refusal)

    @property
    def hashes(self) -> ArtifactHashes:
        """The catalog copy in use: the first one, or the refetched one."""
        return self._hashes

    async def check(self, repo_path: str | None, data: bytes) -> None:
        """Verify one downloaded file. Call BEFORE writing it anywhere."""
        digest = hashlib.sha256(data).hexdigest()
        try:
            self._hashes.check_digest(repo_path, digest)
        except ArtifactMismatch as first:
            if self._refetched:
                raise self._refuse(first) from first
            self._refetched = True
            fresh = await self._load(True)
            if not fresh.declared:
                raise self._refuse(first) from first
            self._hashes = fresh
            try:
                for path, accepted in self._accepted.items():
                    fresh.check_digest(path, accepted)
                fresh.check_digest(repo_path, digest)
            except ArtifactMismatch as second:
                raise self._refuse(second) from second
        if repo_path:
            self._accepted[repo_path] = digest

    def require_complete(self) -> None:
        """:meth:`ArtifactHashes.require_complete` on the catalog copy in use."""
        self._hashes.require_complete()

    def _refuse(self, cause: ArtifactMismatch) -> CommunityArtifactError:
        log.warning("%s (refused after fetching the catalog again)", cause)
        return CommunityArtifactError(self._refusal)
