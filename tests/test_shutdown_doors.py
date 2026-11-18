"""A service stop and a restart end the process through the same shutdown.

The lifespan's end (a service stop) and the restart door
(``openavc.restart.request_exit``) both run ``Engine.shutdown``: the same
stages, in the same order, within the same deadline. Each test drives a real
Engine through the real door, with the process exit and the watchdog timer
recorded instead of run.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from openavc import main, restart
from openavc.core.engine import Engine
from openavc.core.stop_stages import StopRun


class _Exited(BaseException):
    """Stand-in for os._exit, so the test process is never the one ended."""


class _Timer:
    """The watchdog, recorded and never armed."""

    def __init__(self, interval, function):
        self.interval = interval

    def start(self):
        pass


@pytest.fixture
def doors(tmp_path, monkeypatch):
    """A real Engine behind ``openavc.main``'s lifespan, never started, with
    every stage a shutdown runs recorded in ``stages``."""
    engine = Engine(str(tmp_path / "project.avc"))
    monkeypatch.setattr(main, "engine", engine)

    async def initialize(app):
        return None

    monkeypatch.setattr(main, "_initialize_engine", initialize)
    stopping_at_the_audit: list[bool] = []

    async def end_the_audit():
        stopping_at_the_audit.append(engine.stopping)

    monkeypatch.setattr(main.audit_manager, "shutdown", AsyncMock(side_effect=end_the_audit))
    monkeypatch.setattr(main.simulator_proxy, "aclose_client", AsyncMock())
    monkeypatch.setattr(restart.threading, "Timer", _Timer)
    monkeypatch.setenv("OPENAVC_SERVICE_MANAGED", "1")
    stages: list[str] = []
    real_run = StopRun.run

    async def record(self, stage, step):
        stages.append(stage)
        return await real_run(self, stage, step)

    monkeypatch.setattr(StopRun, "run", record)
    engine.stopping_at_the_audit = stopping_at_the_audit
    yield engine, stages
    # A restart driven here left its engine stopped; nothing is left running.


@pytest.mark.asyncio
async def test_a_restart_and_a_service_stop_run_the_same_stages(doors, monkeypatch):
    engine, stages = doors

    def exit_(code):
        raise _Exited(code)

    monkeypatch.setattr(os, "_exit", exit_)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    async with main.lifespan(app):
        restart.accept_restarts(engine, app.state.run_state)
        assert restart.request_exit("hard", "api").accepted
        with pytest.raises(_Exited):
            await asyncio.gather(*restart._exiting)
        by_the_restart = list(stages)
        stages.clear()

    assert stages == by_the_restart
    # The server's own pieces as well as the engine's: the stop a service
    # stop always ran, and the restart now runs too.
    for stage in (
        "the server's start", "the listen watcher", "the push listeners",
        "the device audit", "the devices", "the simulator", "the simulator proxy",
    ):
        assert stage in stages
    assert main.audit_manager.shutdown.await_count == 2
    # The server already says it is stopping when the audit ends, the
    # restart's door included (it has no stop signal to say so first).
    assert engine.stopping_at_the_audit == [True, True]
    assert main.simulator_proxy.aclose_client.await_count == 2


@pytest.mark.asyncio
async def test_the_status_folder_hears_the_start_and_says_stopping_before_the_shutdown(
    doors, monkeypatch, tmp_path,
):
    """The tray and the menu bar read where the server is in its life from
    the folder: starting from the lifespan's first moment, stopping before
    the first stage of the stop."""
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(tmp_path))
    run_state = tmp_path / "status" / "run-state.json"
    at_the_audit: list[str] = []

    async def end_the_audit():
        at_the_audit.append(json.loads(run_state.read_text(encoding="utf-8"))["phase"])

    monkeypatch.setattr(main.audit_manager, "shutdown", AsyncMock(side_effect=end_the_audit))
    async with main.lifespan(FastAPI(openapi_url=None, docs_url=None, redoc_url=None)):
        assert json.loads(run_state.read_text(encoding="utf-8"))["phase"] == "starting"

    assert at_the_audit == ["stopping"]


@pytest.mark.asyncio
async def test_a_restart_the_listen_watcher_asks_for_finishes_its_stop(doors, monkeypatch):
    """The restart request comes from inside the watcher that asked, and the
    shutdown ends that watcher: the stop and the exit still finish."""
    engine, stages = doors
    exited = asyncio.Event()
    monkeypatch.setattr(os, "_exit", lambda code: exited.set())
    restart.accept_restarts(engine)

    async def watcher():
        restart.request_exit("hard", "listen")
        await asyncio.Event().wait()  # watching on, as the real one does

    engine.listen_watcher = asyncio.create_task(watcher())
    await asyncio.wait_for(exited.wait(), timeout=5)

    assert engine.listen_watcher.cancelled()
    assert stages[-1] == "the simulator proxy"
    assert "the devices" in stages and "the simulator" in stages
    main.simulator_proxy.aclose_client.assert_awaited_once()
