"""Project, library, backup, and log REST API endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.api._engine import _get_engine
from server.api.errors import api_error as _api_error
from server.core.project_loader import ProjectConfig, save_project
from server.utils.log_buffer import get_log_buffer

router = APIRouter()
open_router = APIRouter()


# --- Project ---


@router.get("/project")
async def get_project() -> dict[str, Any]:
    """Get the full project configuration (includes runtime revision counter)."""
    engine = _get_engine()
    if engine.project:
        data = engine.project.model_dump(mode="json")
        data["_revision"] = engine._project_revision
        return data
    raise HTTPException(status_code=404, detail="No project loaded")


@router.post("/project/reload")
async def reload_project() -> dict[str, Any]:
    """Reload project.avc from disk."""
    engine = _get_engine()
    await engine.reload_project()
    return {"status": "reloaded"}


@router.put("/project")
async def save_project_config(request: Request) -> dict[str, Any]:
    """Save a full project configuration, then reload.

    If the request body contains a ``_revision`` field, the server checks
    it against the current revision.  A mismatch means another client
    saved since this client last loaded — return 409 Conflict so the
    frontend can prompt the user.
    """
    # Limit request body to 10 MB to prevent memory exhaustion
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Project file too large (max 10 MB)")
    except (ValueError, TypeError):
        pass  # Malformed content-length header — let FastAPI handle the body
    engine = _get_engine()
    body = await request.json()

    # Optimistic concurrency check (14.3)
    client_revision = body.pop("_revision", None)
    if client_revision is not None:
        try:
            rev = int(client_revision)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid _revision value")
        if rev != engine._project_revision:
            raise HTTPException(
                status_code=409,
                detail="Project was modified by another session. Reload to see the latest changes.",
            )

    try:
        project = ProjectConfig(**body)
    except Exception as e:
        raise _api_error(422, "Invalid project configuration", e)
    save_project(engine.project_path, project)
    await engine.reload_project()
    return {"status": "saved", "revision": engine._project_revision}


@router.get("/project/validate-drivers")
async def validate_drivers() -> dict[str, Any]:
    """Check which drivers required by the project are available or missing."""
    from server.core.device_manager import _DRIVER_REGISTRY

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
        if driver_id in _DRIVER_REGISTRY:
            available.append(driver_id)
        else:
            affected = [d.id for d in engine.project.devices if d.driver == driver_id]
            missing.append({"driver_id": driver_id, "affected_devices": affected})

    return {"available": available, "missing": missing}


# --- Project Library ---


@open_router.get("/library")
async def list_library() -> list[dict[str, Any]]:
    """List all saved projects in the library."""
    from server.core.project_library import list_projects
    return list_projects()


@open_router.get("/library/{project_id}")
async def get_library_project(project_id: str) -> dict[str, Any]:
    """Get a saved project with script contents."""
    from server.core.project_library import get_project
    try:
        data, scripts = get_project(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    return {"id": project_id, "project": data, "scripts": scripts}


@router.post("/library")
async def save_to_library(request: Request) -> dict[str, Any]:
    """Save the current project to the library."""
    from server.api.models import LibrarySaveRequest
    from server.core.project_library import save_to_library as _save

    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    body = await request.json()
    data = LibrarySaveRequest(**body)
    scripts_dir = engine.project_path.parent / "scripts"

    try:
        _save(data.id, engine.project, scripts_dir, data.name, data.description)
    except ValueError as e:
        raise _api_error(409, f"Library project '{data.id}' already exists", e)

    return {"status": "created", "id": data.id}


@router.delete("/library/{project_id}")
async def delete_library_project(project_id: str) -> dict[str, Any]:
    """Delete a project from the library."""
    from server.core.project_library import delete_project
    deleted = delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    return {"status": "deleted", "id": project_id}


@router.patch("/library/{project_id}")
async def update_library_project(project_id: str, request: Request) -> dict[str, Any]:
    """Update a saved project's name and/or description."""
    from server.api.models import LibraryUpdateRequest
    from server.core.project_library import update_project_meta

    body = await request.json()
    data = LibraryUpdateRequest(**body)

    try:
        update_project_meta(project_id, data.name, data.description)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")

    return {"status": "updated", "id": project_id}


@router.post("/library/{project_id}/duplicate")
async def duplicate_library_project(project_id: str, request: Request) -> dict[str, Any]:
    """Duplicate a saved project."""
    from server.api.models import LibraryDuplicateRequest
    from server.core.project_library import duplicate_project

    body = await request.json()
    data = LibraryDuplicateRequest(**body)

    try:
        duplicate_project(project_id, data.new_id, data.new_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found in library")
    except ValueError as e:
        raise _api_error(409, f"Library project '{data.new_id}' already exists", e)

    return {"status": "duplicated", "id": data.new_id}


@router.get("/library/{project_id}/export")
async def export_library_project(project_id: str):
    """Download a saved project as .avc or .zip."""
    from fastapi.responses import Response
    from server.core.project_library import export_project

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
    from server.core.project_library import import_project

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
    except ValueError as e:
        raise _api_error(422, f"Invalid project file '{filename}'", e)

    return {
        "status": "imported",
        "id": result["id"],
        "installed_drivers": result.get("installed_drivers", []),
        "missing_drivers": result.get("missing_drivers", []),
        "warnings": result.get("warnings", []),
    }


# --- Project Creation ---


@router.post("/project/open-from-library")
async def open_from_library(request: Request) -> dict[str, Any]:
    """Replace the current project with a saved project from the library."""
    from server.api.models import LibraryOpenRequest
    from server.core.project_library import open_from_library as _open, sanitize_id
    from server.core.backup_manager import create_backup

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
    from server.core.project_library import create_blank_project, sanitize_id, replace_scripts
    from server.core.backup_manager import create_backup

    engine = _get_engine()
    body = await request.json()

    project_name = body.get("project_name", "New Room")
    project_id = sanitize_id(body.get("project_id") or project_name)

    # Back up current project before replacing with blank
    import asyncio
    await asyncio.to_thread(create_backup, engine.project_path.parent, "Before creating blank project")

    project = create_blank_project(project_id, project_name)
    save_project(engine.project_path, project)

    scripts_dir = engine.project_path.parent / "scripts"
    replace_scripts(scripts_dir, {})

    await engine.broadcast_ws({
        "type": "project.replaced",
        "project_name": project_name,
        "source": "blank",
    })
    await engine.reload_project()

    return {"status": "created", "project_name": project_name}


# --- Logs ---


@router.get("/logs/recent")
async def get_recent_logs(count: int = 100, category: str = "") -> list[dict[str, Any]]:
    """Get recent log entries, optionally filtered by category."""
    entries = get_log_buffer().get_recent(count)
    if category:
        entries = [e for e in entries if e.get("category") == category]
    return entries


# --- Backups ---


@router.get("/backups")
async def list_backups_endpoint() -> list[dict[str, Any]]:
    """List available project backups (ZIP + legacy .avc.bak)."""
    from server.core.backup_manager import list_backups

    engine = _get_engine()
    project_dir = engine.project_path.parent
    backups = list_backups(project_dir)
    return [
        {
            "filename": b.filename,
            "reason": b.reason,
            "timestamp": b.timestamp,
            "project_name": b.project_name,
            "size": b.size_bytes,
            "format": b.format,
        }
        for b in backups
    ]


@router.post("/backups/create")
async def create_backup_endpoint(request: Request) -> dict[str, Any]:
    """Create a manual backup of the current project."""
    from server.core.backup_manager import create_backup

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
    from server.core.backup_manager import create_backup, restore_from_backup

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

    restore_from_backup(backup_path, project_dir)
    await engine.reload_project()
    return {"status": "restored", "filename": filename}
