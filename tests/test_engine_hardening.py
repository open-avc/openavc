"""Regression tests for the engine.py audit hardening pass.

Covers, one finding per test where practical:

- H-015  _sync_devices re-adds on enabled / child_entities / pending_settings changes
- H-016  chained variable bindings propagate; cycles terminate
- M-018/L-016  network info detection caches and never leaks the socket FD
- M-019  stop() serializes against an in-flight reload (holds _reload_lock)
- M-020  reload rollback re-syncs devices and plugins
- M-021  periodic backup runs off the event-loop thread
- M-022  non-primitive source_map values are coerced to flat primitives
- M-023  glob metacharacters in source_key are rejected
- M-024  a de-persisted variable reverts to its default on load
- M-025  state.json is pruned when a variable is de-persisted
- M-027  list `selected` two-way binding is written on select events
- L-017  get_status gates host/network identifiers behind include_sensitive
- L-018  state.set UI action coerces non-primitive values
- L-019  a clean reload zeroes system.startup_errors
- L-020  stop() drains pending state.changed events
- L-021  reconcile_runtime_services reconciles ISC/mDNS to live system config
"""

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.core.engine import Engine
from server.core.project_loader import load_project


def _write_project(tmp_path, *, variables=None, devices=None, ui_pages=None,
                   plugins=None) -> str:
    project = {
        "version": "0.5.0",
        "project": {"id": "p", "name": "P"},
        "variables": variables or [],
        "macros": [],
        "devices": devices or [],
        "device_groups": [],
        "connections": {},
        "scripts": [],
        "plugins": plugins or {},
        "ui": {
            "settings": {},
            "pages": ui_pages or [
                {"id": "main", "name": "Main",
                 "grid": {"columns": 12, "rows": 8}, "elements": []},
            ],
        },
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(project), encoding="utf-8")
    return str(path)


# ── H-015: hot-reload device change detection ──


@pytest.mark.parametrize("field,old,new", [
    ("enabled", True, False),
    ("child_entities", {}, {"encoder": {"001": {"label": "Lobby"}}}),
    ("pending_settings", {}, {"brightness": 50}),
])
@pytest.mark.asyncio
async def test_sync_devices_reapplies_enabled_child_pending(tmp_path, field, old, new):
    """_sync_devices must re-add a device when enabled / child_entities /
    pending_settings change — not only name / driver / connection."""
    device = {"id": "d1", "driver": "generic_tcp", "name": "D1",
              "config": {"host": "1.2.3.4"}, field: new}
    eng = Engine(_write_project(tmp_path, devices=[device]))
    eng.project = load_project(eng.project_path)

    new_resolved = eng.resolved_device_config(eng.project.devices[0])
    old_resolved = json.loads(json.dumps(new_resolved))  # deep copy
    old_resolved[field] = old

    devices = MagicMock()
    devices.get_device_configs.return_value = {"d1": old_resolved}
    devices.get_device_config.return_value = old_resolved
    devices.update_device = AsyncMock()
    devices.add_device = AsyncMock()
    devices.remove_device = AsyncMock()
    eng.devices = devices

    await eng._sync_devices()

    devices.update_device.assert_awaited_once()
    assert devices.update_device.await_args.args[0] == "d1"


@pytest.mark.asyncio
async def test_sync_devices_no_change_does_not_reapply(tmp_path):
    """An unchanged device must not be torn down and re-added."""
    device = {"id": "d1", "driver": "generic_tcp", "name": "D1",
              "config": {"host": "1.2.3.4"}, "enabled": True}
    eng = Engine(_write_project(tmp_path, devices=[device]))
    eng.project = load_project(eng.project_path)
    resolved = eng.resolved_device_config(eng.project.devices[0])

    devices = MagicMock()
    devices.get_device_configs.return_value = {"d1": json.loads(json.dumps(resolved))}
    devices.get_device_config.return_value = json.loads(json.dumps(resolved))
    devices.update_device = AsyncMock()
    devices.add_device = AsyncMock()
    devices.remove_device = AsyncMock()
    eng.devices = devices

    await eng._sync_devices()
    devices.update_device.assert_not_awaited()


# ── H-016: chained variable bindings ──


@pytest.mark.asyncio
async def test_chained_variable_bindings_propagate(tmp_path):
    """var B bound to var A's key updates when A's source changes."""
    eng = Engine(_write_project(tmp_path, variables=[
        {"id": "a", "type": "string", "default": "", "source_key": "device.x.power"},
        {"id": "b", "type": "string", "default": "", "source_key": "var.a"},
    ]))
    eng.project = load_project(eng.project_path)
    eng._bind_variable_sources()

    eng.state.set("device.x.power", "on", source="device")

    assert eng.state.get("var.a") == "on"
    assert eng.state.get("var.b") == "on"  # the chain — frozen before the fix


@pytest.mark.asyncio
async def test_cyclic_variable_bindings_terminate(tmp_path):
    """A<->B binding cycle must not loop forever."""
    eng = Engine(_write_project(tmp_path, variables=[
        {"id": "c", "type": "string", "default": "", "source_key": "var.d"},
        {"id": "d", "type": "string", "default": "", "source_key": "var.c"},
    ]))
    eng.project = load_project(eng.project_path)
    eng._bind_variable_sources()

    # Would hang without the re-entrancy guard.
    eng.state.set("var.c", "hello", source="ui")

    assert eng.state.get("var.c") == "hello"
    assert eng.state.get("var.d") == "hello"
    assert eng._var_binding_active == set()  # cleaned up after the cascade


# ── M-022: source_map flat-primitive coercion ──


@pytest.mark.asyncio
async def test_source_map_nonprimitive_is_coerced(tmp_path):
    eng = Engine(_write_project(tmp_path, variables=[
        {"id": "a", "type": "string", "default": "", "source_key": "device.x.mode",
         "source_map": {"on": ["a", "b"]}},
    ]))
    eng.project = load_project(eng.project_path)
    eng._bind_variable_sources()

    eng.state.set("device.x.mode", "on", source="device")

    val = eng.state.get("var.a")
    assert isinstance(val, str)
    assert val == json.dumps(["a", "b"])


# ── M-023: glob source_key rejected ──


@pytest.mark.asyncio
async def test_glob_source_key_is_rejected(tmp_path):
    eng = Engine(_write_project(tmp_path, variables=[
        {"id": "a", "type": "string", "default": "x", "source_key": "device.*.power"},
    ]))
    eng.project = load_project(eng.project_path)
    eng._init_variable_values({})  # seed var.a default ("x")
    eng._bind_variable_sources()

    assert eng._var_binding_subs == []  # no subscription registered
    eng.state.set("device.y.power", "on", source="device")
    assert eng.state.get("var.a") == "x"  # no spurious fan-in write


# ── M-024 / M-025: de-persist load filter + disk prune ──


def test_init_variable_values_ignores_stale_depersisted_value(tmp_path):
    """A variable whose persist flag is off reverts to default even if
    state.json still holds an old value."""
    eng = Engine(_write_project(tmp_path, variables=[
        {"id": "vol", "type": "number", "default": 10, "persist": False},
    ]))
    eng.project = load_project(eng.project_path)

    keys = eng._init_variable_values({"var.vol": 99})  # stale persisted value

    assert eng.state.get("var.vol") == 10  # default, not the stale 99
    assert keys == set()  # not persistent → not watched


def test_init_variable_values_restores_persistent_value(tmp_path):
    eng = Engine(_write_project(tmp_path, variables=[
        {"id": "vol", "type": "number", "default": 10, "persist": True},
    ]))
    eng.project = load_project(eng.project_path)

    keys = eng._init_variable_values({"var.vol": 42})

    assert eng.state.get("var.vol") == 42
    assert keys == {"var.vol"}


def test_update_keys_prunes_depersisted_from_disk(tmp_path):
    from server.core.state_persister import StatePersister
    from server.core.state_store import StateStore

    store = StateStore()
    store.set("var.a", "aval", source="system")
    store.set("var.b", "bval", source="system")
    state_file = tmp_path / "state.json"
    persister = StatePersister(state_file, store)
    persister.start({"var.a", "var.b"})
    persister._write()  # seed state.json with both keys

    assert json.loads(state_file.read_text()) == {"var.a": "aval", "var.b": "bval"}

    persister.update_keys({"var.a"})  # de-persist var.b

    assert json.loads(state_file.read_text()) == {"var.a": "aval"}
    persister.stop()


# ── M-027: list selected two-way binding ──


@pytest.mark.asyncio
async def test_select_event_writes_selected_binding(tmp_path):
    eng = Engine(_write_project(
        tmp_path,
        variables=[{"id": "source", "type": "string", "default": ""}],
        ui_pages=[{
            "id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8},
            "elements": [{
                "id": "src_list", "type": "list", "list_style": "selectable",
                "bindings": {"selected": {"key": "var.source"}},
            }],
        }],
    ))
    eng.project = load_project(eng.project_path)

    await eng.handle_ui_event("select", "src_list", {"value": "hdmi2"})

    assert eng.state.get("var.source") == "hdmi2"


# ── L-017: status disclosure gating ──


def test_get_status_gates_sensitive_fields(tmp_path):
    eng = Engine(_write_project(tmp_path))
    eng.project = load_project(eng.project_path)
    # Prime the cache so detection never touches real adapters.
    eng._network_info = ("10.0.0.5", "host1", ["10.0.0.5", "192.168.9.5"])

    full = eng.get_status(include_sensitive=True)
    assert full["hostname"] == "host1"
    assert full["local_ip"] == "10.0.0.5"
    assert full["local_ips"] == ["10.0.0.5", "192.168.9.5"]
    assert "bind_address" in full

    redacted = eng.get_status(include_sensitive=False)
    assert "hostname" not in redacted
    assert "local_ip" not in redacted
    assert "local_ips" not in redacted
    assert "bind_address" not in redacted
    # Non-sensitive fields still present.
    assert redacted["status"] in ("running", "stopped")
    assert "version" in redacted


# ── M-018 / L-016: network info caching + no FD leak ──


def test_detect_network_info_caches(tmp_path, monkeypatch):
    eng = Engine(_write_project(tmp_path))
    count = {"enum": 0, "host": 0}

    def fake_enum():
        count["enum"] += 1
        return ["192.168.1.50", "10.50.0.50"]

    monkeypatch.setattr("server.core.engine.network_scanner.get_ranked_interface_ips",
                        fake_enum)
    monkeypatch.setattr("server.core.engine.socket.gethostname",
                        lambda: count.__setitem__("host", count["host"] + 1) or "myhost")

    first = eng._detect_network_info()
    second = eng._detect_network_info()

    # local_ip is the top-ranked leg; the rest ride along for the setup screen.
    assert first == ("192.168.1.50", "myhost", ["192.168.1.50", "10.50.0.50"])
    assert second == first
    assert count["enum"] == 1  # second call served from cache
    assert count["host"] == 1


def test_detect_network_info_closes_socket_on_connect_failure(tmp_path, monkeypatch):
    """No route at all: the fallback socket is closed and the engine reports
    loopback rather than leaking a descriptor on an isolated control network."""
    eng = Engine(_write_project(tmp_path))
    closed = {"v": False}

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            closed["v"] = True
            return False

        def settimeout(self, t):
            pass

        def connect(self, addr):
            raise OSError("no route to host")

        def getsockname(self):  # pragma: no cover - never reached
            return ("x", 0)

    # The route lookup lives with the rest of the adapter enumeration now.
    monkeypatch.setattr("server.discovery.network_scanner.socket.socket",
                        lambda *a, **k: FakeSock())
    monkeypatch.setattr("server.discovery.network_scanner.get_interface_ips",
                        lambda: [])
    monkeypatch.setattr("server.core.engine.socket.gethostname", lambda: "h")

    ip, host, ips = eng._detect_network_info()

    assert ip == "127.0.0.1"  # fell back
    assert ips == []
    assert closed["v"], "socket must be closed even when connect() fails"


# ── L-018: state.set UI action coercion ──


@pytest.mark.asyncio
async def test_state_set_action_coerces_nonprimitive(tmp_path):
    eng = Engine(_write_project(tmp_path))
    eng.project = load_project(eng.project_path)

    await eng._execute_action(
        {"action": "state.set", "key": "var.x", "value": {"nested": 1}},
        data={}, element=None,
    )

    val = eng.state.get("var.x")
    assert isinstance(val, str)
    assert val == json.dumps({"nested": 1})


# ── M-019: stop serializes against reload ──


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_reload(tmp_path):
    eng = Engine(_write_project(tmp_path))
    eng.project = load_project(eng.project_path)
    eng._running = True

    await eng._reload_lock.acquire()  # simulate an in-flight reload
    done = {"v": False}

    async def do_stop():
        await eng.stop()
        done["v"] = True

    task = asyncio.create_task(do_stop())
    await asyncio.sleep(0.05)
    assert not done["v"], "stop() ran while a reload held the lock"

    eng._reload_lock.release()
    await asyncio.wait_for(task, timeout=2)
    assert done["v"]


# ── L-020: stop drains pending events ──


@pytest.mark.asyncio
async def test_stop_drains_pending_events(tmp_path):
    eng = Engine(_write_project(tmp_path))
    eng.project = load_project(eng.project_path)
    eng._running = True

    calls = {"n": 0}
    real_flush = eng.state.flush_pending_events

    async def counting_flush():
        calls["n"] += 1
        await real_flush()

    eng.state.flush_pending_events = counting_flush
    await eng.stop()
    assert calls["n"] >= 1


# ── M-020: reload rollback re-syncs devices and plugins ──


@pytest.mark.asyncio
async def test_reload_rollback_resyncs_devices_and_plugins(tmp_path):
    eng = Engine(_write_project(tmp_path))
    eng.project = load_project(eng.project_path)
    eng._running = True

    sync = {"devices": 0, "plugins": 0}

    async def count_devices():
        sync["devices"] += 1

    async def count_plugins():
        sync["plugins"] += 1

    eng._sync_devices = count_devices
    eng._sync_plugins = count_plugins

    async def boom():
        raise RuntimeError("late reload failure")

    eng._reload_isc = boom  # fails after the normal-path syncs

    with pytest.raises(RuntimeError, match="late reload failure"):
        await eng.reload_project()

    # One sync in the normal path, one in the rollback path.
    assert sync["devices"] == 2
    assert sync["plugins"] == 2

    await eng.triggers.stop()


# ── L-019: clean reload zeroes startup_errors ──


@pytest.mark.asyncio
async def test_clean_reload_zeroes_startup_errors(tmp_path):
    eng = Engine(_write_project(tmp_path))
    eng.project = load_project(eng.project_path)
    eng._running = True
    eng.state.set("system.startup_errors", 3, source="system")

    await eng.reload_project()

    assert eng.state.get("system.startup_errors") == 0
    await eng.triggers.stop()


# ── M-021: periodic backup runs off the event loop ──


@pytest.mark.asyncio
async def test_periodic_backup_runs_off_event_loop(tmp_path, monkeypatch):
    eng = Engine(_write_project(tmp_path))
    main_thread = threading.current_thread()
    seen = {}

    def fake_create_backup(parent, reason):
        seen["thread"] = threading.current_thread()
        seen["reason"] = reason

    monkeypatch.setattr("server.core.backup_manager.create_backup", fake_create_backup)

    real_sleep = asyncio.sleep
    n = {"v": 0}

    async def fake_sleep(_secs):
        n["v"] += 1
        if n["v"] >= 2:
            raise asyncio.CancelledError()  # break out after one backup
        await real_sleep(0)

    monkeypatch.setattr("server.core.engine.asyncio.sleep", fake_sleep)

    eng._dirty_since_backup = True
    eng._last_backup_time = 0

    await eng._periodic_backup_loop()  # CancelledError is caught inside

    assert seen.get("reason") == "Auto-backup"
    assert seen["thread"] is not main_thread


# ── L-021: reconcile ISC/mDNS to live config ──


@pytest.mark.asyncio
async def test_reconcile_runtime_services_stops_mdns_when_disabled(tmp_path):
    from server.system_config import get_system_config

    eng = Engine(_write_project(tmp_path))
    eng.project = load_project(eng.project_path)

    class FakeAdvertiser:
        def __init__(self):
            self.stopped = False

        async def stop(self):
            self.stopped = True

    adv = FakeAdvertiser()
    eng.mdns_advertiser = adv

    cfg = get_system_config()
    original = cfg.get("discovery", "advertise", True)
    cfg.set("discovery", "advertise", False)
    try:
        await eng.reconcile_runtime_services()
    finally:
        cfg.set("discovery", "advertise", original)

    assert adv.stopped
    assert eng.mdns_advertiser is None
