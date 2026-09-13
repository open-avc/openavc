"""
THE bounds on the two awaits a device that stopped reading can park forever —
shared by every streaming transport (TCP, serial, SSH).

``StreamWriter.write()`` only queues bytes; ``drain()`` is where the caller
waits for the peer to take them. A device that keeps its socket open but stops
reading it (wedged firmware, a half-crashed control board, a serial peer
holding flow control asserted) leaves that await with nothing to wait for, and
asyncio's ``drain()`` has no timeout of its own — so the send blocks forever,
the command that issued it never returns, and the device goes on reporting
``connected`` because nothing ever raised.

How much a device can absorb before the block bites is a kernel socket-buffer
question, not a protocol one: it is the send buffer plus the peer's receive
window, both auto-tuned and neither knowable here. That is why the guard is a
clock and not a byte count — measured on one machine, the same stalled peer
swallowed ~1.2 MB with a 128 KB receive buffer and ~800 KB with a 4 KB one, and
a dozen identical sends went through before the thirteenth blocked. A byte cap
would have to guess at all of that; a deadline does not care.

Raising :class:`ConnectionFaultError` rather than returning a failure is what
gets the rest of the platform moving: the transport's existing ``except
(ConnectionError, OSError)`` arm runs ``_handle_disconnect()``, which clears
``connected`` and wakes the reconnect loop, and the API answers the caller 503
instead of hanging. The ``write_stalled`` code tells the integrator the device
is reachable but not listening, which is a different repair from a dropped
link.

**Closing has the same hole, and leaving it open cancels the cure.**
``wait_closed()` waits for the very bytes the peer is refusing, so tearing the
connection down blocks exactly where sending did. The teardown is what emits
``device.disconnected``, so a close that never returns means the reconnect loop
is never started and the device stays offline for good — a worse outcome than
the hang, and one that only appears once sending is fixed and disconnects
actually start happening. :func:`close_or_abandon` therefore gives the flush a
short deadline and then drops the socket outright: the queued bytes are going
nowhere regardless, and releasing the connection is what lets the device come
back.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from openavc.core.connection_fault import WRITE_STALLED, ConnectionFaultError
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Seconds to let a single write settle before calling the peer stalled.
#
# This bounds backpressure, not round-trip time: nothing here waits on a reply,
# only on the device reading bytes off its own socket. A device under load may
# legitimately stall a moment, so the window is generous — but it is far below
# any human's patience for a button that does nothing, which is the failure
# this exists to prevent.
WRITE_DRAIN_TIMEOUT_S = 5.0

# Seconds to let a close flush before the socket is dropped on the floor.
#
# Shorter than the send deadline on purpose: by this point the connection is
# being torn down and nothing is waiting on those bytes arriving, while the
# reconnect that restores the device is queued behind this returning. Patience
# here buys nothing and costs the recovery.
CLOSE_FLUSH_TIMEOUT_S = 2.0


class _Drainable(Protocol):
    """The one method this guard needs — an asyncio StreamWriter or stdin."""

    async def drain(self) -> None: ...


class _Closeable(Protocol):
    """An asyncio StreamWriter, as far as closing is concerned."""

    def close(self) -> None: ...
    async def wait_closed(self) -> None: ...


async def drain_or_stalled(
    writer: _Drainable,
    name: str,
    *,
    timeout: float | None = None,
) -> None:
    """Await ``writer.drain()``, giving up after ``timeout`` seconds.

    Args:
        writer: The stream whose queued bytes are being flushed.
        name: Device label, for the message the integrator reads.
        timeout: Seconds to wait. ``None`` reads :data:`WRITE_DRAIN_TIMEOUT_S`
            at call time, so a test can shorten the wait without every caller
            having to thread a value through.

    Raises:
        ConnectionFaultError: Coded ``write_stalled`` when the peer has not
            taken the bytes within ``timeout``. A subclass of ``ConnectionError``,
            so callers already handling one need no new arm.
    """
    limit = WRITE_DRAIN_TIMEOUT_S if timeout is None else timeout
    try:
        await asyncio.wait_for(writer.drain(), timeout=limit)
    except asyncio.TimeoutError:
        # The label and the deadline are diagnostics and belong in the log. The
        # exception's own message becomes ``offline_detail`` on the device card,
        # so it says what happened and what to do about it, and nothing else.
        log.warning(
            "[%s] write stalled: the device took no data for %gs.", name, limit
        )
        raise ConnectionFaultError(
            "The device stopped accepting data. It's still on the network "
            "but not reading its connection — power-cycle it if it stays "
            "this way.",
            code=WRITE_STALLED,
        ) from None


async def close_or_abandon(
    writer: _Closeable,
    name: str,
    *,
    timeout: float | None = None,
) -> None:
    """Close ``writer``, giving up on the graceful flush if the peer stalls.

    Never raises: closing is already the failure path, and the caller's next
    move — emitting the disconnect that starts the reconnect — must happen
    whether or not the socket went quietly.

    Args:
        writer: The stream to close.
        name: Device label, for the log line when the flush is abandoned.
        timeout: Seconds to allow. ``None`` reads :data:`CLOSE_FLUSH_TIMEOUT_S`
            at call time.
    """
    limit = CLOSE_FLUSH_TIMEOUT_S if timeout is None else timeout
    try:
        writer.close()
    except (ConnectionError, OSError, AttributeError, RuntimeError):
        return
    wait_closed = getattr(writer, "wait_closed", None)
    if wait_closed is None:
        return
    try:
        await asyncio.wait_for(wait_closed(), timeout=limit)
    except asyncio.TimeoutError:
        # The peer never took the queued bytes. Abort the socket so the fd is
        # released now; without this the teardown parks here and the reconnect
        # behind it never runs.
        log.warning(
            "[%s] close timed out after %gs — the device never took the "
            "queued bytes; dropping the connection.",
            name,
            limit,
        )
        transport = getattr(writer, "transport", None)
        abort = getattr(transport, "abort", None)
        if abort is not None:
            try:
                abort()
            except Exception:  # nothing left to salvage; teardown continues
                log.debug("[%s] abort() failed during close", name, exc_info=True)
    except (ConnectionError, OSError, AttributeError):
        pass
