"""Guards for the Windows system-tray app (installer/tray.py).

tray.py is not an importable package (it lives under installer/ and imports
infi.systray lazily inside run()), so it is loaded by path. These tests cover
the two non-GUI behaviors that have silently broken before:

- "Check for Updates" must open the IDE Updates view. The router matches the
  bare view id after stripping the leading '#', so the deep link must be
  '#updates', not '#/updates' (which falls back to the Dashboard).
- The tooltip's "Update available" line is driven by _update_available, which
  the status poll must populate from the /api/health payload (and clear when
  the server is down).
"""

import importlib.util
from pathlib import Path

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


def test_server_config_falls_back_when_the_data_folder_refuses_a_stat(monkeypatch, tmp_path):
    """An administrator may restrict the data folder to SYSTEM and Administrators.

    The tray runs as the signed-in user, so even checking whether system.json
    exists is refused. It must start on the defaults, not fail.
    """
    for name in ("OPENAVC_PORT", "OPENAVC_TLS_ENABLED", "OPENAVC_TLS_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    original_exists = Path.exists

    def refused(self, *args, **kwargs):
        if self.name == "system.json":
            raise PermissionError(13, "Access is denied", str(self))
        return original_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", refused)

    assert tray_mod._get_server_config() == {
        "http_port": tray_mod.DEFAULT_PORT,
        "tls_enabled": False,
        "tls_port": 8443,
    }
