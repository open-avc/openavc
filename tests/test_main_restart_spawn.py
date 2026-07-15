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

from server.main import _spawn_replacement


@pytest.fixture
def spawn_capture(monkeypatch, tmp_path):
    """Capture the Popen call _spawn_replacement makes, without spawning."""
    calls: list[dict] = []

    def fake_popen(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # Keep the breadcrumb log out of the real data dir.
    import server.system_config as system_config
    monkeypatch.setattr(system_config, "get_data_dir", lambda: tmp_path)
    return calls


def test_replaces_argv0_with_running_interpreter(spawn_capture, monkeypatch):
    """A framework-binary argv[0] (macOS re-exec) is swapped for sys.executable."""
    monkeypatch.setattr(
        sys, "orig_argv",
        ["/opt/homebrew/Frameworks/Python.framework/Python", "-m", "server.main"],
        raising=False,
    )
    monkeypatch.delattr(sys, "frozen", raising=False)

    _spawn_replacement()

    assert len(spawn_capture) == 1
    cmd = spawn_capture[0]["cmd"]
    assert cmd[0] == sys.executable
    assert cmd[1:] == ["-m", "server.main"]


def test_preserves_arguments_and_sets_restart_flag(spawn_capture, monkeypatch):
    """Extra CLI flags survive the relaunch and the child gets the retry hint."""
    monkeypatch.setattr(
        sys, "orig_argv",
        [sys.executable, "-m", "server.main", "--simulator"],
        raising=False,
    )
    monkeypatch.delattr(sys, "frozen", raising=False)

    _spawn_replacement()

    cmd = spawn_capture[0]["cmd"]
    assert cmd == [sys.executable, "-m", "server.main", "--simulator"]
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
