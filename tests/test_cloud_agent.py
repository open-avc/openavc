"""
Tests for the OpenAVC Cloud Agent module.

Tests cover: crypto, protocol, handshake, session, sequencer,
heartbeat, state relay, command handler, and agent integration.
"""

import asyncio
import json

import pytest

from server.cloud.crypto import (
    hkdf_sha256,
    derive_auth_key,
    derive_signing_key,
    compute_hmac,
    verify_hmac,
    compute_auth_proof,
    verify_auth_proof,
    canonical_json,
    sign_message,
    verify_message_signature,
    generate_nonce,
    generate_system_key,
    hash_system_key,
)
from server.cloud.protocol import (
    PROTOCOL_VERSION,
    HELLO, CHALLENGE, AUTHENTICATE, SESSION_START,
    AUTH_FAILED, VERSION_MISMATCH, RESUME, RESUME_FROM,
    HEARTBEAT, STATE_BATCH, ALERT, PONG, SESSION_ROTATE, SESSION_INVALID, COMMAND,
    COMMAND_RESULT,
    HANDSHAKE_TYPES, UPSTREAM_TYPES, DOWNSTREAM_TYPES,
    MESSAGE_PRIORITY,
    build_hello, build_authenticate, build_resume,
    build_signed_message, build_heartbeat, build_state_batch,
    build_pong, build_command_result,
    parse_message, is_handshake_message,
    verify_steady_state_message, extract_payload,
    ProtocolError,
)
from server.cloud.handshake import Handshake, HandshakeResult, HandshakeError
from server.cloud.session import Session, SessionInvalid
from server.cloud.sequencer import Sequencer


# ===========================================================================
# Crypto Tests
# ===========================================================================


class TestCrypto:
    """Tests for server.cloud.crypto."""

    def test_hkdf_deterministic(self):
        """HKDF produces the same output for the same inputs."""
        ikm = b"test-key-material"
        salt = b"test-salt"
        info = b"test-info"
        key1 = hkdf_sha256(ikm, salt, info, 32)
        key2 = hkdf_sha256(ikm, salt, info, 32)
        assert key1 == key2
        assert len(key1) == 32

    def test_hkdf_different_info_different_keys(self):
        """Different info strings produce different keys."""
        ikm = b"test-key-material"
        salt = b"test-salt"
        key1 = hkdf_sha256(ikm, salt, b"info-a", 32)
        key2 = hkdf_sha256(ikm, salt, b"info-b", 32)
        assert key1 != key2

    def test_hkdf_different_salt_different_keys(self):
        """Different salts produce different keys."""
        ikm = b"test-key-material"
        key1 = hkdf_sha256(ikm, b"salt-a", b"info", 32)
        key2 = hkdf_sha256(ikm, b"salt-b", b"info", 32)
        assert key1 != key2

    def test_hkdf_variable_length(self):
        """HKDF can produce keys of different lengths."""
        ikm = b"test-key-material"
        key16 = hkdf_sha256(ikm, b"salt", b"info", 16)
        key32 = hkdf_sha256(ikm, b"salt", b"info", 32)
        key64 = hkdf_sha256(ikm, b"salt", b"info", 64)
        assert len(key16) == 16
        assert len(key32) == 32
        assert len(key64) == 64

    def test_derive_auth_key(self):
        """derive_auth_key produces a 32-byte key deterministically."""
        system_key = generate_system_key()
        system_id = "test-system-123"
        key1 = derive_auth_key(system_key, system_id)
        key2 = derive_auth_key(system_key, system_id)
        assert key1 == key2
        assert len(key1) == 32

    def test_derive_auth_key_different_ids(self):
        """Different system IDs produce different auth keys."""
        system_key = generate_system_key()
        key1 = derive_auth_key(system_key, "system-a")
        key2 = derive_auth_key(system_key, "system-b")
        assert key1 != key2

    def test_derive_signing_key(self):
        """derive_signing_key uses salt and session_id for uniqueness."""
        system_key = generate_system_key()
        salt = b"random-salt-bytes"
        key1 = derive_signing_key(system_key, salt, "session-1")
        key2 = derive_signing_key(system_key, salt, "session-2")
        assert key1 != key2  # Different session IDs
        assert len(key1) == 32

    def test_hmac_sign_verify(self):
        """HMAC signing and verification round-trip."""
        key = b"test-signing-key-32-bytes-long!!"
        message = b"hello world"
        sig = compute_hmac(key, message)
        assert verify_hmac(key, message, sig)

    def test_hmac_wrong_key_fails(self):
        """HMAC verification with wrong key fails."""
        key = b"correct-key"
        wrong_key = b"wrong-key!!"
        message = b"hello world"
        sig = compute_hmac(key, message)
        assert not verify_hmac(wrong_key, message, sig)

    def test_hmac_tampered_message_fails(self):
        """HMAC verification with tampered message fails."""
        key = b"test-signing-key"
        sig = compute_hmac(key, b"original message")
        assert not verify_hmac(key, b"tampered message", sig)

    def test_auth_proof_round_trip(self):
        """Challenge-response proof can be computed and verified."""
        system_key = generate_system_key()
        system_id = "test-system-456"
        auth_key = derive_auth_key(system_key, system_id)
        nonce = generate_nonce()
        timestamp = "2026-03-16T14:30:00.000Z"

        proof = compute_auth_proof(auth_key, nonce, system_id, timestamp)
        assert verify_auth_proof(auth_key, nonce, system_id, timestamp, proof)

    def test_auth_proof_wrong_nonce_fails(self):
        """Auth proof verification fails with wrong nonce."""
        system_key = generate_system_key()
        system_id = "test-system"
        auth_key = derive_auth_key(system_key, system_id)
        nonce = generate_nonce()
        timestamp = "2026-03-16T14:30:00.000Z"

        proof = compute_auth_proof(auth_key, nonce, system_id, timestamp)
        assert not verify_auth_proof(auth_key, "wrong-nonce", system_id, timestamp, proof)

    def test_canonical_json_sorted_keys(self):
        """Canonical JSON sorts keys."""
        obj1 = {"b": 2, "a": 1, "c": 3}
        obj2 = {"c": 3, "a": 1, "b": 2}
        assert canonical_json(obj1) == canonical_json(obj2)

    def test_canonical_json_no_whitespace(self):
        """Canonical JSON has no extra whitespace."""
        obj = {"key": "value", "num": 42}
        result = canonical_json(obj)
        assert b" " not in result
        assert b"\n" not in result

    def test_canonical_json_deterministic(self):
        """Canonical JSON is deterministic across calls."""
        obj = {"nested": {"b": 2, "a": 1}, "list": [3, 1, 2], "str": "hello"}
        assert canonical_json(obj) == canonical_json(obj)

    def test_sign_verify_message(self):
        """Message signing and verification round-trip."""
        key = hkdf_sha256(b"test-key", b"salt", b"info", 32)
        msg = {"type": "heartbeat", "ts": "2026-01-01T00:00:00Z", "seq": 1, "session": "tok", "payload": {}}
        sig = sign_message(key, msg)
        assert verify_message_signature(key, msg, sig)

    def test_sign_verify_message_with_sig_field(self):
        """verify_message_signature ignores the sig field in the message."""
        key = hkdf_sha256(b"test-key", b"salt", b"info", 32)
        msg = {"type": "heartbeat", "ts": "2026-01-01T00:00:00Z", "seq": 1, "session": "tok", "payload": {}}
        sig = sign_message(key, msg)
        msg["sig"] = sig
        assert verify_message_signature(key, msg, sig)

    def test_generate_system_key(self):
        """System key is 64 bytes."""
        key = generate_system_key()
        assert len(key) == 64
        assert isinstance(key, bytes)

    def test_generate_nonce_uniqueness(self):
        """Generated nonces are unique."""
        nonces = {generate_nonce() for _ in range(100)}
        assert len(nonces) == 100

    def test_hash_system_key(self):
        """System key hash is a hex string."""
        key = generate_system_key()
        h = hash_system_key(key)
        assert len(h) == 64  # SHA-256 hex = 64 chars
        assert all(c in "0123456789abcdef" for c in h)


# ===========================================================================
# Protocol Tests
# ===========================================================================


class TestProtocol:
    """Tests for server.cloud.protocol."""

    def test_protocol_version(self):
        assert PROTOCOL_VERSION == 1

    def test_message_type_sets_no_overlap(self):
        """Upstream, downstream, and handshake types don't overlap."""
        assert not (UPSTREAM_TYPES & DOWNSTREAM_TYPES)
        assert not (UPSTREAM_TYPES & HANDSHAKE_TYPES)
        assert not (DOWNSTREAM_TYPES & HANDSHAKE_TYPES)

    def test_build_hello(self):
        msg = build_hello(
            system_id="sys-1", version="1.0", hostname="test",
            project_name="Room", capabilities=["monitoring"],
            os_info="Linux", hardware="Pi", deployment_mode="appliance",
            python_version="3.11",
        )
        assert msg["type"] == HELLO
        assert "ts" in msg
        assert msg["payload"]["protocol_version"] == PROTOCOL_VERSION
        assert msg["payload"]["system_id"] == "sys-1"
        assert msg["payload"]["capabilities"] == ["monitoring"]
        # Handshake messages should NOT have seq/session/sig
        assert "seq" not in msg
        assert "session" not in msg
        assert "sig" not in msg

    def test_build_authenticate(self):
        msg = build_authenticate("sys-1", "2026-01-01T00:00:00Z", "proof-hex")
        assert msg["type"] == AUTHENTICATE
        assert msg["payload"]["proof"] == "proof-hex"
        assert "sig" not in msg

    def test_build_resume(self):
        msg = build_resume(100, 5, "2026-01-01T00:00:00Z")
        assert msg["type"] == RESUME
        assert msg["payload"]["last_ack_seq"] == 100
        assert msg["payload"]["buffered_count"] == 5

    def test_build_signed_message(self):
        key = hkdf_sha256(b"key", b"salt", b"info", 32)
        msg = build_signed_message(HEARTBEAT, {"cpu": 10}, 1, "session-tok", key)
        assert msg["type"] == HEARTBEAT
        assert msg["seq"] == 1
        assert msg["session"] == "session-tok"
        assert "sig" in msg
        # Verify the signature
        assert verify_message_signature(key, msg, msg["sig"])

    def test_build_heartbeat(self):
        key = hkdf_sha256(b"key", b"salt", b"info", 32)
        msg = build_heartbeat(
            seq=5, session_token="tok", signing_key=key,
            uptime_seconds=3600, cpu_percent=12.345, memory_percent=34.1,
            disk_percent=22.7, device_count=5, devices_connected=4,
            devices_error=1, active_ws_clients=2, temperature_celsius=52.3,
        )
        assert msg["type"] == HEARTBEAT
        assert msg["payload"]["cpu_percent"] == 12.3  # Rounded
        assert msg["payload"]["temperature_celsius"] == 52.3
        assert "sig" in msg

    def test_build_state_batch(self):
        key = hkdf_sha256(b"key", b"salt", b"info", 32)
        changes = [{"key": "device.proj.power", "value": "on", "ts": "..."}]
        msg = build_state_batch(1, "tok", key, changes)
        assert msg["type"] == STATE_BATCH
        assert msg["payload"]["changes"] == changes

    def test_build_pong(self):
        key = hkdf_sha256(b"key", b"salt", b"info", 32)
        msg = build_pong(10, "tok", key, "nonce-123")
        assert msg["type"] == PONG
        assert msg["payload"]["nonce"] == "nonce-123"

    def test_build_command_result(self):
        key = hkdf_sha256(b"key", b"salt", b"info", 32)
        msg = build_command_result(1, "tok", key, "req-1", True, result="OK")
        assert msg["type"] == COMMAND_RESULT
        assert msg["payload"]["success"] is True
        assert msg["payload"]["request_id"] == "req-1"

    def test_parse_message_valid(self):
        raw = json.dumps({"type": "heartbeat", "ts": "...", "payload": {}})
        msg = parse_message(raw)
        assert msg["type"] == "heartbeat"

    def test_parse_message_bytes(self):
        raw = json.dumps({"type": "test"}).encode("utf-8")
        msg = parse_message(raw)
        assert msg["type"] == "test"

    def test_parse_message_invalid_json(self):
        with pytest.raises(ProtocolError, match="Invalid JSON"):
            parse_message("not json")

    def test_parse_message_missing_type(self):
        with pytest.raises(ProtocolError, match="missing 'type'"):
            parse_message(json.dumps({"payload": {}}))

    def test_parse_message_not_object(self):
        with pytest.raises(ProtocolError, match="JSON object"):
            parse_message(json.dumps([1, 2, 3]))

    def test_is_handshake_message(self):
        assert is_handshake_message({"type": HELLO})
        assert is_handshake_message({"type": CHALLENGE})
        assert is_handshake_message({"type": SESSION_START})
        assert not is_handshake_message({"type": HEARTBEAT})
        assert not is_handshake_message({"type": COMMAND})

    def test_verify_steady_state_missing_fields(self):
        key = b"x" * 32
        with pytest.raises(ProtocolError, match="missing 'seq'"):
            verify_steady_state_message({"type": "heartbeat", "session": "t", "sig": "s"}, key)

    def test_verify_steady_state_valid(self):
        key = hkdf_sha256(b"key", b"salt", b"info", 32)
        msg = build_signed_message(HEARTBEAT, {}, 1, "tok", key)
        assert verify_steady_state_message(msg, key)

    def test_verify_steady_state_bad_sig(self):
        key = hkdf_sha256(b"key", b"salt", b"info", 32)
        msg = build_signed_message(HEARTBEAT, {}, 1, "tok", key)
        msg["sig"] = "bad-signature"
        assert not verify_steady_state_message(msg, key)

    def test_extract_payload(self):
        assert extract_payload({"payload": {"x": 1}}) == {"x": 1}
        assert extract_payload({"type": "test"}) == {}

    def test_message_priority_ordering(self):
        """State batches should have lower priority than alerts."""
        assert MESSAGE_PRIORITY[STATE_BATCH] < MESSAGE_PRIORITY[ALERT]
        assert MESSAGE_PRIORITY[ALERT] < MESSAGE_PRIORITY[COMMAND_RESULT]


# ===========================================================================
# Handshake Tests
# ===========================================================================


class TestHandshake:
    """Tests for server.cloud.handshake."""

    def _make_handshake(self):
        system_key = generate_system_key()
        system_id = "test-system-id"
        return Handshake(
            system_id=system_id,
            system_key=system_key,
            version="1.0.0",
            hostname="test-host",
            project_name="Test Room",
            capabilities=["monitoring"],
        ), system_key, system_id

    @pytest.mark.asyncio
    async def test_full_handshake(self):
        """Test the complete handshake flow with a mock server."""
        hs, system_key, system_id = self._make_handshake()
        auth_key = derive_auth_key(system_key, system_id)
        signing_salt = generate_nonce(32).encode("utf-8")  # raw bytes
        signing_salt_hex = signing_salt.hex()

        sent_messages = []
        recv_queue = asyncio.Queue()

        async def send(msg):
            sent_messages.append(msg)

        async def recv():
            return await recv_queue.get()

        # Mock server responses
        nonce = generate_nonce()
        challenge_msg = json.dumps({
            "type": CHALLENGE,
            "ts": "2026-01-01T00:00:00Z",
            "payload": {"nonce": nonce, "server_version": "1.0", "challenge_expires": "2026-12-31T00:00:00Z"},
        })

        session_start_msg = json.dumps({
            "type": SESSION_START,
            "ts": "2026-01-01T00:00:00Z",
            "payload": {
                "session_id": "session-123",
                "session_token": "token-abc",
                "signing_key_salt": signing_salt_hex,
                "session_expires": "2026-01-01T01:00:00Z",
                "enabled_capabilities": ["monitoring"],
                "config": {"heartbeat_interval": 60},
            },
        })

        await recv_queue.put(challenge_msg)
        await recv_queue.put(session_start_msg)

        result = await hs.perform(send, recv)

        # Verify hello was sent
        assert sent_messages[0]["type"] == HELLO
        assert sent_messages[0]["payload"]["system_id"] == system_id

        # Verify authenticate was sent
        assert sent_messages[1]["type"] == AUTHENTICATE
        auth_payload = sent_messages[1]["payload"]
        assert auth_payload["system_id"] == system_id
        # Verify the proof is valid
        assert verify_auth_proof(
            auth_key, nonce, system_id, auth_payload["timestamp"], auth_payload["proof"]
        )

        # Verify result
        assert isinstance(result, HandshakeResult)
        assert result.session_id == "session-123"
        assert result.session_token == "token-abc"
        assert result.enabled_capabilities == ["monitoring"]
        assert result.config["heartbeat_interval"] == 60
        assert len(result.signing_key) == 32

    async def _run_to_session_start(self, hs, session_payload):
        """Drive perform() through challenge -> session_start with the given payload."""
        recv_queue = asyncio.Queue()

        async def send(msg):
            pass

        await recv_queue.put(json.dumps({
            "type": CHALLENGE, "ts": "t", "payload": {"nonce": generate_nonce()},
        }))
        await recv_queue.put(json.dumps({
            "type": SESSION_START, "ts": "t", "payload": session_payload,
        }))
        return await hs.perform(send, recv_queue.get)

    @pytest.mark.asyncio
    async def test_handshake_rejects_incompatible_cloud_version(self):
        """A session_start advertising an unsupported cloud version fails closed."""
        hs, system_key, system_id = self._make_handshake()
        salt_hex = generate_nonce(32).encode("utf-8").hex()
        with pytest.raises(HandshakeError) as exc_info:
            await self._run_to_session_start(hs, {
                "protocol_version": PROTOCOL_VERSION + 1,
                "session_id": "s", "session_token": "tok",
                "signing_key_salt": salt_hex,
                "enabled_capabilities": [], "config": {},
            })
        assert exc_info.value.reason == "version_mismatch"

    @pytest.mark.asyncio
    async def test_handshake_accepts_matching_cloud_version(self):
        """A session_start advertising our version completes the handshake."""
        hs, system_key, system_id = self._make_handshake()
        salt_hex = generate_nonce(32).encode("utf-8").hex()
        result = await self._run_to_session_start(hs, {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": "s", "session_token": "tok",
            "signing_key_salt": salt_hex,
            "enabled_capabilities": [], "config": {},
        })
        assert result.session_id == "s"

    @pytest.mark.asyncio
    async def test_handshake_auth_failed(self):
        """Handshake raises HandshakeError on auth_failed."""
        hs, _, _ = self._make_handshake()

        recv_queue = asyncio.Queue()

        await recv_queue.put(json.dumps({
            "type": AUTH_FAILED,
            "ts": "...",
            "payload": {"reason": "unknown_system", "message": "System not found"},
        }))

        with pytest.raises(HandshakeError) as exc_info:
            await hs.perform(lambda m: asyncio.sleep(0), recv_queue.get)

        assert exc_info.value.reason == "unknown_system"

    @pytest.mark.asyncio
    async def test_handshake_version_mismatch(self):
        """Handshake raises HandshakeError on version_mismatch."""
        hs, _, _ = self._make_handshake()

        recv_queue = asyncio.Queue()
        await recv_queue.put(json.dumps({
            "type": VERSION_MISMATCH,
            "ts": "...",
            "payload": {"supported_versions": [2, 3], "message": "Update required"},
        }))

        with pytest.raises(HandshakeError) as exc_info:
            await hs.perform(lambda m: asyncio.sleep(0), recv_queue.get)

        assert exc_info.value.reason == "version_mismatch"

    @pytest.mark.asyncio
    async def test_handshake_timeout(self):
        """Handshake times out if server doesn't respond."""
        hs, _, _ = self._make_handshake()

        # recv that never returns
        async def slow_recv():
            await asyncio.sleep(999)
            return ""

        # Override timeout for test speed
        from server.cloud import handshake as hs_module
        orig_timeout = hs_module.HANDSHAKE_TIMEOUT
        hs_module.HANDSHAKE_TIMEOUT = 0.1

        try:
            with pytest.raises(HandshakeError) as exc_info:
                await hs.perform(lambda m: asyncio.sleep(0), slow_recv)
            assert exc_info.value.reason == "timeout"
        finally:
            hs_module.HANDSHAKE_TIMEOUT = orig_timeout

    @pytest.mark.asyncio
    async def test_resume_flow(self):
        """Test resume negotiation after reconnection."""
        hs, _, _ = self._make_handshake()

        sent = []
        recv_queue = asyncio.Queue()

        await recv_queue.put(json.dumps({
            "type": RESUME_FROM,
            "ts": "...",
            "payload": {"replay_from_seq": 50, "server_last_seen_seq": 100},
        }))

        replay_from = await hs.send_resume(
            send=lambda m: sent.append(m) or asyncio.sleep(0),
            recv=recv_queue.get,
            last_ack_seq=100,
            buffered_count=5,
            disconnected_at="2026-01-01T00:00:00Z",
        )

        assert replay_from == 50
        assert sent[0]["type"] == RESUME
        assert sent[0]["payload"]["last_ack_seq"] == 100


# ===========================================================================
# Session Tests
# ===========================================================================


class TestSession:
    """Tests for server.cloud.session."""

    def _make_session(self):
        system_key = generate_system_key()
        signing_key = derive_signing_key(system_key, b"salt", "session-1")
        return Session(
            session_id="session-1",
            session_token="token-abc",
            signing_key=signing_key,
            session_expires="2099-01-01T00:00:00Z",
            system_key=system_key,
        ), signing_key

    def test_sign_and_verify(self):
        """Session can sign and verify messages."""
        session, key = self._make_session()
        msg = {"type": "heartbeat", "ts": "...", "seq": 1, "payload": {}}
        signed = session.sign_outgoing(msg)
        assert "sig" in signed
        assert "session" in signed
        assert session.verify_incoming(signed)

    def test_verify_bad_sig_fails(self):
        """Verification fails with tampered signature."""
        session, _ = self._make_session()
        msg = {"type": "heartbeat", "ts": "...", "seq": 1, "payload": {}}
        signed = session.sign_outgoing(msg)
        signed["sig"] = "tampered"
        assert not session.verify_incoming(signed)

    def test_is_valid(self):
        session, _ = self._make_session()
        assert session.is_valid

    def test_is_valid_expired(self):
        """Expired session reports not valid."""
        system_key = generate_system_key()
        signing_key = derive_signing_key(system_key, b"salt", "s1")
        session = Session("s1", "tok", signing_key, "2020-01-01T00:00:00Z", system_key)
        assert not session.is_valid

    def test_invalidate(self):
        session, _ = self._make_session()
        assert session.is_valid
        session.invalidate()
        assert not session.is_valid

    def test_sign_after_invalidate_raises(self):
        session, _ = self._make_session()
        session.invalidate()
        with pytest.raises(SessionInvalid):
            session.sign_outgoing({"type": "test", "ts": "...", "seq": 1, "payload": {}})

    def test_session_rotate(self):
        """Session rotation updates token and signing key."""
        session, old_key = self._make_session()
        old_token = session.session_token

        new_salt = generate_nonce(32)
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "new-token-xyz",
                "new_signing_key_salt": new_salt,
                "new_session_expires": "2099-06-01T00:00:00Z",
                "switch_at_seq": 10,
            },
        }
        session.handle_session_rotate(rotate_msg)

        # Not rotated yet — switch_at_seq not reached
        assert session.session_token == old_token

        # Simulate reaching the rotation seq
        session.check_rotation(10)
        assert session.session_token == "new-token-xyz"
        assert session.signing_key != old_key

    def test_session_rotate_ignores_malformed_salt(self):
        """A malformed (non-hex) rotation salt is ignored — no uncaught
        exception, and no rotation is armed, so the agent stays on the working
        current key instead of diverging from the cloud."""
        session, _ = self._make_session()
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "new-token",
                "new_signing_key_salt": "not-hex-zz",
                "switch_at_seq": 5,
            },
        }
        session.handle_session_rotate(rotate_msg)  # must not raise
        assert session._pending_token is None  # nothing armed
        session.check_rotation(999)
        assert session.session_token == "token-abc"  # unchanged

    def test_session_rotate_ignores_missing_switch_at_seq(self):
        """A rotate without switch_at_seq is ignored, not silently armed as a
        rotation check_rotation can never apply (which would leave the agent on
        the old signing key while the cloud switches)."""
        session, _ = self._make_session()
        rotate_msg = {
            "type": SESSION_ROTATE,
            "payload": {
                "new_session_token": "new-token",
                "new_signing_key_salt": generate_nonce(32),
                # switch_at_seq intentionally omitted
            },
        }
        session.handle_session_rotate(rotate_msg)
        assert session._pending_token is None  # rotation not armed
        session.check_rotation(999)
        assert session.session_token == "token-abc"

    def test_is_valid_naive_expiry_fails_closed(self):
        """A timezone-naive expiry can't be compared to an aware now; treat the
        session as invalid rather than crashing the caller with a TypeError."""
        system_key = generate_system_key()
        signing_key = derive_signing_key(system_key, b"salt", "s1")
        session = Session("s1", "tok", signing_key, "2099-01-01T00:00:00", system_key)
        assert session.is_valid is False

    def test_is_valid_unparseable_expiry_fails_closed(self):
        """An unparseable expiry fails closed (was swallowed and treated valid)."""
        system_key = generate_system_key()
        signing_key = derive_signing_key(system_key, b"salt", "s1")
        session = Session("s1", "tok", signing_key, "not-a-timestamp", system_key)
        assert session.is_valid is False

    def test_handle_session_invalid(self):
        """session_invalid raises SessionInvalid."""
        session, _ = self._make_session()
        msg = {
            "type": SESSION_INVALID,
            "payload": {"reason": "duplicate_connection"},
        }
        with pytest.raises(SessionInvalid):
            session.handle_session_invalid(msg)
        assert not session.is_valid


# ===========================================================================
# Sequencer Tests
# ===========================================================================


class TestSequencer:
    """Tests for server.cloud.sequencer."""

    def test_assign_seq_increments(self):
        seq = Sequencer()
        msg1 = {"type": "heartbeat"}
        msg2 = {"type": "heartbeat"}
        s1 = seq.assign_seq(msg1)
        s2 = seq.assign_seq(msg2)
        assert s1 == 1
        assert s2 == 2
        assert msg1["seq"] == 1
        assert msg2["seq"] == 2

    def test_ack_removes_from_buffer(self):
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "heartbeat"})
        assert seq.buffer_count == 3

        seq.handle_ack({"payload": {"last_seq": 2}})
        assert seq.buffer_count == 1
        assert seq.last_ack_seq == 2

    def test_ack_idempotent(self):
        """Acking the same seq twice is a no-op."""
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.handle_ack({"payload": {"last_seq": 1}})
        seq.handle_ack({"payload": {"last_seq": 1}})
        assert seq.buffer_count == 0

    def test_validate_downstream_seq(self):
        seq = Sequencer()
        assert seq.validate_downstream_seq(1)
        assert seq.validate_downstream_seq(2)
        assert not seq.validate_downstream_seq(2)  # Duplicate
        assert not seq.validate_downstream_seq(1)  # Old

    def test_validate_downstream_seq_gap(self):
        """Gaps are accepted with a warning (server may skip)."""
        seq = Sequencer()
        assert seq.validate_downstream_seq(1)
        assert seq.validate_downstream_seq(5)  # Gap, but accepted
        assert seq.last_downstream_seq == 5

    def test_buffer_overflow_evicts_low_priority(self):
        """Buffer overflow evicts lowest-priority messages first."""
        seq = Sequencer(max_buffer_size=3)
        seq.assign_seq({"type": "alert"})         # priority 4
        seq.assign_seq({"type": "state_batch"})    # priority 0 (lowest)
        seq.assign_seq({"type": "command_result"})  # priority 5
        assert seq.buffer_count == 3

        # This should evict state_batch (priority 0)
        seq.assign_seq({"type": "heartbeat"})  # priority 2
        assert seq.buffer_count == 3  # Still 3 (overflow evicted one)

        # The state_batch (seq 2) should have been evicted
        remaining = seq.get_unacked_messages()
        types = [m["type"] for m in remaining]
        assert "state_batch" not in types

    def test_get_unacked_messages(self):
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "alert"})
        msgs = seq.get_unacked_messages()
        assert len(msgs) == 2
        assert msgs[0]["type"] == "heartbeat"
        assert msgs[1]["type"] == "alert"

    def test_get_replay_messages(self):
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})  # seq 1
        seq.assign_seq({"type": "alert"})       # seq 2
        seq.assign_seq({"type": "heartbeat"})  # seq 3

        msgs = seq.get_replay_messages(2)
        assert len(msgs) == 2
        assert msgs[0]["seq"] == 2
        assert msgs[1]["seq"] == 3

    def test_reset_for_new_session(self):
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        seq.assign_seq({"type": "heartbeat"})
        assert seq.next_seq == 3

        seq.reset_for_new_session()
        assert seq.next_seq == 1
        assert seq.last_downstream_seq == 0
        # Buffer preserved for replay after reconnect
        assert seq.buffer_count == 2

    def test_clear_buffer(self):
        seq = Sequencer()
        seq.assign_seq({"type": "heartbeat"})
        assert seq.buffer_count == 1
        seq.clear_buffer()
        assert seq.buffer_count == 0


# ===========================================================================
# Heartbeat Tests
# ===========================================================================


class TestHeartbeat:
    """Tests for server.cloud.heartbeat."""

    @pytest.mark.asyncio
    async def test_collect_basic_metrics(self):
        """HeartbeatCollector returns expected fields even without psutil."""
        from server.core.state_store import StateStore
        from server.core.device_manager import DeviceManager
        from server.core.event_bus import EventBus
        from server.cloud.heartbeat import HeartbeatCollector

        state = StateStore()
        events = EventBus()
        devices = DeviceManager(state, events)
        collector = HeartbeatCollector(state, devices, ws_client_count_fn=lambda: 3)

        metrics = await collector.collect()
        assert "uptime_seconds" in metrics
        assert "cpu_percent" in metrics
        assert "memory_percent" in metrics
        assert "disk_percent" in metrics
        assert "device_count" in metrics
        assert "devices_connected" in metrics
        assert "devices_error" in metrics
        assert metrics["active_ws_clients"] == 3
        assert metrics["uptime_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_devices_error_counts_offline_reason(self):
        """devices_error counts devices carrying an offline_reason fault code.

        The metric used to read ``device.<id>.error``, a state key nothing
        writes (device errors are events), so it always reported 0 to the
        cloud even with faulting hardware.
        """
        from server.core.state_store import StateStore
        from server.cloud.heartbeat import HeartbeatCollector

        class FakeDevices:
            def list_devices(self):
                return [{"id": "proj"}, {"id": "amp"}, {"id": "cam"}]

        state = StateStore()
        # proj is faulted, amp reconnected (reason cleared to None), cam clean.
        state.set("device.proj.offline_reason", "connection_refused")
        state.set("device.amp.offline_reason", None)

        collector = HeartbeatCollector(state, FakeDevices())
        metrics = await collector.collect()
        assert metrics["devices_error"] == 1
        assert metrics["device_count"] == 3

    @pytest.mark.asyncio
    async def test_first_collect_reports_primed_cpu(self):
        """Constructing the collector primes psutil's CPU sampling.

        ``psutil.cpu_percent(interval=0)`` returns a meaningless 0.0 on its
        first-ever call (it measures the delta since the previous call), so
        without a priming call in __init__ the first heartbeat under-reports
        a busy CPU.
        """
        from unittest.mock import patch

        from server.core.state_store import StateStore
        from server.cloud import heartbeat as hb

        if not hb.HAS_PSUTIL:
            pytest.skip("psutil not installed")

        class FakeDevices:
            def list_devices(self):
                return []

        calls = []

        def fake_cpu_percent(interval=None):
            calls.append(interval)
            return 12.5

        with patch.object(hb.psutil, "cpu_percent", side_effect=fake_cpu_percent):
            collector = hb.HeartbeatCollector(StateStore(), FakeDevices())
            assert len(calls) == 1, "__init__ must take the throwaway priming reading"
            metrics = await collector.collect()
        assert metrics["cpu_percent"] == 12.5


# ===========================================================================
# Command Handler Tests
# ===========================================================================


class TestCommandHandler:
    """Tests for server.cloud.command_handler."""

    @pytest.mark.asyncio
    async def test_handle_device_command(self):
        """Command handler delegates device commands to DeviceManager."""
        from server.core.state_store import StateStore
        from server.core.event_bus import EventBus
        from server.core.device_manager import DeviceManager
        from server.cloud.command_handler import CommandHandler

        state = StateStore()
        events = EventBus()
        devices = DeviceManager(state, events)

        # Track what the agent sends
        sent_messages = []

        class MockAgent:
            _config = {"heartbeat_interval": 30}
            _connected = True
            _session = None

            async def send_message(self, msg_type, payload):
                sent_messages.append((msg_type, payload))

        agent = MockAgent()
        handler = CommandHandler(agent, devices, events)

        msg = {
            "type": "command",
            "payload": {
                "request_id": "req-1",
                "device_id": "projector1",
                "command": "power_on",
                "params": {},
                "user_id": "user-1",
                "user_name": "tech@integrator.com",
            },
        }

        await handler.handle(msg)

        # Should have sent a command_result (device doesn't exist, so error)
        assert len(sent_messages) == 1
        assert sent_messages[0][0] == COMMAND_RESULT
        assert sent_messages[0][1]["request_id"] == "req-1"

    @pytest.mark.asyncio
    async def test_handle_restart(self):
        """Restart handler sends result before requesting restart."""
        from server.core.state_store import StateStore
        from server.core.event_bus import EventBus
        from server.core.device_manager import DeviceManager
        from server.cloud.command_handler import CommandHandler

        state = StateStore()
        events = EventBus()
        devices = DeviceManager(state, events)

        sent = []
        emitted = []

        class MockAgent:
            _config = {}
            async def send_message(self, msg_type, payload):
                sent.append((msg_type, payload))

        events.on("system.restart_requested", lambda e, p: emitted.append(p))

        handler = CommandHandler(MockAgent(), devices, events)

        msg = {
            "type": "restart",
            "payload": {
                "request_id": "req-2",
                "mode": "graceful",
                "user_id": "u1",
                "user_name": "admin",
            },
        }

        await handler.handle(msg)

        assert sent[0][1]["success"] is True
        assert len(emitted) == 1
        assert emitted[0]["mode"] == "graceful"

    @pytest.mark.asyncio
    async def test_config_push_migrates_old_schema(self, tmp_path):
        """A config_push carrying an older-schema project is migrated to the
        current format before it's validated and saved — not persisted with
        stale field placement to be re-migrated only on the next disk reload."""
        import json
        from server.core.state_store import StateStore
        from server.core.event_bus import EventBus
        from server.core.device_manager import DeviceManager
        from server.cloud.command_handler import CommandHandler

        state = StateStore()
        events = EventBus()
        devices = DeviceManager(state, events)

        sent = []

        class MockAgent:
            async def send_message(self, msg_type, payload):
                sent.append((msg_type, payload))

        # Pre-existing on-disk project uses a distinct id, so we can tell the
        # pushed project actually replaced it (i.e. the save happened).
        project_path = tmp_path / "project.avc"
        project_path.write_text(
            json.dumps({"openavc_version": "0.7.0", "project": {"id": "old", "name": "Old"}}),
            encoding="utf-8",
        )

        reloaded = []
        applied = []

        async def reload_fn():
            reloaded.append(True)

        # Mirror the seam contract: apply_project persists the pushed project
        # (the reconcile itself is pinned by the engine tests).
        async def apply_fn(project, **kwargs):
            from server.core.project_loader import save_project
            save_project(project_path, project)
            applied.append(kwargs)
            return 1

        handler = CommandHandler(
            MockAgent(), devices, events,
            reload_fn=reload_fn, apply_fn=apply_fn, project_path=str(project_path),
        )

        # Push a 0.6.0-schema project; the current schema is 0.7.0.
        old_schema = {"openavc_version": "0.6.0", "project": {"id": "pushed", "name": "Room"}}
        await handler._handle_config_push(
            {"project_json": old_schema, "mode": "full_replace"}, "req-cfg", "tech@x.com",
        )

        saved = json.loads(project_path.read_text(encoding="utf-8"))
        # Migration ran before the save: the persisted version is the current one.
        assert saved["openavc_version"] == "0.7.0", saved.get("openavc_version")
        # And the pushed project is what got saved (not the pre-existing one).
        assert saved["project"]["id"] == "pushed"
        # The push went through the seam — LOAD origin, no OCC check (a fleet
        # push wins by design, but the apply bumps + broadcasts so an open IDE
        # 409s instead of silently reverting it). No double reload-from-disk.
        from server.core.project_diff import ProjectOrigin
        assert applied == [{"origin": ProjectOrigin.LOAD, "persist": True}]
        assert reloaded == []
        assert sent and sent[-1][1]["success"] is True

    @pytest.mark.asyncio
    async def test_bare_config_push_reloads_from_disk(self, tmp_path):
        """A config_push with no project_json is a plain reload-from-disk."""
        import json
        from server.core.state_store import StateStore
        from server.core.event_bus import EventBus
        from server.core.device_manager import DeviceManager
        from server.cloud.command_handler import CommandHandler

        state = StateStore()
        events = EventBus()
        devices = DeviceManager(state, events)

        sent = []

        class MockAgent:
            async def send_message(self, msg_type, payload):
                sent.append((msg_type, payload))

        project_path = tmp_path / "project.avc"
        project_path.write_text(
            json.dumps({"openavc_version": "0.7.0", "project": {"id": "p", "name": "P"}}),
            encoding="utf-8",
        )

        reloaded = []
        applied = []

        async def reload_fn():
            reloaded.append(True)

        async def apply_fn(project, **kwargs):
            applied.append(kwargs)
            return 1

        handler = CommandHandler(
            MockAgent(), devices, events,
            reload_fn=reload_fn, apply_fn=apply_fn, project_path=str(project_path),
        )

        await handler._handle_config_push({"mode": "reload"}, "req-cfg2", "tech@x.com")

        assert reloaded == [True]
        assert applied == []
        assert sent and sent[-1][1]["success"] is True


# ===========================================================================
# State Relay Tests
# ===========================================================================


class TestStateRelay:
    """Tests for server.cloud.state_relay."""

    def test_on_state_change_batches(self):
        """State changes are collected into the top-tier batch."""
        from server.core.state_store import StateStore
        from server.cloud.state_relay import StateRelay

        state = StateStore()
        agent = type("MockAgent", (), {"_config": {}})()
        relay = StateRelay(agent, state)

        relay._on_state_change("device.proj.power", None, "on", "driver")
        relay._on_state_change("var.room_active", False, True, "ws")

        # Both keys are top-level (3-segment device key + var key) so they
        # land in the fast tier — child/low buckets stay empty.
        top = relay._batches["top"]
        assert len(top) == 2
        assert top[0]["key"] == "device.proj.power"
        assert top[0]["value"] == "on"
        assert top[1]["key"] == "var.room_active"
        assert top[1]["value"] is True
        assert relay._batches["child"] == []
        assert relay._batches["low"] == []

    def test_skips_cloud_internal_state(self):
        """Cloud-internal state keys are not relayed."""
        from server.core.state_store import StateStore
        from server.cloud.state_relay import StateRelay

        state = StateStore()
        agent = type("MockAgent", (), {"_config": {}})()
        relay = StateRelay(agent, state)

        relay._on_state_change("system.cloud.connected", None, True, "system")
        assert all(len(b) == 0 for b in relay._batches.values())

    def test_skips_isc_state(self):
        """ISC remote state is not relayed (prevents echo loops)."""
        from server.core.state_store import StateStore
        from server.cloud.state_relay import StateRelay

        state = StateStore()
        agent = type("MockAgent", (), {"_config": {}})()
        relay = StateRelay(agent, state)

        relay._on_state_change("isc.peer1.status", None, "online", "isc")
        assert all(len(b) == 0 for b in relay._batches.values())

    def test_format_ts(self):
        """Timestamp formatting produces ISO 8601 with Z suffix."""
        from server.cloud.state_relay import StateRelay

        ts = 1711724400.123  # Fixed epoch
        formatted = StateRelay._format_ts(ts)
        assert formatted.endswith("Z")
        assert "T" in formatted

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        """Start subscribes to state, stop unsubscribes."""
        from server.core.state_store import StateStore
        from server.core.event_bus import EventBus
        from server.cloud.state_relay import StateRelay

        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)

        sent = []

        class MockAgent:
            _config = {"state_batch_interval": 0.1, "state_batch_max_size": 500}
            async def send_message(self, msg_type, payload):
                sent.append((msg_type, payload))

        relay = StateRelay(MockAgent(), state)
        await relay.start()

        # Trigger a state change
        state.set("var.test", 42, source="test")

        # Wait for flush
        await asyncio.sleep(0.3)

        await relay.stop()

        # First message is the initial snapshot (empty, with snapshot flag),
        # followed by at least one incremental batch with the state change.
        assert len(sent) >= 2
        assert sent[0][0] == STATE_BATCH
        assert sent[0][1].get("snapshot") is True

        # Find the incremental batch containing our state change
        all_changes = []
        for msg_type, payload in sent[1:]:
            assert msg_type == STATE_BATCH
            all_changes.extend(payload.get("changes", []))
        assert any(c["key"] == "var.test" and c["value"] == 42 for c in all_changes)


# ===========================================================================
# Agent Throttle Cleanup Tests
# ===========================================================================


class TestAgentThrottle:
    """Tests for CloudAgent throttle handling."""

    @pytest.mark.asyncio
    async def test_unthrottle_removes_entry(self):
        """_unthrottle releases the event and removes all per-type tracking."""
        from server.cloud.agent import CloudAgent

        # Create a minimal agent instance without actually connecting
        agent = CloudAgent.__new__(CloudAgent)
        agent._throttles = {}
        agent._throttle_tasks = {}
        agent._throttle_deadlines = {}

        # Manually set up a throttle entry
        event = asyncio.Event()
        event.clear()
        agent._throttles["state_batch"] = event
        agent._throttle_deadlines["state_batch"] = 0.0

        # Run _unthrottle with a very short delay
        await agent._unthrottle("state_batch", 0.01)

        # Entry should be removed and the event released
        assert "state_batch" not in agent._throttles
        assert "state_batch" not in agent._throttle_deadlines
        assert event.is_set()


# ===========================================================================
# Capability Gating (A23) — Agent drops downstream messages whose required
# capability wasn't negotiated in session_start.enabled_capabilities. Spec
# §13.8: "If `tunnel` is not enabled, the agent does not listen for
# `tunnel_open` messages." Same intent for `diagnostic`.
# ===========================================================================


class TestAgentCapabilityGating:
    """Tests for CloudAgent._handle_message capability gating."""

    def _make_agent(self, enabled_capabilities: list[str]):
        """Build a minimal CloudAgent for dispatch testing."""
        from server.cloud.agent import CloudAgent

        agent = CloudAgent.__new__(CloudAgent)
        agent._session = None  # Skip signature verification path
        agent._enabled_capabilities = enabled_capabilities

        # Spy handlers — record calls without actually doing anything
        class Spy:
            def __init__(self):
                self.called = False

            async def handle_tunnel_open(self, msg):
                self.called = "tunnel_open"

            async def handle_tunnel_close(self, msg):
                self.called = "tunnel_close"

            async def handle(self, msg):
                # CommandHandler dispatch covers diagnostic alongside command/config_push
                self.called = msg.get("type")

        agent._tunnel_handler = Spy()
        agent._command_handler = Spy()
        agent._ai_tool_handler = None
        return agent

    @pytest.mark.asyncio
    async def test_tunnel_open_dropped_when_capability_missing(self):
        """tunnel_open is silently dropped if 'tunnel' isn't in enabled_capabilities."""
        agent = self._make_agent(enabled_capabilities=["monitoring"])
        await agent._handle_message({"type": "tunnel_open", "payload": {"tunnel_id": "t1"}})
        assert agent._tunnel_handler.called is False

    @pytest.mark.asyncio
    async def test_tunnel_open_dispatched_when_capability_enabled(self):
        """tunnel_open reaches the handler when 'tunnel' is enabled."""
        agent = self._make_agent(enabled_capabilities=["monitoring", "tunnel"])
        await agent._handle_message({"type": "tunnel_open", "payload": {"tunnel_id": "t1"}})
        assert agent._tunnel_handler.called == "tunnel_open"

    @pytest.mark.asyncio
    async def test_tunnel_close_dropped_when_capability_missing(self):
        """tunnel_close is dropped without 'tunnel'."""
        agent = self._make_agent(enabled_capabilities=[])
        await agent._handle_message({"type": "tunnel_close", "payload": {"tunnel_id": "t1"}})
        assert agent._tunnel_handler.called is False

    @pytest.mark.asyncio
    async def test_diagnostic_dropped_when_capability_missing(self):
        """diagnostic is dropped without 'diagnostics' even though it routes through command_handler."""
        agent = self._make_agent(enabled_capabilities=["monitoring", "remote_access"])
        await agent._handle_message({
            "type": "diagnostic",
            "payload": {"request_id": "r1", "action": "ping", "target": "1.2.3.4"},
        })
        assert agent._command_handler.called is False

    @pytest.mark.asyncio
    async def test_diagnostic_dispatched_when_capability_enabled(self):
        """diagnostic reaches command_handler when 'diagnostics' is enabled."""
        agent = self._make_agent(enabled_capabilities=["diagnostics"])
        await agent._handle_message({
            "type": "diagnostic",
            "payload": {"request_id": "r1", "action": "ping", "target": "1.2.3.4"},
        })
        assert agent._command_handler.called == "diagnostic"

    @pytest.mark.asyncio
    async def test_command_dropped_when_remote_access_missing(self):
        """H-020: command is gated on 'remote_access' — dropped when it's missing."""
        agent = self._make_agent(enabled_capabilities=["monitoring"])
        await agent._handle_message({
            "type": "command",
            "payload": {"request_id": "r1", "device_id": "d1", "command": "noop"},
        })
        assert agent._command_handler.called is False

    @pytest.mark.asyncio
    async def test_command_dispatched_when_remote_access_enabled(self):
        """H-020: command reaches the handler when 'remote_access' is enabled."""
        agent = self._make_agent(enabled_capabilities=["monitoring", "remote_access"])
        await agent._handle_message({
            "type": "command",
            "payload": {"request_id": "r1", "device_id": "d1", "command": "noop"},
        })
        assert agent._command_handler.called == "command"

    @pytest.mark.asyncio
    async def test_config_push_and_restart_gated_on_remote_access(self):
        """H-020: config_push and restart are also gated on 'remote_access'."""
        # Without remote_access: both dropped.
        agent = self._make_agent(enabled_capabilities=["monitoring"])
        await agent._handle_message({"type": "config_push", "payload": {"request_id": "r1"}})
        assert agent._command_handler.called is False
        await agent._handle_message({"type": "restart", "payload": {"request_id": "r2"}})
        assert agent._command_handler.called is False

        # With remote_access: both dispatched.
        agent = self._make_agent(enabled_capabilities=["remote_access"])
        await agent._handle_message({"type": "config_push", "payload": {"request_id": "r1"}})
        assert agent._command_handler.called == "config_push"
        await agent._handle_message({"type": "restart", "payload": {"request_id": "r2"}})
        assert agent._command_handler.called == "restart"

    def test_capability_map_vocabulary_matches_default(self):
        """The capability names in _CAPABILITY_GATED must come from DEFAULT_CAPABILITIES.

        Regression for A44: cloud and agent must agree on the capability vocabulary,
        otherwise capability gating silently blocks everything.
        """
        from server.cloud.agent import DEFAULT_CAPABILITIES, _CAPABILITY_GATED

        for required in _CAPABILITY_GATED.values():
            assert required in DEFAULT_CAPABILITIES, (
                f"Gated capability '{required}' not in DEFAULT_CAPABILITIES "
                f"({DEFAULT_CAPABILITIES}) — vocabulary drift"
            )

    def test_software_update_gated_on_fleet_update(self):
        """A59: software_update is gated on the 'fleet_update' capability so a
        cloud with features_disabled=['fleet_update'] can disable updates."""
        from server.cloud.agent import _CAPABILITY_GATED
        assert _CAPABILITY_GATED.get("software_update") == "fleet_update"

    @pytest.mark.asyncio
    async def test_software_update_dropped_when_fleet_update_missing(self):
        """A59: software_update with fleet_update missing from
        enabled_capabilities is silently dropped, even if the message
        otherwise looks valid."""
        agent = self._make_agent(enabled_capabilities=["monitoring", "tunnel"])
        await agent._handle_message({
            "type": "software_update",
            "payload": {"target_version": "1.2.3"},
        })
        assert agent._command_handler.called is False

    @pytest.mark.asyncio
    async def test_software_update_dispatched_when_fleet_update_enabled(self):
        """A59: software_update reaches the command handler when fleet_update
        is enabled."""
        agent = self._make_agent(
            enabled_capabilities=["monitoring", "fleet_update"]
        )
        await agent._handle_message({
            "type": "software_update",
            "payload": {"target_version": "1.2.3"},
        })
        assert agent._command_handler.called == "software_update"


# ===========================================================================
# A59 — Honor `features_disabled` from cloud's upgrade_required field.
# Without this, an outdated agent would accept feature messages the cloud
# expects it to reject. The cloud still sends the full enabled_capabilities
# list for back-compat, so the agent has to subtract features_disabled.
# ===========================================================================


class TestFeaturesDisabledSubtraction:
    """Tests for features_disabled handling in _connect_and_run."""

    def _build_handshake_result(
        self,
        enabled_capabilities: list[str],
        features_disabled: list[str] | None = None,
    ):
        """Build a HandshakeResult with optional upgrade_required."""
        from server.cloud.handshake import HandshakeResult
        upgrade = None
        if features_disabled is not None:
            upgrade = {
                "min_version": "1.2.0",
                "message": "Test upgrade required",
                "features_disabled": features_disabled,
            }
        return HandshakeResult(
            session_id="s1",
            session_token="tok",
            signing_key=b"k" * 32,
            session_expires="2099-01-01T00:00:00Z",
            enabled_capabilities=enabled_capabilities,
            config={},
            upgrade_required=upgrade,
        )

    def _apply_caps(self, agent, result):
        """Run just the bits of _connect_and_run that touch capabilities."""
        # Mirror the order from _connect_and_run: set caps first, then
        # subtract features_disabled if upgrade_required is present.
        agent._enabled_capabilities = result.enabled_capabilities
        if result.upgrade_required:
            features_disabled = result.upgrade_required.get("features_disabled") or []
            if features_disabled:
                agent._enabled_capabilities = [
                    c for c in agent._enabled_capabilities if c not in features_disabled
                ]

    def test_features_disabled_subtracts_from_enabled_capabilities(self):
        """A59: features in features_disabled are removed from the active list."""
        from server.cloud.agent import CloudAgent
        agent = CloudAgent.__new__(CloudAgent)
        agent._enabled_capabilities = []

        result = self._build_handshake_result(
            enabled_capabilities=["monitoring", "tunnel", "fleet_update", "diagnostics"],
            features_disabled=["fleet_update", "diagnostics"],
        )
        self._apply_caps(agent, result)

        assert "fleet_update" not in agent._enabled_capabilities
        assert "diagnostics" not in agent._enabled_capabilities
        # Unaffected capabilities remain
        assert "monitoring" in agent._enabled_capabilities
        assert "tunnel" in agent._enabled_capabilities

    def test_no_upgrade_required_keeps_all_capabilities(self):
        """When upgrade_required is absent, the full list is kept."""
        from server.cloud.agent import CloudAgent
        agent = CloudAgent.__new__(CloudAgent)
        agent._enabled_capabilities = []

        result = self._build_handshake_result(
            enabled_capabilities=["monitoring", "fleet_update"],
            features_disabled=None,
        )
        self._apply_caps(agent, result)

        assert agent._enabled_capabilities == ["monitoring", "fleet_update"]

    def test_empty_features_disabled_keeps_all(self):
        """An empty features_disabled list is a no-op."""
        from server.cloud.agent import CloudAgent
        agent = CloudAgent.__new__(CloudAgent)
        agent._enabled_capabilities = []

        result = self._build_handshake_result(
            enabled_capabilities=["monitoring", "fleet_update"],
            features_disabled=[],
        )
        self._apply_caps(agent, result)

        assert agent._enabled_capabilities == ["monitoring", "fleet_update"]


# ===========================================================================
# A21 — Diagnostic actions. The agent now implements all five spec §13.12
# actions instead of returning "not yet implemented".
# ===========================================================================


class TestDiagnosticActions:
    """Tests for the five diagnostic action implementations on the agent."""

    @pytest.mark.asyncio
    async def test_dns_lookup_resolves(self):
        """DNS lookup returns at least one address for a well-known host."""
        from server.cloud.command_handler import _diagnostic_dns_lookup
        result = await _diagnostic_dns_lookup("localhost", {"record_type": "A"})
        assert result["resolved"] is True
        assert result["host"] == "localhost"
        assert len(result["addresses"]) >= 1

    @pytest.mark.asyncio
    async def test_dns_lookup_fails_gracefully(self):
        """Unresolvable hostname returns resolved=False with the error string."""
        from server.cloud.command_handler import _diagnostic_dns_lookup
        result = await _diagnostic_dns_lookup(
            "definitely-not-a-real-host-12345.invalid", {"record_type": "A"},
        )
        assert result["resolved"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tcp_check_requires_port(self):
        """tcp_check returns an error if `port` is missing from params."""
        from server.cloud.command_handler import _diagnostic_tcp_check
        result = await _diagnostic_tcp_check("127.0.0.1", {})
        assert result["open"] is False
        assert "port required" in result["error"]

    @pytest.mark.asyncio
    async def test_tcp_check_to_closed_port(self):
        """tcp_check returns open=False for a port that's almost certainly closed locally."""
        from server.cloud.command_handler import _diagnostic_tcp_check
        result = await _diagnostic_tcp_check("127.0.0.1", {"port": 1, "timeout": 1})
        assert result["open"] is False

    @pytest.mark.asyncio
    async def test_tcp_check_to_open_port(self):
        """tcp_check returns open=True when an asyncio server is bound to the port."""
        import asyncio
        from server.cloud.command_handler import _diagnostic_tcp_check

        async def _handle(reader, writer):
            writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            result = await _diagnostic_tcp_check("127.0.0.1", {"port": port, "timeout": 2})
            assert result["open"] is True
            assert result["host"] == "127.0.0.1"
            assert result["port"] == port
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_tcp_check_rejects_port_zero(self):
        """A literal port 0 is an invalid request — clear error, not a probe of
        port 1 (which the old clamp produced)."""
        from server.cloud.command_handler import _diagnostic_tcp_check
        result = await _diagnostic_tcp_check("127.0.0.1", {"port": 0})
        assert result["open"] is False
        assert "invalid port" in result["error"]
        assert "host" not in result  # never reached the probe

    @pytest.mark.asyncio
    async def test_tcp_check_rejects_out_of_range_port(self):
        """A port above 65535 is rejected with a clear error, not clamped."""
        from server.cloud.command_handler import _diagnostic_tcp_check
        result = await _diagnostic_tcp_check("127.0.0.1", {"port": 70000})
        assert result["open"] is False
        assert "invalid port" in result["error"]

    def test_validate_exec_target_accepts_hostnames_and_ips(self):
        """Hostnames and IPs (v4/v6) are valid diagnostic targets."""
        from server.cloud.command_handler import _validate_exec_target
        for good in ("example.com", "device-1.local", "192.168.1.10", "2001:db8::1"):
            assert _validate_exec_target(good) is None, good

    def test_validate_exec_target_rejects_dash_and_junk(self):
        """A leading dash (option injection) and non-host characters are rejected."""
        from server.cloud.command_handler import _validate_exec_target
        assert _validate_exec_target("") is not None
        assert _validate_exec_target("-oProxyCommand=x") is not None
        assert _validate_exec_target("--flood") is not None
        assert _validate_exec_target("a b") is not None  # whitespace
        assert _validate_exec_target("$(rm -rf)") is not None

    @pytest.mark.asyncio
    async def test_ping_rejects_dash_target(self):
        """ping never spawns a subprocess for a dash-prefixed target."""
        from server.cloud.command_handler import _diagnostic_ping
        result = await _diagnostic_ping("-c1000", {})
        assert result["reachable"] is False
        assert "must not start with '-'" in result["error"]

    @pytest.mark.asyncio
    async def test_traceroute_rejects_dash_target(self):
        """traceroute never spawns a subprocess for a dash-prefixed target."""
        from server.cloud.command_handler import _diagnostic_traceroute
        result = await _diagnostic_traceroute("-x", {})
        assert "must not start with '-'" in result["error"]

    @pytest.mark.asyncio
    async def test_communicate_bounded_kills_hung_process(self):
        """A subprocess that never returns is killed and reaped, and the overall
        bound raises TimeoutError instead of hanging the diagnostic task."""
        import asyncio
        from server.cloud.command_handler import _communicate_bounded

        class _HungProc:
            def __init__(self):
                self.killed = False
                self.waited = False

            async def communicate(self):
                await asyncio.sleep(3600)  # never within the test bound

            def kill(self):
                self.killed = True

            async def wait(self):
                self.waited = True

        proc = _HungProc()
        with pytest.raises(asyncio.TimeoutError):
            await _communicate_bounded(proc, 0.05)
        assert proc.killed is True
        assert proc.waited is True

    @pytest.mark.asyncio
    async def test_port_scan_finds_open_port(self):
        """port_scan returns the open ports from the requested list."""
        import asyncio
        from server.cloud.command_handler import _diagnostic_port_scan

        async def _handle(reader, writer):
            writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            result = await _diagnostic_port_scan(
                "127.0.0.1", {"ports": [port, 1, 2, 3]},
            )
            assert port in result["open"]
            assert result["scanned"] == 4
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_handle_diagnostic_dispatches_action(self):
        """_handle_diagnostic in CommandHandler routes to the right action and
        sends a DIAGNOSTIC_RESULT (not COMMAND_RESULT) message back."""
        from server.cloud.command_handler import CommandHandler
        from server.cloud.protocol import DIAGNOSTIC_RESULT
        from server.core.state_store import StateStore
        from server.core.event_bus import EventBus
        from server.core.device_manager import DeviceManager

        state = StateStore()
        events = EventBus()
        devices = DeviceManager(state, events)

        sent = []

        class MockAgent:
            _config = {}
            async def send_message(self, msg_type, payload):
                sent.append((msg_type, payload))

        handler = CommandHandler(MockAgent(), devices, events)

        msg = {
            "type": "diagnostic",
            "payload": {
                "request_id": "diag-1",
                "action": "dns_lookup",
                "target": "localhost",
                "params": {"record_type": "A"},
                "user_id": "u",
                "user_name": "tester",
            },
        }
        await handler.handle(msg)

        # Must send DIAGNOSTIC_RESULT, not COMMAND_RESULT
        assert len(sent) == 1
        assert sent[0][0] == DIAGNOSTIC_RESULT
        assert sent[0][1]["request_id"] == "diag-1"
        assert sent[0][1]["success"] is True
        assert sent[0][1]["result"]["resolved"] is True

    @pytest.mark.asyncio
    async def test_handle_diagnostic_unknown_action(self):
        """Unknown action returns success=False with a descriptive error."""
        from server.cloud.command_handler import CommandHandler
        from server.core.state_store import StateStore
        from server.core.event_bus import EventBus
        from server.core.device_manager import DeviceManager

        state = StateStore()
        events = EventBus()
        devices = DeviceManager(state, events)

        sent = []

        class MockAgent:
            _config = {}
            async def send_message(self, msg_type, payload):
                sent.append((msg_type, payload))

        handler = CommandHandler(MockAgent(), devices, events)

        msg = {
            "type": "diagnostic",
            "payload": {
                "request_id": "diag-bad",
                "action": "snorgle",
                "target": "x",
                "user_id": "u",
                "user_name": "tester",
            },
        }
        await handler.handle(msg)
        assert sent[0][1]["success"] is False
        assert "snorgle" in sent[0][1]["error"]
