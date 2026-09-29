"""A ``project.reloaded`` broadcast names the Programmer tab whose request
made the change, so that tab can tell its own echo from another session's
edit.

The id travels in the ``X-OpenAVC-Client`` request header, through the
middleware into ``core.client_origin.current_client_id``, and out of the
engine's reload broadcast as ``origin_client``. A persist made outside any
request (the bookkeeping worker) carries no origin.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.core.client_origin import (
    CLIENT_HEADER,
    current_client_id,
    parse_client_id,
    reload_origin,
)
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.middleware.client_origin import ClientOriginMiddleware


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
    assert current_client_id.get() is None
    assert reload_origin() == {}


# ── Through the middleware ────────────────────────────────────────────────


def _app() -> TestClient:
    app = FastAPI()
    app.add_middleware(ClientOriginMiddleware)

    @app.get("/who")
    async def who():
        return {"client": current_client_id.get(), **reload_origin()}

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

    token = current_client_id.set("tab-1")
    try:
        await eng.apply_project_edit(mutate)
    finally:
        current_client_id.reset(token)

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
