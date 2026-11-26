"""Regression tests for the cloud AI system tools (cloud/tools/system_tools.py).

get_logs sliced the newest ``count`` entries BEFORE applying the
category/level/search/since filters, so a filtered query only ever saw the
newest ``count`` entries and silently missed older matches. It also shipped
raw log text to the cloud AI verbatim — at DEBUG the transport loggers put
whole TX/RX payloads (credentials included) in the buffer — and raised
opaque TypeErrors when the AI sent numbers as strings. ``wait`` clamped
before coercing, so ``seconds="30"`` crashed the clamp itself, and
``start_discovery_scan`` forwarded unvalidated subnets/timeout: the tool
reported a running scan that a non-numeric timeout had already doomed, and
a bare-string subnets value was iterated character by character.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import openavc.api.discovery as discovery_module
import openavc.utils.log_buffer as log_buffer_module
from openavc.cloud.tools.system_tools import SystemToolsMixin
from openavc.core.project_loader import DeviceConfig
from openavc.core.project_logins import secrets_held_in_the_clear
from openavc.transport.wire_log import format_wire_data
from openavc.utils.log_buffer import LogBuffer, LogEntry
from openavc.utils.log_redaction import (
    get_secret_registry,
    project_secret_values,
    register_config_secrets,
    register_system_secrets,
)


class _Handler(SystemToolsMixin):
    def __init__(self, engine=None):
        self._engine = engine

    def _get_engine(self):
        return self._engine


@pytest.fixture(autouse=True)
def _clean_registry():
    get_secret_registry().clear()
    yield
    get_secret_registry().clear()


def _registered(project) -> None:
    """Hand the masking the project's secrets, as the engine does whenever
    the project is replaced (``ProjectLoginsMixin._project_secrets``)."""
    get_secret_registry().set_project_secrets(
        project_secret_values(project) | secrets_held_in_the_clear(project),
    )


def _entry(message: str, *, level: str = "INFO", age_seconds: float = 0.0) -> LogEntry:
    return LogEntry(
        timestamp=time.time() - age_seconds,
        level=level,
        source="openavc.transport.tcp",
        category="device",
        message=message,
    )


def _install_buffer(monkeypatch, entries: list[LogEntry]) -> LogBuffer:
    buffer = LogBuffer()
    for entry in entries:
        buffer.append(entry)
    monkeypatch.setattr(log_buffer_module, "get_log_buffer", lambda: buffer)
    return buffer


async def test_get_logs_filters_before_slicing(monkeypatch):
    # One old ERROR buried under newer INFO chatter: a filtered query for
    # errors must find it even with a small count. The old slice-first
    # order returned the newest `count` entries, filtered them, and came
    # back empty.
    entries = [_entry("connection refused", level="ERROR", age_seconds=60)]
    entries += [_entry(f"poll ok {i}") for i in range(10)]
    _install_buffer(monkeypatch, entries)

    result = await _Handler(None)._get_logs({"count": 5, "level": "error"})
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["message"] == "connection refused"


async def test_get_logs_redacts_a_running_device_s_login(monkeypatch):
    buffer = _install_buffer(
        monkeypatch,
        [_entry("TX: PASS hunter22\\r", level="DEBUG"), _entry("poll ok")],
    )
    # What a driver registers as it is built (BaseDriver), from the config
    # its settings and its connection row resolve to.
    register_config_secrets("disp1", {"password": "hunter22", "host": "10.0.0.2"}, None)

    result = await _Handler(None)._get_logs({"count": 10})
    messages = [e["message"] for e in result]
    assert "TX: PASS ***\\r" in messages
    assert not any("hunter22" in m for m in messages)
    # The local buffer keeps the original text — only the copy shipped to
    # the cloud is scrubbed.
    assert any("hunter22" in e.message for e in buffer._entries)


async def test_get_logs_redacts_what_the_project_holds_in_the_clear(monkeypatch):
    # A disabled device runs no driver, and a connection no device uses yet is
    # no driver's: the project's registration covers both.
    project = SimpleNamespace(
        devices=[DeviceConfig(id="spare", driver="", name="Spare", enabled=False,
                              config={"password": "spare-pw-71"})],
        connections={"disp1": {"password": "conn-pw-82", "host": "10.0.0.2"}},
        plugins={},
    )
    _registered(project)
    _install_buffer(monkeypatch, [_entry("TX: spare-pw-71 conn-pw-82", level="DEBUG")])

    result = await _Handler(None)._get_logs({"count": 10})
    assert [e["message"] for e in result] == ["TX: *** ***"]


async def test_get_logs_redacts_plugin_config_secrets(monkeypatch):
    # Plugin entries in a loaded project are PluginConfig models, not dicts:
    # a credential in .config, declared or only named like one, is masked.
    from openavc.core.project_loader import PluginConfig

    project = SimpleNamespace(
        devices=[],
        connections={},
        plugins={
            "mqtt_bridge": PluginConfig(
                enabled=True, config={"api_token": "tok-9f8e7d6c"}
            ),
        },
    )
    _registered(project)
    _install_buffer(
        monkeypatch,
        [_entry("TX: AUTH tok-9f8e7d6c", level="DEBUG"), _entry("poll ok")],
    )

    result = await _Handler(None)._get_logs({"count": 10})
    messages = [e["message"] for e in result]
    assert "TX: AUTH ***" in messages
    assert not any("tok-9f8e7d6c" in m for m in messages)


async def test_get_logs_redacts_the_projects_mesh_key(monkeypatch):
    from openavc.core.project_loader import ISCConfig

    project = SimpleNamespace(
        devices=[], connections={}, plugins={},
        isc=ISCConfig(enabled=False, auth_key="mesh-key-4417"),
    )
    _registered(project)
    _install_buffer(monkeypatch, [_entry("peer joined with mesh-key-4417")])

    result = await _Handler(None)._get_logs({"count": 10})
    assert [e["message"] for e in result] == ["peer joined with ***"]


async def test_get_logs_redacts_this_system_s_own_secrets(monkeypatch):
    import openavc.config as runtime
    import openavc.system_config as system_config

    system = {"auth": {"programmer_password": "env-admin-pw", "api_key": "env-api-key-3"},
              "cloud": {"system_id": "sys-1"}}
    monkeypatch.setattr(system_config, "get_system_config",
                        lambda: SimpleNamespace(to_dict=lambda: system))
    monkeypatch.setattr(runtime, "CLOUD_SYSTEM_KEY", "cloud-key-5521", raising=False)
    register_system_secrets()
    _install_buffer(monkeypatch, [_entry("env-admin-pw env-api-key-3 cloud-key-5521 sys-1")])

    result = await _Handler(None)._get_logs({"count": 10})
    assert [e["message"] for e in result] == ["*** *** *** sys-1"]


async def test_get_logs_redacts_a_setting_the_driver_marks_secret(monkeypatch):
    """A secret by the driver's own schema, under a name the name rule would miss."""
    from openavc.drivers import registry
    from openavc.drivers.base import BaseDriver

    class _AcmeCoded(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_coded_logs", "name": "Acme Coded", "transport": "tcp",
            "config_schema": {
                "host": {"type": "string"},
                "site_code": {"type": "string", "secret": True},
                "admin_code": {"type": "password"},
            },
            "state_variables": {}, "commands": {},
        }

    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    registry.register_driver(_AcmeCoded)
    device = DeviceConfig(
        id="coded", driver="acme_coded_logs", name="Coded", enabled=False,
        config={"host": "10.0.0.4", "site_code": "9931-ALPHA", "admin_code": "7731-BRAVO"},
    )
    _registered(SimpleNamespace(devices=[device], connections={}, plugins={}))
    _install_buffer(monkeypatch, [_entry("TX: SITE 9931-ALPHA ADMIN 7731-BRAVO", level="DEBUG")])

    result = await _Handler(None)._get_logs({"count": 10})
    assert [e["message"] for e in result] == ["TX: SITE *** ADMIN ***"]


async def test_get_logs_shows_a_username_outside_its_device_s_traffic(monkeypatch):
    # A username names an account rather than unlocking one: masked where its
    # own device's traffic is formatted, shown in every other line, as the
    # log shows it here.
    register_config_secrets("disp1", {"username": "operator", "password": "hunter22"}, None)
    _install_buffer(monkeypatch, [
        _entry(format_wire_data(b"LOGIN operator hunter22", device_id="disp1"), level="DEBUG"),
        _entry("Display 1 signed in as operator"),
    ])

    result = await _Handler(None)._get_logs({"count": 10})
    assert [e["message"] for e in result] == [
        "LOGIN *** ***", "Display 1 signed in as operator",
    ]


async def test_get_logs_redaction_respects_word_boundaries(monkeypatch):
    # An SNMP community of "public" is a real secret, but plain substring
    # redaction mangled unrelated words that merely contain it (republic,
    # publication). Scrub the value where it stands as its own token; leave
    # coincidental substrings intact.
    register_config_secrets("switch1", {"community": "public", "host": "10.0.0.9"}, None)
    _install_buffer(
        monkeypatch,
        [
            _entry("SNMP community=public accepted", level="DEBUG"),
            _entry("the republic issued a publication", level="INFO"),
        ],
    )
    result = await _Handler(None)._get_logs({"count": 10})
    messages = [e["message"] for e in result]
    # The standalone secret token is scrubbed...
    assert "SNMP community=*** accepted" in messages
    # ...but words that merely contain it are untouched.
    assert "the republic issued a publication" in messages


async def test_get_logs_coerces_string_numbers(monkeypatch):
    entries = [_entry("old line", age_seconds=3600)]
    entries += [_entry(f"line {i}") for i in range(5)]
    _install_buffer(monkeypatch, entries)
    handler = _Handler(None)

    result = await handler._get_logs({"count": "3"})
    assert len(result) == 3

    recent = await handler._get_logs({"count": 10, "since_seconds": "60"})
    assert all("old line" != e["message"] for e in recent)


async def test_get_logs_rejects_garbage_numbers(monkeypatch):
    _install_buffer(monkeypatch, [_entry("line")])
    handler = _Handler(None)

    assert "error" in await handler._get_logs({"count": "many"})
    assert "error" in await handler._get_logs({"since_seconds": "soon"})


async def test_wait_coerces_and_clamps(monkeypatch):
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    handler = _Handler(None)

    result = await handler._wait({"seconds": "2"})
    assert result["waited_seconds"] == 2
    assert slept == [2]

    result = await handler._wait({"seconds": 500})
    assert result["waited_seconds"] == 120

    result = await handler._wait({"seconds": "a while"})
    assert "error" in result
    assert len(slept) == 2  # no sleep on a rejected value


class _FakeDiscoveryEngine:
    def __init__(self):
        self.config = {}
        self.calls = []

    async def start_scan(self, subnets=None, timeout=None):
        self.calls.append({"subnets": subnets, "timeout": timeout})
        return "scan1"

    def get_status(self):
        return {"status": "running", "subnets": ["192.168.1.0/24"], "duration": 0}


async def test_start_scan_validates_inputs(monkeypatch):
    engine = _FakeDiscoveryEngine()
    monkeypatch.setattr(discovery_module, "_engine", engine)
    handler = _Handler(None)

    # A bare string would be iterated character by character as targets.
    result = await handler._start_discovery_scan({"subnets": "192.168.1.0/24"})
    assert "error" in result
    # A non-numeric timeout used to kill the scan AFTER reporting it running.
    result = await handler._start_discovery_scan({"timeout": "fast"})
    assert "error" in result
    assert engine.calls == []

    result = await handler._start_discovery_scan(
        {"subnets": ["192.168.1.0/24"], "timeout": "300"}
    )
    assert result["scan_id"] == "scan1"
    assert engine.calls == [{"subnets": ["192.168.1.0/24"], "timeout": 300.0}]


class _FinishedDiscoveryEngine(_FakeDiscoveryEngine):
    def get_status(self):
        self.calls.append("status")
        return {"status": "complete", "duration": 4, "warnings": []}

    def get_results(self):
        return []


async def test_discovery_results_wait_reads_a_timeout_sent_as_text(monkeypatch):
    engine = _FinishedDiscoveryEngine()
    monkeypatch.setattr(discovery_module, "_engine", engine)
    handler = _Handler(None)

    assert await handler._get_discovery_results({"wait": True, "timeout": "soon"}) == {
        "error": "timeout must be a number of seconds",
    }
    assert engine.calls == []

    result = await handler._get_discovery_results({"wait": True, "timeout": "60"})
    assert result["scan_status"] == "complete"
    assert engine.calls == ["status", "status"]  # the wait's poll, then the answer's


async def test_a_scan_the_assistant_starts_reaches_an_open_discovery_page(monkeypatch):
    """A scan the assistant starts sends the same updates as one the Discovery
    tab starts: that it began, its phases, each device it finds and its end,
    to the same broadcast, so a Discovery page open at the time shows it."""
    from openavc.discovery.engine import DiscoveryEngine

    sent: list[dict] = []

    async def broadcast(message):
        sent.append(message)

    engine = DiscoveryEngine()
    monkeypatch.setattr(discovery_module, "_engine", None)
    monkeypatch.setattr(discovery_module, "_broadcast_fn", None)
    discovery_module.set_discovery_engine(engine)
    discovery_module.set_broadcast_fn(broadcast)

    async def one_phase(subnets):
        await engine._set_phase(1, "ping_sweep", "Scanning for live hosts...")
        await engine._on_ping_found("10.77.0.2")

    monkeypatch.setattr(engine, "_scan_pipeline", one_phase)

    result = await _Handler(None)._start_discovery_scan({"subnets": ["10.77.0.0/30"]})
    assert result["status"] == "running"
    await engine._scan_task

    types = [m["type"] for m in sent]
    # The start first, so a page that did not start the scan shows it running.
    assert sent[0] == {
        "type": "discovery.started",
        "scan_id": result["scan_id"],
        "subnets": ["10.77.0.0/30"],
        "started_at": engine.scan_status.started_at,
        "budget_seconds": round(engine.scan_status.budget_seconds, 2),
    }
    assert types[1] == "discovery.phase"
    assert "10.77.0.2" in [m["device"]["ip"] for m in sent if m["type"] == "discovery.update"]
    assert types[-1] == "discovery.complete"
    assert sent[-1]["scan_id"] == result["scan_id"]
