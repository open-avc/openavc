"""
REST API endpoints for plugin management.

Management endpoints require programmer auth (same as device management).
Static plugin assets served for iframe / audio / image loads are on the open
router — see the comment above `open_router` below.
"""

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from server.api.auth import programmer_auth_satisfied, require_programmer_auth
from server.api.errors import api_error as _api_error
from server.core.plugin_config import (
    missing_required_for_plugin,
    validate_config_for_plugin,
)
from server.core.project_loader import (
    PluginConfig,
    build_default_plugin_config,
    get_plugin_setup_fields,
)
from server.utils.logger import get_logger

log = get_logger(__name__)

# Context-action names: plain identifiers, optionally dashed. Everything the
# IDE emits (declared context_actions ids, matrix route/unroute, preset
# actions) matches; event-name splicing does not.
_ACTION_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

router = APIRouter(prefix="/api", dependencies=[Depends(require_programmer_auth)])
# Open router — for static plugin assets (HTML/JS/CSS/images/audio) that the
# panel runtime fetches as iframe src or media src. Browser resource loads
# can't attach Authorization headers, so requiring auth here just produces a
# native HTTP Basic dialog inside the panel iframe. The handler hardens
# against path traversal, blocks symlinks, and serves unknown extensions as
# application/octet-stream, so opening these is the same security shape as
# /api/projects/{id}/assets/* (also open).
open_router = APIRouter(prefix="/api")

# Non-erroring Basic scheme: lets an open-router handler read credentials when
# present without forcing a 401 (which would summon the browser's native dialog
# on an unauthenticated panel).
_basic = HTTPBasic(auto_error=False)

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
# so FastAPI doesn't match "browse", "installed" as a plugin_id.


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


# Open router (no auth): the panel runtime fetches this on every load to learn
# each plugin panel-element's sandbox permissions before the first render. The
# room panel is unauthenticated by design, so requiring auth here returns 401
# WWW-Authenticate: Basic to a standalone panel, which makes the browser pop its
# native HTTP Basic dialog (an unfillable username/password prompt). The payload
# is read-only UI metadata — the same security shape as the plugin panel/files
# assets already on the open router. Registered before the protected
# /plugins/{plugin_id}, and the open router is mounted first, so "extensions" is
# never matched as a plugin_id.
@open_router.get("/plugins/extensions")
async def get_all_extensions() -> dict[str, Any]:
    """Get all UI extensions from running plugins."""
    engine = _get_engine()
    return engine.plugin_loader.get_all_extensions()


@router.get("/plugins/macro-actions")
async def get_all_macro_actions() -> dict[str, Any]:
    """Get all macro actions registered by running plugins.

    Used by the macro builder to populate the action picker with plugin actions.
    """
    engine = _get_engine()
    return {"actions": engine.plugin_loader.get_all_macro_actions()}


@router.get("/plugins/script-api")
async def get_all_script_api() -> dict[str, Any]:
    """Get all SCRIPT_API methods registered by running plugins.

    Used by the script editor to power autocomplete and hover docs for
    `openavc.plugins.<plugin_id>.<method>(...)` calls.
    """
    engine = _get_engine()
    return {"methods": engine.plugin_loader.get_all_script_api()}


# ──── Plugin Static Files ────

# Content types served from plugin directories. Anything not on this list
# is served as application/octet-stream — files plugins use as data, not
# code. Executable types like .py and .sh are deliberately not listed and
# fall through to octet-stream so browsers won't try to execute them.
_PLUGIN_FILE_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".m4s": "video/iso.segment",
    ".mpd": "application/dash+xml",
    ".vtt": "text/vtt",
}


def _serve_plugin_file(plugin_id: str, subdir: str, file_path: str):
    """Shared helper: serve a static file from a plugin directory subtree."""
    from server.config import get_config

    config = get_config()
    base_dir = Path(config.plugin_repo_path) / plugin_id
    if subdir:
        base_dir = base_dir / subdir
    resolved = (base_dir / file_path).resolve()

    # Security: prevent path traversal and symlink escape
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if resolved.is_symlink():
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = _PLUGIN_FILE_CONTENT_TYPES.get(
        resolved.suffix.lower(), "application/octet-stream"
    )
    return FileResponse(resolved, media_type=media_type)


@open_router.get("/plugins/{plugin_id}/panel/{file_path:path}")
async def serve_plugin_panel_file(plugin_id: str, file_path: str):
    """Serve static files from a plugin's panel/ directory for iframe rendering."""
    return _serve_plugin_file(plugin_id, "panel", file_path)


@open_router.get("/plugins/{plugin_id}/files/{file_path:path}")
async def serve_plugin_file(plugin_id: str, file_path: str):
    """Serve any static file from inside a plugin's directory.

    Used for plugin-bundled assets (sound libraries, images, fonts,
    JSON data, etc.) that need to be fetched by the panel runtime.
    Path traversal and symlinks are rejected. Unknown extensions are
    served as application/octet-stream so browsers won't auto-execute
    unexpected file types.
    """
    return _serve_plugin_file(plugin_id, "", file_path)


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

    entry = engine.project.plugins.get(plugin_id)
    if entry is None:
        # First time — build default config from schema
        schema = getattr(plugin_class, "CONFIG_SCHEMA", {}) or {}
        config = build_default_plugin_config(schema)
    else:
        config = entry.config

    # Start first so a failed start persists enabled=False (won't retry on
    # the next restart). The seam's plugin sync then sees runtime == project
    # and does nothing further.
    success = await engine.plugin_loader.start_plugin(plugin_id, config)

    def mutate(project):
        if plugin_id not in project.plugins:
            project.plugins[plugin_id] = PluginConfig(
                enabled=success,
                config=config,
            )
        else:
            project.plugins[plugin_id].enabled = success

    await engine.apply_project_edit(mutate)

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

    def mutate(project):
        if plugin_id not in project.plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not in project")
        project.plugins[plugin_id].enabled = False

    # The plugins-section reconcile stops the running plugin.
    await engine.apply_project_edit(mutate)

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
    if not isinstance(new_config, dict):
        raise HTTPException(status_code=400, detail="Plugin config must be a JSON object")

    # Same CONFIG_SCHEMA validation the cloud AI path applies. Wrong types
    # are rejected; required fields that aren't set yet only warn — the
    # config form saves incrementally during first-time setup, so a
    # partially-filled config must persist (the plugin just can't start
    # until it's complete).
    err = validate_config_for_plugin(plugin_id, new_config)
    if err:
        raise HTTPException(status_code=400, detail=err)
    missing = missing_required_for_plugin(plugin_id, new_config)

    # Hot-apply when the plugin supports it, else restart — before the seam
    # apply, so the reconcile sees the running config already current and
    # doesn't apply it a second time.
    outcome = await engine.plugin_loader.restart_or_apply(plugin_id, new_config)

    def mutate(project):
        if plugin_id not in project.plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not in project")
        project.plugins[plugin_id].config = new_config

    await engine.apply_project_edit(mutate)

    result: dict[str, Any] = {"status": "updated", "plugin_id": plugin_id, "applied": outcome}
    if missing:
        # Structured list so the IDE can tell "setup still in progress"
        # (form already marks required fields) from a real restart failure.
        result["missing_required"] = sorted(missing)
        result["warning"] = (
            f"Config saved, but required field(s) {', '.join(sorted(missing))} "
            f"are not set yet. Plugin '{plugin_id}' can't run until they are."
        )
    elif outcome == "start_failed":
        result["warning"] = (
            f"Config saved, but plugin '{plugin_id}' failed to restart with it "
            f"and is stopped. Check the config values and plugin logs."
        )
    return result


@router.delete("/plugins/{plugin_id}/config")
async def remove_plugin_config(plugin_id: str) -> dict[str, Any]:
    """Remove a plugin's reference (config + enabled flag) from the project.

    This is how a project drops a plugin that isn't installed — the
    missing-plugin banner's "Remove Plugin Config" — without having to
    install it first. Works for installed plugins too: a running plugin is
    stopped before its entry is removed. Plugin files and persistent data
    are untouched; use DELETE /plugins/{plugin_id} to uninstall.
    """
    engine = _get_engine()
    if not engine.project or plugin_id not in engine.project.plugins:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not in project")

    def mutate(project):
        if plugin_id not in project.plugins:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not in project")
        del project.plugins[plugin_id]
        project.plugin_dependencies = [
            d for d in project.plugin_dependencies
            if d.plugin_id != plugin_id
        ]

    try:
        # The plugins-section reconcile stops a running plugin and drops its
        # missing/incompatible tracking + broadcast state keys, and the
        # revision bump 409s an open editor's stale full-project PUT instead
        # of letting it restore the entry.
        await engine.apply_project_edit(mutate)
        return {"status": "removed", "plugin_id": plugin_id}
    except HTTPException:
        raise
    except Exception as e:
        raise _api_error(500, f"Failed to remove plugin config for '{plugin_id}'", e)


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

    # Only plugins in the project can receive action events — an arbitrary
    # id would let one session emit into any plugin namespace it invents.
    if not engine.project or plugin_id not in engine.project.plugins:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not in project")

    # Action names are plain identifiers (declared context_actions, matrix
    # route/unroute, preset actions all match); anything else would splice
    # extra segments into the event name.
    if not _ACTION_ID_RE.match(action_id):
        raise HTTPException(status_code=400, detail=f"Invalid action id '{action_id}'")

    # Build payload from request body (if any). Event payloads are dicts
    # everywhere in the runtime — a non-dict body would make every
    # subscriber (triggers, scripts, the plugin itself) throw instead of run.
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Action payload must be a JSON object")

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
        # Validation failures (bad id, non-catalog URL, unsafe dependency) and
        # the already-installed conflict all raise ValueError; surface the
        # actual reason rather than a blanket "already installed".
        if "already installed" in str(e):
            raise _api_error(409, str(e), e)
        raise _api_error(400, str(e), e)
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
        was_running = engine.plugin_loader.is_running(plugin_id)
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
        raise _api_error(404, str(e), e)
    except Exception as e:
        raise _api_error(500, f"Failed to update plugin '{plugin_id}'", e)


@router.get("/plugins/{plugin_id}/data-info")
async def get_plugin_data_info_endpoint(plugin_id: str) -> dict[str, Any]:
    """Return whether the plugin has a persistent data directory and its size.

    Used by the IDE uninstall dialog to show a "discard X MB of plugin data?"
    prompt with an accurate size.
    """
    from server.core.plugin_installer import get_plugin_data_info

    try:
        return get_plugin_data_info(plugin_id)
    except ValueError as e:
        raise _api_error(422, f"Invalid plugin id '{plugin_id}'", e)


# Open router (no auth) so a standalone room panel can fetch it without a 401 —
# a 401 here would pop the browser's native Basic dialog. A full plugin token is
# still only minted for an authenticated caller; an unauthenticated panel gets a
# panel-scoped token that the /ext/* guard honors only for routes the plugin
# declared panel-reachable (or an empty token when it declared none), so the
# rest of the plugin's ext surface stays programmer-only.
@open_router.get("/plugins/{plugin_id}/ext-token")
async def get_plugin_ext_token(
    plugin_id: str,
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> dict[str, Any]:
    """Mint a short-lived, plugin-scoped token for the plugin's panel iframe.

    The panel runtime fetches this and hands it to the plugin iframe via
    `openavc:init.ext_token`; the iframe presents it to the plugin's
    `/api/plugins/{id}/ext/*` routes, which can't otherwise carry programmer
    credentials.

    - Open instance (no auth): empty token, `auth_required: false`.
    - Claimed instance, authenticated caller (Programmer IDE, or a panel
      embedded in it): a full plugin token (`scope: "full"`).
    - Claimed instance, unauthenticated caller (standalone room panel): a
      panel-scoped token (`scope: "panel"`) when the plugin declared
      panel-reachable ext paths — the guard only honors it for those routes.
      Otherwise an empty token with `auth_required: true` and a 200 — never a
      401, so the browser stays quiet.
    """
    from server.api.plugin_ext import (
        auth_required,
        has_panel_paths,
        mint_panel_token,
        mint_plugin_token,
    )

    if not auth_required():
        return {"token": "", "expires_at": 0, "auth_required": False, "scope": ""}
    if programmer_auth_satisfied(request, credentials):
        token, expires_at = mint_plugin_token(plugin_id)
        return {
            "token": token,
            "expires_at": expires_at,
            "auth_required": True,
            "scope": "full",
        }
    if has_panel_paths(plugin_id):
        token, expires_at = mint_panel_token(plugin_id)
        return {
            "token": token,
            "expires_at": expires_at,
            "auth_required": True,
            "scope": "panel",
        }
    return {"token": "", "expires_at": 0, "auth_required": True, "scope": ""}


@router.delete("/plugins/{plugin_id}")
async def uninstall_plugin_endpoint(
    plugin_id: str,
    remove_data: bool = False,
) -> dict[str, Any]:
    """Uninstall a plugin and remove it from the project.

    Query parameter `remove_data=true` also deletes the plugin's persistent
    data directory (sidecar binaries, cached state, etc.). Default is to
    keep it so a future reinstall doesn't need to re-download large assets.
    """
    from server.core.plugin_installer import uninstall_plugin

    engine = _get_engine()
    project_plugins = engine.project.plugins if engine.project else None

    try:
        # Stop plugin if running
        if engine.plugin_loader.is_running(plugin_id):
            await engine.plugin_loader.stop_plugin(plugin_id)

        result = await uninstall_plugin(
            plugin_id, project_plugins, remove_data=remove_data
        )

        # Remove from project file so it doesn't show as "missing" on restart.
        # The apply bumps the revision (a stale editor PUT would otherwise
        # silently restore the entry) and its plugin reconcile clears the
        # loader tracking for ids the project no longer references.
        if engine.project and plugin_id in engine.project.plugins:
            def mutate(project):
                project.plugins.pop(plugin_id, None)
                project.plugin_dependencies = [
                    d for d in project.plugin_dependencies
                    if d.plugin_id != plugin_id
                ]

            await engine.apply_project_edit(mutate)

        # Clear tracking directly too: the files are gone even when the
        # plugin was never referenced by the project (no entry to diff).
        engine.plugin_loader.remove_plugin_tracking(plugin_id)

        return result
    except ValueError as e:
        raise _api_error(422, f"Failed to uninstall plugin '{plugin_id}'", e)
    except Exception as e:
        raise _api_error(500, f"Failed to uninstall plugin '{plugin_id}'", e)
