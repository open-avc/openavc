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

from server.api.auth import require_programmer_auth
from server.utils.logger import get_logger

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
    """Serve an asset file (with caching headers). No auth required for panel access."""
    assets_dir = _assets_dir()
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
    """List all assets in the project."""
    assets_dir = _assets_dir()
    assets = _list_assets_metadata(assets_dir)
    return {"assets": assets, "total_size": _get_total_size(assets_dir)}


@router.post("/projects/{project_id}/assets")
async def upload_asset(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload an asset file. Returns the asset reference."""
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


@router.delete("/projects/{project_id}/assets/{filename}")
async def delete_asset(project_id: str, filename: str) -> dict[str, str]:
    """Delete an asset file."""
    safe_name = _sanitize_filename(filename)
    assets_dir = _assets_dir()
    path = assets_dir / safe_name

    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found")

    path.unlink()
    log.info(f"Asset deleted: {safe_name}")
    publish_assets_state(_get_engine())
    return {"status": "deleted", "name": safe_name}
