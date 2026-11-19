"""A test that runs the server's lifespan leaves no run-state record behind.

``with TestClient(app)`` runs ``openavc.main``'s real lifespan on the one app
every test shares: it puts a run-state record on the app and attaches it to
the engine, and the record ends saying stopping. A later test on the same
worker that reaches the app without a lifespan of its own would then be
turned away as not ready (the WebSocket's close, the splash's 503), so the
harness forgets the record after every test (``tests/conftest.py``).

The two tests run in this order on one worker (each file is one xdist group).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from openavc import main
from openavc.middleware.startup_splash import STOPPING, not_ready_phase


def test_a_lifespan_ends_with_its_record_saying_stopping():
    with TestClient(main.app):
        pass
    assert main.app.state.run_state.phase == STOPPING
    assert main.engine.run_state is main.app.state.run_state


def test_the_next_test_finds_no_record_left():
    assert not_ready_phase(main.app) is None
    assert not hasattr(main.app.state, "run_state")
    assert main.engine.run_state is None
