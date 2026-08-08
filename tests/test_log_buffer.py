"""Tests for the log buffer and streaming infrastructure."""

import asyncio
import logging
import time

import pytest

from openavc.utils.log_buffer import (
    USER_SCRIPT_NAMESPACE,
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
        source="openavc.core.engine",
        category="system",
        message="Hello",
    )
    d = entry.to_dict()
    assert d["level"] == "INFO"
    assert d["category"] == "system"
    assert d["message"] == "Hello"


# --- Category derivation ---


def test_categorize_macro():
    assert _categorize_source("openavc.core.macro_engine", "") == "macro"


def test_categorize_device_drivers():
    assert _categorize_source("openavc.drivers.pjlink", "") == "device"


def test_categorize_device_manager():
    assert _categorize_source("openavc.core.device_manager", "") == "device"


def test_categorize_script_api_proxy():
    """A script's own log.* output goes through script_api's logger."""
    assert _categorize_source("openavc.core.script_api", "") == "script"


def test_categorize_script_engine():
    assert _categorize_source("openavc.core.script_engine", "") == "script"


def test_categorize_a_loaded_user_script():
    """A script that calls getLogger(__name__) is named off the script namespace."""
    assert _categorize_source(f"{USER_SCRIPT_NAMESPACE}.room_lights", "") == "script"


def test_the_platform_prefix_alone_is_not_a_script():
    """The whole platform is named openavc.* now, so the prefix means nothing.

    This is the guard on the failure this rename could have introduced silently:
    the categoriser used to read a bare "openavc" prefix as "user script",
    because back then that name was conjured at runtime and only scripts wore
    it. Restore that test and every platform line below collapses into the
    script category — the System Log view goes quietly wrong and nothing fails.
    """
    assert _categorize_source("openavc", "") == "system"
    assert _categorize_source("openavc.core.engine", "") == "system"
    assert _categorize_source("openavc.cloud.agent", "") == "system"


def test_categorize_system_default():
    assert _categorize_source("openavc.core.engine", "") == "system"
    assert _categorize_source("openavc.api.rest", "") == "system"
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


def test_buffer_get_recent_count_zero_returns_empty():
    """count=0 must return nothing, not the whole buffer (the [-0:] slice
    trap: [-0:] is [0:], i.e. everything)."""
    buf = LogBuffer(maxlen=10)
    for i in range(5):
        buf.append(LogEntry(
            timestamp=float(i), level="INFO", source="test",
            category="system", message=f"msg {i}",
        ))
    assert buf.get_recent(0) == []


def test_buffer_get_recent_negative_count_returns_empty():
    """A negative count must return nothing, not a wrong window ([-(-3):] = [3:])."""
    buf = LogBuffer(maxlen=10)
    for i in range(5):
        buf.append(LogEntry(
            timestamp=float(i), level="INFO", source="test",
            category="system", message=f"msg {i}",
        ))
    assert buf.get_recent(-3) == []


def test_buffer_get_recent_category_filters_before_slice():
    """The category filter must scan the whole buffer, then take the newest
    count — filtering after the slice makes a busy log return too few (or
    zero) matches when the newest entries are all another category."""
    buf = LogBuffer(maxlen=200)
    for i in range(10):
        buf.append(LogEntry(
            timestamp=float(i), level="INFO", source="test",
            category="device", message=f"dev {i}",
        ))
    # Bury the device entries under newer system chatter
    for i in range(50):
        buf.append(LogEntry(
            timestamp=float(100 + i), level="INFO", source="test",
            category="system", message=f"sys {i}",
        ))
    recent = buf.get_recent(20, category="device")
    assert len(recent) == 10
    assert all(e["category"] == "device" for e in recent)


def test_buffer_get_recent_category_takes_newest_matches():
    buf = LogBuffer(maxlen=200)
    for i in range(30):
        buf.append(LogEntry(
            timestamp=float(i), level="INFO", source="test",
            category="device", message=f"dev {i}",
        ))
    recent = buf.get_recent(5, category="device")
    assert [e["message"] for e in recent] == [f"dev {i}" for i in range(25, 30)]


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
        name="openavc.core.macro_engine",
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


# --- Device extraction (System Log Device filter depends on this field) ---


def _emit(buf: LogBuffer, name: str, msg: str) -> dict:
    handler = BufferHandler(buf)
    record = logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    handler.emit(record)
    return buf.get_recent(1)[0]


def test_device_extracted_from_driver_prefix():
    buf = LogBuffer()
    entry = _emit(buf, "openavc.drivers.pjlink", "[proj1] Poll failed - not connected")
    assert entry["device"] == "proj1"


def test_device_extracted_from_transport_prefix():
    buf = LogBuffer()
    entry = _emit(buf, "openavc.transport.tcp", "[hdmi_matrix] Connected")
    assert entry["device"] == "hdmi_matrix"


def test_device_empty_without_prefix():
    buf = LogBuffer()
    entry = _emit(buf, "openavc.core.device_manager", "Failed to connect 'proj1': timeout")
    assert entry["device"] == ""


def test_device_not_extracted_for_non_device_categories():
    # Macro/script/system lines may use their own [tag] prefixes — those are
    # not device ids and must not populate the device field
    buf = LogBuffer()
    entry = _emit(buf, "openavc.core.macro_engine", "[room_on] Executing step 2")
    assert entry["device"] == ""
    entry = _emit(buf, "openavc.core.engine", "[startup] Ready")
    assert entry["device"] == ""


def test_device_prefix_with_spaces_not_treated_as_id():
    buf = LogBuffer()
    entry = _emit(buf, "openavc.drivers.pjlink", "[not an id] message")
    assert entry["device"] == ""


def test_device_field_in_to_dict():
    entry = LogEntry(
        timestamp=1000.0,
        level="INFO",
        source="openavc.drivers.pjlink",
        category="device",
        message="[proj1] Connected",
        device="proj1",
    )
    assert entry.to_dict()["device"] == "proj1"
