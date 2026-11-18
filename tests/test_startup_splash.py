"""What the server answers while the engine is still starting, and what the
run-state record answers once it is up.

The HTTP listener serves before the engine is up. Until the record says
ready, the status route answers from it for the splash page's poll, the rest
of the API answers 503, and every other page is the splash itself; once
ready, or on an app whose lifespan never made a record, requests pass
through, and the status route itself answers from the same record.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.routes import system
from openavc.core.start_stages import StartProblem, StartRun
from openavc.middleware.startup_splash import RunState, StartupSplashMiddleware

_PLUGINS = StartProblem("plugins", "The plugins did not start.")


def _record(*, ready=False, error=None, problems=()) -> RunState:
    run = StartRun()
    run.problems.extend(problems)
    record = RunState()
    if ready:
        record.started(run)
    elif error is not None:
        run.at = "loading the project"
        record.stopped(run, RuntimeError(error))
    return record


def _client(run_state: RunState | None = None) -> TestClient:
    app = FastAPI()
    if run_state is not None:
        app.state.run_state = run_state

    app.include_router(system.open_router, prefix="/api")

    @app.get("/api/devices")
    def devices():
        return {"from": "route"}

    @app.get("/panel")
    def panel():
        return {"from": "route"}

    app.add_middleware(StartupSplashMiddleware)
    return TestClient(app)


def test_while_starting_the_status_route_reports_not_ready():
    reply = _client(_record()).get("/api/startup-status")
    assert reply.status_code == 200
    assert reply.json() == {"ready": False, "error": None, "problems": []}


def test_a_failed_start_reports_its_stage_and_error_to_the_splash():
    reply = _client(_record(error="project unreadable")).get("/api/startup-status")
    assert reply.json() == {
        "ready": False,
        "error": "Loading the project failed: project unreadable",
        "problems": [],
    }


def test_while_starting_the_api_answers_503():
    reply = _client(_record()).get("/api/devices")
    assert reply.status_code == 503
    assert reply.json() == {"detail": "Server is starting up"}


def test_while_starting_a_page_is_the_splash():
    reply = _client(_record()).get("/panel?x=1")
    assert reply.status_code == 503
    assert reply.headers["content-type"].startswith("text/html")
    assert "<title>OpenAVC</title>" in reply.text
    assert "Starting up..." in reply.text
    assert "fetch('/api/startup-status')" in reply.text


def test_a_path_merely_starting_with_api_is_not_the_api():
    reply = _client(_record()).get("/apiary")
    assert reply.status_code == 503
    assert reply.headers["content-type"].startswith("text/html")


def test_once_ready_everything_passes_through():
    client = _client(_record(ready=True))
    for path in ("/api/devices", "/panel"):
        assert client.get(path).json() == {"from": "route"}


def test_once_ready_the_status_route_answers_what_the_start_went_on_past():
    reply = _client(_record(ready=True, problems=[_PLUGINS])).get("/api/startup-status")
    assert reply.status_code == 200
    assert reply.json() == {
        "ready": True,
        "error": None,
        "problems": [{"stage": "plugins", "detail": "The plugins did not start."}],
    }


def test_an_app_that_never_ran_the_lifespan_passes_through():
    client = _client()
    assert client.get("/api/devices").json() == {"from": "route"}
    assert client.get("/api/startup-status").json() == {
        "ready": True, "error": None, "problems": [],
    }
