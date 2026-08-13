"""Project, library, backup, and log REST API endpoints."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from openavc.api._engine import _get_engine
from openavc.api.errors import api_error as _api_error
from openavc.core.engine import ProjectRevisionConflictError
from openavc.core.project_loader import ProjectConfig
from openavc.ui.matrix_model import resolve_matrix_config
from openavc.utils.log_buffer import get_log_buffer
from openavc.drivers.registry import is_driver_registered

router = APIRouter()


# --- Project ---


@router.get("/project")
async def get_project() -> JSONResponse:
    """Get the full project configuration with ETag for concurrency control."""
    engine = _get_engine()
    if engine.project:
        data = engine.project.model_dump(mode="json")
        return JSONResponse(
            content=data,
            headers={"ETag": f'"{engine._project_revision}"'},
        )
    raise HTTPException(status_code=404, detail="No project loaded")


@router.get("/ui/resolved")
async def get_resolved_ui() -> dict[str, Any]:
    """The saved UI as a panel receives it, with every matrix expanded.

    ``/api/project`` above is the authoring copy and stays terse; this is the
    rendering copy. The panel uses it for its own first paint when it is
    embedded in the Builder and the editor has not pushed a project yet.
    """
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=404, detail="No project loaded")
    return {"ui": engine.panel_ui()}


@router.post("/ui/resolve-matrix")
async def resolve_matrix_configs(request: Request) -> dict[str, Any]:
    """Expand matrix configs the Builder is still editing.

    The Builder holds unsaved edits, so it cannot ask for the saved project's
    resolution -- and it must not resolve locally, because its canvas is an
    iframe of the real panel and the panel reads resolved lists only (D6). So it
    posts the configs it is about to draw and gets them back expanded, keyed by
    element id.

    Configs rather than the whole project: this is called while somebody types,
    and a matrix config is the only part of a page that has anything to expand.
    """
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Too many matrix configs (max 1 MB)")
    try:
        payload = json.loads(body or b"{}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e
    configs = payload.get("configs") if isinstance(payload, dict) else None
    if not isinstance(configs, dict):
        raise HTTPException(status_code=400, detail="Expected {\"configs\": {id: config}}")
    return {
        "configs": {
            str(element_id): resolve_matrix_config(config)
            for element_id, config in configs.items()
        }
    }


@router.post("/project/reload")
async def reload_project() -> dict[str, Any]:
    """Reload project.avc from disk."""
    engine = _get_engine()
    await engine.reload_project()
    return {"status": "reloaded"}


@router.put("/project")
async def save_project_config(request: Request) -> dict[str, Any]:
    """Save a full project configuration, then reload.

    Supports optimistic concurrency via If-Match header containing the
    ETag from a previous GET.  A mismatch means another client saved
    since this client last loaded — returns 409 Conflict.
    """
    max_body = 10 * 1024 * 1024
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > max_body:
            raise HTTPException(status_code=413, detail="Project file too large (max 10 MB)")
    except (ValueError, TypeError):
        pass
    raw = await request.body()
    if len(raw) > max_body:
        raise HTTPException(status_code=413, detail="Project file too large (max 10 MB)")
    import json as _json
    try:
        body = _json.loads(raw)
    except _json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Project must be a JSON object")
    engine = _get_engine()

    # Optimistic concurrency: the If-Match header carrying the ETag from the
    # last GET. A `_revision` body field used to mean the same thing; it is
    # refused rather than ignored, because the project model allows extra
    # fields (it would otherwise be persisted into the saved project) and
    # because a caller sending it believes it is protected from concurrent
    # saves — dropping it silently is how one session's edit disappears.
    if "_revision" in body:
        raise HTTPException(
            status_code=400,
            detail="The '_revision' body field is no longer supported. "
                   "Send the ETag from your last GET /api/project in an If-Match header.",
        )

    if_match = request.headers.get("if-match")
    expected_rev: int | None = None
    if if_match is not None:
        try:
            expected_rev = int(if_match.strip('"'))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid If-Match value")

    # The migration chain keys on this field and treats a missing one as the
    # OLDEST format -- running the whole chain over a current-format body,
    # which collapses every placement and re-divides every rem value, then
    # saves the wreckage stamped current. Every legitimate producer (the IDE,
    # exports, the library, the seed) includes it, so absence is a caller bug;
    # refuse it loudly instead of corrupting the project silently.
    if "openavc_version" not in body:
        raise HTTPException(
            status_code=422,
            detail="Project body is missing 'openavc_version'. Send the whole "
                   "document from GET /api/project, version field included.",
        )

    try:
        # Run the format-migration chain before validating: this door takes
        # whole project documents (Programmer import saves through here), so an
        # older export must migrate exactly like a disk load would. Current-
        # format bodies pass through untouched.
        from openavc.core.project_migration import migrate_project
        body, _ = migrate_project(body)
        project = ProjectConfig(**body)
    except Exception as e:
        raise _api_error(422, "Invalid project configuration", e)
    # Compare-and-set runs inside the engine, under the same lock that
    # increments the revision — checked here, two concurrent saves could
    # both pass and one edit would be silently lost.
    try:
        new_revision = await engine.apply_project(
            project, expected_revision=expected_rev
        )
    except ProjectRevisionConflictError:
        raise HTTPException(
            status_code=409,
            detail="Project was modified by another session. Reload to see the latest changes.",
        )
    return JSONResponse(
        content={"status": "saved"},
        headers={"ETag": f'"{new_revision}"'},
    )


@router.get("/project/validate-drivers")
async def validate_drivers() -> dict[str, Any]:
    """Check which drivers required by the project are available or missing."""

    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    available = []
    missing = []
    seen: set[str] = set()
    for device in engine.project.devices:
        driver_id = device.driver
        if driver_id in seen:
            continue
        seen.add(driver_id)
        if is_driver_registered(driver_id):
            available.append(driver_id)
        else:
            affected = [d.id for d in engine.project.devices if d.driver == driver_id]
            missing.append({"driver_id": driver_id, "affected_devices": affected})

    return {"available": available, "missing": missing}


@router.get("/project/export")
async def export_current_project():
    """Download the running project as a .zip bundle.

    The same bundle a library export produces: project.avc plus the scripts,
    drivers, plugins, assets and custom controls the room needs to come up on
    another machine. Built from what is on disk, so an IDE with unsaved
    changes saves before it asks for this.
    """
    from fastapi.responses import Response
    from openavc.core.project_library import export_active_project

    engine = _get_engine()
    try:
        content, filename, content_type = export_active_project(engine.project_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No project file to export")

    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Project Library ---


@router.get("/library")
async def list_library() -> dict[str, Any]:
    """List all saved projects in the library."""
    from openavc.core.project_library import list_projects
    return {"projects": list_projects()}


@router.get("/library/{project_id}")
async def get_library_project(project_id: str) -> dict[str, Any]:
    """Get a saved project with script contents."""
    from openavc.core.project_library import get_project
    try:
        data, scripts = get_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    return {"project_id": project_id, "project": data, "scripts": scripts}


@router.post("/library")
async def save_to_library(request: Request) -> dict[str, Any]:
    """Save the current project to the library."""
    from openavc.api.models import LibrarySaveRequest
    from openavc.core.project_library import save_to_library as _save

    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    body = await request.json()
    data = LibrarySaveRequest(**body)
    scripts_dir = engine.project_path.parent / "scripts"
    assets_dir = engine.project_path.parent / "assets"
    ui_dir = engine.project_path.parent / "ui"

    try:
        _save(data.id, engine.project, scripts_dir, data.name, data.description,
              assets_dir=assets_dir, ui_dir=ui_dir)
    except ValueError as e:
        raise _api_error(409, f"Library project '{data.id}' already exists", e)

    return {"status": "created", "project_id": data.id}


@router.delete("/library/{project_id}")
async def delete_library_project(project_id: str) -> dict[str, Any]:
    """Delete a project from the library."""
    from openavc.core.project_library import delete_project
    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    return {"status": "deleted", "project_id": project_id}


@router.patch("/library/{project_id}")
async def update_library_project(project_id: str, request: Request) -> dict[str, Any]:
    """Update a saved project's name and/or description."""
    from openavc.api.models import LibraryUpdateRequest
    from openavc.core.project_library import update_project_meta

    body = await request.json()
    data = LibraryUpdateRequest(**body)

    try:
        update_project_meta(project_id, data.name, data.description)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")

    return {"status": "updated", "project_id": project_id}


@router.post("/library/{project_id}/duplicate")
async def duplicate_library_project(project_id: str, request: Request) -> dict[str, Any]:
    """Duplicate a saved project."""
    from openavc.api.models import LibraryDuplicateRequest
    from openavc.core.project_library import duplicate_project

    body = await request.json()
    data = LibraryDuplicateRequest(**body)

    try:
        duplicate_project(project_id, data.new_id, data.new_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    except ValueError as e:
        raise _api_error(409, f"Library project '{data.new_id}' already exists", e)

    return {"status": "duplicated", "project_id": data.new_id}


@router.get("/library/{project_id}/export")
async def export_library_project(project_id: str):
    """Download a saved project as .avc or .zip."""
    from fastapi.responses import Response
    from openavc.core.project_library import export_project

    try:
        content, filename, content_type = export_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")

    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/library/import")
async def import_library_project(request: Request) -> dict[str, Any]:
    """Upload a .avc or .zip file to the project library."""
    from openavc.core.project_library import ProjectExistsError, import_project

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=422, detail="No file provided. Use 'file' field in multipart form.")

    filename = upload.filename or "unknown.avc"
    if not filename.endswith((".avc", ".zip")):
        raise HTTPException(status_code=422, detail="File must be .avc or .zip")

    content = await upload.read()
    override_id = form.get("id")

    try:
        result = import_project(content, filename, override_id)
    except ProjectExistsError as e:
        raise _api_error(
            409,
            f"The project library already has '{e.project_id}'. Delete that one from the "
            f"Project Library first, then import again.",
            e,
        )
    except ValueError as e:
        raise _api_error(422, f"Invalid project file '{filename}'", e)

    return {
        "status": "imported",
        "project_id": result["id"],
        "installed_drivers": result.get("installed_drivers", []),
        "missing_drivers": result.get("missing_drivers", []),
        "installed_plugins": result.get("installed_plugins", []),
        "missing_plugins": result.get("missing_plugins", []),
        "warnings": result.get("warnings", []),
    }


# --- Project Creation ---


@router.post("/project/open-from-library")
async def open_from_library(request: Request) -> dict[str, Any]:
    """Replace the current project with a saved project from the library."""
    from openavc.api.models import LibraryOpenRequest
    from openavc.core.project_library import open_from_library as _open, sanitize_id
    from openavc.core.backup_manager import create_backup

    engine = _get_engine()
    body = await request.json()
    data = LibraryOpenRequest(**body)

    project_id = sanitize_id(data.project_id or data.project_name)
    scripts_dir = engine.project_path.parent / "scripts"

    # Back up current project (including scripts) before replacing
    import asyncio
    await asyncio.to_thread(create_backup, engine.project_path.parent, f"Before opening '{data.project_name}'")

    try:
        _open(data.library_id, engine.project_path, scripts_dir,
              project_id, data.project_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{data.library_id}' not found in library")
    except ValidationError as e:
        # A stored project that no longer validates even after migration —
        # surface a friendly 422 instead of a raw 500 (the pre-open backup of
        # the current project has already been taken and stays available).
        raise _api_error(
            422,
            f"Saved project '{data.library_id}' is not a valid project file",
            e,
        )

    await engine.broadcast_ws({
        "type": "project.replaced",
        "project_name": data.project_name,
        "source": "library",
    })
    await engine.reload_project()

    return {"status": "created", "project_name": data.project_name}


@router.post("/project/create-blank")
async def create_blank(request: Request) -> dict[str, Any]:
    """Reset to an empty project."""
    from openavc.core.project_library import create_blank_project, sanitize_id, replace_scripts
    from openavc.core.backup_manager import create_backup

    engine = _get_engine()
    body = await request.json()

    project_name = body.get("project_name", "New Room")
    project_id = sanitize_id(body.get("project_id") or project_name)

    # Back up current project before replacing with blank
    import asyncio
    await asyncio.to_thread(create_backup, engine.project_path.parent, "Before creating blank project")

    project = create_blank_project(project_id, project_name)

    scripts_dir = engine.project_path.parent / "scripts"
    replace_scripts(scripts_dir, {})

    await engine.broadcast_ws({
        "type": "project.replaced",
        "project_name": project_name,
        "source": "blank",
    })
    # A whole new project: LOAD origin persists it and does the full
    # reconcile (library rescan, startup triggers) in one pass instead of
    # save-then-reload-from-disk.
    from openavc.core.project_diff import ProjectOrigin
    await engine.apply_project(project, origin=ProjectOrigin.LOAD)

    return {"status": "created", "project_name": project_name}


# --- Logs ---


@router.get("/logs/recent")
async def get_recent_logs(count: int = 100, category: str = "") -> dict[str, Any]:
    """Get recent log entries, optionally filtered by category."""
    return {"logs": get_log_buffer().get_recent(count, category=category)}


# --- Backups ---


@router.get("/backups")
async def list_backups_endpoint() -> dict[str, Any]:
    """List available project backups (ZIP + legacy .avc.bak)."""
    from openavc.core.backup_manager import list_backups

    engine = _get_engine()
    project_dir = engine.project_path.parent
    backups = list_backups(project_dir)
    return {"backups": [
        {
            "filename": b.filename,
            "reason": b.reason,
            "timestamp": b.timestamp,
            "project_name": b.project_name,
            "size": b.size_bytes,
            "format": b.format,
        }
        for b in backups
    ]}


@router.post("/backups/create")
async def create_backup_endpoint(request: Request) -> dict[str, Any]:
    """Create a manual backup of the current project."""
    from openavc.core.backup_manager import create_backup

    engine = _get_engine()
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    reason = body.get("reason", "Manual backup")

    import asyncio
    path = await asyncio.to_thread(create_backup, engine.project_path.parent, reason)
    if not path:
        raise HTTPException(status_code=404, detail="No project to back up")
    return {"status": "created", "filename": path.name}


@router.post("/backups/{filename:path}/restore")
async def restore_backup(filename: str) -> dict[str, Any]:
    """Restore a project from a backup file (ZIP or legacy .avc.bak)."""
    from openavc.core.backup_manager import create_backup, restore_from_backup

    engine = _get_engine()
    project_dir = engine.project_path.parent

    # Resolve the backup path (supports both "backups/file.zip" and "file.avc.bak")
    backup_path = (project_dir / filename).resolve()

    # Security: ensure the backup is within the project directory tree
    try:
        backup_path.relative_to(project_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail=f"Backup '{filename}' not found")
    if not (backup_path.name.endswith(".zip") or backup_path.name.endswith(".avc.bak")):
        raise HTTPException(status_code=400, detail="Not a valid backup file")

    # Create a backup before restoring
    import asyncio
    await asyncio.to_thread(create_backup, project_dir, "Before restore")

    # Stop the state persister first so its pending debounced flush (up to a 1s
    # window) can't overwrite the state.json we're about to restore.
    if engine.persister:
        engine.persister.stop()

    restore_from_backup(backup_path, project_dir)
    await engine.reload_project()
    # Re-apply the restored state.json to the store + restart the persister, so
    # the restore takes effect immediately and isn't written back over.
    engine.reload_persisted_state()
    return {"status": "restored", "filename": filename}
