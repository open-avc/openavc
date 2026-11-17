"""The crypto vectors of the wire contract: inputs and the exact outputs both sides must produce.

The agent (``openavc/cloud/crypto.py``) and the cloud each implement key
derivation, canonical JSON, message signing, the handshake proof and the
signature on a system's HTTP requests. They stay byte-compatible only if both
produce these outputs. ``contract_gen`` copies this table into
``wire_contract.json`` under ``vectors``; openavc's vector test and the cloud's
(through its vendored copy) run every entry against their own code.

Every value here is written out, never computed: a vector computed by the code
it checks would follow that code wherever it went. If a vector fails, restore
the behaviour; never change the vector. Bytes are hex (``*_hex``); a request
body is its UTF-8 text.

Shared inputs: the system key is bytes 0..63, the signing-key salt bytes 0..31.
"""

from __future__ import annotations

from typing import Any

_SYSTEM_KEY_HEX = bytes(range(64)).hex()
_SIGNING_KEY_SALT_HEX = bytes(range(32)).hex()
_SYSTEM_ID = "0f7b1a9c-3d52-4e88-9a41-6c2f8e5d7b30"
_SESSION_ID = "b4d6f2a1-8c3e-45d9-b7a0-1e9f6c4d2a85"
_AUTH_KEY_HEX = "0d4bedcd397836e6279bbd875b330068d7bb48c5cc5948baf30b134802168be9"
_SIGNING_KEY_HEX = "c03dd3a52240ad77f38a3f1e53d8bb2a5fa76ee213f704b9c09d475d475411da"

# Unsorted keys, a map whose keys the sender holds as integers, non-ASCII
# text, and a bool, a float and a null.
_MESSAGE: dict[str, Any] = {
    "type": "state_batch",
    "seq": 42,
    "ts": "2026-01-02T03:04:05Z",
    "changes": [
        {"key": "device.proj1.power", "value": True},
        {"key": "var.room_name", "value": "Salle bleue — café"},
        {"key": "device.amp.volume", "value": -12.5},
        {"key": "device.amp.mute", "value": None},
    ],
    "ports": {"22": "ssh", "443": "https", "80": "http"},
}
_CANONICAL = (
    '{"changes":[{"key":"device.proj1.power","value":true},'
    '{"key":"var.room_name","value":"Salle bleue — café"},'
    '{"key":"device.amp.volume","value":-12.5},'
    '{"key":"device.amp.mute","value":null}],'
    '"ports":{"22":"ssh","443":"https","80":"http"},'
    '"seq":42,"ts":"2026-01-02T03:04:05Z","type":"state_batch"}'
)
_SIGNATURE = "5ac0e56f95c7da5aea0d86259accc39380dbb2a5489d49e4abd09c62418b642b"

VECTORS: dict[str, Any] = {
    "description": (
        "Inputs and the exact outputs both sides must produce. Bytes are hex (*_hex); a "
        "request body is its UTF-8 text. 'integer_keys' names the top-level maps whose "
        "keys the sender holds as integers: rebuilt that way, the message gives the same "
        "bytes. If a vector fails, restore the behaviour; never change the vector."
    ),
    "hkdf_sha256": [
        {
            "name": "RFC 5869 test case 1",
            "ikm_hex": "0b" * 22,
            "salt_hex": "000102030405060708090a0b0c",
            "info_hex": "f0f1f2f3f4f5f6f7f8f9",
            "length": 42,
            "okm_hex": (
                "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                "34007208d5b887185865"
            ),
        },
        {
            "name": "RFC 5869 test case 3: an empty salt and info",
            "ikm_hex": "0b" * 22,
            "salt_hex": "",
            "info_hex": "",
            "length": 42,
            "okm_hex": (
                "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d"
                "9d201395faa4b61a96c8"
            ),
        },
    ],
    "auth_key": [
        {
            "name": "the handshake's auth key",
            "system_key_hex": _SYSTEM_KEY_HEX,
            "system_id": _SYSTEM_ID,
            "auth_key_hex": _AUTH_KEY_HEX,
        },
    ],
    "signing_key": [
        {
            "name": "a session's signing key",
            "system_key_hex": _SYSTEM_KEY_HEX,
            "signing_key_salt_hex": _SIGNING_KEY_SALT_HEX,
            "session_id": _SESSION_ID,
            "signing_key_hex": _SIGNING_KEY_HEX,
        },
    ],
    "canonical_json": [
        {
            "name": "sorted keys, no spaces, UTF-8 text unescaped",
            "message": _MESSAGE,
            "integer_keys": ["ports"],
            "canonical": _CANONICAL,
        },
    ],
    "signing": [
        {
            "name": "a message",
            "signing_key_hex": _SIGNING_KEY_HEX,
            "message": _MESSAGE,
            "integer_keys": ["ports"],
            "sig": _SIGNATURE,
        },
        {
            "name": "a message that already carries a sig: the sig field is never signed",
            "signing_key_hex": _SIGNING_KEY_HEX,
            "message": {**_MESSAGE, "sig": "0" * 64},
            "integer_keys": [],
            "sig": _SIGNATURE,
        },
    ],
    "auth_proof": [
        {
            "name": "the handshake's proof",
            "auth_key_hex": _AUTH_KEY_HEX,
            "nonce": "9c1f4e2d7a6b3850",
            "system_id": _SYSTEM_ID,
            "timestamp": "2026-01-02T03:04:05.678901Z",
            "proof": "f23d9b4b95251d8a1ffac78e131521c26b57cb4f6f5e426b86186ae331a34207",
        },
    ],
    "http_system_signature": [
        {
            "name": "a request with no body",
            "system_key_hex": _SYSTEM_KEY_HEX,
            "system_id": _SYSTEM_ID,
            "timestamp": "2026-01-02T03:04:05.678901+00:00",
            "body": "",
            "signature": "cb72eff874ce1180ada004d24c14b11b634ea45be6fea3c90bbd97b0f5b26329",
        },
        {
            "name": "a JSON body with non-ASCII text",
            "system_key_hex": _SYSTEM_KEY_HEX,
            "system_id": _SYSTEM_ID,
            "timestamp": "2026-01-02T03:04:05.678901+00:00",
            "body": (
                '{"message":"Allume le projecteur de la salle bleue — merci",'
                '"conversation_id":null}'
            ),
            "signature": "9a501816a197bba51bfcdeb48ad884855829ec0263b61f66fc8664b440ffc402",
        },
    ],
}
