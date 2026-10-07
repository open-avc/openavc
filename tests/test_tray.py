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
