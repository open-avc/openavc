"""What a start does when each of its stages raises: goes on past it, with
the stage kept on the run-state record in words, or stops, named by its
stage (core/engine_lifecycle.py's docstring says which and why).

Driven through the server's own start (``openavc.main._initialize_engine``)
around a real engine, so the server's stages and the engine's are one list
in the order they run. The raise is made at the stage itself, so the stage's
own lines never run. A stage added to the start fails
``test_a_start_runs_exactly_the_stages_decided_here`` until it has its row in
``STAGES``.
"""

from __future__ import annotations

import contextlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openavc import main
from openavc.core.engine import Engine
from openavc.core.start_stages import StartRun
from openavc.middleware.startup_splash import FAILED, READY, RunState
from tests.listen_helpers import use_plan

BROKE = "acme stage broke"

#: Every stage of a start, in the order it runs: None for a stage that stops
#: the start when it raises, else the stage code it is kept under on the
#: run-state record when the start goes on past it.
STAGES: dict[str, str | None] = {
    "checking the last update": None,
    "working out who can connect": "who_can_connect",
    "starting live updates to open screens": None,
    "preparing the system settings": None,
    "loading the driver library": "drivers",
    "setting up the system status": None,
    "loading the project": None,
    "loading the variables": None,
    "loading the macros": None,
    "adding the devices": None,
    "starting the plugins": "plugins",
    "saving the start-up backup": "start_backup",
    "starting the script engine": None,
    "loading the scripts": "scripts",
    "loading the triggers": None,
    "starting live macro and plugin updates": None,
    "starting the settings devices report": "filled_in_settings",
    "starting the network services": "network_services",
    "starting the periodic backup and clean-up": "periodic_tasks",
    "clearing files left by earlier commands": "staged_files",
    "marking the system started": None,
    "starting the triggers": None,
    "starting the update checks": "update_checks",
    "starting panel software updates": "panel_software",
    "confirming the last update": None,
    "loading the drivers' discovery data": "discovery_data",
    "setting up restarts": None,
    "logging the addresses to open": "addresses",
}


def _broken() -> None:
    raise RuntimeError(BROKE)


class _BreakOne(StartRun):
    """A start whose stage ``target`` raises in place of its own lines, and
    which records every stage it reaches, in order."""

    def __init__(self, target: str | None = None) -> None:
        super().__init__()
        self.target = target
        self.reached: list[str] = []

    def _step(self, doing: str, step):
        self.reached.append(doing)
        return _broken if doing == self.target else step

    @contextlib.contextmanager
    def stage(self, doing: str):
        self._step(doing, None)
        with super().stage(doing):
            if doing == self.target:
                _broken()
            yield

    async def run(self, doing: str, step):
        return await super().run(doing, self._step(doing, step))

    async def go_on(self, stage, step):
        return await super().go_on(stage, self._step(stage.doing, step))


async def _nothing(*_a, **_k) -> None:
    return None


def _project(tmp_path) -> str:
    project = {
        "version": "0.16.0",
        "project": {"id": "p", "name": "Acme Stages"},
        "variables": [{"id": "v", "type": "string", "default": ""}],
        "macros": [{"id": "m", "name": "M", "steps": []}],
        "devices": [],
        "device_groups": [],
        "connections": {},
        "scripts": [],
        "plugins": {},
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []},
        ]},
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(project), encoding="utf-8")
    return str(path)


@pytest.fixture
def start(monkeypatch, tmp_path):
    """``start(target)``: the server's start around a real engine on a small
    project, with stage ``target`` raising (None: none), returning the
    run-state record, the run and the engine. Stood in for: the update work
    (no update is pending), the listen watcher, ISC, the mDNS advertisement,
    the cloud agent and the update checks' own timer; who can connect is
    this computer only. Nothing restarts the
    process, so a failed start waits on the splash page at once."""
    from openavc.updater import platform
    from openavc.updater.manager import UpdateManager

    monkeypatch.setattr("openavc.updater.host.get_firmware_host", lambda: None)
    monkeypatch.setattr("openavc.updater.rollback.check_rollback_needed", lambda *_a: False)
    for name in ("clear_stale_rollback_marker", "confirm_startup", "remove_leftover_update_tasks"):
        monkeypatch.setattr(f"openavc.updater.rollback.{name}", lambda *_a, **_k: None)
    monkeypatch.setattr(UpdateManager, "start_auto_check", _nothing)
    monkeypatch.setattr(UpdateManager, "stop_auto_check", _nothing)
    watcher = Mock()
    watcher.return_value.run = AsyncMock()
    monkeypatch.setattr("openavc.listen.ListenWatcher", watcher)
    use_plan("loopback")
    monkeypatch.setattr("openavc.status_folder._status_dir", lambda: tmp_path / "status")
    monkeypatch.delenv("OPENAVC_SERVICE_MANAGED", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(platform, "_is_docker", lambda: False)
    monkeypatch.setattr(platform, "_DEPLOYMENT_MARKER", tmp_path / "no-marker")

    async def run(target: str | None):
        eng = Engine(_project(tmp_path))
        for name in ("_start_isc", "_start_mdns_advertiser", "_start_cloud_agent"):
            monkeypatch.setattr(eng, name, _nothing)
        monkeypatch.setattr(main, "engine", eng)
        the_run = _BreakOne(target)
        monkeypatch.setattr(main, "StartRun", lambda: the_run)
        app = SimpleNamespace(state=SimpleNamespace(run_state=RunState()))
        await main._initialize_engine(app)
        return app.state.run_state, the_run, eng

    return run


async def _stopped(eng: Engine) -> None:
    """The engine stopped, and the listen watcher a start that got that far
    began."""
    if eng.listen_watcher is not None:
        eng.listen_watcher.cancel()
        with contextlib.suppress(BaseException):
            await eng.listen_watcher
    await eng.stop()


@pytest.mark.asyncio
async def test_a_start_runs_exactly_the_stages_decided_here(start):
    record, run, eng = await start(None)
    try:
        assert run.reached == list(STAGES)
        assert (record.phase, record.error, record.problems) == (READY, None, ())
    finally:
        await _stopped(eng)


@pytest.mark.asyncio
@pytest.mark.parametrize("doing", [d for d, code in STAGES.items() if code is None])
async def test_a_stage_the_room_needs_stops_the_start_named_by_its_stage(start, doing):
    record, run, eng = await start(doing)
    try:
        assert run.reached == list(STAGES)[: list(STAGES).index(doing) + 1]
        assert record.phase == FAILED
        assert record.error == f"{doing[:1].upper()}{doing[1:]} failed: {BROKE}"
        assert record.problems == ()
        # Whichever stage stopped it, the start reads as not started.
        assert eng.state.get("system.started") is not True
    finally:
        await _stopped(eng)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("doing", "code"), [(d, code) for d, code in STAGES.items() if code is not None],
)
async def test_a_service_the_room_runs_without_is_kept_and_the_start_goes_on(
        start, caplog, doing, code):
    with caplog.at_level(logging.WARNING, logger="openavc.core.engine"):
        record, run, eng = await start(doing)
    try:
        assert run.reached == list(STAGES)
        assert (record.phase, record.error) == (READY, None)
        assert [problem.stage for problem in record.problems] == [code]
        # The sentence any reader may be told: never the exception's text.
        detail = record.problems[0].detail
        assert BROKE not in detail
        assert detail.endswith("Check the server log for the reason.")
        assert eng.state.get("system.started") is True
        name = code.replace("_", " ")
        assert f"Engine started with 1 error(s): {name[:1].upper()}{name[1:]}: {BROKE}" in [
            r.getMessage() for r in caplog.records
        ]
    finally:
        await _stopped(eng)
