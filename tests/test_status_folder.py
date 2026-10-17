"""The data folder's status/ subfolder: what the server writes for the tray app
and the macOS menu bar app.

On Windows the installer restricts the data folder to the service and
administrators (installer/secure-data-dir.bat), and the tray app runs as the
signed-in user, so the one thing the tray can read is status/. On macOS the
server runs as root and the menu bar app as the signed-in user, so the server
writes server.json readable by everyone. The server writes two files there:
where it listens, at every start, and why it failed to start, when it does.
The tray reads both; the menu bar reads where it listens. Nothing ties the
ends together but these tests: tray.py and menubar.py are standalone scripts
that import nothing from the server, so each end names the folder and files
itself.

The folder is readable by every local account, so these tests also hold it to
carrying exactly those two files and those fields.
"""

import importlib.util
import json
import os
import stat
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

import openavc.main as main
from openavc import config

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
    monkeypatch.setattr(tray_mod, "STARTUP_ERROR_FILE", status / tray_mod.STARTUP_ERROR_FILE.name)
    monkeypatch.setattr(tray_mod, "SERVER_ADDRESS_FILE", status / tray_mod.SERVER_ADDRESS_FILE.name)
    monkeypatch.setattr(menubar_mod, "SERVER_ADDRESS_FILE", status / menubar_mod.SERVER_ADDRESS_FILE.name)
    return tmp_path


def test_both_ends_name_the_same_folder_and_files():
    assert tray_mod.STATUS_DIR == tray_mod.DATA_DIR / main.STATUS_DIR_NAME
    assert tray_mod.STARTUP_ERROR_FILE == tray_mod.STATUS_DIR / main.STARTUP_ERROR_FILE_NAME
    assert tray_mod.SERVER_ADDRESS_FILE == tray_mod.STATUS_DIR / main.SERVER_ADDRESS_FILE_NAME
    assert menubar_mod.SERVER_ADDRESS_FILE == (
        menubar_mod.DATA_DIR / main.STATUS_DIR_NAME / main.SERVER_ADDRESS_FILE_NAME
    )


def test_the_menu_bar_reads_the_address_and_problem_the_server_records(data_dir, monkeypatch):
    from tests.listen_helpers import ETHERNET, plan_for

    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8444)
    unplugged = plan_for("adapter", ETHERNET, adapters=[])
    monkeypatch.setattr(config, "listen_plan", lambda: unplugged)

    main._write_server_address()

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

    main._write_server_address()

    mode = stat.S_IMODE((data_dir / "status" / "server.json").stat().st_mode)
    assert mode & 0o044 == 0o044, oct(mode)


def test_the_tray_reads_the_address_the_server_records(data_dir, monkeypatch):
    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8444)

    main._write_server_address()

    written = json.loads((data_dir / "status" / "server.json").read_text(encoding="utf-8"))
    ports = {"http_port": 8090, "tls_enabled": True, "tls_port": 8444}
    assert {k: written[k] for k in ports} == ports
    assert tray_mod._get_server_config() == ports


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
    main._write_startup_error("port_in_use", "an earlier failure")

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


def test_the_folder_every_account_can_read_carries_only_these(data_dir, monkeypatch):
    """status/ is readable by every local account on Windows: nothing else goes in it."""
    monkeypatch.setattr(config, "HTTP_PORT", 8090)

    main._write_server_address()
    main._write_startup_error("tls_error", "HTTPS is enabled but the TLS listener cannot start.")

    status = data_dir / "status"
    assert sorted(p.name for p in status.iterdir()) == ["server.json", "startup-error.json"]
    assert set(json.loads((status / "server.json").read_text(encoding="utf-8"))) == {
        "http_port", "tls_enabled", "tls_port", "listen",
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
