"""
OpenAVC Asset Management API.

Handles uploading, listing, serving, and deleting project assets
(images, icons, backgrounds, audio) used by the panel UI and plugins.
"""

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from openavc.api.auth import require_programmer_auth
from openavc.core.asset_references import asset_users
from openavc.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_programmer_auth)])
# Open router for serving assets to the panel (no auth required)
open_router = APIRouter(prefix="/api")

_engine = None

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a"}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS

IMAGE_MIME_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    "image/x-icon", "image/vnd.microsoft.icon",
}
AUDIO_MIME_TYPES = {
    "audio/mpeg", "audio/mp3",
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/ogg", "audio/vorbis",
    "audio/mp4", "audio/x-m4a", "audio/aac",
}
ALLOWED_MIME_TYPES = IMAGE_MIME_TYPES | AUDIO_MIME_TYPES

MAX_IMAGE_SIZE = 50 * 1024 * 1024     # 50 MB per image
MAX_AUDIO_SIZE = 200 * 1024 * 1024    # 200 MB per audio file
MAX_TOTAL_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB per project (shared across types)
FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-][a-zA-Z0-9_\-. ]*\.[a-zA-Z0-9]+$")


def _asset_type(ext: str) -> str:
    """Classify an extension as 'image' or 'audio'. Caller must pre-validate ext."""
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "image"


def _max_size_for(ext: str) -> int:
    """Per-file size limit for the given extension."""
    if ext in AUDIO_EXTENSIONS:
        return MAX_AUDIO_SIZE
    return MAX_IMAGE_SIZE

# SVG sanitization: reject SVGs containing dangerous elements or attributes
SVG_DANGEROUS_PATTERNS = [
    re.compile(r"<script[\s>]", re.IGNORECASE),
    re.compile(r"on\w+\s*=", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"<foreignObject[\s>]", re.IGNORECASE),
    re.compile(r"<iframe[\s>]", re.IGNORECASE),
    re.compile(r"@import", re.IGNORECASE),
    re.compile(r"<animate[^>]*attributeName\s*=\s*[\"']href", re.IGNORECASE),
    re.compile(r"data:text/html", re.IGNORECASE),
    re.compile(r"<style[\s>]", re.IGNORECASE),
    re.compile(r"<base[\s>]", re.IGNORECASE),
    re.compile(r"<embed[\s>]", re.IGNORECASE),
    re.compile(r"<object[\s>]", re.IGNORECASE),
    re.compile(r"xlink:href\s*=\s*[\"'](?!#)", re.IGNORECASE),
]


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def _get_engine():
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not started")
    return _engine


def _assets_dir() -> Path:
    """Get the assets directory for the current project."""
    engine = _get_engine()
    project_dir = Path(engine.project_path).parent
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    return assets_dir


def _assets_dir_for(project_id: str) -> Path:
    """Resolve the assets directory addressed by a ``project_id`` URL segment.

    The panel and IDE address the live project as ``default`` (and some older
    links omit it); that maps to the active project's assets dir — the
    long-standing behavior. Any other id names a saved library project and
    resolves to *its* assets dir, so a library-project asset URL serves that
    project's bytes instead of silently returning the active project's. The
    dir may legitimately not exist (a library project with no assets); the
    caller's existence check then yields a clean 404.
    """
    if project_id in ("", "default"):
        return _assets_dir()
    from openavc.core.project_library import _lib_dir, sanitize_id
    return _lib_dir() / sanitize_id(project_id) / "assets"


def _require_active_project(project_id: str) -> None:
    """Guard the write endpoints (upload/delete) to the active project.

    ``default`` (or empty) addresses the active project — the one being
    edited — and is the only id these mutating routes accept. Any other id
    names a saved library project, which this surface deliberately does not
    mutate (there's no editing UI for a library project's assets). Reject it
    explicitly with a 404 rather than silently writing to the active project:
    a mis-addressed write must fail loudly, not land on the wrong project.
    """
    if project_id not in ("", "default"):
        raise HTTPException(
            status_code=404,
            detail="Assets can only be modified on the active project.",
        )


def _list_assets_metadata(assets_dir: Path) -> list[dict[str, Any]]:
    """Build the asset metadata list shared between the API and state publishing."""
    out: list[dict[str, Any]] = []
    for f in sorted(assets_dir.iterdir()):
        ext = f.suffix.lower()
        if f.is_file() and ext in ALLOWED_EXTENSIONS:
            out.append({
                "name": f.name,
                "size": f.stat().st_size,
                "extension": ext.lstrip("."),
                "type": _asset_type(ext),
            })
    return out


def publish_assets_state(engine) -> None:
    """Republish the project's asset catalog to the `project.assets` state key.

    The value is a JSON-encoded list of ``{name, size, extension, type}``
    objects — one per asset. State values must be flat primitives, hence
    the JSON serialization. Plugins (e.g. Audio Player) subscribe to this
    key so they can pick up newly-uploaded assets without polling.

    Called at engine startup and after every upload/delete.
    """
    try:
        project_dir = Path(engine.project_path).parent
        assets_dir = project_dir / "assets"
        if assets_dir.is_dir():
            metadata = _list_assets_metadata(assets_dir)
        else:
            metadata = []
        engine.state.set("project.assets", json.dumps(metadata), source="system")
    except Exception:  # Catch-all: never block uploads on telemetry
        log.exception("Failed to publish project.assets state")


def _sanitize_filename(raw: str) -> str:
    """Sanitize filename: strip path components, validate characters."""
    # Strip any path components (prevent directory traversal)
    name = Path(raw).name
    if not name or not FILENAME_PATTERN.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filename: {raw!r}. Use alphanumeric, hyphens, underscores, dots.",
        )
    return name


def _validate_svg(content: bytes) -> None:
    """Check SVG content for dangerous patterns and well-formedness."""
    # First verify it's valid XML (catches billion-laughs, entity expansion, etc.)
    try:
        import defusedxml.ElementTree as ET
        ET.fromstring(content)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="SVG is not valid XML.",
        )

    # Then check for dangerous patterns in the raw text
    text = content.decode("utf-8", errors="replace")
    for pattern in SVG_DANGEROUS_PATTERNS:
        if pattern.search(text):
            raise HTTPException(
                status_code=400,
                detail="SVG contains potentially unsafe content (scripts, styles, or event handlers).",
            )


def _get_total_size(assets_dir: Path) -> int:
    """Calculate total size of all assets."""
    return sum(f.stat().st_size for f in assets_dir.iterdir() if f.is_file())


# --- Endpoints ---


@open_router.get("/projects/{project_id}/assets/{filename:path}")
async def serve_asset(project_id: str, filename: str):
    """Serve an asset file (with caching headers). No auth required for panel access.

    Honors ``project_id``: ``default`` serves the active project, any other id
    serves that saved library project's assets. (The authenticated ``list``
    endpoint below honors the id the same way; the mutating ``upload``/
    ``delete`` endpoints act only on the active project and reject any other id,
    so library templates aren't mutated through this surface.)
    """
    assets_dir = _assets_dir_for(project_id)
    # Prevent directory traversal
    safe_path = (assets_dir / filename).resolve()
    try:
        safe_path.relative_to(assets_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not safe_path.exists() or not safe_path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")

    return FileResponse(
        safe_path,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/projects/{project_id}/assets")
async def list_assets(project_id: str) -> dict[str, Any]:
    """List all assets for the addressed project.

    ``default`` lists the active project; any other id lists that saved
    library project's assets. A project with no assets directory yields an
    empty list rather than an error.

    Each entry for the ACTIVE project also carries ``used_by``: who still shows
    it, in the words the delete refuses in, so the Asset Browser can say what a
    file is for before somebody presses delete rather than after. A library
    project is not loaded, so nothing can be said about what uses its assets
    and the key is left off entirely -- an empty list there would read as "used
    by nothing", which is a different claim.
    """
    assets_dir = _assets_dir_for(project_id)
    if not assets_dir.is_dir():
        return {"assets": [], "total_size": 0}
    assets = _list_assets_metadata(assets_dir)
    engine = _get_engine()
    project = getattr(engine, "project", None) if engine else None
    if project is not None and project_id in ("", "default"):
        themes = _installed_themes()
        for asset in assets:
            asset["used_by"] = asset_users(project, asset["name"], themes=themes)
    return {"assets": assets, "total_size": _get_total_size(assets_dir)}


@router.post("/projects/{project_id}/assets")
async def upload_asset(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload an asset file. Returns the asset reference."""
    _require_active_project(project_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    filename = _sanitize_filename(file.filename)
    ext = Path(filename).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type {ext} not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    max_size = _max_size_for(ext)

    # Read content
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(content)} bytes). Maximum for {ext} files: {max_size} bytes.",
        )

    assets_dir = _assets_dir()
    total = _get_total_size(assets_dir)
    if total + len(content) > MAX_TOTAL_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Project asset quota exceeded. Total: {total} bytes, limit: {MAX_TOTAL_SIZE} bytes.",
        )

    # SVG sanitization
    if ext == ".svg":
        _validate_svg(content)

    # Write file
    dest = assets_dir / filename
    dest.write_bytes(content)
    log.info(f"Asset uploaded: {filename} ({len(content)} bytes)")
    publish_assets_state(_get_engine())

    return {
        "name": filename,
        "reference": f"assets://{filename}",
        "size": len(content),
        "type": _asset_type(ext),
    }


def _installed_themes() -> list[dict[str, Any]]:
    """Every theme a panel could be running, built-in and custom alike.

    A theme's ``page_defaults.background_image`` resolves through the same
    ``assets://`` path an element's does, so an asset only a theme names is
    still an asset in use -- and losing it takes the background off every page
    running that theme rather than one. Asked of ``api.themes``' own reader
    rather than re-globbing the two directories here: a second answer to "which
    themes exist" is how one door would come to disagree with the other.
    """
    from openavc.api.themes import _list_all_themes

    try:
        return _list_all_themes()
    except Exception:
        # Deliberately everything, including the HTTPException that reader
        # raises when the themes module has no engine: whether themes can be
        # read is not a reason to refuse a delete or to answer 503 to a
        # listing. The consequence is a narrower answer, not a wrong one.
        log.debug("Themes unavailable for the asset reference check", exc_info=True)
        return []


@router.delete("/projects/{project_id}/assets/{filename}")
async def delete_asset(project_id: str, filename: str) -> dict[str, str]:
    """Delete an asset file.

    A file something still shows is not a file to delete, so this asks the same
    walk the AI's ``delete_asset`` asks (``core/asset_references.asset_users``)
    and refuses with the pages and elements named. The aftermath is the reason
    it refuses rather than warns: unlike a device or a script, a deleted asset
    leaves nothing behind to point somewhere else, and the panel says nothing
    at all -- a background that has gone draws the colour underneath and an
    icon that has gone draws nothing.
    """
    _require_active_project(project_id)
    safe_name = _sanitize_filename(filename)
    assets_dir = _assets_dir()
    path = assets_dir / safe_name

    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found")

    # Asked after the file is known to exist, so an element pointing at a name
    # that is already gone answers "not found" here and at the AI's door alike.
    engine = _get_engine()
    project = getattr(engine, "project", None) if engine else None
    if project is not None:
        still_shown = asset_users(project, safe_name, themes=_installed_themes())
        if still_shown:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{safe_name}' is still shown by {', '.join(still_shown)}. "
                    f"Point them at another file, or delete them first, "
                    f"then this asset can go."
                ),
            )

    path.unlink()
    log.info(f"Asset deleted: {safe_name}")
    publish_assets_state(_get_engine())
    return {"status": "deleted", "name": safe_name}
