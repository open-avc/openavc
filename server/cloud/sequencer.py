"""
OpenAVC Cloud — Sequence number tracking and delivery guarantees.

Manages upstream sequence numbering, downstream sequence validation,
the send buffer (for replay after reconnection), and ack processing.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

from server.cloud.protocol import MESSAGE_PRIORITY, extract_payload
from server.utils.logger import get_logger

log = get_logger(__name__)

# Default max buffer size
DEFAULT_BUFFER_SIZE = 1000


class Sequencer:
    """
    Manages sequence numbers and the send buffer for reliable delivery.

    Upstream (agent → cloud):
    - Assigns monotonically increasing sequence numbers starting at 1.
    - Retains sent messages in a buffer until acknowledged by the server.
    - On buffer overflow, drops lowest-priority messages first.

    Downstream (cloud → agent):
    - Tracks the last seen downstream sequence number.
    - Detects gaps and duplicates.
    """

    def __init__(self, max_buffer_size: int = DEFAULT_BUFFER_SIZE):
        self._max_buffer_size = max_buffer_size

        # Upstream
        self._next_seq: int = 1
        # Ordered dict: seq -> (message_dict, priority)
        self._send_buffer: OrderedDict[int, tuple[dict[str, Any], int]] = OrderedDict()
        self._last_ack_seq: int = 0

        # Downstream
        self._last_downstream_seq: int = 0
        self._last_gap: tuple[int, int] | None = None

        # Use threading.Lock for sync methods (all operations are microsecond-fast
        # dict mutations, never held across await points). Safe for event loop usage.
        self._lock = threading.Lock()

    @property
    def next_seq(self) -> int:
        """The next upstream sequence number to assign."""
        return self._next_seq

    @property
    def last_ack_seq(self) -> int:
        """The last upstream sequence number acknowledged by the server."""
        return self._last_ack_seq

    @property
    def last_downstream_seq(self) -> int:
        """The last downstream sequence number processed."""
        return self._last_downstream_seq

    @property
    def buffer_count(self) -> int:
        """Number of un-acknowledged messages in the send buffer."""
        with self._lock:
            return len(self._send_buffer)

    def assign_seq(self, msg: dict[str, Any]) -> int:
        """
        Assign the next upstream sequence number to a message and buffer it.

        The message is modified in-place to include the 'seq' field and
        added to the send buffer for replay if needed.

        Args:
            msg: The message dict (must have 'type' field).

        Returns:
            The assigned sequence number.
        """
        msg_type = msg.get("type", "")
        priority = MESSAGE_PRIORITY.get(msg_type, 3)

        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            msg["seq"] = seq

            # Add to buffer
            self._send_buffer[seq] = (msg, priority)

            # Handle overflow
            if len(self._send_buffer) > self._max_buffer_size:
                self._evict_lowest_priority()

        return seq

    def handle_ack(self, msg: dict[str, Any]) -> None:
        """
        Process an ack message from the server.

        Removes all messages up to and including the acknowledged
        sequence number from the send buffer.

        Args:
            msg: The ack message dict.
        """
        payload = extract_payload(msg)
        acked_seq = payload.get("last_seq", 0)

        if acked_seq <= self._last_ack_seq:
            return  # Already processed this ack or older

        with self._lock:
            self._last_ack_seq = acked_seq

            # Remove acked messages from buffer
            seqs_to_remove = [s for s in self._send_buffer if s <= acked_seq]
            for s in seqs_to_remove:
                del self._send_buffer[s]

        if seqs_to_remove:
            log.debug(
                f"Sequencer: ack'd through seq {acked_seq}, "
                f"removed {len(seqs_to_remove)} from buffer, "
                f"{len(self._send_buffer)} remaining"
            )

    def validate_downstream_seq(self, seq: int) -> bool:
        """
        Validate a downstream sequence number.

        Checks for duplicates and out-of-order messages.

        Args:
            seq: The downstream sequence number.

        Returns:
            True if the sequence is valid (new, sequential).
        """
        if seq <= self._last_downstream_seq:
            log.warning(
                f"Sequencer: duplicate/old downstream seq {seq} "
                f"(last seen: {self._last_downstream_seq})"
            )
            return False

        if seq != self._last_downstream_seq + 1:
            # Gap detected — log and record for reporting, but still accept
            expected = self._last_downstream_seq + 1
            log.warning(
                f"Sequencer: downstream seq gap — expected "
                f"{expected}, got {seq}"
            )
            self._last_gap = (expected, seq - 1)

        self._last_downstream_seq = seq
        return True

    def pop_gap(self) -> tuple[int, int] | None:
        """Return and clear the last detected downstream gap, if any.

        Returns:
            (expected_start, missing_end) tuple, or None if no gap.
        """
        gap = self._last_gap
        self._last_gap = None
        return gap

    def get_unacked_messages(self) -> list[dict[str, Any]]:
        """
        Get all un-acknowledged messages for replay after reconnection.

        Returns messages sorted by sequence number. The caller should
        re-sign these with the new session before sending.

        Returns:
            List of message dicts, ordered by sequence number.
        """
        with self._lock:
            return [msg for msg, _priority in self._send_buffer.values()]

    def get_replay_messages(self, replay_from_seq: int) -> list[dict[str, Any]]:
        """
        Get messages to replay from a specific sequence number.

        Called after receiving resume_from from the server.

        Args:
            replay_from_seq: Replay messages with seq >= this value.

        Returns:
            List of message dicts to replay, ordered by sequence number.
        """
        with self._lock:
            return [
                msg
                for seq, (msg, _priority) in self._send_buffer.items()
                if seq >= replay_from_seq
            ]

    def reset_for_new_session(self) -> None:
        """
        Reset sequence counters for a new session.

        Called after a fresh handshake. The send buffer is preserved
        (messages will be replayed), but sequence counters restart.
        """
        with self._lock:
            self._next_seq = 1
            self._last_downstream_seq = 0
            # Clear the send buffer — old messages have stale sequence numbers
            # that would collide with the new session's numbering
            self._send_buffer.clear()
            # Don't reset last_ack_seq — used for resume negotiation

    def clear_buffer(self) -> None:
        """Clear the send buffer (e.g., after successful replay)."""
        with self._lock:
            self._send_buffer.clear()

    def _evict_lowest_priority(self) -> None:
        """
        Evict the lowest-priority message from the buffer.

        Priority order (lowest = evicted first):
        state_batch (0) < log (1) < heartbeat (2) < alert_resolved (3) <
        alert (4) < command_result (5) < pong (6)

        Must be called while holding self._lock.
        """
        if not self._send_buffer:
            return

        # Find the entry with the lowest priority
        min_seq = None
        min_priority = float("inf")
        for seq, (_msg, priority) in self._send_buffer.items():
            if priority < min_priority:
                min_priority = priority
                min_seq = seq

        if min_seq is not None:
            evicted_msg = self._send_buffer[min_seq][0]
            del self._send_buffer[min_seq]
            log.debug(
                f"Sequencer: buffer overflow — evicted {evicted_msg.get('type')} "
                f"seq {min_seq} (priority {min_priority})"
            )
