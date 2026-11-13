"""The community driver catalog as this server reads it: which addresses are
the catalog's, the file hashes it publishes for a driver, and bringing one
driver's files in from it (``CatalogFiles``).

Every file a community install or update downloads goes through
``CatalogFiles._fetch``: one internet request (``utils/outbound.py``'s client:
the OS certificate store, the environment's proxy, a 15 second timeout), then
the check against the hashes the catalog publishes for that file's own path,
before anything is written. The hashes are looked up here from the community
index, never taken from the request, and a disagreement fetches the index once
more past the cached copy before refusing (``utils/community_integrity.py``
``CatalogCheck``). The index is one cache on this server (``index_cache``), so
Browse Community and an install's check read the same copy.

Each of the three files an install may bring answers a failure in its own
words (the main file, a YAML driver's declared companion, a Python driver's
conventional companions), and those differ on purpose or by history:
``tests/test_driver_catalog_fetch_pins.py`` holds each as it is. What a door
does around them (the version floor, the rollback, the registry) is the
door's own, in ``routes/drivers.py``.
"""

import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple
from urllib.parse import urlparse

from fastapi import HTTPException

from openavc.api.errors import api_error as _api_error
from openavc.drivers.driver_loader import (
    COMPANION_SUFFIXES,
    DISCOVERY_COMPANION_SUFFIX,
    DRIVER_FILE_STEM,
    companion_relpath_from_yaml,
    require_mutable_driver_file,
)
from openavc.utils.logger import get_logger
from openavc.utils.outbound import internet_client, internet_failure

log = get_logger(__name__)

# This server's one copy of the community index (``index_cache``).
_index_cache: Any = None


def index_cache() -> Any:
    """The community index this server reads, made on first use: Browse
    Community lists it and every install's hash lookup reads it."""
    global _index_cache
    if _index_cache is None:
        from openavc.discovery.community_index import CommunityIndexCache

        _index_cache = CommunityIndexCache()
    return _index_cache


def _drivers_owner_repo() -> str:
    from openavc.utils.community_integrity import DRIVERS_OWNER_REPO
    return DRIVERS_OWNER_REPO


def _is_catalog_url(url: str) -> bool:
    """True when ``url`` is a file on the driver catalog's published branch."""
    from openavc.utils.community_integrity import (
        DRIVERS_OWNER_REPO,
        CommunityArtifactError,
        validate_catalog_url,
    )
    try:
        validate_catalog_url(url, owner_repo=DRIVERS_OWNER_REPO)
    except CommunityArtifactError:
        return False
    return True


def require_catalog_url(url: str, *, what: str) -> None:
    """Reject a driver URL that isn't under the official community catalog.

    A ``.py`` community driver is imported and registered — arbitrary
    server-side code — so "is it on github.com?" is not a source check: every
    attacker-controlled repo on GitHub answers yes. This pins installs to the
    curated catalog repo, the same way plugin installs are already pinned.

    It matters most for the callers that aren't a person clicking Install: the
    discovery install-and-match flow and the cloud AI's install tool both reach
    this endpoint with a URL chosen upstream.
    """
    from openavc.utils.community_integrity import (
        DRIVERS_OWNER_REPO,
        CommunityArtifactError,
        validate_catalog_url,
    )
    try:
        validate_catalog_url(url, owner_repo=DRIVERS_OWNER_REPO)
    except CommunityArtifactError as e:
        # Name the repo in every rejection, not just the wrong-path one. "It's
        # a GitHub URL" is precisely the test that was never enough, so the
        # message says which repo instead of which host.
        raise HTTPException(
            status_code=422,
            detail=(
                f"{what} must be an https URL under the official community "
                f"driver catalog ({DRIVERS_OWNER_REPO}) — {e}"
            ),
        )


# What a person sees when a driver's download and the catalog still disagree
# after the catalog was fetched again: almost always a driver updated in the
# last few minutes, while GitHub's CDN still serves the previous copy of one
# side. The hashes go to the log (CatalogCheck).
_DRIVER_NOT_YET_INSTALLABLE = (
    "This driver was updated a few minutes ago and cannot be installed yet. "
    "Try again in five minutes."
)


async def _catalog_hashes(driver_id: str, force: bool = False):
    """The catalog's declared file hashes for ``driver_id``.

    Looked up server-side from the community index — never taken from the
    request. A caller-supplied hash would be checked against bytes the same
    caller chose, which verifies nothing. ``force`` fetches the index now
    instead of answering from this server's cached copy.
    """
    from openavc.utils.community_integrity import ArtifactHashes

    files = None
    try:
        for entry in await index_cache().get_drivers(force=force):
            if entry.get("id") == driver_id:
                candidate = entry.get("files")
                if isinstance(candidate, dict):
                    files = candidate
                break
    except Exception:
        # Catalog unreachable. The artifact download itself would normally fail
        # too, so this is rare; treat it as "no hashes published" rather than
        # blocking an install on a catalog hiccup.
        log.warning("Could not read community catalog hashes for %r", driver_id)

    return ArtifactHashes(
        f"Driver '{driver_id}'", files, source="the community driver catalog"
    )


async def catalog_check(driver_id: str):
    """One install's or update's check against the catalog (main file and
    companions alike), which fetches the catalog again once before refusing."""
    from openavc.utils.community_integrity import CatalogCheck

    return await CatalogCheck.fetch(
        lambda force: _catalog_hashes(driver_id, force=force),
        refusal=_DRIVER_NOT_YET_INSTALLABLE,
    )


async def _verify_against_catalog(catalog, url: str, data: bytes) -> None:
    """Check downloaded bytes against the catalog before they reach the disk.

    502 rather than 422 on a mismatch: the request was fine, the upstream
    served something other than what the catalog says it publishes.
    """
    if catalog is None:
        return
    from openavc.utils.community_integrity import (
        DRIVERS_OWNER_REPO,
        CommunityArtifactError,
        catalog_relpath,
    )
    try:
        await catalog.check(catalog_relpath(url, owner_repo=DRIVERS_OWNER_REPO), data)
    except CommunityArtifactError as e:
        raise HTTPException(status_code=502, detail=str(e))


def _companion_name(suffixes: tuple[str, ...]) -> "re.Pattern[str]":
    """A companion file name the catalog doors will write: a driver file stem,
    then one of ``suffixes``."""
    return re.compile(rf"^{DRIVER_FILE_STEM}({'|'.join(map(re.escape, suffixes))})$")


# The companion a YAML driver declares is its discovery file, and a Python
# driver's are found by name: one per suffix the loader knows, so a new kind
# of companion is fetched where it is published without a second list here.
_DECLARED_COMPANION_NAME = _companion_name((DISCOVERY_COMPANION_SUFFIX,))
_CONVENTIONAL_COMPANION_NAME = _companion_name(COMPANION_SUFFIXES)


class CompanionFetch(NamedTuple):
    """Outcome of a best-effort companion fetch.

    ``published`` is the field that matters to update, and it is deliberately
    narrow: it is ``False`` **only** when the catalog answered a definite 404,
    meaning this version of the driver genuinely ships without that companion.
    Every other unhappy path — a transport error, a refused hostname, a name
    that isn't one of the two documented suffixes — leaves it ``True``, because
    those mean "we don't know", and update deletes the local copy when it is
    ``False``. Getting that distinction backwards would delete a working
    simulator every time the network hiccuped.
    """

    path: Path | None
    published: bool = True


def _driver_file_kind(url: str) -> str:
    """The kind of driver a catalog URL names, by its extension."""
    if url.endswith(".avcdriver"):
        return ".avcdriver"
    if url.endswith(".py"):
        return ".py"
    raise HTTPException(status_code=422, detail="URL must point to a .avcdriver or .py file")


class CatalogFiles:
    """One install's or update's files from the community catalog.

    The install and the update bring a catalog driver onto this server the
    same way, so the rules they share live here: the kind of driver the URL
    names and the file it lands in (named for the id, not the URL), how every
    file is fetched (``_fetch``: an internet request, then the catalog check
    before anything is written), and the three files an install may bring,
    each with its own answer to a failure: the main file (a 502 naming the
    driver), the companion a YAML driver names (required: a 502 naming the
    companion) and a Python driver's conventional companions (optional: only
    a definite 404 says one is not published). What each door does around
    them, the version floor, the rollback and the registry, is the door's own.
    """

    def __init__(self, driver_id: str, url: str, driver_repo: Path, catalog: Any) -> None:
        self.driver_id = driver_id
        self.url = url
        self.driver_repo = driver_repo
        self.catalog = catalog
        self.kind = _driver_file_kind(url)
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in driver_id)
        self.file_name = f"{safe_id}{self.kind}"
        self.path = driver_repo / self.file_name

    async def _fetch(self, url: str, *, absent_ok: bool = False) -> Any:
        """One catalog file's response, checked against the catalog before
        any caller writes it. ``None`` for a 404 when ``absent_ok``; otherwise
        a refused status raises ``httpx.HTTPStatusError`` and a failed request
        ``httpx.RequestError``, for the caller to word, and a file the catalog
        does not publish the 502 that says so."""
        async with internet_client(timeout=15.0) as client:
            resp = await client.get(url)
            if absent_ok and resp.status_code == 404:
                return None
            resp.raise_for_status()
        await _verify_against_catalog(self.catalog, url, resp.content)
        return resp

    async def main_file(self) -> tuple[str, bytes]:
        """The driver file's text and bytes, checked against the catalog and
        refused for a protected id, both before anything is written. A failed
        download is the 502 a person sees: GitHub's own status when it refused,
        else why the request failed."""
        import httpx

        try:
            resp = await self._fetch(self.url)
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"GitHub returned {e.response.status_code}")
        except httpx.RequestError as e:
            raise _api_error(
                502,
                internet_failure(e, "raw.githubusercontent.com")
                or f"Failed to download driver '{self.driver_id}'",
                e,
            )
        require_mutable_driver_file(self.file_name, resp.content)
        return resp.text, resp.content

    async def yaml_companion(self, yaml_text: str) -> Path | None:
        """Download the companion a YAML driver names, beside it. ``None`` for
        a Python driver or a YAML that names none; a failure raises, and the
        door removes what it wrote."""
        if self.kind != ".avcdriver":
            return None
        relpath = companion_relpath_from_yaml(yaml_text)
        if not relpath:
            return None
        return await self.declared_companion(relpath)

    async def declared_companion(self, companion_relpath: str) -> Path:
        """Download the companion a YAML driver declares and write it to the
        driver folder; its local path. Any failure raises a descriptive 422 or
        502 so the door can roll back what it wrote."""
        import httpx

        companion_filename, companion_url = self._declared_companion_source(companion_relpath)
        try:
            resp = await self._fetch(companion_url)
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Driver '{self.driver_id}' references companion "
                    f"{companion_filename!r}, but GitHub returned "
                    f"{e.response.status_code} for it. Install aborted."
                ),
            )
        except httpx.RequestError as e:
            raise _api_error(
                502,
                f"Driver '{self.driver_id}' references companion "
                f"{companion_filename!r} but the download failed",
                e,
            )
        companion_filepath = self.driver_repo / companion_filename
        companion_filepath.write_bytes(resp.content)
        return companion_filepath

    def _declared_companion_source(self, companion_relpath: str) -> tuple[str, str]:
        """The file name and catalog address a YAML's companion declaration
        resolves to, refused (422) before anything is asked for.

        ``companion_relpath`` is resolved against the YAML's URL and held to
        the same catalog rule as the YAML itself (the path comes from
        downloaded content, so a host check would let it name any repository
        on GitHub), and its file name to the documented ``_discovery.py``
        suffix and safe characters.
        """
        from urllib.parse import urljoin

        companion_filename = PurePosixPath(companion_relpath).name
        # Require the documented ``_discovery.py`` suffix. Anything else is
        # either a typo or an attempt to use the companion path to land an
        # arbitrary .py file in driver_repo. The uninstall path uses the
        # same suffix check before removing companion files, so this keeps
        # the install / uninstall contract symmetric.
        if not _DECLARED_COMPANION_NAME.match(companion_filename):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Driver '{self.driver_id}' declares companion "
                    f"{companion_relpath!r} with an invalid filename — must "
                    f"end in '{DISCOVERY_COMPANION_SUFFIX}' and use only letters, numbers, "
                    "hyphens, and underscores."
                ),
            )

        companion_url = urljoin(self.url, companion_relpath)
        if not _is_catalog_url(companion_url):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Driver '{self.driver_id}' references companion at "
                    f"{companion_url!r}, which is not a file in the official "
                    f"community driver catalog ({_drivers_owner_repo()})."
                ),
            )
        return companion_filename, companion_url

    async def python_companions(
        self, on_unpublished: Callable[[Path], None] | None = None
    ) -> None:
        """Fetch a Python driver's discovery and simulator files, named from
        the URL's file name, so discovery's backup path works and the device
        can be simulated. A missing one is no failure; ``on_unpublished`` is
        told the local file each definite 404 leaves stale, right after it.

        The main file is named from the id instead, so the two names agree
        only while a catalog driver's file name matches its id.
        """
        if self.kind != ".py":
            return
        stem = PurePosixPath(urlparse(self.url).path).stem
        if not stem:
            return
        for suffix in COMPANION_SUFFIXES:
            name = f"{stem}{suffix}"
            fetched = await self.conventional_companion(name)
            if not fetched.published and on_unpublished is not None:
                on_unpublished(self.driver_repo / name)

    async def conventional_companion(self, companion_filename: str) -> CompanionFetch:
        """Best-effort fetch of one of a Python driver's sibling companions.

        Unlike ``declared_companion`` (YAML drivers, where the companion is
        declared and required), Python-driver companions (``*_discovery.py`` /
        ``*_sim.py``) are located by naming convention and are OPTIONAL: a
        missing one must not fail the install, because the main ``.py``
        controls hardware and auto-identifies from its inline ``tcp_probe``
        without them, and the simulator is a bonus.
        """
        import httpx
        from urllib.parse import urljoin

        # Convention-named, but validate anyway: only the documented suffixes,
        # so a redirect can't land an arbitrary .py in driver_repo.
        if not _CONVENTIONAL_COMPANION_NAME.match(companion_filename):
            return CompanionFetch(None)
        companion_url = urljoin(self.url, companion_filename)
        if not _is_catalog_url(companion_url):
            return CompanionFetch(None)

        companion_filepath = self.driver_repo / companion_filename
        try:
            # A companion that fails the catalog check is NOT swallowed like a
            # fetch error below: "optional" means it may be absent, not that a
            # wrong-bytes copy may be installed. Raises past this handler.
            resp = await self._fetch(companion_url, absent_ok=True)
            if resp is None:
                # The one case we can be sure about: this version ships without
                # that companion.
                return CompanionFetch(None, published=False)
            companion_filepath.write_bytes(resp.content)
        except (httpx.HTTPStatusError, httpx.RequestError, OSError) as e:
            log.warning(
                "Optional companion %s not installed: %s", companion_filename, e
            )
            return CompanionFetch(None)
        return CompanionFetch(companion_filepath)
