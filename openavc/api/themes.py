"""
OpenAVC Theme API.

The REST doors onto the panel themes: list, read, create, update, delete,
export and import. Which themes there are, where each is looked up and what a
theme file may hold are ``openavc/core/themes.py``'s; a refusal there is a 400
with its sentence.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from openavc.api.host_policy import ProgrammerRoute, RenderRoute
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse

from openavc.api.auth import require_programmer_auth
from openavc.api.errors import StructuredApiError
from openavc.core import file_news, themes
from openavc.utils.fileio import atomic_write_text
from openavc.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(route_class=ProgrammerRoute, prefix="/api", dependencies=[Depends(require_programmer_auth)])
# Open router (no auth): the panel runtime fetches a single theme by id to apply
# it before the first render. The room panel holds no programmer credential, so a
# protected GET returns 401 WWW-Authenticate: Basic to a standalone panel, which
# makes the browser pop its native HTTP Basic dialog (an unfillable
# username/password prompt). Theme data is cosmetic CSS variables — the same
# security shape as /api/projects/{id}/assets/* (also open). Only the
# single-theme GET is open; the theme list and all mutations stay protected.
open_router = APIRouter(route_class=RenderRoute, prefix="/api")

_engine = None


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def _get_engine():
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not started")
    return _engine


def _custom_themes_dir() -> Path:
    """The custom themes folder of the project the engine runs."""
    return themes.project_themes_dir(_get_engine().project_path)


@contextmanager
def _as_bad_request() -> Iterator[None]:
    """A theme the rule refuses is a 400 with the rule's sentence."""
    try:
        yield
    except themes.ThemeRefused as refused:
        raise HTTPException(status_code=400, detail=str(refused)) from None


def _theme_file(base: Path, theme_id: str) -> Path:
    with _as_bad_request():
        return themes.theme_file(base, theme_id)


def _find(theme_id: str) -> dict[str, Any]:
    with _as_bad_request():
        theme = themes.find_theme(theme_id, _custom_themes_dir)
    if theme is None:
        raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")
    return theme


def _check(data: Any) -> None:
    with _as_bad_request():
        themes.check_theme(data)


def _write(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=4, ensure_ascii=False))


# --- Endpoints ---


@router.get("/themes")
async def list_themes() -> dict[str, Any]:
    """List all available themes (built-in + project custom).

    Includes the full `variables` map so the Theme Studio picker can render
    rich per-card previews (real fonts, button colors, surface tones) without
    fetching each theme individually. Element_defaults are still excluded —
    they're only needed when actually editing a theme via the per-element
    sections.
    """
    found = themes.all_themes(_custom_themes_dir())
    return {"themes": [
        {
            "id": t["id"],
            "name": t["name"],
            "version": t.get("version", "1.0.0"),
            "author": t.get("author", ""),
            "description": t.get("description", ""),
            "preview_colors": t.get("preview_colors", []),
            "variables": t.get("variables", {}),
            "source": t.get("_source", "custom"),
            "mode": themes.theme_mode(t),
        }
        for t in found
    ]}


@open_router.get("/themes/{theme_id}")
async def get_theme(theme_id: str) -> dict[str, Any]:
    """Get full theme definition. Open (no auth) so the unauthenticated panel
    can load and apply the project's theme without triggering the browser's
    native HTTP Basic dialog."""
    return _find(theme_id)


@router.post("/themes")
async def create_theme(data: dict[str, Any]) -> dict[str, Any]:
    """Create a new custom theme."""
    _check(data)
    theme_id = data["id"]

    # Don't allow overwriting built-in themes
    if themes.is_builtin(theme_id):
        raise HTTPException(status_code=409, detail=f"Cannot overwrite built-in theme '{theme_id}'")

    async with _get_engine().holding_project_files():
        custom_path = _theme_file(_custom_themes_dir(), theme_id)
        if custom_path.exists():
            raise HTTPException(status_code=409, detail=f"Custom theme '{theme_id}' already exists")
        _write(custom_path, data)
    log.info(f"Created custom theme: {theme_id}")
    await file_news.themes_changed(_get_engine(), theme_id)
    return {"status": "created", "theme_id": theme_id, "mode": themes.theme_mode(data)}


@router.put("/themes/{theme_id}")
async def update_theme(theme_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Update a custom theme."""
    async with _get_engine().holding_project_files():
        custom_path = _custom_theme_to_change(theme_id, "modify")
        _check(data)
        data["id"] = theme_id  # Prevent ID change via PUT
        _write(custom_path, data)
    log.info(f"Updated custom theme: {theme_id}")
    await file_news.themes_changed(_get_engine(), theme_id)
    return {"status": "updated", "theme_id": theme_id}


@router.delete("/themes/{theme_id}")
async def delete_theme(theme_id: str) -> dict[str, str]:
    """Delete a custom theme. Built-in themes cannot be deleted."""
    async with _get_engine().holding_project_files():
        _custom_theme_to_change(theme_id, "delete").unlink()
    log.info(f"Deleted custom theme: {theme_id}")
    await file_news.themes_changed(_get_engine(), theme_id)
    return {"status": "deleted", "theme_id": theme_id}


def _custom_theme_to_change(theme_id: str, verb: str) -> Path:
    """The custom theme file an update or a delete changes: never a built-in's."""
    with _as_bad_request():
        builtin = themes.is_builtin(theme_id)
    if builtin:
        raise HTTPException(status_code=403, detail=f"Cannot {verb} built-in themes")
    custom_path = _theme_file(_custom_themes_dir(), theme_id)
    if not custom_path.exists():
        raise HTTPException(status_code=404, detail=f"Custom theme '{theme_id}' not found")
    return custom_path


@router.get("/themes/{theme_id}/export")
async def export_theme(theme_id: str):
    """Download theme as .avctheme file (JSON)."""
    theme = _find(theme_id)
    theme.pop("_source", None)
    return JSONResponse(
        content=theme,
        headers={
            "Content-Disposition": f'attachment; filename="{theme_id}.avctheme"',
        },
    )


@router.post("/themes/import")
async def import_theme(
    file: UploadFile = File(...), overwrite: bool = False
) -> dict[str, Any]:
    """Upload and import a .avctheme file.

    Safe by default: importing a theme whose id collides with an existing
    *custom* theme returns 409 (with a machine-readable ``code: theme_exists``)
    rather than silently replacing what the user may have edited. The Theme
    Studio import dialog catches that and offers Overwrite (retry with
    ``?overwrite=true``) or Keep both (re-import under a fresh id). A built-in
    id collision is always refused — built-ins can't be overwritten at all.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    content = await file.read()
    if len(content) > 1024 * 1024:  # 1 MB limit
        raise HTTPException(status_code=400, detail="Theme file too large (max 1 MB)")

    try:
        data = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    _check(data)
    theme_id = data["id"]

    if themes.is_builtin(theme_id):
        raise HTTPException(status_code=409, detail=f"Cannot overwrite built-in theme '{theme_id}'")

    async with _get_engine().holding_project_files():
        custom_path = _theme_file(_custom_themes_dir(), theme_id)
        if custom_path.exists() and not overwrite:
            # The UI turns this one into an "overwrite?" prompt, so it needs the id
            # and name as data — hence the sibling fields beside the readable detail.
            raise StructuredApiError(
                409,
                f"A custom theme with id '{theme_id}' already exists.",
                code="theme_exists",
                theme_id=theme_id,
                name=data.get("name", theme_id),
            )
        _write(custom_path, data)
    log.info(f"Imported theme: {theme_id} (overwrite={overwrite})")
    await file_news.themes_changed(_get_engine(), theme_id)
    return {
        "status": "imported", "theme_id": theme_id, "name": data.get("name", ""),
        "mode": themes.theme_mode(data),
    }
