"""
OpenAVC Cloud — Connection handshake manager.

Manages the multi-step challenge-response handshake:
1. Agent sends 'hello' with system info and capabilities
2. Server responds with 'challenge' containing a nonce
3. Agent computes HMAC proof and sends 'authenticate'
4. Server validates and responds with 'session_start'

On reconnection, after the handshake completes the agent also sends
a 'resume' message to negotiate replay of buffered messages.
"""

from __future__ import annotations

import asyncio
import platform
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from openavc.cloud.crypto import derive_auth_key, compute_auth_proof, derive_signing_key
from openavc.cloud.protocol import (
    CHALLENGE, SESSION_START,
    AUTH_FAILED, VERSION_MISMATCH, RESUME_FROM,
    SUPPORTED_PROTOCOL_VERSIONS,
    build_hello, build_authenticate, build_resume,
    parse_message, extract_payload, _now_iso,
)
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Handshake must complete within this many seconds
HANDSHAKE_TIMEOUT = 30


@dataclass
class HandshakeResult:
    """Result of a successful handshake."""
    session_id: str
    session_token: str
    signing_key: bytes
    session_expires: str
    enabled_capabilities: list[str]
    config: dict[str, Any]
    upgrade_required: dict[str, Any] | None = None


class HandshakeError(Exception):
    """Raised when the handshake fails."""

    def __init__(
        self,
        message: str,
        reason: str = "unknown",
        detail_versions: list[int] | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        # Only set for a version mismatch: the versions the other side said it
        # speaks, so the connection loop can name them in the operator-facing
        # detail rather than burying them in a log line.
        self.detail_versions = detail_versions


class Handshake:
    """
    Manages the cloud agent connection handshake.

    Usage:
        hs = Handshake(system_id, system_key, ...)
        result = await hs.perform(send_fn, recv_fn)
    """

    def __init__(
        self,
        system_id: str,
        system_key: bytes,
        version: str,
        capabilities: list[str],
    ):
        self.system_id = system_id
        self.system_key = system_key
        self.version = version
        self.capabilities = capabilities

        # Derive the auth key once (stable for a given system_key + system_id)
        self._auth_key = derive_auth_key(system_key, system_id)

    async def perform(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        recv: Callable[[], Awaitable[str]],
    ) -> HandshakeResult:
        """
        Perform the full handshake sequence.

        Args:
            send: Async function to send a message dict (serialized to JSON by caller).
            recv: Async function to receive a raw message string.

        Returns:
            HandshakeResult on success.

        Raises:
            HandshakeError: If the handshake fails (auth rejected, version mismatch, timeout).
            asyncio.TimeoutError: If the handshake takes longer than HANDSHAKE_TIMEOUT.
        """
        try:
            return await asyncio.wait_for(
                self._do_handshake(send, recv),
                timeout=HANDSHAKE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise HandshakeError(
                f"Handshake timed out after {HANDSHAKE_TIMEOUT}s",
                reason="timeout",
            )

    async def _do_handshake(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        recv: Callable[[], Awaitable[str]],
    ) -> HandshakeResult:
        """Internal handshake logic."""
        # Step 1: Send hello
        os_info = f"{platform.system()} {platform.release()} {platform.machine()}"
        hardware = platform.node()

        # Detect deployment type for cloud-managed updates
        try:
            from openavc.updater.platform import detect_deployment_type
            deployment_type = detect_deployment_type().value
        except Exception:
            deployment_type = "unknown"

        hello_msg = build_hello(
            system_id=self.system_id,
            version=self.version,
            capabilities=self.capabilities,
            os_info=os_info,
            hardware=hardware,
            deployment_mode=deployment_type,
        )
        log.debug("Handshake: sending hello")
        await send(hello_msg)

        # Step 2: Receive challenge
        raw = await recv()
        msg = parse_message(raw)
        msg_type = msg["type"]

        if msg_type == AUTH_FAILED:
            payload = extract_payload(msg)
            reason = payload.get("reason", "unknown")
            message = payload.get("message", "Authentication failed")
            raise HandshakeError(message, reason=reason)

        if msg_type == VERSION_MISMATCH:
            payload = extract_payload(msg)
            supported = payload.get("supported_versions", [])
            message = payload.get("message", "Protocol version not supported")
            # Retrying can't fix this — the cloud has dropped every version we
            # speak, so the instance needs a software update. Carry the cloud's
            # supported list into the error so the agent can put a real cause in
            # front of the operator instead of a bare "disconnected".
            log.error(
                f"Handshake: protocol version mismatch. Cloud supports {supported}, "
                f"this instance speaks {SUPPORTED_PROTOCOL_VERSIONS}. "
                "Update OpenAVC on this instance to reconnect."
            )
            raise HandshakeError(
                message, reason="version_mismatch", detail_versions=supported
            )

        if msg_type != CHALLENGE:
            raise HandshakeError(
                f"Expected 'challenge', got '{msg_type}'",
                reason="unexpected_message",
            )

        payload = extract_payload(msg)
        nonce = payload.get("nonce")
        if not nonce:
            raise HandshakeError("Challenge missing nonce", reason="bad_challenge")

        log.debug("Handshake: received challenge, computing proof")

        # Step 3: Compute proof and send authenticate
        timestamp = _now_iso()
        proof = compute_auth_proof(self._auth_key, nonce, self.system_id, timestamp)

        auth_msg = build_authenticate(self.system_id, timestamp, proof)
        await send(auth_msg)

        # Step 4: Receive session_start
        raw = await recv()
        msg = parse_message(raw)
        msg_type = msg["type"]

        if msg_type == AUTH_FAILED:
            payload = extract_payload(msg)
            reason = payload.get("reason", "unknown")
            message = payload.get("message", "Authentication failed")
            raise HandshakeError(message, reason=reason)

        if msg_type != SESSION_START:
            raise HandshakeError(
                f"Expected 'session_start', got '{msg_type}'",
                reason="unexpected_message",
            )

        payload = extract_payload(msg)

        # session_start carries the version the cloud settled on out of the set
        # we advertised in hello. Accept anything we actually speak, not just our
        # newest — that is what lets a cloud running ahead of the fleet drop back
        # to an older agent instead of rejecting it. Outside our range we fail
        # closed: a version we don't speak may reshape downstream payloads, and
        # mis-parsing them silently is worse than refusing the session. A cloud
        # that omits the field is the pre-negotiation v1 baseline, so treat
        # missing as compatible and keep the rollout safe in either order.
        cloud_version = payload.get("protocol_version")
        if cloud_version is not None and cloud_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise HandshakeError(
                f"Cloud settled on protocol version {cloud_version}, which this "
                f"instance does not speak (supports {SUPPORTED_PROTOCOL_VERSIONS})",
                reason="version_mismatch",
                detail_versions=[cloud_version],
            )

        # Extract session info
        session_id = payload.get("session_id")
        session_token = payload.get("session_token")
        signing_key_salt_hex = payload.get("signing_key_salt")
        session_expires = payload.get("session_expires", "")
        enabled_capabilities = payload.get("enabled_capabilities", [])
        config = payload.get("config", {})
        upgrade_required = payload.get("upgrade_required")

        if not all([session_id, session_token, signing_key_salt_hex]):
            raise HandshakeError(
                "session_start missing required fields",
                reason="bad_session_start",
            )

        # Derive session signing key
        try:
            signing_key_salt = bytes.fromhex(signing_key_salt_hex)
        except ValueError as e:
            raise HandshakeError(
                f"Invalid signing_key_salt hex: {e}",
                reason="bad_session_start",
            )
        signing_key = derive_signing_key(self.system_key, signing_key_salt, session_id)

        log.info(
            f"Handshake: session established (id={session_id[:8]}..., "
            f"capabilities={enabled_capabilities})"
        )

        return HandshakeResult(
            session_id=session_id,
            session_token=session_token,
            signing_key=signing_key,
            session_expires=session_expires,
            enabled_capabilities=enabled_capabilities,
            config=config,
            upgrade_required=upgrade_required,
        )

    async def send_resume(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        recv: Callable[[], Awaitable[str]],
        last_ack_seq: int,
        buffered_count: int,
        disconnected_at: str,
    ) -> int:
        """
        Send a resume message after re-handshake on reconnection.

        The three payload fields are diagnostics for the cloud's log line;
        the cloud always replies with replay_from_seq=1 (full replay of the
        unacked buffer — see build_resume).

        Args:
            send: Send function.
            recv: Receive function.
            last_ack_seq: The last sequence number acknowledged by the server before disconnect.
            buffered_count: Number of buffered messages to replay.
            disconnected_at: ISO timestamp of when the disconnection occurred.

        Returns:
            The sequence number to replay from (from the server's resume_from
            response; 1 in practice).

        Raises:
            HandshakeError: If the resume exchange fails.
        """
        resume_msg = build_resume(last_ack_seq, buffered_count, disconnected_at)
        log.debug(f"Handshake: sending resume (last_ack_seq={last_ack_seq}, buffered={buffered_count})")
        await send(resume_msg)

        try:
            raw = await asyncio.wait_for(recv(), timeout=HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            raise HandshakeError("Resume negotiation timed out", reason="timeout")

        msg = parse_message(raw)
        if msg["type"] != RESUME_FROM:
            raise HandshakeError(
                f"Expected 'resume_from', got '{msg['type']}'",
                reason="unexpected_message",
            )

        payload = extract_payload(msg)
        replay_from = payload.get("replay_from_seq", 0)
        log.info(f"Handshake: server says replay from seq {replay_from}")
        return replay_from
