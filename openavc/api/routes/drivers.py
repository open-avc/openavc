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
install) and its sentences. What follows every change, here and at the
Builder's doors, is `driver_change.changed`: the simulation, the devices,
Discovery and the announcement, in one order for every door. Each door holds
its driver's id (`driver_change.holding`) from its first check to its
announcement, so a second door on the same driver begins once the first has
answered; an import holds the id its file declares.
`tests/test_driver_install_doors_pins.py` holds each door as it is.

Four neighbours are *not* here: `api/driver_catalog.py` (the community
catalog: its addresses, hashes and downloads), `routes/driver_definitions.py`
(the Driver Builder's definition CRUD), `routes/driver_test.py` (the live-test
and dry-run harness) and `routes/python_drivers.py` (``.py`` source
management). This module calls into the last for `remove_python_companions`,
since a Python driver's file set is that module's rule to own.
`announce_drivers_changed` is `driver_change.announce`, under the name the
doors that do not call `changed` yet (the Code view's, a project's) import it
by. Drivers > Installed's rows are `installed_drivers`, which the AI's
get_installed_drivers answers too.
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
    changed,
    check_driver_file,
    claim,
    floor_refusal,
    holding,
)
# Re-exported for the doors that do not call ``changed`` yet (module docstring).
from openavc.drivers.driver_change import announce as announce_drivers_changed  # noqa: F401
from openavc.drivers.driver_loader import (
    COMPANION_SUFFIXES,
    DISCOVERY_COMPANION_SUFFIX,
    DRIVER_FILE_STEM,
    companion_relpath_from_yaml,
    declared_driver_info,
    driver_companions,
    driver_files_for,
    require_mutable_driver_file,
    restore_driver_registration,
    serving_file,
)
from openavc.drivers.refused_files import load_error, load_notices
from openavc.utils.logger import get_logger
from openavc.drivers.registry import (
    list_registered_drivers,
    registered_driver_classes,
    require_mutable_driver,
)

log = get_logger(__name__)


router = APIRouter(route_class=ProgrammerRoute)


def _enforce_min_platform_version(required: str) -> None:
    """Raise HTTPException 422 if the running OpenAVC is older than
    ``required``, the floor a request names. The rule is
    ``driver_change.floor_refusal``."""
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


def admit_or_answer(change: DriverChange, gates: Gates, failed: str) -> Admitted:
    """``driver_change.admit`` as this API answers it, at every door that
    writes a driver file (the Driver Builder's saves too): its own refusal as
    its status and sentence, with the loader's reasons beneath it when the
    loader refused the file (``definition_invalid``: one per line in
    ``detail``, and as ``errors``); a protected identity and a gate's refusal
    as they were raised; and anything else the door's 500 sentence
    ``failed``."""
    try:
        return admit(change, gates)
    except DriverRefused as e:
        raise _refusal_answer(e)
    except (HTTPException, InstalledEquipmentError):
        raise
    except Exception as e:
        raise _api_error(500, failed, e)


def claim_or_answer(driver_id: str) -> None:
    """``driver_change.claim`` as this API answers it: at a door that learns
    an id only once it has read or run a file, before anything is registered
    under it, the 409 while another change to that driver is under way."""
    try:
        claim(driver_id)
    except DriverRefused as e:
        raise _refusal_answer(e)


def _refusal_answer(refusal: DriverRefused) -> HTTPException:
    """``admit``'s refusal as this API answers it: its status and sentence,
    with the loader's reasons beneath it when there are any. Gates that name
    no sentence of their own (the Builder's saves) answer under
    ``definition_invalid``'s heading, which counts the reasons."""
    if refusal.errors:
        return definition_invalid(
            list(refusal.errors), preamble=refusal.detail or None, status_code=refusal.status_code
        )
    return HTTPException(status_code=refusal.status_code, detail=refusal.detail)


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


def _driver_dirs(driver_repo: Path) -> list[Path]:
    """The library's directories, the built-ins first, as the loader reads them."""
    from openavc.system_config import DRIVER_DEFINITIONS_DIR

    return [DRIVER_DEFINITIONS_DIR, driver_repo]


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
    async with holding(body.driver_id):
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
        admit_or_answer(
            DriverChange(driver_repo, files.file_name, download.files, optional=download.optional),
            Gates(driver_class=lambda cls: _enforce_driver_id_match(cls, files.driver_id)),
            failed=f"Failed to load driver '{files.driver_id}'",
        )
        outcome = await changed(body.driver_id)
        return {
            "status": "installed",
            "driver_id": body.driver_id,
            "file": files.file_name,
            "activated_devices": outcome.activated,
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
    async with holding(_import_id(filename, content)):
        return await _import_driver_file(driver_repo, filename, content)


def _import_id(filename: str, content: bytes) -> str:
    """The id an import holds while it runs: the one its driver file declares,
    or the name the file arrived with when no id can be read without running
    it (``admit`` claims the id the file turns out to be)."""
    driver_id = declared_driver_info(filename, content).get("id")
    return driver_id if isinstance(driver_id, str) and driver_id else Path(filename).stem


async def _import_driver_file(driver_repo: Path, filename: str, content: bytes) -> dict[str, Any]:
    """The single-file import, from its first check to its announcement."""
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
    admitted = admit_or_answer(
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
    outcome = await changed(driver_id)
    return {
        "status": "uploaded",
        "driver_id": driver_id,
        "file": filename,
        "activated_devices": outcome.activated,
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
    async with holding(_import_id(main_name, entries[main_name])):
        for name, data in entries.items():
            require_mutable_driver_file(name, data)
        entries, main_name = _bundle_named_for_its_id(entries, main_name)

        _check_driver_file(main_name, entries[main_name])
        admitted = admit_or_answer(
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
        outcome = await changed(driver_id)
        return {
            "status": "uploaded",
            "driver_id": driver_id,
            "files": sorted(entries.keys()),
            "activated_devices": outcome.activated,
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
    of the class registered from it, else by what its source declares
    (``python_driver_info``), else by its file name made readable, with no
    version; and why it did not load (``load_error``), as a definition's entry
    has it."""
    from openavc.drivers.driver_loader import python_driver_info
    from openavc.drivers.python_modules import module_name

    info: dict[str, Any] = {"id": filepath.stem, **python_driver_info(filepath)}
    for _registered_id, cls in registered_driver_classes():
        if cls.DRIVER_INFO.get("id") and cls.__module__ == module_name(filepath):
            info = cls.DRIVER_INFO
            break
    return {
        "id": info.get("id") or filepath.stem,
        "name": info.get("name", filepath.stem.replace("_", " ").title()),
        "format": "python",
        "filename": filepath.name,
        "version": info.get("version", ""),
        "load_error": load_error(filepath),
        "load_notices": load_notices(filepath),
    }


def installed_drivers(driver_repo: Path) -> list[dict[str, Any]]:
    """THE list of drivers installed in ``driver_repo``, which Drivers >
    Installed and the AI's get_installed_drivers both answer: one row per
    driver, the file that serves it (``serving_file``), or, when none of its
    files loaded, the one that would, with why. Definitions first, then Python
    drivers, each by file name. Companions and underscore helpers are never
    drivers. No folder, no rows."""
    from openavc.drivers.driver_loader import serving_files

    if not driver_repo.exists():
        return []
    listed = sorted(serving_files([driver_repo], refused_too=True).values())
    definitions = [_installed_definition_entry(f) for f in listed if f.suffix == ".avcdriver"]
    python = [_installed_python_entry(f) for f in listed if f.suffix == ".py"]
    return [entry for entry in definitions if entry is not None] + python


@router.get("/drivers/installed")
async def list_installed_community_drivers() -> dict[str, Any]:
    """List drivers installed in driver_repo/ (``installed_drivers``)."""
    return {"drivers": installed_drivers(_get_driver_repo_dir())}


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
        from openavc.drivers.python_modules import forget_module

        forget_module(deleted_file)


@router.delete("/drivers/installed/{driver_id}")
async def uninstall_driver(driver_id: str) -> dict[str, Any]:
    """Uninstall a driver from driver_repo/ and unregister from memory."""
    async with holding(driver_id):
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
        # A built-in of the same id serves it again; else the id is dropped.
        if restore_driver_registration(driver_id, _driver_dirs(driver_repo)):
            await changed(driver_id)
        else:
            await changed(gone=[driver_id])
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
    """The installed file a community update replaces: the one that serves
    the id in the driver folder (``serving_file``), or, when none of its files
    loaded, the one that would."""
    return serving_file([driver_repo], driver_id, refused_too=True)


def _what_an_update_removes(
    old_file: Path, old_companion: Path | None, files: CatalogFiles, download: CatalogDownload
) -> list[str]:
    """The files an update takes away: the old copy when it had another name,
    with a Python copy's own companions when the new version is a definition;
    the old YAML's companion when the new one names another or none; and a
    Python companion the catalog answered a definite 404 for, since this
    version dropped it and the old copy would outlive its driver. Only on a
    404: a transport error is not one, so a flaky network can't delete a
    working simulator."""
    removes = list(download.unpublished)
    if old_file != files.path:
        removes.append(old_file.name)
        if old_file.suffix == ".py" and files.kind != ".py":
            removes.extend(companion.name for companion in driver_companions(old_file))
    if old_companion is not None and old_companion.name not in download.files:
        removes.append(old_companion.name)
    return removes


@router.post("/drivers/installed/{driver_id}/update")
async def update_driver(driver_id: str, request: Request) -> dict[str, Any]:
    """Update an installed community driver to a newer version."""
    async with holding(driver_id):
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
        admitted = admit_or_answer(
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
        # A device already running the driver was built from the old class and
        # goes on using it until it is built again, so an update only reaches the
        # fleet here.
        outcome = await changed(driver_id)
        return {
            "status": "updated",
            "driver_id": driver_id,
            "file": files.file_name,
            "devices_reconnected": outcome.reconnected,
            "devices_not_reconnected": outcome.not_reconnected,
        }
