"""The stored form of the admin password and of the API key.

`system.json` held the web admin password exactly as typed, so one readable
file — a lifted SD card, an emailed diagnostic, or any plugin, which runs
in-process as the service user — yielded a working credential. What is stored
now is a scrypt digest carrying the parameters that made it.

The API key is the same exposure one field over and gets its own record, a fast
salted one — it is presented on every request a machine makes, so the password's
deliberately slow hash would be paid by the legitimate caller and by anyone
sending a wrong key. That difference is pinned below rather than described,
because it is the kind of thing a later "let's use one function for both"
would quietly undo.

The awkward part of the contract is the *plaintext* case, and it is deliberate
rather than legacy: `OPENAVC_PROGRAMMER_PASSWORD` / `OPENAVC_API_KEY` are
supplied fresh each boot and never written to the file, so a value that is not
a digest is compared literally. Half of what is below pins that door open, and
the other half pins it narrow.
"""

from openavc.utils.password_hash import (
    hash_api_key,
    hash_password,
    looks_hashed,
    verify_api_key,
    verify_password,
)


class TestStoredForm:
    def test_a_hashed_password_verifies(self):
        stored = hash_password("hunter2hunter2")
        assert verify_password("hunter2hunter2", stored) is True

    def test_the_password_is_not_in_the_stored_form(self):
        """The whole point: reading the file yields no usable credential."""
        assert "hunter2hunter2" not in hash_password("hunter2hunter2")

    def test_a_wrong_password_does_not_verify(self):
        stored = hash_password("hunter2hunter2")
        assert verify_password("hunter2hunter3", stored) is False
        assert verify_password("", stored) is False

    def test_the_same_password_stores_differently_every_time(self):
        """A per-password salt — otherwise two boxes sharing a password share a
        digest, and one cracked file cracks both."""
        assert hash_password("hunter2hunter2") != hash_password("hunter2hunter2")

    def test_the_record_names_its_algorithm_and_cost(self):
        stored = hash_password("hunter2hunter2")
        algo, n, r, p, _salt, _digest = stored.split("$")
        assert algo == "scrypt"
        assert (int(n), int(r), int(p)) == (16384, 8, 1)

    def test_a_record_is_verified_with_its_own_parameters(self):
        """Cost can be re-tuned later without stranding stored passwords: the
        verifier reads n/r/p out of the record, not out of the module."""
        cheap = _record_at_cost("hunter2hunter2", n=1024)
        assert "$1024$" in cheap
        assert verify_password("hunter2hunter2", cheap) is True
        assert verify_password("wrong", cheap) is False

    def test_unicode_survives_the_round_trip(self):
        stored = hash_password("pässwörd-ünïcode")
        assert verify_password("pässwörd-ünïcode", stored) is True

    def test_a_long_password_survives_the_round_trip(self):
        long_one = "x" * 512
        assert verify_password(long_one, hash_password(long_one)) is True


class TestNoCredential:
    def test_an_empty_password_stores_as_empty_not_as_a_digest(self):
        """"Unclaimed" is read by truthiness all over the auth module. A digest
        of the empty string would claim the instance and match nothing."""
        assert hash_password("") == ""

    def test_nothing_verifies_against_an_empty_credential(self):
        assert verify_password("", "") is False
        assert verify_password("anything", "") is False


class TestPlaintextIsStillAccepted:
    """`OPENAVC_PROGRAMMER_PASSWORD` never becomes a digest — it is supplied per
    boot and deliberately not persisted. A hand-provisioned `system.json` lands
    here too, until the next start converts it."""

    def test_a_plaintext_credential_verifies(self):
        assert verify_password("env-supplied", "env-supplied") is True

    def test_a_wrong_password_against_a_plaintext_credential_does_not(self):
        assert verify_password("env-supplie", "env-supplied") is False

    def test_looks_hashed_is_what_separates_the_two(self):
        assert looks_hashed(hash_password("hunter2hunter2")) is True
        assert looks_hashed("hunter2hunter2") is False
        assert looks_hashed("") is False


class TestMalformedRecords:
    """A record we cannot read authenticates nobody — never everybody."""

    def test_a_truncated_record_verifies_nothing(self):
        assert verify_password("hunter2hunter2", "scrypt$16384$8$1$abc") is False

    def test_a_record_with_a_non_numeric_cost_verifies_nothing(self):
        assert verify_password("x", "scrypt$lots$8$1$YWJj$YWJj") is False

    def test_a_record_with_unusable_base64_verifies_nothing(self):
        assert verify_password("x", "scrypt$16384$8$1$not!base64$YWJj") is False

    def test_a_record_with_an_impossible_cost_verifies_nothing(self):
        """n must be a power of two above 1; OpenSSL raises otherwise, and a
        raise on the sign-in path would be a 500 instead of a refusal."""
        assert verify_password("x", "scrypt$3$8$1$YWJj$YWJj") is False

    def test_a_record_naming_another_algorithm_is_treated_as_a_plaintext(self):
        """Not ours, so not a digest — it falls to the literal comparison, which
        is the only safe reading of a value we did not write."""
        assert verify_password("bcrypt$2b$whatever", "bcrypt$2b$whatever") is True
        assert verify_password("something else", "bcrypt$2b$whatever") is False


def _record_at_cost(plaintext: str, *, n: int) -> str:
    """A valid record at a non-default cost, built the way the module does."""
    import base64
    import hashlib
    import os

    salt = os.urandom(16)
    digest = hashlib.scrypt(
        plaintext.encode("utf-8"), salt=salt, n=n, r=8, p=1,
        maxmem=128 * n * 8 * 2, dklen=32,
    )
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"scrypt${n}$8$1${b64(salt)}${b64(digest)}"


# ---------------------------------------------------------------------------
# The API key
# ---------------------------------------------------------------------------


class TestTheApiKeyRecord:
    """Same exposure as the password, one field over: `X-API-Key` satisfies the
    admin check outright, so a plugin that could read `system.json` had full
    API access. Stored as a digest now."""

    def test_a_hashed_key_verifies(self):
        stored = hash_api_key("integration-key-1")
        assert verify_api_key("integration-key-1", stored) is True

    def test_the_key_is_not_in_the_stored_form(self):
        assert "integration-key-1" not in hash_api_key("integration-key-1")

    def test_a_wrong_key_does_not_verify(self):
        stored = hash_api_key("integration-key-1")
        assert verify_api_key("integration-key-2", stored) is False
        assert verify_api_key("", stored) is False

    def test_the_same_key_stores_differently_every_time(self):
        """Salted, so one leaked file cannot answer "is this the same key as
        the one on that other box"."""
        assert hash_api_key("integration-key-1") != hash_api_key("integration-key-1")

    def test_the_record_names_its_algorithm(self):
        algo, _salt, _digest = hash_api_key("integration-key-1").split("$")
        assert algo == "sha256"

    def test_unicode_survives_the_round_trip(self):
        stored = hash_api_key("clé-intégration-café")
        assert verify_api_key("clé-intégration-café", stored) is True

    def test_an_empty_key_stores_as_empty_not_as_a_digest(self):
        """"No key is set" is read by truthiness all over the auth checks. A
        digest there would make the instance look claimed by a key nobody
        holds."""
        assert hash_api_key("") == ""

    def test_nothing_verifies_against_an_empty_credential(self):
        assert verify_api_key("", "") is False
        assert verify_api_key("anything", "") is False


class TestTheApiKeyStaysCheap:
    """The key is presented on every request a machine makes, and by anyone
    sending a wrong one — including over the WebSocket handshake, which no rate
    limiter sees. The password's slow hash on that path would be a lever, not a
    defence, which is the whole reason the two records differ."""

    def test_verifying_a_key_does_not_touch_scrypt(self, monkeypatch):
        import hashlib

        def _refuse(*args, **kwargs):
            raise AssertionError("verify_api_key must not run a slow KDF")

        stored = hash_api_key("integration-key-1")
        monkeypatch.setattr(hashlib, "scrypt", _refuse)
        assert verify_api_key("integration-key-1", stored) is True
        assert verify_api_key("wrong", stored) is False

    def test_hashing_a_key_does_not_touch_scrypt(self, monkeypatch):
        import hashlib

        def _refuse(*args, **kwargs):
            raise AssertionError("hash_api_key must not run a slow KDF")

        monkeypatch.setattr(hashlib, "scrypt", _refuse)
        assert hash_api_key("integration-key-1").startswith("sha256$")


class TestTheApiKeyPlaintextDoor:
    """`OPENAVC_API_KEY` supplies a plaintext fresh each boot and is never
    written to the file, and a hand-provisioned system.json holds one until the
    next server start. Both compare literally."""

    def test_a_plaintext_key_verifies(self):
        assert verify_api_key("from-env-key", "from-env-key") is True

    def test_a_wrong_key_against_a_plaintext_does_not(self):
        assert verify_api_key("something else", "from-env-key") is False

    def test_looks_hashed_is_what_separates_the_two(self):
        assert looks_hashed(hash_api_key("integration-key-1")) is True
        assert looks_hashed("integration-key-1") is False


class TestTheTwoRecordsDoNotCrossOver:
    """Each verifier refuses the other's record instead of falling back to the
    literal comparison. A credential in the wrong field authenticates nobody —
    which matters because the literal fallback is otherwise wide open: the
    record itself is what an attacker reading the file would present."""

    def test_a_password_record_in_the_api_key_field_verifies_nothing(self):
        stored = hash_password("hunter2hunter2")
        assert verify_api_key("hunter2hunter2", stored) is False
        assert verify_api_key(stored, stored) is False

    def test_an_api_key_record_in_the_password_field_verifies_nothing(self):
        stored = hash_api_key("integration-key-1")
        assert verify_password("integration-key-1", stored) is False
        assert verify_password(stored, stored) is False


class TestMalformedApiKeyRecords:
    """A record we cannot read authenticates nobody — never everybody."""

    def test_a_truncated_record_verifies_nothing(self):
        assert verify_api_key("integration-key-1", "sha256$YWJj") is False

    def test_a_record_with_too_many_parts_verifies_nothing(self):
        assert verify_api_key("x", "sha256$YWJj$YWJj$YWJj") is False

    def test_a_record_with_unusable_base64_verifies_nothing(self):
        assert verify_api_key("x", "sha256$not!base64$YWJj") is False

    def test_a_record_with_an_empty_salt_verifies_nothing(self):
        assert verify_api_key("x", "sha256$$YWJj") is False
