"""Python driver source management — the Programmer IDE's Code view.

CRUD over the ``.py`` files in ``driver_repo/``: list, read, save, create,
delete, hot-reload, and export as a bundle. Every path resolves through
`_safe_driver_path`, so a driver id can only ever name a file inside
``driver_repo/``.

**A Python driver is a file set, not a file.** The main ``<id>.py`` travels
with its conventional siblings — ``<id>_discovery.py`` (discovery probe) and
``<id>_sim.py`` (simulator). Install fetches the trio as one unit, so export
and delete treat it as one unit too; that rule lives here, in
`python_driver_companions`, and `routes/drivers.py` calls it when uninstalling
a ``.py`` driver.

Note the auth posture, which is deliberately *stricter* than its neighbours:
every write here carries `require_claimed_auth` on top of the router's
programmer auth, because saving a driver is arbitrary code that this server
will import and run. The definition-CRUD writes next door do not, and
`tests/test_route_auth_posture.py` pins the difference.

Split out of `routes/drivers.py`, which had grown to hold three unrelated
tenants.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from server.api._engine import _get_engine
from server.api.auth import require_claimed_auth
from server.api.errors import api_error as _api_error
from server.api.models import PythonDriverCreateRequest
from server.drivers.driver_loader import COMPANION_SUFFIXES
from server.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter()


def python_driver_companions(main_path: Path) -> list[Path]:
    """The companion files that exist beside a Python driver's ``<stem>.py``.

    Only the two documented suffixes, only the driver's own siblings, only in
    the driver's own directory — so this can never reach a file that isn't
    part of this driver.
    """
    candidates = (
        main_path.with_name(f"{main_path.stem}{suffix}")
        for suffix in COMPANION_SUFFIXES
    )
    return [path for path in candidates if path.is_file()]


def remove_python_companions(main_path: Path) -> list[str]:
    """Delete a Python driver's conventional sibling companions.

    Install / import fetch the trio as one unit, so deleting the driver
    removes it as one unit too — otherwise orphaned companions linger in
    ``driver_repo/``. Returns the names removed.
    """
    removed: list[str] = []
    for companion in python_driver_companions(main_path):
        companion.unlink(missing_ok=True)
        removed.append(companion.name)
    return removed


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
        raise _api_error(500, f"Could not read driver '{driver_id}'.", e)

    return {"driver_id": driver_id, "filename": filepath.name, "source": source}


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

    files = [main_path, *python_driver_companions(main_path)]

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
    """Save the source code of a Python driver file.

    ``require_valid_syntax`` decides what happens to source that will not
    parse, and the two answers belong to the two buttons in the Code view:

    * **Save** (default, flag absent) writes it and *says so* —
      ``{"status": "saved", "syntax_error": ..., "line": N}``. Half-finished
      code is the normal state of an editor and refusing to keep it would lose
      work; what the author must not do is walk away believing the file is
      fine. The old behaviour wrote it and said nothing at all.
    * **Save & Reload** (flag set) refuses: ``{"status": "error", ...}`` and
      **nothing is written**. That button means "make this the live driver",
      and a file that cannot parse cannot load on any future startup — so
      persisting it swaps a working driver on disk for one that drops its
      devices at the next restart, while the still-running process hides it.

    A refusal is reported at HTTP 200 in the body, matching the reload route
    next door (documented behaviour), because the editor needs the structured
    ``line`` to mark the offending row and an HTTPException detail is prose.
    """
    from server.drivers.driver_loader import python_source_syntax_error

    filepath = _safe_driver_path(driver_id)
    source = body.get("source")
    if source is None:
        raise HTTPException(status_code=422, detail="Missing 'source' field")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Python driver '{driver_id}' not found")

    syntax = python_source_syntax_error(source, filepath.name)
    if syntax and body.get("require_valid_syntax"):
        return {"status": "error", "driver_id": driver_id, "saved": False, **syntax}

    try:
        filepath.write_text(source, encoding="utf-8")
    except OSError as e:
        raise _api_error(500, f"Could not save driver '{driver_id}'.", e)

    result: dict[str, Any] = {"status": "saved", "driver_id": driver_id}
    if syntax:
        result["syntax_error"] = syntax["error"]
        result["line"] = syntax["line"]
    return result


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
        raise _api_error(500, f"Could not create driver '{body.id}'.", e)

    # Try to load and register immediately
    from server.drivers.driver_loader import load_python_driver_file
    from server.core.device_manager import register_driver

    driver_class = load_python_driver_file(filepath)
    if driver_class:
        register_driver(driver_class)

    return {"status": "created", "driver_id": body.id}


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
    removed_companions = remove_python_companions(filepath)

    # Unregister from driver registry
    from server.core.device_manager import unregister_driver
    unregister_driver(driver_id)

    # Clean up sys.modules
    import sys
    module_name = f"openavc_driver_{driver_id}"
    sys.modules.pop(module_name, None)

    log.info(f"Deleted Python driver: {driver_id}")
    return {"status": "deleted", "driver_id": driver_id, "removed_companions": removed_companions}


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
