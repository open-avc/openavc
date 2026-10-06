"""A plugin can ask for a port, and the ask has to survive the whole way out.

The Video Panel's sidecar binds UDP 8189 for WebRTC, which is the only path a
panel in the room has. Nothing opened it: Windows scopes its installer rule to
`openavc-server.exe` and the port belongs to `mediamtx.exe`, and the Linux
helper only ever opened TCP. Every test of that feature ran loopback to
loopback, where no firewall is consulted, so nothing caught it -- and on Windows
it fails silently, because a service has no session for a firewall prompt.

These pin the declaration itself. The Linux helper reads the file this writes
and is exercised separately by its own shell dry-run.
"""

import json
import subprocess
import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

import pytest

from openavc.core.plugin_ports import (
    PORTS_FILENAME,
    collect,
    declared_ports,
    read,
    validate_declaration,
    write,
)


def _plugin(ports, **info):
    base = {
        "id": "demo", "name": "Demo", "version": "1.0.0", "author": "x",
        "description": "d", "category": "integration", "license": "MIT",
        "capabilities": ["network_listen"],
    }
    base.update(info)
    if ports is not None:
        base["network_ports"] = ports
    return type("DemoPlugin", (), {"PLUGIN_INFO": base})


# ── The declaration ──────────────────────────────────────────────────────────


def test_a_well_formed_declaration_is_accepted():
    ok, err = validate_declaration(
        [{"port": 8189, "protocol": "udp", "reason": "WebRTC video to panels"}]
    )
    assert ok, err


def test_a_reason_is_required():
    """It is what an administrator reads when they find a port open and want to
    know whether it may be closed. A port with no stated reason is one nobody
    can safely retire."""
    ok, err = validate_declaration([{"port": 8189, "protocol": "udp"}])
    assert not ok
    assert "reason" in err


@pytest.mark.parametrize("port", [0, -1, 65536, "8189", None, True])
def test_a_port_that_is_not_a_port_is_refused(port):
    ok, _ = validate_declaration([{"port": port, "reason": "r"}])
    assert not ok


def test_a_protocol_we_do_not_open_is_refused():
    ok, err = validate_declaration([{"port": 500, "protocol": "sctp", "reason": "r"}])
    assert not ok and "protocol" in err


@pytest.mark.parametrize("port", [22, 3389, 445])
def test_a_plugin_may_not_ask_for_a_port_that_belongs_to_the_host(port):
    """A typo must not open SSH. These are refused by number, before anything
    reaches a firewall."""
    ok, err = validate_declaration([{"port": port, "reason": "please no"}])
    assert not ok
    assert str(port) in err


def test_the_same_port_twice_is_refused():
    ok, _ = validate_declaration([
        {"port": 8189, "protocol": "udp", "reason": "a"},
        {"port": 8189, "protocol": "udp", "reason": "b"},
    ])
    assert not ok


def test_the_same_port_on_different_protocols_is_fine():
    ok, err = validate_declaration([
        {"port": 8189, "protocol": "udp", "reason": "media"},
        {"port": 8189, "protocol": "tcp", "reason": "signalling"},
    ])
    assert ok, err


def test_a_malformed_declaration_is_refused_not_trimmed():
    """Dropping the bad entry and keeping the rest would ship a plugin that
    asked for a port, did not get it, and failed on a network we cannot see."""
    ok, _ = validate_declaration([
        {"port": 8189, "protocol": "udp", "reason": "good"},
        {"port": 70000, "reason": "bad"},
    ])
    assert not ok


# ── Reading it off a plugin, and merging ─────────────────────────────────────


def test_a_plugin_that_declares_nothing_asks_for_nothing():
    assert declared_ports(_plugin(None)) == []


def test_protocol_defaults_to_tcp():
    assert declared_ports(_plugin([{"port": 9000, "reason": "r"}]))[0]["protocol"] == "tcp"


def test_two_plugins_wanting_one_port_open_it_once_and_both_are_named():
    entries = collect({
        "a": _plugin([{"port": 8189, "protocol": "udp", "reason": "video"}]),
        "b": _plugin([{"port": 8189, "protocol": "udp", "reason": "also video"}]),
    })
    assert len(entries) == 1
    assert entries[0]["plugins"] == ["a", "b"]
    assert "video" in entries[0]["reason"] and "also video" in entries[0]["reason"]


def test_collect_takes_instances_as_well_as_classes():
    """The loader holds running instances, not classes."""
    cls = _plugin([{"port": 8189, "protocol": "udp", "reason": "video"}])
    assert collect({"a": cls()}) == collect({"a": cls})


# ── The file the Linux helper reads ──────────────────────────────────────────


def test_the_file_is_written_where_the_helper_looks(tmp_path):
    entries = collect({"a": _plugin([{"port": 8189, "protocol": "udp", "reason": "v"}])})
    path = write(tmp_path, entries)
    assert path == tmp_path / PORTS_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ports"][0]["port"] == 8189
    assert payload["ports"][0]["protocol"] == "udp"
    assert read(tmp_path) == entries


def test_an_empty_list_is_still_written(tmp_path):
    """This is how a port gets CLOSED. Skipping the write when nothing is
    declared would leave the last non-empty list standing forever, and a rule
    that outlives the plugin that asked for it is worse than never opening
    one."""
    write(tmp_path, [{"port": 8189, "protocol": "udp", "plugins": ["a"], "reason": "v"}])
    write(tmp_path, [])
    assert read(tmp_path) == []


def test_reading_a_missing_or_corrupt_file_is_not_an_error(tmp_path):
    assert read(tmp_path) == []
    (tmp_path / PORTS_FILENAME).write_text("{not json", encoding="utf-8")
    assert read(tmp_path) == []


def test_the_file_says_what_it_is_for(tmp_path):
    """Somebody will find it and wonder whether editing it changes anything."""
    write(tmp_path, [])
    payload = json.loads((tmp_path / PORTS_FILENAME).read_text(encoding="utf-8"))
    assert "comment" in payload


# ── The Windows half ─────────────────────────────────────────────────────────
#
# NOTHING below may call the real `sync()` on Windows without stubbing the netsh
# layer first. It changes the host firewall, and it succeeds whenever the test
# runner happens to be elevated -- which a developer's shell usually is not and
# CI usually is. That asymmetry is exactly how the first version of this file
# passed here and then opened UDP 8189 on a GitHub runner. The rule store is
# stubbed too, so what a test sees does not depend on the runner's own rules.


@pytest.fixture
def fake_firewall(monkeypatch):
    """Record what would be run, and let the test say what netsh replies and
    which rules the store holds.

    Also the only way to reach the success path at all: it needs elevation,
    so on an ordinary developer machine the real thing can only ever fail.
    """
    from openavc.system import firewall

    fake = SimpleNamespace(
        calls=[],
        replies={"show": (True, ""), "add": (True, ""), "delete": (True, "")},
        store=set(),
    )

    def _fake(args):
        fake.calls.append(args)
        verb = next((a for a in args if a in ("show", "add", "delete")), "")
        return fake.replies.get(verb, (True, ""))

    monkeypatch.setattr(firewall, "_netsh", _fake)
    monkeypatch.setattr(firewall, "_existing_rule_names", lambda: set(fake.store))
    monkeypatch.setattr(firewall.sys, "platform", "win32")
    return fake


@pytest.mark.skipif(sys.platform.startswith("win"), reason="tests the non-Windows branch")
def test_off_windows_nothing_is_touched():
    """Linux and macOS get their ports from the helper that runs as root before
    the server; this module must do nothing at all there."""
    from openavc.system import firewall

    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["opened"] == [] and report["closed"] == []


def test_a_declared_port_is_added(fake_firewall):
    from openavc.system import firewall

    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["opened"] == ["8189/udp"]
    add = next(c for c in fake_firewall.calls if "add" in c)
    assert "protocol=UDP" in add and "localport=8189" in add
    assert f"name={firewall.rule_name(8189, 'udp')}" in add


def test_a_port_nobody_asks_for_any_more_is_removed(fake_firewall):
    """The half that makes the declaration honest."""
    from openavc.system import firewall

    fake_firewall.store.add(firewall.rule_name(8189, "udp"))
    report = firewall.sync([])
    assert report["closed"] == ["8189/udp"]
    assert any("delete" in c for c in fake_firewall.calls)


def test_a_rule_already_present_is_not_added_twice(fake_firewall):
    from openavc.system import firewall

    fake_firewall.store.add(firewall.rule_name(8189, "udp"))
    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["opened"] == [] and report["closed"] == []
    assert not any("add" in c for c in fake_firewall.calls)


def test_rules_that_are_not_ours_are_left_alone(fake_firewall):
    """An administrator's own rule for the same port must never be removed."""
    from openavc.system import firewall

    fake_firewall.store.update({"Some other thing UDP 8189", "OpenAVC"})
    firewall.sync([])
    assert not any("delete" in c for c in fake_firewall.calls)


# netsh prints its listing in the language Windows is installed in. Reading it
# for "Rule Name:" found nothing on this install, so every sync added the rule
# again and none was ever closed. These put that listing where the old code
# looked, and the rule in the store, to pin that the answer no longer depends
# on netsh's language.
_GERMAN_SHOW_RULE = (
    "\n"
    "Regelname:                            OpenAVC plugin UDP 8189\n"
    "----------------------------------------------------------------------\n"
    "Aktiviert:                            Ja\n"
    "Richtung:                             Eingehend\n"
    "Protokoll:                            UDP\n"
    "Lokaler Port:                         8189\n"
    "Aktion:                               Zulassen\n"
    "Ok.\n"
)


def test_a_german_windows_closes_a_port_nobody_asks_for(fake_firewall):
    from openavc.system import firewall

    fake_firewall.replies["show"] = (True, _GERMAN_SHOW_RULE)
    fake_firewall.store.add(firewall.rule_name(8189, "udp"))
    report = firewall.sync([])
    assert report["closed"] == ["8189/udp"]


def test_a_german_windows_does_not_add_a_rule_it_already_has(fake_firewall):
    from openavc.system import firewall

    fake_firewall.replies["show"] = (True, _GERMAN_SHOW_RULE)
    fake_firewall.store.add(firewall.rule_name(8189, "udp"))
    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["opened"] == []
    assert not any("add" in c for c in fake_firewall.calls)


def test_nothing_reads_netshs_listing(fake_firewall):
    """The listing is translated; netsh is asked only to add and delete,
    which answer by exit code."""
    from openavc.system import firewall

    fake_firewall.store.add(firewall.rule_name(9000, "tcp"))
    firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert fake_firewall.calls
    assert not any("show" in c for c in fake_firewall.calls)


def test_a_refusal_is_reported_rather_than_raised(fake_firewall):
    """Running unelevated is the normal developer case and must not look like
    a crash -- the caller degrades, it does not fail."""
    from openavc.system import firewall

    fake_firewall.replies["add"] = (False, "The requested operation requires elevation")
    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["refused"] == ["8189/udp"]
    assert report["opened"] == []


def test_the_manual_command_is_one_a_person_can_paste():
    from openavc.system import firewall

    cmd = firewall.manual_command(8189, "udp")
    assert cmd.startswith("netsh advfirewall firewall add rule")
    assert "protocol=UDP" in cmd and "localport=8189" in cmd


def test_rule_names_carry_a_prefix_so_nothing_else_is_touched():
    from openavc.system import firewall

    name = firewall.rule_name(8189, "udp")
    assert name.startswith(firewall.RULE_PREFIX)
    assert name.endswith("8189")


# ── Reading the rule store ───────────────────────────────────────────────────
#
# Each value under the store's key is one rule, a `|`-separated string whose
# field names do not change with the language Windows is installed in. These
# are shaped like what a German install holds.

_STORE = {
    "{1A2B}": "v2.30|Action=Allow|Active=TRUE|Dir=In|Protocol=17|LPort=8189"
              "|Name=OpenAVC plugin UDP 8189|",
    # A rule an administrator disabled is still there.
    "{3C4D}": "v2.30|Action=Allow|Active=FALSE|Dir=In|Protocol=6|LPort=9000"
              "|Name=OpenAVC plugin TCP 9000|",
    # The installer's own rule, scoped to the program.
    "{5E6F}": "v2.30|Action=Allow|Active=TRUE|Dir=In"
              "|App=C:\\Program Files\\OpenAVC\\openavc-server.exe|Name=OpenAVC|",
    # A built-in rule names itself by resource string.
    "WINRM-HTTP-In-TCP": "v2.30|Action=Allow|Active=FALSE|Dir=In|Protocol=6"
                         "|LPort=5985|App=System|Name=@FirewallAPI.dll,-35001"
                         "|Desc=@FirewallAPI.dll,-35002|EmbedCtxt=@FirewallAPI.dll,-35000|",
    # Somebody else's rule whose description happens to read like ours.
    "{7A8B}": "v2.30|Action=Allow|Active=TRUE|Dir=In|Protocol=17|LPort=8189"
              "|Name=Übertragungsdienst|Desc=OpenAVC plugin UDP 8189 (nicht von OpenAVC)|",
}


def test_a_rule_string_gives_its_name_and_nothing_else():
    from openavc.system import firewall

    names = {firewall._rule_string_name(v) for v in _STORE.values()}
    assert names == {
        "OpenAVC plugin UDP 8189",
        "OpenAVC plugin TCP 9000",
        "OpenAVC",
        "@FirewallAPI.dll,-35001",
        "Übertragungsdienst",
    }
    assert firewall._rule_string_name("v2.30|Action=Allow|Dir=In|") is None


def test_only_our_names_become_rules_to_reconcile(fake_firewall):
    """A description that reads like one of our names is not one of our rules."""
    from openavc.system import firewall

    fake_firewall.store.update(firewall._rule_string_name(v) for v in _STORE.values())
    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["opened"] == []
    assert report["closed"] == ["9000/tcp"]
    deleted = [c for c in fake_firewall.calls if "delete" in c]
    assert deleted == [[
        "advfirewall", "firewall", "delete", "rule",
        f"name={firewall.rule_name(9000, 'tcp')}",
    ]]


class _FakeWinreg(ModuleType):
    """Just enough of `winreg` to walk one key's values, on any platform."""

    HKEY_LOCAL_MACHINE = object()
    REG_SZ = 1
    REG_DWORD = 4

    def __init__(self, values=None, open_error=None):
        super().__init__("winreg")
        self.values = values or []
        self.open_error = open_error
        self.opened = []

    def OpenKey(self, hive, path):  # noqa: N802 -- winreg's own name
        if self.open_error is not None:
            raise self.open_error
        self.opened.append((hive, path))
        return nullcontext(self)

    def EnumValue(self, key, index):  # noqa: N802 -- winreg's own name
        if index >= len(self.values):
            raise OSError(259, "No more data is available")
        return self.values[index]


def test_the_store_is_read_whole_from_the_local_rule_key(monkeypatch):
    from openavc.system import firewall

    fake = _FakeWinreg(
        [(k, v, _FakeWinreg.REG_SZ) for k, v in _STORE.items()]
        + [("NotARule", 7, _FakeWinreg.REG_DWORD)]
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    names = firewall._existing_rule_names()
    assert names == {firewall._rule_string_name(v) for v in _STORE.values()}
    assert fake.opened == [(_FakeWinreg.HKEY_LOCAL_MACHINE, firewall._RULES_KEY)]
    assert firewall._RULES_KEY.endswith(r"\FirewallPolicy\FirewallRules")


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="reads the real rule store")
def test_the_real_store_holds_rule_strings_with_names():
    """Read-only. A Windows install ships hundreds of built-in rules, named by
    resource string, so an empty answer here means the key or the field is wrong
    and the module would see none of its own rules either."""
    from openavc.system import firewall

    names = firewall._existing_rule_names()
    assert names, f"no rules read from HKLM\\{firewall._RULES_KEY}"
    assert any(n.startswith("@") for n in names)


@pytest.mark.parametrize("error", [PermissionError(5, "Access is denied"),
                                   FileNotFoundError(2, "not found")])
def test_a_store_that_cannot_be_read_is_empty_not_an_error(monkeypatch, error):
    from openavc.system import firewall

    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(open_error=error))
    assert firewall._existing_rule_names() == set()


def test_netsh_output_in_another_code_page_does_not_abort_a_sync(monkeypatch):
    """netsh writes the OEM code page. A byte that is not a character in the
    code page Python would read it in must reach the log, not raise."""
    from openavc.system import firewall

    real_run = subprocess.run
    script = (
        "import sys; sys.stdout.buffer.write(b'Regel \\x81\\xfe abgelehnt\\n'); "
        "sys.exit(1)"
    )

    def _run(argv, **kwargs):
        assert argv[0] == "netsh"
        return real_run([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(firewall.subprocess, "run", _run)
    ok, out = firewall._netsh(["advfirewall", "firewall", "add", "rule"])
    assert ok is False
    assert out.startswith("Regel ") and out.endswith(" abgelehnt")


def test_the_uninstaller_removes_the_rules_the_server_opens():
    """Each plugin rule is scoped by port, not by program, so one the uninstaller
    leaves behind keeps that port open for anything on the computer. It reads
    the same store for the same names; this pins the two together."""
    from pathlib import Path

    from openavc.system import firewall

    iss = (Path(__file__).resolve().parents[1] / "installer" / "setup.iss").read_text(
        encoding="utf-8"
    )
    assert f"FirewallRulesKey = '{firewall._RULES_KEY}';" in iss
    assert f"PluginRuleMarker = '|Name={firewall.RULE_PREFIX} ';" in iss
    uninstall = iss.split("if CurUninstallStep = usUninstall then", 1)[1].split("end;", 1)[0]
    assert "RemovePluginFirewallRules();" in uninstall


# ── What survives a shutdown ─────────────────────────────────────────────────
#
# On Linux the server cannot open a port itself: it runs unprivileged and
# `openavc.system.firewall.sync` returns without doing anything off Windows.
# The only thing that acts is `installer/firewall-sync.sh`, run as root by
# `ExecStartPre` BEFORE the server starts -- so the file it reads at boot is
# whatever the LAST run left behind. That makes shutdown part of this feature,
# which is not obvious from either side on its own.


class _FakeState:
    def delete(self, key):
        pass


class _FakeEvents:
    async def emit(self, *args, **kwargs):
        pass


class _FakeMacros:
    def unregister_plugin_actions(self, plugin_id):
        pass


class _FakeScripts:
    def unregister_plugin_methods(self, plugin_id):
        pass


def _loader_holding(monkeypatch, tmp_path, plugin_id, ports):
    """A PluginLoader with one running plugin, writing into tmp_path."""
    from openavc.core import plugin_loader as loader_module

    monkeypatch.setattr(
        loader_module, "get_platform_id", lambda: "linux", raising=False
    )
    import openavc.system_config as system_config

    monkeypatch.setattr(system_config, "get_data_dir", lambda: tmp_path)

    loader = loader_module.PluginLoader(
        state_store=_FakeState(), event_bus=_FakeEvents(),
        macro_engine=_FakeMacros(), device_manager=None,
    )
    instance = _plugin(ports)()
    # The loader stops plugins by awaiting stop(); without one it logs a fault
    # that has nothing to do with what is being tested here.
    async def _stop():
        return None
    instance.stop = _stop
    loader._instances[plugin_id] = instance
    loader._status[plugin_id] = "running"
    return loader


@pytest.mark.asyncio
async def test_shutting_the_server_down_leaves_the_declared_ports_written(
    tmp_path, monkeypatch,
):
    """The port has to still be asked for when the machine comes back up.

    Stopping one plugin means the user disabled it, and the empty file that
    results is how the helper learns to close the port. Stopping every plugin
    because the process is exiting means nothing of the kind -- but it went
    through the same path, so every clean shutdown wrote an empty file, and the
    helper read that file on the next boot before any plugin could rewrite it.
    The port was therefore never open on Linux, on any boot, ever.
    """
    loader = _loader_holding(
        monkeypatch, tmp_path, "video",
        [{"port": 8189, "protocol": "udp", "reason": "WebRTC"}],
    )
    loader.sync_declared_ports()
    assert [e["port"] for e in read(tmp_path)] == [8189]

    await loader.stop_all()

    assert [e["port"] for e in read(tmp_path)] == [8189], (
        "a clean shutdown emptied the file the boot-time helper reads"
    )


@pytest.mark.asyncio
async def test_disabling_the_only_plugin_still_closes_its_port(
    tmp_path, monkeypatch,
):
    """The don't-break-this half, and the reason this is not just a skip.

    A single stop is the user disabling a plugin, and the empty file is the
    whole mechanism for closing the rule. Suppressing the write on 'no plugins
    left' would leave a port open for a plugin nobody has any more.
    """
    loader = _loader_holding(
        monkeypatch, tmp_path, "video",
        [{"port": 8189, "protocol": "udp", "reason": "WebRTC"}],
    )
    loader.sync_declared_ports()
    assert [e["port"] for e in read(tmp_path)] == [8189]

    await loader.stop_plugin("video")

    assert read(tmp_path) == []
