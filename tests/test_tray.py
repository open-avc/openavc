"""Guards for the Windows system-tray app (installer/tray.py).

tray.py is not an importable package (it lives under installer/ and imports
infi.systray lazily inside run()), so it is loaded by path. These tests cover
the non-GUI behaviors that have silently broken before:

- The tray runs as the signed-in user, and the installer restricts the data
  folder to the service and administrators, so everything the tray reads comes
  from the status folder the server writes for it, never system.json.

- "Check for Updates" must open the IDE Updates view. The router matches the
  bare view id after stripping the leading '#', so the deep link must be
  '#updates', not '#/updates' (which falls back to the Dashboard).
- The tooltip's "Update available" line is driven by _update_available, which
  the status poll must populate from the /api/health payload (and clear when
  the server is down).
- When the adapter Accept connections from names is missing, the tooltip says
  so in the server's own sentence, read from the status folder at every poll.
"""

import importlib.util
import json
from pathlib import Path

import pytest

TRAY_PATH = Path(__file__).resolve().parents[1] / "installer" / "tray.py"


def _load_tray():
    spec = importlib.util.spec_from_file_location("openavc_tray_under_test", TRAY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tray_mod = _load_tray()
OpenAVCTray = tray_mod.OpenAVCTray


def test_check_updates_opens_updates_view_without_slash(monkeypatch):
    tray = OpenAVCTray()
    opened = []
    monkeypatch.setattr(tray_mod.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(tray_mod, "_api_get", lambda *a, **k: None)

    tray._check_updates(None)

    assert opened == [f"{tray._base_url}/programmer#updates"]
    # '#/updates' would route to the Dashboard, hiding the update result.
    assert "#/updates" not in opened[0]


def _run_one_poll(tray, monkeypatch):
    """Drive _poll_status through exactly one iteration by stopping in sleep."""
    def _stop_sleep(_):
        tray._running = False

    monkeypatch.setattr(tray_mod.time, "sleep", _stop_sleep)
    tray._running = True
    tray._poll_status()


def test_poll_status_populates_update_available(monkeypatch):
    tray = OpenAVCTray()
    monkeypatch.setattr(tray_mod, "_api_get", lambda *a, **k: {
        "status": "healthy",
        "version": "0.23.0",
        "devices": {"total": 0, "connected": 0},
        "update_available": "0.24.0",
    })

    _run_one_poll(tray, monkeypatch)

    assert tray._update_available == "0.24.0"
    assert "Update available: v0.24.0" in tray._build_tooltip()


def test_poll_status_clears_update_available_when_server_down(monkeypatch):
    tray = OpenAVCTray()
    tray._update_available = "0.24.0"  # stale value from a prior poll
    monkeypatch.setattr(tray_mod, "_api_get", lambda *a, **k: None)

    _run_one_poll(tray, monkeypatch)

    assert tray._update_available == ""
    assert "Update available" not in tray._build_tooltip()


DEFAULTS = {"http_port": tray_mod.DEFAULT_PORT, "tls_enabled": False, "tls_port": 8443}


@pytest.fixture
def status_dir(monkeypatch, tmp_path):
    """A data folder like the installer leaves it, with the tray pointed at it."""
    for name in ("OPENAVC_PORT", "OPENAVC_TLS_ENABLED", "OPENAVC_TLS_PORT"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "OpenAVC"
    status = data_dir / "status"
    status.mkdir(parents=True)
    monkeypatch.setattr(tray_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(tray_mod, "STATUS_DIR", status)
    monkeypatch.setattr(tray_mod, "STARTUP_ERROR_FILE", status / "startup-error.json")
    monkeypatch.setattr(tray_mod, "SERVER_ADDRESS_FILE", status / "server.json")
    return status


def test_server_config_comes_from_the_status_folder_not_system_json(status_dir):
    """The tray cannot read system.json on an installed system, so it must not try.

    A system.json beside it naming another port is ignored: the address the
    server recorded is the one it is listening on.
    """
    (status_dir.parent / "system.json").write_text(
        json.dumps({"network": {"http_port": 9999}}), encoding="utf-8",
    )
    (status_dir / "server.json").write_text(
        json.dumps({"http_port": 8090, "tls_enabled": True, "tls_port": 8444}),
        encoding="utf-8",
    )

    config = tray_mod._get_server_config()

    assert config == {"http_port": 8090, "tls_enabled": True, "tls_port": 8444}
    assert tray_mod._base_url(config) == "https://localhost:8444"


def test_server_config_defaults_before_the_server_has_ever_started(status_dir):
    assert tray_mod._get_server_config() == DEFAULTS


@pytest.mark.parametrize("content", ["{not json", "[8090]", '{"http_port": "eighty"}'])
def test_server_config_defaults_on_a_malformed_file(status_dir, content):
    (status_dir / "server.json").write_text(content, encoding="utf-8")
    assert tray_mod._get_server_config() == DEFAULTS


def test_server_config_falls_back_when_the_status_folder_refuses_a_stat(status_dir, monkeypatch):
    """If status is ever refused to the signed-in user, even a stat fails.

    The tray must start on the defaults, not fail.
    """
    original_exists = Path.exists

    def refused(self, *args, **kwargs):
        if self.name == "server.json":
            raise PermissionError(13, "Access is denied", str(self))
        return original_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", refused)

    assert tray_mod._get_server_config() == DEFAULTS


def test_an_environment_override_still_wins_over_the_recorded_port(status_dir, monkeypatch):
    (status_dir / "server.json").write_text(
        json.dumps({"http_port": 8090, "tls_enabled": False, "tls_port": 8443}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAVC_PORT", "8181")
    assert tray_mod._get_server_config()["http_port"] == 8181


class _InlineThread:
    """Runs the dialog where the tray would start a thread for it."""

    def __init__(self, target, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def test_startup_error_is_read_from_the_status_folder(status_dir, monkeypatch):
    shown = []
    monkeypatch.setattr(tray_mod, "_show_error_dialog", lambda title, message: shown.append(message))
    monkeypatch.setattr(tray_mod.threading, "Thread", _InlineThread)
    (status_dir / "startup-error.json").write_text(
        json.dumps({"error": "port_in_use", "message": "HTTP port 8080 is already in use."}),
        encoding="utf-8",
    )
    tray = OpenAVCTray()

    tray._check_startup_error()

    assert tray._startup_error == "HTTP port 8080 is already in use."
    assert shown == ["HTTP port 8080 is already in use."]


PROBLEM = "Not accepting connections from the network: the Ethernet adapter isn't connected."

HEALTHY = {
    "status": "healthy",
    "version": "0.37.0",
    "devices": {"total": 5, "connected": 3},
}


def _record(status_dir, **listen):
    (status_dir / "server.json").write_text(json.dumps({
        "http_port": 8080, "tls_enabled": False, "tls_port": 8443,
        "listen": {"mode": "adapter", "addresses": ["127.0.0.1"], **listen},
    }), encoding="utf-8")


def test_the_tooltip_says_why_the_network_cannot_connect(status_dir, monkeypatch):
    """The server's own sentence, ahead of the device count it replaces:
    Windows cuts a tooltip off at 128 characters."""
    _record(status_dir, problem=PROBLEM)
    monkeypatch.setattr(tray_mod, "_api_get", lambda *a, **k: HEALTHY)
    tray = OpenAVCTray()

    _run_one_poll(tray, monkeypatch)

    assert tray._build_tooltip() == f"OpenAVC v0.37.0 - Running - {PROBLEM}"


def test_the_tooltip_counts_devices_while_the_network_can_connect(status_dir, monkeypatch):
    _record(status_dir, problem=None)
    monkeypatch.setattr(tray_mod, "_api_get", lambda *a, **k: HEALTHY)
    tray = OpenAVCTray()

    _run_one_poll(tray, monkeypatch)

    assert tray._build_tooltip() == "OpenAVC v0.37.0 - Running - 3/5 devices online"


def test_a_stopped_server_shows_no_listen_problem(status_dir, monkeypatch):
    """The record is the last start's; a stopped server is just stopped."""
    _record(status_dir, problem=PROBLEM)
    monkeypatch.setattr(tray_mod, "_api_get", lambda *a, **k: None)
    tray = OpenAVCTray()
    tray._listen_problem = PROBLEM

    _run_one_poll(tray, monkeypatch)

    assert tray._listen_problem == ""
    assert PROBLEM not in tray._build_tooltip()


@pytest.mark.parametrize("content", [
    "{not json", "[8080]", '{"http_port": 8080}', '{"listen": null}',
    '{"listen": {"problem": 42}}', '{"listen": "adapter"}',
])
def test_no_listen_problem_from_a_record_without_one(status_dir, content):
    (status_dir / "server.json").write_text(content, encoding="utf-8")
    assert tray_mod._get_listen_problem() == ""


def test_no_listen_problem_before_the_server_has_ever_started(status_dir):
    assert tray_mod._get_listen_problem() == ""


def test_a_port_changed_in_settings_reaches_the_tray_with_the_restart(status_dir, monkeypatch):
    """The server records its ports at every start; the tray reads them at
    every poll, so it follows without being restarted itself."""
    asked = []
    monkeypatch.setattr(tray_mod, "_api_get", lambda path, cfg, **k: asked.append(cfg["http_port"]))
    tray = OpenAVCTray()
    (status_dir / "server.json").write_text(
        json.dumps({"http_port": 8090, "tls_enabled": False, "tls_port": 8443}),
        encoding="utf-8",
    )

    _run_one_poll(tray, monkeypatch)

    assert asked == [8090]
    assert tray._base_url == "http://localhost:8090"


# --- Reset Programmer Password ------------------------------------------------


def test_the_reset_file_is_one_the_server_reads():
    from openavc.api.auth import RESET_REQUEST_NAMES

    assert tray_mod.RESET_REQUEST_FILE.name in RESET_REQUEST_NAMES
    assert tray_mod.RESET_REQUEST_FILE.parent == tray_mod.DATA_DIR


def test_the_elevated_half_writes_the_file_and_restarts_the_service(tmp_path, monkeypatch):
    flag = tmp_path / "reset-programmer-password"
    monkeypatch.setattr(tray_mod, "RESET_REQUEST_FILE", flag)
    ran = []

    class Done:
        returncode = 0

    monkeypatch.setattr(tray_mod.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or Done())

    assert tray_mod._reset_programmer_password_elevated() == 0
    assert flag.exists()
    assert ran == [[tray_mod.NSSM_PATH, "restart", tray_mod.SERVICE_NAME]]


def test_the_elevated_half_runs_without_starting_a_second_tray(monkeypatch):
    monkeypatch.setattr(tray_mod.sys, "argv", ["openavc-tray.exe", tray_mod.RESET_ARG])
    monkeypatch.setattr(tray_mod, "_reset_programmer_password_elevated", lambda: 0)
    monkeypatch.setattr(tray_mod, "OpenAVCTray", lambda: pytest.fail("started the tray"))

    with pytest.raises(SystemExit) as done:
        tray_mod.main()
    assert done.value.code == 0


def test_saying_no_resets_nothing(monkeypatch):
    tray = OpenAVCTray()
    monkeypatch.setattr(tray_mod, "_ask_yes_no", lambda *a: False)
    monkeypatch.setattr(tray_mod, "_launch_elevated_reset", lambda: pytest.fail("launched"))
    tray._reset_programmer_password_flow()


def test_a_declined_windows_prompt_says_the_password_was_not_reset(monkeypatch):
    tray = OpenAVCTray()
    shown = []
    monkeypatch.setattr(tray_mod, "_ask_yes_no", lambda *a: True)
    monkeypatch.setattr(tray_mod, "_launch_elevated_reset", lambda: False)
    monkeypatch.setattr(tray_mod, "_show_error_dialog", lambda title, msg: shown.append(msg))
    monkeypatch.setattr(tray_mod, "_show_info_dialog", lambda *a: pytest.fail("said it worked"))

    tray._reset_programmer_password_flow()

    assert shown and "not reset" in shown[0]


# --- Shortcuts open the page on the port the server recorded -----------------


@pytest.mark.parametrize("page", ["programmer", "panel"])
def test_open_uses_the_recorded_port(monkeypatch, page):
    opened = []
    monkeypatch.setattr(tray_mod, "_get_server_config", lambda: {"http_port": 9090, "tls_enabled": False})
    monkeypatch.setattr(tray_mod.webbrowser, "open", lambda url: opened.append(url))

    assert tray_mod._open_page(page) == 0
    assert opened == [f"http://localhost:9090/{page}"]


def test_open_refuses_a_page_it_does_not_know(monkeypatch):
    monkeypatch.setattr(tray_mod.webbrowser, "open", lambda url: pytest.fail("opened"))
    assert tray_mod._open_page("settings") == 2


def test_open_runs_without_starting_a_tray(monkeypatch):
    monkeypatch.setattr(tray_mod.sys, "argv", ["openavc-tray.exe", tray_mod.OPEN_ARG, "panel"])
    opened = []
    monkeypatch.setattr(tray_mod, "_open_page", lambda page: opened.append(page) or 0)
    monkeypatch.setattr(tray_mod, "OpenAVCTray", lambda: pytest.fail("started the tray"))

    with pytest.raises(SystemExit) as done:
        tray_mod.main()
    assert done.value.code == 0
    assert opened == ["panel"]


def test_a_second_tray_does_not_start(monkeypatch):
    monkeypatch.setattr(tray_mod.sys, "argv", ["openavc-tray.exe"])
    monkeypatch.setattr(tray_mod, "_tray_already_running", lambda: True)
    monkeypatch.setattr(tray_mod, "OpenAVCTray", lambda: pytest.fail("started a second tray"))
    tray_mod.main()


def test_every_tray_shortcut_has_a_plain_link_for_installs_without_the_tray():
    iss = (TRAY_PATH.parent / "setup.iss").read_text(encoding="utf-8")
    icons = iss.split("[Icons]", 1)[1].split("\n[", 1)[0]
    for name, page in (
        (r"{autodesktop}\OpenAVC Programmer", "programmer"),
        (r"{group}\OpenAVC Programmer", "programmer"),
        (r"{group}\OpenAVC Panel", "panel"),
    ):
        lines = [line for line in icons.splitlines() if line.startswith(f'Name: "{name}"')]
        assert any(f'Parameters: "--open {page}"' in line and "Components: shortcuts and tray" in line for line in lines)
        assert any("http://localhost:8080/" + page in line and "Components: shortcuts and not tray" in line for line in lines)
        # The .url an earlier install left is removed, or an upgrade shows both.
        assert f'Type: files; Name: "{name}.url"' in iss
