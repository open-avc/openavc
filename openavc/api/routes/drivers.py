"""Driver installation and definition CRUD — getting drivers onto this server.

Two halves of one job, which is why they share a module:

* **Install / import / update / uninstall** — the community catalog
  (`/drivers/install`), file upload (`/drivers/upload`, `/drivers/upload-bundle`),
  and the installed-driver lifecycle. Everything downloaded is hashed against
  the catalog before it is written (`server/utils/community_integrity.py`) and
  gated on `min_platform_version`.
* **Driver-definition CRUD** — list / get / validate / create / update / patch
  / delete / reload for the ``.avcdriver`` definitions the Driver Builder edits.

Two neighbours were split out of this module and are *not* here:
`routes/driver_test.py` (the live-test and dry-run harness) and
`routes/python_drivers.py` (``.py`` source management). This module calls into
the latter for `remove_python_companions`, since a Python driver's file set is
that module's rule to own.
"""

import re
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from openavc.api._engine import _get_engine
from openavc.api.auth import require_claimed_auth
from openavc.api.errors import StructuredApiError
from openavc.api.errors import api_error as _api_error
from openavc.api.models import (
    CommunityDriverInstallRequest,
    DriverDefinitionRequest,
)
from openavc.api.routes.python_drivers import remove_python_companions
from openavc.drivers.driver_loader import COMPANION_SUFFIXES
from openavc.utils.logger import get_logger
from openavc.drivers.registry import (
    list_registered_drivers,
    register_driver,
    registered_driver_classes,
    unregister_driver,
)

log = get_logger(__name__)

router = APIRouter()


def _parse_semver(v: str) -> tuple[int, int, int]:
    """Parse "X.Y.Z" into a comparable 3-tuple.

    Always three parts, so "0.22" compares equal to "0.22.0" instead of
    less-than, and a part with a pre-release/build suffix keeps its numeric
    prefix ("22-rc1" -> 22) instead of vanishing from the tuple.
    """
    parts: list[int] = []
    for piece in v.strip().split(".")[:3]:
        match = re.match(r"\d+", piece)
        parts.append(int(match.group()) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def _enforce_min_platform_version(required: str) -> None:
    """Raise HTTPException 422 if the running OpenAVC is older than ``required``.

    Used by both the request-field path and the post-download YAML-parsed path
    so /api/drivers/install and /api/discovery/install-and-match converge on
    the same gate (A65).
    """
    from openavc.version import __version__

    try:
        current_tup = _parse_semver(__version__)
        required_tup = _parse_semver(required)
    except Exception:
        log.debug("Version parse failed; allowing install", exc_info=True)
        return
    if current_tup < required_tup:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This driver requires OpenAVC {required} or later. "
                f"You are running {__version__}. Please update OpenAVC first."
            ),
        )


def _peek_min_platform_version(yaml_text: str) -> str | None:
    """Best-effort extract ``min_platform_version`` from raw driver YAML."""
    try:
        import yaml as _yaml
        parsed = _yaml.safe_load(yaml_text)
    except Exception:
        return None
    if isinstance(parsed, dict):
        value = parsed.get("min_platform_version")
        if isinstance(value, str) and value:
            return value
    return None


def _enforce_driver_id_match(
    driver_class: Any,
    expected_id: str,
    main_path: Path,
    companion_path: Path | None = None,
) -> None:
    """Raise HTTPException 422 if the driver's own DRIVER_INFO id differs from
    the requested ``expected_id``.

    The registry keys on the file's own DRIVER_INFO id, but the filename and the
    listing/edit/delete surfaces key on the requested id. If they diverge, a
    later edit/delete can't find the driver and a mismatched id can silently
    overwrite an unrelated registered driver — require them to agree before
    registering. Rolls back the downloaded file(s) on mismatch so the install
    stays atomic.
    """
    info = getattr(driver_class, "DRIVER_INFO", None)
    internal_id = info.get("id") if isinstance(info, dict) else None
    if internal_id and internal_id != expected_id:
        main_path.unlink(missing_ok=True)
        if companion_path:
            companion_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail=(
                f"Downloaded driver declares id '{internal_id}', not the "
                f"requested '{expected_id}'. Install it under id '{internal_id}'."
            ),
        )


# --- Drivers ---


@router.get("/drivers")
async def list_drivers() -> dict[str, Any]:
    """List all available driver types with their metadata."""
    return {"drivers": list_registered_drivers()}


@router.get("/drivers/{driver_id}/help")
async def get_driver_help(driver_id: str) -> dict[str, Any]:
    """Get help text (overview + setup instructions) for an installed driver."""

    for drv in list_registered_drivers():
        if drv.get("id") == driver_id:
            help_info = drv.get("help")
            if help_info and isinstance(help_info, dict):
                return {
                    "driver_id": driver_id,
                    "overview": help_info.get("overview", ""),
                    "setup": help_info.get("setup", ""),
                }
            raise HTTPException(status_code=404, detail="Driver has no help information")

    raise HTTPException(status_code=404, detail="Driver not found")


# --- Community / Installed Drivers ---

# Base URL for the community driver repo on GitHub
COMMUNITY_REPO_URL = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"

# Hosts the install / update endpoints are allowed to fetch from. Used
# by both the YAML download and the sibling companion download so the
# allowlist stays consistent.
#
# A host check alone is NOT the gate — see _require_catalog_url below. Kept
# for the error text and for the discovery-hint fetches that aren't installs.
_GITHUB_HOSTS: frozenset[str] = frozenset({
    "raw.githubusercontent.com",
    "github.com",
    "api.github.com",
})


def _require_catalog_url(url: str, *, what: str) -> None:
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


async def _catalog_hashes(driver_id: str):
    """The catalog's declared file hashes for ``driver_id``.

    Looked up server-side from the community index — never taken from the
    request. A caller-supplied hash would be checked against bytes the same
    caller chose, which verifies nothing.
    """
    from openavc.utils.community_integrity import ArtifactHashes

    files = None
    try:
        from openavc.discovery.community_index import CommunityIndexCache

        if not hasattr(get_community_drivers, "_cache"):
            get_community_drivers._cache = CommunityIndexCache()
        for entry in await get_community_drivers._cache.get_drivers():
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


def _verify_against_catalog(hashes, url: str, data: bytes) -> None:
    """Check downloaded bytes against the catalog before they reach the disk.

    502 rather than 422 on a mismatch: the request was fine, the upstream
    served something other than what the catalog says it publishes.
    """
    if hashes is None:
        return
    from openavc.utils.community_integrity import (
        DRIVERS_OWNER_REPO,
        CommunityArtifactError,
        catalog_relpath,
    )
    try:
        hashes.check(catalog_relpath(url, owner_repo=DRIVERS_OWNER_REPO), data)
    except CommunityArtifactError as e:
        raise HTTPException(status_code=502, detail=str(e))


def _get_driver_repo_dir() -> Path:
    """Get the driver_repo/ directory path."""
    from openavc.system_config import DRIVER_REPO_DIR
    return DRIVER_REPO_DIR


def _strip_listing_decorations(definition: dict) -> dict:
    """Drop the bookkeeping the listing endpoint adds to a definition.

    ``GET /driver-definitions`` decorates each definition with where it came
    from — ``source`` ("builtin"/"user"), and historically ``_source_file``.
    Those are not driver-format keys, and an editor that loaded a definition
    from that endpoint hands them straight back when it saves. The authoring
    gate is strict about undeclared keys on purpose, so without this the
    server rejects a save over a key the server itself added. Same carve-out
    the Builder's own validator makes (validateDriver.ts).
    """
    return {
        key: value
        for key, value in definition.items()
        if key != "source" and not key.startswith("_")
    }


def _definition_invalid(errors: list[str], preamble: str | None = None) -> StructuredApiError:
    """422 for a driver definition that failed validation.

    The ``detail`` string is the whole message — heading plus the errors, one per
    line — so a client that only reads ``detail`` still shows the author every
    problem to fix. The same list rides along in ``errors`` for anything that
    wants to render them individually.
    """
    heading = preamble or f"{len(errors)} validation error(s) in driver definition"
    detail = f"{heading}:\n" + "\n".join(errors) if errors else heading
    return StructuredApiError(422, detail, errors=errors)


def _reject_unknown_keys(driver_def: dict[str, Any], cleanup: Path | None = None) -> None:
    """Import gate: refuse a definition carrying keys the contract doesn't declare.

    Importing is authoring's cousin — the person who can fix the typo is right
    here, and nothing is running on the driver yet, so refusing costs a
    re-export and saves a section that silently does nothing. (The runtime
    loader only warns, deliberately: dropping a driver already in service would
    take its devices offline. See ``unknown_key_errors``.)
    """
    from openavc.drivers.avcdriver_semantic import unknown_key_errors

    problems = unknown_key_errors(driver_def)
    if not problems:
        return
    if cleanup is not None:
        cleanup.unlink(missing_ok=True)
    raise _definition_invalid(
        problems,
        preamble=(
            f"{len(problems)} unrecognized key(s) in driver definition — "
            "check the spelling against the driver contract"
        ),
    )


def _companion_relpath_from_yaml(yaml_text: str) -> str | None:
    """Return the raw ``discovery.python.file`` string if declared.

    Used by install / update / uninstall to locate the sibling Python
    companion that goes with a YAML driver. Drivers like ``crestron_cip``
    and ``onvif_camera`` declare e.g. ``python: ./crestron_cip_discovery.py``
    in their ``discovery:`` block; the runtime can't function without
    that file present in ``driver_repo/`` next to the YAML.

    Returns ``None`` if the YAML can't be parsed or has no companion;
    the caller decides what that means in context.
    """
    import yaml as _yaml
    try:
        parsed = _yaml.safe_load(yaml_text)
    except _yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    discovery = parsed.get("discovery") or {}
    if not isinstance(discovery, dict):
        return None
    block = discovery.get("python")
    if isinstance(block, str):
        return block or None
    if isinstance(block, dict):
        path = block.get("file")
        if isinstance(path, str) and path:
            return path
    return None


async def _download_companion(
    *,
    yaml_url: str,
    companion_relpath: str,
    driver_repo: Path,
    driver_id: str,
    hashes=None,
) -> Path:
    """Download a YAML driver's sibling Python companion.

    Resolves ``companion_relpath`` against the YAML's URL via
    ``urljoin``, validates the resulting host stays on the GitHub
    allowlist (so a hostile YAML can't redirect the fetch to an
    arbitrary URL), sanitizes the filename the same way the upload
    endpoint does, and writes the file to ``driver_repo``. Returns
    the local path.

    Raises ``HTTPException`` with a descriptive 422 / 502 on any
    failure so callers can roll back partial state.
    """
    import re
    import httpx
    from pathlib import PurePosixPath
    from urllib.parse import urljoin, urlparse

    companion_url = urljoin(yaml_url, companion_relpath)
    parsed = urlparse(companion_url)
    if not parsed.hostname or parsed.hostname not in _GITHUB_HOSTS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Driver '{driver_id}' references companion at "
                f"{companion_url!r}, which is not on an allowed host "
                f"({', '.join(sorted(_GITHUB_HOSTS))})."
            ),
        )

    companion_filename = PurePosixPath(companion_relpath).name
    # Require the documented ``_discovery.py`` suffix. Anything else is
    # either a typo or an attempt to use the companion path to land an
    # arbitrary .py file in driver_repo. The uninstall path uses the
    # same suffix check before removing companion files, so this keeps
    # the install / uninstall contract symmetric.
    if not re.match(r'^[a-zA-Z0-9_\-]+_discovery\.py$', companion_filename):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Driver '{driver_id}' declares companion "
                f"{companion_relpath!r} with an invalid filename — must "
                "end in '_discovery.py' and use only letters, numbers, "
                "hyphens, and underscores."
            ),
        )

    companion_filepath = driver_repo / companion_filename
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(companion_url)
            resp.raise_for_status()
            _verify_against_catalog(hashes, companion_url, resp.content)
            companion_filepath.write_bytes(resp.content)
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Driver '{driver_id}' references companion "
                f"{companion_filename!r}, but GitHub returned "
                f"{e.response.status_code} for it. Install aborted."
            ),
        )
    except httpx.RequestError as e:
        raise _api_error(
            502,
            f"Driver '{driver_id}' references companion "
            f"{companion_filename!r} but the download failed",
            e,
        )

    return companion_filepath


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


async def _try_download_python_companion(
    *,
    main_url: str,
    companion_filename: str,
    driver_repo: Path,
    hashes=None,
) -> CompanionFetch:
    """Best-effort fetch of a Python driver's conventional sibling companion.

    Unlike ``_download_companion`` (YAML drivers, where the companion is
    declared and required), Python-driver companions (``*_discovery.py`` /
    ``*_sim.py``) are located by naming convention and are OPTIONAL: a missing
    one must not fail the install, because the main ``.py`` controls hardware
    and auto-identifies from its inline ``tcp_probe`` without them, and the
    simulator is a bonus.
    """
    import re
    import httpx
    from urllib.parse import urljoin, urlparse

    # Convention-named, but validate anyway: only the two documented suffixes,
    # so a redirect can't land an arbitrary .py in driver_repo.
    if not re.match(r'^[a-zA-Z0-9_\-]+_(discovery|sim)\.py$', companion_filename):
        return CompanionFetch(None)
    companion_url = urljoin(main_url, companion_filename)
    parsed = urlparse(companion_url)
    if not parsed.hostname or parsed.hostname not in _GITHUB_HOSTS:
        return CompanionFetch(None)

    companion_filepath = driver_repo / companion_filename
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(companion_url)
            if resp.status_code == 404:
                # The one case we can be sure about: this version ships without
                # that companion.
                return CompanionFetch(None, published=False)
            resp.raise_for_status()
            # A companion that fails the catalog check is NOT swallowed like a
            # fetch error below: "optional" means it may be absent, not that a
            # wrong-bytes copy may be installed. Raises past this handler.
            _verify_against_catalog(hashes, companion_url, resp.content)
            companion_filepath.write_bytes(resp.content)
    except (httpx.HTTPStatusError, httpx.RequestError, OSError) as e:
        log.warning(
            "Optional companion %s not installed: %s", companion_filename, e
        )
        return CompanionFetch(None)
    return CompanionFetch(companion_filepath)


@router.get("/drivers/community")
async def get_community_drivers() -> dict[str, Any]:
    """Fetch the community driver index from GitHub (cached)."""
    from openavc.discovery.community_index import CommunityIndexCache

    if not hasattr(get_community_drivers, "_cache"):
        get_community_drivers._cache = CommunityIndexCache()

    drivers = await get_community_drivers._cache.get_drivers()
    return {"drivers": drivers, "error": None if drivers else "Failed to fetch community drivers"}


@router.post("/drivers/install")
async def install_community_driver(body: CommunityDriverInstallRequest) -> dict[str, Any]:
    """Download and install a driver from the community repo."""
    import httpx
    from openavc.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    # min_platform_version supplied by the caller. The YAML body itself is
    # checked again after download (see _enforce_min_platform_version) so
    # callers like /api/discovery/install-and-match — which don't carry the
    # field on the request — can't bypass the gate (A65, cousin to A32).
    if body.min_platform_version:
        _enforce_min_platform_version(body.min_platform_version)

    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    # Pin the source to the official catalog repo (not merely "some GitHub URL")
    url = body.file_url
    from urllib.parse import urlparse
    _require_catalog_url(url, what="Driver URL")

    # The catalog's hashes for this driver, read server-side. Fetched before the
    # download so a mismatch is caught with nothing yet written.
    hashes = await _catalog_hashes(body.driver_id)

    # Determine file type from URL
    if url.endswith(".avcdriver"):
        ext = ".avcdriver"
    elif url.endswith(".py"):
        ext = ".py"
    else:
        raise HTTPException(status_code=422, detail="URL must point to a .avcdriver or .py file")

    # Sanitize filename from driver_id
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in body.driver_id)
    filename = f"{safe_id}{ext}"
    filepath = driver_repo / filename

    # Download the file, check it against the catalog, and only then write it.
    # Verifying after the write would mean a driver that fails the check has
    # already landed in driver_repo/, where the loader would find it.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            yaml_text = resp.text
            _verify_against_catalog(hashes, url, resp.content)
            filepath.write_bytes(resp.content)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"GitHub returned {e.response.status_code}")
    except httpx.RequestError as e:
        raise _api_error(502, f"Failed to download driver '{body.driver_id}'", e)

    # For YAML drivers, also enforce min_platform_version parsed from the file
    # itself. This guards endpoints that call install_community_driver without
    # passing the request field (Discovery's install-and-match, future callers).
    if ext == ".avcdriver":
        yaml_min_version = _peek_min_platform_version(yaml_text)
        if yaml_min_version:
            try:
                _enforce_min_platform_version(yaml_min_version)
            except HTTPException:
                # Roll back the download so an incompatible driver isn't left
                # on disk.
                filepath.unlink(missing_ok=True)
                raise

    # If this is a YAML driver with a sibling Python companion (e.g.
    # crestron_cip → crestron_cip_discovery.py), fetch the companion
    # alongside. Roll back the YAML on any companion-fetch failure so
    # the install is atomic.
    companion_filepath: Path | None = None
    if ext == ".avcdriver":
        relpath = _companion_relpath_from_yaml(yaml_text)
        if relpath:
            try:
                companion_filepath = await _download_companion(
                    yaml_url=url,
                    companion_relpath=relpath,
                    driver_repo=driver_repo,
                    driver_id=body.driver_id,
                    hashes=hashes,
                )
            except HTTPException:
                filepath.unlink(missing_ok=True)
                raise

    # Register the driver
    try:
        if ext == ".avcdriver":
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                filepath.unlink(missing_ok=True)
                if companion_filepath:
                    companion_filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="Invalid driver definition file")
            driver_class = create_configurable_driver_class(driver_def)
            _enforce_driver_id_match(driver_class, body.driver_id, filepath, companion_filepath)
            register_driver(driver_class)
        else:
            driver_class = load_python_driver_file(filepath)
            if driver_class is None:
                filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="No valid driver class found in Python file")
            _enforce_driver_id_match(driver_class, body.driver_id, filepath)
            register_driver(driver_class)
    except HTTPException:
        raise
    except Exception as e:
        filepath.unlink(missing_ok=True)
        if companion_filepath:
            companion_filepath.unlink(missing_ok=True)
        raise _api_error(500, f"Failed to load driver '{body.driver_id}'", e)

    # For Python drivers, also fetch the conventional sibling companions
    # (_discovery.py / _sim.py) so the install is complete: discovery's backup
    # path works and the device can be simulated. They're located by naming
    # convention (not declared) and optional, so a 404 just means the driver
    # ships without one. YAML drivers fetch their declared companion above and
    # get simulation from their inline `simulator:` section instead.
    if ext == ".py":
        from pathlib import PurePosixPath
        src_stem = PurePosixPath(urlparse(url).path).stem
        if src_stem:
            for suffix in COMPANION_SUFFIXES:
                await _try_download_python_companion(
                    main_url=url,
                    companion_filename=f"{src_stem}{suffix}",
                    driver_repo=driver_repo,
                    hashes=hashes,
                )

    # Refresh discovery engine with new driver hints
    from openavc.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    # Promote any project devices that were orphaned because this driver
    # wasn't installed yet. Without this the user would have to reload the
    # project (or restart) to see the device come online.
    activated: list[str] = []
    try:
        engine = _get_engine()
        activated = await engine.devices.retry_all_orphans()
    except Exception:
        log.exception("Failed to retry orphans after install")

    return {
        "status": "installed",
        "driver_id": body.driver_id,
        "file": filename,
        "activated_devices": activated,
    }


@router.post("/drivers/upload")
async def upload_driver(request: Request) -> dict[str, Any]:
    """Upload a driver file (.avcdriver or .py) from the user's computer."""
    from openavc.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    # Accept multipart form data with a "file" field
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=422, detail="No file provided. Use 'file' field in multipart form.")

    raw_filename = upload.filename or "unknown"
    # Sanitize filename: strip directory components to prevent path traversal
    import re as _re
    from pathlib import PurePosixPath as _PurePosixPath
    filename = _PurePosixPath(raw_filename).name
    if not filename.endswith((".avcdriver", ".py")):
        raise HTTPException(status_code=422, detail="File must be .avcdriver or .py")
    # Reject filenames with suspicious characters (allow alphanumeric, hyphens, underscores, dots)
    if not _re.match(r'^[a-zA-Z0-9_\-]+\.(avcdriver|py)$', filename):
        raise HTTPException(status_code=422, detail="Invalid filename — use only letters, numbers, hyphens, and underscores")

    content = await upload.read()
    filepath = driver_repo / filename
    filepath.write_bytes(content)

    # Register the driver
    try:
        if filename.endswith(".avcdriver"):
            # Companion check: a YAML that declares discovery.python won't
            # actually function unless the sibling _discovery.py is also
            # present in driver_repo/. load_driver_file would log + return
            # None below, but the user gets the generic 'Invalid driver
            # definition file' message — surface a more useful one here.
            import yaml as _yaml_peek
            from openavc.drivers.driver_loader import companion_relpath_from_def
            try:
                _peek = _yaml_peek.safe_load(content)
            except _yaml_peek.YAMLError:
                _peek = None
            if isinstance(_peek, dict):
                companion_relpath = companion_relpath_from_def(_peek)
                if companion_relpath:
                    companion_filename = _PurePosixPath(companion_relpath).name
                    companion_path = driver_repo / companion_filename
                    if not companion_path.is_file():
                        filepath.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                f"Driver {filename} declares a Python "
                                f"companion ({companion_relpath!r}) but "
                                f"{companion_filename!r} is not in the "
                                "driver library yet. Upload the "
                                "_discovery.py file first, then re-upload "
                                "this driver."
                            ),
                        )
            driver_def = load_driver_file(filepath)
            if driver_def is None:
                filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="Invalid driver definition file")
            _reject_unknown_keys(driver_def, filepath)
            driver_class = create_configurable_driver_class(driver_def)
            register_driver(driver_class)
            driver_id = driver_def.get("id", filename)
        else:
            driver_class = load_python_driver_file(filepath)
            if driver_class is None:
                filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="No valid driver class found in Python file")
            register_driver(driver_class)
            driver_id = driver_class.DRIVER_INFO.get("id", filename)
    except HTTPException:
        raise
    except Exception as e:
        filepath.unlink(missing_ok=True)
        raise _api_error(500, f"Failed to load uploaded driver '{filename}'", e)

    # Promote any project devices waiting on this driver — same flow as
    # /drivers/install so manual uploads behave identically.
    activated: list[str] = []
    try:
        engine = _get_engine()
        activated = await engine.devices.retry_all_orphans()
    except Exception:
        log.exception("Failed to retry orphans after upload")

    return {
        "status": "uploaded",
        "driver_id": driver_id,
        "file": filename,
        "activated_devices": activated,
    }


@router.post("/drivers/upload-bundle")
async def upload_driver_bundle(request: Request) -> dict[str, Any]:
    """Upload a driver as a .zip bundle: one driver file plus its companions.

    A Python driver is really a bundle — the main ``.py`` plus an optional
    ``*_discovery.py`` companion and an optional ``*_sim.py`` simulator. This
    endpoint accepts a zip of those files (it also handles a YAML driver +
    its ``_discovery.py``), drops them into ``driver_repo/``, then loads and
    registers the single main driver. Companion-only zips are rejected.

    Note: a Python driver is executable code; loading one runs it in the
    server process. This validates file *shape* (zip integrity, allowed
    names/types, exactly one main), not safety — the same trust model as
    installing any community Python driver.
    """
    import io
    import re
    import zipfile
    from pathlib import PurePosixPath

    from openavc.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=422, detail="No file provided. Use 'file' field in multipart form.")
    raw_name = upload.filename or "bundle.zip"
    if not raw_name.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Bundle must be a .zip file")

    content = await upload.read()
    # Zip-bomb guards: generous ceilings (a driver bundle is a few small text
    # files) that only stop pathological inputs, not legitimate drivers.
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Bundle is too large (max 25 MB).")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Not a valid .zip file.")

    name_re = re.compile(r'^[a-zA-Z0-9_\-]+\.(py|avcdriver)$')
    members = [m for m in archive.infolist() if not m.is_dir()]
    if len(members) > 100:
        raise HTTPException(status_code=422, detail="Bundle has too many files.")
    if sum(m.file_size for m in members) > 50 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Bundle contents are too large.")

    # Validate every entry up front: basename only (defuses path traversal),
    # allowed name + type. Reject the whole bundle on any stray file so the
    # contract is unambiguous.
    entries: dict[str, bytes] = {}
    for member in members:
        base = PurePosixPath(member.filename).name
        if not base or not name_re.match(base):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Bundle contains a disallowed file: {member.filename!r}. "
                    "Only .py and .avcdriver files are allowed."
                ),
            )
        entries[base] = archive.read(member)
    if not entries:
        raise HTTPException(status_code=422, detail="Bundle is empty.")

    mains = [n for n in entries if not n.endswith(COMPANION_SUFFIXES)]
    if not mains:
        raise HTTPException(
            status_code=422,
            detail="Bundle has no main driver file — it contains only companions.",
        )
    if len(mains) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Bundle has more than one driver file ({', '.join(sorted(mains))}). "
                "A bundle must hold exactly one driver plus its companions."
            ),
        )
    main_name = mains[0]

    # Write everything, then load the main. Roll back only the files this call
    # created, so re-importing over an existing driver that fails to load
    # doesn't delete the user's previous good copy.
    created: list[Path] = []
    try:
        for name, data in entries.items():
            filepath = driver_repo / name
            if not filepath.exists():
                created.append(filepath)
            filepath.write_bytes(data)

        main_path = driver_repo / main_name
        if main_name.endswith(".avcdriver"):
            driver_def = load_driver_file(main_path)
            if driver_def is None:
                raise HTTPException(status_code=422, detail="Invalid driver definition file in bundle.")
            _reject_unknown_keys(driver_def)
            register_driver(create_configurable_driver_class(driver_def))
            driver_id = driver_def.get("id", main_name)
        else:
            driver_class = load_python_driver_file(main_path)
            if driver_class is None:
                raise HTTPException(status_code=422, detail="No valid driver class found in the bundle's Python file.")
            register_driver(driver_class)
            driver_id = driver_class.DRIVER_INFO.get("id", main_name)
    except HTTPException:
        for fp in created:
            fp.unlink(missing_ok=True)
        raise
    except Exception as e:
        for fp in created:
            fp.unlink(missing_ok=True)
        raise _api_error(500, f"Failed to load uploaded driver bundle '{raw_name}'", e)

    # Refresh discovery hints + promote any devices waiting on this driver,
    # matching the single-file upload / community install paths.
    from openavc.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()
    activated: list[str] = []
    try:
        engine = _get_engine()
        activated = await engine.devices.retry_all_orphans()
    except Exception:
        log.exception("Failed to retry orphans after bundle upload")

    return {
        "status": "uploaded",
        "driver_id": driver_id,
        "files": sorted(entries.keys()),
        "activated_devices": activated,
    }


@router.get("/drivers/installed")
async def list_installed_community_drivers() -> dict[str, Any]:
    """List drivers installed in driver_repo/."""
    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        return {"drivers": []}

    installed: list[dict[str, Any]] = []

    # Scan .avcdriver files
    for filepath in sorted(driver_repo.glob("*.avcdriver")):
        try:
            import yaml
            data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                installed.append({
                    "id": data.get("id", filepath.stem),
                    "name": data.get("name", filepath.stem),
                    "format": "avcdriver",
                    "filename": filepath.name,
                    "version": data.get("version", ""),
                })
        except (yaml.YAMLError, OSError):
            installed.append({
                "id": filepath.stem,
                "name": filepath.stem,
                "format": "avcdriver",
                "filename": filepath.name,
                "version": "",
            })

    # Scan .py files (skip discovery / simulator companions and
    # underscore-prefixed helpers — they live next to drivers but
    # aren't drivers themselves).
    from openavc.drivers.driver_loader import _is_driver_file

    for filepath in sorted(driver_repo.glob("*.py")):
        if not _is_driver_file(filepath):
            continue
        driver_id = filepath.stem
        driver_name = filepath.stem.replace("_", " ").title()

        # Try to extract actual info from the loaded registry
        driver_version = ""
        for reg_id, cls in registered_driver_classes():
            info = cls.DRIVER_INFO
            # Match by checking if the module was loaded from this file
            if info.get("id") and filepath.stem in getattr(
                cls, "__module__", ""
            ):
                driver_id = info["id"]
                driver_name = info.get("name", driver_name)
                driver_version = info.get("version", "")
                break

        installed.append({
            "id": driver_id,
            "name": driver_name,
            "format": "python",
            "filename": filepath.name,
            "version": driver_version,
        })

    return {"drivers": installed}


@router.delete("/drivers/installed/{driver_id}")
async def uninstall_driver(driver_id: str) -> dict[str, Any]:
    """Uninstall a driver from driver_repo/ and unregister from memory."""

    # Safety check: don't allow uninstalling if devices are using this driver
    engine = _get_engine()
    if engine.project:
        using_devices = [
            d.id for d in engine.project.devices
            if d.driver == driver_id
        ]
        if using_devices:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot uninstall: driver is in use by device(s): {', '.join(using_devices)}",
            )

    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        raise HTTPException(status_code=404, detail="Driver not found")

    # Find the file by stem or by reading the driver ID from the file
    deleted_file = None
    for filepath in list(driver_repo.glob("*.avcdriver")) + list(driver_repo.glob("*.py")):
        if filepath.name.startswith("_"):
            continue
        if filepath.stem == driver_id:
            deleted_file = filepath
            break
        # Check actual ID inside YAML files
        try:
            if filepath.suffix == ".avcdriver":
                import yaml
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id") == driver_id:
                    deleted_file = filepath
                    break
        except (yaml.YAMLError, OSError):
            continue

    if not deleted_file:
        raise HTTPException(status_code=404, detail=f"Driver '{driver_id}' not found in driver_repo")

    # If the YAML declared a Python companion, delete it too — the
    # install endpoint fetches the pair as one unit, and leaving the
    # companion behind would clutter the Code tab and the Installed
    # Drivers panel with an orphaned probe file.
    companion_to_delete: Path | None = None
    if deleted_file.suffix == ".avcdriver":
        try:
            yaml_text = deleted_file.read_text(encoding="utf-8")
        except OSError:
            yaml_text = ""
        if yaml_text:
            relpath = _companion_relpath_from_yaml(yaml_text)
            if relpath:
                from pathlib import PurePosixPath
                companion_filename = PurePosixPath(relpath).name
                candidate = driver_repo / companion_filename
                # Only remove if it actually lives in driver_repo (so a
                # stray "../foo.py" path can't escape) and the companion
                # follows the documented `_discovery.py` suffix — anything
                # else is the user's own .py and shouldn't be touched.
                try:
                    candidate.resolve().relative_to(driver_repo.resolve())
                except ValueError:
                    candidate = None
                if candidate and candidate.exists() and candidate.name.endswith("_discovery.py"):
                    companion_to_delete = candidate

    deleted_file.unlink(missing_ok=True)
    if companion_to_delete is not None:
        companion_to_delete.unlink(missing_ok=True)
    # Python drivers carry their companions by naming convention rather than a
    # YAML declaration; drop the discovery / sim siblings to match the install
    # side, which fetches them as part of the same install.
    if deleted_file.suffix == ".py":
        remove_python_companions(deleted_file)
    unregister_driver(driver_id)

    # Refresh discovery engine so stale matches are cleared
    from openavc.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    return {"status": "uninstalled", "driver_id": driver_id}


@router.post("/drivers/installed/{driver_id}/update")
async def update_driver(driver_id: str, request: Request) -> dict[str, Any]:
    """Update an installed community driver to a newer version."""
    import httpx
    from openavc.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        raise HTTPException(status_code=404, detail="Driver not found")

    body = await request.json()
    file_url = body.get("file_url")
    if not file_url:
        raise HTTPException(status_code=422, detail="file_url is required")

    # Check minimum platform version requirement (caller-supplied; YAML-based
    # check happens after download below)
    min_ver = body.get("min_platform_version")
    if min_ver:
        _enforce_min_platform_version(min_ver)

    # Find the existing file
    old_file = None
    for filepath in list(driver_repo.glob("*.avcdriver")) + list(driver_repo.glob("*.py")):
        if filepath.name.startswith("_"):
            continue
        if filepath.stem == driver_id:
            old_file = filepath
            break
        try:
            if filepath.suffix == ".avcdriver":
                import yaml
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("id") == driver_id:
                    old_file = filepath
                    break
        except (yaml.YAMLError, OSError):
            continue

    if not old_file:
        raise HTTPException(status_code=404, detail=f"Driver '{driver_id}' not found in driver_repo")

    # Validate the new URL the same way install does — the find-old-file
    # block above was happy to accept any file_url, but the install
    # endpoint pins the source to the official catalog repo.
    _require_catalog_url(file_url, what="Driver URL")

    hashes = await _catalog_hashes(driver_id)

    # Determine file type from URL
    if file_url.endswith(".avcdriver"):
        ext = ".avcdriver"
    elif file_url.endswith(".py"):
        ext = ".py"
    else:
        raise HTTPException(status_code=422, detail="URL must point to a .avcdriver or .py file")

    # Resolve the existing companion (if any) before we touch anything,
    # so we know what to clean up after the new install lands.
    old_companion: Path | None = None
    if old_file.suffix == ".avcdriver":
        try:
            old_yaml_text = old_file.read_text(encoding="utf-8")
        except OSError:
            old_yaml_text = ""
        if old_yaml_text:
            old_relpath = _companion_relpath_from_yaml(old_yaml_text)
            if old_relpath:
                from pathlib import PurePosixPath
                old_companion_name = PurePosixPath(old_relpath).name
                candidate = driver_repo / old_companion_name
                try:
                    candidate.resolve().relative_to(driver_repo.resolve())
                except ValueError:
                    candidate = None
                if (
                    candidate
                    and candidate.exists()
                    and candidate.name.endswith("_discovery.py")
                ):
                    old_companion = candidate

    # Download new version
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in driver_id)
    new_filename = f"{safe_id}{ext}"
    new_filepath = driver_repo / new_filename

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            new_content = resp.text
            new_bytes = resp.content
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"GitHub returned {e.response.status_code}")
    except httpx.RequestError as e:
        raise _api_error(502, f"Failed to download driver '{driver_id}'", e)

    # Check before the swap below unregisters the driver and deletes the old
    # file: a failed update must leave the working driver in place.
    _verify_against_catalog(hashes, file_url, new_bytes)

    # For YAML drivers, also enforce the version pulled from the file itself
    # so a caller that omits min_platform_version still can't install an
    # incompatible driver (A65).
    if ext == ".avcdriver":
        yaml_min_version = _peek_min_platform_version(new_content)
        if yaml_min_version:
            _enforce_min_platform_version(yaml_min_version)

    # Unregister old, delete old file, write new
    unregister_driver(driver_id)
    if old_file != new_filepath:
        old_file.unlink(missing_ok=True)
    new_filepath.write_bytes(new_bytes)

    # Fetch the new YAML's sibling companion (if any). If that fails,
    # roll back the new YAML and remove the old companion — the user
    # is left with neither old nor new but a clear error, which is the
    # same atomicity the install endpoint provides.
    new_companion: Path | None = None
    if ext == ".avcdriver":
        relpath = _companion_relpath_from_yaml(new_content)
        if relpath:
            try:
                new_companion = await _download_companion(
                    yaml_url=file_url,
                    companion_relpath=relpath,
                    driver_repo=driver_repo,
                    driver_id=driver_id,
                    hashes=hashes,
                )
            except HTTPException:
                new_filepath.unlink(missing_ok=True)
                if old_companion is not None and old_companion.exists():
                    old_companion.unlink(missing_ok=True)
                raise

    # The new YAML may declare a different companion filename than the
    # old one — drop the orphaned old companion in that case.
    if (
        old_companion is not None
        and old_companion.exists()
        and (new_companion is None or old_companion != new_companion)
    ):
        old_companion.unlink(missing_ok=True)

    # Refresh a Python driver's convention companions, exactly as install
    # fetches them. Without this the new driver code lands beside the previous
    # version's `_sim.py` / `_discovery.py`: the simulator keeps answering with
    # the old protocol while the driver speaks the new one, so a project tested
    # against it passes on a stale answer, and discovery probes with logic that
    # no longer matches the driver.
    #
    # Names come from the source URL's stem, the same as install — the main
    # file is named from `driver_id` instead, so the two only agree while every
    # catalog driver's filename matches its id. Assert rather than inherit that.
    if ext == ".py":
        from pathlib import PurePosixPath
        from urllib.parse import urlparse as _up
        src_stem = PurePosixPath(_up(file_url).path).stem
        if src_stem:
            for suffix in COMPANION_SUFFIXES:
                companion_name = f"{src_stem}{suffix}"
                fetched = await _try_download_python_companion(
                    main_url=file_url,
                    companion_filename=companion_name,
                    driver_repo=driver_repo,
                    hashes=hashes,
                )
                if not fetched.published:
                    # A definite 404: this version dropped the companion, so the
                    # previous version's copy has to go with it or it outlives
                    # its driver forever. Only on a 404 — a transport error
                    # leaves `published` True precisely so a flaky network can't
                    # delete a working simulator.
                    stale = driver_repo / companion_name
                    if stale.is_file():
                        stale.unlink(missing_ok=True)
                        log.info(
                            "Removed %s — no longer published for driver '%s'",
                            companion_name, driver_id,
                        )

    # Load and register new version
    try:
        if ext == ".avcdriver":
            driver_def = load_driver_file(new_filepath)
            if driver_def is None:
                raise HTTPException(status_code=422, detail="Invalid driver definition file")
            driver_class = create_configurable_driver_class(driver_def)
            register_driver(driver_class)
        else:
            driver_class = load_python_driver_file(new_filepath)
            if driver_class is None:
                raise HTTPException(status_code=422, detail="No valid driver class found in Python file")
            register_driver(driver_class)
    except HTTPException:
        raise
    except Exception as e:
        raise _api_error(500, f"Failed to load updated driver '{driver_id}'", e)

    from openavc.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    return {"status": "updated", "driver_id": driver_id, "file": new_filename}


# --- Driver Definitions ---


def _get_driver_dirs() -> list[Path]:
    """Get directories containing driver definitions."""
    from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
    return [
        DRIVER_DEFINITIONS_DIR,
        DRIVER_REPO_DIR,
    ]


@router.get("/driver-definitions")
async def list_driver_definitions() -> dict[str, Any]:
    """List all JSON driver definitions.

    Adds a `source` field to each entry: `"builtin"` for drivers that ship
    in the platform's read-only definitions directory, `"user"` for drivers
    that live in the user/community driver_repo (created via the Driver
    Builder or installed from the community catalog). The Driver Builder
    uses this to gate edit-in-place vs. customize-a-copy.
    """
    from openavc.drivers.driver_loader import list_driver_definitions as _list
    from openavc.system_config import DRIVER_DEFINITIONS_DIR

    dirs = _get_driver_dirs()
    definitions = _list(dirs)
    builtin_root = str(Path(DRIVER_DEFINITIONS_DIR).resolve())
    for d in definitions:
        source_file = d.pop("_source_file", "")
        try:
            resolved = str(Path(source_file).resolve()) if source_file else ""
            d["source"] = "builtin" if resolved.startswith(builtin_root) else "user"
        except OSError:
            d["source"] = "user"
    return {"definitions": definitions}


@router.get("/driver-definitions/{driver_id}")
async def get_driver_definition(driver_id: str) -> dict:
    """Get a single JSON driver definition by ID."""
    from openavc.drivers.driver_loader import list_driver_definitions as _list

    dirs = _get_driver_dirs()
    for d in _list(dirs):
        if d.get("id") == driver_id:
            d.pop("_source_file", None)
            return d
    raise HTTPException(status_code=404, detail=f"Driver definition '{driver_id}' not found")


@router.post("/driver-definitions/validate")
async def validate_driver_definition_draft(
    body: Any = Body(...),
) -> dict[str, Any]:
    """Validate a driver definition without saving it.

    Answers the question the Driver Builder asks on every keystroke: what is
    wrong with this draft? The rules are the platform's own — the same
    function the save routes call, at the same ``strict`` setting — so what
    the editor shows and what a save accepts cannot disagree.

    Each issue carries a ``path`` (``commands.mute``, ``state_variables.volume``,
    ``config_schema.password``, ``responses[2]``, or ``""`` for a whole-driver
    rule) so the editor can put it on the right tab beside the right control.

    Deliberately takes the raw body rather than ``DriverDefinitionRequest``:
    that model would reject a mistyped field with a 422 before any rule ran,
    and a mistyped field is exactly what an author needs a readable message
    about. A draft that isn't a mapping comes back as one issue, not an error.
    """
    from openavc.drivers.driver_loader import validate_driver_issues

    if not isinstance(body, dict):
        return {
            "issues": [
                {
                    "severity": "error",
                    "message": "Driver definition must be a mapping",
                    "path": "",
                }
            ]
        }

    # Same carve-out the save routes make: the listing endpoint decorates a
    # definition with where it came from, and an editor that loaded one hands
    # those keys straight back.
    draft = _strip_listing_decorations(body)

    # strict: this is an authoring surface, so an undeclared key is a typo to
    # show now rather than a mystery when the save refuses it.
    return {"issues": validate_driver_issues(draft, strict=True)}


@router.post("/driver-definitions")
async def create_driver_definition(body: DriverDefinitionRequest) -> dict:
    """Create a new JSON driver definition."""
    from openavc.drivers.driver_loader import (
        list_driver_definitions as _list,
        save_driver_definition,
        validate_driver_definition,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    dirs = _get_driver_dirs()
    # Echo only what the client sent: exclude_unset keeps model defaults
    # (manufacturer, delimiter, empty containers) out of the saved YAML;
    # explicit nulls are dropped, not written into the file.
    driver_def = _strip_listing_decorations(
        body.model_dump(exclude_unset=True, exclude_none=True)
    )

    # Check for duplicate ID
    existing = _list(dirs)
    if any(d.get("id") == driver_def["id"] for d in existing):
        raise HTTPException(
            status_code=409,
            detail=f"Driver definition '{driver_def['id']}' already exists",
        )

    # Validate. strict: this is an authoring gate, so an undeclared key is a
    # typo to reject now, not a mystery to debug later.
    errors = validate_driver_definition(driver_def, strict=True)
    if errors:
        raise _definition_invalid(errors)

    # Save to driver_repo (user/community directory)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(driver_def, save_dir)

    # Register immediately
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)

    # Reconnect any devices orphaned while waiting for this driver id, so a
    # device that referenced a not-yet-created driver comes online as soon as
    # it's authored (mirrors the Python hot-reload path; no full reload needed).
    reconnected = await _get_engine().devices.reload_driver(driver_def["id"])

    return {"status": "created", "driver_id": driver_def["id"], "devices_reconnected": reconnected}


@router.put("/driver-definitions/{driver_id}")
async def update_driver_definition(driver_id: str, body: DriverDefinitionRequest) -> dict:
    """Update an existing JSON driver definition."""
    from openavc.drivers.driver_loader import (
        delete_driver_definition,
        is_builtin_driver,
        list_driver_definitions as _list,
        restore_driver_registration,
        save_driver_definition,
        validate_driver_definition,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    dirs = _get_driver_dirs()
    # Echo only what the client sent (see create_driver_definition).
    driver_def = _strip_listing_decorations(
        body.model_dump(exclude_unset=True, exclude_none=True)
    )

    # Must already exist
    existing = _list(dirs)
    if not any(d.get("id") == driver_id for d in existing):
        raise HTTPException(
            status_code=404,
            detail=f"Driver definition '{driver_id}' not found",
        )

    # Built-in drivers are read-only: editing in place would unlink the
    # shipped file from the install tree (no recovery). The Driver Builder
    # forks built-ins to an editable copy with a new id instead.
    if is_builtin_driver(driver_id, dirs):
        raise HTTPException(
            status_code=403,
            detail="Built-in drivers are read-only. Customize a copy to edit.",
        )
    if driver_def.get("id") != driver_id and is_builtin_driver(driver_def.get("id", ""), dirs):
        raise HTTPException(
            status_code=403,
            detail=f"Driver id '{driver_def.get('id')}' belongs to a read-only built-in driver.",
        )

    # Validate. strict: this is an authoring gate, so an undeclared key is a
    # typo to reject now, not a mystery to debug later.
    errors = validate_driver_definition(driver_def, strict=True)
    if errors:
        raise _definition_invalid(errors)

    # Delete old and save new
    delete_driver_definition(driver_id, dirs)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(driver_def, save_dir)

    # Re-register, then reconnect live devices so they pick up the new class
    # without a full project reload (mirrors the Python hot-reload path).
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)

    engine = _get_engine()
    reconnected = await engine.devices.reload_driver(driver_def["id"])
    # A renamed driver id leaves devices on the old id orphaned — retry those too.
    if driver_def.get("id") != driver_id:
        # The rename removed the user file for the old id. If that file was
        # overriding a shipped built-in, re-register the built-in so the old
        # id keeps working; otherwise drop the stale registration.
        restore_driver_registration(driver_id, dirs)
        reconnected = reconnected + await engine.devices.reload_driver(driver_id)

    return {"status": "updated", "driver_id": driver_def["id"], "devices_reconnected": reconnected}


def _merge_patch(current: Any, patch: Any) -> Any:
    """JSON Merge Patch (RFC 7386): objects merge recursively, a null value
    deletes the key, and anything else (arrays included) replaces wholesale.

    A shallow top-level merge here silently destroyed sibling entries: a
    PATCH updating one command replaced the entire ``commands`` block and
    persisted the truncated driver, breaking every device bound to the
    dropped commands.
    """
    if not isinstance(patch, dict):
        return patch
    merged = dict(current) if isinstance(current, dict) else {}
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = _merge_patch(merged.get(key), value)
    return merged


@router.patch("/driver-definitions/{driver_id}")
async def patch_driver_definition(driver_id: str, body: dict) -> dict:
    """Partially update a driver definition (JSON Merge Patch semantics)."""
    from openavc.drivers.driver_loader import (
        delete_driver_definition,
        is_builtin_driver,
        list_driver_definitions as _list,
        save_driver_definition,
        validate_driver_definition,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    dirs = _get_driver_dirs()

    # Find existing definition
    existing = _list(dirs)
    current = next((d for d in existing if d.get("id") == driver_id), None)
    if current is None:
        raise HTTPException(
            status_code=404,
            detail=f"Driver definition '{driver_id}' not found",
        )

    # Built-in drivers are read-only (see update_driver_definition).
    if is_builtin_driver(driver_id, dirs):
        raise HTTPException(
            status_code=403,
            detail="Built-in drivers are read-only. Customize a copy to edit.",
        )

    merged = _merge_patch(current, body)
    # Don't allow changing ID via PATCH
    merged["id"] = driver_id
    merged = _strip_listing_decorations(merged)

    # Validate merged result (authoring gate — strict, as for create/replace)
    errors = validate_driver_definition(merged, strict=True)
    if errors:
        raise _definition_invalid(errors)

    # Delete old and save merged
    delete_driver_definition(driver_id, dirs)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(merged, save_dir)

    # Re-register, then reconnect live devices so they pick up the new class
    # without a full project reload (mirrors the Python hot-reload path).
    driver_class = create_configurable_driver_class(merged)
    register_driver(driver_class)
    reconnected = await _get_engine().devices.reload_driver(driver_id)

    return {"status": "updated", "driver_id": driver_id, "devices_reconnected": reconnected}


@router.delete("/driver-definitions/{driver_id}")
async def delete_driver_definition_endpoint(driver_id: str) -> dict:
    """Delete a JSON driver definition."""
    from openavc.drivers.driver_loader import (
        delete_driver_definition,
        is_builtin_driver,
        restore_driver_registration,
    )

    dirs = _get_driver_dirs()

    # Built-in drivers ship inside the install tree and can't be deleted —
    # unlinking one would remove it permanently with no recovery.
    if is_builtin_driver(driver_id, dirs):
        raise HTTPException(
            status_code=403,
            detail="Built-in drivers can't be deleted.",
        )

    deleted = delete_driver_definition(driver_id, dirs)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Driver definition '{driver_id}' not found",
        )
    # The deleted file may have been a user copy overriding a shipped
    # built-in: re-register the built-in so the id keeps working, or drop
    # the stale registration when nothing serves the id anymore.
    restored = restore_driver_registration(driver_id, dirs)
    if restored:
        # Reconnect devices on this driver so they pick up the restored class.
        await _get_engine().devices.reload_driver(driver_id)
    return {"status": "deleted", "driver_id": driver_id, "builtin_restored": restored}


@router.post("/driver-definitions/{driver_id}/reload", dependencies=[Depends(require_claimed_auth)])
async def reload_driver_definition(driver_id: str) -> dict:
    """Re-read a YAML (``.avcdriver``) driver from disk and reconnect its devices.

    The Driver Builder re-registers a driver when you save it, but a
    ``.avcdriver`` file hand-edited on disk (outside the Builder — e.g. while
    developing a driver in ``driver_repo/``) doesn't take effect until a full
    project reload or restart. This mirrors the Python driver hot-reload route
    for declarative YAML drivers.
    """
    import yaml as _yaml

    from openavc.drivers.driver_loader import (
        find_driver_file_by_id,
        load_driver_file,
        validate_driver_definition,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    filepath = find_driver_file_by_id(_get_driver_dirs(), driver_id)
    if filepath is None:
        raise HTTPException(
            status_code=404,
            detail=f"No .avcdriver file declaring '{driver_id}' found on disk",
        )

    driver_def = load_driver_file(filepath)
    if driver_def is None:
        # load_driver_file logs the cause; surface the concrete validation
        # errors when we can so a hand-edit mistake is correctable.
        try:
            raw = _yaml.safe_load(filepath.read_text(encoding="utf-8"))
            errors = validate_driver_definition(raw) if isinstance(raw, dict) else ["not a YAML mapping"]
        except (OSError, _yaml.YAMLError):
            errors = []
        raise _definition_invalid(
            errors,
            preamble=f"Driver file '{filepath.name}' is invalid or its "
                     f"discovery companion is missing (see server log)",
        )

    # Re-register, then reconnect live devices so they pick up the disk version
    # without a full project reload (mirrors the Python hot-reload path).
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)

    engine = _get_engine()
    reconnected = await engine.devices.reload_driver(driver_def["id"])
    # A renamed id on disk leaves devices on the requested id orphaned — retry.
    if driver_def["id"] != driver_id:
        reconnected = reconnected + await engine.devices.reload_driver(driver_id)

    return {
        "status": "reloaded",
        "driver_id": driver_def["id"],
        "file": filepath.name,
        "devices_reconnected": reconnected,
    }
