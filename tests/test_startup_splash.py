"""What the server answers while the engine is still starting, and what the
run-state record answers once it is up and on its way down.

The HTTP listener serves before the engine is up. Until the start finishes,
the status route answers from the record for the splash page's poll, the
rest of the API answers 503, and every other page is the splash itself; once
it has finished, or on an app whose lifespan never made a record, requests
pass through, and the status route itself answers from the same record.
``/api/health`` answers 503 with the phase whenever the server is not ready:
starting, failed to start, or stopping.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.routes import system
from openavc.core.start_stages import StartProblem, StartRun
from openavc.middleware.startup_splash import (
    FAILED, READY, STARTING, STOPPING, RunState, StartupSplashMiddleware,
)
from openavc.version import __version__

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

    @app.get("/api/devices")
    def devices():
        return {"from": "route"}

    # Ahead of the real one, which needs an engine.
    @app.get("/api/health")
    def health():
        return {"status": "healthy"}

    app.include_router(system.open_router, prefix="/api")

    @app.get("/panel")
    def panel():
        return {"from": "route"}

    app.add_middleware(StartupSplashMiddleware)
    return TestClient(app)


def test_while_starting_the_status_route_reports_not_ready():
    reply = _client(_record()).get("/api/startup-status")
    assert reply.status_code == 200
    assert reply.json() == {
        "phase": STARTING, "ready": False, "version": __version__,
        "error": None, "problems": [], "held": False, "retry_in": None,
    }


def test_a_failed_start_reports_its_stage_and_error_to_the_splash():
    reply = _client(_record(error="project unreadable")).get("/api/startup-status")
    assert reply.json() == {
        "phase": FAILED,
        "ready": False,
        "version": __version__,
        "error": "Loading the project failed: project unreadable",
        "problems": [],
        "held": False,
        "retry_in": None,
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
    assert "fetch('/api/startup-status'" in reply.text


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
        "phase": READY,
        "ready": True,
        "version": __version__,
        "error": None,
        "problems": [{"stage": "plugins", "detail": "The plugins did not start."}],
        "held": False,
        "retry_in": None,
    }


def test_an_app_that_never_ran_the_lifespan_passes_through():
    client = _client()
    assert client.get("/api/devices").json() == {"from": "route"}
    assert client.get("/api/health").json() == {"status": "healthy"}
    assert client.get("/api/startup-status").json() == {
        "phase": READY, "ready": True, "version": __version__,
        "error": None, "problems": [], "held": False, "retry_in": None,
    }


# ===========================================================================
# /api/health: 503 whenever the server is not ready
# ===========================================================================


def test_while_starting_health_answers_503_with_the_phase():
    reply = _client(_record()).get("/api/health")
    assert reply.status_code == 503
    assert reply.json() == {"status": STARTING, "version": __version__}


def test_after_a_failed_start_health_answers_503_failed():
    reply = _client(_record(error="project unreadable")).get("/api/health")
    assert reply.status_code == 503
    assert reply.json() == {"status": FAILED, "version": __version__}


def test_once_ready_health_is_the_routes():
    reply = _client(_record(ready=True)).get("/api/health")
    assert reply.status_code == 200
    assert reply.json() == {"status": "healthy"}


def test_once_stopping_health_answers_503_and_the_rest_still_answers():
    """A restart's wait and the stop: the server is going, so a monitor, a
    container's health check and the tray hear so, while a save made in the
    wait still reaches its route."""
    record = _record(ready=True)
    record.stopping()
    client = _client(record)

    reply = client.get("/api/health")
    assert reply.status_code == 503
    assert reply.json() == {"status": STOPPING, "version": __version__}
    assert client.get("/api/devices").json() == {"from": "route"}
    assert client.get("/api/startup-status").json()["phase"] == STOPPING
    assert client.get("/api/startup-status").json()["ready"] is False


def test_a_stop_during_the_start_keeps_the_api_closed():
    """The engine has no project yet, so the stop changes the phase and
    nothing else."""
    record = _record()
    record.stopping()
    client = _client(record)

    assert client.get("/api/devices").status_code == 503
    assert client.get("/panel").status_code == 503
    assert client.get("/api/health").json() == {"status": STOPPING, "version": __version__}


# ===========================================================================
# The record's phase
# ===========================================================================


def test_stopping_is_final():
    """A start that finishes or fails after the stop began leaves it said."""
    finished = RunState()
    finished.stopping()
    finished.started(StartRun())
    assert finished.phase == STOPPING

    failed = RunState()
    failed.stopping()
    run = StartRun()
    run.at = "loading the project"
    failed.stopped(run, RuntimeError("project unreadable"))
    assert failed.phase == STOPPING


def test_each_phase_is_reported_as_it_is_reached():
    said: list[tuple] = []
    record = RunState(report=lambda *args: said.append(args))
    record.started(StartRun())
    record.stopping()
    record.stopping()

    assert said == [
        (STARTING, __version__, None),
        (READY, __version__, None),
        (STOPPING, __version__, None),
    ]


def test_a_failed_start_is_reported_with_its_reason():
    said: list[tuple] = []
    record = RunState(report=lambda *args: said.append(args))
    run = StartRun()
    run.at = "loading the project"
    record.stopped(run, RuntimeError("project unreadable"))
    record.stopping()

    assert said == [
        (STARTING, __version__, None),
        (FAILED, __version__, "Loading the project failed: project unreadable"),
        (STOPPING, __version__, None),
    ]


def test_a_report_that_raises_never_reaches_the_one_saying_it():
    """The stop signal's handler says stopping: nothing may raise there
    before uvicorn's own handler runs."""
    def broken(*args):
        raise OSError("disk full")

    record = RunState(report=broken)
    record.stopping()
    assert record.phase == STOPPING
