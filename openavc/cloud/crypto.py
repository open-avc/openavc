"""
OpenAVC Cloud — Cryptographic primitives for the cloud agent protocol.

All cryptography uses Python stdlib (hmac, hashlib, secrets). No external
crypto libraries required.

Key concepts:
- System key: 64-byte master secret, stored on the instance, never sent over the wire.
- Auth key: Derived from system key via HKDF. Used for challenge-response authentication.
- Signing key: Derived from system key + session salt via HKDF. Used for HMAC message signatures.
- Canonical JSON: Deterministic JSON serialization for signature computation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from typing import Any


# --- HKDF-SHA256 (RFC 5869) ---

def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    """HKDF-Extract: PRK = HMAC-Hash(salt, IKM)."""
    if not salt:
        salt = b"\x00" * 32  # HashLen zeros
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    """HKDF-Expand: OKM = T(1) || T(2) || ... truncated to length."""
    hash_len = 32  # SHA-256 output length
    n = math.ceil(length / hash_len)
    if n > 255:
        raise ValueError("HKDF-Expand: requested length too large")

    okm = b""
    t = b""
    for i in range(1, n + 1):
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t

    return okm[:length]


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """
    HKDF-SHA256 key derivation (RFC 5869).

    Args:
        ikm: Input keying material (e.g., system_key bytes).
        salt: Optional salt (e.g., system_id bytes). Can be empty.
        info: Context/application-specific info string.
        length: Output key length in bytes (default 32).

    Returns:
        Derived key of the requested length.
    """
    prk = _hkdf_extract(salt, ikm)
    return _hkdf_expand(prk, info, length)


# --- Key Derivation Functions ---

AUTH_KEY_INFO = b"openavc-cloud-auth-v1"
SIGNING_KEY_INFO_PREFIX = b"openavc-cloud-sign-v1"


def derive_auth_key(system_key: bytes, system_id: str) -> bytes:
    """
    Derive the authentication key from the system key.

    Used during the challenge-response handshake. The auth key is
    deterministic given the same system_key and system_id, so both
    sides can derive it independently.

    Args:
        system_key: The 64-byte master secret.
        system_id: The system's UUID string.

    Returns:
        32-byte auth key.
    """
    return hkdf_sha256(
        ikm=system_key,
        salt=system_id.encode("utf-8"),
        info=AUTH_KEY_INFO,
        length=32,
    )


def derive_signing_key(system_key: bytes, signing_key_salt: bytes, session_id: str) -> bytes:
    """
    Derive the session signing key.

    A new signing key is derived for each session using a server-provided
    salt. This ensures that even if one session's signing key is compromised,
    other sessions remain secure.

    Args:
        system_key: The 64-byte master secret.
        signing_key_salt: Random salt provided by the server in session_start.
        session_id: The session UUID string.

    Returns:
        32-byte signing key.
    """
    info = SIGNING_KEY_INFO_PREFIX + b":" + session_id.encode("utf-8")
    return hkdf_sha256(
        ikm=system_key,
        salt=signing_key_salt,
        info=info,
        length=32,
    )


# --- HMAC-SHA256 ---

def compute_hmac(key: bytes, message: bytes) -> str:
    """
    Compute HMAC-SHA256 and return as hex string.

    Args:
        key: The signing key.
        message: The message bytes to sign.

    Returns:
        Hex-encoded HMAC-SHA256 digest.
    """
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_hmac(key: bytes, message: bytes, expected_hex: str) -> bool:
    """
    Verify an HMAC-SHA256 signature using constant-time comparison.

    Args:
        key: The signing key.
        message: The message bytes that were signed.
        expected_hex: The expected hex-encoded HMAC to compare against.

    Returns:
        True if the signature is valid.
    """
    computed = hmac.new(key, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, expected_hex)


# --- Challenge-Response ---

def compute_auth_proof(auth_key: bytes, nonce: str, system_id: str, timestamp: str) -> str:
    """
    Compute the authentication proof for the challenge-response handshake.

    proof = HMAC-SHA256(auth_key, nonce || system_id || timestamp)

    The nonce comes from the server's challenge message. The system_id and
    timestamp come from the agent. All three are concatenated and signed.

    Args:
        auth_key: Derived auth key (from derive_auth_key).
        nonce: Base64-encoded nonce from the server's challenge.
        system_id: The system's UUID string.
        timestamp: ISO 8601 timestamp string.

    Returns:
        Hex-encoded proof string.
    """
    message = (nonce + system_id + timestamp).encode("utf-8")
    return compute_hmac(auth_key, message)


def verify_auth_proof(
    auth_key: bytes, nonce: str, system_id: str, timestamp: str, proof: str
) -> bool:
    """
    Verify a challenge-response proof (used on the server side).

    Args:
        auth_key: Derived auth key.
        nonce: The nonce that was sent in the challenge.
        system_id: The system_id from the authenticate message.
        timestamp: The timestamp from the authenticate message.
        proof: The proof string from the authenticate message.

    Returns:
        True if the proof is valid.
    """
    message = (nonce + system_id + timestamp).encode("utf-8")
    return verify_hmac(auth_key, message, proof)


# --- Canonical JSON ---

def _normalize_keys(obj: Any) -> Any:
    """Recursively convert dict keys to strings.

    json.dumps converts int keys to strings during serialization, but
    sort_keys=True sorts the *original* Python keys before conversion.
    Integer keys sort numerically (22, 80, 443) while string keys sort
    lexicographically ("22", "443", "80"), producing different canonical
    output. Normalizing to strings first ensures consistent ordering
    that matches post-JSON-round-trip data.
    """
    if isinstance(obj, dict):
        return {str(k): _normalize_keys(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize_keys(item) for item in obj]
    return obj


def canonical_json(obj: dict[str, Any]) -> bytes:
    """
    Serialize a dict to canonical JSON bytes for signature computation.

    Canonical form: sorted keys, no extra whitespace, ensure_ascii=False,
    UTF-8 encoded. Dict keys are normalized to strings first so that
    sort order is consistent regardless of key types.

    Args:
        obj: The message dict (without the 'sig' field).

    Returns:
        UTF-8 encoded canonical JSON bytes.
    """
    return json.dumps(
        _normalize_keys(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# --- Message Signing ---

def sign_message(signing_key: bytes, message: dict[str, Any]) -> str:
    """
    Sign a message dict and return the hex-encoded signature.

    The message must NOT contain a 'sig' field — it is computed from
    the canonical JSON of all other fields.

    Args:
        signing_key: The session signing key.
        message: The message dict (without 'sig').

    Returns:
        Hex-encoded HMAC-SHA256 signature.
    """
    msg_bytes = canonical_json(message)
    return compute_hmac(signing_key, msg_bytes)


def verify_message_signature(
    signing_key: bytes, message: dict[str, Any], signature: str
) -> bool:
    """
    Verify a message's signature.

    Extracts all fields except 'sig', computes canonical JSON,
    and verifies the HMAC.

    Args:
        signing_key: The session signing key.
        message: The full message dict (may or may not contain 'sig').
        signature: The signature to verify.

    Returns:
        True if the signature is valid.
    """
    # Build a copy without the sig field
    msg_without_sig = {k: v for k, v in message.items() if k != "sig"}
    msg_bytes = canonical_json(msg_without_sig)
    return verify_hmac(signing_key, msg_bytes, signature)


# --- Utilities ---

def generate_nonce(length: int = 32) -> str:
    """
    Generate a cryptographically random nonce as a hex string.

    Args:
        length: Number of random bytes (default 32, producing 64 hex chars).

    Returns:
        Hex-encoded random string.
    """
    return secrets.token_hex(length)


def generate_system_key() -> bytes:
    """
    Generate a new 64-byte system key.

    Returns:
        64 random bytes suitable for use as a system key.
    """
    return secrets.token_bytes(64)


def hash_system_key(system_key: bytes) -> str:
    """
    Hash a system key for storage (cloud side stores the hash, not the key).

    Returns:
        Hex-encoded SHA-256 hash of the system key.
    """
    return hashlib.sha256(system_key).hexdigest()
