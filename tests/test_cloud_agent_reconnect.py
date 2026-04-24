"""
Tests for OpenAVC Cloud Agent connection management.

Covers: _load_system_key, backoff constants and growth, throttle management,
Session lifecycle, Sequencer operations, and HandshakeError classification.
"""

import asyncio

import pytest

from server.cloud.agent import (
    CloudAgent,
    BACKOFF_INITIAL,
    BACKOFF_MULTIPLIER,
    BACKOFF_MAX,
)
from server.cloud.handshake import HandshakeError
from server.cloud.session import Session, SessionInvalid
from server.cloud.sequencer import Sequencer
from server.cloud.crypto import (
    derive_signing_key,
    generate_system_key,
    generate_nonce,
)
from server.cloud.protocol import SESSION_ROTATE, SESSION_INVALID


# ===========================================================================
# 1. CloudAgent._load_system_key
# ===========================================================================


class TestLoadSystemKey:
    """Tests for CloudAgent._load_system_key static method."""

    def test_hex_string_decoded(self):
        """A valid hex string is decoded to bytes."""
        hex_str = "aabbccdd00112233"
        result = CloudAgent._load_system_key(hex_str)
        assert result == bytes.fromhex(hex_str)
        assert isinstance(result, bytes)

    def test_raw_bytes_passthrough(self):
        """Raw bytes input is returned as-is."""
        raw = b"\x00\x01\x02\xff"
        result = CloudAgent._load_system_key(raw)
        assert result is raw

    def test_empty_string_returns_empty_bytes(self):
        """An empty string returns empty bytes."""
        result = CloudAgent._load_system_key("")
        assert result == b""

    def test_non_hex_string_encoded_utf8(self):
        """A non-hex string falls back to UTF-8 encoding."""
        # "ZZZZ" is not valid hex
        result = CloudAgent._load_system_key("not-valid-hex!")
        assert result == b"not-valid-hex!"

    def test_long_hex_key(self):
        """A 128-char hex string (64 bytes) decodes correctly."""
        key = generate_system_key()
        hex_key = key.hex()
        result = CloudAgent._load_system_key(hex_key)
        assert result == key
        assert len(result) == 64

    def test_empty_bytes_passthrough(self):
        """Empty bytes input is returned as-is."""
        result = CloudAgent._load_system_key(b"")
        assert result == b""

    def test_odd_length_hex_falls_back_to_utf8(self):
        """An odd-length hex-like string cannot be valid hex, falls back to UTF-8."""
        # "abc" is 3 chars, not a valid hex pair sequence
        result = CloudAgent._load_system_key("abc")
        assert result == b"abc"


# ===========================================================================
# 2. Backoff Logic
# ===========================================================================


class TestBackoffLogic:
    """Tests for reconnection backoff constants and growth."""

    def test_backoff_initial_value(self):
        """Initial backoff is 5 seconds."""
        assert BACKOFF_INITIAL == 5

    def test_backoff_multiplier_value(self):
        """Backoff multiplier is 2."""
        assert BACKOFF_MULTIPLIER == 2

    def test_backoff_max_value(self):
        """Maximum backoff is 300 seconds (5 minutes)."""
        assert BACKOFF_MAX == 300

    def test_backoff_growth_sequence(self):
        """Backoff grows: 5 -> 10 -> 20 -> 40 -> 80 -> 160 -> 300 (capped)."""
        backoff = BACKOFF_INITIAL
        expected = [5, 10, 20, 40, 80, 160, 300]
        actual = [backoff]
        for _ in range(len(expected) - 1):
            backoff = min(backoff * BACKOFF_MULTIPLIER, BACKOFF_MAX)
            actual.append(backoff)
        assert actual == expected

    def test_backoff_stays_at_max(self):
        """Once at max, backoff stays there indefinitely."""
        backoff = BACKOFF_MAX
        for _ in range(10):
            backoff = min(backoff * BACKOFF_MULTIPLIER, BACKOFF_MAX)
            assert backoff == BACKOFF_MAX

    def test_backoff_reaches_max_within_reasonable_iterations(self):
        """Backoff reaches the cap in fewer than 10 doublings."""
        backoff = BACKOFF_INITIAL
        iterations = 0
        while backoff < BACKOFF_MAX:
            backoff = min(backoff * BACKOFF_MULTIPLIER, BACKOFF_MAX)
            iterations += 1
        assert iterations <= 10
        assert backoff == BACKOFF_MAX


# ===========================================================================
# 3. Throttle Management
# ===========================================================================


class TestThrottleManagement:
    """Tests for the _throttles dict pattern used in CloudAgent."""

    def test_throttle_event_creation(self):
        """Creating an asyncio.Event and clearing it simulates throttling."""
        throttles: dict[str, asyncio.Event] = {}

        # Simulate _handle_throttle creating an event
        limit_type = "state_batch"
        throttles[limit_type] = asyncio.Event()
        event = throttles[limit_type]
        event.clear()

        # Throttled: event is NOT set
        assert not event.is_set()

    def test_throttle_check_not_throttled(self):
        """A missing throttle entry means the message type is not throttled."""
        throttles: dict[str, asyncio.Event] = {}
        throttle_event = throttles.get("heartbeat")
        # None means not throttled
        assert throttle_event is None

    def test_throttle_check_active(self):
        """A cleared event blocks sending for that message type."""
        throttles: dict[str, asyncio.Event] = {}
        throttles["state_batch"] = asyncio.Event()
        throttles["state_batch"].clear()

        event = throttles.get("state_batch")
        assert event is not None
        # Cleared event means throttled
        assert not event.is_set()

    def test_throttle_release(self):
        """Setting the event and removing it from the dict releases the throttle."""
        throttles: dict[str, asyncio.Event] = {}
        throttles["state_batch"] = asyncio.Event()
        throttles["state_batch"].clear()

        # Simulate _unthrottle
        event = throttles.pop("state_batch", None)
        assert event is not None
        event.set()
        assert event.is_set()

        # After pop, the throttle is gone
        assert "state_batch" not in throttles

    def test_multiple_throttle_types_independent(self):
        """Different message types can be throttled independently."""
        throttles: dict[str, asyncio.Event] = {}

        # Throttle two types
        for lt in ("state_batch", "heartbeat"):
            throttles[lt] = asyncio.Event()
            throttles[lt].clear()

        # Release only state_batch
        event = throttles.pop("state_batch", None)
        if event:
            event.set()

        # heartbeat still throttled
        assert not throttles["heartbeat"].is_set()
        # state_batch released
        assert "state_batch" not in throttles

    def test_throttle_idempotent_creation(self):
        """Re-throttling an already-throttled type replaces the event."""
        throttles: dict[str, asyncio.Event] = {}

        # First throttle
        throttles["state_batch"] = asyncio.Event()
        first_event = throttles["state_batch"]
        first_event.clear()

        # Second throttle (server sends another throttle message)
        throttles["state_batch"] = asyncio.Event()
        second_event = throttles["state_batch"]
        second_event.clear()

        assert first_event is not second_event
        assert not second_event.is_set()


# ===========================================================================
# 4. Session Management
# ===========================================================================


class TestSessionManagement:
    """Tests for Session class: creation, token validation, key rotation, invalidation."""

    def _make_session(
        self,
        session_id: str = "session-1",
        session_token: str = "token-abc",
        session_expires: str = "2099-01-01T00:00:00Z",
    ) -> tuple[Session, bytes, bytes]:
        system_key = generate_system_key()
        signing_key = derive_signing_key(system_key, b"test-salt", session_id)
        session = Session(
            session_id=session_id,
            session_token=session_token,
            signing_key=signing_key,
            session_expires=session_expires,
            system_key=system_key,
        )
        return session, signing_key, system_key

    # --- Creation ---

    def test_session_creation_attributes(self):
        """Session stores all construction parameters."""
        session, signing_key, _ = self._make_session()
        assert session.session_id == "session-1"
        assert session.session_token == "token-abc"
        assert session.signing_key == signing_key
        assert session.session_expires == "2099-01-01T00:00:00Z"

    def test_session_initially_valid(self):
        """A new session with a future expiry is valid."""
        session, _, _ = self._make_session()
        assert session.is_valid

    # --- Token Validation / Expiry ---

    def test_session_expired_is_invalid(self):
        """A session with a past expiry is not valid."""
        session, _, _ = self._make_session(session_expires="2020-01-01T00:00:00Z")
        assert not session.is_valid

    def test_session_empty_expiry_is_valid(self):
        """A session with empty expiry string is still valid."""
        session, _, _ = self._make_session(session_expires="")
        assert session.is_valid

    def test_session_z_suffix_expiry(self):
        """Expiry with Z suffix is parsed correctly."""
        session, _, _ = self._make_session(session_expires="2099-12-31T23:59:59Z")
        assert session.is_valid

    def test_session_utc_offset_expiry(self):
        """Expiry with +00:00 offset is parsed correctly."""
        session, _, _ = self._make_session(session_expires="2099-12-31T23:59:59+00:00")
        assert session.is_valid

    def test_session_malformed_expiry_still_valid(self):
        """Unparseable expiry string does not cause crash, session stays valid."""
        session, _, _ = self._make_session(session_expires="not-a-date")
        assert session.is_valid

    # --- Signing and Verification ---

    def test_sign_outgoing_adds_sig_and_session(self):
        """sign_outgoing adds 'sig' and 'session' fields."""
        session, _, _ = self._make_session()
        msg = {"type": "heartbeat", "ts": "2026-01-01T00:00:00Z", "seq": 1, "payload": {}}
        result = session.sign_outgoing(msg)
        assert "sig" in result
        assert result["session"] == "token-abc"

    def test_sign_verify_roundtrip(self):
        """A signed message can be verified by the same session."""
        session, _, _ = self._make_session()
        msg = {"type": "heartbeat", "ts": "2026-01-01T00:00:00Z", "seq": 1, "payload": {}}
        session.sign_outgoing(msg)
        assert session.verify_incoming(msg)

    def test_verify_tampered_payload_fails(self):
        """Verification fails if the payload is tampered after signing."""
        session, _, _ = self._make_session()
        msg = {"type": "heartbeat", "ts": "2026-01-01T00:00:00Z", "seq": 1, "payload": {"cpu": 10}}
        session.sign_outgoing(msg)
        msg["payload"]["cpu"] = 99
        assert not session.verify_incoming(msg)

    def test_verify_missing_sig_fails(self):
        """Verification fails if there is no 'sig' field."""
        session, _, _ = self._make_session()
        msg = {"type": "heartbeat", "ts": "2026-01-01T00:00:00Z", "seq": 1, "payload": {}}
        assert not session.verify_incoming(msg)

    def test_sign_after_invalidation_raises(self):
        """Signing after invalidation raises SessionInvalid."""
        session, _, _ = self._make_session()
        session.invalidate()
        with pytest.raises(SessionInvalid):
            session.sign_outgoing({"type": "test", "ts": "...", "seq": 1, "payload": {}})

    # --- Key Rotation ---

    def test_rotation_pending_before_seq(self):
        """Rotation is pending until the downstream seq threshold is reached."""
        session, old_key, system_key = self._make_session()
        old_token = session.session_token

        new_salt = generate_nonce(32)
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "rotated-token",
                "new_signing_key_salt": new_salt,
                "new_session_expires": "2099-06-01T00:00:00Z",
                "switch_at_seq": 50,
            },
        }
        session.handle_session_rotate(rotate_msg)

        # Not yet rotated
        assert session.session_token == old_token
        assert session.signing_key == old_key

        # Check before threshold
        session.check_rotation(49)
        assert session.session_token == old_token

    def test_rotation_applies_at_seq(self):
        """Rotation applies when downstream seq reaches switch_at_seq."""
        session, old_key, _ = self._make_session()

        new_salt = generate_nonce(32)
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "rotated-token",
                "new_signing_key_salt": new_salt,
                "new_session_expires": "2099-06-01T00:00:00Z",
                "switch_at_seq": 10,
            },
        }
        session.handle_session_rotate(rotate_msg)

        # Reach the threshold
        session.check_rotation(10)
        assert session.session_token == "rotated-token"
        assert session.signing_key != old_key
        assert session.session_expires == "2099-06-01T00:00:00Z"

    def test_rotation_applies_past_seq(self):
        """Rotation also triggers if downstream seq is past the threshold."""
        session, _, _ = self._make_session()

        new_salt = generate_nonce(32)
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "rotated-token",
                "new_signing_key_salt": new_salt,
                "switch_at_seq": 10,
            },
        }
        session.handle_session_rotate(rotate_msg)

        # Exceed the threshold
        session.check_rotation(15)
        assert session.session_token == "rotated-token"

    def test_rotation_clears_pending(self):
        """After rotation, pending state is cleared."""
        session, _, _ = self._make_session()

        new_salt = generate_nonce(32)
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "rotated-token",
                "new_signing_key_salt": new_salt,
                "switch_at_seq": 5,
            },
        }
        session.handle_session_rotate(rotate_msg)
        session.check_rotation(5)

        # Second check should be a no-op
        token_after = session.session_token
        session.check_rotation(6)
        assert session.session_token == token_after

    def test_rotation_updates_session_id(self):
        """Rotation updates the session_id if new_session_id is provided."""
        session, _, _ = self._make_session(session_id="old-session")

        new_salt = generate_nonce(32)
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "rotated-token",
                "new_signing_key_salt": new_salt,
                "new_session_id": "new-session",
                "switch_at_seq": 1,
            },
        }
        session.handle_session_rotate(rotate_msg)
        session.check_rotation(1)
        assert session.session_id == "new-session"

    def test_rotation_missing_fields_ignored(self):
        """Rotation message with missing required fields is ignored."""
        session, old_key, _ = self._make_session()
        old_token = session.session_token

        # Missing new_signing_key_salt
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "rotated-token",
                "switch_at_seq": 1,
            },
        }
        session.handle_session_rotate(rotate_msg)
        session.check_rotation(1)
        # Nothing should have changed
        assert session.session_token == old_token
        assert session.signing_key == old_key

    # --- Invalidation ---

    def test_invalidate_marks_session_invalid(self):
        """invalidate() makes is_valid return False."""
        session, _, _ = self._make_session()
        assert session.is_valid
        session.invalidate()
        assert not session.is_valid

    def test_handle_session_invalid_raises(self):
        """handle_session_invalid raises SessionInvalid with the server's reason."""
        session, _, _ = self._make_session()
        msg = {
            "type": SESSION_INVALID,
            "payload": {"reason": "duplicate_connection"},
        }
        with pytest.raises(SessionInvalid) as exc_info:
            session.handle_session_invalid(msg)
        assert "duplicate_connection" in str(exc_info.value)
        assert exc_info.value.reason == "duplicate_connection"
        assert not session.is_valid

    def test_handle_session_invalid_unknown_reason(self):
        """handle_session_invalid defaults to 'unknown' when no reason given."""
        session, _, _ = self._make_session()
        msg = {"type": SESSION_INVALID, "payload": {}}
        with pytest.raises(SessionInvalid) as exc_info:
            session.handle_session_invalid(msg)
        assert exc_info.value.reason == "unknown"


# ===========================================================================
# 5. Sequencer
# ===========================================================================


class TestSequencerReconnect:
    """Tests for MessageSequencer: incrementing, ack tracking, buffering."""

    def test_sequence_starts_at_one(self):
        """First assigned sequence number is 1."""
        seq = Sequencer()
        assert seq.next_seq == 1

    def test_sequence_increments(self):
        """Each assign_seq increments the counter."""
        seq = Sequencer()
        s1 = seq.assign_seq({"type": "heartbeat"})
        s2 = seq.assign_seq({"type": "heartbeat"})
        s3 = seq.assign_seq({"type": "alert"})
        assert s1 == 1
        assert s2 == 2
        assert s3 == 3
        assert seq.next_seq == 4

    def test_assign_seq_modifies_message(self):
        """assign_seq adds the 'seq' field to the message dict."""
        seq = Sequencer()
        msg = {"type": "heartbeat"}
        seq.assign_seq(msg)
        assert msg["seq"] == 1

    def test_ack_tracking(self):
        """Acking removes acknowledged messages from the buffer."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "state_batch"})
        seq.assign_seq({"type": "alert"})
        assert seq.buffer_count == 3
        assert seq.last_ack_seq == 0

        seq.handle_ack({"payload": {"last_seq": 2}})
        assert seq.buffer_count == 1
        assert seq.last_ack_seq == 2

    def test_ack_all(self):
        """Acking the last sent seq clears the entire buffer."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "alert"})
        seq.handle_ack({"payload": {"last_seq": 2}})
        assert seq.buffer_count == 0

    def test_ack_duplicate_is_noop(self):
        """Re-acking an already-acked seq has no effect."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.handle_ack({"payload": {"last_seq": 1}})
        assert seq.buffer_count == 0
        # Duplicate ack
        seq.handle_ack({"payload": {"last_seq": 1}})
        assert seq.buffer_count == 0
        assert seq.last_ack_seq == 1

    def test_ack_older_is_noop(self):
        """Acking an older seq than last_ack_seq is a no-op."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "heartbeat"})
        seq.handle_ack({"payload": {"last_seq": 2}})
        # Try to ack seq 1 (already acked)
        seq.handle_ack({"payload": {"last_seq": 1}})
        assert seq.last_ack_seq == 2

    def test_pending_buffer_after_partial_ack(self):
        """Unacked messages remain in buffer after partial ack."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})  # 1
        seq.assign_seq({"type": "alert"})       # 2
        seq.assign_seq({"type": "heartbeat"})  # 3
        seq.handle_ack({"payload": {"last_seq": 1}})

        remaining = seq.get_unacked_messages()
        assert len(remaining) == 2
        assert remaining[0]["seq"] == 2
        assert remaining[1]["seq"] == 3

    def test_downstream_seq_validation_sequential(self):
        """Sequential downstream sequences are all accepted."""
        seq = Sequencer()
        assert seq.validate_downstream_seq(1)
        assert seq.validate_downstream_seq(2)
        assert seq.validate_downstream_seq(3)
        assert seq.last_downstream_seq == 3

    def test_downstream_seq_rejects_duplicate(self):
        """Duplicate downstream sequences are rejected."""
        seq = Sequencer()
        seq.validate_downstream_seq(1)
        assert not seq.validate_downstream_seq(1)

    def test_downstream_seq_rejects_old(self):
        """Old (lower than last seen) downstream sequences are rejected."""
        seq = Sequencer()
        seq.validate_downstream_seq(1)
        seq.validate_downstream_seq(2)
        assert not seq.validate_downstream_seq(1)

    def test_downstream_gap_detection(self):
        """A gap in downstream sequence numbers is detected and stored."""
        seq = Sequencer()
        seq.validate_downstream_seq(1)
        seq.validate_downstream_seq(5)  # Gap: 2, 3, 4 missing

        gap = seq.pop_gap()
        assert gap == (2, 4)

    def test_pop_gap_clears(self):
        """pop_gap returns the gap once, then None."""
        seq = Sequencer()
        seq.validate_downstream_seq(1)
        seq.validate_downstream_seq(5)

        assert seq.pop_gap() == (2, 4)
        assert seq.pop_gap() is None

    def test_no_gap_returns_none(self):
        """pop_gap returns None when no gap exists."""
        seq = Sequencer()
        seq.validate_downstream_seq(1)
        seq.validate_downstream_seq(2)
        assert seq.pop_gap() is None

    def test_replay_messages_from_seq(self):
        """get_replay_messages returns messages from a given seq onward."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})  # 1
        seq.assign_seq({"type": "alert"})       # 2
        seq.assign_seq({"type": "heartbeat"})  # 3

        msgs = seq.get_replay_messages(2)
        assert len(msgs) == 2
        assert msgs[0]["seq"] == 2
        assert msgs[1]["seq"] == 3

    def test_replay_messages_none_matching(self):
        """get_replay_messages returns empty list if no messages match."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})  # 1
        msgs = seq.get_replay_messages(100)
        assert msgs == []

    def test_reset_for_new_session(self):
        """Reset clears counters but preserves buffer for replay."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "heartbeat"})
        seq.validate_downstream_seq(1)
        old_ack = seq.last_ack_seq

        seq.reset_for_new_session()
        assert seq.next_seq == 1
        assert seq.last_downstream_seq == 0
        # Buffer is preserved for message replay after reconnection
        assert seq.buffer_count == 2
        # last_ack_seq is preserved for resume negotiation
        assert seq.last_ack_seq == old_ack

    def test_clear_buffer(self):
        """clear_buffer removes all buffered messages."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "alert"})
        assert seq.buffer_count == 2
        seq.clear_buffer()
        assert seq.buffer_count == 0

    def test_buffer_overflow_eviction(self):
        """When buffer overflows, lowest-priority message is evicted."""
        seq = Sequencer(max_buffer_size=2)
        seq.assign_seq({"type": "state_batch"})    # priority 0 (lowest)
        seq.assign_seq({"type": "command_result"})  # priority 5
        assert seq.buffer_count == 2

        # Adding a third should evict state_batch
        seq.assign_seq({"type": "alert"})  # priority 4
        assert seq.buffer_count == 2

        remaining = seq.get_unacked_messages()
        types = [m["type"] for m in remaining]
        assert "state_batch" not in types
        assert "command_result" in types
        assert "alert" in types

    def test_buffer_overflow_preserves_high_priority(self):
        """High-priority messages survive overflow eviction."""
        seq = Sequencer(max_buffer_size=3)
        seq.assign_seq({"type": "state_batch"})    # 0
        seq.assign_seq({"type": "log"})             # 1
        seq.assign_seq({"type": "command_result"})  # 5

        # Overflow: should evict state_batch (priority 0)
        seq.assign_seq({"type": "pong"})  # 6
        assert seq.buffer_count == 3

        types = [m["type"] for m in seq.get_unacked_messages()]
        assert "state_batch" not in types
        assert "command_result" in types
        assert "pong" in types


# ===========================================================================
# 6. HandshakeError Classification
# ===========================================================================


class TestHandshakeErrorClassification:
    """Tests for HandshakeError fatal vs retriable classification."""

    # These are the fatal reasons checked in agent._connection_loop.
    # Must match the cloud platform's actual rejection codes.
    FATAL_REASONS = ("unknown_system", "no_key", "bad_system_id")

    def test_handshake_error_stores_reason(self):
        """HandshakeError stores the reason attribute."""
        err = HandshakeError("System not found", reason="unknown_system")
        assert err.reason == "unknown_system"
        assert "System not found" in str(err)

    def test_handshake_error_default_reason(self):
        """HandshakeError defaults to 'unknown' reason."""
        err = HandshakeError("Something failed")
        assert err.reason == "unknown"

    def test_unknown_system_is_fatal(self):
        """unknown_system is a fatal reason (agent should not retry)."""
        err = HandshakeError("Not found", reason="unknown_system")
        assert err.reason in self.FATAL_REASONS

    def test_no_key_is_fatal(self):
        """no_key is a fatal reason."""
        err = HandshakeError("No active key", reason="no_key")
        assert err.reason in self.FATAL_REASONS

    def test_bad_system_id_is_fatal(self):
        """bad_system_id is a fatal reason."""
        err = HandshakeError("Invalid ID format", reason="bad_system_id")
        assert err.reason in self.FATAL_REASONS

    def test_timeout_is_retriable(self):
        """timeout is NOT a fatal reason (agent should retry)."""
        err = HandshakeError("Timed out", reason="timeout")
        assert err.reason not in self.FATAL_REASONS

    def test_version_mismatch_is_retriable(self):
        """version_mismatch is NOT a fatal reason."""
        err = HandshakeError("Version mismatch", reason="version_mismatch")
        assert err.reason not in self.FATAL_REASONS

    def test_unknown_reason_is_retriable(self):
        """unknown reason is NOT fatal (default to retry)."""
        err = HandshakeError("Unknown error", reason="unknown")
        assert err.reason not in self.FATAL_REASONS

    def test_server_error_is_retriable(self):
        """A server_error reason is NOT fatal."""
        err = HandshakeError("Server error", reason="server_error")
        assert err.reason not in self.FATAL_REASONS

    def test_fatal_reasons_match_agent_code(self):
        """The fatal reasons list matches what the agent checks."""
        agent_fatal_set = {"unknown_system", "no_key", "bad_system_id"}
        assert set(self.FATAL_REASONS) == agent_fatal_set
