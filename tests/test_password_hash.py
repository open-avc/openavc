"""The stored form of the admin password.

`system.json` held the web admin password exactly as typed, so one readable
file — a lifted SD card, an emailed diagnostic, or any plugin, which runs
in-process as the service user — yielded a working credential. What is stored
now is a scrypt digest carrying the parameters that made it.

The awkward part of the contract is the *plaintext* case, and it is deliberate
rather than legacy: `OPENAVC_PROGRAMMER_PASSWORD` is supplied fresh each boot
and never written to the file, so a value that is not a digest is compared
literally. Half of what is below pins that door open, and the other half pins
it narrow.
"""

from openavc.utils.password_hash import hash_password, looks_hashed, verify_password


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
