"""Tests for the BaseDriver poll-loop watchdog.

These tests prove that when poll() raises a transport-level error N times
in a row, the driver gets marked disconnected and the polling loop exits.
This is the platform-level guarantee that makes `device.<id>.connected`
truthful regardless of which transport (or no transport) the driver picked.
"""

import asyncio
from typing import Any

import httpx
import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import BaseDriver


class _CountingDriver(BaseDriver):
    """Test fixture: poll() behavior is configurable per instance."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "test_counting",
        "name": "Test Counting Driver",
        "category": "test",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.poll_count = 0
        # Test sets these to control poll() behavior
        self.poll_raises: BaseException | None = None
        self.poll_returns_cleanly = True

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def poll(self) -> None:
        self.poll_count += 1
        if self.poll_raises is not None:
            raise self.poll_raises
        # Else: clean return


def _make_driver() -> _CountingDriver:
    state = StateStore()
    events = EventBus()
    return _CountingDriver(
        device_id="test_dev",
        config={"max_missed_polls": 3},
        state=state,
        events=events,
    )


@pytest.mark.asyncio
async def test_watchdog_fires_after_three_connection_errors() -> None:
    """poll() raising ConnectionError 3x → connected flips to False, loop exits."""
    drv = _make_driver()
    drv._connected = True
    drv.set_state("connected", True)
    drv.poll_raises = ConnectionError("unreachable")

    await drv.start_polling(0.01)
    # Wait long enough for 3+ polls plus the watchdog disconnect
    await asyncio.sleep(0.2)

    assert drv.get_state("connected") is False
    assert drv._connected is False
    # Poll task should have exited on its own
    assert drv._poll_task is None or drv._poll_task.done()


@pytest.mark.asyncio
async def test_watchdog_fires_on_httpx_connect_error() -> None:
    """poll() raising httpx.ConnectError counts toward dry polls (Sonos case)."""
    drv = _make_driver()
    drv._connected = True
    drv.set_state("connected", True)
    drv.poll_raises = httpx.ConnectError("connection refused")

    await drv.start_polling(0.01)
    await asyncio.sleep(0.2)

    assert drv.get_state("connected") is False


@pytest.mark.asyncio
async def test_watchdog_fires_on_httpx_timeout() -> None:
    """poll() raising httpx.TimeoutException counts toward dry polls."""
    drv = _make_driver()
    drv._connected = True
    drv.set_state("connected", True)
    drv.poll_raises = httpx.ConnectTimeout("timeout")

    await drv.start_polling(0.01)
    await asyncio.sleep(0.2)

    assert drv.get_state("connected") is False


@pytest.mark.asyncio
async def test_watchdog_does_not_fire_on_clean_polls() -> None:
    """poll() returning cleanly keeps connected=True indefinitely."""
    drv = _make_driver()
    drv._connected = True
    drv.set_state("connected", True)
    drv.poll_raises = None

    await drv.start_polling(0.01)
    await asyncio.sleep(0.15)
    await drv.stop_polling()

    assert drv.get_state("connected") is True
    assert drv.poll_count >= 5  # Should have polled multiple times


@pytest.mark.asyncio
async def test_watchdog_does_not_fire_on_protocol_errors() -> None:
    """poll() raising ValueError (protocol-level) doesn't penalize watchdog."""
    drv = _make_driver()
    drv._connected = True
    drv.set_state("connected", True)
    drv.poll_raises = ValueError("unexpected response shape")

    await drv.start_polling(0.01)
    await asyncio.sleep(0.15)
    await drv.stop_polling()

    # Device is reachable (it responded, just with garbage) — connected stays True
    assert drv.get_state("connected") is True


@pytest.mark.asyncio
async def test_dry_polls_reset_on_recovery() -> None:
    """Failed polls then a success resets the counter (no premature disconnect)."""
    state = StateStore()
    events = EventBus()
    drv = _CountingDriver(
        device_id="test_dev",
        # Give the test plenty of headroom — 10 misses required
        config={"max_missed_polls": 10},
        state=state,
        events=events,
    )
    drv._connected = True
    drv.set_state("connected", True)
    drv.poll_raises = ConnectionError("flaky")

    await drv.start_polling(0.01)
    # Let a few polls fail (well under the 10-poll threshold)
    await asyncio.sleep(0.05)
    # Recover
    drv.poll_raises = None
    # Let several polls succeed
    await asyncio.sleep(0.1)
    await drv.stop_polling()

    # Should still be connected — recovery reset the dry-poll counter
    assert drv.get_state("connected") is True


@pytest.mark.asyncio
async def test_watchdog_classifies_specific_fault_from_poll_error() -> None:
    """A plain ConnectionError whose message names a specific cause is
    classified (connection_refused) rather than defaulting to no_response, so a
    state-change trigger sees the right offline edge."""
    from server.core.connection_fault import CONNECTION_REFUSED

    drv = _make_driver()
    drv.config["host"] = "192.0.2.1"
    drv.config["port"] = 4000
    drv._connected = True
    drv.set_state("connected", True)
    drv.poll_raises = ConnectionError("connection refused")

    await drv.start_polling(0.01)
    await asyncio.sleep(0.2)

    assert drv.get_state("connected") is False
    assert drv.last_fault is not None
    assert drv.last_fault.code == CONNECTION_REFUSED


@pytest.mark.asyncio
async def test_watchdog_uses_no_response_for_a_generic_drop() -> None:
    """A poll error with no specific signature keeps the stopped-answering
    no_response wording (the classifier's generic transport_disconnected
    fallback would read as 'connection dropped', which is less accurate for a
    device that was answering and then went quiet)."""
    from server.core.connection_fault import NO_RESPONSE

    drv = _make_driver()
    drv._connected = True
    drv.set_state("connected", True)
    drv.poll_raises = OSError("boom")

    await drv.start_polling(0.01)
    await asyncio.sleep(0.2)

    assert drv.get_state("connected") is False
    assert drv.last_fault is not None
    assert drv.last_fault.code == NO_RESPONSE
    assert "stopped answering" in drv.last_fault.message


@pytest.mark.asyncio
async def test_verify_reachable_returns_true_for_localhost() -> None:
    """_verify_reachable returns True when a listener accepts."""
    drv = _make_driver()

    async def _handle(reader, writer):
        writer.close()

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await drv._verify_reachable("127.0.0.1", port, timeout=2.0) is True
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_verify_reachable_returns_false_for_dead_host() -> None:
    """_verify_reachable returns False when nothing is listening."""
    drv = _make_driver()
    # 127.0.0.1:1 — reserved, nothing should be listening
    assert await drv._verify_reachable("127.0.0.1", 1, timeout=0.5) is False
