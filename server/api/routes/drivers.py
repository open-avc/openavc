"""Driver listing, installation, definitions, and Python driver REST API endpoints."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from server.api._engine import _get_engine, _rate_limit_test
from server.api.auth import require_claimed_auth
from server.api.errors import api_error as _api_error
from server.api.models import (
    CommunityDriverInstallRequest,
    DriverDefinitionRequest,
    PythonDriverCreateRequest,
    TestCommandRequest,
)
from server.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter()


def _parse_semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".")[:3] if x.isdigit())


def _enforce_min_platform_version(required: str) -> None:
    """Raise HTTPException 422 if the running OpenAVC is older than ``required``.

    Used by both the request-field path and the post-download YAML-parsed path
    so /api/drivers/install and /api/discovery/install-and-match converge on
    the same gate (A65).
    """
    from server.version import __version__

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


# --- Drivers ---


@router.get("/drivers")
async def list_drivers() -> list[dict[str, Any]]:
    """List all available driver types with their metadata."""
    from server.core.device_manager import get_driver_registry
    return get_driver_registry()


@router.get("/drivers/{driver_id}/help")
async def get_driver_help(driver_id: str) -> dict[str, Any]:
    """Get help text (overview + setup instructions) for an installed driver."""
    from server.core.device_manager import get_driver_registry

    for drv in get_driver_registry():
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
_GITHUB_HOSTS: frozenset[str] = frozenset({
    "raw.githubusercontent.com",
    "github.com",
    "api.github.com",
})


def _get_driver_repo_dir() -> Path:
    """Get the driver_repo/ directory path."""
    from server.system_config import DRIVER_REPO_DIR
    return DRIVER_REPO_DIR


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
            companion_filepath.write_text(resp.text, encoding="utf-8")
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


async def _try_download_python_companion(
    *,
    main_url: str,
    companion_filename: str,
    driver_repo: Path,
) -> Path | None:
    """Best-effort fetch of a Python driver's conventional sibling companion.

    Unlike ``_download_companion`` (YAML drivers, where the companion is
    declared and required), Python-driver companions (``*_discovery.py`` /
    ``*_sim.py``) are located by naming convention and are OPTIONAL: a missing
    one must not fail the install, because the main ``.py`` controls hardware
    and auto-identifies from its inline ``tcp_probe`` without them, and the
    simulator is a bonus. Returns the written path, or ``None`` when the
    sibling doesn't exist (404) or can't be fetched.
    """
    import re
    import httpx
    from urllib.parse import urljoin, urlparse

    # Convention-named, but validate anyway: only the two documented suffixes,
    # so a redirect can't land an arbitrary .py in driver_repo.
    if not re.match(r'^[a-zA-Z0-9_\-]+_(discovery|sim)\.py$', companion_filename):
        return None
    companion_url = urljoin(main_url, companion_filename)
    parsed = urlparse(companion_url)
    if not parsed.hostname or parsed.hostname not in _GITHUB_HOSTS:
        return None

    companion_filepath = driver_repo / companion_filename
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(companion_url)
            if resp.status_code == 404:
                return None  # optional companion simply isn't published
            resp.raise_for_status()
            companion_filepath.write_text(resp.text, encoding="utf-8")
    except (httpx.HTTPStatusError, httpx.RequestError, OSError) as e:
        log.warning(
            "Optional companion %s not installed: %s", companion_filename, e
        )
        return None
    return companion_filepath


def _remove_python_companions(main_path: Path) -> list[str]:
    """Delete a Python driver's conventional sibling companions.

    Given the main ``<stem>.py``, removes ``<stem>_discovery.py`` and
    ``<stem>_sim.py`` from the same directory when present. Install / import
    fetch the trio as one unit, so deleting the driver removes it as one unit
    too — otherwise orphaned companions linger in ``driver_repo/``. Safe by
    construction: only the two documented suffixes, only the driver's own
    siblings, only inside the driver's directory. Returns the names removed.
    """
    removed: list[str] = []
    repo = main_path.parent
    stem = main_path.stem
    for suffix in ("_discovery.py", "_sim.py"):
        companion = repo / f"{stem}{suffix}"
        if companion.is_file():
            companion.unlink(missing_ok=True)
            removed.append(companion.name)
    return removed


@router.get("/drivers/community")
async def get_community_drivers() -> dict[str, Any]:
    """Fetch the community driver index from GitHub (cached)."""
    from server.discovery.community_index import CommunityIndexCache

    if not hasattr(get_community_drivers, "_cache"):
        get_community_drivers._cache = CommunityIndexCache()

    drivers = await get_community_drivers._cache.get_drivers()
    return {"drivers": drivers, "error": None if drivers else "Failed to fetch community drivers"}


@router.post("/drivers/install")
async def install_community_driver(body: CommunityDriverInstallRequest) -> dict[str, Any]:
    """Download and install a driver from the community repo."""
    import httpx
    from server.core.device_manager import register_driver
    from server.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from server.drivers.configurable import create_configurable_driver_class

    # min_platform_version supplied by the caller. The YAML body itself is
    # checked again after download (see _enforce_min_platform_version) so
    # callers like /api/discovery/install-and-match — which don't carry the
    # field on the request — can't bypass the gate (A65, cousin to A32).
    if body.min_platform_version:
        _enforce_min_platform_version(body.min_platform_version)

    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    # Validate URL points to GitHub
    url = body.file_url
    from urllib.parse import urlparse
    parsed_url = urlparse(url)
    if not parsed_url.hostname or parsed_url.hostname not in _GITHUB_HOSTS:
        raise HTTPException(
            status_code=422,
            detail=f"Driver URL must be from GitHub ({', '.join(sorted(_GITHUB_HOSTS))})",
        )

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

    # Download the file
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            yaml_text = resp.text
            filepath.write_text(yaml_text, encoding="utf-8")
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
            register_driver(driver_class)
        else:
            driver_class = load_python_driver_file(filepath)
            if driver_class is None:
                filepath.unlink(missing_ok=True)
                raise HTTPException(status_code=422, detail="No valid driver class found in Python file")
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
            for suffix in ("_discovery.py", "_sim.py"):
                await _try_download_python_companion(
                    main_url=url,
                    companion_filename=f"{src_stem}{suffix}",
                    driver_repo=driver_repo,
                )

    # Refresh discovery engine with new driver hints
    from server.api.discovery import refresh_all_device_matches
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
    from server.core.device_manager import register_driver
    from server.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from server.drivers.configurable import create_configurable_driver_class

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
            from server.drivers.driver_loader import companion_relpath_from_def
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

    from server.core.device_manager import register_driver
    from server.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from server.drivers.configurable import create_configurable_driver_class

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

    def _is_companion(name: str) -> bool:
        return name.endswith("_discovery.py") or name.endswith("_sim.py")

    mains = [n for n in entries if not _is_companion(n)]
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
    from server.api.discovery import refresh_all_device_matches
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
    from server.drivers.driver_loader import _is_driver_file

    for filepath in sorted(driver_repo.glob("*.py")):
        if not _is_driver_file(filepath):
            continue
        driver_id = filepath.stem
        driver_name = filepath.stem.replace("_", " ").title()

        # Try to extract actual info from the loaded registry
        driver_version = ""
        from server.core.device_manager import _DRIVER_REGISTRY
        for reg_id, cls in _DRIVER_REGISTRY.items():
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
    from server.core.device_manager import unregister_driver

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
        _remove_python_companions(deleted_file)
    unregister_driver(driver_id)

    # Refresh discovery engine so stale matches are cleared
    from server.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    return {"status": "uninstalled", "driver_id": driver_id}


@router.post("/drivers/installed/{driver_id}/update")
async def update_driver(driver_id: str, request: Request) -> dict[str, Any]:
    """Update an installed community driver to a newer version."""
    import httpx
    from server.core.device_manager import register_driver, unregister_driver
    from server.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from server.drivers.configurable import create_configurable_driver_class

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
    # endpoint requires a GitHub host.
    from urllib.parse import urlparse as _urlparse
    parsed_new_url = _urlparse(file_url)
    if not parsed_new_url.hostname or parsed_new_url.hostname not in _GITHUB_HOSTS:
        raise HTTPException(
            status_code=422,
            detail=f"Driver URL must be from GitHub ({', '.join(sorted(_GITHUB_HOSTS))})",
        )

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
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"GitHub returned {e.response.status_code}")
    except httpx.RequestError as e:
        raise _api_error(502, f"Failed to download driver '{driver_id}'", e)

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
    new_filepath.write_text(new_content, encoding="utf-8")

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

    from server.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    return {"status": "updated", "driver_id": driver_id, "file": new_filename}


# --- Driver Definitions ---


def _get_driver_dirs() -> list[Path]:
    """Get directories containing driver definitions."""
    from server.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
    return [
        DRIVER_DEFINITIONS_DIR,
        DRIVER_REPO_DIR,
    ]


@router.get("/driver-definitions")
async def list_driver_definitions() -> list[dict]:
    """List all JSON driver definitions.

    Adds a `source` field to each entry: `"builtin"` for drivers that ship
    in the platform's read-only definitions directory, `"user"` for drivers
    that live in the user/community driver_repo (created via the Driver
    Builder or installed from the community catalog). The Driver Builder
    uses this to gate edit-in-place vs. customize-a-copy.
    """
    from server.drivers.driver_loader import list_driver_definitions as _list
    from server.system_config import DRIVER_DEFINITIONS_DIR

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
    return definitions


@router.get("/driver-definitions/{driver_id}")
async def get_driver_definition(driver_id: str) -> dict:
    """Get a single JSON driver definition by ID."""
    from server.drivers.driver_loader import list_driver_definitions as _list

    dirs = _get_driver_dirs()
    for d in _list(dirs):
        if d.get("id") == driver_id:
            d.pop("_source_file", None)
            return d
    raise HTTPException(status_code=404, detail=f"Driver definition '{driver_id}' not found")


@router.post("/driver-definitions")
async def create_driver_definition(body: DriverDefinitionRequest) -> dict:
    """Create a new JSON driver definition."""
    from server.drivers.driver_loader import (
        list_driver_definitions as _list,
        save_driver_definition,
        validate_driver_definition,
    )
    from server.drivers.configurable import create_configurable_driver_class
    from server.core.device_manager import register_driver

    dirs = _get_driver_dirs()
    driver_def = body.model_dump(exclude_none=True)

    # Check for duplicate ID
    existing = _list(dirs)
    if any(d.get("id") == driver_def["id"] for d in existing):
        raise HTTPException(
            status_code=409,
            detail=f"Driver definition '{driver_def['id']}' already exists",
        )

    # Validate
    errors = validate_driver_definition(driver_def)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors, "message": f"{len(errors)} validation error(s) in driver definition"},
        )

    # Save to driver_repo (user/community directory)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(driver_def, save_dir)

    # Register immediately
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)

    return {"status": "created", "id": driver_def["id"]}


@router.put("/driver-definitions/{driver_id}")
async def update_driver_definition(driver_id: str, body: DriverDefinitionRequest) -> dict:
    """Update an existing JSON driver definition."""
    from server.drivers.driver_loader import (
        delete_driver_definition,
        is_builtin_driver,
        list_driver_definitions as _list,
        save_driver_definition,
        validate_driver_definition,
    )
    from server.drivers.configurable import create_configurable_driver_class
    from server.core.device_manager import register_driver

    dirs = _get_driver_dirs()
    driver_def = body.model_dump(exclude_none=True)

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

    # Validate
    errors = validate_driver_definition(driver_def)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors, "message": f"{len(errors)} validation error(s) in driver definition"},
        )

    # Delete old and save new
    delete_driver_definition(driver_id, dirs)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(driver_def, save_dir)

    # Re-register
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)

    return {"status": "updated", "id": driver_def["id"]}


@router.patch("/driver-definitions/{driver_id}")
async def patch_driver_definition(driver_id: str, body: dict) -> dict:
    """Partially update a driver definition (merge provided fields)."""
    from server.drivers.driver_loader import (
        delete_driver_definition,
        is_builtin_driver,
        list_driver_definitions as _list,
        save_driver_definition,
        validate_driver_definition,
    )
    from server.drivers.configurable import create_configurable_driver_class
    from server.core.device_manager import register_driver

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

    # Merge: shallow merge top-level keys from body into current
    merged = {**current, **body}
    # Don't allow changing ID via PATCH
    merged["id"] = driver_id

    # Validate merged result
    errors = validate_driver_definition(merged)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors, "message": f"{len(errors)} validation error(s) in driver definition"},
        )

    # Delete old and save merged
    delete_driver_definition(driver_id, dirs)
    save_dir = dirs[1]  # driver_repo/
    save_driver_definition(merged, save_dir)

    # Re-register
    driver_class = create_configurable_driver_class(merged)
    register_driver(driver_class)

    return {"status": "updated", "id": driver_id}


@router.delete("/driver-definitions/{driver_id}")
async def delete_driver_definition_endpoint(driver_id: str) -> dict:
    """Delete a JSON driver definition."""
    from server.drivers.driver_loader import delete_driver_definition, is_builtin_driver

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
    # Also unregister from runtime driver registry
    from server.core.device_manager import unregister_driver
    unregister_driver(driver_id)
    return {"status": "deleted", "id": driver_id}


@router.post("/driver-definitions/{driver_id}/test-command")
async def test_driver_command(driver_id: str, body: TestCommandRequest) -> dict:
    """Test a driver command against live hardware.

    If the request includes a `definition` and `command_name`, the endpoint
    instantiates the actual `ConfigurableDriver` runtime — running auth and
    on_connect just like production — and sends the named command. This is
    the production code path: anything that works in the test panel will
    work when the driver is wired into a real device.

    Falls back to a raw send-and-wait when only `command_string` is given,
    for one-off "what does this device say if I send X" probes.
    """
    _rate_limit_test(f"test_command:{driver_id}")

    if body.definition and body.command_name:
        return await _test_via_configurable_driver(body)

    return await _test_raw(body)


@router.get("/driver-test-conflicts")
async def check_connection_conflict(
    host: str,
    port: str,
    transport: str = "tcp",
) -> dict:
    """Find production devices using the same host:port as a planned test (A81).

    Many AV devices allow only one TCP control session at a time. The test
    panel calls this before opening a competing connection so the UI can
    warn the user and offer to pause the production driver.

    Returns ``{"conflicts": [...]}`` with one entry per matching device. An
    empty list means it's safe to test. Matching is currently TCP-only —
    the single-session problem is a TCP-specific failure mode.
    """
    if transport != "tcp":
        return {"conflicts": []}
    engine = _get_engine()
    if not engine.project:
        return {"conflicts": []}

    try:
        target_port = int(port)
    except (TypeError, ValueError):
        return {"conflicts": []}

    from server.core.device_manager import get_driver_registry

    registry = {d["id"]: d for d in get_driver_registry()}

    conflicts: list[dict[str, Any]] = []
    for device in engine.project.devices:
        if not device.enabled:
            continue
        driver_info = registry.get(device.driver)
        if not driver_info or driver_info.get("transport", "tcp") != "tcp":
            continue
        conn = engine.project.connections.get(device.id, {})
        cfg = {**device.config, **conn}
        device_host = str(cfg.get("host", ""))
        try:
            device_port = int(cfg.get("port", 0))
        except (TypeError, ValueError):
            continue
        if device_host != host or device_port != target_port:
            continue
        connected = bool(engine.state.get(f"device.{device.id}.connected"))
        paused = bool(engine.state.get(f"device.{device.id}.paused"))
        conflicts.append({
            "device_id": device.id,
            "device_name": device.name,
            "driver": device.driver,
            "connected": connected,
            "paused": paused,
        })
    return {"conflicts": conflicts}


async def _test_via_configurable_driver(body: TestCommandRequest) -> dict:
    """Run a command through the live ConfigurableDriver code path.

    Builds an isolated StateStore + EventBus, instantiates a one-shot driver
    from the supplied definition with `poll_interval` forced to 0, hooks
    on_data_received to capture incoming bytes, and reports the response,
    state changes, and any errors back to the caller.
    """
    import asyncio
    from server.core.state_store import StateStore
    from server.core.event_bus import EventBus
    from server.drivers.configurable import create_configurable_driver_class

    # Build the per-test config: definition's defaults, then user overrides
    # (host, port, credentials), then poll forced off so we don't start a
    # background poller for a one-shot test.
    definition = dict(body.definition or {})
    default_config = dict(definition.get("default_config") or {})
    # Serial drivers use `port` as a string path (e.g. "COM3"); IP transports
    # need an int. Coerce numeric strings for IP, leave serial strings alone.
    transport_type = definition.get("transport", body.transport)
    port_value: int | str = body.port
    if transport_type != "serial":
        try:
            port_value = int(body.port)
        except (TypeError, ValueError):
            return {
                "success": False,
                "sent": None,
                "received": [],
                "state_changes": {},
                "error": f"Invalid port for {transport_type}: {body.port!r}",
            }
    config = {
        **default_config,
        **(body.config_overrides or {}),
        "host": body.host,
        "port": port_value,
        "poll_interval": 0,
    }
    # Patch the definition so the runtime sees poll-off.
    definition["default_config"] = {**default_config, "poll_interval": 0}

    state = StateStore()
    events = EventBus()

    try:
        driver_cls = create_configurable_driver_class(definition)
    except Exception as e:
        return {
            "success": False,
            "sent": None,
            "received": [],
            "state_changes": {},
            "error": f"Driver definition is invalid: {e}",
        }

    # Capture state changes so the panel can show what the command moved.
    initial_state: dict[str, Any] = {}

    driver = driver_cls(
        device_id=f"test_{definition.get('id', 'driver')}",
        config=config,
        state=state,
        events=events,
    )
    initial_state = dict(state.snapshot()) if hasattr(state, "snapshot") else {}

    # Hook on_data_received to capture inbound bytes for display while still
    # running the production response-matching logic so state changes happen.
    received_chunks: list[bytes] = []
    response_event = asyncio.Event()
    original_on_data = driver.on_data_received

    async def capture_on_data(data: bytes) -> None:
        received_chunks.append(data)
        response_event.set()
        await original_on_data(data)

    driver.on_data_received = capture_on_data  # type: ignore[method-assign]

    sent_repr: str | None = None
    error_text: str | None = None

    try:
        try:
            await driver.connect()
        except Exception as e:
            return {
                "success": False,
                "sent": None,
                "received": [_decode_for_display(b) for b in received_chunks],
                "state_changes": {},
                "error": f"Connect failed: {e}",
            }

        cmd_def = (definition.get("commands") or {}).get(body.command_name)
        if cmd_def is None:
            return {
                "success": False,
                "sent": None,
                "received": [_decode_for_display(b) for b in received_chunks],
                "state_changes": {},
                "error": f"Unknown command '{body.command_name}'",
            }

        sent_repr = _describe_outgoing(definition, cmd_def, config, body.params or {})

        # send_command is fire-and-forget for TCP/OSC. For HTTP it returns
        # the response synchronously and on_data_received gets called with
        # the body, so capture_on_data fires the event in either case.
        try:
            send_result = await driver.send_command(
                body.command_name, body.params or {}
            )
        except Exception as e:
            error_text = f"Send failed: {e}"
            send_result = None

        # If nothing has come back yet, give the device a moment to reply.
        if not received_chunks and error_text is None:
            try:
                await asyncio.wait_for(response_event.wait(), timeout=body.timeout)
            except asyncio.TimeoutError:
                # No response is OK for fire-and-forget commands — surface
                # it as info, not an error, so users can tell the difference.
                if send_result is False or send_result is None:
                    error_text = "No response within timeout"

        # Settle period — many devices send a response in multiple frames
        # (NEC NaviSet per-parameter lines, Christie LX ACK then DATA). Without
        # this short wait the disconnect tears down the transport before
        # trailing frames arrive. HTTP already returns the full response
        # synchronously so we skip the settle there.
        if (
            received_chunks
            and error_text is None
            and transport_type in ("tcp", "udp", "osc", "serial")
        ):
            await asyncio.sleep(min(0.3, body.timeout / 4))

    finally:
        try:
            await driver.disconnect()
        except Exception:
            pass

    final_state = dict(state.snapshot()) if hasattr(state, "snapshot") else {}
    state_changes = {
        k: v
        for k, v in final_state.items()
        if initial_state.get(k) != v and not k.endswith(".connected")
    }

    return {
        "success": error_text is None,
        "sent": sent_repr,
        "received": [_decode_for_display(b) for b in received_chunks],
        "state_changes": state_changes,
        "error": error_text,
    }


def _decode_for_display(data: bytes) -> str:
    """Best-effort decoding for the test panel UI.

    Tries UTF-8 first; falls back to a hex preview for binary protocols.
    """
    try:
        text = data.decode("utf-8")
        # If it round-trips and renders, show as text.
        if text.isprintable() or any(c in text for c in "\r\n\t"):
            return text
    except UnicodeDecodeError:
        pass
    return data.hex()


def _describe_outgoing(
    definition: dict[str, Any],
    cmd_def: dict[str, Any],
    config: dict[str, Any],
    params: dict[str, Any],
) -> str:
    """Build a human-readable summary of what was sent on the wire.

    Used by the test panel so authors can see which placeholders resolved to
    which values — the same string the runtime substitutes, not the raw
    template.
    """
    from server.drivers.configurable import ConfigurableDriver

    transport = definition.get("transport", "tcp")
    all_params = {**config, **params}

    if "address" in cmd_def:  # OSC
        addr = ConfigurableDriver._safe_substitute(
            cmd_def.get("address", ""), all_params
        )
        args = cmd_def.get("args") or []
        arg_summary = ", ".join(
            f"{a.get('type', 's')}={a.get('value', '')}" for a in args
        )
        return f"OSC {addr}" + (f" [{arg_summary}]" if arg_summary else "")

    if transport == "http" or "method" in cmd_def or "path" in cmd_def:
        method = cmd_def.get("method", "GET").upper()
        path = ConfigurableDriver._safe_substitute(
            cmd_def.get("path", "/"), all_params
        )
        return f"{method} {path}"

    raw = cmd_def.get("send", "") or cmd_def.get("string", "")
    return ConfigurableDriver._safe_substitute(raw, all_params) if raw else ""


async def _test_raw(body: TestCommandRequest) -> dict:
    """Legacy raw-bytes test path — open transport, send command_string, wait.

    No auth, no on_connect. Used when a user types a one-off probe in the
    raw command field without selecting a defined command.
    """
    import asyncio

    if not body.command_string:
        raise HTTPException(
            status_code=422,
            detail="Provide either a definition + command_name or a command_string",
        )

    if body.transport == "http":
        return await _test_http_raw(body)

    if body.transport == "osc":
        return await _test_osc_raw(body)

    if body.transport == "serial":
        return await _test_serial_raw(body)

    if body.transport == "udp":
        return await _test_udp_raw(body)

    if body.transport not in ("tcp",):
        raise HTTPException(
            status_code=422,
            detail="Only TCP, UDP, serial, HTTP, and OSC test connections are supported",
        )

    from server.transport.tcp import TCPTransport

    delimiter = body.delimiter.encode().decode("unicode_escape").encode()
    response_text = None
    error_text = None

    # Coerce numeric-string port to int for IP transports.
    try:
        port = int(body.port)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid TCP port: {body.port!r}",
        ) from None

    try:
        transport = await TCPTransport.create(
            host=body.host,
            port=port,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            delimiter=delimiter,
            timeout=body.timeout,
        )
    except ConnectionError as e:
        return {
            "success": False,
            "sent": body.command_string,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }

    try:
        cmd_data = body.command_string.encode().decode("unicode_escape").encode()
        response = await transport.send_and_wait(cmd_data, timeout=body.timeout)
        response_text = _decode_for_display(response)
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for response"
    except (OSError, ValueError, UnicodeError) as e:
        error_text = str(e)
    finally:
        await transport.close()

    return {
        "success": error_text is None,
        "sent": body.command_string,
        "received": [response_text] if response_text is not None else [],
        "state_changes": {},
        "error": error_text,
    }


async def _test_serial_raw(body: TestCommandRequest) -> dict:
    """Raw serial probe — open SerialTransport with the port path in body.port."""
    import asyncio
    from server.transport.serial_transport import SerialTransport

    port_path = str(body.port)
    if not port_path:
        raise HTTPException(
            status_code=422,
            detail="Serial port path is required (e.g. COM3 or /dev/ttyUSB0).",
        )

    delimiter = body.delimiter.encode().decode("unicode_escape").encode()
    response_text = None
    error_text = None

    try:
        transport = await SerialTransport.create(
            port=port_path,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            delimiter=delimiter,
            timeout=body.timeout,
        )
    except (OSError, ConnectionError) as e:
        return {
            "success": False,
            "sent": body.command_string,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }

    try:
        cmd_data = body.command_string.encode().decode("unicode_escape").encode()
        response = await transport.send_and_wait(cmd_data, timeout=body.timeout)
        response_text = _decode_for_display(response)
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for response"
    except (OSError, ValueError, UnicodeError) as e:
        error_text = str(e)
    finally:
        await transport.close()

    return {
        "success": error_text is None,
        "sent": body.command_string,
        "received": [response_text] if response_text is not None else [],
        "state_changes": {},
        "error": error_text,
    }


async def _test_udp_raw(body: TestCommandRequest) -> dict:
    """Raw UDP probe — open UDPTransport, send command_string bytes, await reply.

    The configurable driver runtime already supports UDP in definition mode; this
    surfaces the same shape to the raw probe path so authors can fire one-off
    bytes without first declaring a command.
    """
    import asyncio
    from server.transport.udp import UDPTransport

    try:
        port = int(body.port)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid UDP port: {body.port!r}",
        ) from None

    response_text = None
    error_text = None
    udp = UDPTransport(host=body.host, port=port, name="udp_test")

    try:
        await udp.open()
    except OSError as e:
        return {
            "success": False,
            "sent": body.command_string,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }

    try:
        cmd_data = body.command_string.encode().decode("unicode_escape").encode()
        response = await udp.send_and_wait(cmd_data, timeout=body.timeout)
        response_text = _decode_for_display(response)
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for response"
    except (OSError, ValueError, UnicodeError) as e:
        error_text = str(e)
    finally:
        await udp.close()

    return {
        "success": error_text is None,
        "sent": body.command_string,
        "received": [response_text] if response_text is not None else [],
        "state_changes": {},
        "error": error_text,
    }


async def _test_http_raw(body: TestCommandRequest) -> dict:
    """Raw HTTP probe — parses 'METHOD /path' out of command_string."""
    import httpx

    cmd = body.command_string.strip()
    method = "GET"
    path = cmd
    if " " in cmd:
        parts = cmd.split(None, 1)
        if parts[0].upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            method = parts[0].upper()
            path = parts[1]

    scheme = "https" if body.port == 443 else "http"
    url = f"{scheme}://{body.host}:{body.port}{path}"
    sent = f"{method} {url}"

    try:
        async with httpx.AsyncClient(
            timeout=body.timeout, verify=False
        ) as client:
            resp = await client.request(method, url)
            return {
                "success": True,
                "sent": sent,
                "received": [f"HTTP {resp.status_code}\n{resp.text[:2000]}"],
                "state_changes": {},
                "error": None,
            }
    except httpx.TimeoutException:
        return {
            "success": False,
            "sent": sent,
            "received": [],
            "state_changes": {},
            "error": "HTTP request timed out",
        }
    except Exception as e:
        return {
            "success": False,
            "sent": sent,
            "received": [],
            "state_changes": {},
            "error": str(e),
        }


async def _test_osc_raw(body: TestCommandRequest) -> dict:
    """Raw OSC probe — sends the address with no args."""
    import asyncio
    from server.transport.osc_codec import osc_encode_message, osc_decode_message
    from server.transport.udp import UDPTransport

    address = body.command_string.strip()
    if not address.startswith("/"):
        address = "/" + address

    response_text = None
    error_text = None
    sent = f"OSC {address}"

    udp = UDPTransport(host=body.host, port=body.port, name="osc_test")
    try:
        await udp.open()
        msg = osc_encode_message(address)
        response = await udp.send_and_wait(msg, timeout=body.timeout)
        try:
            resp_addr, resp_args = osc_decode_message(response)
            arg_strs = [str(v) for _, v in resp_args]
            response_text = f"{resp_addr} [{', '.join(arg_strs)}]"
        except (ValueError, Exception):
            response_text = response.hex()
    except asyncio.TimeoutError:
        error_text = "Timeout waiting for OSC response"
    except (OSError, ValueError) as e:
        error_text = str(e)
    finally:
        await udp.close()

    return {
        "success": error_text is None,
        "sent": sent,
        "received": [response_text] if response_text is not None else [],
        "state_changes": {},
        "error": error_text,
    }


# --- Python Drivers ---


def _safe_driver_path(driver_id: str) -> Path:
    """Resolve a driver ID to a safe file path in driver_repo/."""
    from server.system_config import DRIVER_REPO_DIR

    # Sanitize: only allow alphanumeric + underscore + hyphen
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', driver_id):
        raise HTTPException(status_code=400, detail="Invalid driver ID: only alphanumeric, underscore, and hyphen allowed")

    filepath = DRIVER_REPO_DIR / f"{driver_id}.py"

    # Ensure path stays within driver_repo/
    try:
        filepath.resolve().relative_to(DRIVER_REPO_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid driver ID")

    return filepath


@router.get("/python-drivers")
async def list_python_drivers() -> dict:
    """List all Python driver files in driver_repo/."""
    from server.drivers.driver_loader import list_python_drivers as _list
    from server.system_config import DRIVER_REPO_DIR

    drivers = _list([DRIVER_REPO_DIR])

    # Add devices_using info from device manager
    engine = _get_engine()
    for driver in drivers:
        driver["devices_using"] = engine.devices.get_devices_using_driver(driver["id"])

    return {"drivers": drivers}


@router.get("/python-drivers/{driver_id}/source")
async def get_python_driver_source(driver_id: str) -> dict:
    """Read the source code of a Python driver file."""
    filepath = _safe_driver_path(driver_id)

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    try:
        source = filepath.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read driver file: {e}")

    return {"id": driver_id, "filename": filepath.name, "source": source}


@router.get("/python-drivers/{driver_id}/bundle")
async def export_python_driver_bundle(driver_id: str):
    """Export a Python driver and its companions as a .zip bundle.

    Bundles the main ``{id}.py`` plus any sibling ``{id}_discovery.py`` and
    ``{id}_sim.py`` present in ``driver_repo/``, so the whole driver can be
    handed to someone as a single file and re-imported via /drivers/upload-bundle.
    """
    import io
    import zipfile
    from fastapi import Response

    main_path = _safe_driver_path(driver_id)
    if not main_path.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    driver_repo = _get_driver_repo_dir()
    files = [main_path]
    for suffix in ("_discovery.py", "_sim.py"):
        companion = driver_repo / f"{driver_id}{suffix}"
        if companion.is_file():
            files.append(companion)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in files:
            zf.write(fp, arcname=fp.name)

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{driver_id}.zip"'},
    )


@router.put("/python-drivers/{driver_id}/source", dependencies=[Depends(require_claimed_auth)])
async def save_python_driver_source(driver_id: str, body: dict) -> dict:
    """Save the source code of a Python driver file."""
    filepath = _safe_driver_path(driver_id)
    source = body.get("source")
    if source is None:
        raise HTTPException(status_code=422, detail="Missing 'source' field")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    try:
        filepath.write_text(source, encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save driver file: {e}")

    return {"status": "saved", "id": driver_id}


@router.post("/python-drivers", dependencies=[Depends(require_claimed_auth)])
async def create_python_driver(body: PythonDriverCreateRequest) -> dict:
    """Create a new Python driver file."""
    from server.system_config import DRIVER_REPO_DIR

    filepath = _safe_driver_path(body.id)

    # Ensure driver_repo/ exists
    DRIVER_REPO_DIR.mkdir(parents=True, exist_ok=True)

    # Atomic creation: 'x' mode fails if the file already exists
    try:
        with open(filepath, "x", encoding="utf-8") as f:
            f.write(body.source)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Python driver '{body.id}' already exists")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to create driver file: {e}")

    # Try to load and register immediately
    from server.drivers.driver_loader import load_python_driver_file
    from server.core.device_manager import register_driver

    driver_class = load_python_driver_file(filepath)
    if driver_class:
        register_driver(driver_class)

    return {"status": "created", "id": body.id}


@router.delete("/python-drivers/{driver_id}", dependencies=[Depends(require_claimed_auth)])
async def delete_python_driver(driver_id: str) -> dict:
    """Delete a Python driver file."""
    filepath = _safe_driver_path(driver_id)

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    # Check if devices are using this driver
    engine = _get_engine()
    using = engine.devices.get_devices_using_driver(driver_id)
    if using:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete driver '{driver_id}': used by devices: {', '.join(using)}",
        )

    # Remove file + its sibling companions (discovery / sim), so deleting a
    # driver that was imported or installed as a bundle doesn't leave orphans.
    filepath.unlink()
    removed_companions = _remove_python_companions(filepath)

    # Unregister from driver registry
    from server.core.device_manager import unregister_driver
    unregister_driver(driver_id)

    # Clean up sys.modules
    import sys
    module_name = f"openavc_driver_{driver_id}"
    sys.modules.pop(module_name, None)

    log.info(f"Deleted Python driver: {driver_id}")
    return {"status": "deleted", "id": driver_id, "removed_companions": removed_companions}


@router.post("/python-drivers/{driver_id}/reload", dependencies=[Depends(require_claimed_auth)])
async def reload_python_driver_endpoint(driver_id: str) -> dict:
    """Hot-reload a Python driver and reconnect affected devices."""
    filepath = _safe_driver_path(driver_id)

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    from server.drivers.driver_loader import reload_python_driver

    result = reload_python_driver(filepath)

    if result["status"] == "error":
        return result

    # Reconnect devices using this driver
    engine = _get_engine()
    new_driver_id = result["driver_id"]
    old_driver_id = result.get("old_driver_id")

    reconnected: list[str] = []

    # Reconnect devices using the new driver ID
    reconnected.extend(await engine.devices.reload_driver(new_driver_id))

    # If the driver ID changed, also reconnect devices using the old ID
    if old_driver_id and old_driver_id != new_driver_id:
        reconnected.extend(await engine.devices.reload_driver(old_driver_id))

    result["devices_reconnected"] = reconnected
    return result
