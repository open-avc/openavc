"""Credentials at rest — what `system.json` is allowed to hold.

`system.json` used to hold the web admin password exactly as typed. Anything
that could read one file got a working credential: a lifted SD card, a
diagnostic copy someone emails, and — the one that matters most here — **any
plugin or script**, because those run in-process as the service user. Installing
a community plugin should not mean handing over the admin password. On a Pi
appliance the same string is the OS account password, so the blast radius was a
shell as well as a login.

The API key is the same exposure one field over, and a more direct one: it is
presented in `X-API-Key` and satisfies the admin check outright, with no
password involved. Both are stored as a digest now, and neither is ever read
back — every consumer either checks presence or verifies a supplied value.

**Two records, because the two credentials are attacked differently.**

    scrypt$<n>$<r>$<p>$<salt-b64>$<digest-b64>   the admin password
    sha256$<salt-b64>$<digest-b64>               the API key

A password is short, human-chosen and guessable, so the cost of *one guess* is
the whole defence and it is paid rarely — a sign-in, and the Programmer SPA
trades it for a session token immediately. scrypt, deliberately slow.

An API key is a bearer token a machine presents on **every request**. The same
slow hash there would be paid by the legitimate caller on each poll (22 ms and
16 MiB per request on a dev Mac, several times that on a Pi) and by anyone who
sends a wrong key, on a path that includes the WebSocket handshake, which no
rate limiter sees. So the cost has to stay near zero, which means the key's own
entropy has to carry the offline case — which is why Settings grew a Generate
button producing 32 random bytes, and why it is the easy path there.

What that leaves: a key somebody types by hand instead is as guessable offline
as they made it. That is worth knowing and is not worth a slow hash, because it
is bounded on both sides — such a key is online-guessable too, which no hashing
fixes, and it is still strictly better off than the plaintext it replaces. The
threat this module exists for is closed either way: a digest cannot be
presented, however cheaply it was computed.

Verification reads the parameters back out of the record, so re-tuning the cost
later leaves every already-stored credential verifiable. `$` is not in the
base64 alphabet, which is what makes the split unambiguous.

Both algorithms are stdlib (OpenSSL under `hashlib`), so the MIT-only
dependency rule is untouched — that is why scrypt was chosen over the usual
argon2/bcrypt packages.

**A value that is not in either form is compared literally.** Two live cases
need it and neither is a legacy tolerance to be removed later:
`OPENAVC_PROGRAMMER_PASSWORD` / `OPENAVC_API_KEY` supply a plaintext fresh each
boot and are deliberately never persisted, and an operator provisioning a box
by hand may write one into `system.json` directly (`SystemConfig` converts that
one on the next start).

`auth.panel_lock_code` is deliberately NOT converted — see the note beside it
in `system_config.py`. `isc.auth_key` and `cloud.system_key` are not candidates
at all: this instance has to present them, so the file's 0600 mode is what
protects those.

Pure stdlib and imports nothing from `openavc`. `system_config` still imports it
inside the function rather than at module scope, because it sits in the import
closure the simulator and the driver validator are held to — see
`tests/test_compiled_protocol_purity.py`.
"""


from __future__ import annotations

import base64
import hashlib
import os
import secrets

# scrypt cost: the parameters the scrypt paper gives for an interactive login.
# 16 MiB and 22 ms measured on the dev Mac; the smallest box OpenAVC ships on is
# a Raspberry Pi, where it is several times that and still fine for a sign-in.
#
# Raising n buys resistance to cracking a stolen `system.json`, which is the
# threat this module exists for. What holds it here is the other side: a
# password check runs on requests nobody has authenticated yet. A wrong password
# over HTTP is throttled (401s feed the brute-force counter at the strict rate),
# but the WebSocket handshake checks a credential too and no rate limiter sees
# it — `/ws` is skipped, and the middleware never runs on a websocket scope
# regardless. So n is also what an unauthenticated caller can make the box spend
# per attempt, in memory as much as in time, and 16 MiB is where that stays
# survivable on a Pi.
_N = 1 << 14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16

_ALGO = "scrypt"
_PREFIX = _ALGO + "$"

# The API key's record. No cost parameters: a plain salted digest, and the salt
# is what stops one leaked file answering "is this the same key as that one".
_ALGO_KEY = "sha256"
_PREFIX_KEY = _ALGO_KEY + "$"

# Every prefix that means "this is a stored digest, not something somebody
# typed". `looks_hashed` is what the startup conversion asks, and its question
# is exactly that, for either credential.
_RECORD_PREFIXES = (_PREFIX, _PREFIX_KEY)


# OpenSSL refuses a derivation whose estimated memory exceeds maxmem, and its
# estimate (128 * r * (n + p + 2)) is a shade over the 128*n*r the parameters
# imply. Ask for double so a future cost bump doesn't trip an opaque ValueError.
def _maxmem(n: int, r: int) -> int:
    return 128 * n * r * 2


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(plaintext: str) -> str:
    """Return the storable form of ``plaintext``.

    An empty password stores as an empty string rather than a digest of nothing:
    "no credential is set" is a state the rest of the system reads by truthiness
    (`is_claimed`, the `if pw` gates in the auth checks), and a digest there
    would claim the instance while matching no password anyone could type.
    """
    if not plaintext:
        return ""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=_N, r=_R, p=_P,
        maxmem=_maxmem(_N, _R),
        dklen=_DKLEN,
    )
    return f"{_PREFIX}{_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def looks_hashed(stored: str) -> bool:
    """Whether ``stored`` is one of our digest records rather than a plaintext.

    Answers for either credential, because the one caller that asks — the
    startup conversion — wants to know whether there is still a plaintext in
    the file, not which algorithm produced the record. Each `verify_*` below
    then refuses a record from the other family, so a value in the wrong field
    authenticates nobody rather than falling back to a literal comparison.
    """
    return bool(stored) and stored.startswith(_RECORD_PREFIXES)


def verify_password(provided: str, stored: str) -> bool:
    """Timing-safe check of ``provided`` against the stored credential.

    Returns False when nothing is stored. Callers all gate on a configured
    password before asking, so this only ever matters as a backstop — but the
    backstop belongs here, where the answer to "does the empty password match
    the empty credential" is unambiguously no.
    """
    if not stored:
        return False
    if not looks_hashed(stored):
        return secrets.compare_digest(provided, stored)

    parsed = _parse(stored)
    if parsed is None:
        # A record we cannot read authenticates nobody. Left deliberately quiet:
        # this runs on every failed sign-in attempt, and the only way to get
        # here is a hand-mangled system.json.
        return False
    n, r, p, salt, expected = parsed
    try:
        actual = hashlib.scrypt(
            provided.encode("utf-8"),
            salt=salt,
            n=n, r=r, p=p,
            maxmem=_maxmem(n, r),
            dklen=len(expected),
        )
    except ValueError:
        # Parameters that OpenSSL rejects (a bad n, or a cost far above what
        # this build's maxmem allows). Same answer as an unreadable record.
        return False
    return secrets.compare_digest(actual, expected)


def hash_api_key(plaintext: str) -> str:
    """Return the storable form of an API key.

    Empty stores as empty, for the same reason an empty password does: "no key
    is set" is read by truthiness all over the auth checks, and a digest there
    would make the instance look claimed by a key nobody holds.
    """
    if not plaintext:
        return ""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.sha256(salt + plaintext.encode("utf-8")).digest()
    return f"{_PREFIX_KEY}{_b64(salt)}${_b64(digest)}"


def verify_api_key(provided: str, stored: str) -> bool:
    """Timing-safe check of ``provided`` against the stored API key.

    Runs on every request that carries `X-API-Key`, so it stays cheap — see the
    module docstring for why that is the right trade for this credential and
    the wrong one for the password.
    """
    if not stored:
        return False
    if not looks_hashed(stored):
        # A plaintext: OPENAVC_API_KEY, or a hand-provisioned system.json that
        # has not been through a server start yet.
        return secrets.compare_digest(provided, stored)

    parts = stored.split("$")
    if len(parts) != 3 or parts[0] != _ALGO_KEY:
        # An unreadable record, or a password record in the API-key field.
        # Either way it authenticates nobody.
        return False
    try:
        salt = base64.b64decode(parts[1], validate=True)
        expected = base64.b64decode(parts[2], validate=True)
    except (ValueError, TypeError):
        return False
    if not salt or not expected:
        return False
    actual = hashlib.sha256(salt + provided.encode("utf-8")).digest()
    return secrets.compare_digest(actual, expected)


def _parse(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
    """Split a record into (n, r, p, salt, digest), or None if it is malformed."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _ALGO:
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        digest = base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError):
        return None
    if n < 2 or r < 1 or p < 1 or not salt or not digest:
        return None
    return n, r, p, salt, digest
