"""A silent install that stopped the tray starts it again.

An update runs the installer silently from a scheduled task as SYSTEM. The
installer stops the tray so its files can be replaced, and the [Run] entry
that starts it again is skipped when silent, so after every update the tray
icon was gone until the next sign-in. Now PrepareToInstall records whether it
stopped a tray, and at the end of a silent install StartTray registers a task
for the Users group, runs it (which starts the tray in the session of each
person signed in) and deletes it.

The first tests read setup.iss, which is all a test can do with it off
Windows. The task tests compile the installer's own [Code] on Windows: one
writes the task file for install folders a person could choose, and one has
Task Scheduler register each file and read it back. Neither starts a tray: a
test machine has nobody signed in to show it to.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from tests import gates, inno_harness

TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
USERS_SID = "S-1-5-32-545"
KEY = r"Software\OpenAVC installer tray test"
TEST_TASK = "OpenAVC tray task test"

# Install folders a person could choose: the default, and names with an
# ampersand (XML reserves it), letters outside ASCII and one outside the BMP.
TRAY_PATHS = [
    r"C:\Program Files\OpenAVC\openavc-tray.exe",
    r"D:\AV & Control's Rooms\openavc-tray.exe",
    "D:\\Räume Übersicht\\Contrôle\\openavc-tray.exe",
    "E:\\Spaces \U0001F39B\\openavc-tray.exe",
]


def _setup_iss() -> str:
    return inno_harness.SETUP_ISS.read_text(encoding="utf-8")


def _section(text: str, name: str) -> list[str]:
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("["):
            inside = line.strip() == f"[{name}]"
            continue
        if inside and line.strip() and not line.startswith(";"):
            lines.append(line)
    return lines


def _procedure(text: str, header: str) -> str:
    start = text.index(header)
    return text[start:text.index("\nend;", start)]


# -- what setup.iss says -------------------------------------------------------


def test_prepare_to_install_records_whether_it_stopped_a_tray():
    prepare = _procedure(_setup_iss(), "function PrepareToInstall(")
    lines = [line.strip() for line in prepare.splitlines()]
    kill = lines.index(
        "Exec('taskkill.exe', '/F /IM openavc-tray.exe', '', SW_HIDE, "
        "ewWaitUntilTerminated, ResultCode);"
    )
    assert lines[kill + 1] == "TrayWasRunning := (ResultCode = 0);"


def test_only_a_silent_install_that_stopped_a_tray_starts_it_again():
    steps = _procedure(_setup_iss(), "procedure CurStepChanged(")
    assert (
        "if (CurStep = ssDone) and WizardSilent() and TrayWasRunning then\n"
        "    StartTray(ExpandConstant('{app}\\openavc-tray.exe'));"
    ) in steps


def test_the_tray_is_found_by_its_file_not_by_the_component():
    """Inno's record of the chosen components is empty on some silent upgrades,
    the path this is for, so the component would read as not installed."""
    start = _procedure(_setup_iss(), "procedure StartTray(")
    assert "if not FileExists(TrayPath) then" in start
    assert "WizardIsComponentSelected" not in start
    assert "WizardIsComponentSelected" not in _procedure(_setup_iss(), "procedure CurStepChanged(")


def test_the_task_is_deleted_once_it_has_run():
    start = _procedure(_setup_iss(), "procedure StartTray(")
    create = start.index("'/create /f /tn \"' + TrayTaskName")
    run = start.index("'/run /tn \"' + TrayTaskName")
    delete = start.index("'/delete /f /tn \"' + TrayTaskName")
    assert create < run < delete


def test_an_install_with_its_wizard_still_starts_the_tray_from_run():
    run = [line for line in _section(_setup_iss(), "Run") if "openavc-tray.exe" in line]
    assert len(run) == 1
    assert "postinstall skipifsilent" in run[0]


# -- the task, built by the installer's own code ------------------------------


def _windows_reason() -> str | None:
    if not sys.platform.startswith("win"):
        return "this is not Windows"
    if not inno_harness.iscc():
        return "Inno Setup (iscc) is not installed"
    return None


def _elevated_reason() -> str | None:
    reason = _windows_reason()
    if reason:
        return reason
    import ctypes

    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return "this process is not elevated"
    except (AttributeError, OSError) as exc:
        return f"elevation could not be checked ({exc})"
    return None


@pytest.fixture(scope="module")
def task_file(tmp_path_factory):
    """TrayTaskXml(path) as StartTray writes it, as bytes on disk."""
    gates.skip_or_fail(gates.HOST_FIREWALL, _windows_reason())
    import winreg

    exe = inno_harness.build(
        inno_harness.code_harness(
            _setup_iss(), "OpenAVC tray task check",
            [
                f"if RegQueryStringValue(HKCU, '{KEY}', 'TrayPath', TrayPath) and",
                f"  RegQueryStringValue(HKCU, '{KEY}', 'TaskFile', TaskFile) then",
                "  SaveStringToFile(TaskFile, TrayTaskXml(TrayPath), False);",
            ],
            variables=["TrayPath, TaskFile: String;"],
            run_as_invoker=True,
        ),
        tmp_path_factory.mktemp("tray_task"), "tray_task",
    )
    folder = tmp_path_factory.mktemp("tray_task_files")

    def build(tray_path: str) -> bytes:
        out = folder / f"task{TRAY_PATHS.index(tray_path)}.xml"
        out.unlink(missing_ok=True)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            winreg.SetValueEx(key, "TrayPath", 0, winreg.REG_SZ, tray_path)
            winreg.SetValueEx(key, "TaskFile", 0, winreg.REG_SZ, str(out))
        try:
            inno_harness.run(exe)
        finally:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)
        assert out.is_file(), "the installer's code wrote no task file"
        return out.read_bytes()

    return build


@pytest.mark.parametrize("tray_path", TRAY_PATHS)
def test_the_task_file_is_one_schtasks_reads(task_file, tray_path):
    """ASCII with no declaration: the one form schtasks takes from this file."""
    raw = task_file(tray_path)
    assert raw.isascii()
    assert not raw.startswith((b"<?xml", b"\xef\xbb\xbf"))


@pytest.mark.parametrize("tray_path", TRAY_PATHS)
def test_the_task_runs_the_tray_for_whoever_is_signed_in(task_file, tray_path):
    task = ET.fromstring(task_file(tray_path))
    principal = task.find(f"{TASK_NS}Principals/{TASK_NS}Principal")
    assert principal.findtext(f"{TASK_NS}GroupId") == USERS_SID
    assert principal.find(f"{TASK_NS}UserId") is None
    assert principal.findtext(f"{TASK_NS}RunLevel") == "LeastPrivilege"
    action = task.find(f"{TASK_NS}Actions/{TASK_NS}Exec")
    assert action.findtext(f"{TASK_NS}Command") == tray_path
    assert action.findtext(f"{TASK_NS}WorkingDirectory") == tray_path.rsplit("\\", 1)[0]


def test_the_task_is_never_held_back_or_stopped(task_file):
    """A task otherwise waits while on battery and is stopped after 72 hours,
    and one instance would leave every other signed-in person without a tray."""
    settings = ET.fromstring(task_file(TRAY_PATHS[0])).find(f"{TASK_NS}Settings")
    assert settings.findtext(f"{TASK_NS}DisallowStartIfOnBatteries") == "false"
    assert settings.findtext(f"{TASK_NS}StopIfGoingOnBatteries") == "false"
    assert settings.findtext(f"{TASK_NS}ExecutionTimeLimit") == "PT0S"
    assert settings.findtext(f"{TASK_NS}MultipleInstancesPolicy") == "Parallel"


@pytest.mark.parametrize("tray_path", TRAY_PATHS)
def test_task_scheduler_registers_the_task_as_written(task_file, tray_path, tmp_path):
    """Registered and read back, never run."""
    gates.skip_or_fail(gates.HOST_FIREWALL, _elevated_reason())
    source = tmp_path / "task.xml"
    source.write_bytes(task_file(tray_path))
    created = subprocess.run(
        ["schtasks", "/create", "/f", "/tn", TEST_TASK, "/xml", str(source)],
        capture_output=True, text=True, errors="replace", timeout=60,
    )
    try:
        assert created.returncode == 0, created.stdout + created.stderr
        read = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                f"$t = Get-ScheduledTask -TaskName '{TEST_TASK}'; "
                "@{ logon = $t.Principal.LogonType.ToString(); "
                "execute = $t.Actions[0].Execute; "
                "battery = $t.Settings.DisallowStartIfOnBatteries; "
                "limit = $t.Settings.ExecutionTimeLimit } | ConvertTo-Json -Compress",
            ],
            capture_output=True, timeout=60,
        )
        assert read.returncode == 0, read.stderr.decode("utf-8", "replace")
        registered = json.loads(read.stdout.decode("utf-8-sig"))
        assert registered == {
            "logon": "Group", "execute": tray_path, "battery": False, "limit": "PT0S",
        }
    finally:
        subprocess.run(
            ["schtasks", "/delete", "/f", "/tn", TEST_TASK],
            capture_output=True, timeout=60,
        )


def test_the_group_is_named_by_its_sid_in_the_code():
    """Users is translated (Benutzer, Utilisateurs); its SID is not."""
    xml = _procedure(_setup_iss(), "function TrayTaskXml(")
    assert re.search(r"<GroupId>S-1-5-32-545</GroupId>", xml)
