"""An update's scheduled task deletes itself, and startup deletes the ones
earlier versions left.

On Windows the updater runs the installer from a one-time Task Scheduler
task as SYSTEM. Updaters used to leave every such task registered, each
pointing at the installer it cached, and a restart could have Task Scheduler
run them all again as missed starts: the oldest installer to finish put that
version back in place of the current one. Now the task's last action deletes
it, and the server deletes every update task that is not running when it
starts, before the automatic rollback can schedule one.

The first tests read the task the launcher writes and run the cleanup against
a stand-in for PowerShell, on any platform. The live ones need a disposable,
elevated Windows machine (CI's Windows leg): one has Task Scheduler run a
launched task to its end and checks it is gone, one leaves a task registered
and one running and checks only the first is removed.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from openavc.updater import rollback
from tests import gates

TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
MAIN = Path(rollback.__file__).resolve().parents[1] / "main.py"


# -- the task the launcher registers -------------------------------------------


@pytest.fixture
def registered(monkeypatch, tmp_path):
    """The task XML and the command that registered it, read inside the fake
    schtasks call because the launcher deletes the file afterwards."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["xml"] = Path(cmd[cmd.index("/xml") + 1]).read_text(encoding="utf-16")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rollback.subprocess, "run", fake_run)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    installer = tmp_path / "OpenAVC-Setup-9.9.9.exe"
    installer.write_bytes(b"")
    assert rollback._launch_installer_via_scheduler(installer, "9.9.9")
    return seen


def _actions(xml: str) -> list[ET.Element]:
    task = ET.fromstring(xml.split("?>", 1)[1])
    return task.findall(f"{TASK_NS}Actions/{TASK_NS}Exec")


def test_the_task_runs_the_installer_then_deletes_itself(registered):
    assert registered["cmd"][registered["cmd"].index("/tn") + 1] == "OpenAVCUpdate-9.9.9"
    install, delete = _actions(registered["xml"])
    assert install.findtext(f"{TASK_NS}Command").endswith("OpenAVC-Setup-9.9.9.exe")
    assert install.findtext(f"{TASK_NS}Arguments") == "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
    assert delete.findtext(f"{TASK_NS}Command") == str(Path(r"C:\Windows") / "System32" / "schtasks.exe")
    assert delete.findtext(f"{TASK_NS}Arguments") == '/delete /f /tn "OpenAVCUpdate-9.9.9"'


def test_the_trigger_has_no_end(registered):
    """An end that passed while the machine slept would leave nothing to install."""
    xml = registered["xml"]
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "EndBoundary" not in xml and "DeleteExpiredTaskAfter" not in xml


def test_a_rollback_task_deletes_itself_too(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["xml"] = Path(cmd[cmd.index("/xml") + 1]).read_text(encoding="utf-16")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rollback.subprocess, "run", fake_run)
    installer = tmp_path / "OpenAVC-Setup-1.0.0.exe"
    installer.write_bytes(b"")
    assert rollback._launch_installer_via_scheduler(installer, "rollback-1.1.0")
    _, delete = _actions(seen["xml"])
    assert delete.findtext(f"{TASK_NS}Arguments") == '/delete /f /tn "OpenAVCUpdate-rollback-1.1.0"'


# -- the cleanup at startup ---------------------------------------------------


def test_the_cleanup_does_nothing_off_windows(monkeypatch):
    monkeypatch.setattr(rollback.sys, "platform", "linux")
    monkeypatch.setattr(
        rollback.subprocess, "run",
        lambda *a, **k: pytest.fail("the cleanup ran a program off Windows"),
    )
    assert rollback.remove_leftover_update_tasks() == []


def test_the_cleanup_reports_what_it_removed(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["env"] = cmd, kwargs["env"]
        out = (
            "removed OpenAVCUpdate-0.21.0\r\n"
            "kept OpenAVCUpdate-0.24.1: Access is denied.\r\n"
            "removed OpenAVCUpdate-rollback-0.25.0\r\n"
        )
        return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")

    monkeypatch.setattr(rollback.sys, "platform", "win32")
    monkeypatch.setattr(rollback.subprocess, "run", fake_run)
    monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\7\Modules")
    assert rollback.remove_leftover_update_tasks() == [
        "OpenAVCUpdate-0.21.0", "OpenAVCUpdate-rollback-0.25.0",
    ]
    assert seen["cmd"][0].endswith(r"WindowsPowerShell\v1.0\powershell.exe")
    assert "-NonInteractive" in seen["cmd"]
    assert not any(k.upper() == "PSMODULEPATH" for k in seen["env"])


def test_the_script_keeps_a_running_task_and_reads_the_state_not_the_status():
    script = rollback._LEFTOVER_TASKS_SCRIPT
    assert "-TaskName 'OpenAVCUpdate-*'" in script
    assert "$_.State -ne 'Running'" in script
    assert "Unregister-ScheduledTask" in script


@pytest.mark.parametrize("failure", [
    FileNotFoundError("powershell.exe"),
    subprocess.TimeoutExpired("powershell", 30),
    PermissionError("denied"),
])
def test_the_cleanup_never_stops_the_server_starting(monkeypatch, failure):
    def fake_run(cmd, **kwargs):
        raise failure

    monkeypatch.setattr(rollback.sys, "platform", "win32")
    monkeypatch.setattr(rollback.subprocess, "run", fake_run)
    assert rollback.remove_leftover_update_tasks() == []


def test_a_failed_cleanup_run_still_returns(monkeypatch):
    monkeypatch.setattr(rollback.sys, "platform", "win32")
    monkeypatch.setattr(
        rollback.subprocess, "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, b"", b"boom"),
    )
    assert rollback.remove_leftover_update_tasks() == []


def test_startup_cleans_up_before_the_automatic_rollback_can_schedule():
    """A cleanup after it would delete the rollback's own task, leaving the
    server exited with nothing scheduled to install."""
    source = MAIN.read_text(encoding="utf-8")
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"remove_leftover_update_tasks", "check_rollback_needed"}
    ]
    order = [call.func.id for call in sorted(calls, key=lambda c: c.lineno)]
    assert order == ["remove_leftover_update_tasks", "check_rollback_needed"]
    gate = source[:source.index("remove_leftover_update_tasks()")]
    assert gate.rstrip().endswith("from openavc.updater.rollback import remove_leftover_update_tasks")
    assert "detect_deployment_type() == DeploymentType.WINDOWS_INSTALLER" in gate[-600:]


# -- live, on a disposable elevated Windows machine ----------------------------


def _elevated_reason() -> str | None:
    if not sys.platform.startswith("win"):
        return "this is not Windows"
    import ctypes

    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return "this process is not elevated"
    except (AttributeError, OSError) as exc:
        return f"elevation could not be checked ({exc})"
    return None


def _exists(name: str) -> bool:
    return subprocess.run(
        ["schtasks", "/query", "/tn", name], capture_output=True, timeout=60,
    ).returncode == 0


def _delete(name: str) -> None:
    subprocess.run(["schtasks", "/end", "/tn", name], capture_output=True, timeout=60)
    subprocess.run(["schtasks", "/delete", "/f", "/tn", name], capture_output=True, timeout=60)


@pytest.fixture
def live():
    gates.skip_or_fail(gates.HOST_FIREWALL, _elevated_reason())


def test_a_launched_task_is_gone_once_it_has_run(live, tmp_path):
    label = f"test-{uuid.uuid4().hex[:8]}"
    name = f"OpenAVCUpdate-{label}"
    marker = tmp_path / "ran.txt"
    stand_in = tmp_path / "OpenAVC-Setup-0.0.0.cmd"
    stand_in.write_text(f'@echo %* > "{marker}"\r\n', encoding="ascii")
    try:
        assert rollback._launch_installer_via_scheduler(stand_in, label)
        assert _exists(name), "the task was not registered"
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and (not marker.exists() or _exists(name)):
            time.sleep(2)
        assert marker.exists(), "Task Scheduler never ran the stand-in installer"
        assert "/VERYSILENT" in marker.read_text(encoding="ascii", errors="replace")
        assert not _exists(name), "the task is still registered after it ran"
    finally:
        _delete(name)


def test_the_cleanup_removes_a_leftover_task_and_keeps_a_running_one(live):
    tag = uuid.uuid4().hex[:8]
    leftover, running = f"OpenAVCUpdate-test-{tag}-left", f"OpenAVCUpdate-test-{tag}-running"
    try:
        for name, command in (
            (leftover, "cmd /c exit 0"),
            (running, "ping -n 120 127.0.0.1"),
        ):
            made = subprocess.run(
                # 00:00 today has passed: registered with a warning, never due.
                ["schtasks", "/create", "/f", "/tn", name, "/ru", "SYSTEM",
                 "/sc", "once", "/st", "00:00", "/tr", command],
                capture_output=True, text=True, errors="replace", timeout=60,
            )
            assert made.returncode == 0, made.stdout + made.stderr
        subprocess.run(["schtasks", "/run", "/tn", running], capture_output=True, timeout=60)
        time.sleep(3)

        removed = rollback.remove_leftover_update_tasks()

        assert leftover in removed
        assert running not in removed
        assert not _exists(leftover)
        assert _exists(running)
    finally:
        _delete(leftover)
        _delete(running)
