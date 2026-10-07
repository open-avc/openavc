"""The data folder's status/ subfolder: what the server writes for the tray app.

On Windows the installer restricts the data folder to the service and
administrators (installer/secure-data-dir.bat), and the tray app runs as the
signed-in user, so the one thing the tray can read is status/. The server
writes two files there: where it listens, at every start, and why it failed to
start, when it does. The tray reads both. Nothing ties the two ends together
but these tests: tray.py is a standalone script that imports nothing from the
server, so each end names the folder and files itself.

The folder is readable by every local account, so these tests also hold it to
carrying exactly those two files and those fields.
"""

import importlib.util
import json
from pathlib import Path

import pytest

import openavc.main as main
from openavc import config

TRAY_PATH = Path(__file__).resolve().parents[1] / "installer" / "tray.py"


def _load_tray():
    spec = importlib.util.spec_from_file_location("openavc_tray_status_test", TRAY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tray_mod = _load_tray()


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
    monkeypatch.setattr(tray_mod, "STARTUP_ERROR_FILE", status / tray_mod.STARTUP_ERROR_FILE.name)
    monkeypatch.setattr(tray_mod, "SERVER_ADDRESS_FILE", status / tray_mod.SERVER_ADDRESS_FILE.name)
    return tmp_path


def test_both_ends_name_the_same_folder_and_files():
    assert tray_mod.STATUS_DIR == tray_mod.DATA_DIR / main.STATUS_DIR_NAME
    assert tray_mod.STARTUP_ERROR_FILE == tray_mod.STATUS_DIR / main.STARTUP_ERROR_FILE_NAME
    assert tray_mod.SERVER_ADDRESS_FILE == tray_mod.STATUS_DIR / main.SERVER_ADDRESS_FILE_NAME


def test_the_tray_reads_the_address_the_server_records(data_dir, monkeypatch):
    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8444)

    main._write_server_address()

    written = json.loads((data_dir / "status" / "server.json").read_text(encoding="utf-8"))
    assert written == {"http_port": 8090, "tls_enabled": True, "tls_port": 8444}
    assert tray_mod._get_server_config() == written


def test_the_tray_reads_the_startup_error_the_server_records(data_dir, monkeypatch):
    shown = []
    monkeypatch.setattr(tray_mod, "_show_error_dialog", lambda title, message: shown.append(message))
    monkeypatch.setattr(tray_mod.threading, "Thread", _InlineThread)

    main._write_startup_error("port_in_use", "HTTP port 8090 is already in use.")

    assert (data_dir / "status" / "startup-error.json").is_file()
    assert not (data_dir / "startup-error.json").exists()
    tray = tray_mod.OpenAVCTray()
    tray._check_startup_error()
    assert shown == ["HTTP port 8090 is already in use."]

    main._clear_startup_error()

    assert not (data_dir / "status" / "startup-error.json").exists()
    tray._check_startup_error()
    assert tray._startup_error == ""


def test_a_start_records_the_address_and_clears_the_last_error(data_dir, monkeypatch):
    """main() writes where it listens once it knows the port is free, before serving."""
    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "PORT80_REDIRECT", False)
    monkeypatch.setattr(main, "_preflight_port", lambda port, retries: None)
    served = []
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **k: served.append(k["port"]))
    main._write_startup_error("port_in_use", "an earlier failure")

    main.main()

    assert served == [8090]
    assert not (data_dir / "status" / "startup-error.json").exists()
    recorded = json.loads((data_dir / "status" / "server.json").read_text(encoding="utf-8"))
    assert recorded["http_port"] == 8090


def test_the_folder_every_account_can_read_carries_only_these(data_dir, monkeypatch):
    """status/ is readable by every local account on Windows: nothing else goes in it."""
    monkeypatch.setattr(config, "HTTP_PORT", 8090)

    main._write_server_address()
    main._write_startup_error("tls_error", "HTTPS is enabled but the TLS listener cannot start.")

    status = data_dir / "status"
    assert sorted(p.name for p in status.iterdir()) == ["server.json", "startup-error.json"]
    assert set(json.loads((status / "server.json").read_text(encoding="utf-8"))) == {
        "http_port", "tls_enabled", "tls_port",
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
