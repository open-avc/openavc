"""Update system REST endpoints: check, apply, rollback, status, history.

Thin HTTP surface over ``server/updater/manager.py`` — the state machine,
backup, download, apply and rollback logic all live there. The manager is
created lazily on first use and cached on the engine, so a deployment that
never checks for updates never builds one.
"""

from typing import Any

from fastapi import APIRouter

from openavc.api._engine import _get_engine

router = APIRouter()


def _get_update_manager():
    engine = _get_engine()
    if engine.update_manager is None:
        from openavc.updater.manager import UpdateManager
        engine.update_manager = UpdateManager(state_store=engine.state)
    return engine.update_manager


@router.get("/system/updates/check")
async def check_for_updates() -> dict[str, Any]:
    """Check GitHub for available updates."""
    mgr = _get_update_manager()
    return await mgr.check_for_updates()


@router.post("/system/updates/apply")
async def apply_update() -> dict[str, Any]:
    """Download and apply an available update."""
    mgr = _get_update_manager()
    return await mgr.apply_update()


@router.post("/system/updates/rollback")
async def rollback_update() -> dict[str, Any]:
    """Rollback to the previous version."""
    mgr = _get_update_manager()
    return await mgr.rollback()


@router.get("/system/updates/status")
async def get_update_status() -> dict[str, Any]:
    """Get current update status and progress."""
    mgr = _get_update_manager()
    return mgr.get_status()


@router.get("/system/updates/history")
async def get_update_history() -> dict[str, Any]:
    """List past updates with timestamps."""
    mgr = _get_update_manager()
    return {"history": mgr.get_history()}
