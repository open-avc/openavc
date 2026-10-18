"""Lifespan teardown must release resources acquired before engine readiness."""

import asyncio
from unittest.mock import AsyncMock

import aiohttp
from fastapi import FastAPI
import pytest

from openavc import main
from openavc.core.engine import Engine
from openavc.drivers.base import BaseDriver
from openavc.drivers import registry


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["initializing", "failed", "ready"])
@pytest.mark.parametrize("body_error", [False, True])
async def test_shutdown_releases_real_driver_client_before_and_after_readiness(tmp_path, monkeypatch, phase, body_error):
    engine = Engine(str(tmp_path / "project.avc"))
    entered, unwound = asyncio.Event(), asyncio.Event()
    instances = []
    startup = []

    class ResourceDriver(BaseDriver):
        DRIVER_INFO = {"id": "acme_startup", "name": "Acme startup resource", "commands": {}, "state_variables": {}}

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.client = None
            instances.append(self)

        async def connect(self):
            self.client = aiohttp.ClientSession()
            self._connected = True
            self.set_state("connected", True)

        async def disconnect(self):
            assert unwound.is_set(), "Startup must finish cancellation before device teardown"
            if self.client is not None:
                await self.client.close()
            await super().disconnect()

        async def send_command(self, command, params=None):
            raise AssertionError("Lifecycle checks must not issue a hardware command")

    monkeypatch.setitem(registry._DRIVER_REGISTRY, "acme_startup", ResourceDriver)

    async def initialize(app):
        startup.append(asyncio.current_task())
        try:
            await engine.devices.add_device({"id": "device", "driver": "acme_startup", "name": "Acme", "config": {}})
            await engine.devices.bring_up()
            if phase == "ready":
                app.state.engine_ready = True
            elif phase == "failed":
                app.state.engine_error = "Initialization failed after acquiring a resource"
            entered.set()
            if phase == "initializing":
                await asyncio.Event().wait()
        finally:
            unwound.set()

    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "_initialize_engine", initialize)
    monkeypatch.setattr(main.audit_manager, "shutdown", AsyncMock())
    close_proxy = AsyncMock()
    monkeypatch.setattr(main.simulator_proxy, "aclose_client", close_proxy)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    try:
        try:
            async with main.lifespan(app):
                await asyncio.wait_for(entered.wait(), timeout=5)
                assert len(instances) == 1 and instances[0].client is not None
                assert not instances[0].client.closed
                if body_error:
                    raise RuntimeError("Serving failed")
        except RuntimeError as exc:
            assert body_error and str(exc) == "Serving failed"
        assert unwound.is_set(), "Initialization was left running"
        assert instances[0].client.closed, "Device client survived lifespan shutdown"
        close_proxy.assert_awaited_once()
    finally:
        # Even a failing baseline releases the real test resource. This is not
        # counted as the application's cleanup assertion above.
        for task in startup:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await engine.stop()
        for instance in instances:
            if instance.client is not None:
                await instance.client.close()


@pytest.mark.asyncio
async def test_shutdown_before_initialization_has_started(tmp_path, monkeypatch):
    engine = Engine(str(tmp_path / "project.avc"))
    entered = False

    async def initialize(app):
        nonlocal entered
        entered = True

    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "_initialize_engine", initialize)
    monkeypatch.setattr(main.audit_manager, "shutdown", AsyncMock())
    close_proxy = AsyncMock()
    monkeypatch.setattr(main.simulator_proxy, "aclose_client", close_proxy)
    async with main.lifespan(FastAPI(openapi_url=None, docs_url=None, redoc_url=None)):
        pass
    assert not entered
    close_proxy.assert_awaited_once()
