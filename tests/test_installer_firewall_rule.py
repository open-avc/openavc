"""A Windows update keeps the installer's firewall rule as an administrator left it.

Every update used to delete the "OpenAVC" rule and add it back allowing every
address, so a rule an administrator had limited to the AV network was opened
to everyone. setup.iss now keeps an existing rule and only points it at the
new program; a rule under that name with a port list (the TCP-8080-only rule
installers before 0.23.0 made) is still replaced.

These read the installer script, which is all a test can do with it off
Windows. The same code runs for real, against the firewall, in
test_windows_firewall_live.py.
"""

from __future__ import annotations

from tests import inno_harness


def _procedure(header: str) -> str:
    text = inno_harness.SETUP_ISS.read_text(encoding="utf-8")
    start = text.index(header)
    return text[start:text.index("\nend;", start)]


def test_an_update_no_longer_deletes_the_rule_it_keeps():
    add = _procedure("procedure AddFirewallRule(")
    assert "delete rule" not in add
    assert "KeepOrAddProgramRule('OpenAVC'," in add


def test_a_kept_rule_has_only_its_program_changed():
    """netsh "set rule ... new" changes only the fields it names, so naming
    anything but the program would overwrite what an administrator set."""
    keep = _procedure("procedure KeepOrAddProgramRule(")
    sets = [line for line in keep.splitlines() if "firewall set rule" in line]
    assert len(sets) == 1
    assert sets[0].split(" new ", 1)[1].startswith("program=")
    for field in ("remoteip", "profile", "enable", "localport", "protocol", "action"):
        assert f"{field}=" not in sets[0], f"the kept rule's {field} would be overwritten"


def test_what_is_there_is_read_from_the_rule_store_not_netsh():
    """netsh prints its listing in the language Windows is installed in."""
    kind = _procedure("function InboundRuleKind(")
    assert "RegGetValueNames(HKLM, FirewallRulesKey" in kind
    assert "netsh" not in kind
    assert "'|Name=' + RuleName + '|'" in kind, "a longer name that starts with ours would match"
