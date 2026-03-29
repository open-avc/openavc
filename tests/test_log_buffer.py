"""Tests for the log buffer and streaming infrastructure."""

import asyncio
import logging
import time

import pytest

from server.utils.log_buffer import (
    LogBuffer,
    LogEntry,
    BufferHandler,
    _categorize_source,
)


# --- LogEntry ---


def test_log_entry_to_dict():
    entry = LogEntry(
        timestamp=1000.0,
        level="INFO",
        source="server.core.engine",
        category="system",
        message="Hello",
    )
    d = entry.to_dict()
    assert d["level"] == "INFO"
    assert d["category"] == "system"
    assert d["message"] == "Hello"


# --- Category derivation ---


def test_categorize_macro():
    assert _categorize_source("server.core.macro_engine", "") == "macro"


def test_categorize_device_drivers():
    assert _categorize_source("server.drivers.pjlink", "") == "device"


def test_categorize_device_manager():
    assert _categorize_source("server.core.device_manager", "") == "device"


def test_categorize_script_openavc():
    assert _categorize_source("openavc.script_api", "") == "script"


def test_categorize_script_engine():
    assert _categorize_source("server.core.script_engine", "") == "script"


def test_categorize_system_default():
    assert _categorize_source("server.core.engine", "") == "system"
    assert _categorize_source("server.api.rest", "") == "system"
    assert _categorize_source("uvicorn", "") == "system"


# --- LogBuffer ---


def test_buffer_append_and_get_recent():
    buf = LogBuffer(maxlen=10)
    for i in range(5):
        buf.append(LogEntry(
            timestamp=float(i),
            level="INFO",
            source="test",
            category="system",
            message=f"msg {i}",
        ))
    recent = buf.get_recent(3)
    assert len(recent) == 3
    assert recent[0]["message"] == "msg 2"
    assert recent[2]["message"] == "msg 4"


def test_buffer_maxlen_eviction():
    buf = LogBuffer(maxlen=3)
    for i in range(5):
        buf.append(LogEntry(
            timestamp=float(i),
            level="INFO",
            source="test",
            category="system",
            message=f"msg {i}",
        ))
    recent = buf.get_recent(10)
    assert len(recent) == 3
    assert recent[0]["message"] == "msg 2"


def test_buffer_get_recent_empty():
    buf = LogBuffer()
    assert buf.get_recent() == []


@pytest.mark.asyncio
async def test_buffer_subscribe_receives_entries():
    buf = LogBuffer()
    sub_id, queue = buf.subscribe()
    try:
        entry = LogEntry(
            timestamp=time.time(),
            level="INFO",
            source="test",
            category="system",
            message="hello",
        )
        buf.append(entry)
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.message == "hello"
    finally:
        buf.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_buffer_unsubscribe_stops_delivery():
    buf = LogBuffer()
    sub_id, queue = buf.subscribe()
    buf.unsubscribe(sub_id)
    buf.append(LogEntry(
        timestamp=time.time(),
        level="INFO",
        source="test",
        category="system",
        message="should not arrive",
    ))
    assert queue.empty()


# --- BufferHandler ---


def test_buffer_handler_feeds_buffer():
    buf = LogBuffer()
    handler = BufferHandler(buf)
    logger = logging.getLogger("test.buffer_handler")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.info("test message")
        recent = buf.get_recent(1)
        assert len(recent) == 1
        assert "test message" in recent[0]["message"]
        assert recent[0]["level"] == "INFO"
    finally:
        logger.removeHandler(handler)


def test_buffer_handler_categorizes_from_logger_name():
    buf = LogBuffer()
    handler = BufferHandler(buf)
    # Simulate a macro engine log record
    record = logging.LogRecord(
        name="server.core.macro_engine",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Executing macro",
        args=(),
        exc_info=None,
    )
    handler.emit(record)
    recent = buf.get_recent(1)
    assert recent[0]["category"] == "macro"
