"""The Windows proactor connection-reset filter drops only that noise.

On Windows a browser dropping a kept-alive connection makes asyncio log a
full ConnectionResetError traceback from the proactor's connection-lost
callback, at ERROR level, repeatedly, during normal operation. The filter
suppresses exactly that record so real ERROR lines stay visible.

The symptom is Windows-only, but the filter is pure logic — these run
everywhere so a regression can't hide until someone opens a Windows log.
"""

import logging

from server.utils.logger import _ProactorResetFilter


def _record(msg: str, exc: BaseException | None) -> logging.LogRecord:
    return logging.LogRecord(
        name="asyncio", level=logging.ERROR, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=(type(exc), exc, None) if exc else None,
    )


def test_drops_the_proactor_reset_traceback():
    rec = _record(
        "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
        ConnectionResetError(10054, "An existing connection was forcibly closed"),
    )
    assert _ProactorResetFilter().filter(rec) is False


def test_keeps_a_connection_reset_from_anywhere_else():
    """A device transport losing its peer is a real event worth logging."""
    rec = _record(
        "Device connection dropped while reading",
        ConnectionResetError(104, "Connection reset by peer"),
    )
    assert _ProactorResetFilter().filter(rec) is True


def test_keeps_other_errors_from_the_same_callback():
    """Only ConnectionResetError is benign here — anything else is a bug."""
    rec = _record(
        "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
        ValueError("something genuinely wrong"),
    )
    assert _ProactorResetFilter().filter(rec) is True


def test_keeps_records_with_no_exception():
    assert _ProactorResetFilter().filter(_record("plain message", None)) is True
