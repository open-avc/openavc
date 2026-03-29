"""
OpenAVC Asset Management API.

Handles uploading, listing, serving, and deleting project assets
(images, icons, backgrounds) used by the panel UI.
"""

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

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}
ALLOWED_MIME_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    "image/x-icon", "image/vnd.microsoft.icon",
}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB per file
MAX_TOTAL_SIZE = 50 * 1024 * 1024  # 50 MB per project
FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-][a-zA-Z0-9_\-. ]*\.[a-zA-Z0-9]+$")

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
    assets = []
    for f in sorted(assets_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
            assets.append({
                "name": f.name,
                "size": f.stat().st_size,
                "type": f.suffix.lower().lstrip("."),
            })
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

    # Read content
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(content)} bytes). Maximum: {MAX_FILE_SIZE} bytes.",
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

    return {
        "name": filename,
        "reference": f"assets://{filename}",
        "size": len(content),
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
    return {"status": "deleted", "name": safe_name}
