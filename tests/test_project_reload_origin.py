"""A ``project.reloaded`` broadcast names the Programmer tab whose request
made the change, so that tab can tell its own echo from another session's
edit.

The id travels in the ``X-OpenAVC-Client`` request header, through the
middleware into ``core.client_origin``, and out of the engine's reload
broadcast as ``origin_client``. A persist made outside any request (the
bookkeeping worker) carries no origin, and neither does one made by a task
that outlived the request that started it (the cloud agent's loop after
pairing), except a device's setup wizard, which is the asking tab's own run.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.cloud import agent as agent_mod
from openavc.core.client_origin import (
    CLIENT_HEADER,
    client_origin,
    parse_client_id,
    reload_origin,
    request_client_id,
)
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.setup_actions import SetupActionRunner
from openavc.middleware.client_origin import ClientOriginMiddleware
from openavc import config as config_mod


def _project_dict():
    return {
        "openavc_version": "0.7.0",
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
            "pages": [
                {"id": "main", "name": "Main",
                 "grid": {"columns": 12, "rows": 8}, "elements": []},
            ],
        },
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }


def _engine(tmp_path) -> tuple[Engine, list[dict]]:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(_project_dict()), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    sent: list[dict] = []

    async def record(msg, namespaces=None):
        sent.append(msg)

    eng.broadcast_ws = record
    return eng, sent


# ── The header, parsed ────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["tab-1", "8f3c2a1b-0d9e-4c7a-9b2f-1e5d6a7b8c9d", "A_b-9"])
def test_a_plain_id_is_accepted(raw):
    assert parse_client_id(raw) == raw


@pytest.mark.parametrize("raw", [None, "", "   ", "has space", "x" * 65, "a;b", "<tag>"])
def test_a_malformed_id_is_ignored(raw):
    assert parse_client_id(raw) is None


def test_reload_origin_is_empty_outside_a_request():
    assert request_client_id() is None
    assert reload_origin() == {}


# ── Through the middleware ────────────────────────────────────────────────


def _app() -> TestClient:
    app = FastAPI()
    app.add_middleware(ClientOriginMiddleware)

    @app.get("/who")
    async def who():
        return {"client": request_client_id(), **reload_origin()}

    return TestClient(app)


def test_middleware_sets_the_id_for_the_request_only():
    client = _app()
    r = client.get("/who", headers={CLIENT_HEADER: "tab-1"})
    assert r.json() == {"client": "tab-1", "origin_client": "tab-1"}
    # The next request without the header does not inherit it.
    assert client.get("/who").json() == {"client": None}


def test_middleware_drops_a_malformed_id():
    client = _app()
    r = client.get("/who", headers={CLIENT_HEADER: "not a valid id"})
    assert r.json() == {"client": None}


# ── Out of the engine ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_request_scoped_edit_names_its_tab(tmp_path):
    eng, sent = _engine(tmp_path)

    def mutate(project):
        project.project.name = "Renamed"

    with client_origin("tab-1"):
        await eng.apply_project_edit(mutate)

    reloads = [m for m in sent if m.get("type") == "project.reloaded"]
    assert len(reloads) == 1
    assert reloads[0]["origin_client"] == "tab-1"
    assert reloads[0]["revision"]


@pytest.mark.asyncio
async def test_an_edit_outside_a_request_carries_no_origin(tmp_path):
    eng, sent = _engine(tmp_path)

    def mutate(project):
        project.project.name = "Renamed"

    await eng.apply_project_edit(mutate)

    reloads = [m for m in sent if m.get("type") == "project.reloaded"]
    assert len(reloads) == 1
    assert "origin_client" not in reloads[0]


# ── A task that outlives its request ──────────────────────────────────────
#
# asyncio.create_task copies the request's context, so a task started inside
# a request used to keep its tab id for good. Each case starts the task in one
# request and lets it run on in a second one, after the first has answered.


def _app_with(start, finish) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ClientOriginMiddleware)

    @app.post("/start")
    async def start_route():
        await start()
        return {"started": True}

    @app.post("/finish")
    async def finish_route():
        return {"origin": await finish()}

    return app


def _origin_after_the_request(start, finish) -> Any:
    with TestClient(_app_with(start, finish)) as client:
        assert client.post("/start", headers={CLIENT_HEADER: "tab-1"}).json() == {"started": True}
        return client.post("/finish").json()["origin"]


def test_a_task_started_in_a_request_names_no_tab_once_it_has_answered():
    release = asyncio.Event()
    task: list[asyncio.Task] = []

    async def background():
        await release.wait()
        return reload_origin()

    async def start():
        task.append(asyncio.create_task(background()))

    async def finish():
        release.set()
        return await task[0]

    assert _origin_after_the_request(start, finish) == {}


def test_a_cloud_agent_started_by_a_pairing_request_names_no_tab(tmp_path, monkeypatch):
    """The real Engine._start_cloud_agent and CloudAgent.start, the loop's body
    replaced by a recorder: every save the assistant later makes through the
    agent runs in that loop."""
    eng, _sent = _engine(tmp_path)
    release = asyncio.Event()
    seen: list[dict] = []

    async def recorded_loop(self):
        await release.wait()
        seen.append(reload_origin())

    monkeypatch.setattr(config_mod, "CLOUD_ENABLED", True)
    monkeypatch.setattr(config_mod, "CLOUD_SYSTEM_KEY", "a" * 64)
    monkeypatch.setattr(config_mod, "CLOUD_SYSTEM_ID", "acme-system")
    monkeypatch.setattr(config_mod, "CLOUD_ENDPOINT", "wss://cloud.example.invalid/agent")
    monkeypatch.setattr(agent_mod.CloudAgent, "_connection_loop", recorded_loop)

    async def start():
        await eng._start_cloud_agent()
        assert eng.cloud_agent is not None

    async def finish():
        release.set()
        await eng.cloud_agent._connection_loop_task
        return seen[0]

    try:
        assert _origin_after_the_request(start, finish) == {}
    finally:
        eng.cloud_agent = None


class _WizardDevices:
    def __init__(self, driver) -> None:
        self._driver = driver

    def get_driver(self, device_id):
        return self._driver

    async def begin_setup(self, device_id) -> None:
        pass

    async def end_setup(self, device_id) -> None:
        pass


class _WizardDriver:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.seen: list[dict] = []

    def _set_setup_context(self, ctx) -> None:
        pass

    async def run_setup_action(self, action_id, params, progress) -> dict:
        await self.release.wait()
        # Where the wizard's config save would broadcast project.reloaded.
        self.seen.append(reload_origin())
        return {}


def test_a_setup_wizard_still_names_the_tab_that_started_it():
    """The wizard runs on after its request has answered, and its save is
    the asking tab's own: the IDE re-syncs on that notice itself."""
    driver = _WizardDriver()
    finished = asyncio.Event()

    class _Engine:
        devices = _WizardDevices(driver)

        async def broadcast_ws(self, msg, *, client_type=None):
            if msg.get("status") in ("done", "error"):
                finished.set()

    runner = SetupActionRunner(_Engine())

    async def start():
        await runner.start("w1", {"id": "enable_remote"}, {})

    async def finish():
        driver.release.set()
        await finished.wait()
        return driver.seen[0]

    assert _origin_after_the_request(start, finish) == {"origin_client": "tab-1"}
