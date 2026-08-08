"""Tests for simulator auto-shutdown disable (A25).

The simulator stops itself 5s after the last UI WebSocket client disconnects.
That's nice UX for standalone CLI use (close the browser tab → simulator goes
away). But openavc launches the simulator as a subprocess and never connects
its own WS — only the browser UI does. Without the disable, closing the
Simulator UI tab silently kills the simulator and every driver loses its
simulated connection.
"""

import signal
import sys

from openavc.simulator import _runtime
from openavc.simulator import __main__ as sim_main
from openavc.simulator import api


def test_set_auto_shutdown_updates_module_flag():
    """set_auto_shutdown() flips the module-level flag the WS endpoint reads."""
    api.set_auto_shutdown(True)
    assert api._auto_shutdown is True
    api.set_auto_shutdown(False)
    assert api._auto_shutdown is False
    # Restore the default so later tests see expected state.
    api.set_auto_shutdown(True)


def test_cli_no_auto_shutdown_lands_in_startup_config(monkeypatch):
    """`--no-auto-shutdown` parses and stores `auto_shutdown=False` in the
    startup config the FastAPI lifespan reads.
    """
    monkeypatch.setattr(sim_main.uvicorn.Server, "run", lambda self: None)

    monkeypatch.setattr(sys, "argv", ["openavc-simulator", "--no-auto-shutdown"])
    sim_main.main()
    assert _runtime.startup_config["auto_shutdown"] is False


def test_cli_default_keeps_auto_shutdown_enabled(monkeypatch):
    """Standalone CLI invocations (no flag) keep the original auto-shutdown
    UX so a user running `openavc-simulator` and closing the tab stops the
    process instead of leaving it orphaned.
    """
    monkeypatch.setattr(sim_main.uvicorn.Server, "run", lambda self: None)

    monkeypatch.setattr(sys, "argv", ["openavc-simulator"])
    sim_main.main()
    assert _runtime.startup_config["auto_shutdown"] is True


def test_process_exit_prefers_should_exit_over_hard_kill(monkeypatch):
    """With a uvicorn Server handle present, exit is requested via
    ``should_exit`` (graceful, cross-platform) — never a self-SIGTERM, which
    is a hard TerminateProcess on Windows that skips uvicorn's own hooks."""
    class _FakeServer:
        should_exit = False

    fake = _FakeServer()
    monkeypatch.setattr(_runtime, "uvicorn_server", fake)
    killed = []
    monkeypatch.setattr(api.os, "kill", lambda *a, **k: killed.append(a))

    api._request_process_exit()

    assert fake.should_exit is True
    assert killed == []  # no hard kill when the graceful handle exists


def test_process_exit_falls_back_to_signal_without_server(monkeypatch):
    """Without a Server handle (unusual launch) fall back to self-SIGTERM so
    shutdown still works."""
    monkeypatch.setattr(_runtime, "uvicorn_server", None)
    killed = []
    monkeypatch.setattr(api.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    api._request_process_exit()

    assert len(killed) == 1
    assert killed[0][1] == signal.SIGTERM
