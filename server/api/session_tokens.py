"""
In-memory session tokens for the Programmer SPA.

The browser exchanges the admin password for a short-lived random token at
login (POST /api/auth/session) and stores only the token, so a same-origin
XSS can at worst steal a revocable session — never the password itself.

Design properties:

- Tokens are 256-bit `secrets.token_urlsafe` values. The store keeps only
  their SHA-256 hashes, so a memory dump or stray log line can't yield a
  usable credential.
- Expiry is sliding: each successful validation pushes the expiry out by the
  TTL, so an active Programmer session never bounces mid-work while an idle
  tab eventually re-authenticates.
- Every token records a fingerprint of the credential it was minted under
  (see `auth.credential_fingerprint`). Validation recomputes the current
  fingerprint and rejects on mismatch, so a password (or username) change
  invalidates every outstanding session no matter which code path changed
  it — Settings save, cloud config push, anything.
- The table is process-memory only: a server restart invalidates all
  sessions by construction. Nothing is persisted.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from server.utils.logger import get_logger

log = get_logger(__name__)

SESSION_TTL_SECONDS = 12 * 3600

# Enough for many tabs across several browsers/machines; bounds memory
# against a mint flood (the endpoint is also strict-rate-limited).
MAX_SESSIONS = 64


class _Session:
    __slots__ = ("expires_at", "fingerprint")

    def __init__(self, expires_at: float, fingerprint: str) -> None:
        self.expires_at = expires_at
        self.fingerprint = fingerprint


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionTokenStore:
    """Hashed in-memory token table. Not thread-safe by design — all access
    happens on the event loop thread (FastAPI dependencies and the WS
    handshake), same as the rest of the auth module."""

    def __init__(self, ttl: float = SESSION_TTL_SECONDS, max_sessions: int = MAX_SESSIONS) -> None:
        self._ttl = ttl
        self._max = max_sessions
        self._sessions: dict[str, _Session] = {}

    def issue(self, fingerprint: str) -> tuple[str, int]:
        """Mint a new token bound to the current credential fingerprint.

        Returns (token, expires_in_seconds). The raw token is returned to the
        caller exactly once and never stored.
        """
        self._prune()
        if len(self._sessions) >= self._max:
            # Evict the session closest to expiry — the least-recently-used
            # one under sliding expiry.
            oldest = min(self._sessions, key=lambda k: self._sessions[k].expires_at)
            del self._sessions[oldest]
            log.warning(
                "Session table full (%d); evicted the session closest to expiry",
                self._max,
            )
        token = secrets.token_urlsafe(32)
        self._sessions[_hash(token)] = _Session(time.time() + self._ttl, fingerprint)
        return token, int(self._ttl)

    def validate(self, token: str, fingerprint: str) -> bool:
        """True if the token exists, hasn't expired, and was minted under the
        current credential. Sliding expiry: success extends the session."""
        if not token:
            return False
        session = self._sessions.get(_hash(token))
        if session is None:
            return False
        now = time.time()
        if session.expires_at < now:
            del self._sessions[_hash(token)]
            return False
        if not secrets.compare_digest(session.fingerprint, fingerprint):
            # Credential changed since mint — the session is dead. Drop it so
            # the table doesn't fill with unusable entries.
            del self._sessions[_hash(token)]
            return False
        session.expires_at = now + self._ttl
        return True

    def revoke(self, token: str) -> bool:
        """Delete a session (logout). Returns True if it existed."""
        return self._sessions.pop(_hash(token), None) is not None

    def clear(self) -> None:
        """Drop all sessions (used by tests; a restart does this naturally)."""
        self._sessions.clear()

    def _prune(self) -> None:
        now = time.time()
        expired = [k for k, s in self._sessions.items() if s.expires_at < now]
        for k in expired:
            del self._sessions[k]


# Process-wide store — one table per server process.
store = SessionTokenStore()
