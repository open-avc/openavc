"""What the server answers while the engine is still starting.

The HTTP listener serves before the engine is up. Until ``engine_ready`` is
set, the status route answers for the splash page's poll, the rest of the API
answers 503, and every other page is the splash itself; once ready, or on an
app whose lifespan never set the flag, requests pass through.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.middleware.startup_splash import StartupSplashMiddleware


def _client(**state) -> TestClient:
    app = FastAPI()
    for name, value in state.items():
        setattr(app.state, name, value)

    @app.get("/api/startup-status")
    def status():
        return {"from": "route"}

    @app.get("/api/devices")
    def devices():
        return {"from": "route"}

    @app.get("/panel")
    def panel():
        return {"from": "route"}

    app.add_middleware(StartupSplashMiddleware)
    return TestClient(app)


def test_while_starting_the_status_route_reports_not_ready():
    reply = _client(engine_ready=False, engine_error=None).get("/api/startup-status")
    assert reply.status_code == 200
    assert reply.json() == {"ready": False, "error": None}


def test_a_failed_start_reports_its_error_to_the_splash():
    reply = _client(engine_ready=False, engine_error="project unreadable").get("/api/startup-status")
    assert reply.json() == {"ready": False, "error": "project unreadable"}


def test_while_starting_the_api_answers_503():
    reply = _client(engine_ready=False, engine_error=None).get("/api/devices")
    assert reply.status_code == 503
    assert reply.json() == {"detail": "Server is starting up"}


def test_while_starting_a_page_is_the_splash():
    reply = _client(engine_ready=False, engine_error=None).get("/panel?x=1")
    assert reply.status_code == 503
    assert reply.headers["content-type"].startswith("text/html")
    assert "<title>OpenAVC</title>" in reply.text
    assert "Starting up..." in reply.text
    assert "fetch('/api/startup-status')" in reply.text


def test_a_path_merely_starting_with_api_is_not_the_api():
    reply = _client(engine_ready=False, engine_error=None).get("/apiary")
    assert reply.status_code == 503
    assert reply.headers["content-type"].startswith("text/html")


def test_once_ready_everything_passes_through():
    client = _client(engine_ready=True, engine_error=None)
    for path in ("/api/startup-status", "/api/devices", "/panel"):
        assert client.get(path).json() == {"from": "route"}


def test_an_app_that_never_ran_the_lifespan_passes_through():
    assert _client().get("/api/devices").json() == {"from": "route"}
