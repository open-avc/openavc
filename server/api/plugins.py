"""
REST API endpoints for plugin management.

All endpoints require programmer auth (same as device management).
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from server.api.auth import require_programmer_auth
from server.api.errors import api_error as _api_error
from server.core.project_loader import (
    PluginConfig,
    build_default_plugin_config,
    get_plugin_setup_fields,
    save_project,
)
from server.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_programmer_auth)])

_engine = None


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def _get_engine():
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not started")
    return _engine



# ──── List / Detail ────


@router.get("/plugins")
async def list_plugins() -> list[dict[str, Any]]:
    """List all plugins (installed, missing, incompatible) with status."""
    engine = _get_engine()
    return engine.plugin_loader.list_plugins()


# ──── Browse / Install / Uninstall ────
# These static paths MUST be defined before /plugins/{plugin_id}
# so FastAPI doesn't match "browse", "installed", "extensions" as a plugin_id.


@router.get("/plugins/browse")
async def browse_plugins() -> dict[str, Any]:
    """Fetch community plugin catalog from repository."""
    from server.core.plugin_installer import get_community_plugins

    plugins, error = await get_community_plugins()
    return {"plugins": plugins, "error": error}


@router.get("/plugins/installed")
async def list_installed() -> dict[str, Any]:
    """List all installed plugins in plugin_repo/."""
    from server.core.plugin_installer import list_installed_plugins

    return {"plugins": list_installed_plugins()}


@router.get("/plugins/extensions")
async def get_all_extensions() -> dict[str, Any]:
    """Get all UI extensions from running plugins."""
    engine = _get_engine()
    return engine.plugin_loader.get_all_extensions()


# ──── Plugin Panel Files (serve HTML/JS/CSS for iframe-based panel elements) ────


@router.get("/plugins/{plugin_id}/panel/{file_path:path}")
async def serve_plugin_panel_file(plugin_id: str, file_path: str):
    """Serve static files from a plugin's panel/ directory for iframe rendering."""
    from server.config import get_config

    config = get_config()
    plugin_dir = Path(config.plugin_repo_path) / plugin_id / "panel"
    resolved = (plugin_dir / file_path).resolve()

    # Security: prevent path traversal and symlink escape
    try:
        resolved.relative_to(plugin_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if resolved.is_symlink():
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Determine content type
    suffix = resolved.suffix.lower()
    content_types = {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
    }
    media_type = content_types.get(suffix, "application/octet-stream")
    return FileResponse(resolved, media_type=media_type)


# ──── Detail (dynamic path — must come after all static /plugins/* routes) ────


@router.get("/plugins/{plugin_id}")
async def get_plugin(plugin_id: str) -> dict[str, Any]:
    """Get detailed plugin info."""
    engine = _get_engine()
    info = engine.plugin_loader.get_plugin_info(plugin_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")
    return info


# ──── Enable / Disable ────


@router.post("/plugins/{plugin_id}/enable")
async def enable_plugin(plugin_id: str) -> dict[str, Any]:
    """Enable a plugin. Builds default config if first time."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

    plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
    if plugin_class is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not installed")

    # Check if already in project
    if plugin_id not in engine.project.plugins:
        # First time — build default config from schema
        schema = getattr(plugin_class, "CONFIG_SCHEMA", {}) or {}
        default_config = build_default_plugin_config(schema)
        engine.project.plugins[plugin_id] = PluginConfig(
            enabled=True,
            config=default_config,
        )
    else:
        engine.project.plugins[plugin_id].enabled = True

    # Start first, only persist to disk if start succeeds
    config = engine.project.plugins[plugin_id].config
    success = await engine.plugin_loader.start_plugin(plugin_id, config)

    if not success:
        # Roll back enabled flag so it won't retry on next restart
        engine.project.plugins[plugin_id].enabled = False

    save_project(engine.project_path, engine.project)

    return {
        "status": "enabled" if success else "error",
        "plugin_id": plugin_id,
        "config": config,
    }


@router.post("/plugins/{plugin_id}/disable")
async def disable_plugin(plugin_id: str) -> dict[str, Any]:
    """Disable a plugin (config preserved)."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    if plugin_id not in engine.project.plugins:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not in project")

    engine.project.plugins[plugin_id].enabled = False
    save_project(engine.project_path, engine.project)
    await engine.plugin_loader.stop_plugin(plugin_id)

    return {"status": "disabled", "plugin_id": plugin_id}


# ──── Configuration ────


@router.get("/plugins/{plugin_id}/config")
async def get_plugin_config(plugin_id: str) -> dict[str, Any]:
    """Get plugin configuration."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    entry = engine.project.plugins.get(plugin_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not in project")

    return {"plugin_id": plugin_id, "config": entry.config}


@router.put("/plugins/{plugin_id}/config")
async def update_plugin_config(plugin_id: str, request: Request) -> dict[str, Any]:
    """Update plugin configuration. Restarts plugin if running."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    if plugin_id not in engine.project.plugins:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not in project")

    new_config = await request.json()

    engine.project.plugins[plugin_id].config = new_config
    save_project(engine.project_path, engine.project)

    # Restart if running
    if plugin_id in engine.plugin_loader._instances:
        await engine.plugin_loader.stop_plugin(plugin_id)
        await engine.plugin_loader.start_plugin(plugin_id, new_config)

    return {"status": "updated", "plugin_id": plugin_id}


# ──── Health ────


@router.get("/plugins/{plugin_id}/health")
async def get_plugin_health(plugin_id: str) -> dict[str, Any]:
    """Get plugin health check result."""
    engine = _get_engine()
    return await engine.plugin_loader.get_health(plugin_id)


# ──── Activate After Install ────


@router.post("/plugins/{plugin_id}/activate")
async def activate_plugin(plugin_id: str) -> dict[str, Any]:
    """Activate a previously-missing plugin after install."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    # Re-scan to pick up newly installed plugins
    engine.plugin_loader.scan_plugins()

    # Get saved config
    entry = engine.project.plugins.get(plugin_id)
    config = entry.config if entry else {}

    result = await engine.plugin_loader.activate_plugin(plugin_id, config)
    return result


# ──── Validation ────


@router.get("/project/validate-plugins")
async def validate_plugins() -> dict[str, Any]:
    """Return available/missing/incompatible plugins."""
    engine = _get_engine()
    if not engine.project:
        raise HTTPException(status_code=503, detail="No project loaded")

    plugins_dict = {
        pid: pc.model_dump() if hasattr(pc, "model_dump") else pc
        for pid, pc in engine.project.plugins.items()
    }
    return engine.plugin_loader.validate_plugins(plugins_dict)


# ──── Setup Fields ────


@router.get("/plugins/{plugin_id}/setup-fields")
async def get_setup_fields(plugin_id: str) -> dict[str, Any]:
    """Return required config fields without defaults (for setup dialog)."""
    from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

    plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
    if plugin_class is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not installed")

    schema = getattr(plugin_class, "CONFIG_SCHEMA", {}) or {}
    setup_fields = get_plugin_setup_fields(schema)

    return {
        "plugin_id": plugin_id,
        "setup_required": len(setup_fields) > 0,
        "fields": setup_fields,
    }


@router.post("/plugins/{plugin_id}/context-action/{action_id}")
async def emit_context_action(plugin_id: str, action_id: str, request: Request) -> dict[str, Any]:
    """Emit a context action event for a plugin."""
    engine = _get_engine()

    # Build payload from request body (if any)
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Emit as plugin event: plugin.<id>.action.<action_id>
    event = f"plugin.{plugin_id}.action.{action_id}"
    await engine.events.emit(event, payload)

    return {"status": "emitted", "event": event}


@router.post("/plugins/{plugin_id}/install")
async def install_plugin_endpoint(plugin_id: str, request: Request) -> dict[str, Any]:
    """Install a plugin from the community repository."""
    from server.core.plugin_installer import install_plugin

    body = await request.json()
    file_url = body.get("file_url")
    if not file_url:
        raise HTTPException(status_code=422, detail="file_url is required")

    try:
        result = await install_plugin(plugin_id, file_url)
        return result
    except ValueError as e:
        raise _api_error(409, f"Plugin '{plugin_id}' is already installed", e)
    except Exception as e:
        raise _api_error(500, f"Failed to install plugin '{plugin_id}'", e)


@router.post("/plugins/{plugin_id}/update")
async def update_plugin_endpoint(plugin_id: str, request: Request) -> dict[str, Any]:
    """Update an installed plugin to a newer version, preserving config."""
    from server.core.plugin_installer import update_plugin

    body = await request.json()
    file_url = body.get("file_url")
    if not file_url:
        raise HTTPException(status_code=422, detail="file_url is required")

    engine = _get_engine()

    try:
        # Stop plugin if running (must happen before files are deleted)
        was_running = plugin_id in engine.plugin_loader._instances
        if was_running:
            await engine.plugin_loader.stop_plugin(plugin_id)

        # Update files (rmtree + reinstall) — project config is untouched
        result = await update_plugin(plugin_id, file_url)

        # Restart with existing config if it was running
        restarted = False
        if was_running and engine.project and plugin_id in engine.project.plugins:
            config = engine.project.plugins[plugin_id].config
            try:
                restarted = await engine.plugin_loader.start_plugin(plugin_id, config)
            except Exception as e:
                log.warning(f"Plugin '{plugin_id}' updated but failed to restart: {e}")

        result["restarted"] = restarted
        return result
    except ValueError as e:
        raise _api_error(404, f"Plugin '{plugin_id}' not found", e)
    except Exception as e:
        raise _api_error(500, f"Failed to update plugin '{plugin_id}'", e)


@router.delete("/plugins/{plugin_id}")
async def uninstall_plugin_endpoint(plugin_id: str) -> dict[str, Any]:
    """Uninstall a plugin and remove it from the project."""
    from server.core.plugin_installer import uninstall_plugin

    engine = _get_engine()
    project_plugins = engine.project.plugins if engine.project else None

    try:
        # Stop plugin if running
        if plugin_id in engine.plugin_loader._instances:
            await engine.plugin_loader.stop_plugin(plugin_id)

        result = await uninstall_plugin(plugin_id, project_plugins)

        # Remove from project file so it doesn't show as "missing" on restart
        if engine.project and plugin_id in engine.project.plugins:
            del engine.project.plugins[plugin_id]
            # Clean up plugin_dependencies too
            engine.project.plugin_dependencies = [
                d for d in engine.project.plugin_dependencies
                if d.plugin_id != plugin_id
            ]
            save_project(engine.project_path, engine.project)

        # Clear missing plugin state if tracked
        if plugin_id in engine.plugin_loader._missing_plugins:
            del engine.plugin_loader._missing_plugins[plugin_id]

        return result
    except ValueError as e:
        raise _api_error(422, f"Failed to uninstall plugin '{plugin_id}'", e)
    except Exception as e:
        raise _api_error(500, f"Failed to uninstall plugin '{plugin_id}'", e)
