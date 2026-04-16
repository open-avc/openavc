"""
OpenAVC REST API endpoints.

Aggregator module that imports domain-specific routers and exposes
the two top-level routers (open_router, router) for main.py to mount.
"""

from fastapi import APIRouter, Depends

import server.api._engine as _engine_mod
from server.api.auth import require_programmer_auth

from server.api.routes import devices as _devices_routes
from server.api.routes import drivers as _drivers_routes
from server.api.routes import macros as _macros_routes
from server.api.routes import project as _project_routes
from server.api.routes import scripts as _scripts_routes
from server.api.routes import system as _system_routes
from server.api.routes import variables as _variables_routes


def set_engine(engine) -> None:
    """Set the engine reference (called by main.py at startup)."""
    _engine_mod.set_engine(engine)


def __getattr__(name):
    """Backward compat for lazy imports from other modules."""
    if name == "_engine":
        return _engine_mod._engine
    if name == "CommunityDriverInstallRequest":
        from server.api.models import CommunityDriverInstallRequest
        return CommunityDriverInstallRequest
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Open router — no auth required (status, templates)
open_router = APIRouter(prefix="/api")
# Protected router — requires programmer auth when configured
router = APIRouter(prefix="/api", dependencies=[Depends(require_programmer_auth)])

# Include domain routers (protected)
router.include_router(_variables_routes.router)
router.include_router(_macros_routes.router)
router.include_router(_scripts_routes.router)
router.include_router(_devices_routes.router)
router.include_router(_drivers_routes.router)
router.include_router(_project_routes.router)
router.include_router(_system_routes.router)

# Include open (unauthenticated) sub-routers
open_router.include_router(_project_routes.open_router)
open_router.include_router(_system_routes.open_router)

# Backward compat re-export (used by server.api.discovery)
install_community_driver = _drivers_routes.install_community_driver
