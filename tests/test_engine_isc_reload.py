"""Tests for ISC reload behavior on the Engine (A28).

When a project is reloaded with ISC turned off, the engine stops the manager
and clears its own reference, but it used to leave the script API's `isc`
proxy bound to the now-stopped manager. Scripts calling
`isc.send_to(...)` / `isc.broadcast(...)` then hit the dead manager and
raised a misleading `ConnectionError("Not connected to instance '...')`
instead of the intended `RuntimeError("ISC not enabled")`.
"""

import json

import pytest

from openavc.core.engine import Engine
from openavc.core.script_api import isc as isc_proxy


def _project_with_isc(tmp_path, enabled: bool) -> str:
    project = {
        "version": "0.4.0",
        "project": {"id": "p", "name": "P"},
        "variables": [],
        "macros": [],
        "devices": [],
        "device_groups": [],
        "connections": {},
        "scripts": [],
        "plugins": {},
        "ui": {
            "settings": {},
            "pages": [{"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []}],
        },
        "isc": {"enabled": enabled, "shared_state": [], "peers": [], "auth_key": ""},
        "themes": [],
    }
    project_path = tmp_path / "project.avc"
    project_path.write_text(json.dumps(project), encoding="utf-8")
    return str(project_path)


class _StoppableManager:
    """Stand-in for a running ISCManager that records stop() calls."""

    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_disable_isc_on_reload_unbinds_script_proxy(tmp_path):
    """Regression for A28: the disable branch of _reload_isc must clear the
    script API proxy so isc.send_to() raises "ISC not enabled" instead of
    reaching the stopped manager.
    """
    from openavc.core.project_loader import load_project
    from openavc.system_config import get_system_config

    project_path = _project_with_isc(tmp_path, enabled=False)
    eng = Engine(project_path)
    eng.project = load_project(project_path)
    # Engine acts as if ISC was running so the disable branch is the one taken.
    fake_manager = _StoppableManager()
    eng.isc = fake_manager
    isc_proxy._bind(fake_manager)
    assert isc_proxy._manager is fake_manager

    # Enable ISC in the live system config so the gate depends only on
    # project.isc.enabled (which is False here). _reload_isc reads the system
    # config at call time, so this exercises the "system on, project off ->
    # disable" branch the audit failure mode requires.
    sys_cfg = get_system_config()
    original_isc_enabled = sys_cfg.get("isc", "enabled", True)
    sys_cfg.set("isc", "enabled", True)
    try:
        await eng._reload_isc()
    finally:
        sys_cfg.set("isc", "enabled", original_isc_enabled)

    assert fake_manager.stopped, "manager.stop() should have been awaited"
    assert eng.isc is None
    assert isc_proxy._manager is None, (
        "script API proxy still bound to stopped manager — "
        "scripts will surface ConnectionError instead of 'ISC not enabled'"
    )

    # And calling through the proxy now gives the intended clean error.
    with pytest.raises(RuntimeError, match="ISC not enabled"):
        await isc_proxy.send_to("peer", "evt")
