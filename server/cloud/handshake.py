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
import sys
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from server.cloud.crypto import derive_auth_key, compute_auth_proof, derive_signing_key
from server.cloud.protocol import (
    CHALLENGE, SESSION_START,
    AUTH_FAILED, VERSION_MISMATCH, RESUME_FROM,
    build_hello, build_authenticate, build_resume,
    parse_message, extract_payload, _now_iso,
)
from server.utils.logger import get_logger

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

    def __init__(self, message: str, reason: str = "unknown"):
        super().__init__(message)
        self.reason = reason


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
        hostname: str,
        project_name: str,
        capabilities: list[str],
    ):
        self.system_id = system_id
        self.system_key = system_key
        self.version = version
        self.hostname = hostname
        self.project_name = project_name
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
        python_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        hello_msg = build_hello(
            system_id=self.system_id,
            version=self.version,
            hostname=self.hostname,
            project_name=self.project_name,
            capabilities=self.capabilities,
            os_info=os_info,
            hardware=hardware,
            deployment_mode="standalone",
            python_version=python_ver,
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
            log.error(f"Handshake: version mismatch. Server supports: {supported}")
            raise HandshakeError(message, reason="version_mismatch")

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

        Args:
            send: Send function.
            recv: Receive function.
            last_ack_seq: The last sequence number acknowledged by the server before disconnect.
            buffered_count: Number of buffered messages to replay.
            disconnected_at: ISO timestamp of when the disconnection occurred.

        Returns:
            The sequence number to replay from (from server's resume_from response).

        Raises:
            HandshakeError: If the resume negotiation fails.
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
