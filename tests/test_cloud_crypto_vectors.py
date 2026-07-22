"""Known-answer vectors for the cloud agent's cryptographic primitives.

These pin the exact wire-level output of key derivation, canonical JSON,
message signing, and the handshake proof. The cloud service derives the
same values independently, so ANY change to the output of these functions
breaks every fielded agent's connection at the signature layer.

If one of these tests fails, do not update the expected constants —
restore the behavior. The constants are the contract.
"""

from server.cloud.crypto import (
    canonical_json,
    compute_auth_proof,
    derive_auth_key,
    derive_signing_key,
    hkdf_sha256,
    sign_message,
    verify_message_signature,
)

# Fixed, arbitrary inputs shared by the vectors below.
SYSTEM_KEY = bytes(range(64))
SYSTEM_ID = "0f7b1a9c-3d52-4e88-9a41-6c2f8e5d7b30"
SESSION_ID = "b4d6f2a1-8c3e-45d9-b7a0-1e9f6c4d2a85"
SIGNING_KEY_SALT = bytes(range(32))

# A message exercising the canonicalization edge cases: unsorted keys,
# integer dict keys (normalized to strings, sorted lexicographically),
# non-ASCII text (ensure_ascii=False), bool / float / null values.
MESSAGE = {
    "type": "state_batch",
    "seq": 42,
    "ts": "2026-01-02T03:04:05Z",
    "changes": [
        {"key": "device.proj1.power", "value": True},
        {"key": "var.room_name", "value": "Salle bleue — café"},
        {"key": "device.amp.volume", "value": -12.5},
        {"key": "device.amp.mute", "value": None},
    ],
    "ports": {22: "ssh", 443: "https", 80: "http"},
}

EXPECTED_AUTH_KEY_HEX = "0d4bedcd397836e6279bbd875b330068d7bb48c5cc5948baf30b134802168be9"
EXPECTED_SIGNING_KEY_HEX = "c03dd3a52240ad77f38a3f1e53d8bb2a5fa76ee213f704b9c09d475d475411da"
EXPECTED_CANONICAL = (
    '{"changes":[{"key":"device.proj1.power","value":true},'
    '{"key":"var.room_name","value":"Salle bleue — café"},'
    '{"key":"device.amp.volume","value":-12.5},'
    '{"key":"device.amp.mute","value":null}],'
    '"ports":{"22":"ssh","443":"https","80":"http"},'
    '"seq":42,"ts":"2026-01-02T03:04:05Z","type":"state_batch"}'
).encode("utf-8")
EXPECTED_SIGNATURE = "5ac0e56f95c7da5aea0d86259accc39380dbb2a5489d49e4abd09c62418b642b"

AUTH_NONCE = "9c1f4e2d7a6b3850"
AUTH_TIMESTAMP = "2026-01-02T03:04:05.678901Z"
EXPECTED_AUTH_PROOF = "f23d9b4b95251d8a1ffac78e131521c26b57cb4f6f5e426b86186ae331a34207"


class TestHKDFRFC5869:
    """HKDF-SHA256 pinned to the published RFC 5869 test vectors."""

    def test_case_1_basic(self):
        okm = hkdf_sha256(
            ikm=bytes.fromhex("0b" * 22),
            salt=bytes.fromhex("000102030405060708090a0b0c"),
            info=bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"),
            length=42,
        )
        assert okm.hex() == (
            "3cb25f25faacd57a90434f64d0362f2a"
            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"
        )

    def test_case_3_empty_salt_and_info(self):
        # Exercises the empty-salt zero-fill branch of HKDF-Extract.
        okm = hkdf_sha256(
            ikm=bytes.fromhex("0b" * 22),
            salt=b"",
            info=b"",
            length=42,
        )
        assert okm.hex() == (
            "8da4e775a563c18f715f802a063c5a31"
            "b8a11f5c5ee1879ec3454e5f3c738d2d"
            "9d201395faa4b61a96c8"
        )


class TestKeyDerivationVectors:
    def test_auth_key(self):
        key = derive_auth_key(SYSTEM_KEY, SYSTEM_ID)
        assert key.hex() == EXPECTED_AUTH_KEY_HEX

    def test_signing_key(self):
        key = derive_signing_key(SYSTEM_KEY, SIGNING_KEY_SALT, SESSION_ID)
        assert key.hex() == EXPECTED_SIGNING_KEY_HEX


class TestCanonicalJsonVector:
    def test_exact_bytes(self):
        assert canonical_json(MESSAGE) == EXPECTED_CANONICAL


class TestSignatureVectors:
    def test_sign_message(self):
        signing_key = bytes.fromhex(EXPECTED_SIGNING_KEY_HEX)
        assert sign_message(signing_key, MESSAGE) == EXPECTED_SIGNATURE

    def test_verify_with_sig_field_present(self):
        # Verification must ignore an embedded 'sig' field when canonicalizing.
        signing_key = bytes.fromhex(EXPECTED_SIGNING_KEY_HEX)
        signed = {**MESSAGE, "sig": EXPECTED_SIGNATURE}
        assert verify_message_signature(signing_key, signed, EXPECTED_SIGNATURE)

    def test_auth_proof(self):
        auth_key = bytes.fromhex(EXPECTED_AUTH_KEY_HEX)
        proof = compute_auth_proof(auth_key, AUTH_NONCE, SYSTEM_ID, AUTH_TIMESTAMP)
        assert proof == EXPECTED_AUTH_PROOF
