"""Script CRUD and execution REST API endpoints."""

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from server.api._engine import _get_engine
from server.api.models import ScriptCreateRequest
from server.core.project_loader import save_project

router = APIRouter()


def _get_scripts_dir() -> Path:
    """Get the scripts directory for the current project."""
    engine = _get_engine()
    return engine.project_path.parent / "scripts"


def _find_script_config(script_id: str) -> dict[str, Any] | None:
    """Find a script config by ID in the current project."""
    engine = _get_engine()
    if not engine.project:
        return None
    for s in engine.project.scripts:
        if s.id == script_id:
            return s.model_dump()
    return None


def _safe_script_path(scripts_dir: Path, filename: str) -> Path:
    """Resolve a script filename safely within the scripts directory."""
    resolved = (scripts_dir / filename).resolve()
    try:
        resolved.relative_to(scripts_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid script filename")
    return resolved


@router.get("/scripts/functions")
async def get_script_functions() -> list[dict[str, str]]:
    """Return all callable functions from loaded scripts."""
    engine = _get_engine()
    if not engine.scripts:
        return []
    return engine.scripts.get_callable_functions()


@router.get("/scripts/{script_id}/source")
async def get_script_source(script_id: str) -> dict[str, Any]:
    """Read a script's Python source code from disk."""
    cfg = _find_script_config(script_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' not found")
    scripts_dir = _get_scripts_dir()
    path = _safe_script_path(scripts_dir, cfg["file"])
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Script file not found: {cfg['file']}")
    source = path.read_text(encoding="utf-8")
    return {"id": script_id, "file": cfg["file"], "source": source}


@router.put("/scripts/{script_id}/source")
async def save_script_source(script_id: str, request: Request) -> dict[str, Any]:
    """Save a script's Python source code to disk."""
    cfg = _find_script_config(script_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' not found")
    body = await request.json()
    source = body.get("source", "")
    if len(source) > 1_000_000:
        raise HTTPException(status_code=413, detail="Script source too large (max 1 MB)")
    scripts_dir = _get_scripts_dir()
    path = _safe_script_path(scripts_dir, cfg["file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return {"status": "saved"}


@router.get("/scripts/references")
async def get_script_references() -> dict[str, Any]:
    """Scan all script source files for state key references."""
    engine = _get_engine()
    if not engine.project:
        return {"references": []}

    scripts_dir = _get_scripts_dir()
    references: list[dict[str, Any]] = []

    patterns = [
        (re.compile(r'state\.get\(\s*["\']((device|var|ui|system|isc)\.[^"\']+?)["\']'), "read"),
        (re.compile(r'state\.set\(\s*["\']((device|var|ui|system|isc)\.[^"\']+?)["\']'), "write"),
        (re.compile(r'@on_state_change\(\s*["\']((device|var|ui|system|isc)\.[\w.*]+)["\']'), "subscribe"),
        (re.compile(r'state\.get_namespace\(\s*["\']((device|var|ui|system|isc)\.[\w.]*)["\']'), "read"),
    ]

    for script in engine.project.scripts:
        script_data = script.model_dump()
        try:
            path = _safe_script_path(scripts_dir, script_data["file"])
            if not path.exists():
                continue
            source = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue

        for line_num, line in enumerate(source.splitlines(), 1):
            for pattern, usage_type in patterns:
                for match in pattern.finditer(line):
                    references.append({
                        "script_id": script.id,
                        "script_name": script_data.get("file", script.id),
                        "key": match.group(1),
                        "usage_type": usage_type,
                        "line": line_num,
                    })

    return {"references": references}


@router.post("/scripts")
async def create_script(request: Request) -> dict[str, Any]:
    """Create a new script entry and file."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")
    body = await request.json()
    data = ScriptCreateRequest(**body)

    # Check for duplicate ID
    for s in engine.project.scripts:
        if s.id == data.id:
            raise HTTPException(status_code=409, detail=f"Script '{data.id}' already exists")

    # Write the script file
    scripts_dir = _get_scripts_dir()
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = _safe_script_path(scripts_dir, data.file)
    path.write_text(data.source, encoding="utf-8")

    # Add to project config and save
    from server.core.project_loader import ScriptConfig
    new_script = ScriptConfig(
        id=data.id,
        file=data.file,
        enabled=data.enabled,
        description=data.description,
    )
    engine.project.scripts.append(new_script)
    save_project(engine.project_path, engine.project)
    await engine.reload_project()
    return {"status": "created", "id": data.id}


@router.delete("/scripts/{script_id}")
async def delete_script(script_id: str) -> dict[str, Any]:
    """Remove a script entry and delete its file."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    cfg = _find_script_config(script_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' not found")

    # Delete the file
    scripts_dir = _get_scripts_dir()
    path = _safe_script_path(scripts_dir, cfg["file"])
    if path.exists():
        path.unlink()

    # Remove from project config and save
    engine.project.scripts = [s for s in engine.project.scripts if s.id != script_id]
    save_project(engine.project_path, engine.project)
    await engine.reload_project()
    return {"status": "deleted"}


@router.post("/scripts/reload")
async def reload_scripts() -> dict[str, Any]:
    """Hot-reload all scripts."""
    engine = _get_engine()
    if not engine.scripts:
        raise HTTPException(status_code=503, detail="Script engine not initialized")
    scripts_data = [s.model_dump() for s in engine.project.scripts] if engine.project else []
    count = engine.scripts.reload_scripts(scripts_data)
    errors = engine.scripts.get_load_errors()
    return {"status": "reloaded", "handlers": count, "errors": errors}


@router.get("/scripts/errors")
async def get_script_errors() -> dict[str, str]:
    """Return load errors for scripts that failed to load."""
    engine = _get_engine()
    if not engine.scripts:
        return {}
    return engine.scripts.get_load_errors()
