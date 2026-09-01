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
import sys

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
# passed here and then opened UDP 8189 on a GitHub runner.


@pytest.fixture
def fake_netsh(monkeypatch):
    """Record what would be run, and let the test say what netsh replies.

    Also the only way to reach the success path at all: it needs elevation,
    so on an ordinary developer machine the real thing can only ever fail.
    """
    from openavc.system import firewall

    calls = []
    replies = {"show": (True, ""), "add": (True, ""), "delete": (True, "")}

    def _fake(args):
        calls.append(args)
        verb = next((a for a in args if a in ("show", "add", "delete")), "")
        return replies.get(verb, (True, ""))

    monkeypatch.setattr(firewall, "_netsh", _fake)
    monkeypatch.setattr(firewall.sys, "platform", "win32")
    return calls, replies


@pytest.mark.skipif(sys.platform.startswith("win"), reason="tests the non-Windows branch")
def test_off_windows_nothing_is_touched():
    """Linux and macOS get their ports from the helper that runs as root before
    the server; this module must do nothing at all there."""
    from openavc.system import firewall

    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["opened"] == [] and report["closed"] == []


def test_a_declared_port_is_added(fake_netsh):
    from openavc.system import firewall

    calls, _ = fake_netsh
    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["opened"] == ["8189/udp"]
    add = next(c for c in calls if "add" in c)
    assert "protocol=UDP" in add and "localport=8189" in add
    assert f"name={firewall.rule_name(8189, 'udp')}" in add


def test_a_port_nobody_asks_for_any_more_is_removed(fake_netsh):
    """The half that makes the declaration honest."""
    from openavc.system import firewall

    calls, replies = fake_netsh
    replies["show"] = (True, f"Rule Name:  {firewall.rule_name(8189, 'udp')}\n")
    report = firewall.sync([])
    assert report["closed"] == ["8189/udp"]
    assert any("delete" in c for c in calls)


def test_a_rule_already_present_is_not_added_twice(fake_netsh):
    from openavc.system import firewall

    calls, replies = fake_netsh
    replies["show"] = (True, f"Rule Name:  {firewall.rule_name(8189, 'udp')}\n")
    report = firewall.sync([{"port": 8189, "protocol": "udp"}])
    assert report["opened"] == [] and report["closed"] == []
    assert not any("add" in c for c in calls)


def test_rules_that_are_not_ours_are_left_alone(fake_netsh):
    """An administrator's own rule for the same port must never be removed."""
    from openavc.system import firewall

    calls, replies = fake_netsh
    replies["show"] = (True, "Rule Name:  Some other thing UDP 8189\n")
    firewall.sync([])
    assert not any("delete" in c for c in calls)


def test_a_refusal_is_reported_rather_than_raised(fake_netsh):
    """Running unelevated is the normal developer case and must not look like
    a crash -- the caller degrades, it does not fail."""
    from openavc.system import firewall

    _, replies = fake_netsh
    replies["add"] = (False, "The requested operation requires elevation")
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
