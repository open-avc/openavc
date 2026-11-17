"""The agent's crypto against the wire contract's vectors.

Key derivation, canonical JSON, message signing, the handshake proof and the
signature on a system's HTTP requests must give exactly the outputs in
``wire_contract.json``'s ``vectors``: the cloud runs the same vectors from its
copy of that file, so any change here breaks every fielded agent's connection
at the signature layer.

If one of these fails, restore the behaviour; never change the vector
(``openavc/cloud/wire_vectors.py``).
"""

from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from openavc.cloud import contract_gen, wire_vectors
from openavc.cloud.crypto import (
    canonical_json,
    compute_auth_proof,
    derive_auth_key,
    derive_signing_key,
    hkdf_sha256,
    sign_message,
    sign_system_request,
    verify_message_signature,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VECTORS: dict[str, Any] = json.loads(
    contract_gen.artifact_path(REPO_ROOT).read_text(encoding="utf-8")
)["vectors"]


def _cases(kind: str) -> list[Any]:
    return [pytest.param(v, id=v["name"]) for v in VECTORS[kind]]


def _as_sent(vector: dict[str, Any]) -> dict[str, Any]:
    """The message as the sender holds it: the maps named in ``integer_keys``
    keyed by integers, which JSON cannot carry."""
    message = dict(vector["message"])
    for key in vector["integer_keys"]:
        message[key] = {int(k): v for k, v in message[key].items()}
    return message


def test_every_kind_has_a_vector():
    kinds = set(VECTORS) - {"description"}
    assert kinds == {"hkdf_sha256", "auth_key", "signing_key", "canonical_json",
                     "signing", "auth_proof", "http_system_signature"}
    assert all(VECTORS[kind] for kind in kinds)


@pytest.mark.parametrize("v", _cases("hkdf_sha256"))
def test_hkdf_sha256(v):
    okm = hkdf_sha256(bytes.fromhex(v["ikm_hex"]), bytes.fromhex(v["salt_hex"]),
                      bytes.fromhex(v["info_hex"]), v["length"])
    assert okm.hex() == v["okm_hex"]


@pytest.mark.parametrize("v", _cases("auth_key"))
def test_auth_key(v):
    assert derive_auth_key(bytes.fromhex(v["system_key_hex"]), v["system_id"]).hex() == v["auth_key_hex"]


@pytest.mark.parametrize("v", _cases("signing_key"))
def test_signing_key(v):
    key = derive_signing_key(bytes.fromhex(v["system_key_hex"]),
                             bytes.fromhex(v["signing_key_salt_hex"]), v["session_id"])
    assert key.hex() == v["signing_key_hex"]


@pytest.mark.parametrize("v", _cases("canonical_json"))
def test_canonical_json(v):
    expected = v["canonical"].encode("utf-8")
    assert canonical_json(v["message"]) == expected
    assert canonical_json(_as_sent(v)) == expected


@pytest.mark.parametrize("v", _cases("signing"))
def test_signing(v):
    key = bytes.fromhex(v["signing_key_hex"])
    for message in (v["message"], _as_sent(v)):
        assert sign_message(key, message) == v["sig"]
        assert verify_message_signature(key, message, v["sig"])


@pytest.mark.parametrize("v", _cases("auth_proof"))
def test_auth_proof(v):
    proof = compute_auth_proof(bytes.fromhex(v["auth_key_hex"]), v["nonce"],
                               v["system_id"], v["timestamp"])
    assert proof == v["proof"]


@pytest.mark.parametrize("v", _cases("http_system_signature"))
def test_http_system_signature(v):
    signature = sign_system_request(bytes.fromhex(v["system_key_hex"]), v["system_id"],
                                    v["timestamp"], v["body"].encode("utf-8"))
    assert signature == v["signature"]


@pytest.mark.parametrize("v", _cases("http_system_signature"))
def test_a_signed_request_carries_the_vector_signature(v, monkeypatch):
    """The headers every signed request sends are the vector's signature,
    stamped with the timestamp the request went out at."""
    from openavc.api import ai_proxy

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(v["timestamp"])

    monkeypatch.setattr(ai_proxy, "datetime", _Frozen)
    headers = ai_proxy._sign_request(v["system_id"], bytes.fromhex(v["system_key_hex"]),
                                     v["body"].encode("utf-8"))
    assert headers == {"X-System-ID": v["system_id"], "X-Timestamp": v["timestamp"],
                       "X-Signature": v["signature"]}


def test_the_vectors_are_written_out_never_computed():
    """A vector computed by the code it checks would follow that code."""
    tree = ast.parse(Path(wire_vectors.__file__).read_text(encoding="utf-8"))
    imported = [node for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(getattr(node, "module", None) in ("__future__", "typing") for node in imported)
