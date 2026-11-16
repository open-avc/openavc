"""Getting drivers onto this server and off it: the registered driver list,
Browse Community, install, upload, update, Drivers > Installed and uninstall.

Every door here that brings a driver in (install, update, upload, bundle)
hands the whole change to `drivers/driver_change.admit`, which checks it (the
protected identity, the `min_platform_version` floor the file declares, for
both kinds of driver), stages it, admits the staged copy, swaps it in and
registers it, or refuses with the installed driver left as it was. What a door
keeps is its own: the files it brings (a catalog driver's come through
`driver_catalog.CatalogFiles`, which checks every download against the
catalog's hashes; a hand-imported file has no catalog entry to hash against),
its gates (the unknown-key gate for an import, the id it asked for at an
install), its sentences, and what follows the change (devices, Discovery, the
announcement). `tests/test_driver_install_doors_pins.py` holds each door as it
is. The Driver Builder's save doors ask the same floor (`enforce_declared_floor`).

Four neighbours are *not* here: `api/driver_catalog.py` (the community
catalog: its addresses, hashes and downloads), `routes/driver_definitions.py`
(the Driver Builder's definition CRUD), `routes/driver_test.py` (the live-test
and dry-run harness) and `routes/python_drivers.py` (``.py`` source
management). This module calls into the last for `remove_python_companions`,
since a Python driver's file set is that module's rule to own.
`announce_drivers_changed` is here because every door that writes a driver,
in any of them, ends with it.
"""

import re
from pathlib import Path, PurePosixPath
from typing import Any

from openavc.api.host_policy import ProgrammerRoute
from fastapi import APIRouter, HTTPException, Request

from openavc.api._engine import _get_engine
from openavc.api.errors import StructuredApiError
from openavc.api.errors import api_error as _api_error
from openavc.api.models import CommunityDriverInstallRequest
from openavc.api.driver_catalog import (
    CatalogDownload,
    CatalogFiles,
    catalog_check,
    index_cache,
    refuse_another_id,
    require_catalog_url,
)
from openavc.api.routes.python_drivers import remove_python_companions
from openavc.core.installed_equipment import InstalledEquipmentError
from openavc.drivers.driver_change import (
    Admitted,
    DriverChange,
    DriverRefused,
    Gates,
    admit,
    check_driver_file,
    floor_refusal,
)
from openavc.drivers.driver_loader import (
    COMPANION_SUFFIXES,
    DISCOVERY_COMPANION_SUFFIX,
    DRIVER_FILE_STEM,
    companion_relpath_from_yaml,
    declared_driver_info,
    driver_companions,
    driver_files_for,
    require_mutable_driver_file,
)
from openavc.drivers.refused_files import load_error, load_notices
from openavc.utils.logger import get_logger
from openavc.drivers.registry import (
    list_registered_drivers,
    registered_driver_classes,
    require_mutable_driver,
    unregister_driver,
)

log = get_logger(__name__)


async def announce_drivers_changed(driver_id: str | None = None) -> None:
    """Tell every open Programmer that the drivers on this server changed.

    The IDE keeps its driver lists (Drivers > Installed, the Builder's
    definitions) in a store that only reloads when that store does the
    installing. Many installs happen elsewhere: Discovery's Install & Add, a
    device page's Install, the AI's install tool, an upload, a Builder save,
    a Python driver edit, a project that brings its own drivers, another
    browser. So the server says it once, at every door that writes a driver,
    and every Programmer refreshes. ``driver_id`` is left out when a door
    wrote several (or cannot say which).
    """
    from openavc.api._engine import get_engine_optional

    engine = get_engine_optional()
    if engine is None:
        return
    try:
        message: dict[str, Any] = {"type": "drivers.changed"}
        if driver_id:
            message["driver_id"] = driver_id
        await engine.ws.broadcast(message, client_type="programmer")
    except Exception:
        log.exception("Could not announce the driver change for '%s'", driver_id)

router = APIRouter(route_class=ProgrammerRoute)


def _enforce_min_platform_version(required: str) -> None:
    """Raise HTTPException 422 if the running OpenAVC is older than
    ``required``: the floor a request names, and the Builder's
    (``enforce_declared_floor``). The rule is ``driver_change.floor_refusal``."""
    refusal = floor_refusal(required)
    if refusal:
        raise HTTPException(status_code=422, detail=refusal)


def _check_driver_file(filename: str, content: bytes) -> None:
    """``driver_change.check_driver_file`` as this API answers it, asked of a
    driver file before anything else is fetched or written."""
    try:
        check_driver_file(filename, content)
    except DriverRefused as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


def _admitted(change: DriverChange, gates: Gates, failed: str) -> Admitted:
    """``driver_change.admit`` as this API answers it: its own refusal as its
    status and sentence, with the loader's reasons beneath it when the loader
    refused the file (``definition_invalid``: one per line in ``detail``, and
    as ``errors``); a protected identity and a gate's refusal as they were
    raised; and anything else the door's 500 sentence ``failed``."""
    try:
        return admit(change, gates)
    except DriverRefused as e:
        raise _refusal_answer(e)
    except (HTTPException, InstalledEquipmentError):
        raise
    except Exception as e:
        raise _api_error(500, failed, e)


def _refusal_answer(refusal: DriverRefused) -> HTTPException:
    """``admit``'s refusal as this API answers it: its status and sentence,
    with the loader's reasons beneath it when there are any."""
    if refusal.errors:
        return definition_invalid(
            list(refusal.errors), preamble=refusal.detail, status_code=refusal.status_code
        )
    return HTTPException(status_code=refusal.status_code, detail=refusal.detail)


def enforce_declared_floor(declared: object) -> None:
    """Refuse a driver this platform is too old to run, whatever door it came in.

    Every door that puts a driver on this box asks this one question, so a
    hand-import and a Browse Community install answer it the same way. The
    import doors are the ones with *less* context — no catalog entry, no
    version column, nothing on screen saying what the file needs — so they are
    the last place that should have been the permissive one.
    """
    if isinstance(declared, str) and declared:
        _enforce_min_platform_version(declared)


def _enforce_driver_id_match(driver_class: Any, expected_id: str) -> None:
    """Raise HTTPException 422 if the driver's own DRIVER_INFO id differs from
    the requested ``expected_id``: the gate an install and an update hold the
    staged driver to.

    The registry keys on the file's own DRIVER_INFO id, but the filename and the
    listing/edit/delete surfaces key on the requested id. If they diverge, a
    later edit/delete can't find the driver and a mismatched id can silently
    overwrite an unrelated registered driver — require them to agree before
    registering.
    """
    info = getattr(driver_class, "DRIVER_INFO", None)
    refuse_another_id(info.get("id") if isinstance(info, dict) else None, expected_id)


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


def _get_driver_repo_dir() -> Path:
    """Get the driver_repo/ directory path."""
    from openavc.system_config import DRIVER_REPO_DIR
    return DRIVER_REPO_DIR


def definition_invalid(
    errors: list[str], preamble: str | None = None, status_code: int = 422
) -> StructuredApiError:
    """422 for a driver definition that failed validation.

    The ``detail`` string is the whole message — heading plus the errors, one per
    line — so a client that only reads ``detail`` still shows the author every
    problem to fix. The same list rides along in ``errors`` for anything that
    wants to render them individually.
    """
    heading = preamble or (
        "The driver was not saved. Fix this first"
        if len(errors) == 1
        else "The driver was not saved. Fix these first"
    )
    detail = f"{heading}:\n" + "\n".join(errors) if errors else heading
    return StructuredApiError(status_code, detail, errors=errors)


def _reject_unknown_keys(driver_def: dict[str, Any]) -> None:
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
    raise definition_invalid(
        problems,
        preamble=(
            "The driver was not imported. Check the spelling of these keys "
            "against the driver format"
        ),
    )


def _installed_yaml_companion(main_file: Path, driver_repo: Path) -> Path | None:
    """The companion file an installed YAML driver owns: the one the uninstall
    removes with it and an update replaces.

    Only a ``_discovery.py`` file in the driver folder itself, found by the
    base name of what the YAML names. A name that resolves outside the folder
    (a link, ``..``), or any other ``.py``, is the user's own and is never
    touched. ``None`` for a Python driver too, whose companions go by name
    (``remove_python_companions``), and for a YAML that cannot be read.
    """
    if main_file.suffix != ".avcdriver":
        return None
    try:
        yaml_text = main_file.read_text(encoding="utf-8")
    except OSError:
        return None
    relpath = companion_relpath_from_yaml(yaml_text)
    if not relpath:
        return None
    candidate = driver_repo / PurePosixPath(relpath).name
    try:
        candidate.resolve().relative_to(driver_repo.resolve())
    except ValueError:
        return None
    if candidate.exists() and candidate.name.endswith(DISCOVERY_COMPANION_SUFFIX):
        return candidate
    return None


def _remove_files(*paths: Path | None) -> None:
    """Remove what a failed door wrote; a ``None`` is a file it never wrote."""
    for path in paths:
        if path is not None:
            path.unlink(missing_ok=True)


_COMMUNITY_UNREACHABLE = (
    "Could not reach the community driver library at raw.githubusercontent.com. "
    "Check that this system can reach the internet."
)


@router.get("/drivers/community")
async def get_community_drivers(refresh: bool = False) -> dict[str, Any]:
    """Fetch the community driver index from GitHub (cached; ``refresh``
    fetches it now).

    The cache answers its last good list when a fetch fails, so an empty
    list means no copy was ever fetched: answered 200 with ``error``, the
    sentence the Programmer shows where the library would be.
    """
    cache = index_cache()
    drivers = await cache.get_drivers(force=refresh)
    return {
        "drivers": drivers,
        "error": None if drivers else (cache.failure_sentence or _COMMUNITY_UNREACHABLE),
    }


@router.post("/drivers/install")
async def install_community_driver(body: CommunityDriverInstallRequest) -> dict[str, Any]:
    """Download and install a driver from the community repo."""
    require_mutable_driver(body.driver_id)

    # min_platform_version supplied by the caller. The file's own is checked
    # after the download, for both kinds of driver, so a caller that sends
    # none (Discovery's Install & Add, the AI's install) cannot bypass it.
    if body.min_platform_version:
        _enforce_min_platform_version(body.min_platform_version)

    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    # Pin the source to the official catalog repo (not merely "some GitHub URL")
    require_catalog_url(body.file_url, what="Driver URL")

    # The catalog's hashes for this driver, read server-side. Fetched before the
    # download so a mismatch is caught with nothing yet written.
    files = CatalogFiles(
        body.driver_id, body.file_url, driver_repo, await catalog_check(body.driver_id)
    )

    # Every file is fetched, and checked against the catalog, before anything
    # is written; the driver file is checked first, so a driver this platform
    # cannot run is refused before its companions are asked for.
    download = await files.download(_check_driver_file)
    _admitted(
        DriverChange(driver_repo, files.file_name, download.files, optional=download.optional),
        Gates(driver_class=lambda cls: _enforce_driver_id_match(cls, files.driver_id)),
        failed=f"Failed to load driver '{files.driver_id}'",
    )

    # Refresh discovery engine with new driver hints
    from openavc.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    activated = await _activate_waiting_devices("install")

    await announce_drivers_changed(body.driver_id)
    return {
        "status": "installed",
        "driver_id": body.driver_id,
        "file": files.file_name,
        "activated_devices": activated,
    }


# What each door says above the loader's reasons when it refuses the driver
# file (the community install says ``Gates``' own).
_IMPORT_REFUSED = "The driver was not imported. Fix the driver file first"
_BUNDLE_REFUSED = "The driver in the bundle was not imported. Fix its driver file first"
_UPDATE_REFUSED = (
    "The driver was not updated. Its new file did not load, and the version you had is unchanged"
)

# A driver id that can also be its file name.
_FILE_NAME_ID = re.compile(rf"^{DRIVER_FILE_STEM}$")

# A driver file name an upload may keep, and a bundle may hold.
_DRIVER_FILE_NAME = re.compile(rf"^{DRIVER_FILE_STEM}\.(avcdriver|py)$")


def _named_for_its_id(filename: str, content: bytes) -> str | None:
    """The file name an uploaded driver is saved under: its declared id plus
    its extension, or None when the id cannot be read or cannot be a file name.

    Every other door finds a driver's file by its id, and a browser renames a
    second download of the same file ("academy_td1 (2).py"), so the name it
    arrived with is not the one it keeps.
    """
    driver_id = declared_driver_info(filename, content).get("id")
    if not isinstance(driver_id, str) or not _FILE_NAME_ID.match(driver_id):
        return None
    return f"{driver_id}{Path(filename).suffix}"


def _remove_other_copies(driver_repo: Path, driver_id: str, kept: Path) -> list[str]:
    """Remove the other files in driver_repo that are this driver, of the
    kept file's kind, now that ``kept`` is the driver. A renamed copy left
    behind would be found again by the Code view, export and uninstall.
    Returns the names removed."""
    from openavc.drivers.python_modules import forget_module

    removed: list[str] = []
    for other in driver_files_for(driver_repo, driver_id, kept.suffix):
        if other == kept:
            continue
        if other.suffix == ".py":
            _move_companions(other, kept)
            forget_module(other)
        other.unlink(missing_ok=True)
        removed.append(other.name)
        log.info(f"Driver '{driver_id}': removed {other.name}, replaced by {kept.name}")
    return removed


def _move_companions(other: Path, kept: Path) -> None:
    """A replaced Python copy's simulator and discovery files move to the kept
    name, unless the kept name has its own, which wins."""
    for companion in driver_companions(other):
        target = kept.with_name(f"{kept.stem}{companion.name[len(other.stem):]}")
        if target.exists():
            companion.unlink(missing_ok=True)
        else:
            companion.rename(target)


async def _uploaded_file(request: Request) -> Any:
    """The file an upload door was sent, in the multipart form's ``file`` field."""
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=422, detail="No file provided. Use 'file' field in multipart form.")
    return upload


async def _activate_waiting_devices(after: str) -> list[str]:
    """Bring up the project devices that were waiting for a driver that has
    now arrived, and name them. Without this a person would reload the project
    (or restart) to see the device come online. A failure is logged and never
    the door's: the driver is in place either way."""
    try:
        engine = _get_engine()
        return await engine.devices.retry_all_orphans()
    except Exception:
        log.exception("Failed to retry orphans after %s", after)
        return []


@router.post("/drivers/upload")
async def upload_driver(request: Request) -> dict[str, Any]:
    """Upload a driver file (.avcdriver or .py) from the user's computer."""
    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    upload = await _uploaded_file(request)
    # Sanitize filename: strip directory components to prevent path traversal
    filename = PurePosixPath(upload.filename or "unknown").name
    if not filename.endswith((".avcdriver", ".py")):
        raise HTTPException(status_code=422, detail="File must be .avcdriver or .py")

    content = await upload.read()

    # The same gate Browse Community applies, before the file lands: a driver
    # hand-imported from a partner's website is the same file the catalog
    # install would have refused, and the systems this protects are exactly the
    # ones that have not updated. Asked of the name it arrived with, too.
    require_mutable_driver_file(filename, content)

    # Saved under its declared id; only a driver whose id cannot be read keeps
    # the name it arrived with, and then that name must be a safe one.
    named = _named_for_its_id(filename, content)
    if named is not None:
        filename = named
    elif not _DRIVER_FILE_NAME.match(filename):
        raise HTTPException(status_code=422, detail="Invalid filename — use only letters, numbers, hyphens, and underscores")

    _check_driver_file(filename, content)
    admitted = _admitted(
        DriverChange(driver_repo, filename, {filename: content}),
        Gates(
            definition=_reject_unknown_keys,
            not_a_definition=_IMPORT_REFUSED,
            no_driver_class=_IMPORT_REFUSED,
        ),
        failed=f"Failed to load uploaded driver '{filename}'",
    )
    driver_id = admitted.driver_class.DRIVER_INFO.get("id", filename)
    _remove_other_copies(driver_repo, driver_id, driver_repo / filename)

    # Promote any project devices waiting on this driver — same flow as
    # /drivers/install so manual uploads behave identically.
    activated = await _activate_waiting_devices("upload")

    await announce_drivers_changed(driver_id)
    return {
        "status": "uploaded",
        "driver_id": driver_id,
        "file": filename,
        "activated_devices": activated,
        "notices": list(admitted.notices),
    }


@router.post("/drivers/upload-bundle")
async def upload_driver_bundle(request: Request) -> dict[str, Any]:
    """Upload a driver as a .zip bundle: one driver file plus its companions.

    A Python driver is really a bundle — the main ``.py`` plus an optional
    ``*_discovery.py`` companion and an optional ``*_sim.py`` simulator. This
    endpoint accepts a zip of those files (it also handles a YAML driver +
    its ``_discovery.py``) and admits them as one change: refused, every file
    already in ``driver_repo/`` stays as it was. Companion-only zips are
    rejected.

    Note: a Python driver is executable code; loading one runs it in the
    server process. This validates file *shape* (zip integrity, allowed
    names/types, exactly one main), not safety — the same trust model as
    installing any community Python driver.
    """
    driver_repo = _get_driver_repo_dir()
    driver_repo.mkdir(parents=True, exist_ok=True)

    upload = await _uploaded_file(request)
    raw_name = upload.filename or "bundle.zip"
    if not raw_name.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Bundle must be a .zip file")

    entries = _bundle_entries(await upload.read())
    main_name = _bundle_main(entries)
    for name, data in entries.items():
        require_mutable_driver_file(name, data)
    entries, main_name = _bundle_named_for_its_id(entries, main_name)

    _check_driver_file(main_name, entries[main_name])
    admitted = _admitted(
        DriverChange(driver_repo, main_name, entries),
        Gates(
            definition=_reject_unknown_keys,
            not_a_definition=_BUNDLE_REFUSED,
            no_driver_class=_BUNDLE_REFUSED,
        ),
        failed=f"Failed to load uploaded driver bundle '{raw_name}'",
    )
    driver_id = admitted.driver_class.DRIVER_INFO.get("id", main_name)
    _remove_other_copies(driver_repo, driver_id, driver_repo / main_name)

    # Refresh discovery hints + promote any devices waiting on this driver,
    # matching the single-file upload / community install paths.
    from openavc.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()
    activated = await _activate_waiting_devices("bundle upload")

    await announce_drivers_changed(driver_id)
    return {
        "status": "uploaded",
        "driver_id": driver_id,
        "files": sorted(entries.keys()),
        "activated_devices": activated,
        "notices": list(admitted.notices),
    }


def _bundle_entries(content: bytes) -> dict[str, bytes]:
    """The files a driver bundle holds, by base name, or the 422 saying why it
    is not one. The size ceilings are zip-bomb guards: generous (a driver
    bundle is a few small text files), so they only stop pathological inputs.
    Every entry is checked up front, base name only (which defuses path
    traversal), and any stray file refuses the whole bundle, so the contract
    is unambiguous."""
    archive, members = _bundle_members(content)
    entries: dict[str, bytes] = {}
    for member in members:
        base = PurePosixPath(member.filename).name
        if not base or not _DRIVER_FILE_NAME.match(base):
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
    return entries


def _open_bundle(content: bytes) -> Any:
    """The bundle's zip, refused over 25 MB or when it is not one."""
    import io
    import zipfile

    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Bundle is too large (max 25 MB).")
    try:
        return zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Not a valid .zip file.")


def _bundle_members(content: bytes) -> tuple[Any, list[Any]]:
    """The zip and the files in it (folders aside), refused over the ceilings."""
    archive = _open_bundle(content)
    members = [m for m in archive.infolist() if not m.is_dir()]
    if len(members) > 100:
        raise HTTPException(status_code=422, detail="Bundle has too many files.")
    if sum(m.file_size for m in members) > 50 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Bundle contents are too large.")
    return archive, members


def _bundle_main(entries: dict[str, bytes]) -> str:
    """The bundle's one driver file: every other file is a companion."""
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
    return mains[0]


def _bundle_named_for_its_id(
    entries: dict[str, bytes], main_name: str
) -> tuple[dict[str, bytes], str]:
    """The bundle's files as they are saved: the main file and its conventional
    Python companions under the driver's declared id, as at the single-file
    door. A YAML driver's companion keeps its name: the YAML names it."""
    named = _named_for_its_id(main_name, entries[main_name])
    if named is None or named == main_name:
        return entries, main_name
    new_stem = PurePosixPath(named).stem
    renamed: dict[str, bytes] = {}
    for name, data in entries.items():
        suffix = _python_companion_suffix(name, main_name)
        if name == main_name:
            name = named
        elif suffix:
            name = f"{new_stem}{suffix}"
        renamed[name] = data
    return renamed, named


def _python_companion_suffix(name: str, main_name: str) -> str | None:
    """The suffix (``_sim.py``, ``_discovery.py``) that makes ``name`` a Python
    main file's conventional companion, or None."""
    main_stem = PurePosixPath(main_name).stem
    if not main_name.endswith(".py") or not name.startswith(main_stem):
        return None
    suffix = name[len(main_stem):]
    return suffix if suffix in COMPANION_SUFFIXES else None


def _installed_definition_entry(filepath: Path) -> dict[str, Any] | None:
    """An ``.avcdriver`` in the driver folder as Installed lists it: by its own
    id, name and version; by its file name when it cannot be read, decoded as
    UTF-8 (``UnicodeDecodeError`` is a ``ValueError``) or parsed; and not at
    all when it parses to something other than a mapping. ``load_error`` is
    why it did not load (``refused_files``), else None; ``load_notices`` what
    the author's doors refuse in a file that loaded anyway."""
    import yaml

    try:
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, ValueError):
        data = {}
    else:
        if not isinstance(data, dict):
            return None
    return {
        "id": data.get("id", filepath.stem),
        "name": data.get("name", filepath.stem),
        "format": "avcdriver",
        "filename": filepath.name,
        "version": data.get("version", ""),
        "load_error": load_error(filepath),
        "load_notices": load_notices(filepath),
    }


def _installed_python_entry(filepath: Path) -> dict[str, Any]:
    """A Python driver file as Installed lists it: by the id, name and version
    of the first registered class with an id whose module name contains the
    file's name, else by the file name made readable, with no version; and
    why it did not load (``load_error``), as a definition's entry has it."""
    info: dict[str, Any] = {"id": filepath.stem}
    for _registered_id, cls in registered_driver_classes():
        if cls.DRIVER_INFO.get("id") and filepath.stem in getattr(cls, "__module__", ""):
            info = cls.DRIVER_INFO
            break
    return {
        "id": info["id"],
        "name": info.get("name", filepath.stem.replace("_", " ").title()),
        "format": "python",
        "filename": filepath.name,
        "version": info.get("version", ""),
        "load_error": load_error(filepath),
        "load_notices": load_notices(filepath),
    }


@router.get("/drivers/installed")
async def list_installed_community_drivers() -> dict[str, Any]:
    """List drivers installed in driver_repo/."""
    from openavc.drivers.driver_loader import _is_driver_file

    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        return {"drivers": []}

    definitions = [_installed_definition_entry(f) for f in sorted(driver_repo.glob("*.avcdriver"))]
    # A discovery or simulator companion, or an underscore helper, lives
    # beside its driver but is not one.
    python = [
        _installed_python_entry(f) for f in sorted(driver_repo.glob("*.py")) if _is_driver_file(f)
    ]
    return {"drivers": [entry for entry in definitions if entry is not None] + python}


def _refuse_while_in_use(driver_id: str) -> None:
    """A driver a project device uses is never uninstalled; the 409 names them."""
    engine = _get_engine()
    if not engine.project:
        return
    using_devices = [d.id for d in engine.project.devices if d.driver == driver_id]
    if using_devices:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot uninstall: driver is in use by device(s): {', '.join(using_devices)}",
        )


def _remove_installed_file(deleted_file: Path, driver_repo: Path) -> None:
    """Remove one installed driver file and the companions that travel with it.

    A YAML's declared companion goes too: the install fetches the pair as one
    unit, and a companion left behind would clutter the Code tab and the
    Installed Drivers panel with an orphaned probe file. Python drivers carry
    their companions by naming convention rather than a YAML declaration; the
    discovery and sim siblings go to match the install side, which fetches them
    as part of the same install.
    """
    companion = _installed_yaml_companion(deleted_file, driver_repo)
    deleted_file.unlink(missing_ok=True)
    _remove_files(companion)
    if deleted_file.suffix == ".py":
        remove_python_companions(deleted_file)


@router.delete("/drivers/installed/{driver_id}")
async def uninstall_driver(driver_id: str) -> dict[str, Any]:
    """Uninstall a driver from driver_repo/ and unregister from memory."""
    require_mutable_driver(driver_id)

    _refuse_while_in_use(driver_id)

    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        raise HTTPException(status_code=404, detail="Driver not found")

    # Every file that is this driver, a renamed upload included, so a copy
    # left behind cannot register the driver again at the next start.
    driver_files = (
        driver_files_for(driver_repo, driver_id, ".avcdriver")
        + driver_files_for(driver_repo, driver_id, ".py")
    )
    if not driver_files:
        raise HTTPException(status_code=404, detail=f"Driver '{driver_id}' not found in driver_repo")

    for deleted_file in driver_files:
        _remove_installed_file(deleted_file, driver_repo)
    unregister_driver(driver_id)

    # Refresh discovery engine so stale matches are cleared
    from openavc.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    await announce_drivers_changed(driver_id)
    return {"status": "uninstalled", "driver_id": driver_id}


async def _requested_update_url(request: Request) -> str:
    """The catalog URL an update request names, once the floor the caller
    sent is met. The file's own floor is checked again after the download."""
    body = await request.json()
    file_url = body.get("file_url")
    if not file_url:
        raise HTTPException(status_code=422, detail="file_url is required")
    min_ver = body.get("min_platform_version")
    if min_ver:
        _enforce_min_platform_version(min_ver)
    return file_url


def _file_an_update_replaces(driver_repo: Path, driver_id: str) -> Path | None:
    """The installed file a community update replaces: the first, in folder
    order, YAML files before Python ones, that is named for the id or is a
    YAML declaring it. Helpers (a leading ``_``) are passed over, and so is a
    YAML that cannot be read. A Python file counts by its name only."""
    for filepath in list(driver_repo.glob("*.avcdriver")) + list(driver_repo.glob("*.py")):
        if filepath.name.startswith("_"):
            continue
        if filepath.stem == driver_id or _yaml_declares(filepath, driver_id):
            return filepath
    return None


def _yaml_declares(filepath: Path, driver_id: str) -> bool:
    """Whether a ``.avcdriver`` file declares ``driver_id``; an unreadable one does not."""
    if filepath.suffix != ".avcdriver":
        return False
    import yaml

    try:
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return False
    return isinstance(data, dict) and data.get("id") == driver_id


def _what_an_update_removes(
    old_file: Path, old_companion: Path | None, files: CatalogFiles, download: CatalogDownload
) -> list[str]:
    """The files an update takes away: the old copy when it had another name;
    the old YAML's companion when the new one names another or none; and a
    Python companion the catalog answered a definite 404 for, since this
    version dropped it and the old copy would outlive its driver. Only on a
    404: a transport error is not one, so a flaky network can't delete a
    working simulator."""
    removes = list(download.unpublished)
    if old_file != files.path:
        removes.append(old_file.name)
    if old_companion is not None and old_companion.name not in download.files:
        removes.append(old_companion.name)
    return removes


async def _rebuild_devices(driver_id: str) -> tuple[list[str], list[str]]:
    """Rebuild the devices running a driver that was just replaced: the ones
    that came back, and the ones that were running it and did not.

    A device already running the driver was built from the old class and goes
    on using it until something rebuilds it, so an update only reaches the
    fleet after this swap. It is the same one the Code editor's hot-reload
    does, and the same one the two driver-definition writers do.

    Nothing here may fail the request: the new file is written and registered
    by this point, so an error would report an update that did happen as one
    that did not, and the caller would press Update again. ``reload_driver``
    logs each device's own failure and leaves it out of what it returns.
    """
    reconnected: list[str] = []
    using_before: list[str] = []
    try:
        engine = _get_engine()
        using_before = engine.devices.get_devices_using_driver(driver_id)
        reconnected = await engine.devices.reload_driver(driver_id)
    except Exception:
        log.exception(
            "Driver '%s' was updated, but its devices could not be rebuilt",
            driver_id,
        )
    return reconnected, [d for d in using_before if d not in reconnected]


@router.post("/drivers/installed/{driver_id}/update")
async def update_driver(driver_id: str, request: Request) -> dict[str, Any]:
    """Update an installed community driver to a newer version."""
    require_mutable_driver(driver_id)

    driver_repo = _get_driver_repo_dir()
    if not driver_repo.exists():
        raise HTTPException(status_code=404, detail="Driver not found")

    file_url = await _requested_update_url(request)
    old_file = _file_an_update_replaces(driver_repo, driver_id)
    if not old_file:
        raise HTTPException(status_code=404, detail=f"Driver '{driver_id}' not found in driver_repo")

    # Validate the new URL the same way install does — finding the old file
    # accepts any file_url, but the install endpoint pins the source to the
    # official catalog repo.
    require_catalog_url(file_url, what="Driver URL")
    files = CatalogFiles(driver_id, file_url, driver_repo, await catalog_check(driver_id))

    # Resolve the existing companion (if any) before anything is fetched, so
    # the change knows what it takes away.
    old_companion = _installed_yaml_companion(old_file, driver_repo)

    # The new version's files, a Python driver's convention companions
    # included, exactly as install fetches them. Without those the new driver
    # code would land beside the previous version's `_sim.py` /
    # `_discovery.py`: the simulator would keep answering with the old protocol
    # while the driver speaks the new one, and discovery would probe with logic
    # that no longer matches the driver. A failed update leaves the working
    # driver in place: nothing is written until every file is in hand and the
    # staged copy is admitted.
    download = await files.download(_check_driver_file)
    admitted = _admitted(
        DriverChange(
            driver_repo,
            files.file_name,
            download.files,
            optional=download.optional,
            removes=_what_an_update_removes(old_file, old_companion, files, download),
        ),
        Gates(
            driver_class=lambda cls: _enforce_driver_id_match(cls, driver_id),
            not_a_definition=_UPDATE_REFUSED,
            no_driver_class=_UPDATE_REFUSED,
        ),
        failed=f"Failed to load updated driver '{driver_id}'",
    )
    for name in admitted.removed:
        if name in download.unpublished:
            log.info("Removed %s — no longer published for driver '%s'", name, driver_id)
    reconnected, not_reconnected = await _rebuild_devices(driver_id)

    from openavc.api.discovery import refresh_all_device_matches
    await refresh_all_device_matches()

    await announce_drivers_changed(driver_id)
    return {
        "status": "updated",
        "driver_id": driver_id,
        "file": files.file_name,
        "devices_reconnected": reconnected,
        "devices_not_reconnected": not_reconnected,
    }
