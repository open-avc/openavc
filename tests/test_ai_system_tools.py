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

import server.api.discovery as discovery_module
import server.utils.log_buffer as log_buffer_module
from server.cloud.tools.system_tools import SystemToolsMixin
from server.utils.log_buffer import LogBuffer, LogEntry


class _Handler(SystemToolsMixin):
    def __init__(self, engine=None):
        self._engine = engine

    def _get_engine(self):
        return self._engine


def _engine_with_secret(password: str = "hunter22"):
    device = SimpleNamespace(config={"password": password, "host": "10.0.0.2"})
    project = SimpleNamespace(
        devices=[device],
        connections={"disp1": {"password": password, "host": "10.0.0.2"}},
        plugins={},
    )
    return SimpleNamespace(project=project)


def _entry(message: str, *, level: str = "INFO", age_seconds: float = 0.0) -> LogEntry:
    return LogEntry(
        timestamp=time.time() - age_seconds,
        level=level,
        source="server.transport.tcp",
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


async def test_get_logs_redacts_known_secrets(monkeypatch):
    buffer = _install_buffer(
        monkeypatch,
        [_entry("TX: PASS hunter22\\r", level="DEBUG"), _entry("poll ok")],
    )

    result = await _Handler(_engine_with_secret())._get_logs({"count": 10})
    messages = [e["message"] for e in result]
    assert "TX: PASS ***\\r" in messages
    assert not any("hunter22" in m for m in messages)
    # The local buffer keeps the original text — only the copy shipped to
    # the cloud is scrubbed.
    assert any("hunter22" in e.message for e in buffer._entries)


async def test_get_logs_redacts_plugin_config_secrets(monkeypatch):
    # Plugin entries in a loaded project are PluginConfig models, not dicts —
    # the harvest must read .config off the model, or plugin credentials in
    # DEBUG transport lines ship to the cloud unscrubbed.
    from server.core.project_loader import PluginConfig

    project = SimpleNamespace(
        devices=[],
        connections={},
        plugins={
            "mqtt_bridge": PluginConfig(
                enabled=True, config={"api_token": "tok-9f8e7d6c"}
            ),
        },
    )
    _install_buffer(
        monkeypatch,
        [_entry("TX: AUTH tok-9f8e7d6c", level="DEBUG"), _entry("poll ok")],
    )

    result = await _Handler(SimpleNamespace(project=project))._get_logs({"count": 10})
    messages = [e["message"] for e in result]
    assert "TX: AUTH ***" in messages
    assert not any("tok-9f8e7d6c" in m for m in messages)


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
