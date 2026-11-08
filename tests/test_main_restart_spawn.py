"""Tests for _spawn_replacement — the dev-session restart relauncher.

The replacement child must be launched through the interpreter that is
actually running the server (``sys.executable``), not ``sys.orig_argv[0]``.
On macOS framework builds (Homebrew Python) the interpreter re-execs itself
through Python.app at startup, so orig_argv[0] is the bare framework binary
with no venv attached — a child spawned from it dies on its first import.
Frozen bundles keep their argv untouched.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from openavc.main import _spawn_replacement


@pytest.fixture
def spawn_capture(monkeypatch, tmp_path):
    """Capture the Popen call _spawn_replacement makes, without spawning."""
    calls: list[dict] = []

    def fake_popen(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # Keep the breadcrumb log out of the real data dir.
    import openavc.system_config as system_config
    monkeypatch.setattr(system_config, "get_data_dir", lambda: tmp_path)
    return calls


def test_replaces_argv0_with_running_interpreter(spawn_capture, monkeypatch):
    """A framework-binary argv[0] (macOS re-exec) is swapped for sys.executable."""
    monkeypatch.setattr(
        sys, "orig_argv",
        ["/opt/homebrew/Frameworks/Python.framework/Python", "-m", "openavc.main"],
        raising=False,
    )
    monkeypatch.delattr(sys, "frozen", raising=False)

    _spawn_replacement()

    assert len(spawn_capture) == 1
    cmd = spawn_capture[0]["cmd"]
    assert cmd[0] == sys.executable
    assert cmd[1:] == ["-m", "openavc.main"]


def test_preserves_arguments_and_sets_restart_flag(spawn_capture, monkeypatch):
    """Extra CLI flags survive the relaunch and the child gets the retry hint."""
    monkeypatch.setattr(
        sys, "orig_argv",
        [sys.executable, "-m", "openavc.main", "--simulator"],
        raising=False,
    )
    monkeypatch.delattr(sys, "frozen", raising=False)

    _spawn_replacement()

    cmd = spawn_capture[0]["cmd"]
    assert cmd == [sys.executable, "-m", "openavc.main", "--simulator"]
    assert spawn_capture[0]["env"]["OPENAVC_RESTARTING"] == "1"


def test_frozen_bundle_keeps_argv_untouched(spawn_capture, monkeypatch):
    """Frozen exes relaunch exactly as invoked — argv[0] IS the bundle exe."""
    monkeypatch.setattr(
        sys, "orig_argv",
        ["/Applications/OpenAVC.app/Contents/MacOS/openavc", "--simulator"],
        raising=False,
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    _spawn_replacement()

    cmd = spawn_capture[0]["cmd"]
    assert cmd == ["/Applications/OpenAVC.app/Contents/MacOS/openavc", "--simulator"]


# -- how the child is started, and where its output goes --------------------


def test_a_posix_child_starts_in_its_own_session_writing_to_the_restart_log(
    spawn_capture, monkeypatch, tmp_path, caplog
):
    import logging
    import os

    monkeypatch.setattr(sys, "orig_argv", [sys.executable, "-m", "openavc.main"], raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        _spawn_replacement()

    (spawned,) = spawn_capture
    child_log = tmp_path / "logs" / "restart-child.log"
    assert set(spawned) == {"cmd", "env", "start_new_session", "close_fds", "stdin", "stdout", "stderr"}
    assert spawned["start_new_session"] is True and spawned["close_fds"] is True
    assert spawned["stdin"] is subprocess.DEVNULL and spawned["stderr"] is subprocess.STDOUT
    assert spawned["stdout"].name == str(child_log)
    assert spawned["env"] == {**os.environ, "OPENAVC_RESTARTING": "1"}
    spawned["stdout"].close()
    breadcrumb = child_log.read_text(encoding="utf-8")
    assert breadcrumb.startswith(
        f"\n--- spawning replacement (parent pid={os.getpid()}, "
        f"argv={[sys.executable, '-m', 'openavc.main']}) at "
    )
    assert breadcrumb.endswith(" ---\n")
    assert f"Spawned replacement process pid=12345; child output -> {child_log}" in [
        r.getMessage() for r in caplog.records
    ]


def test_successive_restarts_append_to_the_restart_log(spawn_capture, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "orig_argv", [sys.executable, "-m", "openavc.main"], raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    _spawn_replacement()
    _spawn_replacement()
    for spawned in spawn_capture:
        spawned["stdout"].close()
    text = (tmp_path / "logs" / "restart-child.log").read_text(encoding="utf-8")
    assert text.count("--- spawning replacement") == 2


def test_a_windows_child_gets_a_hidden_console_in_its_own_process_group(
    spawn_capture, monkeypatch
):
    """CREATE_NO_WINDOW, never DETACHED_PROCESS: a child with no console at
    all gives every console grandchild its own visible window."""
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    monkeypatch.setattr(sys, "orig_argv", [sys.executable, "-m", "openavc.main"], raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    _spawn_replacement()

    (spawned,) = spawn_capture
    spawned["stdout"].close()
    assert set(spawned) == {"cmd", "env", "creationflags", "close_fds", "stdin", "stdout", "stderr"}
    assert spawned["creationflags"] == 0x08000000 | 0x00000200
    assert spawned["close_fds"] is True
    assert spawned["stdin"] is subprocess.DEVNULL and spawned["stderr"] is subprocess.STDOUT


def test_with_no_restart_log_the_child_output_is_discarded(spawn_capture, monkeypatch, caplog):
    import logging

    import openavc.system_config as system_config

    def unwritable():
        raise PermissionError("read-only data folder")

    class Folder:
        def __truediv__(self, name):
            return self

        def mkdir(self, **kwargs):
            unwritable()

    monkeypatch.setattr(system_config, "get_data_dir", lambda: Folder())
    monkeypatch.setattr(sys, "orig_argv", [sys.executable, "-m", "openavc.main"], raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        _spawn_replacement()

    (spawned,) = spawn_capture
    assert spawned["stdout"] is subprocess.DEVNULL
    messages = [r.getMessage() for r in caplog.records]
    assert (
        "Could not open restart-child log: read-only data folder; child output will be lost"
        in messages
    )
    assert "Spawned replacement process pid=12345" in messages


def test_a_spawn_that_fails_is_logged_and_never_raised(monkeypatch, tmp_path, caplog):
    import logging

    import openavc.system_config as system_config

    def refuse(cmd, **kwargs):
        raise OSError("no such interpreter")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(system_config, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    with caplog.at_level(logging.ERROR, logger="openavc.main"):
        _spawn_replacement()

    assert [r.getMessage() for r in caplog.records] == [
        "Failed to spawn replacement process; exiting without restart"
    ]


def test_without_orig_argv_it_relaunches_from_executable_and_argv(spawn_capture, monkeypatch):
    monkeypatch.delattr(sys, "orig_argv", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "argv", ["openavc/main.py", "--flag"])
    _spawn_replacement()
    spawn_capture[0]["stdout"].close()
    assert spawn_capture[0]["cmd"] == [sys.executable, "openavc/main.py", "--flag"]


# -- whether a service manager will relaunch the server ---------------------


@pytest.fixture
def no_manager(monkeypatch):
    import os

    from openavc.main import _is_service_managed

    monkeypatch.delenv("OPENAVC_SERVICE_MANAGED", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(os, "getpid", lambda: 4242)
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: False if p == "/.dockerenv" else real_exists(p))
    return _is_service_managed


def test_nothing_says_a_manager_is_there(no_manager):
    assert no_manager() is False


@pytest.mark.parametrize("value, managed", [("1", True), ("0", False), ("true", False)])
def test_the_installer_flag_must_be_exactly_one(no_manager, monkeypatch, value, managed):
    monkeypatch.setenv("OPENAVC_SERVICE_MANAGED", value)
    assert no_manager() is managed


def test_systemd_sets_an_invocation_id(no_manager, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    assert no_manager() is True


def test_process_one_is_a_container(no_manager, monkeypatch):
    import os

    monkeypatch.setattr(os, "getpid", lambda: 1)
    assert no_manager() is True


def test_a_docker_marker_file_is_a_container(no_manager, monkeypatch):
    import os

    monkeypatch.setattr(os.path, "exists", lambda p: p == "/.dockerenv")
    assert no_manager() is True
