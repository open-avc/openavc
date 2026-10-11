"""Windows Firewall for real: the rules the server opens, the installer's own
rule, and the uninstaller.

`test_plugin_declared_ports.py` stubs the firewall, as it must. These do not:
they add and remove real rules and run the installer's own code against them.
So they run only where the run says the machine is disposable --
`OPENAVC_REQUIRE_HOST_FIREWALL=1`, which CI's Windows leg sets -- and never
merely because a shell happens to be elevated. Every rule they make is on a port
no plugin asks for or under a name of its own, and they put back any OpenAVC
plugin rule that was already there.
"""

from __future__ import annotations

import sys
import time

import pytest

from tests import gates, inno_harness

pytestmark = pytest.mark.skipif(
    not gates.is_required(gates.HOST_FIREWALL),
    reason=f"changes the host firewall; set {gates.HOST_FIREWALL}=1 on a disposable machine",
)

# Ports no plugin declares, so nothing here can collide with a real rule.
SERVER_PORT = 47991
UNINSTALL_UDP = 47992
UNINSTALL_TCP = 47993
DECOY_PORT = 47994
DECOY_NAME = "OpenAVC firewall test decoy"
# The installer's rule is "OpenAVC"; its code is run here under this name.
PROGRAM_RULE = "OpenAVC installer rule test"


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


def _rules(name: str) -> list[dict[str, list[str]]]:
    """Every rule in the store with exactly this name, field by field. A
    field can repeat (one LPort per port), so each holds a list."""
    import winreg

    from openavc.system import firewall

    found = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, firewall._RULES_KEY) as key:
        index = 0
        while True:
            try:
                _, value, kind = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if kind != winreg.REG_SZ or firewall._rule_string_name(value) != name:
                continue
            fields: dict[str, list[str]] = {}
            for part in value.split("|")[1:]:
                if "=" in part:
                    field, text = part.split("=", 1)
                    fields.setdefault(field, []).append(text)
            found.append(fields)
    return found


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


def test_the_uninstaller_removes_every_plugin_rule_and_nothing_else(host, tmp_path):
    from openavc.system import firewall

    gates.skip_or_fail(
        gates.HOST_FIREWALL, None if inno_harness.iscc() else "Inno Setup (iscc) is not installed",
    )

    udp = firewall.rule_name(UNINSTALL_UDP, "udp")
    tcp = firewall.rule_name(UNINSTALL_TCP, "tcp")
    keep = _ours_already_there()

    exe = inno_harness.build(
        inno_harness.code_harness(
            inno_harness.SETUP_ISS.read_text(encoding="utf-8"),
            "OpenAVC uninstall check", ["RemovePluginFirewallRules();"],
        ),
        tmp_path, "uninstall_check",
    )

    try:
        _add_rule(udp, "udp", UNINSTALL_UDP)
        _add_rule(udp, "udp", UNINSTALL_UDP)  # a name twice, as the old sync could leave it
        _add_rule(tcp, "tcp", UNINSTALL_TCP)
        _add_rule(DECOY_NAME, "udp", DECOY_PORT, description=udp)
        assert _rule_count(udp) == 2 and _rule_count(tcp) == 1 and _rule_count(DECOY_NAME) == 1

        inno_harness.run(exe)
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


def _names(app: list[str], program) -> bool:
    """Whether a rule's App field is this program, however the path is spelt
    (a runner's TEMP can be a short 8.3 path)."""
    import os

    return len(app) == 1 and os.path.exists(app[0]) and os.path.samefile(app[0], program)


def test_an_update_keeps_the_rule_an_administrator_scoped(host, tmp_path):
    """The installer's rule code, run under a name of its own: it adds the
    rule where there is none, keeps one an administrator restricted and only
    points it at the new program, and replaces the TCP-8080-only rule that
    installers before 0.23.0 made."""
    gates.skip_or_fail(
        gates.HOST_FIREWALL, None if inno_harness.iscc() else "Inno Setup (iscc) is not installed",
    )
    old = tmp_path / "old" / "openavc-server.exe"
    new = tmp_path / "new" / "openavc-server.exe"
    for program in (old, new):
        program.parent.mkdir()
        program.write_bytes(b"")
    exe = inno_harness.build(
        inno_harness.code_harness(
            inno_harness.SETUP_ISS.read_text(encoding="utf-8"),
            "OpenAVC rule check", [f"KeepOrAddProgramRule('{PROGRAM_RULE}', '{new}');"],
        ),
        tmp_path, "rule_check",
    )
    lookalike = f"{PROGRAM_RULE} (TCP)"  # a longer name that starts with ours
    try:
        _add_rule(lookalike, "tcp", DECOY_PORT)
        lookalike_before = _rules(lookalike)

        inno_harness.run(exe)
        [rule] = _rules(PROGRAM_RULE)
        assert _names(rule["App"], new)
        assert rule["Action"] == ["Allow"] and rule["Dir"] == ["In"] and rule["Active"] == ["TRUE"]
        for field in rule:
            assert not field.startswith(("LPort", "Protocol", "RA4", "RA6", "Profile")), (
                f"a new rule is limited by {field}: {rule}"
            )

        ok, out = _netsh(
            "set", "rule", f"name={PROGRAM_RULE}", "dir=in", "new", f"program={old}",
            "remoteip=10.20.30.0/24", "profile=private", "enable=no",
        )
        assert ok, f"netsh could not scope the rule: {out}"
        inno_harness.run(exe)
        [rule] = _rules(PROGRAM_RULE)
        assert _names(rule["App"], new), f"the kept rule does not name the new program: {rule}"
        assert [ra for ra in rule.get("RA4", []) if ra.startswith("10.20.30.0")], (
            f"the addresses the rule allowed were lost: {rule}"
        )
        assert rule["Profile"] == ["Private"]
        assert rule["Active"] == ["FALSE"]
        assert _rules(lookalike) == lookalike_before

        _netsh("delete", "rule", f"name={PROGRAM_RULE}")
        ok, out = _netsh(
            "add", "rule", f"name={PROGRAM_RULE}", "dir=in", "action=allow",
            "protocol=TCP", "localport=8080", f"program={old}",
        )
        assert ok, f"netsh could not add the old installers' rule: {out}"
        inno_harness.run(exe)
        [rule] = _rules(PROGRAM_RULE)
        assert _names(rule["App"], new)
        for field in rule:
            assert not field.startswith(("LPort", "Protocol")), (
                f"the TCP-8080-only rule was kept: {rule}"
            )
        assert _rules(lookalike) == lookalike_before
    finally:
        for name in (PROGRAM_RULE, lookalike):
            _netsh("delete", "rule", f"name={name}")
