"""Each way into the server asks the run-state record whether it is ready
(middleware/startup_splash.py), so they all answer a start, a failed start
and a stop the same way.

- A device's push is answered by the push registry in every phase, on the
  main listener as on the push-only listener: a push that arrives while the
  start is still running reaches its device, not a 503.
- The client socket (``/ws``) and an ISC peer (``/isc/ws``) are accepted and
  then closed with ``NOT_READY_CLOSE``, the phase as the reason, until the
  server is ready; ISC says it is not enabled only once the server is ready
  and it is not.
- ``Engine.stopping`` is the record's stopping (or the engine's own stop), so
  the restart door, which a restart from the cloud takes, refuses a restart
  once the stop signal has said stopping on the record.

Through the real app, on a claimed instance, with a record put on the app in
each phase as its lifespan would.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from openavc import main, restart
from openavc.core.engine import Engine
from openavc.core.start_stages import StartRun
from openavc.middleware.startup_splash import (
    FAILED, NOT_READY_CLOSE, READY, STARTING, STOPPING, RunState,
)
from openavc.transport import http_listener
from tests.panel_access_helpers import lan_client, loopback_client


def _in_phase(phase: str) -> RunState:
    record = RunState()
    if phase == READY:
        record.started(StartRun())
    elif phase == FAILED:
        run = StartRun()
        run.at = "loading the project"
        record.stopped(run, RuntimeError("the project would not load"))
    elif phase == STOPPING:
        record.started(StartRun())
        record.stopping()
    assert record.phase == phase
    return record


@pytest.fixture
def phase(monkeypatch):
    """Put the app's run-state record in a phase, as its lifespan would."""

    def _put(name: str) -> RunState:
        record = _in_phase(name)
        monkeypatch.setattr(main.app.state, "run_state", record, raising=False)
        return record

    return _put


def _closed_with(sock) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as closed:
        sock.receive_json()
    return closed.value


# ---------------------------------------------------------------------------
# The client socket
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [STARTING, FAILED, STOPPING])
def test_a_socket_is_told_the_phase_until_the_server_is_ready(claimed_engine, phase, name):
    phase(name)
    with loopback_client().websocket_connect("/ws?client=panel") as sock:
        # Accepted, so a browser reads the code rather than a bare 1006.
        closed = _closed_with(sock)
    assert (closed.code, closed.reason) == (NOT_READY_CLOSE, name)


def test_once_ready_a_socket_is_served_its_project(claimed_engine, phase):
    phase(READY)
    with loopback_client().websocket_connect("/ws?client=panel") as sock:
        assert sock.receive_json()["type"] == "state.snapshot"
        assert sock.receive_json()["type"] == "ui.definition"


def test_a_refused_socket_registers_nothing(claimed_engine, phase):
    phase(STARTING)
    with loopback_client().websocket_connect("/ws?client=panel&name=flows") as sock:
        _closed_with(sock)
    assert claimed_engine.ws.client_count == 0
    assert claimed_engine.state.get("system.integration.flows.connected") is None


# ---------------------------------------------------------------------------
# An ISC peer
# ---------------------------------------------------------------------------


@pytest.fixture
def no_isc(monkeypatch):
    from openavc.api import isc_ws

    monkeypatch.setattr(isc_ws, "_isc_manager", None)


@pytest.mark.parametrize("name", [STARTING, FAILED, STOPPING])
def test_an_isc_peer_is_told_the_phase_until_the_server_is_ready(no_isc, phase, name):
    phase(name)
    with lan_client().websocket_connect("/isc/ws") as sock:
        closed = _closed_with(sock)
    assert (closed.code, closed.reason) == (NOT_READY_CLOSE, name)


def test_once_ready_isc_says_it_is_not_enabled_when_it_is_not(no_isc, phase):
    phase(READY)
    with lan_client().websocket_connect("/isc/ws") as sock:
        closed = _closed_with(sock)
    assert (closed.code, closed.reason) == (4000, "ISC not enabled")


# ---------------------------------------------------------------------------
# A device's push
# ---------------------------------------------------------------------------


@pytest.fixture
async def subscribed():
    """A device subscribed for pushes from this computer, each body it is
    handed kept in ``bodies``."""
    seen = SimpleNamespace(bodies=[])
    sub = await http_listener.subscribe(
        "acme_widget", "127.0.0.1", lambda request: seen.bodies.append(request.body),
        "acme_widget",
    )
    yield seen
    await sub.close()


async def _push(app) -> int:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        reply = await client.post("/api/push/acme_widget", content=b"<Power>on</Power>")
    return reply.status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [STARTING, FAILED, READY, STOPPING])
async def test_a_push_reaches_its_device_in_every_phase_on_every_listener(subscribed, phase, name):
    phase(name)

    assert await _push(main.app) == 200
    assert await _push(http_listener.push_only_app()) == 200
    assert subscribed.bodies == [b"<Power>on</Power>"] * 2


@pytest.mark.asyncio
async def test_a_push_for_a_device_not_subscribed_is_404_while_starting(phase):
    phase(STARTING)
    assert await _push(main.app) == 404


def test_while_starting_the_rest_of_the_api_still_answers_503(phase):
    phase(STARTING)
    assert lan_client().get("/api/status").status_code == 503


# ---------------------------------------------------------------------------
# The engine's stopping, and the restart door
# ---------------------------------------------------------------------------


def test_the_engine_is_stopping_once_the_record_says_so(tmp_path):
    engine = Engine(str(tmp_path / "project.avc"))
    record = _in_phase(READY)
    engine.attach_server(run_state=record)
    assert engine.stopping is False

    # The stop signal says it on the record (listen_sockets.hears_the_stop).
    record.stopping()

    assert engine.stopping is True


@pytest.mark.asyncio
async def test_a_restart_from_the_cloud_after_the_stop_signal_is_refused(tmp_path, monkeypatch):
    engine = Engine(str(tmp_path / "project.avc"))
    record = _in_phase(READY)
    engine.attach_server(run_state=record)
    exits = []

    async def exit_after(wait, engine_):
        exits.append(wait)

    monkeypatch.setattr(restart, "_exit_after", exit_after)
    restart.accept_restarts(engine, record)
    try:
        record.stopping()

        answer = restart.request_exit("graceful", "cloud")
    finally:
        restart.reset()

    assert (answer.accepted, answer.refusal) == (False, restart.UNDER_WAY)
    assert answer.sentence == "The server is already stopping."
    assert exits == []
