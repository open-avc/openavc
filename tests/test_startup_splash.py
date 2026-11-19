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


# ===========================================================================
# Who may read the status from another address
# ===========================================================================
#
# A restart can move the Programmer to another port or address of the same
# server (Settings: the HTTP or HTTPS port, who can connect). The restart
# dialog waits on the status there, so a page on this server's own network
# may read it from another address. A page from the internet may not.


def _phases() -> dict[str, RunState]:
    stopping = _record(ready=True)
    stopping.stopping()
    return {
        STARTING: _record(),
        FAILED: _record(error="project unreadable"),
        READY: _record(ready=True),
        STOPPING: stopping,
    }


def _status_from(record: RunState, origin: str, host: str = "192.168.1.20:9090"):
    return _client(record).get(
        "/api/startup-status", headers={"Origin": origin, "Host": host},
    )


def test_a_page_at_another_address_of_this_network_reads_the_status_in_every_phase():
    for phase, record in _phases().items():
        reply = _status_from(record, "http://192.168.1.20:8080")
        assert reply.headers.get("access-control-allow-origin") == "http://192.168.1.20:8080", phase
        assert "origin" in reply.headers.get("vary", "").lower(), phase
        assert reply.json()["phase"] == phase


def test_the_pages_a_restart_moves_from_may_read_it():
    record = _record()
    allowed = [
        # Another port or scheme of the name the request was sent to.
        ("http://avc-room1.example.org:8080", "avc-room1.example.org:8443"),
        ("http://AVC-Room1.example.org", "avc-room1.example.org:9090"),
        # This computer's own screen, at a second loopback address.
        ("http://127.0.0.2:8080", "127.0.0.1:8080"),
        ("http://localhost:8080", "127.0.0.1:8080"),
        ("http://[::1]:8080", "127.0.0.1:8080"),
        # Another address of this computer on the local network.
        ("http://10.20.30.5:8080", "192.168.1.20:8080"),
        ("http://172.16.4.9:8080", "127.0.0.1:8080"),
        ("http://169.254.10.2:8080", "127.0.0.1:8080"),
        ("http://100.101.102.103:8080", "127.0.0.1:8080"),
        ("http://[fd00::5]:8080", "127.0.0.1:8080"),
    ]
    for origin, host in allowed:
        reply = _status_from(record, origin, host)
        assert reply.headers.get("access-control-allow-origin") == origin, (origin, host)


def test_a_page_from_the_internet_may_not_read_it():
    record = _record(error="project unreadable")
    refused = [
        "https://example.com",
        "http://8.8.8.8:8080",
        "http://[2001:4860:4860::8888]",
        "null",
        "not a url",
        "ftp://192.168.1.20",
        "http://",
    ]
    for origin in refused:
        reply = _status_from(record, origin)
        assert "access-control-allow-origin" not in reply.headers, origin
        # The page itself still gets its answer, same-origin.
        assert reply.status_code == 200


def test_no_other_route_is_opened_to_another_address():
    client = _client(_record(ready=True))
    for path in ("/api/health", "/api/devices", "/panel"):
        reply = client.get(path, headers={"Origin": "http://192.168.1.20:8080"})
        assert "access-control-allow-origin" not in reply.headers, path


def test_a_page_at_this_computers_own_name_may_read_it(monkeypatch):
    from openavc.middleware import startup_splash

    monkeypatch.setattr(startup_splash.socket, "gethostname", lambda: "AVC-Room1.lan")
    record = _record()
    for origin in ("http://avc-room1.local:8080", "http://avc-room1:8080", "http://avc-room1.lan"):
        reply = _status_from(record, origin, "127.0.0.1:8080")
        assert reply.headers.get("access-control-allow-origin") == origin, origin
    reply = _status_from(record, "http://other-box.local:8080", "127.0.0.1:8080")
    assert "access-control-allow-origin" not in reply.headers
