"""
OpenAVC Cloud — Session management for the cloud agent.

Manages the active session after handshake completion: tracks the session
token, signing key, and expiry. Handles session rotation (new token + key)
and session invalidation (requires reconnection).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from server.cloud.crypto import derive_signing_key, sign_message, verify_message_signature
from server.cloud.protocol import (
    extract_payload,
)
from server.utils.logger import get_logger

log = get_logger(__name__)


class SessionExpired(Exception):
    """Raised when the session token has expired."""
    pass


class SessionInvalid(Exception):
    """Raised when the server invalidates the session."""

    def __init__(self, reason: str = "unknown"):
        super().__init__(f"Session invalidated: {reason}")
        self.reason = reason


class Session:
    """
    Manages an active cloud agent session.

    After the handshake completes, a Session is created with the session_id,
    token, signing key, and expiry. All outgoing messages are signed through
    this session, and all incoming messages are verified.
    """

    def __init__(
        self,
        session_id: str,
        session_token: str,
        signing_key: bytes,
        session_expires: str,
        system_key: bytes,
    ):
        self.session_id = session_id
        self.session_token = session_token
        self.signing_key = signing_key
        self.session_expires = session_expires
        self._system_key = system_key

        # Pending rotation (set when session_rotate is received)
        self._pending_token: str | None = None
        self._pending_signing_key: bytes | None = None
        self._pending_expires: str | None = None
        self._pending_session_id: str | None = None
        self._rotate_at_downstream_seq: int | None = None

        self._valid = True

    @property
    def is_valid(self) -> bool:
        """Check if the session is still valid (not invalidated or expired)."""
        if not self._valid:
            return False
        if self.session_expires:
            try:
                expires_dt = datetime.fromisoformat(
                    self.session_expires.replace("Z", "+00:00")
                )
                if datetime.now(timezone.utc) >= expires_dt:
                    return False
            except ValueError:
                pass  # Can't parse expiry — assume still valid
        return True

    def sign_outgoing(self, msg: dict[str, Any]) -> dict[str, Any]:
        """
        Sign an outgoing message by adding the 'sig' field.

        The message must already have type, ts, seq, session, and payload.
        This method adds the session token and computes the signature.

        Args:
            msg: Message dict without 'sig'.

        Returns:
            Message dict with 'sig' added.
        """
        if not self._valid:
            raise SessionInvalid("Cannot sign — session is invalidated")
        msg["session"] = self.session_token
        msg_without_sig = {k: v for k, v in msg.items() if k != "sig"}
        msg["sig"] = sign_message(self.signing_key, msg_without_sig)
        return msg

    def verify_incoming(self, msg: dict[str, Any]) -> bool:
        """
        Verify the signature on an incoming steady-state message.

        Args:
            msg: Parsed message dict with 'sig' field.

        Returns:
            True if signature is valid.
        """
        sig = msg.get("sig")
        if not sig:
            return False
        return verify_message_signature(self.signing_key, msg, sig)

    def handle_session_rotate(self, msg: dict[str, Any]) -> None:
        """
        Process a session_rotate message from the server.

        The new token and signing key take effect at the downstream
        sequence number specified in 'switch_at_seq'.

        Args:
            msg: The session_rotate message.
        """
        payload = extract_payload(msg)
        new_token = payload.get("new_session_token")
        new_salt_hex = payload.get("new_signing_key_salt")
        new_expires = payload.get("new_session_expires", "")
        switch_at_seq = payload.get("switch_at_seq")

        if not all([new_token, new_salt_hex]):
            log.warning("session_rotate missing required fields, ignoring")
            return

        new_salt = bytes.fromhex(new_salt_hex)
        # Use new session_id for key derivation if provided, otherwise keep current
        new_session_id = payload.get("new_session_id", self.session_id)
        new_signing_key = derive_signing_key(
            self._system_key, new_salt, new_session_id
        )

        self._pending_token = new_token
        self._pending_signing_key = new_signing_key
        self._pending_expires = new_expires
        self._pending_session_id = new_session_id
        self._rotate_at_downstream_seq = switch_at_seq

        log.info(
            f"Session rotation pending — will switch at downstream seq {switch_at_seq}"
        )

    def check_rotation(self, downstream_seq: int) -> None:
        """
        Check if it's time to apply a pending session rotation.

        Called after processing each downstream message. If the downstream
        sequence number has reached the rotation point, swap to the new
        token and signing key.

        Args:
            downstream_seq: The sequence number of the just-processed message.
        """
        if (
            self._rotate_at_downstream_seq is not None
            and downstream_seq >= self._rotate_at_downstream_seq
            and self._pending_token is not None
            and self._pending_signing_key is not None
        ):
            old_token_prefix = self.session_token[:8] if self.session_token else "?"
            self.session_token = self._pending_token
            self.signing_key = self._pending_signing_key
            if self._pending_expires:
                self.session_expires = self._pending_expires
            if self._pending_session_id:
                self.session_id = self._pending_session_id

            # Clear pending
            self._pending_token = None
            self._pending_signing_key = None
            self._pending_expires = None
            self._pending_session_id = None
            self._rotate_at_downstream_seq = None

            log.info(
                f"Session rotated — token {old_token_prefix}... → "
                f"{self.session_token[:8]}..."
            )

    def handle_session_invalid(self, msg: dict[str, Any]) -> None:
        """
        Process a session_invalid message from the server.

        Marks the session as invalid. The agent must close the connection
        and perform a fresh handshake.

        Args:
            msg: The session_invalid message.
        """
        payload = extract_payload(msg)
        reason = payload.get("reason", "unknown")
        log.warning(f"Session invalidated by server: {reason}")
        self._valid = False
        raise SessionInvalid(reason)

    def invalidate(self) -> None:
        """Mark the session as invalid (e.g., on disconnect)."""
        self._valid = False
