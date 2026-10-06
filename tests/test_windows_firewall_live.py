"""Windows Firewall for real: the rules the server opens, and the uninstaller.

`test_plugin_declared_ports.py` stubs the firewall, as it must. These two do
not: they add and remove real rules and run the uninstaller's own code against
them. So they run only where the run says the machine is disposable --
`OPENAVC_REQUIRE_HOST_FIREWALL=1`, which CI's Windows leg sets -- and never
merely because a shell happens to be elevated. Every rule they make is on a port
no plugin asks for, and both put back any OpenAVC plugin rule that was already
there.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import gates

pytestmark = pytest.mark.skipif(
    not gates.is_required(gates.HOST_FIREWALL),
    reason=f"changes the host firewall; set {gates.HOST_FIREWALL}=1 on a disposable machine",
)

SETUP_ISS = Path(__file__).resolve().parents[1] / "installer" / "setup.iss"

# Ports no plugin declares, so nothing here can collide with a real rule.
SERVER_PORT = 47991
UNINSTALL_UDP = 47992
UNINSTALL_TCP = 47993
DECOY_PORT = 47994
DECOY_NAME = "OpenAVC firewall test decoy"


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


def _iscc() -> str | None:
    found = shutil.which("iscc")
    if found:
        return found
    default = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
    return str(default) if default.is_file() else None


def _rule_count(name: str) -> int:
    """How many rules in the store carry exactly this name."""
    import winreg

    from openavc.system import firewall

    count = 0
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, firewall._RULES_KEY) as key:
        index = 0
        while True:
            try:
                _, value, kind = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if kind == winreg.REG_SZ and firewall._rule_string_name(value) == name:
                count += 1
    return count


def _ours_already_there() -> list[dict]:
    """The OpenAVC plugin rules this machine had before the test, as entries."""
    from openavc.system import firewall

    entries = []
    for name in firewall._existing_rule_names():
        if not name.startswith(firewall.RULE_PREFIX + " "):
            continue
        parts = name.split()
        try:
            entries.append({"port": int(parts[-1]), "protocol": parts[-2].lower()})
        except (ValueError, IndexError):
            continue
    return entries


def _netsh(*args: str) -> tuple[bool, str]:
    from openavc.system import firewall

    return firewall._netsh(["advfirewall", "firewall", *args])


def _add_rule(name: str, protocol: str, port: int, description: str | None = None) -> None:
    args = ["add", "rule", f"name={name}", "dir=in", "action=allow",
            f"protocol={protocol.upper()}", f"localport={port}"]
    if description:
        args.append(f"description={description}")
    ok, out = _netsh(*args)
    assert ok, f"netsh could not add {name!r}: {out}"


@pytest.fixture
def host():
    gates.skip_or_fail(gates.HOST_FIREWALL, _host_reason())


def test_a_rule_the_server_opens_is_found_and_closed(host):
    """The store read sees a rule netsh made, by name, once; and the close removes it."""
    from openavc.system import firewall

    name = firewall.rule_name(SERVER_PORT, "udp")
    keep = _ours_already_there()
    wanted = keep + [{"port": SERVER_PORT, "protocol": "udp"}]
    try:
        report = firewall.sync(wanted)
        assert report["refused"] == []
        assert report["opened"] == [f"{SERVER_PORT}/udp"]
        assert name in firewall._existing_rule_names()
        assert _rule_count(name) == 1

        report = firewall.sync(wanted)
        assert report["opened"] == [], "a rule it had already opened was added again"
        assert _rule_count(name) == 1

        report = firewall.sync(keep)
        assert report["closed"] == [f"{SERVER_PORT}/udp"]
        assert _rule_count(name) == 0
        found, _ = _netsh("show", "rule", f"name={name}")
        assert not found, "netsh still lists the rule after it was closed"
    finally:
        _netsh("delete", "rule", f"name={name}")
        firewall.sync(keep)


def uninstall_harness(setup_iss: str) -> str:
    """A setup script holding the installer's real [Code], that runs the
    uninstaller's plugin-rule removal as soon as it starts, then stops.

    Built from the whole [Code] section rather than one procedure copied out,
    so this also compiles everything else in it on every push; CI otherwise
    compiles setup.iss only when it builds a release.
    """
    code = setup_iss[setup_iss.index("[Code]"):]
    assert "function InitializeSetup" not in code, (
        "setup.iss now has its own InitializeSetup; give this harness another way in"
    )
    setup = setup_iss[setup_iss.index("[Setup]"):setup_iss.index("[Code]")]
    privileges = next(
        line for line in setup.splitlines() if line.startswith("PrivilegesRequired=")
    )
    arch = [line for line in setup.splitlines() if line.startswith("Architectures")]
    return "\n".join([
        '#define MyAppVersion "0.0.0"',
        "[Setup]",
        "AppName=OpenAVC uninstall check",
        "AppVersion=0.0.0",
        r"DefaultDirName={autopf}\OpenAVC uninstall check",
        "Uninstallable=no",
        privileges,
        *arch,
        "",
        code.rstrip(),
        "",
        "function InitializeSetup(): Boolean;",
        "begin",
        "  RemovePluginFirewallRules();",
        "  Result := False;",
        "end;",
        "",
    ])


def test_the_uninstaller_removes_every_plugin_rule_and_nothing_else(host, tmp_path):
    from openavc.system import firewall

    iscc = _iscc()
    gates.skip_or_fail(gates.HOST_FIREWALL, None if iscc else "Inno Setup (iscc) is not installed")

    udp = firewall.rule_name(UNINSTALL_UDP, "udp")
    tcp = firewall.rule_name(UNINSTALL_TCP, "tcp")
    keep = _ours_already_there()

    script = tmp_path / "uninstall_check.iss"
    script.write_text(uninstall_harness(SETUP_ISS.read_text(encoding="utf-8")), encoding="utf-8")
    built = subprocess.run(
        [iscc, "/Q", f"/O{tmp_path}", "/Funinstall_check", str(script)],
        capture_output=True, text=True, errors="replace", timeout=300,
    )
    assert built.returncode == 0, f"the installer's [Code] does not compile:\n{built.stdout}\n{built.stderr}"

    try:
        _add_rule(udp, "udp", UNINSTALL_UDP)
        _add_rule(udp, "udp", UNINSTALL_UDP)  # a name twice, as the old sync could leave it
        _add_rule(tcp, "tcp", UNINSTALL_TCP)
        _add_rule(DECOY_NAME, "udp", DECOY_PORT, description=udp)
        assert _rule_count(udp) == 2 and _rule_count(tcp) == 1 and _rule_count(DECOY_NAME) == 1

        subprocess.run(
            [str(tmp_path / "uninstall_check.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            capture_output=True, timeout=300,
        )
        deadline = time.monotonic() + 30
        while (_rule_count(udp) or _rule_count(tcp)) and time.monotonic() < deadline:
            time.sleep(0.5)

        assert _rule_count(udp) == 0
        assert _rule_count(tcp) == 0
        assert _rule_count(DECOY_NAME) == 1, "a rule that only mentions ours in its description was removed"
        assert not any(n.startswith(firewall.RULE_PREFIX + " ") for n in firewall._existing_rule_names())
    finally:
        for name in (udp, tcp, DECOY_NAME):
            _netsh("delete", "rule", f"name={name}")
        firewall.sync(keep)
