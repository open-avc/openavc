"""
OpenAVC Theme API.

Manages built-in and custom panel themes. Themes are JSON files that define
CSS variables and default element styles for the panel UI.
"""

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from server.api.auth import require_programmer_auth
from server.system_config import THEMES_DIR as BUILTIN_THEMES_DIR
from server.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_programmer_auth)])

_engine = None

THEME_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")

REQUIRED_FIELDS = {"name", "id", "version", "variables"}


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def _get_engine():
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not started")
    return _engine


def _custom_themes_dir() -> Path:
    """Get the custom themes directory for the current project."""
    engine = _get_engine()
    project_dir = Path(engine.project_path).parent
    themes_dir = project_dir / "themes"
    themes_dir.mkdir(parents=True, exist_ok=True)
    return themes_dir


def _load_theme(path: Path) -> dict[str, Any]:
    """Load and parse a theme JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def _list_all_themes() -> list[dict[str, Any]]:
    """List all available themes (built-in + custom)."""
    themes = []

    # Built-in themes
    if BUILTIN_THEMES_DIR.exists():
        for f in sorted(BUILTIN_THEMES_DIR.glob("*.json")):
            try:
                theme = _load_theme(f)
                theme["_source"] = "builtin"
                themes.append(theme)
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"Failed to load built-in theme {f.name}: {e}")

    # Custom themes
    custom_dir = _custom_themes_dir()
    for f in sorted(custom_dir.glob("*.json")):
        try:
            theme = _load_theme(f)
            theme["_source"] = "custom"
            themes.append(theme)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to load custom theme {f.name}: {e}")

    return themes


def _validate_theme(data: dict) -> None:
    """Validate theme data has required fields."""
    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing)}")
    if not THEME_ID_PATTERN.match(data["id"]):
        raise HTTPException(status_code=400, detail="Theme ID must be lowercase alphanumeric with hyphens")


# --- Endpoints ---


@router.get("/themes")
async def list_themes() -> list[dict[str, Any]]:
    """List all available themes (built-in + project custom)."""
    themes = _list_all_themes()
    # Return summary without element_defaults for list view
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "version": t.get("version", "1.0.0"),
            "author": t.get("author", ""),
            "description": t.get("description", ""),
            "preview_colors": t.get("preview_colors", []),
            "source": t.get("_source", "custom"),
        }
        for t in themes
    ]


@router.get("/themes/{theme_id}")
async def get_theme(theme_id: str) -> dict[str, Any]:
    """Get full theme definition."""
    # Check built-in first
    builtin_path = BUILTIN_THEMES_DIR / f"{theme_id}.json"
    if builtin_path.exists():
        theme = _load_theme(builtin_path)
        theme["_source"] = "builtin"
        return theme

    # Check custom
    custom_path = _custom_themes_dir() / f"{theme_id}.json"
    if custom_path.exists():
        theme = _load_theme(custom_path)
        theme["_source"] = "custom"
        return theme

    raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")


@router.post("/themes")
async def create_theme(data: dict[str, Any]) -> dict[str, Any]:
    """Create a new custom theme."""
    _validate_theme(data)
    theme_id = data["id"]

    # Don't allow overwriting built-in themes
    if (BUILTIN_THEMES_DIR / f"{theme_id}.json").exists():
        raise HTTPException(status_code=409, detail=f"Cannot overwrite built-in theme '{theme_id}'")

    custom_path = _custom_themes_dir() / f"{theme_id}.json"
    if custom_path.exists():
        raise HTTPException(status_code=409, detail=f"Custom theme '{theme_id}' already exists")

    custom_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    log.info(f"Created custom theme: {theme_id}")
    return {"status": "created", "id": theme_id}


@router.put("/themes/{theme_id}")
async def update_theme(theme_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Update a custom theme."""
    if (BUILTIN_THEMES_DIR / f"{theme_id}.json").exists():
        raise HTTPException(status_code=403, detail="Cannot modify built-in themes")

    custom_path = _custom_themes_dir() / f"{theme_id}.json"
    if not custom_path.exists():
        raise HTTPException(status_code=404, detail=f"Custom theme '{theme_id}' not found")

    _validate_theme(data)
    data["id"] = theme_id  # Prevent ID change via PUT
    custom_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    log.info(f"Updated custom theme: {theme_id}")
    return {"status": "updated", "id": theme_id}


@router.delete("/themes/{theme_id}")
async def delete_theme(theme_id: str) -> dict[str, str]:
    """Delete a custom theme. Built-in themes cannot be deleted."""
    if (BUILTIN_THEMES_DIR / f"{theme_id}.json").exists():
        raise HTTPException(status_code=403, detail="Cannot delete built-in themes")

    custom_path = _custom_themes_dir() / f"{theme_id}.json"
    if not custom_path.exists():
        raise HTTPException(status_code=404, detail=f"Custom theme '{theme_id}' not found")

    custom_path.unlink()
    log.info(f"Deleted custom theme: {theme_id}")
    return {"status": "deleted", "id": theme_id}


@router.get("/themes/{theme_id}/export")
async def export_theme(theme_id: str):
    """Download theme as .avctheme file (JSON)."""
    # Try built-in then custom
    for base in [BUILTIN_THEMES_DIR, _custom_themes_dir()]:
        path = base / f"{theme_id}.json"
        if path.exists():
            theme = _load_theme(path)
            # Remove internal fields
            theme.pop("_source", None)
            return JSONResponse(
                content=theme,
                headers={
                    "Content-Disposition": f'attachment; filename="{theme_id}.avctheme"',
                },
            )
    raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")


@router.post("/themes/import")
async def import_theme(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload and import a .avctheme file."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    content = await file.read()
    if len(content) > 1024 * 1024:  # 1 MB limit
        raise HTTPException(status_code=400, detail="Theme file too large (max 1 MB)")

    try:
        data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    _validate_theme(data)
    theme_id = data["id"]

    if (BUILTIN_THEMES_DIR / f"{theme_id}.json").exists():
        raise HTTPException(status_code=409, detail=f"Cannot overwrite built-in theme '{theme_id}'")

    custom_path = _custom_themes_dir() / f"{theme_id}.json"
    custom_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    log.info(f"Imported theme: {theme_id}")
    return {"status": "imported", "id": theme_id, "name": data.get("name", "")}
