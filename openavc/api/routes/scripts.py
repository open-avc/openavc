"""Script CRUD and execution REST API endpoints."""

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from openavc.api._engine import _get_engine
from openavc.api.auth import require_claimed_auth
from openavc.api.errors import api_error as _api_error
from openavc.api.models import ScriptCreateRequest
from openavc.core.event_references import dead_listeners, project_script_sources
from openavc.utils.fileio import atomic_write_text
from openavc.utils.paths import is_safe_script_filename, safe_path_within

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
    """Resolve a script filename safely within the scripts directory.

    Scripts are flat ``.py`` files directly under ``scripts/``. Reject anything
    that isn't a bare ``.py`` basename (nested subpath, ``..``, non-.py
    extension) before the containment check, so neither a crafted request nor a
    poisoned stored ``file`` can read or write outside that expected shape.
    """
    if not is_safe_script_filename(filename):
        raise HTTPException(status_code=400, detail="Invalid script filename")
    resolved = safe_path_within(scripts_dir, filename)
    if resolved is None:
        raise HTTPException(status_code=400, detail="Invalid script filename")
    return resolved


@router.get("/scripts/functions")
async def get_script_functions() -> dict[str, Any]:
    """Return all callable functions from loaded scripts."""
    engine = _get_engine()
    if not engine.scripts:
        return {"functions": []}
    return {"functions": engine.scripts.get_callable_functions()}


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
    return {"script_id": script_id, "file": cfg["file"], "source": source}


@router.put("/scripts/{script_id}/source", dependencies=[Depends(require_claimed_auth)])
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
    atomic_write_text(path, source)
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
        except (OSError, ValueError, HTTPException):
            # A malformed/hostile stored `file` shouldn't fail the whole scan.
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


@router.post("/scripts/validate")
async def validate_scripts(body: Any = Body(...)) -> dict[str, Any]:
    """Which handlers in these scripts are waiting for an event nothing sends.

    The script half of the dead-control failure: `@on_event("custom.start")`
    against a project where no macro step, no control action and no other
    script ever emits that name. Nothing raises, nothing is logged, and the
    handler below it can be perfectly correct -- which is exactly why the
    search goes to the projector and the cable instead.

    Takes the sources the editor is holding, unsaved, for the same reason the
    macro door takes unsaved macros: the mark has to describe what is on
    screen. What it does NOT take is the emitting side -- macros, controls and
    every other script come from the saved project and from disk, so asking
    about one open script still gets a true answer. A posted `source` is an
    override of the saved copy; a script named without one is checked as it is
    on disk, which is how the editor gets a mark for every row of the list
    while holding only the file that is open.

    Nothing here blocks and nothing 422s (`core/macro_validation` records that
    policy and this shares it): a handler written before the macro that will
    fire it is a normal state for somebody halfway through building a room.
    """
    engine = _get_engine()
    if not isinstance(body, dict) or not isinstance(body.get("scripts"), list):
        raise _api_error(400, 'Expected {"scripts": [{"id": ..., "source": ...}]}')

    sources = project_script_sources(engine.project, _get_scripts_dir())

    posted: list[str] = []
    for entry in body["scripts"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise _api_error(400, "Every script needs a non-empty 'id'")
        source = entry.get("source")
        # A source is an override, not a requirement. The editor holds one
        # script open and wants a mark on every row of the list beside it, so
        # naming a script without its text means "the copy you have".
        if isinstance(source, str):
            sources[entry["id"]] = source
        posted.append(entry["id"])

    issues = dead_listeners(sources, engine.project)
    return {"scripts": {script_id: {"issues": issues.get(script_id, [])} for script_id in posted}}


@router.post("/scripts", dependencies=[Depends(require_claimed_auth)])
async def create_script(data: ScriptCreateRequest) -> dict[str, Any]:
    """Create a new script entry and file."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    # Check for duplicate ID
    for s in engine.project.scripts:
        if s.id == data.id:
            raise HTTPException(status_code=409, detail=f"Script '{data.id}' already exists")

    # Write the script file
    scripts_dir = _get_scripts_dir()
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = _safe_script_path(scripts_dir, data.file)
    atomic_write_text(path, data.source)

    # Add to the project through the one seam: the scripts diff loads just
    # this script instead of the old full reload (which re-executed every
    # script and re-fired startup triggers). The duplicate check runs again
    # inside the mutate — a create racing this one may have appended the id
    # after the check above.
    from openavc.core.project_loader import ScriptConfig
    new_script = ScriptConfig(
        id=data.id,
        file=data.file,
        enabled=data.enabled,
        description=data.description,
    )

    def mutate(project):
        for s in project.scripts:
            if s.id == data.id:
                raise HTTPException(status_code=409, detail=f"Script '{data.id}' already exists")
        project.scripts.append(new_script)

    await engine.apply_project_edit(mutate)
    return {"status": "created", "script_id": data.id}


@router.delete("/scripts/{script_id}")
async def delete_script(script_id: str) -> dict[str, Any]:
    """Remove a script entry and delete its file."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    # The find/404 and file delete run inside the mutate against the
    # project copy taken under the lock; the scripts diff unloads just
    # this script (handlers, subscriptions, timers) without disturbing
    # the others.
    def mutate(project):
        cfg = next((s for s in project.scripts if s.id == script_id), None)
        if cfg is None:
            raise HTTPException(status_code=404, detail=f"Script '{script_id}' not found")

        # Delete the file
        scripts_dir = _get_scripts_dir()
        path = _safe_script_path(scripts_dir, cfg.file)
        if path.exists():
            path.unlink()

        project.scripts = [s for s in project.scripts if s.id != script_id]

    await engine.apply_project_edit(mutate)
    return {"status": "deleted"}


@router.post("/scripts/reload", dependencies=[Depends(require_claimed_auth)])
async def reload_scripts() -> dict[str, Any]:
    """Hot-reload all scripts."""
    engine = _get_engine()
    if not engine.scripts:
        raise HTTPException(status_code=503, detail="Script engine not initialized")
    scripts_data = [s.model_dump() for s in engine.project.scripts] if engine.project else []
    count = engine.scripts.reload_scripts(scripts_data)
    errors = engine.scripts.get_load_errors()
    return {"status": "reloaded", "handlers": count, "errors": errors}


@router.post("/scripts/{script_id}/reload", dependencies=[Depends(require_claimed_auth)])
async def reload_single_script(script_id: str) -> dict[str, Any]:
    """Hot-reload a single script in isolation.

    Other scripts' handlers and timers keep running (their ``every()`` loops
    don't reset), and if the new version fails to import the previously loaded
    version stays active. Mirrors the Python-driver reload contract.
    """
    engine = _get_engine()
    if not engine.scripts:
        raise HTTPException(status_code=503, detail="Script engine not initialized")
    cfg = _find_script_config(script_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Script '{script_id}' not found")
    result = engine.scripts.reload_script(cfg)
    result["errors"] = engine.scripts.get_load_errors()
    return result


@router.get("/scripts/errors")
async def get_script_errors() -> dict[str, Any]:
    """Return load errors for scripts that failed to load."""
    engine = _get_engine()
    if not engine.scripts:
        return {"errors": {}}
    return {"errors": engine.scripts.get_load_errors()}
