"""The Windows installer restricts the data folder to the service and administrators.

A folder made under C:\\ProgramData takes its permissions from it, and those
let every local account read the files and create new ones in the subfolders:
the cloud key in system.json, device passwords in the project and the TLS key
were readable by anyone signed in to the PC, and a .py file put in driver_repo
ran as Local System at the service's next start. installer/secure-data-dir.bat
locks the folder at every install and update; install-service.bat calls it
while the service is stopped.

The first tests read the installer files, which is all a test can do with them
off Windows. The last one runs the script itself against a folder built the
way an older install leaves one, including a file a local account planted and
still owns. That needs an elevated Windows shell, which CI's Windows leg is
(the HOST_FIREWALL gate is its promise of one); it touches nothing outside its
temporary folder.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from tests import gates

INSTALLER = Path(__file__).resolve().parents[1] / "installer"
SECURE_BAT = INSTALLER / "secure-data-dir.bat"
INSTALL_SERVICE_BAT = INSTALLER / "install-service.bat"
SETUP_ISS = INSTALLER / "setup.iss"

SYSTEM, ADMINS, USERS = "S-1-5-18", "S-1-5-32-544", "S-1-5-32-545"
# The SDDL aliases Windows prints for those three.
ALIAS = {"SY": SYSTEM, "BA": ADMINS, "BU": USERS}
READ_EXECUTE = 0x1200A9
FULL = "FA"


def _commands(text: str) -> list[str]:
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().upper().startswith(("REM", "@ECHO"))
    ]


def test_the_service_install_locks_the_folder_while_the_service_is_stopped():
    lines = _commands(INSTALL_SERVICE_BAT.read_text(encoding="utf-8"))
    call = 'call "%INSTALL_DIR%\\secure-data-dir.bat" "%DATA_DIR%"'
    stop = '"%INSTALL_DIR%\\nssm.exe" stop OpenAVC >nul 2>&1'
    start = '"%INSTALL_DIR%\\nssm.exe" start OpenAVC'
    assert call in lines, "install-service.bat no longer runs secure-data-dir.bat"
    assert lines.index(stop) < lines.index(call) < lines.index(start)


def test_the_installer_ships_the_script_on_every_install():
    """Not gated on a component: Inno skips a gated file on some silent upgrades."""
    entries = [
        line for line in SETUP_ISS.read_text(encoding="utf-8").splitlines()
        if line.startswith("Source:") and '"secure-data-dir.bat"' in line
    ]
    assert len(entries) == 1
    assert 'DestDir: "{app}"' in entries[0]
    assert "Components:" not in entries[0]


def test_every_account_is_named_by_sid():
    """A name such as Users or Administrators is translated on a non-English Windows."""
    lines = _commands(SECURE_BAT.read_text(encoding="utf-8"))
    icacls = [line for line in lines if line.lower().startswith("icacls")]
    assert icacls, "secure-data-dir.bat runs no icacls"
    for line in icacls:
        for account in re.findall(r'(?:/grant:r|/setowner)\s+((?:"[^"]*"\s*)+)', line):
            for item in re.findall(r'"([^"]*)"', account):
                assert item.startswith("*S-1-"), f"not a SID: {item!r} in {line!r}"


def test_an_empty_argument_stops_before_anything_is_changed():
    """Without it, an empty %1 makes "%DATA_DIR%\\status" the root of the drive."""
    lines = _commands(SECURE_BAT.read_text(encoding="utf-8"))
    guard = lines.index('if "%DATA_DIR%"=="" exit /b 1')
    first_change = min(
        i for i, line in enumerate(lines) if line.lower().startswith(("icacls", "if not exist"))
    )
    assert guard < first_change


# -- the script itself, on Windows -------------------------------------------


def _host_reason() -> str | None:
    if not sys.platform.startswith("win"):
        return "this is not Windows"
    import ctypes

    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return "this process is not elevated"
    except (AttributeError, OSError) as exc:
        return f"elevation could not be checked ({exc})"
    return None


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _sddl_of(path: str) -> str:
    """The owner and DACL of one path, as SDDL, read through the Windows API.

    Not through PowerShell: Windows PowerShell started from a PowerShell 7
    session (CI's test step) inherits its module path, cannot load Get-Acl,
    and still exits 0.
    """
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]

    file_object, owner_and_dacl = 1, 0x1 | 0x4
    descriptor = ctypes.c_void_p()
    status = advapi.GetNamedSecurityInfoW(
        path, file_object, owner_and_dacl, None, None, None, None, ctypes.byref(descriptor),
    )
    if status:
        raise ctypes.WinError(status)
    try:
        text = ctypes.c_void_p()
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, owner_and_dacl, ctypes.byref(text), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.wstring_at(text.value)
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.LocalFree(descriptor)


def _sddl(root: Path) -> dict[str, str]:
    """Owner and DACL of the folder and everything in it, hidden files included."""
    found = {".": _sddl_of(str(root))}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            full = os.path.join(folder, name)
            found[os.path.relpath(full, root)] = _sddl_of(full)
    return found


def _owner(sddl: str) -> str:
    sid = re.match(r"O:([^:]+?)(?:G:|D:)", sddl).group(1)
    return ALIAS.get(sid, sid)


def _protected(sddl: str) -> bool:
    return bool(re.search(r"D:[A-Z]*P", sddl))


def _aces(sddl: str) -> set[tuple[str, bool, str, str]]:
    """(kind, inherited, rights, sid) for each entry of the DACL."""
    dacl = sddl.split("D:", 1)[1]
    found = set()
    for ace in re.findall(r"\(([^)]*)\)", dacl):
        kind, flags, rights, _obj, _inh, sid = ace.split(";")[:6]
        found.add((kind, "ID" in flags, rights, ALIAS.get(sid, sid)))
    return found


def _rights(rights: str) -> str:
    """One spelling for a mask Windows may print either as an alias or in hex."""
    return f"{int(rights, 16):#x}" if rights.startswith("0x") else rights


def _plant(root: Path, user_sid: str) -> Path:
    """A data folder as an older install leaves it, with a file a local account owns."""
    for sub in ("projects/default", "driver_repo", "plugin_repo/.deps", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "system.json").write_text("{}", encoding="utf-8")
    (root / "projects/default/project.avc").write_text("{}", encoding="utf-8")
    (root / "plugin_repo/.deps/lib.py").write_text("", encoding="utf-8")
    hidden = root / "projects/default/.instance_id"
    hidden.write_text("x", encoding="utf-8")
    _run(["attrib", "+h", str(hidden)])
    _run(["attrib", "+h", str(root / "plugin_repo/.deps")])
    planted = root / "driver_repo" / "planted.py"
    planted.write_text("", encoding="utf-8")
    assert _run(["icacls", str(planted), "/grant", "*S-1-1-0:(F)"]).returncode == 0
    assert _run(["icacls", str(planted), "/setowner", f"*{user_sid}"]).returncode == 0
    assert _owner(_sddl(root)["driver_repo\\planted.py"]) == user_sid
    return planted


def _user_sid() -> str:
    out = _run(["whoami", "/user", "/fo", "csv", "/nh"])
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().split(",")[-1].strip('"')


def test_the_script_locks_an_existing_folder_and_takes_back_a_planted_file(tmp_path):
    gates.skip_or_fail(gates.HOST_FIREWALL, _host_reason())
    root = tmp_path / "OpenAVC"
    _plant(root, _user_sid())

    inherited = {("A", True, FULL, SYSTEM), ("A", True, FULL, ADMINS)}
    users_read = f"{READ_EXECUTE:#x}"
    # Run twice: the install, then an update over the folder it left.
    for run in ("install", "update"):
        result = _run([str(SECURE_BAT), str(root)])
        assert result.returncode == 0, f"{run}: {result.stdout}{result.stderr}"

        acl = _sddl(root)
        top = acl.pop(".")
        assert _protected(top), f"{run}: the folder still inherits from its parent"
        assert {(k, i, _rights(r), s) for k, i, r, s in _aces(top)} == {
            ("A", False, FULL, SYSTEM), ("A", False, FULL, ADMINS),
        }
        assert "status" in acl, f"{run}: no status folder"
        for path, sddl in acl.items():
            assert _owner(sddl) == ADMINS, f"{run}: {path} is owned by {_owner(sddl)}"
            aces = {(k, i, _rights(r), s) for k, i, r, s in _aces(sddl)}
            expected = set(inherited)
            if path == "status":
                expected.add(("A", False, users_read, USERS))
            elif path.startswith("status\\"):
                expected.add(("A", True, users_read, USERS))
            assert aces == expected, f"{run}: {path} has {sorted(aces)}"

        # A file the server writes into status afterwards: every account can read it.
        written = root / "status" / "server.json"
        written.write_text("{}", encoding="utf-8")
        aces = {(k, i, _rights(r), s) for k, i, r, s in _aces(_sddl(root)["status\\server.json"])}
        assert aces == inherited | {("A", True, users_read, USERS)}, f"{run}: {sorted(aces)}"
