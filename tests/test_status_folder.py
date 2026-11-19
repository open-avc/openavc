"""The data folder's status/ subfolder: what the server writes for the tray app
and the macOS menu bar app.

On Windows the installer restricts the data folder to the service and
administrators (installer/secure-data-dir.bat), and the tray app runs as the
signed-in user, so the one thing the tray can read is status/. On macOS the
server runs as root and the menu bar app as the signed-in user, so the server
writes its files readable by everyone. The server writes three files there:
where it listens, at every start; where it is in its life (starting, ready,
failed to start, stopping), at each change; and why it failed to start, when
it does. The tray and the menu bar read all three. Nothing ties the ends
together but these tests: tray.py and menubar.py are standalone scripts that
import nothing from the server, so each end names the folder, the files and
the phases itself.

The folder is readable by every local account, so these tests also hold it to
carrying exactly those three files and those fields.
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import openavc.main as main
from openavc import status_folder
from openavc import config
from openavc.core.start_stages import StartRun
from openavc.middleware import startup_splash
from openavc.middleware.startup_splash import FAILED, PHASES, READY, STARTING, STOPPING

INSTALLER = Path(__file__).resolve().parents[1] / "installer"
TRAY_PATH = INSTALLER / "tray.py"
MENUBAR_PATH = INSTALLER / "menubar.py"


def _load_tray():
    spec = importlib.util.spec_from_file_location("openavc_tray_status_test", TRAY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_menubar():
    """menubar.py with a stand-in for rumps, which exists only on macOS: the
    functions under test never touch it, only the class body names it."""
    rumps = types.ModuleType("rumps")
    rumps.App = type("App", (), {})
    rumps.MenuItem = rumps.Timer = object
    saved = sys.modules.get("rumps")
    sys.modules["rumps"] = rumps
    try:
        spec = importlib.util.spec_from_file_location("openavc_menubar_status_test", MENUBAR_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            del sys.modules["rumps"]
        else:
            sys.modules["rumps"] = saved
    return mod


tray_mod = _load_tray()
menubar_mod = _load_menubar()


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """The server's data folder, with the tray looking at the same one.

    The tray finds the folder from PROGRAMDATA, the server from
    OPENAVC_DATA_DIR; both are pointed here, keeping the tray's own folder and
    file names, so a name that drifts on either side fails below.
    """
    for name in ("OPENAVC_PORT", "OPENAVC_TLS_ENABLED", "OPENAVC_TLS_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(tmp_path))
    status = tmp_path / tray_mod.STATUS_DIR.name
    monkeypatch.setattr(tray_mod, "STATUS_DIR", status)
    for mod in (tray_mod, menubar_mod):
        monkeypatch.setattr(mod, "STATUS_DIR", status)
        for name in ("STARTUP_ERROR_FILE", "SERVER_ADDRESS_FILE", "RUN_STATE_FILE"):
            monkeypatch.setattr(mod, name, status / getattr(mod, name).name)
    return tmp_path


def test_both_ends_name_the_same_folder_and_files():
    for mod in (tray_mod, menubar_mod):
        assert mod.STATUS_DIR == mod.DATA_DIR / status_folder.STATUS_DIR_NAME
        assert mod.STARTUP_ERROR_FILE == mod.STATUS_DIR / status_folder.STARTUP_ERROR_FILE_NAME
        assert mod.SERVER_ADDRESS_FILE == mod.STATUS_DIR / status_folder.SERVER_ADDRESS_FILE_NAME
        assert mod.RUN_STATE_FILE == mod.STATUS_DIR / status_folder.RUN_STATE_FILE_NAME


def test_both_ends_know_every_phase_the_record_reaches():
    for mod in (tray_mod, menubar_mod):
        assert (mod.STARTING, mod.READY, mod.FAILED, mod.STOPPING) == PHASES
        assert mod.STOPPED not in PHASES


def test_the_menu_bar_reads_the_address_and_problem_the_server_records(data_dir, monkeypatch):
    from tests.listen_helpers import ETHERNET, plan_for

    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8444)
    unplugged = plan_for("adapter", ETHERNET, adapters=[])
    monkeypatch.setattr(config, "listen_plan", lambda: unplugged)

    status_folder.write_server_address()

    assert menubar_mod._get_server_config() == {
        "http_port": 8090, "tls_enabled": True, "tls_port": 8444,
    }
    assert menubar_mod._get_listen_problem() == (
        "Not accepting connections from the network: the Ethernet adapter isn't connected."
    )


@pytest.mark.skipif(os.name == "nt", reason="the folder's ACL decides on Windows, not the file mode")
def test_every_account_can_read_where_the_server_listens(data_dir, monkeypatch):
    """On macOS the server runs as root and the menu bar app as the signed-in user."""
    monkeypatch.setattr(config, "HTTP_PORT", 8090)

    status_folder.write_server_address()

    mode = stat.S_IMODE((data_dir / "status" / "server.json").stat().st_mode)
    assert mode & 0o044 == 0o044, oct(mode)


def test_the_tray_reads_the_address_the_server_records(data_dir, monkeypatch):
    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8444)

    status_folder.write_server_address()

    written = json.loads((data_dir / "status" / "server.json").read_text(encoding="utf-8"))
    ports = {"http_port": 8090, "tls_enabled": True, "tls_port": 8444}
    assert {k: written[k] for k in ports} == ports
    assert tray_mod._get_server_config() == ports


def _run_state(data_dir) -> dict:
    return json.loads((data_dir / "status" / "run-state.json").read_text(encoding="utf-8"))


def test_each_phase_replaces_the_last(data_dir):
    status_folder.write_run_state("starting", "1.2.3")
    status_folder.write_run_state("ready", "1.2.3")

    recorded = _run_state(data_dir)
    assert (recorded["phase"], recorded["version"], recorded["pid"]) == ("ready", "1.2.3", os.getpid())
    # A phase with no reason leaves no reason behind.
    assert not (data_dir / "status" / "startup-error.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="the folder's ACL decides on Windows, not the file mode")
def test_every_account_can_read_where_the_server_is_in_its_life(data_dir):
    status_folder.write_run_state("failed", "1.2.3", "Loading the project failed: project unreadable")

    for name in ("run-state.json", "startup-error.json"):
        mode = stat.S_IMODE((data_dir / "status" / name).stat().st_mode)
        assert mode & 0o044 == 0o044, (name, oct(mode))


def test_a_start_records_the_address_and_clears_the_last_error(data_dir, monkeypatch):
    """main() writes where it listens once its sockets are bound, before serving."""
    from openavc import listen, listen_sockets
    from tests.listen_helpers import ETHERNET, plan_for

    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "PORT80_REDIRECT", False)
    chosen = plan_for("adapter", ETHERNET)
    monkeypatch.setattr(listen, "start", lambda: chosen)
    bound = []
    listener = Mock()

    def bind(plan, port, retries=1):
        bound.append(port)
        return [listener], plan.without(["10.20.30.5"])

    monkeypatch.setattr(listen_sockets, "bind_for_plan", bind)
    served = []
    monkeypatch.setattr(main.uvicorn.Server, "run", lambda self, sockets: served.append(sockets))
    status_folder.write_run_state("failed", "1.2.3", "an earlier failure", "port_in_use")

    main.main()

    assert bound == [8090]
    assert served == [[listener]]
    listener.close.assert_called_once_with()
    assert not (data_dir / "status" / "startup-error.json").exists()
    recorded = json.loads((data_dir / "status" / "server.json").read_text(encoding="utf-8"))
    assert recorded["http_port"] == 8090
    # Where it really listens: the bind refused the adapter's address, so
    # loopback only, and the sentence the tray shows says why.
    assert recorded["listen"] == {
        "mode": "adapter",
        "addresses": ["127.0.0.1"],
        "problem": (
            "Not accepting connections from the network: "
            "the Ethernet adapter isn't connected."
        ),
    }
    listen_sockets.push_listeners.reset()


def test_a_port_in_use_is_recorded_for_the_tray(data_dir, monkeypatch):
    from openavc import listen, listen_sockets
    from openavc.listen_sockets import ListenBindError
    from tests.listen_helpers import plan_for

    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(listen, "start", lambda: plan_for("any"))

    def bind(plan, port, retries=1):
        raise ListenBindError("in_use", "0.0.0.0", port, OSError(10048, "in use"))

    monkeypatch.setattr(listen_sockets, "bind_for_plan", bind)
    with pytest.raises(SystemExit):
        main.main()
    error = json.loads((data_dir / "status" / "startup-error.json").read_text(encoding="utf-8"))
    assert error["error"] == "port_in_use"
    assert error["message"].startswith("HTTP port 8090 is already in use.")
    # No lifespan ever ran, so no record: the bind's failure says the phase.
    assert _run_state(data_dir)["phase"] == "failed"


def test_the_folder_every_account_can_read_carries_only_these(data_dir, monkeypatch):
    """status/ is readable by every local account on Windows: nothing else goes in it."""
    monkeypatch.setattr(config, "HTTP_PORT", 8090)

    status_folder.write_server_address()
    status_folder.write_run_state(
        "failed", "1.2.3", "HTTPS is enabled but the TLS listener cannot start.", "tls_error",
    )

    status = data_dir / "status"
    assert sorted(p.name for p in status.iterdir()) == [
        "run-state.json", "server.json", "startup-error.json",
    ]
    assert set(json.loads((status / "server.json").read_text(encoding="utf-8"))) == {
        "http_port", "tls_enabled", "tls_port", "listen",
    }
    assert set(json.loads((status / "run-state.json").read_text(encoding="utf-8"))) == {
        "phase", "version", "pid", "timestamp",
    }
    assert set(json.loads((status / "startup-error.json").read_text(encoding="utf-8"))) == {
        "error", "message", "timestamp",
    }


class _InlineThread:
    """Runs the tray's dialog where it would start a thread for it."""

    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


# --- Where the server is in its life, as the tray and the menu bar read it ---

# The menu bar runs only on macOS; its process check would end the process it
# asks about on Windows, so it is never run there.
READERS = [
    pytest.param(tray_mod, id="tray"),
    pytest.param(
        menubar_mod, id="menubar",
        marks=pytest.mark.skipif(os.name == "nt", reason="the menu bar runs on macOS only"),
    ),
]


def _ended_pid() -> int:
    """The id of a process that has ended."""
    done = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True, text=True, check=True,
    )
    return int(done.stdout)


def _recorded_by(data_dir, pid: int) -> None:
    """The run-state file as a process with ``pid`` wrote it."""
    path = data_dir / "status" / "run-state.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(dict(recorded, pid=pid)), encoding="utf-8")


def _failed_start() -> str:
    """What the record says when loading the project stops the start."""
    run = StartRun()
    record = startup_splash.RunState()
    with run.stage("loading the project"):
        pass
    record.stopped(run, RuntimeError("project unreadable"))
    return record.error


PORT_IN_USE = (
    "HTTP port 8090 is already in use.\n"
    "Another application (or another copy of OpenAVC) is using this port.\n\n"
    "To fix this, change the port in Settings > Network,\n"
    "or set the OPENAVC_PORT environment variable."
)


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("phase", [STARTING, READY, STOPPING])
def test_a_running_server_is_read_in_the_phase_it_recorded(data_dir, reader, phase):
    status_folder.write_run_state(phase, "1.2.3")

    assert reader._run_state() == {"phase": phase, "version": "1.2.3", "error": "", "running": True}


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("phase", [STARTING, READY, STOPPING])
def test_a_file_its_process_left_behind_reads_stopped(data_dir, reader, phase):
    """A server killed, or one that crashed, leaves its last phase behind."""
    status_folder.write_run_state(phase, "1.2.3")
    _recorded_by(data_dir, _ended_pid())

    assert reader._run_state() == {
        "phase": reader.STOPPED, "version": "1.2.3", "error": "", "running": False,
    }


@pytest.mark.parametrize("reader", READERS)
def test_a_file_from_before_this_computer_started_reads_stopped(data_dir, reader, monkeypatch):
    """A shut-down computer leaves stopping behind, and after it starts again
    the old pid may belong to another process."""
    status_folder.write_run_state(STOPPING, "1.2.3")
    monkeypatch.setattr(reader, "_computer_started_at", lambda: time.time() + 60)

    assert reader._run_state()["phase"] == reader.STOPPED


@pytest.mark.parametrize("reader", READERS)
def test_this_computer_started_before_now(reader):
    assert time.time() - 10 * 365 * 86400 < reader._computer_started_at() < time.time()


@pytest.mark.parametrize("reader", READERS)
def test_a_failed_start_keeps_its_reason_after_its_process_ends(data_dir, reader):
    """A port or TLS failure exits at once; the reason still stands."""
    status_folder.write_run_state(FAILED, "1.2.3", PORT_IN_USE, "port_in_use")
    _recorded_by(data_dir, _ended_pid())

    assert reader._run_state() == {
        "phase": FAILED, "version": "1.2.3", "error": PORT_IN_USE, "running": False,
    }


@pytest.mark.parametrize("reader", READERS)
def test_a_stop_after_a_failed_start_leaves_its_old_reason_unread(data_dir, reader):
    status_folder.write_run_state(FAILED, "1.2.3", "an earlier failure")
    status_folder.write_run_state(STOPPING, "1.2.3")
    _recorded_by(data_dir, _ended_pid())

    assert (data_dir / "status" / "startup-error.json").exists()
    assert reader._run_state()["phase"] == reader.STOPPED
    assert reader._run_state()["error"] == ""


@pytest.mark.parametrize("reader", READERS)
def test_no_file_or_one_that_cannot_be_read_reads_stopped(data_dir, reader):
    stopped = {"phase": reader.STOPPED, "version": "", "error": "", "running": False}
    assert reader._run_state() == stopped

    (data_dir / "status").mkdir()
    (data_dir / "status" / "run-state.json").write_text("{not json", encoding="utf-8")
    assert reader._run_state() == stopped

    (data_dir / "status" / "run-state.json").write_text('{"phase": "ready"}', encoding="utf-8")
    assert reader._run_state() == stopped


@pytest.mark.parametrize("reader", READERS)
def test_a_process_runs_until_it_ends(reader):
    assert reader._process_running(os.getpid())
    assert not reader._process_running(_ended_pid())


@pytest.mark.parametrize("reader", READERS)
def test_a_process_another_account_runs_counts_as_running(reader):
    """The server runs as a service account or root and these as the signed-in
    user: being refused a look at a process means it is there."""
    assert reader._process_running(4 if os.name == "nt" else 1)


@pytest.mark.skipif(os.name == "nt", reason="os.kill ends the process on Windows")
@pytest.mark.parametrize("reader", READERS)
def test_a_refused_look_counts_as_running(reader, monkeypatch):
    def refused(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(reader.os, "kill", refused)
    assert reader._process_running(12345)


# --- The tray ---

def _polled_tray(monkeypatch, health: dict | None) -> "tray_mod.OpenAVCTray":
    """A tray after one poll, with /api/health answering ``health`` (None:
    no healthy answer)."""
    shown = []
    monkeypatch.setattr(tray_mod, "_show_error_dialog", lambda title, message: shown.append((title, message)))
    monkeypatch.setattr(tray_mod.threading, "Thread", _InlineThread)
    monkeypatch.setattr(tray_mod, "_api_get", lambda path, cfg, timeout=3.0: health)
    tray = tray_mod.OpenAVCTray()
    tray.shown = shown
    tray._poll_once()
    return tray


def test_the_tray_says_starting_while_the_server_starts(data_dir, monkeypatch):
    status_folder.write_run_state(STARTING, "1.2.3")

    assert _polled_tray(monkeypatch, None)._build_tooltip() == "OpenAVC v1.2.3 - Starting"


def test_the_tray_says_stopping_while_the_server_stops(data_dir, monkeypatch):
    status_folder.write_run_state(STOPPING, "1.2.3")

    assert _polled_tray(monkeypatch, None)._build_tooltip() == "OpenAVC v1.2.3 - Stopping"


def test_the_tray_says_why_a_start_failed_and_shows_it_once(data_dir, monkeypatch):
    """A stage of the engine's start that stopped it, not only a port or TLS."""
    status_folder.write_run_state(FAILED, "1.2.3", _failed_start())

    tray = _polled_tray(monkeypatch, None)
    tray._poll_once()

    assert tray._build_tooltip() == (
        "OpenAVC v1.2.3 - Could not start: Loading the project failed: project unreadable"
    )
    assert tray.shown == [
        ("OpenAVC could not start", "Loading the project failed: project unreadable"),
    ]


def test_the_tray_tooltip_keeps_the_reason_s_first_paragraph_within_the_limit(data_dir, monkeypatch):
    status_folder.write_run_state(FAILED, "1.2.3", PORT_IN_USE, "port_in_use")

    tray = _polled_tray(monkeypatch, None)
    tooltip = tray._build_tooltip()

    assert tooltip.startswith(
        "OpenAVC v1.2.3 - Could not start: HTTP port 8090 is already in use. Another application"
    )
    assert len(tooltip) <= tray_mod.TOOLTIP_LIMIT
    assert tooltip.endswith("...")
    # The dialog has the whole reason, the remedy included.
    assert tray.shown == [("OpenAVC could not start", PORT_IN_USE)]


def test_the_tray_shows_the_next_start_s_failure_too(data_dir, monkeypatch):
    status_folder.write_run_state(FAILED, "1.2.3", "the first failure")
    tray = _polled_tray(monkeypatch, None)
    status_folder.write_run_state(STARTING, "1.2.3")
    tray._poll_once()
    status_folder.write_run_state(FAILED, "1.2.3", "the second failure")
    tray._poll_once()

    assert [message for _, message in tray.shown] == ["the first failure", "the second failure"]


def test_the_tray_says_stopped_when_no_server_runs(data_dir, monkeypatch):
    status_folder.write_run_state(READY, "1.2.3")
    _recorded_by(data_dir, _ended_pid())

    tray = _polled_tray(monkeypatch, None)

    assert tray._build_tooltip() == "OpenAVC v1.2.3 - Stopped"
    assert tray.shown == []


def test_the_tray_says_not_responding_when_a_ready_server_does_not_answer(data_dir, monkeypatch):
    status_folder.write_run_state(READY, "1.2.3")

    assert _polled_tray(monkeypatch, None)._build_tooltip() == "OpenAVC v1.2.3 - Not responding"


def test_the_tray_says_running_from_health(data_dir, monkeypatch):
    status_folder.write_run_state(READY, "1.2.3")
    health = {
        "status": "healthy", "version": "1.2.3",
        "devices": {"total": 3, "connected": 2}, "update_available": "1.2.4",
    }

    assert _polled_tray(monkeypatch, health)._build_tooltip() == (
        "OpenAVC v1.2.3 - Running - 2/3 devices online - Update available: v1.2.4"
    )


# --- The menu bar ---

class _MenuBar(SimpleNamespace):
    """The parts of the menu bar app its poll and its stop use."""

    def __init__(self):
        super().__init__(
            _cfg={"http_port": 8080, "tls_enabled": False, "tls_port": 8443},
            _base="http://localhost:8080",
            title="OpenAVC",
            _status_item=SimpleNamespace(title="Starting..."),
        )


def _menu_line(monkeypatch, health: dict | None) -> str:
    monkeypatch.setattr(menubar_mod, "_api_get", lambda path, cfg, timeout=3.0: health)
    app = _MenuBar()
    menubar_mod.OpenAVCMenuBar._poll(app, None)
    return app._status_item.title


menubar_only = pytest.mark.skipif(os.name == "nt", reason="the menu bar runs on macOS only")


@menubar_only
@pytest.mark.parametrize("phase, line", [
    (STARTING, "v1.2.3 - Starting"),
    (STOPPING, "v1.2.3 - Stopping"),
    (READY, "v1.2.3 - Not responding"),
])
def test_the_menu_bar_says_where_the_server_is_in_its_life(data_dir, monkeypatch, phase, line):
    status_folder.write_run_state(phase, "1.2.3")

    assert _menu_line(monkeypatch, None) == line


@menubar_only
def test_the_menu_bar_says_why_a_start_failed(data_dir, monkeypatch):
    status_folder.write_run_state(FAILED, "1.2.3", PORT_IN_USE, "port_in_use")
    _recorded_by(data_dir, _ended_pid())

    assert _menu_line(monkeypatch, None) == (
        "v1.2.3 - Could not start: HTTP port 8090 is already in use. "
        "Another application (or another copy of OpenAVC) is using this port."
    )


@menubar_only
def test_the_menu_bar_says_stopped_when_no_server_runs(data_dir, monkeypatch):
    assert _menu_line(monkeypatch, None) == "Stopped"


@menubar_only
def test_the_menu_bar_says_running_from_health(data_dir, monkeypatch):
    health = {"status": "healthy", "version": "1.2.3", "devices": {"total": 3, "connected": 2}}

    assert _menu_line(monkeypatch, health) == "v1.2.3 - Running - 2/3 devices online"


def _stop_and_quit(monkeypatch, on_stop) -> list[str]:
    """Stop Server and Quit, with the stop command doing ``on_stop``; what
    the app did: "quit" or "alert"."""
    did: list[str] = []
    monkeypatch.setattr(menubar_mod, "_service_command", lambda action: on_stop())
    monkeypatch.setattr(menubar_mod.rumps, "quit_application", lambda: did.append("quit"), raising=False)
    monkeypatch.setattr(menubar_mod.rumps, "alert", lambda **kw: did.append("alert"), raising=False)
    monkeypatch.setattr(menubar_mod, "time", SimpleNamespace(
        sleep=lambda seconds: None, time=time.time,
        clock_gettime=time.clock_gettime, CLOCK_MONOTONIC=time.CLOCK_MONOTONIC,
    ))
    menubar_mod.OpenAVCMenuBar._stop_and_quit(_MenuBar(), None)
    return did


@menubar_only
def test_stop_and_quit_quits_once_the_server_says_it_is_stopping(data_dir, monkeypatch):
    status_folder.write_run_state(READY, "1.2.3")

    assert _stop_and_quit(monkeypatch, lambda: status_folder.write_run_state(STOPPING, "1.2.3")) == ["quit"]


@menubar_only
def test_stop_and_quit_stays_while_a_failed_start_still_runs(data_dir, monkeypatch):
    """A failed start held on its startup screen answers health with 503: the
    password prompt cancelled leaves it running, and the app says so."""
    status_folder.write_run_state(FAILED, "1.2.3", "Loading the project failed: project unreadable")
    monkeypatch.setattr(menubar_mod, "_api_get", lambda path, cfg, timeout=3.0: None)

    assert _stop_and_quit(monkeypatch, lambda: None) == ["alert"]


@menubar_only
def test_stop_and_quit_quits_when_no_server_runs(data_dir, monkeypatch):
    assert _stop_and_quit(monkeypatch, lambda: None) == ["quit"]
