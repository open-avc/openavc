"""The reset file: a forgotten Programmer password, cleared at startup.

``apply_reset_request`` (openavc/api/auth.py) is the one server path behind
every way of asking for a reset (the Windows tray, the Mac menu bar app, a
file on the Pi's boot partition, a command on Linux or Docker). These pin what
it clears, what it leaves, and when it keeps the file for another try.
"""

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from openavc.api import auth
from openavc.utils.password_hash import hash_api_key, hash_password


@pytest.fixture(autouse=True)
def _isolate(isolated_auth_config):
    cfg = isolated_auth_config
    yield cfg
    for name in auth.RESET_REQUEST_NAMES:
        (cfg.data_dir / name).unlink(missing_ok=True)


@pytest.fixture
def os_sync(monkeypatch):
    from openavc import host_control

    calls = []
    monkeypatch.setattr(host_control, "sync_os_password", lambda pw: calls.append(pw) or True)
    return calls


def _claim(cfg, *, api_key=""):
    cfg.set("auth", "allow_anonymous", False)
    cfg.set("auth", "programmer_username", "admin")
    cfg.set("auth", "programmer_password", hash_password("correct horse"))
    cfg.set("auth", "api_key", hash_api_key(api_key) if api_key else "")
    cfg.save()


def _request(cfg, name=auth.RESET_REQUEST_NAMES[0]):
    path = cfg.data_dir / name
    path.write_text("")
    return path


def test_no_file_changes_nothing(_isolate, os_sync):
    cfg = _isolate
    _claim(cfg)
    assert auth.apply_reset_request() is False
    assert auth.auth_state() == "required"
    assert os_sync == []


@pytest.mark.parametrize("name", auth.RESET_REQUEST_NAMES)
def test_the_file_puts_the_setup_screen_back(_isolate, os_sync, name):
    cfg = _isolate
    _claim(cfg, api_key="acme-integration-key")
    path = _request(cfg, name)

    assert auth.apply_reset_request() is True

    assert auth.auth_state() == "setup"
    assert not path.exists()
    saved = json.loads(cfg.file_path.read_text())["auth"]
    assert saved["programmer_username"] == ""
    assert saved["programmer_password"] == ""
    assert saved["api_key"] == ""
    # The Pi's OS login is locked again until a new password is chosen.
    assert os_sync == [""]


def test_a_new_password_can_be_set_after(_isolate, os_sync):
    cfg = _isolate
    _claim(cfg)
    _request(cfg)
    auth.apply_reset_request()

    auth.claim_instance("battery staple", "owner")

    assert auth._check_credentials("owner", "battery staple")
    assert not auth._check_credentials("admin", "correct horse")


def test_an_api_key_alone_would_leave_nothing_that_signs_in(_isolate, os_sync):
    cfg = _isolate
    _claim(cfg, api_key="acme-integration-key")
    _request(cfg)
    auth.apply_reset_request()
    assert auth.is_claimed() is False


def test_a_login_from_the_environment_is_left_alone(_isolate, os_sync, monkeypatch):
    cfg = _isolate
    _claim(cfg)
    monkeypatch.setitem(
        cfg._env_held, ("auth", "programmer_password"), "OPENAVC_PROGRAMMER_PASSWORD"
    )
    cfg._data["auth"]["programmer_password"] = "from the environment"
    _request(cfg)

    assert auth.apply_reset_request() is True

    assert cfg.get("auth", "programmer_password") == "from the environment"
    assert cfg.get("auth", "programmer_username") == ""


def test_a_failed_save_keeps_the_file_for_the_next_start(_isolate, os_sync, monkeypatch):
    cfg = _isolate
    _claim(cfg)
    path = _request(cfg)

    def refuse(*, raise_errors=False):
        raise OSError("disk full")

    monkeypatch.setattr(cfg, "save", refuse)

    assert auth.apply_reset_request() is False
    assert path.exists()
    assert os_sync == []


# --- The Mac menu bar's door ---------------------------------------------------


def _load_menubar():
    """menubar.py with a stand-in for rumps, which exists only on macOS."""
    rumps = types.ModuleType("rumps")
    rumps.App = type("App", (), {})
    rumps.MenuItem = rumps.Timer = object
    saved = sys.modules.get("rumps")
    sys.modules["rumps"] = rumps
    try:
        path = Path(__file__).resolve().parents[1] / "installer" / "menubar.py"
        spec = importlib.util.spec_from_file_location("openavc_menubar_reset_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            del sys.modules["rumps"]
        else:
            sys.modules["rumps"] = saved
    return mod


def test_the_menu_bar_writes_a_file_the_server_reads():
    menubar = _load_menubar()
    assert menubar.RESET_REQUEST_FILE.name in auth.RESET_REQUEST_NAMES
    assert menubar.RESET_REQUEST_FILE.parent == menubar.DATA_DIR
    command = menubar._reset_command()
    assert f"touch '{menubar.RESET_REQUEST_FILE}'" in command
    assert f"launchctl kickstart -k system/{menubar.SERVICE_LABEL}" in command
    # No double quote: the line sits inside an AppleScript string.
    assert '"' not in command


# --- The Pi's door: a file on the SD card's boot partition --------------------


def _run_prestart(tmp_path, boot_dirs, data):
    app = tmp_path / "app"
    app.mkdir(exist_ok=True)
    env = dict(os.environ, OPENAVC_BOOT_DIRS=" ".join(str(d) for d in boot_dirs))
    env.pop("OPENAVC_FIRMWARE_HOST", None)
    helper = Path(__file__).resolve().parents[1] / "installer" / "update-helper.sh"
    return subprocess.run(
        ["bash", str(helper), str(data), str(app)],
        env=env, capture_output=True, text=True, timeout=20,
    )


posix_only = pytest.mark.skipif(
    os.name != "posix", reason="The prestart helper is a POSIX shell entry point"
)


@posix_only
@pytest.mark.parametrize("name", ["openavc-reset-password", "openavc-reset-password.txt"])
def test_a_boot_partition_file_becomes_the_reset_request(tmp_path, name):
    boot, data = tmp_path / "boot", tmp_path / "data"
    boot.mkdir()
    data.mkdir()
    (boot / name).write_text("")

    done = _run_prestart(tmp_path, [tmp_path / "firmware", boot], data)

    assert done.returncode == 0
    assert (data / auth.RESET_REQUEST_NAMES[0]).is_file()
    assert not (boot / name).exists()


@posix_only
def test_no_boot_file_writes_no_request(tmp_path):
    boot, data = tmp_path / "boot", tmp_path / "data"
    boot.mkdir()
    data.mkdir()
    assert _run_prestart(tmp_path, [boot], data).returncode == 0
    assert not (data / auth.RESET_REQUEST_NAMES[0]).exists()


@posix_only
def test_the_boot_file_stays_when_the_request_cannot_be_written(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "openavc-reset-password").write_text("")
    done = _run_prestart(tmp_path, [boot], tmp_path / "no-data-folder")
    assert done.returncode == 0
    assert (boot / "openavc-reset-password").exists()
